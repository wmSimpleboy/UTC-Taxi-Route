const T = window.TAXI_APP;
const STATUS_UNSET_FILTER = T.statusUnsetFilter;
const STATUS_OPTIONS = ["", "--", ...T.statuses];
function csrfJsonHeaders() {
  return {
    "Content-Type": "application/json",
    "X-CSRFToken": T.csrfToken,
  };
}
function csrfFormHeaders() {
  return { "X-CSRFToken": T.csrfToken };
}


let employees = [];
let filteredEmployees = [];
function getPricing() {
  const carInput = document.getElementById("priceCarInput");
  const kmInput = document.getElementById("priceKmInput");
  const costPerCar = parseFloat(carInput.value) || T.defaultCostPerCar;
  const costPerKm = parseFloat(kmInput.value) || T.defaultCostPerKm;
  return { costPerCar, costPerKm };
}
function getRequestedCars() {
  const input = document.getElementById("requestedCarsInput");
  if (!input) return null;
  const raw = String(input.value || "").trim();
  if (!raw) return null;
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed)) return null;
  return parsed;
}
let selection = new Map();
let activeStatusFilter = "";
let activeRunId = null;
let lastCompletedRunId = null;
let activeRouteController = null;
let lastIssuedRouteRequest = 0;

const totalCountEl = document.getElementById("totalCount");
const selectedCountEl = document.getElementById("selectedCount");
const employeeListEl = document.getElementById("employeeList");
const searchInputEl = document.getElementById("searchInput");
const consoleStatusEl = document.getElementById("consoleStatus");
const summaryEl = document.getElementById("summary");
const routesLinksEl = document.getElementById("routesLinks");
const btnBuildRoutes = document.getElementById("btnBuildRoutes");
const btnCancelRoutes = document.getElementById("btnCancelRoutes");
const btnConfirmRoutes = document.getElementById("btnConfirmRoutes");

function normalizeStatus(value) {
  if (value === null || value === undefined) return "";
  const raw = String(value).trim();
  if (!raw) return "";
  const lower = raw.toLowerCase();
  if (lower === "nan" || lower === "none" || raw === "--") return "";
  return raw;
}

function isUnsetStatus(value) {
  return normalizeStatus(value) === "";
}

function setRouteUiBusy(isBusy) {
  btnBuildRoutes.disabled = isBusy;
  btnCancelRoutes.disabled = !isBusy;
}

function generateRunId() {
  if (window.crypto && window.crypto.randomUUID) {
    return window.crypto.randomUUID();
  }
  return "run-" + Date.now() + "-" + Math.random().toString(16).slice(2);
}

function routeLetter(index) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  if (index < alphabet.length) return alphabet[index];
  const q = Math.floor(index / alphabet.length) - 1;
  const r = index % alphabet.length;
  return routeLetter(q) + alphabet[r];
}

function setStatus(text, color) {
  consoleStatusEl.textContent = text || "";
  if (color) {
    consoleStatusEl.style.color = color;
  }
}

function updateCounters() {
  totalCountEl.textContent = "Всего: " + employees.length;
  let selected = 0;
  for (const id of selection.keys()) {
    if (selection.get(id)) selected++;
  }
  selectedCountEl.textContent = "Выбрано: " + selected;
}

function applyFilters() {
  const q = searchInputEl.value.trim().toLowerCase();
  filteredEmployees = employees.filter(emp => {
    const normalizedStatus = normalizeStatus(emp.status);
    const inStatus =
      !activeStatusFilter
      || (activeStatusFilter === STATUS_UNSET_FILTER && isUnsetStatus(emp.status))
      || normalizedStatus === activeStatusFilter;
    if (!inStatus) return false;
    if (!q) return true;
    const name = (emp.name || "").toLowerCase();
    const addr = (emp.address || "").toLowerCase();
    return name.includes(q) || addr.includes(q);
  });
  renderEmployeeList();
  updateCounters();
}

function renderEmployeeList() {
  employeeListEl.innerHTML = "";
  filteredEmployees.forEach((emp, index) => {
    const row = document.createElement("div");
    row.className = "employee-row";

    const cbContainer = document.createElement("div");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = selection.get(emp.id) || false;
    cb.addEventListener("click", (e) => {
      e.stopPropagation();
      selection.set(emp.id, cb.checked);
      updateCounters();
    });
    cbContainer.appendChild(cb);
    row.appendChild(cbContainer);

    const numCol = document.createElement("div");
    numCol.textContent = String(emp.id);
    row.appendChild(numCol);

    const nameCol = document.createElement("div");
    nameCol.className = "employee-name";
    nameCol.textContent = emp.name;
    row.appendChild(nameCol);

    const addrCol = document.createElement("div");
    addrCol.className = "employee-address";
    addrCol.textContent = emp.address;
    row.appendChild(addrCol);

    const statusCol = document.createElement("div");
    const select = document.createElement("select");
    select.className = "employee-status-select";
    STATUS_OPTIONS.forEach((opt) => {
      const o = document.createElement("option");
      o.value = opt;
      o.textContent = opt || "—";
      if (normalizeStatus(emp.status) === normalizeStatus(opt)) {
        o.selected = true;
      }
      select.appendChild(o);
    });
    select.addEventListener("change", async (e) => {
      const newStatus = e.target.value;
      try {
            const res = await fetch(`/api/employees/${emp.id}/status`, {
              method: "POST",
              headers: csrfJsonHeaders(),
              body: JSON.stringify({ status: newStatus }),
            });
        if (!res.ok) {
          throw new Error(await res.text());
        }
        emp.status = normalizeStatus(newStatus);
        setStatus("Статус сохранён", T.colors.fg_green);
        applyFilters();
      } catch (err) {
        console.error(err);
        setStatus("Ошибка при сохранении статуса", T.colors.fg_red);
      }
    });
    statusCol.appendChild(select);
    row.appendChild(statusCol);

    row.addEventListener("click", () => {
      const current = selection.get(emp.id) || false;
      const next = !current;
      selection.set(emp.id, next);
      cb.checked = next;
      updateCounters();
    });

    employeeListEl.appendChild(row);
  });
}

async function loadEmployees() {
  setStatus("Загрузка сотрудников...", T.colors.fg_yellow);
  try {
    const res = await fetch("/api/employees");
    const data = await res.json();
    employees = data;
    if (!selection.size) {
      for (const emp of employees) selection.set(emp.id, false);
    }
    setStatus("Сотрудники загружены", T.colors.fg_green);
    applyFilters();
  } catch (err) {
    console.error(err);
    setStatus("Ошибка загрузки сотрудников", T.colors.fg_red);
  }
}

async function buildRoutes() {
  const ids = [];
  lastCompletedRunId = null;
  if (btnConfirmRoutes) btnConfirmRoutes.disabled = true;
  for (const [id, checked] of selection.entries()) {
    if (checked) ids.push(id);
  }
  if (!ids.length) {
    alert("Пожалуйста, выберите хотя бы одного сотрудника.");
    return;
  }
  const { costPerCar, costPerKm } = getPricing();
  const requestedCars = getRequestedCars();
  const requestNo = ++lastIssuedRouteRequest;
  const runId = generateRunId();
  activeRunId = runId;
  activeRouteController = new AbortController();
  setRouteUiBusy(true);
  setStatus("Идёт расчёт маршрутов...", T.colors.fg_yellow);
  summaryEl.textContent = "";
  routesLinksEl.innerHTML = "";
  try {
    const res = await fetch("/api/route", {
      method: "POST",
      headers: csrfJsonHeaders(),
      body: JSON.stringify({ ids, costPerCar, costPerKm, requestedCars, runId }),
      signal: activeRouteController.signal,
    });
    if (requestNo !== lastIssuedRouteRequest || runId !== activeRunId) {
      return;
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setStatus(data.error || `Ошибка сервера (${res.status})`, T.colors.fg_red);
      return;
    }
    if (data.cancelled) {
      setStatus("Расчёт отменён", T.colors.fg_yellow);
      if (btnConfirmRoutes) btnConfirmRoutes.disabled = true;
      return;
    }
    summaryEl.textContent = data.summary || "";

    // лучшее решение
    if (Array.isArray(data.routes)) {
      const bestHeader = document.createElement("div");
      bestHeader.textContent = "Лучшее решение:";
      bestHeader.style.marginTop = "4px";
      routesLinksEl.appendChild(bestHeader);

      const urlLines = [];
      data.routes.forEach((url, idx) => {
        const letter = routeLetter(idx);
        const div = document.createElement("div");
        const a = document.createElement("a");
        a.href = url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = `Машина ${letter}`;
        div.appendChild(a);
        routesLinksEl.appendChild(div);
        urlLines.push(`Машина ${letter}: ${url}`);
      });
      if (urlLines.length) {
        summaryEl.textContent += `\n\nURL маршрутов:\n${urlLines.join("\n")}`;
      }
    }

    // альтернативные решения (только ссылки, без авто-открытия)
    if (Array.isArray(data.alternatives)) {
      data.alternatives.forEach((urls, altIdx) => {
        if (!Array.isArray(urls) || !urls.length) return;
        const altHeader = document.createElement("div");
        altHeader.textContent = `Альтернативное решение ${altIdx + 1}:`;
        altHeader.style.marginTop = "8px";
        altHeader.style.fontSize = "12px";
        routesLinksEl.appendChild(altHeader);

        urls.forEach((url, idx) => {
          const letter = routeLetter(idx);
          const div = document.createElement("div");
          const a = document.createElement("a");
          a.href = url;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.textContent = `Машина ${letter} (альт ${altIdx + 1})`;
          div.appendChild(a);
          routesLinksEl.appendChild(div);
        });
      });
    }
    if (Array.isArray(data.map_routes) && data.map_routes.length) {
      renderRoutesOnMap(data.map_routes);
    }
    lastCompletedRunId = runId;
    if (btnConfirmRoutes) btnConfirmRoutes.disabled = false;
    setStatus("Готово", T.colors.fg_green);
  } catch (err) {
    if (err && err.name === "AbortError") {
      setStatus("Запрос отменён", T.colors.fg_yellow);
      return;
    }
    console.error(err);
    setStatus("Ошибка при расчёте маршрутов", T.colors.fg_red);
    if (btnConfirmRoutes) btnConfirmRoutes.disabled = true;
  } finally {
    if (requestNo === lastIssuedRouteRequest) {
      activeRouteController = null;
      activeRunId = null;
      setRouteUiBusy(false);
    }
  }
}

async function cancelRoutes() {
  if (!activeRunId) return;
  const runId = activeRunId;
  if (activeRouteController) {
    activeRouteController.abort();
  }
  try {
        await fetch("/api/route/cancel", {
          method: "POST",
          headers: csrfJsonHeaders(),
          body: JSON.stringify({ runId }),
        });
    setStatus("Отмена расчёта отправлена", T.colors.fg_yellow);
  } catch (err) {
    console.error(err);
    setStatus("Не удалось отменить расчёт", T.colors.fg_red);
  }
}

function getTelegramFilterName() {
  if (!activeStatusFilter) return "Все";
  if (activeStatusFilter === STATUS_UNSET_FILTER) return "Без статуса";
  return activeStatusFilter;
}

async function confirmRoutes() {
  if (!lastCompletedRunId) return;
  if (!btnConfirmRoutes) return;
  btnConfirmRoutes.disabled = true;

  const runId = lastCompletedRunId;
  const filterName = getTelegramFilterName();

  setStatus("Отправка подтверждения...", T.colors.fg_yellow);
  try {
    const res = await fetch("/api/route/confirm", {
      method: "POST",
      headers: csrfJsonHeaders(),
      body: JSON.stringify({ runId, filterName }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || `Ошибка сервера (${res.status})`);
    }

    if (data.telegramSent === false) {
      setStatus(
        "Подтверждено, но Telegram не отправился",
        T.colors.fg_yellow
      );
    } else {
      setStatus("Маршрут подтвержден", T.colors.fg_green);
    }
  } catch (err) {
    console.error(err);
    setStatus("Ошибка при подтверждении маршрута", T.colors.fg_red);
    // Allow re-try.
    btnConfirmRoutes.disabled = false;
  }
}

async function exportExcel() {
  try {
    window.location.href = "/api/employees/export";
  } catch (err) {
    console.error(err);
    setStatus("Ошибка экспорта Excel", T.colors.fg_red);
  }
}

async function importExcel(file) {
  if (!file) return;
  const formData = new FormData();
  formData.append("file", file);
  setStatus("Импорт Excel...", T.colors.fg_yellow);
  try {
    const res = await fetch("/api/employees/import", {
      method: "POST",
      headers: csrfFormHeaders(),
      body: formData,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text);
    }
    setStatus("Импорт завершён, список обновлён", T.colors.fg_green);
    loadEmployees();
  } catch (err) {
    console.error(err);
    setStatus("Ошибка импорта Excel", T.colors.fg_red);
  } finally {
    document.getElementById("importExcelInput").value = "";
  }
}

function copySummary() {
  const text = summaryEl.textContent || "";
  if (!text) return;
  navigator.clipboard.writeText(text).then(
    () => setStatus("Сводка скопирована", T.colors.fg_green),
    () => setStatus("Не удалось скопировать", T.colors.fg_red)
  );
}

document.getElementById("btnSelectAll").addEventListener("click", () => {
  filteredEmployees.forEach(emp => selection.set(emp.id, true));
  renderEmployeeList();
  updateCounters();
});

document.getElementById("btnClearAll").addEventListener("click", () => {
  filteredEmployees.forEach(emp => selection.set(emp.id, false));
  renderEmployeeList();
  updateCounters();
});

document.getElementById("btnReload").addEventListener("click", () => {
  loadEmployees();
});

btnBuildRoutes.addEventListener("click", () => {
  buildRoutes();
});
btnCancelRoutes.addEventListener("click", () => {
  cancelRoutes();
});

btnConfirmRoutes.addEventListener("click", () => {
  confirmRoutes();
});

document.getElementById("btnCopySummary").addEventListener("click", () => {
  copySummary();
});

document.getElementById("btnExportExcel").addEventListener("click", () => {
  exportExcel();
});

const importInput = document.getElementById("importExcelInput");
document.getElementById("btnImportExcel").addEventListener("click", () => {
  importInput.click();
});
importInput.addEventListener("change", (e) => {
  const file = e.target.files && e.target.files[0];
  if (file) {
    importExcel(file);
  }
});

searchInputEl.addEventListener("input", () => {
  applyFilters();
});

document.querySelectorAll("button[data-status-filter]").forEach(btn => {
  btn.addEventListener("click", () => {
    document
      .querySelectorAll("button[data-status-filter]")
      .forEach(b => b.classList.remove("btn-status-active"));
    btn.classList.add("btn-status-active");
    activeStatusFilter = btn.getAttribute("data-status-filter") || "";
    applyFilters();
  });
});

loadEmployees();
setRouteUiBusy(false);

// ========== Leaflet map ==========
const OFFICE = [T.officeLat, T.officeLon];
let leafletMap = null;
let mapLayers = [];
let routeMapData = [];

function initMap() {
  if (leafletMap) return;
  const container = document.getElementById("mapContainer");
  container.style.display = "flex";
  leafletMap = L.map("map").setView(OFFICE, 12);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(leafletMap);
}

function clearMapLayers() {
  if (!leafletMap) return;
  mapLayers.forEach(function (layer) { leafletMap.removeLayer(layer); });
  mapLayers = [];
}

function renderRoutesOnMap(mapRoutes) {
  initMap();
  clearMapLayers();
  routeMapData = mapRoutes;

  const legendEl = document.getElementById("mapLegend");
  legendEl.innerHTML = "";
  const allBounds = L.latLngBounds();

  const officeMarker = L.marker(OFFICE, {
    icon: L.divIcon({
      className: "",
      html: '<div style="background:#1e1e2e;color:#fff;border:2px solid #fff;border-radius:50%;width:26px;height:26px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px;box-shadow:0 2px 6px rgba(0,0,0,.45)">O</div>',
      iconSize: [26, 26],
      iconAnchor: [13, 13],
    }),
    zIndexOffset: 1000,
  }).addTo(leafletMap);
  officeMarker.bindTooltip("Офис");
  mapLayers.push(officeMarker);
  allBounds.extend(OFFICE);

  let hasInexactRoute = false;
  mapRoutes.forEach(function (route, idx) {
    if (!route.exact_geometry || !Array.isArray(route.geometry) || !route.geometry.length) {
      hasInexactRoute = true;
      return;
    }
    var latlngs = route.geometry.map(function (p) { return [p[0], p[1]]; });
    var polyline = L.polyline(latlngs, {
      color: route.color,
      weight: 5,
      opacity: 0.75,
    }).addTo(leafletMap);
    mapLayers.push(polyline);
    allBounds.extend(polyline.getBounds());

    route.waypoints.forEach(function (wp, wpIdx) {
      if (wpIdx === 0) return;
      var marker = L.marker([wp.lat, wp.lon], {
        icon: L.divIcon({
          className: "",
          html: '<div style="background:' + route.color + ';color:#fff;border:2px solid #fff;border-radius:50%;width:22px;height:22px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:11px;box-shadow:0 2px 6px rgba(0,0,0,.35)">' + wp.label + '</div>',
          iconSize: [22, 22],
          iconAnchor: [11, 11],
        }),
      }).addTo(leafletMap);
      marker.bindTooltip(wp.name);
      mapLayers.push(marker);
    });

    var item = document.createElement("span");
    item.className = "map-legend-item";
    var letter = routeLetter(idx);
    var kmLabel = (typeof route.road_distance_km === "number" && route.road_distance_km > 0)
      ? route.road_distance_km
      : route.distance_km;
    item.innerHTML = '<span class="map-legend-dot" style="background:' + route.color + '"></span>Машина ' + letter + ' (' + (route.waypoints.length - 1) + ' чел, ' + kmLabel + ' км)';
    item.addEventListener("click", function () { focusRoute(idx); });
    legendEl.appendChild(item);
  });

  if (allBounds.isValid()) {
    leafletMap.fitBounds(allBounds, { padding: [40, 40] });
  }
  setTimeout(function () { leafletMap.invalidateSize(); }, 150);
  if (hasInexactRoute) {
    setStatus("Часть маршрутов не отрисована: OSRM не вернул точную дорожную геометрию.", T.colors.fg_yellow);
  }
}

function focusRoute(idx) {
  if (!routeMapData[idx]) return;
  var route = routeMapData[idx];
  if (!route.exact_geometry || !Array.isArray(route.geometry) || !route.geometry.length) {
    setStatus("Для этого маршрута нет точной дорожной линии.", T.colors.fg_yellow);
    return;
  }
  var latlngs = route.geometry.map(function (p) { return [p[0], p[1]]; });
  var bounds = L.latLngBounds(latlngs);
  if (bounds.isValid()) {
    leafletMap.fitBounds(bounds, { padding: [60, 60] });
  }
  document.querySelectorAll(".map-legend-item").forEach(function (el, i) {
    el.classList.toggle("active", i === idx);
  });
}
