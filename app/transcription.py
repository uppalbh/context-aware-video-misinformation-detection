from pathlib import Path

import httpx

from app.errors import ProcessingError
from app.transcript import normalize


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
                    "timestamp_granularities[]": ["word", "segment"],
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
