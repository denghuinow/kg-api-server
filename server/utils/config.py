from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


def load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _resolve_str(section: Dict[str, Any], key: str, required: bool = False) -> Optional[str]:
    value = section.get(key)
    if value is not None and str(value).strip() != "":
        return str(value)
    env_key = section.get(f"{key}_env")
    if env_key:
        env_value = os.getenv(str(env_key))
        if env_value is not None and env_value.strip() != "":
            return env_value
    if required:
        raise ValueError(f"配置字段缺失: {key} / {key}_env")
    return None


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    cors_allow_origins: list[str]
    api_key: str


@dataclass(frozen=True)
class Neo4jConfig:
    uri: str
    username: str
    password: str
    database: Optional[str]


@dataclass(frozen=True)
class RetryConfig:
    max_retries: int
    initial_backoff_s: float
    max_backoff_s: float
    backoff_multiplier: float


@dataclass(frozen=True)
class RateLimitConfig:
    rpm: int
    tpm: int


@dataclass(frozen=True)
class ConcurrencyConfig:
    max_in_flight: int


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    api_base_url: Optional[str]
    model: str
    max_tokens: Optional[int]
    temperature: float
    max_retries: int
    rate_limit: RateLimitConfig
    concurrency: ConcurrencyConfig
    retry: RetryConfig
    repetition_penalty: Optional[float]


@dataclass(frozen=True)
class EmbeddingsConfig:
    api_key: str
    api_base_url: Optional[str]
    model: str
    rate_limit: RateLimitConfig
    concurrency: ConcurrencyConfig
    retry: RetryConfig


@dataclass(frozen=True)
class HooksConfig:
    module: str
    full: str
    incremental: str
    connection_string: Optional[str]
    table_name: Optional[str]


@dataclass(frozen=True)
class RetentionConfig:
    max_versions: int
    enable_cleanup: bool


@dataclass(frozen=True)
class QueryConfig:
    default_limit_nodes: int
    default_limit_edges: int
    default_depth: int
    max_depth: int
    max_seed_nodes: int


@dataclass(frozen=True)
class TaskConfig:
    timeout_s: int


@dataclass(frozen=True)
class AppConfig:
    server: ServerConfig
    neo4j: Neo4jConfig
    retention: RetentionConfig
    query: QueryConfig
    hooks: HooksConfig
    llm: LLMConfig
    embeddings: EmbeddingsConfig
    task: TaskConfig
    raw: Dict[str, Any]


def _read_rate_limit(section: Dict[str, Any]) -> RateLimitConfig:
    rl = section.get("rate_limit") or {}
    return RateLimitConfig(
        rpm=int(rl.get("rpm", 0) or 0),
        tpm=int(rl.get("tpm", 0) or 0),
    )


def _read_concurrency(section: Dict[str, Any]) -> ConcurrencyConfig:
    conc = section.get("concurrency") or {}
    return ConcurrencyConfig(max_in_flight=int(conc.get("max_in_flight", 0) or 0))


def _read_retry(section: Dict[str, Any]) -> RetryConfig:
    retry = section.get("retry") or {}
    return RetryConfig(
        max_retries=int(retry.get("max_retries", 0) or 0),
        initial_backoff_s=float(retry.get("initial_backoff_s", 1.0)),
        max_backoff_s=float(retry.get("max_backoff_s", 30.0)),
        backoff_multiplier=float(retry.get("backoff_multiplier", 2.0)),
    )


def parse_config(raw: Dict[str, Any]) -> AppConfig:
    server = raw.get("server") or {}
    neo4j = raw.get("neo4j") or {}
    hooks = raw.get("hooks") or {}
    retention = raw.get("retention") or {}
    query = raw.get("query") or {}
    task = raw.get("task") or {}

    llm = raw.get("llm") or {}
    embeddings = raw.get("embeddings") or {}

    server_cfg = ServerConfig(
        host=str(server.get("host", "0.0.0.0")),
        port=int(server.get("port", 8021)),
        cors_allow_origins=list(server.get("cors_allow_origins", ["*"])),
        api_key=_resolve_str(server, "api_key", required=True) or "",
    )

    neo4j_cfg = Neo4jConfig(
        uri=_resolve_str(neo4j, "uri", required=True) or "",
        username=_resolve_str(neo4j, "username", required=True) or "",
        password=_resolve_str(neo4j, "password", required=True) or "",
        database=_resolve_str(neo4j, "database", required=False),
    )

    hooks_cfg = HooksConfig(
        module=_resolve_str(hooks, "module", required=True) or "",
        full=_resolve_str(hooks, "full", required=True) or "",
        incremental=_resolve_str(hooks, "incremental", required=True) or "",
        connection_string=_resolve_str(hooks, "connection_string", required=False),
        table_name=_resolve_str(hooks, "table_name", required=False),
    )

    retention_cfg = RetentionConfig(
        max_versions=int(retention.get("max_versions", 10)),
        enable_cleanup=bool(retention.get("enable_cleanup", True)),
    )

    query_cfg = QueryConfig(
        default_limit_nodes=int(query.get("default_limit_nodes", 500)),
        default_limit_edges=int(query.get("default_limit_edges", 1000)),
        default_depth=int(query.get("default_depth", 2)),
        max_depth=int(query.get("max_depth", 5)),
        max_seed_nodes=int(query.get("max_seed_nodes", 30)),
    )

    task_cfg = TaskConfig(timeout_s=int(task.get("timeout_s", 0)))

    llm_cfg = LLMConfig(
        api_key=_resolve_str(llm, "api_key", required=True) or "",
        api_base_url=_resolve_str(llm, "api_base_url"),
        model=_resolve_str(llm, "model", required=True) or "",
        max_tokens=int(llm["max_tokens"]) if llm.get("max_tokens") is not None else None,
        temperature=float(llm.get("temperature", 0.0)),
        max_retries=int(llm.get("max_retries", 0)),
        rate_limit=_read_rate_limit(llm),
        concurrency=_read_concurrency(llm),
        retry=_read_retry(llm),
        repetition_penalty=float(llm["repetition_penalty"]) if llm.get("repetition_penalty") is not None else None,
    )

    embeddings_cfg = EmbeddingsConfig(
        api_key=_resolve_str(embeddings, "api_key", required=True) or "",
        api_base_url=_resolve_str(embeddings, "api_base_url"),
        model=_resolve_str(embeddings, "model", required=True) or "",
        rate_limit=_read_rate_limit(embeddings),
        concurrency=_read_concurrency(embeddings),
        retry=_read_retry(embeddings),
    )

    return AppConfig(
        server=server_cfg,
        neo4j=neo4j_cfg,
        retention=retention_cfg,
        query=query_cfg,
        hooks=hooks_cfg,
        llm=llm_cfg,
        embeddings=embeddings_cfg,
        task=task_cfg,
        raw=raw,
    )
