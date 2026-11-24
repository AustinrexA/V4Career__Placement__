#!/usr/bin/env python3
"""Authenticated local HTTP bridge for n8n -> V4Career Windows scraper scripts.

Run from the V4Career project root:
  python n8n_bridge.py

Endpoints:
  GET  /health
  POST /api/run       JSON: {"stage":"jobspy|six_portals|collector1|collector2|collector3"}
  GET  /api/status?job_id=<id>

The bridge only runs fixed, allowlisted commands. It does not accept shell commands.
Set V4CAREER_BRIDGE_TOKEN in the project .env and in the n8n HTTP Request
Authorization header as: Bearer <same-token>.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
HOST = os.getenv("V4CAREER_BRIDGE_HOST", "0.0.0.0")
PORT = int(os.getenv("V4CAREER_BRIDGE_PORT", "8765"))
TOKEN = os.getenv("V4CAREER_BRIDGE_TOKEN", "").strip()
LOG_DIR = ROOT / "logs" / "n8n_bridge"
LOG_DIR.mkdir(parents=True, exist_ok=True)

ROLES = [
    "Software Engineer","Software Developer", "Graduate Engineer Trainee",
    "Graduate Trainee","Cloud Engineer","IT Support Engineer","Hr Executive",
    "Data Analyst", "Data Analyst Intern", "Business Analyst", "Product Analyst",
    "Data Engineer" ,
]
LOCATIONS = ["Chennai", "Bangalore", "Hyderabad"]
SITES = ["indeed", "naukri", "linkedin", "glassdoor", "bayt"]

def build_command(stage: str) -> list[str]:
    py = sys.executable
    if stage == "jobspy":
        return [
            py, "scripts/run_jobspy.py",
            "--roles", *ROLES,
            "--locations", *LOCATIONS,
            "--sites", *SITES,
            "--results-per-site", "15",
            "--hours-old", "48",
            "--restart",
        ]
    if stage == "six_portals":
        return [
            py, "scripts/run_six_portals.py",
            "--source", "all", "--max-results", "20",
            "--delay", "2", "--hours-old", "48",
        ]
    if stage in {"collector1", "collector2", "collector3"}:
        return [
            py, "scripts/run_source_apify.py",
            "--source", stage.upper(), "--keyword", "IT jobs",
            "--location", "India", "--max-results", "20",
            "--hours-old", "48",
        ]
    raise ValueError("Unsupported stage")

STATE: dict[str, dict] = {}
STATE_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()
ACTIVE_JOB_ID: str | None = None

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def execute_stage(job_id: str, stage: str, command: list[str], log_path: Path) -> None:
    global ACTIVE_JOB_ID
    with STATE_LOCK:
        STATE[job_id]["status"] = "running"
        STATE[job_id]["started_at"] = utc_now()
        STATE[job_id]["command"] = command[1:]
    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            log.write(f"Stage: {stage}\nStarted: {utc_now()}\n")
            log.write("Command: " + subprocess.list2cmdline(command) + "\n\n")
            log.flush()
            completed = subprocess.run(
                command,
                cwd=str(ROOT),
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                shell=False,
            )
        with STATE_LOCK:
            STATE[job_id]["return_code"] = completed.returncode
            STATE[job_id]["status"] = "succeeded" if completed.returncode == 0 else "failed"
            STATE[job_id]["finished_at"] = utc_now()
            STATE[job_id]["log_file"] = str(log_path)
    except Exception as exc:
        with STATE_LOCK:
            STATE[job_id]["status"] = "failed"
            STATE[job_id]["error"] = f"{type(exc).__name__}: {exc}"
            STATE[job_id]["finished_at"] = utc_now()
    finally:
        RUN_LOCK.release()
        ACTIVE_JOB_ID = None

class Handler(BaseHTTPRequestHandler):
    server_version = "V4CareerBridge/1.0"

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not TOKEN:
            self._send(503, {"error": "Bridge token is not configured. Set V4CAREER_BRIDGE_TOKEN in .env and restart."})
            return False
        supplied = self.headers.get("Authorization", "")
        expected = "Bearer " + TOKEN
        if not secrets.compare_digest(supplied, expected):
            self._send(401, {"error": "Unauthorized"})
            return False
        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send(200, {"ok": True, "service": "V4Career bridge"})
            return
        if parsed.path != "/api/status":
            self._send(404, {"error": "Not found"})
            return
        if not self._authorized():
            return
        job_id = parse_qs(parsed.query).get("job_id", [""])[0]
        with STATE_LOCK:
            item = STATE.get(job_id)
            if item is None:
                self._send(404, {"error": "Unknown job_id"})
                return
            result = dict(item)
        log_path = result.get("log_file")
        if log_path and Path(log_path).exists():
            result["log_tail"] = Path(log_path).read_text(encoding="utf-8", errors="replace")[-12000:]
        self._send(200, result)

    def do_POST(self):
        global ACTIVE_JOB_ID
        if urlparse(self.path).path != "/api/run":
            self._send(404, {"error": "Not found"})
            return
        if not self._authorized():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 4096:
                self._send(400, {"error": "Request body must be 1..4096 bytes"})
                return
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            stage = str(body.get("stage", "")).strip().lower()
            command = build_command(stage)
        except Exception as exc:
            self._send(400, {"error": f"Invalid request: {exc}"})
            return

        if not RUN_LOCK.acquire(blocking=False):
            self._send(409, {"error": "Another scraper stage is already running", "active_job_id": ACTIVE_JOB_ID})
            return

        job_id = uuid.uuid4().hex
        log_path = LOG_DIR / f"{job_id}_{stage}.log"
        with STATE_LOCK:
            STATE[job_id] = {
                "job_id": job_id, "stage": stage, "status": "queued",
                "created_at": utc_now(), "log_file": str(log_path),
            }
        ACTIVE_JOB_ID = job_id
        thread = threading.Thread(
            target=execute_stage, args=(job_id, stage, command, log_path), daemon=True
        )
        thread.start()
        self._send(202, {"job_id": job_id, "stage": stage, "status": "queued"})

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

def main():
    if not (ROOT / "scripts" / "run_jobspy.py").exists():
        raise SystemExit("Run this bridge from the V4Career project root; scripts/run_jobspy.py was not found.")
    if not TOKEN:
        raise SystemExit("Missing V4CAREER_BRIDGE_TOKEN. Add it to the project .env before starting the bridge.")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"V4Career bridge listening on http://{HOST}:{PORT}")
    print(f"Project root: {ROOT}")
    print("Use host.docker.internal from n8n running in Docker.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping V4Career bridge...")
    finally:
        server.server_close()

if __name__ == "__main__":
    main()
