import logging
import hashlib
import re
import shutil
import time

from app.errors import ProcessingError
from app.investigation import investigate
from app.media import extract, inspect
from app.transcription import transcribe
from app.url_media import download

logger = logging.getLogger(__name__)
ACTIVE = {"queued", "downloading", "extracting", "transcribing"}
INVESTIGATING = {"queued", "retrieving", "interpreting"}


def is_active(row):
    return row["status"] in ACTIVE or row.get("investigation_status") in INVESTIGATING


def process(row, store, cfg):
    folder = cfg.data_dir / row["id"]
    audio = folder / "audio.wav"
    try:
        ingestion = row.get("ingestion", {"kind": "upload"})
        if ingestion["kind"] == "url" and not (folder / "clip").exists():
            row.update(status="downloading", error=None)
            store.save(row)
            downloaded = download(ingestion["url"], folder / "clip", cfg)
            row.update(sha256=downloaded["sha256"], size_bytes=downloaded["size_bytes"])
            ingestion["final_url"] = downloaded["final_url"]
        elif ingestion["kind"] == "url" and not row["sha256"]:
            # Crash after atomic download but before persistence: recover hash without re-fetching.
            clip = folder / "clip"
            row["size_bytes"] = clip.stat().st_size
            if not 0 < row["size_bytes"] <= cfg.max_upload_mb * 1024 * 1024:
                raise ProcessingError(
                    "upload_size_limit", "Retained clip exceeds the upload size limit."
                )
            with clip.open("rb") as f:
                row["sha256"] = hashlib.file_digest(f, "sha256").hexdigest()
        row.update(status="extracting", error=None)
        store.save(row)
        row["metadata"] = inspect(folder / "clip", cfg)
        extract(folder / "clip", audio, cfg)
        row["status"] = "transcribing"
        store.save(row)
        row["transcript"] = transcribe(audio, cfg.openai_key, row["metadata"]["duration"])
        row["status"] = "transcribed"
        row["investigation_status"] = "queued"
    except ProcessingError as exc:
        row.update(status="failed", error=exc.public())
    except Exception:
        # Do not log provider bodies, keys, file paths or transcript content.
        logger.error("Processing failed for analysis %s", row["id"])
        row.update(
            status="failed",
            error={
                "code": "processing_error",
                "message": "Processing failed. Retry or contact the server operator.",
            },
        )
    finally:
        audio.unlink(missing_ok=True)
    store.save(row)
    if row["status"] == "transcribed" or row.get("error", {}).get("code") in {
        "invalid_media",
        "no_audio",
        "duration_limit",
        "unsupported_format",
        "media_limits",
    }:
        (folder / "clip").unlink(missing_ok=True)


def recover(store, cfg):
    rows = store.all()
    for row in rows:
        # Remove extracted audio left by abrupt shutdown, without discarding retryable inputs.
        if re.fullmatch(r"[0-9a-f]{32}", row["id"]):
            (cfg.data_dir / row["id"] / "audio.wav").unlink(missing_ok=True)
            (cfg.data_dir / row["id"] / "clip.part").unlink(missing_ok=True)
        if row["status"] in {"downloading", "extracting", "transcribing"}:
            row.update(
                status="failed",
                error={
                    "code": "interrupted",
                    "message": "Server restarted during processing. Retry this clip.",
                },
            )
            store.save(row)
    # No auto-resubmission after an interrupted provider call: avoids surprise charges.
    for row in rows:
        if row.get("investigation_status") in {"retrieving", "interpreting"}:
            row.update(
                status="transcribed",
                investigation_status="unavailable",
                report=None,
                investigation_error={
                    "code": "interrupted",
                    "message": "Investigation interrupted; retry uses the retained transcript.",
                },
            )
            store.save(row)
    known = {r["id"] for r in rows}
    for folder in cfg.data_dir.iterdir():
        if (
            folder.is_dir()
            and not folder.is_symlink()
            and re.fullmatch(r"[0-9a-f]{32}", folder.name)
            and folder.name not in known
        ):
            shutil.rmtree(folder)


def tick(store, cfg):
    rows = store.all()
    now = time.time()
    for row in rows:
        if row["created_at"] + cfg.retention_hours * 3600 < now:
            shutil.rmtree(cfg.data_dir / row["id"], ignore_errors=True)
            store.delete(row["id"])
    pending = [
        r
        for r in rows
        if (
            r["status"] == "queued"
            or (r.get("investigation_status") == "queued" and r.get("transcript"))
        )
        and r["created_at"] + cfg.retention_hours * 3600 >= now
    ]
    if pending:
        row = min(pending, key=lambda r: r["created_at"])
        if row["status"] == "queued":
            process(row, store, cfg)
            return
        if row.get("ingestion", {}).get("kind") == "synthetic_demo":
            from app.demo import source

            investigate(row, store, cfg, [source()])
        else:
            investigate(row, store, cfg)
