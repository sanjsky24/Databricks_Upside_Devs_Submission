// "Ask DengueRadar": a chat box answered by the DengueRadar Genie space through /api/ask (genie_chat.py).
// Genie writes SQL over the same gold tables as the rest of this screen; each answer can show its table
// and the SQL it ran, so officers can check where a number came from.
// If the page has a #tab-ask element the chat fills that tab; otherwise it is a floating button + panel.
(() => {
  const SAMPLES = [
    "Which areas are forecast High risk in the next two weeks?",
    "Where could new dengue clusters appear?",
    "Which dengue clusters are active right now?",
    "How accurate is the forecast?",
  ];
  const STATUS = {
    SUBMITTED: "Reading your question…", FETCHING_METADATA: "Reading the tables…", FILTERING_CONTEXT: "Reading the tables…",
    ASKING_AI: "Writing the query…", PENDING_WAREHOUSE: "Starting the SQL warehouse…", EXECUTING_QUERY: "Running the query…",
  };
  const SHOW_ROWS = 8;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let conversation = null;
  let busy = false;

  const host = document.getElementById("tab-ask");
  const panelHtml = `
    <section class="ask-panel${host ? " inline" : ""}" id="ask-panel" ${host ? 'aria-label="Ask DengueRadar"' : 'role="dialog" aria-label="Ask DengueRadar" hidden'}>
      <header class="ask-head">
        <div><b>Ask DengueRadar</b><span>Answers from the same tables as this screen, via Genie</span></div>
        <button class="ask-icon" id="ask-new" title="New conversation" aria-label="New conversation">↺</button>
        ${host ? "" : '<button class="ask-icon" id="ask-close" title="Close" aria-label="Close">×</button>'}
      </header>
      <div class="ask-log" id="ask-log" aria-live="polite"></div>
      <form class="ask-form" id="ask-form">
        <textarea id="ask-input" rows="1" maxlength="1000" placeholder="Ask about clusters, risk, teams, accuracy…" aria-label="Your question"></textarea>
        <button type="submit" id="ask-send" aria-label="Send">➤</button>
      </form>
    </section>`;
  if (host) host.insertAdjacentHTML("beforeend", panelHtml);
  else document.body.insertAdjacentHTML("beforeend", `
    <button class="ask-fab" id="ask-fab" aria-expanded="false" aria-controls="ask-panel">
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M4 4h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-5 4v-4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"/></svg>
      Ask DengueRadar
    </button>${panelHtml}`);

  const $ = (id) => document.getElementById(id);
  const log = $("ask-log"), input = $("ask-input"), panel = $("ask-panel"), fab = $("ask-fab");

  function intro() {
    log.innerHTML = `<div class="ask-msg bot"><p>Ask me about dengue in Singapore: active clusters, which areas are forecast
      High, where new clusters may appear, this week's inspection teams, how past forecasts scored, cases and weather.</p>
      <div class="ask-chips">${SAMPLES.map((q) => `<button type="button" class="ask-chip">${esc(q)}</button>`).join("")}</div></div>`;
  }

  function open(show) {
    if (host) return;
    panel.hidden = !show;
    fab.setAttribute("aria-expanded", String(show));
    fab.hidden = show;
    if (show) input.focus();
  }

  // Genie's text uses light markdown: **bold**, bullet lines and line breaks.
  function md(s) {
    const lines = esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").split("\n");
    let html = "", inList = false;
    for (const l of lines) {
      const item = l.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)/);
      if (item && !inList) { html += "<ul>"; inList = true; }
      if (!item && inList) { html += "</ul>"; inList = false; }
      html += item ? `<li>${item[1]}</li>` : (l.trim() ? `<p>${l}</p>` : "");
    }
    return html + (inList ? "</ul>" : "");
  }

  function cell(v) {
    if (v == null) return '<span class="ask-null">–</span>';
    if (/^-?\d+\.\d{4,}(E-?\d+)?$/i.test(v)) return esc(Number(v).toLocaleString("en-SG", { maximumSignificantDigits: 3 }));
    return esc(v);
  }

  function table(a) {
    if (!a.columns.length) return "";
    const head = a.columns.map((c) => `<th>${esc(c.replace(/_/g, " "))}</th>`).join("");
    const td = (v) => `<td${String(v ?? "").length > 40 ? ' class="long"' : ""}>${cell(v)}</td>`;
    const body = a.rows.map((r, i) => `<tr${i >= SHOW_ROWS ? ' class="ask-extra" hidden' : ""}>${r.map(td).join("")}</tr>`).join("");
    const more = a.rows.length > SHOW_ROWS ? `<button type="button" class="ask-more">Show all ${a.rows.length} rows</button>` : "";
    const cut = a.row_count > a.rows.length ? `<p class="ask-note">Showing the first ${a.rows.length} of ${a.row_count} rows.</p>` : "";
    return `<div class="ask-table"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>${more}${cut}`;
  }

  function render(el, a) {
    let html = a.error ? `<p class="ask-err">${esc(a.error)}</p>` : "";
    if (a.text) html += md(a.text);
    else if (a.description) html += `<p>${esc(a.description)}</p>`;
    html += table(a);
    if (a.sql) html += `<details class="ask-sql"><summary>Show SQL</summary><pre>${esc(a.sql)}</pre></details>`;
    if (a.suggestions && a.suggestions.length)
      html += `<div class="ask-chips">${a.suggestions.slice(0, 2).map((q) => `<button type="button" class="ask-chip">${esc(q)}</button>`).join("")}</div>`;
    if (!html) html = "<p>Genie had no answer to that. Try asking about clusters, risk levels, teams or cases.</p>";
    el.innerHTML = html;
    el.classList.remove("wait");
    log.scrollTop = el.previousElementSibling.offsetTop - log.offsetTop - 8;  // show the question and the start of its answer
  }

  async function send(question) {
    question = question.trim();
    if (!question || busy) return;
    busy = true;
    $("ask-send").disabled = true;
    log.insertAdjacentHTML("beforeend", `<div class="ask-msg me">${esc(question)}</div>`);
    log.insertAdjacentHTML("beforeend", `<div class="ask-msg bot wait"><p class="ask-status">Reading your question…</p></div>`);
    const el = log.lastElementChild;
    log.scrollTop = log.scrollHeight;
    try {
      const r = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, conversation_id: conversation }) });
      const start = await r.json();
      if (start.error) throw new Error(start.error);
      conversation = start.conversation_id;
      const url = `/api/ask/${encodeURIComponent(start.conversation_id)}/${encodeURIComponent(start.message_id)}`;
      const until = Date.now() + 180000;
      for (;;) {
        await new Promise((ok) => setTimeout(ok, 1500));
        const a = await (await fetch(url)).json();
        if (a.done) { render(el, a); break; }
        el.querySelector(".ask-status").textContent = STATUS[a.status] || "Working…";
        if (Date.now() > until) throw new Error("Genie took too long. Please try again.");
      }
    } catch (e) {
      el.classList.remove("wait");
      el.innerHTML = `<p class="ask-err">Sorry, that didn't work: ${esc(e.message)}</p>`;
    }
    busy = false;
    $("ask-send").disabled = false;
  }

  if (!host) {
    fab.addEventListener("click", () => open(true));
    $("ask-close").addEventListener("click", () => open(false));
  }
  $("ask-new").addEventListener("click", () => { if (!busy) { conversation = null; intro(); } });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !panel.hidden) open(false); });
  log.addEventListener("click", (e) => {
    if (e.target.classList.contains("ask-chip")) send(e.target.textContent);
    if (e.target.classList.contains("ask-more")) {
      e.target.parentElement.querySelectorAll(".ask-extra").forEach((tr) => { tr.hidden = false; });
      e.target.remove();
    }
  });
  $("ask-form").addEventListener("submit", (e) => { e.preventDefault(); const q = input.value; input.value = ""; send(q); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("ask-form").requestSubmit(); }
  });
  input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 120)}px`; });
  intro();
  if (!host && location.hash === "#ask") open(true);
})();
