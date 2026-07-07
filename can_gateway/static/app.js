/* CAN Gateway add-on — scan-only panel (Ingress-safe relative URLs) */

let lastStatus = {};
let modulesCache = [];
let entitiesCache = [];
let discoveryMeta = {};

function $(id) {
  return document.getElementById(id);
}

function apiUrl(path) {
  return new URL(String(path).replace(/^\//, ""), window.location.href).toString();
}

async function api(path, opts) {
  const res = await fetch(apiUrl(path), opts);
  const data = await res.json().catch(() => ({}));
  return { res, data };
}

function formatTime(ts) {
  if (!ts) return "—";
  return new Date(Number(ts) * 1000).toLocaleString("pl-PL");
}

function connectionLabel(st) {
  if (!st.bus_ok) return st.bus_error ? `Błąd: ${st.bus_error}` : "Rozłączono";
  const port = st.can_port || st.configured_port || "?";
  const br = st.can_bitrate ? `${Math.round(st.can_bitrate / 1000)} kbps` : "?";
  return `${port} / ${st.can_interface || "?"} @ ${br}`;
}

function showError(message) {
  const banner = $("error-banner");
  if (!message) {
    banner.hidden = true;
    banner.textContent = "";
    return;
  }
  banner.hidden = false;
  banner.textContent = message;
}

function updateKeyBanner(st) {
  const banner = $("key-banner");
  if (!st.secure_enabled) {
    banner.hidden = true;
    banner.textContent = "";
    return;
  }
  if (st.master_key_configured) {
    banner.hidden = true;
    banner.textContent = "";
    return;
  }
  banner.hidden = false;
  if (st.master_key_invalid) {
    banner.textContent =
      "MASTER_KEY jest niepoprawny (wymagane 64 znaki hex). Popraw master_key_hex w ustawieniach dodatku.";
  } else {
    banner.textContent =
      st.master_key_required_hint ||
      "Ustaw master_key_hex w konfiguracji dodatku (64 znaki hex), aby odczytać moduły Secure CAN.";
  }
}

function entitiesByModule() {
  const grouped = new Map();
  for (const ent of entitiesCache) {
    const mid = ent.module_id;
    if (mid == null) continue;
    if (!grouped.has(mid)) grouped.set(mid, []);
    grouped.get(mid).push(ent);
  }
  return grouped;
}

function renderStatus() {
  const st = lastStatus;
  const entityTotal = discoveryMeta.entity_count ?? entitiesCache.length ?? "—";
  const discoveryVersion = discoveryMeta.discovery_version ?? "—";
  $("conn-status").innerHTML = `
    <tr><td>Magistrala</td><td class="${st.bus_ok ? "ok" : "err"}">${st.bus_ok ? "Połączono" : "Błąd"}</td></tr>
    <tr><td>Port / interfejs / bitrate</td><td>${connectionLabel(st)}</td></tr>
    <tr><td>Secure CAN</td><td>${st.secure_enabled ? "TAK (MASTER_KEY)" : "NIE"}</td></tr>
    <tr><td>Moduły zapisane</td><td>${st.module_count ?? modulesCache.length ?? 0}</td></tr>
    <tr><td>Encje w katalogu</td><td>${entityTotal} (discovery v${discoveryVersion})</td></tr>
    <tr><td>Ostatni skan</td><td>${st.last_scan_status || discoveryMeta.scan_status || "—"} ${formatTime(st.last_scan_at || discoveryMeta.last_scan_at)}</td></tr>`;
  updateKeyBanner(st);
  $("footer-status").textContent = `${connectionLabel(st)} | moduły: ${modulesCache.length} | encje: ${entityTotal}`;
}

function renderModules() {
  const tb = $("modules-body");
  tb.innerHTML = "";
  $("modules-empty").hidden = modulesCache.length > 0;
  for (const m of modulesCache) {
    const tr = document.createElement("tr");
    const hw = m.hw_name || m.hw_type || "?";
    tr.innerHTML = `
      <td>${m.module_id}</td>
      <td>${hw}</td>
      <td>${m.name || "—"}</td>
      <td>${m.mac || "—"}</td>
      <td>${m.entity_count ?? "—"}</td>
      <td>${formatTime(discoveryMeta.last_scan_at || lastStatus.last_scan_at)}</td>`;
    tb.appendChild(tr);
  }
}

function renderEntityCatalog() {
  const tb = $("entity-modules-body");
  tb.innerHTML = "";
  const grouped = entitiesByModule();
  const moduleRows = modulesCache.length
    ? modulesCache
    : [...grouped.keys()].map((mid) => ({ module_id: mid, name: "", entity_count: grouped.get(mid).length }));

  $("entities-empty").hidden = entitiesCache.length > 0;

  for (const m of moduleRows) {
    const mid = m.module_id;
    const ents = grouped.get(mid) || [];
    const count = m.entity_count ?? ents.length;
    const tr = document.createElement("tr");
    tr.className = "entity-module-row";
    const toggleId = `ent-toggle-${mid}`;
    tr.innerHTML = `
      <td><button type="button" class="secondary toggle-btn" id="${toggleId}" aria-expanded="false" ${ents.length ? "" : "disabled"}>▸</button></td>
      <td>${mid}</td>
      <td>${m.name || "—"}</td>
      <td>${count}</td>`;
    tb.appendChild(tr);

    if (!ents.length) continue;

    const detail = document.createElement("tr");
    detail.className = "entity-detail-row";
    detail.hidden = true;
    detail.dataset.moduleId = String(mid);
    const td = document.createElement("td");
    td.colSpan = 4;
    const inner = document.createElement("table");
    inner.className = "entity-inner-table";
    inner.innerHTML = `
      <thead><tr><th>Platforma</th><th>Nazwa</th><th>unique_id</th></tr></thead>
      <tbody></tbody>`;
    const innerBody = inner.querySelector("tbody");
    for (const ent of ents.sort((a, b) => String(a.unique_id).localeCompare(String(b.unique_id)))) {
      const er = document.createElement("tr");
      er.innerHTML = `
        <td>${ent.platform || "?"}</td>
        <td>${ent.name || "—"}</td>
        <td><code>${ent.unique_id || "—"}</code></td>`;
      innerBody.appendChild(er);
    }
    td.appendChild(inner);
    detail.appendChild(td);
    tb.appendChild(detail);

    const btn = tr.querySelector(`#${toggleId}`);
    btn.onclick = () => {
      const open = detail.hidden;
      detail.hidden = !open;
      btn.textContent = open ? "▾" : "▸";
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    };
  }
}

async function loadAll() {
  const errors = [];
  const [statusRes, discoveryRes, entitiesRes] = await Promise.all([
    api("/api/status"),
    api("/api/discovery"),
    api("/api/entities"),
  ]);

  if (!statusRes.res.ok) {
    errors.push(`status HTTP ${statusRes.res.status}`);
    lastStatus = {};
  } else {
    lastStatus = statusRes.data || {};
  }

  if (!discoveryRes.res.ok) {
    errors.push(`discovery HTTP ${discoveryRes.res.status}`);
    discoveryMeta = {};
    modulesCache = [];
  } else {
    discoveryMeta = discoveryRes.data || {};
    modulesCache = discoveryMeta.modules || [];
  }

  if (!entitiesRes.res.ok) {
    errors.push(`entities HTTP ${entitiesRes.res.status}`);
    entitiesCache = discoveryMeta.entities || [];
  } else {
    entitiesCache = entitiesRes.data?.entities || discoveryMeta.entities || [];
    if (entitiesRes.data?.discovery_version != null) {
      discoveryMeta.discovery_version = entitiesRes.data.discovery_version;
    }
    if (entitiesRes.data?.entity_count != null) {
      discoveryMeta.entity_count = entitiesRes.data.entity_count;
    }
  }

  if (errors.length) {
    showError(`Błąd API: ${errors.join("; ")} — sprawdź logi dodatku CAN Gateway.`);
    $("scan-status").textContent = errors.join("; ");
  } else {
    showError("");
    if ((discoveryMeta.discovery_version ?? 0) === 0 && modulesCache.length === 0) {
      $("scan-status").textContent = "Brak zapisanego skanu — kliknij „Skanuj magistralę”.";
    } else if ((discoveryMeta.discovery_version ?? 0) === 0) {
      $("scan-status").textContent = "Moduły w pamięci, brak katalogu encji — uruchom skan.";
    } else {
      $("scan-status").textContent = "";
    }
  }

  renderStatus();
  renderModules();
  renderEntityCatalog();
}

async function runScan() {
  $("btn-scan").disabled = true;
  $("scan-status").textContent = "Skan w toku…";
  showError("");
  try {
    const { res, data } = await api("/api/scan", { method: "POST" });
    if (!res.ok || !data.ok) {
      const msg = data.error || `HTTP ${res.status}`;
      $("scan-status").textContent = `Błąd skanu: ${msg}`;
      showError(`Skan nie powiódł się: ${msg}`);
      if (Array.isArray(data.warnings) && data.warnings.length) {
        $("scan-status").textContent += ` (${data.warnings.join("; ")})`;
      }
    } else {
      const partial = data.partial ? " (częściowy — brak MASTER_KEY?)" : "";
      const entities = data.entity_count ?? "?";
      $("scan-status").textContent = `Skan OK: ${data.module_count ?? "?"} moduł(ów), ${entities} encji${partial}`;
      if (Array.isArray(data.warnings) && data.warnings.length) {
        $("scan-status").textContent += ` — ${data.warnings[0]}`;
      }
    }
    await loadAll();
  } catch (err) {
    const msg = String(err);
    $("scan-status").textContent = `Błąd: ${msg}`;
    showError(`Błąd połączenia z API: ${msg}`);
  } finally {
    $("btn-scan").disabled = false;
  }
}

$("btn-scan").onclick = runScan;
$("btn-refresh").onclick = loadAll;

loadAll();
setInterval(loadAll, 30000);
