# Dashboard Ui And Filters



---

## Reference: `BRAND_THEME_UPDATE.md`


# V4Career Brand Theme Update

## Palette
- Brand orange: `#FF6700`
- Warm white page background: `#FFF9F3`
- White cards: `#FFFFFF`
- Deep slate navigation: `#263445`
- Pale orange selected surfaces: `#FFE8D8`
- Warm border: `#EADFD5`

## Scope
- Applied orange accents to active sidebar navigation, interactive controls, report-card highlights, focus rings, and export/search actions.
- Applied warm-white page background and warmer card/table/filter surfaces.
- Kept the dark slate sidebar for contrast and readability.
- Updated login page background, input focus, and submit button to match.
- Preserved application logic, collectors, database access code, and existing feature behavior.

## Verification
- Python compilation: passed.
- JavaScript syntax check: passed.
- Full pytest suite could not collect `tests/test_api.py` in this packaging environment because Flask is not installed. Run `python -m pip install -r requirements.txt` and `python -m pytest -q` in the project's virtual environment.
- No live scraping, database writes, or deployment performed.


---

## Reference: `JOB_FAMILY_FILTER_IMPLEMENTATION.md`


# V4Career Job Family + Job Title filters

## What changed

The Job Portal filter toolbar now has two independent, dependent inputs:

1. **Job Family** – searchable major category list with live matching-job counts.
2. **Job Title** – disabled until a family is selected; then shows only original titles in that family, with live counts and an **All jobs in this family** option.

The existing Company, Location, Experience, Salary, Posted After, source selection, pagination, and Excel export controls remain in place.

## Database behaviour

The existing `jobs` table is not renamed or modified. A separate `job_title_family_map` lookup table is created automatically on first use:

- `title_hash` – indexed MD5 key for the exact trimmed title
- `original_title` – original title text, preserved for reference
- `job_family`
- `updated_at`

The included `data/job_family_mapping.json` contains the initial classification of the 15,374 distinct titles from the supplied Neon export. The application synchronizes the titles actually present in Neon into the lookup table. The hash key keeps the join index compact even when scraped titles are long. Newly scraped titles are classified by fallback keyword rules and checked during the periodic mapping sync. Titles that cannot be confidently classified are placed in **Other & Needs Review** rather than silently dropped.

If you prefer to create the table manually before deploying, run `sql/job_title_family_map.sql` in Neon SQL Editor. The application will populate it when the filters load.

## Faceted filter behaviour

- Job Family counts respect the currently selected company, location, experience, salary, posted date, status, skills, and source filters. The family and specific-title selections are excluded from the family facet itself.
- Job Title options are constrained by the selected family and the other active filters. The title selection is excluded from its own facet so the full list remains available.
- Company, Location, Experience, Salary, and Status options are refreshed using the other selected filters while excluding their own current selection.
- Changing Job Family clears the previous Job Title selection.
- Selecting **All jobs in this family** applies the family only and leaves the specific-title filter empty.
- `/api/jobs` and `/api/jobs/export` both apply `job_family` and exact `role` (job title) parameters, so table results and exports stay consistent.

## API query parameters

- `job_family`: selected family label
- `role`: exact original job title
- Existing filter parameters continue to work as before.

## Deployment notes

1. Keep your existing local `.env` file and database credentials; the updated source archive intentionally does not include `.env`.
2. Replace the application source files with the updated files from this archive.
3. Ensure the `data/job_family_mapping.json` file is included in the deployed project.
4. Deploy normally. The lookup table is created and populated when the filter endpoints are first requested.
5. Verify Job Family, Job Title, Company, Location, Experience, Salary, Status, Search, Clear, Refresh, and Export.

The mapping is an initial title-based classification. Review **Other & Needs Review** and ambiguous titles periodically; the original job titles and job records remain unchanged.


---

## Reference: `PORTAL_UI_AND_RETEST_GUIDE.md`


# V4Career: 11 active UI portals + 3 portal retests

## UI allowlist (shown only when the existing approval/token/Actor checks pass)
Naukri, Indeed, Foundit, TimesJobs, Internshala, Wellfound, Glassdoor, Hirist, Shine.com, iimjobs, Jooble.

Freshersworld, National Career Service (NCS), and QuikrJobs remain available to the CLI for retesting but are deliberately excluded from the UI allowlist until they save jobs successfully. Other non-verified portals are also excluded from the Job Portal UI. Existing database rows and job records are not deleted.

## Input fixes
- Freshersworld: removed unsupported `collectDetails` input.
- NCS: removed unsupported `retries` input.
- QuikrJobs: maps an `India` location request to `Bengaluru`, because the Actor rejected `India` as a city. This is a city-scoped test, not nationwide coverage.

Important: `APIFY_INPUT_FRESHERSWORLD`, `APIFY_INPUT_NATIONAL_CAREER_SERVICE_NCS`, or `APIFY_INPUT_QUIKRJOBS` in `.env` overrides these defaults. Remove or correct those variables for the defaults above to apply. `.env` and API tokens are not included in this package.

## Small live test commands
Run from the project root, after confirming `.env` has APIFY_API_TOKEN and APIFY_ACCESS_APPROVED_<KEY>=true for each source. Start with `--max-results 3`.

```powershell
python scripts/run_source_apify.py --source Naukri --keyword "Software Engineer" --location India --max-results 3
python scripts/run_source_apify.py --source Indeed --keyword "Software Engineer" --location India --max-results 3
python scripts/run_source_apify.py --source Foundit --keyword "Software Engineer" --location India --max-results 3
python scripts/run_source_apify.py --source TimesJobs --keyword "Software Engineer" --location India --max-results 3
python scripts/run_source_apify.py --source Internshala --keyword "Software Engineer" --location India --max-results 3
python scripts/run_source_apify.py --source Wellfound --keyword "Software Engineer" --location India --max-results 3
python scripts/run_source_apify.py --source Glassdoor --keyword "Software Engineer" --location India --max-results 3
python scripts/run_source_apify.py --source Hirist --keyword "Software Engineer" --location India --max-results 3
python scripts/run_source_apify.py --source Shine.com --keyword "Software Engineer" --location India --max-results 3
python scripts/run_source_apify.py --source iimjobs --keyword "Software Engineer" --location India --max-results 3
python scripts/run_source_apify.py --source Jooble --keyword "Software Engineer" --location India --max-results 3
python scripts/run_source_apify.py --source Freshersworld --keyword "Software Engineer" --location India --max-results 3
python scripts/run_source_apify.py --source "National Career Service (NCS)" --keyword "Software Engineer" --location India --max-results 3
python scripts/run_source_apify.py --source QuikrJobs --keyword "Software Engineer" --location India --max-results 3
```

Do not test LinkedIn Jobs with the current input: a prior run saved hundreds of jobs despite a small requested limit.


---

## Reference: `PORTAL_UI_CONFIGURATION.md`


# V4Career portal/company UI configuration

## Changes in this package
- Portal API now exposes only portal sources whose Actor/feed, input configuration, API token, and explicit approval flag are configured at runtime.
- Company Career API/UI now exposes only company sources with a validated collector (`verified_collector`) or a fully configured and approved company Actor.
- Reports use the same portal list returned by the API instead of a separate hard-coded list.
- Expanded common field aliases in `scripts/run_source_apify.py` to improve job field mapping.

## Important
- This does not claim all 25 portals are operational. The 25-name catalog is retained for configuration and script discovery, but entries without reviewed Actor IDs/input schemas and explicit approval are hidden from the UI.
- The project `.env` is intentionally not packaged. Copy your existing local `.env` into the extracted `V4Career` folder and do not share it. Existing database settings and credentials must be preserved.
- The 5 missing Actor IDs remain unresolved; do not use guessed IDs. Portal API visibility is determined from the actual runtime `.env` values.
- Companies shown in the UI are limited to validated collector entries or explicitly configured company Actors. This filter does not delete existing database jobs or source rows.


---

## Reference: `SIDEBAR_UPDATE_2026-10-21.md`


# Sidebar update — 2026-10-21

- Restored the expandable Companies sidebar group with an aligned master checkbox and plus/minus indicator.
- Displayed Infosys as the first company integration; additional companies stay hidden until individually verified and rolled out.
- Applied distinct navigation glyphs for Dashboard, Job Portal, Companies, and Reports.
- Increased sidebar contrast on hover/focus and standardized icon, checkbox, label, count, and dropdown alignment.
- Added cache-busting versions to the CSS and JavaScript assets.

Validation: Python compilation and `node --check static/app.js` passed. Full pytest collection could not run in this environment because Flask is not installed (`ModuleNotFoundError: flask`). No database writes or deployment were performed.


---

## Reference: `UI_COMPANY_PORTAL_SORT_UPDATE.md`


# UI Update: Company Visibility and Portal Ordering

- Removed Accenture, HCLTech, and Wipro from the Companies sidebar UI as requested. This is a frontend visibility change; it does not delete database records or catalog definitions.
- Job portals are now sorted by current stored job count in descending order, with alphabetical order used to break ties.
- Remaining company entries are sorted by current stored job count in descending order, with alphabetical order used to break ties.
- Existing Select all controls and individual checkboxes remain in place.

After replacing the project files, restart Flask and hard-refresh the browser (Ctrl+F5) so the updated JavaScript is loaded.


---

## Reference: `UI_UPGRADE_NOTES.md`


# V4Career UI and Source Display Upgrade

This project archive is based on the uploaded ZIP and includes these targeted changes:

1. Sidebar and main content use separate vertical scroll containers.
2. UI styling is refined with consistent surfaces, hover states, focus states, and typography polish while retaining the existing layout structure.
3. The source API avoids displaying a duplicate Naukri entry when the canonical Naukri row is empty and a populated `Naukri via Apify` row exists. It preserves database records and source IDs, and maps readiness to the Naukri collector configuration.

## Important database compatibility note
The uploaded archive's `app.py` uses MySQL-specific database operations (`mysql.connector`, `ON DUPLICATE KEY UPDATE`) and is **not the Neon PostgreSQL version** described in the continuation brief. This package is based on the uploaded archive and must not replace a newer Neon-backed working directory without first reconciling the database repository and SQL dialect. No database migration is run by this package.

## Run locally
1. Extract this ZIP into a new test folder.
2. Create a local `.env` from your own safe configuration; no `.env` or secrets are included.
3. Install dependencies using `python -m pip install -r requirements.txt`.
4. Configure the database and scraper credentials locally.
5. Run the app using the command already documented for your project, then verify Dashboard, Job Portal, source selection, filters, pagination, export, and authentication.
6. Run `python -m pytest -v`.

The ZIP intentionally excludes `.env`, `.git`, Python caches, and common local dump/backup files.
