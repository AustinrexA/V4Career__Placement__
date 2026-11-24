# Testing Performance And Changelog



---

## Reference: `LOCAL_IMPLEMENTATION_STATUS.md`


# Local implementation notes — 2026-10-20

## Changes in this review build
- Added `/api/dashboard/trend`, a database-backed 14-day job creation count series with zero-filled days.
- Added a compact 14-day trend chart to the Dashboard using existing HTML/CSS/JavaScript (no new chart dependency).
- Added a Dashboard-to-Reports navigation action beside Recent Collection Activity.
- Preserved existing job listing markup and collection workflow.

## Important scope status
This is an incremental review build, **not a completion of the full requested upgrade**. The six separate Reports datasets and their dedicated filters/exports, expanded Data Quality checks, and verified/ready-to-run employer-specific collectors still require implementation and testing. The existing Reports page remains the current overview.

## Local setup (Windows PowerShell)
```powershell
cd .\V4Career_UI_Upgrade
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env with your own local database settings and admin credentials.
python app.py
```
If `.env.example` is not present, create `.env` from the variable names documented in `README.md`; never copy credentials from another environment into a shared ZIP.

## Checks performed in this environment
- Python compilation: passed for `app.py`, `collectors/*.py`, and `collectors/sources/*.py`.
- JavaScript syntax (`node --check static/app.js`): passed.
- Pytest: blocked because Flask is not installed in this execution environment. With `PYTHONPATH=.` set, test collection reached the missing-Flask dependency. No database-backed tests are claimed as passed.
- No production database was contacted and no deployment or GitHub push was performed.


---

## Reference: `PERFORMANCE_OPTIMIZATION_NOTES.md`


# V4Career performance optimization update

This update targets the slow first-page load shown in the browser Network panel.

## Changes

1. **Lazy-load Reports:** The report overview and detailed report no longer fetch data during initial page startup. They load when the user opens Reports. This avoids duplicate dashboard-statistics and source requests while landing on Dashboard.
2. **PostgreSQL connection pooling:** `collectors/db.py` now uses `psycopg_pool.ConnectionPool` for PostgreSQL/Neon and returns leased connections to the pool when existing code calls `connection.close()`. MySQL pooling behavior is unchanged.
3. **Configurable pool limits:** Defaults are `PG_POOL_MIN_SIZE=1`, `PG_POOL_MAX_SIZE=5`, `PG_POOL_TIMEOUT=8`, and `PG_POOL_MAX_LIFETIME=1800`. Adjust only after measuring Render concurrency and Neon connection limits.
4. **Additional safe indexes:** `database/performance_indexes_postgres.sql` now includes indexes for job creation dates and collection-run lookups by source/time. These are non-destructive `CREATE INDEX IF NOT EXISTS` statements.
5. **Dependency:** Requirements use `psycopg[binary,pool]` so Render installs the pooling package.

## Deploy and verify

1. Review the diff and keep the currently deployed revision available for rollback.
2. Deploy the code to a staging service first if possible.
3. Run the SQL statements in `database/performance_indexes_postgres.sql` once against the correct Neon database; index creation changes schema but does not alter job records.
4. Confirm `/api/health` returns healthy and verify job/source counts remain unchanged.
5. In DevTools Network, reload the Dashboard and verify that `/api/reports/jobs` and the report-only `/api/collection-runs?limit=8` request are not issued until Reports is opened.
6. Compare dashboard, job, filter, and report timings before/after. Cache is process-local, so cold and warm requests may differ.

## Validation notes

- `python -m py_compile app.py collectors/db.py` passed.
- `node --check static/app.js` passed where Node.js is available.
- The Python unit-test discovery run could not import `tests/test_api.py` in the local execution environment because Flask is not installed there. Run the full suite in the project's virtual environment after installing `requirements.txt`.
- Do not run database migrations or change job data as part of this performance update. Preserve the MySQL backup.


---

## Reference: `RELEASE_NOTES_2026-10-14.md`


# V4Career update — 2026-10-14

## Changes

1. Job portal checkboxes start unchecked. The dashboard shows a prompt until the user explicitly selects at least one portal. Refreshing source metadata preserves the user's current selection.
2. Changing the role or location no longer clears experience pre-emptively. The selected experience is retained if it is still available for the new filter combination; it is cleared only when the backend returns no matching option.
3. Excel export now has two worksheets: `Job Listings` with a concise description preview and `Full Descriptions` with readable, normalized text, wrapping, wider columns, row sizing, frozen headers, filters, alternating row shading, and clickable job URLs. Imported text is guarded against Excel formula interpretation.
4. Added candidate Apify Actor IDs and source-specific default input schemas for Indeed, Foundit, Internshala, TimesJobs, and Wellfound. The runner maps their common output fields into V4Career's existing job schema and continues to use the current MySQL repository/deduplication path.

## Important collection safety

- Candidate Actors are community-maintained and may change, stop working, or incur usage charges.
- All `APIFY_ACCESS_APPROVED_<PORTAL_KEY>` flags remain `false` by default. A source is skipped until explicitly approved locally.
- The Internshala Actor selected here supports keyword/mode and detail options but does not accept a location parameter; filter its collected records by location in V4Career.
- Do not commit or share `.env`. This ZIP intentionally excludes `.env`, `.git`, virtual environments, caches, and local data.

## Validation performed in this environment

- Python source compilation: passed.
- JavaScript syntax check (`node --check static/app.js`): passed.
- HTML parser check: passed.
- Source catalog unit tests: 3 passed.
- Live Flask/MySQL integration and actual Apify runs were not performed in this environment; configure a local token and database, then run a small approved test for each Actor before scheduling collection.

## Dashboard and salary-filter upgrade
- Added a dedicated Dashboard overview and made Job Portal open the existing listing workspace.
- Added database-backed cards and recent source/collection activity.
- Added normalized salary metadata, salary filters, salary display, and salary fields in Excel export.
- Added `scripts/migrations/20261014_add_salary_fields.sql`; apply once before starting this version.
- Added `README_PRODUCTION_UPGRADE.md`, `.env.example`, and `wsgi.py` for deployment preparation.
- Live `.env`, lock files, Python bytecode, and test caches are excluded from the delivery archive.
- Validation: Python compilation and JavaScript syntax checks passed; normalization/source-catalog tests passed (16 tests, 25 subtests). The Flask API suite could not be run in the build environment because Flask is not installed and package installation is unavailable.
