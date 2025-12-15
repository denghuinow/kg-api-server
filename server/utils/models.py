from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, Field


KGStatus = Literal["IDLE", "BUILDING", "UPDATING", "READY", "FAILED"]
TaskType = Literal["full_build", "incremental_update"]


class APIError(BaseModel):
    code: str
    message: str
    detail: Optional[Any] = None


T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    success: bool
    data: Optional[T] = None
    error: Optional[APIError] = None


class TriggerRequest(BaseModel):
    graph_name: Optional[str] = None
    trigger_source: Optional[str] = None


class TriggerFullBuildResponse(BaseModel):
    task_id: str
    status: KGStatus
    version: str


class TriggerIncrementalUpdateResponse(BaseModel):
    task_id: str
    status: KGStatus
    version: str
    base_version: str


class TaskInfo(BaseModel):
    task_id: str
    type: TaskType
    version: str
    base_version: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime] = None
    progress: Optional[int] = Field(None, ge=0, le=100)
    message: Optional[str] = None
    error: Optional[str] = None


class StatusResponse(BaseModel):
    status: KGStatus
    current_task: Optional[TaskInfo] = None
    latest_ready_version: Optional[str] = None


class TypesResponse(BaseModel):
    version: str
    entity_types: Optional[list[str]] = None
    relation_types: Optional[list[str]] = None


class QueryNode(BaseModel):
    id: str
    types: list[str]
    name: Optional[str] = None
    properties: Optional[Dict[str, Any]] = None


class QueryEdge(BaseModel):
    id: str
    type: str
    source: str
    target: str
    properties: Optional[Dict[str, Any]] = None


class QueryResponse(BaseModel):
    version: str
    nodes: list[QueryNode]
    edges: list[QueryEdge]
    truncated: bool = False


class StatsResponse(BaseModel):
    version: str
    entity_count: int
    relation_count: int
    node_type_count: int

