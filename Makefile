PY=.venv/bin/python
.PHONY: venv up down baseline experiment fix revert report test
venv:
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
up:
	docker compose up -d
down:
	docker compose down
baseline:  ## 40 parallel /dispatch requests, ingested as a Window with critical paths and findings
	$(PY) -m faultline.cli baseline
experiment:  ## pause the route container, compare the graph's predicted blast radius with what happened
	$(PY) -m faultline.cli experiment route
fix:  ## apply HotROD's MySQL-mutex fix and verify it in the graph
	$(PY) -m faultline.cli fix --flags=-M
revert:
	$(PY) -m faultline.cli revert
report:
	$(PY) -m faultline.cli report
test:
	$(PY) -m pytest tests -q
