"""
auth.py — One admin login for the whole API
--------------------------------------------
Set ADMIN_PASSWORD in Backend/.env (and ADMIN_USERNAME if you want something other than "admin").
With no ADMIN_PASSWORD the API stays open, as before — fine on your own computer, not on the internet.

  POST /auth/login   {username, password} -> {token, expires_in}
  GET  /auth/status  is a login needed?   -> {auth_required}
  GET  /auth/me      is my token valid?   -> {ok: true} or 401

The token is "<payload>.<signature>" (HMAC-SHA256), sent as `Authorization: Bearer <token>`.
Set AUTH_SECRET so tokens survive a restart; without it a random one is made on every start.

Left open on purpose, because other services call them and cannot send a token:
  /webhooks/*  (Instantly, inbound SMS and calls)   /voice/*  (call script)   /track/*  (open pixel)
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel
from starlette.responses import JSONResponse

from config import settings

router = APIRouter(prefix="/auth", tags=["Auth"])

PUBLIC_PATHS = {"/", "/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
PUBLIC_PREFIXES = ("/auth/", "/webhooks/", "/voice/", "/track/")

_SECRET = (settings.AUTH_SECRET or secrets.token_urlsafe(32)).encode()

# failed logins per client address: 5 in 10 minutes locks that address out for the rest of the window
_FAIL_WINDOW_SECONDS = 600
_MAX_FAILS = 5
_fails: dict[str, deque] = defaultdict(deque)


def auth_enabled() -> bool:
    return bool(settings.ADMIN_PASSWORD)


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


# ─── Token ────────────────────────────────────────────────────────────────────

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload_b64: str) -> str:
    return _b64(hmac.new(_SECRET, payload_b64.encode(), hashlib.sha256).digest())


def make_token(username: str) -> tuple[str, int]:
    ttl = int(settings.AUTH_TOKEN_HOURS * 3600)
    payload = _b64(json.dumps({"sub": username, "exp": int(time.time()) + ttl}).encode())
    return f"{payload}.{_sign(payload)}", ttl


def verify_token(token: str) -> dict | None:
    try:
        payload_b64, signature = token.split(".", 1)
        if not hmac.compare_digest(signature, _sign(payload_b64)):
            return None
        payload = json.loads(_unb64(payload_b64))
        return payload if payload.get("exp", 0) > time.time() else None
    except Exception:
        return None


def _bearer(headers) -> str:
    value = headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


# ─── Middleware ───────────────────────────────────────────────────────────────

class AuthMiddleware:
    """Rejects API calls without a valid token. Pure ASGI so streaming responses are untouched."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or not auth_enabled()
            or scope["method"] == "OPTIONS"          # CORS preflight carries no token
            or is_public(scope["path"])
        ):
            return await self.app(scope, receive, send)

        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        if verify_token(_bearer(headers)):
            return await self.app(scope, receive, send)

        response = JSONResponse({"detail": "Not authenticated"}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
        await response(scope, receive, send)


def log_auth_status() -> None:
    if auth_enabled():
        logger.info("[auth] login required (ADMIN_PASSWORD is set)")
    else:
        logger.warning("[auth] ADMIN_PASSWORD is not set: the API and admin panel are open to anyone who can reach this server")
    if auth_enabled() and not settings.AUTH_SECRET:
        logger.warning("[auth] AUTH_SECRET is not set: everyone is logged out whenever the backend restarts")


# ─── Routes ───────────────────────────────────────────────────────────────────

class LoginBody(BaseModel):
    username: str = ""
    password: str = ""


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _recent_fails(client: str) -> deque:
    q = _fails[client]
    cutoff = time.time() - _FAIL_WINDOW_SECONDS
    while q and q[0] < cutoff:
        q.popleft()
    return q


@router.get("/status")
def auth_status():
    return {"auth_required": auth_enabled()}


@router.post("/login")
def login(body: LoginBody, request: Request):
    if not auth_enabled():
        raise HTTPException(400, "Login is not set up: add ADMIN_PASSWORD to Backend/.env")

    client = _client(request)
    fails = _recent_fails(client)
    if len(fails) >= _MAX_FAILS:
        raise HTTPException(429, "Too many wrong attempts. Try again in a few minutes.")

    # compare both fields every time so the response time does not reveal which one was wrong
    user_ok = hmac.compare_digest(body.username.encode(), settings.ADMIN_USERNAME.encode())
    pass_ok = hmac.compare_digest(body.password.encode(), settings.ADMIN_PASSWORD.encode())
    if not (user_ok and pass_ok):
        fails.append(time.time())
        raise HTTPException(401, "Wrong username or password")

    fails.clear()
    token, ttl = make_token(settings.ADMIN_USERNAME)
    return {"token": token, "expires_in": ttl}


@router.get("/me")
def me(request: Request):
    if auth_enabled() and not verify_token(_bearer(request.headers)):
        raise HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    return {"ok": True}
