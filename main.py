import asyncio
import html
import logging
import os
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import edge_tts
import requests
from fastapi import FastAPI, Request, BackgroundTasks
from translate import convert_to_malayalam

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot.main")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable is not set. "
        "Set it to your Telegram bot token before starting."
    )

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
VOICE = "en-US-AvaMultilingualNeural"

# Rate limiting
_rate_limits: dict[int, float] = {}
_rate_lock = threading.Lock()
RATE_LIMIT_SECONDS = 30
_MAX_RATE_LIMIT_ENTRIES = 1000

# Input limits
MAX_TEXT_LENGTH = 4000


def _cleanup_rate_limits() -> None:
    """Remove oldest entries if the dict grows too large. Caller must hold _rate_lock."""
    if len(_rate_limits) > _MAX_RATE_LIMIT_ENTRIES:
        sorted_items = sorted(_rate_limits.items(), key=lambda x: x[1], reverse=True)
        _rate_limits.clear()
        _rate_limits.update(sorted_items[:500])


def _is_valid_url(text: str) -> bool:
    """Check if text is a valid HTTP/HTTPS URL."""
    try:
        parsed = urlparse(text)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def _sanitize_text(text: str) -> str:
    """Strip control characters and limit length."""
    # Remove control characters except newline and tab
    cleaned = "".join(
        ch for ch in text
        if ch in ("\n", "\t") or (ord(ch) >= 32)
    )
    return cleaned.strip()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    # Startup: register bot commands
    try:
        resp = requests.post(
            f"{TELEGRAM_API_URL}/setMyCommands",
            json={
                "commands": [
                    {"command": "start", "description": "Start the bot"},
                    {"command": "tts", "description": "Translate and convert text to Malayalam speech"},
                ]
            },
            timeout=10,
        )
        logger.info("Bot commands registered: %s", resp.status_code == 200)
    except Exception:
        logger.error("Failed to register bot commands", exc_info=True)
    yield
    # Shutdown: nothing to clean up


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root():
    return {
        "status": "running",
        "bot": "Malayalam TTS Bot",
        "description": "Translates text and audio to Malayalam speech",
        "webhook": "/telegram"
    }


def send_message(chat_id: int, text: str, reply_to_message_id: int | None = None, parse_mode: str | None = None) -> dict:
    data = {"chat_id": chat_id, "text": text}
    if reply_to_message_id:
        data["reply_to_message_id"] = reply_to_message_id
    if parse_mode:
        data["parse_mode"] = parse_mode
    resp = requests.post(
        f"{TELEGRAM_API_URL}/sendMessage",
        data=data,
        timeout=10,
    )
    if resp.status_code != 200:
        logger.error("sendMessage failed (%s): %s", resp.status_code, resp.text[:200])
        return {}
    return resp.json()


def edit_message(chat_id: int, message_id: int, text: str, parse_mode: str | None = None) -> dict:
    """Edit an existing message in place."""
    data = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode:
        data["parse_mode"] = parse_mode
    resp = requests.post(
        f"{TELEGRAM_API_URL}/editMessageText",
        data=data,
        timeout=10,
    )
    if resp.status_code != 200:
        logger.error("editMessage failed (%s): %s", resp.status_code, resp.text[:200])
        return {}
    return resp.json()


def send_chat_action(chat_id: int, action: str) -> None:
    """Send a chat action (typing, record_voice, etc.) to show activity."""
    resp = requests.post(
        f"{TELEGRAM_API_URL}/sendChatAction",
        data={"chat_id": chat_id, "action": action},
        timeout=10,
    )
    if resp.status_code != 200:
        logger.error("sendChatAction failed (%s): %s", resp.status_code, resp.text[:200])


def _chat_action_loop(chat_id: int, action: str, stop_event: threading.Event) -> None:
    """Send chat action every 4 seconds until stopped."""
    while not stop_event.is_set():
        try:
            send_chat_action(chat_id, action)
        except Exception:
            logger.error("Error sending chat action", exc_info=True)
        stop_event.wait(4)


def check_rate_limit(chat_id: int) -> bool:
    """Return True if the request should be rejected (rate limited)."""
    now = time.time()
    with _rate_lock:
        last = _rate_limits.get(chat_id, 0)
        if now - last < RATE_LIMIT_SECONDS:
            logger.warning("Rate limited chat_id: %s", chat_id)
            return True
        _rate_limits[chat_id] = now
        _cleanup_rate_limits()
    return False


def parse_command(text: str, entities: list | None = None) -> tuple[str | None, str]:
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


def _run_tts_sync(chat_id: int, args: str, reply_to_message_id: int | None = None) -> None:
    """Core TTS processing logic — runs in a background thread.

    This function is synchronous and blocks its thread, but not the
    FastAPI event loop.  All Telegram API calls here use the blocking
    ``requests`` library, which is fine inside a dedicated thread.
    """
    is_url = _is_valid_url(args)
    logger.info("TTS started: chat_id=%s, args_len=%d, is_url=%s", chat_id, len(args), is_url)

    resp = send_message(chat_id, "📥 Downloading...", reply_to_message_id=reply_to_message_id)
    progress_msg_id = resp.get("result", {}).get("message_id") or None

    audio_file = None
    action_stop = threading.Event()
    action_thread = None

    try:
        action_thread = threading.Thread(
            target=_chat_action_loop, args=(chat_id, "typing", action_stop), daemon=True
        )
        action_thread.start()

        if progress_msg_id:
            status = "📥 Downloading and translating..." if is_url else "🌐 Translating..."
            edit_message(chat_id, progress_msg_id, status)

        malayalam_text = convert_to_malayalam(args)
        logger.info("Translation complete: chat_id=%s, text_len=%d", chat_id, len(malayalam_text))

        if not malayalam_text:
            if progress_msg_id:
                edit_message(chat_id, progress_msg_id, "❌ Translation service is busy. Please try again.")
            else:
                send_message(chat_id, "❌ Translation service is busy. Please try again.", reply_to_message_id=reply_to_message_id)
            return

        # Stop typing action, start recording action
        action_stop.set()
        if action_thread:
            action_thread.join(timeout=5)
        action_stop = threading.Event()
        action_thread = threading.Thread(
            target=_chat_action_loop, args=(chat_id, "record_voice", action_stop), daemon=True
        )
        action_thread.start()

        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, "🎙️ Generating audio...")

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            audio_file = tmp.name

        communicate = edge_tts.Communicate(malayalam_text, VOICE)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(communicate.save(audio_file))
        finally:
            loop.close()
        logger.info("Audio generated: chat_id=%s", chat_id)

        with open(audio_file, "rb") as f:
            voice_data = {
                "chat_id": chat_id,
                "caption": malayalam_text[:1024],
                "parse_mode": "HTML",
            }
            if reply_to_message_id:
                voice_data["reply_to_message_id"] = reply_to_message_id
            voice_resp = requests.post(
                f"{TELEGRAM_API_URL}/sendVoice",
                data=voice_data,
                files={"voice": f},
                timeout=60,
            )
        if voice_resp.status_code != 200:
            logger.error("sendVoice failed (%s): %s", voice_resp.status_code, voice_resp.text[:200])
            if progress_msg_id:
                edit_message(chat_id, progress_msg_id, f"❌ Audio send failed: {voice_resp.text}")
            return

        logger.info("Voice sent: chat_id=%s", chat_id)
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, "✅ Done!")

    except Exception as e:
        error_msg = html.escape(str(e))  # Prevent HTML injection
        logger.error("TTS failed: chat_id=%s, error=%s", chat_id, error_msg)
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, f"<b>❌ Error:</b> {error_msg}", parse_mode="HTML")
        else:
            send_message(chat_id, f"<b>❌ Error:</b> {error_msg}", reply_to_message_id=reply_to_message_id, parse_mode="HTML")
    finally:
        action_stop.set()
        if action_thread:
            action_thread.join(timeout=5)
        if audio_file and os.path.exists(audio_file):
            os.remove(audio_file)
        # Clean up downloaded source audio files from yt-dlp
        if is_url:
            _cleanup_downloads()
        logger.info("TTS complete: chat_id=%s", chat_id)


def _cleanup_downloads() -> None:
    """Remove downloaded audio files older than 1 hour from the downloads directory."""
    from pathlib import Path
    downloads = Path("downloads")
    if not downloads.exists():
        return
    cutoff = time.time() - 3600  # 1 hour
    for f in downloads.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                logger.debug("Cleaned up old download: %s", f)
        except OSError:
            pass


@app.post("/telegram")
async def telegram(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if not chat_id or not text:
        logger.debug("Ignoring message: chat_id=%s, text=%s", chat_id, bool(text))
        return {"status": "ignored"}

    # Sanitize input
    text = _sanitize_text(text)
    if not text:
        return {"status": "ignored"}

    logger.info("Received message from chat_id=%s: %s", chat_id, text[:100])

    if _is_valid_url(text):
        command = "/tts"
        args = text
    else:
        entities = message.get("entities", [])
        command, args = parse_command(text, entities)

    if command == "/start":
        send_message(
            chat_id,
            "<b>👋 Welcome to Malayalam TTS Bot!</b>\n\n"
            "I can translate text and Instagram content to Malayalam speech.\n\n"
            "<b>How to use:</b>\n"
            "• <code>/tts Hello world</code> — translate text to speech\n"
            "• Paste any Instagram/YouTube link — I'll extract audio, transcribe & translate\n"
            "• Send any text — I'll translate it to Malayalam\n\n"
            "Send me anything to get started!",
            reply_to_message_id=message.get("message_id"),
            parse_mode="HTML"
        )
        return {"status": "success"}

    if command == "/tts":
        if not args:
            send_message(
                chat_id,
                "<b>Usage:</b>\n"
                "• <code>/tts Hello world</code> — translate text to Malayalam speech\n"
                "• <code>/tts https://instagram.com/...</code> — extract & translate audio",
                reply_to_message_id=message.get("message_id"),
                parse_mode="HTML"
            )
            return {"status": "success"}

        # Enforce text length limit (URLs are exempt)
        if not _is_valid_url(args) and len(args) > MAX_TEXT_LENGTH:
            send_message(
                chat_id,
                f"<b>⚠️ Text too long.</b> Maximum {MAX_TEXT_LENGTH} characters allowed.",
                reply_to_message_id=message.get("message_id"),
                parse_mode="HTML"
            )
            return {"status": "success"}

        if check_rate_limit(chat_id):
            send_message(
                chat_id,
                "<b>⏳ Please wait</b> before sending another request.",
                reply_to_message_id=message.get("message_id"),
                parse_mode="HTML"
            )
            return {"status": "success"}

        background_tasks.add_task(_run_tts_sync, chat_id, args, message.get("message_id"))
        return {"status": "success"}

    send_message(
        chat_id,
        "<b>Unknown command.</b> Try /start or /tts Hello",
        reply_to_message_id=message.get("message_id"),
        parse_mode="HTML"
    )
    return {"status": "success"}
