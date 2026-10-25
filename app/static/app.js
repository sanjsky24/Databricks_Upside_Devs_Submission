// DengueRadar Monday screen. Data comes from /api/data (Lakebase); alerts are drafted by /api/draft (LLM on
// Databricks) and logged with /api/alerts. The team planner runs here so officers can try any number of teams.

const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const title = (s) => String(s || "").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
const pct = (x) => (x == null ? "–" : `${Math.round(100 * x)}%`);
const yes = (v) => v === true || v === "true";
const LEVEL_NUM = { High: 2, Medium: 1, Low: 0 };
const RADIUS = 300; // metres covered by one team, as in notebooks/04_team_plan.py

let D, map, areaLayer, labelLayer, teamLayer, currentPlan;
let ALERTS = [];
const overlays = {};

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
fetch("/api/data")
  .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
  .then((d) => { D = d; init(); })
  .catch((e) => { $("basis").textContent = `Could not load data (${e.message}).`; });

function init() {
  const m = D.meta || {};
  $("today").textContent = new Date().toLocaleDateString("en-SG", { weekday: "long", day: "numeric", month: "short" });
  $("basis").textContent = `Forecast from NEA's list of ${day(m.basis_date)} · clusters as at ${day(m.cluster_list_date)}`;
  D.areas.sort((a, b) => b.predicted_level_num - a.predicted_level_num || b.p_high - a.p_high ||
    (a.new_cluster_rank ?? 99) - (b.new_cluster_rank ?? 99));
  prepBlocks();
  drawMap();
  watchList();
  riskTable();
  retro();
  track();
  tests();
  const teams = $("teams");
  teams.addEventListener("input", () => { $("teams-out").textContent = teams.value; replan(); });
  replan();
  kpis();
  selectArea(D.areas[0].planning_area, false);
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => tab(b.dataset.tab)));
  window.addEventListener("hashchange", route);
  route();
  setupDraftDialog();
  loadAlerts();
}

// ------------------------------------------------------------------ views and tabs
function route() {
  const h = location.hash.slice(1);
  const view = h === "record" ? "record" : "week";
  document.querySelectorAll("[data-section]").forEach((s) => { s.hidden = s.dataset.section !== view; });
  document.querySelectorAll(".views a").forEach((a) => a.classList.toggle("on", a.dataset.view === view));
  if (view === "week") { setTimeout(() => map.invalidateSize(), 0); if (["why", "alerts", "watch", "plan"].includes(h)) tab(h); }
  if (view === "record" && !$("weekly").innerHTML) weekly();  // draw once visible, so it gets its real width
  if (h.startsWith("draft=")) openDraft(decodeURIComponent(h.slice(6)));  // e.g. #draft=JURONG%20WEST
}

function tab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  ["why", "alerts", "watch", "plan"].forEach((t) => { $(`tab-${t}`).hidden = t !== name; });
  if (teamLayer) (name === "plan" || $("lay-teams").checked ? teamLayer.addTo(map) : teamLayer.remove());
}

// ------------------------------------------------------------------ KPIs
function kpis() {
  const n = (lvl) => D.areas.filter((a) => a.predicted_level === lvl).length;
  const cases = D.clusters.reduce((s, c) => s + (c.total || 0), 0);
  const nearHomes = B.filter((b) => yes(b.near_cluster)).reduce((s, b) => s + b.h, 0);
  const weekAgo = Date.now() - 7 * 864e5;
  const sent = ALERTS.filter((a) => a.status === "sent" && new Date(a.created_at) >= weekAgo).length;
  const toAlert = alertAreas().length;
  const tiles = [
    ["high", n("High"), "areas forecast High", "a cluster with 10+ cases, in two weeks"],
    ["", n("Medium"), "areas forecast Medium", "any active cluster, in two weeks"],
    ["", D.clusters.length, "clusters on NEA's list", `${cases} cases in ${new Set(D.clusters.map((c) => c.planning_area)).size} areas`],
    ["", nearHomes.toLocaleString(), "homes within 300 m of a cluster", "the residents most worth alerting"],
    ["", `${sent}/${toAlert}`, "areas alerted this week", "alerts marked sent in the last 7 days"],
  ];
  $("kpis").innerHTML = tiles.map(([cls, v, l, s]) =>
    `<div class="kpi ${cls}"><div class="v">${esc(v)}</div><div class="l">${esc(l)}</div><div class="s">${esc(s)}</div></div>`).join("");
}

// ------------------------------------------------------------------ map
function areaStyle(a) {
  const fill = { High: css("--high"), Medium: css("--medium"), Low: css("--low") }[a.predicted_level];
  const watch = yes(a.new_cluster_watch);
  return {
    fillColor: fill, fillOpacity: a.predicted_level === "Low" ? 1 : 0.88,
    color: watch ? css("--accent") : css("--land-line"), weight: watch ? 2.2 : 1, dashArray: watch ? "5 4" : null,
  };
}

function drawMap() {
  map = L.map("map", { zoomSnap: 0.25, attributionControl: false, scrollWheelZoom: false });
  areaLayer = L.geoJSON({ type: "FeatureCollection", features: D.areas.filter((a) => a.geometry).map((a) => ({ type: "Feature", geometry: a.geometry, properties: a })) }, {
    style: (f) => areaStyle(f.properties),
    onEachFeature: (f, layer) => {
      const a = f.properties;
      layer.on("click", () => selectArea(a.planning_area, true));
      layer.on("mousemove", (e) => showTip(e.originalEvent,
        `<b>${esc(a.area)}</b><br>Today ${esc(a.level_today_name)} → ${esc(a.predicted_level)} in 2 weeks<br>Chance of High ${pct(a.p_high)}` +
        (a.new_cluster_rank && a.level_today === 0 ? `<br>New-cluster rank ${a.new_cluster_rank}` : "")));
      layer.on("mouseout", hideTip);
    },
  }).addTo(map);
  // fit to the main island; the outlying islands would leave the map mostly sea
  const mainland = L.latLngBounds([]);
  areaLayer.eachLayer((l) => { if (!/ISLANDS$/.test(l.feature.properties.planning_area)) mainland.extend(l.getBounds()); });
  const fit = () => { map.invalidateSize(); map.fitBounds(mainland, { padding: [6, 6] }); };
  fit();
  requestAnimationFrame(fit);
  window.addEventListener("resize", fit);

  overlays.wolbachia = L.geoJSON({ type: "FeatureCollection", features: D.wolbachia_zones.filter((z) => z.geometry).map((z) => ({ type: "Feature", geometry: z.geometry, properties: z })) }, {
    style: { color: css("--wol"), weight: 1.2, fillColor: css("--wol"), fillOpacity: 0.3 }, interactive: false,
  });
  overlays.aedes = L.layerGroup(D.high_aedes.map((p) => L.circleMarker([p.lat, p.lon], {
    radius: 3, stroke: false, fillColor: css("--aedes"), fillOpacity: 0.95, interactive: false })));
  overlays.clusters = L.layerGroup(D.clusters.map((c) => {
    const mk = L.circleMarker([c.lat, c.lon], { radius: 4 + 2 * Math.sqrt(c.total || 1), color: css("--surface"), weight: 2,
      fillColor: css("--cluster"), fillOpacity: 0.85 });
    mk.on("mousemove", (e) => showTip(e.originalEvent, `<b>${esc(c.locality)}</b><br>${c.total} cases` +
      (c.recent != null ? ` · ${c.recent} in the last 2 weeks` : "")));
    mk.on("mouseout", hideTip);
    return mk;
  }));
  const named = (a) => a.predicted_level !== "Low" || a.level_today > 0;
  labelLayer = L.layerGroup(D.areas.filter((a) => named(a) || yes(a.new_cluster_watch)).map((a) =>
    L.marker([a.lat, a.lon], { interactive: false, icon: L.divIcon({ className: "area-label", iconSize: null,
      html: named(a)
        ? `<span style="display:inline-block;transform:translate(-50%,-50%);white-space:nowrap">${esc(a.area)}</span>`
        : `<span class="rank-pin" title="${esc(a.area)}">${a.new_cluster_rank}</span>` }) })));

  const toggles = [
    ["wolbachia", "Wolbachia areas", true], ["aedes", "High-mosquito spots", true], ["clusters", "Dengue clusters today", true],
    ["teams", "Team sites", false], ["labels", "Area names", true],
  ];
  $("layers").innerHTML = toggles.map(([k, l, on]) => `<label><input type="checkbox" id="lay-${k}"${on ? " checked" : ""}> ${esc(l)}</label>`).join("");
  const layerOf = (k) => (k === "labels" ? labelLayer : k === "teams" ? teamLayer : overlays[k]);
  toggles.forEach(([k, , on]) => {
    if (on && layerOf(k)) layerOf(k).addTo(map);
    $(`lay-${k}`).addEventListener("change", (e) => { const l = layerOf(k); if (l) (e.target.checked ? l.addTo(map) : l.remove()); });
  });

  $("legend").innerHTML = [
    `<span><i class="sw" style="background:${css("--high")}"></i>High</span>`,
    `<span><i class="sw" style="background:${css("--medium")}"></i>Medium</span>`,
    `<span><i class="sw" style="background:${css("--low")}"></i>Low</span>`,
    `<span><i class="sw" style="border:2px dashed ${css("--accent")};background:none"></i>New-cluster watch (rank)</span>`,
    `<span><i class="sw" style="background:${css("--wol")};opacity:.6"></i>Wolbachia areas</span>`,
    `<span><i class="sw dot" style="background:${css("--aedes")}"></i>High-mosquito spots</span>`,
    `<span><i class="sw dot" style="background:${css("--cluster")}"></i>Dengue clusters (bigger = more cases)</span>`,
    `<span><i class="sw dot" style="background:${css("--team")}"></i>Team sites (300 m)</span>`,
  ].join("");
}

// ------------------------------------------------------------------ Why? panel
function selectArea(pa, switchTab) {
  const a = D.areas.find((x) => x.planning_area === pa);
  if (!a) return;
  areaLayer.eachLayer((l) => {
    const p = l.feature.properties;
    l.setStyle(p.planning_area === pa ? { ...areaStyle(p), color: css("--ink"), weight: 3, dashArray: null } : areaStyle(p));
    if (p.planning_area === pa) l.bringToFront();
  });
  const reasons = (a.why || "").split(" · ").filter(Boolean);
  const last = lastAlert(pa);
  $("tab-why").innerHTML = `
    <p class="why-area">${esc(a.area)}</p>
    <div class="why-levels">Today ${chip(a.level_today_name)} → in 2 weeks ${chip(a.predicted_level)} <span>${esc(a.direction)}</span></div>
    <div class="sub">Chance of High in two weeks</div>
    <div class="meter"><i style="width:${Math.round(100 * (a.p_high || 0))}%"></i></div>
    <div style="font-weight:700;margin-bottom:12px">${pct(a.p_high)}</div>
    <div class="sub" style="margin-bottom:4px">What the model sees here</div>
    <ul class="why-list">${reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
    <div class="facts">
      <div><b>${a.n_clusters}</b>clusters today</div>
      <div><b>${a.recent_cases}</b>cases, last 2 weeks</div>
      <div><b>${a.recent_next_door}</b>recent cases next door</div>
      <div><b>${pct(a.wolbachia_share)}</b>homes in Wolbachia zones</div>
      ${a.new_cluster_rank && a.level_today === 0 ? `<div><b>#${a.new_cluster_rank}</b>new-cluster rank (${pct(a.new_cluster_score)})</div>` : ""}
    </div>
    <div class="why-alert">
      <button class="primary" data-draft="${esc(pa)}">Draft alert for ${esc(a.area)}</button>
      <span class="sub">${last ? `Last alert: ${esc(last.status)} ${esc(day(last.created_at))}` : "No alert yet"}</span>
    </div>`;
  $("tab-why").querySelector("[data-draft]").addEventListener("click", () => openDraft(pa));
  if (switchTab) tab("why");
}

// ------------------------------------------------------------------ new-cluster watch
function watchList() {
  const w = D.areas.filter((a) => yes(a.new_cluster_watch)).sort((a, b) => a.new_cluster_rank - b.new_cluster_rank);
  const h = (D.test_headline || []).find((r) => r.baseline_name === "Past clusters here");
  $("tab-watch").innerHTML = `<p class="sub" style="margin-bottom:6px">Areas with no cluster today, most likely to see a new one within two weeks.
      ${h ? `In tests on 2020–26, a new cluster started in one of these five areas ${pct(h.model)} of the time.` : ""}</p>
    <ol class="watch">${w.map((a) => `<li data-pa="${esc(a.planning_area)}"><span class="rk">${a.new_cluster_rank}</span>
      <span class="nm">${esc(a.area)}</span><span class="pc">${pct(a.new_cluster_score)}</span>
      <span class="rs">${esc(a.why)}</span></li>`).join("")}</ol>`;
  $("tab-watch").querySelectorAll("li").forEach((li) => li.addEventListener("click", () => selectArea(li.dataset.pa, true)));
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
    sites.push({ anchor: `Blk ${a.blk_no} ${title(a.street)}`, planning_area: a.planning_area, area: title(a.planning_area),
      lat: a.lat, lon: a.lon, blocks: sel.map((j) => B[j]), homes: sel.reduce((s, j) => s + B[j].h, 0),
      nearCluster: sel.filter((j) => yes(B[j].near_cluster)).length });
  }
  return sites;
}

function replan() {
  const teams = +$("teams").value;
  const sites = currentPlan = plan(teams);
  const homes = sites.reduce((s, x) => s + x.homes, 0), blocks = sites.reduce((s, x) => s + x.blocks.length, 0);
  $("plan-summary").innerHTML = `<div><b>${homes.toLocaleString()}</b>homes to visit</div><div><b>${blocks}</b>HDB blocks</div>`;
  $("sites").innerHTML = sites.map((s, i) => `<li data-i="${i}"><b>${esc(s.anchor)}</b>, ${esc(s.area)}
    <div class="m">${s.blocks.length} blocks · ${s.homes.toLocaleString()} homes · ${s.nearCluster} near a cluster</div>
    <button class="link" data-site="${i}">Draft alert for these blocks</button></li>`).join("");
  const wasOn = teamLayer && map.hasLayer(teamLayer);
  if (teamLayer) teamLayer.remove();
  teamLayer = L.layerGroup(sites.flatMap((s, i) => [
    L.circle([s.lat, s.lon], { radius: RADIUS, color: css("--team"), weight: 2, dashArray: "4 3", fillColor: css("--team"), fillOpacity: 0.1, interactive: false }),
    L.marker([s.lat, s.lon], { icon: L.divIcon({ className: "team-pin", html: String(i + 1), iconSize: [24, 24], iconAnchor: [12, 12] }) })
      .on("mousemove", (e) => showTip(e.originalEvent, `<b>Team ${i + 1}: ${esc(s.anchor)}</b><br>${s.blocks.length} blocks · ${s.homes.toLocaleString()} homes`))
      .on("mouseout", hideTip),
  ]));
  if (wasOn || $("lay-teams").checked || !$("tab-plan").hidden) teamLayer.addTo(map);
  $("sites").querySelectorAll("li").forEach((li) => li.addEventListener("click", (e) => {
    const s = sites[+li.dataset.i];
    if (e.target.dataset.site != null) { openDraft(s.planning_area, s); return; }
    teamLayer.addTo(map);
    map.setView([s.lat, s.lon], 15);
  }));
}

// ------------------------------------------------------------------ alerts
function alertAreas() {
  // who should hear from us this week: forecast High/Medium, clusters today, or top-5 new-cluster watch
  return D.areas.filter((a) => a.predicted_level !== "Low" || a.level_today > 0 || yes(a.new_cluster_watch));
}
const lastAlert = (pa) => ALERTS.find((x) => x.area === pa);

function loadAlerts() {
  fetch("/api/alerts").then((r) => (r.ok ? r.json() : [])).then((rows) => { ALERTS = rows; alertsTab(); kpis(); })
    .catch(() => { alertsTab(); });
}

function alertsTab() {
  const why = (a) => (a.level_today > 0 ? `${a.n_clusters} cluster${a.n_clusters > 1 ? "s" : ""} today`
    : a.predicted_level !== "Low" ? `forecast ${a.predicted_level}` : `new-cluster watch #${a.new_cluster_rank}`);
  const homesNear = (pa) => B.filter((b) => b.planning_area === pa && yes(b.near_cluster)).reduce((s, b) => s + b.h, 0);
  $("tab-alerts").innerHTML = `<p class="sub" style="margin-bottom:6px">Areas to alert this week. Drafts use only this week's facts for the area.</p>
    <ul class="alert-list">${alertAreas().map((a) => {
      const last = lastAlert(a.planning_area), near = homesNear(a.planning_area);
      return `<li><div><b>${esc(a.area)}</b> ${chip(a.predicted_level)} <span class="sub">${esc(why(a))}${near ? ` · ${near.toLocaleString()} homes near clusters` : ""}</span></div>
        <div class="row"><span class="st ${last ? esc(last.status) : "none"}">${last ? `${last.status === "sent" ? "Sent" : "Draft saved"} ${esc(day(last.created_at))}` : "Not alerted"}</span>
        <button class="primary small" data-draft="${esc(a.planning_area)}">${last ? "New alert" : "Draft alert"}</button></div></li>`;
    }).join("")}</ul>
    <h3 class="log-h">Alert log</h3>
    ${ALERTS.length ? `<ul class="alert-log">${ALERTS.slice(0, 15).map((x) => `<li><div><b>${esc(title(x.area))}</b> · ${esc(x.status)} · ${esc(day(x.created_at))}
      <span class="sub">${esc(x.audience)} · ${esc(x.channel)} · ${esc(x.language)}${x.created_by ? ` · ${esc(x.created_by)}` : ""}</span></div>
      <details><summary>Message</summary><pre>${esc(x.message)}</pre></details></li>`).join("")}</ul>` : `<p class="sub">No alerts yet.</p>`}`;
  $("tab-alerts").querySelectorAll("[data-draft]").forEach((b) => b.addEventListener("click", () => openDraft(b.dataset.draft)));
}

let draftCtx = null;
function openDraft(pa, site) {
  const a = D.areas.find((x) => x.planning_area === pa);
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
  $("draft-dialog").showModal();
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
      area: draftCtx.area, scope: draftCtx.site ? `site: ${draftCtx.site.anchor}` : "area", status,
      audience: $("d-audience").value, channel: $("d-channel").value, language: $("d-language").value,
      homes: blocks.reduce((s, b) => s + (+b.homes), 0), blocks: blocks.length, message: text }) });
    if (!r.ok) { $("d-status").textContent = `Could not save (HTTP ${r.status}).`; return; }
    ALERTS.unshift(await r.json());
    alertsTab(); kpis(); selectArea(draftCtx.area, false);
    $("d-status").textContent = status === "sent" ? "Logged as sent." : "Draft saved.";
    if (status === "sent") setTimeout(() => $("draft-dialog").close(), 600);
  };
  $("d-save").addEventListener("click", () => save("draft"));
  $("d-sent").addEventListener("click", () => save("sent"));
}

// ------------------------------------------------------------------ risk table
function riskTable(all = false) {
  const rows = all ? D.areas : D.areas.slice(0, 12);
  $("risk").innerHTML = `<thead><tr><th>Area</th><th>Today</th><th>In 2 weeks</th><th>Change</th><th>Chance of High</th>
    <th>New-cluster rank</th><th>Why? (what the model sees)</th></tr></thead><tbody>${rows.map((a) => `
    <tr data-pa="${esc(a.planning_area)}"><td><b>${esc(a.area)}</b></td><td>${chip(a.level_today_name)}</td>
    <td>${chip(a.predicted_level)}</td><td>${esc(a.direction)}</td><td class="num">${pct(a.p_high)}</td>
    <td class="num">${a.level_today === 0 ? a.new_cluster_rank ?? "–" : "–"}</td><td class="why">${esc(a.why)}</td></tr>`).join("")}</tbody>`;
  $("risk-more").hidden = all;
  $("risk-more").textContent = `Show all ${D.areas.length} areas`;
  $("risk-more").onclick = () => riskTable(true);
  $("risk").querySelectorAll("tbody tr").forEach((tr) => tr.addEventListener("click", () => {
    selectArea(tr.dataset.pa, true);
    $("map").scrollIntoView({ behavior: "smooth", block: "center" });
  }));
}

// ------------------------------------------------------------------ track record
function tests() {
  const h = D.test_headline || [];
  const years = D.test_by_year || [];
  if (!h.length) { $("tests").innerHTML = `<p class="sub">No test results yet.</p>`; return; }
  const nc = h.find((r) => r.baseline_name === "Past clusters here");
  const chance = h.find((r) => r.baseline_name.startsWith("Chance"));
  const hi = h.find((r) => r.question.startsWith("High"));
  const newTotal = years.reduce((s, y) => s + (+y.new_clusters || 0), 0);
  const bar = (label, v, model) => `<span>${esc(label)}</span><span class="bar${model ? " model" : ""}" style="width:${Math.max(2, 100 * v)}%"></span><span class="n">${pct(v)}</span>`;
  $("tests").innerHTML = `
    <div class="test"><div class="q">Where new clusters start</div>
      <p>Of ${newTotal} new clusters, <b>${Math.round(nc.model * newTotal)}</b> started in an area on that week's top-5 watch list (${pct(nc.model)}).</p>
      <div class="bars">${bar("DengueRadar watch list", nc.model, true)}${bar("Areas with the most past clusters", nc.baseline, false)}
        ${chance ? bar("5 areas picked at random", chance.baseline, false) : ""}</div></div>
    <div class="test"><div class="q">Which areas will be High</div>
      <p>As accurate as assuming today's levels stay the same for two weeks (score ${(+hi.model).toFixed(2)} vs ${(+hi.baseline).toFixed(2)}),
        which is hard to beat because clusters stay open for at least two weeks. The forecast adds a chance for every area.</p></div>`;
}

function retro() {
  const r = D.retro_check || [];
  if (!r.length) { $("retro").innerHTML = `<p class="sub">No retrospective forecast yet.</p>`; return; }
  const f = r[0], n = r.length, final = yes(f.final);
  const count = (k) => r.filter((x) => yes(x[k])).length;
  $("retro-title").textContent = `How the forecast from NEA's ${day(f.basis_date)} list is doing`;
  $("retro-sub").textContent = (final
    ? `Two-week check against NEA's list of ${day(f.compare_date)} (${f.days_later} days later).`
    : `So far: NEA's list of ${day(f.compare_date)}, ${f.days_later} days later. The two-week check uses NEA's first list from ${day(f.final_check_from)}.`) +
    ` Made afterwards using only data known on ${day(f.basis_date)}, so it is not one of the locked forecasts.`;
  const calledHigh = r.filter((x) => x.predicted_level === "High");
  const stillHigh = calledHigh.filter((x) => x.level_now === "High");
  const newOnes = r.filter((x) => yes(x.new_cluster_appeared));
  const inTop5 = newOnes.filter((x) => x.new_cluster_rank && x.new_cluster_rank <= 5);
  const shown = r.filter((x) => x.level_then !== "Low" || x.predicted_level !== "Low" || x.level_now !== "Low" || (x.new_cluster_rank && x.new_cluster_rank <= 5))
    .sort((a, b) => LEVEL_NUM[b.predicted_level] - LEVEL_NUM[a.predicted_level] || LEVEL_NUM[b.level_now] - LEVEL_NUM[a.level_now] ||
      (a.new_cluster_rank ?? 99) - (b.new_cluster_rank ?? 99));
  const mark = (ok) => `<span class="${ok ? "ok" : "miss"}">${ok ? "✓ Right" : "✗ Wrong"}</span>`;
  $("retro").innerHTML = `
    <div class="retro-stats">
      <div><b>${count("model_right")}/${n}</b>areas right<span>"same as now": ${count("same_as_now_right")}/${n}</span></div>
      <div><b>${stillHigh.length}/${calledHigh.length}</b>called High, High now<span>${esc(calledHigh.map((x) => x.area).join(", ") || "none")}</span></div>
      <div><b>${inTop5.length}/${newOnes.length}</b>new clusters in its top 5<span>${esc(newOnes.map((x) => x.area).join(", ") || "no new clusters yet")}</span></div>
    </div>
    <div class="table-wrap"><table class="risk retro-table"><thead><tr><th>Area</th><th>${esc(day(f.basis_date))}</th>
      <th>Forecast</th><th>${esc(day(f.compare_date))}</th><th></th></tr></thead><tbody>
      ${shown.map((x) => `<tr><td><b>${esc(x.area)}</b>${x.new_cluster_rank && x.new_cluster_rank <= 5 ? ` <span class="rank-inline">#${x.new_cluster_rank}</span>` : ""}</td>
        <td>${chip(x.level_then)}</td><td>${chip(x.predicted_level)}</td><td>${chip(x.level_now)}</td>
        <td>${mark(yes(x.model_right))}</td></tr>`).join("")}</tbody></table></div>
    <p class="note">Every other area: forecast Low, Low on ${esc(day(f.compare_date))}.</p>`;
}

function track() {
  const rows = [...D.track_record].sort((a, b) => (a.basis_date < b.basis_date ? -1 : 1));
  if (!rows.length) { $("track").innerHTML = ""; return; }
  $("track").innerHTML = `<p class="note"><b>Locked forecasts</b> (append-only, written before the outcome): ` +
    rows.map((r) => r.status === "scored"
      ? `NEA list ${day(r.basis_date)}: right in ${r.model_right}/${r.areas} areas vs ${r.same_as_now_right} for "same as now"`
      : `NEA list ${day(r.basis_date)}, scored from ${day(r.score_from)}`).map(esc).join(" · ") + `.</p>`;
}

function weekly() {
  const pts = D.weekly_cases.map((r) => ({ d: new Date(`${r.week_end.slice(0, 10)}T00:00:00`), v: +r.cases })).sort((a, b) => a.d - b.d);
  if (!pts.length) return;
  const W = 900, H = 220, L0 = 40, R = 8, T = 10, Bm = 22;
  const x0 = pts[0].d, x1 = pts[pts.length - 1].d, ymax = Math.max(...pts.map((p) => p.v));
  const sx = (d) => L0 + (W - L0 - R) * (d - x0) / (x1 - x0), sy = (v) => T + (H - T - Bm) * (1 - v / ymax);
  const years = [];
  for (let y = x0.getFullYear() + 1; y <= x1.getFullYear(); y++) years.push(new Date(`${y}-01-01T00:00:00`));
  $("weekly").innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Weekly dengue cases">
    <line class="ax" x1="${L0}" x2="${W - R}" y1="${H - Bm}" y2="${H - Bm}"/>
    ${years.map((d) => `<line class="ax" x1="${sx(d)}" x2="${sx(d)}" y1="${T}" y2="${H - Bm}"/><text x="${sx(d) + 4}" y="${H - 6}">${d.getFullYear()}</text>`).join("")}
    <text x="2" y="${T + 9}">${ymax}</text><text x="2" y="${H - Bm}">0</text>
    <polyline class="ln" points="${pts.map((p) => `${sx(p.d).toFixed(1)},${sy(p.v).toFixed(1)}`).join(" ")}"/>
    <line class="hv" id="wk-hv" y1="${T}" y2="${H - Bm}" visibility="hidden"/>
    <rect x="${L0}" y="0" width="${W - L0 - R}" height="${H}" fill="transparent" id="wk-hit"/></svg>`;
  const svg = $("weekly").querySelector("svg"), hv = $("wk-hv");
  $("wk-hit").addEventListener("mousemove", (e) => {
    const r = svg.getBoundingClientRect(), x = (e.clientX - r.left) * W / r.width;
    const t = x0.getTime() + (x - L0) / (W - L0 - R) * (x1 - x0);
    const p = pts.reduce((b, q) => (Math.abs(q.d - t) < Math.abs(b.d - t) ? q : b));
    hv.setAttribute("x1", sx(p.d)); hv.setAttribute("x2", sx(p.d)); hv.setAttribute("visibility", "visible");
    showTip(e, `Week ending ${p.d.toLocaleDateString("en-SG", { day: "numeric", month: "short", year: "numeric" })}<br><b>${p.v} cases</b>`);
  });
  $("wk-hit").addEventListener("mouseleave", () => { hv.setAttribute("visibility", "hidden"); hideTip(); });
}
