# Production image: Daphne (ASGI — HTTP and the multiplayer WebSockets) with
# static files collected for the admin and the API docs. CPU-only PyTorch is
# pinned first so sentence-transformers does not pull the multi-gigabyte CUDA
# wheels onto a 4 GB demo box.
FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install -r requirements.txt

COPY . .

# Admin + API-docs assets, served by Caddy from the shared volume.
RUN SECRET_KEY=build DEBUG=False python manage.py collectstatic --noinput

# SOC2:SEC-05 the server runs as an unprivileged user. HF_HOME keeps the
# embedding-model cache under /app (the deployment mounts a volume there)
# rather than under /root, which the app user cannot write.
ENV HF_HOME=/app/.cache/huggingface
RUN useradd --system --uid 10001 --create-home --home-dir /home/app app \
    && mkdir -p /app/.cache/huggingface \
    && chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/health/ || exit 1

CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]
