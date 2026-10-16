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


def test_public_mode_cannot_execute(st, monkeypatch):
    p = actions.propose_fix(st, ["-M"])
    assert p["ok"]
    monkeypatch.setattr(config, "PUBLIC_MODE", True)
    with pytest.raises(guard.ReadOnlyError):
        actions.approve(st, p["action"])
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
