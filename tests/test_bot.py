import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs

import httpx

with patch.dict(os.environ, {"BOT_TOKEN": "test-token"}):
    import main
    import tts

from telegram_client import TelegramClient


class WebhookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main._rate_limits.clear()
        self.addCleanup(main._rate_limits.clear)
        self.send_message = AsyncMock(return_value={})
        self.run_tts = AsyncMock()
        self.bot = Mock(spec=TelegramClient)
        self.bot.send_message = self.send_message
        for target, name, replacement in (
            (main.app.state, "telegram_client", self.bot),
            (main, "run_tts", self.run_tts),
        ):
            patcher = patch.object(target, name, replacement, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(self.client.aclose)

    async def send(self, text, **fields):
        response = await self.client.post(
            "/telegram",
            json={
                "message": {
                    "chat": {"id": 123},
                    "message_id": 456,
                    "text": text,
                    **fields,
                }
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    async def test_plain_text_and_commands_use_same_pipeline(self):
        for text in ("Hello world", "/tts Hello world", "/tts@ExampleBot Hello world"):
            with self.subTest(text=text):
                main._rate_limits.clear()
                self.run_tts.reset_mock()
                await self.send(text)
                self.run_tts.assert_awaited_once_with(self.bot, 123, "Hello world", 456)

    async def test_links_are_passed_as_literal_text(self):
        text = "https://example.com/recording.mp3"
        for prefix in ("", "/tts "):
            with self.subTest(prefix=prefix):
                main._rate_limits.clear()
                self.run_tts.reset_mock()
                await self.send(prefix + text)
                self.run_tts.assert_awaited_once_with(self.bot, 123, text, 456)

    async def test_all_input_has_same_length_limit(self):
        for prefix in ("", "/tts "):
            for content in ("x", "https://example.com/"):
                with self.subTest(prefix=prefix, content=content):
                    main._rate_limits.clear()
                    self.run_tts.reset_mock()
                    self.send_message.reset_mock()
                    text = content + "a" * (main.MAX_TEXT_LENGTH - len(content))
                    await self.send(prefix + text)
                    self.run_tts.assert_awaited_once_with(self.bot, 123, text, 456)
                    self.run_tts.reset_mock()
                    await self.send(prefix + text + "a")
                    self.run_tts.assert_not_awaited()
                    self.assertIn("Text too long", self.send_message.call_args.args[1])

    async def test_rate_limit_still_applies(self):
        await self.send("/tts Hello")
        await self.send("Another request")
        self.run_tts.assert_awaited_once()
        self.assertIn("Please wait", self.send_message.call_args.args[1])

    async def test_help_and_unknown_commands_do_not_start_tts(self):
        for text in ("/start", "/tts", "/unknown"):
            with self.subTest(text=text):
                await self.send(text)
                self.run_tts.assert_not_awaited()
                reply = self.send_message.call_args.args[1]
                self.assertNotIn("Instagram", reply)
                self.assertNotIn("YouTube", reply)
                self.assertNotIn("translat", reply.lower())

    async def test_empty_messages_are_ignored(self):
        result = await self.send(" \x00 ")
        self.assertEqual(result, {"status": "ignored"})
        self.run_tts.assert_not_awaited()
        self.send_message.assert_not_awaited()


class TTSPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.http_client = Mock(spec=httpx.AsyncClient)
        self.http_client.post = AsyncMock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        self.bot = TelegramClient(self.http_client, main.TELEGRAM_API_URL)
        self.send = AsyncMock(return_value={"result": {"message_id": 789}})
        self.edit = AsyncMock()
        for name, replacement in (
            ("send_message", self.send),
            ("edit_message", self.edit),
            ("send_chat_action", AsyncMock()),
        ):
            patcher = patch.object(self.bot, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_text_generates_voice_and_removes_temporary_audio(self):
        audio_paths = []

        async def save_audio(filename):
            audio_paths.append(Path(filename))
            Path(filename).write_bytes(b"generated audio")

        communicate = Mock(save=AsyncMock(side_effect=save_audio))
        with patch.object(tts.edge_tts, "Communicate", return_value=communicate) as communicate_factory:
            await tts.run_tts(self.bot, 123, "Hello", 456)
        communicate_factory.assert_called_once_with("Hello", tts.VOICE)
        self.assertIn("Generating audio", self.send.call_args.args[1])
        upload = self.http_client.post.call_args
        self.assertEqual(upload.args[0], f"{main.TELEGRAM_API_URL}/sendVoice")
        self.assertEqual(upload.kwargs["data"], {
            "chat_id": "123",
            "caption": "Hello",
            "reply_to_message_id": "456",
        })
        self.assertEqual(upload.kwargs["timeout"], 60.0)
        filename, file, content_type = upload.kwargs["files"]["voice"]
        self.assertEqual(filename, audio_paths[0].name)
        self.assertEqual(content_type, "audio/mpeg")
        self.assertTrue(file.closed)
        self.assertIn("Done", self.edit.call_args.args[2])
        self.assertEqual(len(audio_paths), 1)
        self.assertFalse(audio_paths[0].exists())

    async def test_input_is_passed_unchanged_to_synthesis_and_caption(self):
        for text in (
            "Hello world",
            "https://example.com/recording.mp3",
            str(Path(__file__).resolve()),
            "<b>Hello</b> & goodbye",
            "a" * main.MAX_TEXT_LENGTH,
        ):
            with self.subTest(text=text[:80]):
                communicate = Mock(save=AsyncMock())
                with patch.object(tts.edge_tts, "Communicate", return_value=communicate) as factory:
                    await tts.run_tts(self.bot, 123, text, 456)
                factory.assert_called_once_with(text, tts.VOICE)
                upload_data = self.http_client.post.call_args.kwargs["data"]
                self.assertEqual(upload_data["caption"], text[:1024])
                self.assertNotIn("parse_mode", upload_data)

    async def test_synthesis_failure_without_progress_message_is_reported(self):
        self.send.return_value = {}
        with patch.object(tts.edge_tts, "Communicate", side_effect=RuntimeError("Service unavailable")):
            await tts.run_tts(self.bot, 123, "Hello", 456)
        self.assertIn("Service unavailable", self.send.call_args.args[1])

    async def test_audio_failures_are_reported_and_temporary_files_removed(self):
        for failure in ("synthesis", "upload"):
            with self.subTest(failure=failure):
                self.edit.reset_mock()
                audio_paths = []

                async def save_audio(filename):
                    audio_paths.append(Path(filename))
                    if failure == "synthesis":
                        raise RuntimeError("Audio generation failed")
                    Path(filename).write_bytes(b"generated audio")

                self.http_client.post.return_value = httpx.Response(500, text="Upload failed")
                with patch.object(tts.edge_tts, "Communicate", return_value=Mock(
                    save=AsyncMock(side_effect=save_audio)
                )):
                    await tts.run_tts(self.bot, 123, "Hello", 456)
                self.assertIn("failed", self.edit.call_args.args[2].lower())
                self.assertEqual(len(audio_paths), 1)
                self.assertFalse(audio_paths[0].exists())

    async def test_chat_actions_repeat_and_stop_when_cancelled(self):
        task = asyncio.create_task(tts._chat_action_loop(self.bot, 123, "typing"))
        with patch.object(tts.asyncio, "sleep", AsyncMock()) as sleep:
            self.bot.send_chat_action.side_effect = [None, asyncio.CancelledError()]
            await task
        self.assertEqual(self.bot.send_chat_action.await_count, 2)
        self.bot.send_chat_action.assert_awaited_with(123, "typing")
        sleep.assert_awaited_once_with(4)


class TelegramClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.handle = Mock(return_value=httpx.Response(
            200, json={"ok": True, "result": {"message_id": 789}}
        ))
        self.http_client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        self.addAsyncCleanup(self.http_client.aclose)
        self.bot = TelegramClient(self.http_client, main.TELEGRAM_API_URL)

    async def test_register_commands(self):
        await self.bot.register_commands()
        request = self.handle.call_args.args[0]
        self.assertEqual(str(request.url), f"{main.TELEGRAM_API_URL}/setMyCommands")
        self.assertEqual(json.loads(request.content), {
            "commands": [
                {"command": "start", "description": "Start the bot"},
                {"command": "tts", "description": "Convert text to speech"},
            ]
        })

    async def test_message_payloads_and_responses(self):
        result = await self.bot.send_message(123, "Hello", 456, "HTML")
        request = self.handle.call_args.args[0]
        self.assertEqual(str(request.url), f"{main.TELEGRAM_API_URL}/sendMessage")
        self.assertEqual(parse_qs(request.content.decode()), {
            "chat_id": ["123"], "text": ["Hello"],
            "reply_to_message_id": ["456"], "parse_mode": ["HTML"],
        })
        self.assertEqual(result["result"]["message_id"], 789)

        result = await self.bot.edit_message(123, 789, "Done", "HTML")
        request = self.handle.call_args.args[0]
        self.assertEqual(str(request.url), f"{main.TELEGRAM_API_URL}/editMessageText")
        self.assertEqual(parse_qs(request.content.decode()), {
            "chat_id": ["123"], "message_id": ["789"],
            "text": ["Done"], "parse_mode": ["HTML"],
        })
        self.assertTrue(result["ok"])

    async def test_optional_message_fields_are_omitted(self):
        await self.bot.send_message(123, "Hello")
        self.assertEqual(parse_qs(self.handle.call_args.args[0].content.decode()), {
            "chat_id": ["123"], "text": ["Hello"],
        })
        await self.bot.edit_message(123, 789, "Done")
        self.assertEqual(parse_qs(self.handle.call_args.args[0].content.decode()), {
            "chat_id": ["123"], "message_id": ["789"], "text": ["Done"],
        })

    async def test_chat_action_payload(self):
        await self.bot.send_chat_action(123, "typing")
        request = self.handle.call_args.args[0]
        self.assertEqual(str(request.url), f"{main.TELEGRAM_API_URL}/sendChatAction")
        self.assertEqual(parse_qs(request.content.decode()), {
            "chat_id": ["123"], "action": ["typing"],
        })

    async def test_voice_caption_limit_and_multipart_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "voice.mp3"
            audio_path.write_bytes(b"generated audio")
            response = await self.bot.send_voice(123, str(audio_path), "a" * 1100)
        request = self.handle.call_args.args[0]
        self.assertEqual(str(request.url), f"{main.TELEGRAM_API_URL}/sendVoice")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"multipart/form-data", request.headers["Content-Type"].encode())
        self.assertIn(b"a" * 1024 + b"\r\n", request.content)
        self.assertNotIn(b"a" * 1025, request.content)
        self.assertIn(b'filename="voice.mp3"', request.content)
        self.assertIn(b"audio/mpeg", request.content)
        self.assertIn(b"generated audio", request.content)
        self.assertNotIn(b"reply_to_message_id", request.content)
        self.assertEqual(request.extensions["timeout"]["read"], 60.0)

    async def test_api_errors_remain_logged(self):
        for failure in ("status", "connection"):
            for method, args in (
                (self.bot.send_message, (123, "Hello")),
                (self.bot.edit_message, (123, 789, "Done")),
            ):
                with self.subTest(failure=failure, method=method.__name__):
                    self.handle.return_value = httpx.Response(500, text="Service unavailable")
                    self.handle.side_effect = (
                        httpx.ConnectError("Offline") if failure == "connection" else None
                    )
                    with self.assertLogs("bot.telegram", level="ERROR"):
                        self.assertEqual(await method(*args), {})


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_client_registers_commands_serves_webhook_and_closes(self):
        handle = Mock(return_value=httpx.Response(200, json={"ok": True}))
        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        self.addAsyncCleanup(http_client.aclose)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://testserver",
        ) as webhook_client:
            with patch.object(main.httpx, "AsyncClient", return_value=http_client) as factory:
                async with main.lifespan(main.app):
                    factory.assert_called_once_with(timeout=10.0)
                    self.assertIs(main.app.state.telegram_client.http_client, http_client)
                    self.assertEqual(
                        str(handle.call_args.args[0].url),
                        f"{main.TELEGRAM_API_URL}/setMyCommands",
                    )
                    health = await webhook_client.get("/")
                    self.assertEqual(health.json(), {
                        "status": "running",
                        "bot": "Malayalam TTS Bot",
                        "description": "Converts text to speech",
                        "webhook": "/telegram",
                    })
                    response = await webhook_client.post("/telegram", json={
                        "message": {"chat": {"id": 123}, "text": "/start"}
                    })
                    self.assertEqual(response.json(), {"status": "success"})
                    self.assertEqual(
                        str(handle.call_args.args[0].url),
                        f"{main.TELEGRAM_API_URL}/sendMessage",
                    )
                    self.assertFalse(http_client.is_closed)
        self.assertTrue(http_client.is_closed)

    async def test_client_closes_on_exception_during_lifespan(self):
        http_client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"ok": True})
        ))
        self.addAsyncCleanup(http_client.aclose)
        with patch.object(main.httpx, "AsyncClient", return_value=http_client):
            with self.assertRaisesRegex(RuntimeError, "Application failed"):
                async with main.lifespan(main.app):
                    raise RuntimeError("Application failed")
        self.assertTrue(http_client.is_closed)


if __name__ == "__main__":
    unittest.main()
