"""Authenticated Telegram ingress and durable Azure Queue processing."""
import hmac
import json
import logging
import os

import azure.functions as func
import httpx
from azure.data.tables.aio import TableClient

from main import handle_update
from storage import BotState, TABLE_NAME
from telegram_client import TelegramClient

logger = logging.getLogger("bot.functions")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

app = func.FunctionApp()
QUEUE_NAME = "telegram-updates"
MAX_WEBHOOK_BYTES = 256_000
MAX_QUEUE_BYTES = 48_000
REQUIRED_SETTINGS = (
    "AzureWebJobsStorage", "BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET", "SPEECH_KEY", "SPEECH_REGION",
)


def _json_response(body: dict, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(body), status_code=status, mimetype="application/json")


def _missing_settings() -> list[str]:
    return [name for name in REQUIRED_SETTINGS if not os.environ.get(name)]


@app.route(
    route="health", trigger_arg_name="request", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS,
)
async def health(request: func.HttpRequest) -> func.HttpResponse:
    missing = _missing_settings()
    if missing:
        logger.error("Missing Azure settings: %s", ", ".join(missing))
        return _json_response({"status": "not_configured"}, 503)
    return _json_response({
        "status": "running",
        "bot": "Malayalam TTS Bot",
        "description": "Converts text to speech",
        "webhook": "/api/telegram",
    })


@app.route(
    route="telegram", trigger_arg_name="request", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS,
)
@app.queue_output(arg_name="updates", queue_name=QUEUE_NAME, connection="AzureWebJobsStorage")
async def telegram(request: func.HttpRequest, updates: func.Out[str]) -> func.HttpResponse:
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    if _missing_settings():
        logger.error("Telegram webhook is not configured")
        return _json_response({"error": "Service is not configured."}, 503)
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secret or not hmac.compare_digest(secret.encode(), supplied.encode()):
        logger.warning("Rejected unauthenticated Telegram webhook")
        return _json_response({"error": "Unauthorized."}, 401)
    raw = request.get_body()
    if len(raw) > MAX_WEBHOOK_BYTES:
        logger.warning("Rejected oversized Telegram update")
        return _json_response({"error": "Update is too large."}, 413)
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        logger.warning("Rejected invalid Telegram JSON")
        return _json_response({"error": "Invalid JSON."}, 400)
    if not isinstance(data, dict) or type(data.get("update_id")) is not int:
        logger.warning("Rejected Telegram update without an integer update_id")
        return _json_response({"error": "Invalid update."}, 400)
    message = data.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("text"), str):
        return _json_response({"status": "ignored"})
    chat = message.get("chat")
    if (
        not isinstance(chat, dict) or type(chat.get("id")) is not int or not chat["id"]
        or type(message.get("message_id")) is not int
    ):
        logger.warning("Rejected Telegram update with invalid message identifiers")
        return _json_response({"error": "Invalid message."}, 400)
    # Do not persist sender profiles or other Telegram metadata in the queue.
    queued_message = {
        "chat": {"id": chat["id"]},
        "message_id": message["message_id"],
        "text": message["text"],
    }
    entities = message.get("entities")
    if isinstance(entities, list):
        queued_message["entities"] = [
            {"type": "bot_command", "offset": 0, "length": entity["length"]}
            for entity in entities
            if isinstance(entity, dict) and entity.get("type") == "bot_command"
            and entity.get("offset") == 0 and type(entity.get("length")) is int
            and 0 < entity["length"] <= len(message["text"])
        ]
    queued = json.dumps({
        "update_id": data["update_id"], "message": queued_message,
    }, ensure_ascii=False)
    if len(queued.encode("utf-8")) > MAX_QUEUE_BYTES:
        logger.warning("Rejected Telegram update exceeding queue capacity")
        return _json_response({"error": "Message is too large."}, 413)
    updates.set(queued)
    return _json_response({"status": "queued"})


@app.queue_trigger(arg_name="update", queue_name=QUEUE_NAME, connection="AzureWebJobsStorage")
async def process_update(update: func.QueueMessage) -> None:
    data = json.loads(update.get_body())
    async with TableClient.from_connection_string(
        os.environ["AzureWebJobsStorage"], TABLE_NAME
    ) as table:
        state = BotState(table)
        if not await state.claim_update(data["update_id"]):
            logger.info("Skipping completed update_id=%s", data["update_id"])
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                bot = TelegramClient(client, f"https://api.telegram.org/bot{os.environ['BOT_TOKEN']}")
                await handle_update(data, bot, state)
            await state.complete_update(data["update_id"])
        except Exception:
            logger.exception("Processing failed for update_id=%s; queue will retry", data["update_id"])
            await state.abandon_update(data["update_id"])
            raise


@app.timer_trigger(arg_name="timer", schedule="0 0 3 * * *", use_monitor=True)
async def cleanup_state(timer: func.TimerRequest) -> None:
    async with TableClient.from_connection_string(
        os.environ["AzureWebJobsStorage"], TABLE_NAME
    ) as table:
        await BotState(table).cleanup()
