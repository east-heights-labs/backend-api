-- Migration 002: Venue Accounts + Venue Events
-- OnStage — East Heights Labs
-- Created: 2026-08-25

-- ---------------------------------------------------------------------------
-- venue_accounts
-- One account per venue at MVP. role column ready for multi-user post-alpha.
-- Drop the unique index on venue_id when adding manager/booking agent logins.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS venue_accounts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    venue_id            VARCHAR NOT NULL REFERENCES venues(id) ON DELETE CASCADE,
    email               TEXT UNIQUE NOT NULL,
    password_hash       TEXT,                           -- NULL until invite accepted
    is_active           BOOLEAN NOT NULL DEFAULT FALSE,
    role                TEXT NOT NULL DEFAULT 'owner',  -- 'owner' | 'manager' (future)
    invite_token        TEXT UNIQUE,                    -- single-use, cleared on activation
    invite_expires_at   TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login          TIMESTAMPTZ
);

-- One account per venue MVP constraint — drop this index when multi-user is added
CREATE UNIQUE INDEX IF NOT EXISTS idx_venue_accounts_one_per_venue
    ON venue_accounts(venue_id)
    WHERE is_active = TRUE OR password_hash IS NULL;

-- ---------------------------------------------------------------------------
-- venue_events
-- Events created directly by venue accounts — not sourced from TM/JamBase.
-- Served by /api/events alongside external sources, source='venue'.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS venue_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    venue_id        VARCHAR NOT NULL REFERENCES venues(id) ON DELETE CASCADE,
    created_by      UUID NOT NULL REFERENCES venue_accounts(id),
    title           TEXT NOT NULL,          -- artist name or event name
    event_date      DATE NOT NULL,
    doors_time      TIME,
    stage_time      TIME,
    ticket_url      TEXT,
    price_min       NUMERIC(10,2),
    price_max       NUMERIC(10,2),
    description     TEXT,
    image_url       TEXT,
    is_cancelled    BOOLEAN NOT NULL DEFAULT FALSE,
    is_hidden       BOOLEAN NOT NULL DEFAULT FALSE,  -- soft delete / admin hide
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_venue_events_venue_date
    ON venue_events(venue_id, event_date)
    WHERE is_cancelled = FALSE AND is_hidden = FALSE;

CREATE INDEX IF NOT EXISTS idx_venue_events_date
    ON venue_events(event_date)
    WHERE is_cancelled = FALSE AND is_hidden = FALSE;

-- ---------------------------------------------------------------------------
-- updated_at auto-trigger (reuse pattern from migration 001 if exists)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_venue_accounts_updated_at ON venue_accounts;
CREATE TRIGGER set_venue_accounts_updated_at
    BEFORE UPDATE ON venue_accounts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS set_venue_events_updated_at ON venue_events;
CREATE TRIGGER set_venue_events_updated_at
    BEFORE UPDATE ON venue_events
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
