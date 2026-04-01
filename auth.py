"""
auth.py – Authentication helpers for Smart EVM.

• Password hashing via bcrypt (Passlib)
• JWT creation / verification via python-jose
• HTTPOnly cookie extraction dependency for FastAPI
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from models import WebUser, get_db

# ---------------------------------------------------------------------------
# Configuration – override SECRET_KEY via environment variable in production
# ---------------------------------------------------------------------------
SECRET_KEY: str = os.environ.get(
    "EVM_SECRET_KEY",
    "CHANGE_ME_super_secret_evm_key_32bytes!",  # dev fallback only
)
ALGORITHM       = "HS256"
TOKEN_EXPIRE_MIN = 60  # 60-minute token lifetime

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
_pwd_ctx = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_ctx.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    payload = data.copy()
    expire  = datetime.now(timezone.utc) + (
        expires_delta if expires_delta else timedelta(minutes=TOKEN_EXPIRE_MIN)
    )
    payload.update({"exp": expire})
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """Return decoded payload or None if invalid/expired."""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# FastAPI dependency – protects dashboard routes
# ---------------------------------------------------------------------------
def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> WebUser:
    """
    Reads the JWT from the HTTPOnly 'access_token' cookie.
    Raises a redirect to /login if missing or invalid.
    """
    token: Optional[str] = request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    username: str = payload.get("sub", "")
    user = db.query(WebUser).filter(WebUser.username == username).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    return user
