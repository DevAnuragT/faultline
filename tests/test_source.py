"""Source reading and code-change proposals, against a local fake checkout (no network)."""
import json
from pathlib import Path

import pytest

from faultline import actions, config, ingest, source
from faultline.graph import Store
from faultline.jaeger import normalise

FIX = Path(__file__).parent / "fixtures" / "hotrod_traces.json"
GO = '''package customer

import "time"

// Get returns a customer.
func (d *database) Get(ctx context.Context, customerID int) (*Customer, error) {
	d.tracer.Start(ctx, "SQL SELECT")
	if !d.lock.Lock(ctx, customerID) {
		return nil, errTimeout
	}
	delay.Sleep(d.fixDBDelay, d.fixDBDelay/10)
	return d.customers[customerID], nil
}

func other() {
	return
}
'''


@pytest.fixture(scope="module")
def st(tmp_path_factory):
    root = tmp_path_factory.mktemp("src")
    (root / "services/customer").mkdir(parents=True)
    (root / "services/customer/database.go").write_text(GO)
    source.SOURCE_ROOT = str(root)
    config.GRAPH_NAME = "faultline_test_source"
    try:
        s = Store(); s.q("RETURN 1")
    except Exception:
        pytest.skip("FalkorDB not reachable")
    s.reset()
    ingest.ingest_window(s, "baseline", traces=[normalise(t) for t in json.loads(FIX.read_text())["data"]], progress=lambda *_: None)
    yield s
    s.reset()
    source.SOURCE_ROOT = ""


def test_read_source_returns_only_the_function(st):
    r = source.read("mysql", "SQL SELECT")
    assert r["file"] == "services/customer/database.go" and r["function"] == "database.Get"
    assert r["start_line"] == 6 and r["end_line"] == 13 and "delay.Sleep" in r["code"] and "func other" not in r["code"]
    assert "error" in source.read("nope", "x")


def test_patch_must_match_the_function_and_becomes_a_pr(st, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    bad = "--- a/f\n+++ b/f\n-\tsomething := invented()\n+\tsomething := real()\n"
    r = actions.propose_code_change(st, "mysql", "SQL SELECT", bad, "hallucinated change")
    assert r["ok"] is False and "not in the function" in r["error"]
    assert actions.propose_code_change(st, "mysql", "SQL SELECT", "--- a/f\n+++ b/f\n", "empty")["ok"] is False
    good = "--- a/services/customer/database.go\n+++ b/services/customer/database.go\n-\tdelay.Sleep(d.fixDBDelay, d.fixDBDelay/10)\n+\t// delay removed: the query is served from cache\n"
    p = actions.propose_code_change(st, "mysql", "SQL SELECT", good, "Remove the artificial DB delay")
    assert p["ok"] and "database.Get" in p["title"]
    a = actions.approve(st, p["action"])
    assert a["ok"] and any(f.endswith("database.go.patch") for f in a["files"]) and any(f.endswith("README.md") for f in a["files"])
    readme = (tmp_path / a["written"] / f"proposals/code/{p['action']}/README.md").read_text()
    assert "delay.Sleep" in readme and "code hash" in readme
    # the source changes under the proposal -> approval refuses as stale
    p2 = actions.propose_code_change(st, "mysql", "SQL SELECT", good, "again")
    (Path(source.SOURCE_ROOT) / "services/customer/database.go").write_text(GO.replace("d.fixDBDelay/10", "d.fixDBDelay/5"))
    r2 = actions.approve(st, p2["action"])
    assert r2["ok"] is False and "changed" in r2["error"]


def test_agent_cannot_propose_code_it_did_not_read_and_flags_unread_code(st):
    from faultline.agent import Agent, Toolbox
    box = Toolbox(st)
    patch = "--- a\n+++ b\n-\td.tracer.Start(ctx, \"SQL SELECT\")\n+\td.tracer.Start(ctx, \"SQL SELECT customer\")\n"
    r = box.propose_code_change("mysql", "SQL SELECT", patch, "x")
    assert r["ok"] is False and "read_source" in r["error"]
    assert "code" in box.read_source("mysql", "SQL SELECT")
    r2 = box.propose_code_change("mysql", "SQL SELECT", patch, "x")
    assert r2["ok"] and r2["status"] == "proposed"
    warned = Agent._honesty("Here:\n```go\nfunc x() {}\n```", [{"tool": "overview", "args": {}, "result": {}}])
    assert warned.startswith("(Warning:")
    assert Agent._honesty("plain text", []) == "plain text"
    assert not Agent._honesty("```go\nx\n```", [{"tool": "read_source", "args": {}, "result": {"code": "x"}}]).startswith("(Warning")
