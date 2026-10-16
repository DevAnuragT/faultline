# Design notes (so every part can be explained)

## Call graph (`reachable/callgraph.py`)
* Parses the app and the *real* source of each pinned package with `ast`; nothing is imported or executed.
* Resolution order for a call: local type environment (constructor calls, annotations, `with X() as y`, `self.attr`
  types) → nested functions → module imports (including relative imports and `from x import *` re-exports) → class
  hierarchy (MRO walk, `super()`).
* **Shadowing:** a name bound locally (parameter, assignment, loop/with/except target) never falls back to a same-named
  module function (`tests/test_callgraph.py`).
* **Name-guess edges (`MAYBE_CALLS`)** exist only when the receiver is unresolved. They are limited to (a) candidate
  methods in distributions the calling module actually imports, (b) receivers not rooted in an unanalyzed import
  (so Flask's `request.get_data()` can't match a PyYAML method), (c) at most 6 candidates, ignoring common container names.
* **Registry dispatch:** a call through a local callable fetched from a subscript (`handler = self.registry[tag]; handler(...)`)
  links to functions registered at import time in the same distribution (`MAYBE_CALLS`).
* `REFERENCES` marks functions used as values (registries, callbacks).

## Verdicts (`reachable/analysis.py`)
`REACHABLE` = `shortestPath` over `CALLS` only; `POSSIBLY_REACHABLE` = path allowing `MAYBE_CALLS|REFERENCES`;
`NEEDS_REVIEW` = package not analyzed, no vulnerable function known, or the symbol is only referenced as a value;
`NO_STATIC_PATH` otherwise. The path is stored on the `Finding` (`path`, `kinds`, `layers`) and as `EVIDENCE` edges, so a
later what-if cannot erase history.

## Vulnerable-function mapping (`reachable/symbols.py`)
Fix commit diff → functions of the vulnerable release that contain the removed/context lines (matched by line text, so
version drift doesn't matter) plus handlers named in removed module-level registrations → Gemini picks from that
candidate list (structured JSON). Heuristic fallback is labelled `heuristic`; one human-curated mapping is labelled `curated`.

## Planner (`reachable/planner.py`)
Targets come from exposed advisories' fixed versions. Each target is first checked **alone** against the installed
dependents using the `spec` stored on the graph's `DEPENDS_ON` edges (that is where `urllib3==2.6.3` is rejected because
`requests 2.25.1` requires `urllib3<1.27`; `tests/test_pipeline.py` relaxes the edge in the graph and the rejection disappears).
Constraints of *candidate* versions that are not in the graph yet (e.g. which `requests` release allows `urllib3 2.x`) come
from cached PyPI release metadata. It searches for the smallest dependent version that allows the target, iterates to a
fixpoint, and finally runs a pin-consistency dry-run (every pinned package's requirements on other pinned packages must hold).

## Closed loop (`reachable/verify.py`)
Builds the candidate pins into a private scratch graph (real sources, real advisories), re-triages, compares to the
baseline. Gates: **coverage retained** (each parsed package keeps ≥50% of its analysed functions; if not, status is
`UNVERIFIED` and no success is claimed), **constraints consistent**, **exposure decreases**, **no new exposure**.
The result and the baseline snapshot are stored as a `Remediation` node.

## Approvals (`reachable/actions.py`)
Canonical JSON payload + SHA-256 stored on the `Action`. Execution: tamper check, then rebuild the payload from the
current graph and compare, then execute. The snapshot hash covers pins, dependency edges with their constraints, per-distribution code size, edge counts, app
edges and symbol mappings, so any change in evidence expires pending approvals; re-running triage on an identical graph
does not.

## Read-only public mode (`reachable/guard.py`)
`REACHABLE_PUBLIC=true` makes every mutating backend function raise `ReadOnlyError` on the shared graph (ingest,
what-if, triage, persisted verification, proposals, execution). The UI hides the controls, but the backend is what
enforces it (`tests/test_public.py`). A `Store(ephemeral=True)` (the private scratch graph) is exempt, which is what
lets `verify.run_closed_loop_ephemeral` run the closed loop live for visitors: nothing is persisted, the scratch graph
is deleted, one run at a time (`threading.Lock`) with a 30 s cooldown, `BusyError` instead of queueing. The public site
shows the recorded closed loop, what-if and agent conversation (`reachable/frozen.py`).

## Recorded agent conversation (`reachable/frozen.py`)
`record_agent` runs the three example prompts through the live agent, each in a fresh chat so every turn shows the
tools chosen for that question, and refuses a turn without a tool call. The Agent tab preloads it; the UI test checks
the preload and that an example button sends exactly one request.
