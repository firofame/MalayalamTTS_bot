# MalayalamTTS_bot

Telegram bot that translates Instagram content to Malayalam speech and text.

**Bot:** [t.me/MalayalamTTS_bot](https://t.me/MalayalamTTS_bot)

## Usage

Send any of these to the bot:

- **Instagram link** — extracts audio, transcribes, and translates to Malayalam
- **Plain text** — translates directly to Malayalam
- `/tts <text>` — translate text to Malayalam speech

### How it works

| Input | Process |
|-------|---------|
| Static image post | Translates caption instantly |
| Reel / Story / Video | Downloads audio → transcribes → translates |
| Plain text | Translates directly |

The bot sends the Malayalam translation as a caption on the voice message.

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Start the bot |
| `/tts` | Translate text to Malayalam speech |

## Deploy

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Set `BOT_TOKEN` and `GOOGLE_API_KEY` environment variables.

## Project Structure

```
main.py            — FastAPI Telegram webhook handler
translate.py       — Translation pipeline (text/audio → Malayalam)
download_audio.py  — Audio download via yt-dlp
prompt.txt         — Gemini system prompt
```

## Tech Stack

- **FastAPI** — webhook server
- **yt-dlp** — Instagram content extraction
- **Google Gemini** — transcription and translation
- **Edge TTS** — text-to-speech
- **Telegram Bot API** — messaging
