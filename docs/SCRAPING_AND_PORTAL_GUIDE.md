# Scraping And Portal Guide



---

## Reference: `APIFY_COLLECTOR_SETUP.md`


# V4Career Apify collector accounts

Configure three separate Apify account tokens in `.env` (never commit real tokens):

```dotenv
APIFY_API_TOKEN_COLLECTOR1=your_account_1_token
APIFY_API_TOKEN_COLLECTOR2=your_account_2_token
APIFY_API_TOKEN_COLLECTOR3=your_account_3_token
```

## Account groups and estimated costs (USD / 1,000 jobs)

| Account | Portal | Price |
|---|---|---:|
| 1 | CutShort | $0.80 |
| 1 | Wellfound | $0.50 |
| 1 | Unstop | $1.50 |
| 1 | Foundit | $1.80 |
| 2 | TimesJobs | $1.99 |
| 2 | NCS | $2.99 |
| 2 | Hirist | $1.99 |
| 3 | Instahyre | $1.35 |
| 3 | Freshersworld | $0.79 |
| 3 | Internshala | Not supplied |
| 3 | Shine | Not supplied |

## Run collector groups

```powershell
python scripts/run_source_apify.py --source COLLECTOR1 --keyword "Software Engineer" --location India --max-results 1000
python scripts/run_source_apify.py --source COLLECTOR2 --keyword "Software Engineer" --location India --max-results 1000
python scripts/run_source_apify.py --source COLLECTOR3 --keyword "Software Engineer" --location India --max-results 1000
```

Each portal must also have its `APIFY_ACCESS_APPROVED_<PORTAL_KEY>=true` setting after actor authorization/input is verified. Actor prices shown are user-provided estimates, not guaranteed current Apify charges.


---

## Reference: `COLLECTOR_GROUPS_REPAIR_README.md`


# V4Career Collector 1/2/3 repair package

This package is a targeted repair of the uploaded V4Career project. It does not guarantee that every third-party portal will return 1,000 jobs: each Actor controls its own schema, pagination, access, and plan limits.

## Important safety
- The uploaded `.env` was deliberately **not copied** into this ZIP because it contains credentials. Copy your own `.env` from your local backup into this project folder; never share it or commit it.
- If the uploaded `.env` contained live credentials, rotate the Neon/database password, Apify token, Flask secret, and any other exposed credentials.
- Keep `APIFY_ACCESS_APPROVED_<PORTAL_KEY>=false` until you have checked that Actor's current input schema, terms, and pricing in Apify.
- `--max-results 1000` is a target, not a guarantee and not a promise that the Actor plan allows 1,000 records.
- Collector groups run every portal in that group, so a single command may start several Actor runs and may incur usage charges. Start with `--max-results 10`.

## Run from PowerShell
Open PowerShell in the project root (the folder containing `app.py`).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create `.env` from `.env.example` and fill in your own database connection and Apify settings locally.

Start with one small test per group:

```powershell
python scripts/run_source_apify.py --source COLLECTOR1 --keyword "Software Engineer" --location India --max-results 10
python scripts/run_source_apify.py --source COLLECTOR2 --keyword "Software Engineer" --location India --max-results 10
python scripts/run_source_apify.py --source COLLECTOR3 --keyword "Software Engineer" --location India --max-results 10
```

Only after checking run cost, status, and saved jobs, increase the target:

```powershell
python scripts/run_source_apify.py --source COLLECTOR1 --keyword "Software Engineer" --location India --max-results 1000
python scripts/run_source_apify.py --source COLLECTOR2 --keyword "Software Engineer" --location India --max-results 1000
python scripts/run_source_apify.py --source COLLECTOR3 --keyword "Software Engineer" --location India --max-results 1000
```

Each group continues through the remaining portals when a portal fails and reports successes and failures at the end. A nonzero exit code means at least one portal in the group failed; check the individual error in the JSON output and logs.

## Changes included
- Fixed NCS default input so `maxItems` never exceeds the observed Actor schema ceiling of 100; related request limits are bounded too.
- Added source-scoped external-ID lookup before INSERT to prevent a duplicate external ID from aborting the whole batch. Existing rows are updated; no job rows are deleted.
- Updated existing-job persistence to refresh the URL hash when a stable external ID has a changed URL.
- Collector group token lookup accepts both `APIFY_API_TOKEN_COLLECTOR1` and `APIFY_API_TOKEN_COLLECTOR_1` naming conventions (same for groups 2 and 3).
- Kept per-portal approval flags and Actor schema configuration. These cannot be bypassed by the patch.

## Known limits that code cannot eliminate
- Some portals block scraping (403/404/TLS challenges), change Actor input schemas, require API keys/approval, or restrict output on free plans.
- Jooble previously returned only 10 items on the free Actor plan. A request target of 1,000 cannot override that.
- A portal's returned count can be greater than the number inserted because many returned jobs may already exist and be updated.
- Review each Actor's pricing and input schema before running large batches.


---

## Reference: `CONFIGURE_ALL_50_SOURCES.md`


## Additional portal Actor candidates (configuration update v2)

The catalog now includes public Apify Store candidates for LinkedIn Jobs, Freshersworld, National Career Service (NCS), Jooble, QuikrJobs, and Jora. Their IDs and starter input objects are candidates only; they have not been live-tested in this project. Before each run, inspect the Actor's current Input and Pricing tabs. Keep `APIFY_ACCESS_APPROVED_<KEY>=false` until you confirm terms, pricing, and authorization. LinkedIn Jobs has no built-in default input schema in this runner; set `APIFY_INPUT_LINKEDIN_JOBS` to the exact JSON required by that Actor.

Actor candidates are community-maintained and can change, charge for results, or return output fields that need normalization. The batch runner should not be treated as proof that a portal is operational.


## Batch input-template fixes (Oct 2026)
- Added a built-in input template for the catalogued LinkedIn Actor.
- Changed Jora input to `keywords` (string), `location`, `country: in`, and `maxJobs`.
- Removed Hirist hard-coded city list; use the requested city or all locations for India-wide search.
- Normalize the `, India` suffix to city names for Actors that expect a city-only input.
- Lowered detail/page settings on selected test templates to limit requests.
- Approval flags remain false by default; this patch does not approve or run any Actor.
- Actor schemas can change; run small tests and check each Actor Input tab before live collection.


---

## Reference: `FREE_COLLECTOR_SETUP.md`


# V4Career free job collector (initial implementation)

This package adds `scripts/run_free_sources.py` and does not remove or modify the existing Apify or company-career collectors.

## Included source adapters

- Greenhouse public Job Board API: only employers added to `config/free_sources.json`.
- Lever public Postings API: only employers added to `config/free_sources.json`.
- Remote OK public feed.
- Jobicy public API.

The India filter is intentionally strict: a record is only accepted if its location fields mention India or a recognized Indian city. That means many worldwide-remote jobs are excluded unless the source declares India eligibility. A public feed can return zero India matches and that's a valid result.

**This is not a guaranteed 5,000 jobs/day source.** Public feed sizes and India-eligible results are outside V4Career's control. Do not count duplicate, malformed, non-India or stale listings toward the target.

## Safety

- Default is **dry-run**: no database writes.
- `--save` enables writes through the project's existing `collectors.repository.save_jobs`.
- No schema migration is needed for this first version.
- Configure `DATABASE_URL` using the project's existing `.env` locally or GitHub Actions secret. Never commit `.env`.
- Respect each source's current terms, attribution requirements, rate limits, and allowed use. Do not use this to bypass portal access controls.
- Do not run with `--save` against production until the dry-run output is reviewed.

## Install and run (Windows PowerShell)

From the project root:

```powershell
python -m pip install -r requirements.txt
python scripts/run_free_sources.py --source all --limit-per-source 50
```

That command is a dry-run and reports validated India-location jobs. To save after reviewing:

```powershell
python scripts/run_free_sources.py --source all --limit-per-source 50 --save
```

Test sources independently:

```powershell
python scripts/run_free_sources.py --source greenhouse --limit-per-source 20
python scripts/run_free_sources.py --source lever --limit-per-source 20
python scripts/run_free_sources.py --source remoteok --limit-per-source 20
python scripts/run_free_sources.py --source jobicy --limit-per-source 20
```

## Configure Greenhouse / Lever company boards

Edit `config/free_sources.json` with verified board identifiers, for example:

```json
{
  "greenhouse": [
    {"slug": "verified-board-token", "company": "Example Company"}
  ],
  "lever": [
    {"slug": "verified-lever-site", "company": "Example Company"}
  ]
}
```

These values are examples, not real company board IDs. Do not guess slugs: test the company board and confirm that it returns jobs for the company. Public APIs are per-employer; they are not global job-search APIs.

## What's next

1. Run a dry-run from your Windows environment and inspect each source's counts.
2. Configure verified Greenhouse / Lever boards and test again.
3. Check the database's actual `DATABASE_URL` connection and schema before using `--save`.
4. Add a GitHub Actions schedule only after the workflow is validated. Keep Apify enabled for sources that do not have a suitable, authorized free alternative.
5. Track `fetched`, `valid`, `unique`, `inserted`, and `updated` separately. `inserted` is the count closest to genuinely new jobs.

Naukri, Indeed, Foundit, Internshala, TimesJobs, Wellfound and other India-focused portals are not replaced by these APIs. Continue only through an authorized free feed or access method for each portal.


---

## Reference: `JOBICY_RATE_LIMIT_FIX_README.md`


# Jobicy rate-limit fix

Replace your current:
`scripts/run_six_portals.py`

with the file in this patch.

What changed:
- Jobicy now makes at most two broad feed requests per run (APAC and Anywhere), instead of six geo/industry combinations.
- Filters IT roles locally after fetching.
- Retries HTTP 429 and temporary 5xx responses with bounded exponential backoff.
- Respects `Retry-After` when provided.
- If one geographic feed remains unavailable, it logs a warning and continues with the other feed.
- The other five source collectors are unchanged.

Run only Jobicy first:
```powershell
python scripts/run_six_portals.py --source jobicy --max-results 100
```

If Jobicy still returns 429 after the bounded retries, stop retrying for now. It means the provider is still throttling the public endpoint; wait before trying again. Do not loop the command repeatedly.

This patch does not change database schema, `.env`, or the existing 15 portals.


---

## Reference: `JOBSPY_TESTING.md`


# V4Career JobSpy isolated test

This is a separate test path. The existing Apify runner, free-source collector, database schema, Flask UI, and production `requirements.txt` are unchanged.

## What JobSpy can test

The JobSpy project currently documents support for Indeed and Naukri, along with LinkedIn, Glassdoor, ZipRecruiter, Bayt, BDJobs, and HelloWork. It does **not** document native adapters for TimesJobs, Foundit, Internshala, or Wellfound. Do not assume unsupported portals are covered.

JobSpy is third-party open-source software, but source sites can block requests or change their pages. Follow each portal's current terms and do not use the test to bypass access controls. A zero-result response may mean no matches, a parser issue, or a block; inspect errors/warnings.

## 1. Keep this package separate

Extract this ZIP into a new folder, for example `V4Career_JobSpy_Test_Package`. Do not overwrite the currently working project. The backup ZIP supplied alongside this package is a separate copy of the current free-collector project.

## 2. Install test-only dependency

From the test folder in PowerShell:

```powershell
python -m pip install -r requirements-jobspy-test.txt
```

The main `requirements.txt` was intentionally not changed, so the production deployment does not gain a new dependency.

## 3. Run a small South India test

Start with Bengaluru and a small result cap:

```powershell
python scripts/test_jobspy_sources.py --sites indeed,naukri --keyword "Software Engineer" --location Bengaluru --results 10
```

Then test another role/location after reviewing the first output:

```powershell
python scripts/test_jobspy_sources.py --sites indeed,naukri --keyword "Python Developer" --location Chennai --results 10
python scripts/test_jobspy_sources.py --sites indeed,naukri --keyword "Java Developer" --location Hyderabad --results 10
python scripts/test_jobspy_sources.py --sites indeed,naukri --keyword "Graduate Engineer Trainee" --location Coimbatore --results 10
```

## 4. Where test results go

The script writes only local CSV/JSON files to `jobspy_test_results/`. It does not import V4Career's database repository, read `.env`, or write to Neon/MySQL. Inspect `jobspy_test_report.json` for per-site rows, warnings, and errors.

## 5. What to report back

Share the terminal summary and counts for each site. Do not upload credentials. If the results are usable, the next step is a separate adapter that maps JobSpy fields to V4Career's existing normalized schema, still with dry-run as the default. Database integration should happen only after validating the data and source permissions.


---

## Reference: `SIX_PORTALS_SCRAPING_GUIDE.md`


# V4Career – Fresh scraping for the six additional sources

This guide covers the five remaining remote-job sources while leaving the existing
portal collectors, dashboard, filters, and database repository intact.

## Collection approach

| Source | Method | Credential |
|---|---|---|
| Jobicy | Public JSON API | None |
| Arbeitnow | Public JSON API | None |
| We Work Remotely | Official programming/devops RSS feeds | None |
| Remote OK | Public JSON feed | None |
| Working Nomads | Public exposed jobs feed | None |

The public API/RSS sources do not require additional API keys. Working Nomads'
exposed endpoint is not a documented developer API, so it is treated as best-effort.

## Geographic and job filters

The collector is configured for IT-related roles. It accepts:
- Jobs explicitly located in India or APAC.
- Remote jobs explicitly marked Anywhere, Worldwide, Global, or with no stated
  geographic restriction on the remote-only feeds.
- It rejects postings explicitly restricted to regions such as US-only, Canada-only,
  UK-only, or Europe-only.

This keeps the existing India-focused collection policy while allowing remote roles
that are genuinely open to applicants from anywhere. Some providers do not expose
work-eligibility regions consistently; review source records and adjust the filter
if a provider changes its feed structure.

Jobicy's public API feed covers recent listings (last seven days) and uses cursors.
The script makes a limited number of calls per run. Avoid scheduling it more than
once per hour. Arbeitnow has no server-side keyword/location filters, so filtering
is performed locally after fetching a small number of pages.

## Files changed or added

### Replace
- `collectors/source_catalog.py`
- `scripts/run_source_apify.py`

### Add
- `scripts/run_six_portals.py`
- `tests/test_six_portal_collector.py`
- `ENV_SIX_PORTALS_APPEND.txt`
- `SIX_PORTALS_SCRAPING_GUIDE.md`

No existing `.env` file is included in this patch because it contains database and
API credentials. Append the three non-secret settings from `ENV_SIX_PORTALS_APPEND.txt`
to your existing `.env`. The existing Collector 1 Apify token is reused; do not paste
or share the token in chat.

## Commands (PowerShell)

From your V4Career project root:

1. Install/confirm dependencies:
```powershell
pip install requests beautifulsoup4 python-dotenv apify-client
```

2. First run the five public API/RSS sources (no Apify charge):
```powershell
python scripts/run_six_portals.py --source all --max-results 100
```

3. Run one source individually:
```powershell
python scripts/run_six_portals.py --source jobicy --max-results 100
python scripts/run_six_portals.py --source arbeitnow --max-results 100
python scripts/run_six_portals.py --source we-work-remotely --max-results 100
python scripts/run_six_portals.py --source remote-ok --max-results 100
python scripts/run_six_portals.py --source working-nomads --max-results 100
```

5. Verify saved counts in PostgreSQL:
```sql
SELECT s.name, COUNT(j.id) AS jobs_count
FROM sources s
LEFT JOIN jobs j ON j.source_id = s.id
WHERE s.name IN (
  'Jobicy', 'Arbeitnow',
  'We Work Remotely', 'Remote OK', 'Working Nomads'
)
GROUP BY s.name
ORDER BY s.name;
```

## Safety / expected behavior

- Jobs are normalized with the existing `collectors.normalization.normalize_job`.
- Jobs are saved with the existing `collectors.repository.save_jobs`.
- URL hashes are used for per-source duplicate detection/updating.
- Each source writes collection run status through the existing repository.
- No rows are deleted and no schema migration is required for this script.
- The script does not change the existing 15 portal scraping commands.
- The first live API/Actor execution must be tested in your local environment;
  this environment cannot access your Neon DB or your Apify account.


---

## Reference: `SOURCE_CONFIGURATION_STATUS.md`


# V4Career source catalog and configuration status

Updated from `V4Career_Scope_Job_Portals_and_Companies.xlsx`.

## Job portals

All 25 portals in the scope sheet are listed in the catalog and exposed by the Job Portal API/UI. Naukri, Indeed, Foundit, and TimesJobs are ordered first. The sidebar includes separate controls for selecting the four popular portals or all portals.

Existing candidate Actor IDs and input defaults are available for:
- Naukri
- Indeed
- Foundit
- Internshala
- TimesJobs
- Wellfound

These are community Actor candidates, not guarantees of current availability or permission. Collection remains disabled unless the matching `APIFY_ACCESS_APPROVED_<KEY>` is explicitly set to `true`. For new portals, configure a reviewed Actor ID and `APIFY_INPUT_<KEY>` using the Actor's actual Input schema. Unknown portals no longer receive a guessed generic input. The Glassdoor Actor shown in the user's screenshot (`bebity~glassdoor-jobs-scraper`) is documented as a candidate only; its input schema still needs review before use.

## Company career sites

All 25 companies from the scope sheet are present in the company catalog and visible in the Companies sidebar. The official-site collector is available for all entries, but it only stores validated `schema.org/JobPosting` records whose location indicates India and whose URLs remain on configured official hosts.

Specialized collector paths already exist for Infosys, Wipro, Amazon, Accenture, and Deloitte. The catalog's verification flag is not set for the remaining companies until live results have been validated. Some sites may return zero jobs because listings are JavaScript-rendered, use an external ATS, block requests, or do not publish JobPosting JSON-LD. A zero-result run is not a fabricated vacancy count.

## Safety and secrets

- All Actor approval flags in `.env.example` default to `false`.
- Do not enable collection until you review the Actor's current input schema, price, terms, and authorization.
- `.env` and `.git` are intentionally excluded from this ZIP so credentials and repository history are not redistributed.
- Copy the needed variable names into your local `.env`; do not replace existing DB settings.


---

## Reference: `UI_AND_SCRAPE_UPDATE_FRESHERSWORLD_QUIKRJOBS.md`


# V4Career UI and collection update — Freshersworld + QuikrJobs

## Changes
- Added Freshersworld and QuikrJobs to `UI_PORTAL_KEYS`; both appear in the Job Portal UI when their Actor token/approval configuration is present.
- Kept National Career Service (NCS) hidden from the UI for now.
- Freshersworld defaults request up to the actor's supported five pages.
- QuikrJobs maps India/Bengaluru location inputs to the tested supported city name `Bangalore`, and requests up to five pages based on `--max-results`.
- No database job rows are deleted by these changes.

## Run collection
Run from the V4Career project root after confirming `.env` has `APIFY_API_TOKEN` and `APIFY_ACCESS_APPROVED_FRESHERSWORLD=true` and `APIFY_ACCESS_APPROVED_QUIKRJOBS=true`. Use 100 as a practical batch size; the actor may return fewer jobs due to its own supported pages, availability, or result limits.

```powershell
python scripts/run_source_apify.py --source Freshersworld --keyword "Software Engineer" --location India --max-results 100; python scripts/run_source_apify.py --source QuikrJobs --keyword "Software Engineer" --location "Bangalore, India" --max-results 100
```

`--max-results 100` is a requested cap, not a guarantee that 100 jobs will be found or saved. Check each run's `found`, `inserted`, and `updated` counts.
