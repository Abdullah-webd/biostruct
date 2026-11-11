from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class Role(StrEnum):
    researcher = "researcher"
    admin = "admin"


class RegisterRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    # Strength is validated in security.validate_password_strength so the
    # failure can carry a specific, actionable reason rather than a regex.
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class User(BaseModel):
    """Internal representation. Carries the hash and must never be serialised to a client."""

    id: str
    email: str
    name: str
    password_hash: str
    role: Role = Role.researcher
    created_at: datetime
    last_login_at: datetime | None = None

    def public(self) -> UserPublic:
        return UserPublic(
            id=self.id,
            email=self.email,
            name=self.name,
            role=self.role,
            created_at=self.created_at,
        )


class UserPublic(BaseModel):
    """What the API returns. No password hash, by construction rather than by remembering."""

    id: str
    email: str
    name: str
    role: Role
    created_at: datetime
