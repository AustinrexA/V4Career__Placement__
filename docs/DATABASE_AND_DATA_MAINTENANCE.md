# Database And Data Maintenance



---

## Reference: `BACKUP_AND_TEST_NOTES.md`


This package is a separate test copy. Existing production files and requirements.txt were not modified for JobSpy. Use V4Career_Backup_Before_JobSpy_Test.zip to restore the prior free-collector package. JobSpy test writes only CSV/JSON under jobspy_test_results and never writes to the V4Career database.


---

## Reference: `INSTALL_CANONICAL_JOB_ROLE_GROUPING.txt`


V4Career - Canonical Job Role Grouping

This update fixes the issue shown in the latest screenshot. Previously the dropdown only grouped exact titles that differed by capitalization/spacing. It now groups common title variants into canonical role labels, with counts summed across every matching job record.

Examples:
- Senior Data Analyst - SQL + DATA ANALYST -> Data Analyst
- Lead Data Engineer + Data Engineer -> Data Engineer
- Lead DevOps Engineer + DevOps Engineer -> DevOps Engineer
- Senior Cloud Engineer + Cloud Engineer -> Cloud Engineer
- QA Automation Engineer + Automation Test Engineer -> Automation Test Engineer

The role grouping is family-specific and is implemented in the database title-family mapping. Selecting a canonical role filters all underlying job-title variants in the jobs table.

Files to replace:
1. app.py
2. static/app.js

Database migration:
- On first API request after restart, app.py adds role_group to job_title_family_map if it is not already present.
- It then recalculates family and canonical role group values for all distinct titles.
- Existing jobs rows are not modified or deleted.

Install:
1. Back up your existing app.py and static/app.js.
2. Replace with these files.
3. Restart Flask.
4. Wait for the first filter-options request to finish (it synchronizes the title mapping).
5. Hard refresh with Ctrl+F5.

Checks completed:
- Python syntax compilation passed.
- JavaScript syntax check passed.
- Canonical role grouping sample checks passed.

Live database counts and end-to-end UI behavior must be verified against the connected MySQL/PostgreSQL database after installation.


---

## Reference: `MERGE_CATALOG_FIX_README.md`


# V4Career source catalog correction

This patch is based on the latest uploaded `V4Career_final_version_(4).zip`.

## Replace
- `collectors/source_catalog.py`
- `tests/test_source_catalog.py` (updates stale assertions to the intended 15 + 6 = 21 portal catalog)

## Why
The uploaded project had a source catalog with 21 entries but stale catalog tests expecting a different 25-portal list and a different sidebar allowlist. This patch keeps the 15 portals in the current project, appends the six requested portals, removes the duplicate `DEFAULT_APIFY_ACTORS` declaration, and makes all 21 catalogued portals selectable in the UI.

The five remote boards are identified as one shared multi-board collector group; they are not represented as five independent Actors.

## Apply
Extract this ZIP into the project root and preserve the folder structure. Back up the two existing files first.

## Validate
From the V4Career project directory run:
`python -m pytest tests/test_source_catalog.py -q`

This validates catalog consistency only. It does not prove live Apify Actor access or successful scraping.


---

## Reference: `MERGE_PATCH_README.md`


# V4Career delta files: five remote job boards

This folder is a patch for the existing `V4Career_final_version_(2)` project.
It is NOT a complete project. Copy the contents into the existing project root,
preserving the folder structure and replacing files when Windows asks.

## Files to replace
- `collectors/source_catalog.py`
- `collectors/normalization.py`
- `collectors/repository.py`
- `scripts/run_source_apify.py`
- `database/postgres_schema.sql`
- `database/mysql_schema.sql`

## New files to add
- `scripts/run_remote_multi.py`
- `database/migrations/002_google_jobs_fields.sql`
- `database/migrations/002_google_jobs_fields_mysql.sql`

## Database migration
For an existing Neon/PostgreSQL database, run:
`database/migrations/002_google_jobs_fields.sql`

For an existing MySQL database, run:
`database/migrations/002_google_jobs_fields_mysql.sql`

Do not run both migrations. They add metadata columns and indexes; they do not delete existing job records.
For a brand-new database only, use the updated full schema for the database engine you selected.

## Add these settings to your existing .env (do not overwrite .env)
Keep your existing `APIFY_API_TOKEN`, database URL/credentials, and other settings.
Append and adjust these values after checking the Actor's input schema and your authorization:

```dotenv
APIFY_ACTOR_REMOTE_MULTI=rel8ble/remote-jobs-aggregator
APIFY_ACCESS_APPROVED_REMOTE_MULTI=false
APIFY_INPUT_REMOTE_MULTI={"sources":["remoteok","weworkremotely","jobicy","workingnomads","arbeitnow"]}
```

Keep approval flags `false` until you have reviewed the Actor's terms, data rights,
input schema, and authorization. The five remote portals are collected through one
multi-board Actor; this does not mean five independently tested API integrations.

## Run locally
Five remote portals:
`python scripts/run_remote_multi.py`

The remote runner filters records by IT-related title/category/tags and writes them
through V4Career's existing normalization and database repository.

## Important
- The 15 existing portal entries remain in the catalog; the five requested sources are appended.
- Remote OK, We Work Remotely, Jobicy, Working Nomads, and Arbeitnow use the new multi-board runner.
- Actual Actor runs have not been performed here. Verify the Actor input schema and test with your own token before enabling collection.
- The existing `.env` is deliberately not included in this patch.


---

## Reference: `MYSQL_COMPATIBILITY.md`


# V4Career dual-database compatibility

This development version supports MySQL 8 and PostgreSQL/Neon selected through `DB_ENGINE`. Existing UI, API, collectors and export behavior are intended to remain unchanged.

## Local test setup

1. Extract this ZIP into a new directory. Do not overwrite your existing project.
2. Create `.env` from `.env.example` and configure `DB_ENGINE=mysql` with the connection details for the isolated `job_portal_test` database. Never commit `.env`.
3. Install dependencies: `python -m pip install -r requirements.txt`.
4. Run `python -m compileall -q app.py collectors scripts tests` and `python -m unittest discover -s tests -v`.
5. Open the app and test `/api/health`, dashboard, job listing/search/filtering, company/report pages, Excel export, and one controlled collector run.
6. Verify counts before and after testing. Do not run normalization with `--apply` unless you explicitly intend to consolidate source aliases and have a fresh backup.

## Engine settings

- MySQL: `DB_ENGINE=mysql`, `DB_HOST`, `DB_PORT` (default 3306), `DB_NAME`, `DB_USER`, `DB_PASSWORD`. For a local non-TLS MySQL instance only, `DB_SSL_DISABLED=true`.
- PostgreSQL/Neon: `DB_ENGINE=postgres`, `NEON_HOST`, `NEON_PORT` (default 5432), `NEON_DB`, `NEON_USER`, `NEON_PASSWORD`, and optional `NEON_SSLMODE` (default `require`). If `DB_ENGINE` is absent, the app defaults to PostgreSQL to preserve the previous production behavior.

## Safety and verification limits

- `database/mysql_schema.sql` creates missing tables but is not a migration script and should not be applied blindly to a populated database.
- The PostgreSQL schema and the explicit MySQL-to-Neon migration utilities are retained.
- Do not point local tests at production credentials. Do not deploy until the full test suite and a real MySQL integration test pass.
- This package was syntax-checked in its preparation environment. Database integration and the full test suite still need to be run in an environment with project dependencies and access to the isolated test database.
