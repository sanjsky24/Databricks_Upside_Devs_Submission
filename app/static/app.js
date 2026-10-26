// DengueRadar Monday screen. Data comes from /api/data (Lakebase); alerts are drafted by /api/draft (LLM on
// Databricks) and logged with /api/alerts; /api/me says who signed in. The team planner and its time estimates
// run here so officers can try any number of teams and any assumptions.
// Layout: one board of cards in three columns (index.html); the gaps between cards are drag handles.

const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const title = (s) => String(s || "").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
const pct = (x) => (x == null ? "–" : `${Math.round(100 * x)}%`);
const yes = (v) => v === true || v === "true";
const list = (xs) => (xs.length <= 1 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);
const LEVEL_NUM = { High: 2, Medium: 1, Low: 0 };
const RADIUS = 300; // metres covered by one team, as in notebooks/04_team_plan.py

const svg = (d) => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
const ICON = {
  target: svg('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r=".5"/>'),
  pulse: svg('<path d="M3 12h4l3-8 4 16 3-8h4"/>'),
  home: svg('<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/>'),
  lock: svg('<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>'),
  plus: svg('<path d="M12 5v14M5 12h14"/>'),
  arrow: svg('<path d="M5 12h14M13 6l6 6-6 6"/>'),
  expand: svg('<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>'),
  shrink: svg('<path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7"/>'),
  check: svg('<path d="M5 12l5 5 9-10"/>'),
  cross: svg('<path d="M6 6l12 12M18 6L6 18"/>'),
};

let D, map, areaLayer, labelLayer, teamLayer, routeLayer, blockLayer, streets, routeCtl, fitAll, currentPlan = [];
let routeMode = false;  // a team's route is shown on OpenStreetMap streets
let mapTouched = false;  // once the officer (or a button) moves the map, stop refitting it to Singapore
let ALERTS = [];
let ME = { name: "", account: "" };
let selected = null;     // planning area open in the Areas card
let clMode = "today";    // Clusters card: "today" or "new"
let alertOpen = false;   // Draft-an-alert card: the + button, or the list of areas
const overlays = {};

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode: lasts for this visit */ } },
};

function day(s) {
  if (!s) return "–";
  const d = new Date(s.length === 10 ? `${s}T00:00:00` : s);
  return d.toLocaleDateString("en-SG", { day: "numeric", month: "short" });
}
const chip = (lvl) => `<span class="chip ${esc(lvl)}">${esc(lvl)}</span>`;

// ------------------------------------------------------------------ tooltip
const tip = $("tip");
function showTip(ev, html) {
  tip.innerHTML = html;
  tip.hidden = false;
  tip.style.left = `${Math.min(ev.clientX + 14, window.innerWidth - tip.offsetWidth - 8)}px`;
  tip.style.top = `${ev.clientY + 14}px`;
}
const hideTip = () => { tip.hidden = true; };

// ------------------------------------------------------------------ load
setupSplits();  // before the map, so it is drawn at its final size
fetch("/api/data")
  .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
  .then((d) => { D = d; init(); })
  .catch((e) => { $("basis").textContent = `Could not load data (${e.message}).`; });

function init() {
  const m = D.meta || {};
  $("basis").textContent = `Forecast from NEA's list of ${day(m.basis_date)} · clusters as at ${day(m.cluster_list_date)}`;
  D.areas.sort((a, b) => b.predicted_level_num - a.predicted_level_num || b.p_high - a.p_high ||
    (a.new_cluster_rank ?? 99) - (b.new_cluster_rank ?? 99));
  prepBlocks();
  drawMap();
  areasTable();
  clustersCard();
  record();
  weekly();
  new ResizeObserver(() => requestAnimationFrame(weekly)).observe($("weekly"));
  const teams = $("teams");
  teams.addEventListener("input", () => { $("teams-out").textContent = teams.value; replan(); });
  replan();
  document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => { clMode = b.dataset.mode; clustersCard(); }));
  $("areas-back").addEventListener("click", closeArea);
  setupDraftDialog();
  setupPopup();
  window.addEventListener("hashchange", route);
  render();
  route();
  signIn();
  loadAlerts();
}

// what depends on alerts or the signed-in name
function render() {
  greet(); overall(); alertCard();
  if (selected) selectArea(selected);
}

// links of the form #draft=<PLANNING AREA> open the alert dialog
function route() {
  const h = decodeURIComponent(location.hash.slice(1));
  if (h.startsWith("draft=")) openDraft(h.slice(6));
}

// ------------------------------------------------------------------ sign-in and greeting
async function signIn() {
  try { const r = await fetch("/api/me"); if (r.ok) Object.assign(ME, await r.json()); } catch { /* local preview */ }
  const key = `dengueradar.name.${ME.account || "local"}`;
  ME.name = store.get(key) || "";
  const chipBtn = $("user-chip");
  const showChip = () => { chipBtn.hidden = !ME.name; chipBtn.textContent = ME.name ? `${ME.name} · change` : ""; };
  const ask = () => {
    $("welcome-who").textContent = ME.shared
      ? `You're signed in with the team's shared Databricks account (${ME.account}), so DengueRadar can't tell who you are.`
      : ME.account ? `You're signed in with your Databricks account (${ME.account}).` : "";
    $("welcome-name").value = ME.name || (ME.shared ? "" : ME.suggested_name || "");
    $("welcome").showModal();
    $("welcome-name").focus();
  };
  $("welcome").addEventListener("close", () => {
    const n = $("welcome-name").value.trim();
    if (n) { ME.name = n; store.set(key, n); }
    showChip(); render();
  });
  chipBtn.addEventListener("click", ask);
  if (!ME.name) ask(); else { showChip(); render(); }
}

function greet() {
  // one line, no numbers
  const hour = +new Intl.DateTimeFormat("en-SG", { hour: "numeric", hourCycle: "h23", timeZone: "Asia/Singapore" }).format(new Date());
  const part = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const high = D.areas.filter((a) => a.predicted_level === "High").map((a) => a.area);
  $("hello").textContent = `${part}${ME.name ? `, ${ME.name}` : ""}.`;
  $("hello-sum").textContent = high.length ? `${list(high)} ${high.length === 1 ? "is" : "are"} forecast High.` : "No area is forecast High.";
}

// ------------------------------------------------------------------ overall information
function overall() {
  const lvl = (l) => D.areas.filter((a) => a.predicted_level === l);
  const high = lvl("High"), med = lvl("Medium");
  const cases = D.clusters.reduce((s, c) => s + (c.total || 0), 0);
  const nearHomes = B.filter((b) => yes(b.near_cluster)).reduce((s, b) => s + b.h, 0);
  const need = alertAreas(), sent = need.filter((a) => sentThisWeek(a.planning_area)).length;
  $("overall").innerHTML = `
    <div class="big2">
      <div class="big"><b class="hi">${high.length}</b><span>areas forecast<br>High</span></div>
      <i class="vr"></i>
      <div class="big"><b>${med.length}</b><span>areas forecast<br>Medium</span></div>
    </div>
    <div class="flagged">${[...high, ...med].map((a) => `<button data-pa="${esc(a.planning_area)}">${esc(a.area)} ${chip(a.predicted_level)}</button>`).join("")}</div>
    <div class="progress" role="progressbar" aria-label="Areas alerted this week" aria-valuemin="0" aria-valuemax="${need.length}" aria-valuenow="${sent}">
      <i style="width:${need.length ? (100 * sent) / need.length : 0}%"></i></div>
    <div class="prog-l"><b>${sent}/${need.length}</b> areas alerted this week</div>
    <div class="tiles">
      <div class="tile c"><span class="v">${ICON.target}<b>${D.clusters.length}</b></span><span>clusters on NEA's list</span></div>
      <div class="tile"><span class="v">${ICON.pulse}<b>${cases}</b></span><span>cases in clusters</span></div>
      <div class="tile h"><span class="v">${ICON.home}<b>${nearHomes.toLocaleString()}</b></span><span>homes within 300 m</span></div>
    </div>`;
  $("overall").querySelectorAll("[data-pa]").forEach((b) => b.addEventListener("click", () => selectArea(b.dataset.pa)));
}

// ------------------------------------------------------------------ map (planning areas only, no street tiles)
function areaStyle(a) {
  const fill = { High: css("--high"), Medium: css("--medium"), Low: css("--low") }[a.predicted_level];
  const watch = yes(a.new_cluster_watch) && a.level_today === 0;
  return {
    fillColor: fill, fillOpacity: routeMode ? (a.predicted_level === "Low" ? 0 : 0.12) : a.predicted_level === "Low" ? 1 : 0.88,
    color: watch ? css("--accent") : css("--land-line"), weight: watch ? 2.2 : 1, dashArray: watch ? "5 4" : null,
  };
}

function drawMap() {
  map = L.map("map", { zoomSnap: 0.25, scrollWheelZoom: false });
  map.attributionControl.setPrefix(false);
  map.attributionControl.addAttribution("Dengue data: NEA");  // OpenStreetMap is credited here too while its streets show
  areaLayer = L.geoJSON({ type: "FeatureCollection", features: D.areas.filter((a) => a.geometry).map((a) => ({ type: "Feature", geometry: a.geometry, properties: a })) }, {
    style: (f) => areaStyle(f.properties),
    onEachFeature: (f, layer) => {
      const a = f.properties;
      layer.on("click", () => selectArea(a.planning_area));
      layer.on("mousemove", (e) => showTip(e.originalEvent,
        `<b>${esc(a.area)}</b><br>Today ${esc(a.level_today_name)} → ${esc(a.predicted_level)} in 2 weeks<br>Chance of High ${pct(a.p_high)}` +
        (a.new_cluster_rank && a.level_today === 0 ? `<br>New-cluster rank ${a.new_cluster_rank}` : "")));
      layer.on("mouseout", hideTip);
    },
  }).addTo(map);
  // fit to the main island; the outlying islands would leave the map mostly sea
  const mainland = L.latLngBounds([]);
  areaLayer.eachLayer((l) => { if (!/ISLANDS$/.test(l.feature.properties.planning_area)) mainland.extend(l.getBounds()); });
  fitAll = () => map.fitBounds(mainland, { padding: [6, 6] });
  const fit = () => { map.invalidateSize(); if (!mapTouched) fitAll(); };
  fit();
  new ResizeObserver(fit).observe($("map"));  // cards are resizable
  ["mousedown", "touchstart", "wheel", "keydown"].forEach((ev) =>
    $("map").addEventListener(ev, () => { mapTouched = true; }, { once: true, passive: true }));

  overlays.wolbachia = L.geoJSON({ type: "FeatureCollection", features: D.wolbachia_zones.filter((z) => z.geometry).map((z) => ({ type: "Feature", geometry: z.geometry, properties: z })) }, {
    style: { color: css("--wol"), weight: 1.2, fillColor: css("--wol"), fillOpacity: 0.3 }, interactive: false,
  });
  overlays.aedes = L.layerGroup(D.high_aedes.map((p) => L.circleMarker([p.lat, p.lon], {
    radius: 3.5, color: css("--surface"), weight: 1, fillColor: css("--aedes"), fillOpacity: 0.95, interactive: false })));
  overlays.clusters = L.layerGroup(D.clusters.map((c) => {
    const mk = L.circleMarker([c.lat, c.lon], { radius: 4 + 2 * Math.sqrt(c.total || 1), color: css("--surface"), weight: 2,
      fillColor: css("--cluster"), fillOpacity: 0.8 });
    mk.on("mousemove", (e) => showTip(e.originalEvent, `<b>${esc(c.locality)}</b><br>${c.total} cases` +
      (c.recent != null ? ` · ${c.recent} in the last 2 weeks` : "")));
    mk.on("mouseout", hideTip);
    return mk;
  }));
  const named = (a) => a.predicted_level !== "Low" || a.level_today > 0;
  labelLayer = L.layerGroup(D.areas.filter((a) => named(a) || (yes(a.new_cluster_watch) && a.level_today === 0)).map((a) =>
    L.marker([a.lat, a.lon], { interactive: false, icon: L.divIcon({ className: "area-label", iconSize: null,
      html: named(a)
        ? `<span style="display:inline-block;transform:translate(-50%,-50%);white-space:nowrap">${esc(a.area)}</span>`
        : `<span class="rank-pin" title="${esc(a.area)}">${a.new_cluster_rank}</span>` }) })));

  // HDB blocks appear once zoomed in to street level
  const canvas = L.canvas({ padding: 0.2 });
  const priority = new Map(B.map((b) => [`${b.blk_no}|${b.street}`, b]));
  blockLayer = L.layerGroup();
  const drawBlocks = () => {
    blockLayer.clearLayers();
    if (map.getZoom() < 16 || !$("lay-blocks").checked) { blockLayer.remove(); return; }
    const view = map.getBounds().pad(0.1);
    for (const b of D.hdb_blocks) {
      if (!view.contains([b.lat, b.lon])) continue;
      const p = priority.get(`${b.blk_no}|${b.street}`);
      const col = p ? (yes(p.near_cluster) ? css("--cluster") : css("--aedes")) : css("--block");
      L.circleMarker([b.lat, b.lon], { renderer: canvas, radius: p ? 6 : 4.5, color: css("--surface"), weight: 1, fillColor: col, fillOpacity: 0.95 })
        .on("mousemove", (e) => showTip(e.originalEvent, `<b>Blk ${esc(b.blk_no)} ${esc(title(b.street))}</b><br>${b.floors ?? "?"} floors · ${b.homes} homes` +
          (p ? `<br>${esc(p.reason)}` : "") + (yes(b.in_wolbachia) ? "<br>In a Wolbachia zone" : "")))
        .on("mouseout", hideTip).addTo(blockLayer);
    }
    blockLayer.addTo(map);
  };
  map.on("zoomend moveend", drawBlocks);

  const toggles = [
    ["wolbachia", "Wolbachia", true], ["aedes", "High-mosquito spots", true], ["clusters", "Clusters", true],
    ["teams", "Team sites", false], ["blocks", "HDB blocks", true], ["labels", "Names", true],
  ];
  $("layers").innerHTML = toggles.map(([k, l, on]) => `<label><input type="checkbox" id="lay-${k}"${on ? " checked" : ""}> ${esc(l)}</label>`).join("");
  const layerOf = (k) => (k === "labels" ? labelLayer : k === "teams" ? teamLayer : overlays[k]);
  toggles.forEach(([k, , on]) => {
    if (k === "blocks") { $("lay-blocks").addEventListener("change", drawBlocks); return; }
    if (on && layerOf(k)) layerOf(k).addTo(map);
    $(`lay-${k}`).addEventListener("change", (e) => {
      const l = layerOf(k);
      if (l) (e.target.checked ? l.addTo(map) : l.remove());
      if (k === "teams" && !e.target.checked) exitRoute();
    });
  });

  $("legend").innerHTML = [
    `<span><i class="sw" style="background:${css("--high")}"></i>High in 2 weeks</span>`,
    `<span><i class="sw" style="background:${css("--medium")}"></i>Medium</span>`,
    `<span><i class="sw" style="background:${css("--low")};border:1px solid ${css("--line")}"></i>Low</span>`,
    `<span><i class="sw" style="border:2px dashed ${css("--accent")};background:none"></i>New-cluster watch</span>`,
    `<span><i class="sw" style="background:${css("--wol")};opacity:.5"></i>Wolbachia</span>`,
    `<span><i class="sw dot" style="background:${css("--aedes")}"></i>High-mosquito spots</span>`,
    `<span><i class="sw dot" style="background:${css("--cluster")}"></i>Clusters</span>`,
    `<span><i class="sw dot" style="background:${css("--team")}"></i>Team sites</span>`,
  ].join("");
}

function highlight(pa) {
  areaLayer.eachLayer((l) => {
    const p = l.feature.properties;
    l.setStyle(p.planning_area === pa ? { ...areaStyle(p), color: css("--ink"), weight: 3, dashArray: null } : areaStyle(p));
    if (p.planning_area === pa) l.bringToFront();
  });
}

// ------------------------------------------------------------------ areas: the table, or one area's reasons
function areasTable() {
  $("risk").innerHTML = `<thead><tr><th>Area</th><th>Today</th><th>In 2 wks</th><th class="num">High</th></tr></thead>
    <tbody>${D.areas.map((a) => `<tr data-pa="${esc(a.planning_area)}" tabindex="0"><td><b>${esc(a.area)}</b>${a.level_today === 0 && a.new_cluster_rank && a.new_cluster_rank <= 5 ? ` <span class="rank-inline">#${a.new_cluster_rank}</span>` : ""}</td>
    <td>${chip(a.level_today_name)}</td><td>${chip(a.predicted_level)}</td><td class="num">${pct(a.p_high)}</td></tr>`).join("")}</tbody>`;
  $("risk").querySelectorAll("tbody tr").forEach((tr) => {
    tr.addEventListener("click", () => selectArea(tr.dataset.pa));
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") selectArea(tr.dataset.pa); });
  });
}

function selectArea(pa) {
  const a = D.areas.find((x) => x.planning_area === pa);
  if (!a) return;
  const opening = selected !== pa;
  selected = pa;
  highlight(pa);
  const reasons = (a.why || "").split(" · ").filter(Boolean);
  const last = lastAlert(pa);
  $("h-areas").textContent = a.area;
  $("areas-back").hidden = false;
  $("risk").hidden = true;
  const box = $("area-detail");
  box.hidden = false;
  box.innerHTML = `
    <div class="why-levels">Today ${chip(a.level_today_name)} ${ICON.arrow} In 2 weeks ${chip(a.predicted_level)}</div>
    <div class="gauge"><span class="lbl">Chance of High</span>
      <div class="meter"><i style="width:${Math.round(100 * (a.p_high || 0))}%"></i></div><b>${pct(a.p_high)}</b></div>
    <h3>What the model sees</h3>
    <ul class="why-list">${reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
    <div class="facts">
      <div><b>${a.n_clusters}</b>clusters today</div>
      <div><b>${a.recent_cases}</b>cases, last 2 weeks</div>
      <div><b>${a.recent_next_door}</b>recent cases next door</div>
      <div><b>${pct(a.wolbachia_share)}</b>homes in Wolbachia</div>
      ${a.new_cluster_rank && a.level_today === 0 ? `<div><b>#${a.new_cluster_rank}</b>new-cluster rank</div>` : ""}
    </div>
    <div class="why-alert">
      <button class="primary" data-draft="${esc(pa)}">Draft alert</button>
      <span class="st ${last ? esc(last.status) : "none"}">${last ? `${last.status === "sent" ? "Sent" : "Draft saved"} ${esc(day(last.created_at))}${last.created_by_name ? ` by ${esc(last.created_by_name)}` : ""}` : "Not alerted"}</span>
    </div>`;
  box.querySelector("[data-draft]").addEventListener("click", () => openDraft(pa));
  if (opening) box.parentElement.scrollTop = 0;
}

function closeArea() {
  selected = null;
  $("h-areas").textContent = "Areas";
  $("areas-back").hidden = true;
  $("area-detail").hidden = true;
  $("risk").hidden = false;
  highlight(null);
}

// ------------------------------------------------------------------ clusters: today, or where new ones may start
function clustersCard() {
  document.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.mode === clMode)));
  const box = $("clusters");
  if (clMode === "today") {
    const cs = [...D.clusters].sort((a, b) => (b.total || 0) - (a.total || 0));
    box.innerHTML = `<ul class="cl-list">${cs.map((c, i) => `<li data-i="${i}" tabindex="0" title="${esc(title(c.planning_area))}${c.recent != null ? ` · ${c.recent} cases in the last 2 weeks` : ""}">
      <span class="dot" style="background:${(c.total || 0) >= 10 ? "var(--high)" : "var(--medium)"}"></span>
      <span class="nm">${esc(c.locality)}</span><span class="n">${c.total} cases</span></li>`).join("")}</ul>`;
    box.querySelectorAll("li").forEach((li) => {
      const go = () => { const c = cs[+li.dataset.i]; closePopup(); mapTouched = true; map.setView([c.lat, c.lon], 16); };
      li.addEventListener("click", go);
      li.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    });
  } else {
    const w = D.areas.filter((a) => yes(a.new_cluster_watch)).sort((a, b) => a.new_cluster_rank - b.new_cluster_rank);
    const top = Math.max(...w.map((a) => a.new_cluster_score || 0), 0.01);
    box.innerHTML = `<ol class="nw-list">${w.map((a) => `<li data-pa="${esc(a.planning_area)}" tabindex="0">
      <span class="rk">${a.new_cluster_rank}</span><span class="nm">${esc(a.area)}</span>
      <span class="mbar"><i style="width:${(100 * (a.new_cluster_score || 0)) / top}%"></i></span><span class="pc">${pct(a.new_cluster_score)}</span></li>`).join("")}</ol>`;
    box.querySelectorAll("li").forEach((li) => {
      li.addEventListener("click", () => selectArea(li.dataset.pa));
      li.addEventListener("keydown", (e) => { if (e.key === "Enter") selectArea(li.dataset.pa); });
    });
  }
}

// ------------------------------------------------------------------ team planner
let B, NB;
function prepBlocks() {
  B = D.blocks.map((b) => ({ ...b, w: +b.weight, h: +b.homes }));
  NB = B.map(() => []);
  const r2 = RADIUS * RADIUS;
  for (let i = 0; i < B.length; i++) {
    NB[i].push(i);
    for (let j = i + 1; j < B.length; j++) {
      const dx = B[i].x - B[j].x, dy = B[i].y - B[j].y;
      if (dx * dx + dy * dy <= r2) { NB[i].push(j); NB[j].push(i); }
    }
  }
}

function plan(teams) {
  // greedy: each team takes the 300 m site covering the most priority weight not yet covered (as planner.py)
  const covered = new Uint8Array(B.length);
  const sites = [];
  for (let t = 0; t < teams; t++) {
    let best = -1, bestGain = 0;
    for (let i = 0; i < B.length; i++) {
      let g = 0;
      for (const j of NB[i]) if (!covered[j]) g += B[j].w;
      if (g > bestGain) { bestGain = g; best = i; }
    }
    if (best < 0) break;
    const sel = NB[best].filter((j) => !covered[j]);
    sel.forEach((j) => { covered[j] = 1; });
    const a = B[best];
    sites.push({ anchor: `Blk ${a.blk_no} ${title(a.street)}`, anchorIdx: best, planning_area: a.planning_area, area: title(a.planning_area),
      lat: a.lat, lon: a.lon, idx: sel, blocks: sel.map((j) => B[j]), homes: sel.reduce((s, j) => s + B[j].h, 0),
      nearCluster: sel.filter((j) => yes(B[j].near_cluster)).length });
  }
  return sites;
}

// --- time estimates: planning assumptions, not NEA data
const A = {
  start: "09:00",       // start time
  shiftHours: 8,        // shift length (hours)
  breakMin: 60,         // breaks per shift (minutes)
  officers: 3,          // officers per team
  travelMin: 30,        // travel to the site (minutes)
  walkKmh: 4,           // walking speed (km/h)
  routeFactor: 1.3,     // street detour (× straight line)
  minPerBlock: 8,       // common areas per block (minutes)
  minPerFloor: 1,       // per floor, corridor check (minutes)
  homeShare: 10,        // homes visited (% of homes)
  minPerHome: 4,        // per home visit (minutes)
  floorsIfUnknown: 12,  // floors when HDB's data has none
};

const hhmm = (mins) => { const m = Math.round(mins); return `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`; };

function estimate(site) {
  // walking order: nearest next block, starting at the anchor block
  const left = new Set(site.idx), order = [site.anchorIdx];
  left.delete(site.anchorIdx);
  while (left.size) {
    const p = B[order[order.length - 1]];
    let best = null, bd = Infinity;
    for (const j of left) { const d = (B[j].x - p.x) ** 2 + (B[j].y - p.y) ** 2; if (d < bd) { bd = d; best = j; } }
    order.push(best); left.delete(best);
  }
  const mPerMin = (A.walkKmh * 1000) / 60;
  const [sh, sm] = String(A.start || "09:00").split(":").map(Number);
  const start = sh * 60 + (sm || 0), shift = A.shiftHours * 60, capacity = shift - A.breakMin;
  let dayN = 1, used = A.travelMin, walk = 0, insp = 0, unknownFloors = 0;
  const stops = [];
  order.forEach((j, k) => {
    const b = B[j];
    const legMin = k === 0 ? 0 : Math.hypot(b.x - B[order[k - 1]].x, b.y - B[order[k - 1]].y) * A.routeFactor / mPerMin;
    const floors = b.floors || (unknownFloors++, A.floorsIfUnknown);
    const inspMin = A.minPerBlock + floors * A.minPerFloor + (b.h * A.homeShare / 100) * A.minPerHome / Math.max(1, A.officers);
    if (k > 0 && used + legMin + inspMin > capacity) { dayN += 1; used = A.travelMin; }  // carry on next shift
    used += k === 0 ? 0 : legMin;
    const arrive = start + used + (used > capacity / 2 ? A.breakMin : 0);
    stops.push({ n: k + 1, b, floors, day: dayN, time: hhmm(arrive), minutes: Math.round(inspMin) });
    used += inspMin; walk += legMin; insp += inspMin;
  });
  const lastDay = used + (used > capacity / 2 ? A.breakMin : 0);
  return { order, stops, shifts: dayN, walk, insp, travel: dayN * A.travelMin, breaks: dayN * A.breakMin,
    finish: hhmm(start + lastDay), total: walk + insp + dayN * (A.travelMin + A.breakMin), unknownFloors };
}

function showTeams() {
  $("lay-teams").checked = true;
  teamLayer.addTo(map);
}

function replan() {
  const teams = +$("teams").value;
  currentPlan = plan(teams);
  currentPlan.forEach((s) => { s.est = estimate(s); });
  const homes = currentPlan.reduce((s, x) => s + x.homes, 0), blocks = currentPlan.reduce((s, x) => s + x.blocks.length, 0);
  const multi = currentPlan.filter((s) => s.est.shifts > 1).length;
  $("plan-summary").innerHTML = `<div><b>${homes.toLocaleString()}</b>homes</div><div><b>${blocks}</b>HDB blocks</div>
    <div><b>${multi}/${currentPlan.length}</b>need 2+ shifts</div>`;
  const h = (m) => `${Math.floor(m / 60)} h ${String(Math.round(m % 60)).padStart(2, "0")} min`;
  $("sites").innerHTML = currentPlan.map((s, i) => {
    const e = s.est;
    const parts = `Travel ${Math.round(e.travel)} min · walking ${Math.round(e.walk)} min · inspection ${h(e.insp)} · breaks ${Math.round(e.breaks)} min · ${h(e.total)} in all` +
      (e.unknownFloors ? ` · floors assumed for ${e.unknownFloors} blocks` : "");
    return `<li data-i="${i}"><b>${esc(s.anchor)}</b>, ${esc(s.area)}
      <div class="m">${s.blocks.length} blocks · ${s.homes.toLocaleString()} homes · ${s.nearCluster} near a cluster</div>
      <div class="est">${e.shifts > 1 ? `<span class="warn-chip">Needs ${e.shifts} shifts</span> last shift ends ${e.finish}` : `Finishes about ${e.finish}`}</div>
      <div class="breakdown" title="${esc(parts)}">
        <span style="flex:${e.travel}" class="b-travel"></span><span style="flex:${e.walk}" class="b-walk"></span>
        <span style="flex:${e.insp}" class="b-insp"></span><span style="flex:${e.breaks}" class="b-break"></span></div>
      <div class="site-actions"><button class="link" data-act="route">Show route</button>
        <button class="link" data-act="draft">Draft alert for these blocks</button></div></li>`;
  }).join("");
  const wasOn = teamLayer && map.hasLayer(teamLayer);
  if (teamLayer) teamLayer.remove();
  exitRoute(true);
  teamLayer = L.layerGroup(currentPlan.flatMap((s, i) => [
    L.circle([s.lat, s.lon], { radius: RADIUS, color: css("--team"), weight: 2, dashArray: "4 3", fillColor: css("--team"), fillOpacity: 0.08, interactive: false }),
    L.marker([s.lat, s.lon], { icon: L.divIcon({ className: "team-pin", html: String(i + 1), iconSize: [24, 24], iconAnchor: [12, 12] }) })
      .on("mousemove", (e) => showTip(e.originalEvent, `<b>Team ${i + 1}: ${esc(s.anchor)}</b><br>${s.blocks.length} blocks · ${s.homes.toLocaleString()} homes`))
      .on("mouseout", hideTip),
  ]));
  if (wasOn || $("lay-teams").checked) teamLayer.addTo(map);
  $("sites").querySelectorAll("li").forEach((li) => li.addEventListener("click", (e) => {
    const s = currentPlan[+li.dataset.i];
    if (e.target.dataset.act === "draft") { openDraft(s.planning_area, s); return; }
    closePopup();
    showTeams();
    drawRoute(+li.dataset.i);
  }));
}

// A team's route: the map switches to OpenStreetMap streets so officers can see which blocks are meant;
// each stop is numbered in walking order and labelled with its block number.
function drawRoute(i) {
  const s = currentPlan[i];
  if (!s) return;
  if (routeLayer) routeLayer.remove();
  if (!streets) {
    streets = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' });
    const Exit = L.Control.extend({ onAdd() {
      const b = L.DomUtil.create("button", "route-exit");
      b.type = "button";
      L.DomEvent.disableClickPropagation(b);
      b.addEventListener("click", () => exitRoute());
      return b;
    } });
    routeCtl = new Exit({ position: "topright" });
  }
  if (!routeMode) {
    routeMode = true;
    streets.addTo(map).bringToBack();
    routeCtl.addTo(map);
    $("c-map").classList.add("streets");
    highlight(selected);
  }
  routeCtl.getContainer().innerHTML = `${ICON.cross}Team ${i + 1} · back to risk map`;
  const pts = s.est.order.map((j) => [B[j].lat, B[j].lon]);
  routeLayer = L.layerGroup([
    L.polyline(pts, { color: css("--team"), weight: 3, opacity: 0.85 }),
    ...s.est.stops.map((st) => L.marker([st.b.lat, st.b.lon], { icon: L.divIcon({ className: "stop-pin", html: String(st.n), iconSize: [20, 20], iconAnchor: [10, 10] }) })
      .bindTooltip(`Blk ${esc(st.b.blk_no)}`, { permanent: true, direction: "right", offset: [9, 0], className: "blk-tag" })
      .on("mousemove", (e) => showTip(e.originalEvent, `<b>${st.n}. Blk ${esc(st.b.blk_no)} ${esc(title(st.b.street))}</b><br>Shift ${st.day}, about ${st.time} · ${st.floors} floors · ${st.b.h} homes`))
      .on("mouseout", hideTip)),
  ]).addTo(map);
  mapTouched = true;
  map.fitBounds(L.latLngBounds(pts).pad(0.15), { maxZoom: 18 });
}

function exitRoute(keepView) {
  if (routeLayer) { routeLayer.remove(); routeLayer = null; }
  if (!routeMode) return;
  routeMode = false;
  streets.remove();
  routeCtl.remove();
  $("c-map").classList.remove("streets");
  highlight(selected);
  if (!keepView) { mapTouched = false; fitAll(); }
}

// ------------------------------------------------------------------ alerts
function alertAreas() {
  // who should hear from us this week: forecast High/Medium, clusters today, or top-5 new-cluster watch
  return D.areas.filter((a) => a.predicted_level !== "Low" || a.level_today > 0 || yes(a.new_cluster_watch));
}
const lastAlert = (pa) => ALERTS.find((x) => x.area === pa);
const sentThisWeek = (pa) => ALERTS.some((x) => x.area === pa && x.status === "sent" && Date.now() - new Date(x.created_at) < 7 * 864e5);

function loadAlerts() {
  fetch("/api/alerts").then((r) => (r.ok ? r.json() : [])).then((rows) => { ALERTS = rows; render(); })
    .catch(() => { render(); });
}

// the "+ Draft an alert" card: a + button, then the areas to alert; picking one opens the draft dialog
function alertCard() {
  const card = $("c-alert");
  card.classList.toggle("idle", !alertOpen);
  if (!alertOpen) {
    card.innerHTML = `<button class="add-alert" id="alert-add">${ICON.plus}Draft an alert</button>`;
    $("alert-add").addEventListener("click", () => { alertOpen = true; alertCard(); $("c-alert").querySelector("li")?.focus(); });
    return;
  }
  const state = (pa) => {
    const last = lastAlert(pa);
    if (!last) return ["none", "Not alerted"];
    const when = `${esc(day(last.created_at))}${last.created_by_name ? ` by ${esc(last.created_by_name)}` : ""}`;
    return last.status === "sent" ? ["sent", `Sent ${when}`] : ["draft", `Draft saved ${when}`];
  };
  card.innerHTML = `<header class="card-h"><h2>Draft an alert</h2><button class="x" id="alert-x" aria-label="Close">×</button></header>
    <div class="card-b"><ul class="pick">${alertAreas().map((a) => {
      const [cls, label] = state(a.planning_area);
      return `<li data-pa="${esc(a.planning_area)}" tabindex="0" role="button" aria-label="${esc(a.area)}, forecast ${esc(a.predicted_level)}: ${label}" title="${label}">
        <span class="lv ${esc(a.predicted_level)}" title="Forecast ${esc(a.predicted_level)}"></span><span class="nm">${esc(a.area)}</span><span class="s ${cls}"></span></li>`;
    }).join("")}</ul></div>`;
  $("alert-x").addEventListener("click", () => { alertOpen = false; alertCard(); $("alert-add").focus(); });
  card.querySelectorAll("li").forEach((li) => {
    li.addEventListener("click", () => openDraft(li.dataset.pa));
    li.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDraft(li.dataset.pa); } });
  });
}

let draftCtx = null;
function openDraft(pa, site) {
  const a = D.areas.find((x) => x.planning_area === pa);
  if (!a) return;
  const near = B.filter((b) => b.planning_area === pa && yes(b.near_cluster));
  const blocks = site ? site.blocks : near;
  draftCtx = { area: pa, site: site ? { anchor: site.anchor, blocks: site.blocks.map(({ blk_no, street, homes, weight }) => ({ blk_no, street, homes, weight })) } : null };
  $("draft-title").textContent = `Draft alert · ${a.area}`;
  $("draft-scope").textContent = site
    ? `Team site at ${site.anchor}: ${site.blocks.length} blocks, ${site.homes.toLocaleString()} homes.`
    : blocks.length ? `${blocks.length} blocks within 300 m of today's clusters, ${blocks.reduce((s, b) => s + b.h, 0).toLocaleString()} homes.`
      : "No cluster here today: the draft names the highest-priority blocks in the area.";
  $("d-text").value = "";
  $("d-status").textContent = "";
  $("d-warn").hidden = true;
  if (!$("draft-dialog").open) $("draft-dialog").showModal();
}

function setupDraftDialog() {
  $("d-go").addEventListener("click", async () => {
    if (!draftCtx) return;
    $("d-go").disabled = true;
    $("d-status").textContent = "Drafting with Llama 3.3 on Databricks…";
    $("d-warn").hidden = true;
    try {
      const r = await fetch("/api/draft", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...draftCtx, audience: $("d-audience").value, channel: $("d-channel").value, language: $("d-language").value }) });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      $("d-text").value = d.message;
      $("d-status").textContent = `Draft based on ${d.facts.number_of_blocks} blocks (${d.facts.homes_in_those_blocks.toLocaleString()} homes). Check it before sending.`;
      if (d.unverified_numbers.length) {
        $("d-warn").textContent = `These numbers aren't in this week's data; check or remove them: ${d.unverified_numbers.join(", ")}`;
        $("d-warn").hidden = false;
      }
    } catch (e) {
      $("d-status").textContent = `Could not draft (${e.message}). You can still write the message yourself.`;
    } finally {
      $("d-go").disabled = false;
    }
  });
  $("d-copy").addEventListener("click", () => { navigator.clipboard.writeText($("d-text").value); $("d-status").textContent = "Copied."; });
  $("d-email").addEventListener("click", () => {
    const t = $("d-text").value, m = t.match(/^Subject:\s*(.*)\n+/i);
    const subject = m ? m[1] : `Dengue alert: ${$("draft-title").textContent.replace("Draft alert · ", "")}`;
    window.location.href = `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(m ? t.slice(m[0].length) : t)}`;
  });
  const save = async (status) => {
    const text = $("d-text").value.trim();
    if (!text) { $("d-status").textContent = "Nothing to save yet."; return; }
    const blocks = draftCtx.site ? draftCtx.site.blocks : B.filter((b) => b.planning_area === draftCtx.area && yes(b.near_cluster));
    const r = await fetch("/api/alerts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      area: draftCtx.area, scope: draftCtx.site ? `site: ${draftCtx.site.anchor}` : "area", status, created_by_name: ME.name,
      audience: $("d-audience").value, channel: $("d-channel").value, language: $("d-language").value,
      homes: blocks.reduce((s, b) => s + (+b.homes), 0), blocks: blocks.length, message: text }) });
    if (!r.ok) { $("d-status").textContent = `Could not save (HTTP ${r.status}).`; return; }
    ALERTS.unshift(await r.json());
    render();
    $("d-status").textContent = status === "sent" ? "Logged as sent." : "Draft saved.";
    if (status === "sent") setTimeout(() => $("draft-dialog").close(), 600);
  };
  $("d-save").addEventListener("click", () => save("draft"));
  $("d-sent").addEventListener("click", () => save("sent"));
}

// ------------------------------------------------------------------ track record (charts only)
function ring(num, den, label, base) {
  const r = 26, c = 2 * Math.PI * r, f = den ? num / den : 0;
  return `<div class="ring"><svg viewBox="0 0 64 64" role="img" aria-label="${esc(label)}: ${den ? `${num} of ${den}` : "none yet"}">
    <circle class="trk" cx="32" cy="32" r="${r}" fill="none" stroke-width="7"/>
    <circle class="val${base ? " base" : ""}" cx="32" cy="32" r="${r}" fill="none" stroke-width="7" stroke-linecap="round"
      stroke-dasharray="${(f * c).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 32 32)"/>
    <text x="32" y="36.5" text-anchor="middle">${den ? `${num}/${den}` : "–"}</text></svg>${esc(label)}</div>`;
}

function record() {
  const r = D.retro_check || [];
  if (r.length) {
    const f = r[0], n = r.length, count = (k) => r.filter((x) => yes(x[k])).length;
    const calledHigh = r.filter((x) => x.predicted_level === "High");
    const newOnes = r.filter((x) => yes(x.new_cluster_appeared));
    const shown = r.filter((x) => x.level_then !== "Low" || x.predicted_level !== "Low" || x.level_now !== "Low" || (x.new_cluster_rank && x.new_cluster_rank <= 5))
      .sort((a, b) => LEVEL_NUM[b.predicted_level] - LEVEL_NUM[a.predicted_level] || LEVEL_NUM[b.level_now] - LEVEL_NUM[a.level_now] ||
        (a.new_cluster_rank ?? 99) - (b.new_cluster_rank ?? 99)).slice(0, 4);
    $("h-record").textContent = `${day(f.basis_date)} forecast vs ${day(f.compare_date)}`;
    $("record").innerHTML = `<div class="rec">
      <div class="rings">
        ${ring(count("model_right"), n, "areas right", false)}
        ${ring(count("same_as_now_right"), n, "same as now", true)}
        ${ring(calledHigh.filter((x) => x.level_now === "High").length, calledHigh.length, "High calls held", false)}
        ${ring(newOnes.filter((x) => x.new_cluster_rank && x.new_cluster_rank <= 5).length, newOnes.length, "new in top 5", false)}
      </div>
      <table class="mini"><thead><tr><th>Area</th><th>Forecast</th><th>${esc(day(f.compare_date))}</th><th></th></tr></thead>
      <tbody>${shown.map((x) => `<tr><td><b>${esc(x.area)}</b></td><td>${chip(x.predicted_level)}</td>
        <td>${chip(x.level_now)}</td><td class="r ${yes(x.model_right) ? "ok" : "miss"}" aria-label="${yes(x.model_right) ? "Right" : "Wrong"}">${yes(x.model_right) ? ICON.check : ICON.cross}</td></tr>`).join("")}</tbody></table>
    </div>`;
  }

  // locked forecasts (append-only): when each was made from, and when it can be scored
  const locks = [...(D.track_record || [])].sort((a, b) => (a.basis_date < b.basis_date ? -1 : 1));
  $("locks").innerHTML = locks.map((x) => x.status === "scored"
    ? `<span class="lock scored" title="Locked forecast from NEA's list of ${esc(day(x.basis_date))}, scored">${ICON.lock}${esc(day(x.basis_date))} · ${x.model_right}/${x.areas}</span>`
    : `<span class="lock" title="Locked forecast from NEA's list of ${esc(day(x.basis_date))}, scored from ${esc(day(x.score_from))}">${ICON.lock}${esc(day(x.basis_date))} ${ICON.arrow} ${esc(day(x.score_from))}</span>`).join("");
}

function weekly() {
  const box = $("weekly");
  const pts = (D.weekly_cases || []).map((r) => ({ d: new Date(`${r.week_end.slice(0, 10)}T00:00:00`), v: +r.cases })).sort((a, b) => a.d - b.d);
  const W = box.clientWidth - 16, H = box.clientHeight - 6;
  if (pts.length < 2 || W < 60 || H < 60) { box.innerHTML = ""; return; }
  const L0 = 36, R = 6, T = 8, Bm = 20;
  const x0 = pts[0].d, x1 = pts[pts.length - 1].d, ymax = Math.max(...pts.map((p) => p.v));
  const sx = (d) => L0 + (W - L0 - R) * (d - x0) / (x1 - x0), sy = (v) => T + (H - T - Bm) * (1 - v / ymax);
  const years = [];
  for (let y = x0.getFullYear() + 1; y <= x1.getFullYear(); y++) years.push(new Date(`${y}-01-01T00:00:00`));
  box.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Weekly dengue cases in Singapore">
    <line class="ax" x1="${L0}" x2="${W - R}" y1="${H - Bm}" y2="${H - Bm}"/>
    ${years.map((d) => `<line class="ax" x1="${sx(d)}" x2="${sx(d)}" y1="${T}" y2="${H - Bm}"/><text x="${sx(d) + 4}" y="${H - 5}">${d.getFullYear()}</text>`).join("")}
    <text x="2" y="${T + 9}">${ymax}</text><text x="2" y="${H - Bm}">0</text>
    <polyline class="ln" points="${pts.map((p) => `${sx(p.d).toFixed(1)},${sy(p.v).toFixed(1)}`).join(" ")}"/>
    <line class="hv" id="wk-hv" y1="${T}" y2="${H - Bm}" visibility="hidden"/>
    <rect x="${L0}" y="0" width="${W - L0 - R}" height="${H}" fill="transparent" id="wk-hit"/></svg>`;
  const hv = $("wk-hv");
  $("wk-hit").addEventListener("mousemove", (e) => {
    const x = e.clientX - box.querySelector("svg").getBoundingClientRect().left;
    const t = x0.getTime() + (x - L0) / (W - L0 - R) * (x1 - x0);
    const p = pts.reduce((b, q) => (Math.abs(q.d - t) < Math.abs(b.d - t) ? q : b));
    hv.setAttribute("x1", sx(p.d)); hv.setAttribute("x2", sx(p.d)); hv.setAttribute("visibility", "visible");
    showTip(e, `Week ending ${p.d.toLocaleDateString("en-SG", { day: "numeric", month: "short", year: "numeric" })}<br><b>${p.v} cases</b>`);
  });
  $("wk-hit").addEventListener("mouseleave", () => { hv.setAttribute("visibility", "hidden"); hideTip(); });
}

// ------------------------------------------------------------------ enlarge a card into a pop-up
// The card itself moves into the dialog and back, so whatever it holds (a chat, an open area) stays as it was.
const closePopup = () => { if ($("popup").open) $("popup").close(); };
function setupPopup() {
  const dlg = $("popup");
  let card = null, holder = null;
  const icon = (c, big) => {
    const b = c.querySelector("[data-expand]");
    b.innerHTML = big ? ICON.shrink : ICON.expand;
    b.setAttribute("aria-label", big ? "Back to the board" : "Enlarge");
    b.title = big ? "Back to the board" : "Enlarge";
  };
  // the Ask card's header is built by chat.js
  const askHead = document.querySelector("#c-ask .ask-head");
  if (askHead) askHead.insertAdjacentHTML("beforeend", '<button class="ask-icon" data-expand></button>');
  document.querySelectorAll("[data-expand]").forEach((b) => {
    icon(b.closest(".card"), false);
    b.addEventListener("click", () => {
      const c = b.closest(".card");
      if (card === c) { dlg.close(); return; }
      card = c;
      holder = document.createElement("div");
      holder.className = "card holder";
      holder.style.flexGrow = c.style.flexGrow;
      holder.style.order = getComputedStyle(c).order;
      c.replaceWith(holder);
      dlg.append(c);
      icon(c, true);
      dlg.showModal();
    });
  });
  dlg.addEventListener("close", () => {
    if (!card) return;
    holder.replaceWith(card);
    icon(card, false);
    card = null;
  });
}

// ------------------------------------------------------------------ resizable cards
// Every .split box lays its children out with flex-grow weights (data-sizes). A gutter between two
// neighbours moves the boundary between them only; the weights are kept in this browser.
function setupSplits() {
  document.querySelectorAll(".split").forEach((box) => {
    const kids = [...box.children];
    const horizontal = box.dataset.dir === "row";
    const defaults = box.dataset.sizes.split(",").map(Number);
    const key = `dengueradar.split.${box.id}`;
    let sizes = defaults;
    try {
      const saved = JSON.parse(store.get(key) || "null");
      if (Array.isArray(saved) && saved.length === kids.length && saved.every((v) => v > 0)) sizes = saved;
    } catch { /* keep the defaults */ }
    const apply = () => kids.forEach((k, i) => { k.style.flexGrow = sizes[i]; });
    const save = () => store.set(key, JSON.stringify(sizes.map((v) => +v.toFixed(2))));
    const px = (el) => (horizontal ? el.getBoundingClientRect().width : el.getBoundingClientRect().height);
    const minOf = (el) => +el.dataset.min || (horizontal ? 220 : 110);
    apply();

    kids.slice(1).forEach((next, i) => {
      const prev = kids[i];
      const g = document.createElement("div");
      g.className = "gutter";
      g.tabIndex = 0;
      g.setAttribute("role", "separator");
      g.setAttribute("aria-orientation", horizontal ? "vertical" : "horizontal");
      g.setAttribute("aria-label", "Resize cards (arrow keys; double-click to reset)");
      box.insertBefore(g, next);

      let start = null;
      const begin = (pos) => { start = { pos, a: px(prev), b: px(next), w: sizes[i] + sizes[i + 1] }; };
      const move = (pos) => {
        const total = start.a + start.b;
        const a = Math.min(Math.max(start.a + pos - start.pos, minOf(prev)), total - minOf(next));
        sizes[i] = (start.w * a) / total;
        sizes[i + 1] = start.w - sizes[i];
        apply();
      };
      g.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        g.setPointerCapture(e.pointerId);
        begin(horizontal ? e.clientX : e.clientY);
        g.classList.add("drag");
        document.body.classList.add("resizing", horizontal ? "row-resize" : "col-resize");
      });
      g.addEventListener("pointermove", (e) => { if (start) move(horizontal ? e.clientX : e.clientY); });
      const end = () => {
        if (!start) return;
        start = null; save();
        g.classList.remove("drag");
        document.body.classList.remove("resizing", "row-resize", "col-resize");
      };
      g.addEventListener("pointerup", end);
      g.addEventListener("pointercancel", end);
      g.addEventListener("keydown", (e) => {
        const step = { ArrowLeft: -24, ArrowUp: -24, ArrowRight: 24, ArrowDown: 24 }[e.key];
        if (!step) return;
        e.preventDefault();
        begin(0); move(step); start = null; save();
      });
      g.addEventListener("dblclick", () => { sizes = [...defaults]; apply(); save(); });
    });
  });
}
