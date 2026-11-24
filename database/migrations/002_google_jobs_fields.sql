-- Additive V4Career job metadata migration for Google Jobs and remote job boards.
-- Safe for existing PostgreSQL databases; does not delete or rewrite existing job rows.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS country VARCHAR(120);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS work_mode VARCHAR(40);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS employment_type VARCHAR(100);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS company_logo VARCHAR(2048);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS posted_via VARCHAR(255);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS apply_options JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS raw_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_jobs_country ON jobs (country);
CREATE INDEX IF NOT EXISTS idx_jobs_work_mode ON jobs (work_mode);
CREATE INDEX IF NOT EXISTS idx_jobs_employment_type ON jobs (employment_type);
CREATE INDEX IF NOT EXISTS idx_jobs_posted_via ON jobs (posted_via);
