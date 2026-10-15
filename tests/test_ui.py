"""UI smoke tests via Streamlit's AppTest (needs FalkorDB with the demo graph loaded)."""
import pytest
from streamlit.testing.v1 import AppTest

from reachable import config
from reachable.graph import Store

try:
    DB_UP = Store().exists()
except Exception:
    DB_UP = False

pytestmark = pytest.mark.skipif(not DB_UP, reason="demo graph not loaded (python -m reachable.cli ingest)")
APP = str(config.ROOT / "streamlit_app.py")


def test_example_button_asks_exactly_once():
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    at.session_state.agent.live = False  # keep the test offline and deterministic
    before = len(at.session_state.agent.box.trace)
    next(b for b in at.button if b.label.startswith("What should we fix first")).click().run()
    assert len(at.session_state.chat) == 2
    at.run()  # an unrelated rerun must not re-send the prompt
    assert len(at.session_state.chat) == 2
    assert len(at.session_state.agent.box.trace) == before + 1
