/* Faultline console. Plain JS, one state object, every number from the API. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const state = { window: null, windows: [], meta: {}, overview: null, session: null, chat: [], recorded: null, jobs: {} };
const fmt = (n, d = 0) => (n == null || isNaN(n)) ? "—" : Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });
const pct = (n) => (n == null) ? "—" : `${Math.round(n * 100)}%`;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const when = (ts) => ts ? new Date(ts * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
const api = async (path, opts) => { const r = await fetch(path, opts); if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText); return r.json(); };
const post = (path, body) => api(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
const label = (o) => `${o.service} ${o.op}` + (o.peer ? ` → ${o.peer}` : "");
let toastTimer;
function toast(text) { const t = $("#toast"); t.textContent = text; t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 4200); }

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
  $$(".rail a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  if (view === "trace") loadTrace();
  if (view === "experiments") loadExperiments();
  if (view === "approvals") loadActions();
  if (view === "graph") loadGraphView();
}
window.addEventListener("hashchange", () => { show(location.hash.replace("#", "") || "findings"); window.scrollTo({ top: 0 }); });

/* ---------- boot ---------- */
async function boot() {
  state.meta = await api("/api/meta");
  $("#meta-graph").textContent = state.meta.graph;
  $("#meta-model").textContent = state.meta.model;
  $("#meta-public").hidden = !state.meta.public;
  const w = await api("/api/windows");
  state.windows = w.windows; state.window = w.steady;
  const sel = $("#window");
  sel.innerHTML = [...w.windows].reverse().map((x) => `<option value="${x.id}">${esc(x.label)} · ${x.id} · ${x.traces} traces</option>`).join("");
  sel.value = state.window;
  sel.addEventListener("change", () => { state.window = sel.value; loadOverview(); const v = location.hash.replace("#", "") || "findings"; if (v !== "findings") show(v); });
  for (const id of ["cmp-a", "cmp-b"]) $(`#${id}`).innerHTML = w.windows.map((x) => `<option value="${x.id}">${esc(x.label)} · ${x.id}</option>`).join("");
  $("#cmp-a").value = w.windows[0]?.id; $("#cmp-b").value = w.windows[w.windows.length - 1]?.id;
  $("#cmp-a").addEventListener("change", loadCompare); $("#cmp-b").addEventListener("change", loadCompare);
  state.recorded = await api("/api/agent/recorded");
  if (state.recorded.turns?.length) {
    for (const t of state.recorded.turns) state.chat.push({ role: "user", text: t.prompt }, { role: "assistant", text: t.reply, trace: t.trace, model: state.recorded.model, recorded: true });
    $("#agent-hint").textContent = `recorded conversation with ${state.recorded.model} (${state.recorded.recorded_at.slice(0, 10)}); every tool call shown. Ask your own question below.`;
  }
  renderChat();
  await loadOverview();
  show(location.hash.replace("#", "") || "findings");
  pollActions();
}

/* ---------- overview ---------- */
async function loadOverview() {
  const o = await api(`/api/overview?window=${encodeURIComponent(state.window)}`);
  state.overview = o;
  const h = o.health[0] || {};
  const top = o.owners[0];
  const isExp = (state.windows.find((x) => x.id === state.window)?.label || "").startsWith("pause-");
  $("#s-op").textContent = h.op || "—"; $("#s-traces").textContent = h.traces ? `${h.traces} traces${isExp ? " · during a fault experiment" : ""}` : "";
  $("#s-p50").textContent = fmt(h.p50_ms); $("#s-p95").textContent = fmt(h.p95_ms);
  $("#s-failed").textContent = h.traces ? `${h.failed} / ${h.traces}` : "—"; $("#s-failrate").textContent = h.traces ? pct(h.fail_rate) : "";
  $("#s-owner").textContent = top ? label(top) : "—"; $("#s-owner-share").textContent = top ? `${top.share_pct}% · ${fmt(top.per_request_ms)} ms per request` : "";
  const max = top ? top.share_pct : 1;
  $("#owners tbody").innerHTML = o.owners.slice(0, 9).map((x) => `<tr><td class="barcell"><span class="bar" style="width:${Math.max(2, 72 * x.share_pct / max)}px"></span></td><td>${esc(label(x))}</td><td class="num">${x.share_pct}%</td><td class="num">${fmt(x.per_request_ms)}</td><td class="mono">${codeLink(x.code)}</td></tr>`).join("");
  $("#findings-list").innerHTML = o.findings.map((f) => `<li class="${f.severity}"><span class="stripe"></span><span class="kind">${esc(f.kind.replace(/_/g, " "))}</span><span class="text">${esc(f.detail)}</span></li>`).join("") || `<li class="empty">No findings in this window.</li>`;
  $("#exposure tbody").innerHTML = o.exposure.map((e) => `<tr><td><b>${esc(e.store)}</b> <span class="hint">${esc(e.store_kind)}</span></td><td class="mono">${esc(e.path)}</td><td class="mono">${e.protocols.map((p) => esc(p || "?")).join(" / ")}</td><td class="num">${e.calls_per_request_into_store}</td></tr>`).join("") || `<tr><td colspan="4" class="empty">No data store reached from the edge in this window.</td></tr>`;
  $("#guardrails tbody").innerHTML = o.guardrails.map((g) => `<tr><td class="mono">${esc(g.hop)}</td><td>${esc(g.kind)}</td><td class="mono">${esc(g.value)}</td><td>${esc(g.evidence)}</td><td>${g.priority === "high" && g.kind === "timeout" ? `<button class="btn small" data-propose-guardrail="${esc(g.hop)}" ${state.meta.public ? "disabled" : ""}>Propose</button>` : ""}</td></tr>`).join("");
  $("#retries tbody").innerHTML = o.retries.map((r) => `<tr><td class="mono">${esc(r.service)} ${esc(r.op)}</td><td class="num">${r.retried_errors}</td><td class="num">${fmt(r.wasted_ms_per_request, 1)}</td><td class="num">${fmt(r.retries_on_critical_path)}</td></tr>`).join("") || `<tr><td colspan="4" class="empty">No silent retries found.</td></tr>`;
  $$("[data-propose-guardrail]").forEach((b) => b.addEventListener("click", async () => { const r = await post("/api/propose", { type: "guardrail", hop: b.dataset.proposeGuardrail }); toast(r.ok ? `Proposed: ${r.title || r.action}. Approve it under Approvals.` : r.error); pollActions(); }));
  drawGraph(o);
}

function codeLink(c) {
  if (!c) return "";
  if (c.location) return `<span title="from span attributes">${esc(c.location)}</span>`;
  const text = `${c.file.split("/").pop()}${c.function ? " · " + c.function : ""}`;
  return c.url ? `<a href="${esc(c.url)}" target="_blank" rel="noopener" title="${esc(c.why || "")}">${esc(text)}</a>` : esc(text);
}
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function drawGraph(o) {
  const svg = d3.select("#svc-graph"); svg.selectAll("*").remove();
  const W = svg.node().clientWidth || 600, H = 400;
  svg.attr("viewBox", `0 0 ${W} ${H}`);
  const share = {}; for (const x of o.owners) share[x.service] = (share[x.service] || 0) + x.share_pct;
  const stores = new Set(o.exposure.map((e) => e.store));
  const names = new Set(); o.graph.forEach((e) => { names.add(e.from); names.add(e.to); });
  const nodes = [...names].map((id) => ({ id, share: share[id] || 0, store: stores.has(id), edge: state.meta.edge.includes(id) }));
  const links = o.graph.map((e) => ({ source: e.from, target: e.to, ...e }));
  const ember = css("--ember"), slate = css("--slate"), crit = css("--crit"), store = css("--store"), panel = css("--panel"), line2 = css("--line-2");
  const sim = d3.forceSimulation(nodes).force("link", d3.forceLink(links).id((d) => d.id).distance(170)).force("charge", d3.forceManyBody().strength(-1400)).force("collide", d3.forceCollide(62))
    .force("center", d3.forceCenter(W / 2, H / 2)).force("x", d3.forceX((d) => d.edge ? W * 0.18 : d.store ? W * 0.82 : W / 2).strength(0.25)).force("y", d3.forceY(H / 2).strength(0.05));
  svg.append("defs").append("marker").attr("id", "arrow").attr("viewBox", "0 -4 8 8").attr("refX", 50).attr("markerWidth", 9).attr("markerHeight", 9).attr("markerUnits", "userSpaceOnUse").attr("orient", "auto")
    .append("path").attr("d", "M0,-4L8,0L0,4").attr("fill", line2);
  const link = svg.append("g").selectAll("line").data(links).join("line").attr("stroke", (d) => d.errors ? crit : line2).attr("stroke-width", (d) => 1 + Math.min(d.per_trace, 14) / 2.5).attr("marker-end", "url(#arrow)").attr("stroke-opacity", 0.9);
  const elabel = svg.append("g").selectAll("text").data(links).join("text").attr("class", "edge-label").attr("text-anchor", "middle").text((d) => `${fmt(d.per_trace, 1)}/req${d.errors ? ` · ${d.errors} err` : ""}`);
  const node = svg.append("g").selectAll("g").data(nodes).join("g").call(d3.drag().on("start", (ev, d) => { if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }).on("drag", (ev, d) => { d.fx = ev.x; d.fy = ev.y; }).on("end", (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));
  node.append("rect").attr("x", -46).attr("y", -16).attr("width", 92).attr("height", 32).attr("rx", 4).attr("fill", panel).attr("stroke", (d) => d.share >= 10 ? ember : d.store ? store : line2).attr("stroke-width", (d) => d.share >= 30 ? 2.5 : 1.5);
  node.append("rect").attr("x", -46).attr("y", 12).attr("height", 4).attr("rx", 2).attr("width", (d) => Math.min(92, 92 * d.share / 100)).attr("fill", ember);
  node.append("text").attr("text-anchor", "middle").attr("dy", "-1").text((d) => d.id);
  node.append("text").attr("text-anchor", "middle").attr("dy", "10").attr("class", "edge-label").text((d) => d.share ? `${Math.round(d.share)}% of latency` : d.edge ? "edge" : d.store ? "store" : "");
  node.on("mousemove", (ev, d) => tip(ev, `${d.id}\n${d.share ? `${d.share.toFixed(1)}% of critical-path latency` : "not on the critical path"}`)).on("mouseleave", hideTip);
  link.on("mousemove", (ev, d) => tip(ev, `${d.from} → ${d.to}\n${d.count} calls, ${d.errors} errors\np50 ${fmt(d.p50_ms)} ms · p95 ${fmt(d.p95_ms)} ms\n${fmt(d.per_trace, 1)} calls per request`)).on("mouseleave", hideTip);
  sim.on("tick", () => {
    nodes.forEach((d) => { d.x = Math.max(50, Math.min(W - 50, d.x)); d.y = Math.max(20, Math.min(H - 20, d.y)); });
    link.attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y).attr("x2", (d) => d.target.x).attr("y2", (d) => d.target.y);
    elabel.attr("x", (d) => (d.source.x + d.target.x) / 2).attr("y", (d) => (d.source.y + d.target.y) / 2 - 6);
    node.attr("transform", (d) => `translate(${d.x},${d.y})`);
  });
}
function tip(ev, text) { const t = $("#tip"); t.textContent = text; t.style.whiteSpace = "pre"; t.hidden = false; const x = Math.min(ev.clientX + 14, innerWidth - 330), y = ev.clientY + 14; t.style.left = x + "px"; t.style.top = y + "px"; }
function hideTip() { $("#tip").hidden = true; }

/* ---------- trace ---------- */
async function loadTrace() {
  const c = await api(`/api/chain?window=${encodeURIComponent(state.window)}`);
  const wf = c.waterfall;
  if (!wf) { $("#trace-meta").textContent = "no steady-state trace in this window"; $("#waterfall").innerHTML = ""; return; }
  $("#trace-meta").textContent = `trace ${wf.trace} · ${wf.root} · ${fmt(wf.duration_ms)} ms end to end · ${wf.spans.length} spans`;
  const spans = wf.spans; const rowH = 18, left = 250, right = 20, top = 22; const W = Math.max(760, $(".waterfall-wrap").clientWidth), H = top + spans.length * rowH + 8;
  const x = d3.scaleLinear().domain([0, wf.duration_ms]).range([left, W - right]);
  const svg = d3.select("#waterfall").attr("viewBox", `0 0 ${W} ${H}`).style("height", H + "px"); svg.selectAll("*").remove();
  const ember = css("--ember"), slate = css("--slate"), crit = css("--crit");
  const axis = svg.append("g").attr("class", "axis").attr("transform", `translate(0,${top - 6})`).call(d3.axisTop(x).ticks(8).tickFormat((d) => `${d} ms`).tickSize(-(H - top)));
  axis.select(".domain").remove();
  const g = svg.append("g").selectAll("g").data(spans).join("g").attr("transform", (d, i) => `translate(0,${top + i * rowH})`);
  g.append("text").attr("x", 8 + Math.min(6, 0)).attr("y", 12).attr("dx", (d) => d.depth * 10).text((d) => `${d.service} · ${d.op}`.slice(0, 34)).style("fill", (d) => d.critical ? css("--fg") : css("--fg-3"));
  g.append("rect").attr("x", (d) => x(d.offset_ms)).attr("y", 3).attr("height", rowH - 7).attr("width", (d) => Math.max(1.5, x(d.offset_ms + d.duration_ms) - x(d.offset_ms))).attr("rx", 2)
    .attr("fill", (d) => d.error ? crit : d.critical ? ember : slate).attr("fill-opacity", (d) => d.critical || d.error ? 1 : 0.7);
  g.append("rect").attr("x", (d) => x(d.offset_ms)).attr("y", 3).attr("height", rowH - 7).attr("width", (d) => d.critical ? Math.max(0, x(d.offset_ms + d.self_ms) - x(d.offset_ms)) : 0).attr("rx", 2).attr("fill", css("--fg")).attr("fill-opacity", 0.18);
  g.on("mousemove", (ev, d) => tip(ev, `${d.service} ${d.op}${d.peer ? ` → ${d.peer}` : ""}\nstart +${fmt(d.offset_ms, 1)} ms · ${fmt(d.duration_ms, 1)} ms${d.critical ? ` · self ${fmt(d.self_ms, 1)} ms on the critical path` : ""}${d.error ? "\nERROR" : ""}`)).on("mouseleave", hideTip);
  $("#chain tbody").innerHTML = c.chain.map((s) => `<tr><td>${esc(s.service)}</td><td class="mono">${esc(s.op)}</td><td class="mono">${esc(s.peer)}</td><td class="num">${fmt(s.duration_ms, 1)}</td><td class="num">${s.self_ms >= 1 ? `<b>${fmt(s.self_ms, 1)}</b>` : fmt(s.self_ms, 1)}</td></tr>`).join("");
}

/* ---------- experiments ---------- */
async function loadExperiments() {
  const [p, l] = await Promise.all([api(`/api/planner?window=${encodeURIComponent(state.window)}`), api("/api/ledger")]);
  const maxP = Math.max(...p.experiments.map((r) => r.priority), 0.001);
  $("#planner tbody").innerHTML = p.experiments.map((r) => `<tr><td><b>${esc(r.target)}</b></td><td class="mono"><span class="prio"><i style="width:${100 * r.priority / maxP}%"></i></span>${r.priority.toFixed(2)}</td><td>${r.skip ? `<span class="pill muted">skipped</span> ${esc(r.skip)}` : esc(r.reasons.join("; "))}</td><td>${r.target !== "frontend" && !r.skip ? `<button class="btn small" data-propose-exp="${r.target}" ${state.meta.public ? "disabled" : ""}>Propose pause</button>` : ""}</td></tr>`).join("");
  $$("[data-propose-exp]").forEach((b) => b.addEventListener("click", async () => { const r = await post("/api/propose", { type: "experiment", target: b.dataset.proposeExp }); toast(r.ok ? `Proposed ${r.action}. Approve it under Approvals.` : r.error); pollActions(); }));
  const nf = p.next_fix;
  $("#next-fix").innerHTML = nf && nf.flags ? `<div class="flag">${esc(nf.flags.join(" "))} <span class="hint">on ${esc(nf.service)}</span></div><div>${esc(nf.why)}</div><div class="hint">targets ${esc(nf.targets_owner)} (${nf.share_pct}%, ${fmt(nf.per_request_ms)} ms per request)${nf.note ? `. ${esc(nf.note)}` : ""}${nf.previous_attempt ? `. Previous attempt: ${esc(nf.previous_attempt.verdict)}${nf.previous_attempt.failed_gates.length ? ` on ${esc(nf.previous_attempt.failed_gates.join("; "))}` : ""}` : ""}</div><div><button class="btn primary small" id="propose-fix" ${state.meta.public ? "disabled" : ""}>Propose this fix</button></div>`
    : `<div class="empty">${esc(nf?.note || "No known knob targets the current top owner.")}</div>`;
  $("#propose-fix")?.addEventListener("click", async () => { const r = await post("/api/propose", { type: "fix", flags: nf.flags.join(" "), rationale: `targets ${nf.targets_owner} (${nf.share_pct}%)` }); toast(r.ok ? `Proposed ${r.action}. Approve it under Approvals.` : r.error); pollActions(); });
  const V = (v) => `<span class="pill ${["CONFIRMED", "VERIFIED"].includes(v) ? "ok" : "bad"}">${esc(v)}</span>`;
  $("#experiments-list").innerHTML = l.experiments.map((e) => `<div class="entry"><div class="head">${V(e.verdict)}<b>pause ${esc(e.target)}</b><span class="hash">${esc(e.baseline)} → ${esc(e.observed)}</span><span class="when">${when(e.ts)}</span></div>
    <dl><dt>Hypothesis</dt><dd>${esc(e.hypothesis)}</dd><dt>Observed</dt><dd>${e.impact.map((x) => `${esc(x.op)}: ${pct(x.fail_rate)} failed, p50 ${fmt(x.p50_ms)} ms, ${x.failed_fast ? "failed fast" : "<b>hung</b>"}`).join("; ") || "—"}</dd><dt>Lesson</dt><dd>${esc(e.lesson)}</dd></dl></div>`).join("") || `<div class="empty">No experiment yet. Propose one above.</div>`;
  $("#fixes").innerHTML = l.fixes.map((f) => `<div class="entry"><div class="head">${V(f.verdict)}<b class="mono">${esc(f.flags)}</b> on ${esc(f.service)}<span class="hash">${esc(f.before)} → ${esc(f.after)}</span><span class="when">${when(f.ts)}</span></div>
    <ul class="gates">${f.gates.map((g) => `<li class="${g.passed ? "" : "fail"}">${esc(g.text)}</li>`).join("")}</ul><div class="row"><span class="hint">Next: ${esc(f.next_owner || "—")}</span>${f.verdict === "VERIFIED" ? `<button class="btn small" data-propose-pr="${f.id}" ${state.meta.public ? "disabled" : ""}>Propose draft PR</button>` : ""}</div></div>`).join("") || `<div class="empty">No fix yet.</div>`;
  $$("[data-propose-pr]").forEach((b) => b.addEventListener("click", async () => { const r = await post("/api/propose", { type: "pr", subject: b.dataset.proposePr }); toast(r.ok ? `Proposed ${r.action}: ${r.title}. Approve it under Approvals.` : r.error); pollActions(); }));
  const inc = await api("/api/incidents");
  $("#incidents").innerHTML = inc.incidents.map((i) => `<div class="entry"><div class="head"><span class="pill bad">${esc(i.kind)}</span><b>${esc(i.endpoint)}</b> in <span class="mono">${esc(i.label)} · ${esc(i.window)}</span><span class="when">${i.traces} traces</span></div>
    <dl><dt>Failure rate</dt><dd>${pct(i.fail_rate[0])} → <b>${pct(i.fail_rate[1])}</b></dd><dt>p95</dt><dd>${fmt(i.p95_ms[0])} → <b>${fmt(i.p95_ms[1])}</b> ms</dd>
    ${i.owner ? `<dt>What moved</dt><dd>${esc(label(i.owner))}: ${fmt(i.owner.baseline_per_request_ms)} → ${fmt(i.owner.per_request_ms)} ms per request ${i.owner.code ? `· ${codeLink(i.owner.code)}` : ""}</dd>` : ""}
    ${i.hop ? `<dt>Where to look</dt><dd class="mono">${esc(i.hop)}</dd>` : ""}${i.worst_trace ? `<dt>Worst request</dt><dd class="mono">${esc(i.worst_trace.id)} · ${fmt(i.worst_trace.duration_ms)} ms · status ${esc(i.worst_trace.status || "none")}</dd>` : ""}</dl>
    <div class="row"><button class="btn small" data-propose-issue="${i.window}" ${state.meta.public ? "disabled" : ""}>Propose incident issue</button></div></div>`).join("") || `<div class="empty">No window regressed against the current steady state.</div>`;
  $$("[data-propose-issue]").forEach((b) => b.addEventListener("click", async () => { const r = await post("/api/propose", { type: "issue", window: b.dataset.proposeIssue }); toast(r.ok ? `Proposed ${r.action}. Approve it under Approvals.` : r.error); pollActions(); }));
  loadCompare();
}
async function loadCompare() {
  const a = $("#cmp-a").value, b = $("#cmp-b").value; if (!a || !b) return;
  const d = await api(`/api/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
  const arrow = (x, y, dec = 0) => `<span class="mono">${fmt(x, dec)} → ${fmt(y, dec)}</span>`;
  $("#compare tbody").innerHTML = d.health.map((h) => `<tr><td>${esc(h.op)}</td><td>p50 ${arrow(h.p50_ms[0], h.p50_ms[1])} ms</td><td>p95 ${arrow(h.p95_ms[0], h.p95_ms[1])} ms</td><td>failed ${pct(h.fail_rate[0])} → ${pct(h.fail_rate[1])}</td></tr>`).join("")
    + d.owners.slice(0, 5).map((o) => `<tr><td class="hint">${esc(o.owner)}</td><td colspan="3">${arrow(o.per_request_ms[0], o.per_request_ms[1])} ms per request</td></tr>`).join("")
    + ((d.edges_added.length || d.edges_removed.length) ? `<tr><td colspan="4"><span class="pill wait">drift</span> added ${esc(d.edges_added.join(", ") || "none")}; removed ${esc(d.edges_removed.join(", ") || "none")}</td></tr>` : "");
}

/* ---------- agent ---------- */
function md(text) {
  let h = esc(text).replace(/```[\s\S]*?```/g, (m) => `<pre class="mono">${m.replace(/```\w*\n?/g, "")}</pre>`).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/^### (.*)$/gm, "<b>$1</b>");
  const lines = h.split("\n"); let out = "", inList = false;
  for (const ln of lines) { const li = ln.match(/^\s*[-*]\s+(.*)/); if (li) { if (!inList) { out += "<ul>"; inList = true; } out += `<li>${li[1]}</li>`; } else { if (inList) { out += "</ul>"; inList = false; } if (ln.trim()) out += `<p>${ln}</p>`; } }
  return out + (inList ? "</ul>" : "");
}
function renderChat() {
  const el = $("#chat");
  el.innerHTML = state.chat.map((m) => m.role === "user" ? `<div class="msg user"><span class="who">you</span><div class="bubble">${esc(m.text)}</div></div>`
    : `<div class="msg"><span class="who">${esc(m.model || "agent")}${m.recorded ? " · recorded" : ""}</span>${m.trace?.length ? `<ul class="calls">${m.trace.map((t) => `<li><b>${esc(t.tool)}</b>(${esc(Object.entries(t.args || {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", "))}) ${esc(t.summary || "")}</li>`).join("")}</ul>` : ""}<div class="bubble">${md(m.text)}</div></div>`).join("") || `<div class="empty">Ask a question. Every answer comes from graph tools; proposals wait for your approval.</div>`;
  el.lastElementChild?.scrollIntoView({ block: "end", behavior: "instant" });
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
async function loadActions() {
  const r = await api("/api/actions");
  state.jobs = r.jobs || {};
  const pending = r.actions.filter((a) => a.status === "proposed").length;
  $("#pending-count").textContent = pending; $("#pending-count").hidden = !pending;
  const P = (s) => `<span class="pill ${s === "done" ? "ok" : s === "proposed" ? "wait" : ["failed", "stale", "tampered", "rejected"].includes(s) ? "bad" : "muted"}">${esc(s)}</span>`;
  $("#actions").innerHTML = r.actions.map((a) => { const job = state.jobs[a.id]; return `<div class="entry"><div class="head">${P(job?.status === "running" ? "running" : a.status)}<b>${esc(a.title)}</b><span class="when">${when(a.ts)}</span></div>
    <div class="hint">${esc(a.rationale)}</div><div class="hash">${esc(a.id)} · evidence ${esc(a.hash)}</div>
    ${a.status === "proposed" && !job ? `<div class="row"><button class="btn primary small" data-approve="${a.id}" ${state.meta.public ? "disabled" : ""}>Approve and run</button><button class="btn small" data-reject="${a.id}" ${state.meta.public ? "disabled" : ""}>Reject</button>${state.meta.public ? `<span class="hint">disabled in the read-only demo</span>` : ""}</div>` : ""}
    ${job?.log?.length ? `<pre class="log">${esc(job.log.join("\n"))}</pre>` : ""}
    ${resultView(a.result || job?.result)}</div>`; }).join("") || `<div class="empty">No proposals yet. Ask the agent what to fix, or propose from Experiments.</div>`;
  $$("[data-approve]").forEach((b) => b.addEventListener("click", async () => { b.disabled = true; try { await post(`/api/actions/${b.dataset.approve}/approve`); toast("Running. The log appears below as it goes."); } catch (e) { toast(e.message); } loadActions(); }));
  $$("[data-reject]").forEach((b) => b.addEventListener("click", async () => { await post(`/api/actions/${b.dataset.reject}/reject`); loadActions(); }));
}
function resultView(r) {
  if (!r) return "";
  if (r.verdict) {
    const ok = ["VERIFIED", "CONFIRMED"].includes(r.verdict);
    return `<div class="row"><span class="pill ${ok ? "ok" : "bad"}">${esc(r.verdict)}</span><span class="hash">${esc(r.id || "")}</span></div>`
      + (r.gates ? `<ul class="gates">${r.gates.map((g) => `<li class="${g.passed ? "" : "fail"}">${esc(g.name)}: ${esc(g.detail)}</li>`).join("")}</ul>` : "")
      + (r.lesson ? `<div>${esc(r.lesson)}</div>` : "") + (r.next_owner ? `<div class="hint">Next: ${esc(r.next_owner)}</div>` : "");
  }
  if (r.opened) return `<div>Opened draft PR <a href="${esc(r.opened)}" target="_blank" rel="noopener">${esc(r.opened)}</a> with ${r.files.length} files.</div>`;
  if (r.filed) return `<div>Filed <a href="${esc(r.filed)}" target="_blank" rel="noopener">${esc(r.filed)}</a>.</div>`;
  if (r.written && r.files) return `<div>Wrote <span class="mono">${esc(r.written)}</span>: ${r.files.map((f) => `<span class="mono">${esc(f)}</span>`).join(", ")}. <span class="hint">${esc(r.note || "")}</span></div>`;
  if (r.written && r.title) return `<div>Wrote <span class="mono">${esc(r.written)}</span>. <span class="hint">${esc(r.note || "")}</span></div>`;
  if (r.written) return `<div>Wrote <span class="mono">${esc(r.written)}</span> (${esc(r.timeout)} timeout, bounded retries).</div>`;
  if (r.error) return `<div class="hint">${esc(r.error)}</div>`;
  return `<pre class="log">${esc(JSON.stringify(r, null, 1))}</pre>`;
}
async function pollActions() {
  try { const r = await api("/api/actions"); const pending = r.actions.filter((a) => a.status === "proposed").length; $("#pending-count").textContent = pending; $("#pending-count").hidden = !pending;
    const running = Object.values(r.jobs || {}).some((j) => j.status === "running");
    if (!$("#view-approvals").hidden) await loadActions();
    if (running) setTimeout(pollActions, 2500); else if (Object.keys(state.jobs).length && !running) { state.jobs = r.jobs; const w = await api("/api/windows"); if (w.windows.length !== state.windows.length) { state.windows = w.windows; const sel = $("#window"); sel.innerHTML = [...w.windows].reverse().map((x) => `<option value="${x.id}">${esc(x.label)} · ${x.id} · ${x.traces} traces</option>`).join(""); sel.value = state.window; } }
  } catch (e) {}
}

/* ---------- graph view ---------- */
const EXAMPLES = (w) => ({
  "Critical-path owners (CRISP bottom-up)": `MATCH (t:Trace {window:'${w}'}) WITH collect(t) AS ts, sum(t.duration_ms) AS total UNWIND ts AS t\nMATCH (s:Span {critical:true})-[:OF]->(t)\nRETURN s.service, s.op, round(100.0*sum(s.self_us)/1000/total) AS share ORDER BY share DESC`,
  "Single points of failure (betweenness)": `CALL algo.betweenness({nodeLabels:['Service'], relationshipTypes:['CALLS']}) YIELD node, score\nRETURN node.name, score ORDER BY score DESC`,
  "Blast radius of stopping route": `MATCH path = (r:Service)-[:CALLS* {window:'${w}'}]->(t:Service {name:'route'})\nRETURN [n IN nodes(path) | n.name] AS path`,
  "Prediction vs outcome per experiment": `MATCH (e:Experiment)-[p:PREDICTED_IMPACT]->(o)<-[a:ACTUAL_IMPACT]-(e)\nRETURN e.target, o.name, p.outcome, a.fail_rate, a.failed_fast, e.verdict`,
  "Data stores reachable from the edge": `MATCH (store:Service)<-[c:CALLS {window:'${w}'}]-() WHERE c.proto STARTS WITH 'db:'\nMATCH p = (e:Service {name:'frontend'})-[:CALLS*1..6 {window:'${w}'}]->(store)\nRETURN store.name, [n IN nodes(p) | n.name] AS path, [r IN relationships(p) | r.proto] AS protocols`,
  "Silent retries": `MATCH (c1:Span {window:'${w}', error:true})-[:CHILD_OF]->(p)<-[:CHILD_OF]-(c2:Span)\nWHERE c1.op = c2.op AND c2.start_us > c1.start_us\nRETURN c1.service, c1.op, count(DISTINCT c1) AS retried`,
  "Approvals bound to evidence": `MATCH (a:Action) OPTIONAL MATCH (a)-[:EXECUTED_AS]->(x)\nRETURN a.id, a.type, a.status, left(a.hash, 12) AS evidence, x.id, coalesce(x.verdict, '') AS verdict ORDER BY a.ts DESC`,
});
async function loadGraphView() {
  const m = await api("/api/model"); $("#model").textContent = m.text;
  const ex = EXAMPLES(state.window); const sel = $("#examples");
  sel.innerHTML = Object.keys(ex).map((k) => `<option>${esc(k)}</option>`).join("");
  $("#cypher").value = ex[sel.value];
  sel.onchange = () => ($("#cypher").value = ex[sel.value]);
  $("#run").onclick = async () => {
    const r = await post("/api/query", { cypher: $("#cypher").value });
    if (r.error) { $("#qstat").textContent = r.error; $("#qresult thead").innerHTML = ""; $("#qresult tbody").innerHTML = ""; return; }
    $("#qstat").textContent = `${r.rows.length} row${r.rows.length === 1 ? "" : "s"}`;
    $("#qresult thead").innerHTML = `<tr>${r.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>`;
    $("#qresult tbody").innerHTML = r.rows.map((row) => `<tr>${row.map((v) => `<td class="mono">${esc(typeof v === "object" ? JSON.stringify(v) : v)}</td>`).join("")}</tr>`).join("");
  };
}

boot().catch((e) => { $("#findings-list").innerHTML = `<li class="empty">Cannot load: ${esc(e.message)}. Is FalkorDB up and a window ingested? (make up && make baseline)</li>`; });
