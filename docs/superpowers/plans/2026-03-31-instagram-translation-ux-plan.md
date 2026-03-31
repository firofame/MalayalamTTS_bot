# Instagram Translation UX Enhancement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send translated text immediately before audio, with progress updates and Instagram metadata display.

**Architecture:** Split the monolithic translate+TTS pipeline into independent steps: Instagram metadata extraction → text translation → TTS. Progress messages are edited in place via Telegram's editMessageText API.

**Tech Stack:** FastAPI, yt-dlp, google-genai, edge-tts, requests

---

### Task 1: Create Instagram metadata extraction module

**Files:**
- Create: `instagram.py`
- Test: `tests/test_instagram.py`

- [ ] **Step 1: Write tests for extract_instagram_metadata**

```python
# tests/test_instagram.py
import pytest
from instagram import extract_instagram_metadata, _parse_yt_dlp_info

def test_parse_yt_dlp_info_with_caption():
    info = {
        "description": "Hello world! #test",
        "uploader": "testuser",
        "extractor_key": "Instagram",
        "_type": "url",
    }
    result = _parse_yt_dlp_info(info)
    assert result["caption"] == "Hello world! #test"
    assert result["author"] == "testuser"
    assert result["content_type"] == "Post"

def test_parse_yt_dlp_info_reel():
    info = {
        "description": None,
        "uploader": "reelcreator",
        "extractor_key": "Instagram",
        "product_type": "clips",
    }
    result = _parse_yt_dlp_info(info)
    assert result["caption"] is None
    assert result["author"] == "reelcreator"
    assert result["content_type"] == "Reel"

def test_parse_yt_dlp_info_story():
    info = {
        "description": "Story text here",
        "uploader": "storyuser",
        "product_type": "story",
    }
    result = _parse_yt_dlp_info(info)
    assert result["caption"] == "Story text here"
    assert result["content_type"] == "Story"
```

- [ ] **Step 2: Implement instagram.py**

```python
"""Instagram content extraction — fetches caption and metadata without downloading audio."""
import subprocess
import json
from pathlib import Path


def _parse_yt_dlp_info(info: dict) -> dict:
    """Parse yt-dlp JSON output into a normalized metadata dict."""
    caption = info.get("description") or None
    if caption:
        caption = caption.strip()
        if not caption:
            caption = None

    uploader = info.get("uploader") or info.get("channel") or ""
    # Remove @ prefix if present for display
    author = uploader.lstrip("@")

    # Determine content type
    product = info.get("product_type", "").lower()
    if product == "clips":
        content_type = "Reel"
    elif product == "story":
        content_type = "Story"
    else:
        content_type = "Post"

    return {
        "caption": caption,
        "author": author,
        "content_type": content_type,
    }


def extract_instagram_metadata(url: str) -> dict:
    """Extract caption, author, and content type from an Instagram URL.

    Uses yt-dlp in metadata-only mode (no download).
    Returns: {"caption": str|None, "author": str, "content_type": str}
    Raises: RuntimeError if yt-dlp fails or URL is not extractable.
    """
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--dump-json",
                "--no-download",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("yt-dlp timed out while fetching metadata")

    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr.strip()}")

    # yt-dlp outputs one JSON object per line (may have multiple for playlists)
    # Take the last non-empty line as the primary item
    lines = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("No metadata returned by yt-dlp")

    try:
        info = json.loads(lines[-1])
    except json.JSONDecodeError:
        raise RuntimeError("Failed to parse yt-dlp metadata JSON")

    return _parse_yt_dlp_info(info)
```

- [ ] **Step 3: Run tests**

```bash
cd /home/firoz/Desktop/MalayalamTTS_bot && python -m pytest tests/test_instagram.py -v
```

Expected: All 3 tests pass.

- [ ] **Step 4: Commit**

```bash
cd /home/firoz/Desktop/MalayalamTTS_bot && git add instagram.py tests/test_instagram.py && git commit -m "feat: add Instagram metadata extraction module"
```

---

### Task 2: Split translate.py into independent functions

**Files:**
- Modify: `translate.py`

- [ ] **Step 1: Read current translate.py**

Read the full file at `translate.py` — it currently has `convert_to_audiobook_script()` as a monolithic function.

- [ ] **Step 2: Rewrite translate.py with split functions**

Replace the entire contents of `translate.py` with:

```python
"""Translation and TTS pipeline — independent functions for text translation, audio transcription, and speech synthesis."""
import sys
import os
import subprocess
from pathlib import Path
from google import genai

AUDIO_EXTENSIONS = {'.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac', '.wma', '.opus', '.webm'}

# Separate prompts for text vs audio
TEXT_TRANSLATION_PROMPT = (
    "Detect the source language of the following text and translate it to Malayalam.\n"
    "Preserve emojis, line breaks, and formatting.\n"
    "If the text contains mixed languages, translate everything to Malayalam.\n"
    "Return only the translated text, no explanations or introductions.\n"
)

AUDIO_TRANSCRIPTION_PROMPT = (
    "Transcribe the audio and translate the result to Malayalam.\n"
    "Return only the translated text, no explanations or introductions.\n"
)


def _get_genai_client():
    """Create and return a Gemini API client."""
    return genai.Client()


def translate_text(text: str) -> str:
    """Translate text to Malayalam using Gemini.

    Args:
        text: Source text in any language (commonly Arabic).

    Returns:
        Translated Malayalam text, or empty string on failure.
    """
    client = _get_genai_client()
    response = client.models.generate_content(
        model="models/gemini-3.1-flash-lite-preview",
        contents=[TEXT_TRANSLATION_PROMPT, f"Input Text to Convert:\n\n{text}"],
        config={"temperature": 0.1},
    )
    return response.text.strip() if response.text else ""


def transcribe_audio(audio_path: str) -> str:
    """Transcribe audio file and translate to Malayalam using Gemini.

    Args:
        audio_path: Path to audio file.

    Returns:
        Translated Malayalam text, or empty string on failure.
    """
    client = _get_genai_client()
    print(f"Uploading audio file {audio_path}...")
    myfile = client.files.upload(file=str(audio_path))
    print(f"Transcribing and translating to Malayalam...")

    response = client.models.generate_content(
        model="models/gemini-3.1-flash-lite-preview",
        contents=[AUDIO_TRANSCRIPTION_PROMPT, myfile],
        config={"temperature": 0.1},
    )
    return response.text.strip() if response.text else ""


def download_audio(url: str) -> str:
    """Download audio from URL using yt-dlp and return the file path.

    Args:
        url: YouTube, Instagram, or other supported URL.

    Returns:
        Path to downloaded mp3 file.
    Raises:
        SystemExit if yt-dlp fails.
    """
    output_dir = Path("downloads")
    output_dir.mkdir(exist_ok=True)
    output_template = str(output_dir / "%(title)s.%(ext)s")

    print(f"Downloading audio from {url}...")
    result = subprocess.run(
        ["yt-dlp", "-x", "--audio-format", "mp3", "-o", output_template, url],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"Error: yt-dlp failed:\n{result.stderr}")
        sys.exit(1)

    combined = result.stdout + result.stderr
    for line in combined.splitlines():
        if "[ExtractAudio] Destination:" in line:
            path = line.split("Destination:")[-1].strip()
            if path.endswith(".mp3") and Path(path).exists():
                return path

    mp3s = sorted(output_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
    if mp3s:
        return str(mp3s[0])

    print("Error: Could not find downloaded audio file")
    sys.exit(1)


def convert_to_audiobook_script(input_file: str, output_file: str):
    """Legacy function — transcribes/translates and saves to file.

    Kept for backward compatibility with CLI usage.
    """
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"Error: {input_file} not found")
        return

    is_audio = input_path.suffix.lower() in AUDIO_EXTENSIONS

    if is_audio:
        result = transcribe_audio(input_file)
    else:
        source_text = input_path.read_text(encoding="utf-8")
        result = translate_text(source_text)

    if not result:
        print("Error: Gemini returned empty response.")
        return

    Path(output_file).write_text(result, encoding="utf-8")
    print(f"✅ Success! Saved audiobook script to {output_file}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 translate.py <input> [output_file]")
        print("  input: text file, audio file, or URL (YouTube, Instagram, etc.)")
        sys.exit(1)

    input_arg = sys.argv[1]

    if input_arg.startswith("http://") or input_arg.startswith("https://"):
        input_file = download_audio(input_arg)
        default_name = Path(input_file).stem + "_audiobook.txt"
    else:
        input_file = input_arg
        default_name = Path(input_file).stem + "_audiobook.txt"

    output_file = sys.argv[2] if len(sys.argv) > 2 else str(Path(input_file).parent / default_name)
    convert_to_audiobook_script(input_file, output_file)
```

- [ ] **Step 3: Verify CLI still works**

```bash
cd /home/firoz/Desktop/MalayalamTTS_bot && python translate.py --help 2>&1 || python translate.py 2>&1
```

Expected: Shows usage message (exit code 1 with usage text).

- [ ] **Step 4: Commit**

```bash
cd /home/firoz/Desktop/MalayalamTTS_bot && git add translate.py && git commit -m "refactor: split translate.py into independent translate_text, transcribe_audio functions"
```

---

### Task 3: Update main.py with new flow, progress messages, and text-first delivery

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Read current main.py**

Read the full file at `main.py` — understand the current `/tts` handler flow.

- [ ] **Step 2: Rewrite main.py**

Replace the entire contents of `main.py` with:

```python
import os
import tempfile
import time
import requests
import edge_tts
from fastapi import FastAPI, Request
from translate import translate_text, transcribe_audio, download_audio
from instagram import extract_instagram_metadata

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
VOICE = "en-US-AvaMultilingualNeural"

# Simple rate limiting: chat_id -> last_request_time
_rate_limits = {}
RATE_LIMIT_SECONDS = 30


@app.on_event("startup")
def setup_commands():
    requests.post(
        f"{TELEGRAM_API_URL}/setMyCommands",
        json={
            "commands": [
                {"command": "start", "description": "Start the bot"},
                {"command": "tts", "description": "Translate and convert text to Malayalam speech"},
            ]
        }
    )


def send_message(chat_id: int, text: str) -> dict:
    resp = requests.post(
        f"{TELEGRAM_API_URL}/sendMessage",
        data={"chat_id": chat_id, "text": text}
    )
    return resp.json() if resp.status_code == 200 else {}


def edit_message(chat_id: int, message_id: int, text: str) -> dict:
    """Edit an existing message in place."""
    resp = requests.post(
        f"{TELEGRAM_API_URL}/editMessageText",
        data={"chat_id": chat_id, "message_id": message_id, "text": text}
    )
    return resp.json() if resp.status_code == 200 else {}


def check_rate_limit(chat_id: int) -> bool:
    """Return True if the request should be rejected (rate limited)."""
    now = time.time()
    last = _rate_limits.get(chat_id, 0)
    if now - last < RATE_LIMIT_SECONDS:
        return True
    _rate_limits[chat_id] = now
    return False


def parse_command(text: str, entities: list | None = None):
    if entities:
        for entity in entities:
            if entity.get("type") == "bot_command":
                cmd_len = entity["length"]
                command = text[:cmd_len].split("@")[0]
                args = text[cmd_len:].strip()
                return command, args
    if text.startswith("/"):
        parts = text.split(" ", 1)
        command = parts[0].split("@")[0]
        args = parts[1].strip() if len(parts) > 1 else ""
        return command, args
    return None, text


def _process_tts(chat_id: int, args: str):
    """Core TTS processing logic — called from the webhook handler."""
    # Send initial progress message and capture its message_id
    resp = send_message(chat_id, "📥 Downloading...")
    progress_msg_id = resp.get("result", {}).get("message_id")

    if not progress_msg_id:
        # Fallback: can't edit messages, use send-only approach
        progress_msg_id = None

    input_file = None
    script_txt = None
    audio_file = None

    try:
        caption = None
        metadata = None

        # Step 1: If it's an Instagram URL, try to extract metadata first
        if args.startswith("http://") or args.startswith("https://"):
            try:
                metadata = extract_instagram_metadata(args)
                caption = metadata.get("caption")
            except RuntimeError:
                # yt-dlp failed — fall back to audio download
                if progress_msg_id:
                    edit_message(chat_id, progress_msg_id, "⚠️ Couldn't extract post metadata, trying audio extraction...")

        # Step 2: Get text to translate
        if caption:
            # Use caption directly — no audio download needed
            if progress_msg_id:
                edit_message(chat_id, progress_msg_id, "🌐 Translating...")
            malayalam_text = translate_text(caption)
        elif args.startswith("http://") or args.startswith("https://"):
            # Download audio and transcribe
            if progress_msg_id:
                edit_message(chat_id, progress_msg_id, "📥 Downloading audio...")
            input_file = download_audio(args)
            if progress_msg_id:
                edit_message(chat_id, progress_msg_id, "🌐 Transcribing and translating...")
            malayalam_text = transcribe_audio(input_file)
        else:
            # Plain text input
            if progress_msg_id:
                edit_message(chat_id, progress_msg_id, "🌐 Translating...")
            with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False, encoding="utf-8") as tmp:
                tmp.write(args)
                input_file = tmp.name
            malayalam_text = translate_text(args)

        if not malayalam_text:
            if progress_msg_id:
                edit_message(chat_id, progress_msg_id, "❌ Translation service is busy. Please try again.")
            else:
                send_message(chat_id, "❌ Translation service is busy. Please try again.")
            return

        # Step 3: Send translated text message
        author = metadata.get("author", "") if metadata else ""
        content_type = metadata.get("content_type", "") if metadata else ""

        if metadata and caption:
            header = f"📱 Instagram {content_type} by @{author}" if author else f"📱 Instagram {content_type}"
            text_message = f"{header}\n\n{malayalam_text}"
        elif metadata:
            text_message = f"🎙️ Audio transcribed and translated\n\n{malayalam_text}"
        else:
            text_message = malayalam_text

        # Split long messages if needed
        max_len = 4000
        chunks = [text_message[i:i+max_len] for i in range(0, len(text_message), max_len)]
        for chunk in chunks:
            send_message(chat_id, chunk)

        # Step 4: Generate audio
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, "🎙️ Generating audio...")

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            audio_file = tmp.name

        communicate = edge_tts.Communicate(malayalam_text, VOICE)
        import asyncio
        asyncio.get_event_loop().run_until_complete(communicate.save(audio_file))

        with open(audio_file, "rb") as f:
            requests.post(
                f"{TELEGRAM_API_URL}/sendVoice",
                data={"chat_id": chat_id},
                files={"voice": f}
            )

        # Clean up progress message
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, "✅ Done!")

    except Exception as e:
        error_msg = str(e)
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, f"❌ Error: {error_msg}")
        else:
            send_message(chat_id, f"❌ Error: {error_msg}")
    finally:
        for f in (input_file, script_txt, audio_file):
            if f and os.path.exists(f):
                os.remove(f)


@app.post("/telegram")
async def telegram(request: Request):
    data = await request.json()
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if not chat_id or not text:
        return {"status": "ignored"}

    # Treat bare URLs as /tts commands
    if text.startswith("http://") or text.startswith("https://"):
        command = "/tts"
        args = text
    else:
        entities = message.get("entities", [])
        command, args = parse_command(text, entities)

    if command == "/start":
        send_message(chat_id, "Send /tts your text\nExample: /tts Hello world\n\nPaste any Instagram link to translate it to Malayalam.")
        return {"status": "success"}

    if command == "/tts":
        if not args:
            send_message(chat_id, "Usage: /tts Hello world\nor: /tts https://instagram.com/...")
            return {"status": "success"}

        if check_rate_limit(chat_id):
            send_message(chat_id, "⏳ Please wait before sending another request.")
            return {"status": "success"}

        _process_tts(chat_id, args)
        return {"status": "success"}

    send_message(chat_id, "Unknown command. Try /start or /tts Hello")
    return {"status": "success"}
```

- [ ] **Step 3: Verify imports and syntax**

```bash
cd /home/firoz/Desktop/MalayalamTTS_bot && python -c "import main; print('OK')"
```

Expected: Prints "OK" (may show FastAPI startup warning but no import errors).

- [ ] **Step 4: Commit**

```bash
cd /home/firoz/Desktop/MalayalamTTS_bot && git add main.py && git commit -m "feat: text-first delivery with progress messages and Instagram metadata"
```

---

### Task 4: Update prompt.txt and /start message

**Files:**
- Modify: `prompt.txt`

- [ ] **Step 1: Update prompt.txt**

Replace contents of `prompt.txt`:

```
## Persona
You are a professional translator specializing in converting content to Malayalam.

## Task
Detect the source language of the input and translate it to Malayalam.
If the input is audio, transcribe it first and then translate to Malayalam.
If the input is text, translate it directly to Malayalam.

## Rules
- Preserve emojis, line breaks, and natural formatting
- If the text contains mixed languages, translate everything to Malayalam
- For Islamic content, expand honorifics into full Malayalam form:
  - Prophet (ﷺ/SAW/PBUH) → നബി സല്ലല്ലാഹു അലൈഹി വസല്ലം
  - Allah (SWT) → അല്ലാഹു സുബ്ഹാനഹു വതആലാ
- Convert numerals to Malayalam words
- Write foreign names phonetically in Malayalam script

## Format
Output **only** the translated Malayalam text. Never include:
- Introductions or explanations
- Source language identification
- English text or annotations
- Section headers or notes
```

- [ ] **Step 2: Commit**

```bash
cd /home/firoz/Desktop/MalayalamTTS_bot && git add prompt.txt && git commit -m "update: simplify prompt for text-first translation pipeline"
```

---

### Task 5: Integration verification

**Files:**
- No file changes

- [ ] **Step 1: Run full syntax check**

```bash
cd /home/firoz/Desktop/MalayalamTTS_bot && python -c "
from instagram import extract_instagram_metadata, _parse_yt_dlp_info
from translate import translate_text, transcribe_audio, download_audio, convert_to_audiobook_script
import main
print('All imports successful')
"
```

Expected: "All imports successful"

- [ ] **Step 2: Run unit tests**

```bash
cd /home/firoz/Desktop/MalayalamTTS_bot && python -m pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 3: Final commit**

```bash
cd /home/firoz/Desktop/MalayalamTTS_bot && git status && git log --oneline -5
```

Verify all changes are committed and the last 5 commits show the progression.
