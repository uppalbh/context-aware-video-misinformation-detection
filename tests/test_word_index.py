import json

import httpx
import pytest

from app.errors import ProcessingError
from app.transcription import normalize, transcribe


def payload(words):
    return {"segments": [{"start": 0, "end": 2, "text": "A fixture"}], "words": words}


def word(text, start, end):
    return {"word": text, "start": start, "end": end}


def test_full_index_exact_boundaries_order_and_overlaps():
    raw = [
        word("First", 0.00012345, 1.2),
        word("second", 0.9, 1.3),
        word("boundary", 1.0, 1.4),
        word("overlap", 1.0, 1.1),
        word("last", 2.2, 2.7),
    ]
    result = normalize(payload(raw), 2.25)
    assert result["transcript_by_second"] == {
        "0": ["First", "second"],
        "1": ["boundary", "overlap"],
        "2": ["last"],
    }
    assert result["words"][0]["start"] == 0.00012345
    assert result["words"][-1]["end"] == 2.7  # bounded end overshoot retained, not clamped
    assert [w["id"] for w in result["words"]] == list(range(5))
    assert [token for bucket in result["transcript_by_second"].values() for token in bucket] == [
        w["word"] for w in result["words"]
    ]
    assert result["timeline"] == "uploaded_clip"
    assert result["word_timing_status"] == "available"
    assert json.loads(json.dumps(result)) == result


def test_every_second_and_fractional_tail():
    result = normalize(payload([word("spanning", 0.8, 2.2), word("minute", 65, 65.2)]), 65.25)
    buckets = result["transcript_by_second"]
    assert len(buckets) == 66 and buckets["65"] == ["minute"]
    assert buckets["1"] == [] and buckets["2"] == []  # empty does not mean silence
    assert list(buckets) == [str(i) for i in range(66)]
    assert len(normalize(payload([word("one", 0, 1)]), 2)["transcript_by_second"]) == 2


@pytest.mark.parametrize("words", [None, []])
def test_unavailable_word_timing_keeps_segments(words):
    result = normalize(payload(words), 3)
    assert result["text"] == "A fixture" and result["segments"]
    assert result["words"] is None and result["transcript_by_second"] is None
    assert result["word_timing_status"] == "unavailable"


def test_legacy_provider_response():
    result = normalize({"segments": [{"start": 0, "end": 1, "text": "Legacy"}]}, 3)
    assert result["word_timing_status"] == "unavailable"
    assert result["segments"][0]["text"] == "Legacy"


@pytest.mark.parametrize(
    "words",
    [
        {},
        "not a list",
        [word("x", -0.1, 1)],
        [word("x", 1, 0.9)],
        [word("x", float("nan"), 1)],
        [word("x", 0, float("inf"))],
        [word("x", True, 1)],
        [word("x", "0.5", 1)],
        [word("", 0, 1)],
        [word("x", 3, 3)],
        [word("x", 0, 3.50001)],
        [word("late", 1, 2), word("early", 0.9, 1.1)],
        [{"word": "missing", "start": 0}],
    ],
)
def test_invalid_word_timestamps(words):
    with pytest.raises(ProcessingError) as exc:
        normalize(payload(words), 3)
    assert exc.value.code == "invalid_word_timestamps"


def test_no_speech_is_not_an_empty_dictionary():
    with pytest.raises(ProcessingError) as exc:
        normalize({"segments": [], "words": []}, 2)
    assert exc.value.code == "no_speech"


def test_word_and_duration_resource_bounds():
    with pytest.raises(ProcessingError) as exc:
        normalize(payload([word("x", 0, 1)] * 100_001), 3)
    assert exc.value.code == "invalid_word_timestamps"
    for duration in (0, -1, float("nan"), float("inf"), 3600.1):
        with pytest.raises(ProcessingError):
            normalize(payload([word("x", 0, 1)]), duration)


def test_multipart_requests_both_granularities(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"test audio")
    real_client = httpx.Client

    def handle(request):
        body = request.read()
        assert body.count(b'name="timestamp_granularities[]"') == 2
        assert b"\r\n\r\nword\r\n" in body and b"\r\n\r\nsegment\r\n" in body
        assert b"\r\n\r\nwhisper-1\r\n" in body
        return httpx.Response(200, json=payload([word("fixture", 0.125, 0.5)]))

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    result = transcribe(audio, "test-key", 3)
    assert result["words"][0]["start"] == 0.125
    assert result["transcript_by_second"] == {"0": ["fixture"], "1": [], "2": []}
