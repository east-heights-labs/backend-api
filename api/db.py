"""
db.py — Database connection for OnStage Flask backend.

Uses psycopg2 with a simple per-request connection pattern suitable
for Vercel serverless (short-lived functions, no persistent processes).

Each request gets a fresh connection from the pool; connections are
returned on teardown via Flask's g + teardown_appcontext.

Usage:
  from .db import get_db, init_db_pool
  db = get_db()          # returns psycopg2 connection
  with db.cursor() as cur:
      cur.execute(...)
  db.commit()            # commit explicitly; rollback on exception

Initialization:
  Call init_db_pool(app) once in app factory.
"""

import os
import psycopg2
import psycopg2.pool
from flask import g

_pool = None
DATABASE_URL = os.environ.get("DATABASE_URL", "")


def init_db_pool(app):
    """Initialize connection pool and register teardown. Call once at startup."""
    global _pool
    if not DATABASE_URL:
        app.logger.warning("DATABASE_URL not set — DB features disabled")
        return

    # minconn=1, maxconn=5 — Vercel functions are short-lived; keep it small
    _pool = psycopg2.pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=5,
        dsn=DATABASE_URL,
        sslmode="require",
        connect_timeout=5,
    )

    @app.teardown_appcontext
    def close_db(error):
        conn = g.pop("_db_conn", None)
        if conn is not None and _pool is not None:
            _pool.putconn(conn)


def get_db():
    """Return a psycopg2 connection for the current request context."""
    if _pool is None:
        raise RuntimeError("Database not configured — DATABASE_URL missing")

    if "_db_conn" not in g:
        conn = _pool.getconn()
        conn.autocommit = False
        g._db_conn = conn

    return g._db_conn


def run_migration(sql: str):
    """
    Run a SQL migration string directly (used by CLI).
    Opens its own connection outside Flask request context.
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")

    conn = psycopg2.connect(DATABASE_URL, sslmode="require", connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        conn.close()
