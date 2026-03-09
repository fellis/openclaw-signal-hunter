"""
Signal Hunter - Web Report Server.
FastAPI app: REST API + serves pre-built React SPA from web_server/static/.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from web_server.routers import report, search, workers
from web_server.services.cache import Cache


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.cache = Cache()
    yield


app = FastAPI(title="Signal Hunter Web", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(report.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(workers.router, prefix="/api/workers")

_static_dir = os.path.join(os.path.dirname(__file__), "static")
_index_html = os.path.join(_static_dir, "index.html")


def _serve_spa():
    """Serve index.html for SPA client-side routes (direct URL or refresh)."""
    if os.path.isfile(_index_html):
        return FileResponse(_index_html)
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Frontend not built")


@app.get("/report", include_in_schema=False)
def get_report_spa():
    return _serve_spa()


@app.get("/charts", include_in_schema=False)
def get_charts_spa():
    return _serve_spa()


@app.get("/search", include_in_schema=False)
def get_search_spa():
    return _serve_spa()


@app.get("/logs", include_in_schema=False)
def get_logs_spa():
    return _serve_spa()


@app.get("/help", include_in_schema=False)
def get_help_spa():
    return _serve_spa()


if os.path.isdir(_static_dir):
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")
