import logging
import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, BackgroundTasks

from telegram_client import TelegramClient
from tts import run_tts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot.main")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable is not set. "
        "Set it to your Telegram bot token before starting."
    )

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Rate limiting
_rate_limits: dict[int, float] = {}
RATE_LIMIT_SECONDS = 30
_MAX_RATE_LIMIT_ENTRIES = 1000

# Input limits
MAX_TEXT_LENGTH = 4000

def _cleanup_rate_limits() -> None:
    """Remove oldest entries if the dict grows too large."""
    if len(_rate_limits) > _MAX_RATE_LIMIT_ENTRIES:
        sorted_items = sorted(_rate_limits.items(), key=lambda x: x[1], reverse=True)
        _rate_limits.clear()
        _rate_limits.update(sorted_items[:500])


def _sanitize_text(text: str) -> str:
    """Strip control characters and limit length."""
    # Remove control characters except newline and tab
    cleaned = "".join(
        ch for ch in text
        if ch in ("\n", "\t") or (ord(ch) >= 32)
    )
    return cleaned.strip()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    async with httpx.AsyncClient(timeout=10.0) as http_client:
        bot = TelegramClient(http_client, TELEGRAM_API_URL)
        app.state.telegram_client = bot
        await bot.register_commands()
        yield


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root():
    return {
        "status": "running",
        "bot": "Malayalam TTS Bot",
        "description": "Converts text to speech",
        "webhook": "/telegram"
    }


def check_rate_limit(chat_id: int) -> bool:
    """Return True if the request should be rejected (rate limited)."""
    now = time.time()
    last = _rate_limits.get(chat_id, 0)
    if now - last < RATE_LIMIT_SECONDS:
        logger.warning("Rate limited chat_id: %s", chat_id)
        return True
    _rate_limits[chat_id] = now
    _cleanup_rate_limits()
    return False


def parse_command(text: str, entities: list | None = None) -> tuple[str | None, str]:
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
async def telegram(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if not chat_id or not text:
        logger.debug("Ignoring message: chat_id=%s, text=%s", chat_id, bool(text))
        return {"status": "ignored"}

    # Sanitize input
    text = _sanitize_text(text)
    if not text:
        return {"status": "ignored"}

    bot: TelegramClient = request.app.state.telegram_client
    logger.info("Received message from chat_id=%s: %s", chat_id, text[:100])

    entities = message.get("entities", [])
    command, args = parse_command(text, entities)
    if command is None:
        command = "/tts"

    if command == "/start":
        await bot.send_message(
            chat_id,
            "<b>👋 Welcome to Malayalam TTS Bot!</b>\n\n"
            "I can convert your text directly to speech.\n\n"
            "<b>How to use:</b>\n"
            "• <code>/tts Hello world</code> — convert text to speech\n"
            "• Send any text — I'll read it aloud\n\n"
            "Send me some text to get started!",
            reply_to_message_id=message.get("message_id"),
            parse_mode="HTML"
        )
        return {"status": "success"}

    if command == "/tts":
        if not args:
            await bot.send_message(
                chat_id,
                "<b>Usage:</b>\n"
                "• <code>/tts Hello world</code> — convert text to speech",
                reply_to_message_id=message.get("message_id"),
                parse_mode="HTML"
            )
            return {"status": "success"}

        # Enforce text length limit
        if len(args) > MAX_TEXT_LENGTH:
            await bot.send_message(
                chat_id,
                f"<b>⚠️ Text too long.</b> Maximum {MAX_TEXT_LENGTH} characters allowed.",
                reply_to_message_id=message.get("message_id"),
                parse_mode="HTML"
            )
            return {"status": "success"}

        if check_rate_limit(chat_id):
            await bot.send_message(
                chat_id,
                "<b>⏳ Please wait</b> before sending another request.",
                reply_to_message_id=message.get("message_id"),
                parse_mode="HTML"
            )
            return {"status": "success"}

        background_tasks.add_task(run_tts, bot, chat_id, args, message.get("message_id"))
        return {"status": "success"}

    await bot.send_message(
        chat_id,
        "<b>Unknown command.</b> Try /start or /tts Hello",
        reply_to_message_id=message.get("message_id"),
        parse_mode="HTML"
    )
    return {"status": "success"}
