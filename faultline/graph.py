"""Thin FalkorDB wrapper."""
from __future__ import annotations

from falkordb import FalkorDB

from . import config


class Store:
    def __init__(self, graph: str | None = None) -> None:
        self.db = FalkorDB(host=config.FALKORDB_HOST, port=config.FALKORDB_PORT, username=config.FALKORDB_USERNAME,
                           password=config.FALKORDB_PASSWORD, ssl=config.FALKORDB_SSL, socket_connect_timeout=10)
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
        for label, prop in [("Service", "name"), ("Operation", "key"), ("Window", "id"), ("Trace", "id"),
                            ("Span", "id"), ("Finding", "id"), ("Experiment", "id"), ("Fix", "id")]:
            try:
                self.q(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
            except Exception:
                pass
