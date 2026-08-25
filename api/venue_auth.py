"""
venue_auth.py — Venue account authentication utilities.

JWT-based auth for venue dashboard accounts.
Separate from fan accounts. Stateless. HttpOnly cookie delivery.

JWT payload:
  sub        → venue_account.id (UUID str)
  venue_id   → venue.id (UUID str)
  role       → 'owner' | 'manager'
  iat, exp   → issued/expiry (7-day sliding window)
"""

import os
import uuid
import datetime
from functools import wraps
from typing import Optional

import bcrypt as _bcrypt
import jwt
from flask import request, jsonify, g

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

JWT_SECRET = os.environ.get("VENUE_JWT_SECRET", "dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 7
JWT_REFRESH_THRESHOLD_HOURS = 24   # reissue token if it's been >24h since iat

COOKIE_NAME = "venue_token"
COOKIE_SECURE = True   # always Secure — required for SameSite=None
COOKIE_SAMESITE = "None"  # cross-origin cookie: dashboard.eastheightslabs.com → ehl-backend-vercel.vercel.app


# ---------------------------------------------------------------------------
# Password helpers (bcrypt direct — avoids passlib/bcrypt 4.x compat issue)
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode("utf-8"), _bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def issue_token(venue_account_id: str, venue_id: str, role: str = "owner") -> str:
    now = _now()
    payload = {
        "sub": venue_account_id,
        "venue_id": venue_id,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + datetime.timedelta(days=JWT_EXPIRY_DAYS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate JWT. Raises jwt.PyJWTError on failure."""
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


def should_refresh(payload: dict) -> bool:
    """Return True if token is older than refresh threshold."""
    iat = payload.get("iat", 0)
    age_hours = (_now().timestamp() - iat) / 3600
    return age_hours >= JWT_REFRESH_THRESHOLD_HOURS


def get_token_from_request() -> Optional[str]:
    """Extract JWT from HttpOnly cookie or Authorization header."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    return token or None


def set_auth_cookie(response, token: str):
    """Attach JWT as HttpOnly cookie on a Flask response."""
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        max_age=60 * 60 * 24 * JWT_EXPIRY_DAYS,
        path="/",
    )
    return response


def clear_auth_cookie(response):
    """Clear the auth cookie (logout)."""
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


# ---------------------------------------------------------------------------
# Auth decorator
# ---------------------------------------------------------------------------

def require_venue_auth(f):
    """
    Decorator: requires a valid venue JWT.
    On success, sets g.venue_payload = decoded token dict.
    On failure, returns 401.
    Automatically refreshes token if > 24h old.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = get_token_from_request()
        if not token:
            return jsonify({"error": "Authentication required"}), 401

        try:
            payload = decode_token(token)
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Session expired — please log in again"}), 401
        except jwt.PyJWTError:
            return jsonify({"error": "Invalid token"}), 401

        g.venue_payload = payload

        # Run the view
        result = f(*args, **kwargs)

        # Sliding refresh: reissue token if it's getting old
        if should_refresh(payload):
            new_token = issue_token(
                payload["sub"],
                payload["venue_id"],
                payload.get("role", "owner"),
            )
            # result may be a Response or a tuple — handle both
            from flask import make_response
            if isinstance(result, tuple):
                resp = make_response(*result)
            else:
                resp = make_response(result)
            return set_auth_cookie(resp, new_token)

        return result

    return decorated


def verify_venue_ownership(resource_venue_id: str):
    """
    Check that the authenticated user owns the venue of the resource.
    Call inside a @require_venue_auth route.
    Returns (True, None) or (False, error_response).
    """
    token_venue_id = g.venue_payload.get("venue_id", "")
    if str(resource_venue_id) != token_venue_id:
        return False, (jsonify({"error": "Forbidden — not your venue"}), 403)
    return True, None


# ---------------------------------------------------------------------------
# Invite token helpers
# ---------------------------------------------------------------------------

def generate_invite_token() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex  # 64-char hex


def invite_expiry() -> datetime.datetime:
    return _now() + datetime.timedelta(hours=72)
# force redeploy Tue Aug 25 15:35:34 CDT 2026
