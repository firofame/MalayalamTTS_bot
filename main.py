import os
import requests
import edge_tts
from fastapi import FastAPI, Request, HTTPException

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set.")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

OUTPUT_FILE = "output_audio.mp3"  # The file where the audio will be saved (MP3 format)
VOICE = "ml-IN-MidhunNeural"  # The voice you want to use (in your case, MidhunNeural)

@app.post("/telegram")
async def telegram(request: Request):
    # Get the incoming data
    data = await request.json()
    print(data)  # Print the request payload to the console

    # Extract chat_id and text from the incoming message
    chat_id = data['message']['chat']['id']
    text = data['message']['text']

    # Call the function to generate the voice message
    await generate_and_send_voice(chat_id, text)

    return {"status": "success", "message": "Voice sent"}


async def generate_and_send_voice(chat_id: str, text: str):
    """
    Generate voice from text and send it as a voice message to Telegram.
    """
    # Generate the voice file (MP3 format)
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(OUTPUT_FILE)
    
    # Send the generated voice file via Telegram
    send_message_url = f"{TELEGRAM_API_URL}/sendVoice"
    
    with open(OUTPUT_FILE, 'rb') as audio_file:
        payload = {
            "chat_id": chat_id,
        }
        files = {
            "voice": audio_file,
        }
        
        response = requests.post(send_message_url, data=payload, files=files)
        
        if response.status_code == 200:
            print("Voice message sent successfully.")
        else:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Failed to send voice message: {response.text}",
            )
