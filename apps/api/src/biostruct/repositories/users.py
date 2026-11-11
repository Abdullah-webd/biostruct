from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from biostruct import security
from biostruct.models.user import Role, User


class UserRepository:
    def __init__(self, db: AsyncIOMotorDatabase[dict[str, Any]]) -> None:
        self.db = db

    # --- users -------------------------------------------------------------

    @staticmethod
    def _to_user(doc: dict[str, Any]) -> User:
        return User(
            id=doc["_id"],
            email=doc["email"],
            name=doc["name"],
            password_hash=doc["password_hash"],
            role=Role(doc.get("role", Role.researcher)),
            created_at=doc["created_at"],
            last_login_at=doc.get("last_login_at"),
        )

    async def get_by_email(self, email: str) -> User | None:
        doc = await self.db.users.find_one({"email": email.lower()})
        return self._to_user(doc) if doc else None

    async def get_by_id(self, user_id: str) -> User | None:
        doc = await self.db.users.find_one({"_id": user_id})
        return self._to_user(doc) if doc else None

    async def create(self, *, email: str, name: str, password: str) -> User | None:
        """Create a user. Returns None if the email is already taken.

        Uniqueness is enforced by the unique index, not by a prior read, so two
        simultaneous registrations cannot both succeed.
        """
        now = datetime.now(UTC)
        doc = {
            "_id": str(uuid.uuid4()),
            "email": email.lower(),
            "name": name,
            "password_hash": security.hash_password(password),
            "role": Role.researcher.value,
            "created_at": now,
            "last_login_at": None,
            "schema_version": 1,
        }
        try:
            await self.db.users.insert_one(doc)
        except DuplicateKeyError:
            return None
        return self._to_user(doc)

    async def record_login(self, user_id: str, *, new_password_hash: str | None = None) -> None:
        update: dict[str, Any] = {"last_login_at": datetime.now(UTC)}
        if new_password_hash is not None:
            update["password_hash"] = new_password_hash
        await self.db.users.update_one({"_id": user_id}, {"$set": update})

    # --- sessions ----------------------------------------------------------

    async def create_session(
        self,
        *,
        user_id: str,
        ttl_days: int,
        user_agent: str | None,
        ip: str | None,
    ) -> str:
        """Create a session and return the raw token for the cookie.

        Only the token's hash is stored, so a leaked database cannot be replayed
        as a login.
        """
        token = security.new_session_token()
        now = datetime.now(UTC)
        await self.db.sessions.insert_one(
            {
                "_id": security.hash_session_token(token),
                "user_id": user_id,
                "created_at": now,
                "expires_at": now + timedelta(days=ttl_days),
                "user_agent": (user_agent or "")[:400],
                "ip": ip,
            }
        )
        return token

    async def get_user_for_session(self, token: str) -> User | None:
        """Resolve a cookie token to a user, treating an expired session as absent.

        The TTL index removes expired sessions eventually, but "eventually" is up
        to a minute, so we also compare the expiry here.
        """
        doc = await self.db.sessions.find_one({"_id": security.hash_session_token(token)})
        if not doc:
            return None
        expires_at = doc["expires_at"]
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            return None
        return await self.get_by_id(doc["user_id"])

    async def delete_session(self, token: str) -> None:
        await self.db.sessions.delete_one({"_id": security.hash_session_token(token)})

    async def delete_all_sessions_for_user(self, user_id: str) -> int:
        result = await self.db.sessions.delete_many({"user_id": user_id})
        return int(result.deleted_count)
