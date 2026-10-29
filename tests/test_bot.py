import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

with patch.dict(os.environ, {"BOT_TOKEN": "test-token", "GOOGLE_API_KEY": "test-key"}):
    import main
    import translate


class WebhookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main._rate_limits.clear()
        self.addCleanup(main._rate_limits.clear)
        self.send_message = AsyncMock(return_value={})
        self.run_tts = AsyncMock()
        for name, replacement in (
            ("send_message", self.send_message),
            ("_run_tts_async", self.run_tts),
        ):
            patcher = patch.object(main, name, replacement)
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
                self.run_tts.assert_awaited_once_with(123, "Hello world", 456)

    async def test_links_are_passed_as_literal_text(self):
        text = "https://example.com/recording.mp3"
        for prefix in ("", "/tts "):
            with self.subTest(prefix=prefix):
                main._rate_limits.clear()
                self.run_tts.reset_mock()
                await self.send(prefix + text)
                self.run_tts.assert_awaited_once_with(123, text, 456)

    async def test_all_input_has_same_length_limit(self):
        for prefix in ("", "/tts "):
            for content in ("x", "https://example.com/"):
                with self.subTest(prefix=prefix, content=content):
                    main._rate_limits.clear()
                    self.run_tts.reset_mock()
                    self.send_message.reset_mock()
                    text = content + "a" * (main.MAX_TEXT_LENGTH - len(content))
                    await self.send(prefix + text)
                    self.run_tts.assert_awaited_once_with(123, text, 456)
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

    async def test_empty_messages_are_ignored(self):
        result = await self.send(" \x00 ")
        self.assertEqual(result, {"status": "ignored"})
        self.run_tts.assert_not_awaited()
        self.send_message.assert_not_awaited()


class TranslationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.generate = AsyncMock(return_value=SimpleNamespace(text=" translated text "))
        client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(generate_content=self.generate),
            )
        )
        patcher = patch.object(translate, "client", client)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_plain_text_and_links_are_text_not_audio(self):
        for text in (
            "Hello world",
            "https://example.com/recording.mp3",
            "http://example.com/article",
        ):
            with self.subTest(text=text):
                result = await translate.convert_to_malayalam(text)
                self.assertEqual(result, "translated text")
                self.assertEqual(
                    self.generate.call_args.kwargs["contents"],
                    [translate.SYSTEM_PROMPT, f"Input Text to Convert:\n\n{text}"],
                )

    async def test_existing_paths_are_translated_as_literal_text(self):
        with tempfile.TemporaryDirectory() as directory:
            text_path = Path(directory) / "transcript.txt"
            text_path.write_text("Local transcript", encoding="utf-8")
            audio_path = Path(directory) / "recording.mp3"
            audio_path.write_bytes(b"local audio")
            for path in (text_path, audio_path, Path(directory)):
                with self.subTest(path=path):
                    await translate.convert_to_malayalam(str(path))
                    self.assertEqual(
                        self.generate.call_args.kwargs["contents"],
                        [translate.SYSTEM_PROMPT, f"Input Text to Convert:\n\n{path}"],
                    )

    async def test_maximum_length_text_does_not_access_the_filesystem(self):
        text = "a" * main.MAX_TEXT_LENGTH
        with patch.object(translate, "Path", side_effect=AssertionError("Unexpected file access")):
            await translate.convert_to_malayalam(text)
        self.assertEqual(
            self.generate.call_args.kwargs["contents"],
            [translate.SYSTEM_PROMPT, f"Input Text to Convert:\n\n{text}"],
        )

    async def test_translation_errors_propagate(self):
        self.generate.side_effect = RuntimeError("Translation failed")
        with self.assertRaisesRegex(RuntimeError, "Translation failed"):
            await translate.convert_to_malayalam("Hello")

    async def test_empty_translation_is_an_error(self):
        self.generate.return_value = SimpleNamespace(text=None)
        with self.assertRaisesRegex(RuntimeError, "Gemini returned empty response"):
            await translate.convert_to_malayalam("Hello")


class TTSPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_translation_generates_voice_and_removes_temporary_audio(self):
        audio_paths = []

        async def save_audio(filename):
            audio_paths.append(Path(filename))
            Path(filename).write_bytes(b"generated audio")

        communicate = Mock(save=AsyncMock(side_effect=save_audio))
        http_client = SimpleNamespace(
            post=AsyncMock(return_value=httpx.Response(200, json={"ok": True}))
        )
        with (
            patch.object(main, "send_message", AsyncMock(
                return_value={"result": {"message_id": 789}}
            )) as send,
            patch.object(main, "edit_message", AsyncMock()) as edit,
            patch.object(main, "send_chat_action", AsyncMock()),
            patch.object(main, "convert_to_malayalam", AsyncMock(
                return_value="Translated text"
            )) as convert,
            patch.object(main.edge_tts, "Communicate", return_value=communicate) as tts,
            patch.object(main, "http_client", http_client),
        ):
            await main._run_tts_async(123, "Hello", 456)
        convert.assert_awaited_once_with("Hello")
        tts.assert_called_once_with("Translated text", main.VOICE)
        self.assertIn("Translating", send.call_args.args[1])
        self.assertEqual(http_client.post.call_args.args[0], f"{main.TELEGRAM_API_URL}/sendVoice")
        self.assertEqual(http_client.post.call_args.kwargs["data"]["caption"], "Translated text")
        self.assertIn("Done", edit.call_args.args[2])
        self.assertEqual(len(audio_paths), 1)
        self.assertFalse(audio_paths[0].exists())

    async def test_translation_failure_is_reported(self):
        with (
            patch.object(main, "send_message", AsyncMock(return_value={})) as send,
            patch.object(main, "send_chat_action", AsyncMock()),
            patch.object(main, "convert_to_malayalam", AsyncMock(
                side_effect=RuntimeError("Service unavailable")
            )),
            patch.object(main.edge_tts, "Communicate") as tts,
        ):
            await main._run_tts_async(123, "Hello", 456)
        tts.assert_not_called()
        self.assertIn("Service unavailable", send.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
