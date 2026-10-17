"""Approval ledger and planner on the recorded fixture graph (no docker, no Gemini)."""
import json
from pathlib import Path

import pytest

from faultline import actions, analysis, config, experiments, guard, ingest, planner
from faultline.graph import Store
from faultline.jaeger import normalise

FIX = Path(__file__).parent / "fixtures" / "hotrod_traces.json"


@pytest.fixture(scope="module")
def st():
    config.GRAPH_NAME = "faultline_test_actions"
    try:
        s = Store(); s.q("RETURN 1")
    except Exception:
        pytest.skip("FalkorDB not reachable")
    s.reset()
    traces = [normalise(t) for t in json.loads(FIX.read_text())["data"]]
    ingest.ingest_window(s, "baseline", traces=traces, progress=lambda *_: None)
    yield s
    s.reset()


def test_hypothesis_and_ranking_come_from_the_graph(st):
    w = ingest.latest_window(st, "baseline")
    hyp = experiments.hypothesis(st, w, "route")
    assert "GET /dispatch" in hyp["text"] and hyp["predicted"][0]["op"] == "GET /dispatch"
    ranked = planner.rank_experiments(st, w)
    assert ranked[0]["target"] in ("customer", "driver"), "transit services rank above leaves and the edge"
    assert next(r for r in ranked if r["target"] == "frontend")["reasons"][-1].startswith("edge service")
    assert all(r["priority"] > 0 and r["skip"] is None for r in ranked if r["target"] != "frontend")
    g = planner.guardrails(st, w)
    assert any(x["hop"] == "frontend -> route" and x["kind"] == "timeout" for x in g)


def test_propose_approve_is_bound_to_evidence(st, monkeypatch):
    p = actions.propose_experiment(st, "route")
    assert p["ok"] and p["status"] == "proposed"
    again = actions.propose_experiment(st, "route")
    assert again["action"] == p["action"] and "already" in again["note"]
    assert actions.propose_experiment(st, "mysql")["ok"] is False
    # evidence changes: a new baseline window appears -> approval must refuse as stale, never run docker
    traces = [normalise(t) for t in json.loads(FIX.read_text())["data"]][:3]
    ingest.ingest_window(st, "baseline", traces=traces, progress=lambda *_: None)
    monkeypatch.setattr(experiments, "run_pause", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not execute")))
    r = actions.approve(st, p["action"])
    assert r["ok"] is False and "changed" in r["error"]
    assert actions.list_actions(st, "stale")[0]["id"] == p["action"]


def test_public_mode_cannot_propose_or_execute(st, monkeypatch):
    p = actions.propose_fix(st, ["-M"])
    assert p["ok"]
    monkeypatch.setattr(config, "PUBLIC_MODE", True)
    with pytest.raises(guard.ReadOnlyError):
        actions.approve(st, p["action"])
    r = actions.propose_experiment(st, "driver")
    assert r["ok"] is False and "read-only" in r["error"]
    n_before = st.q("MATCH (a:Action) RETURN count(a)")[0][0]
    assert actions.propose_fix(st, ["-W", "100"])["ok"] is False
    assert st.q("MATCH (a:Action) RETURN count(a)")[0][0] == n_before, "public visitors must not write to the shared graph"
    monkeypatch.setattr(config, "PUBLIC_MODE", False)
    assert actions.reject(st, p["action"], "not now")["status"] == "rejected"


def test_refuted_experiment_blocks_target_until_reviewed(st):
    w = ingest.latest_window(st, "baseline")
    st.q("MATCH (s:Service {name:'driver'}) CREATE (e:Experiment {id:'exp-x', kind:'pause', target:'driver', hypothesis:'h', verdict:'REFUTED', lesson:'l', ts:0})-[:TARGETS]->(s)")
    r = next(x for x in planner.rank_experiments(st, w) if x["target"] == "driver")
    assert r["skip"] and "REFUTED" in r["skip"] and r["priority"] == 0
    actions.review_experiment(st, "exp-x")
    r = next(x for x in planner.rank_experiments(st, w) if x["target"] == "driver")
    assert r["skip"] is None and "tested before" in r["reasons"][-1]


def test_guardrail_proposal_writes_reviewable_yaml_on_approval(st, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    assert actions.propose_guardrail(st, "nowhere -> nothing")["ok"] is False
    p = actions.propose_guardrail(st, "frontend -> route")
    assert p["ok"] and "timeout" in p["title"]
    r = actions.approve(st, p["action"])
    assert r["ok"] and r["written"].endswith("frontend_to_route.yaml")
    text = (tmp_path / r["written"]).read_text()
    assert "kind: VirtualService" in text and "timeout:" in text and "host: route" in text


def test_agent_prefers_claude_when_a_key_is_present_and_degrades_to_graph_tools(st, monkeypatch):
    from faultline.agent import Agent
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    ag = Agent(st)
    assert ag.provider == "claude" and ag.model.startswith("claude-")
    reply, trace = ag.ask("What is slow and why?")
    if "APIConnectionError" in reply:
        pytest.skip("no network")
    assert reply.startswith("(Claude unavailable: AuthenticationError") and trace[0]["tool"] == "overview" and "mysql" in reply


def test_incident_issue_and_pr_proposals(st, tmp_path, monkeypatch):
    from faultline import code
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / "docker-compose.yml").write_text((Path(__file__).parent.parent / "docker-compose.yml").read_text())
    traces = [normalise(t) for t in json.loads(FIX.read_text())["data"]]
    healthy = [t for t in traces if max(s["duration_us"] for s in t["spans"]) < 5_000_000]
    base_w = ingest.ingest_window(st, "baseline", traces=healthy, progress=lambda *_: None)["window"]
    bad_w = ingest.ingest_window(st, "pause-route", traces=traces, progress=lambda *_: None)["window"]
    inc = analysis.incident(st, bad_w)
    assert inc and inc["endpoint"] == "GET /dispatch" and inc["kind"] == "failures" and inc["baseline"] == base_w
    assert inc["owner"]["service"] == "frontend" and inc["hop"] == "frontend -> route" and inc["worst_trace"]["duration_ms"] > 10000
    assert code.pointer("mysql", "SQL SELECT")["file"].endswith("database.go") and code.pointer("nope", "x") is None
    assert analysis.incident(st, base_w) is None
    assert actions.propose_issue(st, base_w)["ok"] is False
    p = actions.propose_issue(st, bad_w)
    assert p["ok"]
    r = actions.approve(st, p["action"])
    assert r["ok"] and r["written"].endswith(f"{bad_w}.md") and "frontend -> route" in (tmp_path / r["written"]).read_text()
    # a PR for an approved guardrail
    g = actions.propose_guardrail(st, "frontend -> route"); actions.approve(st, g["action"])
    pr = actions.propose_pr(st, g["action"])
    assert pr["ok"] and "2 files" in pr["title"]
    assert actions.propose_pr(st, "nothing-here")["ok"] is False
    r = actions.approve(st, pr["action"])
    assert r["ok"] and set(r["files"]) == {"deploy/guardrails/frontend_to_route.yaml", f"docs/changes/{g['action']}.md"}
    assert (tmp_path / r["written"] / "PR_BODY.md").exists()
    # a PR for a VERIFIED fix recorded in the graph
    st.q("MATCH (b:Window {id:$b}), (a:Window {id:$a}), (s:Service {name:'customer'}) CREATE (f:Fix {id:'fix-t', service:'customer', flags:'-M', verdict:'VERIFIED', gates:['PASS x'], next_owner:'n', ts:1})-[:TARGETS]->(s) CREATE (f)-[:BEFORE]->(b) CREATE (f)-[:AFTER]->(a)", {"b": base_w, "a": bad_w})
    pr2 = actions.propose_pr(st, "fix-t"); r2 = actions.approve(st, pr2["action"])
    assert r2["ok"] and "deploy/docker-compose.fix.yml" in r2["files"] and "-M" in (tmp_path / r2["written"] / "deploy/docker-compose.fix.yml").read_text()


def test_remedies_come_from_evidence_and_hypotheses_can_be_refuted(st):
    w = ingest.latest_window(st, "baseline")
    fan = analysis.serialized_fanout(st, w)
    assert fan and fan[0]["hop"] == "frontend -> route" and fan[0]["max_concurrency"] <= 3 and fan[0]["rounds"] >= 2
    rs = planner.remedies(st, w)
    kinds = {(r["kind"], r["service"]) for r in rs}
    assert ("serialized_fanout", "frontend") in kinds and ("per_call_cost", "mysql") in kinds
    assert all(r["evidence"] and r["change"] for r in rs)
    nf = planner.next_fix(st, w)
    assert nf and nf["flags"] in (["-D", "100ms"], ["-W", "100"]) and nf["evidence"] and nf["kind"] in ("per_call_cost", "serialized_fanout")
    h = experiments.hypothesis(st, w, "customer")
    assert "driver" in h["starved"] and "route" in h["starved"] and h["keep_answering"] == []
    assert h["frozen_with"] == ["mysql"] and "mysql" not in h["starved"] and "freeze with it" in h["text"]
    h2 = experiments.hypothesis(st, w, "route")
    assert set(h2["keep_answering"]) >= {"customer", "driver"} and h2["starved"] == []
    assert "keep answering" in h2["text"] and "almost no calls" in h["text"]


def test_contention_is_detected_from_in_flight_overlap(st):
    """24 requests, four at a time, whose DB span grows by 250 ms per concurrent call: the signature of a lock."""
    traces, t = [], 0
    for batch in range(6):
        for k in range(4):
            start = t + k * 1000
            db = 300_000 + 250_000 * k
            traces.append({"id": f"c{batch}{k}", "spans": [
                {"id": f"r{batch}{k}", "parent": None, "service": "frontend", "op": "GET /dispatch", "start_us": start, "duration_us": db + 2000, "kind": "server", "error": False, "status": "200", "attrs": {}},
                {"id": f"d{batch}{k}", "parent": f"r{batch}{k}", "service": "mysql", "op": "SQL SELECT", "start_us": start + 1000, "duration_us": db, "kind": "internal", "error": False, "status": None, "attrs": {"db.system.name": "mysql"}}]})
        t += 2_000_000
    w = ingest.ingest_window(st, "synthetic", traces=traces, progress=lambda *_: None)["window"]
    c = analysis.contention(st, w)
    assert c and c[0]["service"] == "mysql" and c[0]["per_inflight_ms"] > 150 and c[0]["correlation"] > 0.8
    r = planner.remedies(st, w)[0]
    assert r["kind"] == "contention" and r["actuator"]["flags"] == ["-M"] and "per concurrent call" in r["evidence"]


def test_agent_prefers_openrouter_over_gemini_and_degrades(st, monkeypatch):
    from faultline.agent import Agent
    from faultline import openrouter_agent
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "not-a-real-key")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    ag = Agent(st)
    assert ag.provider == "openrouter" and ag.model == config.OPENROUTER_MODEL
    schemas = ag._router.schemas
    assert any(s["function"]["name"] == "propose_code_change" and set(s["function"]["parameters"]["required"]) == {"service", "op", "patch", "summary"} for s in schemas)
    reply, trace = ag.ask("What is slow and why?")
    if "APIConnectionError" in reply:
        pytest.skip("no network")
    assert reply.startswith("(OpenRouter unavailable:") and trace[0]["tool"] == "overview" and "mysql" in reply
