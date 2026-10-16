"""The Streamlit app renders every tab against the recorded fixture graph without exceptions."""
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from faultline import config, ingest
from faultline.graph import Store
from faultline.jaeger import normalise

FIX = Path(__file__).parent / "fixtures" / "hotrod_traces.json"


@pytest.fixture(scope="module")
def graph():
    config.GRAPH_NAME = "faultline_test_ui"
    try:
        s = Store(); s.q("RETURN 1")
    except Exception:
        pytest.skip("FalkorDB not reachable")
    s.reset()
    ingest.ingest_window(s, "baseline", traces=[normalise(t) for t in json.loads(FIX.read_text())["data"]], progress=lambda *_: None)
    yield s
    s.reset()


def test_all_tabs_render(graph, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")  # offline agent
    at = AppTest.from_file(str(Path(__file__).parent.parent / "streamlit_app.py"), default_timeout=60).run()
    assert not at.exception, at.exception
    body = " ".join(m.value for m in at.markdown)
    assert "critical owner" in body and "hang not fail" in body
    assert any("Experiment planner" in m.value for m in at.markdown)
    assert len(at.tabs) == 5
    assert at.sidebar.selectbox[0].options and "baseline" in at.sidebar.selectbox[0].value


def test_agent_offline_answers_from_graph(graph, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    at = AppTest.from_file(str(Path(__file__).parent.parent / "streamlit_app.py"), default_timeout=60).run()
    preloaded = len(at.session_state["chat"])  # the recorded conversation, if data/frozen/agent.json exists
    assert preloaded % 2 == 0 and all(t[2] for t in at.session_state["chat"] if t[0] == "assistant"), "recorded turns carry their tool calls"
    at.button(key="ex0").click().run()
    assert not at.exception
    chat = at.session_state["chat"]
    assert len(chat) == preloaded + 2 and chat[-1][0] == "assistant" and chat[-1][2][0]["tool"] == "overview"
    assert "mysql" in chat[-1][1]
