FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    fastapi>=0.115.0 \
    "uvicorn[standard]>=0.30.0" \
    sentence-transformers \
    numpy \
    pydantic

COPY embedder_service.py .

EXPOSE 6335

CMD ["uvicorn", "embedder_service:app", "--host", "0.0.0.0", "--port", "6335"]
