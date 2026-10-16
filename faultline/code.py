"""Code pointers: where in the source a finding lives.

Two sources, in order: OpenTelemetry code attributes on the span when the instrumentation emits them
(`code.function.name` / `code.file.path` / `code.line.number`, or the older `code.function` / `code.filepath` / `code.lineno`),
and otherwise a lookup table for the demo app (HotROD lives in the Jaeger repository). The table is explicit so the demo never
claims a line it cannot justify; a real deployment gets the first source for free from its own instrumentation."""
from __future__ import annotations

HOTROD = "https://github.com/jaegertracing/jaeger/blob/main/examples/hotrod"
TABLE: dict[tuple[str, str], dict] = {
    ("mysql", "SQL SELECT"): {"file": "services/customer/database.go", "function": "database.Get",
                              "why": "simulated MySQL: a mutex serialises connections and a fixed query delay (-M, -D)"},
    ("customer", "GET /customer"): {"file": "services/customer/server.go", "function": "Server.customer", "why": "HTTP handler calling the database"},
    ("redis-manual", "FindDriverIDs"): {"file": "services/driver/redis.go", "function": "Redis.FindDriverIDs", "why": "simulated Redis scan"},
    ("redis-manual", "GetDriver"): {"file": "services/driver/redis.go", "function": "Redis.GetDriver",
                                    "why": "simulated Redis GET with random timeouts; called once per driver, retried on error"},
    ("driver", "driver.DriverService/FindNearest"): {"file": "services/driver/server.go", "function": "Server.FindNearest",
                                                    "why": "gRPC handler: N+1 loop over driver ids with retry on Redis errors"},
    ("route", "GET /route"): {"file": "services/route/server.go", "function": "Server.route", "why": "CPU-bound route computation per call"},
    ("frontend", "HTTP GET"): {"file": "services/frontend/best_eta.go", "function": "bestETA.getRoutes",
                               "why": "fans out one route call per driver through a worker pool (-W); the HTTP client has no short timeout"},
    ("frontend", "GET /dispatch"): {"file": "services/frontend/server.go", "function": "Server.dispatch", "why": "entry point"},
    ("frontend", "driver.DriverService/FindNearest"): {"file": "services/frontend/best_eta.go", "function": "bestETA.getDrivers", "why": "gRPC client call"},
}
FLAG_SOURCE = {"-M": ("services/config/config.go", "MySQLMutexDisabled"), "-D": ("services/config/config.go", "MySQLGetDelay"),
               "-W": ("services/config/config.go", "RouteWorkerPoolSize")}


def pointer(service: str, op: str, span_code: str = "") -> dict | None:
    """Prefer what the span said about itself; fall back to the demo table."""
    if span_code:
        return {"source": "span attributes", "location": span_code}
    hit = TABLE.get((service, op))
    if not hit:
        return None
    return {"source": "HotROD source map", "file": hit["file"], "function": hit["function"], "why": hit["why"], "url": f"{HOTROD}/{hit['file']}"}


def flag_pointer(flag: str) -> dict | None:
    hit = FLAG_SOURCE.get(flag.split()[0])
    return {"file": hit[0], "symbol": hit[1], "url": f"{HOTROD}/{hit[0]}"} if hit else None
