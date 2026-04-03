from __future__ import annotations

import os
import secrets
import time
from dataclasses import asdict, dataclass
from functools import wraps
from threading import Lock
from typing import List
from uuid import uuid4

import pandas as pd
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file, session, redirect, url_for
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError

from db import save_confirmed_trip
from telegram_notify import send_message_to_users, send_message_to_superusers

from data_tools import (
    load_employees_from_json,
    save_employee_status,
    export_employees_to_excel,
    import_employees_from_excel,
    EMPLOYEES_EXCEL,
)
from routing import (
    run_routing_for_df_with_urls,
    build_route_data_for_map,
    RoutingCancelled,
    RoutingInputError,
    OSRMUnavailableError,
    COST_PER_CAR,
    COST_PER_KM,
    COMPANY_LOCATION,
)

load_dotenv()

BG = "#1e1e2e"
BG_CARD = "#2a2a3d"
BG_INPUT = "#33334d"
BG_CONSOLE = "#181825"
FG = "#cdd6f4"
FG_DIM = "#6c7086"
FG_ACCENT = "#89b4fa"
FG_GREEN = "#a6e3a1"
FG_YELLOW = "#f9e2af"
FG_RED = "#f38ba8"
BORDER = "#45475a"
HIGHLIGHT = "#313244"

STATUSES = ["22:00", "23:00", "00:00", "02:00", "CANADA"]
STATUS_UNSET_FILTER = "__UNSET__"

_ROUTE_CANCEL_LOCK = Lock()
_ROUTE_CANCEL_FLAGS: dict[str, bool] = {}
_ROUTE_CANCEL_META: dict[str, float] = {}
_CANCEL_TTL_S = 3600.0

_ROUTE_RESULTS_LOCK = Lock()
# runId -> cached route data for confirmation (best routes + URLs).
_ROUTE_RESULTS: dict[str, dict] = {}
_ROUTE_RESULTS_META: dict[str, float] = {}
_ROUTE_RESULTS_TTL_S = 3600.0


def _secret_key() -> str:
    k = os.environ.get("FLASK_SECRET_KEY", "").strip()
    if k:
        return k
    if os.environ.get("FLASK_DEBUG") == "1":
        return "dev-only-insecure-secret-change-me"
    raise RuntimeError("Set FLASK_SECRET_KEY in the environment (see .env.example).")


def _plain_password_fallback() -> str | None:
    p = os.environ.get("LOGIN_PASSWORD")
    if p is not None and str(p).strip() != "":
        return str(p).strip()
    if os.environ.get("FLASK_DEBUG") == "1":
        return "1000"
    return None


def _verify_login(username: str, password: str) -> bool:
    expected_user = os.environ.get("LOGIN_USERNAME", "255").strip()
    if username != expected_user:
        return False
    plain = _plain_password_fallback()
    if plain is None:
        return False
    return secrets.compare_digest(password, plain)


def _prune_stale_cancel_flags() -> None:
    now = time.time()
    with _ROUTE_CANCEL_LOCK:
        dead = [
            rid
            for rid, ts in _ROUTE_CANCEL_META.items()
            if now - ts > _CANCEL_TTL_S
        ]
        for rid in dead:
            _ROUTE_CANCEL_FLAGS.pop(rid, None)
            _ROUTE_CANCEL_META.pop(rid, None)


def _set_run_cancelled(run_id: str) -> None:
    with _ROUTE_CANCEL_LOCK:
        _ROUTE_CANCEL_FLAGS[run_id] = True


def _is_run_cancelled(run_id: str) -> bool:
    with _ROUTE_CANCEL_LOCK:
        return bool(_ROUTE_CANCEL_FLAGS.get(run_id, False))


def _register_run(run_id: str) -> None:
    _prune_stale_cancel_flags()
    with _ROUTE_CANCEL_LOCK:
        _ROUTE_CANCEL_FLAGS[run_id] = False
        _ROUTE_CANCEL_META[run_id] = time.time()


def _cleanup_run(run_id: str) -> None:
    with _ROUTE_CANCEL_LOCK:
        _ROUTE_CANCEL_FLAGS.pop(run_id, None)
        _ROUTE_CANCEL_META.pop(run_id, None)


def _prune_stale_route_results() -> None:
    now = time.time()
    with _ROUTE_RESULTS_LOCK:
        dead = [
            rid
            for rid, ts in _ROUTE_RESULTS_META.items()
            if now - ts > _ROUTE_RESULTS_TTL_S
        ]
        for rid in dead:
            _ROUTE_RESULTS.pop(rid, None)
            _ROUTE_RESULTS_META.pop(rid, None)


def _cache_route_for_confirmation(
    *,
    run_id: str,
    employee_ids: List[int],
    filter_name: str | None,
    best_routes: List[dict],
    route_urls: List[str],
    summary: str,
    cost_per_car: float,
    cost_per_km: float,
    requested_cars: int | None,
) -> None:
    _prune_stale_route_results()
    with _ROUTE_RESULTS_LOCK:
        _ROUTE_RESULTS[run_id] = {
            "employee_ids": list(employee_ids),
            "best_routes": best_routes,
            "route_urls": list(route_urls),
            "summary": summary,
            "cost_per_car": float(cost_per_car),
            "cost_per_km": float(cost_per_km),
            "requested_cars": requested_cars,
        }
        _ROUTE_RESULTS_META[run_id] = time.time()


def _route_letter(index: int) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if index < len(alphabet):
        return alphabet[index]
    q = index // len(alphabet) - 1
    r = index % len(alphabet)
    return _route_letter(q) + alphabet[r]


def _format_filter_name_for_tg(filter_name: str) -> str:
    # The client passes user-friendly names already, but keep a safe fallback.
    val = (filter_name or "").strip()
    if not val:
        return "Все"
    return val


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )
    app.secret_key = _secret_key()
    app.config.setdefault("WTF_CSRF_TIME_LIMIT", None)

    CSRFProtect(app)

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):  # noqa: ARG001
        if request.path.startswith("/api/"):
            return jsonify({"error": "CSRF token missing or invalid"}), 400
        return (getattr(e, "description", None) or "CSRF validation failed", 400)

    @dataclass
    class EmployeeDTO:
        id: int
        name: str
        address: str
        lat: float
        lon: float
        status: str

    def _to_dto(df: pd.DataFrame) -> List[EmployeeDTO]:
        employees: List[EmployeeDTO] = []
        for _, row in df.iterrows():
            employees.append(
                EmployeeDTO(
                    id=int(row["Id"]),
                    name=str(row["Name"]),
                    address=str(row["Address"]),
                    lat=float(row["Lat"]),
                    lon=float(row["Lon"]),
                    status=_normalize_status(row.get("Status", "")) if "Status" in row else "",
                )
            )
        return employees

    def _normalize_status(value: object) -> str:
        if value is None:
            return ""
        raw = str(value).strip()
        if not raw:
            return ""
        if raw.lower() in {"nan", "none"} or raw == "--":
            return ""
        return raw

    def login_required(fn):
        @wraps(fn)
        def _wrapped(*args, **kwargs):
            if not session.get("logged_in"):
                return redirect(url_for("login"))
            return fn(*args, **kwargs)

        return _wrapped

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("logged_in"):
            return redirect(url_for("index"))

        error = None
        if request.method == "POST":
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            if _verify_login(username, password):
                session["logged_in"] = True
                return redirect(url_for("index"))
            error = "Неверный логин или пароль."

        return render_template("login.html", error=error)

    @app.get("/logout")
    def logout():
        session.pop("logged_in", None)
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def index() -> str:
        return render_template(
            "index.html",
            bg=BG,
            bg_card=BG_CARD,
            bg_input=BG_INPUT,
            bg_console=BG_CONSOLE,
            fg=FG,
            fg_dim=FG_DIM,
            fg_accent=FG_ACCENT,
            fg_green=FG_GREEN,
            fg_yellow=FG_YELLOW,
            fg_red=FG_RED,
            border=BORDER,
            highlight=HIGHLIGHT,
            statuses=STATUSES,
            cost_per_car=COST_PER_CAR,
            cost_per_km=COST_PER_KM,
            office_lat=COMPANY_LOCATION[0],
            office_lon=COMPANY_LOCATION[1],
            status_unset_filter=STATUS_UNSET_FILTER,
        )

    @app.get("/api/employees")
    @login_required
    def api_employees():
        df = load_employees_from_json()
        employees = _to_dto(df)
        return jsonify([asdict(e) for e in employees])

    @app.get("/api/employees/export")
    @login_required
    def api_export_employees():
        path = export_employees_to_excel()
        return send_file(
            path,
            as_attachment=True,
            download_name="employees.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @app.post("/api/employees/import")
    @login_required
    def api_import_employees():
        if "file" not in request.files:
            return jsonify({"error": "file field is required"}), 400
        file = request.files["file"]
        if not file.filename:
            return jsonify({"error": "empty filename"}), 400

        EMPLOYEES_EXCEL.parent.mkdir(parents=True, exist_ok=True)
        file.save(EMPLOYEES_EXCEL)
        import_employees_from_excel()
        return jsonify({"ok": True})

    @app.post("/api/employees/<int:employee_id>/status")
    @login_required
    def api_update_status(employee_id: int):
        data = request.get_json(silent=True) or {}
        status = _normalize_status(data.get("status", ""))
        if status and status not in STATUSES:
            return (
                jsonify({"error": f"Invalid status {status!r}. Allowed: {STATUSES}"}),
                400,
            )
        save_employee_status(employee_id, status)
        return jsonify({"ok": True})

    @app.post("/api/route")
    @login_required
    def api_route():
        data = request.get_json(silent=True) or {}
        run_id = str(data.get("runId", "")).strip() or str(uuid4())
        _register_run(run_id)
        ids = data.get("ids") or []
        if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
            _cleanup_run(run_id)
            return jsonify({"error": "ids must be a list of integers"}), 400
        if not ids:
            _cleanup_run(run_id)
            return jsonify({"error": "no employee ids provided"}), 400

        df = load_employees_from_json()
        df_selected = df[df["Id"].isin(ids)].reset_index(drop=True)
        if df_selected.empty:
            _cleanup_run(run_id)
            return jsonify({"error": "no employees found for given ids"}), 400

        cost_per_car = data.get("costPerCar")
        cost_per_km = data.get("costPerKm")
        requested_cars_raw = data.get("requestedCars", None)
        requested_cars: int | None = None
        if requested_cars_raw not in (None, ""):
            try:
                requested_cars = int(requested_cars_raw)
            except (TypeError, ValueError):
                _cleanup_run(run_id)
                return jsonify({"error": "requestedCars must be an integer >= 1"}), 400
            if requested_cars < 1:
                _cleanup_run(run_id)
                return jsonify({"error": "requestedCars must be >= 1"}), 400
        try:
            summary, routes, alternatives, best_routes, alt_routes = run_routing_for_df_with_urls(
                df_selected[["Name", "Gender", "Address", "Lat", "Lon"]],
                open_routes=False,
                print_summary=False,
                cost_per_car=cost_per_car,
                cost_per_km=cost_per_km,
                requested_cars=requested_cars,
                is_cancelled=lambda: _is_run_cancelled(run_id),
            )
            if _is_run_cancelled(run_id):
                return jsonify({"cancelled": True, "runId": run_id})
            map_data = (
                build_route_data_for_map(
                    best_routes,
                    is_cancelled=lambda: _is_run_cancelled(run_id),
                )
                if best_routes
                else []
            )
            if _is_run_cancelled(run_id):
                return jsonify({"cancelled": True, "runId": run_id})

            resolved_cost_per_car = (
                float(cost_per_car) if cost_per_car is not None else float(COST_PER_CAR)
            )
            resolved_cost_per_km = (
                float(cost_per_km) if cost_per_km is not None else float(COST_PER_KM)
            )
            _cache_route_for_confirmation(
                run_id=run_id,
                employee_ids=ids,
                filter_name=None,
                best_routes=best_routes,
                route_urls=routes,
                summary=summary,
                cost_per_car=resolved_cost_per_car,
                cost_per_km=resolved_cost_per_km,
                requested_cars=requested_cars,
            )
            return jsonify(
                {
                    "summary": summary,
                    "routes": routes,
                    "alternatives": alternatives,
                    "map_routes": map_data,
                    "runId": run_id,
                }
            )
        except RoutingCancelled:
            return jsonify({"cancelled": True, "runId": run_id})
        except RoutingInputError as e:
            return jsonify({"error": str(e)}), 400
        except OSRMUnavailableError as e:
            return jsonify({"error": str(e)}), 503
        finally:
            _cleanup_run(run_id)

    @app.post("/api/route/cancel")
    @login_required
    def api_route_cancel():
        data = request.get_json(silent=True) or {}
        run_id = str(data.get("runId", "")).strip()
        if not run_id:
            return jsonify({"error": "runId is required"}), 400
        _set_run_cancelled(run_id)
        return jsonify({"ok": True, "runId": run_id})

    @app.post("/api/route/confirm")
    @login_required
    def api_route_confirm():
        data = request.get_json(silent=True) or {}
        run_id = str(data.get("runId", "")).strip()
        filter_name = str(data.get("filterName", "")).strip()

        if not run_id:
            return jsonify({"error": "runId is required"}), 400
        filter_name = _format_filter_name_for_tg(filter_name)

        _prune_stale_route_results()
        with _ROUTE_RESULTS_LOCK:
            payload = _ROUTE_RESULTS.pop(run_id, None)
            _ROUTE_RESULTS_META.pop(run_id, None)

        if not payload:
            return jsonify({"error": "Unknown or expired runId"}), 404

        best_routes: list[dict] = payload["best_routes"]
        route_urls: list[str] = payload["route_urls"]
        summary: str = payload["summary"]
        cost_per_car: float = float(payload["cost_per_car"])
        cost_per_km: float = float(payload["cost_per_km"])
        employee_ids: list[int] = payload["employee_ids"]
        requested_cars: int | None = payload.get("requested_cars")

        total_km = 0.0
        total_cost = 0.0
        routes_json: list[dict] = []

        for i, (r, url) in enumerate(zip(best_routes, route_urls)):
            km = float(r.get("distance_km", 0.0))
            total_km += km
            total_cost += float(cost_per_car) + float(cost_per_km) * km

            passengers: list[dict] = []
            for _, row in r["group"].iterrows():
                passengers.append(
                    {
                        "name": str(row.get("Name", "")),
                        "address": str(row.get("Address", "")),
                        "lat": float(row.get("Lat")),
                        "lon": float(row.get("Lon")),
                    }
                )

            routes_json.append(
                {
                    "car": _route_letter(i),
                    "url": url,
                    "passengers": passengers,
                    "distance_km": km,
                    # Keep the actual order coordinates for traceability.
                    "order": [(float(lat), float(lon)) for lat, lon in r.get("order", [])],
                }
            )

        trip_id = save_confirmed_trip(
            filter_name=filter_name,
            num_cars=len(best_routes),
            num_employees=len(employee_ids),
            total_km=total_km,
            total_cost=total_cost,
            cost_per_car=cost_per_car,
            cost_per_km=cost_per_km,
            summary=summary,
            routes_json=routes_json,
        )

        telegram_sent = True
        telegram_error: str | None = None

        try:
            # --- Build per-car lines for users (basic) ---
            user_car_lines: list[str] = []
            for i, r in enumerate(best_routes):
                group_size = int(len(r["group"]))
                names = ", ".join(
                    str(row.get("Name", "")).strip() for _, row in r["group"].iterrows()
                )
                user_car_lines.append(
                    f"Машина {_route_letter(i)} ({group_size} чел.): {names}"
                )

            user_text = (
                f"{filter_name}\nГруппы машин:\n" + "\n".join(user_car_lines)
            )

            # --- Build detailed lines for superusers (with costs) ---
            super_car_lines: list[str] = []
            for i, (r, url) in enumerate(zip(best_routes, route_urls)):
                group_size = int(len(r["group"]))
                km = float(r.get("distance_km", 0.0))
                car_cost = float(cost_per_car) + float(cost_per_km) * km
                names = ", ".join(
                    str(row.get("Name", "")).strip() for _, row in r["group"].iterrows()
                )
                super_car_lines.append(
                    f"Машина {_route_letter(i)} ({group_size} чел.): {names}\n"
                    f"  {km:.1f} км — {car_cost:,.0f} сум".replace(",", " ")
                )

            super_text = (
                f"{filter_name}\n"
                f"Машин: {len(best_routes)}, Сотрудников: {len(employee_ids)}\n"
                f"Общий км: {total_km:.1f}, Общая сумма: {total_cost:,.0f} сум\n\n".replace(",", " ")
                + "\n".join(super_car_lines)
            )

            # Send basic info to users
            send_message_to_users(text=user_text)
            # Send detailed info to superusers
            send_message_to_superusers(text=super_text)

            # Send route URLs to all
            for i, url in enumerate(route_urls[: len(best_routes)]):
                send_message_to_users(
                    text=f"Машина {_route_letter(i)}: {url}"
                )
                send_message_to_superusers(
                    text=f"Машина {_route_letter(i)}: {url}"
                )
        except Exception as exc:  # noqa: BLE001
            telegram_sent = False
            telegram_error = str(exc)
            app.logger.exception("Telegram send failed")

        return jsonify(
            {
                "ok": True,
                "tripId": trip_id,
                "telegramSent": telegram_sent,
                "telegramError": telegram_error,
            }
        )

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("FLASK_PORT", "5002"))
    debug = os.environ.get("FLASK_DEBUG") == "1"
    app.run(debug=debug, port=port)
