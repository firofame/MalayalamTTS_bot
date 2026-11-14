import html
import logging
from typing import Protocol

from article import MAX_ARTICLE_LENGTH, ArticleError, extract, is_url
from telegram_client import TelegramClient, TelegramError
from tts import run_tts, sanitize_text

logger = logging.getLogger("bot.main")

RATE_LIMIT_SECONDS = 30
MAX_TEXT_LENGTH = 4000


class RateLimiter(Protocol):
    async def check_rate_limit(self, chat_id: int) -> bool: ...


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


async def _read_article(
    bot: TelegramClient, chat_id: int, url: str, reply_to_message_id: int | None,
    limiter: RateLimiter,
) -> None:
    if await limiter.check_rate_limit(chat_id):
        await bot.send_message(
            chat_id,
            "<b>⏳ Please wait</b> before sending another request.",
            reply_to_message_id=reply_to_message_id,
            parse_mode="HTML"
        )
        return

    logger.info("Article TTS started: chat_id=%s", chat_id)
    resp = await bot.send_message(chat_id, "📰 Reading article...", reply_to_message_id=reply_to_message_id)
    progress_msg_id = resp.get("result", {}).get("message_id") or None

    try:
        title, content, truncated = await extract(bot.http_client, url)
    except ArticleError as e:
        error_msg = html.escape(str(e))
        logger.error("Article fetch failed: chat_id=%s, error=%s", chat_id, error_msg)
        if progress_msg_id:
            await bot.edit_message(chat_id, progress_msg_id, f"<b>❌ Error:</b> {error_msg}", parse_mode="HTML")
        else:
            await bot.send_message(chat_id, f"<b>❌ Error:</b> {error_msg}", reply_to_message_id=reply_to_message_id, parse_mode="HTML")
        return

    caption = f"{title}\n\nSource: {url}" if title else f"Source: {url}"
    if truncated:
        caption += f"\n\n⚠️ Truncated at {MAX_ARTICLE_LENGTH:,} characters."

    if progress_msg_id:
        try:
            await bot.edit_message(chat_id, progress_msg_id, "🎙️ Generating audio...")
        except TelegramError:
            logger.warning("Could not update progress notice for chat_id=%s", chat_id)

    await run_tts(bot, chat_id, content, caption, reply_to_message_id, progress_msg_id)


async def handle_update(data: dict, bot: TelegramClient, limiter: RateLimiter) -> dict:
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if not chat_id or not text:
        logger.debug("Ignoring message: chat_id=%s, text=%s", chat_id, bool(text))
        return {"status": "ignored"}

    text = sanitize_text(text)
    if not text:
        return {"status": "ignored"}

    logger.info("Received message from chat_id=%s", chat_id)

    entities = message.get("entities", [])
    command, args = parse_command(text, entities)

    if command == "/start":
        await bot.send_message(
            chat_id,
            "<b>👋 Welcome to Malayalam TTS Bot!</b>\n\n"
            "I can convert your text directly to speech.\n\n"
            "<b>How to use:</b>\n"
            "• <code>/tts Hello world</code> — convert text to speech\n"
            "• <code>/url https://example.com/post</code> — read a blog post as audio\n"
            "• Send any text — I'll read it aloud\n"
            "• Send a link — I'll read the article\n\n"
            "Send me some text to get started!",
            reply_to_message_id=message.get("message_id"),
            parse_mode="HTML"
        )
        return {"status": "success"}

    if command == "/url":
        if not is_url(args):
            await bot.send_message(
                chat_id,
                "<b>Usage:</b>\n"
                "• <code>/url https://example.com/post</code> — read a blog post as audio",
                reply_to_message_id=message.get("message_id"),
                parse_mode="HTML"
            )
            return {"status": "success"}
        await _read_article(bot, chat_id, args, message.get("message_id"), limiter)
        return {"status": "success"}

    if command is None and is_url(text):
        await _read_article(bot, chat_id, text, message.get("message_id"), limiter)
        return {"status": "success"}

    if command is None:
        command = "/tts"

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

        if is_url(args):
            await _read_article(bot, chat_id, args, message.get("message_id"), limiter)
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

        await run_tts(bot, chat_id, args, args, message.get("message_id"))
        return {"status": "success"}

    await bot.send_message(
        chat_id,
        "<b>Unknown command.</b> Try /start or /tts Hello",
        reply_to_message_id=message.get("message_id"),
        parse_mode="HTML"
    )
    return {"status": "success"}
