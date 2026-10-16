# Faultline: research and analysis (Oct 16, 2026)

Working title: **Faultline**. An agent that learns the shape of a running system from its traces, stores that shape
in FalkorDB, and uses the graph to decide what to break, what to fix, and how to prove it.

## 1. The idea, restated

Teams already collect traces (OpenTelemetry, Jaeger, Tempo, Elastic APM). Those tools answer "what happened in this one
request". They do not answer the questions an SRE or a security reviewer actually asks:

- Which services sit on the critical path of our slowest endpoints, and how much latency does each one own?
- Which service is a single point of failure for the most traffic?
- If service X hangs, which endpoints fail, and does anything fail *fast*? (timeouts, circuit breakers)
- Which hops amplify load (one request fans out into dozens of calls), and which of those cross a trust boundary?
- After we change something, did the shape of the system change the way we expected?

Those are graph questions, and the answers should drive actions: run a fault experiment, apply a fix, verify, record.

## 2. What exists (prior art)

| Tool | What it does | What it does not do |
|---|---|---|
| OpenTelemetry Collector **service graph connector** | Pairs client/server spans into `A -> B` edges with request rate, error rate and latency histograms ([docs](https://oneuptime.com/blog/post/2026-02-06-service-graph-connector-opentelemetry-collector/markdown)) | Edges only; no per-trace structure, no critical path, no reasoning, metrics not a graph you can query |
| **Jaeger** dependencies / Tempo / Grafana service map | Service DAG with call counts (`/api/dependencies`) | Same: a picture, not a queryable model |
| **CRISP** (Uber, USENIX ATC '22) | Critical-path analysis over Jaeger traces at 40k endpoints; top-down per endpoint, bottom-up across endpoints, anomaly detection ([paper](https://www.usenix.org/conference/atc22/presentation/zhang-zhizhou), [blog](https://www.uber.com/blog/crisp-critical-path-analysis-for-microservice-architectures/)) | Offline analysis producing heat maps; no action, no graph DB, internal to Uber |
| **Cartography** (Lyft, CNCF) | Infrastructure assets and relationships in Neo4j for security reasoning ([pypi](https://pypi.org/project/cartography/)) | Static cloud inventory; knows nothing about runtime calls or latency |
| **Chaos Monkey / LitmusChaos / Chaos Mesh / Gremlin** | Inject faults; *principles of chaos*: define steady state, hypothesise, minimise blast radius ([principlesofchaos.org](https://principlesofchaos.org/)) | The hypothesis and the blast radius are written by hand. Nobody derives them from the dependency graph |

The gap Faultline fills: **CRISP's critical path + Cartography's "graph as security model" + chaos engineering's
hypothesis loop, in one queryable graph, with an agent that acts on it.** The hypothesis of an experiment is computed
from the graph (reverse reachability from the target), the experiment runs, the observed blast radius is written back
next to the prediction, and the difference is the finding.

## 2b. The current landscape (second research pass, Oct 16 evening)

**Automated chaos at Netflix.** ChAP ran experiments as control/experiment canaries; uptake was low until **Monocle**
(Basiri et al., *Automating Chaos Experiments in Production*, ICSE-SEIP 2019) discovered dependencies from telemetry
and runtime config, ranked candidate experiments by a criticality score combined with a safety score and an experiment
weight, and filtered out experiments that were unsafe, already running, failed and awaiting review, or run recently
([summary](https://blog.acolyer.org/2019/07/05/automating-chaos-experiments-in-production/)). Faultline's planner is
that ranking as graph queries: criticality from betweenness, traffic and blast radius; safety and recency from the
Experiment ledger in the same graph; REFUTED-and-unreviewed targets skipped until a human reviews them.

**Reliability scoring.** Gremlin discovers dependencies by sniffing DNS and sockets and scores each service from
executed tests plus passively detected risks ([how it works](https://gremlin.com/blog/how-gremlins-reliability-score-works)).
Faultline's evidence is the traces themselves, and the score is replaced by explicit findings with the query behind each.

**Service-level fault injection in tests.** Filibuster (Meiklejohn et al., SoCC '21) extends functional test suites
with injected dependency failures, later including database clients ([paper](https://people.eecs.berkeley.edu/~rohanpadhye/files/filibuster-socc21.pdf)).
Complementary: Filibuster works pre-production from code; Faultline works from production shape.

**AI SRE products (2026).** Datadog Bits AI, Resolve AI, Traversal, Cleric and Rootly investigate incidents from
telemetry; Resolve keeps a continuously updated dependency graph, Traversal is criticised for not modelling topology as
a graph, Cleric stops at proposing fixes for a human to ship ([comparison](https://metoro.io/blog/top-ai-sre-tools)).
Research on LLM root-cause agents warns that agents reach correct answers without a sound diagnostic trail
([Lu et al.](https://www.icsoft.org/article/study-finds-llm-rca-agents-can-reach-correct-answers-without-a-sound-diagnostic-trail.html)).
Faultline's stance: the LLM never produces a verdict; every verdict (CONFIRMED/REFUTED, VERIFIED/REJECTED) is computed
by comparing graph windows, every tool call is shown, and anything that touches the system needs a human click bound
to a hash of the evidence.

**Retries in telemetry.** OpenTelemetry's `http.request.resend_count` is stable but rarely emitted; Faultline detects
retries structurally instead (a failed child span followed by a sibling of the same operation under the same parent),
which works on any instrumentation ([semconv](https://opentelemetry.io/docs/specs/semconv/http/http-spans)).

## 3. Why this is a graph problem (and why FalkorDB is load-bearing)

- **Critical path** is a path query over the span tree (CRISP's algorithm walks children by end time). Stored as
  `Span-[:CHILD_OF]->Span` with `critical=true` and `self_us`, "who owns p95 of /dispatch" is one aggregation.
- **Single points of failure** = `algo.betweenness` on the `Service-[:CALLS]->Service` graph, weighted by traffic.
- **Blast radius of stopping X** = reverse reachability: every root operation with a path to any operation of X.
  `algo.SPpaths` / variable-length `MATCH` do it in one query; the prediction is a set of nodes the experiment can
  confirm or refute.
- **Fan-out amplification** = count of child spans per parent per hop; an `N+1` against a cache is both a latency bug
  and a denial-of-service amplifier.
- **Before/after** = two `Window` nodes in the same graph; a fix is verified by diffing critical-path shares and error
  rates between windows, exactly the closed-loop pattern judged well in Reachable.
- **Memory across sessions** (track 2 overlap): experiments, predictions, outcomes and approvals persist as nodes, so
  the agent's next hypothesis is informed by the last outcome.

Available locally (verified on FalkorDB 4.x): `algo.betweenness`, `algo.pageRank`, `algo.SPpaths`, `algo.SSpaths`,
`algo.BFS`, `algo.WCC`, `algo.labelPropagation`, `algo.HarmonicCentrality`, `algo.maxFlow`, `algo.MSF`, `algo.AStar`.

## 4. The demo system (verified today, no fabrication)

Jaeger's **HotROD** ride-dispatch demo is the canonical tracing teaching app: four services (frontend, customer,
driver, route) plus an in-process MySQL and Redis simulation, OpenTelemetry-instrumented, with *deliberate* bugs
(a MySQL connection mutex, a 300 ms DB delay, a 3-worker route pool, flaky Redis). It can run **each service in its own
container** (`frontend --customer-service-hostname=customer ...`), which makes real fault injection a `docker pause`.
It ships three fix flags, `-M` (disable the DB mutex), `-D` (DB delay), `-W` (route worker pool), so an
agent-proposed fix can actually be applied and re-measured.

Measured today on this laptop:

| Scenario | Observation |
|---|---|
| Healthy `/dispatch` | ~700–780 ms, 39–40 spans, 6 services, 2–3 error spans per trace (Redis timeouts, by design) |
| `docker pause route` | `/dispatch` returns after **30.0 s**, 12–13 error spans: the frontend has no useful timeout on the route hop |
| Jaeger `/api/dependencies` | frontend→customer 5, customer→mysql 5, frontend→driver 5, driver→redis 68, frontend→route 36 calls for 5 requests: **13× fan-out to Redis, 7× to route** per request |

Those three rows are already three findings: a critical-path owner, a missing timeout (a hang, not a fail-fast), and
an amplification hop. The agent should find them from the graph, not from me.

## 5. Hackathon fit

Track 1, *Agents That Act on Connected Data*: "the graph has to shape what the agent does." Here every action is
graph-derived: which experiment to run (highest betweenness × traffic, not yet tested), what it predicts (reverse
reachability), whether a fix is accepted (window diff gates). It also touches track 2 (memory across sessions) honestly,
without claiming it.

Rules check: FalkorDB primary and load-bearing (yes); new work during the event (yes, started Oct 16); AI assistance
disclosed; live deployment preferred (read-only public site showing recorded windows and experiments, as Reachable does).

## 6. Honest risk assessment

- **Time.** ~60 hours to the deadline at the time of writing. Reachable is finished, tested (40 tests) and deployed.
  Faultline must reach "ingest → critical path → findings → one real experiment with prediction vs outcome → one
  verified fix" to be competitive. That is roughly: core today, experiment runner and agent tomorrow, UI/deploy/video on
  the 18th morning. Anything slipping a day means submitting Reachable.
- **Judges' familiarity.** Tracing and chaos are well understood; HotROD is recognisable. The pitch is shorter than
  Reachable's and the "wow" (pause a container, watch the graph predict and then confirm the blast radius) is live.
- **Depth vs Reachable.** Reachable has a stronger technical core (static reachability, version planner). Faultline's
  core (CRISP critical path, centrality, reachability) is simpler to explain but equally graph-native.
- **Recommendation.** Build Faultline on this branch with a hard checkpoint at **Oct 17, 20:00 IST**: if the
  experiment loop is not verified end-to-end by then, submit Reachable. Both stay in one repo; master is untouched.

## 7. Graph model (v1)

```
(:Service {name})-[:EXPOSES]->(:Operation {key, name, service})
(:Service)-[:CALLS {window, count, errors, p50_ms, p95_ms}]->(:Service)      one edge per ingestion window
(:Operation)-[:INVOKES {window, count, errors, p95_ms}]->(:Operation)
(:Window {id, label, start, end, traces})
(:Trace {id, window, root, duration_ms, status, errors})-[:IN]->(:Window)
(:Span {id, trace, service, op, start_us, duration_us, kind, error, critical, self_us})-[:OF]->(:Trace)
(:Span)-[:CHILD_OF]->(:Span)      (:Span)-[:ON]->(:Operation)
(:Finding {id, kind, severity, service, detail, window})-[:ABOUT]->(:Service|:Operation)
(:Experiment {id, kind, target, hypothesis, verdict, status, ts})-[:TARGETS]->(:Service)
(:Experiment)-[:BASELINE]->(:Window)  -[:OBSERVED]->(:Window)
(:Experiment)-[:PREDICTED_IMPACT]->(:Operation)   -[:ACTUAL_IMPACT {error_rate, p95_ms}]->(:Operation)
(:Fix {id, service, flags, rationale, verdict, status})-[:BEFORE]->(:Window) -[:AFTER]->(:Window)
```

## 7b. What the agent does (built Oct 16 evening)

Tools, all graph-backed: `overview`, `explain_critical_path` (CRISP top-down chain + bottom-up owners),
`blast_radius` (the hypothesis), `rank_experiments` (Monocle ranking with ledger memory), `next_fix` (top owner → the
knob that targets it, skipping VERIFIED ones), `guardrails` (timeouts = 3 × p95 per hop, retry budgets, fan-out caps,
raised to high priority when an experiment showed a hang), `retries`, `history` (memory across sessions),
`compare_windows` (drift: health, owners, edges added/removed), `propose_experiment`, `propose_fix`, `query_graph`
(read-only). Proposals become Action nodes with a SHA-256 of their evidence; approval re-derives the evidence and
refuses stale or tampered actions; execution is deterministic code; a REJECTED fix is rolled back automatically.

Three kinds of proposal, all approved the same way: a **fault experiment** (pause a container; verdict CONFIRMED/REFUTED
against the graph's prediction), a **fix** (a HotROD flag; four gates; rollback on REJECTED), and a **guardrail** (the
lesson of a hang experiment as a reviewable Istio VirtualService timeout + bounded retries written under `out/`).
The same graph also answers the security question the original idea asked: `attack_surface` lists every data store,
the shortest call path from the edge into it, the protocol of each hop as instrumented (TLS is not observable in these
traces and is reported as "no TLS attribute recorded", never as "plaintext"), and the calls per request into the store.
An MCP server exposes all thirteen read/propose tools to other agents; execution stays behind approval.

Added later on Oct 16: **draft PRs** for every approved change (compose override + gate report, guardrail YAML + note,
incident report), **incident issues** for any window that regressed against steady state (before/after table, the owner that
moved, hop, code pointer, worst trace), and **code pointers** from OpenTelemetry code attributes with an explicit source map
for HotROD. The agent's offline path and the site expose all three.

Measured chain on Oct 16: `-M` VERIFIED (p50 1191 → 788 ms), `-W 100` VERIFIED (788 → 593 ms), agent-proposed
`-D 100ms` (593 → 390 ms) first REJECTED by a strict failure gate on one flaky request out of 40, which led to the
one-request tolerance and the automatic rollback. After each fix the graph names the next owner (MySQL → route queueing
→ Redis fan-out), which is the demo's through-line.

## 8. Plan (status Oct 16, late evening: steps 1 and 2 done, step 3 next)

1. **Core (Oct 16):** compose (Jaeger + 4 HotROD containers + FalkorDB), load generator, Jaeger ingest into windows,
   CRISP critical path, findings (critical owners, SPOF by betweenness, fan-out, hang-not-fail), blast-radius prediction, CLI, tests.
2. **Loop (Oct 17):** experiment runner (`pause`, `fix` via compose override), gates, verdicts; Gemini tool-calling
   agent with human approval for anything that touches the system; read-only public mode.
3. **Ship (Oct 18 morning):** the web console with the service graph, trace waterfall, experiment ledger;
   deploy; video; README; submit by noon, well ahead of 23:59 IST.
