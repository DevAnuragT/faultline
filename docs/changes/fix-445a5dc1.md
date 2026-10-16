# -D 100ms on customer: VERIFIED

Windows `w-49440e6a` -> `w-f2e4d5c4`.

- PASS GET /dispatch median improves >= 20%: p50 583 -> 376 ms (+36%)
- PASS GET /dispatch tail not worse: p95 669 -> 472 ms
- PASS GET /dispatch failures not worse (tolerance: one request or 5%): 1/40 -> 2/40 failed
- PASS customer's critical-path time drops >= 25%: 292 -> 96 ms per request on the critical path

Next owner after this change: redis-manual GetDriver now owns 49.9% (189 ms/request)

Knob in source: `services/config/config.go` (MySQLGetDelay) https://github.com/jaegertracing/jaeger/blob/main/examples/hotrod/services/config/config.go
