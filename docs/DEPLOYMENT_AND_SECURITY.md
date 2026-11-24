# Deployment And Security



---

## Reference: `CLIENT_UPGRADE_GUIDE.md`


# V4Career client upgrade (Version 2 base)

## Install on Windows

1. Extract this ZIP into a new folder; do not overwrite your current project.
2. Open PowerShell in `V4Career_UI_Upgrade`.
3. Create and activate a virtual environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env`, then set the local database connection, `FLASK_SECRET_KEY`, `ADMIN_USERNAME`, and `ADMIN_PASSWORD_HASH`. Do not commit `.env`.
5. Run the existing schema/migrations required by your database, then start the app:

   ```powershell
   python -m flask --app app run --debug
   ```

6. Open `http://127.0.0.1:5000`.

## Implemented in this delivery

- Master checkbox is positioned within each sidebar heading and supports checked/unchecked/indeterminate states for the job portal list and career-site list.
- Company Career Sites is no longer a company-name filter. A separate list of employer career pages is seeded as `company_career` source records; those records are excluded from the Job Portal source list.
- The company-name text filter on the main Job Listings page is preserved.
- Dashboard has four KPI cards: active jobs, jobs added today, enabled collection sources, and failed collection runs today.
- Reports show source totals and recent collection runs.
- Existing job listing page markup and its filters/export controls are otherwise retained.

## Important configuration limits

The career-site entries are source placeholders, not ready-to-run scrapers. Each needs an authorized feed or a compatible scraper integration before collection can run. Selecting a career source does not launch scraping.

This build does not yet include a complete administrator UI for managing customer accounts and per-customer access expiry, nor full report-specific filters and filtered CSV/Excel exports for every requested report type. Those features require additional backend routes, a customer-account schema/migration, and authorization tests. Do not treat this delivery as satisfying those unfinished requirements.

## Safety

- No Render deployment or GitHub push is performed.
- The archive excludes `.env`, `.git`, `__pycache__`, and `.pyc` files.
- Back up your local database before applying any migration. No existing job records are deleted by the changes in this delivery.


---

## Reference: `DEPLOYMENT_LOGIN_OTP_NOTES.md`


# V4Career login OTP and proxy deployment notes

## Login OTP recipient
The OTP recipient is resolved from `ADMIN_EMAIL` or `USER_EMAIL` in the server environment.
Set `ADMIN_EMAIL` to the company email address for admin OTP delivery (and `USER_EMAIL` for the user account if desired).
Do not change `SMTP_USERNAME`/`SMTP_FROM` unless changing the sending mailbox/provider; these are sender credentials, not the OTP recipient.
After changing environment values, redeploy/restart the service.

## Client IP behind Render
The app now uses Werkzeug ProxyFix and `request.remote_addr` for audit IP.
Set `TRUSTED_PROXY_HOPS=1` only after verifying the service has exactly one trusted proxy hop. Never trust arbitrary client-supplied forwarded headers.
IP-derived location remains approximate and may map Neyveli to Chennai or another nearby ISP region.

## Required Render variables
- `ADMIN_EMAIL=your-company-admin-email`
- `TRUSTED_PROXY_HOPS=1` (verified topology only)
- `IP_GEOLOCATION_ENABLED=true`


---

## Reference: `PHASE2_EMAIL_OTP_SETUP.md`


# Phase 2 — Email OTP (built on the newly uploaded ZIP)

## SMTP environment
Keep the private `.env` out of Git. Configure these variables locally and in Render:
`ADMIN_EMAIL`, `USER_EMAIL`, `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`,
`SMTP_USERNAME` (sending Gmail), `SMTP_PASSWORD` (Gmail App Password),
`SMTP_FROM` (same sender Gmail), `SMTP_USE_TLS=true`,
`OTP_TTL_SECONDS=300`, `OTP_RESEND_SECONDS=60`, `OTP_MAX_ATTEMPTS=5`.

Existing `ADMIN_USERNAME` and `USER_USERNAME` settings are used to map the configured email to the role. Role is never supplied by the login form.

## Flow
1. Login form accepts email only; `/login` sends OTP.
2. `/verify-otp` verifies code, expiry, and attempt limit.
3. `/resend-otp` enforces cooldown.
4. Role is resolved from server configuration; successful login sets authenticated session and preserves the existing login audit write.
5. `/logout` clears session.

## Deploy
Set the environment variables in Render Dashboard → V4Career service → Environment, then redeploy. Never commit SMTP credentials.


## Password login (restored)
Password login and Email OTP are both available on `/login`. Configure account passwords in `.env`:
- `ADMIN_PASSWORD=...`
- `USER_PASSWORD=...`

For production, prefer password hashes:
- `ADMIN_PASSWORD_HASH=...`
- `USER_PASSWORD_HASH=...`
Generate a hash in Python with `from werkzeug.security import generate_password_hash; print(generate_password_hash("your-password"))`.
If a hash is configured, it takes precedence over the plain password variable. Keep these secrets out of Git.
