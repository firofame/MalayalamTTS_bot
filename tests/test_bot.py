import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs
from xml.etree import ElementTree

import httpx

import article
import main
import tts
from telegram_client import TelegramClient, TelegramError

TELEGRAM_API_URL = "https://api.telegram.org/bottest-token"


def tracked_temp(paths):
    """Return a NamedTemporaryFile factory recording every path it creates."""
    real = tts.tempfile.NamedTemporaryFile

    def create(*args, **kwargs):
        tmp = real(*args, **kwargs)
        paths.append(Path(tmp.name))
        return tmp

    return create


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = Mock(spec=TelegramClient)
        self.bot.send_message = AsyncMock(return_value={})
        self.limiter = Mock(check_rate_limit=AsyncMock(return_value=False))
        self.run_tts = AsyncMock()
        self.read_article = AsyncMock()
        for name, replacement in (("run_tts", self.run_tts), ("_read_article", self.read_article)):
            patcher = patch.object(main, name, replacement)
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
                self.run_tts.assert_awaited_once_with(self.bot, 123, "Hello world", "Hello world", 456)

    async def test_paths_and_mixed_text_stay_literal(self):
        for text in (str(Path(__file__).resolve()), "See https://example.com/post for details"):
            with self.subTest(text=text):
                self.run_tts.reset_mock()
                await self.send("/tts " + text)
                self.run_tts.assert_awaited_once_with(self.bot, 123, text, text, 456)
                self.read_article.assert_not_awaited()

    async def test_urls_route_to_article_flow(self):
        url = "https://example.com/blog/post"
        for text in (url, "/tts " + url, "/url " + url):
            with self.subTest(text=text):
                self.read_article.reset_mock()
                await self.send(text)
                self.read_article.assert_awaited_once_with(self.bot, 123, url, 456, self.limiter)
                self.run_tts.assert_not_awaited()

    async def test_all_input_has_same_exact_4000_character_limit(self):
        for prefix in ("", "/tts "):
            for content in ("x", "example.com/post", "\U0001f600"):
                with self.subTest(prefix=prefix, content=content):
                    self.run_tts.reset_mock()
                    text = content + "a" * (4000 - len(content))
                    await self.send(prefix + text)
                    self.run_tts.assert_awaited_once_with(self.bot, 123, text, text, 456)
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
        for text in ("/start", "/tts", "/url", "/unknown"):
            with self.subTest(text=text):
                await self.send(text)
                self.run_tts.assert_not_awaited()
                self.read_article.assert_not_awaited()
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
        self.run_tts.assert_awaited_once_with(self.bot, 123, "Hello world", "Hello world", 456)

    def test_command_entities_and_bot_suffix(self):
        self.assertEqual(main.parse_command(
            "/tts@ExampleBot Hello", [{"type": "bot_command", "offset": 0, "length": 15}],
        ), ("/tts", "Hello"))


class ArticleFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.http_client = Mock(spec=httpx.AsyncClient)
        self.bot = TelegramClient(self.http_client, TELEGRAM_API_URL)
        self.send_message = AsyncMock(return_value={"result": {"message_id": 789}})
        self.edit_message = AsyncMock()
        for name, replacement in (
            ("send_message", self.send_message), ("edit_message", self.edit_message),
        ):
            patcher = patch.object(self.bot, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.limiter = Mock(check_rate_limit=AsyncMock(return_value=False))
        self.extract = AsyncMock(return_value=("My Post", "Article body.", False))
        self.run_tts = AsyncMock()
        for name, replacement in (("extract", self.extract), ("run_tts", self.run_tts)):
            patcher = patch.object(main, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def send(self, text):
        return await main.handle_update(
            {"message": {"chat": {"id": 123}, "message_id": 456, "text": text}},
            self.bot, self.limiter,
        )

    async def test_bare_url_fetches_then_synthesizes_with_title_caption(self):
        await self.send("https://example.com/post")
        self.extract.assert_awaited_once_with(self.bot.http_client, "https://example.com/post")
        self.assertIn("Reading article", self.send_message.call_args.args[1])
        self.edit_message.assert_any_await(123, 789, "🎙️ Generating audio...")
        self.run_tts.assert_awaited_once_with(
            self.bot, 123, "Article body.", "My Post\n\nSource: https://example.com/post", 456, 789,
        )

    async def test_missing_title_caption_uses_source_only(self):
        self.extract.return_value = ("", "Body", False)
        await self.send("https://example.com/post")
        self.run_tts.assert_awaited_once_with(
            self.bot, 123, "Body", "Source: https://example.com/post", 456, 789,
        )

    async def test_truncated_articles_note_the_cap(self):
        self.extract.return_value = ("T", "Body", True)
        await self.send("/url https://example.com/post")
        caption = self.run_tts.call_args.args[3]
        self.assertIn(f"Truncated at {article.MAX_ARTICLE_LENGTH:,} characters.", caption)
        self.assertIn("Source:", caption)

    async def test_fetch_failure_edits_progress_with_error(self):
        self.extract.side_effect = article.ArticleError("Could not read this page.")
        with self.assertLogs("bot.main", level="ERROR"):
            await self.send("https://example.com/post")
        self.run_tts.assert_not_awaited()
        self.assertIn("Could not read this page.", self.edit_message.call_args.args[2])

    async def test_article_rate_limit_blocks_fetch(self):
        self.limiter.check_rate_limit = AsyncMock(return_value=True)
        await self.send("https://example.com/post")
        self.extract.assert_not_awaited()
        self.run_tts.assert_not_awaited()
        self.assertIn("Please wait", self.send_message.call_args.args[1])

    async def test_url_command_without_url_shows_usage(self):
        await self.send("/url")
        self.extract.assert_not_awaited()
        self.limiter.check_rate_limit.assert_not_awaited()
        self.assertIn("Usage", self.send_message.call_args.args[1])


class ChunkTests(unittest.TestCase):
    def test_short_text_stays_one_chunk(self):
        self.assertEqual(tts._chunk_text(""), [])
        self.assertEqual(tts._chunk_text("Hello world"), ["Hello world"])
        self.assertEqual(tts._chunk_text("a" * tts.CHUNK_SIZE), ["a" * tts.CHUNK_SIZE])

    def test_long_text_splits_at_boundaries_without_mid_word_cuts(self):
        text = "First paragraph here.\nSecond one follows. Third sentence! Fourth? " * 100
        chunks = tts._chunk_text(text)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), tts.CHUNK_SIZE)
        # Chunking only strips boundary whitespace, so all prose survives.
        without_whitespace = lambda s: "".join(s.split())
        self.assertEqual(without_whitespace("".join(chunks)), without_whitespace(text))

    def test_chunks_end_on_sentence_boundaries(self):
        chunks = tts._chunk_text("A. " * 3000)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertTrue(chunk.endswith("A."))

    def test_text_without_boundaries_still_splits(self):
        chunks = tts._chunk_text("x" * (tts.CHUNK_SIZE + 500))
        self.assertEqual([len(chunk) for chunk in chunks], [tts.CHUNK_SIZE, 500])


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

        with patch.object(tts.tempfile, "NamedTemporaryFile", side_effect=tracked_temp(paths)), \
             patch.object(tts, "synthesize", AsyncMock(return_value=b"generated audio")) as synthesize:
            await tts.run_tts(self.bot, 123, "Hello", "Hello", 456)
        synthesize.assert_awaited_once_with(self.http_client, "Hello")
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
                with patch.object(tts, "synthesize", AsyncMock(return_value=b"audio")) as synthesize:
                    await tts.run_tts(self.bot, 123, text, text, 456)
                synthesize.assert_awaited_once_with(self.http_client, text)
                data = self.http_client.post.call_args.kwargs["data"]
                self.assertEqual(data["caption"], text[:1024])
                self.assertNotIn("parse_mode", data)

    async def test_long_text_is_split_at_boundaries_and_merged_into_one_voice(self):
        text = "A sentence for the test. " * 400
        parts = []
        captured = {}

        async def save(client, chunk):
            parts.append(("part-" + str(len(parts))).encode())
            return parts[-1]

        async def fake_send(chat_id, audio_file, caption, reply_to_message_id=None):
            captured["audio"] = Path(audio_file).read_bytes()
            captured["caption"] = caption

        with patch.object(tts, "synthesize", AsyncMock(side_effect=save)), \
             patch.object(self.bot, "send_voice", AsyncMock(side_effect=fake_send)):
            await tts.run_tts(self.bot, 123, text, "caption", 456)
        self.assertGreater(len(parts), 1)
        self.assertEqual(captured["audio"], b"".join(parts))
        self.assertEqual(captured["caption"], "caption")
        self.assertIn("Done", self.edit.call_args.args[2])

    async def test_empty_text_fails_explicitly_without_upload(self):
        with patch.object(tts, "synthesize", AsyncMock()) as synthesize:
            with self.assertLogs("bot.tts", level="ERROR"):
                await tts.run_tts(self.bot, 123, "", "caption", 456)
        synthesize.assert_not_awaited()
        self.http_client.post.assert_not_awaited()
        self.assertIn("No text", self.edit.call_args.args[2])

    async def test_synthesis_failure_without_progress_message_is_reported(self):
        self.send.return_value = {}
        with patch.object(tts, "synthesize", AsyncMock(side_effect=tts.SpeechError("Service unavailable"))):
            with self.assertLogs("bot.tts", level="ERROR"):
                await tts.run_tts(self.bot, 123, "Hello", "Hello", 456)
        self.assertIn("Service unavailable", self.send.call_args.args[1])

    async def test_audio_failures_are_reported_and_temporary_files_removed(self):
        for failure in ("synthesis", "upload"):
            with self.subTest(failure=failure):
                self.edit.reset_mock()
                paths = []

                async def save(client, chunk):
                    if failure == "synthesis":
                        raise tts.SpeechError("Audio generation failed")
                    return b"generated audio"

                self.http_client.post.return_value = httpx.Response(500)
                with patch.object(tts.tempfile, "NamedTemporaryFile", side_effect=tracked_temp(paths)), \
                     patch.object(tts, "synthesize", AsyncMock(side_effect=save)):
                    with self.assertLogs(level="ERROR"):
                        await tts.run_tts(self.bot, 123, "Hello", "Hello", 456)
                self.assertIn("failed", self.edit.call_args.args[2].lower())
                if failure == "synthesis":
                    self.assertEqual(paths, [])
                else:
                    self.assertEqual(len(paths), 1)
                    self.assertFalse(paths[0].exists())

    async def test_failed_done_notice_does_not_retry_delivered_voice(self):
        self.edit.side_effect = TelegramError("Edit failed")
        with patch.object(tts, "synthesize", AsyncMock(return_value=b"audio")):
            with self.assertLogs("bot.tts", level="WARNING"):
                await tts.run_tts(self.bot, 123, "Hello", "Hello", 456)
        self.http_client.post.assert_awaited_once()

    async def test_unexpected_failure_is_retried_and_audio_cleaned(self):
        paths = []

        with patch.object(tts.tempfile, "NamedTemporaryFile", side_effect=tracked_temp(paths)), \
             patch.object(tts, "synthesize", AsyncMock(return_value=b"audio")), \
             patch.object(self.bot, "send_voice", AsyncMock(side_effect=OSError("Disk failed"))):
            with self.assertRaisesRegex(OSError, "Disk failed"):
                await tts.run_tts(self.bot, 123, "Hello", "Hello", 456)
        self.assertEqual(len(paths), 1)
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
            {"command": "url", "description": "Read a blog post as audio"},
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
            with self.subTest(text=text[:40]):
                audio = await tts.synthesize(self.client, text)
                self.assertEqual(audio, b"audio")
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
                    await tts.synthesize(self.client, "Hello")
                self.handle.assert_called_once()

    async def test_empty_or_non_audio_responses_are_rejected(self):
        for response in (
            httpx.Response(200, content=b"", headers={"Content-Type": "audio/mpeg"}),
            httpx.Response(200, content=b"error", headers={"Content-Type": "text/plain"}),
        ):
            self.handle.return_value = response
            with self.assertRaisesRegex(tts.SpeechError, "invalid or empty"):
                await tts.synthesize(self.client, "Hello")

    async def test_configuration_and_network_failures_are_reported(self):
        with patch.dict(os.environ, {"SPEECH_REGION": "invalid/region"}):
            with self.assertRaisesRegex(tts.SpeechError, "not configured"):
                await tts.synthesize(self.client, "Hello")
        self.handle.assert_not_called()
        self.handle.side_effect = httpx.ReadTimeout("Timed out")
        with self.assertLogs("bot.tts", level="ERROR"):
            with self.assertRaisesRegex(tts.SpeechError, "Could not reach"):
                await tts.synthesize(self.client, "Hello")


if __name__ == "__main__":
    unittest.main()
