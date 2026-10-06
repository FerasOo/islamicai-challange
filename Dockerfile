FROM --platform=$BUILDPLATFORM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY app/frontend/package.json app/frontend/package-lock.json ./
RUN npm ci
COPY app/frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DALEEL_EMBEDDING_BACKEND=torch DALEEL_CPU_THREADS=2 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    HF_HOME=/opt/daleel/app/runtime/hf-cache \
    TORCH_HOME=/opt/daleel/app/runtime/torch-cache \
    RAYON_NUM_THREADS=2 OMP_NUM_THREADS=2

WORKDIR /opt/daleel

RUN apt-get update && apt-get install -y --no-install-recommends libsndfile1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
# CPU-only wheels avoid bundling CUDA libraries in the deployment image.
RUN pip install --no-cache-dir torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cpu
COPY app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY app/backend/ app/backend/
COPY --from=frontend /build/dist/ app/frontend/dist/
COPY app/models/ app/models/
COPY retrieval/ retrieval/
RUN useradd --uid 10001 --create-home daleel \
    && mkdir -p app/runtime && chown -R daleel:daleel app/runtime
ENV DALEEL_CORPUS_IMMUTABLE=1
USER daleel
EXPOSE 8765
HEALTHCHECK --start-period=180s --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8765')+'/api/health',timeout=4)"
CMD ["sh", "-c", "exec python -m uvicorn app.backend.main:app --host 0.0.0.0 --port ${PORT:-8765}"]
