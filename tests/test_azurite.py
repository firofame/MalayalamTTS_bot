"""Optional real Azure Tables integration tests, isolated to a local emulator."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import secrets
import unittest
from unittest.mock import Mock, patch

from azure.data.tables.aio import TableClient, TableServiceClient
import httpx

import function_app
from storage import BotState, UpdateInProgressError
from telegram_client import TelegramError


@unittest.skipUnless(os.environ.get("AZURITE_TABLE_URL"), "Local Azurite is not configured")
class AzuriteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.endpoint = os.environ["AZURITE_TABLE_URL"]
        if not self.endpoint.startswith("http://127.0.0.1:"):
            self.fail("Integration tests may only connect to a loopback Azurite endpoint.")
        defaults = TableClient.from_connection_string("UseDevelopmentStorage=true", "Defaults")
        self.credential = defaults.credential
        self.service = TableServiceClient(self.endpoint, credential=self.credential)
        self.addAsyncCleanup(self.service.close)
        self.name = "BotTest" + secrets.token_hex(8)
        self.table = await self.service.create_table(self.name)
        self.addAsyncCleanup(self.table.close)
        self.addAsyncCleanup(self.service.delete_table, self.name)

    async def test_parallel_chat_requests_have_one_atomic_cooldown_winner(self):
        states = [BotState(self.table) for _ in range(8)]
        await asyncio.gather(*(state.claim_update(index) for index, state in enumerate(states)))
        results = await asyncio.gather(*(state.check_rate_limit(123) for state in states))
        self.assertEqual(results.count(False), 1)
        self.assertEqual(results.count(True), 7)
        for state, result in zip(states, results):
            self.assertEqual(await state.check_rate_limit(123), result)

    async def test_lease_recovery_retains_admission_and_completed_duplicates_are_skipped(self):
        original = BotState(self.table)
        self.assertTrue(await original.claim_update(123))
        self.assertFalse(await original.check_rate_limit(456))
        with self.assertRaises(UpdateInProgressError):
            await BotState(self.table).claim_update(123)
        await original.abandon_update(123)
        retry = BotState(self.table)
        self.assertTrue(await retry.claim_update(123))
        self.assertFalse(await retry.check_rate_limit(456))
        await retry.complete_update(123)
        self.assertFalse(await BotState(self.table).claim_update(123))

    async def test_cleanup_query_and_conditional_deletes_work_with_real_sdk(self):
        state = BotState(self.table)
        await state.claim_update(123)
        with patch("storage.datetime") as clock:
            clock.now.return_value = datetime.now(timezone.utc) + timedelta(days=8)
            await state.cleanup()
        remaining = [row async for row in self.table.list_entities()]
        self.assertEqual(remaining, [])

    async def test_queued_worker_synthesizes_once_and_skips_duplicate_delivery(self):
        await self.run_worker_pipeline(fail_first_message=False)

    async def test_worker_retry_is_not_blocked_by_its_own_cooldown(self):
        await self.run_worker_pipeline(fail_first_message=True)

    async def run_worker_pipeline(self, fail_first_message):
        requests = []
        should_fail = fail_first_message

        def respond(request):
            nonlocal should_fail
            requests.append(request)
            if request.url.path.endswith("/sendMessage") and should_fail:
                should_fail = False
                return httpx.Response(503)
            if request.url.host.endswith(".tts.speech.microsoft.com"):
                return httpx.Response(200, content=b"test audio", headers={"Content-Type": "audio/mpeg"})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 789}})

        client_type = httpx.AsyncClient

        def http_client(**kwargs):
            return client_type(transport=httpx.MockTransport(respond), **kwargs)

        def table_client(*args):
            return TableClient(self.endpoint, self.name, credential=self.credential)

        data = {
            "update_id": 123,
            "message": {"chat": {"id": 456}, "message_id": 789, "text": "a" * 4000},
        }
        with (
            patch.dict(os.environ, {
                "BOT_TOKEN": "test-token", "AzureWebJobsStorage": "UseDevelopmentStorage=true",
                "SPEECH_KEY": "test-key", "SPEECH_REGION": "centralindia",
            }),
            patch.object(function_app.TableClient, "from_connection_string", side_effect=table_client),
            patch.object(function_app.httpx, "AsyncClient", side_effect=http_client),
        ):
            message = Mock(get_body=lambda: json.dumps(data).encode())
            if fail_first_message:
                with self.assertLogs(level="ERROR"), self.assertRaises(TelegramError):
                    await function_app.process_update(message)
            await function_app.process_update(message)
            await function_app.process_update(message)
        speech = [request for request in requests if request.url.host.endswith(".tts.speech.microsoft.com")]
        uploads = [request for request in requests if request.url.path.endswith("/sendVoice")]
        self.assertEqual(len(speech), 1)
        self.assertEqual(len(uploads), 1)
        self.assertIn(b"a" * 4000, speech[0].content)
        self.assertIn(b"test audio", uploads[0].content)
        self.assertEqual((await self.table.get_entity("bot", "update-123"))["Status"], "done")
