import json
import os
import re
from pathlib import Path
from threading import Lock
from typing import Iterable, Union

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

import googlemaps  # noqa: E402

EMPLOYEES_JSON = Path(__file__).with_name("employees.json")
EMPLOYEES_EXCEL = Path(__file__).with_name("employees.xlsx")

# Serialize all reads/writes to employees.json (Flask is multi-threaded).
_EMPLOYEES_FILE_LOCK = Lock()

# Allowed status values (must match ui_app STATUSES when non-empty).
ALLOWED_STATUSES = frozenset({"22:00", "23:00", "00:00", "02:00", "CANADA"})

_gmaps_client: googlemaps.Client | None = None


def _get_gmaps_api_key() -> str:
    return (os.environ.get("GOOGLE_MAPS_API_KEY") or "").strip()


def _get_gmaps_client() -> googlemaps.Client:
    """Lazy init so importing this module does not require a valid API key."""
    global _gmaps_client
    if _gmaps_client is not None:
        return _gmaps_client
    key = _get_gmaps_api_key()
    if not key:
        raise RuntimeError(
            "GOOGLE_MAPS_API_KEY is not set. Set it in .env for geocoding helpers."
        )
    _gmaps_client = googlemaps.Client(key=key)
    return _gmaps_client


def normalize_employee_status(value: object) -> str:
    """
    Normalize status string for storage: map variants to allowed values or ''.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    lower = raw.lower()
    if lower in {"nan", "none"} or raw == "--":
        return ""
    # HH:MM:SS -> HH:MM
    m = re.match(r"^(\d{1,2}):(\d{2}):(\d{2})$", raw)
    if m:
        h, mi, _ = m.groups()
        raw = f"{int(h):02d}:{mi}"
    if raw in ALLOWED_STATUSES:
        return raw
    return ""


def normalize_employee_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize raw DataFrame columns to the standard schema:
    Name, Gender, Address, Lat, Lon.
    """
    # Try to be a bit flexible with incoming column names
    rename_map = {}
    for col in df.columns:
        col_lower = str(col).strip().lower()
        if col_lower in {"name", "fio", "full_name"}:
            rename_map[col] = "Name"
        elif col_lower in {"gender", "sex"}:
            rename_map[col] = "Gender"
        elif col_lower in {"address", "adres"}:
            rename_map[col] = "Address"
        elif col_lower in {"lat", "latitude", "shir"}:
            rename_map[col] = "Lat"
        elif col_lower in {"lon", "lng", "longitude", "dolg"}:
            rename_map[col] = "Lon"

    if rename_map:
        df = df.rename(columns=rename_map)

    missing = [c for c in ["Name", "Gender", "Address", "Lat", "Lon"] if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in employees table: {missing}")

    return df[["Name", "Gender", "Address", "Lat", "Lon"]].copy()


def dataframe_to_employees_json_records(df: pd.DataFrame) -> list[dict]:
    """
    Convert normalized DataFrame to list of JSON records using the schema:
    {
      "id": int,
      "name": str,
      "gender": str,
      "address": str,
      "lat": float,
      "lon": float
    }
    """
    df = normalize_employee_dataframe(df)
    records = []
    for idx, row in df.reset_index(drop=True).iterrows():
        records.append(
            {
                "id": int(idx),
                "name": str(row["Name"]),
                "gender": str(row["Gender"]),
                "address": str(row["Address"]),
                "lat": float(row["Lat"]),
                "lon": float(row["Lon"]),
                # status is optional; default to empty string
                "status": "",
            }
        )
    return records


def save_employees_json(records: Iterable[dict], path: Union[str, Path] = EMPLOYEES_JSON) -> None:
    path = Path(path)
    with _EMPLOYEES_FILE_LOCK:
        with path.open("w", encoding="utf-8") as f:
            json.dump(list(records), f, ensure_ascii=False, indent=2)


def excel_to_json(
    source_path: Union[str, Path] = None,
    json_path: Union[str, Path] = EMPLOYEES_JSON,
) -> None:
    """
    Convert employees Excel/CSV file to employees.json.

    If source_path is None, tries employees.xlsx then employees.csv
    in the same directory as this script.
    """
    base_dir = Path(__file__).parent
    if source_path is None:
        xlsx = base_dir / "employees.xlsx"
        csv = base_dir / "employees.csv"
        if xlsx.exists():
            source_path = xlsx
        elif csv.exists():
            source_path = csv
        else:
            raise FileNotFoundError(
                "No employees.xlsx or employees.csv found next to the script. "
                "Please place your employee table there or pass source_path explicitly."
            )
    source_path = Path(source_path)

    if source_path.suffix.lower() in {".xlsx", ".xls"}:
        df = pd.read_excel(source_path)
    elif source_path.suffix.lower() in {".csv", ".txt"}:
        df = pd.read_csv(source_path)
    else:
        raise ValueError(f"Unsupported source file type: {source_path.suffix}")

    records = dataframe_to_employees_json_records(df)
    save_employees_json(records, json_path)


def load_employees_from_json(path: Union[str, Path] = EMPLOYEES_JSON) -> pd.DataFrame:
    """
    Load employees from employees.json into a DataFrame with columns:
    Id, Name, Gender, Address, Lat, Lon, Status.

    The underlying JSON records may or may not contain a "status" field.
    """
    path = Path(path)
    with _EMPLOYEES_FILE_LOCK:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)

    if not isinstance(raw, list):
        raise ValueError("employees.json must contain a list of employee records.")

    rows = []
    for rec in raw:
        rows.append(
            {
                "Id": rec.get("id"),
                "Name": rec.get("name"),
                "Gender": rec.get("gender"),
                "Address": rec.get("address"),
                "Lat": rec.get("lat"),
                "Lon": rec.get("lon"),
                "Status": rec.get("status", ""),
            }
        )

    df = pd.DataFrame(rows)
    return df


def export_employees_to_excel(path: Union[str, Path] = EMPLOYEES_EXCEL) -> Path:
    """
    Export current employees.json to an Excel file with one column per JSON field.
    """
    path = Path(path)
    df = load_employees_from_json()
    # keep column order stable
    cols = [c for c in ["Id", "Name", "Gender", "Address", "Lat", "Lon", "Status"] if c in df.columns]
    df[cols].to_excel(path, index=False)
    return path


def import_employees_from_excel(path: Union[str, Path] = EMPLOYEES_EXCEL) -> None:
    """
    Read employees from an Excel file and overwrite employees.json.

    Expected columns (case-insensitive): id, name, gender, address, lat, lon, status.
    Extra columns are ignored.
    """
    path = Path(path)
    df = pd.read_excel(path)
    # normalize column names
    rename_map: dict[str, str] = {}
    for col in df.columns:
        cl = str(col).strip().lower()
        if cl == "id":
            rename_map[col] = "id"
        elif cl in {"name", "fio", "full_name"}:
            rename_map[col] = "name"
        elif cl in {"gender", "sex"}:
            rename_map[col] = "gender"
        elif cl in {"address", "adres"}:
            rename_map[col] = "address"
        elif cl in {"lat", "latitude", "shir"}:
            rename_map[col] = "lat"
        elif cl in {"lon", "lng", "longitude", "dolg"}:
            rename_map[col] = "lon"
        elif cl in {"status"}:
            rename_map[col] = "status"
    if rename_map:
        df = df.rename(columns=rename_map)

    records: list[dict] = []
    for idx, row in df.reset_index(drop=True).iterrows():
        st = normalize_employee_status(row.get("status", ""))
        rec = {
            "id": int(row.get("id", idx)),
            "name": str(row.get("name", "")),
            "gender": str(row.get("gender", "Unknown")),
            "address": str(row.get("address", "")),
            "lat": float(row.get("lat")) if pd.notna(row.get("lat")) else None,
            "lon": float(row.get("lon")) if pd.notna(row.get("lon")) else None,
            "status": st,
        }
        records.append(rec)

    save_employees_json(records, EMPLOYEES_JSON)


def save_employee_status(
    employee_id: int,
    status: str,
    path: Union[str, Path] = EMPLOYEES_JSON,
) -> None:
    """
    Update the status of a single employee (by id) in employees.json.

    This keeps all other fields intact and only changes the "status" field.
    """
    path = Path(path)
    with _EMPLOYEES_FILE_LOCK:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)

        if not isinstance(raw, list):
            raise ValueError("employees.json must contain a list of employee records.")

        found = False
        for rec in raw:
            if rec.get("id") == employee_id:
                rec["status"] = status
                found = True
                break

        if not found:
            raise ValueError(f"Employee with id={employee_id} not found in employees.json")

        with path.open("w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)


def _geocode_address(address: str) -> tuple[float, float]:
    """Geocode a single address into (lat, lon) using Google Maps."""
    result = _get_gmaps_client().geocode(address)
    if not result:
        raise ValueError(f"Could not geocode address: {address!r}")
    loc = result[0]["geometry"]["location"]
    return float(loc["lat"]), float(loc["lng"])


def enrich_simple_json_with_geocoding(
    source_path: Union[str, Path] = EMPLOYEES_JSON,
    json_path: Union[str, Path] = EMPLOYEES_JSON,
) -> None:
    """
    Read a simple employees.json (with fields like 'fio' and 'address'),
    geocode each address, and write a full employees.json with schema:
    id, name, gender, address, lat, lon.
    """
    source_path = Path(source_path)
    with source_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, list):
        raise ValueError("Source employees JSON must be a list of records.")

    records: list[dict] = []
    for idx, rec in enumerate(raw):
        name = rec.get("name") or rec.get("fio")
        address = rec.get("address")
        if not name or not address:
            raise ValueError(f"Record #{idx} is missing name/fio or address: {rec!r}")

        lat, lon = _geocode_address(address)

        records.append(
            {
                "id": int(idx),
                "name": str(name),
                "gender": "Unknown",
                "address": str(address),
                "lat": float(lat),
                "lon": float(lon),
            }
        )

    save_employees_json(records, json_path)


if __name__ == "__main__":
    # Helper entry point:
    # 1) If you work from Excel/CSV, use excel_to_json().
    # 2) If you have a simple JSON with only fio/address, use
    #    enrich_simple_json_with_geocoding() to fill coordinates.
    enrich_simple_json_with_geocoding()
