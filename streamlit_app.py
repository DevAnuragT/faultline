"""Faultline UI: the graph decides, the human approves, the system is changed and re-measured."""
from __future__ import annotations

import json
import time

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

from faultline import actions, analysis, config, experiments, ingest, planner
from faultline.agent import Agent
from faultline.graph import Store

st.set_page_config(page_title="Faultline", page_icon="⚡", layout="wide")
PUBLIC = config.PUBLIC_MODE
SEV = {"high": "🔴", "medium": "🟠", "low": "🟡"}
VERDICT = {"VERIFIED": "✅", "REJECTED": "❌", "CONFIRMED": "✅", "REFUTED": "❌"}


@st.cache_resource
def store() -> Store:
    return Store()


def dark_graph(edges: list[dict], owners: dict[str, float]) -> str:
    net = Network(height="420px", width="100%", bgcolor="#0e1117", font_color="#e6edf3", directed=True)
    net.barnes_hut(gravity=-6000, spring_length=180)
    seen = set()
    for e in edges:
        for n in (e["from"], e["to"]):
            if n not in seen:
                share = owners.get(n, 0.0)
                net.add_node(n, label=f"{n}\n{share:.0f}% of latency" if share else n, shape="box",
                             color="#f0883e" if share >= 30 else ("#d29922" if share >= 10 else "#30363d"), font={"size": 16})
                seen.add(n)
        net.add_edge(e["from"], e["to"], label=f"{e['per_trace']:.0f}/req · p95 {e['p95_ms']:.0f} ms" + (f" · {e['errors']} err" if e["errors"] else ""),
                     width=1 + min(e["per_trace"], 10) / 2, color="#8b949e" if not e["errors"] else "#f85149", font={"size": 11, "color": "#8b949e"})
    return net.generate_html().replace("<body>", "<body style='background:#0e1117;margin:0'>").replace(
        "border: 1px solid lightgray;", "border: 1px solid #30363d;")


def render_overview(s: Store, w: str) -> None:
    health = analysis.root_health(s, w)
    owners = analysis.critical_owners(s, w)
    c1, c2, c3, c4 = st.columns(4)
    if health:
        h = health[0]
        c1.metric(f"{h['op']} p50", f"{h['p50_ms']:.0f} ms"); c2.metric("p95", f"{h['p95_ms']:.0f} ms")
        c3.metric("Failed", f"{h['failed']}/{h['traces']}")
        c4.metric("Top owner", owners[0]["service"] if owners else "-", f"{owners[0]['op']} · {owners[0]['share_pct']}%" if owners else "", delta_color="off")
    left, right = st.columns([3, 2])
    with left:
        st.markdown("**Service graph** (this window: calls per request, p95 per hop, errors)")
        by_service: dict[str, float] = {}
        for o in owners:
            by_service[o["service"]] = by_service.get(o["service"], 0.0) + o["share_pct"]
        components.html(dark_graph(analysis.service_graph(s, w), by_service), height=430)
    with right:
        st.markdown("**Who owns the latency** (critical path, CRISP bottom-up, hangs excluded)")
        st.dataframe(pd.DataFrame([{"Owner": analysis.label(o), "Share": f"{o['share_pct']}%", "ms/request": o["per_request_ms"], "Errors": o["errors"]}
                                   for o in owners[:8]]), hide_index=True, width="stretch")
    st.markdown("**Findings** (every one is a graph query; the agent only reads them)")
    for f in analysis.findings(s, w):
        st.markdown(f"{SEV[f['severity']]} **{f['kind'].replace('_', ' ')}** · {f['detail']}")
    with st.expander("Critical path of the slowest steady-state request (top-down)"):
        chain = analysis.critical_chain(s, w)
        if chain:
            st.caption(f"trace `{chain[0]['trace']}`, {chain[0]['trace_ms']} ms end to end")
            st.dataframe(pd.DataFrame([{"Service": c["service"], "Operation": c["op"], "Waits on": c["peer"], "Span ms": c["duration_ms"], "Self ms": c["self_ms"], "Error": c["error"]} for c in chain]),
                         hide_index=True, width="stretch")
    with st.expander("Silent retries and guardrails the graph recommends"):
        r = analysis.retries(s, w)
        if r:
            st.dataframe(pd.DataFrame(r), hide_index=True, width="stretch")
        st.dataframe(pd.DataFrame(planner.guardrails(s, w)), hide_index=True, width="stretch")


def render_ledger(s: Store, w: str) -> None:
    st.markdown("**Experiment planner** (Monocle-style: criticality × safety, recency and review gates; memory is the graph)")
    rows = planner.rank_experiments(s, w)
    st.dataframe(pd.DataFrame([{"Target": r["target"], "Priority": r["priority"], "Blast radius": f"{r['blast_share']:.0%}", "Why": "; ".join(r["reasons"]),
                                "Skipped": r["skip"] or ""} for r in rows]), hide_index=True, width="stretch")
    a, b = st.columns(2)
    with a:
        tgt = st.selectbox("Pause which service?", [r["target"] for r in rows if r["target"] != "frontend"])
        if st.button("Propose experiment", disabled=PUBLIC):
            st.session_state.flash = actions.propose_experiment(s, tgt)
    with b:
        nf = planner.next_fix(s, w)
        st.caption(f"The graph points at: `{' '.join(nf['flags'])}` on {nf['service']} (targets {nf['targets_owner']}, {nf['share_pct']}%)" if nf else "No known knob targets the current top owner.")
        if nf and st.button("Propose that fix", disabled=PUBLIC):
            st.session_state.flash = actions.propose_fix(s, nf["flags"], f"targets {nf['targets_owner']} ({nf['share_pct']}%)")
    if st.session_state.get("flash"):
        st.info(st.session_state.pop("flash"))
    st.markdown("**Experiments** (prediction from the graph vs what happened)")
    _, ex = s.ro("""MATCH (e:Experiment)-[:TARGETS]->(sv) OPTIONAL MATCH (e)-[a:ACTUAL_IMPACT]->(o:Operation)
                    RETURN e.id, sv.name, e.hypothesis, e.verdict, e.lesson, e.ts, collect({op:o.name, fail:a.fail_rate, p50:a.p50_ms, fast:a.failed_fast}) ORDER BY e.ts DESC""")
    for r in ex:
        st.markdown(f"{VERDICT.get(r[3], '')} **{r[3]}** · pause `{r[1]}` · {time.strftime('%H:%M', time.localtime(r[5]))}  \n"
                    f"_Hypothesis:_ {r[2]}  \n_Observed:_ " + "; ".join(f"{x['op']}: {x['fail']:.0%} failed, p50 {x['p50']:.0f} ms, {'failed fast' if x['fast'] else 'hung'}" for x in r[6] if x["op"]) +
                    f"  \n_Lesson:_ {r[4]}")
    st.markdown("**Fixes** (four gates; a rejected fix is rolled back)")
    _, fx = s.ro("MATCH (f:Fix)-[:BEFORE]->(b) MATCH (f)-[:AFTER]->(a) RETURN f.id, f.service, f.flags, f.verdict, f.gates, f.next_owner, b.id, a.id, f.ts ORDER BY f.ts DESC")
    for r in fx:
        with st.expander(f"{VERDICT.get(r[3], '')} {r[3]} · `{r[2]}` on {r[1]} · {r[6]} → {r[7]}", expanded=r is fx[0]):
            for g in r[4]:
                st.markdown(("✅ " if g.startswith("PASS") else "❌ ") + g[5:])
            st.caption(f"Next: {r[5]}")


def render_agent(s: Store) -> None:
    st.caption("Gemini chooses the tools; every tool is a graph query or a proposal. Verdicts and numbers come from the graph.")
    for role, text, trace in st.session_state.chat:
        with st.chat_message(role):
            if trace:
                with st.expander(f"Tool calls the agent chose ({len(trace)})", expanded=True):
                    for t in trace:
                        args = ", ".join(f"{k}={v!r}" for k, v in t["args"].items())
                        st.markdown(f"`{t['tool']}({args})` → {summarise(t['result'])}")
            st.markdown(text)
    cols = st.columns(3)
    for i, q in enumerate(["What is slow and why?", "What should we fix first?", "What would you break to test resilience?"]):
        if cols[i].button(q, key=f"ex{i}"):
            st.session_state.pending = q
    q = st.chat_input("Ask the agent") or st.session_state.pop("pending", None)
    if q:
        with st.spinner("asking…"):
            reply, trace = st.session_state.agent.ask(q)
        st.session_state.chat += [("user", q, None), ("assistant", reply, [{"tool": t["tool"], "args": t["args"], "result": t["result"]} for t in trace])]
        st.rerun()


def summarise(r) -> str:
    if isinstance(r, dict):
        for k in ("verdict", "status", "action", "error", "note", "text", "window"):
            if k in r:
                return f"{k}: {r[k]}"
        return f"{len(r)} fields"
    if isinstance(r, list):
        return f"{len(r)} rows"
    return str(r)[:80]


def render_actions(s: Store) -> None:
    st.caption("Nothing touches the running system without a click here. Approval re-derives the evidence from the graph and "
               "refuses if it changed (stale) or was altered (tampered).")
    rows = actions.list_actions(s)
    if not rows:
        st.info("No actions yet. Ask the agent what to fix, or propose one from the Experiments tab.")
    for a in rows:
        with st.container(border=True):
            c1, c2 = st.columns([4, 1])
            c1.markdown(f"**{a['status'].upper()}** · {a['title']}  \n_{a['rationale']}_  \n`{a['id']}` · evidence hash `{a['hash']}`")
            if a["status"] == "proposed":
                if c2.button("Approve & run", key=f"ap{a['id']}", disabled=PUBLIC, help="read-only demo" if PUBLIC else None):
                    with st.status("running…", expanded=True) as box:
                        try:
                            st.session_state.last = actions.approve(s, a["id"], progress=box.write)
                        except Exception as exc:
                            st.session_state.last = {"ok": False, "error": str(exc)}
                    st.rerun()
                if c2.button("Reject", key=f"rj{a['id']}", disabled=PUBLIC):
                    actions.reject(s, a["id"]); st.rerun()
            elif a["result"]:
                c1.json(a["result"], expanded=False)
    if st.session_state.get("last"):
        st.json(st.session_state.pop("last"), expanded=True)


def render_graph(s: Store, w: str) -> None:
    st.markdown("**Graph model**")
    st.code("""(:Service)-[:EXPOSES]->(:Operation)        (:Service)-[:CALLS {window,count,errors,p50_ms,p95_ms,per_trace}]->(:Service)
(:Trace)-[:IN]->(:Window)                  (:Span {critical,self_us,peer})-[:OF]->(:Trace)   (:Span)-[:CHILD_OF]->(:Span)   (:Span)-[:ON]->(:Operation)
(:Experiment)-[:TARGETS]->(:Service)  -[:BASELINE|OBSERVED]->(:Window)  -[:PREDICTED_IMPACT|ACTUAL_IMPACT]->(:Operation)
(:Fix)-[:BEFORE|AFTER]->(:Window)          (:Action {payload,hash,status})-[:EXECUTED_AS]->(:Experiment|:Fix)""", language="text")
    _, stats = s.ro("MATCH (n) RETURN count(n)"); _, es = s.ro("MATCH ()-[r]->() RETURN count(r)")
    st.caption(f"{stats[0][0]} nodes, {es[0][0]} edges in `{s.name}`")
    examples = {
        "Critical-path owners (CRISP bottom-up)": f"MATCH (t:Trace {{window:'{w}'}}) WITH collect(t) AS ts, sum(t.duration_ms) AS total UNWIND ts AS t\nMATCH (s:Span {{critical:true}})-[:OF]->(t)\nRETURN s.service, s.op, round(100.0*sum(s.self_us)/1000/total) AS share ORDER BY share DESC",
        "Single points of failure (betweenness)": "CALL algo.betweenness({nodeLabels:['Service'], relationshipTypes:['CALLS']}) YIELD node, score RETURN node.name, score ORDER BY score DESC",
        "Blast radius of stopping route": f"MATCH path = (r:Service)-[:CALLS* {{window:'{w}'}}]->(t:Service {{name:'route'}}) RETURN [n IN nodes(path) | n.name] AS path",
        "Prediction vs outcome per experiment": "MATCH (e:Experiment)-[p:PREDICTED_IMPACT]->(o)<-[a:ACTUAL_IMPACT]-(e) RETURN e.target, o.name, p.outcome, a.fail_rate, a.failed_fast, e.verdict",
        "Silent retries": f"MATCH (c1:Span {{window:'{w}', error:true}})-[:CHILD_OF]->(p)<-[:CHILD_OF]-(c2:Span) WHERE c1.op = c2.op AND c2.start_us > c1.start_us RETURN c1.service, c1.op, count(DISTINCT c1)",
    }
    pick = st.selectbox("Example queries", list(examples))
    q = st.text_area("Cypher (read-only)", examples[pick], height=110)
    if st.button("Run"):
        try:
            header, rows = s.ro(q)
            st.dataframe(pd.DataFrame(rows, columns=header), hide_index=True, width="stretch")
        except Exception as exc:
            st.error(str(exc))


def main() -> None:
    try:
        s = store(); s.q("RETURN 1")
    except Exception as exc:
        st.error(f"Cannot connect to FalkorDB at {config.FALKORDB_HOST}:{config.FALKORDB_PORT}: {exc.__class__.__name__}"); store.clear(); return
    wins = ingest.windows(s)
    if not wins:
        st.warning("No windows yet: `make up && make baseline`."); return
    if "agent" not in st.session_state:
        st.session_state.agent = Agent(s); st.session_state.chat = []
    with st.sidebar:
        st.title("⚡ Faultline")
        st.caption("The graph decides what to break and what to fix. A human approves. The system is changed and re-measured.")
        labels = {f"{x['label']} · {x['id']} · {x['traces']} traces": x["id"] for x in reversed(wins)}
        w = labels[st.selectbox("Window", list(labels))]
        if PUBLIC:
            st.info("Read-only public demo: experiments and fixes are recorded runs; approvals are disabled.")
        st.markdown(f"Model: `{st.session_state.agent.model or 'offline (no key)'}` · graph `{s.name}`")
    tabs = st.tabs(["Findings & critical path", "Experiments & fixes", "Agent", "Actions (human approval)", "Graph & Cypher"])
    with tabs[0]:
        render_overview(s, w)
    with tabs[1]:
        render_ledger(s, w)
    with tabs[2]:
        render_agent(s)
    with tabs[3]:
        render_actions(s)
    with tabs[4]:
        render_graph(s, w)


main()
