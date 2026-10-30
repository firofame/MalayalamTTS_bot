import base64
import inspect
import json
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

import azure.functions as func
import httpx

import function_app

SETTINGS = {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true", "BOT_TOKEN": "test-token",
    "TELEGRAM_WEBHOOK_SECRET": "test-secret", "SPEECH_KEY": "test-key", "SPEECH_REGION": "centralindia",
}
UPDATE = {
    "update_id": 321,
    "message": {"chat": {"id": 123}, "message_id": 456, "text": "Hello"},
}


class FunctionTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.functions = {item.get_function_name(): item for item in function_app.app.get_functions()}

    async def asyncSetUp(self):
        settings = patch.dict(os.environ, SETTINGS)
        settings.start()
        self.addCleanup(settings.stop)

    def request(self, data=UPDATE, secret="test-secret", raw=None):
        return func.HttpRequest(
            method="POST", url="https://example.test/api/telegram",
            headers={"X-Telegram-Bot-Api-Secret-Token": secret},
            body=json.dumps(data).encode() if raw is None else raw,
        )

    async def webhook(self, **kwargs):
        output = Mock()
        response = await self.functions["telegram"](self.request(**kwargs), output)
        return response, output

    async def test_authenticated_webhook_only_queues_work_and_removes_profile_metadata(self):
        data = json.loads(json.dumps(UPDATE))
        data["message"]["from"] = {"first_name": "Not stored"}
        response, output = await self.webhook(data=data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.get_body()), {"status": "queued"})
        self.assertEqual(json.loads(output.set.call_args.args[0]), UPDATE)

    async def test_command_entities_survive_queue_normalization(self):
        data = json.loads(json.dumps(UPDATE))
        data["message"].update({
            "text": "/tts\nHello", "entities": [
                {"type": "bot_command", "offset": 0, "length": 4},
                {"type": "text_link", "offset": 5, "length": 5, "url": "https://example.test"},
            ],
        })
        response, output = await self.webhook(data=data)
        self.assertEqual(response.status_code, 200)
        queued = json.loads(output.set.call_args.args[0])
        self.assertEqual(queued["message"]["entities"], [
            {"type": "bot_command", "offset": 0, "length": 4},
        ])

    async def test_missing_wrong_and_non_ascii_secrets_are_rejected(self):
        for secret in ("", "wrong", "\u2603"):
            with self.subTest(secret=secret), self.assertLogs("bot.functions", level="WARNING"):
                response, output = await self.webhook(secret=secret)
                self.assertEqual(response.status_code, 401)
                output.set.assert_not_called()

    async def test_unconfigured_webhook_and_health_fail_explicitly(self):
        with patch.dict(os.environ, {"SPEECH_KEY": ""}), self.assertLogs("bot.functions", level="ERROR"):
            response, output = await self.webhook()
            self.assertEqual(response.status_code, 503)
            output.set.assert_not_called()
            health = await self.functions["health"](self.request())
            self.assertEqual(health.status_code, 503)

    async def test_health_response(self):
        response = await self.functions["health"](self.request())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.get_body())["webhook"], "/api/telegram")

    async def test_invalid_payloads_do_not_enqueue(self):
        for raw, status in (
            (b"{}", 400), (b"[]", 400), (b"null", 400), (b"invalid", 400), (b"\xff", 400),
            (b" " * (function_app.MAX_WEBHOOK_BYTES + 1), 413),
            (json.dumps({"update_id": True}).encode(), 400),
            (json.dumps({"update_id": 1, "message": {"text": "Hi", "chat": {"id": "123"}}}).encode(), 400),
        ):
            with self.subTest(raw=raw[:80]), self.assertLogs("bot.functions", level="WARNING"):
                response, output = await self.webhook(raw=raw)
                self.assertEqual(response.status_code, status)
                output.set.assert_not_called()

    async def test_non_text_messages_are_ignored(self):
        for message in (None, {}, {"photo": []}, {"text": 1}):
            response, output = await self.webhook(data={"update_id": 1, "message": message})
            self.assertEqual(json.loads(response.get_body()), {"status": "ignored"})
            output.set.assert_not_called()

    async def test_full_unicode_message_fits_storage_queue_base64_limit(self):
        data = json.loads(json.dumps(UPDATE))
        data["message"]["text"] = "\U0001f600" * 4000
        response, output = await self.webhook(data=data)
        self.assertEqual(response.status_code, 200)
        encoded = base64.b64encode(output.set.call_args.args[0].encode())
        self.assertLessEqual(len(encoded), 65536)
        self.assertEqual(json.loads(base64.b64decode(encoded)), data)

    async def test_queue_worker_shares_client_and_marks_completion(self):
        state = Mock(
            claim_update=AsyncMock(return_value=True), complete_update=AsyncMock(), abandon_update=AsyncMock(),
        )
        table = AsyncMock()
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"ok": True})
        ))
        message = Mock(get_body=Mock(return_value=json.dumps(UPDATE).encode()))
        with (
            patch.object(function_app.TableClient, "from_connection_string", return_value=table),
            patch.object(function_app, "BotState", return_value=state),
            patch.object(function_app.httpx, "AsyncClient", return_value=client),
            patch.object(function_app, "handle_update", AsyncMock()) as handle,
        ):
            await self.functions["process_update"](message)
            state.claim_update.assert_awaited_once_with(321)
            args = handle.call_args.args
            self.assertEqual(args[0], UPDATE)
            self.assertIs(args[1].http_client, client)
            self.assertIs(args[2], state)
            state.complete_update.assert_awaited_once_with(321)
            state.abandon_update.assert_not_awaited()
            self.assertTrue(client.is_closed)
        table.__aexit__.assert_awaited_once()

    async def test_completed_update_is_not_processed_again(self):
        state = Mock(claim_update=AsyncMock(return_value=False), complete_update=AsyncMock())
        with (
            patch.object(function_app.TableClient, "from_connection_string", return_value=AsyncMock()),
            patch.object(function_app, "BotState", return_value=state),
            patch.object(function_app, "handle_update", AsyncMock()) as handle,
        ):
            await self.functions["process_update"](Mock(get_body=lambda: json.dumps(UPDATE).encode()))
            handle.assert_not_awaited()
            state.complete_update.assert_not_awaited()

    async def test_failed_worker_releases_claim_and_raises_for_queue_retry(self):
        state = Mock(
            claim_update=AsyncMock(return_value=True), complete_update=AsyncMock(), abandon_update=AsyncMock(),
        )
        with (
            patch.object(function_app.TableClient, "from_connection_string", return_value=AsyncMock()),
            patch.object(function_app, "BotState", return_value=state),
            patch.object(function_app, "handle_update", AsyncMock(side_effect=RuntimeError("Offline"))),
            self.assertLogs("bot.functions", level="ERROR"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Offline"):
                await self.functions["process_update"](Mock(get_body=lambda: json.dumps(UPDATE).encode()))
            state.abandon_update.assert_awaited_once_with(321)
            state.complete_update.assert_not_awaited()

    async def test_timer_runs_state_cleanup(self):
        state = Mock(cleanup=AsyncMock())
        with (
            patch.object(function_app.TableClient, "from_connection_string", return_value=AsyncMock()),
            patch.object(function_app, "BotState", return_value=state),
        ):
            await self.functions["cleanup_state"](Mock())
        state.cleanup.assert_awaited_once()

    def test_queue_and_http_binding_configuration(self):
        bindings = json.loads(self.functions["telegram"].get_function_json())["bindings"]
        queued = next(binding for binding in bindings if binding["type"] == "queue")
        self.assertEqual(queued["queueName"], "telegram-updates")
        self.assertEqual(queued["connection"], "AzureWebJobsStorage")
        webhook = next(binding for binding in bindings if binding["type"] == "httpTrigger")
        self.assertEqual(webhook["route"], "telegram")
        self.assertEqual(webhook["authLevel"].lower(), "anonymous")

    def test_all_binding_names_match_python_handler_parameters(self):
        for name, function in self.functions.items():
            with self.subTest(function=name):
                parameters = set(inspect.signature(function.get_user_function()).parameters)
                bindings = {
                    binding.name for binding in function.get_bindings() if binding.name != "$return"
                }
                self.assertEqual(parameters, bindings)
