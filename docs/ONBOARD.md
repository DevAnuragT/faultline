# Point Faultline at your own system

Faultline is not tied to the demo app. Everything it knows about a system comes from one file, the **profile**
(`faultline.yaml`, or `FAULTLINE_PROFILE=/path/to/it`). The bundled `profiles/hotrod.yaml` is the worked example.

## What you need

- **Traces, by any of three routes.** Pick one in the profile's `traces.source`:
  - `otlp` (the default): point your OpenTelemetry SDKs or Collector at Faultline itself. The console's API serves
    `POST /v1/traces` (OTLP/HTTP, protobuf or JSON, gzip accepted), so `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://faultline:8600/v1/traces`
    is the whole integration. `faultline receive --port 4318` runs just the receiver where the console is not wanted.
    Spans are spooled under `data/otlp/` and ingested by time window.
  - `jaeger`: an existing Jaeger (or Tempo's Jaeger-compatible query API) via `traces.jaeger_url`.
  - `file`: an OTLP JSON export or a Jaeger JSON export, `faultline ingest --file export.json`.
- **FalkorDB** (Docker or FalkorDB Cloud).
- Optionally, a way for Faultline to act: a `docker-compose.yml` for the services, or `kubectl` access to a namespace.
  Without one, Faultline runs **observe-only**: findings, critical paths, remedies, incidents, code reading, PR and issue
  proposals all work; experiments and fixes are disabled.

## Six steps

1. `faultline init --jaeger http://your-jaeger:16686 --compose ./docker-compose.yml` writes a starter `faultline.yaml`
   with the services Jaeger knows, the root services it found in real traces, and the compose services it could match.
2. **Services.** For each trace service name, say how it runs: `{compose: name}` or `{deployment: name}` (kubernetes).
   A database or cache that runs inside another service's process gets `{in_process_of: that-service}` so the planner
   never tries to pause it alone and the hypotheses say it freezes with its host.
3. **Load** (optional). List URLs that exercise the system. Delete the block to ingest live traffic instead:
   `faultline ingest --lookback 15m --label baseline`.
4. **Actuators.** The only changes Faultline may ever apply. Each maps a remedy class the graph can find
   (`contention`, `serialized_fanout`, `per_call_cost`) and the owner service to a change: `{args: [...]}` appended to
   the compose command, `{env: {KEY: VAL}}` on compose or kubernetes, or `{replicas: N}`. If your fix is a code change,
   leave the actuator out; the graph still names the remedy, the agent reads the code and proposes a patch.
5. **Code.** Point `code.source_root` at a local checkout, or `code.raw_base` at raw files on the web. If your
   instrumentation emits OpenTelemetry's `code.function.name` / `code.file.path` attributes, that is all; otherwise add
   `code.map` entries for the operations that matter. Go, Python, JavaScript/TypeScript, Java, Kotlin, C# and Rust
   functions are extracted by name.
6. Run it: `faultline baseline` (or `ingest`), `faultline report`, `make run` for the console. Then `experiment <service>`
   and `fix --flags=<actuator id>` when you have a backend, always through the approval path.

## What stays the same for every system

The graph model, the critical-path algorithm, the findings, the remedy evidence (contention from in-flight overlap,
serialized fan-out from per-trace concurrency, per-call cost from critical-path share), the four fix gates, the
three-part refutable hypothesis, the approval ledger with evidence hashes, the agent's tools and its rule that it may
only propose code it has read. Only the profile changes.

## Kubernetes notes

`environment.backend: kubernetes` with `kubernetes: {namespace, context}`. A pause scales the deployment to zero and
back to its previous replica count. Fixes apply `env` or `replicas` changes with `kubectl set env` / `kubectl scale`
and are reverted the same way on rejection; `args` changes need compose. Faultline never edits manifests in git; use the
draft-PR path for that.
