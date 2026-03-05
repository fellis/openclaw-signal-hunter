"""
Embedder HTTP service.
Loads bge-m3 once at startup and serves embedding requests via HTTP.
Runs as a Docker container - always warm, no per-request model load overhead.

Endpoints:
  GET  /health          - liveness check
  POST /embed           - encode a list of texts, returns list of vectors
  POST /embed-query     - encode a single query string, returns one vector
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

log = logging.getLogger("embedder_service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_MODEL_NAME = os.environ.get("EMBEDDER_MODEL", "BAAI/bge-m3")
_model = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model
    log.info("[startup] loading model '%s'...", _MODEL_NAME)
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415
    _model = SentenceTransformer(_MODEL_NAME, device="cpu")
    log.info("[startup] model ready")
    yield
    log.info("[shutdown] bye")


app = FastAPI(title="Signal Hunter Embedder", lifespan=lifespan)


class EmbedRequest(BaseModel):
    texts: list[str]
    normalize: bool = True


class EmbedQueryRequest(BaseModel):
    text: str
    normalize: bool = True


class EmbedResponse(BaseModel):
    vectors: list[list[float]]
    model: str


class EmbedQueryResponse(BaseModel):
    vector: list[float]
    model: str


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "model": _MODEL_NAME, "ready": _model is not None}


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest) -> EmbedResponse:
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")
    if not req.texts:
        return EmbedResponse(vectors=[], model=_MODEL_NAME)

    log.info("[embed] encoding %d texts", len(req.texts))
    vectors: np.ndarray = _model.encode(
        req.texts,
        normalize_embeddings=req.normalize,
        show_progress_bar=False,
    )
    return EmbedResponse(vectors=vectors.tolist(), model=_MODEL_NAME)


@app.post("/embed-query", response_model=EmbedQueryResponse)
def embed_query(req: EmbedQueryRequest) -> EmbedQueryResponse:
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    log.info("[embed-query] encoding query")
    vector: np.ndarray = _model.encode(
        req.text,
        normalize_embeddings=req.normalize,
        show_progress_bar=False,
    )
    return EmbedQueryResponse(vector=vector.tolist(), model=_MODEL_NAME)
