# Core MVP coverage and practical limits

| Goal | Delivered | Verification |
| --- | --- | --- |
| Upload/supported URL | Queued private MP4/MOV; pinned allowlisted HTTPS | Real FFmpeg/probe/extraction, prior live W3C download, policy/transport tests |
| STT/per-second words | Whisper adapter; provider floats, segment clock, every-second start bucket | Mocked requests, normalization/index tests; live STT unverified |
| Source retrieval | Permitted multiple-source corpus/import, exact/fuzzy matching, optional embeddings | Distinct/mismatched/overlapping/repeated fixtures; live embeddings unverified |
| Alignment/context | Separate clocks, approximate ASR, omitted spans/cues, disjoint/reordered flags | Deterministic bounds/spans/ASR/gap tests |
| Interpretation | Structured Responses, evidence IDs/literal quotes, contradictions/uncertainty, missing-key abstention | Mocked request/output/refusal/error tests; live model quality unverified |
| Reports/scores | Distinct source rank, ordinal severity, token coverage; null risk without semantics | Persisted mocked complete and key-free inconclusive/synthetic reports |
| Timeline/history | Paired clock bars, side-by-side text/omissions, evidence IDs, report reopen/download | Node DOM tests and explicit synthetic browser check |
| Queue/privacy | Durable media/investigation states, retained-transcript retry, recovery, limits/retention | API/store/session/retry/restart tests |
| Modular handoff | Callable contracts/docs/migrations 001–003 | CI lint/format/Python/Node checks |

Core implementation is present. No credentials were created, inserted or requested. Private .env remains ignored/unchanged. Live Whisper/context/embeddings and remote Supabase migrations/persistence are unverified; a real credential-backed end-to-end demo remains outstanding. Configured adapters do not establish account/model availability.

Synthetic demo checks retrieval/alignment/report/storage/UI without speech or semantic calls. It is labeled fabricated, excluded from real ingestion, and has null risk. Contract tests use fabricated transcripts/mocked model outputs, never evidence about real events. Production has no bundled real corpus: operator must import permitted timestamped originals.

Bounded heuristic retrieval: ≤100 recordings, top ten lexical sources, optional candidate-passage embedding reranking; no semantic-only all-corpus discovery or internet search. Thresholds/ordinal scores are not calibrated. Repeated/overlapping candidates abstain. Match does not prove originality, truth or intent. Context windows and segment clocks have explicit precision/coverage limits. Models can still reason incorrectly over validated evidence.

Optional audio-forensics training, broad discovery/crawling, retained-video playback, distributed workers, account authentication and deployment are deferred. Timeline compares transcript evidence; raw media are removed after STT and not embedded in browser.

Run pytest, Ruff lint/format, JavaScript syntax and Node UI tests as described in SETUP. CI installs FFmpeg; local real-media tests explicitly skip only if executables are absent. Mocked provider/storage tests never require credentials.

Final local audit: **134 Python tests passed with no skips**, **4 frontend tests passed**, Ruff lint/format, JavaScript syntax and git diff checks passed. Browser synthetic demo persisted its report, displayed separate clocks/omitted qualification, and reopened after page reload. The full upload-to-context-report integration is tested with mocked media/STT/LLM contracts; independent media tests use actual FFmpeg. One existing Starlette TestClient deprecation warning remains. This is fixture/contract evidence, not live provider or remote database verification.
