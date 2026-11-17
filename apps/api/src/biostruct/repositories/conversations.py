"""Reads and writes for the conversation model (M6).

Two things in here are load-bearing and easy to get subtly wrong.

**Ordering.** Message order comes from an integer ``seq``, allocated by
``$inc`` on the session document inside ``find_one_and_update``. That is a
single atomic operation on one document, so two concurrent appends get 7 and 8
rather than both getting 7. Timestamps cannot do this job: an assistant turn and
the tool result it triggered are routinely written in the same millisecond, and
a thread rendered in the wrong order misattributes facts to the wrong step.

**Size.** A tool result can be a 2000-molecule library or a structure file, and
Mongo refuses any document over 16 MB. A write that fails at the end of an
expensive paid run would lose the result we just paid for, so an oversized
payload is written to object storage before the message is inserted, and the
message keeps a reference. The spill is not an error path; it is the normal path
for big results.

Every read filters on ``user_id`` **inside the query**, as in
``ProjectRepository``: a tenant filter applied in Python is one forgotten line
away from serving another researcher's unpublished work.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from biostruct.db import money_from_db, money_to_db
from biostruct.models.conversation import (
    PAYLOAD_INLINE_MAX_BYTES,
    SCHEMA_VERSION,
    Artifact,
    ArtifactCreate,
    Block,
    Citation,
    Message,
    MessageCreate,
    ResearchSession,
    Role,
    SessionCreate,
    SessionStatus,
    SessionUpdate,
    ToolCall,
    ToolResult,
    Usage,
)
from biostruct.models.project import Budget, Infection, RankingWeights
from biostruct.storage import StorageBackend, StorageError

log = logging.getLogger(__name__)

SESSIONS = "research_sessions"
MESSAGES = "messages"
ARTIFACTS = "artifacts"


def _payload_size(payload: dict[str, Any]) -> int:
    """Rough encoded size, used only to decide whether to spill.

    ``default=str`` so a ``Decimal`` or ``datetime`` in a tool payload is
    measured rather than raising — this function must never be the thing that
    fails a write.
    """
    try:
        return len(json.dumps(payload, default=str).encode())
    except (TypeError, ValueError):
        return PAYLOAD_INLINE_MAX_BYTES + 1


def _money_out(value: Decimal | None) -> str | None:
    return money_to_db(value) if value is not None else None


def _money_in(value: Any) -> Decimal | None:
    return money_from_db(value) if value is not None else None


class ConversationRepository:
    def __init__(
        self,
        db: AsyncIOMotorDatabase[dict[str, Any]],
        *,
        storage: StorageBackend | None = None,
    ) -> None:
        self.db = db
        self._storage = storage

    # ------------------------------------------------------------------ sessions

    @staticmethod
    def _to_session(doc: dict[str, Any]) -> ResearchSession:
        budget = doc.get("budget") or {}
        infection = doc.get("infection")
        return ResearchSession(
            id=doc["_id"],
            user_id=doc["user_id"],
            title=doc.get("title", ""),
            infection=Infection(**infection) if infection else None,
            status=SessionStatus(doc.get("status", SessionStatus.active)),
            ranking_weights=RankingWeights(**(doc.get("ranking_weights") or {})),
            budget=Budget(
                limit_usd=money_from_db(budget.get("limit_usd", "0")),
                spent_usd=money_from_db(budget.get("spent_usd", "0")),
                reserved_usd=money_from_db(budget.get("reserved_usd", "0")),
            ),
            message_count=int(doc.get("message_count", 0)),
            last_seq=int(doc.get("last_seq", 0)),
            last_message_at=doc.get("last_message_at"),
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
            schema_version=int(doc.get("schema_version", SCHEMA_VERSION)),
            migrated_from=doc.get("migrated_from", ""),
        )

    async def create_session(
        self, *, user_id: str, data: SessionCreate, default_budget_usd: Decimal
    ) -> ResearchSession:
        now = datetime.now(UTC)
        limit = data.budget_usd if data.budget_usd is not None else default_budget_usd
        doc: dict[str, Any] = {
            "_id": str(uuid.uuid4()),
            "user_id": user_id,
            "title": data.title,
            "infection": data.infection.model_dump() if data.infection else None,
            "status": SessionStatus.active.value,
            "ranking_weights": {k: str(v) for k, v in RankingWeights().as_mapping().items()},
            "budget": {
                "limit_usd": money_to_db(limit),
                "spent_usd": money_to_db(Decimal("0")),
                "reserved_usd": money_to_db(Decimal("0")),
            },
            "message_count": 0,
            "last_seq": 0,
            "last_message_at": None,
            "created_at": now,
            "updated_at": now,
            "schema_version": SCHEMA_VERSION,
            "migrated_from": "",
        }
        await self.db[SESSIONS].insert_one(doc)
        return self._to_session(doc)

    async def get_session(self, session_id: str, *, user_id: str) -> ResearchSession | None:
        doc = await self.db[SESSIONS].find_one({"_id": session_id, "user_id": user_id})
        return self._to_session(doc) if doc else None

    async def list_sessions(
        self,
        user_id: str,
        *,
        status: SessionStatus | None = SessionStatus.active,
        limit: int = 50,
        skip: int = 0,
    ) -> list[ResearchSession]:
        """Most recently *active* first, not most recently created.

        A researcher returning after a week wants the conversation they were in,
        which is rarely the newest one. ``last_message_at`` is null on a session
        nobody has written to, so ``created_at`` is the tiebreak.
        """
        query: dict[str, Any] = {"user_id": user_id}
        if status is not None:
            query["status"] = status.value
        cursor = (
            self.db[SESSIONS]
            .find(query)
            .sort([("last_message_at", -1), ("created_at", -1)])
            .skip(skip)
            .limit(limit)
        )
        return [self._to_session(d) async for d in cursor]

    async def count_sessions(
        self, user_id: str, *, status: SessionStatus | None = SessionStatus.active
    ) -> int:
        query: dict[str, Any] = {"user_id": user_id}
        if status is not None:
            query["status"] = status.value
        return int(await self.db[SESSIONS].count_documents(query))

    async def update_session(
        self, session_id: str, *, user_id: str, data: SessionUpdate
    ) -> ResearchSession | None:
        update: dict[str, Any] = {}
        if data.title is not None:
            update["title"] = data.title
        if data.infection is not None:
            update["infection"] = data.infection.model_dump()
        if data.status is not None:
            update["status"] = data.status.value
        if data.ranking_weights is not None:
            update["ranking_weights"] = {
                k: str(v) for k, v in data.ranking_weights.as_mapping().items()
            }
        if not update:
            return await self.get_session(session_id, user_id=user_id)
        update["updated_at"] = datetime.now(UTC)
        doc = await self.db[SESSIONS].find_one_and_update(
            {"_id": session_id, "user_id": user_id},
            {"$set": update},
            return_document=True,
        )
        return self._to_session(doc) if doc else None

    # ------------------------------------------------------------------ messages

    def _to_message(self, doc: dict[str, Any]) -> Message:
        return Message(
            id=doc["_id"],
            session_id=doc["session_id"],
            user_id=doc["user_id"],
            seq=int(doc["seq"]),
            role=Role(doc["role"]),
            blocks=[
                Block(
                    kind=b.get("kind", "text"),
                    text=b.get("text", ""),
                    estimate_usd=_money_in(b.get("estimate_usd")),
                    flagged=tuple(b.get("flagged") or ()),
                )
                for b in doc.get("blocks") or []
            ],
            tool_calls=[ToolCall(**c) for c in doc.get("tool_calls") or []],
            tool_results=[
                ToolResult(
                    call_id=r.get("call_id", ""),
                    name=r.get("name", ""),
                    summary=r.get("summary", ""),
                    payload=r.get("payload"),
                    payload_ref=r.get("payload_ref", ""),
                    ok=bool(r.get("ok", True)),
                    error=r.get("error", ""),
                    cost_usd=_money_in(r.get("cost_usd")),
                )
                for r in doc.get("tool_results") or []
            ],
            citations=[Citation(**c) for c in doc.get("citations") or []],
            usage=Usage(**doc["usage"]) if doc.get("usage") else None,
            spoken=doc.get("spoken", ""),
            spoken_language=doc.get("spoken_language", ""),
            created_at=doc["created_at"],
            schema_version=int(doc.get("schema_version", SCHEMA_VERSION)),
        )

    async def _spill(self, session_id: str, result: ToolResult) -> dict[str, Any]:
        """Write an oversized payload to object storage, keep a reference.

        If storage is unavailable we keep the ``summary`` and record the failure
        on the result rather than losing the whole message: the summary is what
        the agent reasons from, and a paid run whose result failed to save is
        the single most expensive bug available here.
        """
        body = result.model_dump(mode="json", exclude={"payload"})
        payload = result.payload or {}
        if self._storage is None:
            body["payload"] = None
            body["ok"] = result.ok
            body["error"] = (
                result.error
                or "the full result was too large to store inline and no object store is configured"
            )
            log.warning("tool result for %s spilled with no storage backend", result.name)
            return body
        key = f"tool-results/{session_id}/{uuid.uuid4()}.json"
        try:
            await self._storage.put_bytes(
                key,
                json.dumps(payload, default=str).encode(),
                content_type="application/json",
            )
        except StorageError as exc:
            log.warning("could not store tool result payload: %s", exc)
            body["payload"] = None
            body["error"] = result.error or f"the full result could not be stored: {exc}"
            return body
        body["payload"] = None
        body["payload_ref"] = key
        return body

    async def append_message(
        self, *, session_id: str, user_id: str, data: MessageCreate
    ) -> Message | None:
        """Append one message, allocating its ``seq`` atomically.

        Returns ``None`` when the session does not exist or is not this user's —
        the same ``find_one_and_update`` both authorises the write and allocates
        the number, so there is no window where a sequence is burned for a
        session the caller cannot write to.
        """
        now = datetime.now(UTC)
        session_doc = await self.db[SESSIONS].find_one_and_update(
            {"_id": session_id, "user_id": user_id},
            {
                "$inc": {"last_seq": 1, "message_count": 1},
                "$set": {"last_message_at": now, "updated_at": now},
            },
            return_document=True,
        )
        if session_doc is None:
            return None

        results: list[dict[str, Any]] = []
        for result in data.tool_results:
            if result.payload is not None and _payload_size(result.payload) > (
                PAYLOAD_INLINE_MAX_BYTES
            ):
                results.append(await self._spill(session_id, result))
            else:
                body = result.model_dump(mode="json")
                body["cost_usd"] = _money_out(result.cost_usd)
                results.append(body)

        doc: dict[str, Any] = {
            "_id": str(uuid.uuid4()),
            "session_id": session_id,
            "user_id": user_id,
            "seq": int(session_doc["last_seq"]),
            "role": data.role.value,
            "blocks": [
                {
                    "kind": b.kind.value,
                    "text": b.text,
                    "estimate_usd": _money_out(b.estimate_usd),
                    "flagged": list(b.flagged),
                }
                for b in data.blocks
            ],
            "tool_calls": [c.model_dump(mode="json") for c in data.tool_calls],
            "tool_results": results,
            "citations": [c.model_dump(mode="json") for c in data.citations],
            "usage": data.usage.model_dump() if data.usage else None,
            "spoken": data.spoken,
            "spoken_language": data.spoken_language,
            "created_at": now,
            "schema_version": SCHEMA_VERSION,
        }
        await self.db[MESSAGES].insert_one(doc)
        return self._to_message(doc)

    async def list_messages(
        self, session_id: str, *, user_id: str, after_seq: int = 0, limit: int = 200
    ) -> list[Message]:
        """The thread in order. ``after_seq`` is how the UI tails a live turn."""
        cursor = (
            self.db[MESSAGES]
            .find({"session_id": session_id, "user_id": user_id, "seq": {"$gt": after_seq}})
            .sort("seq", 1)
            .limit(limit)
        )
        return [self._to_message(d) async for d in cursor]

    async def get_message(self, message_id: str, *, user_id: str) -> Message | None:
        doc = await self.db[MESSAGES].find_one({"_id": message_id, "user_id": user_id})
        return self._to_message(doc) if doc else None

    async def load_payload(self, result: ToolResult) -> dict[str, Any] | None:
        """Fetch a spilled payload back. This is the just-in-time re-read."""
        if result.payload is not None:
            return result.payload
        if not result.payload_ref or self._storage is None:
            return None
        try:
            raw = await self._storage.get_bytes(result.payload_ref)
        except StorageError as exc:
            log.warning("could not read spilled tool result %s: %s", result.payload_ref, exc)
            return None
        try:
            loaded = json.loads(raw)
        except ValueError:
            return None
        return loaded if isinstance(loaded, dict) else None

    # ----------------------------------------------------------------- artifacts

    @staticmethod
    def _to_artifact(doc: dict[str, Any]) -> Artifact:
        return Artifact(
            id=doc["_id"],
            session_id=doc["session_id"],
            user_id=doc["user_id"],
            seq=int(doc.get("seq", 0)),
            kind=doc["kind"],
            title=doc.get("title", ""),
            payload=doc.get("payload") or {},
            run_id=doc.get("run_id", ""),
            message_id=doc.get("message_id", ""),
            storage_key=doc.get("storage_key", ""),
            created_at=doc["created_at"],
            schema_version=int(doc.get("schema_version", SCHEMA_VERSION)),
        )

    async def add_artifact(
        self, *, session_id: str, user_id: str, data: ArtifactCreate
    ) -> Artifact | None:
        """Only onto a session the caller owns, checked in the database.

        ``seq`` is allocated the same way a message's is, and for the same
        reason: one turn routinely produces several artifacts within the same
        millisecond, and ordering them by ``created_at`` alone leaves the canvas
        in arbitrary order. The counter is separate from the message counter so
        the two numbers stay meaningful on their own.
        """
        owned = await self.db[SESSIONS].find_one_and_update(
            {"_id": session_id, "user_id": user_id},
            {"$inc": {"last_artifact_seq": 1}},
            return_document=True,
        )
        if owned is None:
            return None
        doc: dict[str, Any] = {
            "_id": str(uuid.uuid4()),
            "session_id": session_id,
            "user_id": user_id,
            "seq": int(owned.get("last_artifact_seq", 1)),
            "kind": data.kind.value,
            "title": data.title,
            "payload": data.payload,
            "run_id": data.run_id,
            "message_id": data.message_id,
            "storage_key": data.storage_key,
            "created_at": datetime.now(UTC),
            "schema_version": SCHEMA_VERSION,
        }
        await self.db[ARTIFACTS].insert_one(doc)
        return self._to_artifact(doc)

    async def list_artifacts(
        self, session_id: str, *, user_id: str, limit: int = 100
    ) -> list[Artifact]:
        cursor = (
            self.db[ARTIFACTS]
            .find({"session_id": session_id, "user_id": user_id})
            # Newest first, by the allocated sequence rather than the clock:
            # several artifacts from one turn share a millisecond.
            .sort("seq", -1)
            .limit(limit)
        )
        return [self._to_artifact(d) async for d in cursor]


__all__ = ["ARTIFACTS", "MESSAGES", "SESSIONS", "ConversationRepository"]
