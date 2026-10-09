import logging
import shutil
import time

from app.errors import ProcessingError
from app.media import extract, inspect
from app.transcription import transcribe

logger = logging.getLogger(__name__)
ACTIVE = {"queued", "extracting", "transcribing"}


def process(row, store, cfg):
    folder = cfg.data_dir / row["id"]
    audio = folder / "audio.wav"
    try:
        row.update(status="extracting", error=None)
        store.save(row)
        row["metadata"] = inspect(folder / "clip", cfg)
        extract(folder / "clip", audio, cfg)
        row["status"] = "transcribing"
        store.save(row)
        row["transcript"] = transcribe(audio, cfg.openai_key, row["metadata"]["duration"])
        row["status"] = "transcribed"
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
        if row["status"] in {"extracting", "transcribing"}:
            row.update(
                status="failed",
                error={
                    "code": "interrupted",
                    "message": "Server restarted during processing. Retry this clip.",
                },
            )
            store.save(row)
    # No auto-resubmission after an interrupted provider call: avoids surprise charges.
    known = {r["id"] for r in rows}
    for folder in cfg.data_dir.iterdir():
        if folder.is_dir() and folder.name not in known:
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
        if r["status"] == "queued" and r["created_at"] + cfg.retention_hours * 3600 >= now
    ]
    if pending:
        process(min(pending, key=lambda r: r["created_at"]), store, cfg)
