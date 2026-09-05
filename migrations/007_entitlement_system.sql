-- Migration 007: Entitlement system
-- Adds: users, user_device_uuids, promo_codes, promo_code_redemptions, admin_accounts
-- Live backend: Flask / east-heights-labs/backend-api
-- Applied to: Railway PostgreSQL
-- Reviewer: approved design (see session notes 2026-09-05)
-- Run: psql $DATABASE_URL -f migrations/007_entitlement_system.sql

BEGIN;

-- ------------------------------------------------------------------
-- users
-- Authenticated accounts via Sign in with Apple.
-- apple_id = stable 'sub' claim from Apple JWT — never changes per user.
-- tier CHECK enforces valid values at DB level.
-- updated_at maintained by trigger below.
-- No device_uuid column — device history in user_device_uuids join table.
-- ------------------------------------------------------------------
CREATE TABLE users (
    id               UUID        NOT NULL DEFAULT gen_random_uuid(),
    apple_id         TEXT        NOT NULL,
    email            TEXT,
    display_name     TEXT,
    tier             TEXT        NOT NULL DEFAULT 'free'
                                 CHECK (tier IN ('free', 'premium', 'lifetime')),
    tier_source      TEXT        NOT NULL DEFAULT 'default'
                                 CHECK (tier_source IN ('default', 'subscription', 'promo_code', 'admin_grant')),
    tier_granted_by  TEXT,                              -- admin_accounts.id (audit trail)
    tier_granted_at  TIMESTAMPTZ,
    tier_expires_at  TIMESTAMPTZ,                       -- NULL = never expires
    promo_code_used  TEXT,                              -- denormalized: last code redeemed
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id),
    CONSTRAINT uq_users_apple_id UNIQUE (apple_id)
);

-- (no separate index needed — UNIQUE constraint above creates one automatically)

-- updated_at trigger — fires on every UPDATE, sets updated_at = NOW()
-- Application does NOT need to set this field manually.
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ------------------------------------------------------------------
-- user_device_uuids
-- Full device history per user — used to backfill favorites/going/
-- stage reports from pre-auth anonymous sessions.
--
-- API layer MUST use INSERT ... ON CONFLICT (user_id, device_uuid)
-- DO UPDATE SET last_seen_at = NOW() on every sign-in.
-- Do not use INSERT + separate UPDATE — the upsert is the contract.
-- ------------------------------------------------------------------
CREATE TABLE user_device_uuids (
    id           UUID        NOT NULL DEFAULT gen_random_uuid(),
    user_id      UUID        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    device_uuid  TEXT        NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id),
    CONSTRAINT uq_user_device UNIQUE (user_id, device_uuid)
);

-- Reverse lookup: find user_id by device_uuid at sign-in
CREATE INDEX idx_user_device_uuids_device ON user_device_uuids (device_uuid);

-- ------------------------------------------------------------------
-- promo_codes
-- Stored entities — not just strings.
-- CHECK (code = UPPER(code)) enforces uppercase at DB level.
-- Application must UPPER() input before insert and lookup.
-- No functional index needed — PK index covers exact-match lookups.
-- ------------------------------------------------------------------
CREATE TABLE promo_codes (
    code              TEXT        NOT NULL CHECK (code = UPPER(code)),
    grants_tier       TEXT        NOT NULL CHECK (grants_tier IN ('premium', 'lifetime')),
    max_redemptions   INTEGER,                           -- NULL = unlimited
    redemption_count  INTEGER     NOT NULL DEFAULT 0,
    expires_at        TIMESTAMPTZ,                       -- NULL = never expires
    created_by        TEXT        NOT NULL,              -- admin_accounts.id
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes             TEXT,
    active            BOOLEAN     NOT NULL DEFAULT TRUE,
    PRIMARY KEY (code)
);

-- ------------------------------------------------------------------
-- promo_code_redemptions
-- One row per (user, code) pair — Option B: one redemption per user
-- per code; users may redeem different codes over time.
-- RESTRICT on code FK: cannot delete a code that has been redeemed.
-- CASCADE on user FK: user deleted → redemptions go with them.
-- Concurrency: API must lock the promo_codes row before checking cap:
--   SELECT ... FROM promo_codes WHERE code = $1 FOR UPDATE
-- then check redemption_count < max_redemptions, insert into
-- promo_code_redemptions, and increment redemption_count — all in
-- one transaction. Lock must be on promo_codes, not promo_code_redemptions.
-- ------------------------------------------------------------------
CREATE TABLE promo_code_redemptions (
    id           UUID        NOT NULL DEFAULT gen_random_uuid(),
    code         TEXT        NOT NULL REFERENCES promo_codes (code) ON DELETE RESTRICT,
    user_id      UUID        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    redeemed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id),
    CONSTRAINT uq_redemptions_per_user_per_code UNIQUE (user_id, code)
);

CREATE INDEX idx_redemptions_code    ON promo_code_redemptions (code);
CREATE INDEX idx_redemptions_user_id ON promo_code_redemptions (user_id);

-- ------------------------------------------------------------------
-- admin_accounts
-- Internal admin users — created via CLI only (scripts/create_admin.py).
-- No self-registration. JWT: HS256, 1hr expiry, role claim = "admin".
-- active flag: soft-disable without deletion; preserves tier_granted_by
-- audit trail on users table.
-- ------------------------------------------------------------------
CREATE TABLE admin_accounts (
    id             UUID        NOT NULL DEFAULT gen_random_uuid(),
    email          TEXT        NOT NULL,
    password_hash  TEXT        NOT NULL,
    active         BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login     TIMESTAMPTZ,
    PRIMARY KEY (id),
    CONSTRAINT uq_admin_accounts_email UNIQUE (email)
);

COMMIT;
