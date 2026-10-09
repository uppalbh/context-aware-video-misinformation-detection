# ClipContext — Final feature set and processing pipeline

**Implemented:** MP4/MOV upload, [supported direct HTTPS video URLs](docs/URL_INGESTION.md), background media validation/audio extraction, private session records, and automatic transcription with segment timestamps, word timestamps, and a complete second-by-second word dictionary. Start with [setup and API documentation](docs/SETUP.md), [teammate integration contracts](docs/INTEGRATION.md), and the [completion checklist](docs/COMPLETION.md). The roadmap below describes the larger product; only stages 1–2 are implemented. Transcription completion is not a misinformation assessment.

For your 24-hour hackathon, I would lock the scope to one complete video-investigation workflow, with an optional experimental audio-forensics model. Your primary goal is to detect misleading context, not merely detect whether a video contains edits.

## 1. Exact features to build

### P0 — Essential features

These must work end to end for your demo.

1\. Clip ingestion

- Upload MP4/MOV video.
- Accept a supported public video URL if time permits.
- Validate size, format, duration, and URL safety.
- Extract audio and store the analysis record.

2\. Automatic transcription

- Convert speech to text using a pretrained transcription API.
- Preserve timestamps for transcript segments.
- Store the transcript and handle transcription failures.

3\. Original-source retrieval

- Search a curated corpus of original recordings and transcripts.
- Combine exact phrase matching with semantic similarity.
- Rank candidate sources and determine whether the evidence is sufficient to accept a match.
- Return source URLs and provenance.

4\. Transcript and timestamp alignment

- Locate the clip's matching passage in the original recording.
- Identify preceding and following sentences.
- Highlight omitted qualifications, negations, questions, and replies.
- Distinguish exact matches from approximate matches.

5\. Context-divergence analysis

- Compare the apparent implication of the isolated clip with its fuller context.
- Use an LLM to interpret the evidence.
- Require findings to cite specific transcript segments.
- Allow a result of inconclusive when evidence is insufficient.

6\. Evidence report

- Contextual-risk score with an explanation.
- Source-match quality and evidence coverage.
- Supporting and contradictory evidence.
- Source links and timestamps.
- Explicit uncertainty and limitations.

### P1 — Features that make the project stand out

7\. Visual evidence timeline

Display the uploaded clip alongside the original recording, with matching timestamps, transcript highlights, and omitted context.

8\. Experimental audio-edit detector

Extract audio features such as MFCCs, spectral changes, and energy. Optionally train a small LSTM or 1D CNN to flag possible acoustic discontinuities. Display these as suspected edit locations, not proof of deception.

9\. Analysis history

Save previous analyses and let users reopen their reports. Reuse the template's authentication if it already exists; otherwise, history can be session-based.

Scope decision: Do not make deepfake classification, model training, authentication, or a large-scale internet crawler prerequisites for the MVP. The audio-edit detector is an optional signal, while source reconstruction and contextual analysis are the core product.

## 2. Exact pipeline for processing a clip

This is the order I would implement in the backend. Every step takes defined inputs and produces outputs for the next step.

### 01

### Receive clip

Validate upload or URL; create analysis ID

### 02

### Extract media

FFmpeg extracts audio and video metadata

### 03

### Transcribe

Speech-to-text produces timestamped transcript

### 04

### Find source

Search the indexed transcript corpus

### 05

### Align passages

Locate the excerpt and surrounding context

### 06

### Analyze meaning

Assess omissions and contextual divergence

### 07

### Build evidence report

Calculate scores and attach source references

### 08

### Save and display

Persist results; frontend fetches the report

### Step 1 — Receive and validate the clip

Input: An uploaded video or supported public video URL.

Your API:

1. Checks the file type, size, and duration limits.
2. Generates an `analysis_id`.
3. Saves the uploaded file in temporary storage or private object storage.
4. Creates a database record with status `queued`.
5. Starts a background processing job and returns the ID to the frontend.

Example response:

```
{
  "analysis_id": "abc123",
  "status": "queued"
}
```

Do not make the frontend wait for the whole pipeline in a single HTTP request.

### Step 2 — Extract audio and metadata

Use FFmpeg to extract the audio track and inspect the video.

Collect:

- Video duration, frame rate, and resolution.
- Audio duration, sample rate, and channel count.
- A temporary audio file for transcription.
- A file hash for identifying exact file matches.

Output: Audio file, media metadata, and processing references.

Do not interpret metadata or a matching hash as proof that the clip's content is truthful.

### Step 3 — Transcribe the clip

Send the extracted audio to your transcription service.

Example output:

| Start | End   | Transcript             |
| ----- | ----- | ---------------------- |
| 4.2 s | 5.1 s | I don't support        |
| 5.1 s | 7.4 s | helping these families |

The values are illustrative.

Store the transcript segments with timestamps. You now have text that can be searched against original recordings.

### Step 4 — Find the original source

This is the most important retrieval stage.

Prepare a small source corpus before the demo. For each source, store its title, URL, transcript, timestamps, and provenance.

Run three retrieval methods:

1. Exact phrase search: Finds distinctive words shared with the clip.
2. Fuzzy text matching: Handles minor transcription differences.
3. Semantic search: Finds passages with similar meaning even if the wording differs.

Combine and rank the candidate matches. If a source has a strong match, continue to alignment. If the match is weak or ambiguous, return `source_not_found` or `inconclusive` rather than inventing a source.

One key detail: the YouTube Data API can help discover videos, but you still need an authorized or otherwise permitted way to obtain the corresponding transcripts. A curated transcript database is the safer approach for the 24-hour deadline.

### Step 5 — Align the clip with the original recording

Once you identify a candidate source:

1. Find the best matching passage in the original transcript.
2. Determine its start and end timestamps.
3. Retrieve the preceding and following transcript segments.
4. Compare the clip transcript against the full passage.
5. Identify omitted text and any important differences.

Example:

UPLOADED CLIP

"I don't support helping these families."

ORIGINAL RECORDING

"Would you support cutting assistance to these families?"

"I don't support helping these families by cutting their benefits."

"I support expanding the program."

Illustrative example. In the actual product, these statements must come from a verified matching source.

The alignment module should output the matching timestamps, exact text matches, omitted passages, and match quality.

### Step 6 — Analyze contextual divergence

Send the LLM a structured evidence package containing:

- The uploaded clip's transcript.
- The matched original passage.
- The surrounding statements.
- The source URL and timestamps.
- The alignment results and uncertainties.

Ask it to determine:

- What impression the isolated excerpt may create.
- Whether the surrounding context materially changes that impression.
- Which omitted statements support that conclusion.
- Whether any evidence contradicts the conclusion.
- What cannot be established from the available evidence.

Require structured JSON output with evidence IDs referencing actual transcript segments. The LLM must not invent quotations or claim to know the uploader's intent.

### Step 7 — Calculate the risk score and create the report

Use a separate scoring module to combine measurable signals and the contextual analysis.

Keep these outputs distinct:

- `source_match_confidence`
- `context_risk_score`
- `evidence_coverage`
- `assessment`
- `uncertainty`

For example, a strong source match with an important omitted qualification could result in high contextual risk. A weak source match should result in an inconclusive assessment, not automatically high risk.

Your score should be treated as a heuristic until you validate it against labeled examples.

### Step 8 — Save results and display them

Persist the completed report, source references, evidence segments, and scores in Supabase.

Update the analysis status to `completed`, then let the frontend retrieve the report using the analysis ID.

The frontend should display:

1. The overall assessment.
2. The contextual-risk score and explanation.
3. The original source and matching timestamps.
4. Side-by-side transcript comparison.
5. Highlighted omissions.
6. Supporting and contradictory evidence.
7. An uncertainty statement.

If any stage fails, update the job status and provide a meaningful error. A failed transcription or missing source should never produce a fabricated completed report.

## 3. Where does the RNN fit into this pipeline?

The audio model should run as a parallel branch, not as a prerequisite for contextual analysis.

Extracted audio

Audio feature extraction

MFCCs · spectral features · energy

Optional trained LSTM / 1D CNN

Possible acoustic edit boundaries

Audio-forensics evidence

Candidate timestamps and model scores

The audio branch can enrich the final report with possible edit locations. It cannot establish that an authentic clip has been taken out of context. Train it only if you have time to build a reasonable dataset and evaluate it on held-out recordings.

## 4. Exact backend modules to implement

Keep your code modular so you can test each stage independently.

| Module                         | Responsibility                                      |
| ------------------------------ | --------------------------------------------------- |
| `api/analyses.py`              | Upload, create analysis, retrieve status and report |
| `services/media.py`            | Validate files and extract audio using FFmpeg       |
| `services/transcription.py`    | Call speech-to-text and normalize timestamps        |
| `services/source_search.py`    | Search and rank candidate source recordings         |
| `services/alignment.py`        | Match transcripts and retrieve surrounding context  |
| `services/context_analysis.py` | LLM-based evidence interpretation                   |
| `services/scoring.py`          | Context-risk and evidence-quality calculations      |
| `services/report.py`           | Assemble the structured report                      |
| `services/audio_forensics.py`  | Optional trained audio-edit model                   |
| `db/`                          | Models, migrations, queries, and persistence        |
| `tests/`                       | Unit tests and end-to-end pipeline tests            |

Use your template's existing naming conventions rather than forcing these exact filenames if the architecture differs.

## 5. The order you should actually build it

## 24-hour implementation checklist

0 of 6

Inspect the template and define API contracts

Hours 0–2

Implement ingestion, transcription, and database persistence

Hours 2–6

Build curated-source retrieval and transcript alignment

Hours 6–12

Implement contextual analysis, scoring, and evidence reports

Hours 12–17

Connect the frontend and test the complete workflow

Hours 17–21

Run the real demo, fix failures, and rehearse

Hours 21–24

Definition of done: A user uploads a real clip, the backend finds its original source, the system reconstructs the surrounding context, and the frontend presents a traceable explanation of whether the excerpt is misleading—with uncertainty when the evidence is insufficient.

That is the complete MVP. Get that working before adding model training or any other stretch feature.
