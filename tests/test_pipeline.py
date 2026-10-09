import shutil
import subprocess
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import ProcessingError
from app.main import create_app
from app.media import extract, inspect
from app.transcription import normalize, transcribe
from app.worker import process, recover, tick


@pytest.fixture
def cfg(tmp_path):
    return Settings(data_dir=tmp_path, store="sqlite", cookie_secure=False, max_upload_mb=1)


@pytest.fixture
def client(cfg):
    with TestClient(create_app(cfg, run_worker=False)) as client:
        client.get("/")
        yield client


def upload(client, data=b"not a video"):
    return client.post("/api/analyses", content=data, headers={"Content-Type": "video/mp4"})


def test_upload_private_and_durable(client, cfg):
    response = upload(client)
    assert response.status_code == 202
    aid = response.json()["analysis_id"]
    row = client.get(f"/api/analyses/{aid}").json()
    assert row["status"] == "queued" and "owner" not in row
    assert row["sha256"] and (cfg.data_dir / aid / "clip").exists()
    assert client.get("/api/analyses").json()[0]["id"] == aid
    client.cookies.clear()
    client.get("/")
    assert client.get(f"/api/analyses/{aid}").status_code == 404
    assert client.get("/api/analyses").json() == []


def test_limits_and_origin(client, cfg):
    assert upload(client, b"").status_code == 400
    assert upload(client, b"a" * (1024 * 1024 + 1)).status_code == 413
    assert (
        client.post(
            "/api/analyses", content=b"x", headers={"Content-Type": "text/plain"}
        ).status_code
        == 415
    )
    assert (
        client.post(
            "/api/analyses", content=b"x", headers={"Origin": "https://attacker.test"}
        ).status_code
        == 403
    )
    assert not list(cfg.data_dir.glob("*/clip"))


def test_normalize():
    result = normalize({"segments": [{"start": 0.1, "end": 1.0, "text": " Hello "}]}, 2)
    assert result["segments"] == [{"id": 0, "start": 0.1, "end": 1.0, "text": "Hello"}]
    assert result["text"] == "Hello"


@pytest.mark.parametrize(
    "segments",
    [
        [],
        [{"start": -1, "end": 1, "text": "x"}],
        [{"start": 1, "end": 0, "text": "x"}],
        [{"start": 0, "end": 99, "text": "x"}],
        [{"start": float("nan"), "end": 1, "text": "x"}],
        [{"start": 1, "end": 2, "text": "x"}, {"start": 0, "end": 1, "text": "y"}],
    ],
)
def test_invalid_segments(segments):
    with pytest.raises(ProcessingError):
        normalize({"segments": segments}, 3)


@pytest.mark.parametrize(
    "status,code", [(401, "provider_auth"), (429, "provider_rate_limit"), (503, "provider_error")]
)
def test_provider_errors(monkeypatch, tmp_path, status, code):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"test")

    def post(self, url, **kwargs):
        assert kwargs["data"]["timestamp_granularities[]"] == ["word", "segment"]
        assert kwargs["data"]["model"] == "whisper-1"
        return httpx.Response(status, json={"error": "SECRET"})

    monkeypatch.setattr(httpx.Client, "post", post)
    with pytest.raises(ProcessingError) as exc:
        transcribe(audio, "test-key", 2)
    assert exc.value.code == code and "SECRET" not in str(exc.value)


def test_worker_transcript_and_retry(client, cfg, monkeypatch):
    aid = upload(client).json()["analysis_id"]
    store = client.app.state.store
    monkeypatch.setattr("app.worker.inspect", lambda *a: {"duration": 2})
    monkeypatch.setattr("app.worker.extract", lambda path, out, cfg: out.write_bytes(b"audio"))
    process(store.get(aid), store, cfg)
    row = store.get(aid)
    assert row["status"] == "failed" and row["error"]["code"] == "provider_not_configured"
    assert not (cfg.data_dir / aid / "audio.wav").exists()
    assert client.post(f"/api/analyses/{aid}/retry").status_code == 202
    transcript = normalize(
        {
            "segments": [{"start": 0, "end": 1, "text": "Real contract fixture"}],
            "words": [
                {"word": "Real", "start": 0.123456, "end": 0.7},
                {"word": "contract", "start": 0.7, "end": 1.2},
                {"word": "fixture", "start": 1.2, "end": 1.8},
            ],
        },
        2,
    )
    monkeypatch.setattr("app.worker.transcribe", lambda *a: transcript)
    process(store.get(aid), store, cfg)
    row = client.get(f"/api/analyses/{aid}").json()
    assert row["status"] == "transcribed" and row["investigation_status"] == "not_started"
    assert row["transcript"] == transcript
    assert not (cfg.data_dir / aid / "clip").exists()
    assert client.post(f"/api/analyses/{aid}/retry").status_code == 409
    assert client.delete(f"/api/analyses/{aid}").status_code == 204


def test_recovery_and_retention(client, cfg):
    aid = upload(client).json()["analysis_id"]
    store = client.app.state.store
    row = store.get(aid)
    row["status"] = "transcribing"
    store.save(row)
    (cfg.data_dir / aid / "audio.wav").write_bytes(b"interrupted audio")
    unrelated = cfg.data_dir / "unrelated-folder"
    unrelated.mkdir()
    orphan = cfg.data_dir / ("a" * 32)
    orphan.mkdir()
    recover(store, cfg)
    assert store.get(aid)["error"]["code"] == "interrupted"
    assert not (cfg.data_dir / aid / "audio.wav").exists()
    assert unrelated.exists() and not orphan.exists()
    row = store.get(aid)
    row["created_at"] = time.time() - 100000
    store.save(row)
    tick(store, cfg)
    assert store.get(aid) is None and not (cfg.data_dir / aid).exists()


@pytest.fixture
def media(cfg):
    if not shutil.which(cfg.ffmpeg) or not shutil.which(cfg.ffprobe):
        pytest.skip("FFmpeg/ffprobe are required for real media integration")
    path = cfg.data_dir / "fixture.mp4"
    subprocess.run(
        [
            cfg.ffmpeg,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x120:r=24:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        timeout=30,
    )
    return path


def test_real_media(media, cfg):
    metadata = inspect(media, cfg)
    assert metadata["width"] == 160 and metadata["frame_rate"] == 24
    assert metadata["sample_rate"] > 0 and metadata["channels"] == 1
    audio = cfg.data_dir / "audio.wav"
    extract(media, audio, cfg)
    assert audio.read_bytes()[:4] == b"RIFF"
    cfg.max_duration = 0.5
    with pytest.raises(ProcessingError, match="exceeds"):
        inspect(media, cfg)


def test_corrupt_and_no_audio(media, cfg):
    corrupt = cfg.data_dir / "corrupt.mp4"
    corrupt.write_bytes(b"garbage")
    with pytest.raises(ProcessingError):
        inspect(corrupt, cfg)
    silent = cfg.data_dir / "silent.mov"
    subprocess.run(
        [cfg.ffmpeg, "-v", "error", "-y", "-i", str(media), "-an", "-c:v", "copy", str(silent)],
        check=True,
        timeout=30,
    )
    with pytest.raises(ProcessingError) as exc:
        inspect(silent, cfg)
    assert exc.value.code == "no_audio"


def test_real_worker_failure(client, cfg, media):
    aid = upload(client, media.read_bytes()).json()["analysis_id"]
    store = client.app.state.store
    tick(store, cfg)
    row = client.get(f"/api/analyses/{aid}").json()
    assert row["metadata"]["width"] == 160
    assert row["status"] == "failed" and row["error"]["code"] == "provider_not_configured"
    assert row["transcript"] is None
    assert row["investigation_status"] == "not_started"
    assert not (cfg.data_dir / aid / "audio.wav").exists()
    bad_id = upload(client).json()["analysis_id"]
    tick(store, cfg)
    assert store.get(bad_id)["error"]["code"] == "invalid_media"
    assert not (cfg.data_dir / bad_id / "clip").exists()


def test_queue_and_chunked_limit(client, cfg):
    cfg.max_pending = 1
    first = upload(client).json()["analysis_id"]
    assert client.delete(f"/api/analyses/{first}").status_code == 409
    assert upload(client).status_code == 429
    row = client.app.state.store.get(first)
    row.update(status="failed", error={"code": "test", "message": "test"})
    client.app.state.store.save(row)
    assert client.delete(f"/api/analyses/{first}").status_code == 204

    def chunks():
        yield b"a" * (700 * 1024)
        yield b"b" * (700 * 1024)

    response = client.post("/api/analyses", content=chunks(), headers={"Content-Type": "video/mp4"})
    assert response.status_code == 413
    assert not list(cfg.data_dir.glob("*/clip"))


def test_delayed_audio_uses_clip_clock(media, cfg):
    import array
    import wave

    delayed = cfg.data_dir / "delayed.mp4"
    subprocess.run(
        [
            cfg.ffmpeg,
            "-v",
            "error",
            "-y",
            "-i",
            str(media),
            "-itsoffset",
            "0.5",
            "-i",
            str(media),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c",
            "copy",
            str(delayed),
        ],
        check=True,
        timeout=30,
    )
    metadata = inspect(delayed, cfg)
    assert 0.4 < metadata["audio_start_seconds"] < 0.6
    audio = cfg.data_dir / "delayed.wav"
    extract(delayed, audio, cfg)
    with wave.open(str(audio), "rb") as wav:
        assert wav.getnframes() / wav.getframerate() > 1.4
        samples = array.array("h", wav.readframes(wav.getnframes()))
        assert max(abs(x) for x in samples[:3200]) < 10  # first 0.2 seconds stays silent
        assert max(abs(x) for x in samples[10000:14000]) > 100
