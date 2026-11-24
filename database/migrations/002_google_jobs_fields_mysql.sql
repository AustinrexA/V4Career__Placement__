-- Additive V4Career job metadata migration for MySQL 8.
-- Run once against the existing V4Career database. Does not delete job rows.
ALTER TABLE jobs
    ADD COLUMN country VARCHAR(120) NULL,
    ADD COLUMN work_mode VARCHAR(40) NULL,
    ADD COLUMN employment_type VARCHAR(100) NULL,
    ADD COLUMN company_logo VARCHAR(2048) NULL,
    ADD COLUMN posted_via VARCHAR(255) NULL,
    ADD COLUMN apply_options JSON NULL,
    ADD COLUMN raw_metadata JSON NULL;

CREATE INDEX idx_jobs_country ON jobs (country);
CREATE INDEX idx_jobs_work_mode ON jobs (work_mode);
CREATE INDEX idx_jobs_employment_type ON jobs (employment_type);
CREATE INDEX idx_jobs_posted_via ON jobs (posted_via);
