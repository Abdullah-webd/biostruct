from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from biostruct.db import money_from_db, money_to_db
from biostruct.models.project import (
    Budget,
    Infection,
    Project,
    ProjectCreate,
    ProjectUpdate,
    RankingWeights,
)


class ProjectRepository:
    """All reads filter on ``user_id`` inside the query itself.

    Not after the fetch. A tenant filter applied in Python is one forgotten line
    away from serving another researcher's unpublished work.
    """

    def __init__(self, db: AsyncIOMotorDatabase[dict[str, Any]]) -> None:
        self.db = db

    @staticmethod
    def _to_project(doc: dict[str, Any]) -> Project:
        budget_doc = doc["budget"]
        return Project(
            id=doc["_id"],
            user_id=doc["user_id"],
            name=doc["name"],
            infection=Infection(**doc["infection"]),
            ranking_weights=RankingWeights(**doc.get("ranking_weights", {})),
            budget=Budget(
                limit_usd=money_from_db(budget_doc["limit_usd"]),
                spent_usd=money_from_db(budget_doc["spent_usd"]),
                reserved_usd=money_from_db(budget_doc["reserved_usd"]),
            ),
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
            schema_version=doc.get("schema_version", 1),
        )

    @staticmethod
    def _weights_to_db(weights: RankingWeights) -> dict[str, str]:
        return {k: str(v) for k, v in weights.as_mapping().items()}

    async def create(
        self, *, user_id: str, data: ProjectCreate, default_budget_usd: Decimal
    ) -> Project:
        now = datetime.now(UTC)
        limit = data.budget_usd if data.budget_usd is not None else default_budget_usd
        doc = {
            "_id": str(uuid.uuid4()),
            "user_id": user_id,
            "name": data.name,
            "infection": data.infection.model_dump(),
            "ranking_weights": self._weights_to_db(RankingWeights()),
            "budget": {
                "limit_usd": money_to_db(limit),
                "spent_usd": money_to_db(Decimal("0")),
                "reserved_usd": money_to_db(Decimal("0")),
            },
            "created_at": now,
            "updated_at": now,
            "schema_version": 1,
        }
        await self.db.projects.insert_one(doc)
        return self._to_project(doc)

    async def list_for_user(self, user_id: str, *, limit: int = 50, skip: int = 0) -> list[Project]:
        cursor = (
            self.db.projects.find({"user_id": user_id})
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        return [self._to_project(d) async for d in cursor]

    async def count_for_user(self, user_id: str) -> int:
        return int(await self.db.projects.count_documents({"user_id": user_id}))

    async def get(self, project_id: str, *, user_id: str) -> Project | None:
        doc = await self.db.projects.find_one({"_id": project_id, "user_id": user_id})
        return self._to_project(doc) if doc else None

    async def update(self, project_id: str, *, user_id: str, data: ProjectUpdate) -> Project | None:
        update: dict[str, Any] = {}
        if data.name is not None:
            update["name"] = data.name
        if data.infection is not None:
            update["infection"] = data.infection.model_dump()
        if data.ranking_weights is not None:
            update["ranking_weights"] = self._weights_to_db(data.ranking_weights)
        if not update:
            return await self.get(project_id, user_id=user_id)
        update["updated_at"] = datetime.now(UTC)
        doc = await self.db.projects.find_one_and_update(
            {"_id": project_id, "user_id": user_id},
            {"$set": update},
            return_document=True,
        )
        return self._to_project(doc) if doc else None

    async def delete(self, project_id: str, *, user_id: str) -> bool:
        result = await self.db.projects.delete_one({"_id": project_id, "user_id": user_id})
        return result.deleted_count == 1
