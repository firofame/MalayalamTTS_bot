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
    resp = send_message(chat_id, "📥 Downloading...")
    progress_msg_id = resp.get("result", {}).get("message_id")

    if not progress_msg_id:
        progress_msg_id = None

    input_file = None
    script_txt = None
    audio_file = None

    try:
        caption = None
        metadata = None

        if args.startswith("http://") or args.startswith("https://"):
            try:
                metadata = extract_instagram_metadata(args)
                caption = metadata.get("caption")
            except RuntimeError:
                if progress_msg_id:
                    edit_message(chat_id, progress_msg_id, "⚠️ Couldn't extract post metadata, trying audio extraction...")

        if caption:
            if progress_msg_id:
                edit_message(chat_id, progress_msg_id, "🌐 Translating...")
            malayalam_text = translate_text(caption)
        elif args.startswith("http://") or args.startswith("https://"):
            if progress_msg_id:
                edit_message(chat_id, progress_msg_id, "📥 Downloading audio...")
            input_file = download_audio(args)
            if progress_msg_id:
                edit_message(chat_id, progress_msg_id, "🌐 Transcribing and translating...")
            malayalam_text = transcribe_audio(input_file)
        else:
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

        author = metadata.get("author", "") if metadata else ""
        content_type = metadata.get("content_type", "") if metadata else ""

        if metadata and caption:
            header = f"📱 Instagram {content_type} by @{author}" if author else f"📱 Instagram {content_type}"
            text_message = f"{header}\n\n{malayalam_text}"
        elif metadata:
            text_message = f"🎙️ Audio transcribed and translated\n\n{malayalam_text}"
        else:
            text_message = malayalam_text

        max_len = 4000
        chunks = [text_message[i:i+max_len] for i in range(0, len(text_message), max_len)]
        for chunk in chunks:
            send_message(chat_id, chunk)

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
