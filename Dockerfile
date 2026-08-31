# 使用官方 Python 3.11 轻量镜像
FROM python:3.11-slim as builder

# 安装编译依赖（部分 Python 包需要编译）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先复制依赖文件，利用 Docker 缓存层
COPY server/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# 第二阶段：运行时镜像（更干净、更小）
FROM python:3.11-slim

# 安全：创建非 root 用户运行服务
RUN groupadd -r appgroup && useradd -r -g appgroup appuser

WORKDIR /app

# 从 builder 复制已安装的依赖
COPY --from=builder /root/.local /home/appuser/.local
ENV PATH=/home/appuser/.local/bin:$PATH

# 复制项目代码
COPY server/ ./server/
COPY data/ ./data/
COPY eval/ ./eval/

# 创建运行时目录并赋权
RUN mkdir -p server/runtime server/chroma_db && chown -R appuser:appgroup /app

# 切换到非 root 用户
USER appuser

# 健康检查：每 30 秒检查 /health，超时 3 秒，连续 3 次失败则认为 unhealthy
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["python", "-m", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
