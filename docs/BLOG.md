# Faultline: letting the trace graph decide what to break, what to fix, and how to prove it

*Anurag Thakur · Graph Hacks, Oct 2026 · Track 1: Agents That Act on Connected Data · [repository](https://github.com/DevAnuragT/faultline)*

## The problem

Every team I have worked with collects traces. Jaeger, Tempo, Datadog, Honeycomb: the spans are all there. And almost
nobody asks the traces the questions that matter during an incident or a reliability review:

- Which service actually owns our latency, and how much of it?
- What breaks if this service dies, and does anything fail *fast*, or does everything hang?
- Did the last change move the bottleneck, or just move it somewhere else?
- What should we change, and what is the evidence?

Those are graph questions. A request is a tree of spans; a system is a graph of calls; a fix is a comparison between
two states of that graph. Tracing tools answer "what happened in this one request". Chaos tools break things, but the
hypothesis is written by hand. Nobody closes the loop: derive the hypothesis from the graph, act, measure, record, and
let the next decision read the last outcome.

Faultline is that loop, for SREs and the engineers who get paged.

## What it does

1. **Ingest.** A window of traces from Jaeger becomes a FalkorDB graph: services, operations, every span, call edges
   with calls per request, p95 and error counts, tagged with the window they came from.
2. **Diagnose, in queries.** Each request's critical path (CRISP, the Uber algorithm) is computed and stored on its
   spans, so "who owns the latency" is one aggregation. Betweenness finds single points of failure. A variable-length
   path query gives the blast radius of stopping a service. Other queries find silent retries, fan-out, requests that
   hang instead of failing, and data stores reachable from the edge.
3. **Decide remedies from evidence, not from a list of knobs.** The graph measures *contention* (an operation gets
   slower the more of it is in flight: MySQL at 236 ms alone, 267 ms more per concurrent call), *serialized fan-out*
   (ten route calls per request, three at a time, four rounds) and *per-call cost*. The environment supplies the
   actuator for each class, if it has one. A remedy with no actuator is reported as advice, never invented as a command.
4. **Act, behind a person.** Three kinds of proposal, one approval path bound to a hash of the evidence: pause a
   container and compare the outcome with the graph's prediction; apply a fix and judge it by four gates, rolling it
   back if rejected; turn a hang into a timeout policy. Approved changes become draft pull requests; regressed windows
   become incident issues with the owner, the hop, the code pointer and the worst trace.
5. **An agent that cannot lie about code.** The model has graph tools and two code tools: it reads the function
   behind an operation before explaining it, and it can only propose a patch against lines it has read. A patch that
   removes a line the function does not contain is rejected. Every tool call is shown.

## What the graph found on the demo system

The target is Jaeger's own HotROD app, four services in four containers, deliberately buggy.

- Baseline: MySQL owned 64% of critical-path latency, Redis was called 13.5 times per request with a fifth of the
  calls silently retried, and every path ran through `customer` and `driver`.
- Pausing `route`: the graph predicted the endpoint fails and that customer and driver keep answering. Confirmed. It
  also found something nobody predicted: the endpoint returned 200 after 35 seconds. There was no timeout.
- Pausing `customer`: driver, Redis and route starved as predicted, and six MySQL calls ran *after customer resumed,
  after every caller had already given up*. No deadline reached the callee, so the work ran late for nobody.
- Three fixes, each verified by four gates: median 1209 → 798 → 583 → 376 ms. After each one the graph named the next
  owner, which is why the fixes came in that order.

## What I learned

- **A hung request is not a slow request.** One 35-second wait dominated every average until steady-state numbers
  excluded hangs and experiments were judged against the baseline median.
- **The newest window is not the truth.** After an experiment, the latest window describes a broken system. Every
  default had to mean "the newest steady-state window", and a rejected fix's window is flagged so it never becomes the
  baseline.
- **Prompts do not stop hallucination; tools do.** The first time I asked a small model what to change in the Redis
  code, it invented a Go function. The fix was not a better prompt: the proposal tool now refuses unless that exact
  function was read in the conversation, and the patch is validated against the real source.
- **Predictions must be refutable.** A single-endpoint demo makes "the endpoint fails" unfalsifiable. Predicting which
  services keep answering and which starve, from the request's measured call order, made the verdict mean something.

## Why FalkorDB

Spans, windows, experiments, fixes, approvals and the agent's memory are one property graph in one instance. The
critical-path shares, the betweenness call, the blast-radius path query and the window-to-window diffs are all
Cypher. The agent's memory is the same graph as its evidence. Remove FalkorDB and nothing in Faultline can decide
anything.

Built in three days with AI coding assistants, disclosed in the README; the model, the gates, the actuator design and
every measurement are mine, and the rehearsal notes in `docs/PROJECT_STORY.md` explain each part.
