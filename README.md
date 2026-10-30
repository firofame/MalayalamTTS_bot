# MalayalamTTS_bot

Telegram-only bot that converts text directly to speech without translation.

**Bot:** [t.me/MalayalamTTS_bot](https://t.me/MalayalamTTS_bot)

## Usage

Send any of these to the bot:

- **Plain text** — converts directly to speech
- `/tts <text>` — convert text to speech

Link extraction and downloading are not supported. Links are treated as plain
text, not fetched. Text input is limited to 4,000 characters, with a
30-second cooldown between speech requests per chat.

Local file input is not supported. File paths in messages are treated as plain text.

### How it works

| Input | Process |
|-------|---------|
| Plain text | Reads the supplied text aloud |
| `/tts <text>` | Reads the supplied text aloud |

The bot sends the original text as a plain-text caption on the voice message,
truncated to Telegram's 1,024-character caption limit.

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Start the bot |
| `/tts` | Convert text to speech |

## Deploy

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Set the `BOT_TOKEN` environment variable. No Google API key is required.

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
main.py             — FastAPI lifecycle, webhook routing, and input validation
telegram_client.py  — Telegram Bot API calls using the shared HTTP client
tts.py              — Text-to-speech workflow, progress, and audio cleanup
```

App startup creates a shared Telegram client, which is passed to the webhook's
background TTS tasks. The HTTP connection pool is closed during app shutdown.

## Tests

After installing dependencies, run the regression tests without contacting
external services:

```bash
python3 -m unittest discover -s tests
```

## Tech Stack

- **FastAPI** — webhook server
- **Edge TTS** — text-to-speech
- **Telegram Bot API** — messaging
