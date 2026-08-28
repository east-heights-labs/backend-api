-- Migration 005: user_favorites and user_going tables
-- Supports /api/favorites and /api/going endpoints called from iOS VenueDetailView.
-- Users are identified by anonymous device UUID (no auth required).

CREATE TABLE IF NOT EXISTS user_favorites (
    id          SERIAL PRIMARY KEY,
    user_uuid   TEXT        NOT NULL,
    venue_id    TEXT        NOT NULL,           -- e.g. "tm_venue_123" or "12" (DB id)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_uuid, venue_id)
);

CREATE INDEX IF NOT EXISTS idx_user_favorites_user ON user_favorites (user_uuid);

CREATE TABLE IF NOT EXISTS user_going (
    id              SERIAL PRIMARY KEY,
    user_uuid       TEXT        NOT NULL,
    event_source    TEXT        NOT NULL,       -- "ticketmaster", "jambase", "venue", "search"
    event_source_id TEXT        NOT NULL,       -- source-specific event id
    event_date      DATE        NOT NULL,
    headliner_name  TEXT,
    venue_name      TEXT,
    doors_time      TEXT,
    estimated_stage_time TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_uuid, event_source, event_source_id)
);

CREATE INDEX IF NOT EXISTS idx_user_going_user_date ON user_going (user_uuid, event_date);
