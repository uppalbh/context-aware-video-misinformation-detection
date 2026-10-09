# Teammate integration contract

This delivery implements README stages **1–2**: local MP4/MOV ingestion, actual-media validation, audio extraction, queued background processing, persistence and timestamped speech transcription. URL ingestion is deferred. Source retrieval/alignment/context assessment/scoring are downstream modules to implement separately. `transcribed` is never an investigation-completed or fake/real result.

## Module boundaries

| Module | Reusable boundary |
| --- | --- |
| `app/media.py` | `inspect(path, Settings) -> metadata`; `extract(path, output, Settings)` probes/decodes private local media, with bounded FFmpeg subprocesses. Neither calls a speech provider nor writes database rows. |
| `app/transcription.py` | `transcribe(audio_path, api_key, duration) -> transcript` calls OpenAI once, then delegates validation/indexing. No UI or database dependency. |
| `app/transcript.py` | `normalize(provider_payload, duration) -> transcript`; `word_index(raw_words, duration) -> (words, per_second)` are pure deterministic normalization/index functions. No network, storage, media processes or frontend dependency. |
| `app/db.py` | `Store.save/get/all/delete` supports server-only Supabase or explicit local SQLite. Transcript fields are JSON/JSONB; no migration is needed to extend the transcript object. |
| `app/worker.py` | `process(row, store, Settings)` owns stage transitions/persistence and extracted-audio cleanup. `recover` handles restart; `tick` expires records and runs one queued job. |
| `app/main.py` | `create_app(Settings)` provides browser/API access, streaming ingestion, session authorization and worker lifecycle. |

The provider module re-exports `normalize` for existing imports. Prefer importing pure processing from `app.transcript` in new integrations. Processing errors expose stable `code` and safe `message` via `ProcessingError.public()`; do not publish FFmpeg stderr, provider response bodies, keys or private media paths.

## HTTP and records

`GET /` establishes a random HttpOnly private-session cookie. Upload **raw bytes** to `POST /api/analyses` with `Content-Type: video/mp4` or `video/quicktime` (not multipart). Response HTTP 202:

```json
{"analysis_id": "<32 hex characters>", "status": "queued"}
```

Poll `GET /api/analyses/{analysis_id}` with the same cookie. Top-level fields include `id`, `status`, `created_at`, `updated_at`, `sha256`, `size_bytes`, `metadata`, `transcript`, `error`, `attempts` and `investigation_status`. The stored owner hash is never returned. Other sessions receive 404.

`queued → extracting → transcribing → transcribed` or `failed`. During queued/active/failed states the transcript may be null. On success `investigation_status` remains **`not_started`**. Hash and media metadata identify/describe a clip, not its truthfulness. Failed jobs expose `{code,message}`, can explicitly retry retained inputs up to three total attempts, and never fabricate a report. Read history, retry and deletion endpoints are described in [SETUP.md](SETUP.md).

## Transcript shape

```text
text: string
segments: [{id: integer, start: seconds, end: seconds, text: string}]
words: [{id: integer, word: string, start: seconds, end: seconds}] | null
transcript_by_second: {"0": [word, ...], "1": [], ..., "65": [word, ...]} | null
word_timing_status: "available" | "unavailable"
timeline: "uploaded_clip"
duration_seconds: number
bucketing: "word_start_floor_seconds"
language: string | null
provider: "openai"
model: "whisper-1"
```

Words are assigned **once by start** in `[s,s+1)`; cross-second words retain their end separately. Every elapsed second through `ceil(duration)-1` exists, including the fractional last second and empty lists. JSON keys are strings. A bucket with no starting word does not establish silence. Exact provider floating-point values are retained in word records; recognition/timing remain model estimates, not ground truth or millisecond accuracy. Starts are nonnegative/nondecreasing and strictly below video duration; overlaps/equal starts are valid; ends tolerate at most 0.5 seconds beyond duration. Nothing is rounded into a different bucket.

Missing word timings keep the segment transcript but set timing status unavailable and both word/index fields null. Malformed timings fail explicitly. Existing stored segment-only JSON remains readable: callers must check presence rather than assuming new fields. No word times are inferred by dividing segment text.

Extracted audio preserves container-relative elapsed timestamps, including leading silence for a late audio stream. Metadata exposes `audio_start_seconds` and `timeline_origin: container_start`. Words use this normalized uploaded-clip timeline. **Keep original-source timestamps and alignment offsets in a separate structure**; never rewrite clip-relative word starts to source times.

## Consuming from a later module

```python
from app.transcript import normalize


# Provider integration already calls normalize; normally consume row["transcript"].
def source_search_input(row: dict) -> dict:
    if row["status"] != "transcribed" or not row.get("transcript"):
        raise ValueError("A transcript must exist before source search")
    transcript = row["transcript"]
    return {
        "analysis_id": row["id"],
        "query_text": transcript["text"],
        "clip_segments": transcript["segments"],
        "clip_words": transcript.get("words"),  # legacy or unavailable -> None
        "clip_second_65": (transcript.get("transcript_by_second") or {}).get("65"),
    }
```

The example is an input adapter only; no source is searched or invented. A downstream worker should consume successfully persisted `transcribed` records, retain separate provenance/evidence, and own its own statuses/schema. Current MVP does not provide a multi-consumer claim API. Do not run a second ingestion worker or overwrite `transcribed` with an unsupported status in this table without extending the schema and orchestration.

## Operational assumptions and verified limits

One worker on one host owns a private persistent volume and durable store. Local file locking prevents duplicate processes using that volume; distributed leasing and cloud media storage are deferred. Extraction/transcription run off the HTTP event loop. Configurable limits and cleanup policy are documented in SETUP.md. Interrupted active jobs fail with explicit retry; queued records survive restart; generated orphan folders and leftover extracted audio are removed without deleting unrelated folders. Successful raw inputs are removed; failed retryable inputs expire with records. Nothing serves media publicly.

SQLite is an explicit development option. Supabase service-role credentials and OpenAI keys stay server-side in ignored configuration. Live provider transcription and remote Supabase migration/access verification require configured credentials; deterministic provider/transport tests do not establish those live capabilities.
