"""Text-to-speech workflow and Telegram progress updates."""
import asyncio
import contextlib
import html
import logging
import os
import re
import tempfile

import httpx

from telegram_client import TelegramClient, TelegramError

logger = logging.getLogger("bot.tts")
VOICE = "en-US-AvaMultilingualNeural"


class SpeechError(RuntimeError):
    pass


async def synthesize(http_client: httpx.AsyncClient, text: str, filename: str) -> None:
    key = os.environ.get("SPEECH_KEY")
    region = os.environ.get("SPEECH_REGION", "")
    if not key or not re.fullmatch(r"[a-z0-9]+", region):
        raise SpeechError("Azure Speech is not configured. Check SPEECH_KEY and SPEECH_REGION.")
    ssml = (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
        f'<voice name="{VOICE}">{html.escape(text)}</voice></speak>'
    )
    try:
        response = await http_client.post(
            f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
            headers={
                "Ocp-Apim-Subscription-Key": key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                "User-Agent": "MalayalamTTSBot",
            },
            content=ssml.encode("utf-8"),
            timeout=60.0,
        )
    except httpx.HTTPError as error:
        logger.error("Azure Speech request failed (%s)", type(error).__name__)
        raise SpeechError("Could not reach Azure Speech. Please try again later.") from None
    if response.status_code == 429:
        raise SpeechError("Azure Speech quota or request limit reached. Please try again later.")
    if response.status_code in (401, 403):
        raise SpeechError("Azure Speech rejected its credentials. Contact the bot operator.")
    if response.status_code != 200:
        raise SpeechError(f"Azure Speech is unavailable (HTTP {response.status_code}).")
    if not response.content or not response.headers.get("content-type", "").startswith("audio/"):
        raise SpeechError("Azure Speech returned invalid or empty audio.")
    with open(filename, "wb") as audio:
        audio.write(response.content)


async def _chat_action_loop(bot: TelegramClient, chat_id: int, action: str) -> None:
    """Send chat action every 4 seconds until cancelled."""
    try:
        while True:
            await bot.send_chat_action(chat_id, action)
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass
    except TelegramError:
        logger.warning("Could not send chat action for chat_id=%s", chat_id)


async def run_tts(
    bot: TelegramClient,
    chat_id: int,
    args: str,
    reply_to_message_id: int | None = None,
) -> None:
    logger.info("TTS started: chat_id=%s, args_len=%d", chat_id, len(args))

    resp = await bot.send_message(chat_id, "🎙️ Generating audio...", reply_to_message_id=reply_to_message_id)
    progress_msg_id = resp.get("result", {}).get("message_id") or None

    audio_file = None
    action_task = None

    try:
        action_task = asyncio.create_task(_chat_action_loop(bot, chat_id, "record_voice"))

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            audio_file = tmp.name

        await synthesize(bot.http_client, args, audio_file)
        logger.info("Audio generated: chat_id=%s", chat_id)

        await bot.send_voice(
            chat_id, audio_file, args, reply_to_message_id,
        )

        logger.info("Voice sent: chat_id=%s", chat_id)
    except (SpeechError, TelegramError) as e:
        error_msg = html.escape(str(e))
        logger.error("TTS failed: chat_id=%s, error=%s", chat_id, error_msg)
        if progress_msg_id:
            await bot.edit_message(chat_id, progress_msg_id, f"<b>❌ Error:</b> {error_msg}", parse_mode="HTML")
        else:
            await bot.send_message(chat_id, f"<b>❌ Error:</b> {error_msg}", reply_to_message_id=reply_to_message_id, parse_mode="HTML")
    else:
        if progress_msg_id:
            try:
                await bot.edit_message(chat_id, progress_msg_id, "✅ Done!")
            except TelegramError:
                logger.warning("Voice delivered but completion notice failed for chat_id=%s", chat_id)
    finally:
        if action_task:
            action_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await action_task
        if audio_file and os.path.exists(audio_file):
            try:
                os.remove(audio_file)
            except OSError:
                logger.error("Failed to remove temporary audio", exc_info=True)
        logger.info("TTS complete: chat_id=%s", chat_id)
