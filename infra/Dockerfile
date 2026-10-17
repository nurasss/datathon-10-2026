# ForteVision — CPU-only image (no GPU, no external AI calls at runtime).
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

# ffmpeg provides ffprobe (isolated metadata probe); libgl1/libglib are needed by
# OpenCV wheels that are not "-headless".
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 libgl1 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY apps/api/requirements.txt /app/apps/api/requirements.txt
RUN pip install --no-cache-dir -r /app/apps/api/requirements.txt

COPY apps /app/apps
COPY packages /app/packages
COPY configs /app/configs
COPY docs /app/docs

ENV FV_PROJECT_ROOT=/app \
    FV_DATA_DIR=/data \
    FV_INFERENCE_CONFIG=/app/configs/inference.default.json \
    PYTHONPATH=/app/apps/api:/app/packages/vision:/app/packages/rules

RUN useradd -m -u 10001 appuser && mkdir -p /data && chown -R appuser:appuser /data /app
USER appuser
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1

WORKDIR /app/apps/api
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-keep-alive", "20"]
