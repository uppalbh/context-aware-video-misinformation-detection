import re

from app.errors import ProcessingError
from app.providers import interpret


def schema(ids):
    ref = {"type": "string", "enum": ids}
    quote = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"evidence_id": ref, "text": {"type": "string"}},
        "required": ["evidence_id", "text"],
    }
    finding = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "finding": {"type": "string"},
            "severity": {"type": "string", "enum": ["none", "low", "moderate", "high"]},
            "support_ids": {"type": "array", "items": ref},
            "contradiction_ids": {"type": "array", "items": ref},
            "quotes": {"type": "array", "items": quote},
        },
        "required": ["finding", "severity", "support_ids", "contradiction_ids", "quotes"],
    }
    properties = {
        "assessment": {
            "type": "string",
            "enum": ["context_changes_meaning", "context_consistent", "inconclusive"],
        },
        "clip_impression": {"type": "string"},
        "full_context": {"type": "string"},
        "findings": {"type": "array", "items": finding},
        "uncertainty": {"type": "array", "items": {"type": "string"}},
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def validate_interpretation(value, evidence):
    allowed = {e["id"]: e["text"] for e in evidence}
    try:
        if set(value) != {
            "assessment",
            "clip_impression",
            "full_context",
            "findings",
            "uncertainty",
        }:
            raise ValueError()
        if value["assessment"] not in {
            "context_changes_meaning",
            "context_consistent",
            "inconclusive",
        }:
            raise ValueError()
        for key in ("clip_impression", "full_context"):
            if not isinstance(value[key], str) or not 1 <= len(value[key]) <= 3000:
                raise ValueError()
        if not isinstance(value["findings"], list) or len(value["findings"]) > 10:
            raise ValueError()
        if value["assessment"] != "inconclusive" and not value["findings"]:
            raise ValueError()
        for finding in value["findings"]:
            if set(finding) != {
                "finding",
                "severity",
                "support_ids",
                "contradiction_ids",
                "quotes",
            }:
                raise ValueError()
            if not isinstance(finding["finding"], str) or not 1 <= len(finding["finding"]) <= 3000:
                raise ValueError()
            if finding["severity"] not in {"none", "low", "moderate", "high"}:
                raise ValueError()
            for name in ("support_ids", "contradiction_ids"):
                refs = finding[name]
                if (
                    not isinstance(refs, list)
                    or len(refs) > 20
                    or any(r not in allowed for r in refs)
                ):
                    raise ValueError()
            if not any(r.startswith("source:") for r in finding["support_ids"]):
                raise ValueError()
            if not any(r.startswith("clip:") for r in finding["support_ids"]):
                raise ValueError()
            if set(finding["support_ids"]) & set(finding["contradiction_ids"]):
                raise ValueError()
            if value["assessment"] == "context_consistent" and finding["severity"] != "none":
                raise ValueError()
            if not isinstance(finding["quotes"], list) or not 1 <= len(finding["quotes"]) <= 20:
                raise ValueError()
            for quote in finding["quotes"]:
                if set(quote) != {"evidence_id", "text"} or quote["evidence_id"] not in allowed:
                    raise ValueError()
                if (
                    quote["evidence_id"]
                    not in finding["support_ids"] + finding["contradiction_ids"]
                    or not isinstance(quote["text"], str)
                    or not quote["text"]
                    or quote["text"] not in allowed[quote["evidence_id"]]
                ):
                    raise ValueError()
        uncertainty = value["uncertainty"]
        if (
            not isinstance(uncertainty, list)
            or not 1 <= len(uncertainty) <= 10
            or any(not isinstance(t, str) or not 1 <= len(t) <= 1000 for t in uncertainty)
        ):
            raise ValueError()
        free = " ".join(
            [value["clip_impression"], value["full_context"]]
            + [f["finding"] for f in value["findings"]]
        )
        if re.search(
            r"uploader.{0,30}(intend|wanted|motive|deliberate)|definitively original", free, re.I
        ):
            raise ValueError()
        # Quoted text in narrative fields is subject to the same literal grounding rule.
        for quoted in re.findall(r'"([^"\n]+)"|“([^”\n]+)”', free):
            literal = quoted[0] or quoted[1]
            if not any(literal in text for text in allowed.values()):
                raise ValueError()
        return value
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ProcessingError(
            "invalid_evidence_refs",
            "Model findings failed evidence/quotation validation; no assessment accepted.",
        )


def analyze(alignment, source, cfg):
    if source["kind"] == "synthetic":
        return {
            "status": "demo_only",
            "assessment": "inconclusive",
            "findings": [],
            "uncertainty": [
                "SYNTHETIC DEMO: deterministic retrieval/alignment only; no model call or real-world conclusion."
            ],
        }
    if not cfg.openai_key:
        return {
            "status": "setup_required",
            "assessment": "inconclusive",
            "findings": [],
            "uncertainty": [
                "Context model unavailable; omissions are text observations, not semantic conclusions."
            ],
        }
    package = {
        "source": {k: source[k] for k in ("title", "url", "provenance", "kind")},
        "evidence": alignment["evidence"],
        "omitted": alignment["omitted"],
        "alignment_flags": {
            k: alignment[k] for k in ("reordered", "disjoint", "gap_segments_not_shown")
        },
    }
    if len(str(package)) > 60000:
        return {
            "status": "unavailable",
            "assessment": "inconclusive",
            "findings": [],
            "uncertainty": [
                "Evidence exceeds context model input limit; no partial interpretation performed."
            ],
        }
    try:
        result = interpret(package, cfg, schema([e["id"] for e in alignment["evidence"]]))
        return {"status": "completed", **validate_interpretation(result, alignment["evidence"])}
    except ProcessingError as exc:
        return {
            "status": "unavailable",
            "assessment": "inconclusive",
            "findings": [],
            "error": exc.public(),
            "uncertainty": [exc.message],
        }
