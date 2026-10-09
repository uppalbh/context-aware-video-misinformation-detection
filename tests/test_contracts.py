import httpx
import pytest

from app.config import Settings
from app.db import Store
from app.errors import ProcessingError
from app.transcription import transcribe


def test_supabase_server_only_contract(tmp_path, monkeypatch):
    cfg = Settings(
        data_dir=tmp_path,
        store="supabase",
        supabase_url="https://example.supabase.co",
        supabase_key="test-service-role",
    )
    requests = []
    row = {"id": "a" * 32, "owner": "hash", "status": "queued"}

    def request(self, method, url, **kwargs):
        requests.append((method, url, kwargs))
        assert kwargs["headers"]["apikey"] == "test-service-role"
        assert kwargs["headers"]["Authorization"] == "Bearer test-service-role"
        return httpx.Response(200, json=[row], request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.Client, "request", request)
    store = Store(cfg)
    store.save(row, new=True)
    store.save(row)
    assert store.get(row["id"]) == row
    assert store.all() == [row]
    store.delete(row["id"])
    assert [r[0] for r in requests] == ["POST", "PATCH", "GET", "GET", "DELETE"]
    assert all(r[1] == "https://example.supabase.co/rest/v1/analyses" for r in requests)
    assert requests[1][2]["params"] == {"id": "eq." + row["id"]}


def test_transcription_success_and_timeout(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fixture")

    def post(self, url, **kwargs):
        assert kwargs["files"]["file"][1].read() == b"fixture"
        return httpx.Response(
            200,
            json={
                "language": "english",
                "segments": [{"start": 0.0, "end": 1.0, "text": " A contract fixture. "}],
            },
        )

    monkeypatch.setattr(httpx.Client, "post", post)
    result = transcribe(audio, "key", 2)
    assert result["text"] == "A contract fixture." and result["language"] == "english"

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("secret provider details")

    monkeypatch.setattr(httpx.Client, "post", timeout)
    with pytest.raises(ProcessingError) as exc:
        transcribe(audio, "key", 2)
    assert exc.value.code == "provider_timeout" and "secret" not in str(exc.value)
