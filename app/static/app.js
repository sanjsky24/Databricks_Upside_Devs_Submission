// DengueRadar Monday screen. All data comes from /api/data (Lakebase); nothing is computed on the
// server except reading tables. The team planner runs here so officers can try any number of teams.

const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const title = (s) => String(s || "").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
const pct = (x) => (x == null ? "–" : `${Math.round(100 * x)}%`);
const LEVEL_NUM = { High: 2, Medium: 1, Low: 0 };
const LEVEL_NAME = ["Low", "Medium", "High"];
const RADIUS = 300; // metres covered by one team, as in notebooks/04_team_plan.py

let D, map, areaLayer, labelLayer, teamLayer, selected;
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
  const x = Math.min(ev.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
  tip.style.left = `${x}px`;
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
  $("basis").textContent = `Forecast from NEA's list of ${day(m.basis_date)} · locked ${day(m.locked_on)} · ` +
    `scored from ${day(m.score_from)} · clusters on the map as at ${day(m.cluster_list_date)}`;
  D.areas.sort((a, b) => b.predicted_level_num - a.predicted_level_num || b.p_high - a.p_high ||
    (a.new_cluster_rank ?? 99) - (b.new_cluster_rank ?? 99));
  prepBlocks();
  kpis();
  drawMap();
  watchList();
  riskTable();
  track();
  tests();
  weekly();
  const plannerInput = $("teams");
  plannerInput.addEventListener("input", () => { $("teams-out").textContent = plannerInput.value; replan(); });
  replan();
  selectArea(D.areas[0].planning_area, false);
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => tab(b.dataset.tab)));
  const start = location.hash.slice(1);  // e.g. /#plan opens the team planner
  if (["why", "watch", "plan"].includes(start)) tab(start);
}

function tab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  ["why", "watch", "plan"].forEach((t) => { $(`tab-${t}`).hidden = t !== name; });
  if (teamLayer) (name === "plan" || $("lay-teams").checked ? teamLayer.addTo(map) : teamLayer.remove());
}

// ------------------------------------------------------------------ KPIs
function kpis() {
  const n = (lvl) => D.areas.filter((a) => a.predicted_level === lvl).length;
  const today = D.areas.filter((a) => a.level_today > 0).length;
  const cases = D.clusters.reduce((s, c) => s + (c.total || 0), 0);
  const tiles = [
    ["high", n("High"), "areas forecast High", "a cluster with 10+ cases, in two weeks"],
    ["", n("Medium"), "areas forecast Medium", "any active cluster, in two weeks"],
    ["", D.clusters.length, "clusters on NEA's list", `${cases} cases · ${today} areas`],
    ["", D.areas.filter((a) => a.new_cluster_watch).length, "areas on new-cluster watch", "clear today, most likely to see a new cluster"],
    ["", "", "priority homes, 6 teams", "", "kpi-homes"],
  ];
  $("kpis").innerHTML = tiles.map(([cls, v, l, s, id]) =>
    `<div class="kpi ${cls}"${id ? ` id="${id}"` : ""}><div class="v">${esc(v)}</div><div class="l">${esc(l)}</div><div class="s">${esc(s)}</div></div>`).join("");
}

// ------------------------------------------------------------------ map
function areaStyle(a) {
  const fill = { High: css("--high"), Medium: css("--medium"), Low: css("--low") }[a.predicted_level];
  const watch = a.new_cluster_watch;
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
        `<b>${esc(a.area)}</b><br>Today ${esc(a.level_today_name)} → ${esc(a.predicted_level)} in 2 weeks<br>P(High) ${pct(a.p_high)}` +
        (a.new_cluster_rank ? `<br>New-cluster rank ${a.new_cluster_rank}` : "")));
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
  labelLayer = L.layerGroup(D.areas.filter((a) => named(a) || a.new_cluster_watch).map((a) =>
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
    `<span><i class="sw" style="border:2px dashed ${css("--accent")};background:none"></i>New-cluster watch (#rank)</span>`,
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
  selected = pa;
  areaLayer.eachLayer((l) => {
    const p = l.feature.properties;
    l.setStyle(p.planning_area === pa ? { ...areaStyle(p), color: css("--ink"), weight: 3, dashArray: null } : areaStyle(p));
    if (p.planning_area === pa) l.bringToFront();
  });
  const reasons = (a.why || "").split(" · ").filter(Boolean);
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
      ${a.new_cluster_rank ? `<div><b>#${a.new_cluster_rank}</b>new-cluster rank (${pct(a.new_cluster_score)})</div>` : ""}
    </div>`;
  if (switchTab) tab("why");
}

// ------------------------------------------------------------------ new-cluster watch
function watchList() {
  const w = D.areas.filter((a) => a.new_cluster_watch).sort((a, b) => a.new_cluster_rank - b.new_cluster_rank);
  $("tab-watch").innerHTML = `<p class="sub" style="margin-bottom:6px">Areas with no cluster today, ranked by the chance a new one starts within two weeks.</p>
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
  // greedy: each team takes the 300 m site covering the most priority weight not yet covered
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
    sites.push({ anchor: `Blk ${a.blk_no} ${title(a.street)}`, area: title(a.planning_area), lat: a.lat, lon: a.lon,
      blocks: sel.length, homes: sel.reduce((s, j) => s + B[j].h, 0), nearCluster: sel.filter((j) => B[j].near_cluster).length });
  }
  // simple plan: the top-weight blocks, spread at least 2 x radius apart
  const order = B.map((_, i) => i).sort((p, q) => B[q].w - B[p].w);
  const simple = new Uint8Array(B.length), anchors = [];
  for (const j of order) {
    if (anchors.length === teams) break;
    if (anchors.every((a) => (B[j].x - B[a].x) ** 2 + (B[j].y - B[a].y) ** 2 > (2 * RADIUS) ** 2)) {
      anchors.push(j);
      NB[j].forEach((k) => { simple[k] = 1; });
    }
  }
  const homes = (mask) => B.reduce((s, b, i) => s + (mask[i] ? b.h : 0), 0);
  return { sites, planHomes: homes(covered), simpleHomes: homes(simple) };
}

function replan() {
  const teams = +$("teams").value;
  const p = plan(teams);
  const ratio = p.simpleHomes ? (p.planHomes / p.simpleHomes).toFixed(1) : "–";
  $("plan-summary").innerHTML = `<div><b>${p.planHomes.toLocaleString()}</b>priority homes reached</div>
    <div><b>${ratio}×</b>vs top blocks spread out (${p.simpleHomes.toLocaleString()})</div>`;
  $("sites").innerHTML = p.sites.map((s, i) => `<li data-i="${i}"><b>${esc(s.anchor)}</b>, ${esc(s.area)}
    <div class="m">${s.blocks} blocks · ${s.homes.toLocaleString()} homes · ${s.nearCluster} near a cluster</div></li>`).join("");
  const kpi = $("kpi-homes");
  if (kpi) {
    kpi.querySelector(".v").textContent = p.planHomes.toLocaleString();
    kpi.querySelector(".l").textContent = `priority homes, ${teams} team${teams > 1 ? "s" : ""}`;
    kpi.querySelector(".s").textContent = `${ratio}× a simple plan`;
  }
  const wasOn = teamLayer && map.hasLayer(teamLayer);
  if (teamLayer) teamLayer.remove();
  teamLayer = L.layerGroup(p.sites.flatMap((s, i) => [
    L.circle([s.lat, s.lon], { radius: RADIUS, color: css("--team"), weight: 2, dashArray: "4 3", fillColor: css("--team"), fillOpacity: 0.1, interactive: false }),
    L.marker([s.lat, s.lon], { icon: L.divIcon({ className: "team-pin", html: String(i + 1), iconSize: [24, 24], iconAnchor: [12, 12] }) })
      .on("mousemove", (e) => showTip(e.originalEvent, `<b>Team ${i + 1}: ${esc(s.anchor)}</b><br>${s.blocks} blocks · ${s.homes.toLocaleString()} homes`))
      .on("mouseout", hideTip),
  ]));
  if (wasOn || $("lay-teams").checked || !$("tab-plan").hidden) teamLayer.addTo(map);
  $("sites").querySelectorAll("li").forEach((li) => li.addEventListener("click", () => {
    const s = p.sites[+li.dataset.i];
    teamLayer.addTo(map);
    map.setView([s.lat, s.lon], 15);
  }));
}

// ------------------------------------------------------------------ risk table
function riskTable(all = false) {
  const rows = all ? D.areas : D.areas.slice(0, 12);
  $("risk").innerHTML = `<thead><tr><th>Area</th><th>Today</th><th>In 2 weeks</th><th>Change</th><th>P(High)</th>
    <th>New-cluster rank</th><th>Why? (what the model sees)</th></tr></thead><tbody>${rows.map((a) => `
    <tr data-pa="${esc(a.planning_area)}"><td><b>${esc(a.area)}</b></td><td>${chip(a.level_today_name)}</td>
    <td>${chip(a.predicted_level)}</td><td>${esc(a.direction)}</td><td class="num">${pct(a.p_high)}</td>
    <td class="num">${a.new_cluster_rank ?? "–"}</td><td class="why">${esc(a.why)}</td></tr>`).join("")}</tbody>`;
  $("risk-more").hidden = all;
  $("risk-more").textContent = `Show all ${D.areas.length} areas`;
  $("risk-more").onclick = () => riskTable(true);
  $("risk").querySelectorAll("tbody tr").forEach((tr) => tr.addEventListener("click", () => {
    selectArea(tr.dataset.pa, true);
    $("map").scrollIntoView({ behavior: "smooth", block: "center" });
  }));
}

// ------------------------------------------------------------------ proof
function track() {
  const rows = [...D.track_record].sort((a, b) => (a.basis_date < b.basis_date ? 1 : -1));
  $("track").innerHTML = rows.map((r) => {
    const scored = r.status === "scored";
    const detail = scored
      ? `Right in ${r.model_right}/${r.areas} areas vs ${r.same_as_now_right} for "same as now" · High caught ${r.high_caught}/${r.high_outcomes} · new clusters in top 5: ${r.new_clusters_in_top5}/${r.new_clusters}`
      : `Waiting for NEA's first list from ${day(r.score_from)} (window ends ${day(r.score_until)})`;
    return `<div class="track-row"><span>NEA list <b>${day(r.basis_date)}</b> · locked ${day(r.locked_on)} · code ${esc(r.code)}</span>
      <span class="st">${scored ? "Scored" : "Waiting"}</span><span class="d">${esc(detail)}</span></div>`;
  }).join("") || `<p class="sub">No locked forecasts yet.</p>`;
}

function tests() {
  const h = D.test_headline;
  if (!h.length) { $("tests").innerHTML = `<p class="sub">No test results yet.</p>`; return; }
  $("test-sub").textContent = `${h[0].test_lists} NEA lists, ${h[0].test}; every year predicted only from the years before it.`;
  const hi = h.find((r) => r.question.startsWith("High"));
  const nc = h.filter((r) => r.question.startsWith("New"));
  const bar = (label, v, max, model, fmt) => `<span>${esc(label)}</span><span class="bar${model ? " model" : ""}" style="width:${Math.max(2, 100 * v / max)}%"></span><span class="n">${fmt(v)}</span>`;
  const signed = (v) => `${v >= 0 ? "+" : "−"}${Math.abs(100 * v).toFixed(1)}`;
  const ci = (r) => (r.diff_p05 == null ? "" : `Difference, 90% range: ${signed(r.diff_p05)} to ${signed(r.diff_p95)} points`);
  let html = "";
  if (nc.length) {
    const past = nc.find((r) => r.baseline_name === "Past clusters here"), chance = nc.find((r) => r.baseline_name.startsWith("Chance"));
    html += `<div class="test"><div class="q">New clusters caught by the top 5 picks</div><div class="bars">
      ${bar("DengueRadar", nc[0].model, 1, true, pct)}${past ? bar("Past clusters here", past.baseline, 1, false, pct) : ""}
      ${chance ? bar("Chance", chance.baseline, 1, false, pct) : ""}</div>
      <div class="ci">${past ? esc(ci(past)) : ""}${chance ? ` · ${(nc[0].model / chance.baseline).toFixed(1)}× chance` : ""}</div></div>`;
  }
  if (hi) {
    html += `<div class="test"><div class="q">High-risk areas (F1: catching High areas vs false alarms)</div><div class="bars">
      ${bar("DengueRadar", hi.model, 1, true, (v) => v.toFixed(2))}${bar("Same as now", hi.baseline, 1, false, (v) => v.toFixed(2))}</div>
      <div class="ci">${esc(ci(hi))}: a tie with the toughest simple rule</div></div>`;
  }
  $("tests").innerHTML = html;
}

function weekly() {
  const pts = D.weekly_cases.map((r) => ({ d: new Date(`${r.week_end.slice(0, 10)}T00:00:00`), v: +r.cases })).sort((a, b) => a.d - b.d);
  if (!pts.length) return;
  const W = 600, H = 190, L0 = 34, R = 8, T = 10, Bm = 22;
  const x0 = pts[0].d, x1 = pts[pts.length - 1].d, ymax = Math.max(...pts.map((p) => p.v));
  const sx = (d) => L0 + (W - L0 - R) * (d - x0) / (x1 - x0), sy = (v) => T + (H - T - Bm) * (1 - v / ymax);
  const years = [];
  for (let y = x0.getFullYear() + 1; y <= x1.getFullYear(); y++) years.push(new Date(`${y}-01-01T00:00:00`));
  $("weekly").innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Weekly dengue cases">
    <line class="ax" x1="${L0}" x2="${W - R}" y1="${H - Bm}" y2="${H - Bm}"/>
    ${years.map((d) => `<line class="ax" x1="${sx(d)}" x2="${sx(d)}" y1="${T}" y2="${H - Bm}"/><text x="${sx(d) + 3}" y="${H - 6}">${d.getFullYear()}</text>`).join("")}
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
