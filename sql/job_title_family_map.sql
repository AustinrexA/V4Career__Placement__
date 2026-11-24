-- V4Career Job Family filter lookup.
-- The Flask application also creates and populates this table automatically.
-- MD5(title) provides a compact, indexed join key for long original titles.
-- This table does not alter the existing jobs table.

CREATE TABLE IF NOT EXISTS job_title_family_map (
    title_hash CHAR(32) PRIMARY KEY,
    original_title TEXT NOT NULL,
    job_family VARCHAR(160) NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
