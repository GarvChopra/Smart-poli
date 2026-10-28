-- Waitlist only. No link to the app database, no names, no health data.
CREATE TABLE IF NOT EXISTS waitlist (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  email_lc      TEXT    NOT NULL,            -- lower-cased email
  code          TEXT    NOT NULL,            -- the person's referral code (unguessable)
  confirm_hash  TEXT,                        -- sha256 of the single-use confirm token; NULL once used
  confirmed_at  INTEGER,                     -- unix seconds
  last_email_at INTEGER NOT NULL,
  created_at    INTEGER NOT NULL,
  referred_by   INTEGER,                     -- id of a confirmed referrer
  board_opt_in  INTEGER NOT NULL DEFAULT 0,  -- shown on the public board only if 1
  initials      TEXT                         -- 1-3 capital letters, only when opted in
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_waitlist_email   ON waitlist (email_lc);
CREATE UNIQUE INDEX IF NOT EXISTS ux_waitlist_code    ON waitlist (code);
CREATE INDEX        IF NOT EXISTS ix_waitlist_confirm ON waitlist (confirm_hash);
CREATE INDEX        IF NOT EXISTS ix_waitlist_ref     ON waitlist (referred_by);
