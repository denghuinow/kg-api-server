FROM python:3.11-slim

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv
RUN pip install --no-cache-dir uv

# 设置工作目录
WORKDIR /app

# 复制 itext2kg 依赖（构建上下文是父目录）
COPY itext2kg /app/../itext2kg

# 复制项目文件
COPY kg-api-server/pyproject.toml kg-api-server/uv.lock ./
COPY kg-api-server/server ./server

# 安装依赖
RUN uv sync --frozen

# 创建 tiktoken 缓存目录并预下载编码文件
# 设置环境变量后尝试下载，失败时仅记录警告（构建时网络可能不可用）
RUN mkdir -p /app/data/tiktoken_cache && \
    TIKTOKEN_CACHE_DIR=/app/data/tiktoken_cache uv run python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')" 2>&1 || echo "警告: tiktoken 编码文件下载失败，将在运行时重试"

# 设置 tiktoken 缓存目录环境变量
ENV TIKTOKEN_CACHE_DIR=/app/data/tiktoken_cache

# 复制配置文件（如果存在）
COPY kg-api-server/config.yaml* ./

# 暴露端口
EXPOSE 8021

# 启动服务
CMD ["uv", "run", "kg-api-server", "--config", "./config.yaml"]

