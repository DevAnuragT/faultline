"""HTTP API + static site. Run: uvicorn faultline.api:app --port 8600"""
from __future__ import annotations

import json
import threading
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import actions, analysis, config, experiments, frozen, ingest, planner
from .agent import Agent, Toolbox
from .graph import Store

app = FastAPI(title="Faultline")
WEB = config.ROOT / "web"
_store: Store | None = None
_agents: dict[str, Agent] = {}
_jobs: dict[str, dict] = {}


def st() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def _w(window: str | None) -> str:
    return window or ingest.latest_window(st())


@app.get("/api/meta")
def meta():
    return {"public": config.PUBLIC_MODE, "graph": config.GRAPH_NAME, "model": Agent(st()).model or "offline",
            "edge": config.EDGE_SERVICES, "recorded": frozen.load_agent() is not None,
            "slo": {"endpoint": f"GET /dispatch", "p95_ms": config.SLO_P95_MS, "fail_rate": config.SLO_FAIL_RATE} if (config.SLO_P95_MS or config.SLO_FAIL_RATE) else None}


@app.get("/api/windows")
def windows():
    s = st()
    _, rows = s.ro("MATCH (w:Window) RETURN w.id, w.label, w.traces, w.created, w.start_us, w.end_us, coalesce(w.steady, true) ORDER BY w.created")
    ws, last_steady = [], None
    for wid, label, traces, created, start_us, end_us, steady in rows:
        kind = "experiment" if label.startswith("pause-") else "steady" if steady else "rejected"
        h = analysis.root_health(s, wid)
        ws.append({"id": wid, "label": label, "traces": traces, "created": created, "start_us": start_us, "end_us": end_us, "kind": kind,
                   "baseline": last_steady, "health": {k: h[0][k] for k in ("op", "p50_ms", "p95_ms", "fail_rate", "traces")} if h else None})
        if kind == "steady":
            last_steady = wid
    return {"windows": ws, "steady": ingest.latest_window(s) if ws else None}


@app.get("/api/status")
def status(window: str | None = None):
    return analysis.status(st(), _w(window))


@app.get("/api/traces")
def traces(window: str | None = None, order: str = "duration", errors: bool = False, limit: int = 20):
    return {"window": _w(window), "traces": analysis.traces(st(), _w(window), order, errors, min(limit, 100))}


@app.get("/api/overview")
def overview(window: str | None = None):
    s, w = st(), _w(window)
    return {"window": w, "health": analysis.root_health(s, w), "owners": analysis.critical_owners(s, w),
            "graph": analysis.service_graph(s, w), "findings": analysis.with_cypher(analysis.findings(s, w), w), "exposure": analysis.exposure(s, w),
            "retries": analysis.retries(s, w), "guardrails": planner.guardrails(s, w), "spof": analysis.single_points_of_failure(s, w)}


@app.get("/api/chain")
def chain(window: str | None = None, root_op: str | None = None):
    s, w = st(), _w(window)
    c = analysis.critical_chain(s, w, root_op)
    return {"window": w, "chain": c, "waterfall": analysis.trace_waterfall(s, c[0]["trace"]) if c else None}


@app.get("/api/trace/{trace_id}")
def trace(trace_id: str):
    return analysis.trace_waterfall(st(), trace_id)


@app.get("/api/planner")
def plan(window: str | None = None):
    s, w = st(), _w(window)
    return {"window": w, "experiments": planner.rank_experiments(s, w), "next_fix": planner.next_fix(s, w), "remedies": planner.remedies(s, w),
            "actions": planner.actions(s, w)}


@app.get("/api/ledger")
def ledger():
    s = st()
    _, ex = s.ro("""MATCH (e:Experiment)-[:TARGETS]->(sv) OPTIONAL MATCH (e)-[a:ACTUAL_IMPACT]->(o:Operation)
                    OPTIONAL MATCH (e)-[:BASELINE]->(b) OPTIONAL MATCH (e)-[:OBSERVED]->(ob)
                    RETURN e.id, sv.name, e.hypothesis, e.verdict, e.lesson, e.ts, b.id, ob.id,
                           collect({op:o.name, fail_rate:a.fail_rate, p50_ms:a.p50_ms, failed_fast:a.failed_fast}), e.parts ORDER BY e.ts DESC""")
    _, fx = s.ro("MATCH (f:Fix)-[:BEFORE]->(b) MATCH (f)-[:AFTER]->(a) RETURN f.id, f.service, f.flags, f.verdict, f.gates, f.next_owner, b.id, a.id, f.ts ORDER BY f.ts DESC")
    return {"experiments": [{"id": r[0], "target": r[1], "hypothesis": r[2], "verdict": r[3], "lesson": r[4], "ts": r[5], "baseline": r[6], "observed": r[7],
                             "impact": [x for x in r[8] if x["op"]],
                             "parts": [{"passed": g.startswith("PASS"), "text": g[5:]} for g in (r[9] or [])]} for r in ex],
            "fixes": [{"id": r[0], "service": r[1], "flags": r[2], "verdict": r[3], "gates": [{"passed": g.startswith("PASS"), "text": g[5:]} for g in r[4]],
                       "next_owner": r[5], "before": r[6], "after": r[7], "ts": r[8]} for r in fx]}


@app.get("/api/incidents")
def incidents():
    return {"incidents": analysis.incidents(st())}


@app.get("/api/compare")
def compare(a: str, b: str):
    return planner.diff_windows(st(), a, b)


@app.get("/api/actions")
def list_actions():
    return {"actions": actions.list_actions(st()), "jobs": _jobs}


class Proposal(BaseModel):
    type: str
    target: str = ""
    flags: str = ""
    hop: str = ""
    window: str = ""
    subject: str = ""
    service: str = ""
    op: str = ""
    patch: str = ""
    summary: str = ""
    rationale: str = ""


@app.post("/api/propose")
def propose(p: Proposal):
    s = st()
    if p.type == "experiment":
        return actions.propose_experiment(s, p.target, p.rationale)
    if p.type == "fix":
        return actions.propose_fix(s, p.flags.split(), p.rationale)
    if p.type == "guardrail":
        return actions.propose_guardrail(s, p.hop, p.rationale)
    if p.type == "issue":
        return actions.propose_issue(s, p.window, p.rationale)
    if p.type == "pr":
        return actions.propose_pr(s, p.subject, p.rationale)
    if p.type == "code_change":
        return actions.propose_code_change(s, p.service, p.op, p.patch, p.summary, p.rationale)
    raise HTTPException(400, "type must be experiment, fix, guardrail, issue, pr or code_change")


@app.get("/api/source")
def read_source(service: str, op: str):
    from faultline import source
    return source.read(service, op)


@app.post("/api/actions/{action_id}/approve")
def approve(action_id: str):
    """Approval runs in a thread (an experiment takes a minute); the client polls /api/actions for the job log."""
    if config.PUBLIC_MODE:
        raise HTTPException(403, "executing an action is disabled in the read-only public demo")
    if action_id in _jobs and _jobs[action_id]["status"] == "running":
        return _jobs[action_id]
    job = _jobs[action_id] = {"status": "running", "log": [], "result": None}

    def run():
        try:
            job["result"] = actions.approve(st(), action_id, progress=lambda m: job["log"].append(str(m)))
            job["status"] = "done"
        except Exception as exc:
            job["result"] = {"ok": False, "error": str(exc)[:300]}
            job["status"] = "failed"

    threading.Thread(target=run, daemon=True).start()
    return job


@app.post("/api/actions/{action_id}/reject")
def reject(action_id: str):
    if config.PUBLIC_MODE:
        raise HTTPException(403, "disabled in the read-only public demo")
    return actions.reject(st(), action_id)


@app.get("/api/agent/recorded")
def recorded():
    return frozen.load_agent() or {"turns": []}


class Ask(BaseModel):
    session: str = ""
    question: str


@app.post("/api/agent/ask")
def ask(a: Ask):
    sid = a.session or uuid.uuid4().hex[:8]
    ag = _agents.setdefault(sid, Agent(st()))
    reply, trace = ag.ask(a.question)
    return {"session": sid, "model": ag.model or "offline", "reply": reply,
            "trace": [{"tool": t["tool"], "args": t["args"], "summary": frozen._compact(t["result"])} for t in trace]}


class Query(BaseModel):
    cypher: str


@app.post("/api/query")
def query(q: Query):
    return Toolbox(st()).query_graph(q.cypher)


@app.get("/api/model")
def model():
    readme = (config.ROOT / "README.md").read_text()
    block = readme.split("## Graph model")[1].split("```")[1]
    return {"text": block.strip("\n")}


@app.middleware("http")
async def no_stale_static(request, call_next):
    """The console is one HTML file plus two assets; never let a browser keep an old app.js after a deploy."""
    response = await call_next(request)
    if request.url.path in ("/", "/index.html", "/app.js", "/styles.css"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")
