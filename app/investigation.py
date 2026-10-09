import logging
from dataclasses import replace

from app.context_analysis import analyze
from app.corpus import load_corpus
from app.errors import ProcessingError
from app.report import assemble
from app.source_search import search


def investigate(row, store, cfg, sources=None):
    try:
        row.update(
            status="transcribed",
            investigation_status="retrieving",
            investigation_error=None,
            report=None,
        )
        store.save(row)
        sources = load_corpus(cfg.corpus_dir) if sources is None else sources
        if row.get("ingestion", {}).get("kind") == "synthetic_demo":
            cfg = replace(cfg, openai_key="", semantic_provider="none")
        # Real uploads never silently match against synthetic examples.
        if row.get("ingestion", {}).get("kind") != "synthetic_demo":
            sources = [s for s in sources if s["kind"] == "real"]
        retrieval = search(row["transcript"], sources, cfg)
        if not retrieval["selected"]:
            row.update(report=assemble(retrieval), investigation_status=retrieval["status"])
        else:
            row["investigation_status"] = "interpreting"
            store.save(row)
            selected = retrieval["selected"]
            context = analyze(selected["alignment"], selected["source"], cfg)
            row["report"] = assemble(retrieval, context)
            row["investigation_status"] = (
                "completed" if context["status"] == "completed" else context["status"]
            )
        if row["investigation_status"] == "completed":
            row["status"] = "completed"
    except ProcessingError as exc:
        row.update(
            investigation_status="unavailable", investigation_error=exc.public(), report=None
        )
    except Exception:
        logging.getLogger(__name__).error("Investigation failed for analysis %s", row["id"])
        row.update(
            investigation_status="unavailable",
            investigation_error={
                "code": "investigation_error",
                "message": "Investigation failed; transcript retained for retry.",
            },
            report=None,
        )
    store.save(row)
