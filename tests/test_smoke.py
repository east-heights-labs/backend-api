"""
test_smoke.py — Smoke tests for the OnStage backend API.

These tests run against the real Flask app (no mocking of HTTP layer).
They require DATABASE_URL and at least TICKETMASTER_API_KEY to be set.

Run with:
    pytest tests/ -v

All three tests must pass before any production deploy.
"""
import json


# ---------------------------------------------------------------------------
# Test 1: Health check
# ---------------------------------------------------------------------------

def test_health_returns_200_and_ok(client):
    """
    GET /api/health must return 200 with {"status": "ok"}.
    This is the minimum signal that the Flask app started and routes are wired.
    """
    resp = client.get("/api/health")

    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}. Body: {resp.data}"
    )

    data = resp.get_json()
    assert data is not None, "Response body is not valid JSON"
    assert data.get("status") == "ok", (
        f"Expected {{\"status\": \"ok\"}}, got {data}"
    )


# ---------------------------------------------------------------------------
# Test 2: Events near Houston
# ---------------------------------------------------------------------------

def test_events_houston_returns_list(client):
    """
    GET /api/events?lat=29.7604&lng=-95.3698&radius=25 must return a JSON
    object with an "events" key containing a list.

    Uses real Ticketmaster + JamBase calls (or mock fallback if no API keys).
    Does NOT assert specific event count — the API is live data.
    """
    resp = client.get("/api/events", query_string={
        "lat": "29.7604",
        "lng": "-95.3698",
        "radius": "25",
    })

    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}. Body: {resp.data}"
    )

    data = resp.get_json()
    assert data is not None, "Response body is not valid JSON"
    assert "events" in data, f"Response missing 'events' key. Got: {list(data.keys())}"
    assert isinstance(data["events"], list), (
        f"'events' must be a list, got {type(data['events'])}"
    )

    # If we have Ticketmaster key, expect at least some results for Houston
    import os
    if os.environ.get("TICKETMASTER_API_KEY"):
        assert len(data["events"]) > 0, (
            "Expected at least 1 event near Houston with Ticketmaster key set. "
            "Check API key or date filter."
        )


# ---------------------------------------------------------------------------
# Test 3: Artist search
# ---------------------------------------------------------------------------

def test_search_artists_returns_shows_array(client):
    """
    GET /api/search/artists?q=Taylor must return {"shows": [...], "total": N}.

    "shows" must be a list. We don't assert non-empty because the DB
    may not have Taylor Swift shows indexed for today's date, but the
    endpoint must return the correct schema without erroring.
    """
    resp = client.get("/api/search/artists", query_string={"q": "Taylor"})

    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}. Body: {resp.data}"
    )

    data = resp.get_json()
    assert data is not None, "Response body is not valid JSON"
    assert "shows" in data, (
        f"Response missing 'shows' key. Got keys: {list(data.keys())}"
    )
    assert isinstance(data["shows"], list), (
        f"'shows' must be a list, got {type(data['shows'])}"
    )

    # count should be a non-negative integer matching len(shows)
    assert "count" in data, f"Response missing 'count' key. Got: {list(data.keys())}"
    assert isinstance(data["count"], int) and data["count"] >= 0, (
        f"'count' must be a non-negative int, got {data.get('count')!r}"
    )
    assert data["count"] == len(data["shows"]), (
        f"'count' ({data['count']}) must equal len(shows) ({len(data['shows'])})"
    )


# ---------------------------------------------------------------------------
# Test 4 (bonus): Rate limiting fires on login after 10 attempts
# ---------------------------------------------------------------------------

def test_login_rate_limit_fires_at_11(client):
    """
    POST /api/venue/login is limited to 10 per minute per IP.
    The 11th attempt from the same IP must return 429 Too Many Requests.
    """
    # Use a unique IP so previous test runs don't bleed (in-memory storage resets between sessions)
    test_ip = "192.0.2.99"  # TEST-NET-1, never a real client

    statuses = []
    for _ in range(10):
        r = client.post(
            "/api/venue/login",
            json={"email": "ratelimit_test@test.invalid", "password": "wrong"},
            environ_base={"REMOTE_ADDR": test_ip},
        )
        statuses.append(r.status_code)

    # All 10 should be auth failures (401), not rate limited
    assert all(s == 401 for s in statuses), (
        f"Expected 10x 401 before rate limit. Got: {statuses}"
    )

    # 11th must be rate limited
    r11 = client.post(
        "/api/venue/login",
        json={"email": "ratelimit_test@test.invalid", "password": "wrong"},
        environ_base={"REMOTE_ADDR": test_ip},
    )
    assert r11.status_code == 429, (
        f"Expected 429 on attempt 11, got {r11.status_code}. "
        "Rate limiting may not be applied to this endpoint."
    )
