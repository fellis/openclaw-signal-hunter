FROM python:3.11-slim

WORKDIR /app

# System deps for sentencepiece build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 6340

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "6340", "--workers", "1"]
