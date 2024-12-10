import os
import requests
from fastapi import FastAPI, HTTPException

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
    data = await request.json()
    print(data)  # This will print the request payload to the console
    return {"status": "success", "message": "Data received"}


@app.post("/sendMessage")
async def send_message(chat_id: str, text: str):
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
