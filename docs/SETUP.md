# Run the two-stage app

The repository initially contained only the product README. This implementation adds a Python 3.11+ FastAPI application, a same-origin browser UI, and server-side persistence without introducing account registration.

## Local development

Install Python 3.11+ and FFmpeg **including ffprobe**, with both executables on PATH. Then from this checkout:

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
# Windows PowerShell: Copy-Item .env.example .env
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --timeout-graceful-shutdown 480
```

Open http://127.0.0.1:8000. The example selects SQLite **only for development** and disables Secure cookies for localhost HTTP. Set `OPENAI_API_KEY` in the server's environment or ignored `.env` file; do not put secrets in the frontend or commit them. A missing key produces an explicit failed job, never sample speech. OpenAI transcription incurs provider charges. The UI discloses that audio is sent to OpenAI.

## Supabase

1. Apply `supabase/migrations/001_analyses.sql`, then `002_url_ingestion.sql` in an existing Supabase project. Existing installations need migration 002 for URL records/statuses.
2. Configure `STORE=supabase`, `SUPABASE_URL=https://your-project.supabase.co`, and `SUPABASE_SERVICE_ROLE_KEY` server-side.
3. Set `COOKIE_SECURE=true` behind HTTPS. Keep `DATA_DIR` on a persistent private volume, outside any web-served directory. On Windows, restrict the directory ACL to the server account; Unix creates directories with mode 0700.
4. Run exactly **one process/worker on one host** with that volume and Supabase table. The local file lock rejects duplicate processes using the same volume. Multi-host deployment requires a distributed claim/lease queue and object storage and is deferred. This app must be the only producer for this table.

Supabase stores queue state, SHA-256, metadata, transcript and safe errors. Raw video/audio remain on the server's private disk and are never publicly served. RLS is enabled; no browser/anonymous policies exist. Only the server service role accesses the table. A random HttpOnly SameSite=Strict cookie scopes API reads/history/retries/deletion; only its SHA-256 is persisted. Clearing the cookie loses session access. This is an anonymous session architecture, not verified user authentication.

Use a current patched FFmpeg build and run as an unprivileged service account/container with memory/CPU/disk limits. For internet-facing operation, configure ingress request size/time limits and rate limits; anonymous session quotas are not an anti-abuse identity system. Reverse proxies must preserve the external Host/scheme for same-origin checks. No resources have been provisioned or deployed by this implementation.

## Configuration defaults

| Variable | Default | Purpose |
| --- | --- | --- |
| `STORE` | `supabase` | Production default fails fast if credentials are absent; `.env.example` explicitly selects SQLite |
| `DATA_DIR` | `./data` | Private persistent volume and queue lock |
| `MAX_UPLOAD_MB` | 100 | Upload bytes, enforced while streaming (1–1024 MB) |
| `MAX_DURATION_SECONDS` | 600 | Actual probed duration (maximum supported setting 3600) |
| `MAX_PENDING_JOBS` | 20 | Global queued/active jobs |
| `MEDIA_RETENTION_HOURS` | 24 | Records and retained inputs expire from creation time |
| `MEDIA_TIMEOUT_SECONDS` | 60 | Timeout per ffprobe/FFmpeg subprocess |
| `FFMPEG_BIN`, `FFPROBE_BIN` | `ffmpeg`, `ffprobe` | Explicit executable paths supported |
| `COOKIE_SECURE` | true | Set false only for local HTTP |
| `VIDEO_URL_HOSTS` | `media.w3.org` | Comma-separated exact HTTPS source hosts; empty disables URL ingestion |

Uploads also time out after 120 seconds; each session may hold ten records, with a global capacity of 500 records until expiry/deletion. One job runs at a time. Video limits are 7680×4320 and 240 fps. Audio becomes mono 16 kHz PCM WAV; extracted output must be below 23.9 MB. Raising the duration limit may trigger this audio limit instead of silently transcribing only part of a clip.

Extracted audio is deleted after each attempt and leftover audio is removed during restart recovery. Inputs are removed after successful transcription or terminal media-validation failure. Other failed inputs remain for explicit retry until retention expires. Retention cleanup runs between jobs, so expiry may be delayed by the bounded in-flight job. Deleting a terminal record removes its remaining media immediately. On startup, generated orphan analysis directories are removed (unrelated directory names are preserved) and interrupted jobs become failed with an explicit retry option; interrupted transcription is never automatically billed again. Queued jobs survive restart. The application must run for scheduled cleanup to occur; configure host-level volume retention if it will remain stopped.

## API contract

Establish the cookie with `GET /`. `POST /api/analyses` takes **raw video bytes**, not multipart: Content-Type `video/mp4` or `video/quicktime`. The MIME is an admission hint; ffprobe checks the actual container, tracks, duration and dimensions asynchronously. Upload receipt returns HTTP 202 `{analysis_id, status: "queued"}` immediately after streaming/storage. Invalid media later becomes a failed record, with a stable error code and readable message. Submit supported direct URLs as JSON to `POST /api/analyses/url`; see [URL contract](URL_INGESTION.md).

- `GET /api/config`: public limits.
- `GET /api/analyses`: this cookie's retained records.
- `GET /api/analyses/{id}`: status, metadata, hash, transcript or error; another session receives 404.
- `POST /api/analyses/{id}/retry`: explicit retry of a failed retained clip, three attempts maximum.
- `DELETE /api/analyses/{id}`: remove a terminal record and media. Active jobs return 409.

States: `queued → [downloading for URLs] → extracting → transcribing → transcribed`, or `failed`. `transcribed` means **only transcription succeeded**. `investigation_status` remains `not_started`; source retrieval, alignment, fact checking, scores and reports are not implemented. Hash and metadata do not establish truth. Segment schema: `{id, start, end, text}`, with finite seconds relative to the uploaded clip. Speech recognition may make mistakes; review the transcript before downstream use.

The provider is OpenAI `whisper-1` with `verbose_json` and both `timestamp_granularities[]=word` and `timestamp_granularities[]=segment`, per [official speech-to-text documentation](https://developers.openai.com/api/docs/guides/speech-to-text). The full extracted audio is sent in one request; the video is **not** sliced into one-second chunks. Existing `OPENAI_API_KEY` configuration is sufficient. Provider requests have a 180-second timeout and do not automatically retry. Missing keys, rejected credentials, quota/rate limits, network failures, empty speech and invalid timestamps have distinguishable errors. Provider bodies and FFmpeg stderr are never exposed as user messages.

### Second-by-second transcript contract

The stored `transcript` JSON and `GET /api/analyses/{id}` preserve `text`, `segments`, `language`, `provider`, and `model`. New fields:

- `words`: ordered `{id, word, start, end}` records. IDs are zero-based indices. Float start/end times retain the provider's numeric values without rounding, stretching, or distributing segment text.
- `transcript_by_second`: JSON object with string keys `"0"` through `str(ceil(video_duration)-1)`, each containing an ordered array of word strings. Second `"65"` is elapsed **01:05** on the uploaded clip. A fractional final second still has a bucket. Integer-duration clips do not get an extra endpoint bucket.
- `word_timing_status`: `available` or `unavailable`.
- `timeline`: `uploaded_clip`. Original-source timestamps, when implemented later, must use a separate timeline; these times are never source offsets.
- `duration_seconds`: probed uploaded-video duration; `bucketing`: `word_start_floor_seconds`.

**Bucketing convention:** a word is placed exactly once where its START falls in `[second, second+1)`. For example, a word starting at 0.95 and ending at 1.4 is listed only under `"0"`, while its precise timing remains in `words`. All empty buckets are explicitly `[]`; they mean **no word starts in that second**, not established silence. Order is preserved within and across buckets, including words with identical starts and overlapping boundaries.

Word starts must be finite, nonnegative, nondecreasing, and strictly before video duration. Ends must be finite and at least the corresponding start. Up to 0.5 seconds of provider end overshoot beyond duration is accepted and retained exactly; starts at or beyond the endpoint are rejected rather than misbucketed or dropped. Overlapping word ends are allowed. Malformed word records fail with `invalid_word_timestamps`. Indexing is bounded by the existing maximum supported video duration (3600 seconds) and a 100,000-word response cap. API timestamp values are **model estimates**: retaining fractional seconds does not establish millisecond accuracy, perfect speech recognition, or exact alignment.

Audio extraction uses the uploaded container's elapsed clock, preserves timestamp gaps, and pads leading silence for a late audio track. Metadata records `audio_start_seconds` and `timeline_origin: container_start`; do not add that start offset again to returned transcript timings.

If word data is missing, null, or empty but valid segments exist, `word_timing_status` is `unavailable` and `words` / `transcript_by_second` are null. Segments stay readable; no per-second dictionary is fabricated. No timestamped speech produces the existing `no_speech` failure. Older stored segment-only records remain readable without migration/reprocessing. The UI labels their unavailable word timing. Transcript JSON is already stored in Supabase JSONB, so no new database migration is needed.

The UI displays at most 60 second buckets and 100 context segments at a time, supports elapsed-second navigation, and downloads the full private transcript as JSON (including words and exact timings). The API and JSON download contain the full index within configured duration limits.

Supported public URL ingestion and its safety boundaries are documented in [URL_INGESTION.md](URL_INGESTION.md).

## Verification

```sh
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
node --check app/static/app.js
node --test tests/ui.test.cjs
```

Tests generate a one-second video/audio fixture with FFmpeg and validate probing/extraction, corrupt media, no audio, duration limits, upload bounds, private session access, timestamp normalization, provider failure contracts, retry, cleanup and restart recovery. Word tests verify the actual encoded multipart request for both granularities, exact boundaries, fractional durations, full/empty buckets, overlapping words, numeric validation, order, lossless indexing, unavailable/legacy handling, and persisted worker results. Node's built-in tests verify bounded rendering and elapsed-second navigation using deterministic DOM/transcript fixtures. Tests stub speech/persistence responses where specified; they never call paid transcription. Real media tests skip explicitly if FFmpeg is missing; CI installs it. TestClient uses SQLite; Supabase transport contracts are tested separately and require no credentials. A real speech smoke test and remote migration verification require configured provider/Supabase access.
