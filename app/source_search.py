from collections import Counter

from app.alignment import align
from app.corpus import tokens
from app.errors import ProcessingError
from app.providers import semantic_scores

STOP = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "of",
    "in",
    "is",
    "it",
    "i",
    "we",
    "you",
    "that",
    "for",
    "on",
}


def search(transcript, sources, cfg):
    query = tokens(transcript.get("text", ""))
    if not query:
        raise ProcessingError(
            "empty_transcript", "A nonempty transcript is required for source search."
        )
    if len(query) > 4096:
        raise ProcessingError("alignment_limit", "Clip transcript exceeds retrieval token limit.")
    phrases = list(
        dict.fromkeys(" ".join(query[i : i + 5]) for i in range(max(0, len(query) - 4)))
    )[:200]
    query_counts = Counter(t for t in query if t not in STOP)
    ranked = []
    for source in sources:
        text = " ".join(s["text"] for s in source["segments"])
        counts = Counter(t for t in tokens(text) if t not in STOP)
        lexical = sum((query_counts & counts).values()) / max(sum(query_counts.values()), 1)
        ranked.append((lexical, source))
    ranked.sort(key=lambda r: (-r[0], r[1]["id"]))
    candidates = []
    for lexical, source in ranked[:10]:
        alignment = align(transcript, source)
        normalized = " " + " ".join(tokens(" ".join(s["text"] for s in source["segments"]))) + " "
        exact = sum(" " + phrase + " " in normalized for phrase in phrases) / max(len(phrases), 1)
        score = 0.7 * alignment["coverage"] + 0.2 * exact + 0.1 * lexical
        candidates.append(
            {
                "source": source,
                "alignment": alignment,
                "lexical_overlap": lexical,
                "exact_phrase_fraction": exact,
                "fuzzy_token_coverage": alignment["coverage"],
                "semantic_similarity": None,
                "rank_score": score,
            }
        )
    semantic_status = "unavailable: lexical retrieval only"
    if candidates:
        documents = [
            " ".join(
                e["text"] for e in c["alignment"]["evidence"] if e["timeline"] == "original_source"
            )
            or " ".join(s["text"] for s in c["source"]["segments"][:3])
            for c in candidates
        ]
        try:
            scores, semantic_status = semantic_scores(transcript["text"], documents, cfg)
            if scores is not None:
                for candidate, score in zip(candidates, scores):
                    candidate["semantic_similarity"] = score
                    candidate["rank_score"] = 0.85 * candidate["rank_score"] + 0.15 * max(0, score)
        except ProcessingError as exc:
            semantic_status = exc.code + ": lexical fallback"
    candidates.sort(key=lambda c: (-c["rank_score"], c["source"]["id"]))
    chosen, status, reason = (
        None,
        "source_not_found",
        "No sufficiently distinctive matching passage in the curated corpus.",
    )
    if candidates:
        best = candidates[0]
        adequate = (
            best["fuzzy_token_coverage"] >= 0.75
            and best["alignment"]["matched_tokens"] >= 8
            and len(set(query) - STOP) >= 6
        )
        if adequate:
            ambiguous = best["alignment"]["ambiguous_passage"] or (
                len(candidates) > 1
                and candidates[1]["fuzzy_token_coverage"] >= 0.7
                and best["rank_score"] - candidates[1]["rank_score"] < 0.08
            )
            if ambiguous:
                status, reason = (
                    "inconclusive",
                    "Repeated passages or overlapping source candidates prevent a unique match.",
                )
            else:
                chosen, status, reason = (
                    best,
                    "matched",
                    "Distinctive textual passage matched a curated source; originality is not independently verified.",
                )
    summaries = [
        {
            "id": c["source"]["id"],
            "title": c["source"]["title"],
            "url": c["source"]["url"],
            "kind": c["source"]["kind"],
            "revision": c["source"]["revision"],
            **{
                k: c[k]
                for k in (
                    "lexical_overlap",
                    "exact_phrase_fraction",
                    "fuzzy_token_coverage",
                    "semantic_similarity",
                    "rank_score",
                )
            },
        }
        for c in candidates
    ]
    return {
        "status": status,
        "reason": reason,
        "candidates": summaries,
        "selected": chosen,
        "semantic_status": semantic_status,
        "distinctive_phrases": phrases[:10],
        "corpus_revisions": {s["id"]: s["revision"] for s in sources},
        "ranking_limits": "Top 10 lexical source candidates; optional semantic passage reranking. Scores are heuristics, not probabilities.",
    }
