# kg-api-server 需求说明（细化版）

## 1. 背景与目标

新建一个知识图谱接口服务项目 `kg-api-server`，对外提供：
- 触发知识图谱全量构建（版本号=触发时间戳）
- 触发知识图谱增量更新（版本号=触发时间戳，增量基于上次已完成版本）
- 查询构建/更新状态（用于展示与防止重复触发）
- 基于“最新已完成版本”的查询能力（类型列表、关键词子图查询、统计）

## 2. 范围与非目标

### 2.1 范围（In Scope）
- 服务端提供一组 HTTP API。
- 数据存储使用 Neo4j。
- 支持多版本隔离：保留历史版本，所有查询默认基于最新已完成版本。
- 通过“钩子函数（hook）”获取构建/更新所需的输入数据（字符串数组）。
- 将版本与任务状态等元数据持久化到 Neo4j（用于重启恢复与防重复触发）。

### 2.2 非目标（Out of Scope）
- 不定义前端界面。
- 不规定具体抽取/建图算法细节（由内部实现或复用现有组件决定），本需求只约束输入、产出与状态一致性。
- 不要求向后兼容旧接口（除非未来显式提出 HTTP API 兼容需求）。

## 3. 术语
- **版本（version）**：一次构建/更新触发时生成的时间戳版本号，UTC 毫秒时间戳字符串。
- **最新版本**：最新一次“已完成（READY）”的版本号，记为 `latest_ready_version`。
- **全量构建（full build）**：从全量数据构建出一个新版本图谱。
- **增量更新（incremental update）**：在上次已完成版本基础上，对增量数据进行更新，产出新版本图谱。
- **多版本隔离**：不同版本的数据在 Neo4j 中逻辑隔离，查询时可稳定读取某一版本，默认只读最新已完成版本。

## 4. 总体设计约束

### 4.1 版本号格式
- 版本号使用触发时间戳（建议使用 UTC 毫秒时间戳 `int64` 或 ISO8601 字符串；实现需在接口响应中明确一种并保持一致）。
- 本需求文档中将版本号作为字符串 `version` 表示（便于 JSON 传输）。

### 4.2 并发与幂等（防重复触发）
- 当系统状态为 `BUILDING` 或 `UPDATING` 时：
  - 禁止再次触发全量构建或增量更新
  - 返回 HTTP `409 Conflict`，并在响应中包含当前任务信息（task_id/version/status）
- 状态查询接口用于前端展示与“防重复触发”。

### 4.3 查询一致性
- 所有查询接口（类型/查询/统计）必须基于 `latest_ready_version`。
- 当有新版本正在构建/更新时：
  - 查询仍返回上一版 `latest_ready_version` 的数据
  - 不允许“读到半成品版本”

### 4.4 配置要求（避免硬编码）
- 服务配置使用 `kg-api-server/config.yaml`（路径可由启动参数指定）。
- Neo4j 连接信息、版本保留策略、分页/limit 默认值等必须可配置。

### 4.5 LLM 并发/限流与重试（OpenAI 兼容）
`kg-api-server` 内部会调用 OpenAI 兼容接口（LLM 与 Embeddings），需提供可配置的并发控制、限流与重试能力，避免触发上游限额导致任务失败。

要求：
- **并发控制**：可配置最大并发（例如同时处理多少段文本/多少批次请求）。
- **限流**：支持按 RPM（requests per minute）与 TPM（tokens per minute）进行节流。
- **重试机制**：对可恢复错误（超时、429、5xx、网络抖动等）按“指数回避（exponential backoff）”重试，支持配置：
  - `max_retries`（最大重试次数）
  - `initial_backoff_s`（初始回退秒数）
  - `max_backoff_s`（最大回退秒数）
  - `backoff_multiplier`（回退倍率，例如 2.0）

## 5. 状态机与持久化

### 5.1 状态机
系统全局状态 `state`：
- `IDLE`：无任务进行中（初始状态可以视为 `READY` 或 `IDLE`，实现自行选择）
- `BUILDING`：全量构建进行中
- `UPDATING`：增量更新进行中
- `READY`：最新版本可用
- `FAILED`：最近一次任务失败（仍可提供上一版 READY 版本查询）

状态转换（核心规则）：
- `IDLE/READY/FAILED` -> 触发全量构建 -> `BUILDING` -> 成功 -> `READY`
- `IDLE/READY/FAILED` -> 触发增量更新 -> `UPDATING` -> 成功 -> `READY`
- `BUILDING/UPDATING` -> 再次触发 -> `409 Conflict`（不改变状态）
- `BUILDING/UPDATING` -> 失败 -> `FAILED`

### 5.2 任务记录（status 查询返回）
需要记录并返回以下信息（字段名建议，最终以实现为准）：
- `status`: 上述状态枚举
- `current_task`: `null` 或任务对象
  - `task_id`：唯一任务 ID（可用与 version 相同）
  - `type`：`full_build` / `incremental_update`
  - `version`：本次任务产生的目标版本号
  - `base_version`：增量更新时的基线版本（=触发时的 `latest_ready_version`）；全量构建可为 `null`
  - `started_at` / `finished_at`
  - `progress`：0~100（可选）
  - `message`：可读状态描述（可选）
  - `error`：失败时错误信息（可选）
- `latest_ready_version`：字符串或 `null`

### 5.3 Neo4j 元数据持久化（单实例/单图谱）
本项目采用“单实例 + 单图谱”，不使用文件持久化；版本与任务状态元数据统一存储在 Neo4j 中。

#### 5.3.1 元数据模型（建议）
- `(:KGState {graph_name: string, status: string, latest_ready_version: string|null, current_task_id: string|null, updated_at: datetime})`
- `(:KGTask {task_id: string, type: string, version: string, base_version: string|null, started_at: datetime, finished_at: datetime|null, progress: int|null, error: string|null})`

约束建议：
- `KGState.graph_name` 唯一（单图谱也可固定为 `"default"`）。
- `KGTask.task_id` 唯一（可直接使用 `version` 或 `version` 派生）。

#### 5.3.2 触发互斥（防重复触发）
- 触发 build/update 前，必须先在 Neo4j 中原子更新 `KGState.status`：
  - 仅当当前 `status` 不为 `BUILDING/UPDATING` 时，才允许将其更新为目标状态并写入 `current_task_id`
  - 否则返回 `409 Conflict`

#### 5.3.3 重启恢复
- 服务启动时读取 `KGState`：
  - 若 `status` 为 `BUILDING/UPDATING`，视为上次任务异常中断：将 `status` 更新为 `FAILED`，并在对应 `KGTask.error` 记录 `server restarted`（或实现“恢复/重跑”，需另行评审）。

## 6. 多版本隔离（Neo4j 存储约束）

### 6.1 版本隔离策略（要求）
必须保证不同版本的数据互不干扰，并可在查询时明确指定版本。

推荐约束（实现可在满足隔离前提下调整）：
- 所有节点与关系都必须带上版本标识属性，例如：
  - 节点属性：`kg_version`
  - 关系属性：`kg_version`
- 查询时必须带上 `kg_version = latest_ready_version` 过滤。

### 6.2 版本保留策略（可配置）
- `retention.max_versions`：保留的最大 READY 版本数量（默认建议 5~20）。
- 超出保留数量时：
  - 可后台清理最旧版本（删除该版本的节点与关系）
  - 清理策略与是否启用由配置决定

## 7. Hook 约束（数据获取）

构建/更新的输入数据通过可插拔 hook 获取。hook 需要返回字符串数组 `List[str]`，每个字符串代表一段文本（如 chunk、段落、文档片段）。

### 7.1 全量数据 hook
- 函数签名（示例）：
  - `get_full_data() -> List[str]`
- 行为约束：
  - 返回全量数据集合
  - 失败需抛出异常，由任务状态记录为 `FAILED`

### 7.2 增量数据 hook
- 函数签名（示例）：
  - `get_incremental_data(since_version: str) -> List[str]`
- 参数说明：
  - `since_version`：上次已完成版本号（`latest_ready_version`）
- 行为约束：
  - 返回自 `since_version` 以来的增量数据集合
  - 若 `latest_ready_version` 为空（第一次运行），增量更新返回 `400`，提示先全量构建

### 7.3 Hook 配置方式（建议）
在 `config.yaml` 指定 hook 的导入路径，例如：
- `hooks.module: "kg_api_server.hooks"`
- `hooks.full: "get_full_data"`
- `hooks.incremental: "get_incremental_data"`

## 8. API 设计

### 8.1 通用约定
- Content-Type：`application/json; charset=utf-8`
- 统一响应结构建议：
  - `success: bool`
  - `data: object | null`
  - `error: {code: str, message: str, detail?: any} | null`

### 8.2 触发全量构建
- `POST /kg/build/full`
- 请求体（示例）：
  - `graph_name?: string`（可选，若系统支持多图谱；当前默认单图谱可省略）
  - `trigger_source?: string`（可选，如 "manual"/"schedule"）
- 行为：
  - 若当前 `status` 为 `BUILDING/UPDATING`，返回 `409`
  - 生成 `version=timestamp`，启动后台任务
  - 调用 `get_full_data()` 获取字符串数组并完成构建
- 响应（示例 data）：
  - `task_id`
  - `status=BUILDING`
  - `version`

### 8.3 触发增量更新
- `POST /kg/update/incremental`
- 请求体（示例）：
  - `graph_name?: string`
  - `trigger_source?: string`
- 行为：
  - 若当前 `status` 为 `BUILDING/UPDATING`，返回 `409`
  - 若 `latest_ready_version` 为空，返回 `400`（提示先执行全量构建）
  - 生成 `version=timestamp`，读取 `base_version=latest_ready_version`
  - 调用 `get_incremental_data(base_version)` 获取增量字符串数组并完成更新，产出新版本
- 响应（示例 data）：
  - `task_id`
  - `status=UPDATING`
  - `version`
  - `base_version`

### 8.4 查询构建/更新状态
- `GET /kg/status`
- 行为：
  - 返回持久化状态 + 当前任务信息 + 最新已完成版本号
- 响应（示例 data）：
  - `status`
  - `latest_ready_version`
  - `current_task`

### 8.5 获取所有实体类型
- `GET /kg/types/entities`
- 行为：
  - 基于 `latest_ready_version` 统计并返回实体类型列表
- 响应（示例 data）：
  - `version`
  - `entity_types: string[]`

### 8.6 获取所有关系类型
- `GET /kg/types/relations`
- 行为：
  - 基于 `latest_ready_version` 统计并返回关系类型列表
- 响应（示例 data）：
  - `version`
  - `relation_types: string[]`

### 8.7 图谱数据查询（关键词子图）
- `GET /kg/query`
- Query 参数：
  - `q?: string`（可选，关键词；不传或为空时返回最新版本全量图谱）
  - `limit_nodes?: int`（默认来自配置）
  - `limit_edges?: int`（默认来自配置）
  - `depth?: int`（可选，子图扩展深度，默认 1~2）
  - `include_properties?: bool`（默认 true）
- 行为：
  - 若 `latest_ready_version` 为空，返回 `404`（表示当前没有可查询的已完成版本）
  - 仅基于 `latest_ready_version` 查询
  - 当 `q` 非空时：返回“匹配关键词的节点”及其一定范围内的关联边，形成子图
  - 当 `q` 不传或为空时：返回 `latest_ready_version` 的全量图谱数据（`nodes`/`edges`）
  - 全量/子图两种模式均受 `limit_nodes`/`limit_edges` 约束；超出限制时设置 `truncated=true`
- 响应（示例 data）：
  - `version`
  - `nodes: [{id, labels/types, name?, properties}]`
  - `edges: [{id, type, source, target, properties}]`
  - `truncated: bool`（因 limit 截断时为 true，可选）

### 8.8 图谱统计
- `GET /kg/stats`
- 行为：
  - 基于 `latest_ready_version` 返回统计值
- 响应（示例 data）：
  - `version`
  - `entity_count: int`
  - `relation_count: int`
  - `node_type_count: int`

## 9. 错误码约定（建议）
- `400 Bad Request`
  - 参数缺失/非法
  - 增量更新但 `latest_ready_version` 为空
- `404 Not Found`
  - 当前没有可查询的已完成版本（`latest_ready_version` 为空）
- `409 Conflict`
  - 当前正在 `BUILDING/UPDATING`，拒绝重复触发
- `500 Internal Server Error`
  - Neo4j 连接失败、hook 异常、内部执行异常

错误响应建议：
- `error.code`：如 `TASK_RUNNING`、`NO_BASE_VERSION`、`HOOK_FAILED`、`NEO4J_ERROR`
- `error.message`：面向用户的简短信息
- `error.detail`：可选的调试信息（生产可关闭）

## 10. 配置文件（config.yaml）需求项（建议）
必须支持但不限于以下配置项：
- `server.host` / `server.port` / `server.cors_allow_origins`
- `neo4j.uri` / `neo4j.username` / `neo4j.password(_env)` / `neo4j.database`
- `retention.max_versions` / `retention.enable_cleanup`
- `query.default_limit_nodes` / `query.default_limit_edges` / `query.default_depth`
- `hooks.module` / `hooks.full` / `hooks.incremental`
- `task.timeout_s`（可选）

OpenAI 兼容模型配置（LLM 与 Embeddings）：
- `llm.api_key` / `llm.api_key_env`
- `llm.api_base_url`
- `llm.model`
- `llm.max_tokens`
- `llm.repetition_penalty`
- `llm.temperature`
- `llm.max_retries`（上游 SDK/HTTP 层最大重试次数；与本项目 backoff 重试策略配合）
- `llm.rate_limit.rpm` / `llm.rate_limit.tpm`
- `llm.concurrency.max_in_flight`（可选，限制同时在途请求数）
- `llm.retry.max_retries` / `llm.retry.initial_backoff_s` / `llm.retry.max_backoff_s` / `llm.retry.backoff_multiplier`

- `embeddings.api_key` / `embeddings.api_key_env`
- `embeddings.api_base_url`
- `embeddings.model`
- `embeddings.rate_limit.rpm` / `embeddings.rate_limit.tpm`
- `embeddings.concurrency.max_in_flight`（可选）
- `embeddings.retry.max_retries` / `embeddings.retry.initial_backoff_s` / `embeddings.retry.max_backoff_s` / `embeddings.retry.backoff_multiplier`

## 11. 验收标准（Acceptance Criteria）
- 能成功触发全量构建，生成新版本号，并在 Neo4j 中写入该版本数据。
- 能成功触发增量更新：以 `base_version=latest_ready_version` 为基线生成新版本，并保留历史版本数据。
- 构建/更新进行中时再次触发返回 `409`，且 `/kg/status` 可准确展示当前任务信息。
- 所有查询接口均只返回 `latest_ready_version` 对应的数据（构建/更新进行中也不影响查询一致性）。
- Neo4j 中的元数据（`KGState/KGTask`）能在服务重启后恢复 `latest_ready_version` 与可用状态（进行中任务按约定标记失败或恢复）。
- LLM/Embeddings 调用支持并发控制、RPM/TPM 限流与指数回避重试，在触发限额或短暂网络异常时任务可自动恢复或以明确错误失败。

## 12. 实现方案（建议）

本节为落地实现建议，用于统一工程结构与关键实现细节；若实现中需要调整，以不违反以上需求约束为准。

### 12.1 技术选型
- Python + FastAPI（HTTP API）
- Neo4j 官方 Python driver（读写图谱与元数据）
- `itext2kg/Atom` + `LangchainOutputParser`（抽取与构图，参考仓库内示例）
- OpenAI 兼容 SDK（例如 `langchain_openai`：`ChatOpenAI`/`OpenAIEmbeddings`）
- 配置：YAML
- 环境管理：uv

### 12.2 项目结构（建议）
- `kg-api-server/`
  - `pyproject.toml` / `uv.lock`
  - `config.example.yaml`
  - `kg_api_server/`
    - `main.py`（FastAPI 启动入口）
    - `config.py`（读取/校验 YAML，支持 *_env）
    - `models.py`（Pydantic 请求/响应模型）
    - `hooks.py`（按配置动态加载 hook）
    - `neo4j_client.py`（driver/session 封装）
    - `state_store.py`（KGState/KGTask 的读写与互斥逻辑）
    - `graph_store.py`（按版本写入/查询图谱数据的 Cypher）
    - `build_service.py`（全量构建/增量更新编排）
    - `rate_limit.py`（RPM/TPM + 并发控制）
    - `retry.py`（指数回避重试）

### 12.3 元数据与互斥实现（单实例）
- `KGState` 固定 `graph_name="default"`。
- 触发 build/update 时：
  - 在同一个事务内检查 `KGState.status`，若非 `BUILDING/UPDATING` 则更新为目标状态并写入 `current_task_id`；否则返回 `409`。
- `GET /kg/status` 读取 `KGState` + 关联 `KGTask` 返回。
- 启动时：
  - 若发现 `KGState.status` 为 `BUILDING/UPDATING`，将其置为 `FAILED` 并记录 `server restarted`。

### 12.4 图数据存储（多版本隔离）
要求满足：
- 图谱节点与关系必须包含属性 `kg_version=<version>`。
- 查询默认过滤 `kg_version = latest_ready_version`。

建议的业务图谱模型（便于类型统计与关键词查询）：
- 节点：统一使用标签 `:Entity`
  - 属性：`kg_version`、`name`、`entity_label`、`embeddings`（可选）以及抽取产生的其它属性
- 关系：统一使用关系类型 `:REL`
  - 属性：`kg_version`、`predicate`、`atomic_facts`/`t_obs`/`t_start`/`t_end`/`embeddings`（按抽取结果写入）

唯一性建议（同版本内）：
- 节点唯一：`(kg_version, entity_label, name)`
- 关系唯一：`(kg_version, start, end, predicate)`

### 12.5 构建/更新流水线（建议）
- 全量构建：
  1) 生成 `version`
  2) 调用 `get_full_data()` 得到 `List[str]`（每项为一段文本）
  3) 使用 `LangchainOutputParser` 从文本段落抽取 `AtomicFact`
  4) `Atom.build_graph(..., existing_knowledge_graph=None, ...)` 得到 `KnowledgeGraph`
  5) 按 `kg_version=version` 写入 Neo4j（节点与关系）
  6) 更新 `KGState.latest_ready_version=version`，`status=READY`
- 增量更新：
  1) 读取 `base_version=latest_ready_version` 并生成 `version`
  2) 调用 `get_incremental_data(base_version)` 得到增量 `List[str]`
  3) 从 Neo4j 读取 `base_version` 对应图谱，转为 `existing_knowledge_graph`
  4) `Atom.build_graph(..., existing_knowledge_graph=base_kg, ...)` 得到新 `KnowledgeGraph`
  5) 按 `kg_version=version` 写入 Neo4j（不覆盖旧版本）
  6) 更新 `KGState.latest_ready_version=version`，`status=READY`

### 12.6 查询实现（Cypher 思路）
- `GET /kg/query`：
  - `q` 为空：返回 `latest_ready_version` 下的全量图谱（受 `limit_nodes/limit_edges` 限制）
  - `q` 非空：先按 `name CONTAINS q`（可扩展到更多属性）筛选起始节点，再按 `depth` 扩展邻接边形成子图（同样受限）
- `GET /kg/types/entities`：`DISTINCT entity_label`（过滤版本）
- `GET /kg/types/relations`：`DISTINCT predicate`（过滤版本）
- `GET /kg/stats`：统计节点数、关系数、不同 `entity_label` 数

### 12.7 并发/限流/重试实现建议
- 并发：用 `asyncio.Semaphore` 控制在途请求数（分别对 LLM 与 Embeddings）。
- RPM/TPM：使用令牌桶或滑动窗口进行节流；TPM 以“估算 tokens”或“上游返回 usage”校准（实现可先估算、后续增强）。
- 重试：对 429/5xx/超时等错误按指数回避重试（配置 `initial_backoff_s/max_backoff_s/backoff_multiplier/max_retries`），并记录到 `KGTask.error` 便于排查。
