# Ingestion and transcription coverage

| Requested goal | Delivered boundary | Verification |
| --- | --- | --- |
| Upload MP4/MOV | Raw-byte upload API + file UI; queued private analysis ID | Actual MP4/MOV probing, upload/streamed byte limits, corrupt/no-audio cases |
| Supported public URL | Direct HTTPS media on exact configured hosts; queued URL UI/API | Live public W3C video download + extraction; redirect, pinning, rebinding, IPv4/IPv6 and limit tests |
| Validate size/format/duration/URL safety | Streaming caps, ffprobe/FFmpeg actual-media validation, pinned public-address TLS downloader | Known-good media and malformed/no-audio/duration-limit fixtures; URL policy/transport failures |
| Extract audio/store analysis | Private generated paths; metadata/hash; off-request worker; SQLite dev/Supabase production adapter and migrations | Real mono WAV extraction, delayed-audio timing, store transport contracts, retries/retention/restart cleanup |
| Pretrained automatic speech recognition | Server-only OpenAI whisper-1 request for full audio | Provider request encoding and error contracts; **live STT pending OPENAI_API_KEY** |
| Preserve segment timestamps | Stored text + ordered segments | Timestamp normalization and persisted worker output |
| Second-by-second transcript | Exact provider word timings + every elapsed-second bucket by word start | Boundaries/overlaps/fractional tail/empty buckets/order/resource bounds; bounded UI navigation |
| Store transcript/handle failures | JSON/JSONB transcript, safe errors, explicit limited retry; no fake completed report | API persistence/private access and failed/interrupted/provider cases |
| Teammate integration | Separate media, URL, provider, pure normalization, storage and orchestration modules | [INTEGRATION.md](INTEGRATION.md), [URL_INGESTION.md](URL_INGESTION.md), documented callable/API contracts |

Successful processing ends at **transcribed**, with `investigation_status:not_started`. Source retrieval/alignment, context/fact assessment, scores, model training and audio forensics are outside this requested delivery. URL support does not extend to arbitrary video platforms.

Local mandatory pipeline and supported URL path are implemented. Live speech recognition needs a server OpenAI key; remote Supabase initialization/persistence needs configured Supabase credentials plus migrations 001/002. Neither was configured at the audit, and no remote deployment or billing resources were provisioned. The local `.env` is ignored/untracked; no secret is committed. Test transcript fixtures remain tests only, never production fallback output.

Final local audit: **95 Python tests**, **2 frontend tests**, Ruff lint/format, JavaScript syntax and Git diff checks passed. The URL download/extraction smoke check ran against actual public media; provider/Supabase tests use deterministic contract fixtures. This evidence verifies the delivered paths within their stated limits, not arbitrary platform downloads or live speech recognition.
