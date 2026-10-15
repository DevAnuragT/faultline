.PHONY: setup test demo run mcp seed

PY ?= .venv/bin/python

setup:  ## create the virtualenv (Python 3.11+) and install dependencies
	uv venv --python 3.12 .venv && uv pip install --python $(PY) -r requirements.txt pytest

test:  ## needs FalkorDB on localhost:6379 (docker run -d -p 6379:6379 falkordb/falkordb)
	$(PY) -m pytest tests

demo:  ## clean ingest into a throwaway graph; prints every number the pitch uses
	$(PY) -m reachable.demo

seed:  ## (re)build the demo graph, run the closed loop, record the what-if for the public site
	$(PY) -m reachable.cli seed

run:
	.venv/bin/streamlit run streamlit_app.py --server.headless true

mcp:  ## MCP server over stdio (pip install -r requirements-mcp.txt first)
	$(PY) -m reachable.mcp_server
