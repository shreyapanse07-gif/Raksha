// ============================================================
// Raksha Dashboard - Frontend Logic
// ============================================================

// Shared "danger ramp" -- the same four colors mean the same thing
// whether you're looking at the Hazard Zone layer or the Response
// Priority layer, so severity reads instantly either way.
const RISK_COLORS = {
  Low: "#34D399",       // emerald
  Moderate: "#FBBF24",  // amber
  High: "#FB923C",      // orange
  Severe: "#F43F5E",    // rose-red
  Unknown: "#6E7897",
};

// CHANGE: tier names now match the problem statement's own language
// ("immediate, short-term, and medium-term relocation") instead of the
// earlier internal CRITICAL/HIGH/MEDIUM/WATCH naming. Colors reuse the
// same ramp as RISK_COLORS (High/Severe) for a single, consistent
// severity language across both layers.
const PRIORITY_COLORS = {
  "Immediate Relocation": "#F43F5E",
  "Short-Term Relocation": "#FB923C",
  "Medium-Term Relocation": "#FBBF24",
};

const SITE_COLOR = "#38BDF8"; // sky-blue, cool contrast against the warm danger ramp

// Converts a priority name into a CSS-safe class suffix, e.g.
// "Immediate Relocation" -> "immediate-relocation". Needed because the
// old `.toLowerCase()` alone breaks on names containing spaces.
function prioritySlug(priority) {
  return (priority || "unknown")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "");
}

let map;
let villageLayer, siteLayer, boundaryLayer, highlightLayer;
let allVillagesGeoJSON = null;
let candidateData = [];
let alertsByName = {};
let siteMarkersById = {};
let atRiskMarkers = []; // tracked so we can toggle permanent name labels on zoom

// Below this zoom level, at-risk villages are hover-only (too many on
// screen for permanent labels to stay readable). At or above it, names
// stay visible on the map without needing to hover -- a standard
// "labels appear once you zoom in" map pattern.
const PERMANENT_LABEL_MIN_ZOOM = 11;

// ------------------------------------------------------------
// INIT MAP
// ------------------------------------------------------------
function initMap() {
  map = L.map("map", { zoomControl: true }).setView([30.0, 79.2], 8);
  map.on("zoomend", updatePermanentLabels);

  // Dark cartographic basemap (Esri World Dark Gray Base) -- this is a
  // genuinely free, no-API-key tile source, unlike CARTO's dark_all
  // endpoint which now requires a registered account/API key (that's
  // what the "API KEY REQUIRED" watermark tiling the map was).
  L.tileLayer("https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", {
    attribution: "Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors, and the GIS User Community",
    maxZoom: 16,
  }).addTo(map);

  // Reference labels (place names, roads) drawn on top of the dark base
  L.tileLayer("https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 16,
  }).addTo(map);

  villageLayer = L.layerGroup().addTo(map);
  siteLayer = L.layerGroup().addTo(map);
}

// ------------------------------------------------------------
// ANIMATED COUNT-UP FOR SUMMARY NUMBERS
// ------------------------------------------------------------
function animateCount(el, target, duration = 900) {
  const start = 0;
  const startTime = performance.now();
  function tick(now) {
    const progress = Math.min((now - startTime) / duration, 1);
    // ease-out cubic
    const eased = 1 - Math.pow(1 - progress, 3);
    const value = Math.round(start + (target - start) * eased);
    el.textContent = value.toLocaleString();
    if (progress < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

// ------------------------------------------------------------
// LOAD SUMMARY STATS
// ------------------------------------------------------------
async function loadSummary() {
  const res = await fetch("/api/summary");
  const data = await res.json();

  const cards = [
    { label: "Villages Assessed", value: data.total_villages },
    { label: "High-Risk Villages", value: data.high_risk_villages },
    { label: "At-Risk Population", value: data.total_at_risk_population },
    { label: "Households to Relocate", value: data.total_households_needing_relocation },
  ];

  const container = document.getElementById("summary-cards");
  container.innerHTML = cards
    .map(
      (c, i) => `
      <div class="summary-card" style="animation-delay:${i * 80}ms">
        <div class="value" data-target="${c.value}">0</div>
        <div class="label">${c.label}</div>
      </div>`
    )
    .join("");

  // Trigger count-up after the cards are in the DOM
  document.querySelectorAll(".summary-card .value").forEach((el) => {
    animateCount(el, parseInt(el.dataset.target, 10) || 0);
  });
}

// ------------------------------------------------------------
// LOAD BOUNDARY OUTLINE
// ------------------------------------------------------------
async function loadBoundary() {
  const res = await fetch("/api/boundary");
  const geojson = await res.json();
  boundaryLayer = L.geoJSON(geojson, {
    style: { color: "#5A6B99", weight: 2, fillOpacity: 0 },
  }).addTo(map);
}

// ------------------------------------------------------------
// LOAD VILLAGES (all clickable, colored/sized by risk & alert level)
// ------------------------------------------------------------
async function loadVillages() {
  const res = await fetch("/api/villages");
  allVillagesGeoJSON = await res.json();
  renderVillages(allVillagesGeoJSON.features);
  populateVillageSearchList(allVillagesGeoJSON.features);
}

function renderVillages(features) {
  villageLayer.clearLayers();
  atRiskMarkers = [];

  features.forEach((feature) => {
    const props = feature.properties;
    const coords = feature.geometry.coordinates; // [lon, lat]
    if (!coords) return;

    const isAtRisk = props.response_priority && props.response_priority !== "";
    const risk = props.risk_label || "Unknown";
    const color = isAtRisk
      ? PRIORITY_COLORS[props.response_priority] || RISK_COLORS[risk]
      : RISK_COLORS[risk] || RISK_COLORS.Unknown;

    // Gentle pulsing glow on the very top tier only -- keeps the effect
    // meaningful (draws the eye to what matters most) instead of
    // animating thousands of markers, which would hurt performance.
    const isImmediate = props.response_priority === "Immediate Relocation";

    const marker = L.circleMarker([coords[1], coords[0]], {
      radius: isAtRisk ? 7 : 4,
      color: color,
      fillColor: color,
      fillOpacity: isAtRisk ? 0.95 : 0.7,
      weight: isAtRisk ? 2 : 1,
      className: isImmediate ? "pulse-marker" : "",
    });

    if (isAtRisk) {
      // Just the name, shown either permanently (zoomed in) or on hover
      // (zoomed out) -- see updatePermanentLabels().
      marker.bindTooltip(props.name || "Unnamed", {
        permanent: false,
        direction: "top",
        offset: [0, -8],
        className: "village-label",
      });
      atRiskMarkers.push(marker);
    } else {
      const label = `${props.name || "Unnamed"} — ${risk} hazard zone`;
      marker.bindTooltip(label, { sticky: true });
    }

    marker.on("click", () => openVillageDrawer(props));
    marker.addTo(villageLayer);
  });

  updatePermanentLabels();
}

// Shows/hides always-on name labels for at-risk villages depending on
// zoom level, so the map isn't cluttered with 4,000+ overlapping labels
// when zoomed out, but names are visible without hovering once you've
// zoomed in on an area.
function updatePermanentLabels() {
  if (!map) return;
  const shouldShow = map.getZoom() >= PERMANENT_LABEL_MIN_ZOOM;
  atRiskMarkers.forEach((marker) => {
    if (shouldShow) marker.openTooltip();
    else marker.closeTooltip();
  });
}

// ------------------------------------------------------------
// SEARCH
// ------------------------------------------------------------
function populateVillageSearchList(features) {
  const datalist = document.getElementById("village-search-list");
  const names = [...new Set(features.map((f) => f.properties.name).filter(Boolean))];
  datalist.innerHTML = names
    .slice(0, 500) // cap for performance
    .map((n) => `<option value="${n}"></option>`)
    .join("");
}

function searchVillage() {
  const query = document.getElementById("village-search").value.trim().toLowerCase();
  if (!query || !allVillagesGeoJSON) return;
  const match = allVillagesGeoJSON.features.find(
    (f) => (f.properties.name || "").toLowerCase() === query
  );
  if (match) {
    const coords = match.geometry.coordinates;
    map.flyTo([coords[1], coords[0]], 12, { duration: 0.8 });
    openVillageDrawer(match.properties);
  }
}

// ------------------------------------------------------------
// FILTER BY RISK / ALERT LEVEL
// ------------------------------------------------------------
function applyVillageFilter() {
  if (!allVillagesGeoJSON) return;
  const checked = [...document.querySelectorAll(".risk-filter:checked")].map((c) => c.value);
  const filtered = allVillagesGeoJSON.features.filter((f) => {
    const risk = f.properties.risk_label || "Unknown";
    return checked.includes(risk);
  });
  renderVillages(filtered);
}

// ------------------------------------------------------------
// SIDEBAR LIST OF AT-RISK VILLAGES, RANKED #1 = MOST URGENT
// ------------------------------------------------------------
function renderAtRiskSidebarList() {
  if (!allVillagesGeoJSON) return;
  const atRisk = allVillagesGeoJSON.features
    .filter((f) => f.properties.response_priority)
    .sort((a, b) => (a.properties.priority_rank || 999) - (b.properties.priority_rank || 999));

  window.__villageById = {};
  const list = document.getElementById("villages-list");
  list.innerHTML = atRisk
    .map((f, i) => {
      const p = f.properties;
      window.__villageById[i] = p;
      const priorityClass = prioritySlug(p.response_priority);
      // Stagger only the first batch so a long list doesn't feel sluggish
      const delay = Math.min(i, 24) * 25;
      return `
      <div class="card" style="animation-delay:${delay}ms" onclick="openVillageDrawerById(${i})">
        <div class="card-header">
          <div class="card-title">#${p.priority_rank} ${p.name || "Unnamed village"}</div>
          <span class="badge priority-${priorityClass}">${p.response_priority}</span>
        </div>
        <div class="card-sub">
          <span>Pop: ${Math.round(p.estimated_population || 0)}</span>
          <span>Households: ${p.households_needing_relocation || "?"}</span>
          <span>Road: ${p.distance_to_road_km ? p.distance_to_road_km.toFixed(1) : "?"} km</span>
        </div>
      </div>`;
    })
    .join("");
}

function openVillageDrawerById(id) {
  openVillageDrawer(window.__villageById[id]);
}

// ------------------------------------------------------------
// LOAD CANDIDATE RELOCATION SITES
// ------------------------------------------------------------
async function loadCandidateSites() {
  const res = await fetch("/api/candidate-sites");
  candidateData = await res.json();

  siteLayer.clearLayers();
  siteMarkersById = {};
  candidateData.slice(0, 20).forEach((site, i) => {
    const marker = L.circleMarker([site.latitude, site.longitude], {
      radius: 7,
      color: SITE_COLOR,
      fillColor: SITE_COLOR,
      fillOpacity: 0.9,
      weight: 2,
    });
    marker.bindTooltip(`Site #${i + 1} - Score ${site.suitability_score}/100`, { sticky: true });
    marker.on("click", () => openSiteDrawer(i));
    marker.addTo(siteLayer);
    siteMarkersById[i] = marker;
  });
}

// ------------------------------------------------------------
// LOAD LIVE RAINFALL-TRIGGER ALERTS (hazard_predictor.py's output, via
// /api/alerts). This is a snapshot from whenever that script was last
// run, not a fresh call on every page load -- see app.py's comment.
// ------------------------------------------------------------
async function loadAlerts() {
  try {
    const res = await fetch("/api/alerts");
    const alerts = await res.json();
    alertsByName = {};
    alerts.forEach((a) => {
      if (a.village) alertsByName[a.village] = a;
    });
  } catch (e) {
    alertsByName = {};
  }
}

// ------------------------------------------------------------
// DETAIL DRAWER - VILLAGE
// ------------------------------------------------------------
function buildAlertSectionHtml(villageName) {
  const alert = alertsByName[villageName];

  if (!alert) {
    // Most at-risk villages are outside the Immediate-Relocation tier
    // that hazard_predictor.py actually checks -- this is expected, not
    // an error, so keep the note low-key.
    return `
      <div class="section-title">Live Rainfall Check</div>
      <p style="font-size:12px; color:var(--text-muted); margin-bottom:4px;">
        Not part of the live-monitored Immediate Relocation tier.
      </p>`;
  }

  if (alert.status === "error") {
    return `
      <div class="section-title">Live Rainfall Check</div>
      <div class="transport-note" style="border-color: rgba(244,63,94,0.3); background: rgba(244,63,94,0.08); color:#FDA4AF;">
        Forecast check failed (${alert.error || "network error"}) when last run.
      </div>`;
  }

  const statusMeta = {
    ALERT: { label: "ALERT — trigger threshold crossed", color: "var(--sev-severe)" },
    watch: { label: "Watch — no trigger yet", color: "var(--sev-moderate)" },
    normal: { label: "Normal", color: "var(--sev-low)" },
  }[alert.status] || { label: alert.status, color: "var(--text-secondary)" };

  const forecastRows = (alert.forecast || [])
    .map((f) => `<div class="adequacy-row"><span>${f.date}</span><b>${f.forecast_mm ?? "?"} mm</b></div>`)
    .join("");

  return `
    <div class="section-title">Live Rainfall Check <span style="color:var(--text-muted); font-weight:400; text-transform:none;">(Open-Meteo, last run)</span></div>
    <div class="transport-note" style="border-color: ${statusMeta.color}; color: var(--text-primary);">
      <b style="color:${statusMeta.color};">${statusMeta.label}</b><br>
      Trigger threshold: ${alert.trigger_threshold_mm} mm / 24h
    </div>
    ${forecastRows}`;
}

function openVillageDrawer(v) {
  if (!v.response_priority) {
    const guidance = v.general_safety_guidance || "No specific guidance available.";
    document.getElementById("drawer-content").innerHTML = `
      <span class="badge ${(v.risk_label || "").toLowerCase()} drawer-badge">${v.risk_label || "Unknown"} Hazard Zone</span>
      <h2>${v.name || "Unnamed village"}</h2>
      <p style="color:#6b7280; font-size:13px;">
        This village is not currently in active relocation planning.
      </p>
      <div class="transport-note" style="margin-top:14px;">
        ${guidance}
      </div>
    `;
    openDrawer();
    return;
  }

  const priorityClass = prioritySlug(v.response_priority);
  const safetyList = (v.safety_measures || "")
    .split("|")
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => `<li>${s}</li>`)
    .join("");

  const hasSite = v.nearest_site_lat && v.nearest_site_lon;

  document.getElementById("drawer-content").innerHTML = `
    <span class="badge priority-${priorityClass} drawer-badge">
      #${v.priority_rank} PRIORITY — ${v.response_priority} (Score ${v.red_alert_score}/100)
    </span>
    <h2>${v.name || "Unnamed village"}</h2>
    <p style="color:#6b7280; font-size:13px;">
      Hazard Zone: <b>${v.risk_label}</b> &nbsp;|&nbsp; Evacuation urgency: <b>${v.evacuation_urgency || "N/A"}</b>
    </p>

    <div class="why-box">
      <b>Why this village is prioritized:</b><br>${v.priority_explanation || ""}
    </div>

    <div class="stat-grid">
      <div class="stat-box">
        <div class="stat-value">${Math.round(v.estimated_population || 0)}</div>
        <div class="stat-label">Estimated Population</div>
      </div>
      <div class="stat-box">
        <div class="stat-value">${Math.round(v.vulnerable_population_est || 0)}</div>
        <div class="stat-label">Vulnerable (children + seniors)</div>
      </div>
      <div class="stat-box">
        <div class="stat-value">${v.households_needing_relocation || "?"}</div>
        <div class="stat-label">Households to Relocate</div>
      </div>
      <div class="stat-box">
        <div class="stat-value">${(v.land_required_sqm || 0).toLocaleString()} m2</div>
        <div class="stat-label">Land Required at New Site</div>
      </div>
    </div>

    ${buildAlertSectionHtml(v.name)}

    <div class="section-title">Rescue / Transport Access</div>
    <div class="transport-note">
      Nearest road type: <b>${v.nearest_road_type || "unknown"}</b> — ${v.distance_to_road_km ? v.distance_to_road_km.toFixed(1) : "?"} km away<br>
      ${v.distance_to_road_km > 5
        ? "This village is relatively remote — consider helicopter evacuation capability as a backup for emergencies."
        : "Road access is reasonably close, supporting standard vehicle-based evacuation."}
    </div>

    <div class="section-title">Recommended Relocation Site</div>
    ${hasSite ? `
      <div class="adequacy-row">
        <span>Distance to site</span><b>${v.nearest_good_site_distance_km} km</b>
      </div>
      <div class="adequacy-row">
        <span>Site suitability score</span><b>${v.nearest_site_suitability}/100</b>
      </div>
      <div class="adequacy-row">
        <span>Hospital access at site</span>
        <span class="adequacy-tag ${v.nearest_site_hospital_adequacy === 'Adequate' ? 'adequate' : 'insufficient'}">${v.nearest_site_hospital_adequacy}</span>
      </div>
      <div class="adequacy-row">
        <span>School access at site</span>
        <span class="adequacy-tag ${v.nearest_site_school_adequacy === 'Adequate' ? 'adequate' : 'insufficient'}">${v.nearest_site_school_adequacy}</span>
      </div>
      <button class="show-site-btn" onclick="flyToRelocationSite(${v.nearest_site_lat}, ${v.nearest_site_lon})">
        Show this site on the map
      </button>
    ` : `<p style="font-size:13px; color:#6b7280;">No suitable relocation site identified nearby yet.</p>`}

    <div class="section-title">Safety Measures</div>
    <ul class="safety-list">${safetyList || "<li>General disaster preparedness guidance applies.</li>"}</ul>
  `;
  openDrawer();
}

function flyToRelocationSite(lat, lon) {
  map.flyTo([lat, lon], 13, { duration: 0.8 });

  if (highlightLayer) map.removeLayer(highlightLayer);
  highlightLayer = L.circleMarker([lat, lon], {
    radius: 14,
    color: SITE_COLOR,
    fillColor: SITE_COLOR,
    fillOpacity: 0.3,
    weight: 3,
    className: "pulse-marker",
  }).addTo(map);

  setTimeout(() => {
    if (highlightLayer) map.removeLayer(highlightLayer);
  }, 4000);
}

// ------------------------------------------------------------
// DETAIL DRAWER - CANDIDATE SITE
// ------------------------------------------------------------
function openSiteDrawer(index) {
  const s = candidateData[index];

  const hospitalTagClass = s.hospital_adequacy === "Adequate" ? "adequate" : "insufficient";
  const schoolTagClass = s.school_adequacy === "Adequate" ? "adequate" : "insufficient";

  const content = `
    <span class="badge site drawer-badge">Suitability: ${s.suitability_score}/100</span>
    <h2>Candidate Relocation Site</h2>
    <p style="color:#6b7280; font-size:13px;">
      Estimated to serve ~${Math.round(s.assumed_population_share)} people
    </p>

    <div class="stat-grid">
      <div class="stat-box">
        <div class="stat-value">${s.dist_to_road_km.toFixed(1)} km</div>
        <div class="stat-label">To Nearest Road</div>
      </div>
      <div class="stat-box">
        <div class="stat-value">${s.dist_to_hospital_km.toFixed(1)} km</div>
        <div class="stat-label">To Nearest Hospital</div>
      </div>
      <div class="stat-box">
        <div class="stat-value">${s.dist_to_school_km.toFixed(1)} km</div>
        <div class="stat-label">To Nearest School</div>
      </div>
      <div class="stat-box">
        <div class="stat-value">${Math.round(s.population_density)}/km2</div>
        <div class="stat-label">Existing Population Density</div>
      </div>
    </div>

    <div class="section-title">Basic Needs Adequacy (within 15km)</div>
    <div class="adequacy-row">
      <span>Hospitals nearby: ${s.hospitals_within_15km}</span>
      <span class="adequacy-tag ${hospitalTagClass}">${s.hospital_adequacy}</span>
    </div>
    <div class="adequacy-row">
      <span>Schools nearby: ${s.schools_within_15km}</span>
      <span class="adequacy-tag ${schoolTagClass}">${s.school_adequacy}</span>
    </div>

    <div class="section-title">Rescue / Transport Access</div>
    <div class="transport-note">
      Nearest road type: <b>${s.nearest_road_type || "unknown"}</b><br>
      ${s.hospital_adequacy === "Insufficient" || s.school_adequacy === "Insufficient"
        ? "This site may need additional service infrastructure (new clinic/school) if selected, or population should be split with a nearby site."
        : "This site has adequate nearby services for its assumed population share."}
    </div>
  `;
  document.getElementById("drawer-content").innerHTML = content;
  openDrawer();
}

// ------------------------------------------------------------
// DRAWER OPEN/CLOSE (with backdrop)
// ------------------------------------------------------------
function openDrawer() {
  document.getElementById("detail-drawer").classList.add("open");
  document.getElementById("drawer-backdrop").classList.add("open");
}

function closeDrawer() {
  document.getElementById("detail-drawer").classList.remove("open");
  document.getElementById("drawer-backdrop").classList.remove("open");
}

// ------------------------------------------------------------
// UI WIRING
// ------------------------------------------------------------
function setupUI() {
  document.getElementById("drawer-close").addEventListener("click", closeDrawer);
  document.getElementById("drawer-backdrop").addEventListener("click", closeDrawer);

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const tab = btn.dataset.tab;
      document.getElementById("villages-panel").classList.toggle("hidden", tab !== "villages");
      document.getElementById("sites-panel").classList.toggle("hidden", tab !== "sites");
    });
  });

  document.getElementById("toggle-villages").addEventListener("change", (e) => {
    if (e.target.checked) map.addLayer(villageLayer);
    else map.removeLayer(villageLayer);
  });

  document.getElementById("toggle-sites").addEventListener("change", (e) => {
    if (e.target.checked) map.addLayer(siteLayer);
    else map.removeLayer(siteLayer);
  });

  document.getElementById("village-search-btn").addEventListener("click", searchVillage);
  document.getElementById("village-search").addEventListener("keydown", (e) => {
    if (e.key === "Enter") searchVillage();
  });

  document.querySelectorAll(".risk-filter").forEach((cb) => {
    cb.addEventListener("change", applyVillageFilter);
  });
}

// ------------------------------------------------------------
// BOOT
// ------------------------------------------------------------
async function boot() {
  initMap();
  setupUI();
  await loadBoundary();
  await loadVillages();
  renderAtRiskSidebarList();
  await Promise.all([loadSummary(), loadCandidateSites(), loadAlerts()]);

  // Fade out the loading splash once everything is ready
  const splash = document.getElementById("loading-splash");
  if (splash) {
    splash.classList.add("fade-out");
    setTimeout(() => splash.remove(), 500);
  }
}

boot();