# Demo script (2:30)

Stack up (`make up`), a baseline ingested (`make baseline`), UI running (`make run`), Jaeger at :16686 open in a tab.

1. **Findings (0:00–0:35).** "HotROD, Jaeger's own demo app, four services in four containers, real OpenTelemetry
   traces. Faultline ingests a window of traces into FalkorDB and computes the critical path of every request." Point at
   the owners table: *MySQL owns 64% of latency; Redis is called 13.5 times per request; every path runs through
   customer and driver.* "Every line is a Cypher query; the LLM has not been involved yet."
2. **Experiment (0:35–1:15).** Experiments tab. "The planner ranks what to break the way Netflix's Monocle did,
   from betweenness, traffic and blast radius, and remembers what it already tested." Propose pausing `route`; Actions
   tab; approve. Watch the status box: hypothesis from the graph, container paused, six requests, unpaused, new window,
   **CONFIRMED, but it hung for 35 s instead of failing**. "The graph predicted the outage; the experiment found the
   missing timeout. That lesson is now a node, and the guardrails table turned that hop red."
3. **Fix (1:15–2:00).** Agent tab: "What should we fix first? Propose it." Show the tool calls it chose, then the
   proposal. Approve in Actions. Customer restarts with `-M`, 40 requests, new window, four gates: **VERIFIED, p50 1191
   to 788 ms**, and the graph names the next owner. "Nothing ran without the click, the approval is bound to a hash of
   the evidence, and a rejected fix rolls itself back."
4. **Why FalkorDB (2:00–2:30).** Graph tab: the critical-path query, the betweenness call, the blast-radius path
   query, the prediction-vs-outcome query. "Spans, windows, experiments, fixes and approvals are one graph, so the
   agent's memory and its evidence are the same thing."
