"""The HTTP API over the recorded fixture graph (no docker, no Gemini)."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from faultline import config, ingest
from faultline.graph import Store
from faultline.jaeger import normalise

FIX = Path(__file__).parent / "fixtures" / "hotrod_traces.json"


@pytest.fixture(scope="module")
def client():
    config.GRAPH_NAME = "faultline_test_api"
    config.GEMINI_API_KEY = ""
    try:
        s = Store(); s.q("RETURN 1")
    except Exception:
        pytest.skip("FalkorDB not reachable")
    s.reset()
    ingest.ingest_window(s, "baseline", traces=[normalise(t) for t in json.loads(FIX.read_text())["data"]], progress=lambda *_: None)
    from faultline import api
    api._store = s
    yield TestClient(api.app)
    s.reset()


def test_overview_chain_and_planner(client):
    r = client.get("/api/overview").json()
    assert r["health"][0]["op"] == "GET /dispatch" and r["owners"][0]["service"] == "mysql"
    assert {f["kind"] for f in r["findings"]} >= {"critical_owner", "hang_not_fail", "exposure"}
    c = client.get("/api/chain").json()
    assert c["waterfall"]["spans"] and any(s["critical"] for s in c["waterfall"]["spans"])
    assert max(s["depth"] for s in c["waterfall"]["spans"]) >= 2
    p = client.get("/api/planner").json()
    assert p["experiments"][0]["target"] in ("customer", "driver")


def test_site_and_agent_and_actions(client, monkeypatch):
    assert client.get("/").status_code == 200 and "<title>Faultline" in client.get("/").text
    a = client.post("/api/agent/ask", json={"question": "What is slow and why?"}).json()
    assert a["model"] == "offline" and a["trace"][0]["tool"] == "overview" and "mysql" in a["reply"]
    p = client.post("/api/propose", json={"type": "experiment", "target": "route"}).json()
    assert p["ok"]
    assert any(x["id"] == p["action"] for x in client.get("/api/actions").json()["actions"])
    monkeypatch.setattr(config, "PUBLIC_MODE", True)
    assert client.post(f"/api/actions/{p['action']}/approve").status_code == 403
    assert client.post("/api/propose", json={"type": "fix", "flags": "-M"}).json()["ok"] is False
    monkeypatch.setattr(config, "PUBLIC_MODE", False)
    assert client.post(f"/api/actions/{p['action']}/reject").json()["status"] == "rejected"
    q = client.post("/api/query", json={"cypher": "MATCH (s:Service) RETURN count(s)"}).json()
    assert q["rows"][0][0] >= 6 and "error" in client.post("/api/query", json={"cypher": "MATCH (n) DELETE n"}).json()


def test_status_traces_windows_and_actions(client):
    ws = client.get("/api/windows").json()["windows"]
    assert ws[0]["kind"] == "steady" and ws[0]["health"]["op"] == "GET /dispatch" and ws[0]["baseline"] is None and ws[0]["start_us"]
    stt = client.get("/api/status").json()
    assert stt["status"] == "baseline" and stt["baseline"] is None
    tr = client.get("/api/traces?order=duration&limit=3").json()["traces"]
    assert len(tr) == 3 and tr[0]["duration_ms"] >= tr[1]["duration_ms"] and tr[0]["duration_ms"] > 10000
    bad = client.get("/api/traces?errors=true").json()["traces"]
    assert bad and all(t["errors"] > 0 or not str(t["status"]).startswith("2") for t in bad)
    f = client.get("/api/overview").json()["findings"]
    assert all("cypher" in x and x["params"]["w"] for x in f if x["kind"] in ("critical_owner", "fan_out", "hang_not_fail", "exposure"))
    acts = client.get("/api/planner").json()["actions"]
    assert acts and acts == sorted(acts, key=lambda a: a["priority"]) and all(a["title"] and a["why"] for a in acts)
    assert any(a["type"] == "experiment" and a["payload"]["target"] in ("customer", "driver") for a in acts)
