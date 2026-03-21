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
        try:
            summary, routes, alternatives, best_routes, alt_routes = run_routing_for_df_with_urls(
                df_selected[["Name", "Gender", "Address", "Lat", "Lon"]],
                open_routes=False,
                print_summary=False,
                cost_per_car=cost_per_car,
                cost_per_km=cost_per_km,
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

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("FLASK_PORT", "5002"))
    debug = os.environ.get("FLASK_DEBUG") == "1"
    app.run(debug=debug, port=port)
