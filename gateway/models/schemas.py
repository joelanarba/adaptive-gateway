"""Pydantic request/response schemas (the API contract)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# --- Auth ---
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=256)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access token lifetime in seconds


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    is_active: bool
    is_admin: bool
    created_at: datetime


# --- API keys (machine-to-machine) ---
class APIKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class APIKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    prefix: str
    is_active: bool
    created_at: datetime


class APIKeyCreated(APIKeyOut):
    # The full secret is returned exactly once, at creation time.
    api_key: str


# --- Admin / introspection ---
class QueueStatus(BaseModel):
    stream_key: str
    depth: int | None
    pending: int | None


class CacheEntryInfo(BaseModel):
    key: str
    ttl_seconds: int


class MessageResponse(BaseModel):
    message: str
