"""Pydantic request/response schemas (the API contract)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# --- Auth ---
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


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
    depth: int
    pending: int


class CacheEntryInfo(BaseModel):
    key: str
    ttl_seconds: int


class MessageResponse(BaseModel):
    message: str
