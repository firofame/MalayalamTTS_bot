import os
import requests
import edge_tts
from fastapi import FastAPI, Request

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
OUTPUT_FILE = "output_audio.mp3"
VOICE = "en-US-AvaMultilingualNeural"


@app.post("/telegram")
async def telegram(request: Request):
    data = await request.json()
    chat_id = data['message']['chat']['id']
    text = data['message']['text']

    # Generate voice and send via Telegram
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(OUTPUT_FILE)

    with open(OUTPUT_FILE, 'rb') as audio_file:
        response = requests.post(f"{TELEGRAM_API_URL}/sendVoice", data={"chat_id": chat_id}, files={"voice": audio_file})
    
    if response.status_code == 200:
        return {"status": "success"}
    return {"status": "error", "message": response.text}
