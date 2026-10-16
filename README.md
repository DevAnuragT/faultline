# Faultline

**An agent that learns a system's shape from its traces, keeps it in FalkorDB, and uses the graph to decide what to
break, what to fix, and how to prove it.**

Status: day 1 (Oct 16, 2026). See [docs/ANALYSIS.md](docs/ANALYSIS.md) for the research, the graph model and the plan.

```
make up          # Jaeger + HotROD (4 service containers) + FalkorDB
make baseline    # 40 parallel /dispatch requests -> a Window in FalkorDB with critical paths and findings
make report      # critical-path owners, single points of failure, fan-out, hangs
make experiment  # pause `route`: the graph predicts the blast radius, the run confirms or refutes it
make fix         # apply HotROD's -M fix to `customer`; four gates decide VERIFIED / REJECTED; the graph names the next owner
make run         # Streamlit UI: findings, experiment planner, agent, human approval, Cypher

.venv/bin/python -m faultline.cli agent "What should we fix first?"   # Gemini proposes; nothing runs
.venv/bin/python -m faultline.cli actions                              # the proposal, with its evidence hash
.venv/bin/python -m faultline.cli approve act-xxxxxxxx                 # human approval -> apply -> re-measure -> verdict
```

**How it decides.** Critical paths (CRISP) say who owns the latency. Betweenness and reverse reachability say who is a
single point of failure and what breaks if it stops: that prediction is the hypothesis of a chaos experiment, and the
observed window confirms or refutes it. Fixes are judged by gates between two windows and rolled back if rejected.
The experiment planner ranks targets the way Netflix's Monocle did (criticality × safety, recency, review gate), with
the experiment ledger in the graph as its memory. The agent (Gemini, tool-calling) only reads the graph and proposes;
a human approves; approval is bound to a hash of the evidence.

Measured on Oct 16 (see `docs/ANALYSIS.md` for the research):

- Baseline: MySQL owns 64% of steady-state critical-path latency; Redis is called 13.5 times per request; every path runs through `customer` and `driver`.
- Pause `route`: the graph predicted `GET /dispatch` fails; it did, but it **hung for 35 s instead of failing**. Lesson recorded: the hop into `route` has no timeout.
- Fix `-M` (customer): p50 1191 → 788 ms, VERIFIED on four gates; the graph says MySQL still owns 34%, so the next fix is `-D`.
- Fix `-W 100` (frontend): p50 788 → 593 ms, VERIFIED; route's queueing time on the critical path gone.
