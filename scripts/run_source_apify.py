"""Run a configured Apify Actor for one V4Career job portal.

Set APIFY_API_TOKEN, APIFY_ACTOR_<PORTAL_KEY>, and optionally
APIFY_INPUT_<PORTAL_KEY> (JSON matching that Actor's documented input schema).
The JSON input may contain {keyword}, {location}, and {max_results} placeholders.
Only use Actors and data collection methods that you are authorized to use.
"""
import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from apify_client import ApifyClient
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from collectors.normalization import normalize_job
from collectors.repository import save_jobs
from collectors.source_catalog import JOB_PORTALS, PORTAL_BY_KEY, PORTAL_BY_NAME, DEFAULT_APIFY_ACTORS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("v4career.apify")


def flatten_text(value):
    if isinstance(value, dict):
        for key in ("raw", "city", "name", "label", "region", "country", "displayName"):
            if value.get(key) not in (None, ""):
                return flatten_text(value[key])
        return ""
    if isinstance(value, list):
        parts = [flatten_text(part) for part in value]
        return ", ".join(part for part in parts if part)
    return str(value).strip() if value is not None else ""


def first(item, keys):
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def map_item(item):
    if not isinstance(item, dict):
        raise ValueError("Actor dataset item must be a JSON object")

    # Google Jobs Actors may emit search-summary rows alongside actual job rows.
    if item.get("type") == "search":
        return None

    is_google_record = (
        item.get("type") == "job"
        or any(key in item for key in ("jobId", "applyUrl", "applyOptions"))
    )
    if is_google_record:
        url = first(item, ("applyUrl", "jobUrl", "url"))
        if isinstance(url, dict):
            url = first(url, ("url", "href", "link"))
        location = flatten_text(first(item, ("location", "jobLocation", "formattedLocation")))
        search_location = flatten_text(item.get("searchLocation"))
        country = flatten_text(first(item, ("country", "countryName")))
        if not country and search_location.lower() not in {"worldwide", "anywhere", "all locations", ""}:
            country = search_location
        return {
            "external_id": first(item, ("jobId", "job_id", "id")),
            "title": first(item, ("title", "jobTitle", "job_title")),
            "company": flatten_text(first(item, ("company", "companyName", "employer"))),
            "location": location,
            "country": country or None,
            "experience": flatten_text(first(item, ("experience", "experienceRange", "experienceText", "seniority"))),
            "salary_min": first(item, ("salaryMin", "salaryYearlyMin", "minSalary")),
            "salary_max": first(item, ("salaryMax", "salaryYearlyMax", "maxSalary")),
            "salary_currency": first(item, ("salaryCurrency", "currency")),
            "salary_period": first(item, ("salaryPeriod", "payPeriod")),
            "salary": first(item, ("salary",)),
            "skills": first(item, ("skills", "qualifications", "technologies")),
            "description": first(item, ("description", "descriptionText", "jobDescription")),
            "job_url": str(url).strip() if url else None,
            "posted_at": first(item, ("postedAt", "posted_at", "datePosted")),
            "status": "active",
            "work_mode": "Remote" if item.get("isRemote") is True else flatten_text(first(item, ("workMode", "workplaceType", "workplace_type"))) or None,
            "employment_type": flatten_text(first(item, ("employmentType", "employment_type"))) or None,
            "company_logo": first(item, ("companyLogoUrl", "company_logo", "logoUrl")),
            "posted_via": flatten_text(first(item, ("via", "source", "jobBoard"))) or None,
            "apply_options": item.get("applyOptions") if isinstance(item.get("applyOptions"), list) else [],
            "raw_metadata": {k: v for k, v in item.items() if k != "description"},
        }

    url = first(item, (
        "job_url", "jobUrl", "job_link", "jobLink", "url", "link",
        "apply_url", "applyUrl", "platform_url", "jobDetailsUrl", "job_url_link", "public_url",
        "jobLinkUrl", "job_url_path", "jobPageUrl", "jobPostingUrl", "href", "canonicalUrl",
    ))
    if isinstance(url, dict):
        url = first(url, ("url", "href", "link"))
    return {
        "external_id": first(item, ("id", "job_id", "jobId", "jobID", "jobKey", "key", "external_id", "jobPostingId", "uuid")),
        "title": first(item, ("title", "job_title", "jobTitle", "position", "role", "primaryRole", "designation", "name", "job_name")),
        "company": flatten_text(first(item, ("company", "company_name", "companyName", "employer", "startupName", "company_name_text", "organization", "hiringCompany"))),
        "location": flatten_text(first(item, ("location", "job_location", "jobLocation", "locations", "locationNames", "jobLocations", "city", "job_city", "workLocation", "formattedLocation"))),
        "experience": flatten_text(first(item, ("experience", "experience_range", "experienceRange", "experienceText", "experienceMinYears", "job_level", "jobLevel", "experience_required", "yearsOfExperience"))),
        "salary_min": first(item, ("salary_min", "salaryMin", "minSalary", "minimumSalary", "salary_minimum")),
        "salary_max": first(item, ("salary_max", "salaryMax", "maxSalary", "maximumSalary", "salary_maximum")),
        "salary_currency": first(item, ("salary_currency", "salaryCurrency", "currency", "salaryCurrencyCode")),
        "salary_period": first(item, ("salary_period", "salaryPeriod", "salary_type", "payPeriod", "salaryFrequency")),
        "salary": first(item, ("salary", "salary_range", "salaryRange", "compensation", "pay")),
        "skills": first(item, ("skills", "key_skills", "keySkills", "technologies", "attributes", "skillTags")),
        "description": first(item, ("description", "descriptionText", "descriptionMarkdown", "descriptionHtml", "job_description", "jobDescription", "summary", "job_summary", "shortDescription", "aboutRole")),
        "job_url": str(url).strip() if url else None,
        "posted_at": first(item, ("posted_at", "postedAt", "posted_date", "postedDate", "datePublished", "postDate", "published_at", "created_at")),
        "status": first(item, ("status", "job_status")) or "active",
        "country": flatten_text(first(item, ("country", "countryName"))) or None,
        "work_mode": flatten_text(first(item, ("work_mode", "workMode", "workplaceType"))) or None,
        "employment_type": flatten_text(first(item, ("employment_type", "employmentType", "jobType"))) or None,
        "company_logo": first(item, ("companyLogoUrl", "company_logo", "logoUrl")),
        "posted_via": flatten_text(first(item, ("via", "source", "jobBoard"))) or None,
        "apply_options": item.get("applyOptions") if isinstance(item.get("applyOptions"), list) else [],
        "raw_metadata": {k: v for k, v in item.items() if k not in {"description", "descriptionHtml"}},
    }


def replace_placeholders(value, keyword, location, max_results):
    replacements = {
        "{keyword}": keyword,
        "{location}": location,
        "{max_results}": max_results,
    }
    if isinstance(value, str):
        if value in replacements:
            return replacements[value]
        for token, replacement in replacements.items():
            value = value.replace(token, str(replacement))
        return value
    if isinstance(value, list):
        return [replace_placeholders(v, keyword, location, max_results) for v in value]
    if isinstance(value, dict):
        return {k: replace_placeholders(v, keyword, location, max_results) for k, v in value.items()}
    return value


COLLECTOR_GROUPS = {
    # Account 1: CutShort, Wellfound, Unstop, Foundit
    "COLLECTOR1": ["CutShort", "Wellfound", "Unstop", "Foundit"],
    # Account 2: TimesJobs, Hirist
    "COLLECTOR2": ["TimesJobs", "Hirist"],
    # Account 3: Instahyre, Freshersworld, Internshala, Shine
    "COLLECTOR3": ["Instahyre", "Freshersworld", "Internshala", "Shine"],
}


def parse_posted_at(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            stamp = float(value)
            if stamp > 10_000_000_000:
                stamp /= 1000
            return datetime.fromtimestamp(stamp, tz=timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            return None
    text_value = str(value).strip()
    if not text_value or text_value.casefold() in {"unknown", "n/a", "none", "nan"}:
        return None
    try:
        dt = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(text_value)
        except (TypeError, ValueError, OverflowError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_india_job(mapped):
    location = " ".join(str(mapped.get(k) or "") for k in ("location", "country")).casefold()
    if "india" in location:
        return True
    indian_places = (
        "bengaluru", "bangalore", "chennai", "hyderabad", "coimbatore", "kochi",
        "ernakulam", "thiruvananthapuram", "trivandrum", "mysuru", "mysore",
        "madurai", "trichy", "tiruchirappalli", "mumbai", "pune", "delhi",
        "noida", "gurugram", "gurgaon", "kolkata", "ahmedabad", "jaipur",
        "visakhapatnam", "vizag", "indore", "bhubaneswar", "lucknow",
    )
    return any(place in location for place in indian_places)


def run_one_source(source_name, keyword, location, max_results, token_override=None, hours_old=48, dry_run=False):
    actor_location = location.strip()
    if actor_location.lower() != "india" and actor_location.lower().endswith(", india"):
        actor_location = actor_location[:-len(", india")].strip()

    key = re.sub(r"[^A-Z0-9]+", "_", source_name.strip().upper()).strip("_")
    portal = PORTAL_BY_KEY.get(key.lower()) or PORTAL_BY_NAME.get(source_name.strip().lower())
    if not portal:
        raise ValueError("Unknown source: " + source_name)

    env_key = portal["key"]
    actor_id = os.getenv(f"APIFY_ACTOR_{env_key}", "").strip()
    if not actor_id and env_key == "NAUKRI":
        actor_id = os.getenv("APIFY_NAUKRI_ACTOR_ID", "").strip()
    if not actor_id:
        actor_id = DEFAULT_APIFY_ACTORS.get(env_key, "")
    token = (token_override or os.getenv(f"APIFY_API_TOKEN_{env_key}", "") or (os.getenv("APIFY_API_TOKEN_COLLECTOR1", "") if env_key == "GOOGLE_JOBS" else "") or os.getenv("APIFY_API_TOKEN", "")).strip()
    if not token:
        raise RuntimeError("Missing API token. Set APIFY_API_TOKEN_COLLECTOR_1/2/3 or APIFY_API_TOKEN in .env.")
    if not actor_id:
        raise RuntimeError(f"No Actor configured for {portal['name'].strip()}. Set APIFY_ACTOR_{env_key} in .env.")
    if os.getenv(f"APIFY_ACCESS_APPROVED_{env_key}", "false").lower() != "true":
        raise RuntimeError(f"Collection for {portal['name'].strip()} is disabled. Set APIFY_ACCESS_APPROVED_{env_key}=true locally only after confirming authorization.")

    input_json = os.getenv(f"APIFY_INPUT_{env_key}", "").strip()
    if input_json:
        try:
            run_input = json.loads(input_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"APIFY_INPUT_{env_key} must be valid JSON: {exc}")
        if not isinstance(run_input, dict):
            raise RuntimeError(f"APIFY_INPUT_{env_key} must contain a JSON object.")
        run_input = replace_placeholders(run_input, keyword, location, max_results)
    else:
        verified_input_defaults = {
            "GOOGLE_JOBS": {"queries": [keyword], "country": "in", "datePosted": "week", "maxItems": min(max_results, 50)},
            "NAUKRI": {"keyword": keyword, "location": location, "maxJobs": max_results, "experienceMin": 0},
            "PLACEMENTINDIA": {"searchKeyword": keyword, "location": "All India", "maxJobs": min(max_results, 10), "experienceLevel": "All", "industry": "All", "jobType": "All"},
            "INDEED": {"query": keyword, "location": location, "maxItems": max_results},
            "FOUNDIT": {"keywords": [keyword], "locations": [location], "maxItems": max_results},
            "INTERNSHALA": {"searchQuery": keyword, "searchMode": "jobs", "fetchDetails": True, "maxResults": max_results},
            "TIMESJOBS": {"searchQuery": keyword, "location": location, "maxResults": max_results, "enrichDetails": True},
            "WELLFOUND": {"roles": [keyword.strip().lower().replace(" ", "-")], "locations": [actor_location.strip().lower().replace(", india", "").replace(" ", "-")], "maxJobs": max_results, "maxPages": max(3, min(100, (max_results + 9) // 10)), "includeDescription": True},
            "LINKEDIN_JOBS": {"urls": ["https://www.linkedin.com/jobs/search/?keywords=" + keyword.replace(" ", "%20") + "&location=" + actor_location.replace(" ", "%20")], "maxJobsPerSearch": max_results, "companyDetails": False, "jobDetails": True, "easyApplyOnly": False, "under10Applicants": False},
            "CUTSHORT": {"searchQueries": [keyword], "maxItems": max_results, "debugMode": False, "includeDetails": False, "remoteOnly": False},
            "HIRIST": {"keywords": [keyword], "locations": [] if actor_location.lower() == "india" else [actor_location], "maxItems": max_results, "maxJobs": max_results, "includeJobDetails": True, "excludeAtsJobs": False, "onlyNewJobs": False, "onlyRespondingRecruiters": False, "onlyWithSalary": False, "remoteOnly": False, "skipDuplicates": True, "titleMustMatchKeyword": False, "debugLog": False},
            "IIMJOBS": {"searchQueries": [keyword], "maxItems": max_results, "includeDetails": True, "debugMode": False},
            "WORKINDIA": {"query": keyword, "location": actor_location, "maxItems": min(max_results, 20)},
            "APNA": {"keyword": keyword, "location": actor_location, "maxItems": min(max_results, 20), "includeDescription": True, "proxyConfiguration": {"useApifyProxy": True}, "startUrls": [{"url": "https://apna.co/jobs/jobs-in-" + actor_location.lower().replace(", india", "").replace(" ", "_")}]},
            "INSTAHYRE": {"jobFunctionSlugs": [], "maxJobs": max_results},
            "SHINE_COM": {"keywords": [keyword], "locations": [actor_location], "maxResults": max_results},
            "GLASSDOOR": {"keyword": keyword, "location": location, "country": "in", "maxPages": max(1, min(100, (max_results + 9) // 10)), "includeEnrichedData": False},
            "FRESHERSWORLD": {"keyword": keyword, "location": actor_location, "results_wanted": max_results, "max_pages": max(1, min(100, (max_results + 19) // 20))},
            "JOOBLE": {"searchQueries": [keyword], "country": "in.jooble.org", "fetchFullDescriptions": False},
            "QUIKRJOBS": {"query": keyword, "includeRaw": False},
            "NATIONAL_CAREER_SERVICE_NCS": {"mode": "search", "query": keyword, "location": actor_location, "maxItems": min(max_results, 100), "maxCandidates": min(max_results * 2, 200), "maxPages": max(2, min(20, (max_results + 19) // 20)), "maxDetailRequests": min(max_results, 100), "maxRequests": min(max(max_results * 3, 12), 200), "deadlineSeconds": 600, "timeoutMs": 20000},
            "JORA": {"keywords": keyword, "location": actor_location},
        }
        if env_key not in verified_input_defaults:
            raise RuntimeError(f"No default input schema for {portal['name'].strip()}. Set APIFY_INPUT_{env_key} in .env.")
        run_input = verified_input_defaults[env_key]

    client = ApifyClient(token)
    logger.info("Starting Actor for source=%s actor=%s requested_max=%d", portal["name"].strip(), actor_id, max_results)
    run = client.actor(actor_id).call(run_input=run_input)
    if not run:
        raise RuntimeError("Apify returned no run result.")

    def run_field(run_object, *names):
        if isinstance(run_object, dict):
            for name in names:
                if run_object.get(name) is not None:
                    return run_object[name]
        for name in names:
            value = getattr(run_object, name, None)
            if value is not None:
                return value
        return None

    run_status = run_field(run, "status")
    if str(run_status).upper() != "SUCCEEDED":
        raise RuntimeError(f"Actor run did not succeed: {run_status}")
    dataset_id = run_field(run, "defaultDatasetId", "default_dataset_id")
    if not dataset_id:
        raise RuntimeError("Actor run succeeded but did not return a dataset ID.")

    normalized, skipped = [], 0
    for index, item in enumerate(client.dataset(dataset_id).iterate_items()):
        try:
            mapped = map_item(item)
            if mapped is None:
                continue
            posted = parse_posted_at(mapped.get("posted_at"))
            if posted is None or posted < datetime.now(timezone.utc) - timedelta(hours=hours_old):
                skipped += 1
                continue
            if posted > datetime.now(timezone.utc) + timedelta(minutes=5) or not is_india_job(mapped):
                skipped += 1
                continue
            mapped["posted_at"] = posted.isoformat()
            normalized.append(normalize_job(mapped))
        except (ValueError, TypeError, AttributeError) as exc:
            skipped += 1
            logger.warning("Skipping invalid %s item %s: %s", portal["name"].strip(), index, exc)
    if dry_run:
        result = {"found": len(normalized), "inserted": 0, "updated": 0, "dry_run": True}
    else:
        result = save_jobs(portal["name"].strip(), portal["base_url"], normalized)
    summary = {"source": portal["name"].strip(), "records_found": len(normalized), "records_skipped": skipped, **result}
    logger.info("Collection complete source=%s found=%d skipped=%d", portal["name"].strip(), len(normalized), skipped)
    print(json.dumps(summary, indent=2))
    return summary


def main():
    parser = argparse.ArgumentParser(description="Collect jobs from one portal or one configured portal collector group")
    parser.add_argument("--source", required=True, help="Portal name/key or COLLECTOR1, COLLECTOR2, COLLECTOR3")
    parser.add_argument("--keyword", default="IT jobs", help="Broad search phrase; actor input schemas may still limit coverage")
    parser.add_argument("--location", default="India")
    parser.add_argument("--max-results", type=int, default=1000, help="Target maximum per portal (default: 1000; actor limits may apply)")
    parser.add_argument("--hours-old", type=int, default=48, help="Strict maximum posting age; missing/unparseable dates are excluded")
    parser.add_argument("--dry-run", action="store_true", help="Collect and validate without database writes")
    args = parser.parse_args()
    loc = args.location.strip().lower()
    if loc != "india" and not loc.endswith(", india"):
        parser.error("V4Career is configured for India-only collection. Use --location India or an Indian city/state ending in ', India'.")
    if args.max_results < 1 or args.max_results > 1000:
        parser.error("--max-results must be between 1 and 1000")
    if args.hours_old < 1:
        parser.error("--hours-old must be at least 1")

    source_key = re.sub(r"[^A-Z0-9]+", "", args.source.strip().upper())
    collector_key = source_key if source_key in COLLECTOR_GROUPS else None
    # Also accept human-friendly collector spellings.
    if collector_key is None:
        match = re.fullmatch(r"COLLECTOR([123])", source_key)
        if match:
            collector_key = "COLLECTOR" + match.group(1)
    if collector_key:
        token_name = f"APIFY_API_TOKEN_{collector_key}"
        token_name_legacy = f"APIFY_API_TOKEN_COLLECTOR_{collector_key[-1]}"
        token_override = (os.getenv(token_name, "").strip() or os.getenv(token_name_legacy, "").strip() or None)
        if not token_override:
            logger.warning("%s / %s is not set; falling back to APIFY_API_TOKEN. Set either variable in .env to use a separate Apify account.", token_name, token_name_legacy)
        summaries, failures = [], []
        for portal_name in COLLECTOR_GROUPS[collector_key]:
            try:
                summaries.append(run_one_source(portal_name, args.keyword, args.location, args.max_results, token_override, args.hours_old, args.dry_run))
            except Exception as exc:
                failures.append({"source": portal_name, "error": str(exc)})
                logger.error("Collector %s source %s failed: %s", collector_key, portal_name, exc)
        print(json.dumps({"collector": collector_key, "target_per_portal": args.max_results, "succeeded": len(summaries), "failed": len(failures), "results": summaries, "failures": failures}, indent=2))
        if failures:
            raise SystemExit(1)
    else:
        try:
            run_one_source(args.source, args.keyword, args.location, args.max_results, hours_old=args.hours_old, dry_run=args.dry_run)
        except Exception as exc:
            raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
