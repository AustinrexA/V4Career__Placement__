"""Canonical catalog for the V4Career job portal integrations.

Catalog membership controls whether a source is selectable in the portal UI.
It does not certify that a third-party Actor/API is currently available or
approved. Keep source names/keys unique and test each collector independently.
"""

JOB_PORTALS = [
    # Existing integrations: keep the four primary portals first.
    {"name": "Naukri", "base_url": "https://www.naukri.com/", "key": "NAUKRI", "popular": True, "method": "JobSpy"},
    {"name": "Indeed", "base_url": "https://in.indeed.com/", "key": "INDEED", "popular": True, "method": "JobSpy"},
    {"name": "Foundit", "base_url": "https://www.foundit.in/", "key": "FOUNDIT", "popular": True, "method": "Apify"},
    {"name": "TimesJobs", "base_url": "https://www.timesjobs.com/", "key": "TIMESJOBS", "popular": True, "method": "Apify"},
    {"name": "LinkedIn", "base_url": "https://www.linkedin.com/jobs/", "key": "LINKEDIN_JOBS", "popular": False, "method": "JobSpy"},
    {"name": "Glassdoor", "base_url": "https://www.glassdoor.co.in/Job/", "key": "GLASSDOOR", "popular": False, "method": "JobSpy"},
    {"name": "Internshala", "base_url": "https://internshala.com/", "key": "INTERNSHALA", "popular": False, "method": "Apify"},
    {"name": "Wellfound", "base_url": "https://wellfound.com/", "key": "WELLFOUND", "popular": False, "method": "Apify"},
    {"name": "CutShort", "base_url": "https://cutshort.io/", "key": "CUTSHORT", "popular": False, "method": "Apify"},
    {"name": "Hirist", "base_url": "https://www.hirist.tech/", "key": "HIRIST", "popular": False, "method": "Apify"},
    {"name": "Instahyre", "base_url": "https://www.instahyre.com/", "key": "INSTAHYRE", "popular": False, "method": "Apify"},
    {"name": "Freshersworld", "base_url": "https://www.freshersworld.com/", "key": "FRESHERSWORLD", "popular": False, "method": "Apify"},
    {"name": "Unstop", "base_url": "https://unstop.com/", "key": "UNSTOP", "popular": False, "method": "Apify"},
    {"name": "Bayt", "base_url": "https://www.bayt.com/", "key": "BAYT", "popular": False, "method": "JobSpy"},
    {"name": "Shine", "base_url": "https://www.shine.com/", "key": "SHINE_COM", "popular": False, "method": "Apify"},
    # Added integrations from the second project.
    {"name": "Jobicy", "base_url": "https://jobicy.com/jobs", "key": "JOBICY", "popular": False, "method": "Apify"},
    {"name": "Arbeitnow", "base_url": "https://www.arbeitnow.com/", "key": "ARBEITNOW", "popular": False, "method": "Apify"},
    {"name": "We Work Remotely", "base_url": "https://weworkremotely.com/", "key": "WE_WORK_REMOTELY", "popular": False, "method": "Apify"},
    {"name": "Remote OK", "base_url": "https://remoteok.com/", "key": "REMOTE_OK", "popular": False, "method": "Apify"},
    {"name": "Working Nomads", "base_url": "https://www.workingnomads.com/jobs", "key": "WORKING_NOMADS", "popular": False, "method": "Apify"},
]

# Every catalogued portal is selectable in the portal UI.
UI_PORTAL_KEYS = {portal["key"] for portal in JOB_PORTALS}

# Apify Actor IDs are candidates only. Collection must still be explicitly
# approved via APIFY_ACCESS_APPROVED_<KEY>=true and validated against the
# Actor's current input schema and terms.
DEFAULT_APIFY_ACTORS = {
    "FOUNDIT": "deriverge~foundit-jobs-scraper",
    "INTERNSHALA": "automation-lab~internshala-scraper",
    "TIMESJOBS": "unfenced-group~timesjobs-scraper",
    "WELLFOUND": "cprussin~wellfound-jobs",
    "CUTSHORT": "silentflow~cutshort-jobs-scraper",
    "HIRIST": "s_actors~hirist-jobs-scraper",
    "INSTAHYRE": "themineworks~instahyre-jobs-scraper",
    "FRESHERSWORLD": "shahidirfan~freshersworld-jobs-scraper",
    "UNSTOP": "data_daemon~unstop-scraper",
    "SHINE_COM": "bovi~shine-scraper",
}

# These five remote boards are handled by scripts/run_six_portals.py through
# one command, using each board's public API/RSS feed (no shared scraping Actor).
REMOTE_MULTI_SOURCE_KEYS = {
    "JOBICY", "ARBEITNOW", "WE_WORK_REMOTELY", "REMOTE_OK", "WORKING_NOMADS"
}

PORTAL_BY_KEY = {portal["key"].lower(): portal for portal in JOB_PORTALS}
PORTAL_BY_NAME = {portal["name"].strip().lower(): portal for portal in JOB_PORTALS}

# Existing estimates/account assignments retained from the project.
APIFY_PORTAL_BILLING = {
    "CUTSHORT": {"usd_per_1000": 0.80, "account": "COLLECTOR1"},
    "WELLFOUND": {"usd_per_1000": 0.50, "account": "COLLECTOR1"},
    "UNSTOP": {"usd_per_1000": 1.50, "account": "COLLECTOR1"},
    "FOUNDIT": {"usd_per_1000": 1.80, "account": "COLLECTOR1"},
    "TIMESJOBS": {"usd_per_1000": 1.99, "account": "COLLECTOR2"},
    "HIRIST": {"usd_per_1000": 1.99, "account": "COLLECTOR2"},
    "INSTAHYRE": {"usd_per_1000": 1.35, "account": "COLLECTOR3"},
    "FRESHERSWORLD": {"usd_per_1000": 0.79, "account": "COLLECTOR3"},
    "INTERNSHALA": {"usd_per_1000": None, "account": "COLLECTOR3"},
    "SHINE_COM": {"usd_per_1000": None, "account": "COLLECTOR3"},
}
