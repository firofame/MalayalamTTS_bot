import os
import tempfile
import requests
from fastapi import FastAPI, Request
from translate import convert_to_audiobook_script, download_audio
from tts import init_browser, close_browser, generate_tts

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


@app.on_event("startup")
async def startup():
    requests.post(
        f"{TELEGRAM_API_URL}/setMyCommands",
        json={
            "commands": [
                {"command": "start", "description": "Start the bot"},
                {"command": "tts", "description": "Translate and convert text to speech"},
            ]
        }
    )
    await init_browser()


@app.on_event("shutdown")
async def shutdown():
    await close_browser()


def send_message(chat_id: int, text: str):
    requests.post(
        f"{TELEGRAM_API_URL}/sendMessage",
        data={"chat_id": chat_id, "text": text}
    )


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
        send_message(chat_id, "Send /tts your text\nExample: /tts Hello world\nor share a URL directly")
        return {"status": "success"}

    if command == "/tts":
        if not args:
            send_message(chat_id, "Usage: /tts Hello world\nor: /tts https://youtube.com/...")
            return {"status": "success"}

        send_message(chat_id, "Processing...")

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            audio_file = tmp.name
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            script_txt = tmp.name
        input_file = None

        try:
            if args.startswith("http://") or args.startswith("https://"):
                input_file = download_audio(args)
            else:
                with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False, encoding="utf-8") as tmp:
                    tmp.write(args)
                    input_file = tmp.name

            convert_to_audiobook_script(input_file, script_txt)
            script = open(script_txt, encoding="utf-8").read().strip()

            if not script:
                send_message(chat_id, "Translation failed. Please try again.")
                return {"status": "error"}

            from pathlib import Path
            await generate_tts(script, Path(audio_file))

            with open(audio_file, "rb") as f:
                response = requests.post(
                    f"{TELEGRAM_API_URL}/sendVoice",
                    data={"chat_id": chat_id},
                    files={"voice": f}
                )
        except Exception as e:
            send_message(chat_id, f"Error: {e}")
            return {"status": "error"}
        finally:
            for f in (input_file, script_txt, audio_file):
                if f and os.path.exists(f):
                    os.remove(f)

        if response.status_code == 200:
            return {"status": "success"}
        return {"status": "error", "message": response.text}

    send_message(chat_id, "Unknown command. Try /start or /tts Hello")
    return {"status": "success"}
