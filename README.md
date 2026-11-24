# V4Career

V4Career is a Flask-based job portal and placement-assistance application.

## Project setup

1. Extract the project and open this `V4Career` folder in your terminal.
2. Create and activate a Python virtual environment:
   ```powershell
   py -m venv venv
   .\venv\Scripts\Activate.ps1
   ```
3. Install the project dependencies:
   ```powershell
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```
4. Create your local `.env` using `.env.example` as a reference. Fill in the database, Flask, and collector settings required by your environment. Never share or commit `.env`.
5. Start the application:
   ```powershell
   python app.py
   ```

Use the database configuration and deployment instructions in `docs/` that match your current environment. Do not run migration or source-merge commands against production without first taking a database backup.

## Documentation

Detailed guides are grouped in the `docs` folder:

- [Scraping and job portal configuration](docs/SCRAPING_AND_PORTAL_GUIDE.md)
- [Company career integrations](docs/COMPANY_INTEGRATIONS.md)
- [Database and data maintenance](docs/DATABASE_AND_DATA_MAINTENANCE.md)
- [Deployment and security](docs/DEPLOYMENT_AND_SECURITY.md)
- [Dashboard, UI and filters](docs/DASHBOARD_UI_AND_FILTERS.md)
- [Testing, performance and change history](docs/TESTING_PERFORMANCE_AND_CHANGELOG.md)

## Validation

Run the tests from the project root:
```powershell
python -m pytest -q
node --check static\app.js
```

The guides include historical implementation notes as well as setup instructions. Treat date-stamped change notes as project history; the current code and `.env.example` are authoritative for enabled features and configuration. Always test in a local or staging environment before production deployment.
