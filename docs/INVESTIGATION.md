# Corpus, evidence workflow and synthetic demo

## Key-free local demo

Use the installed environment and process variables; no .env modification:

```powershell
$env:STORE = 'sqlite'
$env:COOKIE_SECURE = 'false'
$env:ENABLE_SYNTHETIC_DEMO = 'true'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --timeout-graceful-shutdown 480
```

Open localhost and click **Run synthetic demo**. Fabricated council text aligns clip 0–6 seconds to source segment 100–115 seconds, highlighting the housing qualification and surrounding text. No recording exists; this is not speech transcription or model assessment. Report remains inconclusive/demo_only with null risk. All providers are disabled for this route even if server credentials already exist. Reopen from history/download JSON. Disable the flag to remove demo route/button. Synthetic material never replaces real uploads.

## Import permitted original transcripts

Prepare local UTF-8 JSON from a transcript you are authorized to process:

```json
{
  "id": "unique_source_id", "kind": "real", "title": "Recording title",
  "url": "https://publisher.example/recording",
  "provenance": "How recording and timestamped transcript were obtained and attributed",
  "permission": "Specific license, permission or authorization basis",
  "duration": 180,
  "segments": [{"start": 0, "end": 12, "text": "Replace with actual permitted source words."}]
}
```

Shape example only, not real evidence. Run `python -m app.corpus path/to/source.json`; explicit revisions use --replace. Command validates bounds/provenance/timestamps, computes revision and atomically saves CORPUS_DIR/id.json. It never fetches URLs, logs into platforms, obtains captions or uploads corpus. Curate multiple sources to assess ranking. Provenance/permissions remain curator claims. Fabricated imports must use kind:synthetic and are excluded from real uploads. Each investigation reloads current corpus without restart; saved reports keep prior evidence/revisions. Explicit reinvestigation uses saved transcript without STT.

Limits: ≤100 JSON files, ≤5 MB each, ≤1000 segments/20,000 tokens per source, duration ≤14,400 seconds. Ordered finite nonempty timestamped segments/HTTPS reference URLs required. Duplicate IDs/invalid/unreadable files fail explicitly. Corpus is private operator data and does not expire with analyses. Import is single-operator, not concurrent management API.

## Ranking and abstention

Five-token phrases/non-stopword overlap shortlist ten sources; three-segment windows and SequenceMatcher provide fuzzy coverage. Base rank = 0.7×coverage + 0.2×exact-phrase fraction + 0.1×lexical overlap. Explicit SEMANTIC_PROVIDER=openai with existing server key enables text-embedding-3-small, 256 dimensions: rank = 0.85×base + 0.15×nonnegative cosine over candidate context passages. This is configured semantic reranking of a lexical shortlist, not exhaustive semantic discovery. Query/passages truncate to 5,000 characters for embeddings. Failures/no configuration are labeled lexical fallback/unavailable.

Acceptance: ≥75% clip-token coverage, ≥8 matched tokens, ≥6 distinct non-stopword query tokens. Repeated competing passages or second source ≥70% coverage with margin <0.08 abstain as inconclusive. Weak/missing candidates return source_not_found. Thresholds are heuristics, not measured probability. Normalized case/punctuation matching is not a forensic identity test.

Alignment: ≤200 clip segments/4096 tokens, ≤256 tokens per clip segment, ≤80 source context segments, five-second per-source computation budget checked between segments. Matching bounds are full segment bounds, never inferred source word times. Surrounding context is ±2 segments per hit; distant gap IDs recorded. Exact means contiguous normalized tokens; approximate handles differences/omissions. Highlighted unmatched spans may be omissions or ASR errors, not automatic deception. Reordered/disjoint passages have no single clock offset.

## Semantic findings and scores

Configured Responses receives bounded evidence/omissions/flags, store:false, strict schema, ≤3,000 output tokens. Evidence package cap 60,000 characters; larger packages abstain. Missing setup preserves alignment/report with no findings. Refused/unreadable/unsupported output is unavailable. IDs/literal quotations checked against actual segments; clip+source support and uncertainty required. Findings describe contextual divergence, not uploader intent/external truth. Structural grounding cannot guarantee reasoning.

Source_match_confidence is rank heuristic (null if no accepted match); evidence_coverage is matched clip-token fraction, not whole-event coverage. Context_risk_score is max accepted severity: none=0/low=25/moderate=50/high=75. Risk null for unavailable/inconclusive interpretation, never fake probability. Candidates, clocks, literal quotes, contradictory IDs, provenance/revision and limits remain inspectable in report/UI.

Apply migration 003 before Supabase use. Existing absent credentials remain untouched. SQLite supports deterministic demo. Live API operation/remote migrations remain unverified.
