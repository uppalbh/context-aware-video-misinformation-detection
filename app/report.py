from app.scoring import contextual_risk


def assemble(retrieval, interpretation=None):
    selected = retrieval["selected"]
    alignment = selected["alignment"] if selected else None
    source = selected["source"] if selected else None
    context = interpretation or {
        "status": "not_run",
        "assessment": "inconclusive",
        "findings": [],
        "uncertainty": [retrieval["reason"]],
    }
    uncertainty = [
        "Text similarity is not independent proof of original-video identity or factual truth.",
        "Source provenance/permission are curator-supplied claims; ASR and segment timing may be inaccurate.",
    ]
    uncertainty += context["uncertainty"]
    if source and source["kind"] == "synthetic":
        uncertainty.append(
            "SYNTHETIC EXAMPLE: this source is fabricated test/demo material, not verified real-world evidence."
        )
    if alignment and (alignment["reordered"] or alignment["disjoint"]):
        uncertainty.append(
            "Clip passages may be reordered or disjoint; no single source-time offset applies."
        )
    if alignment and alignment["gap_segments_not_shown"]:
        uncertainty.append(
            "Some intervening source segments are outside the bounded evidence display."
        )
    assessment = context["assessment"]
    risk = contextual_risk(context)
    return {
        "schema_version": 1,
        "assessment": assessment,
        "source_status": retrieval["status"],
        "source_match_confidence": selected["rank_score"] if selected else None,
        "source_match_quality_label": "heuristic rank score, not calibrated probability",
        "context_risk_score": risk,
        "context_risk_explanation": "Ordinal heuristic from validated model contextual-divergence severity (none=0, low=25, moderate=50, high=75); null when semantic interpretation is unavailable/inconclusive.",
        "evidence_coverage": alignment["coverage"] if alignment else 0,
        "coverage_definition": "fraction of clip transcript tokens matched; not whole-event evidence coverage",
        "source": source,
        "alignment": alignment,
        "context": context,
        "candidates": retrieval["candidates"],
        "semantic_status": retrieval["semantic_status"],
        "distinctive_phrases": retrieval["distinctive_phrases"],
        "ranking_limits": retrieval["ranking_limits"],
        "corpus_revisions": retrieval["corpus_revisions"],
        "uncertainty": uncertainty,
        "synthetic": bool(source and source["kind"] == "synthetic"),
    }
