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
    """
    Register the DB teardown handler.
    Actual pool creation is deferred to first get_db() call (lazy init).
    This avoids connection failures during Vercel cold start import.
    """
    @app.teardown_appcontext
    def close_db(error):
        conn = g.pop("_db_conn", None)
        if conn is not None and _pool is not None:
            try:
                _pool.putconn(conn)
            except Exception:
                pass


def _ensure_pool():
    """Lazy pool creation — called on first DB access, not at import time."""
    global _pool
    if _pool is not None:
        return
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set — DB features disabled")
    # SimpleConnectionPool is fine for serverless (single-threaded per invocation)
    _pool = psycopg2.pool.SimpleConnectionPool(
        minconn=1,
        maxconn=3,
        dsn=DATABASE_URL,
        sslmode="require",
        connect_timeout=8,
    )


def get_db():
    """Return a psycopg2 connection for the current request context."""
    _ensure_pool()

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
