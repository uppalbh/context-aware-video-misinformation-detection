import math

from app.errors import ProcessingError


def word_index(raw_words, duration: float) -> tuple[list[dict], dict[str, list[str]]]:
    """Index provider words once by start time; retain their original float timings."""
    if not isinstance(raw_words, list) or len(raw_words) > 100_000:
        raise ProcessingError(
            "invalid_word_timestamps", "Transcription returned invalid word data."
        )
    words = []
    by_second = {str(second): [] for second in range(math.ceil(duration))}
    previous = 0.0
    try:
        for raw in raw_words:
            # JSON numbers only; accepting strings/bools would conceal malformed provider data.
            start, end = raw["start"], raw["end"]
            if type(start) not in (int, float) or type(end) not in (int, float):
                raise ValueError()
            start, end = float(start), float(end)
            word = raw["word"].strip()
            if not word or not (
                math.isfinite(start)
                and math.isfinite(end)
                and 0 <= start < duration
                and start <= end <= duration + 0.5
                and start >= previous
            ):
                raise ValueError()
            words.append({"id": len(words), "word": word, "start": start, "end": end})
            by_second[str(math.floor(start))].append(word)
            # Overlapping ends are permitted. Only word starts must be nondecreasing.
            previous = start
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ProcessingError(
            "invalid_word_timestamps", "Transcription returned invalid word timestamps."
        )
    return words, by_second


def normalize(payload: dict, duration: float) -> dict:
    segments = []
    previous = 0.0
    try:
        if not math.isfinite(duration) or not 0 < duration <= 3600:
            raise ValueError()
        for raw in payload["segments"]:
            start, end = float(raw["start"]), float(raw["end"])
            text = raw["text"].strip()
            if not (
                math.isfinite(start)
                and math.isfinite(end)
                and 0 <= start <= duration
                and start <= end <= duration + 0.5
                and start >= previous
            ):
                raise ValueError()
            if text:
                segments.append(
                    {
                        "id": len(segments),
                        "start": round(start, 3),
                        "end": round(min(end, duration), 3),
                        "text": text,
                    }
                )
            previous = start
        if not segments:
            raise ProcessingError("no_speech", "No timestamped speech was recognized in this clip.")
        # Missing timings never become inferred timings or a misleading empty silence index.
        raw_words = payload.get("words")
        if raw_words is None or raw_words == []:
            words, by_second, timing_status = None, None, "unavailable"
        else:
            words, by_second = word_index(raw_words, duration)
            timing_status = "available"
        return {
            "text": " ".join(s["text"] for s in segments),
            "segments": segments,
            "language": payload.get("language"),
            "provider": "openai",
            "model": "whisper-1",
            "words": words,
            "transcript_by_second": by_second,
            "word_timing_status": timing_status,
            "timeline": "uploaded_clip",
            "duration_seconds": duration,
            "bucketing": "word_start_floor_seconds",
        }
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ProcessingError("invalid_transcript", "Transcription returned invalid timestamps.")
