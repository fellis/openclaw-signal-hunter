# Worker runner container: runs run_worker, run_embed_worker, run_collect_worker, embed in a loop.
# No LLM/embedder inside this image - calls embedder via HTTP (EMBEDDER_URL).

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY skill/ ./skill/
COPY core/ ./core/
COPY storage/ ./storage/
COPY collectors/ ./collectors/
COPY scripts/ ./scripts/

RUN chmod +x /app/scripts/run_workers.sh

# config.json and .env mounted at runtime
CMD ["/bin/bash", "/app/scripts/run_workers.sh"]
