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
}
window.addEventListener("hashchange", () => { show(location.hash.replace("#", "") || "findings"); window.scrollTo({ top: 0 }); });
let resizeTimer;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (state.overview && !$("#view-findings").hidden && Math.abs($("#svc-graph").clientWidth - state.graphW) > 40) drawGraph(state.overview); }, 200); });

/* ---------- boot ---------- */
const windowOption = (x) => `<option value="${x.id}">${esc(x.label)} · ${x.id} · ${x.traces} traces</option>`;
async function boot() {
  state.meta = await api("/api/meta");
  $("#meta-graph").textContent = state.meta.graph;
  $("#meta-model").textContent = state.meta.model;
  $("#meta-public").hidden = !state.meta.public;
  const w = await api("/api/windows");
  state.windows = w.windows; state.window = w.steady;
  const sel = $("#window");
  sel.innerHTML = [...w.windows].reverse().map(windowOption).join("");
  sel.value = state.window;
  sel.addEventListener("change", () => { state.window = sel.value; loadOverview(); const v = location.hash.replace("#", "") || "findings"; if (v !== "findings") show(v); });
  for (const id of ["cmp-a", "cmp-b"]) $(`#${id}`).innerHTML = w.windows.map((x) => `<option value="${x.id}">${esc(x.label)} · ${x.id}</option>`).join("");
  $("#cmp-a").value = w.windows[0]?.id; $("#cmp-b").value = w.windows[w.windows.length - 1]?.id;
  $("#cmp-a").addEventListener("change", loadCompare); $("#cmp-b").addEventListener("change", loadCompare);
  state.recorded = await api("/api/agent/recorded");
  if (state.recorded.turns?.length) {
    for (const t of state.recorded.turns) state.chat.push({ role: "user", text: t.prompt }, { role: "assistant", text: t.reply, trace: t.trace, model: state.recorded.model, recorded: true });
    $("#agent-hint").textContent = `A recorded conversation with ${state.recorded.model} from ${state.recorded.recorded_at.slice(0, 10)}, with every graph tool call it made. Ask your own question below; proposals land in Approvals and never run on their own.`;
  }
  renderChat();
  await loadOverview();
  document.body.classList.remove("loading");
  show(location.hash.replace("#", "") || "findings");
  pollActions();
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
    <td><div class="owner-name">${esc(x.service)} <span class="op">${esc(x.op)}${x.peer ? ` → ${esc(x.peer)}` : ""}</span></div>${x.code ? `<span class="owner-code">${codeLink(x.code)}</span>` : ""}</td>
    <td><div class="share"><span class="bar"><i style="width:${Math.max(2, 100 * x.share_pct / max)}%"></i></span><span class="pct">${x.share_pct}%</span></div></td>
    <td class="num">${fmt(x.per_request_ms)}</td></tr>`).join("") || `<tr><td colspan="4">${empty("No critical-path data", "This window has no steady-state traces to attribute.")}</td></tr>`;

  const findings = [...o.findings].sort((a, b) => (SEV[a.severity] ?? 3) - (SEV[b.severity] ?? 3));
  const nHigh = findings.filter((f) => f.severity === "high").length;
  $("#findings-count").innerHTML = findings.length ? `<b>${findings.length}</b> findings${nHigh ? ` · <b>${nHigh}</b> high` : ""}` : "";
  $("#findings-list").innerHTML = findings.map((f) => `<li class="finding ${esc(f.severity)}"><span class="sev"><i></i>${esc(f.severity[0].toUpperCase() + f.severity.slice(1))}</span><span class="kind tag">${esc(f.kind.replace(/_/g, " "))}</span><p class="text">${nice(f.detail)}</p></li>`).join("")
    || `<li class="empty-li">${empty("No findings in this window", "Pick the steady-state window in the top bar, or ingest traces with <code>make baseline</code>.")}</li>`;

  $("#exposure tbody").innerHTML = o.exposure.map((e) => `<tr><td><b>${esc(e.store)}</b><span class="cell-sub">${esc(e.store_kind)}</span></td><td class="path">${nice(e.path)}</td><td><div class="tags">${e.protocols.map((p) => `<span class="tag">${esc(p || "?")}</span>`).join("")}</div></td><td class="num">${e.calls_per_request_into_store}</td></tr>`).join("")
    || `<tr><td colspan="4">${empty("Nothing reachable", "No data store is reached from the edge in this window.")}</td></tr>`;
  $("#guardrails tbody").innerHTML = o.guardrails.map((g) => `<tr><td class="hop">${nice(g.hop)}</td><td><span class="tag">${esc(g.kind)}</span>${g.priority === "high" ? ` <span class="pill wait" title="An experiment showed this hop hangs"><i></i>high</span>` : ""}</td><td class="mono strong">${nice(g.value)}</td><td class="evidence">${nice(g.evidence)}</td><td class="act">${g.priority === "high" && g.kind === "timeout" ? `<button class="btn small" data-propose-guardrail="${esc(g.hop)}" ${lock()}>Propose</button>` : ""}</td></tr>`).join("")
    || `<tr><td colspan="5">${empty("No guardrails to recommend", "Every hop already has headroom in this window.")}</td></tr>`;
  $("#retries tbody").innerHTML = o.retries.map((r) => `<tr><td class="mono">${esc(r.service)} ${esc(r.op)}</td><td class="num">${r.retried_errors}</td><td class="num">${fmt(r.wasted_ms_per_request, 1)}</td><td class="num">${fmt(r.retries_on_critical_path)}</td></tr>`).join("")
    || `<tr><td colspan="4">${empty("No silent retries", "No failed call was retried without surfacing an error.", "check")}</td></tr>`;
  $$("[data-propose-guardrail]").forEach((b) => b.addEventListener("click", async () => { const r = await post("/api/propose", { type: "guardrail", hop: b.dataset.proposeGuardrail }); proposed(r, `Proposed: ${r.title || r.action}. Approve it under Approvals.`); pollActions(); }));
  drawGraph(o);
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
  for (const [id, color] of [["arrow", "--line-2"], ["arrow-err", "--crit"]])
    defs.append("marker").attr("id", id).attr("viewBox", "0 -4 8 8").attr("refX", 8).attr("markerWidth", 8).attr("markerHeight", 8).attr("markerUnits", "userSpaceOnUse").attr("orient", "auto")
      .append("path").attr("d", "M0,-4L8,0L0,4").style("fill", V(color));
  const link = svg.append("g").selectAll("line").data(links).join("line").attr("class", "edge").style("stroke", (d) => d.errors ? V("--crit") : V("--line-2"))
    .attr("stroke-width", (d) => 1.2 + Math.min(d.per_trace, 14) / 2.4).attr("marker-end", (d) => d.errors ? "url(#arrow-err)" : "url(#arrow)").attr("stroke-opacity", 0.9).attr("stroke-linecap", "round");
  /* wide transparent strokes make thin edges easy to hover */
  const hit = svg.append("g").selectAll("line").data(links).join("line").attr("class", "edge-hit").attr("stroke-width", 14).style("stroke", "transparent").style("pointer-events", "stroke");
  const elabel = svg.append("g").selectAll("text").data(links).join("text").attr("class", "edge-label").attr("text-anchor", "middle").text((d) => `${fmt(d.per_trace, 1)}/req${d.errors ? ` · ${d.errors} err` : ""}`);
  const node = svg.append("g").selectAll("g").data(nodes).join("g").attr("class", "node").call(d3.drag().on("start", (ev, d) => { if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }).on("drag", (ev, d) => { d.fx = ev.x; d.fy = ev.y; }).on("end", (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));
  node.append("rect").attr("class", "box").attr("x", -NW / 2).attr("y", -NH / 2).attr("width", NW).attr("height", NH).attr("rx", 6).style("fill", V("--panel"))
    .style("stroke", (d) => d.share >= 10 ? V("--ember") : d.store ? V("--store") : V("--line-2")).attr("stroke-width", (d) => d.share >= 30 ? 2.2 : 1.4);
  node.append("rect").attr("x", -NW / 2 + 6).attr("y", NH / 2 - 6).attr("height", 3).attr("rx", 1.5).attr("width", (d) => d.share >= 1 ? Math.max(3, (NW - 12) * d.share / 100) : 0).style("fill", V("--ember"));
  node.append("text").attr("text-anchor", "middle").attr("dy", "-2").text((d) => d.id);
  node.append("text").attr("text-anchor", "middle").attr("dy", "11").attr("class", "node-sub").text((d) => d.share >= 1 ? `${Math.round(d.share)}% of latency` : d.edge ? "edge" : d.store ? "data store" : "");
  node.on("mousemove", (ev, d) => tip(ev, `${d.id}\n${d.share ? `${d.share.toFixed(1)}% of critical-path latency` : "not on the critical path"}`)).on("mouseleave", hideTip);
  hit.on("mousemove", (ev, d) => tip(ev, `${d.from} → ${d.to}\n${d.count} calls, ${d.errors} errors\np50 ${fmt(d.p50_ms)} ms · p95 ${fmt(d.p95_ms)} ms\n${fmt(d.per_trace, 1)} calls per request`)).on("mouseleave", hideTip);
  /* end each edge at the target's box border so the arrowhead stays visible */
  const clip = (s, t) => { const dx = t.x - s.x, dy = t.y - s.y; const k = Math.min(Math.abs((NW / 2 + 3) / (dx || 1e-6)), Math.abs((NH / 2 + 3) / (dy || 1e-6))); return [t.x - dx * Math.min(k, 1), t.y - dy * Math.min(k, 1)]; };
  sim.on("tick", () => {
    nodes.forEach((d) => { d.x = Math.max(NW / 2 + 4, Math.min(W - NW / 2 - 4, d.x)); d.y = Math.max(NH / 2 + 6, Math.min(H - NH / 2 - 6, d.y)); });
    for (const sel of [link, hit]) sel.attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y).attr("x2", (d) => clip(d.source, d.target)[0]).attr("y2", (d) => clip(d.source, d.target)[1]);
    elabel.attr("x", (d) => (d.source.x + d.target.x) / 2).attr("y", (d) => (d.source.y + d.target.y) / 2 - 6);
    node.attr("transform", (d) => `translate(${d.x},${d.y})`);
  });
}
function tip(ev, text) { const t = $("#tip"); t.textContent = text; t.hidden = false; const x = Math.min(ev.clientX + 14, innerWidth - t.offsetWidth - 12), y = Math.min(ev.clientY + 14, innerHeight - t.offsetHeight - 12); t.style.left = Math.max(8, x) + "px"; t.style.top = y + "px"; }
function hideTip() { $("#tip").hidden = true; }

/* ---------- trace ---------- */
async function loadTrace() {
  const c = await api(`/api/chain?window=${encodeURIComponent(state.window)}`);
  const wf = c.waterfall;
  if (!wf) {
    $("#trace-meta").textContent = "No steady-state trace in this window.";
    $("#waterfall").innerHTML = ""; $("#waterfall").style.height = "0px";
    $("#chain tbody").innerHTML = `<tr><td colspan="5">${empty("No trace to show", "This window has no steady-state request. Pick another window in the top bar.")}</td></tr>`;
    return;
  }
  const crit = wf.spans.filter((s) => s.critical).length;
  $("#trace-meta").innerHTML = [["", wf.root], [fmt(wf.duration_ms), "ms end to end"], [wf.spans.length, "spans"], [crit, "on the critical path"], ["", `trace ${wf.trace.slice(0, 12)}…`]]
    .map(([v, l]) => `<span class="m">${v !== "" ? `<b>${esc(v)}</b> ` : ""}${esc(l)}</span>`).join("");
  $("#trace-meta").title = `trace ${wf.trace}`;
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
  $("#chain tbody").innerHTML = c.chain.map((s) => `<tr><td>${esc(s.service)}</td><td class="mono">${esc(s.op)}</td><td class="mono muted">${esc(s.peer) || `<span class="hint">—</span>`}</td><td class="num">${fmt(s.duration_ms, 1)}</td><td class="num">${s.self_ms >= 1 ? `<b>${fmt(s.self_ms, 1)}</b>` : fmt(s.self_ms, 1)}</td></tr>`).join("");
}

/* ---------- experiments ---------- */
const VERDICT = {
  CONFIRMED: ["ok", "The outcome matched what the graph predicted."],
  VERIFIED: ["ok", "All four gates passed; the fix was kept."],
  REFUTED: ["bad", "The outcome contradicted the graph's prediction."],
  REJECTED: ["bad", "A gate failed; the fix was rolled back."],
};
const verdict = (v) => { const [k, t] = VERDICT[v] || [["CONFIRMED", "VERIFIED"].includes(v) ? "ok" : "bad", ""]; return `<span class="pill ${k}" title="${esc(t)}"><i></i>${esc(v)}</span>`; };
const gateItem = (passed, name, detail) => `<li class="${passed ? "" : "fail"}">${icon(passed ? "check" : "cross")}<span class="g-name">${nice(name)}</span>${detail ? `<span class="g-detail">${nice(detail)}</span>` : ""}</li>`;
const splitGate = (text) => { const i = text.lastIndexOf(": "); return i > 0 ? [text.slice(0, i), text.slice(i + 2)] : [text, ""]; };
const winSpan = (a, b) => `<span class="span" title="before → after window">${esc(a)} → ${esc(b)}</span>`;
const countMeta = (id, n, noun) => { $(id).innerHTML = n ? `<b>${n}</b> ${noun}${n === 1 ? "" : "s"}` : ""; };

async function loadExperiments() {
  const [p, l] = await Promise.all([api(`/api/planner?window=${encodeURIComponent(state.window)}`), api("/api/ledger")]);
  const maxP = Math.max(...p.experiments.map((r) => r.priority), 0.001);
  $("#planner tbody").innerHTML = p.experiments.map((r) => {
    const hist = r.reasons.filter((x) => x.startsWith("tested before")), rest = r.reasons.filter((x) => !x.startsWith("tested before"));
    return `<tr><td><b>${esc(r.target)}</b></td>
      <td><div class="prio"><span class="bar"><i style="width:${100 * r.priority / maxP}%"></i></span><span class="v">${r.priority.toFixed(2)}</span></div></td>
      <td><div class="reasons">${r.skip ? `<div><span class="pill muted"><i></i>skipped</span> ${esc(r.skip)}</div>` : ""}<div>${rest.map(nice).join(" · ")}</div>${hist.map((x) => `<div class="history">${nice(x)}</div>`).join("")}</div></td>
      <td class="act">${r.target !== "frontend" && !r.skip ? `<button class="btn small" data-propose-exp="${r.target}" ${lock()}>Propose pause</button>` : ""}</td></tr>`;
  }).join("") || `<tr><td colspan="4">${empty("Nothing to break", "The planner found no candidate target in this window.")}</td></tr>`;
  $$("[data-propose-exp]").forEach((b) => b.addEventListener("click", async () => { const r = await post("/api/propose", { type: "experiment", target: b.dataset.proposeExp }); proposed(r, `Proposed ${r.action}. Approve it under Approvals.`); pollActions(); }));
  const nf = p.next_fix;
  $("#next-fix").innerHTML = nf && nf.flags ? `<div class="flag">${esc(nf.flags.join(" "))}<span class="on">on ${esc(nf.service)}</span></div><p>${esc(nf.why)}</p>
      <dl class="kv"><dt>Targets</dt><dd>${esc(nf.targets_owner)} · ${nf.share_pct}% · ${fmt(nf.per_request_ms)} ms per request</dd>
      ${nf.note ? `<dt>Note</dt><dd>${esc(nf.note)}</dd>` : ""}${nf.previous_attempt ? `<dt>Last attempt</dt><dd>${verdict(nf.previous_attempt.verdict)} ${nf.previous_attempt.failed_gates.length ? `on ${esc(nf.previous_attempt.failed_gates.join("; "))}` : ""}</dd>` : ""}</dl>
      <div><button class="btn primary small" id="propose-fix" ${lock()}>Propose this fix</button></div>`
    : empty("No known knob for the top owner", `${esc(nf?.note || "No configuration knob targets the current top latency owner.")} Ask the Agent for options, or add a guardrail from Findings.`);
  $("#propose-fix")?.addEventListener("click", async () => { const r = await post("/api/propose", { type: "fix", flags: nf.flags.join(" "), rationale: `targets ${nf.targets_owner} (${nf.share_pct}%)` }); proposed(r, `Proposed ${r.action}. Approve it under Approvals.`); pollActions(); });

  countMeta("#n-experiments", l.experiments.length, "experiment");
  $("#experiments-list").innerHTML = l.experiments.map((e) => `<article class="card"><div class="card-head">${verdict(e.verdict)}<h5 class="card-title">pause <span class="mono">${esc(e.target)}</span></h5>${winSpan(e.baseline, e.observed)}${ago(e.ts)}</div>
    <dl class="kv"><dt>Hypothesis</dt><dd>${esc(e.hypothesis)}</dd><dt>Observed</dt><dd>${e.impact.map((x) => `${esc(x.op)}: ${pct(x.fail_rate)} failed, p50 ${fmt(x.p50_ms)} ms, ${x.failed_fast ? "failed fast" : "<b>hung</b>"}`).join("; ") || "—"}</dd></dl>
    <div class="lesson"><b>Lesson</b>${nice(e.lesson)}</div></article>`).join("") || empty("No experiment yet", "Propose a pause from What to break next, then approve it under Approvals.", "flask");
  $("#n-fixes").innerHTML = l.fixes.length ? `<b>${l.fixes.length}</b> ${l.fixes.length === 1 ? "fix" : "fixes"}` : "";
  $("#fixes").innerHTML = l.fixes.map((f) => `<article class="card"><div class="card-head">${verdict(f.verdict)}<h5 class="card-title"><span class="mono">${esc(f.flags)}</span> <span class="muted">on ${esc(f.service)}</span></h5>${winSpan(f.before, f.after)}${ago(f.ts)}</div>
    <ul class="gates">${f.gates.map((g) => gateItem(g.passed, ...splitGate(g.text))).join("")}</ul>
    <div class="card-foot"><span class="hint">Next owner: ${nice(f.next_owner || "—")}</span>${f.verdict === "VERIFIED" ? `<button class="btn small" data-propose-pr="${f.id}" ${lock()}>Propose draft PR</button>` : ""}</div></article>`).join("") || empty("No fix yet", "Propose one from What to fix next. It is measured against four gates before it is kept.");
  $$("[data-propose-pr]").forEach((b) => b.addEventListener("click", async () => { const r = await post("/api/propose", { type: "pr", subject: b.dataset.proposePr }); proposed(r, `Proposed ${r.action}: ${r.title}. Approve it under Approvals.`); pollActions(); }));
  const inc = await api("/api/incidents");
  countMeta("#n-incidents", inc.incidents.length, "incident");
  $("#incidents").innerHTML = inc.incidents.map((i) => `<article class="card"><div class="card-head"><span class="pill bad"><i></i>${esc(i.kind)}</span><h5 class="card-title">${esc(i.endpoint)} <span class="muted">in</span> <span class="mono">${esc(i.label)}</span></h5><span class="span">${esc(i.window)}</span><span class="when">${i.traces} traces</span></div>
    <dl class="kv"><dt>Failure rate</dt><dd><span class="from">${pct(i.fail_rate[0])} →</span> <b>${pct(i.fail_rate[1])}</b></dd><dt>p95</dt><dd><span class="from">${fmt(i.p95_ms[0])} →</span> <b>${fmt(i.p95_ms[1])}</b> ms</dd>
    ${i.owner ? `<dt>What moved</dt><dd>${esc(label(i.owner))}: ${fmt(i.owner.baseline_per_request_ms)} → ${fmt(i.owner.per_request_ms)} ms per request ${i.owner.code ? `· ${codeLink(i.owner.code)}` : ""}</dd>` : ""}
    ${i.hop ? `<dt>Where to look</dt><dd class="mono">${nice(i.hop)}</dd>` : ""}${i.worst_trace ? `<dt>Worst request</dt><dd class="mono">${esc(i.worst_trace.id)} · ${fmt(i.worst_trace.duration_ms)} ms · status ${esc(i.worst_trace.status || "none")}</dd>` : ""}</dl>
    <div class="card-foot"><span class="hint">Files a GitHub issue with this evidence once approved.</span><button class="btn small" data-propose-issue="${i.window}" ${lock()}>Propose incident issue</button></div></article>`).join("") || empty("No incidents", "No window regressed against the current steady state.", "check");
  $$("[data-propose-issue]").forEach((b) => b.addEventListener("click", async () => { const r = await post("/api/propose", { type: "issue", window: b.dataset.proposeIssue }); proposed(r, `Proposed ${r.action}. Approve it under Approvals.`); pollActions(); }));
  loadCompare();
}
async function loadCompare() {
  const a = $("#cmp-a").value, b = $("#cmp-b").value; if (!a || !b) return;
  const d = await api(`/api/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
  /* lower is better for every measure here: latency, failure rate, time owned */
  const delta = (x, y, points = false) => {
    if (x == null || y == null) return `<span class="delta flat">—</span>`;
    if (points) { const dp = Math.round((y - x) * 100); return `<span class="delta ${dp > 0 ? "up" : dp < 0 ? "down" : "flat"}">${dp > 0 ? "+" : ""}${dp} pts</span>`; }
    if (!x) return `<span class="delta ${y > 0 ? "up" : "flat"}">${y > 0 ? "new" : "0%"}</span>`;
    const r = (y - x) / x, cls = Math.abs(r) < 0.02 ? "flat" : r > 0 ? "up" : "down";
    const txt = y / x >= 10 ? `×${fmt(y / x, 0)}` : `${r > 0 ? "+" : ""}${Math.round(r * 100)}%`;
    return `<span class="delta ${cls}">${txt}</span>`;
  };
  const row = (lbl, x, y, unit, dec = 0, points = false) => `<tr><td class="lbl-cell">${lbl}</td><td class="num">${points ? pct(x) : `${fmt(x, dec)}${unit}`}</td><td class="num">${points ? pct(y) : `${fmt(y, dec)}${unit}`}</td><td class="num">${delta(x, y, points)}</td></tr>`;
  $("#compare tbody").innerHTML = d.health.map((h) => `<tr class="sect"><td colspan="4">${esc(h.op)}</td></tr>${row("p50 latency", h.p50_ms[0], h.p50_ms[1], " ms")}${row("p95 latency", h.p95_ms[0], h.p95_ms[1], " ms")}${row("failed requests", h.fail_rate[0], h.fail_rate[1], "", 0, true)}`).join("")
    + (d.owners.length ? `<tr class="sect"><td colspan="4">Critical-path time per request</td></tr>` + d.owners.slice(0, 5).map((o) => row(nice(o.owner), o.per_request_ms[0], o.per_request_ms[1], " ms")).join("") : "")
    + ((d.edges_added.length || d.edges_removed.length) ? `<tr class="sect"><td colspan="4">Topology</td></tr><tr><td colspan="4"><div class="drift"><span class="pill wait"><i></i>drift</span> added ${nice(d.edges_added.join(", ") || "none")}; removed ${nice(d.edges_removed.join(", ") || "none")}</div></td></tr>` : "");
}

/* ---------- agent ---------- */
function md(text) {
  let h = esc(text).replace(/```[\s\S]*?```/g, (m) => `<pre class="mono">${m.replace(/```\w*\n?/g, "")}</pre>`).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/^### (.*)$/gm, "<b>$1</b>");
  const lines = h.split("\n"); let out = "", inList = false;
  for (const ln of lines) { const li = ln.match(/^\s*[-*]\s+(.*)/); if (li) { if (!inList) { out += "<ul>"; inList = true; } out += `<li>${li[1]}</li>`; } else { if (inList) { out += "</ul>"; inList = false; } if (ln.trim()) out += `<p>${ln}</p>`; } }
  return out + (inList ? "</ul>" : "");
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
    <div class="meta-line"><span class="tag">${esc(a.type || "action")}</span><span>${esc(a.id)}</span><span class="fp" title="Hash of the evidence this proposal was derived from. Approval re-derives it and refuses to run if it changed.">${icon("fp", "ico")}evidence <b>${esc(a.hash)}</b></span></div>
    ${a.status === "proposed" && !job ? `<div class="actions"><button class="btn primary small" data-approve="${a.id}" ${lock()}>Approve and run</button><button class="btn small" data-reject="${a.id}" ${lock()}>Reject</button>${state.meta.public ? `<span class="locked"><svg class="ico" aria-hidden="true"><use href="#i-lock"/></svg>Turned off in the read-only demo</span>` : ""}</div>` : ""}
    ${job?.log?.length ? `<div class="log-head">${job.status === "running" ? `<i class="live" aria-hidden="true"></i>Live log` : "Log"}</div><pre class="log" aria-live="polite">${esc(job.log.join("\n"))}</pre>` : ""}
    ${resultView(a.result || job?.result)}</article>`;
}
async function loadActions() {
  const r = await api("/api/actions");
  state.jobs = r.jobs || {};
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
    if (running) setTimeout(pollActions, 2500); else if (Object.keys(state.jobs).length && !running) { state.jobs = r.jobs; const w = await api("/api/windows"); if (w.windows.length !== state.windows.length) { state.windows = w.windows; const sel = $("#window"); sel.innerHTML = [...w.windows].reverse().map(windowOption).join(""); sel.value = state.window; } }
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
    .append("path").attr("d", "M0,-4L8,0L0,4").style("fill", V("--line-2"));
  const g = svg.append("g");
  for (const [a, b, rel, at = 0.5] of SCHEMA.edges) {
    const A = pos[a], B = pos[b];
    if (a === b) {
      const side = SCHEMA.nodes.find((n) => n.id === a).loop || "left";
      let d, lx, ly, anchor = "middle";
      if (side === "left") { d = `M${A.x - 48},${A.y - 10} C${A.x - 108},${A.y - 42} ${A.x - 108},${A.y + 26} ${A.x - 48},${A.y + 10}`; lx = Math.max(4, A.x - 104); ly = A.y + 42; anchor = "start"; }
      else if (side === "top") { d = `M${A.x - 14},${A.y - 18} C${A.x - 50},${A.y - 70} ${A.x + 50},${A.y - 70} ${A.x + 14},${A.y - 18}`; lx = A.x; ly = A.y - 62; }
      else { d = `M${A.x + 48},${A.y - 10} C${A.x + 108},${A.y - 42} ${A.x + 108},${A.y + 26} ${A.x + 48},${A.y + 10}`; lx = A.x + 80; ly = A.y + 40; }
      g.append("path").attr("d", d).style("fill", "none").style("stroke", V("--line-2")).attr("stroke-width", 1.2).attr("marker-end", "url(#sarrow)");
      g.append("text").attr("class", "rel").attr("x", lx).attr("y", ly).attr("text-anchor", anchor).text(rel);
      continue;
    }
    const dx = B.x - A.x, dy = B.y - A.y, L = Math.hypot(dx, dy), ux = dx / L, uy = dy / L;
    const x1 = A.x + ux * 50, y1 = A.y + uy * 19, x2 = B.x - ux * 54, y2 = B.y - uy * 21;
    g.append("line").attr("x1", x1).attr("y1", y1).attr("x2", x2).attr("y2", y2).style("stroke", V("--line-2")).attr("stroke-width", 1.2).attr("marker-end", "url(#sarrow)");
    const mx = x1 + (x2 - x1) * at, my = y1 + (y2 - y1) * at;
    const t = g.append("text").attr("class", "rel").attr("x", mx).attr("y", my - 5).attr("text-anchor", "middle").text(rel);
    const bb = t.node().getBBox();
    g.insert("rect", () => t.node()).attr("x", bb.x - 3).attr("y", bb.y - 1).attr("width", bb.width + 6).attr("height", bb.height + 2).style("fill", V("--panel"));
  }
  const node = svg.append("g").selectAll("g").data(SCHEMA.nodes).join("g").attr("transform", (d) => `translate(${pos[d.id].x},${pos[d.id].y})`);
  node.append("rect").attr("x", -50).attr("y", -19).attr("width", 100).attr("height", 38).attr("rx", 6).style("fill", V("--raised"))
    .style("stroke", (d) => ["Span", "Experiment", "Fix", "Action"].includes(d.id) ? V("--fg-3") : V("--line-2")).attr("stroke-width", 1.3);
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
  sel.onchange = () => { $("#cypher").value = ex[sel.value]; runQuery(); };
  $("#run").onclick = runQuery;
  $("#cypher").onkeydown = (ev) => { if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); runQuery(); } };
  runQuery();
}

boot().catch((e) => {
  document.body.classList.remove("loading");
  $("#findings-list").innerHTML = `<li class="empty-li">${empty("Cannot load the console", `${esc(e.message)}. Is FalkorDB up and a window ingested? Run <code>make up</code> and <code>make baseline</code>.`, "alert")}</li>`;
  toast(`Cannot load: ${e.message}`, "bad");
});
