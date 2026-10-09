import math
from pathlib import Path

import httpx

from app.errors import ProcessingError


def normalize(payload: dict, duration: float) -> dict:
    segments = []
    previous = 0.0
    try:
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
        return {
            "text": " ".join(s["text"] for s in segments),
            "segments": segments,
            "language": payload.get("language"),
            "provider": "openai",
            "model": "whisper-1",
        }
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ProcessingError("invalid_transcript", "Transcription returned invalid timestamps.")


def transcribe(audio: Path, key: str, duration: float) -> dict:
    if not key:
        raise ProcessingError(
            "provider_not_configured", "Server needs an OPENAI_API_KEY to transcribe."
        )
    try:
        with httpx.Client(timeout=httpx.Timeout(180, connect=10)) as client, audio.open("rb") as f:
            response = client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files={"file": ("audio.wav", f, "audio/wav")},
                data={
                    "model": "whisper-1",
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "segment",
                },
            )
        if response.status_code in (401, 403):
            raise ProcessingError(
                "provider_auth", "Server transcription credentials were rejected."
            )
        if response.status_code == 429:
            raise ProcessingError(
                "provider_rate_limit", "Transcription quota or rate limit reached. Retry later."
            )
        if response.is_error:
            raise ProcessingError("provider_error", "Transcription provider failed. Retry later.")
        return normalize(response.json(), duration)
    except httpx.TimeoutException:
        raise ProcessingError("provider_timeout", "Transcription timed out. Retry later.")
    except httpx.RequestError:
        raise ProcessingError(
            "provider_unreachable", "Transcription provider could not be reached."
        )
    except ValueError:
        raise ProcessingError(
            "invalid_transcript", "Transcription returned an unreadable response."
        )
