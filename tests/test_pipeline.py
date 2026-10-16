"""End-to-end against a local FalkorDB using a recorded HotROD trace (no Jaeger needed)."""
import json
import os
from pathlib import Path

import pytest

from faultline import analysis, config, ingest
from faultline.graph import Store

FIX = Path(__file__).parent / "fixtures" / "hotrod_traces.json"


@pytest.fixture(scope="module")
def st():
    config.GRAPH_NAME = "faultline_test"
    try:
        s = Store(); s.q("RETURN 1")
    except Exception:
        pytest.skip("FalkorDB not reachable on localhost")
    s.reset()
    yield s
    s.reset()


def test_ingest_and_analyse_recorded_traces(st):
    from faultline.jaeger import normalise
    traces = [normalise(t) for t in json.loads(FIX.read_text())["data"]]
    r = ingest.ingest_window(st, "fixture", traces=traces, progress=lambda *_: None)
    w = r["window"]
    sg = analysis.service_graph(st, w)
    assert {(e["from"], e["to"]) for e in sg} >= {("frontend", "customer"), ("frontend", "driver"), ("frontend", "route"), ("customer", "mysql"), ("driver", "redis-manual")}
    owners = analysis.critical_owners(st, w)
    assert owners and abs(sum(o["share_pct"] for o in owners) - 100) < 1.0, "critical self times must sum to end-to-end latency"
    spof = {s["service"]: s["betweenness"] for s in analysis.single_points_of_failure(st, w)}
    assert spof["customer"] > 0 and spof["driver"] > 0 and spof["mysql"] == 0
    fo = {(f["from"], f["to"]): f["calls_per_request"] for f in analysis.fan_out(st, w)}
    assert fo[("driver", "redis-manual")] > 5 and fo[("frontend", "route")] > 5
    br = analysis.blast_radius(st, w, "route")
    assert br["dependent_services"] == ["frontend"] and br["root_operations"][0]["op"] == "GET /dispatch"
    hangs = analysis.hangs(st, w)
    assert hangs and hangs[0]["duration_ms"] > 10000 and hangs[0]["worst"]["service"] == "frontend" and hangs[0]["worst"]["peer"] == "route"
    peers = {o["peer"] for o in owners if o["service"] == "frontend" and o["op"] == "HTTP GET"}
    assert peers <= {"route", "customer"} and "" not in peers, "client spans must name the service they wait on"
    polluted = analysis.critical_owners(st, w, include_hangs=True)
    assert polluted[0]["service"] == "frontend" and owners[0]["service"] != "frontend", "hangs must not dominate steady-state owners"
    health = analysis.root_health(st, w)
    assert health[0]["op"] == "GET /dispatch" and health[0]["failed"] == 2 and health[0]["traces"] == 5
    kinds = {f["kind"] for f in analysis.findings(st, w)}
    assert {"critical_owner", "fan_out", "hang_not_fail", "single_point_of_failure"} <= kinds
    assert sum(1 for f in analysis.findings(st, w) if f["kind"] == "hang_not_fail") == 1, "identical hangs are one finding"
    n = analysis.record_findings(st, w, analysis.findings(st, w))
    assert n >= len(kinds) and st.q("MATCH (f:Finding {window:$w})-[:ABOUT]->(:Service) RETURN count(f)", {"w": w})[0][0] == n
