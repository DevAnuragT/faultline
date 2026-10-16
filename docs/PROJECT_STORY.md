# Faultline: project story

Solo entry, Track 1 (Agents That Act on Connected Data). Built Oct 16–18, 2026 with AI coding assistants
(Claude via Claude Code, Copilot CLI); the design decisions, the measurements and the verification are mine, and I can
explain every query.

## Inspiration

Every team I have worked with collects traces and looks at them one request at a time. Nobody asks the traces the
questions that matter: which service actually owns our latency, what breaks if this one stops, did our last change
move the bottleneck or just move it somewhere else. Those are graph questions. And the tools that *act* on
reliability, chaos experiments and configuration fixes, are driven by hand-written hypotheses, even though Netflix
showed in 2019 (Monocle) that the hypothesis can be derived from the dependency graph.

## What it does

Faultline turns a window of Jaeger/OpenTelemetry traces into a FalkorDB graph: services, operations, spans with
CRISP critical paths, per-window call edges. From that graph it computes who owns the latency, which services are
single points of failure, which hops fan out or retry silently, and what would break if a service stopped. Then it
acts: it ranks and runs fault experiments whose hypothesis is the graph's prediction, and it proposes and applies
fixes that four gates verify or reject (and roll back). A Gemini agent reads the graph and proposes; a human approves;
approval is bound to a hash of the evidence.

## How it was built

Python, FalkorDB, Jaeger's HotROD demo running as four containers, Streamlit. Core on day one: ingest, critical path,
findings, experiments, fixes. Day two: the agent, the approval ledger, the Monocle-style planner, guardrails, retry
detection, UI, recorded public mode.

## What I measured (all reproducible with `make up && make baseline && make experiment && make fix`)

- Baseline: MySQL 64% of critical-path latency, Redis 13.5 calls per request, no redundancy anywhere.
- Pause route: predicted `GET /dispatch` fails, confirmed, and it hung 35 s instead of failing fast.
- `-M`: p50 1191 → 788 ms, VERIFIED. `-W 100`: 788 → 593 ms, VERIFIED. `-D 100ms`: 593 → 390 ms, REJECTED once by a
  strict failure gate on one flaky request (a real HotROD retry-exhaustion 500), which produced the one-request
  tolerance and the automatic rollback.

## Challenges

- A hung request is not a slow request. One 30 s wait dominated every average until critical-path shares excluded
  hangs by default and experiments were judged against the *baseline* median.
- The latest window is not always the truth: after an experiment, the newest window describes a broken system. Every
  default now means "the newest steady-state window".
- LLM honesty. The agent once claimed a tool had failed when it had never called it. Now every tool is wrapped so a
  failure is logged and returned, and the prompt forbids describing actions without a tool call.

## What's next

Real timeouts and circuit breakers as proposable changes (the guardrails table already computes them), OpenTelemetry
Collector input instead of the Jaeger API, and the security side of the same graph: trust boundaries and data stores
reachable from the edge.
