from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

from itext2kg.atom.models import Entity, KnowledgeGraph, Relationship, RelationshipProperties
from itext2kg.atom.models.entity import EntityProperties

from ..utils import QueryConfig, RetentionConfig
from .neo4j_client import Neo4jClient
from ..neo4j_props import props_dict


def _chunks(items: List[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    if size <= 0:
        yield items
        return
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _np_to_list(v: Any) -> Optional[list[float]]:
    if v is None:
        return None
    if isinstance(v, np.ndarray):
        return [float(x) for x in v.tolist()]
    if isinstance(v, list):
        return [float(x) for x in v]
    return None


def _list_to_np(v: Any) -> Optional[np.ndarray]:
    if v is None:
        return None
    if isinstance(v, np.ndarray):
        return v
    if isinstance(v, list):
        try:
            return np.array([float(x) for x in v], dtype=np.float64)
        except Exception:
            return None
    return None


@dataclass(frozen=True)
class VersionedGraphStore:
    client: Neo4jClient
    graph_name: str = "default"

    def write_knowledge_graph(self, version: str, kg: KnowledgeGraph, batch_size: int = 500) -> None:
        node_rows: List[Dict[str, Any]] = []
        for e in kg.entities:
            node_rows.append(
                {
                    "kg_version": version,
                    "entity_label": str(e.label or ""),
                    "name": str(e.name or ""),
                    "props": {
                        "kg_version": version,
                        "entity_label": str(e.label or ""),
                        "name": str(e.name or ""),
                        "embeddings": _np_to_list(getattr(e.properties, "embeddings", None)),
                    },
                }
            )

        rel_rows: List[Dict[str, Any]] = []
        for r in kg.relationships:
            start = r.startEntity
            end = r.endEntity
            predicate = str(r.name or "related_to")
            rel_rows.append(
                {
                    "kg_version": version,
                    "start_label": str(start.label or ""),
                    "start_name": str(start.name or ""),
                    "end_label": str(end.label or ""),
                    "end_name": str(end.name or ""),
                    "predicate": predicate,
                    "props": {
                        "kg_version": version,
                        "predicate": predicate,
                        "atomic_facts": list(getattr(r.properties, "atomic_facts", []) or []),
                        "t_obs": list(getattr(r.properties, "t_obs", []) or []),
                        "t_start": list(getattr(r.properties, "t_start", []) or []),
                        "t_end": list(getattr(r.properties, "t_end", []) or []),
                        "embeddings": _np_to_list(getattr(r.properties, "embeddings", None)),
                    },
                }
            )

        node_query = """
UNWIND $rows AS row
MERGE (e:Entity {kg_version: row.kg_version, entity_label: row.entity_label, name: row.name})
SET e += row.props
RETURN count(e) AS n
"""
        rel_query = """
UNWIND $rows AS row
MATCH (s:Entity {kg_version: row.kg_version, entity_label: row.start_label, name: row.start_name})
MATCH (t:Entity {kg_version: row.kg_version, entity_label: row.end_label, name: row.end_name})
MERGE (s)-[r:REL {kg_version: row.kg_version, predicate: row.predicate}]->(t)
SET r += row.props
RETURN count(r) AS n
"""

        for batch in _chunks(node_rows, batch_size):
            self.client.run(node_query, {"rows": batch})
        for batch in _chunks(rel_rows, batch_size):
            self.client.run(rel_query, {"rows": batch})

    def load_knowledge_graph(self, version: str) -> KnowledgeGraph:
        node_query = """
MATCH (e:Entity {kg_version: $v})
RETURN e
"""
        rel_query = """
MATCH (s:Entity {kg_version: $v})-[r:REL {kg_version: $v}]->(t:Entity {kg_version: $v})
RETURN s, properties(r) AS rp, t
"""

        entities: List[Entity] = []
        entity_index: Dict[Tuple[str, str], Entity] = {}
        for row in self.client.run(node_query, {"v": version}):
            n = row["e"]
            props = props_dict(n)
            label = str(props.get("entity_label", "") or "")
            name = str(props.get("name", "") or "")
            emb = _list_to_np(props.get("embeddings"))
            ent = Entity(label=label, name=name, properties=EntityProperties(embeddings=emb))
            entities.append(ent)
            entity_index[(label, name)] = ent

        relationships: List[Relationship] = []
        for row in self.client.run(rel_query, {"v": version}):
            s = row["s"]
            t = row["t"]
            sp = props_dict(s)
            tp = props_dict(t)
            start_label = str(sp.get("entity_label", "") or "")
            start_name = str(sp.get("name", "") or "")
            end_label = str(tp.get("entity_label", "") or "")
            end_name = str(tp.get("name", "") or "")

            start_ent = entity_index.get((start_label, start_name))
            end_ent = entity_index.get((end_label, end_name))
            if start_ent is None or end_ent is None:
                continue

            rp = row.get("rp") or {}
            if not isinstance(rp, dict):
                rp = {}
            rel_props = RelationshipProperties(
                embeddings=_list_to_np(rp.get("embeddings")),
                atomic_facts=list(rp.get("atomic_facts", []) or []),
                t_obs=list(rp.get("t_obs", []) or []),
                t_start=list(rp.get("t_start", []) or []),
                t_end=list(rp.get("t_end", []) or []),
            )
            predicate = rp.get("predicate")
            if not predicate:
                predicate = "related_to"
            relationships.append(
                Relationship(
                    startEntity=start_ent,
                    endEntity=end_ent,
                    name=str(predicate),
                    properties=rel_props,
                )
            )
        return KnowledgeGraph(entities=entities, relationships=relationships)

    def cleanup_old_versions(self, retention: RetentionConfig) -> List[str]:
        if not retention.enable_cleanup or retention.max_versions <= 0:
            return []

        query = """
MATCH (s:KGState {graph_name: $graph_name})
WITH s.latest_ready_version AS latest
MATCH (t:KGTask)
WHERE t.finished_at IS NOT NULL AND (t.error IS NULL OR t.error = '')
WITH latest, collect(DISTINCT t.version) AS versions
RETURN latest, versions
"""
        rows = self.client.run(query, {"graph_name": self.graph_name})
        if not rows:
            return []
        latest = rows[0].get("latest")
        versions = list(rows[0].get("versions") or [])

        def _sort_key(v: str) -> int:
            try:
                return int(v)
            except Exception:
                return 0

        versions_sorted = sorted([str(v) for v in versions if v], key=_sort_key, reverse=True)
        keep = set(versions_sorted[: retention.max_versions])
        if latest:
            keep.add(str(latest))

        to_delete = [v for v in versions_sorted if v not in keep]
        for v in to_delete:
            self.delete_version_data(v)
        return to_delete

    def delete_version_data(self, version: str) -> None:
        query = """
MATCH (e:Entity {kg_version: $v})
DETACH DELETE e
RETURN 1 AS _ignored
"""
        self.client.run(query, {"v": version})

    def get_entity_types(self, version: str) -> list[str]:
        query = """
MATCH (e:Entity {kg_version: $v})
RETURN DISTINCT e.entity_label AS t
ORDER BY t
"""
        return [str(r["t"]) for r in self.client.run(query, {"v": version}) if r.get("t") is not None]

    def get_relation_types(self, version: str) -> list[str]:
        query = """
MATCH ()-[r:REL {kg_version: $v}]->()
RETURN DISTINCT r.predicate AS t
ORDER BY t
"""
        return [str(r["t"]) for r in self.client.run(query, {"v": version}) if r.get("t") is not None]

    def get_stats(self, version: str) -> Tuple[int, int, int]:
        q1 = "MATCH (e:Entity {kg_version: $v}) RETURN count(e) AS n, count(DISTINCT e.entity_label) AS t"
        q2 = "MATCH ()-[r:REL {kg_version: $v}]->() RETURN count(r) AS n"
        r1 = self.client.run(q1, {"v": version})[0]
        r2 = self.client.run(q2, {"v": version})[0]
        return int(r1["n"]), int(r2["n"]), int(r1["t"])

    def query_graph(
        self,
        version: str,
        q: Optional[str],
        limit_nodes: int,
        limit_edges: int,
        depth: int,
        max_seed_nodes: int,
        include_properties: bool,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], bool]:
        q = (q or "").strip()
        limit_nodes_plus = int(max(1, limit_nodes)) + 1
        limit_edges_plus = int(max(0, limit_edges)) + 1
        depth = int(max(0, depth))

        nodes: Dict[str, Dict[str, Any]] = {}
        edges: Dict[str, Dict[str, Any]] = {}

        def add_node(n: Any) -> None:
            props = props_dict(n)
            node_id = f"{props.get('entity_label','')}:{props.get('name','')}"
            if node_id in nodes:
                return
            if include_properties:
                cleaned = {k: v for k, v in props.items() if k not in {"embeddings", "kg_version"}}
            else:
                cleaned = None
            nodes[node_id] = {
                "id": node_id,
                "types": ["Entity", str(props.get("entity_label", "") or "")],
                "name": str(props.get("name", "") or "") or None,
                "properties": cleaned,
            }

        def add_edge(s: Any, r: Any, t: Any) -> None:
            sp = props_dict(s)
            tp = props_dict(t)
            rp = props_dict(r)
            source_id = f"{sp.get('entity_label','')}:{sp.get('name','')}"
            target_id = f"{tp.get('entity_label','')}:{tp.get('name','')}"
            predicate = rp.get("predicate")
            if not predicate:
                # Fallback: try to get type from r if it's an object, otherwise use default
                predicate = getattr(r, "type", None) if not isinstance(r, dict) else None
                if not predicate:
                    predicate = "related_to"
            predicate = str(predicate)
            edge_id = f"{source_id}->{predicate}->{target_id}"
            if edge_id in edges:
                return
            if include_properties:
                cleaned = {k: v for k, v in rp.items() if k not in {"embeddings", "kg_version"}}
            else:
                cleaned = None
            edges[edge_id] = {
                "id": edge_id,
                "type": predicate,
                "source": source_id,
                "target": target_id,
                "properties": cleaned,
            }

        if q:
            seed_query = """
MATCH (s:Entity {kg_version: $v})
WHERE toLower(s.name) CONTAINS toLower($q)
RETURN s
LIMIT $seed_limit
"""
            seed_rows = self.client.run(
                seed_query,
                {"v": version, "q": q, "seed_limit": int(max(1, max_seed_nodes))},
            )
            for row in seed_rows:
                add_node(row["s"])

            if depth > 0 and limit_edges > 0 and seed_rows:
                expand_query = """
MATCH (s:Entity {kg_version: $v})
WHERE toLower(s.name) CONTAINS toLower($q)
WITH s LIMIT $seed_limit
MATCH (s)-[rels:REL*1..$depth]-(n:Entity {kg_version: $v})
WHERE ALL(r IN rels WHERE r.kg_version = $v)
UNWIND rels AS r
WITH DISTINCT r
LIMIT $limit_edges
MATCH (a)-[r]->(b)
RETURN a AS s, properties(r) AS rp, b AS t
"""
                rows = self.client.run(
                    expand_query,
                    {
                        "v": version,
                        "q": q,
                        "seed_limit": int(max(1, max_seed_nodes)),
                        "depth": depth,
                        "limit_edges": limit_edges_plus,
                    },
                )
                for row in rows:
                    add_node(row["s"])
                    add_node(row["t"])
                    add_edge(row["s"], row["rp"], row["t"])
        else:
            edge_query = """
MATCH (s:Entity {kg_version: $v})-[r:REL {kg_version: $v}]->(t:Entity {kg_version: $v})
RETURN s, properties(r) AS rp, t
LIMIT $limit_edges
"""
            if limit_edges > 0:
                rows = self.client.run(edge_query, {"v": version, "limit_edges": limit_edges_plus})
                for row in rows:
                    add_node(row["s"])
                    add_node(row["t"])
                    add_edge(row["s"], row["rp"], row["t"])

            if not nodes:
                node_query = """
MATCH (e:Entity {kg_version: $v})
RETURN e
LIMIT $limit_nodes
"""
                for row in self.client.run(node_query, {"v": version, "limit_nodes": limit_nodes_plus}):
                    add_node(row["e"])

        truncated = False
        if len(nodes) > limit_nodes:
            truncated = True
            nodes = dict(list(nodes.items())[:limit_nodes])
        if len(edges) > limit_edges:
            truncated = True
            edges = dict(list(edges.items())[:limit_edges])

        used_node_ids = set(nodes.keys())
        edges = {k: v for k, v in edges.items() if v["source"] in used_node_ids and v["target"] in used_node_ids}
        return list(nodes.values()), list(edges.values()), truncated
