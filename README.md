# kg-api-server

按 `kg-api-server/REQ.md` 实现的知识图谱接口服务（FastAPI + Neo4j，多版本隔离，最新 READY 版本查询）。

## 运行

```bash
cd kg-api-server
uv run kg-api-server --config ./config.yaml
```

## 配置

参考 `kg-api-server/config.example.yaml`，建议用环境变量提供密钥与 Neo4j 密码。

## Tiktoken 缓存配置

为了避免网络环境下的 SSL 连接错误，建议预先下载 tiktoken 编码文件到本地缓存。

### 方法 1: 使用预下载脚本（推荐）

```bash
cd kg-api-server
# 预下载 tiktoken 编码文件到本地缓存
uv run python prepare_tiktoken_cache.py
```

脚本会在 `./data/tiktoken_cache` 目录下创建缓存文件，Docker Compose 会自动挂载此目录。

### 方法 2: Docker 构建时自动下载

Dockerfile 已配置在构建时自动下载 tiktoken 编码文件，但可能因网络问题失败。

### 方法 3: 手动挂载本地缓存

如果已有 tiktoken 缓存目录，可以直接挂载：

```yaml
# docker-compose.yml
volumes:
  - /path/to/your/tiktoken_cache:/app/data/tiktoken_cache
```

### 环境变量

通过 `TIKTOKEN_CACHE_DIR` 环境变量指定缓存目录：

```bash
export TIKTOKEN_CACHE_DIR=/app/data/tiktoken_cache
```

Docker Compose 已自动配置此环境变量。

