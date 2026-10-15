"""Reachable: graph-proven vulnerability triage. Run: streamlit run streamlit_app.py"""
from __future__ import annotations

import pandas as pd
import streamlit as st
from pyvis.network import Network

from reachable import actions, analysis, config, ingest
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


def path_graph(s: Store, finding_ids: list[str], height: str = "430px") -> str:
    net = Network(height=height, width="100%", directed=True, bgcolor="#0e1117", font_color="#e6e6e6",
                  cdn_resources="in_line")
    net.set_options('{"layout":{"hierarchical":{"enabled":true,"direction":"LR","sortMethod":"directed",'
                    '"levelSeparation":120,"nodeSpacing":60}},"physics":{"enabled":false},'
                    '"nodes":{"font":{"size":20,"color":"#e6e6e6"},"margin":10},"edges":{"width":2,"arrows":{"to":{"scaleFactor":0.8}}}}')
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


def verdict_table(findings: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{"": VERDICT_STYLE[f["verdict"]], "Verdict": f["verdict"], "Package": f"{f['package']} {f['version']}",
                          "Advisory": f["vuln"], "CVE": f["cve"] or "", "Severity": f["severity"],
                          "Fix": f["fixed_version"] or ""} for f in findings])


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
        store.clear()
        return
    ensure_graph(s)
    if "agent" not in st.session_state:
        st.session_state.agent = Agent(s)
        st.session_state.chat = []

    findings = analysis.latest_findings(s)
    run_id = findings[0]["run"]

    with st.sidebar:
        st.title("🕸️ Reachable")
        st.caption("Which of your CVEs can actually be reached? Proven with graph paths in FalkorDB.")
        st.markdown(f"**Run:** `{findings[0]['label']}`  \n**Gemini:** {'on' if st.session_state.agent.live else 'off (offline mode)'}")
        st.divider()
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

    tab_triage, tab_agent, tab_actions, tab_cypher = st.tabs(["Triage & proof", "Agent", "Actions (human approval)", "Graph & Cypher"])

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
                    st.iframe(path_graph(s, [f["finding"]]), height=450)
                    with st.expander("Witness path (stored as EVIDENCE edges)"):
                        st.dataframe(pd.DataFrame(path), hide_index=True, width="stretch")
        st.subheader("Where to fix first")
        c1, c2 = st.columns(2)
        gp = analysis.guard_points(s, run_id)
        up = analysis.upgrade_plan(s, run_id)
        c1.markdown("**Guard points**: app functions on the most exposed paths")
        c1.dataframe(pd.DataFrame(gp), hide_index=True, width="stretch")
        c2.markdown("**Upgrade leverage**: one version bump, how many exposed advisories it clears")
        c2.dataframe(pd.DataFrame(up).drop(columns=["advisories"]) if up else pd.DataFrame(), hide_index=True,
                     width="stretch")
        exposed = [f["finding"] for f in findings if f["verdict"] in ("REACHABLE", "POSSIBLY_REACHABLE")]
        if exposed:
            st.subheader("Exposure map: every exposed path")
            st.iframe(path_graph(s, exposed, "520px"), height=540)

    with tab_agent:
        st.caption("Gemini calls graph tools (explain_finding, recommend_fixes, query_graph, ...). It can propose actions; you approve them.")
        for role, text, trace in st.session_state.chat:
            with st.chat_message(role):
                st.markdown(text)
                if trace:
                    with st.expander(f"Tool calls ({len(trace)})"):
                        for t in trace:
                            st.code(f"{t['tool']}({t['args']})", language="python")
        examples = ["Summarize the risk: what is actually reachable?", "Why is GHSA-q2q7-5pp4-w6pg reachable? Show the path.",
                    "What should we fix first?", "Propose issues for everything reachable."]
        picked = st.pills("Try", examples, selection_mode="single")
        prompt = st.chat_input("Ask about your vulnerabilities...") or picked
        if prompt:
            reply, trace = st.session_state.agent.ask(prompt)
            st.session_state.chat += [("user", prompt, None), ("assistant", reply, trace)]
            st.rerun()

    with tab_actions:
        st.caption("Nothing runs until you approve. On approval the graph is re-checked, so stale plans are refused.")
        if st.button("Propose OpenVEX document for this run"):
            actions.propose_vex(s)
            st.rerun()
        rows = actions.list_actions(s)
        if not rows:
            st.write("No actions yet. Ask the agent to propose issues.")
        for a in rows:
            c = st.columns([2, 5, 2, 2])
            c[0].write(f"`{a['id']}`")
            c[1].write(f"**{a['type']}**: {a['title']}" + (f"  → {a['team']}" if a["team"] else ""))
            c[2].write(a["status"])
            if a["status"] == "proposed" and c[3].button("Approve", key=a["id"]):
                res = actions.execute(s, a["id"])
                (st.success if res["ok"] else st.error)(str(res))
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
