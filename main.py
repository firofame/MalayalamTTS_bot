import os
import requests
from fastapi import FastAPI, Request, HTTPException
import edge_tts

VOICE = "ml-IN-MidhunNeural"
OUTPUT_FILE = "test.mp3"

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set.")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.post("/telegram")
async def telegram(request: Request):
    # Get the incoming data
    data = await request.json()
    print(data)  # Print the request payload to the console

    # Extract chat_id and text from the incoming message
    chat_id = data['message']['chat']['id']
    text = data['message']['text']

    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(OUTPUT_FILE)

    # Call the send_message function to echo the text back
    return await send_message(chat_id=chat_id, text=text)


@app.post("/sendMessage")
async def send_message(chat_id: str, text: str):
    """
    Endpoint to send a message via Telegram bot.
    
    Parameters:
    - chat_id: Unique identifier for the target chat or channel username.
    - text: Message text to be sent.
    """
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN is not set.")
    
    send_message_url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    
    response = requests.post(send_message_url, json=payload)
    if response.status_code == 200:
        return {"status": "success", "data": response.json()}
    else:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Failed to send message: {response.text}",
        )
