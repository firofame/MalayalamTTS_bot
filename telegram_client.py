"""Telegram Bot API calls using the application's shared HTTP client."""
import logging
import os

import httpx

logger = logging.getLogger("bot.telegram")


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, http_client: httpx.AsyncClient, api_url: str):
        self.http_client = http_client
        self.api_url = api_url

    async def register_commands(self) -> None:
        await self._post("setMyCommands", json={
            "commands": [
                {"command": "start", "description": "Start the bot"},
                {"command": "tts", "description": "Convert text to speech"},
            ]
        })
        logger.info("Bot commands registered")

    async def _post(self, method: str, **kwargs) -> httpx.Response:
        try:
            response = await self.http_client.post(f"{self.api_url}/{method}", **kwargs)
        except httpx.HTTPError as error:
            # HTTPX exception messages can contain the bot token in the URL.
            logger.error("Telegram %s failed (%s)", method, type(error).__name__)
            raise TelegramError(f"Telegram {method} connection failed.") from None
        if response.status_code != 200:
            logger.error("Telegram %s returned HTTP %s", method, response.status_code)
            raise TelegramError(f"Telegram {method} failed (HTTP {response.status_code}).")
        try:
            result = response.json()
        except ValueError:
            logger.error("Telegram %s returned invalid JSON", method)
            raise TelegramError("Telegram returned an invalid response.") from None
        if not isinstance(result, dict) or result.get("ok") is not True:
            logger.error("Telegram %s rejected the request", method)
            raise TelegramError(f"Telegram {method} rejected the request.")
        return response

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
        return (await self._post("sendMessage", data=data)).json()

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
        return (await self._post("editMessageText", data=data)).json()

    async def send_chat_action(self, chat_id: int, action: str) -> None:
        await self._post("sendChatAction", data={"chat_id": chat_id, "action": action})

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
            }
            if reply_to_message_id:
                data["reply_to_message_id"] = str(reply_to_message_id)
            return await self._post(
                "sendVoice",
                data=data,
                files={"voice": (os.path.basename(audio_file), f, "audio/mpeg")},
                timeout=60.0,
            )
