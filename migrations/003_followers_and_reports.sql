-- Migration 003: Venue Followers + Venue Stage Time Reports
-- OnStage — East Heights Labs
-- Created: 2026-08-25

-- ---------------------------------------------------------------------------
-- venue_followers
-- Fan follow relationships. Fan identified by device UUID (anonymous).
-- venue_id references venues.id (VARCHAR).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS venue_followers (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    venue_id        VARCHAR NOT NULL REFERENCES venues(id) ON DELETE CASCADE,
    fan_uuid        TEXT NOT NULL,          -- device UUID from iOS app
    followed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(venue_id, fan_uuid)              -- one follow per device per venue
);

CREATE INDEX IF NOT EXISTS idx_venue_followers_venue
    ON venue_followers(venue_id);

-- ---------------------------------------------------------------------------
-- venue_stage_reports
-- Fan-submitted stage time reports scoped to a venue + event.
-- status: 'pending' | 'confirmed' | 'flagged'
-- Confirmed by venue account → overrides community estimate, gets Venue badge.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS venue_stage_reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    venue_id        VARCHAR NOT NULL REFERENCES venues(id) ON DELETE CASCADE,
    event_id        TEXT,                   -- TM/JamBase event ID (nullable — may not match)
    artist_name     TEXT NOT NULL,
    venue_name      TEXT,
    event_date      DATE,
    stage_time      TIME NOT NULL,
    fan_uuid        TEXT,                   -- submitting device (anonymous)
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | confirmed | flagged
    reviewed_by     UUID REFERENCES venue_accounts(id),
    reviewed_at     TIMESTAMPTZ,
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_venue_stage_reports_venue_status
    ON venue_stage_reports(venue_id, status);

CREATE INDEX IF NOT EXISTS idx_venue_stage_reports_venue_date
    ON venue_stage_reports(venue_id, event_date);
