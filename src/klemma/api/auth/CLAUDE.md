# Auth Package

JWT + argon2 authentication layer for the Klemma SaaS backend (ADR-009). JWT via `PyJWT[crypto]` (replaced `python-jose` due to CVE-2024-33663/33664).

## Modules

### config.py (40 lines)
`AuthConfig` — settings from environment variables.
- `KLEMMA_JWT_SECRET` — required in production; ephemeral random in development/test
- `KLEMMA_ENV=production` — triggers fail-fast if secret is not set
- `KLEMMA_ACCESS_TOKEN_EXPIRE_MINUTES` (default: 15)
- `KLEMMA_REFRESH_TOKEN_EXPIRE_DAYS` (default: 30)
- Module-level singleton `auth_config` used by `tokens.py`

### password.py (20 lines)
`hash_password(plain) -> str` / `verify_password(plain, hashed) -> bool` — argon2id via `argon2-cffi`.

### tokens.py (67 lines)
JWT creation and verification.
- `create_access_token(user_id, email) -> str` — 15-min access token, type=`"access"`
- `create_refresh_token(user_id) -> str` — 30-day token, includes `jti` for rotation
- `decode_token(token) -> dict | None` — validates and decodes; returns None on any error
- `hash_token(token) -> str` — SHA256 hex, for storing refresh tokens at rest
- `refresh_token_expires_at() -> str` — ISO 8601 expiry string

### schemas.py (45 lines)
Pydantic request/response models: `UserCreate`, `UserLogin`, `TokenResponse`, `RefreshRequest`, `UserResponse`.
- `TokenResponse` includes `user_id` — returned on register, login, and refresh
- `UserCreate.password`: min 8, max 128 characters
- `UserCreate.name`: max 255 characters
- `UserLogin.password`: min 1, max 128 characters

### deps.py (60 lines)
FastAPI dependency injection.
- `set_user_store(store)` / `get_user_store()` — module-level `UserStore` singleton; set in `app.py` lifespan
- `get_current_user(credentials)` — `Depends()` helper; validates bearer token, returns `UserRecord`

## Data flow: login

```
POST /auth/login → UserLogin schema
  → UserStore.get_user_by_email()
  → verify_password(plain, hash)
  → create_access_token() + create_refresh_token()
  → UserStore.save_refresh_token(hash_token(refresh))
  → TokenResponse
```

## Maintaining this file
Update when adding new auth modules or changing the token/password strategy.

See: [API routes](../routes/CLAUDE.md) | [API package](../CLAUDE.md) | [Protocols](../../protocols.py)
