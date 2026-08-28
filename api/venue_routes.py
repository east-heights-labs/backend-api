"""
venue_routes.py — Venue dashboard API routes.

Blueprint: venue_bp
Prefix:    /api/venue
Admin:     /api/admin/venue

Auth: JWT via HttpOnly cookie or Authorization: Bearer header.
All write operations scope-checked to venue_id in token.

Routes (public):
  POST /api/venue/login
  GET  /api/venue/accept-invite          validate token, return venue name
  POST /api/venue/accept-invite          set password, activate, return JWT

Routes (authenticated):
  POST /api/venue/logout
  GET  /api/venue/me
  GET  /api/venue/events
  POST /api/venue/events
  PUT  /api/venue/events/<event_id>
  DELETE /api/venue/events/<event_id>

Routes (admin — ADMIN_SECRET header):
  POST /api/admin/venue/invite           generate invite for a venue
  GET  /api/admin/venue/accounts         list all venue accounts
"""

import os
import datetime

from flask import Blueprint, request, jsonify, g, make_response

from venue_auth import (
    hash_password,
    verify_password,
    issue_token,
    set_auth_cookie,
    clear_auth_cookie,
    require_venue_auth,
    verify_venue_ownership,
    generate_invite_token,
    invite_expiry,
)
from db import get_db
from limiter import limiter

venue_bp = Blueprint("venue", __name__)

ADMIN_SECRET = os.environ.get("VENUE_ADMIN_SECRET", "dev-admin-secret")


# ---------------------------------------------------------------------------
# Admin guard
# ---------------------------------------------------------------------------

def require_admin(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        secret = request.headers.get("X-Admin-Secret", "")
        if secret != ADMIN_SECRET:
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Admin: generate invite
# ---------------------------------------------------------------------------

@venue_bp.route("/api/admin/venue/invite", methods=["POST"])
@require_admin
def admin_create_invite():
    """
    Generate a single-use invite link for a venue.
    Body: { venue_id: UUID, email: str }
    Returns: { invite_url: str, expires_at: str }
    """
    data = request.get_json() or {}
    venue_id = (data.get("venue_id") or "").strip()
    email = (data.get("email") or "").strip().lower()

    if not venue_id or not email:
        return jsonify({"error": "venue_id and email required"}), 400

    db = get_db()
    with db.cursor() as cur:
        # Verify venue exists
        cur.execute("SELECT id, name FROM venues WHERE id = %s", (venue_id,))
        venue = cur.fetchone()
        if not venue:
            return jsonify({"error": "Venue not found"}), 404

        # Check for existing active account
        cur.execute(
            "SELECT id, is_active FROM venue_accounts WHERE venue_id = %s AND is_active = TRUE",
            (venue_id,)
        )
        if cur.fetchone():
            return jsonify({"error": "Venue already has an active account"}), 409

        token = generate_invite_token()
        expires = invite_expiry()

        # Upsert — replace any pending invite for this venue+email
        cur.execute("""
            INSERT INTO venue_accounts (venue_id, email, invite_token, invite_expires_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (email) DO UPDATE
              SET invite_token = EXCLUDED.invite_token,
                  invite_expires_at = EXCLUDED.invite_expires_at,
                  updated_at = NOW()
            RETURNING id
        """, (venue_id, email, token, expires))
        db.commit()

    base_url = os.environ.get("DASHBOARD_BASE_URL", "https://dashboard.eastheightslabs.com")
    invite_url = f"{base_url}/accept-invite?token={token}"

    return jsonify({
        "ok": True,
        "venue_name": venue[1],
        "email": email,
        "invite_url": invite_url,
        "expires_at": expires.isoformat(),
    })


# ---------------------------------------------------------------------------
# Admin: list accounts
# ---------------------------------------------------------------------------

@venue_bp.route("/api/admin/venue/accounts", methods=["GET"])
@require_admin
def admin_list_accounts():
    db = get_db()
    with db.cursor() as cur:
        cur.execute("""
            SELECT va.id, va.email, va.is_active, va.role,
                   va.created_at, va.last_login,
                   v.name AS venue_name, v.id AS venue_id
            FROM venue_accounts va
            JOIN venues v ON v.id = va.venue_id
            ORDER BY va.created_at DESC
        """)
        rows = cur.fetchall()

    accounts = []
    for r in rows:
        accounts.append({
            "id": str(r[0]),
            "email": r[1],
            "is_active": r[2],
            "role": r[3],
            "created_at": r[4].isoformat() if r[4] else None,
            "last_login": r[5].isoformat() if r[5] else None,
            "venue_name": r[6],
            "venue_id": str(r[7]),
        })

    return jsonify({"accounts": accounts, "count": len(accounts)})


# ---------------------------------------------------------------------------
# Public: validate invite token (GET — for dashboard to pre-fill venue name)
# ---------------------------------------------------------------------------

@venue_bp.route("/api/venue/accept-invite", methods=["GET"])
def get_invite_info():
    token = request.args.get("token", "").strip()
    if not token:
        return jsonify({"error": "token required"}), 400

    db = get_db()
    with db.cursor() as cur:
        cur.execute("""
            SELECT va.id, va.email, va.invite_expires_at, v.name
            FROM venue_accounts va
            JOIN venues v ON v.id = va.venue_id
            WHERE va.invite_token = %s AND va.is_active = FALSE
        """, (token,))
        row = cur.fetchone()

    if not row:
        return jsonify({"error": "Invalid or already-used invite token"}), 404

    expires_at = row[2]
    if expires_at and expires_at < datetime.datetime.now(datetime.timezone.utc):
        return jsonify({"error": "Invite token has expired"}), 410

    return jsonify({
        "email": row[1],
        "venue_name": row[3],
        "expires_at": expires_at.isoformat() if expires_at else None,
    })


# ---------------------------------------------------------------------------
# Public: accept invite + set password
# ---------------------------------------------------------------------------

@venue_bp.route("/api/venue/accept-invite", methods=["POST"])
@limiter.limit("10 per minute")
def accept_invite():
    """
    Body: { token: str, password: str }
    Activates account, returns JWT in cookie.
    """
    data = request.get_json() or {}
    token = (data.get("token") or "").strip()
    password = (data.get("password") or "").strip()

    if not token or not password:
        return jsonify({"error": "token and password required"}), 400

    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    db = get_db()
    with db.cursor() as cur:
        cur.execute("""
            SELECT va.id, va.venue_id, va.role, va.invite_expires_at
            FROM venue_accounts va
            WHERE va.invite_token = %s AND va.is_active = FALSE
        """, (token,))
        row = cur.fetchone()

        if not row:
            return jsonify({"error": "Invalid or already-used invite token"}), 404

        account_id, venue_id, role, expires_at = row

        if expires_at and expires_at < datetime.datetime.now(datetime.timezone.utc):
            return jsonify({"error": "Invite token has expired"}), 410

        pw_hash = hash_password(password)

        cur.execute("""
            UPDATE venue_accounts
            SET password_hash = %s,
                is_active = TRUE,
                invite_token = NULL,
                invite_expires_at = NULL,
                updated_at = NOW()
            WHERE id = %s
        """, (pw_hash, account_id))
        db.commit()

    jwt_token = issue_token(str(account_id), str(venue_id), role)
    resp = make_response(jsonify({"ok": True, "message": "Account activated", "token": jwt_token}))
    return set_auth_cookie(resp, jwt_token)  # still set cookie for backward compat


# ---------------------------------------------------------------------------
# Public: login
# ---------------------------------------------------------------------------

@venue_bp.route("/api/venue/login", methods=["POST"])
@limiter.limit("10 per minute")
def venue_login():
    """
    Body: { email: str, password: str }
    Returns JWT in HttpOnly cookie.
    """
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()

    if not email or not password:
        return jsonify({"error": "email and password required"}), 400

    db = get_db()
    with db.cursor() as cur:
        cur.execute("""
            SELECT id, venue_id, role, password_hash, is_active
            FROM venue_accounts
            WHERE email = %s
        """, (email,))
        row = cur.fetchone()

    # Constant-time: always verify even if no row (prevents timing attacks)
    # Pre-computed dummy hash for "admin" — never matches real passwords
    dummy_hash = "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewdBPj4J/HS0y.Mu"
    stored_hash = row[3] if row else dummy_hash

    if not row or not row[4]:
        verify_password(password, dummy_hash)  # burn time
        return jsonify({"error": "Invalid email or password"}), 401

    if not verify_password(password, stored_hash):
        return jsonify({"error": "Invalid email or password"}), 401

    account_id, venue_id, role = row[0], row[1], row[2]

    # Update last_login
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE venue_accounts SET last_login = NOW() WHERE id = %s",
            (account_id,)
        )
        db.commit()

    jwt_token = issue_token(str(account_id), str(venue_id), role)
    resp = make_response(jsonify({"ok": True, "token": jwt_token}))
    return set_auth_cookie(resp, jwt_token)  # still set cookie for backward compat


# ---------------------------------------------------------------------------
# Authenticated: logout
# ---------------------------------------------------------------------------

@venue_bp.route("/api/venue/logout", methods=["POST"])
@require_venue_auth
def venue_logout():
    resp = make_response(jsonify({"ok": True}))
    return clear_auth_cookie(resp)


# ---------------------------------------------------------------------------
# Authenticated: me
# ---------------------------------------------------------------------------

@venue_bp.route("/api/venue/me", methods=["GET"])
@require_venue_auth
def venue_me():
    venue_id = g.venue_payload["venue_id"]
    db = get_db()
    with db.cursor() as cur:
        cur.execute("""
            SELECT va.id, va.email, va.role, va.last_login,
                   v.id, v.name, v.city, v.lat, v.lng
            FROM venue_accounts va
            JOIN venues v ON v.id = va.venue_id
            WHERE va.id = %s
        """, (g.venue_payload["sub"],))
        row = cur.fetchone()

    if not row:
        return jsonify({"error": "Account not found"}), 404

    return jsonify({
        "account": {
            "id": str(row[0]),
            "email": row[1],
            "role": row[2],
            "last_login": row[3].isoformat() if row[3] else None,
        },
        "venue": {
            "id": str(row[4]),
            "name": row[5],
            "city": row[6],
            "lat": float(row[7]) if row[7] else None,
            "lng": float(row[8]) if row[8] else None,
        },
    })


# ---------------------------------------------------------------------------
# Authenticated: list venue's events
# ---------------------------------------------------------------------------

@venue_bp.route("/api/venue/events", methods=["GET"])
@require_venue_auth
def list_venue_events():
    venue_id = g.venue_payload["venue_id"]
    db = get_db()
    with db.cursor() as cur:
        cur.execute("""
            SELECT id, title, event_date, doors_time, stage_time,
                   ticket_url, price_min, price_max, description,
                   image_url, is_cancelled, created_at, updated_at
            FROM venue_events
            WHERE venue_id = %s
            ORDER BY event_date DESC, doors_time ASC
        """, (venue_id,))
        rows = cur.fetchall()

    events = []
    for r in rows:
        events.append({
            "id": str(r[0]),
            "title": r[1],
            "event_date": r[2].isoformat() if r[2] else None,
            "doors_time": str(r[3]) if r[3] else None,
            "stage_time": str(r[4]) if r[4] else None,
            "ticket_url": r[5],
            "price_min": float(r[6]) if r[6] else None,
            "price_max": float(r[7]) if r[7] else None,
            "description": r[8],
            "image_url": r[9],
            "is_cancelled": r[10],
            "created_at": r[11].isoformat() if r[11] else None,
            "updated_at": r[12].isoformat() if r[12] else None,
        })

    return jsonify({"events": events, "count": len(events)})


# ---------------------------------------------------------------------------
# Authenticated: create event
# ---------------------------------------------------------------------------

@venue_bp.route("/api/venue/events", methods=["POST"])
@require_venue_auth
def create_venue_event():
    venue_id = g.venue_payload["venue_id"]
    account_id = g.venue_payload["sub"]
    data = request.get_json() or {}

    title = (data.get("title") or "").strip()
    event_date = (data.get("event_date") or "").strip()

    if not title or not event_date:
        return jsonify({"error": "title and event_date required"}), 400

    # Validate date
    try:
        datetime.date.fromisoformat(event_date)
    except ValueError:
        return jsonify({"error": "event_date must be YYYY-MM-DD"}), 400

    # Optional fields
    doors_time = data.get("doors_time")     # "HH:MM" or None
    stage_time = data.get("stage_time")     # "HH:MM" or None
    ticket_url = data.get("ticket_url")
    price_min = data.get("price_min")
    price_max = data.get("price_max")
    description = data.get("description")
    image_url = data.get("image_url")

    db = get_db()
    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO venue_events
              (venue_id, created_by, title, event_date, doors_time, stage_time,
               ticket_url, price_min, price_max, description, image_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, created_at
        """, (venue_id, account_id, title, event_date,
              doors_time, stage_time, ticket_url,
              price_min, price_max, description, image_url))
        row = cur.fetchone()
        db.commit()

    return jsonify({
        "ok": True,
        "event_id": str(row[0]),
        "created_at": row[1].isoformat(),
    }), 201


# ---------------------------------------------------------------------------
# Authenticated: update event
# ---------------------------------------------------------------------------

@venue_bp.route("/api/venue/events/<event_id>", methods=["PUT"])
@require_venue_auth
def update_venue_event(event_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT venue_id FROM venue_events WHERE id = %s", (event_id,))
        row = cur.fetchone()

    if not row:
        return jsonify({"error": "Event not found"}), 404

    ok, err = verify_venue_ownership(str(row[0]))
    if not ok:
        return err

    data = request.get_json() or {}
    allowed = ["title", "event_date", "doors_time", "stage_time",
               "ticket_url", "price_min", "price_max", "description",
               "image_url", "is_cancelled"]
    updates = {k: v for k, v in data.items() if k in allowed}

    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400

    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [event_id]

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            f"UPDATE venue_events SET {set_clause}, updated_at = NOW() WHERE id = %s",
            values
        )
        db.commit()

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Authenticated: delete (soft — sets is_hidden)
# ---------------------------------------------------------------------------

@venue_bp.route("/api/venue/events/<event_id>", methods=["DELETE"])
@require_venue_auth
def delete_venue_event(event_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT venue_id FROM venue_events WHERE id = %s", (event_id,))
        row = cur.fetchone()

    if not row:
        return jsonify({"error": "Event not found"}), 404

    ok, err = verify_venue_ownership(str(row[0]))
    if not ok:
        return err

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE venue_events SET is_hidden = TRUE, updated_at = NOW() WHERE id = %s",
            (event_id,)
        )
        db.commit()

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Community reports — fan-submitted stage times, scoped to venue
# ---------------------------------------------------------------------------

@venue_bp.route("/api/venue/community-reports", methods=["GET"])
@require_venue_auth
def list_community_reports():
    venue_id = g.venue_payload["venue_id"]
    status_filter = request.args.get("status", "pending")

    db = get_db()
    with db.cursor() as cur:
        if status_filter == "all":
            cur.execute("""
                SELECT id, artist_name, event_date, stage_time, status, submitted_at, event_id
                FROM venue_stage_reports
                WHERE venue_id = %s
                ORDER BY submitted_at DESC LIMIT 100
            """, (venue_id,))
        else:
            cur.execute("""
                SELECT id, artist_name, event_date, stage_time, status, submitted_at, event_id
                FROM venue_stage_reports
                WHERE venue_id = %s AND status = %s
                ORDER BY submitted_at DESC LIMIT 100
            """, (venue_id, status_filter))
        rows = cur.fetchall()

    reports = [{
        "id": str(r[0]),
        "artist_name": r[1],
        "event_date": r[2].isoformat() if r[2] else None,
        "stage_time": str(r[3]) if r[3] else None,
        "status": r[4],
        "submitted_at": r[5].isoformat() if r[5] else None,
        "event_id": r[6],
    } for r in rows]

    return jsonify({"reports": reports, "count": len(reports)})


@venue_bp.route("/api/venue/community-reports/<report_id>/confirm", methods=["POST"])
@require_venue_auth
def confirm_report(report_id):
    venue_id = g.venue_payload["venue_id"]
    account_id = g.venue_payload["sub"]

    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT venue_id FROM venue_stage_reports WHERE id = %s", (report_id,))
        row = cur.fetchone()

    if not row:
        return jsonify({"error": "Report not found"}), 404
    if str(row[0]) != venue_id:
        return jsonify({"error": "Forbidden"}), 403

    db = get_db()
    with db.cursor() as cur:
        cur.execute("""
            UPDATE venue_stage_reports
            SET status = 'confirmed', reviewed_by = %s, reviewed_at = NOW()
            WHERE id = %s
        """, (account_id, report_id))
        db.commit()

    return jsonify({"ok": True})


@venue_bp.route("/api/venue/community-reports/<report_id>/flag", methods=["POST"])
@require_venue_auth
def flag_report(report_id):
    venue_id = g.venue_payload["venue_id"]
    account_id = g.venue_payload["sub"]

    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT venue_id FROM venue_stage_reports WHERE id = %s", (report_id,))
        row = cur.fetchone()

    if not row:
        return jsonify({"error": "Report not found"}), 404
    if str(row[0]) != venue_id:
        return jsonify({"error": "Forbidden"}), 403

    db = get_db()
    with db.cursor() as cur:
        cur.execute("""
            UPDATE venue_stage_reports
            SET status = 'flagged', reviewed_by = %s, reviewed_at = NOW()
            WHERE id = %s
        """, (account_id, report_id))
        db.commit()

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Followers
# ---------------------------------------------------------------------------

@venue_bp.route("/api/venue/followers", methods=["GET"])
@require_venue_auth
def get_followers():
    venue_id = g.venue_payload["venue_id"]

    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM venue_followers WHERE venue_id = %s", (venue_id,))
        total = cur.fetchone()[0]

        cur.execute("""
            SELECT DATE(followed_at AT TIME ZONE 'UTC') AS day, COUNT(*)
            FROM venue_followers
            WHERE venue_id = %s AND followed_at >= NOW() - INTERVAL '30 days'
            GROUP BY day ORDER BY day
        """, (venue_id,))
        daily_rows = cur.fetchall()

    return jsonify({
        "total": total,
        "daily": [{"date": str(r[0]), "count": r[1]} for r in daily_rows],
    })


# ---------------------------------------------------------------------------
# Public fan-facing: follow / unfollow / submit report
# ---------------------------------------------------------------------------

@venue_bp.route("/api/venue/<venue_id>/follow", methods=["POST"])
def fan_follow(venue_id):
    data = request.get_json() or {}
    fan_uuid = (data.get("fan_uuid") or "").strip()
    if not fan_uuid:
        return jsonify({"error": "fan_uuid required"}), 400

    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT id FROM venues WHERE id = %s", (venue_id,))
        if not cur.fetchone():
            return jsonify({"error": "Venue not found"}), 404
        cur.execute("""
            INSERT INTO venue_followers (venue_id, fan_uuid)
            VALUES (%s, %s) ON CONFLICT (venue_id, fan_uuid) DO NOTHING
        """, (venue_id, fan_uuid))
        db.commit()

    return jsonify({"ok": True, "following": True})


@venue_bp.route("/api/venue/<venue_id>/follow", methods=["DELETE"])
def fan_unfollow(venue_id):
    data = request.get_json() or {}
    fan_uuid = (data.get("fan_uuid") or "").strip()
    if not fan_uuid:
        return jsonify({"error": "fan_uuid required"}), 400

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "DELETE FROM venue_followers WHERE venue_id = %s AND fan_uuid = %s",
            (venue_id, fan_uuid)
        )
        db.commit()

    return jsonify({"ok": True, "following": False})


@venue_bp.route("/api/venue/<venue_id>/stage-report", methods=["POST"])
def fan_submit_report(venue_id):
    """Fan-submitted stage time scoped to a venue (replaces legacy /api/stagetime/report for venue-aware submissions)."""
    import datetime as _dt
    data = request.get_json() or {}
    artist_name = (data.get("artist_name") or "").strip()
    stage_time_str = (data.get("stage_time") or "").strip()
    event_date = (data.get("event_date") or "").strip()
    fan_uuid = (data.get("fan_uuid") or "").strip() or None
    event_id = (data.get("event_id") or "").strip() or None

    if not artist_name or not stage_time_str:
        return jsonify({"error": "artist_name and stage_time required"}), 400

    try:
        h, m = int(stage_time_str.split(":")[0]), int(stage_time_str.split(":")[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        stage_time_str = f"{h:02d}:{m:02d}"
    except (ValueError, IndexError):
        return jsonify({"error": "stage_time must be HH:MM"}), 400

    parsed_date = None
    if event_date:
        try:
            parsed_date = _dt.date.fromisoformat(event_date)
        except ValueError:
            return jsonify({"error": "event_date must be YYYY-MM-DD"}), 400

    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT id FROM venues WHERE id = %s", (venue_id,))
        if not cur.fetchone():
            return jsonify({"error": "Venue not found"}), 404
        cur.execute("""
            INSERT INTO venue_stage_reports
              (venue_id, event_id, artist_name, event_date, stage_time, fan_uuid)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (venue_id, event_id, artist_name, parsed_date, stage_time_str, fan_uuid))
        db.commit()

    return jsonify({"ok": True, "message": f"Report submitted for {artist_name}"})
