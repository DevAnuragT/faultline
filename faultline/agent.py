"""Gemini function-calling agent. Every fact comes from a graph tool; the model explains and proposes, it never
executes: humans approve actions, deterministic code runs them. Each tool call is recorded for the UI."""
from __future__ import annotations

import re
import time

from . import actions, analysis, config, experiments, ingest, llm, planner
from .graph import Store

SYSTEM = """You are Faultline, a reliability agent. Your only knowledge of the system comes from tools that query a
FalkorDB graph built from distributed traces: services, operations, spans with critical paths, per-window call edges,
and a ledger of experiments, fixes and actions.
Rules:
- Always call tools; never guess numbers or verdicts. Quote the numbers the tools return (ms, %, counts, window ids).
- Verdicts come from the graph: an experiment is CONFIRMED/REFUTED by comparing the predicted blast radius with the
  observed window; a fix is VERIFIED/REJECTED by its gates. Never upgrade a REJECTED or REFUTED result in prose.
- When asked what is wrong / what is slow / what is risky: call overview, then explain_critical_path for the main
  endpoint, and cite the owners by share and ms per request.
- When asked what to fix or improve: call overview, then next_fix and rank_experiments. Then you MUST call
  propose_fix (with the flags next_fix returned) or propose_experiment (with the top-ranked target) before answering:
  a recommendation without a proposal is incomplete. Nothing runs until a human approves it in the Actions tab: say
  "proposed" (never "applied", "ran" or "started") and give the action id the tool returned. After a fix is VERIFIED,
  say what the graph names as the next owner.
- When asked about resilience, timeouts, retries or chaos: use rank_experiments, guardrails, retries and history. When
  an experiment's lesson says a hop hangs, propose_guardrail for that hop (it is the fix for the hang).
- When asked about security, exposure, attack surface or data stores: call attack_surface and report paths and hops;
  never claim a hop is encrypted or unencrypted, only that no TLS attribute was recorded.
- history is your memory across sessions: check it before proposing something that was already tried.
- If a tool returns {"error": ...}, quote that error. Never claim a tool failed or was unavailable unless its result
  says so, and never describe an action you did not call a tool for.
- Be concise: short paragraphs or bullets, no filler, no praise."""

FORBIDDEN = re.compile(r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP|CALL\s+db\.|LOAD\s+CSV)\b", re.I)


class Toolbox:
    def __init__(self, st: Store) -> None:
        self.st = st
        self.trace: list[dict] = []

    def _log(self, name: str, args: dict, result) -> None:
        self.trace.append({"tool": name, "args": args, "result": result, "ts": time.time()})

    def _w(self, window: str | None = None) -> str:
        return window or ingest.latest_window(self.st)

    def overview(self, window: str = "") -> dict:
        """Steady-state picture of the latest (or given) window: per-endpoint health (p50/p95/failure rate), the
        top critical-path owners with their share of end-to-end latency, the service graph edges, and the findings."""
        w = self._w(window or None)
        res = {"window": w, "windows": ingest.windows(self.st)[-6:], "health": analysis.root_health(self.st, w),
               "critical_owners": analysis.critical_owners(self.st, w)[:6], "service_graph": analysis.service_graph(self.st, w),
               "findings": analysis.findings(self.st, w)}
        self._log("overview", {"window": w}, res)
        return res

    def explain_critical_path(self, root_op: str = "", window: str = "") -> dict:
        """Top-down critical path (CRISP) of the slowest steady-state request for an endpoint: the ordered chain of
        spans that end-to-end latency actually waited on, with self time per span, plus the bottom-up owners."""
        w = self._w(window or None)
        res = {"window": w, "chain": analysis.critical_chain(self.st, w, root_op or None),
               "owners": analysis.critical_owners(self.st, w, root_op or None)[:6]}
        self._log("explain_critical_path", {"root_op": root_op, "window": w}, res)
        return res

    def blast_radius(self, service: str, window: str = "") -> dict:
        """What the graph predicts breaks if `service` stops: root operations with a call path into it, and the
        services that depend on it. This is the hypothesis a pause experiment tests."""
        w = self._w(window or None)
        res = experiments.hypothesis(self.st, w, service)
        self._log("blast_radius", {"service": service, "window": w}, res)
        return res

    def rank_experiments(self, window: str = "") -> list[dict]:
        """Which fault experiment to run next, Monocle-style: criticality (betweenness, traffic, blast radius)
        discounted by safety and recency; targets whose last experiment is REFUTED and unreviewed are skipped."""
        w = self._w(window or None)
        res = planner.rank_experiments(self.st, w)
        self._log("rank_experiments", {"window": w}, res)
        return res

    def next_fix(self, window: str = "") -> dict:
        """The fix the graph points at: the top critical-path owner mapped to the configuration knob that targets it,
        skipping fixes already VERIFIED. Returns null when no known knob targets the current owner."""
        w = self._w(window or None)
        res = planner.next_fix(self.st, w) or {"note": "no known knob targets the current top owner"}
        self._log("next_fix", {"window": w}, res)
        return res

    def guardrails(self, window: str = "") -> list[dict]:
        """Timeouts (3 x p95 per hop), retry budgets and fan-out caps derived from measured latency, marked high
        priority where an experiment showed the hop hangs instead of failing."""
        w = self._w(window or None)
        res = planner.guardrails(self.st, w)
        self._log("guardrails", {"window": w}, res)
        return res

    def retries(self, window: str = "") -> list[dict]:
        """Silent retries found in the span trees: failed calls followed by a retry of the same operation, with the
        time the failed attempts cost per request and how many retries land on the critical path."""
        w = self._w(window or None)
        res = analysis.retries(self.st, w)
        self._log("retries", {"window": w}, res)
        return res

    def attack_surface(self, window: str = "") -> list[dict]:
        """Security view: each data store, the shortest call path from an edge service into it, protocol per hop as
        recorded in the spans (TLS is not observable in traces and is reported as such), and calls per request into the store."""
        w = self._w(window or None)
        res = analysis.exposure(self.st, w)
        self._log("attack_surface", {"window": w}, res)
        return res

    def history(self) -> dict:
        """Memory: every experiment (hypothesis, verdict, lesson) and fix (flags, verdict, gates, next owner) recorded in
        the graph, newest first, plus pending/done actions."""
        _, ex = self.st.ro("MATCH (e:Experiment)-[:TARGETS]->(s) RETURN e.id, s.name, e.hypothesis, e.verdict, e.lesson, e.ts ORDER BY e.ts DESC LIMIT 10")
        _, fx = self.st.ro("MATCH (f:Fix) RETURN f.id, f.service, f.flags, f.verdict, f.gates, f.next_owner, f.ts ORDER BY f.ts DESC LIMIT 10")
        res = {"experiments": [{"id": r[0], "target": r[1], "hypothesis": r[2], "verdict": r[3], "lesson": r[4]} for r in ex],
               "fixes": [{"id": r[0], "service": r[1], "flags": r[2], "verdict": r[3], "gates": r[4], "next_owner": r[5]} for r in fx],
               "actions": actions.list_actions(self.st)[:10]}
        self._log("history", {}, res)
        return res

    def compare_windows(self, window_a: str, window_b: str) -> dict:
        """Drift between two windows: endpoint p50/p95/failure rate, critical-path owners, dependency edges added or removed."""
        res = planner.diff_windows(self.st, window_a, window_b)
        self._log("compare_windows", {"a": window_a, "b": window_b}, res)
        return res

    def propose_experiment(self, service: str, rationale: str = "") -> dict:
        """Propose (not run) pausing `service` to test the graph's blast-radius prediction. A human must approve."""
        res = actions.propose_experiment(self.st, service, rationale)
        self._log("propose_experiment", {"service": service}, res)
        return res

    def propose_fix(self, flags: str, rationale: str = "") -> dict:
        """Propose (not apply) a HotROD fix: flags is one of "-M", "-D 100ms", "-W 100". A human must approve; the fix is
        then applied, re-measured and judged by four gates."""
        res = actions.propose_fix(self.st, flags.split(), rationale)
        self._log("propose_fix", {"flags": flags}, res)
        return res

    def propose_guardrail(self, hop: str, rationale: str = "") -> dict:
        """Propose (not apply) a timeout + bounded-retry policy for one hop, e.g. "frontend -> route", sized from
        guardrails(). Approval writes a reviewable Istio VirtualService YAML; the hop's experiment evidence is cited."""
        res = actions.propose_guardrail(self.st, hop, rationale)
        self._log("propose_guardrail", {"hop": hop}, res)
        return res

    def query_graph(self, cypher: str) -> dict:
        """Run a READ-ONLY Cypher query (labels: Service, Operation, Window, Trace, Span, Finding, Experiment, Fix,
        Action; edges: EXPOSES, CALLS{window}, INVOKES{window}, IN, OF, CHILD_OF, ON, ABOUT, TARGETS, BASELINE,
        OBSERVED, PREDICTED_IMPACT, ACTUAL_IMPACT, BEFORE, AFTER, EXECUTED_AS). At most 25 rows."""
        if FORBIDDEN.search(cypher):
            res = {"error": "only read-only queries are allowed"}
        else:
            try:
                header, rows = self.st.ro(cypher)
                res = {"columns": header, "rows": rows[:25]}
            except Exception as exc:
                res = {"error": str(exc)[:300]}
        self._log("query_graph", {"cypher": cypher}, res)
        return res

    def tools(self) -> list:
        return [self._safe(f) for f in self._raw_tools()]

    def _safe(self, fn):
        """Keep the name, signature and docstring (Gemini builds the schema from them); log and return any failure."""
        import functools

        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                res = {"error": f"{exc.__class__.__name__}: {str(exc)[:200]}"}
                self._log(fn.__name__, kwargs or {"args": [str(a) for a in args]}, res)
                return res
        return wrapped

    def _raw_tools(self) -> list:
        return [self.overview, self.explain_critical_path, self.blast_radius, self.rank_experiments, self.next_fix, self.guardrails,
                self.retries, self.attack_surface, self.history, self.compare_windows, self.propose_experiment, self.propose_fix, self.propose_guardrail, self.query_graph]


class Agent:
    def __init__(self, st: Store) -> None:
        self.st = st
        self.box = Toolbox(st)
        self.chat = None
        self.model = ""
        self.provider = "offline"
        self.live = False
        try:
            from . import claude_agent
            if claude_agent.available():
                self._claude = claude_agent.ClaudeChat(self.box, SYSTEM)
                self.model, self.provider, self.live = self._claude.model, "claude", True
                return
        except Exception:  # SDK missing or misconfigured: fall through to Gemini
            pass
        self.live = bool(config.GEMINI_API_KEY)
        if self.live:
            self.provider = "gemini"
            from google import genai
            from google.genai import types

            self._types = types
            self._client = genai.Client(api_key=config.GEMINI_API_KEY, http_options=types.HttpOptions(timeout=60000))
            self._new_chat(llm.models()[0])

    def _new_chat(self, model: str, history=None) -> None:
        self.model = model
        self.chat = self._client.chats.create(model=model, history=history, config=self._types.GenerateContentConfig(
            system_instruction=SYSTEM, tools=self.box.tools(), temperature=0))

    def ask(self, message: str) -> tuple[str, list[dict]]:
        start = len(self.box.trace)
        if self.provider == "claude":
            try:
                return self._claude.send(message), self.box.trace[start:]
            except Exception as exc:
                return f"(Claude unavailable: {exc.__class__.__name__}; answering from graph tools directly)\n\n" + self._offline(message), self.box.trace[start:]
        if self.live:
            last: Exception | None = None
            for _ in range(len(config.GEMINI_FALLBACK_MODELS) + 1):
                try:
                    reply = self.chat.send_message(message).text or ""
                    return reply, self.box.trace[start:]
                except Exception as exc:
                    last = exc
                    if llm.is_quota_error(exc):
                        llm.mark_exhausted(self.model)
                        nxt = llm.models()[0]
                    elif llm.is_transient(exc):  # 503 "high demand": wait a moment, move along the chain, keep the model
                        time.sleep(2)
                        chain = llm.models()
                        nxt = chain[(chain.index(self.model) + 1) % len(chain)] if self.model in chain else chain[0]
                    else:
                        break
                    self._new_chat(nxt, history=self.chat.get_history())
            return f"(Gemini unavailable: {last.__class__.__name__}; answering from graph tools directly)\n\n" + self._offline(message), self.box.trace[start:]
        return self._offline(message), self.box.trace[start:]

    def _offline(self, message: str) -> str:
        m = message.lower()
        if any(k in m for k in ("fix", "improve", "faster", "speed")):
            o = self.box.overview(); nf = self.box.next_fix()
            top = o["critical_owners"][0]
            if "flags" in nf:
                p = self.box.propose_fix(" ".join(nf["flags"]), f"targets {nf['targets_owner']} ({nf['share_pct']}%)")
                prev = nf.get("previous_attempt")
                return (f"Top owner: {analysis.label(top)} at {top['share_pct']}% ({top['per_request_ms']} ms/request). "
                        + (f"{nf['note']}. " if nf.get("note") else "")
                        + f"Proposed `{' '.join(nf['flags'])}` on {nf['service']} (targets {nf['targets_owner']}, {nf['share_pct']}%): "
                        f"action {p.get('action')} awaiting approval."
                        + (f" Note: a previous attempt was {prev['verdict']}" + (f" on: {'; '.join(prev['failed_gates'])}" if prev["failed_gates"] else "") + "." if prev else ""))
            return f"Top owner: {analysis.label(top)} at {top['share_pct']}%; no known knob targets it."
        if any(k in m for k in ("chaos", "experiment", "break", "resilien", "timeout")):
            r = self.box.rank_experiments(); g = self.box.guardrails()
            best = next((x for x in r if not x["skip"]), None)
            lines = [f"- {x['target']}: priority {x['priority']}" + (f" (skipped: {x['skip']})" if x["skip"] else f" ({'; '.join(x['reasons'])})") for x in r]
            if best:
                p = self.box.propose_experiment(best["target"]); lines.append(f"Proposed pausing {best['target']}: action {p.get('action')}.")
            hung = [x for x in g if x["priority"] == "high" and x["kind"] == "timeout"]
            for x in hung[:1]:
                p = self.box.propose_guardrail(x["hop"]); lines.append(f"An experiment showed {x['hop']} hangs: proposed a {x['value']} timeout with bounded retries, action {p.get('action')}.")
            lines += [f"- guardrail {x['hop']}: {x['kind']} {x['value']} ({x['evidence']})" for x in g[:4]]
            return "\n".join(lines)
        o = self.box.overview()
        h = o["health"][0] if o["health"] else {}
        return (f"Window {o['window']}: {h.get('op')} p50 {h.get('p50_ms')} ms, p95 {h.get('p95_ms')} ms, {h.get('fail_rate', 0):.0%} failed.\n"
                + "\n".join(f"- {analysis.label(x)}: {x['share_pct']}% ({x['per_request_ms']} ms/request)" for x in o["critical_owners"][:4])
                + "\n" + "\n".join(f"- [{f['severity']}] {f['detail']}" for f in o["findings"][:5]))
