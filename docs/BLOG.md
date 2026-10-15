# Which of your 33 CVEs can actually be reached? Proving it with a FalkorDB graph

Every dependency scanner gives you the same thing: a long list. For a six-package Flask app, OSV reports 33 advisories.
Most teams cannot tell which ones matter, so they either patch everything or ignore the list.

I built **Reachable** for the FalkorDB Graph Hacks hackathon (Track 1: agents that act on connected data). It answers one
question with a graph traversal: *can a public endpoint reach the vulnerable function?*

## Three layers, one graph

* **Code:** endpoints → functions → calls, extracted with Python's `ast` from the app *and* the real package sources
  (nothing is installed or executed).
* **Supply chain:** repo → packages → transitive packages.
* **Advisories:** OSV records → affected package → the vulnerable function.

The interesting part is encoding trust in the schema. A call resolved through imports is a `CALLS` edge. A call on an
unresolved receiver, or runtime registry dispatch, is `MAYBE_CALLS`. A function used as a value is `REFERENCES`. Then
"provable" and "plausible" are two Cypher queries:

```cypher
WITH v, f, h, shortestPath((h)-[:CALLS*1..14]->(f)) AS p WHERE p IS NOT NULL            // REACHABLE
WITH v, f, h, shortestPath((h)-[:CALLS|MAYBE_CALLS|REFERENCES*1..14]->(f)) AS p ...      // POSSIBLY_REACHABLE
```

## Finding the vulnerable function

Advisories rarely say which function is vulnerable. Reachable downloads the fix commit, maps the changed lines to functions
of the vulnerable release, and asks Gemini to pick the vulnerable symbol from that candidate list (structured output,
cached). The model explains and picks; it never decides reachability, the graph does.

## An agent that acts, with guardrails

The Gemini agent calls graph tools (`explain_finding`, `recommend_fixes`, read-only `query_graph`) and can *propose* issues
and OpenVEX documents. A human approves; on approval the graph is re-checked and stale plans are refused. Findings keep
their witness path as `EVIDENCE` edges, so every verdict is auditable.

Aggregating those stored paths yields advice a flat list cannot: the app function sitting on the most exposed paths, and
the one version bump that clears the most exposed advisories.

## The what-if moment

Remove the `requests.get` call from one endpoint, re-ingest only the app layer, and ten advisories flip to
`NO_STATIC_PATH`. An issue proposed before the change is refused as stale.

## Honesty about limits

Static Python call graphs are approximate, so Reachable never says "safe". `NO_STATIC_PATH` means "no path found", and
framework packages it does not model stay `NEEDS_REVIEW`. Reachability is also not exploitability.

Code, data model and queries: see the repository README. Built with FalkorDB, Gemini and GitHub Copilot CLI.
