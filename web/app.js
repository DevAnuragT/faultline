/* Faultline console. Plain JS, one state object, every number from the API. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const state = { window: null, windows: [], meta: {}, overview: null, session: null, chat: [], recorded: null, jobs: {}, chain: null, counts: null, graphW: 0 };
const fmt = (n, d = 0) => (n == null || isNaN(n)) ? "—" : Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });
const pct = (n) => (n == null) ? "—" : `${Math.round(n * 100)}%`;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const when = (ts) => ts ? new Date(ts * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
const ago = (ts) => {
  if (!ts) return "";
  const s = Math.max(0, Date.now() / 1000 - ts);
  const rel = s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : s < 86400 * 7 ? `${Math.round(s / 86400)} d ago` : when(ts);
  return `<time datetime="${new Date(ts * 1000).toISOString()}" title="${esc(when(ts))}">${rel}</time>`;
};
/* in-flight requests drive the thin progress bar under the top bar; it only appears for requests slower than 150 ms */
let inflight = 0, busyTimer;
function busy(d) {
  inflight = Math.max(0, inflight + d);
  clearTimeout(busyTimer);
  if (inflight > 0) busyTimer = setTimeout(() => document.body.classList.toggle("busy", inflight > 0), 150);
  else document.body.classList.remove("busy");
}
const api = async (path, opts) => {
  busy(1);
  try { const r = await fetch(path, opts); if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText); return await r.json(); }
  finally { busy(-1); }
};
const post = (path, body) => api(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
const label = (o) => `${o.service} ${o.op}` + (o.peer ? ` → ${o.peer}` : "");
/* display-only typography: arrows and comparison signs */
const nice = (s) => esc(s).replace(/ -&gt; /g, " → ").replace(/-&gt;/g, "→").replace(/&gt;=/g, "≥").replace(/&lt;=/g, "≤");

/* inline icons for rendered content (stroke icons, 20px grid) */
const ICON = {
  check: '<path d="M5 10.5l3.2 3.2L15 6.5"/>',
  cross: '<path d="M6 6l8 8M14 6l-8 8"/>',
  ext: '<path d="M8 4.5H4.5v11h11V12M11 4.5h4.5V9M15.5 4.5L9 11"/>',
  info: '<circle cx="10" cy="10" r="7"/><path d="M10 9v4.5M10 6.6h.01"/>',
  alert: '<path d="M10 3.5l7 12.5H3z"/><path d="M10 8.5v3.2M10 14h.01"/>',
  inbox: '<path d="M3.5 11l2-6.5h9l2 6.5v4.5h-13z"/><path d="M3.5 11h4l1 2h3l1-2h4"/>',
  flask: '<path d="M7.5 2.75h5M8.25 2.75v5.1L3.9 15.2A1.4 1.4 0 0 0 5.1 17.25h9.8a1.4 1.4 0 0 0 1.2-2.05l-4.35-7.35v-5.1"/>',
  fp: '<path d="M6 16c1-1.5 1.5-3.5 1.5-6a2.5 2.5 0 0 1 5 0c0 1.5-.2 3-.6 4.3M10 10c0 2.6-.6 4.9-1.8 6.7M4.2 13.3c.5-1 .8-2.1.8-3.3a5 5 0 0 1 9.4-2.4M15 10.5c0 1.4-.2 2.7-.5 3.9"/>',
  mark: '<path d="M5 21 L12 10.5 L17.5 18 L21.5 13 L27 21"/>',
};
const icon = (name, cls = "") => `<svg viewBox="${name === "mark" ? "0 0 32 32" : "0 0 20 20"}" class="${cls}" aria-hidden="true">${ICON[name]}</svg>`;
const empty = (title, text, ic = "info") => `<div class="empty">${icon(ic)}<div><b>${esc(title)}</b>${text ? `<p>${text}</p>` : ""}</div></div>`;
/* public demo: every write button is disabled and says why */
const lock = () => state.meta.public ? `disabled title="Turned off in the read-only public demo"` : "";

let toastTimer;
function toast(text, kind = "") {
  const t = $("#toast");
  t.className = `toast ${kind}`;
  t.innerHTML = `${icon(kind === "ok" ? "check" : kind === "bad" ? "alert" : "info")}<span>${esc(text)}</span>`;
  t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), kind === "bad" ? 7000 : 4200);
}
const proposed = (r, text) => toast(r.ok ? text : r.error, r.ok ? "ok" : "bad");

/* ---------- theme ---------- */
(function theme() {
  let saved = null; try { saved = localStorage.getItem("fl-theme"); } catch (e) {}
  if (saved) document.documentElement.dataset.theme = saved;
  $("#theme").addEventListener("click", () => {
    const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("fl-theme", next); } catch (e) {}
    if (state.overview) { drawGraph(state.overview); }
  });
})();

/* ---------- routing ---------- */
function show(view) {
  $$(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
  $$(".rail a").forEach((a) => { const on = a.dataset.view === view; a.classList.toggle("active", on); if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  if (view === "findings" && state.overview && Math.abs(($("#svc-graph").clientWidth || 0) - state.graphW) > 40) drawGraph(state.overview);
  if (view === "trace") loadTrace();
  if (view === "experiments") loadExperiments();
  if (view === "approvals") loadActions();
  if (view === "graph") loadGraphView();
  document.body.dataset.view = view;
  syncHash(view);
}
/* deep links: #view?w=<window>&t=<trace>. Pasting one restores the view, the window and the open trace. */
const VIEWS = ["findings", "trace", "experiments", "agent", "approvals", "graph"];
function route() { const [v, q] = location.hash.slice(1).split("?"); const p = new URLSearchParams(q || ""); return { view: VIEWS.includes(v) ? v : "findings", w: p.get("w"), t: p.get("t") }; }
function linkFor(view) { const p = new URLSearchParams(); if (state.window) p.set("w", state.window); if (view === "trace" && state.traceId) p.set("t", state.traceId); return `#${view}?${p}`; }
function syncHash(view) { const h = linkFor(view); if (location.hash !== h) history.replaceState(null, "", h); }
function go(view) { history.pushState(null, "", linkFor(view)); show(view); window.scrollTo({ top: 0 }); }
window.addEventListener("hashchange", async () => {
  const r = route();
  if (r.w && r.w !== state.window && state.windows.some((x) => x.id === r.w)) { state.window = r.w; state.traceId = null; $("#window").value = r.w; setCompareDefaults(); loadOverview(); }
  if (r.t) state.traceId = r.t;
  show(r.view); window.scrollTo({ top: 0 });
});
let resizeTimer;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (state.overview && !$("#view-findings").hidden && Math.abs($("#svc-graph").clientWidth - state.graphW) > 40) drawGraph(state.overview); }, 200); });

/* ---------- boot ---------- */
const windowOption = (x) => `<option value="${x.id}">${esc(x.label)} · ${x.id} · ${x.traces} traces</option>`;
/* the picker groups windows by what they are: steady state, deliberate fault experiments, and fixes that were rolled back */
const KIND_GROUP = { steady: "Steady state", experiment: "Fault experiments", rejected: "Rejected fixes, rolled back" };
function windowOptions(ws) {
  const newest = [...ws].reverse();
  return Object.entries(KIND_GROUP).map(([k, lbl]) => { const xs = newest.filter((x) => (x.kind || "steady") === k); return xs.length ? `<optgroup label="${lbl}">${xs.map(windowOption).join("")}</optgroup>` : ""; }).join("")
    + newest.filter((x) => x.kind && !KIND_GROUP[x.kind]).map(windowOption).join("");
}
const compareOption = (x) => `<option value="${x.id}">${esc(x.label)} · ${x.id}${x.kind && x.kind !== "steady" ? ` (${x.kind})` : ""}</option>`;
async function boot() {
  state.meta = await api("/api/meta");
  $("#meta-graph").textContent = state.meta.graph;
  $("#meta-model").textContent = state.meta.model;
  $("#meta-public").hidden = !state.meta.public;
  const w = await api("/api/windows");
  state.windows = w.windows; state.window = w.steady;
  const r0 = route();
  if (r0.w && w.windows.some((x) => x.id === r0.w)) state.window = r0.w;
  if (r0.t) state.traceId = r0.t;
  const sel = $("#window");
  sel.innerHTML = windowOptions(w.windows);
  sel.value = state.window;
  sel.addEventListener("change", () => { state.window = sel.value; state.traceId = null; setCompareDefaults(); loadOverview(); const v = route().view; if (v !== "findings") show(v); else syncHash(v); });
  for (const id of ["cmp-a", "cmp-b"]) $(`#${id}`).innerHTML = w.windows.map(compareOption).join("");
  $("#cmp-a").value = w.windows[0]?.id; $("#cmp-b").value = w.windows[w.windows.length - 1]?.id;
  setCompareDefaults();
  $("#cmp-a").addEventListener("change", loadCompare); $("#cmp-b").addEventListener("change", loadCompare);
  state.recorded = await api("/api/agent/recorded");
  if (state.recorded.turns?.length) {
    for (const t of state.recorded.turns) state.chat.push({ role: "user", text: t.prompt }, { role: "assistant", text: t.reply, trace: t.trace, model: t.model || state.recorded.model, recorded: true });
    $("#agent-hint").textContent = `A recorded conversation with ${state.recorded.model} from ${state.recorded.recorded_at.slice(0, 10)}, with every graph tool call it made. Ask your own question below; proposals land in Approvals and never run on their own.`;
  }
  renderChat();
  await loadOverview();
  document.body.classList.remove("loading");
  show(route().view);
  pollActions();
  tourAuto();
}

/* ---------- overview ---------- */
const SEV = { high: 0, medium: 1, low: 2 };
async function loadOverview() {
  const o = await api(`/api/overview?window=${encodeURIComponent(state.window)}`);
  state.overview = o;
  const h = o.health[0] || {};
  const top = o.owners[0];
  const isExp = (state.windows.find((x) => x.id === state.window)?.label || "").startsWith("pause-");
  $("#s-op").textContent = h.op || "—";
  $("#s-traces").innerHTML = h.traces ? `${h.traces} traces${isExp ? ` · <span class="flag">fault experiment</span>` : ""}` : "no traces in this window";
  $("#s-p50").textContent = fmt(h.p50_ms); $("#s-p95").textContent = fmt(h.p95_ms);
  $("#s-tail").textContent = h.p50_ms ? `${fmt(h.p95_ms / h.p50_ms, 1)}× the median` : "slowest 1 in 20";
  $("#s-failrate").textContent = h.traces ? pct(h.fail_rate) : "—";
  $("#s-failed").textContent = h.traces ? `${h.failed} of ${h.traces} requests` : "";
  $("#k-failed").classList.toggle("is-crit", h.fail_rate >= 0.25);
  $("#k-failed").classList.toggle("is-warn", h.fail_rate >= 0.1 && h.fail_rate < 0.25);
  $("#s-owner").textContent = top ? label(top) : "—"; $("#s-owner").title = top ? label(top) : "";
  $("#s-owner-share").textContent = top ? `${top.share_pct}% of critical-path time · ${fmt(top.per_request_ms)} ms per request` : "";
  $("#s-owner-bar").style.width = top ? `${Math.min(100, top.share_pct)}%` : "0";

  const max = top ? top.share_pct : 1;
  $("#owners tbody").innerHTML = o.owners.slice(0, 9).map((x, i) => `<tr class="${i === 0 ? "lead" : ""}">
    <td class="num rank">${i + 1}</td>
    <td><div class="owner-name">${esc(x.service)} <span class="op">${esc(x.op)}${x.peer ? ` → ${esc(x.peer)}` : ""}</span></div>${x.code ? `<span class="owner-code">${codeLink(x.code)}${readCodeBtn(x.service, x.op)}</span>` : ""}</td>
    <td data-sort-value="${x.share_pct}"><div class="share"><span class="bar"><i style="width:${Math.max(2, 100 * x.share_pct / max)}%"></i></span><span class="pct">${x.share_pct}%</span></div></td>
    <td class="num">${fmt(x.per_request_ms)}</td></tr>`).join("") || `<tr><td colspan="4">${empty("No critical-path data", "This window has no steady-state traces to attribute.")}</td></tr>`;

  const findings = [...o.findings].sort((a, b) => (SEV[a.severity] ?? 3) - (SEV[b.severity] ?? 3));
  const nHigh = findings.filter((f) => f.severity === "high").length;
  $("#findings-count").innerHTML = findings.length ? `<b>${findings.length}</b> findings${nHigh ? ` · <b>${nHigh}</b> high` : ""}` : "";
  $("#findings-list").innerHTML = findings.map((f) => `<li class="finding ${esc(f.severity)}"><span class="sev"><i></i>${esc(f.severity[0].toUpperCase() + f.severity.slice(1))}</span><span class="kind tag">${esc(f.kind.replace(/_/g, " "))}</span><div class="text"><p>${nice(f.detail)}</p>${f.cypher ? queryBox(f.cypher, f.params) : ""}</div></li>`).join("")
    || `<li class="empty-li">${empty("No findings in this window", "Pick the steady-state window in the top bar, or ingest traces with <code>make baseline</code>.")}</li>`;

  $("#exposure tbody").innerHTML = o.exposure.map((e) => `<tr><td><b>${esc(e.store)}</b><span class="cell-sub">${esc(e.store_kind)}</span></td><td class="path">${nice(e.path)}</td><td><div class="tags">${e.protocols.map((p) => `<span class="tag">${esc(p || "?")}</span>`).join("")}</div></td><td class="num">${e.calls_per_request_into_store}</td></tr>`).join("")
    || `<tr><td colspan="4">${empty("Nothing reachable", "No data store is reached from the edge in this window.")}</td></tr>`;
  $("#guardrails tbody").innerHTML = o.guardrails.map((g) => `<tr><td class="hop">${nice(g.hop)}</td><td><span class="tag">${esc(g.kind)}</span>${g.priority === "high" ? ` <span class="pill wait" title="An experiment showed this hop hangs"><i></i>high</span>` : ""}</td><td class="mono strong">${nice(g.value)}</td><td class="evidence">${nice(g.evidence)}</td><td class="act">${g.priority === "high" && g.kind === "timeout" ? `<button class="btn small" data-propose-guardrail="${esc(g.hop)}" ${lock()}>Propose</button>` : ""}</td></tr>`).join("")
    || `<tr><td colspan="5">${empty("No guardrails to recommend", "Every hop already has headroom in this window.")}</td></tr>`;
  $("#retries tbody").innerHTML = o.retries.map((r) => `<tr><td class="mono">${esc(r.service)} ${esc(r.op)}</td><td class="num">${r.retried_errors}</td><td class="num">${fmt(r.wasted_ms_per_request, 1)}</td><td class="num">${fmt(r.retries_on_critical_path)}</td></tr>`).join("")
    || `<tr><td colspan="4">${empty("No silent retries", "No failed call was retried without surfacing an error.", "check")}</td></tr>`;
  $$("[data-propose-guardrail]").forEach((b) => b.addEventListener("click", async () => { const r = await post("/api/propose", { type: "guardrail", hop: b.dataset.proposeGuardrail }); proposed(r, `Proposed: ${r.title || r.action}. Approve it under Approvals.`); pollActions(); }));
  applySort("owners"); applySort("guardrails");
  drawGraph(o);
  loadStatus(); loadNextSteps();
}

function codeLink(c) {
  if (!c) return "";
  if (c.location) return `<span title="from span attributes">${esc(c.location)}</span>`;
  const text = `${c.file.split("/").pop()}${c.function ? " · " + c.function : ""}`;
  return c.url ? `<a href="${esc(c.url)}" target="_blank" rel="noopener" title="${esc(c.why || "")}">${esc(text)}</a>` : esc(text);
}
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
const V = (name) => `var(${name})`;

function drawGraph(o) {
  const svg = d3.select("#svc-graph"); svg.selectAll("*").remove();
  const W = svg.node().clientWidth || 600, H = svg.node().clientHeight || 420;
  state.graphW = svg.node().clientWidth;
  svg.attr("viewBox", `0 0 ${W} ${H}`);
  const share = {}; for (const x of o.owners) share[x.service] = (share[x.service] || 0) + x.share_pct;
  const stores = new Set(o.exposure.map((e) => e.store));
  const names = new Set(); o.graph.forEach((e) => { names.add(e.from); names.add(e.to); });
  const nodes = [...names].map((id) => ({ id, share: share[id] || 0, store: stores.has(id), edge: state.meta.edge.includes(id) }));
  const links = o.graph.map((e) => ({ source: e.from, target: e.to, ...e }));
  const narrow = W < 560, NW = narrow ? 96 : 112, NH = 38;
  const sim = d3.forceSimulation(nodes).force("link", d3.forceLink(links).id((d) => d.id).distance(Math.min(210, W / 3.8))).force("charge", d3.forceManyBody().strength(narrow ? -1100 : -1800)).force("collide", d3.forceCollide(narrow ? 54 : 66))
    .force("center", d3.forceCenter(W / 2, H / 2)).force("x", d3.forceX((d) => d.edge ? W * 0.13 : d.store ? W * 0.86 : W / 2).strength(0.3)).force("y", d3.forceY(H / 2).strength(0.04));
  const defs = svg.append("defs");
  for (const [id, color] of [["arrow", "--fg-3"], ["arrow-err", "--crit"]])
    defs.append("marker").attr("id", id).attr("viewBox", "0 -4 8 8").attr("refX", 8).attr("markerWidth", 8).attr("markerHeight", 8).attr("markerUnits", "userSpaceOnUse").attr("orient", "auto")
      .append("path").attr("d", "M0,-4L8,0L0,4").style("fill", V(color));
  const link = svg.append("g").selectAll("line").data(links).join("line").attr("class", "edge").style("stroke", (d) => d.errors ? V("--crit") : V("--fg-3"))
    .attr("stroke-width", (d) => 1.6 + Math.min(d.per_trace, 14) / 2.4).attr("marker-end", (d) => d.errors ? "url(#arrow-err)" : "url(#arrow)").attr("stroke-opacity", (d) => d.errors ? 0.95 : 0.75).attr("stroke-linecap", "round");
  /* wide transparent strokes make thin edges easy to hover */
  const hit = svg.append("g").selectAll("line").data(links).join("line").attr("class", "edge-hit").attr("stroke-width", 14).style("stroke", "transparent").style("pointer-events", "stroke");
  const elabel = svg.append("g").selectAll("text").data(links).join("text").attr("class", "edge-label").attr("text-anchor", "middle").text((d) => `${fmt(d.per_trace, 1)}/req${d.errors ? ` · ${d.errors} err` : ""}`);
  const node = svg.append("g").selectAll("g").data(nodes).join("g").attr("class", "node").call(d3.drag().on("start", (ev, d) => { if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }).on("drag", (ev, d) => { d.fx = ev.x; d.fy = ev.y; }).on("end", (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));
  node.append("rect").attr("class", "box").attr("x", -NW / 2).attr("y", -NH / 2).attr("width", NW).attr("height", NH).attr("rx", 6).style("fill", V("--panel"))
    .style("stroke", (d) => d.share >= 10 ? V("--ember") : d.store ? V("--store") : V("--line-2")).attr("stroke-width", (d) => d.share >= 30 ? 2.2 : 1.4);
  node.append("rect").attr("x", -NW / 2 + 6).attr("y", NH / 2 - 6).attr("height", 3).attr("rx", 1.5).attr("width", (d) => d.share >= 1 ? Math.max(3, (NW - 12) * d.share / 100) : 0).style("fill", V("--ember"));
  node.append("text").attr("text-anchor", "middle").attr("dy", "-2").text((d) => d.id);
  node.append("text").attr("text-anchor", "middle").attr("dy", "11").attr("class", "node-sub").text((d) => d.share >= 1 ? `${Math.round(d.share)}% of latency` : d.edge ? "edge" : d.store ? "data store" : "");
  node.filter((d) => d.store).append("circle").attr("cx", NW / 2 - 7).attr("cy", -NH / 2 + 7).attr("r", 3.2).style("fill", V("--store")).append("title").text("data store");
  node.on("mousemove", (ev, d) => tip(ev, `${d.id}\n${d.share ? `${d.share.toFixed(1)}% of critical-path latency` : "not on the critical path"}`)).on("mouseleave", hideTip);
  hit.on("mousemove", (ev, d) => tip(ev, `${d.from} → ${d.to}\n${d.count} calls, ${d.errors} errors\np50 ${fmt(d.p50_ms)} ms · p95 ${fmt(d.p95_ms)} ms\n${fmt(d.per_trace, 1)} calls per request`)).on("mouseleave", hideTip);
  /* end each edge at the target's box border so the arrowhead stays visible */
  const clip = (s, t) => { const dx = t.x - s.x, dy = t.y - s.y; const k = Math.min(Math.abs((NW / 2 + 3) / (dx || 1e-6)), Math.abs((NH / 2 + 3) / (dy || 1e-6))); return [t.x - dx * Math.min(k, 1), t.y - dy * Math.min(k, 1)]; };
  sim.on("tick", () => {
    nodes.forEach((d) => { d.x = Math.max(NW / 2 + 4, Math.min(W - NW / 2 - 4, d.x)); d.y = Math.max(NH / 2 + 6, Math.min(H - NH / 2 - 6, d.y)); });
    for (const sel of [link, hit]) sel.attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y).attr("x2", (d) => clip(d.source, d.target)[0]).attr("y2", (d) => clip(d.source, d.target)[1]);
    /* labels sit beside the edge, offset along its normal, never on the line */
    elabel.attr("x", (d) => { const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y, L = Math.hypot(dx, dy) || 1; return (d.source.x + d.target.x) / 2 - (dy / L) * 10; })
      .attr("y", (d) => { const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y, L = Math.hypot(dx, dy) || 1; return (d.source.y + d.target.y) / 2 + (dx / L) * 10 + 3; });
    node.attr("transform", (d) => `translate(${d.x},${d.y})`);
  });
}
function tip(ev, text) { const t = $("#tip"); t.textContent = text; t.hidden = false; const x = Math.min(ev.clientX + 14, innerWidth - t.offsetWidth - 12), y = Math.min(ev.clientY + 14, innerHeight - t.offsetHeight - 12); t.style.left = Math.max(8, x) + "px"; t.style.top = y + "px"; }
function hideTip() { $("#tip").hidden = true; }

/* ---------- trace ---------- */
async function loadTrace() {
  const w = state.window, id = state.traceId;
  renderTraceMode();
  loadTraceList(w);
  if (id) {
    const wf = await api(`/api/trace/${encodeURIComponent(id)}`);
    if (state.traceId !== id) return;
    if (!wf.spans.length) {
      clearWaterfall(`No trace with id ${id} in the graph.`);
      $("#chain tbody").innerHTML = `<tr><td colspan="5">${empty("Trace not found", `Check the id, or open one of the slowest requests listed here. <button class="linkish" type="button" data-trace-open="">Back to the slowest request</button>`)}</td></tr>`;
      return;
    }
    drawWaterfall(wf);
    /* the critical chain of any trace is its critical spans in start order */
    renderChain(wf.spans.filter((x) => x.critical).map((x) => ({ service: x.service, op: x.op, peer: x.peer, duration_ms: x.duration_ms, self_ms: x.self_ms })));
    return;
  }
  const c = await api(`/api/chain?window=${encodeURIComponent(w)}`);
  if (state.traceId || state.window !== w) return;
  const wf = c.waterfall;
  if (!wf) {
    clearWaterfall("No steady-state trace in this window.");
    $("#chain tbody").innerHTML = `<tr><td colspan="5">${empty("No trace to show", "This window has no steady-state request. Pick another window in the top bar.")}</td></tr>`;
    return;
  }
  drawWaterfall(wf);
  renderChain(c.chain);
}
function clearWaterfall(text) { $("#trace-meta").textContent = text; $("#waterfall").innerHTML = ""; $("#waterfall").style.height = "0px"; }
function renderTraceMode() {
  $("#trace-mode").innerHTML = state.traceId
    ? `Showing trace <span class="mono">${esc(state.traceId.slice(0, 16))}${state.traceId.length > 16 ? "…" : ""}</span> <button class="linkish" type="button" data-trace-open="">Back to the slowest request</button>`
    : "Showing the slowest steady-state request in this window.";
}
function drawWaterfall(wf) {
  const crit = wf.spans.filter((s) => s.critical).length;
  $("#trace-meta").innerHTML = [["", wf.root], [fmt(wf.duration_ms), "ms end to end"], [wf.spans.length, "spans"], [crit, "on the critical path"]]
    .map(([v, l]) => `<span class="m">${v !== "" ? `<b>${esc(v)}</b> ` : ""}${esc(l)}</span>`).join("")
    + `<span class="m idm">trace <span class="mono">${esc(wf.trace.slice(0, 12))}…</span>${copyBtn(wf.trace, "trace id")}</span>`;
  $("#trace-meta").title = `trace ${wf.trace}`;
  state.shownTrace = wf.trace; markShownTrace();
  const spans = wf.spans; const rowH = 22, left = 270, right = 24, top = 26; const W = Math.max(760, $(".waterfall-wrap").clientWidth), H = top + spans.length * rowH + 10;
  const x = d3.scaleLinear().domain([0, wf.duration_ms]).range([left, W - right]);
  const svg = d3.select("#waterfall").attr("viewBox", `0 0 ${W} ${H}`).style("height", H + "px"); svg.selectAll("*").remove();
  const axis = svg.append("g").attr("class", "axis").attr("transform", `translate(0,${top - 8})`).call(d3.axisTop(x).ticks(8).tickFormat((d) => `${d} ms`).tickSize(-(H - top)));
  axis.select(".domain").remove();
  const g = svg.append("g").selectAll("g").data(spans).join("g").attr("class", "span-row").attr("transform", (d, i) => `translate(0,${top + i * rowH})`);
  g.append("rect").attr("class", "row-bg").attr("x", 0).attr("y", 0).attr("width", W).attr("height", rowH);
  g.append("text").attr("x", 20).attr("y", 15).attr("dx", (d) => d.depth * 10).attr("class", (d) => d.critical ? "crit" : "").text((d) => { const t = `${d.service} · ${d.op}${d.peer ? ` → ${d.peer}` : ""}`; return t.length > 36 - d.depth ? t.slice(0, 35 - d.depth) + "…" : t; });
  g.append("rect").attr("x", (d) => x(d.offset_ms)).attr("y", 5).attr("height", rowH - 10).attr("width", (d) => Math.max(2, x(d.offset_ms + d.duration_ms) - x(d.offset_ms))).attr("rx", 2)
    .style("fill", (d) => d.error ? V("--crit") : d.critical ? V("--ember") : V("--slate")).attr("fill-opacity", (d) => d.critical || d.error ? 1 : 0.8);
  g.append("rect").attr("x", (d) => x(d.offset_ms)).attr("y", 5).attr("height", rowH - 10).attr("width", (d) => d.critical ? Math.max(0, x(d.offset_ms + d.self_ms) - x(d.offset_ms)) : 0).attr("rx", 2).style("fill", V("--fg")).attr("fill-opacity", 0.22);
  g.on("mousemove", (ev, d) => tip(ev, `${d.service} ${d.op}${d.peer ? ` → ${d.peer}` : ""}\nstart +${fmt(d.offset_ms, 1)} ms · ${fmt(d.duration_ms, 1)} ms${d.critical ? ` · self ${fmt(d.self_ms, 1)} ms on the critical path` : ""}${d.error ? "\nERROR" : ""}`)).on("mouseleave", hideTip);
}
function renderChain(chain) {
  $("#chain tbody").innerHTML = chain.map((s) => `<tr><td>${esc(s.service)}</td><td class="mono">${esc(s.op)}</td><td class="mono muted">${esc(s.peer) || `<span class="hint">—</span>`}</td><td class="num">${fmt(s.duration_ms, 1)}</td><td class="num">${s.self_ms >= 1 ? `<b>${fmt(s.self_ms, 1)}</b>` : fmt(s.self_ms, 1)}</td></tr>`).join("")
    || `<tr><td colspan="5">${empty("No critical spans", "This trace has no spans marked on the critical path.")}</td></tr>`;
}
/* the request list comes from /api/traces: slowest first, or only the requests with error spans */
async function loadTraceList(w) {
  const errors = state.traceFilter === "errors";
  $$("[data-trace-filter]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.traceFilter === (errors ? "errors" : "slowest"))));
  let r;
  try { r = await api(`/api/traces?window=${encodeURIComponent(w)}&order=duration&errors=${errors}&limit=12`); }
  catch (e) { r = { error: e.message }; }
  if (state.window !== w) return;
  $("#trace-list tbody").innerHTML = r.error ? `<tr><td colspan="4">${empty("Could not list requests", esc(r.error), "alert")}</td></tr>`
    : r.traces.map((t) => `<tr data-trace="${esc(t.id)}"><td><span class="mono">${esc(String(t.id).slice(0, 12))}…</span>${copyBtn(t.id, "trace id")}<span class="cell-sub">${esc(t.root)}${t.start_us ? ` · ${clock(t.start_us)}` : ""}${t.errors ? ` · ${t.errors} error span${t.errors === 1 ? "" : "s"}` : ""}</span></td><td class="num">${fmt(t.duration_ms)}</td><td><span class="pill ${String(t.status).startsWith("5") ? "bad" : "muted"}"><i></i>${esc(t.status || "none")}</span></td><td class="act"><button class="btn small" type="button" data-trace-open="${esc(t.id)}">Open</button></td></tr>`).join("")
    || `<tr><td colspan="4">${empty(errors ? "No request with errors" : "No requests", errors ? "Every request in this window ran without an error span." : "This window has no traces.", errors ? "check" : "info")}</td></tr>`;
  markShownTrace();
}
document.addEventListener("click", (e) => { const b = e.target.closest?.("[data-trace-filter]"); if (b) { state.traceFilter = b.dataset.traceFilter; loadTraceList(state.window); } });
function markShownTrace() { $$("#trace-list tr[data-trace]").forEach((tr) => { const on = tr.dataset.trace === state.shownTrace; tr.classList.toggle("current", on); const b = tr.querySelector("[data-trace-open]"); if (b) { b.disabled = on; b.textContent = on ? "Shown" : "Open"; } }); }
function openTrace(id) {
  state.traceId = id || null;
  if (route().view !== "trace") { go("trace"); return; }
  syncHash("trace"); loadTrace(); window.scrollTo({ top: 0 });
}
$("#trace-form").addEventListener("submit", (e) => { e.preventDefault(); const id = $("#trace-q").value.trim().replace(/^.*[#?&]t=/, ""); openTrace(id); $("#trace-q").value = ""; });
document.addEventListener("click", (e) => { const b = e.target.closest?.("[data-trace-open]"); if (b) { e.preventDefault(); openTrace(b.dataset.traceOpen); } });

/* ---------- experiments ---------- */
const VERDICT = {
  CONFIRMED: ["ok", "Every checkable part of the graph's hypothesis held."],
  VERIFIED: ["ok", "All four gates passed; the fix was kept."],
  REFUTED: ["bad", "At least one part of the graph's hypothesis did not hold."],
  REJECTED: ["bad", "A gate failed; the fix was rolled back."],
};
const verdict = (v) => { const [k, t] = VERDICT[v] || [["CONFIRMED", "VERIFIED"].includes(v) ? "ok" : "bad", ""]; return `<span class="pill ${k}" title="${esc(t)}"><i></i>${esc(v)}</span>`; };
const gateItem = (passed, name, detail) => `<li class="${passed ? "" : "fail"}">${icon(passed ? "check" : "cross")}<span class="g-name">${nice(name)}</span>${detail ? `<span class="g-detail">${nice(detail)}</span>` : ""}</li>`;
const splitPart = (text) => { const i = text.indexOf(": "); return i > 0 ? [text.slice(0, i), text.slice(i + 2)] : [text, ""]; };
const splitGate = (text) => { const i = text.lastIndexOf(": "); return i > 0 ? [text.slice(0, i), text.slice(i + 2)] : [text, ""]; };
const winSpan = (a, b) => `<span class="span" title="before → after window">${esc(a)} → ${esc(b)}</span>`;
const countMeta = (id, n, noun) => { $(id).innerHTML = n ? `<b>${n}</b> ${noun}${n === 1 ? "" : "s"}` : ""; };

async function loadExperiments() {
  const [p, l] = await Promise.all([api(`/api/planner?window=${encodeURIComponent(state.window)}`), api("/api/ledger")]);
  const maxP = Math.max(...p.experiments.map((r) => r.priority), 0.001);
  $("#planner tbody").innerHTML = p.experiments.map((r) => {
    const hist = r.reasons.filter((x) => x.startsWith("tested before")), rest = r.reasons.filter((x) => !x.startsWith("tested before"));
    return `<tr><td><b>${esc(r.target)}</b></td>
      <td data-sort-value="${r.priority}"><div class="prio"><span class="bar"><i style="width:${100 * r.priority / maxP}%"></i></span><span class="v">${r.priority.toFixed(2)}</span></div></td>
      <td><div class="reasons">${r.skip ? `<div><span class="pill muted"><i></i>skipped</span> ${esc(r.skip)}</div>` : ""}<div>${rest.map(nice).join(" · ")}</div>${hist.map((x) => `<div class="history">${nice(x)}</div>`).join("")}</div></td>
      <td class="act">${r.target !== "frontend" && !r.skip ? `<button class="btn small" data-propose-exp="${r.target}" ${lock()}>Propose pause</button>` : ""}</td></tr>`;
  }).join("") || `<tr><td colspan="4">${empty("Nothing to break", "The planner found no candidate target in this window.")}</td></tr>`;
  applySort("planner");
  state.planner = p; state.ledger = l;
  $$("[data-propose-exp]").forEach((b) => b.addEventListener("click", async () => { const r = await post("/api/propose", { type: "experiment", target: b.dataset.proposeExp }); proposed(r, `Proposed ${r.action}. Approve it under Approvals.`); pollActions(); }));
  const nf = p.next_fix;
  $("#next-fix").innerHTML = remedyPanel(p, l);
  $("#propose-fix")?.addEventListener("click", async () => { const r = await post("/api/propose", { type: "fix", flags: nf.flags.join(" "), rationale: `targets ${nf.targets_owner} (${nf.share_pct}%)` }); proposed(r, `Proposed ${r.action}. Approve it under Approvals.`); pollActions(); });

  countMeta("#n-experiments", l.experiments.length, "experiment");
  $("#experiments-list").innerHTML = l.experiments.map((e) => `<article class="card"><div class="card-head">${verdict(e.verdict)}<h5 class="card-title">pause <span class="mono">${esc(e.target)}</span></h5>${winSpan(e.baseline, e.observed)}${ago(e.ts)}</div>
    <dl class="kv"><dt>Hypothesis</dt><dd>${esc(e.hypothesis)}${e.parts?.length ? `<ul class="gates parts" aria-label="Parts of the hypothesis">${e.parts.map((g) => gateItem(g.passed, ...splitPart(g.text))).join("")}</ul>` : ""}</dd><dt>Observed</dt><dd>${e.impact.map((x) => `${esc(x.op)}: ${pct(x.fail_rate)} failed, p50 ${fmt(x.p50_ms)} ms, ${x.failed_fast ? "failed fast" : "<b>hung</b>"}`).join("; ") || "—"}</dd></dl>
    <div class="lesson"><b>Lesson</b>${nice(e.lesson)}</div></article>`).join("") || empty("No experiment yet", "Propose a pause from What to break next, then approve it under Approvals.", "flask");
  $("#n-fixes").innerHTML = l.fixes.length ? `<b>${l.fixes.length}</b> ${l.fixes.length === 1 ? "fix" : "fixes"}` : "";
  $("#fixes").innerHTML = l.fixes.map((f) => `<article class="card"><div class="card-head">${verdict(f.verdict)}<h5 class="card-title"><span class="mono">${esc(f.flags)}</span> <span class="muted">on ${esc(f.service)}</span></h5>${winSpan(f.before, f.after)}${ago(f.ts)}</div>
    <ul class="gates">${f.gates.map((g) => gateItem(g.passed, ...splitGate(g.text))).join("")}</ul>
    <div class="card-foot"><span class="hint">Next owner: ${nice(f.next_owner || "—")}</span>${f.verdict === "VERIFIED" ? `<button class="btn small" data-propose-pr="${f.id}" ${lock()}>Propose draft PR</button>` : ""}</div></article>`).join("") || empty("No fix yet", "Propose one from What to fix next. It is measured against four gates before it is kept.");
  $$("[data-propose-pr]").forEach((b) => b.addEventListener("click", async () => { const r = await post("/api/propose", { type: "pr", subject: b.dataset.proposePr }); proposed(r, `Proposed ${r.action}: ${r.title}. Approve it under Approvals.`); pollActions(); }));
  const inc = await api("/api/incidents");
  countMeta("#n-incidents", inc.incidents.length, "incident");
  state.incidents = inc.incidents;
  $("#incidents").innerHTML = inc.incidents.map(incidentCard).join("") || empty("No incidents", "No window regressed against the current steady state.", "check");
  $$("[data-propose-issue]").forEach((b) => b.addEventListener("click", async () => { const r = await post("/api/propose", { type: "issue", window: b.dataset.proposeIssue }); proposed(r, `Proposed ${r.action}. Approve it under Approvals.`); pollActions(); }));
  loadCompare();
}
async function loadCompare() {
  const a = $("#cmp-a").value, b = $("#cmp-b").value; if (!a || !b) return;
  const d = await api(`/api/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
  const delta = (x, y, points = false) => deltaChip(x, y, points) || `<span class="delta flat">—</span>`;
  const row = (lbl, x, y, unit, dec = 0, points = false) => `<tr><td class="lbl-cell">${lbl}</td><td class="num">${points ? pct(x) : `${fmt(x, dec)}${unit}`}</td><td class="num">${points ? pct(y) : `${fmt(y, dec)}${unit}`}</td><td class="num">${delta(x, y, points)}</td></tr>`;
  $("#compare tbody").innerHTML = d.health.map((h) => `<tr class="sect"><td colspan="4">${esc(h.op)}</td></tr>${row("p50 latency", h.p50_ms[0], h.p50_ms[1], " ms")}${row("p95 latency", h.p95_ms[0], h.p95_ms[1], " ms")}${row("failed requests", h.fail_rate[0], h.fail_rate[1], "", 0, true)}`).join("")
    + (d.owners.length ? `<tr class="sect"><td colspan="4">Critical-path time per request</td></tr>` + d.owners.slice(0, 5).map((o) => row(nice(o.owner), o.per_request_ms[0], o.per_request_ms[1], " ms")).join("") : "")
    + ((d.edges_added.length || d.edges_removed.length) ? `<tr class="sect"><td colspan="4">Topology</td></tr><tr><td colspan="4"><div class="drift"><span class="pill wait"><i></i>drift</span> added ${nice(d.edges_added.join(", ") || "none")}; removed ${nice(d.edges_removed.join(", ") || "none")}</div></td></tr>` : "");
}

/* ---------- agent ---------- */
/* Markdown for agent replies. Everything is escaped first, so the model can never inject HTML; then a small block parser
   handles headings (a bold line, not a page heading), pipe tables, bullet and numbered lists, rules, code fences and inline code. */
function md(text) {
  const keep = [];
  const stash = (html) => `\u0000${keep.push(html) - 1}\u0000`;
  let src = esc(text).replace(/\r\n?/g, "\n")
    .replace(/```[^\n]*\n?([\s\S]*?)```/g, (m, code) => `\n${stash(`<pre class="mono">${code.replace(/\n$/, "")}</pre>`)}\n`);
  const inl = (x) => x.replace(/`([^`]+)`/g, (m, c) => stash(`<code>${c}</code>`))
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/__([^_]+)__/g, "<b>$1</b>")
    .replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?![*\w])/g, "$1<i>$2</i>");
  const cells = (row) => row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  const isAlign = (row) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(row);
  const lines = src.split("\n"); let out = "", list = null;
  const close = () => { if (list) { out += `</${list}>`; list = null; } };
  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i], t = ln.trim();
    if (!t) { close(); continue; }
    if (/^\u0000\d+\u0000$/.test(t)) { close(); out += t; continue; }
    let m;
    if ((m = t.match(/^#{1,6}\s+(.*?)\s*#*$/))) { close(); out += `<p class="md-h">${inl(m[1])}</p>`; continue; }
    if (/^([-*_])(\s*\1){2,}$/.test(t)) { close(); out += "<hr>"; continue; }
    if (t.startsWith("|") && i + 1 < lines.length && isAlign(lines[i + 1])) {
      close();
      const head = cells(t), align = cells(lines[i + 1]).map((c) => (/^-+:$/.test(c) ? "num" : ""));
      const body = []; i += 2;
      while (i < lines.length && lines[i].trim().startsWith("|")) body.push(cells(lines[i++]));
      i--;
      out += `<div class="md-table"><table><thead><tr>${head.map((c, k) => `<th class="${align[k] || ""}">${inl(c)}</th>`).join("")}</tr></thead><tbody>${body.map((r) => `<tr>${head.map((_, k) => `<td class="${align[k] || ""}">${inl(r[k] ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
      continue;
    }
    if ((m = ln.match(/^\s*[-*+]\s+(.*)/))) { if (list !== "ul") { close(); out += "<ul>"; list = "ul"; } out += `<li>${inl(m[1])}</li>`; continue; }
    if ((m = ln.match(/^\s*(\d+)[.)]\s+(.*)/))) { if (list !== "ol") { close(); out += `<ol${m[1] !== "1" ? ` start="${m[1]}"` : ""}>`; list = "ol"; } out += `<li>${inl(m[2])}</li>`; continue; }
    if (list && /^\s{2,}\S/.test(ln)) { out = out.replace(/<\/li>$/, ` ${inl(t)}</li>`); continue; }
    close(); out += `<p>${inl(t)}</p>`;
  }
  close();
  return out.replace(/\u0000(\d+)\u0000/g, (m, k) => keep[+k]).replace(/\u0000(\d+)\u0000/g, (m, k) => keep[+k]);
}
function callList(trace) {
  if (!trace?.length) return "";
  const names = [...new Set(trace.map((t) => t.tool))].join(", ");
  return `<details class="calls"><summary><span>${trace.length} graph tool call${trace.length === 1 ? "" : "s"}</span><span class="names">${esc(names)}</span></summary><ol>${trace.map((t) =>
    `<li><span class="sig">${esc(t.tool)}<span class="args">(${esc(Object.entries(t.args || {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", "))})</span></span>${t.summary ? `<span class="res">${esc(t.summary)}</span>` : ""}</li>`).join("")}</ol></details>`;
}
function renderChat() {
  const el = $("#chat");
  el.innerHTML = state.chat.map((m) => {
    if (m.role === "user") return `<div class="msg user"><div class="bubble">${esc(m.text)}</div></div>`;
    const thinking = m.model === "thinking", failed = m.model === "error";
    return `<div class="msg bot${failed ? " error" : ""}"><div class="avatar">${icon("mark")}</div><div class="body">
      <div class="who"><b>${esc(thinking ? state.meta.model || "agent" : m.model || "agent")}</b>${m.recorded ? `<span class="tag" title="Replayed from a recorded session; the tool calls and reply are exactly what the model produced">recorded</span>` : ""}</div>
      ${callList(m.trace)}
      ${thinking ? `<div class="typing" role="status"><i></i><i></i><i></i><span>Reading the graph</span></div>` : `<div class="md">${md(m.text)}</div>`}</div></div>`;
  }).join("") || `<div class="chat-empty"><b>Ask about this window</b><p>Every answer comes from graph tools, and each call is listed with its arguments. Anything the agent proposes waits for your approval.</p></div>`;
  if (!el.closest(".view").hidden) el.scrollTop = el.scrollHeight;
}
async function ask(q) {
  if (!q) return;
  state.chat.push({ role: "user", text: q }); state.chat.push({ role: "assistant", text: "…", model: "thinking" }); renderChat();
  try { const r = await post("/api/agent/ask", { session: state.session || "", question: q }); state.session = r.session; state.chat[state.chat.length - 1] = { role: "assistant", text: r.reply, trace: r.trace, model: r.model }; }
  catch (e) { state.chat[state.chat.length - 1] = { role: "assistant", text: `Request failed: ${e.message}`, model: "error" }; }
  renderChat(); pollActions();
}
$("#ask").addEventListener("submit", (ev) => { ev.preventDefault(); const q = $("#q").value.trim(); $("#q").value = ""; ask(q); });
$$(".chips button").forEach((b) => b.addEventListener("click", () => ask(b.dataset.q)));
$("#clear-chat").addEventListener("click", () => { state.chat = []; state.session = null; renderChat(); });

/* ---------- approvals ---------- */
const STATUS = { done: ["ok", "done"], proposed: ["wait", "awaiting approval"], running: ["run", "running"], failed: ["bad", "failed"], stale: ["bad", "stale evidence"], tampered: ["bad", "tampered"], rejected: ["muted", "rejected"] };
const statusPill = (s) => { const [k, t] = STATUS[s] || ["muted", s]; return `<span class="pill ${k}"><i></i>${esc(t)}</span>`; };
function actionCard(a) {
  const job = state.jobs[a.id];
  const status = job?.status === "running" ? "running" : a.status;
  return `<article class="card${a.status === "proposed" && !job ? " pending" : ""}"><div class="card-head">${statusPill(status)}<h4 class="card-title">${nice(a.title)}</h4>${ago(a.ts)}</div>
    <p class="card-text">${nice(a.rationale)}</p>
    ${a.type === "code_change" && a.body ? diffBlock(a.body) : ""}
    <div class="meta-line"><span class="tag">${esc((a.type || "action").replace(/_/g, " "))}</span><span class="idm">${esc(a.id)}${copyBtn(a.id, "action id")}</span><span class="idm"><span class="fp" tabindex="0" data-help="evidence-hash">${icon("fp", "ico")}evidence <b>${esc(a.hash)}</b></span>${copyBtn(a.hash, "evidence hash")}</span></div>
    ${a.status === "proposed" && !job ? `<div class="actions"><button class="btn primary small" data-approve="${a.id}" ${lock()}>Approve and run</button><button class="btn small" data-reject="${a.id}" ${lock()}>Reject</button>${state.meta.public ? `<span class="locked"><svg class="ico" aria-hidden="true"><use href="#i-lock"/></svg>Turned off in the read-only demo</span>` : ""}</div>` : ""}
    ${job?.log?.length ? `<div class="log-head">${job.status === "running" ? `<i class="live" aria-hidden="true"></i>Live log` : "Log"}</div><pre class="log" aria-live="polite">${esc(job.log.join("\n"))}</pre>` : ""}
    ${resultView(a.result || job?.result)}</article>`;
}
async function loadActions() {
  const r = await api("/api/actions");
  state.jobs = r.jobs || {}; state.actions = r.actions;
  const pending = r.actions.filter((a) => a.status === "proposed").length;
  $("#pending-count").textContent = pending; $("#pending-count").hidden = !pending;
  const waiting = r.actions.filter((a) => a.status === "proposed" || state.jobs[a.id]?.status === "running"), rest = r.actions.filter((a) => !waiting.includes(a));
  $("#actions").innerHTML = r.actions.length ? `
    <section class="group"><h3 class="group-title">Waiting for approval <span class="n">${waiting.length}</span></h3>${waiting.length ? `<div class="ledger">${waiting.map(actionCard).join("")}</div>` : empty("Nothing waiting", "Ask the agent what to fix, or propose from Findings and Experiments.", "check")}</section>
    ${rest.length ? `<section class="group"><h3 class="group-title">History <span class="n">${rest.length}</span></h3><div class="ledger">${rest.map(actionCard).join("")}</div></section>` : ""}`
    : empty("No proposals yet", "Ask the agent what to fix, or propose an experiment, fix or guardrail from Findings and Experiments. It will wait here for your approval.", "inbox");
  $$("#actions .log").forEach((el) => (el.scrollTop = el.scrollHeight));
  $$("[data-approve]").forEach((b) => b.addEventListener("click", async () => { b.disabled = true; try { await post(`/api/actions/${b.dataset.approve}/approve`); toast("Running. The live log appears on the card as it goes.", "ok"); } catch (e) { toast(e.message, "bad"); } loadActions(); }));
  $$("[data-reject]").forEach((b) => b.addEventListener("click", async () => { await post(`/api/actions/${b.dataset.reject}/reject`); loadActions(); }));
}
function ghRef(url) { const m = String(url).match(/github\.com\/([^/]+\/[^/]+)\/(pull|issues)\/(\d+)/); return m ? `${m[1]}#${m[3]}` : url; }
const extLink = (url, kind) => `<a class="ext" href="${esc(url)}" target="_blank" rel="noopener">${esc(kind)} ${esc(ghRef(url))}${icon("ext")}</a>`;
function resultView(r) {
  if (!r) return "";
  if (r.verdict) {
    return `<div class="result"><div class="row">${verdict(r.verdict)}<span class="mono hint">${esc(r.id || "")}</span></div>`
      + (r.gates ? `<ul class="gates">${r.gates.map((g) => gateItem(g.passed, g.name, g.detail)).join("")}</ul>` : "")
      + (r.lesson ? `<div>${nice(r.lesson)}</div>` : "") + (r.next_owner ? `<div class="hint">Next owner: ${nice(r.next_owner)}</div>` : "") + `</div>`;
  }
  if (r.opened) return `<div class="result"><div class="row">${extLink(r.opened, "Draft PR")}<span class="hint">${r.files.length} files</span></div><div class="files">${r.files.map((f) => `<span class="tag">${esc(f)}</span>`).join("")}</div></div>`;
  if (r.filed) return `<div class="result"><div class="row">${extLink(r.filed, "Issue")}<span class="hint">filed with the evidence</span></div></div>`;
  if (r.written && r.files) return `<div class="result"><div>Wrote <span class="mono">${esc(r.written)}</span>: ${r.files.map((f) => `<span class="mono">${esc(f)}</span>`).join(", ")}. <span class="hint">${esc(r.note || "")}</span></div></div>`;
  if (r.written && r.title) return `<div class="result"><div>Wrote <span class="mono">${esc(r.written)}</span>. <span class="hint">${esc(r.note || "")}</span></div></div>`;
  if (r.written) return `<div class="result"><div>Wrote <span class="mono">${esc(r.written)}</span> (${esc(r.timeout)} timeout, bounded retries).</div></div>`;
  if (r.error) return `<div class="result error">${esc(r.error)}</div>`;
  if (r.why !== undefined) return `<div class="result"><div class="hint">Rejected${r.why ? `: ${esc(r.why)}` : " by a reviewer"}.</div></div>`;
  return `<pre class="log">${esc(JSON.stringify(r, null, 1))}</pre>`;
}
async function pollActions() {
  try { const r = await api("/api/actions"); const pending = r.actions.filter((a) => a.status === "proposed").length; $("#pending-count").textContent = pending; $("#pending-count").hidden = !pending;
    const running = Object.values(r.jobs || {}).some((j) => j.status === "running");
    if (!$("#view-approvals").hidden) await loadActions();
    if (running) setTimeout(pollActions, 2500); else if (Object.keys(state.jobs).length && !running) { state.jobs = r.jobs; const w = await api("/api/windows"); if (w.windows.length !== state.windows.length) { state.windows = w.windows; const sel = $("#window"); sel.innerHTML = windowOptions(w.windows); sel.value = state.window; for (const id of ["cmp-a", "cmp-b"]) { const c = $(`#${id}`), v = c.value; c.innerHTML = w.windows.map(compareOption).join(""); c.value = v; } } }
  } catch (e) {}
}

/* ---------- graph view ---------- */
const EXAMPLES = (w) => ({
  "Critical-path owners (CRISP bottom-up)": `MATCH (t:Trace {window:'${w}'}) WITH collect(t) AS ts, sum(t.duration_ms) AS total UNWIND ts AS t\nMATCH (s:Span {critical:true})-[:OF]->(t)\nWITH s.service AS service, s.op AS op, sum(s.self_us) AS owned_us, total\nRETURN service, op, round(1000.0*owned_us/1000/total)/10 AS share_pct ORDER BY share_pct DESC`,
  "Single points of failure (betweenness)": `CALL algo.betweenness({nodeLabels:['Service'], relationshipTypes:['CALLS']}) YIELD node, score\nRETURN node.name, score ORDER BY score DESC`,
  "Blast radius of stopping route": `MATCH path = (r:Service)-[:CALLS* {window:'${w}'}]->(t:Service {name:'route'})\nRETURN [n IN nodes(path) | n.name] AS path`,
  "Prediction vs outcome per experiment": `MATCH (e:Experiment)-[p:PREDICTED_IMPACT]->(o)<-[a:ACTUAL_IMPACT]-(e)\nRETURN e.target, o.name, p.outcome, a.fail_rate, a.failed_fast, e.verdict`,
  "Data stores reachable from the edge": `MATCH (store:Service)<-[c:CALLS {window:'${w}'}]-() WHERE c.proto STARTS WITH 'db:'\nMATCH p = (e:Service {name:'frontend'})-[:CALLS*1..6 {window:'${w}'}]->(store)\nRETURN store.name, [n IN nodes(p) | n.name] AS path, [r IN relationships(p) | r.proto] AS protocols`,
  "Silent retries": `MATCH (c1:Span {window:'${w}', error:true})-[:CHILD_OF]->(p)<-[:CHILD_OF]-(c2:Span)\nWHERE c1.op = c2.op AND c2.start_us > c1.start_us\nRETURN c1.service, c1.op, count(DISTINCT c1) AS retried`,
  "Approvals bound to evidence": `MATCH (a:Action) OPTIONAL MATCH (a)-[:EXECUTED_AS]->(x)\nRETURN a.id, a.type, a.status, left(a.hash, 12) AS evidence, x.id, coalesce(x.verdict, '') AS verdict ORDER BY a.ts DESC`,
});
const SCHEMA = {
  nodes: [
    { id: "Finding", x: 0.17, y: 0.10, sub: "kind, severity, detail" },
    { id: "Service", x: 0.17, y: 0.38, sub: "name", loop: "left" }, { id: "Operation", x: 0.44, y: 0.38, sub: "key, name, service", loop: "right" },
    { id: "Span", x: 0.44, y: 0.66, sub: "critical, self_us, peer, proto, code", loop: "left" }, { id: "Trace", x: 0.44, y: 0.90, sub: "id, root, duration_ms, status" },
    { id: "Window", x: 0.63, y: 0.86, sub: "id, label, traces" },
    { id: "Experiment", x: 0.63, y: 0.16, sub: "hypothesis, verdict, lesson" }, { id: "Fix", x: 0.92, y: 0.62, sub: "flags, verdict, gates" },
    { id: "Action", x: 0.92, y: 0.16, sub: "type, payload, hash, status" },
  ],
  edges: [
    ["Service", "Service", "CALLS {window}"], ["Service", "Operation", "EXPOSES"], ["Operation", "Operation", "INVOKES {window}"],
    ["Span", "Operation", "ON"], ["Span", "Span", "CHILD_OF"], ["Span", "Trace", "OF"], ["Trace", "Window", "IN"],
    ["Finding", "Service", "ABOUT"], ["Experiment", "Service", "TARGETS", 0.78], ["Experiment", "Operation", "PREDICTED / ACTUAL_IMPACT"],
    ["Experiment", "Window", "BASELINE / OBSERVED"], ["Fix", "Service", "TARGETS", 0.28], ["Fix", "Window", "BEFORE / AFTER"],
    ["Action", "Experiment", "EXECUTED_AS"], ["Action", "Fix", "EXECUTED_AS"],
  ],
};
function drawSchema(counts) {
  const svg = d3.select("#schema"); svg.selectAll("*").remove();
  const W = svg.node().clientWidth || 700, H = svg.node().clientHeight || 500; svg.attr("viewBox", `0 0 ${W} ${H}`);
  const pos = {}; SCHEMA.nodes.forEach((n) => (pos[n.id] = { x: Math.max(56, Math.min(W - 56, n.x * W)), y: n.y * H }));
  svg.append("defs").append("marker").attr("id", "sarrow").attr("viewBox", "0 -4 8 8").attr("refX", 8).attr("markerWidth", 8).attr("markerHeight", 8).attr("markerUnits", "userSpaceOnUse").attr("orient", "auto")
    .append("path").attr("d", "M0,-4L8,0L0,4").style("fill", V("--fg-3"));
  const g = svg.append("g");
  for (const [a, b, rel, at = 0.5] of SCHEMA.edges) {
    const A = pos[a], B = pos[b];
    if (a === b) {
      const side = SCHEMA.nodes.find((n) => n.id === a).loop || "left";
      let d, lx, ly, anchor = "middle";
      if (side === "left") { d = `M${A.x - 48},${A.y - 10} C${A.x - 108},${A.y - 42} ${A.x - 108},${A.y + 26} ${A.x - 48},${A.y + 10}`; lx = Math.max(4, A.x - 104); ly = A.y + 42; anchor = "start"; }
      else if (side === "top") { d = `M${A.x - 14},${A.y - 18} C${A.x - 50},${A.y - 70} ${A.x + 50},${A.y - 70} ${A.x + 14},${A.y - 18}`; lx = A.x; ly = A.y - 62; }
      else { d = `M${A.x + 48},${A.y - 10} C${A.x + 108},${A.y - 42} ${A.x + 108},${A.y + 26} ${A.x + 48},${A.y + 10}`; lx = A.x + 80; ly = A.y + 40; }
      g.append("path").attr("d", d).style("fill", "none").style("stroke", V("--fg-3")).attr("stroke-opacity", 0.8).attr("stroke-width", 1.4).attr("marker-end", "url(#sarrow)");
      g.append("text").attr("class", "rel").attr("x", lx).attr("y", ly).attr("text-anchor", anchor).text(rel);
      continue;
    }
    const dx = B.x - A.x, dy = B.y - A.y, L = Math.hypot(dx, dy), ux = dx / L, uy = dy / L;
    const x1 = A.x + ux * 50, y1 = A.y + uy * 19, x2 = B.x - ux * 54, y2 = B.y - uy * 21;
    g.append("line").attr("x1", x1).attr("y1", y1).attr("x2", x2).attr("y2", y2).style("stroke", V("--fg-3")).attr("stroke-opacity", 0.8).attr("stroke-width", 1.4).attr("marker-end", "url(#sarrow)");
    const mx = x1 + (x2 - x1) * at, my = y1 + (y2 - y1) * at;
    const t = g.append("text").attr("class", "rel").attr("x", mx).attr("y", my - 5).attr("text-anchor", "middle").text(rel);
    const bb = t.node().getBBox();
    g.insert("rect", () => t.node()).attr("x", bb.x - 3).attr("y", bb.y - 1).attr("width", bb.width + 6).attr("height", bb.height + 2).style("fill", V("--panel"));
  }
  const node = svg.append("g").selectAll("g").data(SCHEMA.nodes).join("g").attr("transform", (d) => `translate(${pos[d.id].x},${pos[d.id].y})`);
  node.append("rect").attr("x", -50).attr("y", -19).attr("width", 100).attr("height", 38).attr("rx", 6).style("fill", V("--raised"))
    .style("stroke", (d) => ["Span", "Experiment", "Fix", "Action"].includes(d.id) ? V("--fg-2") : V("--fg-3")).attr("stroke-width", (d) => ["Span", "Experiment", "Fix", "Action"].includes(d.id) ? 1.6 : 1.2);
  node.append("text").attr("text-anchor", "middle").attr("dy", "-2").text((d) => `:${d.id}`);
  node.append("text").attr("class", "sub").attr("text-anchor", "middle").attr("dy", "12").text((d) => counts[d.id] != null ? `${counts[d.id].toLocaleString()} nodes` : "");
  node.append("title").text((d) => `${d.id} {${d.sub}}`);
}
async function runQuery() {
  const btn = $("#run"), label = $("#run-label"), stat = $("#qstat"), err = $("#qerror"), wrap = $(".result-wrap"), empty = $("#qempty");
  btn.disabled = true; label.textContent = "Running…"; stat.textContent = "";
  const t0 = performance.now();
  let r;
  try { r = await post("/api/query", { cypher: $("#cypher").value }); }
  catch (e) { r = { error: e.message }; }
  const ms = Math.max(1, Math.round(performance.now() - t0));
  btn.disabled = false; label.textContent = "Run query";
  if (r.error) {
    err.textContent = r.error; err.hidden = false; wrap.hidden = true; empty.hidden = true;
    $("#qresult thead").innerHTML = ""; $("#qresult tbody").innerHTML = ""; stat.textContent = "query failed";
    $("#results-panel").scrollIntoView({ block: "nearest", behavior: "smooth" }); return;
  }
  err.hidden = true;
  const isNum = (v) => typeof v === "number";
  const numeric = r.columns.map((_, i) => r.rows.length > 0 && r.rows.every((row) => row[i] == null || isNum(row[i])));
  $("#qresult thead").innerHTML = `<tr>${r.columns.map((c, i) => `<th class="${numeric[i] ? "num" : ""}">${esc(c)}</th>`).join("")}</tr>`;
  $("#qresult tbody").innerHTML = r.rows.map((row) => `<tr>${row.map((v, i) => { const s = v == null ? "" : typeof v === "object" ? JSON.stringify(v) : isNum(v) ? (Number.isInteger(v) ? v.toLocaleString() : v.toFixed(2)) : v; return `<td class="${numeric[i] ? "num" : ""}" title="${esc(typeof v === "object" ? JSON.stringify(v) : v)}">${esc(s)}</td>`; }).join("")}</tr>`).join("");
  const n = r.rows.length;
  stat.textContent = `${n} row${n === 1 ? "" : "s"}${n >= 25 ? " (limit)" : ""} · ${ms} ms`;
  wrap.hidden = n === 0; empty.hidden = n > 0; empty.textContent = n === 0 ? "The query ran and returned no rows." : "";
  $("#results-panel").scrollIntoView({ block: "nearest", behavior: "smooth" });
}
async function loadGraphView() {
  const m = await api("/api/model"); $("#model").textContent = m.text;
  try {
    const c = await post("/api/query", { cypher: "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*)" });
    const counts = {}; (c.rows || []).forEach(([l, n]) => (counts[l] = n));
    state.counts = counts;
    drawSchema(counts);
    $("#model-counts").innerHTML = Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([l, n]) => `<span class="m"><b>${n.toLocaleString()}</b> ${esc(l)}</span>`).join("");
  } catch (e) { drawSchema({}); }
  const ex = EXAMPLES(state.window); const sel = $("#examples");
  const prev = sel.value;
  sel.innerHTML = Object.keys(ex).map((k) => `<option>${esc(k)}</option>`).join("");
  if (prev && ex[prev]) sel.value = prev;
  $("#cypher").value = ex[sel.value];
  if (state.pendingCypher) { $("#cypher").value = state.pendingCypher; state.pendingCypher = null; }
  sel.onchange = () => { $("#cypher").value = ex[sel.value]; runQuery(); };
  $("#run").onclick = runQuery;
  $("#cypher").onkeydown = (ev) => { if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); runQuery(); } };
  runQuery();
}

/* ======================================================================
   Help for newcomers: glossary tooltips, the Start here card and the tour.
   All of the wording a first-time visitor reads lives in HELP and TOUR.
   ====================================================================== */
const HELP = {
  "window": "A window is one batch of traces captured together, such as the baseline or the run right after a fix.",
  "critical-path": "The critical path is the chain of calls a request actually waited on; speeding up anything off it does not make the request faster.",
  "self-time": "Self time is the part of a span spent in that service itself, not waiting on the calls it made.",
  "blast-radius": "Blast radius is the share of requests that would fail if this service stopped.",
  "gate": "A gate is a pass-or-fail check on the new measurements; a fix is kept only if every gate passes.",
  "evidence-hash": "The evidence hash fingerprints the data behind a proposal; if that data changes before approval, the run is refused.",
  "read-graph": "Thicker edges carry more calls per request. Orange marks the services that own latency, pink edges carry errors, blue outlines are data stores.",
  "owners-method": "Each operation is credited only with the time the request spent waiting on it (CRISP, bottom-up); hung requests are left out.",
  "guardrails": "Timeouts are set at three times the hop's p95; a guardrail is high priority when an experiment showed that hop hangs.",
  "planner": "Priority is criticality times safety, lowered for targets tested recently; the experiment ledger is the planner's memory.",
  "remedy-contention": "Contention: requests wait their turn for the same lock, connection or worker, so the wait grows with load.",
  "remedy-serialized_fanout": "Serialized fan-out: one request makes many calls but only a few run at once, so they queue behind each other.",
  "remedy-per_call_cost": "Per-call cost: a single call is slow on its own; the remedy is to make each call cheaper, not to make fewer of them.",
  "remedy-hang": "Hang: a call waits far past its normal time because nothing times it out.",
  "remedy-retry_storm": "Retry storm: failed calls are retried so often that the retries themselves add load and latency.",
};
const TOUR = [
  { view: "findings", target: [".window-field", "#strip"], atTop: true,
    title: "Pick a window, read its vitals",
    body: "A window is one batch of traces, such as the baseline or the run after a fix. These tiles are its vitals: typical and slowest latency, failures, and who owns the time." },
  { view: "findings", target: ["#findings-list"], up: ".panel",
    title: "What the graph found",
    body: "Problems in this window, highest severity first. Every line is a Cypher query over the trace graph, so each one can be checked." },
  { view: "trace", target: ["#waterfall"], up: ".panel", clip: 430,
    title: "The slowest request, span by span",
    body: "Each bar is one call, laid out in time. Orange bars are the critical path: the spans this request actually waited on." },
  { view: "experiments", target: ["#planner"], up: ".grid",
    title: "Break things on purpose, then fix them",
    body: "What to break next and what to fix next, ranked by the graph. Further down is the ledger of every experiment and fix already tried." },
  { view: "approvals", target: ["#actions > :first-child"],
    title: "Nothing runs without a click here",
    body: "Every proposal waits for a person. Approval is bound to the evidence hash: if the data changed since the proposal, the run is refused.",
    publicBody: "On this public demo, proposing and approving are turned off. The ledger shows runs that were recorded earlier, with their evidence hashes." },
  { view: "agent", target: ["#ask"],
    title: "Ask in plain words",
    body: "Pick a suggested question or type your own. The agent only reads the graph and proposes; every tool call it makes is listed." },
  { view: "graph", target: ["#view-graph .g-split"], clip: 500,
    title: "The data underneath",
    body: "The schema of the trace graph and a read-only Cypher console. Choose an example query and run it to see the rows." },
];

/* storage can be missing or throw (private windows, blocked site data); the console works the same without it */
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
};

/* glossary tooltips: hover or focus shows, a tap pins, Esc or a click elsewhere closes */
const help = (function () {
  const box = $("#help"); let owner = null, pinned = false;
  const place = (el) => {
    box.style.left = "0px"; box.style.top = "0px"; box.hidden = false;
    const r = el.getBoundingClientRect(), w = box.offsetWidth, h = box.offsetHeight, vw = document.documentElement.clientWidth;
    const x = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), vw - w - 8);
    let y = r.bottom + 8; if (y + h > innerHeight - 8) y = r.top - h - 8;
    box.style.left = `${x}px`; box.style.top = `${Math.max(8, y)}px`;
  };
  const open = (el) => { const t = HELP[el.dataset.help]; if (!t) return; if (owner && owner !== el) owner.removeAttribute("aria-describedby"); owner = el; box.textContent = t; el.setAttribute("aria-describedby", "help"); place(el); };
  const close = () => { if (owner) owner.removeAttribute("aria-describedby"); owner = null; pinned = false; box.hidden = true; };
  document.addEventListener("mouseover", (e) => { const el = e.target.closest?.("[data-help]"); if (el && el !== owner) { pinned = false; open(el); } });
  document.addEventListener("mouseout", (e) => { const el = e.target.closest?.("[data-help]"); if (el && el === owner && !pinned && !el.contains(e.relatedTarget) && document.activeElement !== el) close(); });
  document.addEventListener("focusin", (e) => { const el = e.target.closest?.("[data-help]"); if (el) open(el); });
  document.addEventListener("focusout", (e) => { if (e.target.closest?.("[data-help]") === owner && !pinned) close(); });
  document.addEventListener("click", (e) => {
    const el = e.target.closest?.("[data-help]");
    if (el) { e.preventDefault(); if (owner === el && pinned) close(); else { open(el); pinned = true; } }
    else if (owner) close();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && owner) close(); });
  addEventListener("scroll", () => { if (owner) place(owner); }, { passive: true });
  return { close };
})();

/* Start here card: shown until dismissed */
(function starter() {
  const card = $("#start-here");
  if (store.get("fl-start") !== "dismissed") card.hidden = false;
  $("#start-dismiss").addEventListener("click", () => { card.hidden = true; store.set("fl-start", "dismissed"); toast("Hidden. The ? button in the top bar replays the tour at any time."); });
})();

/* the tour: a spotlight on one part of the console per step, with a short card */
const tour = { open: false, i: 0, root: null, spot: null, card: null, from: "findings", focus: null, raf: 0, key: "", seen: false };
const currentView = () => ($$(".view").find((v) => !v.hidden)?.id || "view-findings").replace("view-", "");
const docked = () => matchMedia("(max-width: 860px)").matches;
function tourBuild() {
  const root = document.createElement("div");
  root.className = "tour"; root.hidden = true;
  root.innerHTML = `<div class="tour-spot" aria-hidden="true"></div>
    <div class="tour-card" role="dialog" aria-modal="true" aria-labelledby="tour-title" aria-describedby="tour-body">
      <div class="tour-progress" aria-hidden="true">${TOUR.map(() => "<i></i>").join("")}</div>
      <p class="tour-count" id="tour-count"></p>
      <h2 id="tour-title"></h2>
      <p class="tour-body" id="tour-body"></p>
      <div class="tour-actions">
        <button type="button" class="btn ghost small" data-tour="skip">Skip tour</button>
        <button type="button" class="btn small" data-tour="back">Back</button>
        <button type="button" class="btn primary small" data-tour="next">Next</button>
      </div>
    </div>`;
  document.body.appendChild(root);
  root.addEventListener("click", (e) => {
    const b = e.target.closest("[data-tour]"); if (!b) return;
    const act = b.dataset.tour;
    if (act === "skip") tourEnd(); else if (act === "back") tourGo(tour.i - 1); else if (tour.i === TOUR.length - 1) tourEnd(); else tourGo(tour.i + 1);
  });
  Object.assign(tour, { root, spot: root.querySelector(".tour-spot"), card: root.querySelector(".tour-card") });
}
function tourStart(at = 0) {
  if (!tour.root) tourBuild();
  help.close();
  if (!tour.open) { tour.from = currentView(); tour.focus = document.activeElement; }
  tour.open = true; tour.root.hidden = false; document.body.classList.add("touring");
  /* views load asynchronously; without this the browser's scroll anchoring drags the page away from the spotlight */
  document.documentElement.style.overflowAnchor = "none";
  document.addEventListener("keydown", tourKeys, true);
  tourGo(at);
  cancelAnimationFrame(tour.raf); tour.raf = requestAnimationFrame(tourLoop);
}
function tourGo(i) {
  if (i < 0 || i >= TOUR.length) return;
  tour.i = i; const s = TOUR[i];
  if (currentView() !== s.view) { history.replaceState(null, "", `#${s.view}`); show(s.view); }
  $("#tour-count").textContent = `Step ${i + 1} of ${TOUR.length}`;
  $("#tour-title").textContent = s.title;
  $("#tour-body").textContent = state.meta.public && s.publicBody ? s.publicBody : s.body;
  $$(".tour-progress i", tour.card).forEach((el, k) => el.classList.toggle("on", k <= i));
  tour.card.querySelector('[data-tour="back"]').hidden = i === 0;
  const next = tour.card.querySelector('[data-tour="next"]');
  next.textContent = i === TOUR.length - 1 ? "Finish" : "Next";
  tour.key = ""; tour.settle = performance.now() + 1600;
  requestAnimationFrame(() => { tourScroll(s); next.focus({ preventScroll: true }); });
}
function tourEls(s) {
  const els = s.target.map((q) => { const el = $(q); return el && s.up ? el.closest(s.up) || el : el; }).filter((el) => el && el.getClientRects().length);
  return els.length ? els : [$(`#view-${s.view} .view-head`)].filter(Boolean);
}
function tourRect(s) {
  const rs = tourEls(s).map((el) => el.getBoundingClientRect()), pad = 6;
  if (!rs.length) return null;
  const top = Math.min(...rs.map((r) => r.top)), left = Math.min(...rs.map((r) => r.left));
  let bottom = Math.max(...rs.map((r) => r.bottom)); const right = Math.max(...rs.map((r) => r.right));
  if (s.clip) bottom = Math.min(bottom, top + s.clip);
  return { top: top - pad, left: left - pad, right: right + pad, bottom: bottom + pad };
}
function tourScroll(s, onlyIfMoved = false) {
  const r = tourRect(s);
  const want = s.atTop || !r ? 0 : Math.max(0, scrollY + r.top - (docked() ? 12 : $(".top").getBoundingClientRect().bottom + 16));
  if (onlyIfMoved && Math.abs(want - scrollY) < 24) return;
  window.scrollTo({ top: want, behavior: "instant" });
}
function tourLoop() {
  if (!tour.open) return;
  tour.raf = requestAnimationFrame(tourLoop);
  const s = TOUR[tour.i], vw = document.documentElement.clientWidth, vh = innerHeight, m = 12, dock = docked();
  /* while a step's view is still loading, keep its target in place even if the view scrolls itself */
  if (performance.now() < tour.settle) tourScroll(s, true);
  const card = tour.card, cw = card.offsetWidth, ch = card.offsetHeight;
  const r = tourRect(s) || { top: vh / 2, left: vw / 2, right: vw / 2, bottom: vh / 2 };
  /* keep the spotlight inside the visible area, and above the docked card on a phone */
  const maxB = dock ? vh - ch - 2 * m : vh - 4;
  const t = Math.max(4, Math.min(r.top, maxB)), b = Math.max(t, Math.min(maxB, r.bottom)), l = Math.max(4, r.left), rr = Math.max(l, Math.min(vw - 4, r.right));
  let x = null, y = null;
  if (!dock) {
    const fitX = Math.min(Math.max(m, l), vw - cw - m);
    if (b + m + ch <= vh - m) { x = fitX; y = b + m; }
    else if (t - m - ch >= m) { x = fitX; y = t - m - ch; }
    else if (rr + m + cw <= vw - m) { x = rr + m; y = Math.min(Math.max(m, t), vh - ch - m); }
    else if (l - m - cw >= m) { x = l - m - cw; y = Math.min(Math.max(m, t), vh - ch - m); }
    else { x = Math.max(m, Math.min(rr, vw) - cw - 2 * m); y = Math.max(m, Math.min(b, vh) - ch - 2 * m); }
  }
  const key = [t, l, rr, b, x, y, dock].map((v) => (typeof v === "number" ? Math.round(v) : v)).join();
  if (key === tour.key) return;
  tour.key = key;
  Object.assign(tour.spot.style, { top: `${t}px`, left: `${l}px`, width: `${rr - l}px`, height: `${b - t}px` });
  card.style.left = dock ? "" : `${x}px`; card.style.top = dock ? "" : `${y}px`;
}
function tourEnd(restore = true) {
  if (!tour.open) return;
  tour.open = false; tour.seen = true; cancelAnimationFrame(tour.raf);
  tour.root.hidden = true; document.body.classList.remove("touring"); document.documentElement.style.overflowAnchor = "";
  document.removeEventListener("keydown", tourKeys, true);
  store.set("fl-tour", "done");
  if (restore && currentView() !== tour.from) { history.replaceState(null, "", `#${tour.from}`); show(tour.from); }
  window.scrollTo({ top: 0 });
  const back = tour.focus && tour.focus !== document.body && document.contains(tour.focus) ? tour.focus : $("#tour-btn");
  back.focus({ preventScroll: true });
}
function tourKeys(e) {
  if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); tourEnd(); }
  else if (e.key === "ArrowRight") { e.preventDefault(); if (tour.i === TOUR.length - 1) tourEnd(); else tourGo(tour.i + 1); }
  else if (e.key === "ArrowLeft") { e.preventDefault(); tourGo(tour.i - 1); }
  else if (e.key === "Tab") {
    const f = $$("button", tour.card).filter((b) => !b.hidden && !b.disabled);
    const k = f.indexOf(document.activeElement);
    e.preventDefault(); f[(k + (e.shiftKey ? -1 : 1) + f.length) % f.length].focus();
  }
}
function tourAuto() { if (!tour.seen && store.get("fl-tour") !== "done") tourStart(); }
document.addEventListener("click", (e) => { if (e.target.closest?.("[data-tour-start]")) { e.preventDefault(); tourStart(); } });
/* leaving through the browser's back button ends the tour instead of stranding it */
window.addEventListener("hashchange", () => { if (tour.open && route().view !== TOUR[tour.i].view) tourEnd(false); });

/* ======================================================================
   SRE workflow: baseline status, next steps, runbook incidents, copyable ids,
   keyboard shortcuts, sortable tables, density, Markdown export.
   ====================================================================== */
const byCreated = () => [...state.windows].sort((a, b) => a.created - b.created);
const winLabel = (id) => { const x = state.windows.find((w) => w.id === id); return x ? x.label : id; };
const winById = (id) => state.windows.find((w) => w.id === id) || null;
const isExperiment = (id) => winById(id)?.kind === "experiment";
/* each window names its own baseline: the steady-state window captured before it (from /api/windows) */
const baselineOf = (id) => winById(winById(id)?.baseline);
function setCompareDefaults() {
  const base = baselineOf(state.window);
  if (base) { $("#cmp-a").value = base.id; $("#cmp-b").value = state.window; }
}
/* lower is better for every measure shown with a delta: latency, failure rate, time owned */
function deltaChip(x, y, points = false, cls = "delta") {
  if (x == null || y == null) return "";
  if (points) { const dp = Math.round((y - x) * 1000) / 10; return `<span class="${cls} ${dp > 0.5 ? "up" : dp < -0.5 ? "down" : "flat"}">${dp > 0 ? "+" : dp < 0 ? "−" : "±"}${fmt(Math.abs(dp), Math.abs(dp) < 10 && dp % 1 ? 1 : 0)} pts</span>`; }
  if (!x) return `<span class="${cls} ${y > 0 ? "up" : "flat"}">${y > 0 ? "new" : "±0%"}</span>`;
  const r = (y - x) / x, c = Math.abs(r) < 0.02 ? "flat" : r > 0 ? "up" : "down";
  return `<span class="${cls} ${c}">${y / x >= 10 ? `×${fmt(y / x, 0)}` : `${r > 0 ? "+" : r < 0 ? "−" : "±"}${Math.round(Math.abs(r) * 100)}%`}</span>`;
}
const clock = (us) => us ? new Date(us / 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
function spanOf(a, b) {
  if (!a || !b) return "";
  const secs = Math.max(1, Math.round((b - a) / 1e6));
  return `${new Date(a / 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" })} ${clock(a)} to ${clock(b)} (${secs < 120 ? `${secs} s` : `${Math.round(secs / 60)} min`})`;
}
const span = (id) => { const w = winById(id); return w ? spanOf(w.start_us, w.end_us) : ""; };

/* ---------- status banner and KPI deltas ---------- */
function pctChip(p, cls = "delta") {
  if (p == null) return "";
  const c = Math.abs(p) < 2 ? "flat" : p > 0 ? "up" : "down";
  return `<span class="${cls} ${c}">${p >= 900 ? `×${fmt(1 + p / 100, 0)}` : `${p > 0 ? "+" : p < 0 ? "−" : "±"}${Math.round(Math.abs(p))}%`}</span>`;
}
function ptsChip(p, cls = "delta") {
  if (p == null) return "";
  const c = Math.abs(p) < 0.5 ? "flat" : p > 0 ? "up" : "down", a = Math.abs(p);
  return `<span class="${cls} ${c}">${p > 0 ? "+" : p < 0 ? "−" : "±"}${fmt(a, a < 10 && a % 1 ? 1 : 0)} pts</span>`;
}
/* the banner and the KPI deltas come from /api/status, which uses the incident thresholds, so they never disagree with the incident list */
async function loadStatus() {
  const w = state.window, me = winById(w);
  let s, d = null;
  try {
    [s, d] = await Promise.all([api(`/api/status?window=${encodeURIComponent(w)}`),
      me?.baseline ? api(`/api/compare?a=${encodeURIComponent(me.baseline)}&b=${encodeURIComponent(w)}`).catch(() => null) : null]);
  } catch (e) { return; }
  if (state.window !== w) return;
  state.status = { ...s, kind: s.status, w, mover: d?.owners?.[0] || null };
  const dl = s.deltas, vs = s.baseline ? `against ${s.baseline_label} (${s.baseline})` : "";
  $("#d-p50").innerHTML = dl ? pctChip(dl.p50_pct, "kdelta-chip") : ""; $("#d-p50").title = vs;
  $("#d-p95").innerHTML = dl ? pctChip(dl.p95_pct, "kdelta-chip") : ""; $("#d-p95").title = vs;
  $("#d-fail").innerHTML = dl ? ptsChip(dl.fail_rate_pts, "kdelta-chip") : ""; $("#d-fail").title = vs;
  renderStatus(state.status);
}
const STATUS_TEXT = { ok: ["ok", "Healthy"], degraded: ["wait", "Degraded"], failing: ["bad", "Failing"], baseline: ["muted", "Baseline"] };
function statusSentence(st) {
  const op = esc(st.endpoint || state.overview?.health[0]?.op || "This endpoint"), lbl = `<b>${esc(st.label || winLabel(st.w))}</b>`;
  if (st.kind === "baseline") return `${op} in ${lbl} is the first window. Later windows are compared against it.`;
  const why = (st.reasons || []).map((r) => nice(r).replace(new RegExp(`\\b${(st.baseline_label || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`), `<b>${esc(st.baseline_label)}</b>`)).join("; ");
  return `${op} in ${lbl}${st.kind === "ok" ? "" : ` against <b>${esc(st.baseline_label)}</b>`}: ${why}.`;
}
function sloPill(slo) {
  if (!slo || !state.meta.slo) return "";
  const t = state.meta.slo, ok = slo.p95_ok && slo.fail_ok;
  const detail = `SLO for ${t.endpoint || "the endpoint"}: p95 within ${fmt(t.p95_ms)} ms (${slo.p95_ok ? "met" : "breached"}), failures within ${pct(t.fail_rate)} (${slo.fail_ok ? "met" : "breached"})`;
  return `<span class="pill ${ok ? "ok" : "bad"}" title="${esc(detail)}"><i></i>${ok ? "within SLO" : "breaching SLO"}</span>`;
}
function renderStatus(st) {
  const [k, word] = STATUS_TEXT[st.kind] || ["muted", st.kind];
  const exp = isExperiment(st.w), dl = st.deltas, facts = [];
  if (dl) facts.push(`<span class="sf"><span class="k">p50</span>${pctChip(dl.p50_pct)}</span><span class="sf"><span class="k">p95</span>${pctChip(dl.p95_pct)}</span><span class="sf"><span class="k">failed</span>${ptsChip(dl.fail_rate_pts)}</span>`);
  if (st.mover && st.mover.per_request_ms[0] !== st.mover.per_request_ms[1]) facts.push(`<span class="sf mover"><span class="k">biggest mover</span>${nice(st.mover.owner)} <span class="mono">${fmt(st.mover.per_request_ms[0])} → ${fmt(st.mover.per_request_ms[1])} ms</span></span>`);
  $("#status").className = `statusbar is-${st.kind}`;
  $("#status").innerHTML = `<span class="st-pills"><span class="pill ${k} st-pill"><i></i>${word}</span>${sloPill(st.slo)}</span>
    <div class="st-main"><p class="st-line">${statusSentence(st)}${exp ? ` <span class="tag">fault experiment</span>` : winById(st.w)?.kind === "rejected" ? ` <span class="tag">rejected fix</span>` : ""}</p>
      <div class="st-facts">${facts.join("")}<span class="sf idm"><span class="k">window</span><span class="mono">${esc(st.w)}</span>${copyBtn(st.w, "window id")}</span>${span(st.w) ? `<span class="sf"><span class="k">traces</span>${esc(span(st.w))}</span>` : ""}</div></div>
    <div class="st-actions">${st.baseline ? `<a class="btn small" href="#experiments" data-compare-baseline>Compare with ${esc(st.baseline_label)}</a>` : ""}</div>`;
}
document.addEventListener("click", (e) => { if (e.target.closest?.("[data-compare-baseline]")) { e.preventDefault(); setCompareDefaults(); go("experiments"); setTimeout(() => $("#compare").closest(".panel").scrollIntoView({ block: "start" }), 300); } });

/* ---------- next steps on Findings: the planner's ranked actions ---------- */
const ACTION = {
  issue: ["incident", "bad", "Propose incident issue"], fix: ["fix", "wait", "Propose fix"], guardrail: ["guardrail", "wait", "Propose guardrail"],
  experiment: ["experiment", "muted", "Propose pause"], advice: ["advice", "muted", ""],
};
const subjectOf = (pl) => !pl ? null : pl.type === "guardrail" ? pl.hop : pl.type === "issue" ? pl.window : pl.type === "experiment" ? pl.target : pl.type === "fix" ? [].concat(pl.flags).join(" ") : pl.subject;
async function loadNextSteps() {
  const w = state.window;
  let p, acts;
  try { [p, acts] = await Promise.all([api(`/api/planner?window=${encodeURIComponent(w)}`), api("/api/actions")]); }
  catch (e) { $("#next-steps").innerHTML = `<li class="empty-li">${empty("Could not load next steps", esc(e.message), "alert")}</li>`; return; }
  if (state.window !== w) return;
  state.planner = p; state.actions = acts.actions;
  const steps = [...(p.actions || [])].sort((a, b) => (a.priority ?? 9) - (b.priority ?? 9)).slice(0, 5);
  const pending = (pl) => { const sub = subjectOf(pl); return sub != null && state.actions.find((a) => a.status === "proposed" && a.type === pl.type && a.subject === sub); };
  $("#next-count").innerHTML = steps.length ? `<b>${steps.length}</b> step${steps.length === 1 ? "" : "s"}` : "";
  $("#next-steps").innerHTML = steps.map((st) => {
    const [kind, tone, verb] = ACTION[st.type] || [st.type, "muted", "Propose"];
    const rem = st.type === "advice" ? (p.remedies || []).find((r) => !r.actuator && String(st.title).includes(`${r.service} ${r.op}`)) : null;
    const act = !st.payload ? `<span class="no-act" title="No actuator in this environment: the change is made in code"><svg class="ico" aria-hidden="true"><use href="#i-lock"/></svg>code change</span>${rem ? readCodeBtn(rem.service, rem.op, true) : ""}<a class="btn small" href="#experiments">See remedies</a>`
      : pending(st.payload) ? `<span class="pill wait"><i></i>awaiting approval</span><a class="btn small" href="#approvals">Review in Approvals</a>`
      : `<button class="btn small${st.type === "issue" ? " primary" : ""}" type="button" data-next="${esc(JSON.stringify(st.payload))}" ${lock()}>${esc(verb || "Propose")}</button>`;
    const extra = st.type === "issue" && st.payload?.window && st.payload.window !== w ? `<p class="step-extra"><a class="linkish" href="#findings?w=${encodeURIComponent(st.payload.window)}">Open that window</a></p>` : "";
    return `<li class="step tone-${tone}"><span class="step-kind">${esc(kind)}</span>
      <div class="step-main"><b>${nice(cap(st.title))}</b><p>${nice(st.why || "")}</p>${extra}</div><div class="step-act">${act}</div></li>`;
  }).join("") || `<li class="empty-li">${empty("Nothing to do", "The planner has no action to propose for this window.", "check")}</li>`;
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest?.("[data-next]"); if (!b || b.disabled) return;
  b.disabled = true;
  try { const r = await post("/api/propose", JSON.parse(b.dataset.next)); proposed(r, `Proposed: ${r.title || r.action}. Approve it under Approvals.`); }
  catch (err) { toast(err.message, "bad"); b.disabled = false; }
  pollActions(); loadNextSteps();
});

/* ---------- incidents as runbook entries ---------- */
function incidentCard(i) {
  const failing = i.kind === "failures";
  const sg = i.suggested;
  const trace = i.worst_trace ? `<span class="idm"><span class="mono">${esc(i.worst_trace.id.slice(0, 16))}…</span>${copyBtn(i.worst_trace.id, "trace id")}</span> ${fmt(i.worst_trace.duration_ms)} ms · status ${esc(i.worst_trace.status || "none")} · <a class="linkish" href="#trace?w=${encodeURIComponent(i.window)}&t=${encodeURIComponent(i.worst_trace.id)}">Open waterfall</a>` : "";
  return `<article class="card runbook"><div class="card-head"><span class="pill ${failing ? "bad" : "wait"}"><i></i>${failing ? "failing" : "degraded"}</span><h5 class="card-title">${esc(i.endpoint)} <span class="muted">in</span> <span class="mono">${esc(i.label)}</span></h5><span class="span idm">${esc(i.window)}${copyBtn(i.window, "window id")}</span><span class="when">${i.traces} traces</span></div>
    <dl class="kv">
      <dt>Impact</dt><dd>${failing ? `<b>${pct(i.fail_rate[1])}</b> of requests failed <span class="from">(${pct(i.fail_rate[0])} before)</span>; ` : ""}p95 <span class="from">${fmt(i.p95_ms[0])} →</span> <b>${fmt(i.p95_ms[1])}</b> ms; p50 <span class="from">${fmt(i.p50_ms[0])} →</span> <b>${fmt(i.p50_ms[1])}</b> ms</dd>
      <dt>When</dt><dd>${esc(spanOf(i.started_us, i.ended_us) || span(i.window) || "not recorded")} <span class="from">· baseline ${esc(i.baseline_label || winLabel(i.baseline))} (${esc(i.baseline)})</span></dd>
      ${i.owner ? `<dt>What moved</dt><dd>${esc(label(i.owner))}: ${fmt(i.owner.baseline_per_request_ms)} → <b>${fmt(i.owner.per_request_ms)}</b> ms per request ${i.owner.code ? `· ${codeLink(i.owner.code)}` : ""}</dd>` : ""}
      ${i.hop ? `<dt>Where to look</dt><dd class="mono">${nice(i.hop)}</dd>` : ""}
      ${trace ? `<dt>Worst request</dt><dd>${trace}</dd>` : ""}
      <dt>Next step</dt><dd>${sg ? `<b>${nice(cap(sg.title))}</b>. ${nice(cap(sg.why || ""))}.` : "File the incident with this evidence."}</dd>
    </dl>
    <div class="card-foot"><span class="hint">Each becomes a proposal that waits in Approvals.</span>${sg?.payload && sg.type !== "issue" ? `<button class="btn small" type="button" data-next="${esc(JSON.stringify(sg.payload))}" ${lock()}>${esc((ACTION[sg.type] || [])[2] || "Propose")}</button>` : ""}<button class="btn small" data-propose-issue="${i.window}" ${lock()}>Propose incident issue</button></div></article>`;
}

/* ---------- copyable ids ---------- */
const copyBtn = (text, what) => `<button class="copy" type="button" data-copy="${esc(text)}" data-copy-what="${esc(what)}" aria-label="Copy ${esc(what)}" title="Copy ${esc(what)}"><svg class="ico" aria-hidden="true"><use href="#i-copy"/></svg></button>`;
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; }
  catch (e) {
    const ta = document.createElement("textarea"); ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0"; ta.style.top = "0";
    document.body.appendChild(ta); ta.select(); let ok = false; try { ok = document.execCommand("copy"); } catch (e2) {} ta.remove(); return ok;
  }
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest?.("[data-copy]"); if (!b) return;
  e.preventDefault(); e.stopPropagation();
  const ok = await copyText(b.dataset.copy);
  toast(ok ? `Copied the ${b.dataset.copyWhat}: ${b.dataset.copy.length > 40 ? b.dataset.copy.slice(0, 40) + "…" : b.dataset.copy}` : "The browser blocked the clipboard. Select the text and copy it instead.", ok ? "ok" : "bad");
  if (ok) { b.classList.add("done"); setTimeout(() => b.classList.remove("done"), 1200); }
});
async function copyLink() { const ok = await copyText(location.href); toast(ok ? "Copied a link to this view, with the window and any open trace." : "The browser blocked the clipboard. Copy the address bar instead.", ok ? "ok" : "bad"); }
$("#copy-link").addEventListener("click", copyLink);

/* ---------- density ---------- */
function setDensity(d) { document.documentElement.dataset.density = d; $("#density").setAttribute("aria-pressed", String(d === "compact")); store.set("fl-density", d); }
setDensity(store.get("fl-density") === "compact" ? "compact" : "comfortable");
$("#density").addEventListener("click", () => setDensity(document.documentElement.dataset.density === "compact" ? "comfortable" : "compact"));

/* ---------- sortable tables ---------- */
const SORTS = {};
function initSort(id) {
  const table = $(`#${id}`);
  $$("th[data-sort]", table).forEach((th) => {
    const col = th.cellIndex;
    th.innerHTML = `<button type="button" class="th-sort">${th.innerHTML}<i aria-hidden="true"></i></button>`;
    th.querySelector("button").addEventListener("click", () => {
      const cur = SORTS[id], first = th.dataset.sort === "num" ? "desc" : "asc";
      SORTS[id] = { col, type: th.dataset.sort, dir: cur && cur.col === col ? (cur.dir === "asc" ? "desc" : "asc") : first };
      applySort(id);
    });
  });
}
function applySort(id) {
  const s = SORTS[id], table = $(`#${id}`); if (!s || !table) return;
  $$("th[data-sort]", table).forEach((th) => th.setAttribute("aria-sort", th.cellIndex === s.col ? (s.dir === "asc" ? "ascending" : "descending") : "none"));
  const tb = table.tBodies[0], rows = [...tb.rows].filter((r) => r.cells.length > 1);
  const val = (r) => { const c = r.cells[s.col]; return c ? (c.dataset.sortValue ?? c.textContent.trim()) : ""; };
  const num = (v) => { const n = parseFloat(String(v).replace(/[^0-9.\-]/g, "")); return isNaN(n) ? Infinity : n; };
  rows.sort((a, b) => { const r = s.type === "num" ? num(val(a)) - num(val(b)) : val(a).localeCompare(val(b), undefined, { numeric: true }); return s.dir === "asc" ? r : -r; });
  rows.forEach((r) => tb.appendChild(r));
}
["owners", "guardrails", "planner"].forEach(initSort);

/* ---------- keyboard ---------- */
const shortcuts = $("#shortcuts");
function openShortcuts() { if (!shortcuts.open) shortcuts.showModal(); }
shortcuts.addEventListener("click", (e) => {
  if (e.target === shortcuts || e.target.closest("[data-close]") || e.target.closest("[data-tour-start]")) shortcuts.close();
});
$("#shortcuts-btn").addEventListener("click", openShortcuts);
function stepWindow(dir) {
  const ws = byCreated(), i = ws.findIndex((x) => x.id === state.window), n = ws[i + dir];
  if (!n) { toast(dir > 0 ? "This is the newest window." : "This is the oldest window."); return; }
  const sel = $("#window"); sel.value = n.id; sel.dispatchEvent(new Event("change"));
  toast(`Window: ${n.label} · ${n.id}`);
}
let gAt = 0;
document.addEventListener("keydown", (e) => {
  if (tour.open || e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented) return;
  const t = e.target;
  if (t.closest?.("input, textarea, select, [contenteditable=true]")) { if (e.key === "Escape" && t.blur) t.blur(); return; }
  if (shortcuts.open) return;
  const k = e.key;
  if (gAt && Date.now() - gAt < 1500) {
    gAt = 0; const v = { f: "findings", t: "trace", e: "experiments", a: "agent", p: "approvals", g: "graph" }[k];
    if (v) { e.preventDefault(); go(v); }
    return;
  }
  if (k === "g") { gAt = Date.now(); return; }
  if (k === "?") { e.preventDefault(); openShortcuts(); }
  else if (k === "/") { e.preventDefault(); if (route().view !== "agent") go("agent"); $("#q").focus(); }
  else if (k === "w") { e.preventDefault(); $("#window").focus(); }
  else if (k === "[" || k === "]") { e.preventDefault(); stepWindow(k === "]" ? 1 : -1); }
  else if (k === "y") { e.preventDefault(); copyLink(); }
  else if (k === "d") { e.preventDefault(); $("#density").click(); }
});

/* ---------- Markdown for incident docs and reviews ---------- */
const mdEsc = (x) => String(x ?? "").replace(/\|/g, "\\|").replace(/\n/g, " ");
const plain = (x) => String(x ?? "").replace(/ -> /g, " → ");
function mdReport() {
  const o = state.overview, h = o.health[0] || {}, st = state.status || {}, base = winById(st.baseline), bh = base?.health, dl = st.deltas;
  const L = [`# ${h.op || "Endpoint"} in ${winLabel(state.window)} (${state.window})`, "",
    `Status: **${(STATUS_TEXT[st.kind] || ["", st.kind || "unknown"])[1]}**${base ? ` against ${base.label} (${base.id})` : ""}${(st.reasons || []).length ? `: ${plain(st.reasons.join("; "))}` : ""}. Link: ${location.href}`, ""];
  if (span(state.window)) L.push(`Traces: ${span(state.window)}, ${h.traces} requests.`, "");
  const strip = (x) => x.replace(/<[^>]+>/g, "");
  const cmp = !!(bh && dl);
  L.push("| Measure | This window |" + (cmp ? ` ${base.label} | Change |` : ""), "|---|---:|" + (cmp ? "---:|---:|" : ""));
  const failNow = cmp ? bh.fail_rate + dl.fail_rate_pts / 100 : h.fail_rate, hung = cmp && Math.abs(failNow - h.fail_rate) > 0.01;
  const rows = [["p50", `${fmt(h.p50_ms)} ms`, cmp && `${fmt(bh.p50_ms)} ms`, cmp && strip(pctChip(dl.p50_pct))],
    ["p95", `${fmt(h.p95_ms)} ms`, cmp && `${fmt(bh.p95_ms)} ms`, cmp && strip(pctChip(dl.p95_pct))],
    [hung ? "Failed or hung" : "Failed", hung ? pct(failNow) : `${pct(h.fail_rate)} (${h.failed} of ${h.traces})`, cmp && pct(bh.fail_rate), cmp && strip(ptsChip(dl.fail_rate_pts))]];
  rows.forEach((r) => L.push(`| ${r[0]} | ${r[1]} |` + (cmp ? ` ${r[2]} | ${r[3]} |` : "")));
  L.push("", "## Who owns the latency", "", "| # | Owner | Share | ms / request | Code |", "|---:|---|---:|---:|---|");
  o.owners.slice(0, 6).forEach((x, i) => L.push(`| ${i + 1} | ${mdEsc(label(x))} | ${x.share_pct}% | ${fmt(x.per_request_ms)} | ${mdEsc(x.code ? (x.code.location || `${x.code.file}${x.code.function ? " " + x.code.function : ""}`) : "")} |`));
  L.push("", "## Findings", "");
  [...o.findings].sort((a, b) => (SEV[a.severity] ?? 3) - (SEV[b.severity] ?? 3)).forEach((f) => L.push(`- **${f.severity}** · ${f.kind.replace(/_/g, " ")}: ${plain(f.detail)}`));
  const gs = o.guardrails.filter((g) => g.priority === "high");
  if (gs.length) { L.push("", "## High-priority guardrails", ""); gs.forEach((g) => L.push(`- ${plain(g.hop)}: ${g.kind} ${plain(g.value)}. ${plain(g.evidence)}`)); }
  const steps = $$("#next-steps .step-main b").map((b) => b.textContent);
  if (steps.length) { L.push("", "## Next steps", ""); steps.forEach((x, i) => L.push(`${i + 1}. ${x}`)); }
  L.push("", "_Every number above is a query over the Faultline trace graph._");
  return L.join("\n");
}
function mdLedger() {
  const l = state.ledger || { experiments: [], fixes: [] }, inc = state.incidents || [];
  const L = ["# Reliability ledger", "", `Link: ${location.href}`, "", "## Incidents", ""];
  if (!inc.length) L.push("None against the current steady state.");
  inc.forEach((i) => L.push(`- **${i.kind}** · ${i.endpoint} in ${i.label} (${i.window}): failed ${pct(i.fail_rate[0])} → ${pct(i.fail_rate[1])}, p95 ${fmt(i.p95_ms[0])} → ${fmt(i.p95_ms[1])} ms against ${winLabel(i.baseline)}.`
    + (i.hop ? ` Where to look: ${plain(i.hop)}.` : "") + (i.worst_trace ? ` Worst request ${i.worst_trace.id} (${fmt(i.worst_trace.duration_ms)} ms).` : "")));
  L.push("", "## Experiments", "");
  if (!l.experiments.length) L.push("None yet.");
  l.experiments.forEach((e) => L.push(`### ${e.verdict}: pause ${e.target} (${when(e.ts)})`, "", `- Hypothesis: ${e.hypothesis}`, ...(e.parts || []).map((g) => `  - [${g.passed ? "x" : " "}] ${plain(g.text)}`),
    `- Observed: ${e.impact.map((x) => `${x.op}: ${pct(x.fail_rate)} failed, p50 ${fmt(x.p50_ms)} ms, ${x.failed_fast ? "failed fast" : "hung"}`).join("; ") || "nothing recorded"}`, `- Lesson: ${plain(e.lesson)}`, `- Windows: ${e.baseline} → ${e.observed}`, ""));
  L.push("## Fixes", "");
  if (!l.fixes.length) L.push("None yet.");
  l.fixes.forEach((f) => { L.push(`### ${f.verdict}: \`${f.flags}\` on ${f.service} (${when(f.ts)})`, ""); f.gates.forEach((g) => L.push(`- [${g.passed ? "x" : " "}] ${plain(g.text).replace(/>=/g, "≥")}`)); L.push(`- Next owner: ${f.next_owner || "none"}`, `- Windows: ${f.before} → ${f.after}`, ""); });
  return L.join("\n");
}
async function exportMd(build, what) {
  if (!state.overview) return;
  const text = build(), ok = await copyText(text);
  toast(ok ? `Copied the ${what} as Markdown (${text.split("\n").length} lines). Paste it into the incident or review doc.` : "The browser blocked the clipboard.", ok ? "ok" : "bad");
}
$("#export-report").addEventListener("click", () => exportMd(mdReport, "window report"));
$("#export-ledger").addEventListener("click", () => exportMd(mdLedger, "ledger"));

/* ---------- remedies: the graph names the change from evidence; the environment may or may not supply a knob ---------- */
const remedyName = (r) => /->/.test(r.op) ? nice(r.op) : `${esc(r.service)} ${esc(r.op)}`;
const REMEDY = { contention: "contention", serialized_fanout: "serialized fan-out", per_call_cost: "per-call cost", hang: "hang", retry_storm: "retry storm" };
const cap = (x) => { const t = String(x ?? "").trim().replace(/\.$/, ""); return t ? t[0].toUpperCase() + t.slice(1) : ""; };
/* evidence sentences start with a service name, which keeps its case */
const sentence = (x) => String(x ?? "").trim().replace(/\.$/, "");
function remedyPanel(p, l) {
  const rs = p.remedies || [], nf = p.next_fix;
  if (!rs.length && !nf) return empty("Nothing to fix", "The graph found no remedy with measured evidence in this window.", "check");
  const verified = new Set((l.fixes || []).filter((f) => f.verdict === "VERIFIED").map((f) => f.flags.split(" ")[0]));
  const pendingFix = (flags) => (state.actions || []).find((a) => a.status === "proposed" && a.type === "fix" && a.subject === flags);
  const main = nf ? { kind: nf.kind, owner: nf.targets_owner, name: nice(nf.targets_owner), share: nf.share_pct, ms: nf.per_request_ms, evidence: nf.evidence || nf.why, change: nf.change, actuator: { flags: nf.flags, service: nf.service, what: nf.why }, note: nf.note, previous: nf.previous_attempt, next: true }
    : { kind: rs[0].kind, owner: `${rs[0].service} ${rs[0].op}`, name: remedyName(rs[0]), share: rs[0].share_pct, ms: rs[0].per_request_ms, evidence: rs[0].evidence, change: rs[0].change, actuator: rs[0].actuator };
  const rest = rs.filter((r) => `${r.service} ${r.op}` !== main.owner);
  const tag = (kind) => `<span class="tag remedy-kind" tabindex="0" data-help="remedy-${esc(kind)}">${esc(REMEDY[kind] || kind || "remedy")}</span>`;
  const actState = (a, isMain, why) => {
    if (!a) return `<span class="no-act"><svg class="ico" aria-hidden="true"><use href="#i-lock"/></svg>No actuator in this environment</span>`;
    const flags = a.flags.join(" ");
    if (verified.has(a.flags[0]) && !(isMain && main.next)) return `<span class="pill ok" title="A fix with this knob already passed every gate"><i></i>applied, verified</span>`;
    if (pendingFix(flags)) return `<span class="pill wait"><i></i>awaiting approval</span>`;
    return isMain && main.next ? `<button class="btn primary small" id="propose-fix" ${lock()}>Propose this fix</button>`
      : `<button class="btn small" type="button" data-next="${esc(JSON.stringify({ type: "fix", flags, rationale: why }))}" ${lock()}>Propose fix</button>`;
  };
  const a = main.actuator;
  let html = `<div class="remedy-main">
    <div class="remedy-head">${tag(main.kind)}<span class="remedy-owner"><b>${main.name}</b> · ${main.share}% · ${fmt(main.ms)} ms per request</span></div>
    <p class="remedy-evidence">${nice(sentence(main.evidence))}.</p>
    <dl class="remedy-kv">
      ${main.change ? `<dt>Change</dt><dd>${nice(cap(main.change))}.</dd>` : ""}
      <dt>Actuator</dt><dd>${a ? `<div class="act-what"><span class="act-line"><code>${esc(a.flags.join(" "))}</code> on ${esc(a.service)}</span><span class="hint">${esc(cap(a.what || ""))}</span></div><div class="act-do">${actState(a, true, `targets ${main.owner} (${main.share}%)`)}</div>`
        : `<div class="act-what">${actState(null)}<span class="hint">The graph names the change; making it here is a code change.</span></div>`}</dd>
    </dl>
    ${main.note ? `<p class="hint">${nice(cap(main.note))}.</p>` : ""}${main.previous ? `<p class="hint">Last attempt: ${verdict(main.previous.verdict)} ${main.previous.failed_gates.length ? `on ${esc(main.previous.failed_gates.join("; "))}` : ""}</p>` : ""}
  </div>`;
  if (rest.length) html += `<div class="remedy-rest"><h5>Other remedies, by latency at stake</h5><ul class="remedies">${rest.map((r) => `<li>
      <div class="r-top">${tag(r.kind)}<b>${remedyName(r)}</b><span class="hint">${r.share_pct}% · ${fmt(r.per_request_ms)} ms</span></div>
      <p>${nice(sentence(r.evidence))}. ${nice(cap(r.change))}.</p>
      <div class="r-act">${r.actuator ? `<code>${esc(r.actuator.flags.join(" "))}</code> on ${esc(r.actuator.service)}` : ""}${actState(r.actuator, false, `targets ${r.service} ${r.op} (${r.share_pct}%)`)}</div></li>`).join("")}</ul></div>`;
  return html;
}

/* ---------- every finding is a query: show it, or run it in the Graph console ---------- */
const cypherLiteral = (v) => typeof v === "number" ? String(v) : Array.isArray(v) ? `[${v.map(cypherLiteral).join(", ")}]` : `'${String(v).replace(/\\/g, "\\\\").replace(/'/g, "\\'")}'`;
const bindParams = (cy, params) => String(cy).replace(/\$([A-Za-z_]\w*)/g, (m, k) => (params && k in params ? cypherLiteral(params[k]) : m));
function queryBox(cy, params) {
  const bound = bindParams(cy, params);
  return `<div class="f-q"><button class="linkish q-toggle" type="button" aria-expanded="false">Show query</button>
    <div class="q-box" hidden><pre class="q-code">${esc(cy)}</pre>${params && Object.keys(params).length ? `<p class="hint">Parameters: ${Object.entries(params).map(([k, v]) => `<span class="mono">$${esc(k)} = ${esc(cypherLiteral(v))}</span>`).join(", ")}</p>` : ""}
    <div class="q-actions"><button class="btn small" type="button" data-run-cypher="${esc(bound)}"><svg class="ico" aria-hidden="true"><use href="#i-play"/></svg>Run in Graph</button>${copyBtn(bound, "query")}</div></div></div>`;
}
document.addEventListener("click", (e) => {
  const t = e.target.closest?.(".q-toggle");
  if (t) { const box = t.nextElementSibling, open = box.hidden; box.hidden = !open; t.setAttribute("aria-expanded", String(open)); t.textContent = open ? "Hide query" : "Show query"; return; }
  const r = e.target.closest?.("[data-run-cypher]");
  if (r) { e.preventDefault(); state.pendingCypher = r.dataset.runCypher; go("graph"); }
});

/* ---------- code: read the function behind an operation, then let the agent propose the change ---------- */
const readCodeBtn = (service, op, asButton = false) => `<button type="button" class="${asButton ? "btn small" : "linkish read-code"}" data-read-code="${esc(JSON.stringify([service, op]))}">${asButton ? "Read the code" : "Read the code"}</button>`;
function diffBlock(patch) {
  const rows = String(patch).replace(/\n$/, "").split("\n").map((ln) => {
    const k = ln.startsWith("+++") || ln.startsWith("---") ? "meta" : ln.startsWith("@@") ? "hunk" : ln.startsWith("+") ? "add" : ln.startsWith("-") ? "del" : "ctx";
    return `<span class="dl ${k}">${esc(ln) || " "}</span>`;
  }).join("");
  const add = (patch.match(/^\+(?!\+\+)/gm) || []).length, del = (patch.match(/^-(?!--)/gm) || []).length;
  return `<div class="diff-wrap"><div class="diff-head"><span class="mono">patch</span><span class="d-add">+${add}</span><span class="d-del">−${del}</span>${copyBtn(patch, "patch")}</div><pre class="diff" tabindex="0" aria-label="Proposed patch">${rows}</pre></div>`;
}
const drawer = $("#code-drawer");
async function readCode(service, op) {
  state.codeFor = [service, op];
  $("#cd-title").textContent = `${service} ${op}`;
  $("#cd-sub").textContent = "Reading the code…";
  $("#cd-body").innerHTML = `<span class="skel"></span><span class="skel"></span><span class="skel short"></span>`;
  $("#cd-repo").hidden = true;
  if (!drawer.open) drawer.showModal();
  let r;
  try { r = await api(`/api/source?service=${encodeURIComponent(service)}&op=${encodeURIComponent(op)}`); } catch (e) { r = { error: e.message }; }
  if (state.codeFor[0] !== service || state.codeFor[1] !== op) return;
  if (r.error) { $("#cd-sub").textContent = `${service} ${op}`; $("#cd-body").innerHTML = empty("Could not read the code", esc(r.error), "alert"); return; }
  $("#cd-title").textContent = r.function || `${service} ${op}`;
  $("#cd-sub").innerHTML = `<span class="mono">${esc(r.file)}</span>${r.start_line ? ` · lines ${r.start_line}–${r.end_line}` : ""}${copyBtn(`${r.file}${r.start_line ? `:${r.start_line}` : ""}`, "file and line")}`;
  const lines = String(r.code || "").split("\n");
  $("#cd-body").innerHTML = `${r.why ? `<p class="cd-why"><span class="k">Why the graph points here</span>${esc(cap(r.why))}.</p>` : ""}
    <pre class="src" tabindex="0" aria-label="Source of ${esc(r.function || op)}">${lines.map((ln, i) => `<span class="sl"><span class="ln" aria-hidden="true">${(r.start_line || 1) + i}</span>${esc(ln) || " "}</span>`).join("")}</pre>`;
  if (r.url) { $("#cd-repo").href = r.url; $("#cd-repo").hidden = false; }
}
document.addEventListener("click", (e) => { const b = e.target.closest?.("[data-read-code]"); if (b) { e.preventDefault(); const [sv, op] = JSON.parse(b.dataset.readCode); readCode(sv, op); } });
drawer.addEventListener("click", (e) => { if (e.target === drawer || e.target.closest("[data-close]")) drawer.close(); });
$("#cd-ask").addEventListener("click", () => {
  const [service, op] = state.codeFor || [];
  if (!service) return;
  drawer.close();
  if (route().view !== "agent") go("agent");
  ask(`What should change in the code behind ${service} ${op}? Read it first, then propose it with propose_code_change.`);
});

boot().catch((e) => {
  document.body.classList.remove("loading");
  $("#findings-list").innerHTML = `<li class="empty-li">${empty("Cannot load the console", `${esc(e.message)}. Is FalkorDB up and a window ingested? Run <code>make up</code> and <code>make baseline</code>.`, "alert")}</li>`;
  toast(`Cannot load: ${e.message}`, "bad");
});
