"""Reachable: graph-proven vulnerability triage. Run: streamlit run streamlit_app.py"""
from __future__ import annotations

import time

import pandas as pd
import streamlit as st
from pyvis.network import Network

from reachable import actions, analysis, config, frozen, ingest, planner, verify
from reachable.agent import Agent
from reachable.graph import Store

st.set_page_config(page_title="Reachable", page_icon="🕸️", layout="wide")

VERDICT_STYLE = {"REACHABLE": "🔴", "POSSIBLY_REACHABLE": "🟠", "NEEDS_REVIEW": "🟡", "NO_STATIC_PATH": "⚪"}
KIND_STYLE = {"CALLS": "exact call", "MAYBE_CALLS": "name-guess call", "REFERENCES": "value reference", "ENTRY": "entry"}


@st.cache_resource
def store() -> Store:
    return Store()


def ensure_graph(s: Store) -> None:
    if not s.exists():
        with st.status("First run: building the graph (call graph, advisories, symbol mapping)...", expanded=True) as status:
            ingest.full_ingest(s, progress=lambda m: status.write(m))
            analysis.triage(s)
            status.update(label="Graph ready", state="complete")
    elif not analysis.latest_findings(s):
        analysis.triage(s)


def path_graph(s: Store, finding_ids: list[str], height: str = "600px") -> str:
    net = Network(height=height, width="100%", directed=True, bgcolor="#0e1117", font_color="#e6e6e6",
                  cdn_resources="in_line")
    net.set_options('{"layout":{"hierarchical":{"enabled":true,"direction":"UD","sortMethod":"directed",'
                    '"levelSeparation":80,"nodeSpacing":170}},"physics":{"enabled":false},'
                    '"nodes":{"font":{"size":16,"color":"#e6e6e6"},"margin":9},"edges":{"width":2,"arrows":{"to":{"scaleFactor":0.8}}}}')
    seen: set[str] = set()
    for fid in finding_ids:
        vuln = s.q("MATCH (f:Finding {id:$id})-[:FOR]->(v:Vuln) RETURN v.id, v.package", {"id": fid})[0]
        ev = s.q("MATCH (f:Finding {id:$id})-[e:EVIDENCE]->(n:Function) RETURN e.step, n.qualname, n.layer, e.via "
                 "ORDER BY e.step", {"id": fid})
        endpoint = s.q("MATCH (f:Finding {id:$id})-[:VIA]->(e:Endpoint) RETURN e.id", {"id": fid})
        prev = None
        if endpoint:
            eid = f"EP:{endpoint[0][0]}"
            if eid not in seen:
                net.add_node(eid, label=endpoint[0][0], color="#2ea043", shape="box")
                seen.add(eid)
            prev = eid
        for i, (step, q, layer, via) in enumerate(ev):
            last = i == len(ev) - 1
            parts = q.split(".")
            label = "\n".join(parts[-2:]) if len(parts) > 1 else q
            if q not in seen:
                color = "#da3633" if last else ("#1f6feb" if layer == "app" else "#6e7681")
                net.add_node(q, label=label, color=color, shape="box", title=q)
                seen.add(q)
            if prev:
                net.add_edge(prev, q, dashes=(via not in ("CALLS", "ENTRY")),
                             title=KIND_STYLE.get(via, via), color="#8b949e" if via == "CALLS" else "#d29922")
            prev = q
        vnode = f"V:{vuln[0]}"
        if vnode not in seen:
            net.add_node(vnode, label=f"{vuln[0]}\n{vuln[1]}", color="#f85149", shape="diamond")
            seen.add(vnode)
        if prev:
            net.add_edge(prev, vnode, title="VULNERABLE_SYMBOL", color="#f85149")
    html = net.generate_html()
    dark = "<style>html,body{background:#0e1117;margin:0}.card{border:0!important;background:#0e1117!important}" \
           "#mynetwork{border:0!important}</style></head>"
    return html.replace("</head>", dark, 1)


GATE_ICON = {True: "✅", False: "❌"}
STATUS_STYLE = {"VERIFIED": ("success", "✅ VERIFIED"), "REJECTED": ("error", "⛔ REJECTED"), "UNVERIFIED": ("warning", "⚠️ UNVERIFIED")}


def render_fix_tab(s: Store, public: bool) -> None:
    st.caption("The agent plans a compatible upgrade, rebuilds the graph with the candidate versions in a private scratch "
               "graph, re-triages, and only a VERIFIED result can become a pull request. Say 'graph-checked', not 'secure'.")
    rem = verify.latest_remediation(s)
    live = st.session_state.get("live_rem")
    if public:
        c1, c2 = st.columns(2)
        if c1.button("Plan & verify a fix now (live, in a private scratch graph)", width="stretch"):
            try:
                with st.status("Planning and verifying in a scratch graph (about 15 seconds)...", expanded=True) as status:
                    st.session_state.live_rem = verify.run_closed_loop_ephemeral(s, progress=status.write)
                st.rerun()
            except verify.BusyError as exc:
                st.warning(f"Not right now: {exc}. The operator's recorded run is shown below.")
        c2.button("Propose draft PR", width="stretch", disabled=True,
                  help="Disabled on the public site: proposals and approvals write to the graph.")
        if live:
            rem = live
    if not public:
        c1, c2 = st.columns(2)
        if c1.button("Plan & verify a fix (builds a scratch graph)", width="stretch"):
            with st.status("Planning and verifying...", expanded=True) as status:
                verify.run_closed_loop(s, progress=status.write)
            st.rerun()
        if rem and rem["status"] == "VERIFIED" and c2.button("Propose draft PR", width="stretch"):
            st.session_state.pr_msg = actions.propose_fix_pr(s)
            st.rerun()
        if st.session_state.get("pr_msg"):
            m = st.session_state.pr_msg
            (st.success if m["ok"] else st.error)(f"Proposed `{m.get('action')}`: approve it in the Actions tab." if m["ok"] else m["error"])
    if not rem:
        st.info("No remediation checked yet." + ("" if public else " Click the button above."))
        return
    kind, label = STATUS_STYLE[rem["status"]]
    getattr(st, kind)(f"{label}  ·  remediation `{rem['id']}`")
    if public and rem is live:
        st.caption(f"**Live result** from your click: planned from the shared graph, verified in a private scratch graph that "
                   f"was then deleted (baseline snapshot `{rem['baseline_snapshot']}` → candidate `{rem['candidate_snapshot']}`). "
                   "Nothing was written to the shared graph.")
    elif public:
        st.caption(f"Read-only demo: showing the operator's recorded run (baseline snapshot `{rem['baseline_snapshot']}` → "
                   f"candidate `{rem['candidate_snapshot']}`, recorded {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(rem['ts']))}).")
    for r in rem["plan"]["rejected"]:
        st.warning(f"**The graph rejected the naive plan:** {r['reason']}.  \n**Resolution:** {r['resolution']}.")
    a, b, c = st.columns(3)
    exp_b = rem["before"].get("REACHABLE", 0) + rem["before"].get("POSSIBLY_REACHABLE", 0)
    exp_a = rem["after"].get("REACHABLE", 0) + rem["after"].get("POSSIBLY_REACHABLE", 0)
    a.metric("Exposed advisories", f"{exp_b} → {exp_a}")
    b.metric("Cleared", len(rem["cleared"]))
    c.metric("New exposure", len(rem["new_exposed"]))
    st.markdown("**Gates**")
    st.dataframe(pd.DataFrame([{"": GATE_ICON[g["passed"]], "Gate": g["name"], "Detail": g["detail"]} for g in rem["gates"]]),
                 hide_index=True, width="stretch")
    st.markdown("**Changes**")
    st.dataframe(pd.DataFrame(rem["plan"]["changes"]), hide_index=True, width="stretch")
    st.code(rem["requirements_diff"], language="diff")
    with st.expander("Verdicts before / after"):
        st.json({"before": rem["before"], "after": rem["after"], "cleared": rem["cleared"]})


def verdict_table(findings: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{"": VERDICT_STYLE[f["verdict"]], "Verdict": f["verdict"], "Package": f"{f['package']} {f['version']}",
                          "Advisory": f["vuln"], "CVE": f["cve"] or "", "Severity": f["severity"],
                          "Fix": f["fixed_version"] or ""} for f in findings])


def network_probe(host: str, port: int) -> str:
    """DNS, then a raw TCP connect: tells apart 'host blocks the port' from 'bad credentials' without leaking secrets."""
    import socket
    import time as _t

    lines = []
    try:
        ips = sorted({a[4][0] for a in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
        lines.append(f"DNS: {host} -> {', '.join(ips)}")
    except Exception as exc:
        return f"DNS: FAILED ({exc.__class__.__name__}: {exc}). The host name is wrong or this host has no DNS."
    t = _t.time()
    try:
        with socket.create_connection((host, port), timeout=8):
            lines.append(f"TCP connect to port {port}: OK in {_t.time() - t:.2f}s -> the port is open; check username/password/ssl")
    except Exception as exc:
        lines.append(f"TCP connect to port {port}: FAILED after {_t.time() - t:.1f}s ({exc.__class__.__name__}: {exc})")
        lines.append("-> this host cannot reach the database port. Outbound traffic is blocked here, or the FalkorDB Cloud "
                     "instance is not READY / restricts source IPs. Credentials were not even tried.")
    return "\n".join(lines)


def main() -> None:
    try:
        s = store()
        s.q("RETURN 1")
    except Exception as exc:
        st.error(f"Cannot connect to FalkorDB at {config.FALKORDB_HOST}:{config.FALKORDB_PORT} "
                 f"(user set: {bool(config.FALKORDB_USERNAME)}, password set: {bool(config.FALKORDB_PASSWORD)}, "
                 f"ssl: {config.FALKORDB_SSL}). {exc.__class__.__name__}")
        st.warning(f"Streamlit secrets seen by the app (names only): {config.SECRETS_INFO['keys'] or 'none'}"
                   + (f"  |  secrets parse error: {config.SECRETS_INFO['error']}" if config.SECRETS_INFO["error"] else ""))
        st.info("Running locally: `docker run -d -p 6379:6379 -p 3000:3000 falkordb/falkordb:latest`.  "
                "On Streamlit Cloud: check App settings > Secrets (FALKORDB_HOST / PORT / USERNAME / PASSWORD / SSL).")
        with st.expander("Network probe from this host", expanded=True):
            st.code(network_probe(config.FALKORDB_HOST, config.FALKORDB_PORT), language="text")
        store.clear()
        return
    ensure_graph(s)
    if "agent" not in st.session_state:
        st.session_state.agent = Agent(s)
        st.session_state.chat = []
        st.session_state.recorded = frozen.load_agent()
        if st.session_state.recorded:  # show the agent working before the visitor types anything
            for t in st.session_state.recorded["turns"]:
                st.session_state.chat += [("user", t["prompt"], None), ("assistant", t["reply"], t["trace"])]

    findings = analysis.latest_findings(s)
    run_id = findings[0]["run"]
    PUBLIC = config.PUBLIC_MODE

    with st.sidebar:
        st.title("🕸️ Reachable")
        st.caption("Which of your CVEs can actually be reached? Proven with graph paths in FalkorDB.")
        st.markdown(f"**Run:** `{findings[0]['label']}`  \n**Gemini:** {'on' if st.session_state.agent.live else 'off (offline mode)'}")
        st.divider()
        if PUBLIC:
            st.info("Read-only public demo: rebuilds, what-if edits and real actions are disabled by the backend.")
            fw = frozen.load_whatif()
            if fw:
                st.subheader("What-if (recorded)")
                st.caption(f"{fw['description']}: exposed advisories {fw['exposed_before']} → {fw['exposed_after']} "
                           f"(snapshots `{fw['baseline_snapshot']}` → `{fw['variant_snapshot']}`)")
                with st.expander(f"{len(fw['flips'])} verdict flips"):
                    st.dataframe(pd.DataFrame(fw["flips"]), hide_index=True)
        else:
            st.subheader("What-if: change the code")
            variant = st.radio("App version", list(ingest.VARIANTS), format_func=lambda k: ingest.VARIANTS[k]["label"])
            if st.button("Re-ingest app code & re-triage", width="stretch"):
                with st.spinner("Rewriting the app layer of the graph..."):
                    before = {f["vuln"]: f["verdict"] for f in findings}
                    ingest.reload_app_layer(s, variant)
                    analysis.triage(s, label=variant)
                    st.session_state.diff = {f["vuln"]: (before[f["vuln"]], f["verdict"])
                                             for f in analysis.latest_findings(s) if before.get(f["vuln"]) != f["verdict"]}
                st.rerun()
            if st.button("Rebuild everything from scratch", width="stretch"):
                s.reset()
                st.rerun()

    st.header("Vulnerability reachability triage")
    counts = {v: sum(1 for f in findings if f["verdict"] == v) for v in VERDICT_STYLE}
    cols = st.columns(5)
    cols[0].metric("Advisories (pip-audit would list)", len(findings))
    for col, (v, n) in zip(cols[1:], counts.items()):
        col.metric(f"{VERDICT_STYLE[v]} {v.replace('_', ' ').title()}", n)

    if st.session_state.get("diff"):
        st.success("Verdicts changed by the code change: " + "; ".join(
            f"**{k}** {a} → **{b}**" for k, (a, b) in st.session_state.diff.items()))

    tab_triage, tab_agent, tab_fix, tab_actions, tab_cypher = st.tabs(
        ["Triage & proof", "Agent", "Graph-checked fix", "Actions (human approval)", "Graph & Cypher"])

    with tab_triage:
        left, right = st.columns([5, 6])
        with left:
            show = st.multiselect("Filter", list(VERDICT_STYLE), default=["REACHABLE", "POSSIBLY_REACHABLE", "NEEDS_REVIEW"])
            view = [f for f in findings if f["verdict"] in show]
            st.dataframe(verdict_table(view), hide_index=True, width="stretch", height=360)
            choice = st.selectbox("Inspect advisory", [f["vuln"] for f in view] or [""])
        with right:
            f = next((x for x in findings if x["vuln"] == choice), None)
            if f:
                st.subheader(f"{VERDICT_STYLE[f['verdict']]} {f['verdict']}  ·  {f['package']} {f['version']}")
                st.write(f["summary"])
                st.info(f["reason"])
                st.caption(f"Vulnerable symbol found by **{f['symbol_source']}** (confidence {f['symbol_confidence']}): "
                           f"{f['symbol_rationale']}" + (f"  \n[fix commit]({f['patch_url']})" if f["patch_url"] else ""))
                path = analysis.witness_path(s, f["finding"])
                if path:
                    st.iframe(path_graph(s, [f["finding"]], "600px"), height=620)
                    with st.expander("Witness path (stored as EVIDENCE edges)"):
                        st.dataframe(pd.DataFrame(path), hide_index=True, width="stretch")
        st.subheader("Where to fix first")
        c1, c2 = st.columns(2)
        gp = analysis.guard_points(s, run_id)
        c1.markdown("**Guard points**: app functions on the most exposed paths")
        c1.dataframe(pd.DataFrame(gp), hide_index=True, width="stretch")
        c2.markdown("**Version-aware upgrade plan** (constraints read from the graph)")
        try:
            plan = planner.plan_remediation(s, run_id)
            c2.dataframe(pd.DataFrame(plan.changes)[["package", "from", "to"]], hide_index=True, width="stretch")
            for r in plan.rejected:
                c2.caption(f"⚠️ {r['reason']} → {r['resolution']}")
        except Exception as exc:
            c2.caption(f"Plan unavailable ({exc.__class__.__name__}).")
        exposed = [f["finding"] for f in findings if f["verdict"] in ("REACHABLE", "POSSIBLY_REACHABLE")]
        if exposed:
            st.subheader("Exposure map: every exposed path")
            st.iframe(path_graph(s, exposed, "720px"), height=740)

    with tab_fix:
        render_fix_tab(s, PUBLIC)

    with tab_agent:
        st.caption("Gemini decides which graph tools to call (explain_finding, recommend_fixes, verify_remediation, query_graph, ...). "
                   "Verdicts come from the graph, never from the model. It can propose actions; you approve them.")
        rec = st.session_state.get("recorded")
        if rec and st.session_state.chat and st.session_state.chat[0][1] == rec["turns"][0]["prompt"]:
            c1, c2 = st.columns([6, 1])
            c1.info(f"Recorded conversation with `{rec['model']}` on graph snapshot `{rec['snapshot']}`, every tool call shown. "
                    "Ask your own question below.")
            if c2.button("Clear", width="stretch"):
                st.session_state.chat = []
                st.rerun()
        for role, text, trace in st.session_state.chat:
            with st.chat_message(role):
                if trace:
                    with st.expander(f"Tool calls the agent chose ({len(trace)})", expanded=True):
                        for t in trace:
                            args = ", ".join(f"{k}={v!r}" for k, v in t["args"].items())
                            st.markdown(f"`{t['tool']}({args})` → {t.get('summary', '')}")
                st.markdown(text)
        examples = ["Summarize the risk: what is actually reachable?", "Why is GHSA-q2q7-5pp4-w6pg reachable? Show the path.",
                    "What should we fix first? Plan it, check it, and propose the PR.", "Propose issues for everything reachable."]
        picked = None
        for col, ex in zip(st.columns(len(examples)), examples):
            if col.button(ex, key=f"ex-{ex}", width="stretch"):
                picked = ex
        prompt = st.chat_input("Ask about your vulnerabilities...") or picked
        if prompt:
            with st.spinner("Agent is calling graph tools..."):
                reply, trace = st.session_state.agent.ask(prompt)
            compact = [{"tool": t["tool"], "args": t["args"], "summary": frozen._compact(t["result"])} for t in trace]
            st.session_state.chat += [("user", prompt, None), ("assistant", reply, compact)]
            st.rerun()

    with tab_actions:
        st.caption("Nothing runs until you approve. Approval is bound to the exact payload and graph snapshot: any change "
                   "to the evidence, target or content aborts it.")
        if not PUBLIC and st.button("Propose OpenVEX document for this run"):
            actions.propose_vex(s)
            st.rerun()
        if st.session_state.get("last_action"):
            aid, res = st.session_state.last_action
            if res["ok"]:
                st.success(f"`{aid}` executed: {res.get('url') or res.get('file') or res.get('note', res['status'])}")
            else:
                st.error(f"`{aid}` refused: {res['error']}")
        rows = actions.list_actions(s)
        if not rows:
            st.write("No actions yet. Ask the agent to propose issues.")
        for a in rows:
            c = st.columns([2, 5, 2, 2])
            c[0].write(f"`{a['id']}`")
            c[1].write(f"**{a['type']}**: {a['title']}" + (f"  → {a['team']}" if a["team"] else ""))
            c[2].write(a["status"])
            if a["status"] == "proposed" and not PUBLIC and c[3].button("Approve", key=a["id"]):
                st.session_state.last_action = (a["id"], actions.execute(s, a["id"]))
                st.rerun()
            elif a["url"]:
                c[3].caption(a["url"])

    with tab_cypher:
        st.markdown("**The verdict is a graph query.** Exact-path proof (from `reachable/analysis.py`):")
        st.code(analysis.EXACT_PATH.strip(), language="cypher")
        st.code(analysis.ANY_PATH.strip(), language="cypher")
        q = st.text_area("Run read-only Cypher", "MATCH (v:Vuln)-[:AFFECTS]->(p:PackageVersion) RETURN p.key, count(v) AS advisories ORDER BY advisories DESC")
        if st.button("Run"):
            res = st.session_state.agent.box.query_graph(q)
            if "error" in res:
                st.error(res["error"])
            else:
                st.dataframe(pd.DataFrame(res["rows"], columns=res["columns"]), hide_index=True)
        hubs = analysis.supply_chain_chokepoints(s)
        st.markdown("**Supply-chain hubs** (`algo.betweenness` over DEPENDS_ON)")
        st.dataframe(pd.DataFrame(hubs), hide_index=True)


main()
