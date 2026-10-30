"""Durable duplicate detection and per-chat cooldowns across Function instances."""
import logging
import time
from datetime import datetime, timedelta, timezone

from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError
from azure.data.tables import TableTransactionError, UpdateMode
from azure.data.tables.aio import TableClient

from main import RATE_LIMIT_SECONDS

logger = logging.getLogger("bot.storage")
TABLE_NAME = "TelegramBotState"
UPDATE_LEASE_SECONDS = 360


class UpdateInProgressError(RuntimeError):
    pass


class BotState:
    def __init__(self, table: TableClient):
        self.table = table
        self.update_id: int | None = None

    async def claim_update(self, update_id: int) -> bool:
        self.update_id = update_id
        now = time.time()
        entity = {
            "PartitionKey": "bot",
            "RowKey": f"update-{update_id}",
            "Status": "processing",
            "LeaseUntil": now + UPDATE_LEASE_SECONDS,
        }
        try:
            await self.table.create_entity(entity)
            return True
        except ResourceExistsError:
            existing = await self.table.get_entity("bot", f"update-{update_id}")
        if existing["Status"] == "done":
            return False
        if existing["LeaseUntil"] > now:
            raise UpdateInProgressError("Update is already being processed.")
        try:
            await self.table.update_entity(
                entity,
                mode=UpdateMode.MERGE,
                etag=existing.metadata["etag"],
                match_condition=MatchConditions.IfNotModified,
            )
        except ResourceModifiedError:
            raise UpdateInProgressError("Another worker claimed the update.") from None
        return True

    async def complete_update(self, update_id: int) -> None:
        await self.table.update_entity({
            "PartitionKey": "bot",
            "RowKey": f"update-{update_id}",
            "Status": "done",
            "LeaseUntil": 0.0,
        })

    async def abandon_update(self, update_id: int) -> None:
        await self.table.update_entity({
            "PartitionKey": "bot",
            "RowKey": f"update-{update_id}",
            "LeaseUntil": 0.0,
        })

    async def check_rate_limit(self, chat_id: int) -> bool:
        if self.update_id is None:
            raise RuntimeError("Claim an update before checking its cooldown.")
        for _ in range(3):
            update = await self.table.get_entity("bot", f"update-{self.update_id}")
            if "RateLimitAllowed" in update:
                return not update["RateLimitAllowed"]
            now = time.time()
            try:
                cooldown = await self.table.get_entity("bot", f"chat-{chat_id}")
            except ResourceNotFoundError:
                cooldown = None
            allowed = cooldown is None or now - cooldown["LastRequest"] >= RATE_LIMIT_SECONDS
            options = {
                "mode": UpdateMode.MERGE,
                "etag": update.metadata["etag"],
                "match_condition": MatchConditions.IfNotModified,
            }
            operations = [("update", {
                "PartitionKey": "bot",
                "RowKey": f"update-{self.update_id}",
                "RateLimitAllowed": allowed,
            }, options)]
            if allowed:
                entity = {"PartitionKey": "bot", "RowKey": f"chat-{chat_id}", "LastRequest": now}
                if cooldown is None:
                    operations.append(("create", entity))
                else:
                    operations.append(("update", entity, {
                        "mode": UpdateMode.MERGE,
                        "etag": cooldown.metadata["etag"],
                        "match_condition": MatchConditions.IfNotModified,
                    }))
            try:
                # Persist the decision and cooldown together, so retries keep their admission.
                await self.table.submit_transaction(operations)
                return not allowed
            except TableTransactionError as error:
                if error.status_code not in (409, 412):
                    raise
        raise RuntimeError("Could not reserve the chat cooldown after concurrent updates.")

    async def cleanup(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        async for entity in self.table.query_entities(
            "Timestamp lt @cutoff", parameters={"cutoff": cutoff}
        ):
            try:
                await self.table.delete_entity(
                    entity["PartitionKey"],
                    entity["RowKey"],
                    etag=entity.metadata["etag"],
                    match_condition=MatchConditions.IfNotModified,
                )
            except (ResourceModifiedError, ResourceNotFoundError):
                logger.debug("Skipping state changed during cleanup")
