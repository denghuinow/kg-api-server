from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from neo4j import GraphDatabase, Driver

from ..utils import Neo4jConfig


@dataclass(frozen=True)
class Neo4jClient:
    driver: Driver
    database: Optional[str]

    @classmethod
    def from_config(cls, cfg: Neo4jConfig) -> "Neo4jClient":
        driver = GraphDatabase.driver(cfg.uri, auth=(cfg.username, cfg.password))
        return cls(driver=driver, database=cfg.database)

    def close(self) -> None:
        self.driver.close()

    def run(self, query: str, params: Optional[Dict[str, Any]] = None) -> list[dict[str, Any]]:
        params = params or {}
        with self.driver.session(database=self.database) as session:
            result = session.run(query, params)
            return [r.data() for r in result]

