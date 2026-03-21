# Taxi Locations

## Setup

1. Copy `.env.example` to `.env` and set secrets (see comments inside).
2. Install dependencies: `pip install -r requirements.txt` (use your venv).
3. Start OSRM (see `setup_osrm.ps1`) and set `OSRM_BASE_URL` (default `http://localhost:5001`).
4. Run the app from `TaxiLocations/`:
   - `set FLASK_DEBUG=1` (Windows) for local dev, then `python main.py`  
   - Default web port: **5002** (`FLASK_PORT`). OSRM should use another port (e.g. 5001).

Login: set `LOGIN_USERNAME` and `LOGIN_PASSWORD`. With `FLASK_DEBUG=1`, if `LOGIN_PASSWORD` is unset, the app falls back to password `1000` (development only).

## Layout

- `TaxiLocations/ui_app.py` — Flask app  
- `TaxiLocations/routing.py` — VRP + OSRM  
- `TaxiLocations/data_tools.py` — `employees.json` / Excel  
- `TaxiLocations/templates/`, `TaxiLocations/static/` — UI  
