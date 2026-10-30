import unittest
from unittest.mock import AsyncMock, patch

from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError
from azure.data.tables import TableEntity, TableTransactionError

from storage import BotState, UpdateInProgressError


def entity(**fields):
    result = TableEntity(fields)
    result._metadata = {"etag": "test-etag"}
    return result


class StateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.table = AsyncMock()
        self.state = BotState(self.table)
        clock = patch("storage.time.time", return_value=1000.0)
        clock.start()
        self.addCleanup(clock.stop)

    async def test_new_update_is_claimed_and_completed(self):
        self.assertTrue(await self.state.claim_update(123))
        self.assertEqual(self.table.create_entity.call_args.args[0], {
            "PartitionKey": "bot", "RowKey": "update-123",
            "Status": "processing", "LeaseUntil": 1360.0,
        })
        await self.state.complete_update(123)
        self.assertEqual(self.table.update_entity.call_args.args[0]["Status"], "done")

    async def test_completed_duplicate_is_skipped(self):
        self.table.create_entity.side_effect = ResourceExistsError()
        self.table.get_entity.return_value = entity(Status="done")
        self.assertFalse(await self.state.claim_update(123))
        self.table.update_entity.assert_not_awaited()

    async def test_active_duplicate_retries_instead_of_acknowledging(self):
        self.table.create_entity.side_effect = ResourceExistsError()
        self.table.get_entity.return_value = entity(Status="processing", LeaseUntil=1100.0)
        with self.assertRaises(UpdateInProgressError):
            await self.state.claim_update(123)

    async def test_expired_claim_is_recovered_without_losing_admission(self):
        self.table.create_entity.side_effect = ResourceExistsError()
        self.table.get_entity.return_value = entity(Status="processing", LeaseUntil=900.0, RateLimitAllowed=True)
        self.assertTrue(await self.state.claim_update(123))
        options = self.table.update_entity.call_args.kwargs
        self.assertEqual(options["etag"], "test-etag")
        self.assertEqual(options["mode"], "merge")
        self.assertEqual(options["match_condition"], MatchConditions.IfNotModified)

    async def test_simultaneous_recovery_does_not_process_twice(self):
        self.table.create_entity.side_effect = ResourceExistsError()
        self.table.get_entity.return_value = entity(Status="processing", LeaseUntil=900.0)
        self.table.update_entity.side_effect = ResourceModifiedError()
        with self.assertRaises(UpdateInProgressError):
            await self.state.claim_update(123)

    async def test_failed_update_is_released_for_retry(self):
        await self.state.abandon_update(123)
        self.assertEqual(self.table.update_entity.call_args.args[0]["LeaseUntil"], 0.0)

    async def test_first_request_records_admission_and_cooldown_atomically(self):
        await self.state.claim_update(123)
        self.table.get_entity.side_effect = [entity(), ResourceNotFoundError()]
        self.assertFalse(await self.state.check_rate_limit(456))
        operations = self.table.submit_transaction.call_args.args[0]
        self.assertTrue(operations[0][1]["RateLimitAllowed"])
        self.assertEqual(operations[1], ("create", {
            "PartitionKey": "bot", "RowKey": "chat-456", "LastRequest": 1000.0,
        }))

    async def test_cooldown_threshold_is_exactly_30_seconds(self):
        for previous, limited in ((970.001, True), (970.0, False), (969.0, False)):
            with self.subTest(previous=previous):
                await self.state.claim_update(123)
                self.table.get_entity.side_effect = [entity(), entity(LastRequest=previous)]
                self.assertEqual(await self.state.check_rate_limit(456), limited)
                operations = self.table.submit_transaction.call_args.args[0]
                self.assertEqual(operations[0][1]["RateLimitAllowed"], not limited)
                self.assertEqual(len(operations), 1 if limited else 2)

    async def test_retry_reuses_original_rate_limit_decision(self):
        for allowed in (False, True):
            with self.subTest(allowed=allowed):
                await self.state.claim_update(123)
                self.table.get_entity.return_value = entity(RateLimitAllowed=allowed)
                self.assertEqual(await self.state.check_rate_limit(456), not allowed)
                self.table.submit_transaction.assert_not_awaited()

    async def test_concurrent_cooldown_winner_is_respected(self):
        await self.state.claim_update(123)
        self.table.get_entity.side_effect = [
            entity(), entity(LastRequest=900.0), entity(), entity(LastRequest=999.0),
        ]
        collision = TableTransactionError(message="Concurrent modification")
        collision.status_code = 412
        self.table.submit_transaction.side_effect = [collision, []]
        self.assertTrue(await self.state.check_rate_limit(456))
        self.assertEqual(self.table.submit_transaction.await_count, 2)

    async def test_storage_errors_propagate_instead_of_bypassing_limits(self):
        await self.state.claim_update(123)
        self.table.get_entity.side_effect = RuntimeError("Storage unavailable")
        with self.assertRaisesRegex(RuntimeError, "Storage unavailable"):
            await self.state.check_rate_limit(456)

    async def test_unclaimed_update_cannot_consume_cooldown(self):
        with self.assertRaisesRegex(RuntimeError, "Claim an update"):
            await self.state.check_rate_limit(456)

    async def test_cleanup_deletes_old_entities_conditionally(self):
        async def results():
            yield entity(PartitionKey="bot", RowKey="update-123")
            yield entity(PartitionKey="bot", RowKey="chat-456")

        self.table.query_entities = lambda *args, **kwargs: results()
        self.table.delete_entity.side_effect = [ResourceModifiedError(), None]
        await self.state.cleanup()
        self.assertEqual(self.table.delete_entity.await_count, 2)
        self.assertEqual(self.table.delete_entity.call_args.kwargs["etag"], "test-etag")
