from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Dict, Optional, Tuple

from neo4j import ManagedTransaction

from ..utils import KGStatus, TaskInfo, TaskType
from .neo4j_client import Neo4jClient


GRAPH_NAME_DEFAULT = "default"


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class KGState:
    status: KGStatus
    latest_ready_version: Optional[str]
    current_task_id: Optional[str]
    updated_at: datetime


class TaskConflictError(RuntimeError):
    def __init__(self, state: KGState, current_task: Optional[TaskInfo]):
        super().__init__("TASK_RUNNING")
        self.state = state
        self.current_task = current_task


class StateStore:
    def __init__(self, client: Neo4jClient, graph_name: str = GRAPH_NAME_DEFAULT):
        self.client = client
        self.graph_name = graph_name

    def ensure_schema(self) -> None:
        statements = [
            "CREATE CONSTRAINT kgstate_graph_name IF NOT EXISTS FOR (s:KGState) REQUIRE s.graph_name IS UNIQUE",
            "CREATE CONSTRAINT kgtask_task_id IF NOT EXISTS FOR (t:KGTask) REQUIRE t.task_id IS UNIQUE",
            "CREATE CONSTRAINT entity_unique IF NOT EXISTS FOR (e:Entity) REQUIRE (e.kg_version, e.entity_label, e.name) IS UNIQUE",
        ]
        for stmt in statements:
            self.client.run(stmt)

    def recover_if_interrupted(self) -> None:
        query = """
MERGE (s:KGState {graph_name: $graph_name})
ON CREATE SET
  s.status = 'IDLE',
  s.latest_ready_version = null,
  s.current_task_id = null,
  s.updated_at = datetime()
WITH s
CALL (s) {
  WITH s
  OPTIONAL MATCH (t:KGTask {task_id: s.current_task_id})
  WITH s, t
  WHERE s.status IN ['BUILDING','UPDATING']
  SET s.status = 'FAILED', s.updated_at = datetime(), s.current_task_id = null
  FOREACH (_ IN CASE WHEN t IS NULL THEN [] ELSE [1] END |
    SET t.error = coalesce(t.error, 'server restarted'), t.finished_at = datetime()
  )
  RETURN 1 AS _ignored
}
RETURN 1 AS _ignored
"""
        self.client.run(query, {"graph_name": self.graph_name})

    def get_state_and_task(self) -> Tuple[KGState, Optional[TaskInfo]]:
        query = """
MERGE (s:KGState {graph_name: $graph_name})
ON CREATE SET
  s.status = 'IDLE',
  s.latest_ready_version = null,
  s.current_task_id = null,
  s.updated_at = datetime()
WITH s
OPTIONAL MATCH (t:KGTask {task_id: s.current_task_id})
RETURN s AS state, t AS task
"""
        rows = self.client.run(query, {"graph_name": self.graph_name})
        row = rows[0]
        state_node = row["state"]
        task_node = row.get("task")
        state = KGState(
            status=str(state_node["status"]),
            latest_ready_version=state_node.get("latest_ready_version"),
            current_task_id=state_node.get("current_task_id"),
            updated_at=state_node["updated_at"].to_native(),
        )
        task = _taskinfo_from_node(task_node) if task_node else None
        
        # 如果状态是 FAILED 且没有当前任务，返回最近一次失败的任务
        if state.status == "FAILED" and task is None:
            failed_task_query = """
MATCH (t:KGTask)
WHERE t.finished_at IS NOT NULL AND t.error IS NOT NULL
RETURN t
ORDER BY t.finished_at DESC
LIMIT 1
"""
            failed_rows = self.client.run(failed_task_query)
            if failed_rows:
                failed_task_node = failed_rows[0].get("t")
                if failed_task_node:
                    task = _taskinfo_from_node(failed_task_node)
        
        return state, task

    def try_start_task(
        self,
        task_type: TaskType,
        version: str,
        base_version: Optional[str],
    ) -> TaskInfo:
        def _tx(tx: ManagedTransaction) -> Dict[str, Any]:
            query = """
MERGE (s:KGState {graph_name: $graph_name})
ON CREATE SET
  s.status = 'IDLE',
  s.latest_ready_version = null,
  s.current_task_id = null,
  s.updated_at = datetime()
WITH s
OPTIONAL MATCH (running:KGTask {task_id: s.current_task_id})
WITH s, running
CALL (s, running) {
  WITH s, running
  WHERE s.status IN ['BUILDING','UPDATING']
  RETURN {conflict: true, state: s, task: running} AS out
  UNION
  WITH s, running
  WHERE NOT s.status IN ['BUILDING','UPDATING']
  MERGE (t:KGTask {task_id: $task_id})
  ON CREATE SET
    t.type = $task_type,
    t.version = $version,
    t.base_version = $base_version,
    t.started_at = datetime(),
    t.finished_at = null,
    t.progress = 0,
    t.error = null
  SET s.status = $target_status, s.current_task_id = $task_id, s.updated_at = datetime()
  RETURN {conflict: false, state: s, task: t} AS out
}
RETURN out
"""
            target_status = "BUILDING" if task_type == "full_build" else "UPDATING"
            res = tx.run(
                query,
                {
                    "graph_name": self.graph_name,
                    "task_id": version,
                    "task_type": task_type,
                    "version": version,
                    "base_version": base_version,
                    "target_status": target_status,
                },
            )
            return res.single()["out"]

        with self.client.driver.session(database=self.client.database) as session:
            out = session.execute_write(_tx)

        state_node = out["state"]
        task_node = out.get("task")
        state = KGState(
            status=str(state_node["status"]),
            latest_ready_version=state_node.get("latest_ready_version"),
            current_task_id=state_node.get("current_task_id"),
            updated_at=state_node["updated_at"].to_native(),
        )
        task = _taskinfo_from_node(task_node) if task_node else None

        if bool(out["conflict"]):
            raise TaskConflictError(state=state, current_task=task)
        if task is None:
            raise RuntimeError("Failed to create task")
        return task

    def update_task_progress(self, task_id: str, progress: int, message: Optional[str] = None) -> None:
        query = """
MATCH (t:KGTask {task_id: $task_id})
SET t.progress = $progress
FOREACH (_ IN CASE WHEN $message IS NULL THEN [] ELSE [1] END | SET t.message = $message)
RETURN 1 AS _ignored
"""
        self.client.run(query, {"task_id": task_id, "progress": int(progress), "message": message})

    def mark_task_success(self, task_id: str, version: str) -> None:
        query = """
MATCH (s:KGState {graph_name: $graph_name})
MATCH (t:KGTask {task_id: $task_id})
SET
  s.status = 'READY',
  s.latest_ready_version = $version,
  s.current_task_id = null,
  s.updated_at = datetime(),
  t.finished_at = datetime(),
  t.progress = 100,
  t.error = null
RETURN 1 AS _ignored
"""
        self.client.run(
            query,
            {"graph_name": self.graph_name, "task_id": task_id, "version": version},
        )

    def mark_task_failed(self, task_id: str, error: str) -> None:
        query = """
MATCH (s:KGState {graph_name: $graph_name})
MATCH (t:KGTask {task_id: $task_id})
SET
  s.status = 'FAILED',
  s.current_task_id = null,
  s.updated_at = datetime(),
  t.finished_at = datetime(),
  t.error = $error
RETURN 1 AS _ignored
"""
        self.client.run(
            query,
            {"graph_name": self.graph_name, "task_id": task_id, "error": str(error)},
        )


def _taskinfo_from_node(task_node: Any) -> TaskInfo:
    return TaskInfo(
        task_id=str(task_node["task_id"]),
        type=str(task_node["type"]),
        version=str(task_node["version"]),
        base_version=task_node.get("base_version"),
        started_at=task_node["started_at"].to_native(),
        finished_at=task_node["finished_at"].to_native() if task_node.get("finished_at") else None,
        progress=int(task_node["progress"]) if task_node.get("progress") is not None else None,
        message=task_node.get("message"),
        error=task_node.get("error"),
    )

