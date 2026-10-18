"""OTLP in, no Jaeger: the receiver, the file importer and the full analysis on OTLP-shaped data."""
import gzip
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from faultline import analysis, config, ingest, otlp
from faultline.graph import Store

FIX = Path(__file__).parent / "fixtures" / "hotrod_traces.json"


def _otlp_json(trace_hex: str, t0_ns: int) -> dict:
    """A request: gateway -> orders (server) -> postgres (client span), with code attributes, one erroring span."""
    def span(sid, parent, name, start, end, kind, attrs=None, status=None):
        d = {"traceId": trace_hex, "spanId": sid, "name": name, "kind": kind, "startTimeUnixNano": str(start), "endTimeUnixNano": str(end),
             "attributes": [{"key": k, "value": ({"intValue": str(v)} if isinstance(v, int) else {"stringValue": v})} for k, v in (attrs or {}).items()]}
        if parent: d["parentSpanId"] = parent
        if status: d["status"] = {"code": status}
        return d
    return {"resourceSpans": [
        {"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "gateway"}}]},
         "scopeSpans": [{"spans": [span("a1", None, "POST /orders", t0_ns, t0_ns + 900_000_000, "SPAN_KIND_SERVER", {"http.request.method": "POST", "http.response.status_code": 200}),
                                   span("a2", "a1", "POST", t0_ns + 10_000_000, t0_ns + 880_000_000, "SPAN_KIND_CLIENT", {"server.address": "orders"})]}]},
        {"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "orders"}}]},
         "scopeSpans": [{"spans": [span("b1", "a2", "POST /orders", t0_ns + 20_000_000, t0_ns + 870_000_000, 2, {"code.function.name": "create_order", "code.file.path": "orders/handlers.py"}),
                                   span("b2", "b1", "INSERT orders", t0_ns + 30_000_000, t0_ns + 800_000_000, 3, {"db.system.name": "postgresql", "server.address": "postgres"}, "STATUS_CODE_ERROR"),
                                   span("b3", "b1", "INSERT orders", t0_ns + 805_000_000, t0_ns + 860_000_000, 3, {"db.system.name": "postgresql", "server.address": "postgres"})]}]},
    ]}


@pytest.fixture(scope="module")
def st():
    config.GRAPH_NAME = "faultline_test_otlp"
    try:
        s = Store(); s.q("RETURN 1")
    except Exception:
        pytest.skip("FalkorDB not reachable")
    s.reset(); yield s; s.reset()


def test_receiver_accepts_json_and_gzip_and_ingests_a_window(st, tmp_path, monkeypatch):
    monkeypatch.setattr(otlp, "SPOOL", tmp_path / "spans.jsonl"); monkeypatch.setattr(otlp, "_spool_loaded", True)
    otlp._buffer.clear(); otlp._seen_at.clear()
    from faultline import api
    api._store = st
    client = TestClient(api.app)
    t0 = 1_700_000_000_000_000_000
    for i in range(6):
        doc = _otlp_json(f"{i:032x}", t0 + i * 2_000_000_000)
        body = json.dumps(doc).encode()
        if i % 2:
            r = client.post("/v1/traces", content=gzip.compress(body), headers={"content-type": "application/json", "content-encoding": "gzip"})
        else:
            r = client.post("/v1/traces", content=body, headers={"content-type": "application/json"})
        assert r.status_code == 200 and r.json() == {"partialSuccess": {}}
    assert client.get("/api/otlp").json()["spans_received"] == 30
    assert (tmp_path / "spans.jsonl").read_text().count("\n") == 30
    traces = otlp.traces(settle_s=0.0)
    assert len(traces) == 6 and all(len(t["spans"]) == 5 for t in traces)
    w = ingest.ingest_window(st, "otlp", traces=traces, progress=lambda *_: None)["window"]
    owners = analysis.critical_owners(st, w)
    assert owners[0]["service"] == "orders" and owners[0]["op"] == "INSERT orders" and owners[0]["peer"] == "postgres" and owners[0]["share_pct"] > 80
    graph = {(e["from"], e["to"]): e for e in analysis.service_graph(st, w)}
    assert ("gateway", "orders") in graph and ("orders", "postgres") in graph and graph[("orders", "postgres")]["proto"] == "db:postgresql"
    from faultline import profile
    monkeypatch.setattr(profile, "EDGE_SERVICES", ["gateway"])
    ex = analysis.exposure(st, w)
    assert ex and ex[0]["store"] == "postgres" and ex[0]["path"] == "gateway -> orders -> postgres"
    assert any(o["service"] == "orders" and o["code"] and "orders/handlers.py" in o["code"]["location"] for o in owners)
    ret = analysis.retries(st, w)
    assert ret and ret[0]["service"] == "postgres" and ret[0]["retried_errors"] == 6
    health = analysis.root_health(st, w)[0]
    assert health["op"] == "POST /orders" and health["service"] == "gateway" and health["traces"] == 6


def test_protobuf_payload_decodes(monkeypatch, tmp_path):
    from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
    from opentelemetry.proto.trace.v1 import trace_pb2
    from opentelemetry.proto.common.v1 import common_pb2
    req = trace_service_pb2.ExportTraceServiceRequest()
    rs = req.resource_spans.add(); kv = rs.resource.attributes.add(); kv.key = "service.name"; kv.value.string_value = "checkout"
    sp = rs.scope_spans.add().spans.add(); sp.trace_id = bytes.fromhex("ab" * 16); sp.span_id = bytes.fromhex("cd" * 8); sp.name = "GET /cart"
    sp.kind = trace_pb2.Span.SPAN_KIND_SERVER; sp.start_time_unix_nano = 1_000_000_000; sp.end_time_unix_nano = 1_250_000_000
    a = sp.attributes.add(); a.key = "http.response.status_code"; a.value.int_value = 503; sp.status.code = 2
    spans = otlp.spans_from_protobuf(req.SerializeToString())
    assert spans == [dict(spans[0])] and spans[0]["service"] == "checkout" and spans[0]["kind"] == "server" and spans[0]["error"] and spans[0]["status"] == "503" and spans[0]["duration_us"] == 250_000


def test_file_import_reads_jaeger_and_otlp_exports(tmp_path):
    jt = otlp.traces_from_file(str(FIX))
    assert len(jt) == 5 and all(t["spans"] for t in jt)
    p = tmp_path / "export.json"; p.write_text(json.dumps(_otlp_json("f" * 32, 1_700_000_000_000_000_000)))
    ot = otlp.traces_from_file(str(p))
    assert len(ot) == 1 and len(ot[0]["spans"]) == 5 and ot[0]["spans"][0]["parent"] is None
