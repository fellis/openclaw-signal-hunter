"""
Translation microservice using MADLAD-400-3B-MT via CTranslate2.

Endpoints:
  POST /translate  - translate a batch of texts
  GET  /health     - health check + model info
  GET  /languages  - supported language codes

MADLAD-400 uses language tags like "2ru" (Russian), "2uk" (Ukrainian), etc.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import ctranslate2
import sentencepiece as spm
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from huggingface_hub import snapshot_download
from pydantic import BaseModel

log = logging.getLogger("translator")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

MODEL_ID = os.environ.get("MODEL_ID", "cstr/madlad400-3b-ct2-int8")
MODEL_DIR = os.environ.get("MODEL_DIR", "/models/madlad400-3b-ct2-int8")
INTER_THREADS = int(os.environ.get("INTER_THREADS", "4"))
INTRA_THREADS = int(os.environ.get("INTRA_THREADS", "8"))
MAX_BATCH = int(os.environ.get("MAX_BATCH", "32"))
MAX_INPUT_LENGTH = int(os.environ.get("MAX_INPUT_LENGTH", "512"))

# MADLAD-400 language tag mapping (target language → tag prepended to input)
LANG_TAGS: dict[str, str] = {
    "ru": "2ru",
    "uk": "2uk",
    "de": "2de",
    "fr": "2fr",
    "es": "2es",
    "zh": "2zh",
    "ja": "2ja",
    "ko": "2ko",
    "pt": "2pt",
    "it": "2it",
    "pl": "2pl",
    "nl": "2nl",
    "tr": "2tr",
    "ar": "2ar",
}


class TranslateRequest(BaseModel):
    texts: list[str]
    target_lang: str = "ru"
    max_decoding_length: int = 256


class TranslateResponse(BaseModel):
    translations: list[str]
    target_lang: str
    elapsed_ms: int


# Global model state loaded on startup
_translator: ctranslate2.Translator | None = None
_sp: spm.SentencePieceProcessor | None = None


def _ensure_model() -> tuple[ctranslate2.Translator, spm.SentencePieceProcessor]:
    global _translator, _sp
    if _translator is None or _sp is None:
        raise RuntimeError("Model not loaded")
    return _translator, _sp


def _download_model() -> str:
    """Download model from HuggingFace if not already cached."""
    if os.path.exists(os.path.join(MODEL_DIR, "model.bin")):
        log.info("Model already cached at %s", MODEL_DIR)
        return MODEL_DIR

    log.info("Downloading %s to %s ...", MODEL_ID, MODEL_DIR)
    os.makedirs(MODEL_DIR, exist_ok=True)
    snapshot_download(
        repo_id=MODEL_ID,
        local_dir=MODEL_DIR,
        local_dir_use_symlinks=False,
    )
    log.info("Download complete.")
    return MODEL_DIR


def _load_model(model_dir: str) -> tuple[ctranslate2.Translator, spm.SentencePieceProcessor]:
    log.info("Loading CTranslate2 model from %s ...", model_dir)
    translator = ctranslate2.Translator(
        model_dir,
        device="cpu",
        inter_threads=INTER_THREADS,
        intra_threads=INTRA_THREADS,
        compute_type="int8",
    )

    sp_path = os.path.join(model_dir, "sentencepiece.model")
    if not os.path.exists(sp_path):
        # Some repos use spiece.model
        sp_path = os.path.join(model_dir, "spiece.model")
    sp = spm.SentencePieceProcessor()
    sp.Load(sp_path)
    log.info("Model loaded. inter_threads=%d intra_threads=%d", INTER_THREADS, INTRA_THREADS)
    return translator, sp


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _translator, _sp
    model_dir = _download_model()
    _translator, _sp = _load_model(model_dir)
    yield
    _translator = None
    _sp = None


app = FastAPI(title="MADLAD-400 Translation Service", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok" if _translator is not None else "loading",
        "model": MODEL_ID,
        "inter_threads": INTER_THREADS,
        "intra_threads": INTRA_THREADS,
        "supported_languages": list(LANG_TAGS.keys()),
    }


@app.get("/languages")
def languages() -> dict[str, list[str]]:
    return {"languages": list(LANG_TAGS.keys())}


@app.post("/translate", response_model=TranslateResponse)
def translate(req: TranslateRequest) -> TranslateResponse:
    translator, sp = _ensure_model()

    if req.target_lang not in LANG_TAGS:
        raise HTTPException(400, f"Unsupported target_lang '{req.target_lang}'. Supported: {list(LANG_TAGS.keys())}")

    if not req.texts:
        return TranslateResponse(translations=[], target_lang=req.target_lang, elapsed_ms=0)

    if len(req.texts) > MAX_BATCH:
        raise HTTPException(400, f"Batch too large: max {MAX_BATCH} texts per request")

    tag = LANG_TAGS[req.target_lang]
    t0 = time.monotonic()

    # MADLAD-400: encode "<2ru> " + text together via sentencepiece.
    # The sp model recognizes <2ru> as a special token in the encoder input.
    tokenized = [
        sp.Encode(f"<{tag}> {text[:MAX_INPUT_LENGTH]}", out_type=str)
        for text in req.texts
    ]

    results = translator.translate_batch(
        tokenized,
        max_decoding_length=req.max_decoding_length,
        beam_size=2,
        no_repeat_ngram_size=4,
    )

    translations = [
        sp.Decode(r.hypotheses[0]) for r in results
    ]

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    log.info(
        "Translated %d texts to '%s' in %dms (%.1f ms/text)",
        len(req.texts), req.target_lang, elapsed_ms, elapsed_ms / len(req.texts),
    )

    return TranslateResponse(
        translations=translations,
        target_lang=req.target_lang,
        elapsed_ms=elapsed_ms,
    )
