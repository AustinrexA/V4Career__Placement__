import os

import json
import hashlib

import re
import time
from threading import RLock
from collections import OrderedDict

from datetime import date, datetime

from flask import Flask, jsonify, request, render_template, send_file, redirect, url_for, session, g, make_response

from dotenv import load_dotenv

from collectors.db import get_connection, is_mysql, DATABASE_ERRORS, column_names

from collectors.source_catalog import JOB_PORTALS, DEFAULT_APIFY_ACTORS, UI_PORTAL_KEYS
from collectors.company_career_catalog import COMPANY_CAREER_SOURCES

from io import BytesIO, StringIO
import csv
from html import unescape

from openpyxl import Workbook

from datetime import datetime, timedelta

from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

from werkzeug.security import check_password_hash

# Load environment variables from the project root.

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# Reviewed initial title-to-family assignments generated from the supplied Neon
# title export. New titles are classified by the fallback rules below.
_JOB_FAMILY_FILE = os.path.join(PROJECT_ROOT, "data", "job_family_mapping.json")
try:
    with open(_JOB_FAMILY_FILE, "r", encoding="utf-8") as _family_file:
        _JOB_FAMILY_SEED = {
            str(title).strip().lower(): str(family).strip()
            for title, family in json.load(_family_file).items()
            if str(title).strip() and str(family).strip()
        }
except (OSError, ValueError, TypeError):
    _JOB_FAMILY_SEED = {}

_JOB_FAMILY_SYNC_LOCK = RLock()
_JOB_FAMILY_SYNC_AT = 0.0

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

app = Flask(__name__)

_configured_secret = os.getenv("FLASK_SECRET_KEY", "").strip()
if os.getenv("FLASK_ENV", "development").lower() == "production" and len(_configured_secret) < 32:
    raise RuntimeError("Production requires FLASK_SECRET_KEY to be set to a random value of at least 32 characters.")
app.secret_key = _configured_secret or os.urandom(32)

app.config.update(

    SESSION_COOKIE_HTTPONLY=True,

    SESSION_COOKIE_SAMESITE="Lax",

    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",

    PERMANENT_SESSION_LIFETIME=60 * 60 * 8,

)

@app.before_request
def _start_request_timer():
    g._v4career_request_started = time.perf_counter()

@app.after_request
def _log_slow_request(response):
    started = getattr(g, "_v4career_request_started", None)
    if started is not None:
        elapsed_ms = (time.perf_counter() - started) * 1000
        if elapsed_ms >= 1000:
            app.logger.warning(
                "PERF api endpoint=%s method=%s status=%s duration_ms=%.1f",
                request.endpoint or "unknown", request.method, response.status_code, elapsed_ms
            )
    return response

# Short-lived per-process response cache for expensive, read-only API endpoints.
# This reduces repeated dashboard/facet/report work while keeping data reasonably fresh.
# It is intentionally disabled in tests and never caches errors or unauthenticated responses.
_API_CACHE_TTLS = {
    "dashboard_stats": 15,
    "filter_options": 20,
    "list_sources": 15,
    "list_company_career_sources": 15,
    "report_jobs": 8,
    "list_jobs": 8,
    "dashboard_trend": 20,
}
_API_CACHE_MAX_ENTRIES = 256
_api_response_cache = OrderedDict()
_api_response_cache_lock = RLock()


def _api_cache_key():
    if app.config.get("TESTING") or request.method != "GET" or not is_logged_in():
        return None
    ttl = _API_CACHE_TTLS.get(request.endpoint or "")
    if not ttl:
        return None
    return (request.endpoint, request.query_string.decode("utf-8", errors="replace")), ttl


@app.before_request
def _serve_cached_api_response():
    key_and_ttl = _api_cache_key()
    if not key_and_ttl:
        return None
    key, _ttl = key_and_ttl
    now = time.monotonic()
    with _api_response_cache_lock:
        cached = _api_response_cache.get(key)
        if cached and cached[0] > now:
            _api_response_cache.move_to_end(key)
            app.logger.debug("PERF cache_hit endpoint=%s", request.endpoint)
            response = make_response(cached[1], cached[2])
            response.headers["Content-Type"] = cached[3]
            response.headers["X-V4Career-Cache"] = "HIT"
            return response
        if cached:
            _api_response_cache.pop(key, None)
    return None


@app.after_request
def _store_cached_api_response(response):
    key_and_ttl = _api_cache_key()
    if not key_and_ttl or response.status_code != 200 or response.headers.get("Set-Cookie") or response.headers.get("X-V4Career-Cache") == "HIT":
        return response
    key, ttl = key_and_ttl
    try:
        body = response.get_data()
    except Exception:
        return response
    with _api_response_cache_lock:
        _api_response_cache[key] = (
            time.monotonic() + ttl, body, response.status_code,
            response.headers.get("Content-Type", "application/json"),
        )
        _api_response_cache.move_to_end(key)
        while len(_api_response_cache) > _API_CACHE_MAX_ENTRIES:
            _api_response_cache.popitem(last=False)
    response.headers["X-V4Career-Cache"] = "MISS"
    return response


def is_logged_in():

    return bool(session.get("authenticated"))

@app.before_request

def require_login():

    # Keep the login page, login submission, static assets, and health check public.

    if request.endpoint in {"login", "health", "static"}:

        return None

    if request.path.startswith("/api/") and not is_logged_in():

        return jsonify({"error": "Authentication required", "login_url": url_for("login")}), 401

    if request.path == "/" and not is_logged_in():

        return redirect(url_for("login"))

    return None

# Phase 2: Email OTP authentication
import hashlib
import secrets
import smtplib
from email.message import EmailMessage

OTP_TTL_SECONDS = int(os.getenv("OTP_TTL_SECONDS", "300"))
OTP_RESEND_SECONDS = int(os.getenv("OTP_RESEND_SECONDS", "60"))
OTP_MAX_ATTEMPTS = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))

def _otp_accounts():
    """Resolve login emails to roles using server-side configuration only."""
    result = []
    for role in ("admin", "user"):
        email = os.getenv(f"{role.upper()}_EMAIL", "").strip().lower()
        username = os.getenv(f"{role.upper()}_USERNAME", "").strip()
        if email and username:
            result.append({"email": email, "username": username, "role": role})
    return result

def _send_otp_email(recipient, code):
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("SMTP_FROM", smtp_username).strip()
    if not host or not sender or not smtp_username or not smtp_password:
        raise RuntimeError("SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD and SMTP_FROM must be configured.")
    msg = EmailMessage()
    msg["Subject"] = "Your V4Career verification code"
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(
        f"Your V4Career login code is: {code}\\n\\n"
        f"This code expires in {max(1, OTP_TTL_SECONDS // 60)} minutes. "
        "Do not share it with anyone."
    )
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        if os.getenv("SMTP_USE_TLS", "true").lower() == "true":
            smtp.starttls()
        smtp.login(smtp_username, smtp_password)
        smtp.send_message(msg)

def _create_otp(account):
    code = f"{secrets.randbelow(1_000_000):06d}"
    _send_otp_email(account["email"], code)
    session.clear()
    session["pending_otp_email"] = account["email"]
    session["pending_otp_username"] = account["username"]
    session["pending_otp_role"] = account["role"]
    session["pending_otp_digest"] = hashlib.sha256(code.encode()).hexdigest()
    session["pending_otp_expiry"] = time.time() + OTP_TTL_SECONDS
    session["pending_otp_sent_at"] = time.time()
    session["pending_otp_attempts"] = 0
    session.permanent = True

# Phase 3: persistent login/logout audit
def _write_login_audit(email="", username="", role="unknown", event="login", status="success"):
    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS login_audit (
            id BIGINT PRIMARY KEY, username VARCHAR(150), role VARCHAR(30),
            login_at VARCHAR(40), ip_address VARCHAR(100), email VARCHAR(255),
            event VARCHAR(20), status VARCHAR(20), event_at VARCHAR(40), location VARCHAR(255)
        )""")
        # Backward-compatible upgrades for databases created by Phase 2.
        for col, typ in [("email","VARCHAR(255)"),("event","VARCHAR(20)"),("status","VARCHAR(20)"),("event_at","VARCHAR(40)"),("location","VARCHAR(255)") ]:
            cur.execute(f"ALTER TABLE login_audit ADD COLUMN IF NOT EXISTS {col} {typ}")
        # IP is taken from Flask's remote_addr (configure ProxyFix only for trusted proxies).
        ip = request.remote_addr or "unknown"
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        location = "Unknown"
        # Approximate city/region/country from public IP; not GPS-level location.
        try:
            import ipaddress, urllib.request, json
            parsed_ip = ipaddress.ip_address(ip)
            if parsed_ip.is_global and os.getenv("IP_GEOLOCATION_ENABLED", "true").lower() == "true":
                req = urllib.request.Request(
                    f"https://ipwho.is/{ip}?fields=success,city,region,country",
                    headers={"User-Agent": "V4Career-Audit/1.0"})
                with urllib.request.urlopen(req, timeout=1.5) as response:
                    geo = json.loads(response.read().decode("utf-8"))
                if geo.get("success"):
                    location = ", ".join(v for v in (geo.get("city"), geo.get("region"), geo.get("country")) if v) or "Unknown"
        except Exception:
            location = "Unknown"
        cur.execute("""INSERT INTO login_audit
            (id, username, role, login_at, ip_address, email, event, status, event_at, location)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (time.time_ns(), username or "unknown", role or "unknown", stamp, ip,
             email or "", event, status, stamp, location))
        conn.commit()
    except Exception:
        app.logger.exception("Unable to write login audit record")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

from functools import wraps
def _phase4_login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not is_logged_in(): return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped

# Phase 4 credit tables
def _ensure_credit_tables():
    c=get_connection(); cur=c.cursor()
    try:
        cur.execute("CREATE TABLE IF NOT EXISTS user_credits (username VARCHAR(150) PRIMARY KEY,email VARCHAR(255),balance INTEGER NOT NULL DEFAULT 5,account_status VARCHAR(30) NOT NULL DEFAULT 'active',subscription_plan VARCHAR(80) NOT NULL DEFAULT 'free',subscription_status VARCHAR(30) NOT NULL DEFAULT 'inactive',subscription_expires_at VARCHAR(40),created_at VARCHAR(40),updated_at VARCHAR(40))")
        cur.execute("CREATE TABLE IF NOT EXISTS credit_history (id BIGINT PRIMARY KEY,username VARCHAR(150),email VARCHAR(255),action VARCHAR(30),amount INTEGER,balance_after INTEGER,job_id BIGINT,job_title VARCHAR(500),details VARCHAR(1000),created_at VARCHAR(40))")
        cur.execute("CREATE TABLE IF NOT EXISTS credit_settings (setting_key VARCHAR(100) PRIMARY KEY,setting_value VARCHAR(255))")
        cur.execute("SELECT setting_value FROM credit_settings WHERE setting_key=%s",('free_credits',))
        if not cur.fetchone(): cur.execute("INSERT INTO credit_settings(setting_key,setting_value) VALUES(%s,%s)",('free_credits','5'))
        c.commit()
    finally: cur.close(); c.close()

def _ensure_user_credit(username,email):
    _ensure_credit_tables(); c=get_connection(); cur=c.cursor()
    try:
        cur.execute("SELECT balance FROM user_credits WHERE username=%s",(username,))
        if cur.fetchone(): return
        cur.execute("SELECT setting_value FROM credit_settings WHERE setting_key=%s",('free_credits',)); r=cur.fetchone(); amount=int(r[0]) if r else 5
        now=datetime.now().astimezone().isoformat(timespec='seconds')
        cur.execute("INSERT INTO user_credits(username,email,balance,created_at,updated_at) VALUES(%s,%s,%s,%s,%s)",(username,email,amount,now,now))
        cur.execute("INSERT INTO credit_history(id,username,email,action,amount,balance_after,details,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",(time.time_ns(),username,email,'allocation',amount,amount,'Welcome free credits',now))
        c.commit()
    finally: cur.close(); c.close()


@app.route("/login", methods=["GET", "POST"])
def login():
    if is_logged_in() and request.method == "GET":
        return redirect(url_for("home"))
    error = None
    if request.method == "POST":
        login_id = request.form.get("email", "").strip().lower()
        supplied = request.form.get("password", "")
        account = next((a for a in _otp_accounts()
                        if a["email"].lower() == login_id or a["username"].lower() == login_id), None)
        email = account["email"] if account else login_id
        if not account:
            _write_login_audit(email=email, role="unknown", event="login", status="failed")
            error = "Incorrect email or password."
        else:
            configured = os.getenv(f"{account['role'].upper()}_PASSWORD", "")
            configured_hash = os.getenv(f"{account['role'].upper()}_PASSWORD_HASH", "")
            valid = (check_password_hash(configured_hash, supplied) if configured_hash
                     else bool(configured) and secrets.compare_digest(supplied, configured))
            if valid:
                # Password-only login: OTP verification is disabled in this build.
                username = account["username"]
                role = account["role"]
                email = account["email"]
                if role == "user":
                    _ensure_user_credit(username, email)
                session.clear()
                session["authenticated"] = True
                session["username"] = username
                session["role"] = role
                session["email"] = email
                session.permanent = True
                _write_login_audit(email=email, username=username, role=role, event="login", status="success")
                return redirect(url_for("home"))
            else:
                _write_login_audit(email=account["email"], username=account["username"], role=account["role"], event="login", status="failed")
                error = "Incorrect email or password."
    return render_template("login.html", error=error)

@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    if not session.get("pending_otp_email"):
        return redirect(url_for("login"))
    error = None
    if request.method == "POST":
        if time.time() > float(session.get("pending_otp_expiry", 0)):
            session.pop("pending_otp_digest", None)
            error = "Verification code expired. Request a new code."
        elif int(session.get("pending_otp_attempts", 0)) >= OTP_MAX_ATTEMPTS:
            session.pop("pending_otp_digest", None)
            error = "Too many incorrect attempts. Request a new code."
        else:
            code = request.form.get("otp", "").strip()
            digest = hashlib.sha256(code.encode()).hexdigest()
            if len(code) == 6 and secrets.compare_digest(digest, session.get("pending_otp_digest", "")):
                username = session["pending_otp_username"]
                role = session["pending_otp_role"]
                email = session["pending_otp_email"]
                if role == "user": _ensure_user_credit(username,email)
                session.clear()
                session["authenticated"] = True
                session["username"] = username
                session["role"] = role
                session["email"] = email
                session.permanent = True
                _write_login_audit(email=email, username=username, role=role, event="login", status="success")
                return redirect(url_for("home"))
            session["pending_otp_attempts"] = int(session.get("pending_otp_attempts", 0)) + 1
            _write_login_audit(email=session.get("pending_otp_email",""), username=session.get("pending_otp_username",""), role=session.get("pending_otp_role","unknown"), event="login", status="failed")
            error = "Incorrect verification code."
    return render_template("verify_otp.html", error=error, email=session.get("pending_otp_email"))

@app.route("/resend-otp", methods=["POST"])
def resend_otp():
    email = session.get("pending_otp_email")
    account = next((a for a in _otp_accounts() if a["email"] == email), None)
    if not account:
        session.clear()
        return redirect(url_for("login"))
    remaining = OTP_RESEND_SECONDS - (time.time() - float(session.get("pending_otp_sent_at", 0)))
    if remaining > 0:
        return render_template("verify_otp.html",
            error=f"Wait {int(remaining) + 1} seconds before resending.", email=email)
    try:
        _create_otp(account)
        return redirect(url_for("verify_otp"))
    except Exception:
        app.logger.exception("OTP resend failed")
        return render_template("verify_otp.html", error="Could not resend code. Try again.", email=email)

@app.route("/logout", methods=["POST"])
def logout():
    if session.get("authenticated"):
        _write_login_audit(email=session.get("email",""), username=session.get("username",""), role=session.get("role","unknown"), event="logout", status="success")
    session.clear()
    return redirect(url_for("login"))

# Convert dates and other non-JSON values into JSON-safe values.

def json_safe(value):

    if isinstance(value, (datetime, date)):

        return value.isoformat()

    if isinstance(value, bytes):

        return value.decode("utf-8", errors="replace")

    return value

def row_to_dict(cursor, row):

    columns = column_names(cursor)

    result = dict(zip(columns, row))

    for key, value in result.items():

        if key == "skills" and isinstance(value, str):

            try:

                result[key] = json.loads(value)

            except (ValueError, TypeError):

                pass

        else:

            result[key] = json_safe(value)

    return result

def fetch_all(query, params=()):

    started = time.perf_counter()
    connect_started = time.perf_counter()
    connection = get_connection()
    connect_ms = (time.perf_counter() - connect_started) * 1000
    cursor = connection.cursor()

    try:
        query_started = time.perf_counter()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        query_ms = (time.perf_counter() - query_started) * 1000
        columns = column_names(cursor)

        results = []

        for row in rows:
            item = dict(zip(columns, row))
            for key, value in item.items():
                if key == "skills" and isinstance(value, str):
                    try:
                        item[key] = json.loads(value)
                    except (ValueError, TypeError):
                        pass
                else:
                    item[key] = json_safe(value)
            results.append(item)

        total_ms = (time.perf_counter() - started) * 1000
        if connect_ms >= 250 or query_ms >= 500 or total_ms >= 1000:
            app.logger.warning(
                "PERF db engine=%s connect_ms=%.1f query_fetch_ms=%.1f total_ms=%.1f rows=%s",
                os.getenv("DB_ENGINE", "postgres").lower(), connect_ms, query_ms, total_ms, len(rows)
            )
        return results

    finally:
        cursor.close()
        connection.close()

def fetch_one(query, params=()):

    results = fetch_all(query, params)

    return results[0] if results else None


JOB_FAMILY_NAMES = [
    "Software Development & Engineering",
    "Data, Analytics & Artificial Intelligence",
    "IT Infrastructure, Cloud & Cybersecurity",
    "Testing & Quality Assurance",
    "ERP & Enterprise Applications",
    "Database Engineering & Administration",
    "Human Resources & Talent Management",
    "Sales, Marketing & Business Development",
    "Finance, Accounting & Banking",
    "Business Management, Consulting & Administration",
    "Design, Creative & Content",
    "Customer Support, Service & Operations",
    "Core Engineering & Manufacturing",
    "Graduate, Internship & Entry-Level",
    "Other & Needs Review",
]

_JOB_FAMILY_FALLBACK_RULES = [
    ("Graduate, Internship & Entry-Level", r"\b(intern(ship)?|apprentice(ship)?|graduate trainee|graduate engineer trainee|trainee|fresher|early career|entry[ -]?level|campus hire|new grad)\b"),
    ("Data, Analytics & Artificial Intelligence", r"\b(data|analytics|analyst|business intelligence|power bi|tableau|machine learning|artificial intelligence|data science|statistician|alteryx)\b"),
    ("IT Infrastructure, Cloud & Cybersecurity", r"\b(cloud|devops|security|cyber|network|infrastructure|system admin|linux|windows admin|soc analyst|iam)\b"),
    ("Testing & Quality Assurance", r"\b(test|testing|quality assurance|\bqa\b|sdet|validation)\b"),
    ("ERP & Enterprise Applications", r"\b(sap|salesforce|sfdc|servicenow|erp|oracle fusion|workday|dynamics|pega|sharepoint)\b"),
    ("Database Engineering & Administration", r"\b(database|\bdba\b|postgres|mysql|oracle dba|sql developer|database administrator)\b"),
    ("Human Resources & Talent Management", r"\b(recruit|talent acquisition|human resource|\bhr\b|staffing|people partner)\b"),
    ("Sales, Marketing & Business Development", r"\b(sales|marketing|business development|account executive|lead generation|telesales)\b"),
    ("Finance, Accounting & Banking", r"\b(finance|accountant|accounting|audit|tax|payroll|banking|financial analyst)\b"),
    ("Design, Creative & Content", r"\b(design|creative|content|writer|video editor|\bux\b|\bui\b|graphic|visual designer)\b"),
    ("Customer Support, Service & Operations", r"\b(support|service|operations|non.?voice|voice process|call center|customer|ar caller|helpdesk|help desk|process associate)\b"),
    ("Core Engineering & Manufacturing", r"\b(mechanical|civil|electrical|electronics|manufactur|production|technician|simulation engineer|automotive)\b"),
    ("Business Management, Consulting & Administration", r"\b(project|program|manager|consultant|consulting|business associate|executive assistant|administrat|team lead|implementation analyst)\b"),
    ("Software Development & Engineering", r"\b(developer|programmer|software engineer|software development|full.?stack|frontend|front.?end|backend|back.?end|java|python|javascript|react|angular|vue|node\.?js|\.net|dotnet|spring boot|application engineer|technical architect|solution architect)\b"),
]


def _classify_job_family(title):
    text = str(title or "").strip()
    if not text:
        return "Other & Needs Review"
    lowered = text.lower()
    seeded = _JOB_FAMILY_SEED.get(lowered)
    if seeded:
        return seeded
    for family, pattern in _JOB_FAMILY_FALLBACK_RULES:
        if re.search(pattern, lowered, re.IGNORECASE):
            return family
    return "Other & Needs Review"



# Canonical role groups make title variants appear under one readable role.
# Rules are intentionally family-specific so similar words in unrelated
# families are not accidentally merged.
_JOB_ROLE_GROUP_RULES = {
    "Software Development & Engineering": [
        ("Full Stack Developer", r"\b(full[ -]?stack)\b"),
        ("Backend Developer", r"\b(back[ -]?end|server[ -]?side)\b"),
        ("Frontend Developer", r"\b(front[ -]?end|ui developer)\b"),
        ("Spring Boot Developer", r"\bspring\s*boot\b"),
        ("Java Developer", r"\bjava\b"),
        ("Python Developer", r"\bpython\b"),
        ("PHP Developer", r"\bphp\b"),
        ("Node.js Developer", r"\bnode\.?js\b"),
        ("JavaScript Developer", r"\bjavascript|typescript\b"),
        ("React Developer", r"\breact(\.js)?\b"),
        ("Angular Developer", r"\bangular\b"),
        ("Mobile App Developer", r"\b(android|ios|mobile app|react native|flutter)\b"),
        ("Web Developer", r"\bweb developer|web application developer\b"),
        ("Software Architect", r"\b(software|solution|technical|application) architect\b"),
        (".NET Developer", r"(\.net|dotnet|c\s*#)"),
        ("Software Engineer", r"\bsoftware engineer\b|\bapplication engineer\b"),
        ("Software Developer", r"\bsoftware developer\b|\bapplication developer\b|\bprogrammer\b|\bdeveloper\b"),
    ],
    "Data, Analytics & Artificial Intelligence": [
        ("Data Analyst", r"\bdata\s*analyst\b|\bdata analytics analyst\b|\bdata reporting analyst\b"),
        ("Data Scientist", r"\bdata scientist\b|\bapplied scientist\b"),
        ("Data Engineer", r"\bdata engineer\b|\bbig data engineer\b|\betl (developer|engineer)\b"),
        ("Power BI Developer", r"\bpower\s*bi\b.*\b(developer|engineer)\b|\bpowerbi developer\b"),
        ("Business Intelligence Developer", r"\b(business intelligence|bi)\b.*\b(developer|engineer)\b"),
        ("Power BI Analyst", r"\bpower\s*bi\b.*\b(analyst|specialist)\b|\bpowerbi analyst\b"),
        ("Business Intelligence Analyst", r"\b(business intelligence|bi)\b.*\b(analyst|specialist)\b|\bbi analyst\b"),
        ("Tableau Developer", r"\btableau\b.*\b(developer|engineer)\b"),
        ("Tableau Analyst", r"\btableau\b.*\b(analyst|specialist)\b"),
        ("Machine Learning Engineer", r"\b(machine learning|ml)\b.*\b(engineer|developer)\b|\bml engineer\b"),
        ("AI Engineer", r"\b(ai|artificial intelligence|gen(?:erative)? ai|genai|llm|large language model)\b.*\b(engineer|developer|specialist)\b|\b(ai|genai|llm) engineer\b"),
        ("AI / ML Researcher", r"\b(ai|machine learning|ml)\b.*\b(research|researcher|scientist)\b"),
        ("NLP Engineer", r"\b(nlp|natural language processing)\b"),
        ("Computer Vision Engineer", r"\bcomputer vision\b"),
        ("Data Architect", r"\bdata architect\b"),
        ("Analytics Engineer", r"\banalytics engineer\b"),
        ("Data Quality Analyst", r"\bdata quality\b"),
        ("Data Visualization Specialist", r"\b(data visualization|data visualisation)\b"),
        ("Data Warehouse Engineer", r"\b(data warehouse|dwh)\b"),
        ("Statistician", r"\b(statistician|statistical analyst)\b"),
        ("Research Analyst", r"\bresearch analyst\b"),
    ],
    "IT Infrastructure, Cloud & Cybersecurity": [
        ("DevOps Engineer", r"\bdevops\b"),
        ("Cloud Engineer", r"\bcloud\b.*\b(engineer|specialist|administrator)\b|\b(aws|azure|gcp) cloud engineer\b"),
        ("Cloud Architect", r"\bcloud architect\b|\b(aws|azure|gcp) architect\b"),
        ("Site Reliability Engineer", r"\b(site reliability|sre)\b"),
        ("System Administrator", r"\b(system|systems|linux|windows|unix)\b.*\b(administrator|admin|engineer)\b|\bsysadmin\b"),
        ("Network Engineer", r"\b(network|routing|switching)\b.*\b(engineer|administrator)\b|\bnetwork engineer\b"),
        ("Cybersecurity Analyst", r"\b(cyber ?security|soc|security operations|information security)\b.*\b(analyst|specialist)\b|\bsoc analyst\b"),
        ("Security Engineer", r"\b(security|information security|application security|iam)\b.*\b(engineer|architect|specialist)\b"),
        ("Penetration Tester", r"\b(penetration|penetration testing|ethical hack|vulnerability)\b"),
        ("Infrastructure Engineer", r"\binfrastructure\b.*\b(engineer|specialist|administrator)\b"),
        ("Database Administrator", r"\b(database|db)\s*(administrator|admin|dba)\b|\bdba\b"),
        ("IT Support Engineer", r"\b(it|technical|desktop|help ?desk)\b.*\b(support|service desk)\b|\bdesktop support\b"),
        ("IT Operations Engineer", r"\b(it|infrastructure|cloud)\s*operations\b"),
        ("Systems Engineer", r"\bsystems engineer\b"),
        ("Storage Engineer", r"\b(storage|backup)\b.*\b(engineer|administrator)\b"),
        ("Virtualization Engineer", r"\b(vmware|virtualization|virtualisation)\b"),
        ("NOC Engineer", r"\bnoc\b.*\b(engineer|analyst)\b|\bnetwork operations center\b"),
    ],
    "Testing & Quality Assurance": [
        ("SDET", r"\bsdet\b|software development engineer in test"),
        ("Automation Test Engineer", r"\b(automation|automated)\b.*\b(test|qa|quality)\b|\bqa automation\b|\bselenium\b|\bplaywright tester\b"),
        ("Manual Test Engineer", r"\bmanual\b.*\b(test|qa)\b"),
        ("Performance Test Engineer", r"\b(performance|load|stress)\b.*\b(test|qa)\b"),
        ("API Test Engineer", r"\bapi\b.*\b(test|testing)\b|\bpostman tester\b"),
        ("Mobile Test Engineer", r"\bmobile\b.*\b(test|testing|qa)\b"),
        ("QA Engineer", r"\b(qa|quality assurance|test|testing)\b.*\b(engineer|analyst|specialist)\b|\bsoftware tester\b|\btester\b"),
        ("Quality Analyst", r"\bquality analyst\b|\bqa analyst\b"),
        ("Test Lead", r"\b(test|qa|quality)\b.*\blead\b"),
    ],
    "ERP & Enterprise Applications": [
        ("SAP Consultant", r"\bsap\b"),
        ("Salesforce Developer", r"\bsalesforce|sfdc\b"),
        ("ServiceNow Developer", r"\bservicenow\b"),
        ("Workday Consultant", r"\bworkday\b"),
        ("Oracle Applications Consultant", r"\boracle (fusion|apps|applications)\b"),
        ("Microsoft Dynamics Consultant", r"\b(microsoft dynamics|dynamics 365|d365)\b"),
        ("ERP Consultant", r"\berp\b"),
        ("CRM Consultant", r"\bcrm\b"),
        ("SharePoint Developer", r"\bsharepoint\b"),
        ("Pega Developer", r"\bpega\b"),
    ],
    "Database Engineering & Administration": [
        ("Database Administrator", r"\b(database|db)\s*(administrator|admin|dba)\b|\bdba\b"),
        ("Database Developer", r"\b(database|sql)\s*(developer|engineer)\b"),
        ("Data Warehouse Engineer", r"\b(data warehouse|dwh)\b"),
        ("Database Architect", r"\bdatabase architect\b"),
        ("SQL Developer", r"\bsql developer\b"),
        ("ETL Developer", r"\betl\b"),
        ("Database Analyst", r"\bdatabase analyst\b"),
    ],
    "Human Resources & Talent Management": [
        ("Talent Acquisition Specialist", r"\b(talent acquisition|recruiter|recruitment)\b"),
        ("HR Business Partner", r"\bhr business partner\b|\bhrbp\b"),
        ("HR Generalist", r"\bhr generalist|human resources generalist\b"),
        ("HR Manager", r"\b(hr|human resources)\b.*\bmanager\b"),
        ("HR Operations Specialist", r"\bhr operations\b"),
        ("Payroll Specialist", r"\bpayroll\b"),
        ("Learning & Development Specialist", r"\b(learning and development|l&d|training and development)\b"),
    ],
    "Sales, Marketing & Business Development": [
        ("Business Development Executive", r"\bbusiness development\b"),
        ("Sales Executive", r"\bsales\b.*\b(executive|representative|associate|officer)\b"),
        ("Account Manager", r"\baccount manager\b"),
        ("Account Executive", r"\baccount executive\b"),
        ("Digital Marketing Specialist", r"\bdigital marketing\b"),
        ("Marketing Executive", r"\bmarketing\b.*\b(executive|specialist|associate)\b"),
        ("SEO Specialist", r"\bseo\b"),
        ("Social Media Specialist", r"\bsocial media\b"),
        ("Growth Marketing Specialist", r"\bgrowth marketing\b"),
        ("Pre-Sales Consultant", r"\b(pre.?sales|presales|sales consultant)\b"),
    ],
    "Finance, Accounting & Banking": [
        ("Accountant", r"\b(accountant|accounting executive)\b"),
        ("Financial Analyst", r"\bfinancial analyst\b"),
        ("Auditor", r"\b(audit|auditor)\b"),
        ("Tax Specialist", r"\btax\b"),
        ("Banking Operations Associate", r"\b(bank|banking)\b.*\b(operation|associate|officer)\b"),
        ("Accounts Payable Specialist", r"\b(accounts payable|payable)\b"),
        ("Accounts Receivable Specialist", r"\b(accounts receivable|receivable)\b"),
        ("Finance Manager", r"\bfinance manager\b"),
        ("Credit Analyst", r"\bcredit analyst\b"),
    ],
    "Business Management, Consulting & Administration": [
        ("Project Manager", r"\bproject manager\b"),
        ("Program Manager", r"\bprogram manager\b"),
        ("Business Analyst", r"\bbusiness analyst\b"),
        ("Management Consultant", r"\b(management|business|strategy) consultant\b"),
        ("Operations Manager", r"\boperations manager\b"),
        ("Project Coordinator", r"\bproject coordinator\b"),
        ("Business Operations Analyst", r"\bbusiness operations\b"),
        ("Implementation Specialist", r"\bimplementation\b"),
        ("Administrative Executive", r"\b(administrative|administration|office admin)\b"),
    ],
    "Design, Creative & Content": [
        ("UI / UX Designer", r"\b(ui/?ux|ux/ui|user experience|user interface)\b"),
        ("Graphic Designer", r"\bgraphic designer\b"),
        ("Product Designer", r"\bproduct designer\b"),
        ("Visual Designer", r"\bvisual designer\b"),
        ("Content Writer", r"\b(content writer|copywriter|technical writer)\b"),
        ("Video Editor", r"\bvideo editor\b"),
        ("Motion Graphics Designer", r"\b(motion graphics|animation designer)\b"),
        ("3D Designer", r"\b(3d|three dimensional)\b.*\b(designer|artist)\b"),
    ],
    "Customer Support, Service & Operations": [
        ("Customer Support Executive", r"\b(customer|technical|client)\b.*\b(support|service)\b"),
        ("Helpdesk Support Engineer", r"\b(help ?desk|service desk|desktop support)\b"),
        ("Technical Support Engineer", r"\btechnical support\b"),
        ("Operations Associate", r"\boperations\b.*\b(associate|executive|analyst)\b"),
        ("Process Associate", r"\bprocess associate\b"),
        ("Call Center Executive", r"\b(call ?center|voice process|non.?voice)\b"),
        ("Customer Success Specialist", r"\bcustomer success\b"),
        ("Service Delivery Manager", r"\bservice delivery\b"),
    ],
    "Core Engineering & Manufacturing": [
        ("Mechanical Engineer", r"\bmechanical\b"),
        ("Electrical Engineer", r"\belectrical\b"),
        ("Electronics Engineer", r"\belectronics?\b"),
        ("Civil Engineer", r"\bcivil\b"),
        ("Manufacturing Engineer", r"\b(manufacturing|production)\b"),
        ("Quality Engineer", r"\bquality\b.*\b(engineer|inspector)\b"),
        ("Automation Engineer", r"\b(industrial|process) automation\b|\bautomation engineer\b"),
        ("Design Engineer", r"\bdesign engineer\b"),
        ("Maintenance Engineer", r"\bmaintenance engineer\b"),
        ("Production Engineer", r"\bproduction engineer\b"),
    ],
    "Graduate, Internship & Entry-Level": [
        ("Software Intern", r"\b(software|developer|programming)\b.*\bintern(ship)?\b|\bintern(ship)?\b.*\b(software|developer)\b"),
        ("Data Intern", r"\b(data|analytics|ai|machine learning)\b.*\bintern(ship)?\b|\bintern(ship)?\b.*\b(data|analytics|ai)\b"),
        ("Engineering Intern", r"\b(engineering|engineer)\b.*\bintern(ship)?\b|\bintern(ship)?\b.*\b(engineering|engineer)\b"),
        ("Business Intern", r"\b(business|marketing|finance|hr)\b.*\bintern(ship)?\b"),
        ("Graduate Trainee", r"\b(graduate|fresher|trainee|entry[ -]?level|campus)\b"),
        ("Apprentice", r"\bapprentice(ship)?\b"),
    ],
    "Other & Needs Review": [
        ("Other Roles", r".*"),
    ],
}


def _canonicalize_job_role(title, family):
    """Map title variants to a concise canonical role within their family."""
    raw = str(title or "").strip()
    if not raw:
        return "Other Roles"
    lowered = re.sub(r"\s+", " ", raw.lower())
    rules = _JOB_ROLE_GROUP_RULES.get(family, [])
    for label, pattern in rules:
        if re.search(pattern, lowered, re.IGNORECASE):
            return label
    # Remove common seniority / employment qualifiers for otherwise
    # equivalent titles, but preserve the actual role wording.
    cleaned = re.sub(
        r"\b(senior|sr\.?|junior|jr\.?|lead|principal|staff|associate|"
        r"assistant|trainee|contract|temporary|permanent|remote|hybrid)\b",
        " ", raw, flags=re.IGNORECASE
    )
    cleaned = re.sub(r"[\s\-–—|,/]+", " ", cleaned).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.title() if cleaned else raw


def _ensure_job_family_mapping(force=False):
    """Create/synchronize a separate title-family lookup without modifying jobs."""
    global _JOB_FAMILY_SYNC_AT
    if app.config.get("TESTING"):
        return
    now = time.monotonic()
    if not force and now - _JOB_FAMILY_SYNC_AT < 300:
        return
    with _JOB_FAMILY_SYNC_LOCK:
        now = time.monotonic()
        if not force and now - _JOB_FAMILY_SYNC_AT < 300:
            return
        connection = get_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS job_title_family_map ("
                "title_hash CHAR(32) PRIMARY KEY, "
                "original_title TEXT NOT NULL, "
                "job_family VARCHAR(160) NOT NULL, "
                "role_group VARCHAR(200), "
                "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            connection.commit()
            try:
                cursor.execute("ALTER TABLE job_title_family_map ADD COLUMN role_group VARCHAR(200)")
                connection.commit()
            except Exception:
                # Column already exists on databases upgraded previously.
                connection.rollback()

        finally:
            cursor.close()
            connection.close()

        # The title export seeds known titles. The DB scan also discovers newly
        # scraped titles and classifies them without changing the jobs table.
        existing_rows = fetch_all(
            "SELECT title_hash, job_family, role_group FROM job_title_family_map"
        )
        existing = {
            str(row.get("title_hash") or "").strip():
                (str(row.get("job_family") or "").strip(),
                 str(row.get("role_group") or "").strip())
            for row in existing_rows
        }
        titles = fetch_all(
            "SELECT DISTINCT TRIM(title) AS title FROM jobs "
            "WHERE title IS NOT NULL AND TRIM(title) <> ''"
        )
        pending = []
        for row in titles:
            title = str(row.get("title") or "").strip()
            if not title:
                continue
            title_hash = hashlib.md5(title.encode("utf-8")).hexdigest()
            family = _classify_job_family(title)
            role_group = _canonicalize_job_role(title, family)
            if existing.get(title_hash) != (family, role_group):
                pending.append((title_hash, title, family, role_group))
        if pending:
            connection = get_connection()
            cursor = connection.cursor()
            try:
                if is_mysql():
                    upsert_sql = (
                        "INSERT INTO job_title_family_map (title_hash, original_title, job_family, role_group) "
                        "VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE "
                        "original_title=VALUES(original_title), job_family=VALUES(job_family), "
                        "role_group=VALUES(role_group), updated_at=CURRENT_TIMESTAMP"
                    )
                else:
                    upsert_sql = (
                        "INSERT INTO job_title_family_map (title_hash, original_title, job_family, role_group) "
                        "VALUES (%s, %s, %s, %s) ON CONFLICT (title_hash) DO UPDATE SET "
                        "original_title=EXCLUDED.original_title, job_family=EXCLUDED.job_family, "
                        "role_group=EXCLUDED.role_group, updated_at=CURRENT_TIMESTAMP"
                    )
                for offset in range(0, len(pending), 500):
                    cursor.executemany(upsert_sql, pending[offset:offset + 500])
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
                connection.close()
        _JOB_FAMILY_SYNC_AT = time.monotonic()


@app.route("/", methods=["GET"])

def home():

    role = str(session.get("role", "admin")).strip().lower()
    return render_template("index.html", user_role=role, username=session.get("username", ""))

@app.route("/api/health", methods=["GET"])

def health():

    connection = None

    cursor = None

    try:

        connection = get_connection()

        cursor = connection.cursor()

        cursor.execute("SELECT 1")

        cursor.fetchone()

        return jsonify({
            "status": "healthy",
            "database": "connected",
            "engine": "mysql" if is_mysql() else "postgres",
        })

    except DATABASE_ERRORS:

        app.logger.exception("Database health check failed")

        return jsonify({

            "status": "unhealthy",

            "database": "unavailable",

        }), 503

    finally:

        if cursor:

            cursor.close()

        if connection:

            connection.close()


def _expand_location_terms(values):
    """Expand canonical location labels to common database spellings."""
    aliases = {
        "bengaluru": ["Bengaluru", "Bangalore", "Bangalore Rural", "Bangalore Urban", "Bengaluru Rural", "Bengaluru Urban"],
        "bangalore": ["Bengaluru", "Bangalore", "Bangalore Rural", "Bangalore Urban", "Bengaluru Rural", "Bengaluru Urban", "Bangalore, Rural", "Bengaluru, Rural", "Bangalore Metropolitan", "Bengaluru Metropolitan", "Devanahalli", "Doddaballapur", "Hoskote", "Nelamangala", "Ramanagara"],
        "mumbai": ["Mumbai", "Bombay"],
        "bombay": ["Mumbai", "Bombay"],
        "chennai": ["Chennai", "Madras", "Chennai Rural", "Chennai Urban", "Chennai, Tamil Nadu"],
        "madras": ["Chennai", "Madras"],
        "kolkata": ["Kolkata", "Calcutta"],
        "calcutta": ["Kolkata", "Calcutta"],
        "gurugram": ["Gurugram", "Gurgaon"],
        "aluva": ["Aluva", "Alwaye"],
        "alwaye": ["Aluva", "Alwaye"],
        "kochi": ["Kochi", "Cochin", "Ernakulam"],
        "cochin": ["Kochi", "Cochin", "Ernakulam"],
        "hyderabad": ["Hyderabad", "Secunderabad"],
        "delhi": ["Delhi", "New Delhi", "Delhi NCR", "Delhi / NCR", "Delhi Ncr"],
        "new delhi": ["Delhi", "New Delhi", "Delhi NCR", "Delhi / NCR"],
        "mysuru": ["Mysuru", "Mysore"],
        "mysore": ["Mysuru", "Mysore"],
        "mangaluru": ["Mangaluru", "Mangalore"],
        "mangalore": ["Mangaluru", "Mangalore"],
        "trivandrum": ["Trivandrum", "Thiruvananthapuram"],
        "thiruvananthapuram": ["Trivandrum", "Thiruvananthapuram"],
        "tiruchirappalli": ["Tiruchirappalli", "Trichy"],
        "trichy": ["Tiruchirappalli", "Trichy"],
        "noida": ["Noida", "Greater Noida"],
        "gurgaon": ["Gurugram", "Gurgaon"],
    }
    expanded = []
    seen = set()
    for value in values:
        terms = aliases.get(value.strip().lower(), [value.strip()])
        for term in terms:
            if term and term.lower() not in seen:
                seen.add(term.lower())
                expanded.append(term)
    return expanded

def _query_filter_conditions(args, exclude=frozenset()):
    """Build parameterized job filters shared by faceted filter endpoints."""
    conditions = []
    params = []

    role = args.get("role", "").strip()
    job_family = args.get("job_family", "").strip()
    location = args.get("location", "").strip()
    experience = args.get("experience", "").strip()
    salary_min = args.get("salary_min", "").strip()
    salary_max = args.get("salary_max", "").strip()
    salary_currency = args.get("salary_currency", "INR").strip().upper()
    salary_period = args.get("salary_period", "").strip().lower()
    company_text = args.get("company", "").strip()
    search = args.get("search", "").strip()
    skills = args.get("skills", "").strip()
    status = args.get("status", "").strip().lower()
    posted_after = args.get("posted_after", "").strip()
    source_ids = args.get("source_ids", "").strip()
    companies = [value.strip() for value in args.getlist("companies") if value.strip()]

    if "job_family" not in exclude and job_family:
        conditions.append(
            "EXISTS (SELECT 1 FROM job_title_family_map fm "
            "WHERE fm.title_hash = MD5(TRIM(j.title)) AND fm.job_family = %s)"
        )
        params.append(job_family)
    if "role" not in exclude and role:
        conditions.append(
            "EXISTS (SELECT 1 FROM job_title_family_map rfm "
            "WHERE rfm.title_hash = MD5(TRIM(j.title)) AND rfm.role_group = %s)"
        )
        params.append(role)
    if "location" not in exclude and location:
        selected = _expand_location_terms([part.strip() for part in location.split(",") if part.strip()])
        if selected:
            conditions.append("(" + " OR ".join(["j.location LIKE %s"] * len(selected)) + ")")
            params.extend([f"%{part}%" for part in selected])
    if "experience" not in exclude and experience:
        exp_pattern = _experience_match_pattern(experience)
        if exp_pattern:
            # MySQL uses REGEXP; PostgreSQL uses the POSIX regex operator ~.
            regex_operator = "~" if os.getenv("DB_ENGINE", "postgres").strip().lower() in {"postgres", "postgresql", "neon"} else "REGEXP"
            conditions.append(f"j.experience {regex_operator} %s")
            params.append(exp_pattern)
        else:
            conditions.append("LOWER(TRIM(j.experience)) = LOWER(TRIM(%s))")
            params.append(experience)
    if "salary" not in exclude:
        if salary_min:
            try:
                minimum_salary = float(salary_min)
                if minimum_salary < 0: raise ValueError
            except ValueError:
                raise ValueError("salary_min must be a non-negative number")
            conditions.append("j.salary_currency = %s AND j.salary_period = COALESCE(NULLIF(%s, ''), j.salary_period) AND COALESCE(j.salary_max, j.salary_min) >= %s")
            params.extend([salary_currency, salary_period, minimum_salary])
        if salary_max:
            try:
                maximum_salary = float(salary_max)
                if maximum_salary < 0: raise ValueError
            except ValueError:
                raise ValueError("salary_max must be a non-negative number")
            conditions.append("j.salary_currency = %s AND j.salary_period = COALESCE(NULLIF(%s, ''), j.salary_period) AND COALESCE(j.salary_min, j.salary_max) <= %s")
            params.extend([salary_currency, salary_period, maximum_salary])
        if salary_min and salary_max and float(salary_min) > float(salary_max):
            raise ValueError("Minimum salary cannot exceed maximum salary")
    if "company" not in exclude and company_text:
        conditions.append("j.company LIKE %s")
        params.append(f"%{company_text}%")
    if "companies" not in exclude and companies:
        conditions.append("LOWER(TRIM(j.company)) IN (" + ",".join(["LOWER(TRIM(%s))"] * len(companies)) + ")")
        params.extend(companies)
    if "posted_after" not in exclude and posted_after:
        try:
            datetime.strptime(posted_after, "%Y-%m-%d")
        except ValueError:
            raise ValueError("posted_after must use YYYY-MM-DD format")
        conditions.append("j.posted_at >= %s")
        params.append(posted_after)

    if "search" not in exclude and search:
        conditions.append("(j.title LIKE %s OR j.company LIKE %s OR j.description LIKE %s)")
        pattern = f"%{search}%"
        params.extend([pattern, pattern, pattern])
    if "skills" not in exclude and skills:
        conditions.append("CAST(j.skills AS TEXT) LIKE %s")
        params.append(f"%{skills}%")
    if "status" not in exclude and status:
        conditions.append("LOWER(COALESCE(j.status, '')) = %s")
        params.append(status)
    if "sources" not in exclude and "source_ids" in args:
        try:
            ids = [int(value) for value in source_ids.split(",") if value.strip()]
        except ValueError as exc:
            raise ValueError("source_ids must be comma-separated integers") from exc
        if not ids:
            conditions.append("1 = 0")
        else:
            conditions.append("j.source_id IN (" + ",".join(["%s"] * len(ids)) + ")")
            params.extend(ids)

    return conditions, params


def _where_sql(conditions):
    return " WHERE " + " AND ".join(conditions) if conditions else ""


def _ensure_portal_catalog_rows():
    """Idempotently insert only missing catalog sources; never delete job data."""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT LOWER(TRIM(name)) FROM sources")
        existing = {row[0] for row in cursor.fetchall() if row and row[0]}
        missing = [portal for portal in JOB_PORTALS if portal["name"].strip().lower() not in existing]
        for portal in missing:
            cursor.execute(
                "INSERT INTO sources (name, base_url, source_type, is_enabled) VALUES (%s, %s, 'job_website', TRUE)",
                (portal["name"], portal["base_url"]),
            )
        if missing:
            connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


PORTAL_COLLECTOR_GROUP = {
    "CUTSHORT": "COLLECTOR1",
    "FOUNDIT": "COLLECTOR1",
    "GLASSDOOR": "COLLECTOR1",
    "SHINE_COM": "COLLECTOR1",
    "WELLFOUND": "COLLECTOR1",
    "HIRIST": "COLLECTOR2",
    "IIMJOBS": "COLLECTOR2",
    "TIMESJOBS": "COLLECTOR2",
    "FRESHERSWORLD": "COLLECTOR3",
    "JOOBLE": "COLLECTOR3",
    "NATIONAL_CAREER_SERVICE_NCS": "COLLECTOR3",
}


def _portal_has_apify_token(portal_key):
    """Recognize group-specific collector tokens and the legacy shared token."""
    group = PORTAL_COLLECTOR_GROUP.get(str(portal_key or "").upper())
    if group and os.getenv(f"APIFY_API_TOKEN_{group}", "").strip():
        return True
    return bool(os.getenv("APIFY_API_TOKEN", "").strip())


def _portal_configured(portal_key):
    feed_ready = (
        portal_key == "NAUKRI"
        and os.getenv("NAUKRI_ACCESS_APPROVED", "false").lower() == "true"
        and bool(os.getenv("NAUKRI_FEED_URL", "").strip())
    )
    actor_id = (
        os.getenv(f"APIFY_ACTOR_{portal_key}", "").strip()
        or (os.getenv("APIFY_NAUKRI_ACTOR_ID", "").strip() if portal_key == "NAUKRI" else "")
        or DEFAULT_APIFY_ACTORS.get(portal_key, "")
    )
    known_default_inputs = {
        "NAUKRI", "INDEED", "FOUNDIT", "INTERNSHALA", "TIMESJOBS", "WELLFOUND",
        "GLASSDOOR", "HIRIST", "SHINE_COM", "IIMJOBS", "JOOBLE", "FRESHERSWORLD",
        "NATIONAL_CAREER_SERVICE_NCS", "QUIKRJOBS", "CUTSHORT", "JORA", "PLACEMENTINDIA",
    }
    input_ready = portal_key in known_default_inputs or bool(os.getenv(f"APIFY_INPUT_{portal_key}", "").strip())
    actor_ready = (
        os.getenv(f"APIFY_ACCESS_APPROVED_{portal_key}", "false").lower() == "true"
        and _portal_has_apify_token(portal_key)
        and bool(actor_id)
        and input_ready
    )
    return feed_ready or actor_ready


@app.route("/api/user-dashboard", methods=["GET"])
def user_dashboard():
    """Job-seeker dashboard using stored records and a rolling last-24-hours window."""
    connection = None
    cursor = None
    try:
        since_24h = "DATE_SUB(NOW(), INTERVAL 24 HOUR)" if is_mysql() else "(CURRENT_TIMESTAMP - INTERVAL '24 hours')"
        connection = get_connection()
        cursor = connection.cursor(dictionary=True) if is_mysql() else connection.cursor()
        queries = {
            "active_jobs": "SELECT COUNT(*) AS total FROM jobs WHERE LOWER(COALESCE(status, 'active')) NOT IN ('closed','inactive','expired')",
            "jobs_last_24h": f"SELECT COUNT(*) AS total FROM jobs WHERE created_at >= {since_24h}",
            "companies_last_24h": f"SELECT COUNT(DISTINCT TRIM(company)) AS total FROM jobs WHERE created_at >= {since_24h} AND company IS NOT NULL AND TRIM(company) <> ''",
            "locations_last_24h": f"SELECT COUNT(DISTINCT TRIM(location)) AS total FROM jobs WHERE created_at >= {since_24h} AND location IS NOT NULL AND TRIM(location) <> ''",
            "locations": f"SELECT TRIM(location) AS label, COUNT(*) AS total FROM jobs WHERE created_at >= {since_24h} AND location IS NOT NULL AND TRIM(location) <> '' GROUP BY TRIM(location) ORDER BY total DESC LIMIT 6",
            "companies": f"SELECT TRIM(company) AS label, COUNT(*) AS total FROM jobs WHERE created_at >= {since_24h} AND company IS NOT NULL AND TRIM(company) <> '' GROUP BY TRIM(company) ORDER BY MAX(created_at) DESC LIMIT 6",
            "roles": f"SELECT TRIM(title) AS label, COUNT(*) AS total FROM jobs WHERE created_at >= {since_24h} AND title IS NOT NULL AND TRIM(title) <> '' GROUP BY TRIM(title) ORDER BY total DESC LIMIT 6",
            "recent_jobs": f"SELECT title, company, location, created_at FROM jobs WHERE created_at >= {since_24h} ORDER BY created_at DESC LIMIT 8"
        }
        data = {}
        for key, query in queries.items():
            cursor.execute(query)
            rows = cursor.fetchall()
            if key in {"active_jobs", "jobs_last_24h", "companies_last_24h", "locations_last_24h"}:
                row = rows[0] if rows else {}
                data[key] = int((row.get("total") if isinstance(row, dict) else row[0]) or 0)
            elif key == "recent_jobs":
                data[key] = [{"title": (r.get("title") if isinstance(r, dict) else r[0]), "company": (r.get("company") if isinstance(r, dict) else r[1]), "location": (r.get("location") if isinstance(r, dict) else r[2]), "created_at": (r.get("created_at") if isinstance(r, dict) else r[3])} for r in rows]
            else:
                data[key] = [{"label": (r.get("label") if isinstance(r, dict) else r[0]), "total": int((r.get("total") if isinstance(r, dict) else r[1]) or 0)} for r in rows]
        return jsonify(data)
    except DATABASE_ERRORS:
        app.logger.exception("Failed to load user dashboard")
        return jsonify({"error": "Unable to load job-market insights."}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route("/api/dashboard/stats", methods=["GET"])
def dashboard_stats():
    """Return database-backed job and scraper activity metrics for the dashboard."""
    if is_mysql():
        week_start = "DATE_SUB(CURRENT_DATE, INTERVAL WEEKDAY(CURRENT_DATE) DAY)"
        month_start = "DATE_FORMAT(CURRENT_DATE, '%Y-%m-01')"
        next_month = "DATE_ADD(DATE_FORMAT(CURRENT_DATE, '%Y-%m-01'), INTERVAL 1 MONTH)"
        tomorrow = "DATE_ADD(CURRENT_DATE, INTERVAL 1 DAY)"
    else:
        week_start = "DATE_TRUNC('week', CURRENT_DATE)"
        month_start = "DATE_TRUNC('month', CURRENT_DATE)"
        next_month = "DATE_TRUNC('month', CURRENT_DATE) + INTERVAL '1 month'"
        tomorrow = "CURRENT_DATE + INTERVAL '1 day'"
    try:
        totals = fetch_one(f"""
            SELECT
                SUM(CASE WHEN LOWER(COALESCE(j.status, 'active')) NOT IN ('closed', 'inactive', 'expired') THEN 1 ELSE 0 END) AS total_jobs,
                SUM(CASE WHEN j.created_at >= CURRENT_DATE AND j.created_at < {tomorrow} THEN 1 ELSE 0 END) AS jobs_added_today,
                SUM(CASE WHEN j.created_at >= {week_start} THEN 1 ELSE 0 END) AS jobs_added_this_week,
                SUM(CASE WHEN j.created_at >= {month_start} AND j.created_at < {next_month} THEN 1 ELSE 0 END) AS jobs_added_this_month,
                SUM(CASE WHEN j.salary_min IS NOT NULL OR j.salary_max IS NOT NULL THEN 1 ELSE 0 END) AS jobs_with_salary
            FROM jobs j
        """) or {}
        run_metrics = fetch_one(f"""
            SELECT
                COALESCE(SUM(CASE WHEN cr.started_at >= CURRENT_DATE THEN cr.records_found ELSE 0 END), 0) AS scraper_found_today,
                COALESCE(SUM(CASE WHEN cr.started_at >= {week_start} THEN cr.records_found ELSE 0 END), 0) AS scraper_found_this_week,
                COALESCE(SUM(CASE WHEN cr.started_at >= {month_start} AND cr.started_at < {next_month} THEN cr.records_found ELSE 0 END), 0) AS scraper_found_this_month,
                COALESCE(SUM(CASE WHEN cr.started_at >= CURRENT_DATE THEN cr.records_inserted ELSE 0 END), 0) AS scraper_inserted_today,
                COALESCE(SUM(CASE WHEN cr.started_at >= CURRENT_DATE AND cr.status = 'failed' THEN 1 ELSE 0 END), 0) AS failed_runs_today
            FROM collection_runs cr
        """) or {}
        # Count sources separately from jobs so the source metric does not join and
        # aggregate the entire jobs table on every dashboard refresh.
        source_metrics = fetch_one("""
            SELECT
                (SELECT COUNT(DISTINCT LOWER(TRIM(s.name)))
                 FROM jobs j
                 INNER JOIN sources s ON s.id = j.source_id
                 WHERE NULLIF(TRIM(s.name), '') IS NOT NULL
                   AND LOWER(TRIM(s.name)) NOT IN ('csv import', 'all sources')) AS total_sources,
                (SELECT COUNT(DISTINCT j.source_id) FROM jobs j
                 INNER JOIN sources s ON s.id = j.source_id
                 WHERE LOWER(TRIM(s.name)) IN ('naukri','indeed','linkedin','linkedin jobs','glassdoor','foundit','timesjobs','bayt','internshala','wellfound','unstop','hirist','freshersworld','shine','cutshort','instahyre','amazon','deloitte','infosys')) AS enabled_sources
        """) or {}
        by_source = fetch_all("""
            SELECT s.name AS source_name,
                   COUNT(DISTINCT j.id) AS job_count,
                   (SELECT MAX(cr3.started_at) FROM collection_runs cr3 WHERE cr3.source_id = s.id) AS last_run_at,
                   (SELECT cr2.status FROM collection_runs cr2 WHERE cr2.source_id = s.id ORDER BY cr2.started_at DESC, cr2.id DESC LIMIT 1) AS last_run_status
            FROM sources s
            LEFT JOIN jobs j ON j.source_id = s.id
            WHERE LOWER(TRIM(s.name)) NOT IN ('csv import', 'all sources')
            GROUP BY s.id, s.name
            ORDER BY job_count DESC, s.name ASC
            LIMIT 8
        """)
        recent_runs = fetch_all("""
            SELECT cr.id, s.name AS source_name, cr.started_at, cr.finished_at, cr.status,
                   cr.records_found, cr.records_inserted, cr.records_updated, cr.error_message
            FROM collection_runs cr
            LEFT JOIN sources s ON s.id = cr.source_id
            ORDER BY cr.started_at DESC
            LIMIT 8
        """)
        return jsonify({"metrics": {**totals, **run_metrics, **source_metrics}, "top_sources": by_source, "recent_runs": recent_runs})
    except DATABASE_ERRORS:
        app.logger.exception("Failed to load dashboard statistics")
        return jsonify({"error": "Unable to load dashboard statistics. Confirm the salary migration has been applied."}), 500



@app.route("/api/admin/analytics", methods=["GET"])
def admin_analytics():
    """Compact job-market aggregates for the administrator dashboard."""
    if not is_logged_in() or str(session.get("role", "")).lower() != "admin":
        return jsonify({"error": "Admin access required"}), 403
    try:
        total = fetch_one("SELECT COUNT(*) AS total FROM jobs") or {}
        # Reuse the same title-to-family mapping used by the Job Portal filters.
        _ensure_job_family_mapping()
        fresher = fetch_one("""
            SELECT COUNT(*) AS total FROM jobs
            WHERE LOWER(COALESCE(experience, '')) ~
            '(^|[^0-9])(0([.]0+)?|fresher(s)?|entry[ -]?level|no experience)([^0-9]|$)'
        """) if not is_mysql() else fetch_one("""
            SELECT COUNT(*) AS total FROM jobs
            WHERE LOWER(COALESCE(experience, '')) REGEXP
            '(^|[^0-9])(0([.]0+)?|fresher(s)?|entry[ -]?level|no experience)([^0-9]|$)'
        """)
        experienced = fetch_one("""
            SELECT COUNT(*) AS total FROM jobs
            WHERE experience IS NOT NULL AND TRIM(experience) <> ''
              AND LOWER(TRIM(experience)) NOT IN ('fresher','freshers','entry level','entry-level','no experience','0','0.0')
        """) or {}
        portals = fetch_all("""
            SELECT COALESCE(NULLIF(TRIM(s.name), ''), 'Unknown portal') AS label,
                   COUNT(j.id) AS total
            FROM jobs j LEFT JOIN sources s ON s.id = j.source_id
            GROUP BY COALESCE(NULLIF(TRIM(s.name), ''), 'Unknown portal')
            ORDER BY total DESC, label ASC
        """)
        # Only the 15 portal integrations configured in the portal catalog are
        # represented in the portal donut/card; company-career feeds are separate.
        portal_allowlist = {
            "naukri", "indeed", "linkedin", "linkedin jobs", "glassdoor", "foundit",
            "timesjobs", "bayt", "internshala", "wellfound", "unstop", "hirist",
            "freshersworld", "shine", "cutshort", "instahyre"
        }
        portals = [row for row in portals if str(row.get("label", "")).strip().lower() in portal_allowlist]
        portals.sort(key=lambda row: (-int(row.get("total") or 0), str(row.get("label") or "").lower()))

        companies = fetch_all("""
            SELECT COALESCE(NULLIF(TRIM(company), ''), 'Unknown company') AS label,
                   COUNT(*) AS total
            FROM jobs GROUP BY COALESCE(NULLIF(TRIM(company), ''), 'Unknown company')
            ORDER BY total DESC, label ASC
        """)
        locations = fetch_all("""
            SELECT COALESCE(NULLIF(TRIM(location), ''), 'Unknown location') AS label,
                   COUNT(*) AS total
            FROM jobs GROUP BY COALESCE(NULLIF(TRIM(location), ''), 'Unknown location')
            ORDER BY total DESC, label ASC
        """)
        family_counts = fetch_all("""
            SELECT COALESCE(NULLIF(TRIM(m.job_family), ''), 'Other & Needs Review') AS label,
                   COUNT(j.id) AS total
            FROM jobs j
            LEFT JOIN job_title_family_map m
              ON TRIM(j.title) = m.original_title
            GROUP BY COALESCE(NULLIF(TRIM(m.job_family), ''), 'Other & Needs Review')
        """)
        family_count_lookup = {
            str(row.get("label") or "").strip(): int(row.get("total") or 0)
            for row in family_counts
        }
        job_families = [
            {"label": family, "total": family_count_lookup.get(family, 0)}
            for family in JOB_FAMILY_NAMES
        ]
        # Normalize common India location variants into consistent city buckets.
        normalized_locations = {}

        for row in locations:
            raw = str(row.get("label") or "").strip()
            key = re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()
            key = " ".join(key.split())
            count = int(row.get("total") or 0)
            words = set(key.split())

            # Omit country-wide/unknown records from city distribution.
            if key in {
                "india", "in", "all india", "pan india",
                "unknown location", "remote india", "india remote"
            }:
                continue

            if words.intersection({"bengaluru", "bangalore"}) or key in {
                "ka", "karnataka", "ka india", "karnataka india"
            }:
                label = "Bengaluru, Karnataka, India"
            elif "chennai" in words or key in {
                "tn", "tamil nadu", "tn india", "tamil nadu india"
            }:
                label = "Chennai, Tamil Nadu, India"
            elif "hyderabad" in words or key in {
                "ts", "telangana", "ts india", "telangana india"
            }:
                label = "Hyderabad, Telangana, India"
            elif "mumbai" in words or key in {
                "mh", "maharashtra", "mh india", "maharashtra india"
            }:
                label = "Mumbai, Maharashtra, India"
            elif key in {
                "delhi", "new delhi", "ncr", "delhi ncr",
                "new delhi india", "delhi india"
            }:
                label = "Delhi NCR, India"
            elif words.intersection({"gurgaon", "gurugram"}):
                label = "Gurugram, Haryana, India"
            else:
                label = raw

            normalized_locations[label] = normalized_locations.get(label, 0) + count

        locations = [
            {"label": label, "total": count}
            for label, count in normalized_locations.items()
        ]
        locations.sort(key=lambda row: (-row["total"], row["label"].lower()))

        return jsonify({
            "total_jobs": int(total.get("total") or 0),
            "fresher_jobs": int((fresher or {}).get("total") or 0),
            "experienced_jobs": int(experienced.get("total") or 0),
            "portals": portals, "portal_source_count": int((fetch_one("""
                SELECT COUNT(DISTINCT LOWER(TRIM(s.name))) AS total
                FROM jobs j
                INNER JOIN sources s ON s.id = j.source_id
                WHERE NULLIF(TRIM(s.name), '') IS NOT NULL
                  AND LOWER(TRIM(s.name)) NOT IN ('csv import', 'all sources')
            """) or {}).get("total") or 0),
            "companies": companies, "locations": locations,
            "job_families": job_families,
            "job_family_total": sum(row["total"] for row in job_families)
        })
    except DATABASE_ERRORS:
        app.logger.exception("Admin analytics query failed")
        return jsonify({"error": "Unable to load admin analytics"}), 500

@app.route("/api/dashboard/trend", methods=["GET"])
def dashboard_trend():
    """Return a complete 14-day daily count series from actual job records."""
    try:
        if is_mysql():
            rows = fetch_all("""
                SELECT DATE(created_at) AS day, COUNT(*) AS job_count
                FROM jobs WHERE created_at >= DATE_SUB(CURRENT_DATE, INTERVAL 13 DAY)
                  AND created_at < DATE_ADD(CURRENT_DATE, INTERVAL 1 DAY)
                GROUP BY DATE(created_at) ORDER BY day
            """)
        else:
            rows = fetch_all("""
                SELECT CAST(created_at AS DATE) AS day, COUNT(*) AS job_count
                FROM jobs WHERE created_at >= CURRENT_DATE - INTERVAL '13 days'
                  AND created_at < CURRENT_DATE + INTERVAL '1 day'
                GROUP BY CAST(created_at AS DATE) ORDER BY day
            """)
        counts = {str(row['day'])[:10]: int(row.get('job_count') or 0) for row in rows}
        today = date.today()
        series = [{"date": (today.fromordinal(today.toordinal()-offset)).isoformat(),
                   "count": counts.get((today.fromordinal(today.toordinal()-offset)).isoformat(), 0)}
                  for offset in range(13, -1, -1)]
        return jsonify({"data": series})
    except DATABASE_ERRORS:
        app.logger.exception("Failed to load dashboard trend")
        return jsonify({"error": "Unable to load 14-day job trend"}), 500


@app.route("/api/locations", methods=["GET"])
def list_locations():
    """Return location facets constrained by all currently selected filters."""
    try:
        _ensure_job_family_mapping()
        conditions, params = _query_filter_conditions(request.args, exclude={"location"})
        where_clause = _where_sql(conditions)
        query = "SELECT DISTINCT TRIM(j.location) AS location FROM jobs j" + where_clause
        query += (" AND " if conditions else " WHERE ") + "j.location IS NOT NULL AND TRIM(j.location) <> '' ORDER BY location"
        rows = fetch_all(query, tuple(params))
        values = [row["location"] for row in rows if row.get("location")]
        return jsonify({"data": values, "count": len(values)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except DATABASE_ERRORS:
        app.logger.exception("Failed to retrieve job locations")
        return jsonify({"error": "Unable to retrieve locations from the database"}), 500


@app.route("/api/company-career-sources", methods=["GET"])
def list_company_career_sources():
    """List company career sources; avoid rewriting every source on each GET."""
    try:
        # Seed only genuinely missing catalog rows. The previous implementation
        # updated every company source on every page load, creating avoidable
        # writes and network round-trips against remote hosted databases.
        existing = fetch_all("SELECT name FROM sources")
        existing_names = {str(row.get("name", "")).strip().lower() for row in existing}
        missing = [
            source for source in COMPANY_CAREER_SOURCES
            if source["name"].strip().lower() not in existing_names
        ]
        if missing:
            connection = get_connection()
            cursor = connection.cursor()
            try:
                insert_sql = (
                    "INSERT INTO sources (name, base_url, source_type, is_enabled) "
                    "VALUES (%s, %s, %s, %s)"
                )
                cursor.executemany(
                    insert_sql,
                    [
                        (source["name"], source["base_url"], "company_career", False)
                        for source in missing
                    ],
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
                connection.close()

        data = fetch_all(
            "SELECT s.id, s.name, s.base_url, s.source_type, s.is_enabled, "
            "COUNT(DISTINCT j.id) AS job_count FROM sources s "
            "LEFT JOIN jobs j ON j.source_id=s.id WHERE s.source_type=%s "
            "GROUP BY s.id, s.name, s.base_url, s.source_type, s.is_enabled ORDER BY s.name",
            ("company_career",),
        )
        source_by_name = {source["name"].strip().lower(): source for source in COMPANY_CAREER_SOURCES}
        data = [item for item in data if str(item.get("name", "")).strip().lower() in source_by_name]
        for item in data:
            catalog_item = source_by_name.get(str(item.get("name", "")).strip().lower())
            key = catalog_item["key"] if catalog_item else ""
            item["company"] = catalog_item.get("company", "") if catalog_item else ""
            item["key"] = key
            item["career_url"] = catalog_item.get("base_url", item.get("base_url", "")) if catalog_item else item.get("base_url", "")
            item["verified_collector"] = bool(catalog_item and catalog_item.get("verified_collector"))
            actor_configured = bool(
                key
                and os.getenv("APIFY_API_TOKEN", "").strip()
                and os.getenv(f"APIFY_ACTOR_COMPANY_{key}", "").strip()
                and os.getenv(f"APIFY_INPUT_COMPANY_{key}", "").strip()
                and os.getenv(f"APIFY_ACCESS_APPROVED_COMPANY_{key}", "false").strip().lower() == "true"
            )
            item["collector_configured"] = bool(item["verified_collector"] or actor_configured)
            item["structured_collector_available"] = bool(item["verified_collector"])
            item["collector_method"] = {
                "INFOSYS": "Official public careers search API",
                "WIPRO": "Official SuccessFactors RSS with job-search HTML fallback",
                "AMAZON": "Official Amazon Jobs search JSON with HTML fallback",
                "ACCENTURE": "Official Accenture careers search listing parser",
                "DELOITTE": "Official-site JobPosting JSON-LD; optional reviewed Apify Actor",
            }.get(key, "Official careers source")
        # Hide companies whose collector has not been validated or explicitly configured.
        data = [item for item in data if item.get("collector_configured")]
        return jsonify({
            "data": data,
            "count": len(data),
            "note": "Only validated official-site collectors or explicitly configured, approved company Actors are shown.",
        })
    except DATABASE_ERRORS:
        app.logger.exception("Failed to list company career sources")
        return jsonify({"error": "Unable to load company career sources"}), 500


@app.route("/api/companies", methods=["GET"])
def list_companies():
    """Return companies with exact stored job counts, respecting other filters."""
    try:
        _ensure_job_family_mapping()
        conditions, params = _query_filter_conditions(request.args, exclude={"company", "companies"})
        where_clause = _where_sql(conditions)
        query = "SELECT TRIM(j.company) AS name, COUNT(DISTINCT j.id) AS job_count FROM jobs j" + where_clause
        query += (" AND " if conditions else " WHERE ") + "j.company IS NOT NULL AND TRIM(j.company) <> '' GROUP BY TRIM(j.company) ORDER BY name"
        rows = fetch_all(query, tuple(params))
        return jsonify({"data": rows, "count": len(rows)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except DATABASE_ERRORS:
        app.logger.exception("Failed to retrieve companies")
        return jsonify({"error": "Unable to retrieve companies from the database"}), 500


def _canonical_experience(value):
    """Normalize scraped experience text into the curated dynamic UI buckets."""
    text = str(value or "").strip().lower()
    if not text or text in {"0", "fresher", "freshers", "entry level", "entry-level", "no experience"}:
        return "Freshers (0–2)" if text else None

    nums = re.findall(r"\d+(?:\.\d+)?", text)
    if not nums:
        return None
    a = float(nums[0])
    is_range = len(nums) >= 2 and any(token in text for token in ("-", "–", "—", "to"))
    b = float(nums[1]) if is_range else None

    # Curated experience choices; raw scraped labels are mapped to these.
    if b is not None:
        if a == 0:
            if b <= 1: return "0–1 Years"
            if b <= 2: return "0–2 Years"
            return "0–5 Years"
        if a == 1 and b <= 5: return "1–5 Years"
        if a == 3 and b <= 5: return "3–5 Years"
        if a == 5 and b <= 7: return "5–7 Years"
        if a == 6 and b <= 8: return "6–8 Years"
        if a == 7 and b <= 9: return "7–9 Years"
        if a >= 10 and b <= 20: return "10–20 Years"
        if a >= 20 or b >= 20: return "20+ Years"
        # Broad nonstandard ranges map to the closest available standard band.
        if a <= 1 and b <= 5: return "1–5 Years"
        if a <= 3 and b <= 5: return "3–5 Years"
        if a <= 5 and b <= 7: return "5–7 Years"
        if a <= 7 and b <= 9: return "7–9 Years"
        if a <= 10 and b <= 20: return "10–20 Years"
        return None

    if a == 0: return "Freshers (0–2)"
    if a in (12, 13, 14, 15, 18): return f"{int(a)} Years"
    if a >= 20: return "20+ Years"
    return None


def _experience_match_pattern(canonical):
    """Match raw labels that normalize to a selected curated experience bucket."""
    patterns = {
        "Freshers (0–2)": r"(^|[^0-9])(0([.]0+)?(?![[:space:]]*(to|-|–|—)[[:space:]]*[1-9])|fresher(s)?|entry[ -]?level|no experience)([^0-9]|$)|(^|[^0-9])0([.]0+)?[[:space:]]*(to|-|–|—)[[:space:]]*([.]0+)?([1-2])([^0-9]|$)",
        "0–1 Years": r"(^|[^0-9])0([.]0+)?[[:space:]]*(to|-|–|—)[[:space:]]*([.]0+)?1([^0-9]|$)",
        "0–2 Years": r"(^|[^0-9])0([.]0+)?[[:space:]]*(to|-|–|—)[[:space:]]*([.]0+)?([1-2])([^0-9]|$)",
         "1–2 Years": r"(^|[^0-9])1([.]0+)?[[:space:]]*(to|-|–|—)[[:space:]]*([.]0+)?2([^0-9]|$)",
        "2–3 Years": r"(^|[^0-9])2([.]0+)?[[:space:]]*(to|-|–|—)[[:space:]]*([.]0+)?3([^0-9]|$)",
        "5–8 Years": r"(^|[^0-9])5([.]0+)?[[:space:]]*(to|-|–|—)[[:space:]]*([6-8])([^0-9]|$)",
        "8–12 Years": r"(^|[^0-9])8([.]0+)?[[:space:]]*(to|-|–|—)[[:space:]]*(9|10|11|12)([^0-9]|$)",
        "12+ Years": r"(^|[^0-9])(12|1[3-9]|[2-9][0-9])([.]0+)?([[:space:]]*(\\+|plus|years?|yrs?|yr))?([^0-9]|$)",
        "1–5 Years": r"(^|[^0-9])1([.]0+)?[[:space:]]*(to|-|–|—)[[:space:]]*([2-5])([^0-9]|$)",
        "3–5 Years": r"(^|[^0-9])3([.]0+)?[[:space:]]*(to|-|–|—)[[:space:]]*([4-5])([^0-9]|$)",
        "5–7 Years": r"(^|[^0-9])5([.]0+)?[[:space:]]*(to|-|–|—)[[:space:]]*([6-7])([^0-9]|$)",
        "6–8 Years": r"(^|[^0-9])6([.]0+)?[[:space:]]*(to|-|–|—)[[:space:]]*([7-8])([^0-9]|$)",
        "7–9 Years": r"(^|[^0-9])7([.]0+)?[[:space:]]*(to|-|–|—)[[:space:]]*([8-9])([^0-9]|$)",
        "10–20 Years": r"(^|[^0-9])10([.]0+)?[[:space:]]*(to|-|–|—)[[:space:]]*(1[1-9]|20)([^0-9]|$)",
        "12 Years": r"(^|[^0-9])12([.]0+)?([[:space:]]*(years?|yrs?|yr))?([^0-9]|$)",
        "13 Years": r"(^|[^0-9])13([.]0+)?([[:space:]]*(years?|yrs?|yr))?([^0-9]|$)",
        "14 Years": r"(^|[^0-9])14([.]0+)?([[:space:]]*(years?|yrs?|yr))?([^0-9]|$)",
        "15 Years": r"(^|[^0-9])15([.]0+)?([[:space:]]*(years?|yrs?|yr))?([^0-9]|$)",
        "18 Years": r"(^|[^0-9])18([.]0+)?([[:space:]]*(years?|yrs?|yr))?([^0-9]|$)",
        "20+ Years": r"(^|[^0-9])(20|2[1-9]|[3-9][0-9])([.]0+)?([[:space:]]*(\+|plus|years?|yrs?|yr))?([^0-9]|$)",
    }
    return patterns.get(str(canonical or "").strip())


@app.route("/api/salary-options", methods=["GET"])
def salary_options():
    """Return salary bands available in the currently filtered job set."""
    try:
        _ensure_job_family_mapping()
        conditions, params = _query_filter_conditions(request.args, exclude={"salary"})
        where = _where_sql(conditions)
        sql = f"""
            SELECT
              SUM(CASE WHEN COALESCE(j.salary_max,j.salary_min) >= 0
                    AND COALESCE(j.salary_min,j.salary_max) <= 300000 THEN 1 ELSE 0 END) AS b0,
              SUM(CASE WHEN COALESCE(j.salary_max,j.salary_min) >= 300000
                    AND COALESCE(j.salary_min,j.salary_max) <= 500000 THEN 1 ELSE 0 END) AS b1,
              SUM(CASE WHEN COALESCE(j.salary_max,j.salary_min) >= 500000
                    AND COALESCE(j.salary_min,j.salary_max) <= 800000 THEN 1 ELSE 0 END) AS b2,
              SUM(CASE WHEN COALESCE(j.salary_max,j.salary_min) >= 800000
                    AND COALESCE(j.salary_min,j.salary_max) <= 1200000 THEN 1 ELSE 0 END) AS b3,
              SUM(CASE WHEN COALESCE(j.salary_max,j.salary_min) >= 1200000
                    AND COALESCE(j.salary_min,j.salary_max) <= 2000000 THEN 1 ELSE 0 END) AS b4,
              SUM(CASE WHEN COALESCE(j.salary_max,j.salary_min) >= 2000000 THEN 1 ELSE 0 END) AS b5
            FROM jobs j {where}
              {"AND" if conditions else "WHERE"} j.salary_currency = 'INR'
              AND LOWER(COALESCE(j.salary_period,'')) IN ('year','annual','')
              AND (j.salary_min IS NOT NULL OR j.salary_max IS NOT NULL)
        """
        row = fetch_one(sql, tuple(params)) or {}
        counts = [int(row.get(f"b{i}") or 0) for i in range(6)]
        bands = [
            {"value":"0-300000","label":"Up to ₹3 LPA"},
            {"value":"300000-500000","label":"₹3–5 LPA"},
            {"value":"500000-800000","label":"₹5–8 LPA"},
            {"value":"800000-1200000","label":"₹8–12 LPA"},
            {"value":"1200000-2000000","label":"₹12–20 LPA"},
            {"value":"2000000-","label":"₹20 LPA and above"},
        ]
        available = [
            {**band, "job_count": count}
            for band, count in zip(bands, counts) if count > 0
        ]
        return jsonify({"data": available})
    except DATABASE_ERRORS:
        app.logger.exception("Failed to load salary filter options")
        return jsonify({"error":"Unable to load salary options"}), 500

@app.route("/api/filter-options", methods=["GET"])
def filter_options():
    """Return dependent job-family, title, location, and experience facets."""
    try:
        _ensure_job_family_mapping()

        # Family options ignore the family/title selection itself, but respect
        # every other active filter (source, company, location, experience, salary).
        family_conditions, family_params = _query_filter_conditions(
            request.args, exclude={"job_family", "role"}
        )
        family_where = _where_sql(family_conditions)
        family_query = (
            "SELECT fm.job_family AS value, COUNT(*) AS job_count "
            "FROM jobs j JOIN job_title_family_map fm "
            "ON fm.title_hash = MD5(TRIM(j.title))"
            + family_where
            + (" AND " if family_conditions else " WHERE ")
            + "j.title IS NOT NULL AND TRIM(j.title) <> '' "
            "GROUP BY fm.job_family ORDER BY job_count DESC, fm.job_family"
        )

        # Return every distinct job title belonging to the selected family.
        # Group only case/outer-whitespace variants so the dropdown does not
        # show duplicate spellings, while preserving the real source title.
        # All active filters except role and family are retained; family is
        # applied through the title-family map join below.
        role_conditions, role_params = _query_filter_conditions(
            request.args, exclude={"role", "job_family"}
        )
        role_where = _where_sql(role_conditions)
        selected_family = request.args.get("job_family", "").strip()
        if selected_family:
            role_conditions.append("fm.job_family = %s")
            role_params.append(selected_family)
            role_where = _where_sql(role_conditions)
        roles_query = (
            "SELECT fm.role_group AS value, COUNT(*) AS job_count "
            "FROM jobs j "
            "JOIN job_title_family_map fm "
            "ON fm.title_hash = MD5(TRIM(j.title))"
            + role_where
            + (" AND " if role_conditions else " WHERE ")
            + "j.title IS NOT NULL AND TRIM(j.title) <> '' "
            "GROUP BY fm.role_group "
            "ORDER BY job_count DESC, value"
        )

        exp_conditions, exp_params = _query_filter_conditions(
            request.args, exclude={"experience"}
        )
        loc_conditions, loc_params = _query_filter_conditions(
            request.args, exclude={"location"}
        )
        status_conditions, status_params = _query_filter_conditions(
            request.args, exclude={"status"}
        )
        status_query = (
            "SELECT LOWER(TRIM(j.status)) AS value, COUNT(*) AS job_count FROM jobs j"
            + _where_sql(status_conditions)
            + (" AND " if status_conditions else " WHERE ")
            + "j.status IS NOT NULL AND TRIM(j.status) <> '' "
            "GROUP BY LOWER(TRIM(j.status)) ORDER BY value"
        )
        exp_query = (
            "SELECT TRIM(j.experience) AS value, COUNT(*) AS job_count FROM jobs j"
            + _where_sql(exp_conditions)
            + (" AND " if exp_conditions else " WHERE ")
            + "j.experience IS NOT NULL AND TRIM(j.experience) <> '' "
            "GROUP BY TRIM(j.experience)"
        )
        loc_query = (
            "SELECT TRIM(j.location) AS value, COUNT(*) AS job_count FROM jobs j"
            + _where_sql(loc_conditions)
            + (" AND " if loc_conditions else " WHERE ")
            + "j.location IS NOT NULL AND TRIM(j.location) <> '' "
            "GROUP BY TRIM(j.location) ORDER BY value"
        )

        families = fetch_all(family_query, tuple(family_params))
        roles = (
            fetch_all(roles_query, tuple(role_params))
            if request.args.get("job_family", "").strip()
            else []
        )
        experiences = fetch_all(exp_query, tuple(exp_params))
        locations = fetch_all(loc_query, tuple(loc_params))
        statuses = fetch_all(status_query, tuple(status_params))

        def experience_key(item):
            value = str(item.get("value") or "")
            numbers = [int(number) for number in re.findall(r"\d+", value)]
            return (numbers[0] if numbers else 999,
                    numbers[1] if len(numbers) > 1 else numbers[0] if numbers else 999,
                    value.lower())

        experience_groups = OrderedDict()
        for item in sorted(experiences, key=experience_key):
            raw_value = str(item.get("value") or "").strip()
            canonical = _canonical_experience(raw_value)
            if canonical:
                bucket = experience_groups.setdefault(canonical, {"value": canonical, "job_count": 0})
                bucket["job_count"] += int(item.get("job_count") or 0)

        experience_order = [
            "Freshers (0–2)", "0–1 Years", "0–2 Years", "1–2 Years",
            "2–3 Years", "1–5 Years", "3–5 Years", "5–7 Years",
            "5–8 Years", "6–8 Years", "7–9 Years", "8–12 Years",
            "10–20 Years", "12+ Years", "12 Years", "13 Years",
            "14 Years", "15 Years", "18 Years", "20+ Years",
        ]
        ordered_experiences = [
            experience_groups[name] for name in experience_order
            if name in experience_groups
        ]

        return jsonify({
            "families": families,
            "roles": roles,
            "experiences": ordered_experiences,
            "locations": [item["value"] for item in locations if item.get("value")],
            "statuses": statuses,
        })
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except DATABASE_ERRORS:
        app.logger.exception("Failed to retrieve dynamic filter options")
        return jsonify({"error": "Unable to retrieve filter options"}), 500


@app.route("/api/jobs", methods=["GET"])

def list_jobs():

    try:
        _ensure_job_family_mapping()

        # Pagination parameters.

        try:

            page = max(1, int(request.args.get("page", 1)))

            per_page = int(request.args.get("per_page", 10))

        except ValueError:

            return jsonify({

                "error": "page and per_page must be integers"

            }), 400

        if per_page < 1 or per_page > 100:

            return jsonify({

                "error": "per_page must be between 1 and 100"

            }), 400

        # Search and filter parameters.

        search = request.args.get("search", "").strip()

        role = request.args.get("role", "").strip()
        job_family = request.args.get("job_family", "").strip()

        location = request.args.get("location", "").strip()

        company = request.args.get("company", "").strip()

        source_id = request.args.get("source_id", "").strip()

        source_ids = request.args.get("source_ids", "").strip()

        experience = request.args.get("experience", "").strip()
        salary_min = request.args.get("salary_min", "").strip()
        salary_max = request.args.get("salary_max", "").strip()
        salary_currency = request.args.get("salary_currency", "").strip().upper()
        salary_period = request.args.get("salary_period", "").strip().lower()
        salary_currency = request.args.get("salary_currency", "INR").strip().upper()
        salary_period = request.args.get("salary_period", "").strip().lower()
        skills = request.args.get("skills", "").strip()
        posted_after = request.args.get("posted_after", "").strip()
        status = request.args.get("status", "").strip().lower()

        conditions = []

        params = []

        if job_family:
            conditions.append(
                "EXISTS (SELECT 1 FROM job_title_family_map fm "
                "WHERE fm.title_hash = MD5(TRIM(j.title)) AND fm.job_family = %s)"
            )
            params.append(job_family)

        if role:
            conditions.append(
                "EXISTS (SELECT 1 FROM job_title_family_map rfm "
                "WHERE rfm.title_hash = MD5(TRIM(j.title)) AND rfm.role_group = %s)"
            )
            params.append(role)

        if search:
            conditions.append(
                "(j.title LIKE %s OR j.company LIKE %s "
                "OR j.description LIKE %s)"
            )
            pattern = f"%{search}%"
            params.extend([pattern, pattern, pattern])

        if location:

            selected_locations = _expand_location_terms(
                [part.strip() for part in location.split(",") if part.strip()]
            )

            if selected_locations:
                location_clauses = ["j.location LIKE %s" for _ in selected_locations]
                params.extend([f"%{selected_location}%" for selected_location in selected_locations])
                conditions.append("(" + " OR ".join(location_clauses) + ")")

        if company:

            conditions.append("j.company LIKE %s")

            params.append(f"%{company}%")

        selected_companies = [value.strip() for value in request.args.getlist("companies") if value.strip()]

        if selected_companies:

            conditions.append("LOWER(TRIM(j.company)) IN (" + ",".join(["LOWER(TRIM(%s))"] * len(selected_companies)) + ")")

            params.extend(selected_companies)

        if experience:
            experience_pattern = _experience_match_pattern(experience)
            if experience_pattern:
                conditions.append("COALESCE(TRIM(j.experience), '') ~ %s")
                params.append(experience_pattern)
            else:
                conditions.append("LOWER(TRIM(j.experience)) = LOWER(TRIM(%s))")
                params.append(experience)
        if salary_min or salary_max:
            try:
                min_value = float(salary_min) if salary_min else None
                max_value = float(salary_max) if salary_max else None
                if (min_value is not None and min_value < 0) or (max_value is not None and max_value < 0): raise ValueError
                if min_value is not None and max_value is not None and min_value > max_value: raise ValueError
            except ValueError:
                return jsonify({"error": "Salary values must be non-negative numbers and minimum cannot exceed maximum"}), 400
            if min_value is not None:
                conditions.append("j.salary_currency = %s AND (%s = '' OR j.salary_period = %s) AND COALESCE(j.salary_max, j.salary_min) >= %s")
                params.extend([salary_currency, salary_period, salary_period, min_value])
            if max_value is not None:
                conditions.append("j.salary_currency = %s AND (%s = '' OR j.salary_period = %s) AND COALESCE(j.salary_min, j.salary_max) <= %s")
                params.extend([salary_currency, salary_period, salary_period, max_value])
        if skills:
            conditions.append("CAST(j.skills AS TEXT) LIKE %s")
            params.append(f"%{skills}%")
        if posted_after:
            try: datetime.strptime(posted_after, "%Y-%m-%d")
            except ValueError: return jsonify({"error": "posted_after must use YYYY-MM-DD format"}), 400
            conditions.append("j.posted_at >= %s")
            params.append(posted_after)
        if status:
            conditions.append("LOWER(j.status) = %s")
            params.append(status)

        if "source_ids" in request.args:

            try:

                selected_source_ids = [int(value) for value in source_ids.split(",") if value.strip()]

            except ValueError:

                return jsonify({"error": "source_ids must be comma-separated integers"}), 400

            if not selected_source_ids:

                # An explicit empty selection means no website is selected: return zero jobs.

                conditions.append("1 = 0")

            else:

                conditions.append("j.source_id IN (" + ",".join(["%s"] * len(selected_source_ids)) + ")")

                params.extend(selected_source_ids)

        if source_id:

            try:

                source_id = int(source_id)

            except ValueError:

                return jsonify({

                    "error": "source_id must be an integer"

                }), 400

            conditions.append("j.source_id = %s")

            params.append(source_id)

        where_clause = (

            " WHERE " + " AND ".join(conditions)

            if conditions else ""

        )

        count_result = fetch_one(

            "SELECT COUNT(*) AS total FROM jobs j" + where_clause,

            tuple(params),

        )

        total = count_result["total"]

        query = """

            SELECT

                j.id,

                j.source_id,

                s.name AS source_name,

                j.external_id,

                j.title,

                j.company,

                j.location,

                j.experience,
                j.salary_min,
                j.salary_max,
                j.salary_currency,
                j.salary_period,
                j.skills,

                j.description,

                j.job_url,

                j.posted_at,

                j.first_seen_at,

                j.last_seen_at,

                j.status,

                j.created_at,

                j.updated_at

            FROM jobs j

            LEFT JOIN sources s ON s.id = j.source_id

        """

        query += where_clause

        query += " ORDER BY j.id DESC LIMIT %s OFFSET %s"

        query_params = tuple(params) + (

            per_page,

            (page - 1) * per_page,

        )

        jobs = fetch_all(query, query_params)

        return jsonify({

            "data": jobs,

            "pagination": {

                "page": page,

                "per_page": per_page,

                "total": total,

                "total_pages": (

                    (total + per_page - 1) // per_page

                ),

            },

            "filters": {

                "search": search,

                "role": role,

                "location": location,

                "company": company,

                "source_id": source_id or None,

            },

        })

    except DATABASE_ERRORS:

        app.logger.exception("Failed to retrieve jobs")

        return jsonify({

            "error": "Unable to retrieve jobs from the database"

        }), 500

@app.route("/api/reports/jobs/options", methods=["GET"])
def report_job_options():
    """Return distinct report filter values directly from persisted job records."""
    category = request.args.get("category", "title").strip().lower()
    allowed = {
        "title": ("title", "Job title"),
        "company": ("company", "Company"),
        "location": ("location", "Location"),
            }
    try:
        if category in {"family", "role"}:
            _ensure_job_family_mapping()
            selected_family = request.args.get("job_family", "").strip()
            if category == "family":
                rows = fetch_all("""
                    SELECT fm.job_family AS value, COUNT(*) AS job_count
                    FROM jobs j JOIN job_title_family_map fm
                      ON fm.title_hash = MD5(TRIM(j.title))
                    WHERE j.title IS NOT NULL AND TRIM(j.title) <> ''
                    GROUP BY fm.job_family
                    ORDER BY job_count DESC, fm.job_family
                """)
            else:
                if not selected_family:
                    return jsonify({"data": []})
                rows = fetch_all("""
                    SELECT fm.role_group AS value, COUNT(*) AS job_count
                    FROM jobs j JOIN job_title_family_map fm
                      ON fm.title_hash = MD5(TRIM(j.title))
                    WHERE fm.job_family = %s
                      AND j.title IS NOT NULL AND TRIM(j.title) <> ''
                    GROUP BY fm.role_group
                    ORDER BY job_count DESC, fm.role_group
                """, (selected_family,))
            return jsonify({"data": [{"value": r["value"], "label": r["value"], "count": r["job_count"], "job_count": r["job_count"]} for r in rows]})
        if category == "portal":
            # Keep the same portal set and labels as Dashboard analytics.
            portal_names = ["Naukri","Indeed","LinkedIn","Glassdoor","Foundit","TimesJobs","Bayt",
                "Internshala","Wellfound","Unstop","Hirist","Freshersworld","Shine","CutShort","Instahyre"]
            rows = fetch_all("""
                SELECT s.id, TRIM(s.name) AS value, COUNT(j.id) AS job_count
                FROM sources s LEFT JOIN jobs j ON j.source_id=s.id
                WHERE LOWER(TRIM(s.name)) IN
                  ('naukri','indeed','linkedin','linkedin jobs','glassdoor','foundit','timesjobs','bayt',
                   'internshala','wellfound','unstop','hirist','freshersworld','shine','cutshort','instahyre')
                  AND COALESCE(s.source_type,'job_website') <> 'company_career'
                GROUP BY s.id,s.name ORDER BY s.name
            """)
            canonical={str(r["value"]).strip().lower():r for r in rows}
            data=[]
            for name in portal_names:
                row=canonical.get(name.lower()) or canonical.get((name+" Jobs").lower())
                if row:
                    data.append({"value":str(row["id"]),"label":name,"source_id":row["id"],"count":row["job_count"]})
            return jsonify({"data":data})
        if category == "company":
            rows=fetch_all("""
                SELECT COALESCE(NULLIF(TRIM(company),''),'Unknown company') AS label, COUNT(*) AS total
                FROM jobs GROUP BY COALESCE(NULLIF(TRIM(company),''),'Unknown company')
                ORDER BY total DESC,label ASC
            """)
            return jsonify({"data":[{"value":r["label"],"label":r["label"],"count":r["total"]} for r in rows]})
        if category == "location":
            rows=fetch_all("""
                SELECT COALESCE(NULLIF(TRIM(location),''),'Unknown location') AS label,COUNT(*) AS total
                FROM jobs GROUP BY COALESCE(NULLIF(TRIM(location),''),'Unknown location')
                ORDER BY total DESC,label ASC
            """)
            normalized={}
            for r in rows:
                raw=str(r["label"] or "").strip()
                key=re.sub(r"[^a-z0-9]+"," ",raw.lower()).strip()
                key=" ".join(key.split()); words=set(key.split()); n=int(r.get("total") or 0)
                if key in {"india","in","all india","pan india","unknown location","remote india","india remote"}: continue
                if words.intersection({"bengaluru","bangalore"}) or key in {"ka","karnataka","ka india","karnataka india"}: label="Bengaluru, Karnataka, India"
                elif "chennai" in words or key in {"tn","tamil nadu","tn india","tamil nadu india"}: label="Chennai, Tamil Nadu, India"
                elif "hyderabad" in words or key in {"ts","telangana","ts india","telangana india"}: label="Hyderabad, Telangana, India"
                elif "mumbai" in words or key in {"mh","maharashtra","mh india","maharashtra india"}: label="Mumbai, Maharashtra, India"
                elif key in {"delhi","new delhi","ncr","delhi ncr","new delhi india","delhi india"}: label="Delhi NCR, India"
                elif words.intersection({"gurgaon","gurugram"}): label="Gurugram, Haryana, India"
                else: label=raw
                normalized[label]=normalized.get(label,0)+n
            data=[{"value":k,"label":k,"count":v} for k,v in sorted(normalized.items(),key=lambda x:(-x[1],x[0].lower()))]
            return jsonify({"data":data})
        if category == "fresher":
            return jsonify({"data":[
                {"value":"freshers","label":"Freshers"},
                {"value":"experienced","label":"Experienced"},
                {"value":"unclassified","label":"Unclassified"}
            ]})
        if category == "salary":
            rows = fetch_all("""
                SELECT DISTINCT salary_min, salary_max, salary_currency, salary_period
                FROM jobs
                WHERE salary_min IS NOT NULL OR salary_max IS NOT NULL
                ORDER BY salary_min NULLS FIRST, salary_max NULLS FIRST
            """)
            options = []
            for row in rows:
                low, high = row.get("salary_min"), row.get("salary_max")
                currency = row.get("salary_currency") or "INR"
                period = row.get("salary_period") or ""
                amount = (f"{low:,.0f}" if low is not None else "—") + " – " + (f"{high:,.0f}" if high is not None else "—")
                label = f"{currency} {amount}" + (f" / {period}" if period else "")
                options.append({"value": label, "label": label, "min": low, "max": high, "currency": currency, "period": period})
            return jsonify({"data": options})
        if category not in allowed:
            return jsonify({"error": "category must be portal, fresher, company, or location"}), 400
        column = allowed[category][0]
        rows = fetch_all(
            f"SELECT DISTINCT TRIM({column}) AS value FROM jobs WHERE {column} IS NOT NULL AND TRIM({column}) <> '' ORDER BY value"
        )
        return jsonify({"data": [{"value": row["value"], "label": row["value"]} for row in rows if row.get("value") not in (None, "")]})
    except DATABASE_ERRORS:
        app.logger.exception("Failed to load report filter options")
        return jsonify({"error": "Unable to load report filter options"}), 500


@app.route("/api/reports/jobs", methods=["GET"])
def report_jobs():
    """Paginated job report using the same family/role mapping as Job Portal."""
    try:
        _ensure_job_family_mapping()
        page = max(1, int(request.args.get("page", 1)))
        per_page = int(request.args.get("per_page", 20))

        if per_page < 1 or per_page > 100:
            return jsonify({
                "error": "per_page must be between 1 and 100"
            }), 400

        conditions = []
        params = []

        keyword = request.args.get("keyword", "").strip()
        company = request.args.get("company", "").strip()
        location = request.args.get("location", "").strip()
        role = request.args.get("role", "").strip()
        title = request.args.get("title", "").strip()
        job_family = request.args.get("job_family", "").strip()
        role_group = request.args.get("role", "").strip()
        experience = request.args.get("experience", "").strip()
        salary_min = request.args.get("salary_min", "").strip()
        salary_max = request.args.get("salary_max", "").strip()
        salary_currency = request.args.get("salary_currency", "").strip().upper()
        salary_period = request.args.get("salary_period", "").strip().lower()
        source_id = request.args.get("source_id", "").strip()
        source_type = request.args.get("source_type", "").strip().lower()
        posted_from = request.args.get("posted_from", "").strip()
        posted_to = request.args.get("posted_to", "").strip()

        if keyword:
            conditions.append(
                "(j.title LIKE %s OR j.company LIKE %s "
                "OR j.description LIKE %s)"
            )
            pattern = f"%{keyword}%"
            params.extend([pattern, pattern, pattern])

        if company:
            conditions.append("LOWER(TRIM(COALESCE(j.company,''))) = LOWER(TRIM(%s))")
            params.append(company)

        if location:
            loc_norm=location.strip().lower()
            location_aliases={
                "bengaluru, karnataka, india":["%Bengaluru%","%Bangalore%","%Karnataka%","KA","KA, India"],
                "chennai, tamil nadu, india":["%Chennai%","%Tamil Nadu%","TN","TN, India"],
                "hyderabad, telangana, india":["%Hyderabad%","%Telangana%","TS","TS, India"],
                "mumbai, maharashtra, india":["%Mumbai%","%Maharashtra%","MH","MH, India"],
                "delhi ncr, india":["%Delhi%","%NCR%"],
                "gurugram, haryana, india":["%Gurugram%","%Gurgaon%","%Haryana%"]
            }
            aliases=location_aliases.get(loc_norm)
            if aliases:
                conditions.append("("+" OR ".join("LOWER(COALESCE(j.location,'')) LIKE LOWER(%s)" for _ in aliases)+")")
                params.extend(aliases)
            else:
                conditions.append("LOWER(TRIM(COALESCE(j.location,''))) = LOWER(TRIM(%s))")
                params.append(location)

        if title:
            conditions.append("LOWER(TRIM(j.title)) = LOWER(TRIM(%s))")
            params.append(title)

        if job_family:
            conditions.append(
                "EXISTS (SELECT 1 FROM job_title_family_map fm "
                "WHERE fm.title_hash = MD5(TRIM(j.title)) AND fm.job_family = %s)"
            )
            params.append(job_family)
        if role_group and role_group != "__ALL_IN_FAMILY__":
            conditions.append(
                "EXISTS (SELECT 1 FROM job_title_family_map rfm "
                "WHERE rfm.title_hash = MD5(TRIM(j.title)) AND rfm.role_group = %s)"
            )
            params.append(role_group)

        if experience:
            conditions.append("j.experience = %s")
            params.append(experience)

        report_source_id = request.args.get("report_source_id", "").strip()
        fresher_group = request.args.get("fresher_group", "").strip().lower()
        if report_source_id:
            try:
                conditions.append("j.source_id = %s")
                params.append(int(report_source_id))
            except ValueError:
                return jsonify({"error":"Invalid portal selection"}),400
        exp_norm = "LOWER(TRIM(COALESCE(j.experience,'')))"
        fresher_expr = "(" + " OR ".join([f"{exp_norm} LIKE %s", f"{exp_norm} LIKE %s", f"{exp_norm} LIKE %s", f"{exp_norm} IN (%s,%s,%s)"]) + ")"
        fresher_params = ["%fresher%", "%entry%level%", "%no experience%", "0", "0.0", "0.00"]
        if fresher_group == "freshers":
            conditions.append(fresher_expr); params.extend(fresher_params)
        elif fresher_group == "experienced":
            conditions.append(f"{exp_norm} <> '' AND NOT {fresher_expr}"); params.extend(fresher_params)
        elif fresher_group == "unclassified":
            conditions.append(f"({exp_norm} = '' OR {fresher_expr})"); params.extend(fresher_params)

        if salary_min or salary_max:
            try:
                minimum = float(salary_min) if salary_min else None
                maximum = float(salary_max) if salary_max else None
            except ValueError:
                return jsonify({"error": "Invalid salary filter"}), 400
            if minimum is not None:
                conditions.append("COALESCE(j.salary_max, j.salary_min) >= %s")
                params.append(minimum)
            if maximum is not None:
                conditions.append("COALESCE(j.salary_min, j.salary_max) <= %s")
                params.append(maximum)
        if salary_currency:
            conditions.append("UPPER(COALESCE(j.salary_currency, 'INR')) = %s")
            params.append(salary_currency)
        if salary_period:
            conditions.append("LOWER(COALESCE(j.salary_period, '')) = %s")
            params.append(salary_period)

        if source_id:
            try:
                source_id = int(source_id)
            except ValueError:
                return jsonify({"error": "Invalid source_id"}), 400

            conditions.append("j.source_id = %s")
            params.append(source_id)

        if source_type in {"career_site", "company_career"}:
            conditions.append("s.source_type = %s")
            params.append("company_career")
        elif source_type == "portal":
            conditions.append("COALESCE(s.source_type, 'job_website') <> %s")
            params.append("company_career")
        elif source_type:
            return jsonify({"error": "source_type must be portal or career_site"}), 400

        for value, column, label in [
            (posted_from, "j.posted_at", "posted_from"),
            (posted_to, "j.posted_at", "posted_to"),
        ]:
            if value:
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except ValueError:
                    return jsonify({
                        "error": f"{label} must use YYYY-MM-DD format"
                    }), 400

        if posted_from:
            conditions.append("j.posted_at >= %s")
            params.append(posted_from)

        if posted_to:
            conditions.append("j.posted_at < %s")
            params.append(
            (datetime.strptime(posted_to, "%Y-%m-%d")
            + timedelta(days=1)).strftime("%Y-%m-%d")
    )

        where_clause = (
            " WHERE " + " AND ".join(conditions)
            if conditions else ""
        )

        # The count query only needs the sources join when a source-type filter is
        # active. Avoid joining sources for ordinary report pagination/count requests.
        needs_source_join = source_type in {"career_site", "company_career", "portal"}
        count_from = " FROM jobs j LEFT JOIN sources s ON s.id = j.source_id" if needs_source_join else " FROM jobs j"
        count_row = fetch_one(
            "SELECT COUNT(*) AS total" + count_from + where_clause,
            tuple(params),
        )
        total = count_row["total"]

        sort_key = request.args.get("sort", "posted_date_desc").strip().lower()
        sort_options = {
            "posted_date_desc": "j.posted_at DESC, j.id DESC",
            "posted_date_asc": "j.posted_at ASC, j.id DESC",
            "title_asc": "j.title ASC, j.id DESC",
            "company_asc": "j.company ASC, j.id DESC",
        }
        order_by = sort_options.get(sort_key, sort_options["posted_date_desc"])
        rows = fetch_all(
            """
            SELECT
                j.id,
                j.title,
                j.company,
                j.location,
                j.experience,
                j.salary_min,
                j.salary_max,
                j.salary_currency,
                j.salary_period,
                j.posted_at,
                j.status,
                j.source_id,
                j.job_url,
                s.name AS source_name
            FROM jobs j
            LEFT JOIN sources s ON s.id = j.source_id
            """
            + where_clause
            + " ORDER BY " + order_by + " LIMIT %s OFFSET %s",
            tuple(params) + (per_page, (page - 1) * per_page),
        )

        return jsonify({
            "data": rows,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": (total + per_page - 1) // per_page,
            },
        })

    except (ValueError, TypeError):
        return jsonify({"error": "Invalid report parameters"}), 400

    except DATABASE_ERRORS:
        app.logger.exception("Failed to load jobs report")
        return jsonify({
            "error": "Unable to load jobs report"
        }), 500


@app.route("/api/jobs/<int:job_id>", methods=["GET"])

def get_job(job_id):

    try:

        job = fetch_one(

            """

            SELECT

                j.*,

                s.name AS source_name,

                s.base_url AS source_base_url

            FROM jobs j

            LEFT JOIN sources s ON s.id = j.source_id

            WHERE j.id = %s

            """,

            (job_id,),

        )

        if not job:

            return jsonify({

                "error": "Job not found",

                "job_id": job_id,

            }), 404

        return jsonify({"data": job})

    except DATABASE_ERRORS:

        app.logger.exception("Failed to retrieve job details")

        return jsonify({

            "error": "Unable to retrieve job details"

        }), 500

@app.route("/api/sources", methods=["GET"])
def list_sources():
    try:
        # Seed and expose only sources present in the maintained portal catalog.
        _ensure_portal_catalog_rows()
        sources = fetch_all("""
            SELECT s.id, s.name, s.base_url, s.source_type, s.is_enabled,
                   s.created_at, s.updated_at, COUNT(DISTINCT j.id) AS job_count
            FROM sources s LEFT JOIN jobs j ON j.source_id = s.id
            WHERE LOWER(TRIM(s.name)) <> 'csv import'
              AND LOWER(TRIM(s.name)) <> 'all sources'
              AND COALESCE(s.source_type, 'job_website') <> 'company_career'
            GROUP BY s.id, s.name, s.base_url, s.source_type, s.is_enabled,
                     s.created_at, s.updated_at
            ORDER BY s.name
        """)
        key_by_name = {portal["name"].strip().lower(): portal["key"] for portal in JOB_PORTALS}
        # Some older databases contain a separate "Naukri via Apify" source.
        # Keep the database rows and job ownership unchanged, but present the
        # populated collector row as the single Naukri option when the canonical
        # Naukri row has no jobs. This avoids duplicate UI entries and preserves
        # source-ID filtering for the jobs actually collected by Apify.
        naukri_rows = [row for row in sources if str(row.get("name", "")).strip().lower() in {"naukri", "naukri via apify"}]
        canonical = next((row for row in naukri_rows if str(row.get("name", "")).strip().lower() == "naukri"), None)
        apify_row = next((row for row in naukri_rows if str(row.get("name", "")).strip().lower() == "naukri via apify"), None)
        if canonical and apify_row and int(canonical.get("job_count") or 0) == 0 and int(apify_row.get("job_count") or 0) > 0:
            sources = [row for row in sources if row is not canonical]
            apify_row["name"] = "Naukri"
            apify_row["base_url"] = canonical.get("base_url") or apify_row.get("base_url")
        # Canonical 15-portal UI: one row per configured portal, merge DB records
        # case-insensitively (e.g. CutShort/Cutshort), retain source IDs for filters.
        db_by_name = {}
        for row in sources:
            nm = str(row.get("name", "")).strip().lower()
            canonical_nm = "cutshort" if nm == "cutshort" else nm
            db_by_name.setdefault(canonical_nm, []).append(row)
        canonical_sources = []
        for portal in JOB_PORTALS:
            pname = portal["name"].strip()
            pkey = pname.lower()
            aliases = {"cutshort": {"cutshort"}, "linkedin": {"linkedin", "linkedin jobs"},
                       "ncs": {"ncs", "national career service (ncs)", "national career service"},
                       "shine": {"shine", "shine.com"}}
            names = aliases.get(pkey, {pkey})
            matched = [row for row in sources if str(row.get("name", "")).strip().lower() in names]
            ids = [int(row["id"]) for row in matched if row.get("id") is not None]
            total_jobs = sum(int(row.get("job_count") or 0) for row in matched)
            representative = matched[0] if matched else {}
            item = {
                "id": int(representative["id"]) if representative.get("id") is not None else -(len(canonical_sources)+1),
                "source_ids": ids,
                "name": pname, "base_url": portal["base_url"],
                "source_type": "job_website", "is_enabled": True,
                "job_count": total_jobs, "key": portal["key"],
                "collector_configured": _portal_configured(portal["key"]),
                "method": portal.get("method", "Apify")
            }
            canonical_sources.append(item)
        sources = canonical_sources
        return jsonify({"data": sources, "count": len(sources)})
    except DATABASE_ERRORS:
        app.logger.exception("Failed to retrieve sources")
        return jsonify({"error": "Unable to retrieve sources"}), 500


@app.route("/api/collection-runs", methods=["GET"])

def list_collection_runs():

    try:

        try:

            limit = int(request.args.get("limit", 10))

        except ValueError:

            return jsonify({

                "error": "limit must be an integer"

            }), 400

        if limit < 1 or limit > 100:

            return jsonify({

                "error": "limit must be between 1 and 100"

            }), 400

        runs = fetch_all(

            """

            SELECT

                cr.id,

                cr.source_id,

                s.name AS source_name,

                cr.started_at,

                cr.finished_at,

                cr.status,

                cr.records_found,

                cr.records_inserted,

                cr.records_updated,

                cr.error_message

            FROM collection_runs cr

            LEFT JOIN sources s ON s.id = cr.source_id

            ORDER BY cr.started_at DESC

            LIMIT %s

            """,

            (limit,),

        )

        return jsonify({

            "data": runs,

            "count": len(runs),

        })

    except DATABASE_ERRORS:

        app.logger.exception("Failed to retrieve collection runs")

        return jsonify({

            "error": "Unable to retrieve collection history"

        }), 500

@app.errorhandler(404)

def not_found(error):

    return jsonify({

        "error": "Endpoint not found"

    }), 404

@app.errorhandler(405)

def method_not_allowed(error):

    return jsonify({

        "error": "HTTP method not allowed"

    }), 405

def _clean_excel_text(value):
    """Convert text/HTML descriptions into readable, line-wrapped Excel text."""
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(?:p|div|section|article|li|tr|h[1-6])\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li\b[^>]*>", "• ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ").replace("\t", "    ")
    lines = [re.sub(r"[ \f\v]+", " ", line).strip() for line in text.split("\n")]
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    # Excel permits at most 32,767 characters in a cell.
    if len(cleaned) > 32700:
        cleaned = cleaned[:32680].rstrip() + "\n[Description truncated to Excel's cell limit]"
    return cleaned


def _excel_safe_value(value):
    """Avoid interpreting imported text as an Excel formula."""
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + value
    return value


def _style_export_sheet(sheet, widths, table_name):
    header_fill = PatternFill(fill_type="solid", fgColor="16324F")
    header_font = Font(bold=True, color="FFFFFF", size=10)
    thin_border = Border(bottom=Side(style="thin", color="DCE3EB"))
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(vertical="center", horizontal="left", wrap_text=True)
    sheet.row_dimensions[1].height = 30
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width
    for row_index, row in enumerate(sheet.iter_rows(min_row=2), start=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = thin_border
            if row_index % 2 == 0:
                cell.fill = PatternFill(fill_type="solid", fgColor="F3F6FA")
        # Keep rows readable without allowing very long descriptions to dominate the sheet.
        if len(row) >= 1 and sheet.row_dimensions[row_index].height is None:
            sheet.row_dimensions[row_index].height = 36
    sheet.sheet_view.showGridLines = False


@app.route("/api/reports/jobs/export", methods=["POST"])
def export_report_jobs_excel():
    """Build the Reports Excel file from IDs returned by the paginated Reports API.

    The browser applies the exact same report filters as PDF by collecting every
    page from /api/reports/jobs. This endpoint only enriches those IDs with the
    full description and writes the workbook in streaming mode.
    """
    try:
        payload = request.get_json(silent=True) or {}
        raw_ids = payload.get("job_ids", [])
        if not isinstance(raw_ids, list) or not raw_ids:
            return jsonify({"error": "No matching jobs were supplied. Click Filter first."}), 400
        if len(raw_ids) > 100000:
            return jsonify({"error": "The selected report exceeds the supported Excel export size."}), 400

        job_ids = []
        seen = set()
        for value in raw_ids:
            try:
                job_id = int(value)
            except (TypeError, ValueError):
                return jsonify({"error": "The report contains an invalid job ID."}), 400
            if job_id <= 0:
                return jsonify({"error": "The report contains an invalid job ID."}), 400
            if job_id not in seen:
                seen.add(job_id)
                job_ids.append(job_id)

        # Fetch in small batches to avoid database parameter and memory spikes.
        jobs_by_id = {}
        for offset in range(0, len(job_ids), 500):
            batch = job_ids[offset:offset + 500]
            placeholders = ",".join(["%s"] * len(batch))
            batch_rows = fetch_all(
                f"""SELECT j.id, j.title, j.company, j.location, j.experience,
                           j.salary_min, j.salary_max, j.salary_currency,
                           j.salary_period, j.skills, s.name AS source_name,
                           j.posted_at, j.status, j.job_url, j.description
                    FROM jobs j LEFT JOIN sources s ON s.id = j.source_id
                    WHERE j.id IN ({placeholders})""",
                tuple(batch),
            )
            jobs_by_id.update({int(row["id"]): row for row in batch_rows})

        # Preserve the same ordering returned by the paginated Reports API.
        workbook = Workbook(write_only=True)
        listings = workbook.create_sheet("Job Listings")
        descriptions = workbook.create_sheet("Full Descriptions")
        header_fill = PatternFill(fill_type="solid", fgColor="16324F")
        header_font = Font(bold=True, color="FFFFFF", size=10)

        def append_header(sheet, values):
            cells = []
            for value in values:
                cell = WriteOnlyCell(sheet, value=value)
                cell.fill = header_fill
                cell.font = header_font
                cells.append(cell)
            sheet.append(cells)

        from openpyxl.cell import WriteOnlyCell
        append_header(listings, ["ID", "Job Title", "Company", "Location", "Experience", "Salary Min", "Salary Max", "Currency", "Salary Period", "Skills", "Source", "Posted Date", "Status", "Job URL", "Description Preview"])
        append_header(descriptions, ["Job ID", "Job Title", "Company", "Location", "Full Job Description"])

        for job_id in job_ids:
            job = jobs_by_id.get(job_id)
            if not job:
                continue
            skills_value = job.get("skills")
            if isinstance(skills_value, (list, dict)):
                skills_value = json.dumps(skills_value, ensure_ascii=False)
            description = _clean_excel_text(job.get("description"))
            preview = description[:500]
            if len(description) > 500:
                preview = preview.rsplit(" ", 1)[0].rstrip() + "… See Full Descriptions tab."
            posted = job.get("posted_at")
            if isinstance(posted, (datetime, date)):
                posted = posted.isoformat()
            listings.append([
                _excel_safe_value(job.get("id")),
                _excel_safe_value(str(job.get("title") or "")),
                _excel_safe_value(str(job.get("company") or "")),
                _excel_safe_value(str(job.get("location") or "")),
                _excel_safe_value(str(job.get("experience") or "")),
                _excel_safe_value(job.get("salary_min")),
                _excel_safe_value(job.get("salary_max")),
                _excel_safe_value(str(job.get("salary_currency") or "")),
                _excel_safe_value(str(job.get("salary_period") or "")),
                _excel_safe_value(skills_value if isinstance(skills_value, str) else str(skills_value or "")),
                _excel_safe_value(str(job.get("source_name") or "")),
                _excel_safe_value(str(posted or "")),
                _excel_safe_value(str(job.get("status") or "")),
                _excel_safe_value(str(job.get("job_url") or "")),
                _excel_safe_value(preview),
            ])
            descriptions.append([
                _excel_safe_value(job.get("id")),
                _excel_safe_value(str(job.get("title") or "")),
                _excel_safe_value(str(job.get("company") or "")),
                _excel_safe_value(str(job.get("location") or "")),
                _excel_safe_value(description),
            ])

        for sheet, widths in ((listings, [10, 32, 28, 25, 18, 16, 16, 12, 14, 35, 20, 20, 14, 48, 68]), (descriptions, [12, 32, 28, 25, 100])):
            sheet.freeze_panes = "A2"
            sheet.sheet_view.showGridLines = False
            for index, width in enumerate(widths, start=1):
                sheet.column_dimensions[chr(64 + index)].width = width

        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        return send_file(
            output,
            as_attachment=True,
            download_name="v4career_filtered_jobs.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except DATABASE_ERRORS:
        app.logger.exception("Failed to export filtered report jobs")
        return jsonify({"error": "Unable to retrieve the filtered jobs for Excel export."}), 500
    except Exception:
        app.logger.exception("Unexpected error while exporting filtered report jobs")
        return jsonify({"error": "Unable to generate the Excel report. Please try again."}), 500


@app.route("/api/jobs/export", methods=["GET"])
def export_jobs():
    try:
        _ensure_job_family_mapping()
        search = request.args.get("search", "").strip()
        role = request.args.get("role", "").strip()
        job_family = request.args.get("job_family", "").strip()
        location = request.args.get("location", "").strip()
        company = request.args.get("company", "").strip()
        source_id = request.args.get("source_id", "").strip()
        source_ids = request.args.get("source_ids", "").strip()
        experience = request.args.get("experience", "").strip()
        skills = request.args.get("skills", "").strip()
        posted_after = request.args.get("posted_after", "").strip()
        posted_before = request.args.get("posted_before", "").strip()
        status = request.args.get("status", "").strip()
        salary_min = request.args.get("salary_min", "").strip()
        salary_max = request.args.get("salary_max", "").strip()
        salary_currency = request.args.get("salary_currency", "INR").strip().upper()
        salary_period = request.args.get("salary_period", "").strip().lower()

        conditions, params = [], []
        if job_family:
            conditions.append(
                "EXISTS (SELECT 1 FROM job_title_family_map fm "
                "WHERE fm.title_hash = MD5(TRIM(j.title)) AND fm.job_family = %s)"
            )
            params.append(job_family)
        if role:
            conditions.append(
                "EXISTS (SELECT 1 FROM job_title_family_map rfm "
                "WHERE rfm.title_hash = MD5(TRIM(j.title)) AND rfm.role_group = %s)"
            )
            params.append(role)
        if search:
            conditions.append("(j.title LIKE %s OR j.company LIKE %s OR j.description LIKE %s)")
            pattern = f"%{search}%"
            params.extend([pattern, pattern, pattern])
        if location:
            aliases={
                "bengaluru, karnataka, india":["%Bengaluru%","%Bangalore%","%Karnataka%","KA","KA, India"],
                "chennai, tamil nadu, india":["%Chennai%","%Tamil Nadu%","TN","TN, India"],
                "hyderabad, telangana, india":["%Hyderabad%","%Telangana%","TS","TS, India"],
                "mumbai, maharashtra, india":["%Mumbai%","%Maharashtra%","MH","MH, India"],
                "delhi ncr, india":["%Delhi%","%NCR%"],
                "gurugram, haryana, india":["%Gurugram%","%Gurgaon%","%Haryana%"]}
            selected_locations=aliases.get(location.strip().lower(),[f"%{location}%"])
            conditions.append("("+" OR ".join(["LOWER(COALESCE(j.location,'')) LIKE LOWER(%s)"]*len(selected_locations))+")")
            params.extend(selected_locations)
        if company:
            conditions.append("LOWER(TRIM(COALESCE(j.company,''))) = LOWER(TRIM(%s))")
            params.append(company)
        selected_companies = [value.strip() for value in request.args.getlist("companies") if value.strip()]
        if selected_companies:
            conditions.append("LOWER(TRIM(j.company)) IN (" + ",".join(["LOWER(TRIM(%s))"] * len(selected_companies)) + ")")
            params.extend(selected_companies)
        if source_ids:
            try:
                selected_source_ids = [int(value) for value in source_ids.split(",") if value.strip()]
            except ValueError:
                return jsonify({"error": "source_ids must be comma-separated integers"}), 400
            if not selected_source_ids:
                conditions.append("1 = 0")
            else:
                conditions.append("j.source_id IN (" + ",".join(["%s"] * len(selected_source_ids)) + ")")
                params.extend(selected_source_ids)
        report_source_id = request.args.get("report_source_id", "").strip()
        if report_source_id:
            try:
                conditions.append("j.source_id = %s"); params.append(int(report_source_id))
            except ValueError:
                return jsonify({"error":"Invalid portal selection"}),400
        fresher_group = request.args.get("fresher_group", "").strip().lower()
        exnorm = "LOWER(TRIM(COALESCE(j.experience,'')))"
        fresher_expr = "(" + " OR ".join([f"{exnorm} LIKE %s", f"{exnorm} LIKE %s", f"{exnorm} LIKE %s", f"{exnorm} IN (%s,%s,%s)"]) + ")"
        fp = ["%fresher%", "%entry%level%", "%no experience%", "0", "0.0", "0.00"]
        if fresher_group == "freshers": conditions.append(fresher_expr); params.extend(fp)
        elif fresher_group == "experienced": conditions.append(f"{exnorm} <> '' AND NOT {fresher_expr}"); params.extend(fp)
        elif fresher_group == "unclassified": conditions.append(f"({exnorm} = '' OR {fresher_expr})"); params.extend(fp)
        if source_id:
            try:
                source_id = int(source_id)
            except ValueError:
                return jsonify({"error": "source_id must be an integer"}), 400
            conditions.append("j.source_id = %s")
            params.append(source_id)
        report_source_id = request.args.get("report_source_id", "").strip()
        if report_source_id:
            try:
                conditions.append("j.source_id = %s")
                params.append(int(report_source_id))
            except ValueError:
                return jsonify({"error":"Invalid portal selection"}),400
        if experience:
            # Match the same experience normalization used by /api/jobs.
            experience_pattern = _experience_match_pattern(experience)
            if experience_pattern:
                conditions.append("COALESCE(TRIM(j.experience), '') ~ %s")
                params.append(experience_pattern)
            else:
                conditions.append("LOWER(TRIM(j.experience)) = LOWER(TRIM(%s))")
                params.append(experience)
        if skills:
            conditions.append("CAST(j.skills AS TEXT) LIKE %s")
            params.append(f"%{skills}%")
        if posted_after:
            try:
                datetime.strptime(posted_after, "%Y-%m-%d")
            except ValueError:
                return jsonify({"error": "posted_after must use YYYY-MM-DD format"}), 400
            conditions.append("j.posted_at >= %s")
            params.append(posted_after)
        if posted_before:
            try:
                before_exclusive = (datetime.strptime(posted_before, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            except ValueError:
                return jsonify({"error": "posted_before must use YYYY-MM-DD format"}), 400
            conditions.append("j.posted_at < %s")
            params.append(before_exclusive)
        if status:
            conditions.append("LOWER(j.status) = %s")
            params.append(status.lower())
        if salary_min or salary_max:
            try:
                min_value = float(salary_min) if salary_min else None
                max_value = float(salary_max) if salary_max else None
                if (min_value is not None and min_value < 0) or (max_value is not None and max_value < 0): raise ValueError
                if min_value is not None and max_value is not None and min_value > max_value: raise ValueError
            except ValueError:
                return jsonify({"error": "Salary values must be non-negative numbers and minimum cannot exceed maximum"}), 400
            if min_value is not None:
                conditions.append("j.salary_currency = %s AND (%s = '' OR j.salary_period = %s) AND COALESCE(j.salary_max, j.salary_min) >= %s")
                params.extend([salary_currency, salary_period, salary_period, min_value])
            if max_value is not None:
                conditions.append("j.salary_currency = %s AND (%s = '' OR j.salary_period = %s) AND COALESCE(j.salary_min, j.salary_max) <= %s")
                params.extend([salary_currency, salary_period, salary_period, max_value])

        excluded_raw = request.args.get("exclude_job_ids", "").strip()
        if excluded_raw:
            try:
                excluded_ids = sorted({int(part.strip()) for part in excluded_raw.split(",") if part.strip()})
                if any(job_id <= 0 for job_id in excluded_ids) or len(excluded_ids) > 10000:
                    raise ValueError
            except ValueError:
                return jsonify({"error": "exclude_job_ids must contain positive integer IDs (maximum 10000)"}), 400
            if excluded_ids:
                conditions.append("j.id NOT IN (" + ",".join(["%s"] * len(excluded_ids)) + ")")
                params.extend(excluded_ids)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        jobs = fetch_all(
            """
            SELECT j.id, j.title, j.company, j.location, j.experience, j.salary_min, j.salary_max, j.salary_currency, j.salary_period, j.skills,
                   s.name AS source_name, j.posted_at, j.status, j.job_url, j.description
            FROM jobs j LEFT JOIN sources s ON s.id = j.source_id
            """ + where_clause + " ORDER BY j.last_seen_at DESC",
            tuple(params),
        )

        if request.args.get("format", "xlsx").strip().lower() == "csv":
            csv_buffer = StringIO()
            writer = csv.writer(csv_buffer)
            writer.writerow(["ID", "Job Title", "Company", "Location", "Experience", "Salary Min", "Salary Max", "Currency", "Salary Period", "Skills", "Source", "Posted Date", "Status", "Job URL", "Description"])
            for job in jobs:
                skills_value = job.get("skills")
                if isinstance(skills_value, (list, dict)):
                    skills_value = json.dumps(skills_value, ensure_ascii=False)
                posted_value = job.get("posted_at")
                if isinstance(posted_value, (datetime, date)):
                    posted_value = posted_value.isoformat()
                writer.writerow([
                    job.get("id"), job.get("title"), job.get("company"), job.get("location"), job.get("experience"),
                    job.get("salary_min"), job.get("salary_max"), job.get("salary_currency"), job.get("salary_period"),
                    skills_value, job.get("source_name"), posted_value, job.get("status"), job.get("job_url"),
                    _clean_excel_text(job.get("description")),
                ])
            csv_output = BytesIO(csv_buffer.getvalue().encode("utf-8-sig"))
            csv_output.seek(0)
            return send_file(csv_output, as_attachment=True, download_name="v4career_scraper_report.csv", mimetype="text/csv; charset=utf-8")

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Job Listings"
        sheet.append([
            "ID", "Job Title", "Company", "Location", "Experience", "Salary Min", "Salary Max", "Currency", "Salary Period", "Skills",
            "Source", "Posted Date", "Status", "Job URL", "Description Preview"
        ])

        descriptions = workbook.create_sheet("Full Descriptions")
        descriptions.append(["Job ID", "Job Title", "Company", "Location", "Full Job Description"])

        for job in jobs:
            job_skills = job.get("skills")
            if isinstance(job_skills, (list, dict)):
                job_skills = json.dumps(job_skills, ensure_ascii=False)
            description = _clean_excel_text(job.get("description"))
            preview_limit = 500
            if len(description) > preview_limit:
                preview = description[:preview_limit].rsplit(" ", 1)[0].rstrip() + "… See Full Descriptions tab."
            else:
                preview = description
            url = str(job.get("job_url") or "").strip()
            posted = job.get("posted_at")
            if isinstance(posted, (datetime, date)):
                posted = posted.isoformat()
            sheet.append([
                _excel_safe_value(job.get("id")),
                _excel_safe_value(str(job.get("title") or "")),
                _excel_safe_value(str(job.get("company") or "")),
                _excel_safe_value(str(job.get("location") or "")),
                _excel_safe_value(str(job.get("experience") or "")),
                _excel_safe_value(job.get("salary_min")),
                _excel_safe_value(job.get("salary_max")),
                _excel_safe_value(str(job.get("salary_currency") or "")),
                _excel_safe_value(str(job.get("salary_period") or "")),
                _excel_safe_value(job_skills if isinstance(job_skills, str) else str(job_skills or "")),
                _excel_safe_value(str(job.get("source_name") or "")),
                _excel_safe_value(str(posted or "")),
                _excel_safe_value(str(job.get("status") or "")),
                _excel_safe_value(url),
                _excel_safe_value(preview),
            ])
            listing_row = sheet.max_row
            if url.lower().startswith(("https://", "http://")):
                sheet.cell(listing_row, 10).hyperlink = url
                sheet.cell(listing_row, 10).style = "Hyperlink"
            sheet.row_dimensions[listing_row].height = 48

            descriptions.append([
                _excel_safe_value(job.get("id")),
                _excel_safe_value(str(job.get("title") or "")),
                _excel_safe_value(str(job.get("company") or "")),
                _excel_safe_value(str(job.get("location") or "")),
                _excel_safe_value(description),
            ])
            desc_row = descriptions.max_row
            visual_lines = sum(max(1, (len(line) + 94) // 95) for line in description.splitlines()) or 1
            descriptions.row_dimensions[desc_row].height = min(360, max(48, 15 * min(visual_lines, 24)))

        _style_export_sheet(sheet, [10, 32, 28, 25, 18, 16, 16, 12, 14, 35, 20, 20, 14, 48, 68], "JobListings")
        _style_export_sheet(descriptions, [12, 32, 28, 25, 100], "FullDescriptions")
        descriptions.sheet_properties.pageSetUpPr.fitToPage = True
        descriptions.page_setup.fitToWidth = 1
        descriptions.page_setup.fitToHeight = 0
        descriptions.page_setup.orientation = "landscape"
        descriptions.sheet_properties.outlinePr.summaryBelow = True
        workbook.properties.title = "V4Career Job Listings Export"
        workbook.properties.subject = "Filtered job listings and full descriptions"
        workbook.properties.creator = "V4Career"

        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        return send_file(
            output,
            as_attachment=True,
            download_name="v4career_job_listings.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except DATABASE_ERRORS:
        app.logger.exception("Failed to export jobs")
        return jsonify({"error": "Unable to export jobs"}), 500
    except Exception:
        app.logger.exception("Unexpected error while exporting jobs")
        return jsonify({"error": "Unable to generate the Excel export"}), 500




@app.route("/api/login-audit", methods=["GET"])
def login_audit():
    if session.get("role") != "admin":
        return jsonify({"error": "Admin access required"}), 403
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS login_audit (
        id BIGINT PRIMARY KEY, username VARCHAR(150), role VARCHAR(30),
        login_at VARCHAR(40), ip_address VARCHAR(100), email VARCHAR(255),
        event VARCHAR(20), status VARCHAR(20), event_at VARCHAR(40)
    )""")
    for col, typ in [("email","VARCHAR(255)"),("event","VARCHAR(20)"),("status","VARCHAR(20)"),("event_at","VARCHAR(40)"),("location","VARCHAR(255)") ]:
        cur.execute(f"ALTER TABLE login_audit ADD COLUMN IF NOT EXISTS {col} {typ}")
    conn.commit()
    cur.execute("""SELECT id, username, role, COALESCE(event_at, login_at), ip_address,
                          COALESCE(email,''), COALESCE(event,'login'), COALESCE(status,'success'), COALESCE(location,'Unknown')
                   FROM login_audit ORDER BY id DESC LIMIT 10""")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify({"data": [{"id": str(r[0]), "username": r[1], "role": r[2],
        "timestamp": r[3], "ip_address": r[4], "email": r[5], "event": r[6],
        "status": r[7], "location": r[8]} for r in rows]})
@app.route('/credits')
@_phase4_login_required
def credits_page():
    if session.get('role')!='admin': return redirect(url_for('home'))
    if request.args.get('partial') == '1':
        return render_template('credits_admin_partial.html')
    return render_template('credits_admin.html')

@app.route('/api/credits/me')
@_phase4_login_required
def credits_me():
    if session.get('role')=='admin': return jsonify({'unlimited':True})
    _ensure_user_credit(session.get('username'),session.get('email'))
    c=get_connection(); cur=c.cursor()
    try:
        cur.execute('SELECT balance,account_status,subscription_plan,subscription_status FROM user_credits WHERE username=%s',(session.get('username'),)); r=cur.fetchone()
        return jsonify({'balance':r[0],'account_status':r[1],'subscription_plan':r[2],'subscription_status':r[3]})
    finally: cur.close(); c.close()

@app.route('/api/credits/open-job/<int:job_id>')
@_phase4_login_required
def credits_open_job(job_id):
    c=get_connection(); cur=c.cursor()
    try:
        cur.execute('SELECT job_url,title FROM jobs WHERE id=%s',(job_id,)); job=cur.fetchone()
        if not job or not job[0]: return 'Job link unavailable',404
        if session.get('role')=='admin': return redirect(job[0])
        cur.execute('SELECT balance,account_status FROM user_credits WHERE username=%s',(session.get('username'),)); r=cur.fetchone()
        if not r or r[1]!='active': return 'Account inactive. Contact admin.',403
        if int(r[0])<=0: return 'No credits remaining. Contact admin or subscribe.',402
        bal=int(r[0])-1; now=datetime.now().astimezone().isoformat(timespec='seconds')
        cur.execute('UPDATE user_credits SET balance=%s,updated_at=%s WHERE username=%s',(bal,now,session.get('username')))
        cur.execute('INSERT INTO credit_history(id,username,email,action,amount,balance_after,job_id,job_title,details,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(time.time_ns(),session.get('username'),session.get('email'),'consume',1,bal,job_id,job[1],'Opened source job link',now))
        c.commit()
        if request.args.get('json') == '1':
            return jsonify({'ok': True, 'balance': bal, 'url': job[0]})
        return redirect(job[0])
    finally: cur.close(); c.close()

@app.route('/api/admin/credits',methods=['GET','POST'])
@_phase4_login_required
def admin_credits_api():
    if session.get('role')!='admin': return jsonify({'error':'Admin only'}),403
    _ensure_credit_tables(); c=get_connection(); cur=c.cursor()
    try:
        if request.method=='POST':
            d=request.get_json(silent=True) or request.form; act=d.get('action'); u=d.get('username',''); amt=max(0,int(d.get('amount',0) or 0)); now=datetime.now().astimezone().isoformat(timespec='seconds')
            if act=='allocation':
                cur.execute('UPDATE credit_settings SET setting_value=%s WHERE setting_key=%s',(str(amt),'free_credits')); c.commit(); return jsonify({'ok':True})
            cur.execute('SELECT email,balance FROM user_credits WHERE username=%s',(u,)); r=cur.fetchone()
            if not r:return jsonify({'error':'User not found'}),404
            email,balance=r[0],int(r[1]); new=balance
            if act=='add':new+=amt
            elif act=='deduct':new=max(0,new-amt)
            elif act=='reset':
                cur.execute('SELECT setting_value FROM credit_settings WHERE setting_key=%s',('free_credits',)); s=cur.fetchone();new=int(s[0]) if s else 5
            elif act=='status':
                cur.execute('UPDATE user_credits SET account_status=%s WHERE username=%s',(d.get('status','active'),u));c.commit();return jsonify({'ok':True})
            elif act=='subscription':
                cur.execute('UPDATE user_credits SET subscription_plan=%s,subscription_status=%s,subscription_expires_at=%s WHERE username=%s',(d.get('plan','free'),d.get('subscription_status','active'),d.get('expires_at') or None,u));c.commit();return jsonify({'ok':True})
            else:return jsonify({'error':'Invalid action'}),400
            cur.execute('UPDATE user_credits SET balance=%s,updated_at=%s WHERE username=%s',(new,now,u))
            cur.execute('INSERT INTO credit_history(id,username,email,action,amount,balance_after,details,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(time.time_ns(),u,email,act,amt,new,'Admin adjustment',now));c.commit();return jsonify({'ok':True,'balance':new})
        cur.execute('SELECT username,email,balance,account_status,subscription_plan,subscription_status,subscription_expires_at FROM user_credits ORDER BY username'); users=[dict(zip(['username','email','balance','account_status','subscription_plan','subscription_status','subscription_expires_at'],r)) for r in cur.fetchall()]
        cur.execute('SELECT id,username,email,action,amount,balance_after,job_id,job_title,details,created_at FROM credit_history ORDER BY created_at DESC LIMIT 500'); hist=[dict(zip(['id','username','email','action','amount','balance_after','job_id','job_title','details','created_at'],r)) for r in cur.fetchall()]
        cur.execute('SELECT setting_value FROM credit_settings WHERE setting_key=%s',('free_credits',));s=cur.fetchone()
        return jsonify({'users':users,'history':hist,'free_credits':int(s[0]) if s else 5})
    finally:cur.close();c.close()

if __name__ == "__main__":
    app.run(debug=True)
