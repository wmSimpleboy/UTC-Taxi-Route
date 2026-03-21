import os
import time
import logging
import functools
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, List, Tuple, Dict, Any

import pandas as pd
import requests as _requests
import webbrowser
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

_osrm_session = _requests.Session()


class RoutingCancelled(Exception):
    pass


class OSRMUnavailableError(RuntimeError):
    """Raised when the OSRM HTTP server cannot be reached or returns an error."""


def _ensure_not_cancelled(is_cancelled: Callable[[], bool] | None) -> None:
    if is_cancelled and is_cancelled():
        raise RoutingCancelled("Routing run cancelled")

# === CONFIGURATION ===
COMPANY_LOCATION: Tuple[float, float] = (41.285062, 69.268777)
MAX_GROUP_SIZE: int = 4
EPSILON_DUPLICATE: float = 0.00001

# === COST SETTINGS (UZS) ===
COST_PER_CAR: float = 15_000
COST_PER_KM: float = 2_200

def _osrm_base_url() -> str:
    return (os.environ.get("OSRM_BASE_URL") or "http://localhost:5000").rstrip("/")


OSRM_BASE_URL: str = _osrm_base_url()


# ---------------------------------------------------------------------------
# Distance helpers
# ---------------------------------------------------------------------------

def fetch_distance_matrix(
    points: List[Tuple[float, float]],
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> List[List[float]]:
    """
    Fetch NxN distance matrix (km) via OSRM Table API.
    points[0] must be the depot (office).
    """
    if not points:
        return []

    _ensure_not_cancelled(is_cancelled)
    coords = ";".join(f"{lon},{lat}" for lat, lon in points)
    base = _osrm_base_url()
    url = f"{base}/table/v1/driving/{coords}?annotations=distance"
    try:
        resp = _osrm_session.get(url, timeout=30)
        resp.raise_for_status()
    except _requests.RequestException as e:
        raise OSRMUnavailableError(
            f"Не удалось подключиться к OSRM ({base}). "
            "Запустите сервер (например: setup_osrm.ps1 / docker) и проверьте OSRM_BASE_URL в .env."
        ) from e
    data = resp.json()
    if data.get("code") != "Ok":
        raise OSRMUnavailableError(
            f"OSRM table error: code={data.get('code')!r} (URL base: {base})"
        )

    return [[d / 1000.0 for d in row] for row in data["distances"]]


# ---------------------------------------------------------------------------
# Preprocessing
# ---------------------------------------------------------------------------

def preprocess_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    """Add tiny offsets to identical coordinates (deterministic, stable across runs)."""
    counts: dict[tuple[float, float], int] = {}
    new_coords: list[tuple[float, float]] = []
    for _, row in df.iterrows():
        lat0, lon0 = float(row["Lat"]), float(row["Lon"])
        key = (lat0, lon0)
        n = counts.get(key, 0)
        counts[key] = n + 1
        if n > 0:
            lat = lat0 + EPSILON_DUPLICATE * n
            lon = lon0 + EPSILON_DUPLICATE * n * 0.7
        else:
            lat, lon = lat0, lon0
        new_coords.append((lat, lon))
    df = df.copy()
    df["Lat"], df["Lon"] = zip(*new_coords)
    return df


# ---------------------------------------------------------------------------
# OR-Tools VRP solver
# ---------------------------------------------------------------------------

_COST_SCALE = 100  # multiply km*COST_PER_KM by this to keep integer precision


@functools.lru_cache(maxsize=1)
def _load_ortools():
    from ortools.constraint_solver import routing_enums_pb2, pywrapcp
    return routing_enums_pb2, pywrapcp


def _time_limit_for_n(n_emp: int) -> int:
    if n_emp <= 10:
        return 5
    if n_emp <= 30:
        return 10
    if n_emp <= 50:
        return 13
    return 15


def _multistart_config(routing_enums_pb2, n_emp: int):
    t = _time_limit_for_n(n_emp)
    return [
        (
            routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION,
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH,
            t,
        ),
        (
            routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC,
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH,
            t,
        ),
        (
            routing_enums_pb2.FirstSolutionStrategy.SAVINGS,
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH,
            t,
        ),
        (
            routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION,
            routing_enums_pb2.LocalSearchMetaheuristic.TABU_SEARCH,
            t,
        ),
    ]


def _build_matrices(
    dist_km: List[List[float]],
    cost_per_km: float,
) -> Tuple[List[List[int]], List[List[int]]]:
    """
    Build scaled integer matrices for OR-Tools.

    Open-route logic:
    - Returning to depot is free (node -> depot cost/distance = 0).
    - This removes implicit "must return to office" penalty.
    """
    n = len(dist_km)
    cost_matrix = [[0] * n for _ in range(n)]
    dist_matrix_int = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            cost_matrix[i][j] = int(round(dist_km[i][j] * cost_per_km * _COST_SCALE))
            dist_matrix_int[i][j] = int(round(dist_km[i][j] * 1000))

    for i in range(1, n):
        cost_matrix[i][0] = 0
        dist_matrix_int[i][0] = 0

    return cost_matrix, dist_matrix_int


def _solve_vrp_once(
    df: pd.DataFrame,
    dist_km: List[List[float]],
    cost_matrix: List[List[int]],
    dist_matrix_int: List[List[int]],
    *,
    first_solution_strategy: int,
    local_search_metaheuristic: int,
    time_limit_s: int,
    random_seed: int,
    cost_per_car: float,
    is_cancelled: Callable[[], bool] | None = None,
) -> Tuple[int, List[Dict[str, Any]]]:
    _ensure_not_cancelled(is_cancelled)
    routing_enums_pb2, pywrapcp = _load_ortools()
    n_emp = len(df)
    n = len(dist_km)
    num_vehicles = n_emp

    manager = pywrapcp.RoutingIndexManager(n, num_vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    def cost_cb(from_idx, to_idx):
        return cost_matrix[manager.IndexToNode(from_idx)][manager.IndexToNode(to_idx)]

    cost_cb_idx = routing.RegisterTransitCallback(cost_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(cost_cb_idx)
    routing.SetFixedCostOfAllVehicles(int(cost_per_car * _COST_SCALE))

    demands = [0] + [1] * n_emp

    def demand_cb(idx):
        return demands[manager.IndexToNode(idx)]

    demand_cb_idx = routing.RegisterUnaryTransitCallback(demand_cb)
    routing.AddDimensionWithVehicleCapacity(
        demand_cb_idx, 0, [MAX_GROUP_SIZE] * num_vehicles, True, "Capacity",
    )

    def dist_cb(from_idx, to_idx):
        return dist_matrix_int[manager.IndexToNode(from_idx)][manager.IndexToNode(to_idx)]

    dist_cb_idx = routing.RegisterTransitCallback(dist_cb)
    routing.AddDimension(dist_cb_idx, 0, 999_999_999, True, "Distance")

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = first_solution_strategy
    params.local_search_metaheuristic = local_search_metaheuristic
    params.time_limit.FromSeconds(time_limit_s)
    params.solution_limit = 2_000_000
    if hasattr(params, "random_seed"):
        setattr(params, "random_seed", random_seed)

    solution = routing.SolveWithParameters(params)
    _ensure_not_cancelled(is_cancelled)
    if not solution:
        return int(1e18), []

    routes: List[Dict[str, Any]] = []
    for v in range(num_vehicles):
        _ensure_not_cancelled(is_cancelled)
        idx = routing.Start(v)
        nodes: List[int] = []
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if node != 0:
                nodes.append(node)
            idx = solution.Value(routing.NextVar(idx))
        if not nodes:
            continue

        total_km = dist_km[0][nodes[0]]
        for i in range(len(nodes) - 1):
            total_km += dist_km[nodes[i]][nodes[i + 1]]

        df_rows = [ni - 1 for ni in nodes]
        group_df = df.iloc[df_rows].copy()
        order = [(row["Lat"], row["Lon"]) for _, row in group_df.iterrows()]
        routes.append({"group": group_df, "order": order, "distance_km": total_km})

    return int(solution.ObjectiveValue()), routes


def solve_vrp(
    df: pd.DataFrame,
    *,
    cost_per_car: float = COST_PER_CAR,
    cost_per_km: float = COST_PER_KM,
    is_cancelled: Callable[[], bool] | None = None,
) -> Tuple[List[Dict[str, Any]], List[List[Dict[str, Any]]]]:
    """
    Solve the Capacitated VRP minimizing total cost =
        sum(COST_PER_CAR for each used vehicle) +
        sum(COST_PER_KM * km for each vehicle's route).

    All multistart configs run in parallel via ThreadPoolExecutor.
    OR-Tools releases the GIL during its C++ solve, so threads give
    real parallelism here.
    """
    n_emp = len(df)
    if n_emp == 0:
        return [], []

    coords = [(row["Lat"], row["Lon"]) for _, row in df.iterrows()]
    all_points = [COMPANY_LOCATION] + coords  # 0 = depot

    dist_km = fetch_distance_matrix(all_points, is_cancelled=is_cancelled)

    cost_matrix, dist_matrix_int = _build_matrices(dist_km, cost_per_km)

    best_obj = int(1e18)
    best_routes: List[Dict[str, Any]] = []
    alt_solutions: List[List[Dict[str, Any]]] = []

    try:
        routing_enums_pb2, _ = _load_ortools()
        multistart = _multistart_config(routing_enums_pb2, n_emp)
    except Exception as exc:
        log.warning("OR-Tools unavailable, using fallback single routes: %s", exc)
        fallback = _fallback_single(df, dist_km)
        return fallback, []

    with ThreadPoolExecutor(max_workers=len(multistart)) as executor:
        futures = {}
        for run_idx, (first_strategy, metaheuristic, run_time) in enumerate(multistart, 1):
            _ensure_not_cancelled(is_cancelled)
            fut = executor.submit(
                _solve_vrp_once,
                df=df,
                dist_km=dist_km,
                cost_matrix=cost_matrix,
                dist_matrix_int=dist_matrix_int,
                first_solution_strategy=first_strategy,
                local_search_metaheuristic=metaheuristic,
                time_limit_s=run_time,
                random_seed=42 + run_idx,
                cost_per_car=cost_per_car,
                is_cancelled=is_cancelled,
            )
            futures[fut] = run_idx

        for fut in as_completed(futures):
            _ensure_not_cancelled(is_cancelled)
            try:
                obj, routes = fut.result()
            except RoutingCancelled:
                raise
            except Exception:
                log.exception("VRP solver run %d failed", futures[fut])
                continue
            if not routes:
                continue
            if obj < best_obj:
                if best_routes:
                    alt_solutions.append(best_routes)
                best_obj = obj
                best_routes = routes
            else:
                alt_solutions.append(routes)

    if not best_routes:
        log.warning("VRP solver found no solution — falling back to single routes")
        fallback = _fallback_single(df, dist_km)
        return fallback, []

    return best_routes, alt_solutions


def _fallback_single(
    df: pd.DataFrame, dist_km: List[List[float]],
) -> List[Dict[str, Any]]:
    """Each employee gets own car (worst case)."""
    return [
        {
            "group": df.iloc[[i]].copy(),
            "order": [(row["Lat"], row["Lon"])],
            "distance_km": dist_km[0][i + 1],
        }
        for i, (_, row) in enumerate(df.iterrows())
    ]


# ---------------------------------------------------------------------------
# Cost helpers
# ---------------------------------------------------------------------------

def calc_car_cost(distance_km: float, cost_per_car: float, cost_per_km: float) -> float:
    return cost_per_car + cost_per_km * distance_km


def format_cost(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


# ---------------------------------------------------------------------------
# Summary & URL generation
# ---------------------------------------------------------------------------

def summarize_routes(
    routes: List[Dict[str, Any]],
    *,
    cost_per_car: float = COST_PER_CAR,
    cost_per_km: float = COST_PER_KM,
) -> str:
    summaries = []
    total_km = 0.0
    total_cost = 0.0

    for i, r in enumerate(routes, 1):
        g = r["group"]
        km = r["distance_km"]
        cost = calc_car_cost(km, cost_per_car, cost_per_km)
        total_km += km
        total_cost += cost

        members = "\n".join(
            f"  - {row['Name']} — {row['Address']}" for _, row in g.iterrows()
        )
        drops = "\n".join(
            f"     {j + 1}. ({lat:.6f}, {lon:.6f})"
            for j, (lat, lon) in enumerate(r["order"])
        )
        summaries.append(
            f"Машина {i} ({len(g)} чел.):\n{members}\n"
            f"  Порядок высадки:\n{drops}\n"
            f"  Километраж: {km:.2f} км\n"
            f"  Стоимость:  {format_cost(cost)} сум"
        )

    sep = "\n" + "=" * 50 + "\n"
    header = f"Офис: {COMPANY_LOCATION}\n"
    footer = (
        f"\n{'=' * 50}\n"
        f"ИТОГО:  {len(routes)} машин(ы)  |  "
        f"{total_km:.2f} км  |  {format_cost(total_cost)} сум\n"
        f"(Вызов машины: {format_cost(cost_per_car)} сум + "
        f"{format_cost(cost_per_km)} сум/км)"
    )
    return header + sep.join(summaries) + footer


def build_yandex_route_urls_from_routes(routes: List[Dict[str, Any]]) -> List[str]:
    urls: List[str] = []
    for r in routes:
        wps = [f"{COMPANY_LOCATION[0]},{COMPANY_LOCATION[1]}"] + [
            f"{lat},{lon}" for lat, lon in r["order"]
        ]
        urls.append("https://yandex.com/maps/?rtext=" + "~".join(wps) + "&rtt=auto")
    return urls


ROUTE_COLORS = [
    "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
    "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990",
]


def fetch_route_geometry(
    points: List[Tuple[float, float]],
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> tuple[List[List[float]], float, bool]:
    """
    Fetch exact road geometry + distance from OSRM Route API.
    Returns: (geometry_latlon, distance_km, exact_ok).
    """
    if len(points) < 2:
        return ([[lat, lon] for lat, lon in points], 0.0, True)

    _ensure_not_cancelled(is_cancelled)
    coords = ";".join(f"{lon},{lat}" for lat, lon in points)
    base = _osrm_base_url()
    url = f"{base}/route/v1/driving/{coords}?overview=full&geometries=geojson"
    try:
        resp = _osrm_session.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") == "Ok" and data.get("routes"):
            route0 = data["routes"][0]
            geojson_coords = route0["geometry"]["coordinates"]
            distance_km = float(route0.get("distance", 0.0)) / 1000.0
            return ([[c[1], c[0]] for c in geojson_coords], distance_km, True)
    except Exception as e:
        log.warning("OSRM route geometry fetch failed: %s", e)
    return ([], 0.0, False)


def _fetch_single_route_map_data(
    i: int,
    r: Dict[str, Any],
    is_cancelled: Callable[[], bool] | None,
) -> Dict[str, Any]:
    """Build map data for one route (used by parallel executor)."""
    _ensure_not_cancelled(is_cancelled)
    all_points = [COMPANY_LOCATION] + list(r["order"])
    geometry, road_distance_km, exact_ok = fetch_route_geometry(
        all_points, is_cancelled=is_cancelled
    )

    waypoints = [{
        "lat": COMPANY_LOCATION[0],
        "lon": COMPANY_LOCATION[1],
        "name": "Офис",
        "label": "O",
    }]
    for j, (_, row) in enumerate(r["group"].iterrows()):
        waypoints.append({
            "lat": r["order"][j][0],
            "lon": r["order"][j][1],
            "name": str(row["Name"]),
            "label": str(j + 1),
        })

    return {
        "waypoints": waypoints,
        "geometry": geometry,
        "exact_geometry": exact_ok,
        "road_distance_km": round(road_distance_km, 2),
        "distance_km": round(r["distance_km"], 2),
        "color": ROUTE_COLORS[i % len(ROUTE_COLORS)],
    }


def build_route_data_for_map(
    routes: List[Dict[str, Any]],
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> List[Dict[str, Any]]:
    """Build structured route data for Leaflet map rendering (parallel geometry fetch)."""
    if not routes:
        return []

    with ThreadPoolExecutor(max_workers=min(len(routes), 8)) as executor:
        futures = {
            executor.submit(_fetch_single_route_map_data, i, r, is_cancelled): i
            for i, r in enumerate(routes)
        }
        results: dict[int, Dict[str, Any]] = {}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results[idx] = fut.result()
            except RoutingCancelled:
                raise
            except Exception:
                log.exception("Geometry fetch for route %d failed", idx)

    return [results[i] for i in sorted(results)]


def open_yandex_routes_from_urls(urls: List[str]) -> None:
    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    webbrowser.register("chrome", None, webbrowser.BackgroundBrowser(chrome_path))
    for i, url in enumerate(urls, 1):
        print(f"Opening Car {i} route: {url}")
        webbrowser.get("chrome").open_new_tab(url)
        time.sleep(2)


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------

def run_routing_for_df_with_urls(
    df: pd.DataFrame,
    *,
    open_routes: bool = False,
    print_summary: bool = True,
    cost_per_car: float | None = None,
    cost_per_km: float | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> Tuple[str, List[str], List[List[str]], List[Dict[str, Any]], List[List[Dict[str, Any]]]]:
    _ensure_not_cancelled(is_cancelled)
    missing = df[df["Lat"].isna() | df["Lon"].isna()]
    if not missing.empty:
        log.warning("Missing coords:\n%s", missing[["Name", "Address"]])
    df = df.dropna(subset=["Lat", "Lon"]).reset_index(drop=True)

    if df.empty:
        msg = "Нет сотрудников с координатами для маршрутизации."
        if print_summary:
            print(msg)
        return msg, [], [], [], []

    if cost_per_car is None:
        cost_per_car = COST_PER_CAR
    if cost_per_km is None:
        cost_per_km = COST_PER_KM

    df = preprocess_duplicates(df)
    best_routes, alt_routes = solve_vrp(
        df,
        cost_per_car=cost_per_car,
        cost_per_km=cost_per_km,
        is_cancelled=is_cancelled,
    )
    _ensure_not_cancelled(is_cancelled)

    summary = summarize_routes(
        best_routes, cost_per_car=cost_per_car, cost_per_km=cost_per_km,
    )
    if print_summary:
        print(summary)

    urls = build_yandex_route_urls_from_routes(best_routes)
    alt_urls = [build_yandex_route_urls_from_routes(rts) for rts in alt_routes]
    if open_routes:
        open_yandex_routes_from_urls(urls)
    return summary, urls, alt_urls, best_routes, alt_routes


def run_routing_for_df(
    df: pd.DataFrame,
    *,
    open_routes: bool = False,
    print_summary: bool = True,
    cost_per_car: float | None = None,
    cost_per_km: float | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> str:
    s, _, _, _, _ = run_routing_for_df_with_urls(
        df,
        open_routes=open_routes,
        print_summary=print_summary,
        cost_per_car=cost_per_car,
        cost_per_km=cost_per_km,
        is_cancelled=is_cancelled,
    )
    return s
