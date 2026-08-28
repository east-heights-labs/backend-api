"""
limiter.py — Shared Flask-Limiter instance.

Initialized here so venue_routes.py can import it without circular imports.
The actual app binding happens in index.py via limiter.init_app(app).
"""
import os
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

_redis_url = os.environ.get("REDIS_URL", "")

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=_redis_url if _redis_url else None,
    default_limits=[],    # no global limit — applied per-route only
    swallow_errors=True,  # don't 500 if Redis is briefly unavailable
)
