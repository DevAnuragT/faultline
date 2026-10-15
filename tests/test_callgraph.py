"""Call-graph correctness tests (no database needed)."""
from pathlib import Path

import pytest

from reachable import callgraph, sources
from reachable.callgraph import CALLS, MAYBE_CALLS
from reachable.config import DEMO_APP


def make_pkg(root: Path, name: str, files: dict[str, str]) -> Path:
    pkg = root / name
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    for fname, body in files.items():
        (pkg / fname).write_text(body)
    return pkg


def edge_kinds(cg, src, dst_suffix):
    return {k for (s, d), k in cg.edges.items() if s == src and d.endswith(dst_suffix)}


def test_parameter_shadowing_never_resolves_to_module_function(tmp_path):
    app = make_pkg(tmp_path, "shadowapp", {"a.py": (
        "def dangerous():\n    return 1\n\n"
        "def endpoint(dangerous):\n    return dangerous()\n\n"
        "def real():\n    return dangerous()\n")})
    cg = callgraph.build([(app, "shadowapp")])
    assert edge_kinds(cg, "shadowapp.a.endpoint", "a.dangerous") == set()
    assert edge_kinds(cg, "shadowapp.a.real", "a.dangerous") == {CALLS}


def test_local_assignment_shadows_module_function(tmp_path):
    app = make_pkg(tmp_path, "shadowapp2", {"a.py": (
        "def dangerous():\n    return 1\n\n"
        "def endpoint(cb):\n    dangerous = cb\n    return dangerous()\n")})
    cg = callgraph.build([(app, "shadowapp2")])
    assert edge_kinds(cg, "shadowapp2.a.endpoint", "a.dangerous") == set()


def test_receiver_from_unanalyzed_import_is_not_guessed(tmp_path):
    lib = make_pkg(tmp_path, "yamlish", {"c.py": "class Base:\n    def get_data(self):\n        return 1\n"})
    app = make_pkg(tmp_path, "webapp", {"v.py": (
        "from flask import request\n\n"
        "def handler():\n    return request.get_data()\n")})
    cg = callgraph.build([(app, "webapp"), (lib, "yamlish==1")])
    assert edge_kinds(cg, "webapp.v.handler", "get_data") == set()


def test_name_guess_limited_to_distributions_the_module_imports(tmp_path):
    lib = make_pkg(tmp_path, "unrelated", {"c.py": "class Base:\n    def process(self):\n        return 1\n"})
    related = make_pkg(tmp_path, "related", {"c.py": "class Worker:\n    def process(self):\n        return 2\n"})
    app = make_pkg(tmp_path, "svc", {"v.py": (
        "from related import c\n\n"
        "def handler(obj):\n    return obj.process()\n")})
    cg = callgraph.build([(app, "svc"), (lib, "unrelated==1"), (related, "related==1")])
    guessed = {d for (s, d), k in cg.edges.items() if s == "svc.v.handler" and k == MAYBE_CALLS}
    assert guessed == {"related.c.Worker.process"}


@pytest.fixture(scope="module")
def vulnshop_cg():
    roots = [(DEMO_APP / "vulnshop", "vulnshop")]
    for name, ver in [("PyYAML", "5.3.1"), ("requests", "2.25.1"), ("urllib3", "1.26.4"), ("Jinja2", "3.1.2")]:
        for r in sources.package_roots(sources.fetch_package(name, ver)):
            roots.append((r, f"{name}=={ver}"))
    return callgraph.build(roots)


def bfs(cg, start, goal, kinds):
    prev, queue = {start: None}, [start]
    while queue:
        cur = queue.pop(0)
        if cur == goal:
            path = []
            while cur:
                path.append(cur)
                cur = prev[cur]
            return path[::-1]
        for (s, d), k in cg.edges.items():
            if s == cur and k in kinds and d not in prev:
                prev[d] = cur
                queue.append(d)
    return None


def test_golden_path_import_settings_reaches_yaml_load_with_exact_calls(vulnshop_cg):
    path = bfs(vulnshop_cg, "vulnshop.app.import_settings", "yaml.load", {CALLS})
    assert path == ["vulnshop.app.import_settings", "vulnshop.settings_loader.load_user_settings",
                    "vulnshop.settings_loader.parse_settings", "yaml.full_load", "yaml.load"]


def test_flask_request_get_data_does_not_link_into_pyyaml(vulnshop_cg):
    assert edge_kinds(vulnshop_cg, "vulnshop.app.import_settings", "get_data") == set()


def test_pyyaml_exposure_only_uses_registry_dispatch_as_weak_edge(vulnshop_cg):
    path = bfs(vulnshop_cg, "vulnshop.app.import_settings", "yaml.constructor.FullConstructor.construct_python_object_new",
              {CALLS, MAYBE_CALLS})
    assert path and path[:5] == ["vulnshop.app.import_settings", "vulnshop.settings_loader.load_user_settings",
                                "vulnshop.settings_loader.parse_settings", "yaml.full_load", "yaml.load"]
    weak = [(a.split(".")[-1], b.split(".")[-1]) for a, b in zip(path, path[1:])
            if vulnshop_cg.edges[(a, b)] != CALLS]
    # Exactly two honest weak links: yaml.load instantiates a parameterized Loader class, and
    # construct_object dispatches through a runtime constructor registry.
    assert weak == [("load", "get_single_data"), ("construct_object", "construct_python_object_new")]
