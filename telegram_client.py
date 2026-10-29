"""Telegram Bot API calls using the application's shared HTTP client."""
import logging
import os

import httpx

logger = logging.getLogger("bot.telegram")


class TelegramClient:
    def __init__(self, http_client: httpx.AsyncClient, api_url: str):
        self.http_client = http_client
        self.api_url = api_url

    async def register_commands(self) -> None:
        try:
            resp = await self.http_client.post(
                f"{self.api_url}/setMyCommands",
                json={
                    "commands": [
                        {"command": "start", "description": "Start the bot"},
                        {"command": "tts", "description": "Translate and convert text to Malayalam speech"},
                    ]
                },
            )
            logger.info("Bot commands registered: %s", resp.status_code == 200)
        except Exception:
            logger.error("Failed to register bot commands", exc_info=True)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        parse_mode: str | None = None,
    ) -> dict:
        data: dict[str, int | str] = {"chat_id": chat_id, "text": text}
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id
        if parse_mode:
            data["parse_mode"] = parse_mode
        try:
            resp = await self.http_client.post(
                f"{self.api_url}/sendMessage",
                data=data,
            )
            if resp.status_code != 200:
                logger.error("sendMessage failed (%s): %s", resp.status_code, resp.text[:200])
                return {}
            return resp.json()
        except Exception:
            logger.error("sendMessage error", exc_info=True)
            return {}

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: str | None = None,
    ) -> dict:
        data: dict[str, int | str] = {
            "chat_id": chat_id, "message_id": message_id, "text": text,
        }
        if parse_mode:
            data["parse_mode"] = parse_mode
        try:
            resp = await self.http_client.post(
                f"{self.api_url}/editMessageText",
                data=data,
            )
            if resp.status_code != 200:
                logger.error("editMessage failed (%s): %s", resp.status_code, resp.text[:200])
                return {}
            return resp.json()
        except Exception:
            logger.error("editMessage error", exc_info=True)
            return {}

    async def send_chat_action(self, chat_id: int, action: str) -> None:
        try:
            await self.http_client.post(
                f"{self.api_url}/sendChatAction",
                data={"chat_id": chat_id, "action": action},
            )
        except Exception:
            logger.error("sendChatAction error", exc_info=True)

    async def send_voice(
        self,
        chat_id: int,
        audio_file: str,
        caption: str,
        reply_to_message_id: int | None = None,
    ) -> httpx.Response:
        with open(audio_file, "rb") as f:
            data = {
                "chat_id": str(chat_id),
                "caption": caption[:1024],
                "parse_mode": "HTML",
            }
            if reply_to_message_id:
                data["reply_to_message_id"] = str(reply_to_message_id)
            resp = await self.http_client.post(
                f"{self.api_url}/sendVoice",
                data=data,
                files={"voice": (os.path.basename(audio_file), f, "audio/mpeg")},
                timeout=60.0,
            )
        if resp.status_code != 200:
            logger.error("sendVoice failed (%s): %s", resp.status_code, resp.text[:200])
        return resp
