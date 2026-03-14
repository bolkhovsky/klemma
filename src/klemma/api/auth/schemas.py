"""Pydantic schemas for auth requests and responses."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr


class UserCreate(BaseModel):
    """Registration request."""

    email: EmailStr
    password: str
    name: str = ""


class UserLogin(BaseModel):
    """Login request."""

    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """Token pair returned on login/register/refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    """Refresh token request."""

    refresh_token: str


class UserResponse(BaseModel):
    """Public user info returned by /auth/me."""

    user_id: str
    email: str
    name: str
    email_verified: bool
