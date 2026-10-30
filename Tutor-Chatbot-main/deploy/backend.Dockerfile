# syntax=docker/dockerfile:1
# 后端镜像：FastAPI + uvicorn（Python 3.11，与本机 venv 3.11.8 对齐）
# 构建上下文 = Tutor-Chatbot-main/   （见 deploy/docker-compose.yml）
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/app/.model_cache

WORKDIR /app

# libgomp1: onnxruntime(fastembed 重排) 运行时依赖；curl: 健康检查用
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# 代码（.model_cache / chroma_* / logs 由 .dockerignore 排除，运行时用卷挂载）
COPY backend/ ./

# 非 root 运行
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
