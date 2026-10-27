"""
auth.py — JWT Authentication for ecomdropship.ai Admin Panel
-------------------------------------------------------------
Simple but secure single-admin login system.
Credentials stored in .env — no database needed.

Flow:
  1. POST /auth/login  → validate credentials → return JWT token
  2. Frontend stores token in localStorage
  3. Every protected route checks token via get_current_admin()
  4. Token expires in 8 hours
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from config import settings
from rate_limit import limiter

# ── Router ────────────────────────────────────────────────────────────────────
router = APIRouter(prefix="/auth", tags=["Auth"])

# ── Security setup ────────────────────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer()

ALGORITHM    = "HS256"
TOKEN_EXPIRY = 8   # hours


# ── Pydantic models ───────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = TOKEN_EXPIRY * 3600


# ── Helpers ───────────────────────────────────────────────────────────────────
def _create_token(data: dict, expires_hours: int = TOKEN_EXPIRY) -> str:
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(hours=expires_hours)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def _verify_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


def _verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


# ── Dependency — protects all admin routes ────────────────────────────────────
def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """
    Dependency injected into every protected endpoint.
    Raises 401 if token is missing, expired, or invalid.
    """
    token = credentials.credentials
    payload = _verify_token(token)

    if not payload or payload.get("sub") != settings.ADMIN_USERNAME:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


# ── Routes ────────────────────────────────────────────────────────────────────
@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
def login(request: Request, req: LoginRequest):
    """
    Validates username + password against .env credentials.
    Returns JWT token valid for 8 hours.

    Security measures:
    - bcrypt password hashing
    - JWT with expiry
    - Generic error message (doesn't reveal if username or password is wrong)
    - Rate limited to 5 attempts/minute per IP (in-app, via slowapi) —
      blocks brute-force even with no reverse proxy in front of the API.
      If you ALSO put nginx/Cloudflare in front later, you can layer
      another limit there too, but this one works standalone.
    """
    # Check username
    if req.username != settings.ADMIN_USERNAME:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    # Check password — compare against hashed password in .env
    # If ADMIN_PASSWORD_HASH is set, use it. Otherwise fall back to plain (dev only)
    if settings.ADMIN_PASSWORD_HASH:
        if not _verify_password(req.password, settings.ADMIN_PASSWORD_HASH):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )
    else:
        # Plain password fallback — only for initial setup
        if req.password != settings.ADMIN_PASSWORD:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )

    token = _create_token({"sub": req.username})
    return TokenResponse(access_token=token)


@router.post("/logout")
def logout():
    """
    JWT is stateless — actual logout happens on frontend by deleting token.
    This endpoint exists for frontend to call cleanly.
    """
    return {"message": "Logged out successfully"}


@router.get("/verify")
def verify_token(admin: dict = Depends(get_current_admin)):
    """
    Frontend calls this on page load to check if stored token is still valid.
    Returns 200 if valid, 401 if expired/invalid.
    """
    return {"valid": True, "username": admin.get("sub")}