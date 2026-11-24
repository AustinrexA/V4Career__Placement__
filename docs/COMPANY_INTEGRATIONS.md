# Company Integrations



---

## Reference: `ALL_COMPANY_INTEGRATION_README.md`


# V4Career — Unified Company Collector Bundle

This bundle combines the uploaded full V4Career project with the latest Infosys API collector and concurrent structured-collector runner. It keeps the existing Flask UI and repository/database layer rather than rebuilding them.

## Company sources in the catalog
The catalog includes TCS, Infosys, Wipro, HCLTech, Accenture, Cognizant, Capgemini, Amazon, Microsoft, Google, IBM, Adobe, Zoho, Freshworks, and Paytm.

## Important distinction
- **Company catalog entry** means the official career site is configured as a starting point. It does not guarantee that the site exposes public structured job data.
- **Infosys** has a dedicated public search API adapter. The adapter was previously tested locally by the user against the live endpoint (100 unique jobs and 3 official job URLs returning HTTP 200).
- **Other companies** use the existing conservative schema.org `JobPosting` collector unless a separate adapter is implemented and validated. A site may return zero jobs if it uses client-rendered content, a different API, anti-bot protection, or does not publish JSON-LD. This bundle does not falsely label those sources as verified API integrations.
- The concurrent batch runner can reduce total wall-clock time, but concurrency is bounded and does not bypass site rate limits or access restrictions.

## Install and test (PowerShell)
1. Extract the ZIP into a new folder; keep the current working project and database backup.
2. Copy your own local `.env` into the project root only if needed. The ZIP deliberately excludes `.env` and secrets. Do not upload `.env`.
3. Activate your existing Python environment and install dependencies if needed:
   `python -m pip install -r requirements.txt`
4. Run offline tests first:
   `python -m pytest -q`
5. Test only Infosys without saving records:
   ```powershell
   @'
   from collectors.company_career_catalog import COMPANY_CAREER_BY_KEY
   from collectors.company_career_structured import collect_structured_jobs
   jobs, stats = collect_structured_jobs(COMPANY_CAREER_BY_KEY["INFOSYS"], max_results=10, timeout=30, max_pages=10)
   print("jobs:", len(jobs))
   print(stats)
   '@ | python -
   ```
6. Run a single-company collection only after reviewing `.env` database settings and confirming the target database is the intended local/test database. The CLI collection runner writes records.

## Collect companies
- One company, with persistence: `python scripts/run_company_career_structured.py --source INFOSYS --max-results 100`
- All catalog companies concurrently, with persistence: `python scripts/run_company_careers_structured_batch.py --max-results 50 --workers 3`

The batch runner writes to the configured database. Start with one company in a local/test database before using the all-company batch. The structured batch will report failures and may report zero jobs for sources without publicly exposed JSON-LD.

## Not yet claimed
This bundle does not claim that all 25 companies have verified, company-specific API adapters, nor that all 15 return live job data. Amazon, Wipro, TCS, and the other sources still require source-specific endpoint research and live validation where the generic collector cannot find genuine job postings.


---

## Reference: `COMPANY_CAREER_INTEGRATION_UPDATE.md`


# V4Career Company Career Integration Update

## Implemented

- Added a built-in structured collector in `collectors/company_career_structured.py` for the 15 existing company sources.
- The collector only accepts schema.org `JobPosting` JSON-LD, requires an India-indicating location, checks employer identity when supplied, validates the job URL against the configured official host allowlist, deduplicates within a run, and skips generic program/career pages.
- Added TCS entry-level, NQT and Ignite/Smart Hiring discovery URLs. Those pages are discovery points only; they are not themselves treated as job records.
- Added `scripts/run_company_career_structured.py` for a single company and `scripts/run_company_careers_structured_batch.py` for all catalog companies.
- Hardened the optional Apify company runner so it rejects known landing pages, generic program titles, mismatched employer names, and URLs outside the configured official host allowlist. Missing Actor status no longer defaults to `active`.
- Added failure-run recording for unreachable official career pages using the existing MySQL/PostgreSQL repository helpers.
- Added company job counts and method labels to the Companies sidebar. A JSON-LD label is not a claim that the site has been live-verified.
- Added `.env.example` containing placeholders only and updated integration documentation.

## Test results

Passed:

- Python compilation for modified Python modules and `app.py`.
- Focused regression suite: **30 tests passed, 40 subtests passed** (`test_company_career_catalog.py`, `test_company_career_structured.py`, `test_normalization.py`, `test_source_catalog.py`, `test_db_compatibility.py`).

Blocked:

- Full `pytest -q` cannot collect `tests/test_api.py` in the current environment because Flask is not installed (`ModuleNotFoundError: flask`). Install project requirements and rerun the full suite.

Not tested:

- No live company websites, Apify Actors, or database were called during implementation.
- No database migration, production data change, or Render deployment was performed.
- Some company career pages may render jobs with JavaScript or on a separate ATS. Those sources require live validation and, when applicable, a verified hostname in `CAREER_ALLOWED_HOSTS_<KEY>` or a separately reviewed Actor configuration.

## Run locally

From the project root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env with your existing local DB settings; do not use production credentials for the first test.
python scripts/run_company_career_structured.py --source TCS --max-results 10 --max-pages 20
python -m pytest -q
```

Run all companies only after testing one source and confirming that `.env` points to a local/test database:

```powershell
python scripts/run_company_careers_structured_batch.py --max-results 50 --max-pages 60
```

The live collection commands make HTTP requests and write collection history/job records to the configured database. They were not run while creating this package.

## Current staged scope (2026-10-21)

Only Infosys, Amazon and Wipro remain in the company-career catalog while they are integrated individually. Infosys has a dedicated public-search API adapter and its field mapping has been updated for the observed API schema. Amazon and Wipro still require separate live collection verification; catalog presence alone must not be described as a successful collector. The Companies sidebar group is intentionally hidden until each company is validated.


## 2026-10-21 — Company collector and sidebar update

- Company sidebar now lists every configured official company source, even when its collector has not yet passed live verification. The count remains the number of stored job records for that source.
- Amazon collector now tries the official `amazon.jobs/en/search.json` search response and normalizes returned job records, with HTML job-card fallback.
- Wipro collector keeps the official RSS feed, rejects placeholder entries, and falls back to the official SuccessFactors `/search/?q=&locationsearch=India` results page when the feed contains no individual vacancies.
- Accenture listing parsing accepts the existing `jobdetails?id=` form and common official careers job-detail path variants.
- Infosys collection logic is unchanged.
- Live collection still needs to be run from the user's configured environment to confirm the current public responses; this environment has no DNS/network access to those career sites. Mocked tests validate parsing and normalization but are not evidence of live vacancies being inserted.


---

## Reference: `COMPANY_CATALOG_UPDATE_2026-10-21.md`


# Company catalog update — 2026-10-21

## Visible company sources
- Infosys
- Amazon
- Deloitte
- HCLTech
- Cognizant
- Capgemini
- Tech Mahindra
- IBM

Wipro and Accenture have been removed from the active company career catalog. The UI endpoint builds its visible list from this catalog, so stale company career source rows are filtered out of the company sidebar without deleting job history or database records.

The five newly added companies use their official careers entry pages and the existing conservative JSON-LD `JobPosting` collector. Their presence in the UI is not a guarantee that the public page exposes structured vacancies; each source must be run and checked before describing its collector as verified. Infosys, Amazon and Deloitte existing collector paths remain unchanged.

## Commands
```powershell
python scripts/run_company_career_structured.py --source HCLTECH --max-results 100
python scripts/run_company_career_structured.py --source COGNIZANT --max-results 100
python scripts/run_company_career_structured.py --source CAPGEMINI --max-results 100
python scripts/run_company_career_structured.py --source TECHMAHINDRA --max-results 100
python scripts/run_company_career_structured.py --source IBM --max-results 100
```


---

## Reference: `COMPANY_COLLECTOR_FIX_NOTES.md`


# V4Career company collector update — 2026-10-21

## Changes in this update
- Added a dedicated Deloitte India SuccessFactors listing collector. It recognizes official vacancy links of the form `/job/<slug>/<numeric-id>/`, paginates using `startrow`, normalizes the jobs and passes them into the existing save pipeline.
- Kept the working Infosys public search API collector unchanged.
- Kept the Amazon official JSON search collector unchanged.
- Preserved all five configured companies in `collectors/company_career_catalog.py`, which is the source used by the UI/company API.
- Added a mocked test for Deloitte SuccessFactors job-card extraction.

## Verification
Targeted tests:
`python -m pytest -q tests/test_company_career_structured.py tests/test_company_career_catalog.py tests/test_source_catalog.py tests/test_normalization.py tests/test_db_compatibility.py`

Result: 33 passed, 12 subtests passed.

The full test suite could not be collected in this packaging environment because Flask is not installed (`ModuleNotFoundError: flask`).

## Important live status
- Infosys: user's latest run found 100 and updated 100.
- Amazon: user's latest run found 100 and inserted 100.
- Wipro: still requires a live test of its client-side SAP SuccessFactors widget/API. The current feed and server-rendered HTML did not expose vacancy cards.
- Accenture: still requires a live test of the current client-side search data endpoint. The fetched HTML pages did not expose vacancy cards.
- Deloitte: collector is implemented and has mocked parser coverage, but has not been live-run from this packaging environment.

Do not describe Wipro, Accenture, or Deloitte as live-verified until the user's collector output confirms `jobs_found > 0` and records are inserted or updated. No production database or Render deployment was changed.


---

## Reference: `COMPANY_SCRAPER_SETUP.md`


# Company career scraper setup

V4Career keeps company-career sources separate from general job portals. The Companies sidebar filters jobs already stored in the existing database; it does not launch a scrape when a checkbox is selected. Collection is run explicitly from the command line or through a reviewed scheduler.

## Current collection method

`scripts/run_company_career_structured.py` looks for schema.org `JobPosting` JSON-LD on the official career URLs in `collectors/company_career_catalog.py`. It only saves records that pass the collector's vacancy validation and India-location checks. Company catalog membership is not proof that live collection works for that company. Some career sites do not expose this data to ordinary HTTP clients, and some block requests.

TCS currently returns HTTP 403 on all four configured pages in the last reported local run. That is an access failure, not proof that there are no TCS vacancies. Do not work around access controls or save program descriptions as jobs. If a company needs an alternative, review an official public feed/ATS endpoint or a permitted Apify Actor's current input schema, output fields, terms and pricing first. Only then configure the per-company `APIFY_ACTOR_COMPANY_<KEY>`, `APIFY_INPUT_COMPANY_<KEY>` and `APIFY_ACCESS_APPROVED_COMPANY_<KEY>` values in the local `.env`. Never invent Actor IDs or commit tokens.

## Test one company

Use a separate local/test database in `.env` before running a collector, because collection writes to the configured database. Example for TCS:

```powershell
python scripts/run_company_career_structured.py --source TCS --max-results 10 --max-pages 20 --timeout 15
```

Run other companies individually by replacing `TCS` with `INFOSYS`, `WIPRO`, `HCLTECH`, `ACCENTURE`, `COGNIZANT`, `CAPGEMINI`, `AMAZON`, `MICROSOFT`, `GOOGLE`, `IBM`, `ADOBE`, `ZOHO`, `FRESHWORKS` or `PAYTM`. Do not run the batch until you have reviewed per-source access and test results.

## Batch run

After source-by-source verification, run:

```powershell
python scripts/run_company_careers_structured_batch.py --max-results 50 --max-pages 60
```

Review each source's pages attempted/fetched/blocked, valid jobs found, inserted/updated counts and run status. A zero-job result after successful page fetches means no qualifying structured vacancies were found; a result where no pages could be fetched is a failed run.

## UI behavior

The Companies sidebar displays company names and counts only. Its checkbox filters matching stored company-career source records in Job Listings. It does not scrape on checkbox click. The sidebar intentionally omits `Setup`, `Actor`, `Unverified`, and `JSON-LD` badges to keep the navigation clean; collector configuration and status remain a technical setup/testing concern, not a UI status claim.


## Infosys API collector and faster batch runs

Infosys uses its public career search API through the dedicated branch in `collectors/company_career_structured.py`; it does not require an Apify Actor. The following Python snippet is a read-only preview and does not connect to the database:

```powershell
@'
from collectors.company_career_catalog import COMPANY_CAREER_BY_KEY
from collectors.company_career_structured import collect_structured_jobs
jobs, stats = collect_structured_jobs(COMPANY_CAREER_BY_KEY["INFOSYS"], max_results=100, timeout=30)
print(stats)
print("Jobs returned:", len(jobs))
'@ | python -
```

To persist results, use the standard runner below only after reviewing the configured database settings in `.env`:

```powershell
python .\scripts\run_company_career_structured.py --source INFOSYS --max-results 100 --timeout 30
```

For a faster full catalog run, the structured batch runner supports a conservative worker pool (default 3, allowed 1-6):

```powershell
python .\scripts\run_company_careers_structured_batch.py --max-results 50 --workers 3
```

Use `--workers 1` for serial collection or when diagnosing source throttling. A source-specific preview is available via `--source INFOSYS`, but the runner writes results to the database; use `collect_structured_jobs` directly for a read-only preview.


## Official company collector smoke tests (India jobs)

Infosys is the only source currently marked verified in the UI. The following are
official-site candidates and must be tested before marking verified:

```powershell
python scripts/run_company_career_structured.py --source WIPRO --max-results 100 --max-pages 10 --timeout 30
python scripts/run_company_career_structured.py --source ACCENTURE --max-results 100 --max-pages 10 --timeout 30
python scripts/run_company_career_structured.py --source DELOITTE --max-results 100 --max-pages 10 --timeout 30
```

A successful process exit is not enough by itself: confirm `jobs_found` is greater
than zero, records are inserted/updated without database errors, and the saved
jobs appear in the UI. These candidates intentionally remain unverified until
those checks pass.


## Official company collectors (Infosys, Wipro, Amazon, Accenture)

Run one company at a time from the project root:

```powershell
python scripts/run_company_career_structured.py --source INFOSYS --max-results 100
python scripts/run_company_career_structured.py --source WIPRO --max-results 100
python scripts/run_company_career_structured.py --source AMAZON --max-results 100
python scripts/run_company_career_structured.py --source ACCENTURE --max-results 100
```

These commands use the existing V4Career database configuration and collector/repository pipeline. They do not require an invented Apify Actor ID. Infosys uses its public careers search API; Wipro reads its official India RSS feed; Amazon reads the official Amazon Jobs India search page; Accenture starts from its official India job-search route. The Wipro feed may currently contain no real vacancy items, and the Amazon/Accenture parsers must be considered unverified until the run output reports actual individual jobs. A zero-job run is reported as zero; it is not replaced with made-up or program-description records.


---

## Reference: `UI_AND_COMPANY_SOURCE_UPGRADE.md`


# V4Career — Company source filtering and report UI upgrade

## Included changes

- Company career sources remain distinct from general job portals in the sidebar and database.
- Selecting one or more company checkboxes now filters the same Job Listings dataset by the selected company-career source IDs. Selecting a company does not itself start a scrape; collectors remain independently run/configured.
- Portal and company source IDs are combined for the existing job list, dependent facets and normal job export. Clearing filters preserves selected sources.
- Company entries display `Unverified` until an approved Actor configuration exists. The built-in structured collector's presence is not treated as proof that a company site is currently collectible.
- The Reports page is simplified to four database-backed summary metrics, essential keyword/source/company/location/date filters, sorting, pagination, Excel/CSV export, and recent collection activity with error details.
- The report API now supports safe sort choices, company-career source-type filtering, and the report export supports an inclusive posted-to date and CSV format.
- Sidebar select-all checkboxes are positioned before the Job Portal and Companies labels.
- Styling uses a restrained navy/slate/teal gradient, compact report cards, clearer hierarchy and responsive report filters.

## Important collection limitation

The company catalog and structured collector are present for TCS, Infosys, Wipro, HCLTech, Accenture, Cognizant, Capgemini, Amazon, Microsoft, Google, IBM, Adobe, Zoho, Freshworks and Paytm. This does **not** mean all 15 sites are live operational sources. Each source still requires successful access to genuine individual job listings. TCS returned HTTP 403 on all four configured official pages in the last reported run, so TCS collection remains blocked until a verified official feed/ATS endpoint or reviewed permitted collector is available.

## Verification performed for this upgrade

- `python -m py_compile app.py tests/test_api.py collectors/company_career_structured.py scripts/run_company_career_structured.py` — passed.
- `node --check static/app.js` — passed.
- HTML parse and duplicate-ID check — passed; no duplicate IDs found.
- Full pytest suite could not be run in this build environment because Flask is not installed and package installation is unavailable. Run `python -m pytest -q` in the project's Windows virtual environment before deployment.

No database migration, production database write, credential change, or deployment was performed as part of this UI and source-filter change.

## Superseding UI refinement (2026-10-21)

- The sidebar now shows only Foundit, Glassdoor, Indeed, Internshala, Naukri, TimesJobs and Wellfound. Legacy source rows and their jobs are not deleted; non-listed sources are simply hidden from the portal UI/API response.
- Removed the Companies checkbox group from the sidebar so company career sources can be added one at a time after their collector is validated. The catalog is staged to Infosys, Amazon and Wipro only.
- Replaced the Job Portal dropdown chevron with a plus/minus glyph, aligned the icon/master-checkbox/label columns, increased sidebar spacing, softened the orange accent, and increased UI font sizes.
- Corrected Infosys API normalization to read the live API's `postingTitle`, `createdOn`, description fields and min/max experience fields. This mapping is covered by the collector tests; a database write and UI persistence run still need to be verified in the user's local environment.
- Verification in this build container: 32 tests passed and 13 subtests passed across catalog, structured collector, normalization and database-compatibility modules. Full API tests could not be collected because Flask is not installed in this environment.
