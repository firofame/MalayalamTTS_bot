import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, AsyncMock, Mock, patch
from urllib.parse import parse_qs
from xml.etree import ElementTree

import httpx

import main
import tts
from telegram_client import TelegramClient, TelegramError

TELEGRAM_API_URL = "https://api.telegram.org/bottest-token"


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = Mock(spec=TelegramClient)
        self.bot.send_message = AsyncMock(return_value={})
        self.limiter = Mock(check_rate_limit=AsyncMock(return_value=False))
        self.run_tts = AsyncMock()
        patcher = patch.object(main, "run_tts", self.run_tts)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def send(self, text, **fields):
        return await main.handle_update({
            "message": {"chat": {"id": 123}, "message_id": 456, "text": text, **fields},
        }, self.bot, self.limiter)

    async def test_plain_text_and_commands_use_same_pipeline(self):
        for prefix in ("", "/tts ", "/tts@ExampleBot "):
            with self.subTest(prefix=prefix):
                self.run_tts.reset_mock()
                await self.send(prefix + "Hello world")
                self.run_tts.assert_awaited_once_with(self.bot, 123, "Hello world", 456)

    async def test_links_and_paths_are_literal_text(self):
        for text in ("https://example.com/recording.mp3", str(Path(__file__).resolve())):
            for prefix in (("", "/tts ") if text.startswith("https://") else ("/tts ",)):
                with self.subTest(text=text, prefix=prefix):
                    self.run_tts.reset_mock()
                    await self.send(prefix + text)
                    self.run_tts.assert_awaited_once_with(self.bot, 123, text, 456)

    async def test_all_input_has_same_exact_4000_character_limit(self):
        for prefix in ("", "/tts "):
            for content in ("x", "https://example.com/", "\U0001f600"):
                with self.subTest(prefix=prefix, content=content):
                    self.run_tts.reset_mock()
                    text = content + "a" * (4000 - len(content))
                    await self.send(prefix + text)
                    self.run_tts.assert_awaited_once_with(self.bot, 123, text, 456)
                    self.run_tts.reset_mock()
                    await self.send(prefix + text + "a")
                    self.run_tts.assert_not_awaited()
                    self.assertIn("Text too long", self.bot.send_message.call_args.args[1])

    async def test_rate_limit_still_applies(self):
        self.limiter.check_rate_limit.side_effect = [False, True]
        await self.send("/tts Hello")
        await self.send("Another request")
        self.run_tts.assert_awaited_once()
        self.assertIn("Please wait", self.bot.send_message.call_args.args[1])

    async def test_help_and_unknown_commands_do_not_start_tts_or_consume_cooldown(self):
        for text in ("/start", "/tts", "/unknown"):
            with self.subTest(text=text):
                await self.send(text)
                self.run_tts.assert_not_awaited()
                self.limiter.check_rate_limit.assert_not_awaited()
                reply = self.bot.send_message.call_args.args[1]
                self.assertNotIn("YouTube", reply)
                self.assertNotIn("translat", reply.lower())

    async def test_empty_messages_are_ignored(self):
        self.assertEqual(await self.send(" \x00 "), {"status": "ignored"})
        self.run_tts.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()

    async def test_invalid_xml_characters_are_removed(self):
        await self.send("Hello\x00\ud800\ufffe\uffff world")
        self.run_tts.assert_awaited_once_with(self.bot, 123, "Hello world", 456)

    def test_command_entities_and_bot_suffix(self):
        self.assertEqual(main.parse_command(
            "/tts@ExampleBot Hello", [{"type": "bot_command", "offset": 0, "length": 15}],
        ), ("/tts", "Hello"))


class TTSPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.http_client = Mock(spec=httpx.AsyncClient)
        self.http_client.post = AsyncMock(return_value=httpx.Response(200, json={"ok": True}))
        self.bot = TelegramClient(self.http_client, TELEGRAM_API_URL)
        self.send = AsyncMock(return_value={"result": {"message_id": 789}})
        self.edit = AsyncMock()
        for name, replacement in (
            ("send_message", self.send), ("edit_message", self.edit),
            ("send_chat_action", AsyncMock()),
        ):
            patcher = patch.object(self.bot, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_text_generates_voice_and_removes_temporary_audio(self):
        paths = []

        async def save(client, text, filename):
            paths.append(Path(filename))
            Path(filename).write_bytes(b"generated audio")

        with patch.object(tts, "synthesize", AsyncMock(side_effect=save)) as synthesize:
            await tts.run_tts(self.bot, 123, "Hello", 456)
        synthesize.assert_awaited_once_with(self.http_client, "Hello", str(paths[0]))
        self.assertIn("Generating audio", self.send.call_args.args[1])
        upload = self.http_client.post.call_args
        self.assertEqual(upload.args[0], f"{TELEGRAM_API_URL}/sendVoice")
        self.assertEqual(upload.kwargs["data"], {
            "chat_id": "123", "caption": "Hello", "reply_to_message_id": "456",
        })
        self.assertEqual(upload.kwargs["timeout"], 60.0)
        filename, file, content_type = upload.kwargs["files"]["voice"]
        self.assertEqual(filename, paths[0].name)
        self.assertEqual(content_type, "audio/mpeg")
        self.assertTrue(file.closed)
        self.assertIn("Done", self.edit.call_args.args[2])
        self.assertFalse(paths[0].exists())

    async def test_input_is_passed_unchanged_to_synthesis_and_plain_caption(self):
        for text in (
            "Hello world", "https://example.com/recording.mp3",
            str(Path(__file__).resolve()), "<b>Hello</b> & goodbye", "a" * 4000,
        ):
            with self.subTest(text=text[:80]):
                with patch.object(tts, "synthesize", AsyncMock()) as synthesize:
                    await tts.run_tts(self.bot, 123, text, 456)
                synthesize.assert_awaited_once_with(self.http_client, text, ANY)
                data = self.http_client.post.call_args.kwargs["data"]
                self.assertEqual(data["caption"], text[:1024])
                self.assertNotIn("parse_mode", data)

    async def test_synthesis_failure_without_progress_message_is_reported(self):
        self.send.return_value = {}
        with patch.object(tts, "synthesize", AsyncMock(side_effect=tts.SpeechError("Service unavailable"))):
            with self.assertLogs("bot.tts", level="ERROR"):
                await tts.run_tts(self.bot, 123, "Hello", 456)
        self.assertIn("Service unavailable", self.send.call_args.args[1])

    async def test_audio_failures_are_reported_and_temporary_files_removed(self):
        for failure in ("synthesis", "upload"):
            with self.subTest(failure=failure):
                self.edit.reset_mock()
                paths = []

                async def save(client, text, filename):
                    paths.append(Path(filename))
                    if failure == "synthesis":
                        raise tts.SpeechError("Audio generation failed")
                    Path(filename).write_bytes(b"generated audio")

                self.http_client.post.return_value = httpx.Response(500)
                with patch.object(tts, "synthesize", AsyncMock(side_effect=save)):
                    with self.assertLogs(level="ERROR"):
                        await tts.run_tts(self.bot, 123, "Hello", 456)
                self.assertIn("failed", self.edit.call_args.args[2].lower())
                self.assertFalse(paths[0].exists())

    async def test_failed_done_notice_does_not_retry_delivered_voice(self):
        self.edit.side_effect = TelegramError("Edit failed")
        with patch.object(tts, "synthesize", AsyncMock()):
            with self.assertLogs("bot.tts", level="WARNING"):
                await tts.run_tts(self.bot, 123, "Hello", 456)
        self.http_client.post.assert_awaited_once()

    async def test_unexpected_failure_is_retried_and_audio_cleaned(self):
        paths = []

        async def fail(client, text, filename):
            paths.append(Path(filename))
            raise OSError("Disk failed")

        with patch.object(tts, "synthesize", AsyncMock(side_effect=fail)):
            with self.assertRaisesRegex(OSError, "Disk failed"):
                await tts.run_tts(self.bot, 123, "Hello", 456)
        self.assertFalse(paths[0].exists())

    async def test_chat_actions_repeat_and_stop_when_cancelled(self):
        task = asyncio.create_task(tts._chat_action_loop(self.bot, 123, "typing"))
        with patch.object(tts.asyncio, "sleep", AsyncMock()) as sleep:
            self.bot.send_chat_action.side_effect = [None, asyncio.CancelledError()]
            await task
        self.assertEqual(self.bot.send_chat_action.await_count, 2)
        sleep.assert_awaited_once_with(4)


class TelegramClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.handle = Mock(return_value=httpx.Response(
            200, json={"ok": True, "result": {"message_id": 789}},
        ))
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        self.addAsyncCleanup(self.client.aclose)
        self.bot = TelegramClient(self.client, TELEGRAM_API_URL)

    async def test_register_commands(self):
        await self.bot.register_commands()
        request = self.handle.call_args.args[0]
        self.assertEqual(str(request.url), f"{TELEGRAM_API_URL}/setMyCommands")
        self.assertEqual(json.loads(request.content), {"commands": [
            {"command": "start", "description": "Start the bot"},
            {"command": "tts", "description": "Convert text to speech"},
        ]})

    async def test_message_payloads_and_responses(self):
        result = await self.bot.send_message(123, "Hello", 456, "HTML")
        request = self.handle.call_args.args[0]
        self.assertEqual(str(request.url), f"{TELEGRAM_API_URL}/sendMessage")
        self.assertEqual(parse_qs(request.content.decode()), {
            "chat_id": ["123"], "text": ["Hello"], "reply_to_message_id": ["456"], "parse_mode": ["HTML"],
        })
        self.assertEqual(result["result"]["message_id"], 789)
        result = await self.bot.edit_message(123, 789, "Done", "HTML")
        request = self.handle.call_args.args[0]
        self.assertEqual(str(request.url), f"{TELEGRAM_API_URL}/editMessageText")
        self.assertEqual(parse_qs(request.content.decode()), {
            "chat_id": ["123"], "message_id": ["789"], "text": ["Done"], "parse_mode": ["HTML"],
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
        self.assertEqual(str(request.url), f"{TELEGRAM_API_URL}/sendChatAction")
        self.assertEqual(parse_qs(request.content.decode()), {"chat_id": ["123"], "action": ["typing"]})

    async def test_voice_caption_limit_and_multipart_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice.mp3"
            path.write_bytes(b"generated audio")
            response = await self.bot.send_voice(123, str(path), "a" * 1100)
        request = self.handle.call_args.args[0]
        self.assertEqual(str(request.url), f"{TELEGRAM_API_URL}/sendVoice")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"a" * 1024 + b"\r\n", request.content)
        self.assertNotIn(b"a" * 1025, request.content)
        self.assertIn(b'filename="voice.mp3"', request.content)
        self.assertIn(b"audio/mpeg", request.content)
        self.assertIn(b"generated audio", request.content)
        self.assertNotIn(b"reply_to_message_id", request.content)
        self.assertEqual(request.extensions["timeout"]["read"], 60.0)

    async def test_api_errors_are_logged_and_propagate_without_leaking_token(self):
        for failure in ("status", "connection"):
            for method, args in (
                (self.bot.send_message, (123, "Hello")),
                (self.bot.edit_message, (123, 789, "Done")),
                (self.bot.send_chat_action, (123, "typing")),
            ):
                with self.subTest(failure=failure, method=method.__name__):
                    self.handle.return_value = httpx.Response(500)
                    self.handle.side_effect = (
                        httpx.ConnectError(f"Failed {TELEGRAM_API_URL}") if failure == "connection" else None
                    )
                    with self.assertLogs("bot.telegram", level="ERROR") as logs:
                        with self.assertRaises(TelegramError) as error:
                            await method(*args)
                    self.assertNotIn("test-token", str(error.exception))
                    self.assertNotIn("test-token", "".join(logs.output))

    async def test_invalid_success_responses_are_rejected(self):
        for response in (
            httpx.Response(200, json={"ok": False}), httpx.Response(200, json=[]),
            httpx.Response(200, text="Not JSON"),
        ):
            with self.subTest(response=response):
                self.handle.return_value = response
                with self.assertLogs("bot.telegram", level="ERROR"):
                    with self.assertRaises(TelegramError):
                        await self.bot.send_message(123, "Hello")


class AzureSpeechTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.handle = Mock(return_value=httpx.Response(
            200, content=b"audio", headers={"Content-Type": "audio/mpeg"},
        ))
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        self.addAsyncCleanup(self.client.aclose)
        settings = patch.dict(os.environ, {"SPEECH_KEY": "test-key", "SPEECH_REGION": "centralindia"})
        settings.start()
        self.addCleanup(settings.stop)

    async def test_ssml_escaping_voice_and_full_4000_characters(self):
        for text in ("<b>Hello</b> & \"goodbye\"", "a" * 4000, "\u0d28\u0d2e\u0d38\u0d4d\u0d15\u0d3e\u0d30\u0d02"):
            with self.subTest(text=text[:40]), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "voice.mp3"
                await tts.synthesize(self.client, text, str(path))
                self.assertEqual(path.read_bytes(), b"audio")
                request = self.handle.call_args.args[0]
                self.assertEqual(str(request.url), "https://centralindia.tts.speech.microsoft.com/cognitiveservices/v1")
                self.assertEqual(request.headers["Ocp-Apim-Subscription-Key"], "test-key")
                self.assertEqual(request.headers["X-Microsoft-OutputFormat"], "audio-24khz-48kbitrate-mono-mp3")
                voice = ElementTree.fromstring(request.content)[0]
                self.assertEqual(voice.attrib["name"], tts.VOICE)
                self.assertEqual(voice.text, text)

    async def test_upstream_errors_are_explicit_without_retries(self):
        for status, expected in ((429, "quota"), (401, "credentials"), (403, "credentials"), (500, "unavailable")):
            with self.subTest(status=status):
                self.handle.reset_mock()
                self.handle.return_value = httpx.Response(status)
                with self.assertRaisesRegex(tts.SpeechError, expected):
                    await tts.synthesize(self.client, "Hello", "unused.mp3")
                self.handle.assert_called_once()

    async def test_empty_or_non_audio_responses_are_rejected(self):
        for response in (
            httpx.Response(200, content=b"", headers={"Content-Type": "audio/mpeg"}),
            httpx.Response(200, content=b"error", headers={"Content-Type": "text/plain"}),
        ):
            self.handle.return_value = response
            with self.assertRaisesRegex(tts.SpeechError, "invalid or empty"):
                await tts.synthesize(self.client, "Hello", "unused.mp3")

    async def test_configuration_and_network_failures_are_reported(self):
        with patch.dict(os.environ, {"SPEECH_REGION": "invalid/region"}):
            with self.assertRaisesRegex(tts.SpeechError, "not configured"):
                await tts.synthesize(self.client, "Hello", "unused.mp3")
        self.handle.assert_not_called()
        self.handle.side_effect = httpx.ReadTimeout("Timed out")
        with self.assertLogs("bot.tts", level="ERROR"):
            with self.assertRaisesRegex(tts.SpeechError, "Could not reach"):
                await tts.synthesize(self.client, "Hello", "unused.mp3")


if __name__ == "__main__":
    unittest.main()
