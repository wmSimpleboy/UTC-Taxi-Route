# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Taxi Locations is a Flask web app for dispatching shared taxi rides for company employees in Uzbekistan. It solves Vehicle Routing Problems (VRP) using OR-Tools and OSRM to optimally assign employees to cars and build driving routes. Confirmed trips are saved to SQLite and optionally announced via a Telegram bot.

## Running Locally

```bash
# 1. Activate venv and install deps
pip install -r requirements.txt

# 2. Copy .env.example to .env, fill in secrets

# 3. Start OSRM (required for routing)
docker compose up -d osrm
# Or use setup_osrm.ps1 on Windows

# 4. Run the app (from repo root)
set FLASK_DEBUG=1          # Windows; enables dev fallback password "1000"
python TaxiLocations/main.py
# Listens on http://127.0.0.1:5002 (or FLASK_PORT)
```

Docker full stack: `docker compose --profile full up -d --build` (web on port 4017, OSRM on 5000).

## Architecture

**Request flow:** Browser → Flask (`ui_app.py`) → `routing.py` (VRP solver) → OSRM server (driving distances/routes) → response with map data + Yandex Maps URLs.

Key modules inside `TaxiLocations/`:

- **`main.py`** — Entry point. Loads dotenv, starts Telegram bot thread, then runs Flask.
- **`ui_app.py`** — Flask app factory (`create_app()`). All API endpoints live here. Session-based auth with CSRF protection. Route results are cached in-memory per `runId` for the confirm flow.
- **`routing.py`** — Core VRP logic. Fetches OSRM distance matrix, solves with OR-Tools (multi-start strategies with configurable time limits), builds Yandex Maps URLs. Routes >25 km can auto-split into two cars. Constants: `COST_PER_CAR`, `COST_PER_KM`, `COMPANY_LOCATION`.
- **`data_tools.py`** — Employee CRUD on `employees.json`. Excel import/export. Google Maps geocoding helper. Thread-safe file access via `_EMPLOYEES_FILE_LOCK`.
- **`db.py`** — SQLite persistence for confirmed trips (`data/trips.db`). Aggregation queries for stats (today/month/range).
- **`telegram_bot.py`** — Long-polling Telegram bot (background thread). Commands: `/today`, `/month`, `/report YYYY-MM-DD YYYY-MM-DD`. Only responds to `TELEGRAM_CHAT_IDS`.
- **`telegram_notify.py`** — One-way Telegram message sender (used by confirm endpoint to push route info to chats).

**Frontend:** Single-page app in `templates/index.html` + `static/js/app.js`. Dark theme with Catppuccin-style colors defined as constants in `ui_app.py`. Uses Leaflet for maps.

## Key Environment Variables

- `OSRM_BASE_URL` — OSRM server (default `http://localhost:5000`). Required for routing.
- `FLASK_PORT` / `FLASK_HOST` — Web server bind (default 5002 / 127.0.0.1).
- `FLASK_DEBUG=1` — Dev mode; relaxes secret key and password requirements.
- `SPLIT_MIN_ROUTE_KM` / `SPLIT_MAX_EXTRA_COST_UZS` — Route splitting thresholds.
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_IDS` — Optional Telegram integration.
- `GOOGLE_MAPS_API_KEY` — Only needed for geocoding in `data_tools.py`.

## Employee Data

Employees are stored in `TaxiLocations/employees.json` (JSON array with id, name, gender, address, lat, lon, status). The UI supports Excel import/export. Status values: `22:00`, `23:00`, `00:00`, `02:00`, `CANADA`.

## CI/CD

GitLab CI (`.gitlab-ci.yml`) builds and deploys via Docker Compose on pushes to `main`. Runner tag: `crm-dev`.

## Language Notes

UI text and log messages are in Russian. Cost units are UZS (Uzbekistani som). The app is localized for Tashkent, Uzbekistan (company office coordinates hardcoded in `routing.py`).
