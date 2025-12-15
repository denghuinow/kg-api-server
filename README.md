# kg-api-server

按 `kg-api-server/REQ.md` 实现的知识图谱接口服务（FastAPI + Neo4j，多版本隔离，最新 READY 版本查询）。

## 运行

```bash
cd kg-api-server
uv run kg-api-server --config ./config.yaml
```

## 配置

参考 `kg-api-server/config.example.yaml`，建议用环境变量提供密钥与 Neo4j 密码。

