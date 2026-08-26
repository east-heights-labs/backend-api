-- Migration 004: Fan Stage Time Reports (general — no venue FK required)
-- OnStage — East Heights Labs
-- Created: 2026-08-26
--
-- Replaces the /tmp/stagetime_reports.json storage that was lost on every
-- Vercel cold start. This table accepts reports without a venue_id so the
-- iOS /api/stagetime/report endpoint works without a venue context.

CREATE TABLE IF NOT EXISTS fan_stagetime_reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artist_name     TEXT NOT NULL,
    venue_name      TEXT,
    city            TEXT,
    event_date      DATE,
    stage_time      TIME NOT NULL,
    fan_uuid        TEXT,                   -- optional device UUID
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fan_stagetime_reports_artist
    ON fan_stagetime_reports(lower(artist_name));

CREATE INDEX IF NOT EXISTS idx_fan_stagetime_reports_date
    ON fan_stagetime_reports(event_date);
