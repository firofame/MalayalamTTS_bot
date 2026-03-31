#!/usr/bin/env python3
"""Text-to-Speech converter using Google Docs and Playwright."""

import sys
import base64
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright, Page, Browser

# Configuration
CONFIG = {
    'doc_url': 'https://docs.google.com/document/d/1WVxgs-UywesdGppo1zLFR-YA57TQiwEpXDjKoq9EfyM/edit?usp=sharing',
    'max_chunk_length': 20_000,
    'insert_chunk_size': 4000,
    'timeout': 120_000,
}

SELECTORS = {
    'tts_button': '#textToSpeechToolbarButton',
    'editor': '.kix-appview-editor',
    'player_audio': '.kixAudioPlayerView [data-media-url][data-media-type="audio"]',
    'player_max_time': '.docsUiWizAudioSliderMaxTime',
    'player_close': '.kixAudioPlayerPaletteCloseButton[aria-label="Close"]',
}

# Shared browser state
_playwright = None
_browser = None
_page = None


def split_text(text: str) -> list[str]:
    """Split text into chunks that fit within maxChunkLength."""
    chunks = []
    current = ''

    for line in text.split('\n'):
        while len(line) > 300 and ',' in line:
            idx = line.index(',')
            line = line[:idx] + '.' + line[idx + 1:]

        if current and len(current) + len(line) + 1 > CONFIG['max_chunk_length']:
            chunks.append(current)
            current = ''
        current += ('\n' if current else '') + line

    if current:
        chunks.append(current)

    return chunks


async def click(page: Page, selector: str):
    await page.locator(selector).first.click(timeout=CONFIG['timeout'])


async def wait_for_time_display(page: Page):
    await page.wait_for_function(
        """() => /^\\d{1,2}:\\d{2}(:\\d{2})?$/.test(document.querySelector('.docsUiWizAudioSliderMaxTime')?.textContent?.trim() || '')""",
        timeout=CONFIG['timeout']
    )


async def get_blob_url(page: Page) -> str:
    result = await page.wait_for_function(
        """() => {
            const url = document.querySelector('.kixAudioPlayerView [data-media-url][data-media-type="audio"]')?.getAttribute('data-media-url') || '';
            return url.startsWith('blob:') ? url : null;
        }""",
        timeout=CONFIG['timeout']
    )
    return await result.json_value()


async def save_blob(page: Page, blob_url: str, output_path: Path):
    base64_data = await page.evaluate("""async (url) => {
        const res = await fetch(url);
        const blob = await res.blob();
        return new Promise((resolve) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result.split(',')[1]);
            reader.readAsDataURL(blob);
        });
    }""", blob_url)

    output_path.write_bytes(base64.b64decode(base64_data))


async def close_player(page: Page):
    try:
        await page.locator(SELECTORS['player_close']).first.click(timeout=3000)
        await asyncio.sleep(0.5)
    except Exception:
        pass


async def insert_text(page: Page, text: str):
    await click(page, SELECTORS['editor'])
    await asyncio.sleep(0.5)

    mod = 'Meta' if sys.platform == 'darwin' else 'Control'

    await page.keyboard.press(f'{mod}+A')
    await asyncio.sleep(0.2)
    await page.keyboard.press('Backspace')
    await asyncio.sleep(0.7)

    normalized = text.replace('\r\n', '\n')
    chunk_size = CONFIG['insert_chunk_size']

    for i in range(0, len(normalized), chunk_size):
        await page.keyboard.insert_text(normalized[i:i + chunk_size])

    await asyncio.sleep(0.5)


async def generate_audio(page: Page) -> str:
    for i in range(2):
        await click(page, SELECTORS['tts_button'])
        await page.wait_for_selector(SELECTORS['player_max_time'], timeout=CONFIG['timeout'])
        await wait_for_time_display(page)

        if i == 0:
            await close_player(page)

    return await get_blob_url(page)


async def init_browser():
    """Launch browser and navigate to Google Docs."""
    global _playwright, _browser, _page

    _playwright = await async_playwright().start()
    _browser = await _playwright.firefox.launch(
        headless=True,
        firefox_user_prefs={'media.volume_scale': '0.0'}
    )

    context = await _browser.new_context(storage_state='/etc/secrets/auth.json')
    _page = await context.new_page()

    await _page.goto(CONFIG['doc_url'], wait_until='domcontentloaded')
    await _page.wait_for_selector(SELECTORS['editor'], timeout=CONFIG['timeout'])
    print("Browser ready")


async def close_browser():
    """Close browser and cleanup."""
    global _playwright, _browser, _page

    if _browser:
        await _browser.close()
    if _playwright:
        await _playwright.stop()

    _browser = None
    _playwright = None
    _page = None


async def generate_tts(text: str, output_path: Path):
    """Generate TTS audio from text and save to output_path."""
    if not _page:
        raise RuntimeError("Browser not initialized. Call init_browser() first.")

    if not text.strip():
        raise ValueError("Empty text")

    await close_player(_page)
    await insert_text(_page, text)
    await generate_audio(_page)
    await save_blob(_page, await get_blob_url(_page), output_path)
    await close_player(_page)


# CLI support
def parse_args():
    if len(sys.argv) < 2:
        print('Usage: python tts.py input.txt [output.mp3]', file=sys.stderr)
        sys.exit(1)

    input_path = Path(sys.argv[1]).resolve()
    output_path = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else input_path.with_suffix('.mp3')
    return input_path, output_path


async def cli_main():
    input_path, output_path = parse_args()
    text = input_path.read_text(encoding='utf-8')

    if not text.strip():
        raise ValueError('Empty input file')

    chunks = split_text(text)
    print(f'Processing {len(chunks)} chunk(s)...')

    await init_browser()

    try:
        for i, chunk in enumerate(chunks):
            if len(chunks) > 1:
                out = output_path.with_name(f"{output_path.stem}-{i + 1}{output_path.suffix}")
            else:
                out = output_path

            print(f'\n--- Chunk {i + 1}/{len(chunks)} ---')
            await generate_tts(chunk, out)
            print(f'Saved {out}')

        print('\nDone!')
    finally:
        await close_browser()


if __name__ == '__main__':
    try:
        asyncio.run(cli_main())
    except Exception as err:
        print(f'Error: {err}', file=sys.stderr)
        sys.exit(1)
