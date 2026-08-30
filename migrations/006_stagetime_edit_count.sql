-- Migration 006: Add edit_count to fan_stagetime_reports
-- Tracks how many times a user has edited their stage time submission.
-- Cap enforced at application layer (5 edits per UUID per artist per 24 hours).

ALTER TABLE fan_stagetime_reports
  ADD COLUMN IF NOT EXISTS edit_count INTEGER NOT NULL DEFAULT 0;
