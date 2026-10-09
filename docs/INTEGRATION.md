# Teammate integration contracts

Core README workflow is implemented in modular Python services. Networking is confined to media/provider/store adapters; curated URLs are references, never automatic downloads. One durable worker owns state changes. See [SETUP.md](SETUP.md) for HTTP/session/media contracts and [INVESTIGATION.md](INVESTIGATION.md) for corpus/ranking limits.

| Module | Callable boundary |
| --- | --- |
| `app/media.py` | `inspect(path,cfg) -> metadata`; `extract(path,out,cfg)` bounded FFmpeg/ffprobe, uploaded container clock |
| `app/url_media.py` | `validate_url(url,hosts)`; `download(url,out,cfg)` direct allowlisted public-address pinned HTTPS media |
| `app/transcription.py` | `transcribe(audio,key,duration)` single Whisper call, pure normalization delegation |
| `app/transcript.py` | `normalize(payload,duration)`; `word_index(words,duration)` pure validation/every-second indexing |
| `app/corpus.py` | `validate_source(JSON)` normalized source/revision; `load_corpus(Path)` sources; CLI operator import |
| `app/source_search.py` | `search(transcript,sources,cfg)` status/candidates/selected/revisions/phrases/semantic_status; multiple-source comparison/abstention |
| `app/alignment.py` | `align(transcript,source)` passages/evidence/omitted/coverage/flags; segment-clock/token-span matching |
| `app/providers.py` | `semantic_scores(query,documents,cfg)` embeddings; `interpret(package,cfg,schema)` structured Responses; `DiscoveryProvider` future interface only |
| `app/context_analysis.py` | `analyze(alignment,source,cfg)` interpretation or abstention; `validate_interpretation(value,evidence)` IDs/quotes/schema |
| `app/scoring.py` | `contextual_risk(context)` null or ordinal severity heuristic, separate from ranking/coverage |
| `app/report.py` | `assemble(retrieval,interpretation=None)` schema_version:1 report; deterministic, no network/storage |
| `app/investigation.py` | `investigate(row,store,cfg,sources=None)` persists investigation; excludes synthetic corpus for real ingestion |
| `app/worker.py` | `process` ingestion/STT/cleanup; `tick` expires/dispatches oldest queued job; `recover` interruption; `is_active` shared guard |
| `app/db.py` | `Store.save/get/all/delete`; SQLite JSON or server-only Supabase JSONB |
| `app/main.py` | `create_app(cfg,run_worker=True)` session API, admission, lifecycle, browser UI |

Adapters use `ProcessingError(code,safe_message)`; never expose provider bodies, secrets, FFmpeg stderr or media paths. STT adapter re-exports normalization for backwards compatibility. Migration 003 adds report/investigation fields and completed; transcript JSON needs no shape migration.

## Transcript contract preserved

`text`, ordered `segments:[{id,start,end,text}]`, `words:[{id,word,start,end}]|null`, `transcript_by_second:{"0":[word],"1":[],...}|null`, `word_timing_status`, `timeline:"uploaded_clip"`, `duration_seconds`, `bucketing:"word_start_floor_seconds"`, `language`, `provider`, `model`. Words go once into floor(start) and retain provider floats. Every second through ceil(duration)-1 exists. Empty buckets mean no word starts, not confirmed silence. Missing word data yields unavailable/null fields, never invented times. Legacy segment-only records remain readable. ASR/times are estimates. Leading audio gaps are preserved; do not add metadata offsets again.

## Retrieval/alignment contracts

```python
from app.corpus import load_corpus
from app.source_search import search

result = search(row["transcript"], load_corpus(cfg.corpus_dir), cfg)
if result["selected"]:
    alignment = result["selected"]["alignment"]
    source = result["selected"]["source"]
```

Selected contains source/alignment/component scores; null for weak/ambiguous matches. Candidate summaries include ID/title/URL/kind/revision and lexical/exact/fuzzy/semantic/rank scores. Revision hashes snapshot transcript/provenance; reports preserve those revisions and source text, so corpus replacement does not rewrite history.

Evidence IDs are clip:c{index} and source:s{index}, with timeline/start/end/text. Passages map clip segment bounds to source segment bounds, source IDs, exact/approximate/unmatched status and coverage. No source word time is inferred. Omitted identifies literal char_start/char_end/text spans, position and language cues; spans can be ASR differences, and cues are not semantic judgments. Reordered/disjoint flags preclude a single offset; bounded context can omit distant gap segments, listed explicitly.

## Context/report contracts

Model fields: assessment (context_changes_meaning/context_consistent/inconclusive), clip_impression, full_context, findings, uncertainty. Each finding has finding/severity, support_ids including source and clip, disjoint contradiction_ids, and ≥1 literal cited quote {evidence_id,text}. Unknown IDs, fabricated quotes, extra fields, empty uncertainty, unsupported intent/originality claims and inconsistent severity fail validation. Structural/literal grounding cannot prove model reasoning is sound.

Report fields: schema_version, assessment, source_status, source_match_confidence/quality label, context_risk_score/explanation, evidence_coverage/definition, source, alignment, context, candidates, semantic_status, distinctive_phrases, ranking_limits, corpus_revisions, uncertainty, synthetic. Risk is null for unavailable/inconclusive interpretation. No fake/real or truth score exists. Supporting/contradictory IDs and literal quotes remain inspectable in UI/JSON.

## State/ownership

Transcribed means STT exists; investigation owns its queue/terminal states. Completed requires valid model output; assessment may still be inconclusive. Missing corpus yields source_not_found; ambiguity inconclusive; absent model setup setup_required with alignment; provider/corpus errors unavailable. Investigation retry uses transcript, never rebills Whisper. Interrupted provider interpretation is not auto-retried. Legacy records explicitly queue through session API.

One host/worker/private volume/file lock; no distributed claims or second producer. API capacities count both queues. Retention removes analyses, not operator corpus. Credentials stay server-only; live provider/migration verification remains outstanding. No internet crawler/discovery service is implemented. Future discovery must obtain permitted transcripts and validate/import before retrieval; a returned URL alone is not evidence.
