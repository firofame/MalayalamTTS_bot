"""Translation-to-speech workflow and Telegram progress updates."""
import asyncio
import contextlib
import html
import logging
import os
import tempfile

import edge_tts

from telegram_client import TelegramClient
from translate import convert_to_malayalam

logger = logging.getLogger("bot.tts")
VOICE = "en-US-AvaMultilingualNeural"


async def _chat_action_loop(bot: TelegramClient, chat_id: int, action: str) -> None:
    """Send chat action every 4 seconds until cancelled."""
    try:
        while True:
            await bot.send_chat_action(chat_id, action)
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.error("Error in chat action loop", exc_info=True)


async def run_tts(
    bot: TelegramClient,
    chat_id: int,
    args: str,
    reply_to_message_id: int | None = None,
) -> None:
    logger.info("TTS started: chat_id=%s, args_len=%d", chat_id, len(args))

    resp = await bot.send_message(chat_id, "🌐 Translating...", reply_to_message_id=reply_to_message_id)
    progress_msg_id = resp.get("result", {}).get("message_id") or None

    audio_file = None
    action_task = None

    try:
        action_task = asyncio.create_task(_chat_action_loop(bot, chat_id, "typing"))

        malayalam_text = await convert_to_malayalam(args)
        logger.info("Translation complete: chat_id=%s, text_len=%d", chat_id, len(malayalam_text))

        if not malayalam_text:
            if progress_msg_id:
                await bot.edit_message(chat_id, progress_msg_id, "❌ Translation service is busy. Please try again.")
            else:
                await bot.send_message(chat_id, "❌ Translation service is busy. Please try again.", reply_to_message_id=reply_to_message_id)
            return

        action_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await action_task
        action_task = asyncio.create_task(_chat_action_loop(bot, chat_id, "record_voice"))

        if progress_msg_id:
            await bot.edit_message(chat_id, progress_msg_id, "🎙️ Generating audio...")

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            audio_file = tmp.name

        communicate = edge_tts.Communicate(malayalam_text, VOICE)
        await communicate.save(audio_file)
        logger.info("Audio generated: chat_id=%s", chat_id)

        voice_resp = await bot.send_voice(
            chat_id, audio_file, malayalam_text, reply_to_message_id,
        )
        if voice_resp.status_code != 200:
            if progress_msg_id:
                await bot.edit_message(chat_id, progress_msg_id, f"❌ Audio send failed: {voice_resp.text}")
            return

        logger.info("Voice sent: chat_id=%s", chat_id)
        if progress_msg_id:
            await bot.edit_message(chat_id, progress_msg_id, "✅ Done!")

    except Exception as e:
        error_msg = html.escape(str(e))
        logger.error("TTS failed: chat_id=%s, error=%s", chat_id, error_msg)
        if progress_msg_id:
            await bot.edit_message(chat_id, progress_msg_id, f"<b>❌ Error:</b> {error_msg}", parse_mode="HTML")
        else:
            await bot.send_message(chat_id, f"<b>❌ Error:</b> {error_msg}", reply_to_message_id=reply_to_message_id, parse_mode="HTML")
    finally:
        if action_task:
            action_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await action_task
        if audio_file and os.path.exists(audio_file):
            try:
                os.remove(audio_file)
            except OSError:
                logger.error("Failed to remove temporary audio", exc_info=True)
        logger.info("TTS complete: chat_id=%s", chat_id)
