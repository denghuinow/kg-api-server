from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from itext2kg.atom import Atom

from ..core import BuildService
from ..storage import (
    GRAPH_NAME_DEFAULT,
    Neo4jClient,
    StateStore,
    TaskConflictError,
    VersionedGraphStore,
)
from ..utils import (
    APIResponse,
    AppConfig,
    QueryResponse,
    StatsResponse,
    StatusResponse,
    TriggerFullBuildResponse,
    TriggerIncrementalUpdateResponse,
    TypesResponse,
    build_llm_resources,
    load_hooks,
    setup_logging,
    ThrottledLangchainOutputParser,
)
from ..utils.result_code import (
    ERROR,
    SUCCESS,
    TOKEN_FAIL_OR_EXPIRE,
    TOKEN_IS_NULL,
    KG_BUILD_FAILED,
    KG_INVALID_GRAPH_NAME,
    KG_NO_BASE_VERSION,
    KG_NO_READY_VERSION,
    KG_TASK_RUNNING,
    KG_UPDATE_FAILED,
)

logger = logging.getLogger(__name__)

# Bearer Token 验证
security = HTTPBearer(auto_error=False)


def verify_bearer_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
    cfg: Optional[AppConfig] = None,
) -> None:
    """
    验证 Bearer Token。强制要求验证，没有或错误将拒绝访问。
    """
    if cfg is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="服务器配置错误",
        )

    if cfg.server.api_key is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="服务器未配置 API Key",
        )

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少认证信息，请在请求头中提供 Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    if token != cfg.server.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_bearer_token_dependency(cfg: AppConfig):
    """创建 Bearer Token 验证依赖函数"""
    async def _verify(credentials: Optional[HTTPAuthorizationCredentials] = Security(security)) -> None:
        verify_bearer_token(credentials=credentials, cfg=cfg)
    return _verify


def _ok(data: Any) -> JSONResponse:
    return JSONResponse(
        content=APIResponse(code=SUCCESS[0], msg=SUCCESS[1], data=data, error=None).model_dump(mode="json")
    )


def _err(result_code: tuple[str, str], detail: Any = None) -> JSONResponse:
    """
    返回错误响应
    
    Args:
        result_code: 状态码元组 (code, msg)
        detail: 详细错误信息（可选）
    """
    error_detail = str(detail) if detail is not None else None
    return JSONResponse(
        content=APIResponse(
            code=result_code[0], msg=result_code[1], data=None, error=error_detail
        ).model_dump(mode="json"),
    )


class Resources:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.neo4j = Neo4jClient.from_config(cfg.neo4j)
        self.state_store = StateStore(self.neo4j, graph_name=GRAPH_NAME_DEFAULT)
        self.graph_store = VersionedGraphStore(self.neo4j, graph_name=GRAPH_NAME_DEFAULT)

        self.state_store.ensure_schema()
        self.state_store.recover_if_interrupted()

        self.hooks = load_hooks(cfg.hooks)

        llm_res = build_llm_resources(cfg)
        self.parser = ThrottledLangchainOutputParser(
            llm_model=llm_res.llm,
            embeddings_model=llm_res.embeddings,
            llm_limiter=llm_res.llm_limiter,
            emb_limiter=llm_res.emb_limiter,
            llm_retry=llm_res.llm_retry,
            emb_retry=llm_res.emb_retry,
            llm_max_concurrency=cfg.llm.concurrency.max_in_flight if cfg.llm.concurrency.max_in_flight > 0 else None,
            emb_max_in_flight=cfg.embeddings.concurrency.max_in_flight if cfg.embeddings.concurrency.max_in_flight > 0 else None,
        )
        self.atom = Atom(llm_model=llm_res.llm, embeddings_model=llm_res.embeddings, llm_output_parser=self.parser)

        self.build_service = BuildService(
            cfg=cfg,
            state_store=self.state_store,
            graph_store=self.graph_store,
            hooks=self.hooks,
            atom=self.atom,
            parser=self.parser,
        )

    def close(self) -> None:
        self.neo4j.close()


def create_app(cfg: AppConfig) -> FastAPI:
    app = FastAPI(title="kg-api-server", version="0.1.0")
    app.state.resources = Resources(cfg)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.server.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 创建 Bearer Token 验证依赖
    bearer_token_dependency = get_bearer_token_dependency(cfg)

    # 添加 HTTPException 异常处理器，统一响应格式
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """统一处理 HTTPException，转换为标准响应格式，固定返回 HTTP 200"""
        # 根据异常详情判断业务状态码
        if "缺少认证信息" in str(exc.detail) or "TOKEN为空" in str(exc.detail):
            result_code = TOKEN_IS_NULL
        elif "无效的认证令牌" in str(exc.detail) or "TOKEN校验失败" in str(exc.detail):
            result_code = TOKEN_FAIL_OR_EXPIRE
        else:
            # 其他错误：使用系统异常
            result_code = ERROR
        return _err(result_code, detail=str(exc.detail))

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        res: Resources = app.state.resources
        res.close()

    @app.get("/kg/status", dependencies=[Depends(bearer_token_dependency)])
    async def kg_status() -> JSONResponse:
        res: Resources = app.state.resources
        state, current_task = res.state_store.get_state_and_task()
        data = StatusResponse(
            status=state.status,
            latest_ready_version=state.latest_ready_version,
            current_task=current_task,
        )
        return _ok(data.model_dump(mode="json"))

    @app.post("/kg/build/full", dependencies=[Depends(bearer_token_dependency)])
    async def kg_build_full(request: Request) -> JSONResponse:
        res: Resources = app.state.resources
        payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        graph_name = (payload or {}).get("graph_name")
        if graph_name and str(graph_name).strip() != GRAPH_NAME_DEFAULT:
            return _err(KG_INVALID_GRAPH_NAME, detail=f"仅支持 graph_name={GRAPH_NAME_DEFAULT}")

        try:
            r = await res.build_service.trigger_full_build()
            data = TriggerFullBuildResponse(task_id=r.task_id, status="BUILDING", version=r.version)
            return _ok(data.model_dump(mode="json"))
        except TaskConflictError as e:
            detail = StatusResponse(
                status=e.state.status,
                latest_ready_version=e.state.latest_ready_version,
                current_task=e.current_task,
            ).model_dump(mode="json")
            return _err(KG_TASK_RUNNING, detail=detail)
        except Exception as e:
            logger.exception("触发全量构建失败")
            return _err(KG_BUILD_FAILED, detail=str(e))

    @app.post("/kg/update/incremental", dependencies=[Depends(bearer_token_dependency)])
    async def kg_update_incremental(request: Request) -> JSONResponse:
        res: Resources = app.state.resources
        payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        graph_name = (payload or {}).get("graph_name")
        if graph_name and str(graph_name).strip() != GRAPH_NAME_DEFAULT:
            return _err(KG_INVALID_GRAPH_NAME, detail=f"仅支持 graph_name={GRAPH_NAME_DEFAULT}")

        state, _ = res.state_store.get_state_and_task()
        if not state.latest_ready_version:
            return _err(KG_NO_BASE_VERSION)

        try:
            r = await res.build_service.trigger_incremental_update(latest_ready_version=state.latest_ready_version)
            data = TriggerIncrementalUpdateResponse(
                task_id=r.task_id,
                status="UPDATING",
                version=r.version,
                base_version=r.base_version or state.latest_ready_version,
            )
            return _ok(data.model_dump(mode="json"))
        except TaskConflictError as e:
            detail = StatusResponse(
                status=e.state.status,
                latest_ready_version=e.state.latest_ready_version,
                current_task=e.current_task,
            ).model_dump(mode="json")
            return _err(KG_TASK_RUNNING, detail=detail)
        except Exception as e:
            logger.exception("触发增量更新失败")
            return _err(KG_UPDATE_FAILED, detail=str(e))

    @app.get("/kg/types/entities", dependencies=[Depends(bearer_token_dependency)])
    async def kg_types_entities() -> JSONResponse:
        res: Resources = app.state.resources
        state, _ = res.state_store.get_state_and_task()
        if not state.latest_ready_version:
            return _err(KG_NO_READY_VERSION)
        types = res.graph_store.get_entity_types(state.latest_ready_version)
        data = TypesResponse(version=state.latest_ready_version, entity_types=types)
        return _ok(data.model_dump(mode="json"))

    @app.get("/kg/types/relations", dependencies=[Depends(bearer_token_dependency)])
    async def kg_types_relations() -> JSONResponse:
        res: Resources = app.state.resources
        state, _ = res.state_store.get_state_and_task()
        if not state.latest_ready_version:
            return _err(KG_NO_READY_VERSION)
        types = res.graph_store.get_relation_types(state.latest_ready_version)
        data = TypesResponse(version=state.latest_ready_version, relation_types=types)
        return _ok(data.model_dump(mode="json"))

    @app.get("/kg/query", dependencies=[Depends(bearer_token_dependency)])
    async def kg_query(
        q: Optional[str] = Query(None),
        entity_types: Optional[str] = Query(None, description="实体类型筛选，支持多选，逗号分隔"),
        relation_types: Optional[str] = Query(None, description="关系类型筛选，支持多选，逗号分隔"),
        limit_nodes: Optional[int] = Query(None, ge=1),
        limit_edges: Optional[int] = Query(None, ge=0),
        depth: Optional[int] = Query(None, ge=0),
        include_properties: bool = Query(False),
    ) -> JSONResponse:
        res: Resources = app.state.resources
        state, _ = res.state_store.get_state_and_task()
        if not state.latest_ready_version:
            return _err(KG_NO_READY_VERSION)

        def _split_csv(v: Optional[str]) -> Optional[list[str]]:
            if not v:
                return None
            items = [s.strip() for s in str(v).split(",")]
            items = [s for s in items if s]
            return items or None

        nodes, edges, truncated = res.graph_store.query_graph(
            version=state.latest_ready_version,
            q=q,
            entity_types=_split_csv(entity_types),
            relation_types=_split_csv(relation_types),
            limit_nodes=limit_nodes or res.cfg.query.default_limit_nodes,
            limit_edges=limit_edges or res.cfg.query.default_limit_edges,
            depth=depth if depth is not None else res.cfg.query.default_depth,
            max_seed_nodes=res.cfg.query.max_seed_nodes,
            include_properties=include_properties,
        )
        data = QueryResponse(version=state.latest_ready_version, nodes=nodes, edges=edges, truncated=truncated)
        return _ok(data.model_dump(mode="json"))

    @app.get("/kg/stats", dependencies=[Depends(bearer_token_dependency)])
    async def kg_stats() -> JSONResponse:
        res: Resources = app.state.resources
        state, _ = res.state_store.get_state_and_task()
        if not state.latest_ready_version:
            return _err(KG_NO_READY_VERSION)

        entity_count, relation_count, node_type_count = res.graph_store.get_stats(state.latest_ready_version)
        data = StatsResponse(
            version=state.latest_ready_version,
            entity_count=entity_count,
            relation_count=relation_count,
            node_type_count=node_type_count,
        )
        return _ok(data.model_dump(mode="json"))

    return app
