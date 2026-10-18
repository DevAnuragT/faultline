"""Profiles describe any system; the bundled HotROD one is just the default."""
import importlib
import os
from pathlib import Path

from faultline import profile, source


def test_hotrod_profile_is_the_default():
    assert profile.NAME == "hotrod" and profile.BACKEND == "compose"
    assert set(profile.PAUSABLE) == {"frontend", "customer", "driver", "route"}
    assert profile.IN_PROCESS_OF == {"mysql": "customer", "redis-manual": "driver"}
    assert profile.actuator_for("contention", "mysql")["id"] == "-M" and profile.actuator_for("serialized_fanout", "frontend")["target"] == "frontend"
    assert profile.actuator_for("per_call_cost", "redis-manual") is None
    assert profile.runtime_name("customer") == "customer" and ("mysql", "SQL SELECT") in profile.CODE_MAP


def test_a_custom_profile_can_describe_a_kubernetes_system(tmp_path, monkeypatch):
    y = tmp_path / "faultline.yaml"
    y.write_text("""
name: shop
traces: {jaeger_url: http://tempo:16686, root_services: [api-gateway]}
environment: {backend: kubernetes, kubernetes: {namespace: shop}}
services:
  api-gateway: {deployment: gateway}
  orders: {deployment: orders-svc}
  postgres: {in_process_of: orders}
actuators:
  - {id: orders-pool, kind: contention, service: postgres, target: orders, change: {env: {DB_POOL: "32"}}, what: raise the pool}
code: {source_root: /src/shop, map: [{service: orders, op: "POST /orders", file: orders/handlers.py, function: create_order}]}
""")
    monkeypatch.setenv("FAULTLINE_PROFILE", str(y))
    importlib.reload(profile)
    try:
        assert profile.NAME == "shop" and profile.BACKEND == "kubernetes" and profile.K8S["namespace"] == "shop"
        assert profile.LOAD_URLS == [] and profile.PAUSABLE == ["api-gateway", "orders"] and profile.runtime_name("orders") == "orders-svc"
        assert profile.actuator_for("contention", "postgres")["change"] == {"env": {"DB_POOL": "32"}}
        assert profile.EDGE_SERVICES == ["api-gateway"] and profile.SOURCE_ROOT == "/src/shop"
    finally:
        monkeypatch.delenv("FAULTLINE_PROFILE")
        importlib.reload(profile)
    assert profile.NAME == "hotrod"


def test_function_extraction_across_languages():
    py = "import x\n\nclass A:\n    def other(self):\n        return 1\n\n    async def create_order(self, req):\n        db.insert(req)\n        return ok\n\ndef tail():\n    pass\n"
    r = source.extract_function(py, "A.create_order", "orders/handlers.py")
    assert r and r["start_line"] == 7 and "db.insert" in r["code"] and "def tail" not in r["code"]
    js = "const a = 1;\nexport async function createOrder(req) {\n  if (x) { y(); }\n  return ok;\n}\nfunction other() {}\n"
    r = source.extract_function(js, "createOrder", "orders.ts")
    assert r and r["code"].startswith("export async function createOrder") and "function other" not in r["code"]
    java = "public class Orders {\n  public Order createOrder(Request req) {\n    if (a) {\n      b();\n    }\n    return o;\n  }\n  void other() {}\n}\n"
    r = source.extract_function(java, "Orders.createOrder", "Orders.java")
    assert r and "return o;" in r["code"] and "void other" not in r["code"]
    go = "package p\n\nfunc (s *Server) route(w http.ResponseWriter) {\n\tx()\n}\n\nfunc other() {}\n"
    r = source.extract_function(go, "Server.route", "server.go")
    assert r and r["code"].endswith("}") and "other" not in r["code"]
