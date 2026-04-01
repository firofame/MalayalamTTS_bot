import os
import subprocess
import tempfile
import time
import asyncio
import requests
import edge_tts
from fastapi import FastAPI, Request
from translate import convert_to_malayalam

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
VOICE = "en-US-AvaMultilingualNeural"

# Simple rate limiting: chat_id -> last_request_time
_rate_limits = {}
RATE_LIMIT_SECONDS = 30
_MAX_RATE_LIMIT_ENTRIES = 1000


def _cleanup_rate_limits():
    """Remove oldest entries if the dict grows too large."""
    if len(_rate_limits) > _MAX_RATE_LIMIT_ENTRIES:
        # Keep only the 500 most recent entries
        sorted_items = sorted(_rate_limits.items(), key=lambda x: x[1], reverse=True)
        _rate_limits.clear()
        _rate_limits.update(sorted_items[:500])


@app.on_event("startup")
def setup_commands():
    # Verify yt-dlp is available
    try:
        result = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, timeout=5)
        print(f"yt-dlp version: {result.stdout.strip()}")
    except Exception as e:
        print(f"WARNING: yt-dlp not found: {e}")

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
    _cleanup_rate_limits()
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


def _run_tts_sync(chat_id: int, args: str):
    """Core TTS processing logic — runs in a background thread.

    This function is synchronous and blocks its thread, but not the
    FastAPI event loop.  All Telegram API calls here use the blocking
    ``requests`` library, which is fine inside a dedicated thread.
    """
    resp = send_message(chat_id, "📥 Downloading...")
    progress_msg_id = resp.get("result", {}).get("message_id") or None

    audio_file = None

    try:
        if progress_msg_id:
            status = "📥 Downloading and translating..." if args.startswith("http") else "🌐 Translating..."
            edit_message(chat_id, progress_msg_id, status)

        malayalam_text = convert_to_malayalam(args)

        if not malayalam_text:
            if progress_msg_id:
                edit_message(chat_id, progress_msg_id, "❌ Translation service is busy. Please try again.")
            else:
                send_message(chat_id, "❌ Translation service is busy. Please try again.")
            return

        max_len = 4000
        chunks = [malayalam_text[i:i+max_len] for i in range(0, len(malayalam_text), max_len)]
        for chunk in chunks:
            send_message(chat_id, chunk)

        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, "🎙️ Generating audio...")

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            audio_file = tmp.name

        communicate = edge_tts.Communicate(malayalam_text, VOICE)
        asyncio.run(communicate.save(audio_file))

        with open(audio_file, "rb") as f:
            voice_resp = requests.post(
                f"{TELEGRAM_API_URL}/sendVoice",
                data={"chat_id": chat_id},
                files={"voice": f},
            )
        if voice_resp.status_code != 200:
            if progress_msg_id:
                edit_message(chat_id, progress_msg_id, f"❌ Audio send failed: {voice_resp.text}")
            return

        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, "✅ Done!")

    except Exception as e:
        error_msg = str(e)
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, f"❌ Error: {error_msg}")
        else:
            send_message(chat_id, f"❌ Error: {error_msg}")
    finally:
        if audio_file and os.path.exists(audio_file):
            os.remove(audio_file)


@app.post("/telegram")
async def telegram(request: Request):
    data = await request.json()
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if not chat_id or not text:
        return {"status": "ignored"}

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

        await asyncio.to_thread(_run_tts_sync, chat_id, args)
        return {"status": "success"}

    send_message(chat_id, "Unknown command. Try /start or /tts Hello")
    return {"status": "success"}
