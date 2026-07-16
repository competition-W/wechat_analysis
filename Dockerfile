FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
COPY api/ api/
COPY services/ services/
COPY models/ models/
COPY config/ config/
COPY utils/ utils/

RUN pip install --no-cache-dir uv && uv pip install --system .

FROM python:3.11-slim

WORKDIR /app

RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY api/ api/
COPY services/ services/
COPY models/ models/
COPY config/ config/
COPY utils/ utils/
COPY data/ data/

RUN mkdir -p logs archive reports && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

# Dashboard caches and request coalescing are process-local. One worker keeps
# them effective; FastAPI still runs the synchronous database routes in its
# thread pool. Scale out only after moving these caches to a shared backend.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
