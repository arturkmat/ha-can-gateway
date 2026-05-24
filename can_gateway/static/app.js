/* CAN Gateway web panel — pełna parytet z konfiguratorem Windows */
const ROLES = ["Unused","Button","Relay","DS18B20","BinarySensor","I2C_SDA","I2C_SCL","HC595","SHUTTER_UP","SHUTTER_DOWN","MCP23017","NTC"];
const ACTIONS = ["Jednoklik","Dwuklik","Trojklik","Czteroklik","Piecioklik","Dlugie nacisniecie"];
const STATES = ["Wylacz","Zalacz","Przelacz"];
const SENSOR_TYPES = {1:"DS18B20",2:"I2C",3:"SHT30",4:"BME280",5:"NTC",6:"Binary"};

let selectedId = null;
let modulesCache = [];
let pinoutCache = null;
let currentTab = "connection";
let tabLoadTimer = null;
let tabLoading = false;
let mappingsCache = [];
let sensorsCache = [];
let footerConnectionText = "Magistrala: …";
let footerScanText = "Brak skanu";
let progressTaskToken = 0;
let guiRefreshInProgress = false;
let lastStatus = {};
let lastEntityCount = 0;
let draftMappings = [];

function $(id) { return document.getElementById(id); }

function progressShow(text, value) {
  $("progress-label").textContent = text;
  $("progress-fill").style.width = Math.max(0, Math.min(100, Number(value) || 0)) + "%";
}
function progressIdle() { progressShow("Gotowy", 0); }

function updateFooter(connection, scan) {
  if (connection !== undefined) footerConnectionText = connection;
  if (scan !== undefined) footerScanText = scan;
  $("footer-status").textContent = `${footerConnectionText} | ${footerScanText}`;
}

function updateLockBanner() {
  const banner = $("lock-banner");
  if (selectedId == null) { banner.classList.remove("visible"); return; }
  const m = moduleById(selectedId);
  if (m?.key_mismatch) {
    banner.textContent = `Moduł ID=${selectedId} — niezgodny MASTER_KEY. Napraw klucz w panelu serwisowym.`;
    banner.classList.add("visible");
  } else banner.classList.remove("visible");
}

function connectionLabelFromStatus(st) {
  if (!st.bus_ok) return st.bus_error ? `Błąd: ${st.bus_error}` : "Rozłączono";
  const port = st.can_port || st.configured_port || "?";
  const br = st.can_bitrate ? `${Math.round(st.can_bitrate / 1000)} kbps` : "?";
  return `Połączono: ${port} / ${st.can_interface || "?"} ${br}${st.secure_enabled ? " · Secure" : ""}`;
}

function scanLabelFromStatus(st, modules) {
  if (guiRefreshInProgress) return footerScanText;
  if (st.last_scan_status === "never") return "Brak skanu";
  if (st.last_scan_status === "error") return "Błąd skanu";
  return `Moduły: ${st.module_count ?? modules?.length ?? 0}`;
}

function selectedConnectionSuffix() {
  if (selectedId == null) return "Wybrano: brak";
  const m = moduleById(selectedId);
  return m?.key_mismatch ? `Wybrano ID=${selectedId} (MASTER_KEY!)` : `Wybrano ID=${selectedId}`;
}

async function runProgressSteps(steps, onComplete) {
  const token = ++progressTaskToken;
  guiRefreshInProgress = true;
  try {
    for (let i = 0; i < steps.length; i++) {
      if (token !== progressTaskToken) return;
      const [label, fn] = steps[i];
      progressShow(label, steps.length <= 1 ? 50 : (i / steps.length) * 90 + 5);
      await fn();
    }
    if (token === progressTaskToken) {
      progressShow("Gotowe", 100);
      if (onComplete) onComplete();
      setTimeout(() => { if (!tabLoading) progressIdle(); }, 400);
    }
  } finally { guiRefreshInProgress = false; }
}

function apiUrl(path) {
  return new URL(String(path).replace(/^\//, ""), window.location.href).toString();
}
async function api(path, opts) {
  const res = await fetch(apiUrl(path), opts);
  const data = await res.json().catch(() => ({}));
  return { res, data };
}

function appendLog(msg) {
  const box = $("log-box");
  const ts = new Date().toLocaleTimeString("pl-PL", { hour12: false });
  box.textContent += `[${ts}] ${msg}\n`;
  box.scrollTop = box.scrollHeight;
}

function setTab(name) {
  currentTab = name;
  document.querySelectorAll(".tab").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".panel").forEach(p => p.classList.toggle("active", p.id === "panel-" + name));
  if (name !== "logs" && selectedId != null) scheduleTabLoad();
  else renderActivePanel();
}

document.querySelectorAll(".tab").forEach(btn => btn.addEventListener("click", () => setTab(btn.dataset.tab)));

function moduleById(id) { return modulesCache.find(x => x.module_id === id); }

function setTabLoading(on, text) {
  tabLoading = on;
  if (on && text) progressShow(text.replace(/^\[tab-load\]\s*/, ""), 35);
  $("btn-reload-tab").disabled = on || selectedId == null;
}

async function loadCurrentTab(force, wrapProgress = true) {
  if (selectedId == null || currentTab === "logs" || currentTab === "connection") return;
  if (tabLoading && !force) return;
  const inner = async () => {
    setTabLoading(true, `ID=${selectedId} · ${currentTab}`);
    appendLog(`[tab-load] ID=${selectedId}, ${currentTab}`);
    const { res, data } = await api(`/api/modules/${selectedId}/tab-load`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tab: currentTab }),
    });
    for (const step of (data.steps || [])) {
      appendLog(`  ${step.name}: ${step.ok ? "OK" : "ERR"}${step.error ? " — " + step.error : ""}`);
    }
    if (!res.ok) { setTabLoading(false); throw new Error(data.error || res.status); }
    if (data.mappings) mappingsCache = data.mappings;
    if (data.sensors) sensorsCache = data.sensors;
    await loadAll(false);
    renderActivePanel();
    setTabLoading(false);
  };
  if (wrapProgress && !guiRefreshInProgress) await runProgressSteps([[`Ładowanie ${currentTab}…`, inner]]);
  else await inner();
}

function scheduleTabLoad() {
  if (tabLoadTimer) clearTimeout(tabLoadTimer);
  tabLoadTimer = setTimeout(() => loadCurrentTab(false), 80);
}

async function loadAll(refreshTab) {
  const { res, data } = await api("/api/state");
  if (!res.ok) { updateFooter("Błąd API", `HTTP ${res.status}`); return; }
  lastStatus = data.status || {};
  lastEntityCount = (data.entities || []).length;
  modulesCache = data.modules || [];
  updateFooter(
    selectedId != null ? selectedConnectionSuffix() : connectionLabelFromStatus(lastStatus),
    scanLabelFromStatus(lastStatus, modulesCache)
  );
  updateLockBanner();
  renderConnection();
  renderModules();
  updateButtons();
  if (refreshTab !== false) renderActivePanel();
}

function renderConnection() {
  const st = lastStatus;
  $("conn-status").innerHTML = `
    <tr><td>Magistrala</td><td class="${st.bus_ok ? "ok" : "err"}">${st.bus_ok ? "OK" : "BŁĄD"}</td></tr>
    <tr><td>Port / interfejs</td><td>${st.can_port || st.configured_port || "-"} / ${st.can_interface || "-"}</td></tr>
    <tr><td>Bitrate</td><td>${st.can_bitrate || "-"}</td></tr>
    <tr><td>Secure CAN</td><td>${st.secure_enabled ? "TAK" : "NIE"}</td></tr>
    <tr><td>Moduły</td><td>${st.module_count ?? 0}</td></tr>
    <tr><td>Ostatni skan</td><td>${st.last_scan_status || "-"} ${st.last_scan_at ? new Date(st.last_scan_at * 1000).toLocaleString("pl-PL") : ""}</td></tr>
    <tr><td>Encje HA</td><td>${(dataEntitiesCount())}</td></tr>`;
}

function dataEntitiesCount() { return lastEntityCount; }

function updateButtons() {
  const has = selectedId != null;
  ["btn-reboot","btn-ota-read","btn-ota-upload","btn-gpio-clear","btn-reload-tab","btn-deep-refresh",
   "btn-service","btn-mapping-send","btn-mapping-clear","btn-rename","btn-identify","btn-provision-key"].forEach(id => {
    const el = $(id);
    if (el) el.disabled = !has || tabLoading;
  });
}

function renderModules() {
  const tb = $("modules-body");
  tb.innerHTML = "";
  for (const m of modulesCache) {
    const tr = document.createElement("tr");
    tr.className = selectedId === m.module_id ? "selected" : "";
    const key = m.has_master_key === true ? "TAK" : m.has_master_key === false ? "NIE" : "?";
    const sum = m.summary_details || `B=${m.button_count ?? "?"} R=${m.relay_count ?? "?"} S=${m.shutter_count ?? "?"}`;
    tr.innerHTML = `<td>${m.module_id}</td><td>${m.name || "-"}</td><td>${m.hw_name || m.hw_type}</td><td>${m.mac || "-"}</td><td>${key}</td><td>${sum}</td>`;
    tr.onclick = () => selectModule(m.module_id);
    tb.appendChild(tr);
  }
}

async function selectModule(id) {
  selectedId = id;
  pinoutCache = null;
  mappingsCache = [];
  sensorsCache = [];
  draftMappings = [];
  $("ota-box").style.display = "none";
  renderModules();
  updateButtons();
  updateFooter(selectedConnectionSuffix(), scanLabelFromStatus(lastStatus, modulesCache));
  updateLockBanner();
  scheduleTabLoad();
}

function renderActivePanel() {
  if (currentTab === "connection") renderConnection();
  if (selectedId == null) return;
  const m = moduleById(selectedId);
  if (currentTab === "control") renderControl(selectedId);
  if (currentTab === "gpio") renderGpioTab(selectedId);
  if (currentTab === "shutters") renderShutters(selectedId);
  if (currentTab === "mapping") renderMappings(m?.runtime?.mappings?.length ? m.runtime.mappings : mappingsCache);
  if (currentTab === "sensors") renderSensors(m?.runtime?.sensors?.length ? m.runtime.sensors : sensorsCache);
}

function renderControl(id) {
  const m = moduleById(id);
  if (!m) return;
  const relays = m.control_relays?.length ? m.control_relays : (m.runtime?.relays || []);
  const rb = $("relays-box");
  if (!relays.length) { rb.innerHTML = "<span class='sub'>Brak danych — przeładuj Sterowanie</span>"; return; }
  rb.innerHTML = "";
  for (const r of relays) {
    if (r.shutter_reserved) continue;
    const wrap = document.createElement("div");
    wrap.className = "relay-row";
    const pulse = r.pulse_ms || 0;
    wrap.innerHTML = `<span class="pill">R${r.relay_no}</span> ${r.on ? "ON" : "OFF"}${pulse ? ` <span class="warn">[${pulse}ms]</span>` : ""}`;
    ["on","off","toggle"].forEach(st => {
      const b = document.createElement("button");
      b.textContent = st.toUpperCase(); b.className = "relay-btn secondary";
      b.onclick = () => setRelay(id, r.relay_no, st, pulse);
      wrap.appendChild(b);
    });
    const inp = document.createElement("input"); inp.type = "number"; inp.value = pulse;
    const save = document.createElement("button"); save.textContent = "Impuls ms"; save.className = "relay-btn secondary";
    save.onclick = () => setPulse(id, r.relay_no, inp.value);
    wrap.appendChild(inp); wrap.appendChild(save);
    rb.appendChild(wrap);
  }
}

function renderShutters(id) {
  const m = moduleById(id);
  const tb = $("shutters-body");
  tb.innerHTML = "";
  if (!m) return;
  const rt = m.runtime || {};
  const map = rt.shutter_map || {};
  const status = {};
  for (const s of (rt.shutters || [])) status[s.shutter_no] = s;
  const nums = new Set([...Object.keys(map).map(Number), ...Object.keys(status).map(Number)]);
  if (!nums.size) { tb.innerHTML = "<tr><td colspan='6' class='sub'>Brak rolet — przeładuj zakładkę</td></tr>"; return; }
  for (const sid of [...nums].sort((a,b)=>a-b)) {
    const pair = map[String(sid)] || map[sid] || [0,0];
    const st = status[sid] || {};
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>S${sid}</td><td>${pair[0]||"-"}</td><td>${pair[1]||"-"}</td><td>${st.position ?? "?"}%</td><td class="sh-cmd"></td><td class="sh-cfg"></td>`;
    tr.querySelector(".sh-cmd").append(...["open","stop","close"].map(cmd => {
      const b = document.createElement("button"); b.textContent = cmd; b.className = "relay-btn secondary";
      b.onclick = () => setShutter(id, sid, cmd); return b;
    }));
    const posInp = document.createElement("input"); posInp.type = "number"; posInp.min = 0; posInp.max = 100; posInp.placeholder = "%";
    const posBtn = document.createElement("button"); posBtn.textContent = "Poz"; posBtn.className = "relay-btn secondary";
    posBtn.onclick = () => setShutter(id, sid, "position", posInp.value);
    tr.querySelector(".sh-cfg").append(posInp, posBtn);
    tb.appendChild(tr);
  }
}

function renderMappings(rows) {
  const all = [...(rows || []), ...draftMappings];
  const tb = $("mapping-body");
  tb.innerHTML = "";
  if (!all.length) { tb.innerHTML = "<tr><td colspan='7' class='sub'>Brak mapowań</td></tr>"; return; }
  for (const r of all) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${r.button||r.button_num||"-"}</td><td>${r.action||"-"}</td><td>${r.receiver||"Lokalny"}</td><td>${r.target_id||r.target_module_id||"-"}</td><td>${r.target_type||r.kind||"-"}</td><td>${r.target||r.relay_num||"-"}</td><td>${r.state||"-"}</td>`;
    tb.appendChild(tr);
  }
}

function renderSensors(rows) {
  const tb = $("sensors-body");
  tb.innerHTML = "";
  const m = moduleById(selectedId);
  const scan = m?.runtime?.sensor_scan;
  if (scan) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="3" class="hint">Scan flags=0x${(scan.flags||0).toString(16)} DS18=${scan.ds18_gpio_or_count}</td>`;
    tb.appendChild(tr);
  }
  if (!rows?.length) { tb.innerHTML += "<tr><td colspan='3' class='sub'>Brak telemetrii — użyj Skan</td></tr>"; return; }
  rows.forEach((s, i) => {
    const tr = document.createElement("tr");
    const hex = (s.data || []).map(b => (b&0xff).toString(16).padStart(2,"0")).join(" ");
    tr.innerHTML = `<td>${s.sensor_no ?? i+1}</td><td>${SENSOR_TYPES[s.sensor_type] || s.sensor_type}</td><td>${hex || "-"}</td>`;
    tb.appendChild(tr);
  });
}

async function renderGpioTab(id) {
  const { data } = await api(`/api/modules/${id}/pinout`);
  pinoutCache = data;
  renderPinoutCanvas();
  renderGpioTable();
  const m = moduleById(id);
  const bt = m?.runtime?.button_timing || {};
  $("multiclick-ms").value = bt.multiclick_ms ?? 400;
  $("longpress-ms").value = bt.longpress_ms ?? 800;
}

function renderPinoutCanvas() {
  const canvas = $("pinout-canvas");
  canvas.innerHTML = "";
  if (!pinoutCache?.ok || !pinoutCache.profile) {
    canvas.innerHTML = "<p class='hint'>Brak profilu pinout dla tego HW</p>";
    return;
  }
  const imgName = pinoutCache.profile.board_image;
  if (imgName) {
    const img = document.createElement("img");
    img.src = apiUrl(`/static/assets/${imgName}`);
    img.alt = pinoutCache.pinout_name;
    img.className = "pinout-img";
    canvas.appendChild(img);
  }
  const grid = document.createElement("div");
  grid.className = "pinout-grid";
  for (const row of pinoutCache.gpios || []) {
    const pin = document.createElement("div");
    pin.className = "pin-chip" + (row.reserved ? " reserved" : "");
    pin.title = row.strapping_note || "";
    pin.textContent = `GPIO${row.gpio} · ${row.role_name || "Unused"}`;
    if (!row.reserved) pin.onclick = () => { $("gpio-focus").value = row.gpio; };
    grid.appendChild(pin);
  }
  canvas.appendChild(grid);
}

function renderGpioTable() {
  const tb = $("gpio-body");
  tb.innerHTML = "";
  $("pinout-hint").textContent = pinoutCache?.ok
    ? `Pinout: ${pinoutCache.pinout_name} · ${(pinoutCache.gpios||[]).length} pinów`
    : (pinoutCache?.error || "Brak profilu");
  for (const row of pinoutCache?.gpios || []) {
    const tr = document.createElement("tr");
    if (row.reserved) tr.className = "reserved";
    const state = row.logical != null ? `${row.logical}/${row.raw}` : "—";
    tr.innerHTML = `<td>GPIO${row.gpio}</td><td>${row.role_name||"Unused"}</td><td>${row.index??0}</td><td>${state}</td><td class="gpio-assign"></td><td></td>`;
    const cell = tr.querySelector(".gpio-assign");
    if (!row.reserved) {
      const sel = document.createElement("select");
      ROLES.forEach(r => { const o = document.createElement("option"); o.value = r; o.textContent = r; if (r === (row.role_name||"Unused")) o.selected = true; sel.appendChild(o); });
      const idx = document.createElement("input"); idx.type = "number"; idx.value = row.index || 0;
      const flags = document.createElement("input"); flags.type = "number"; flags.min = 0; flags.max = 3; flags.value = row.flags || 0; flags.title = "flags";
      const btn = document.createElement("button"); btn.textContent = "OK"; btn.className = "relay-btn";
      btn.onclick = () => assignGpio(row.gpio, sel.value, idx.value, flags.value);
      const clr = document.createElement("button"); clr.textContent = "×"; clr.className = "relay-btn danger";
      clr.onclick = () => clearGpio(row.gpio);
      cell.append(sel, idx, flags, btn);
      tr.lastElementChild.appendChild(clr);
    }
    tb.appendChild(tr);
  }
}

async function setRelay(mid, relayNo, state, pulseMs = 0) {
  const { res, data } = await api(`/api/modules/${mid}/relays/${relayNo}`, {
    method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ state }),
  });
  if (res.ok) { await loadAll(false); renderControl(mid); return; }
  appendLog(`Relay error: ${data.error || res.status}`);
}
async function setPulse(mid, relayNo, pulse_ms) {
  await api(`/api/modules/${mid}/relays/${relayNo}/pulse`, { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ pulse_ms: Number(pulse_ms) }) });
  await loadCurrentTab(true);
}
async function setShutter(mid, shutterNo, command, param = 0) {
  await api(`/api/modules/${mid}/shutters/${shutterNo}`, { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ command, param: Number(param)||0 }) });
  await loadAll(false); renderShutters(mid);
}
async function assignGpio(gpio, role, index, flags = 0) {
  if (selectedId == null) return;
  await api(`/api/modules/${selectedId}/gpio/${gpio}`, { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ role, index: Number(index), flags: Number(flags) }) });
  await renderGpioTab(selectedId);
}
async function clearGpio(gpio) {
  if (selectedId == null) return;
  await api(`/api/modules/${selectedId}/gpio/${gpio}`, { method: "DELETE" });
  await renderGpioTab(selectedId);
}

function addDraftMapping() {
  draftMappings.push({
    kind: "button_relay",
    button_num: Number($("map-btn").value) || 1,
    action: $("map-action").value,
    target_module_id: Number($("map-target-id").value) || selectedId,
    relay_num: Number($("map-relay").value) || 1,
    state: $("map-state").value,
    button: `Btn ${$("map-btn").value}`,
    receiver: "Lokalny",
    target_type: "Przekaznik",
    target_id: $("map-target-id").value || "-",
    target: $("map-relay").value,
  });
  renderMappings(mappingsCache);
}

async function sendMappingsToModule() {
  if (selectedId == null) return;
  const rows = draftMappings.length ? draftMappings : mappingsCache;
  const payload = rows.map(r => ({
    kind: r.kind || (r.target_type === "Roleta" ? "button_shutter" : "button_relay"),
    source_module_id: selectedId,
    target_module_id: Number(r.target_module_id || r.target_id || selectedId),
    button_num: Number(r.button_num || (String(r.button||"").match(/\d+/)||[1])[0]),
    action: r.action || "Jednoklik",
    relay_num: Number(r.relay_num || r.target || 1),
    relay_state: r.state || "Zalacz",
    shutter_num: Number(r.shutter_num || r.target || 1),
    shutter_cmd: 1,
  }));
  const { res, data } = await api(`/api/modules/${selectedId}/mappings`, {
    method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ mappings: payload }),
  });
  appendLog(res.ok ? `Mapowania zapisane: ${data.applied}` : `Mapowanie błąd: ${data.errors?.join(", ") || data.error}`);
  draftMappings = [];
  mappingsCache = data.mappings || [];
  await loadAll(false);
}

async function clearAllMappings() {
  if (selectedId == null || !confirm("Wyczyścić WSZYSTKIE mapowania modułu?")) return;
  await api(`/api/modules/${selectedId}/mappings`, { method: "DELETE" });
  draftMappings = []; mappingsCache = [];
  await loadAll(false);
}

async function saveButtonTiming() {
  if (selectedId == null) return;
  await api(`/api/modules/${selectedId}/button-timing`, {
    method: "PUT", headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ multiclick_ms: Number($("multiclick-ms").value), longpress_ms: Number($("longpress-ms").value) }),
  });
  await loadAll(false);
}

async function saveShutterConfig() {
  if (selectedId == null) return;
  const sid = Number($("shutter-slot").value) || 1;
  await api(`/api/modules/${selectedId}/shutters/${sid}/config`, {
    method: "PUT", headers: {"Content-Type":"application/json"},
    body: JSON.stringify({
      relay_open: Number($("shutter-open-relay").value),
      relay_close: Number($("shutter-close-relay").value),
      time_open_s: Number($("shutter-open-s").value),
      time_close_s: Number($("shutter-close-s").value),
    }),
  });
  await loadCurrentTab(true);
}

async function scanSensors(kind) {
  if (selectedId == null) return;
  appendLog(`Skan ${kind}…`);
  const { res, data } = await api(`/api/modules/${selectedId}/scan/${kind}`, { method: "POST" });
  appendLog(res.ok ? `Skan ${kind} OK` : `Skan ${kind} err: ${data.error}`);
  await loadAll(false);
}

function openServicePanel() {
  $("service-modal").classList.add("visible");
  if (selectedId != null) {
    $("svc-module-id").textContent = selectedId;
    refreshMasterKeyStatus();
  }
}
function closeServicePanel() { $("service-modal").classList.remove("visible"); }

async function refreshMasterKeyStatus() {
  if (selectedId == null) return;
  const { data } = await api(`/api/modules/${selectedId}/master-key`);
  $("svc-key-status").textContent = data.ok ? (data.has_master_key ? "MASTER_KEY: TAK" : "MASTER_KEY: NIE") : "Błąd odczytu";
}

async function provisionMasterKey() {
  if (selectedId == null) return;
  const key = $("svc-master-key").value.trim();
  const { res, data } = await api(`/api/modules/${selectedId}/master-key`, {
    method: "POST", headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ master_key_hex: key }),
  });
  appendLog(res.ok ? "MASTER_KEY wysłany" : `Provisioning błąd: ${data.error}`);
  await refreshMasterKeyStatus();
  await loadAll(false);
}

async function renameModule() {
  if (selectedId == null) return;
  const name = prompt("Nowa nazwa modułu:", moduleById(selectedId)?.name || "");
  if (!name) return;
  await api(`/api/modules/${selectedId}/name`, { method: "PUT", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ name }) });
  await loadAll(true);
}

async function identifySelected() {
  if (selectedId == null) return;
  await api(`/api/modules/${selectedId}/identify`, { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ seconds: 5 }) });
  appendLog(`Identify ID=${selectedId}`);
}

async function setModuleIdByMac() {
  const mac = $("set-id-mac").value.trim();
  const mid = Number($("set-id-new").value);
  const { res, data } = await api("/api/modules/set-id-by-mac", {
    method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ mac, module_id: mid }),
  });
  appendLog(res.ok ? `Ustawiono ID=${mid} dla MAC ${mac}` : `Błąd: ${data.error}`);
  if (res.ok) await api("/api/scan", { method: "POST" }).then(() => loadAll(true));
}

async function uploadOta() {
  if (selectedId == null) return;
  const file = $("ota-file").files[0];
  if (!file) return alert("Wybierz plik .bin");
  appendLog(`OTA upload ${file.name} (${file.size} B)…`);
  const fd = new FormData();
  fd.append("firmware", file);
  const res = await fetch(apiUrl(`/api/modules/${selectedId}/ota/upload`), { method: "POST", body: fd });
  const data = await res.json().catch(() => ({}));
  $("ota-box").style.display = "block";
  $("ota-box").textContent = JSON.stringify(data, null, 2);
  appendLog(res.ok ? "OTA OK" : `OTA błąd: ${data.error}`);
}

$("btn-refresh").onclick = () => loadAll(true);
$("btn-deep-refresh").onclick = async () => {
  if (selectedId == null) return;
  await runProgressSteps([[`Deep refresh ID=${selectedId}…`, async () => {
    await api(`/api/modules/${selectedId}/refresh`, { method: "POST" });
    await loadAll(true); await loadCurrentTab(true, false);
  }]]);
};
$("btn-reload-tab").onclick = () => loadCurrentTab(true);
$("btn-scan").onclick = async () => {
  $("btn-scan").disabled = true;
  await runProgressSteps([["Skan F5…", async () => {
    const { res, data } = await api("/api/scan", { method: "POST" });
    if (!res.ok) throw new Error(data.error);
    await loadAll(true);
    if (selectedId != null) await loadCurrentTab(true, false);
  }]], () => appendLog("Skan zakończony"));
  $("btn-scan").disabled = false;
};
$("btn-reboot").onclick = async () => {
  if (selectedId == null || !confirm(`Restart ID=${selectedId}?`)) return;
  await api(`/api/modules/${selectedId}/reboot`, { method: "POST" });
};
$("btn-gpio-clear").onclick = async () => {
  if (selectedId == null || !confirm("Wyczyścić wszystkie role GPIO?")) return;
  await api(`/api/modules/${selectedId}/gpio/clear`, { method: "POST" });
  await loadCurrentTab(true);
};
$("btn-ota-read").onclick = async () => {
  if (selectedId == null) return;
  const { data } = await api(`/api/modules/${selectedId}/ota`);
  $("ota-box").style.display = "block";
  $("ota-box").textContent = JSON.stringify(data, null, 2);
};
$("btn-ota-upload").onclick = uploadOta;
$("btn-clear-log").onclick = () => { $("log-box").textContent = ""; };
$("btn-service").onclick = openServicePanel;
$("btn-service-close").onclick = closeServicePanel;
$("btn-provision-key").onclick = provisionMasterKey;
$("btn-rename").onclick = renameModule;
$("btn-identify").onclick = identifySelected;
$("btn-set-module-id").onclick = setModuleIdByMac;
$("btn-mapping-add").onclick = addDraftMapping;
$("btn-mapping-send").onclick = sendMappingsToModule;
$("btn-mapping-clear").onclick = clearAllMappings;
$("btn-save-timing").onclick = saveButtonTiming;
$("btn-save-shutter-cfg").onclick = saveShutterConfig;
$("btn-scan-1wire").onclick = () => scanSensors("1wire");
$("btn-scan-i2c").onclick = () => scanSensors("i2c");
$("btn-scan-sensors").onclick = () => scanSensors("sensors");
$("btn-scan-mcp").onclick = () => scanSensors("mcp23017");

document.addEventListener("keydown", e => {
  if (e.key === "F5") { e.preventDefault(); $("btn-scan").click(); }
});

loadAll(false);
setInterval(() => { if (!guiRefreshInProgress && !tabLoading) loadAll(false); }, 30000);
