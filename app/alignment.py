"""Segment-clock alignment; token positions are textual spans, never inferred word timings."""

import re
import time
from collections import Counter
from difflib import SequenceMatcher

from app.corpus import tokens
from app.errors import ProcessingError


def align(transcript, source):
    deadline = time.monotonic() + 5
    clip = transcript.get("segments", [])
    if not clip or len(clip) > 200 or sum(len(tokens(s["text"])) for s in clip) > 4096:
        raise ProcessingError(
            "alignment_limit", "Alignment supports up to 200 segments / 4096 clip tokens."
        )
    source_segments = source["segments"]
    windows = []
    for i in range(len(source_segments)):
        segment_indices = list(range(i, min(i + 3, len(source_segments))))
        positions = [
            (index, j, token)
            for index in segment_indices
            for j, token in enumerate(tokens(source_segments[index]["text"]))
        ]
        windows.append((positions, Counter(p[2] for p in positions)))
    passages, matched, total, ambiguous = [], set(), 0, False
    for ci, segment in enumerate(clip):
        if time.monotonic() > deadline:
            raise ProcessingError(
                "alignment_limit",
                "Alignment exceeded its per-source computation budget; no partial match accepted.",
            )
        query = tokens(segment["text"])
        total += len(query)
        if not query or len(query) > 256:
            raise ProcessingError(
                "alignment_limit", "An alignment segment exceeds 256 tokens or is empty."
            )
        query_counts = Counter(query)
        shortlist = sorted(
            windows, key=lambda w: sum((w[1] & query_counts).values()), reverse=True
        )[:20]
        choices = {}
        for positions, _ in shortlist:
            match = SequenceMatcher(None, query, [p[2] for p in positions], autojunk=False)
            pairs = [
                (block.a + offset, positions[block.b + offset])
                for block in match.get_matching_blocks()
                for offset in range(block.size)
            ]
            if not pairs:
                continue
            signature = tuple((p[1][0], p[1][1]) for p in pairs)
            choices[signature] = pairs
        best = sorted(choices.values(), key=lambda p: len(p), reverse=True)
        if not best or len(best[0]) / len(query) < 0.5 or len(best[0]) < min(3, len(query)):
            passages.append(
                {
                    "clip_id": f"clip:c{ci}",
                    "clip_start": segment["start"],
                    "clip_end": segment["end"],
                    "match": "unmatched",
                    "coverage": 0,
                }
            )
            continue
        pairs = best[0]
        chosen_positions = {(p[1][0], p[1][1]) for p in pairs}
        for alternate in best[1:]:
            alternate_positions = {(p[1][0], p[1][1]) for p in alternate}
            if (
                len(alternate) >= len(pairs) * 0.95
                and len(chosen_positions & alternate_positions) < len(pairs) * 0.5
            ):
                ambiguous = True
        matched.update((p[1][0], p[1][1]) for p in pairs)
        indices = sorted({p[1][0] for p in pairs})
        first, last = pairs[0][1], pairs[-1][1]
        exact = len(pairs) == len(query) and all(
            b == a + 1 for a, b in zip([p[0] for p in pairs], [p[0] for p in pairs][1:])
        )
        span_tokens = sum(
            len(tokens(source_segments[i]["text"])) for i in range(first[0], last[0] + 1)
        )
        span_tokens -= first[1] + len(tokens(source_segments[last[0]]["text"])) - last[1] - 1
        exact = exact and span_tokens == len(query)
        passages.append(
            {
                "clip_id": f"clip:c{ci}",
                "clip_start": segment["start"],
                "clip_end": segment["end"],
                "source_ids": [f"source:{source_segments[i]['id']}" for i in indices],
                "source_start": source_segments[first[0]]["start"],
                "source_end": source_segments[last[0]]["end"],
                "match": "exact" if exact else "approximate",
                "coverage": len(pairs) / len(query),
                "matched_tokens": len(pairs),
            }
        )
    hit = sorted({i for i, _ in matched})
    context_indices = sorted(
        {j for i in hit for j in range(max(0, i - 2), min(len(source_segments), i + 3))}
    )
    if len(context_indices) > 80:
        raise ProcessingError("alignment_limit", "Matched context exceeds 80 source segments.")
    evidence = [
        {
            "id": f"clip:c{i}",
            "timeline": "uploaded_clip",
            **{k: s[k] for k in ("start", "end", "text")},
        }
        for i, s in enumerate(clip)
    ]
    evidence += [
        {
            "id": f"source:{source_segments[i]['id']}",
            "timeline": "original_source",
            **{k: source_segments[i][k] for k in ("start", "end", "text")},
        }
        for i in context_indices
    ]
    omitted = []
    for i in context_indices:
        segment = source_segments[i]
        spans = list(re.finditer(r"\w+(?:['’]\w+)?", segment["text"]))
        groups, active = [], []
        for j, span in enumerate(spans):
            if (i, j) not in matched:
                active.append(span)
            elif active:
                groups.append(active)
                active = []
        if active:
            groups.append(active)
        for group in groups:
            start, end = group[0].start(), group[-1].end()
            text = segment["text"][start:end]
            omitted.append(
                {
                    "evidence_id": f"source:{segment['id']}",
                    "text": text,
                    "char_start": start,
                    "char_end": end,
                    "position": "within_matched_segment" if i in hit else "surrounding_context",
                    "language_cues": sorted(
                        set(tokens(text))
                        & {"not", "no", "never", "unless", "if", "but", "except", "only", "however"}
                    ),
                }
            )
    source_starts = [p["source_start"] for p in passages if "source_start" in p]
    gaps = [i for i in range(hit[0], hit[-1] + 1) if i not in hit] if hit else []
    # Include disjoint gap evidence only when bounded; the preceding/following windows above may omit large gaps.
    return {
        "passages": passages,
        "evidence": evidence,
        "omitted": omitted,
        "coverage": sum(p.get("matched_tokens", 0) for p in passages) / max(total, 1),
        "matched_tokens": sum(p.get("matched_tokens", 0) for p in passages),
        "ambiguous_passage": ambiguous,
        "reordered": source_starts != sorted(source_starts),
        "disjoint": bool(gaps),
        "gap_segments_not_shown": [
            f"source:{source_segments[i]['id']}" for i in gaps if i not in context_indices
        ],
        "timestamp_precision": "source/clip segment boundaries; token positions are not word times",
    }
