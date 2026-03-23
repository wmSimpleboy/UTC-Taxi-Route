# Taxi Locations

## Setup

1. Copy `.env.example` to `.env` and set secrets (see comments inside).
2. Install dependencies: `pip install -r requirements.txt` (use your venv).
3. Start OSRM — **Docker Compose** (recommended) or `setup_osrm.ps1`. Set `OSRM_BASE_URL` if yours differs (default `http://localhost:5000`).
   - `docker compose up -d osrm` — только OSRM, данные в `./osrm-data` (сначала подготовьте `.osrm`, см. комментарии в `docker-compose.yml`).
   - `docker compose --profile full up -d --build` — OSRM + веб-приложение в контейнере (`OSRM_BASE_URL` внутри сети: `http://osrm:5000`).
4. Run the app from `TaxiLocations/` (если не используете профиль `full`):
   - `set FLASK_DEBUG=1` (Windows) for local dev, then `python main.py`  
   - Default web port: **5002** (`FLASK_PORT`). OSRM is usually on **5000** — do not run Flask on the same port.

Login: set `LOGIN_USERNAME` and `LOGIN_PASSWORD`. With `FLASK_DEBUG=1`, if `LOGIN_PASSWORD` is unset, the app falls back to password `1000` (development only).

## Route splitting

Routes longer than **25 km** are automatically split into two cars when the total cost increase stays within **10 000 UZS**. Configure via `.env`:
- `SPLIT_MIN_ROUTE_KM` (default `25`) — minimum route distance to consider splitting.
- `SPLIT_MAX_EXTRA_COST_UZS` (default `10000`) — max acceptable cost increase for the split.

## Layout

- `TaxiLocations/ui_app.py` — Flask app  
- `TaxiLocations/routing.py` — VRP + OSRM  
- `TaxiLocations/data_tools.py` — `employees.json` / Excel  
- `TaxiLocations/templates/`, `TaxiLocations/static/` — UI  
