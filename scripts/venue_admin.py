#!/usr/bin/env python3
"""
venue_admin.py — CLI for venue account management (alpha admin tool).

Usage:
  python scripts/venue_admin.py invite <venue_id> <email>
  python scripts/venue_admin.py list
  python scripts/venue_admin.py deactivate <account_id>
  python scripts/venue_admin.py reinvite <account_id> <email>

Reads DATABASE_URL and DASHBOARD_BASE_URL from environment (or .env).
"""

import sys
import os
import uuid
import datetime

# Load .env if present
_env = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(_env):
    with open(_env) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DASHBOARD_BASE_URL = os.environ.get("DASHBOARD_BASE_URL", "https://dashboard.eastheightslabs.com")

if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set")
    sys.exit(1)

import psycopg2


def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require", connect_timeout=10)


def generate_token():
    return uuid.uuid4().hex + uuid.uuid4().hex


def invite_expiry():
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=72)


# ---------------------------------------------------------------------------

def cmd_invite(venue_id: str, email: str):
    """Generate an invite link for a venue."""
    email = email.strip().lower()
    conn = get_conn()
    with conn.cursor() as cur:
        # Verify venue exists
        cur.execute("SELECT id, name FROM venues WHERE id = %s", (venue_id,))
        venue = cur.fetchone()
        if not venue:
            print(f"ERROR: No venue with id {venue_id}")
            sys.exit(1)

        # Check active account
        cur.execute(
            "SELECT id FROM venue_accounts WHERE venue_id = %s AND is_active = TRUE",
            (venue_id,)
        )
        if cur.fetchone():
            print(f"ERROR: Venue '{venue[1]}' already has an active account")
            sys.exit(1)

        token = generate_token()
        expires = invite_expiry()

        cur.execute("""
            INSERT INTO venue_accounts (venue_id, email, invite_token, invite_expires_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (email) DO UPDATE
              SET invite_token = EXCLUDED.invite_token,
                  invite_expires_at = EXCLUDED.invite_expires_at,
                  updated_at = NOW()
        """, (venue_id, email, token, expires))
        conn.commit()

    invite_url = f"{DASHBOARD_BASE_URL}/accept-invite?token={token}"
    print(f"\n✅ Invite created")
    print(f"   Venue:      {venue[1]}")
    print(f"   Email:      {email}")
    print(f"   Expires:    {expires.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"   Invite URL: {invite_url}\n")


def cmd_list():
    """List all venue accounts."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT va.id, va.email, va.is_active, va.role,
                   va.last_login, v.name
            FROM venue_accounts va
            JOIN venues v ON v.id = va.venue_id
            ORDER BY va.created_at DESC
        """)
        rows = cur.fetchall()

    if not rows:
        print("No venue accounts found.")
        return

    print(f"\n{'ID':<38} {'Email':<30} {'Venue':<30} {'Status':<10} {'Role':<10} {'Last Login'}")
    print("-" * 130)
    for r in rows:
        status = "active" if r[2] else "pending"
        login = r[4].strftime('%Y-%m-%d') if r[4] else "never"
        print(f"{str(r[0]):<38} {r[1]:<30} {r[5]:<30} {status:<10} {r[3]:<10} {login}")
    print()


def cmd_deactivate(account_id: str):
    """Deactivate a venue account."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE venue_accounts SET is_active = FALSE, updated_at = NOW() WHERE id = %s RETURNING email",
            (account_id,)
        )
        row = cur.fetchone()
        conn.commit()

    if not row:
        print(f"ERROR: No account with id {account_id}")
        sys.exit(1)
    print(f"✅ Deactivated account: {row[0]}")


def cmd_reinvite(account_id: str, email: str):
    """Generate a fresh invite for an existing (possibly deactivated) account."""
    email = email.strip().lower()
    token = generate_token()
    expires = invite_expiry()

    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE venue_accounts
            SET email = %s,
                invite_token = %s,
                invite_expires_at = %s,
                is_active = FALSE,
                password_hash = NULL,
                updated_at = NOW()
            WHERE id = %s
            RETURNING id
        """, (email, token, expires, account_id))
        row = cur.fetchone()
        conn.commit()

    if not row:
        print(f"ERROR: No account with id {account_id}")
        sys.exit(1)

    invite_url = f"{DASHBOARD_BASE_URL}/accept-invite?token={token}"
    print(f"\n✅ Re-invite created")
    print(f"   Email:      {email}")
    print(f"   Expires:    {expires.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"   Invite URL: {invite_url}\n")


# ---------------------------------------------------------------------------

COMMANDS = {
    "invite": (cmd_invite, ["venue_id", "email"]),
    "list": (cmd_list, []),
    "deactivate": (cmd_deactivate, ["account_id"]),
    "reinvite": (cmd_reinvite, ["account_id", "email"]),
}

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in COMMANDS:
        print("Usage:")
        for name, (_, params) in COMMANDS.items():
            print(f"  python scripts/venue_admin.py {name} {' '.join(f'<{p}>' for p in params)}")
        sys.exit(1)

    cmd_name = args[0]
    fn, params = COMMANDS[cmd_name]
    provided = args[1:]

    if len(provided) != len(params):
        print(f"Usage: python scripts/venue_admin.py {cmd_name} {' '.join(f'<{p}>' for p in params)}")
        sys.exit(1)

    fn(*provided)
