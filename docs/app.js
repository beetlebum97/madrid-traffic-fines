const form = document.querySelector("#filters");
const results = document.querySelector("#results");
const message = document.querySelector("#message");
const mapElement = document.querySelector("#map");
const mapMessage = document.querySelector("#map-message");
const mapSummary = document.querySelector("#map-summary");
const mapButton = document.querySelector("#load-map");
const monthNames = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
const integer = new Intl.NumberFormat("es-ES");
const percent = new Intl.NumberFormat("es-ES", { style: "percent", maximumFractionDigits: 1 });
const euros = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
let leafletMap;
let locationLayer;
let currentMapData;
let mapRenderGeneration = 0;
const severityFilter = document.querySelector("#offense-filter");
const severityOptions = [...document.querySelectorAll(".severity-option")];
const yearInput = document.querySelector("#year");
const monthInput = document.querySelector("#month");
const coverageRange = document.querySelector("#coverage-range");
const coverageUpdated = document.querySelector("#coverage-updated");
const coverageStorageKey = "traficfines.datasetCoverage.v1";
yearInput.max = new Date().getFullYear();
let availableThrough = null;
let periodInputTouched = false;

yearInput.addEventListener("input", () => { periodInputTouched = true; });
monthInput.addEventListener("change", () => { periodInputTouched = true; });
// Cobertura fija en modo demo estática


form.addEventListener("submit", async (event) => {
  event.preventDefault();
  mapRenderGeneration += 1;
  const year = document.querySelector("#year").value;
  const month = document.querySelector("#month").value;
  periodInputTouched = true;
  const availabilityMessage = periodAvailabilityMessage(Number(year), Number(month));
  if (availabilityMessage) {
    mapRenderGeneration += 1;
    mapElement.hidden = true;
    mapSummary.hidden = true;
    message.hidden = false;
    message.className = "message error";
    message.textContent = availabilityMessage;
    results.hidden = true;
    return;
  }
  mapElement.hidden = true;
  mapSummary.hidden = true;
  mapMessage.hidden = true;
  mapButton.disabled = false;
  mapButton.innerHTML = 'Cargar mapa <span aria-hidden="true">→</span>';
  message.hidden = false;
  message.className = "message";
  message.textContent = "Consultando el portal de datos y calculando el resumen…";
  document.querySelector("#status").textContent = "Cargando…";
  results.hidden = true;
  try {
    const response = await fetch("./data/month.json");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "No se pudo cargar el periodo.");
    render(data);
    results.hidden = false;
    message.hidden = true;
  } catch (error) {
    message.classList.add("error");
    message.textContent = error.message;
  }
});

mapButton.addEventListener("click", loadLocations);
severityOptions.forEach((input) => input.addEventListener("change", renderFilteredMap));

async function loadLocations() {
  const year = document.querySelector("#year").value;
  const month = document.querySelector("#month").value;
  const availabilityMessage = periodAvailabilityMessage(Number(year), Number(month));
  if (availabilityMessage) {
    mapMessage.hidden = false;
    mapMessage.className = "map-message error";
    mapMessage.textContent = availabilityMessage;
    return;
  }
  mapButton.disabled = true;
  mapMessage.hidden = false;
  mapMessage.className = "map-message";
  mapMessage.textContent = "Buscando las direcciones en el callejero oficial y preparando los puntos…";
  try {
    if (!window.L || !window.proj4) throw new Error("No se pudo cargar la librería del mapa. Comprueba la conexión a Internet y recarga la página.");
    const response = await fetch("./data/locations.json");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "No se pudieron cargar las ubicaciones.");
    if (data.schema_version !== 2 || !Array.isArray(data.points) || !Array.isArray(data.severity_types)) {
      throw new Error("El servidor local está ejecutando una versión anterior. Deténlo, vuelve a iniciar `uv run python webapp/server.py` y recarga la página.");
    }
    mapMessage.hidden = true;
    showMap(data);
    mapButton.innerHTML = 'Actualizar mapa <span aria-hidden="true">↻</span>';
  } catch (error) {
    mapMessage.hidden = false;
    mapMessage.classList.add("error");
    mapMessage.textContent = error.message;
  } finally {
    mapButton.disabled = false;
  }
}

function loadCachedCoverage() {
  try {
    const cached = JSON.parse(localStorage.getItem(coverageStorageKey) || "null");
    if (cached) applyCoverage(cached);
  } catch (error) {
    console.warn("No se pudo leer la cobertura guardada:", error.message);
  }
}

async function loadCoverage() {
  try {
    const response = await fetch("/api/coverage");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "No se pudo consultar la cobertura.");
    applyCoverage(data);
    localStorage.setItem(coverageStorageKey, JSON.stringify(data));
  } catch (error) {
    // La consulta de cobertura es informativa: no debe impedir consultar los datos.
    console.warn("No se pudo actualizar la cobertura publicada:", error.message);
  }
}

function applyCoverage(data) {
  const end = data?.available_through;
  if (!Number.isInteger(end?.year) || !Number.isInteger(end?.month) || end.month < 1 || end.month > 12 || end.year < 2015) return;
  availableThrough = end;
  yearInput.max = availableThrough.year;
  if (!periodInputTouched) {
    yearInput.value = availableThrough.year;
    monthInput.value = availableThrough.month;
  } else if (Number(yearInput.value) > availableThrough.year) {
    yearInput.value = availableThrough.year;
  }
  const monthName = monthNames[availableThrough.month - 1];
  const capitalizedMonth = monthName.charAt(0).toUpperCase() + monthName.slice(1);
  coverageRange.textContent = `Enero de 2015 — ${capitalizedMonth} de ${availableThrough.year}`;
  coverageUpdated.textContent = data.updated_at ? `Ficha municipal actualizada el ${data.updated_at}` : "Cobertura consultada en el portal municipal";
}

function periodAvailabilityMessage(year, month) {
  if (!availableThrough || year < 2015) return "";
  const isAfterCoverage = year > availableThrough.year
    || (year === availableThrough.year && month > availableThrough.month);
  if (!isAfterCoverage) return "";
  const latestMonth = monthNames[availableThrough.month - 1];
  const requestedMonth = monthNames[month - 1] || "periodo seleccionado";
  const latestLabel = `${latestMonth.charAt(0).toUpperCase()}${latestMonth.slice(1)} de ${availableThrough.year}`;
  const requestedLabel = `${requestedMonth.charAt(0).toUpperCase()}${requestedMonth.slice(1)} de ${year}`;
  return `El portal ofrece datos hasta ${latestLabel}. Todavía no hay datos publicados para ${requestedLabel}.`;
}

function showMap(data) {
  currentMapData = data;
  mapElement.hidden = false;
  mapSummary.hidden = false;
  severityFilter.hidden = false;
  const counts = Object.fromEntries(data.severity_types.map(({ name, count }) => [name, count]));
  document.querySelector("#count-leve").textContent = `(${integer.format(counts.LEVE || 0)})`;
  document.querySelector("#count-grave").textContent = `(${integer.format(counts.GRAVE || 0)})`;
  document.querySelector("#count-muy-grave").textContent = `(${integer.format(counts["MUY GRAVE"] || 0)})`;
  severityOptions.forEach((input) => { input.checked = true; });
  document.querySelector("#offense-filter-summary").textContent = "Calificaciones · todas";
  if (!data.address_directory_available && data.address_directory_error) {
    mapMessage.hidden = false;
    mapMessage.className = "map-message";
    mapMessage.textContent = `No se pudo consultar el callejero oficial; por seguridad no se muestran coordenadas del fichero. ${data.address_directory_error}`;
  }

  if (!leafletMap) {
    proj4.defs("EPSG:25830", "+proj=utm +zone=30 +ellps=GRS80 +units=m +no_defs +type=crs");
    leafletMap = L.map(mapElement, { preferCanvas: true }).setView([40.4168, -3.7038], 11);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a>',
    }).addTo(leafletMap);
    locationLayer = L.markerClusterGroup({ showCoverageOnHover: false, maxClusterRadius: 42 });
    leafletMap.addLayer(locationLayer);
  }

  drawMapPoints(data);
  window.setTimeout(() => leafletMap.invalidateSize(), 50);
}

function renderFilteredMap() {
  if (!currentMapData) return;
  const selected = new Set(severityOptions.filter((input) => input.checked).map((input) => input.value));
  document.querySelector("#offense-filter-summary").textContent = selected.size === severityOptions.length
    ? "Calificaciones · todas"
    : selected.size ? `Calificaciones · ${[...selected].join(", ")}` : "Calificaciones · ninguna";
  drawMapPoints(currentMapData, selected);
}

async function drawMapPoints(data, selected = new Set(["LEVE", "GRAVE", "MUY GRAVE"])) {
  const generation = ++mapRenderGeneration;
  locationLayer.clearLayers();
  const bounds = L.latLngBounds();
  let displayedRecords = 0;
  const batchSize = 200;
  mapMessage.hidden = true;
  mapMessage.className = "map-message";
  mapMessage.textContent = "Actualizando los puntos del mapa…";
  mapMessage.hidden = false;
  if (!selected.size) {
    mapSummary.textContent = `0 multas en ubicaciones · 0 puntos · ${integer.format(data.without_location)} registros sin ubicación`;
    mapMessage.textContent = "Selecciona al menos una gravedad para mostrar puntos.";
    return;
  }
  for (let offset = 0; offset < data.points.length; offset += batchSize) {
    if (generation !== mapRenderGeneration) return;
    const batchEnd = Math.min(offset + batchSize, data.points.length);
    for (let index = offset; index < batchEnd; index += 1) {
      const point = data.points[index];
      const severities = point.severities.filter((severity) => selected.has(severity.name));
      const count = severities.reduce((total, severity) => total + severity.count, 0);
      if (!count) continue;
      if (!point.position) point.position = proj4("EPSG:25830", "EPSG:4326", [point.x, point.y]);
      const [longitude, latitude] = point.position;
      if (!Number.isFinite(latitude) || !Number.isFinite(longitude) || latitude < 40.25 || latitude > 40.65 || longitude < -4.05 || longitude > -3.35) continue;
      const position = [latitude, longitude];
      bounds.extend(position);
      displayedRecords += count;
      const size = count >= 100 ? 30 : count >= 10 ? 26 : 22;
      const label = count > 999 ? `${Math.round(count / 100) / 10}k` : integer.format(count);
      const icon = L.divIcon({
        className: "",
        html: `<span class="map-point" style="width:${size}px;height:${size}px">${label}</span>`,
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2],
      });
      const filteredPoint = { ...point, count, examples: severities.flatMap((severity) => severity.examples).slice(0, 3) };
      const marker = L.marker(position, { icon, title: `${integer.format(count)} multas geolocalizadas` });
      const example = filteredPoint.examples[0] || {};
      const hoverDetails = [example.severity, example.offense].filter(Boolean).map(escapeText).join(" · ");
      marker.bindTooltip(`${integer.format(count)} multas geolocalizadas${hoverDetails ? `<br>${hoverDetails}` : ""}`, { direction: "top", sticky: true });
      marker.bindPopup(popupFor(filteredPoint), { maxWidth: 330 });
      locationLayer.addLayer(marker);
    }
    if (batchEnd < data.points.length) await new Promise((resolve) => window.requestAnimationFrame(resolve));
  }
  if (generation !== mapRenderGeneration) return;
  if (bounds.isValid()) {
    leafletMap.fitBounds(bounds, { padding: [24, 24], maxZoom: 13 });
    mapMessage.hidden = true;
  } else {
      mapMessage.hidden = false;
      mapMessage.textContent = "No hay ubicaciones para las gravedades seleccionadas.";
  }
  mapSummary.textContent = `${integer.format(displayedRecords)} multas en ubicaciones · ${integer.format(locationLayer.getLayers().length)} puntos · ${integer.format(data.without_location)} registros sin ubicación`;
  window.setTimeout(() => leafletMap.invalidateSize(), 50);
}

function popupFor(point) {
  const examples = point.examples.map((example) => {
    const info = [example.place, example.hour && `Hora ${example.hour}`, example.amount != null && euros.format(example.amount)].filter(Boolean).join(" · ");
    return `<div class="popup-example"><strong>${escapeText(example.severity || "Multa")}</strong>${example.offense ? ` · ${escapeText(example.offense)}` : ""}<div class="popup-meta">${escapeText(info)}${example.source ? ` · ${escapeText(example.source)}` : ""}</div></div>`;
  }).join("");
  return `<div class="popup-title">${integer.format(point.count)} multas geolocalizadas</div><div class="popup-meta">Registros coincidentes en este punto${examples ? " · ejemplos" : ""}</div>${examples}`;
}

function render(data) {
  const monthName = monthNames[data.month - 1];
  document.querySelector("#period-title").textContent = `${monthName[0].toLocaleUpperCase("es-ES")}${monthName.slice(1)} ${data.year}`;
  document.querySelector("#status").textContent = "Datos públicos · periodo consultado";
  document.querySelector("#records").textContent = integer.format(data.records);
  document.querySelector("#maximum").textContent = euros.format(data.payments.maximum);
  document.querySelector("#minimum").textContent = euros.format(data.payments.minimum);
  renderBars(document.querySelector("#categories-chart"), data.categories);
  renderLine(document.querySelector("#hourly-chart"), data.hourly);
}

function renderBars(target, values) {
  const entries = Object.entries(values).sort((a, b) => b[1] - a[1]);
  if (!entries.length) { target.textContent = "No hay datos de calificación para este periodo."; return; }
  const width = 520, height = 230, left = 132, right = 8, valueColumn = 110, top = 12, row = 54;
  const max = Math.max(...entries.map((entry) => entry[1]), 1);
  const total = entries.reduce((sum, [, value]) => sum + value, 0);
  const plotWidth = width - left - right - valueColumn;
  const bars = entries.map(([name, value], i) => {
    const y = top + i * row;
    const barWidth = plotWidth * value / max;
    const valueX = left + barWidth + 8;
    return `<text class="bar-label" x="0" y="${y + 20}">${escapeText(name)}</text><rect class="bar" x="${left}" y="${y}" width="${barWidth}" height="28" rx="5"></rect><text class="bar-value" x="${valueX}" y="${y + 19}">${integer.format(value)}</text><text class="bar-share" x="${width - right}" y="${y + 19}" text-anchor="end">${percent.format(value / total)}</text>`;
  }).join("");
  target.innerHTML = `<svg viewBox="0 0 ${width} ${Math.max(height, top + entries.length * row)}" role="img" aria-label="Multas por calificación">${bars}</svg>`;
}

function renderLine(target, values) {
  if (!values?.length) { target.textContent = "No hay datos horarios para este periodo."; return; }
  const width = 520, height = 230, left = 54, right = 14, top = 18, bottom = 34;
  const max = Math.max(...values, 1);
  const magnitude = 10 ** Math.floor(Math.log10(max));
  const normalized = max / magnitude;
  const ceilingFactor = [1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10].find((factor) => factor >= normalized) || 10;
  const axisMax = ceilingFactor * magnitude;
  const tickStep = axisMax >= 20000 ? 10000 : axisMax >= 5000 ? 5000 : axisMax >= 1000 ? 1000 : Math.max(1, axisMax / 4);
  const ticksY = [];
  for (let tick = 0; tick < axisMax; tick += tickStep) ticksY.push(tick);
  if (ticksY.at(-1) !== axisMax) ticksY.push(axisMax);
  const plotW = width - left - right, plotH = height - top - bottom;
  const points = values.map((value, index) => `${left + (plotW * index / (values.length - 1))},${top + plotH * (1 - value / axisMax)}`);
  const area = `${left},${height - bottom} ${points.join(" ")} ${width - right},${height - bottom}`;
  const grids = ticksY.map((tick) => {
    const y = top + plotH * (1 - tick / axisMax);
    return `<line class="gridline" x1="${left}" y1="${y}" x2="${width - right}" y2="${y}"/><text x="0" y="${y + 4}">${integer.format(tick)}</text>`;
  }).join("");
  const ticks = [0, 4, 8, 12, 16, 20, 23].map((hour) => `<text x="${left + plotW * hour / 23}" y="${height - 8}" text-anchor="middle">${hour}h</text>`).join("");
  target.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Multas por hora"><polygon class="area" points="${area}"/>${grids}<polyline class="line" points="${points.join(" ")}"/>${ticks}</svg>`;
}

function escapeText(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

// Configuración automática para la demo estática de GitHub Pages
periodInputTouched = true;
yearInput.value = "2026";
monthInput.value = "6";
yearInput.disabled = true;
monthInput.disabled = true;

window.addEventListener("submit", (event) => {
  if (event.target !== form) return;
  event.preventDefault();
  event.stopPropagation();
  if (!results.hidden) {
    results.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}, true);

window.addEventListener("DOMContentLoaded", async () => {
  try {
    const response = await fetch("./data/month.json");
    const data = await response.json();
    if (!response.ok) throw new Error("No se pudieron cargar los datos de la demo.");
    render(data);
    results.hidden = false;
    message.hidden = true;
  } catch (error) {
    message.hidden = false;
    message.className = "message error";
    message.textContent = error.message;
  }
});
