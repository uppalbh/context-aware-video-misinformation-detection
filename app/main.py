import asyncio
import hashlib
import logging
import re
import secrets
import shutil
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from filelock import FileLock

from app.config import Settings
from app.db import Store
from app.worker import ACTIVE, recover, tick


def create_app(cfg=None, run_worker=True):
    cfg = cfg or Settings()
    cfg.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    upload_lock = asyncio.Lock()

    async def worker_loop(store, stop):
        while not stop.is_set():
            try:
                await asyncio.to_thread(tick, store, cfg)
            except Exception:
                logging.error("Worker persistence unavailable; retrying on next poll")
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
            except TimeoutError:
                pass

    @asynccontextmanager
    async def lifespan(app):
        # A single process owns the private disk and durable queue. Fail fast on duplicate workers.
        with FileLock(cfg.data_dir / "server.lock", timeout=0):
            app.state.store = Store(cfg)
            await asyncio.to_thread(recover, app.state.store, cfg)
            stop = asyncio.Event()
            task = asyncio.create_task(worker_loop(app.state.store, stop)) if run_worker else None
            try:
                yield
            finally:
                if task:
                    stop.set()
                    # Wait for the bounded in-flight provider/subprocess call before releasing the lock.
                    await task

    app = FastAPI(title="ClipContext ingestion & transcription", lifespan=lifespan)
    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    async def persistence_error(request, exc):
        return JSONResponse(
            status_code=503, content={"detail": "Analysis storage is unavailable. Retry later."}
        )

    app.add_exception_handler(httpx.HTTPError, persistence_error)
    app.add_exception_handler(sqlite3.Error, persistence_error)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    def owner(request):
        token = request.cookies.get("clipcontext_session", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise HTTPException(401, "Open ClipContext to establish a private session.")
        return hashlib.sha256(token.encode()).hexdigest()

    def write_guard(request):
        # Same-origin browser requests only; no CORS. CLI clients may omit Origin.
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Cross-origin uploads are not allowed.")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Cross-site requests are not allowed.")

    async def owned(request, analysis_id):
        if not re.fullmatch(r"[0-9a-f]{32}", analysis_id):
            raise HTTPException(404, "Analysis not found.")
        row = await asyncio.to_thread(app.state.store.get, analysis_id)
        if not row or not secrets.compare_digest(row["owner"], owner(request)):
            raise HTTPException(404, "Analysis not found.")
        return row

    def public(row):
        return {k: v for k, v in row.items() if k != "owner"}

    @app.get("/")
    def index(request: Request):
        response = FileResponse(static / "index.html")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", request.cookies.get("clipcontext_session", "")):
            response.set_cookie(
                "clipcontext_session",
                secrets.token_urlsafe(32),
                httponly=True,
                secure=cfg.cookie_secure,
                samesite="strict",
                max_age=86400 * 7,
            )
        return response

    @app.get("/api/config")
    def config():
        return {
            "max_upload_bytes": cfg.max_upload_mb * 1024 * 1024,
            "max_duration_seconds": cfg.max_duration,
            "url_ingestion": False,
        }

    @app.post("/api/analyses", status_code=202)
    async def upload(request: Request):
        write_guard(request)
        user = owner(request)
        if request.headers.get("content-type", "").split(";")[0] not in {
            "video/mp4",
            "video/quicktime",
        }:
            raise HTTPException(415, "Send an MP4/MOV file as the raw request body.")
        async with upload_lock:
            rows = await asyncio.to_thread(app.state.store.all)
            if sum(r["status"] in ACTIVE for r in rows) >= cfg.max_pending:
                raise HTTPException(429, "Processing queue is full. Retry later.")
            # Bound records/storage and provider spending per session.
            if sum(r["owner"] == user for r in rows) >= 10 or len(rows) >= 500:
                raise HTTPException(
                    429, "Daily analysis capacity reached. Try again after retention expires."
                )
            limit = cfg.max_upload_mb * 1024 * 1024
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                raise HTTPException(400, "Invalid content length.")
            if length > limit:
                raise HTTPException(413, "Clip exceeds the upload size limit.")
            analysis_id = uuid.uuid4().hex
            folder = cfg.data_dir / analysis_id
            folder.mkdir(mode=0o700)
            digest, size = hashlib.sha256(), 0
            try:
                async with asyncio.timeout(120):
                    with (folder / "clip").open("wb") as f:
                        async for chunk in request.stream():
                            size += len(chunk)
                            if size > limit:
                                raise HTTPException(413, "Clip exceeds the upload size limit.")
                            digest.update(chunk)
                            await asyncio.to_thread(f.write, chunk)
                if not size:
                    raise HTTPException(400, "Clip is empty.")
                row = {
                    "id": analysis_id,
                    "owner": user,
                    "status": "queued",
                    "created_at": time.time(),
                    "sha256": digest.hexdigest(),
                    "size_bytes": size,
                    "metadata": None,
                    "transcript": None,
                    "error": None,
                    "attempts": 1,
                    "investigation_status": "not_started",
                }
                await asyncio.to_thread(app.state.store.save, row, True)
            except BaseException as exc:
                shutil.rmtree(folder, ignore_errors=True)
                if isinstance(exc, TimeoutError):
                    raise HTTPException(408, "Upload timed out.")
                raise
            return {"analysis_id": analysis_id, "status": "queued"}

    @app.get("/api/analyses")
    async def history(request: Request):
        user = owner(request)
        return [
            public(row)
            for row in await asyncio.to_thread(app.state.store.all)
            if row["owner"] == user
        ]

    @app.get("/api/analyses/{analysis_id}")
    async def result(analysis_id: str, request: Request):
        return public(await owned(request, analysis_id))

    @app.post("/api/analyses/{analysis_id}/retry", status_code=202)
    async def retry(analysis_id: str, request: Request):
        write_guard(request)
        async with upload_lock:
            row = await owned(request, analysis_id)
            if row["status"] != "failed" or row["attempts"] >= 3:
                raise HTTPException(
                    409, "Only failed jobs with fewer than three attempts can retry."
                )
            if not (cfg.data_dir / analysis_id / "clip").exists():
                raise HTTPException(410, "Media was removed. Upload a new clip.")
            rows = await asyncio.to_thread(app.state.store.all)
            if sum(r["status"] in ACTIVE for r in rows) >= cfg.max_pending:
                raise HTTPException(429, "Processing queue is full. Retry later.")
            row.update(status="queued", error=None, attempts=row["attempts"] + 1)
            await asyncio.to_thread(app.state.store.save, row)
            return {"analysis_id": analysis_id, "status": "queued"}

    @app.delete("/api/analyses/{analysis_id}", status_code=204)
    async def delete(analysis_id: str, request: Request):
        write_guard(request)
        async with upload_lock:
            row = await owned(request, analysis_id)
            if row["status"] in ACTIVE:
                raise HTTPException(409, "Wait until processing ends before deleting.")
            await asyncio.to_thread(app.state.store.delete, analysis_id)
            shutil.rmtree(cfg.data_dir / analysis_id, ignore_errors=True)
        return Response(status_code=204)

    return app


app = create_app()
