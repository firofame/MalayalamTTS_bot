import os
import tempfile
import requests
import edge_tts
from fastapi import FastAPI, Request

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
VOICE = "en-US-AvaMultilingualNeural"


@app.on_event("startup")
def setup_commands():
    requests.post(
        f"{TELEGRAM_API_URL}/setMyCommands",
        json={
            "commands": [
                {"command": "start", "description": "Start the bot"},
                {"command": "tts", "description": "Convert text to speech"},
            ]
        }
    )


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

    entities = message.get("entities", [])
    command, args = parse_command(text, entities)

    if command == "/start":
        send_message(chat_id, "Send /tts your text\nExample: /tts Hello world")
        return {"status": "success"}

    if command == "/tts":
        if not args:
            send_message(chat_id, "Usage: /tts Hello world")
            return {"status": "success"}

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            output_file = tmp.name

        try:
            communicate = edge_tts.Communicate(args, VOICE)
            await communicate.save(output_file)

            with open(output_file, "rb") as audio_file:
                response = requests.post(
                    f"{TELEGRAM_API_URL}/sendVoice",
                    data={"chat_id": chat_id},
                    files={"voice": audio_file}
                )
        finally:
            os.remove(output_file)

        if response.status_code == 200:
            return {"status": "success"}
        return {"status": "error", "message": response.text}

    send_message(chat_id, "Unknown command. Try /start or /tts Hello")
    return {"status": "success"}
