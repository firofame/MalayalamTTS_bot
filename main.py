import logging
from typing import Protocol

from telegram_client import TelegramClient
from tts import run_tts

logger = logging.getLogger("bot.main")

RATE_LIMIT_SECONDS = 30
MAX_TEXT_LENGTH = 4000


class RateLimiter(Protocol):
    async def check_rate_limit(self, chat_id: int) -> bool: ...


def _sanitize_text(text: str) -> str:
    """Keep only characters that can be included in Azure SSML."""
    cleaned = "".join(
        ch for ch in text
        if ch in ("\n", "\t")
        or 32 <= ord(ch) <= 0xD7FF
        or 0xE000 <= ord(ch) <= 0xFFFD
        or 0x10000 <= ord(ch) <= 0x10FFFF
    )
    return cleaned.strip()


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


async def handle_update(data: dict, bot: TelegramClient, limiter: RateLimiter) -> dict:
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if not chat_id or not text:
        logger.debug("Ignoring message: chat_id=%s, text=%s", chat_id, bool(text))
        return {"status": "ignored"}

    text = _sanitize_text(text)
    if not text:
        return {"status": "ignored"}

    logger.info("Received message from chat_id=%s", chat_id)

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

        if len(args) > MAX_TEXT_LENGTH:
            await bot.send_message(
                chat_id,
                f"<b>⚠️ Text too long.</b> Maximum {MAX_TEXT_LENGTH} characters allowed.",
                reply_to_message_id=message.get("message_id"),
                parse_mode="HTML"
            )
            return {"status": "success"}

        if await limiter.check_rate_limit(chat_id):
            await bot.send_message(
                chat_id,
                "<b>⏳ Please wait</b> before sending another request.",
                reply_to_message_id=message.get("message_id"),
                parse_mode="HTML"
            )
            return {"status": "success"}

        await run_tts(bot, chat_id, args, message.get("message_id"))
        return {"status": "success"}

    await bot.send_message(
        chat_id,
        "<b>Unknown command.</b> Try /start or /tts Hello",
        reply_to_message_id=message.get("message_id"),
        parse_mode="HTML"
    )
    return {"status": "success"}
