"""
conftest.py — pytest fixtures for OnStage backend tests.

Sets up a Flask test client pointed at api/index.py.
DATABASE_URL must be set (or mocked) for DB-dependent endpoints.
TICKETMASTER_API_KEY, SETLIST_FM_API_KEY are read from environment.
"""
import sys
import os
import pytest

# Make the api/ directory importable (mirrors Vercel's sys.path insert)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

# Ensure we have the Railway DATABASE_URL for tests that hit the DB
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:xcZZUdTJmUGTgjGpNvkNZdxMMDsqfYcd@yamanote.proxy.rlwy.net:39610/railway",
)
os.environ.setdefault("DATABASE_URL", DATABASE_URL)

# Suppress limiter in-memory warning during tests
os.environ.setdefault("REDIS_URL", "")

# Venue auth secrets — test values, not production
os.environ.setdefault("VENUE_JWT_SECRET", "test-jwt-secret-not-for-production-use")
os.environ.setdefault("VENUE_ADMIN_SECRET", "test-admin-secret-not-for-production-use")


@pytest.fixture(scope="session")
def app():
    """Create the Flask app once per test session."""
    from index import app as flask_app
    flask_app.config["TESTING"] = True
    yield flask_app


@pytest.fixture(scope="session")
def client(app):
    """Flask test client."""
    with app.test_client() as c:
        yield c
