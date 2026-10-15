"""Thin FalkorDB wrapper."""
from __future__ import annotations

from falkordb import FalkorDB

from . import config


class Store:
    def __init__(self, graph: str | None = None) -> None:
        self.db = FalkorDB(host=config.FALKORDB_HOST, port=config.FALKORDB_PORT, password=config.FALKORDB_PASSWORD)
        self.name = graph or config.GRAPH_NAME
        self.g = self.db.select_graph(self.name)

    def q(self, cypher: str, params: dict | None = None) -> list[list]:
        return self.g.query(cypher, params or {}).result_set

    def ro(self, cypher: str, params: dict | None = None) -> tuple[list[str], list[list]]:
        res = self.g.ro_query(cypher, params or {})
        return [h[1] if isinstance(h, (list, tuple)) else h for h in res.header], res.result_set

    def reset(self) -> None:
        try:
            self.g.delete()
        except Exception:
            pass
        self.g = self.db.select_graph(self.name)

    def ensure_indexes(self) -> None:
        for label, prop in [("Function", "qualname"), ("Module", "name"), ("PackageVersion", "key"),
                            ("Vuln", "id"), ("Endpoint", "id"), ("Team", "name"), ("Finding", "id"),
                            ("Repo", "name"), ("Run", "id"), ("Action", "id")]:
            try:
                self.q(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
            except Exception:
                pass  # already exists

    def exists(self) -> bool:
        try:
            return bool(self.q("MATCH (v:Vuln) RETURN count(v)")[0][0])
        except Exception:
            return False


def batches(rows: list, n: int = 400):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]
