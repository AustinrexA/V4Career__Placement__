
"""
V4Career - Multi-board Remote Jobs Collector

Collects jobs from:
- Remote OK
- We Work Remotely
- Himalayas
- Jobicy
- Working Nomads
- Arbeitnow

Remotive is excluded until reuse permission is confirmed.
Get on Board requires a separate collector.

Uses the existing V4Career normalization and PostgreSQL repository.
"""

import os
import json
import logging
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from apify_client import ApifyClient

from collectors.normalization import normalize_job
from collectors.repository import save_jobs
from collectors.source_catalog import JOB_PORTALS


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

logger = logging.getLogger("v4career.remote_multi")


SOURCE_NAMES = {
    "remoteok": "Remote OK",
    "weworkremotely": "We Work Remotely",
    "himalayas": "Himalayas",
    "jobicy": "Jobicy",
    "workingnomads": "Working Nomads",
    "arbeitnow": "Arbeitnow",
    "remotive": "Remotive",
}

# IT-focused title/tag/category keywords.
IT_KEYWORDS = {
    "software", "developer", "development", "engineer",
    "engineering", "programmer", "programming",
    "frontend", "front-end", "backend", "back-end",
    "fullstack", "full-stack", "web development",
    "mobile development", "react", "angular", "vue",
    "node.js", "python", "java", "javascript",
    "typescript", "golang", "ruby",
    "php", "dotnet", ".net", "c++", "c#",
    "devops", "cloud", "aws", "azure", "gcp",
    "cybersecurity", "security engineer", "soc analyst",
    "data engineer", "data scientist", "data analyst",
    "machine learning", "artificial intelligence",
    "ai engineer", "ml engineer", "qa engineer",
    "automation tester", "software tester",
    "quality assurance engineer", "sdet",
    "database", "sql", "dba", "system administrator",
    "network engineer", "site reliability",
    "sre", "platform engineer", "it support",
    "technical support", "solutions architect",
    "systems engineer", "embedded", "robotics",
    "blockchain", "ui developer", "ux engineer",
    "product engineer", "technical lead",
}


def get_run_field(run_object, *field_names):
    """Read fields from dictionary or Apify Run model."""

    if isinstance(run_object, dict):
        for name in field_names:
            if run_object.get(name) is not None:
                return run_object[name]

    for name in field_names:
        value = getattr(run_object, name, None)
        if value is not None:
            return value

    for method_name in ("model_dump", "dict"):
        method = getattr(run_object, method_name, None)

        if callable(method):
            try:
                data = method()

                if isinstance(data, dict):
                    for name in field_names:
                        if data.get(name) is not None:
                            return data[name]

            except Exception:
                pass

    return None


def flatten_text(value):
    if isinstance(value, dict):
        for key in (
            "raw", "name", "label", "city",
            "region", "country", "displayName"
        ):
            if value.get(key) not in (None, ""):
                return flatten_text(value[key])

        return ""

    if isinstance(value, list):
        return ", ".join(
            flatten_text(item)
            for item in value
            if flatten_text(item)
        )

    return str(value).strip() if value is not None else ""


def is_it_job(item):
    """
    Filter using title, category and tags.
    Does not classify a job as IT merely because
    its long description mentions a technology.
    """

    searchable = " ".join([
        str(item.get("title") or ""),
        str(item.get("category") or ""),
        flatten_text(item.get("tags")),
    ]).lower()

    return any(keyword in searchable for keyword in IT_KEYWORDS)


def map_job(item):
    """Map aggregator output to V4Career's job structure."""

    source_key = str(item.get("source") or "").lower()
    source_name = SOURCE_NAMES.get(source_key)

    if not source_name:
        raise ValueError(
            f"Unknown aggregator source: {source_key}"
        )

    job_url = (
        item.get("applyUrl")
        or item.get("sourceUrl")
    )

    if not job_url:
        raise ValueError("Job application URL is missing")

    restrictions = item.get("locationRestrictions") or []

    location = (
        item.get("location")
        or flatten_text(restrictions)
        or ("Worldwide" if item.get("isWorldwide") else None)
    )

    salary = {
        "salary_min": item.get("salaryMin"),
        "salary_max": item.get("salaryMax"),
        "salary_currency": item.get("salaryCurrency"),
        "salary_period": item.get("salaryPeriod"),
    }

    raw_metadata = dict(item)
    raw_metadata["aggregator_source"] = source_key
    raw_metadata["aggregator_id"] = item.get("id")
    raw_metadata["also_listed_on"] = item.get("alsoListedOn") or []

    mapped = {
        "external_id": (
            item.get("id")
            or f"{source_key}:{item.get('sourceId')}"
        ),
        "title": item.get("title"),
        "company": item.get("company"),
        "location": location,
        "country": flatten_text(restrictions) or None,
        "experience": item.get("seniority"),
        **salary,
        "salary": item.get("salaryText"),
        "skills": item.get("tags") or [],
        "description": item.get("description"),
        "job_url": job_url,
        "posted_at": item.get("postedAt"),
        "status": "active",
        "work_mode": (
            "Remote"
            if item.get("isWorldwide")
            else None
        ),
        "employment_type": item.get("employmentType"),
        "company_logo": item.get("companyLogo"),
        "posted_via": source_name,
        "apply_options": [],
        "raw_metadata": raw_metadata,
    }

    return source_name, mapped


def main():

    token = os.getenv("APIFY_API_TOKEN", "").strip()

    if not token:
        raise RuntimeError(
            "APIFY_API_TOKEN is missing from .env"
        )

    actor_id = os.getenv(
        "APIFY_ACTOR_REMOTE_MULTI",
        "rel8ble/remote-jobs-aggregator"
    ).strip()

    if not actor_id:
        raise RuntimeError("Multi-board Actor ID is missing")

    if os.getenv(
        "APIFY_ACCESS_APPROVED_REMOTE_MULTI",
        "false"
    ).lower() != "true":
        raise RuntimeError(
            "Confirm authorized collection and reuse "
            "before enabling this Actor."
        )

    input_json = os.getenv(
        "APIFY_INPUT_REMOTE_MULTI",
        ""
    ).strip()

    if not input_json:
        raise RuntimeError(
            "APIFY_INPUT_REMOTE_MULTI is missing from .env"
        )

    try:
        run_input = json.loads(input_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Invalid APIFY_INPUT_REMOTE_MULTI JSON: {exc}"
        )

    if not isinstance(run_input, dict):
        raise RuntimeError("Actor input must be a JSON object")

    configured_sources = run_input.get("sources", [])

    if not isinstance(configured_sources, list):
        raise RuntimeError("Actor sources must be a list")

    for source in configured_sources:
        if source not in SOURCE_NAMES:
            raise RuntimeError(
                f"Unsupported source configured: {source}"
            )

    client = ApifyClient(token)

    logger.info(
        "Starting multi-board Actor: %s",
        actor_id
    )

    run = client.actor(actor_id).call(
        run_input=run_input
    )

    if not run:
        raise RuntimeError("Apify returned no run result")

    status = get_run_field(run, "status")

    if str(status).upper() != "SUCCEEDED":
        raise RuntimeError(
            f"Actor did not succeed. Status: {status}"
        )

    dataset_id = get_run_field(
        run,
        "default_dataset_id",
        "defaultDatasetId"
    )

    if not dataset_id:
        raise RuntimeError(
            "Actor succeeded but returned no dataset ID"
        )

    logger.info("Dataset ID: %s", dataset_id)

    # Find the original portal URLs in the existing source catalog.
    portal_urls = {
        portal["name"]: portal["base_url"]
        for portal in JOB_PORTALS
    }

    jobs_by_source = {
        source_name: []
        for source_name in SOURCE_NAMES.values()
    }

    skipped = 0
    non_it = 0
    total = 0

    for index, item in enumerate(
        client.dataset(dataset_id).iterate_items()
    ):

        total += 1

        try:

            if not isinstance(item, dict):
                skipped += 1
                continue

            source_key = str(
                item.get("source") or ""
            ).lower()

            source_name = SOURCE_NAMES.get(source_key)

            if not source_name:
                skipped += 1
                continue

            if not is_it_job(item):
                non_it += 1
                continue

            source_name, mapped = map_job(item)

            normalized = normalize_job(mapped)

            jobs_by_source[source_name].append(normalized)

        except (ValueError, TypeError, AttributeError) as exc:

            skipped += 1

            logger.warning(
                "Skipping dataset item %s: %s",
                index,
                exc
            )

    summaries = []

    for source_name, jobs in jobs_by_source.items():

        if not jobs:
            continue

        base_url = portal_urls.get(source_name)

        if not base_url:
            logger.warning(
                "No source catalog URL for %s",
                source_name
            )
            continue

        result = save_jobs(
            source_name,
            base_url,
            jobs
        )

        summaries.append(result)

        logger.info(
            "%s: found=%s inserted=%s updated=%s",
            source_name,
            result["found"],
            result["inserted"],
            result["updated"]
        )

    final_result = {
        "actor": actor_id,
        "dataset_id": dataset_id,
        "dataset_records": total,
        "it_jobs_processed": sum(
            len(jobs)
            for jobs in jobs_by_source.values()
        ),
        "non_it_jobs_skipped": non_it,
        "invalid_records_skipped": skipped,
        "sources": summaries,
    }

    print(json.dumps(final_result, indent=2))

    logger.info("Multi-board collection completed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.exception("Multi-board collection failed")
        raise SystemExit(str(exc))
