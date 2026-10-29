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

### Speech synthesis returns HTTP 403

An error containing `Sec-MS-GEC-Version=1-...` indicates the old Edge speech
authentication protocol. Use `edge-tts>=7.3.1,<7.4`, which supports v2 tokens
and includes a server-date parsing fix.

For an existing installation, upgrade dependencies and restart the bot:

```bash
python -m pip install --upgrade -r requirements.txt
python -m pip show edge-tts
```

On Render, redeploy after updating the requirements. If the old version is
still installed, use **Clear build cache & deploy**. If 403 errors persist
with the updated package, check the server's UTC clock and whether the
hosting network can reach Microsoft's speech service.

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
- **Google Gemini 3.5 Flash-Lite** (`gemini-3.5-flash-lite`) — transcription and translation with medium thinking
- **Edge TTS** — text-to-speech
- **Telegram Bot API** — messaging
