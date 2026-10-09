import hashlib
import socket
import ssl
import threading
import time

import dns.resolver
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import ProcessingError
from app.main import create_app
from app.transcript import normalize
from app.url_media import (
    PinnedHTTPSConnection,
    download,
    public_address,
    resolve_public,
    validate_url,
)
from app.worker import recover, tick

URL = "https://media.w3.org/fixture.mp4"


@pytest.fixture
def cfg(tmp_path):
    return Settings(
        data_dir=tmp_path,
        store="sqlite",
        cookie_secure=False,
        max_upload_mb=1,
        url_hosts=("media.w3.org",),
        openai_key="",
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://media.w3.org/a.mp4",
        "https://media.w3.org:444/a.mp4",
        "https://user:pass@media.w3.org/a.mp4",
        "https://media.w3.org.evil.test/a.mp4",
        "https://127.0.0.1/a.mp4",
        "https://[::1]/a.mp4",
        "https://2130706433/a.mp4",
        "https://media.w3.org%2f@evil.test/a.mp4",
        "https://media.w3.org/a.mp4?token=secret",
        "https://media.w3.org/a.mp4#fragment",
        "https://media.w3.org/a.mp4\n",
        "https://media.w3.org\\@evil.test/a.mp4",
        "https://media.w3.org./a.mp4",
        "https://media.w3.org/a%xx.mp4",
        "https://media.w3.org/watch",
        "https://youtube.com/watch?v=x",
    ],
)
def test_unsupported_urls(url):
    with pytest.raises(ProcessingError) as exc:
        validate_url(url, ("media.w3.org",))
    assert exc.value.code == "unsupported_url"


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "192.0.2.1",
        "::1",
        "::",
        "fc00::1",
        "fe80::1",
        "ff02::1",
        "2001:db8::1",
        "::ffff:127.0.0.1",
        "64:ff9b::a00:1",
        "2002:0a00:0001::",
        "2001::1",
    ],
)
def test_private_reserved_and_transition_addresses(address):
    assert not public_address(address)


def test_dns_all_addresses_checked_and_bounded(monkeypatch):
    calls = []

    def resolve(self, host, family, **kwargs):
        calls.append((host, family, kwargs))
        return ["8.8.8.8"] if family == "A" else ["::1"]

    monkeypatch.setattr(dns.resolver.Resolver, "resolve", resolve)
    with pytest.raises(ProcessingError) as exc:
        resolve_public("media.w3.org", time.monotonic() + 120)
    assert exc.value.code == "unsafe_url"
    assert all(c[2]["lifetime"] <= 5 and c[2]["search"] is False for c in calls)


def test_connection_pins_numeric_ip_and_keeps_hostname_tls(monkeypatch):
    calls = []

    class Sock:
        def settimeout(self, timeout):
            calls.append(("timeout", timeout))

        def connect(self, target):
            calls.append(("connect", target))

        def do_handshake(self):
            calls.append(("handshake",))

        def close(self):
            pass

    class Context:
        def wrap_socket(self, sock, **kwargs):
            calls.append(("tls", kwargs))
            return sock

    monkeypatch.setattr(socket, "socket", lambda *a: Sock())
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **kw: pytest.fail("Unexpected DNS re-resolution")
    )
    conn = PinnedHTTPSConnection("media.w3.org", ["8.8.8.8"], time.monotonic() + 120)
    assert conn._context.check_hostname and conn._context.verify_mode != 0
    conn._context = Context()
    conn.connect()
    assert ("connect", ("8.8.8.8", 443)) in calls
    assert ("tls", {"server_hostname": "media.w3.org", "do_handshake_on_connect": False}) in calls


class Response:
    def __init__(self, data=b"video fixture", status=200, headers=None):
        self.data, self.status = data, status
        self.headers = headers or {}

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read1(self, size):
        chunk, self.data = self.data[:size], self.data[size:]
        return chunk


def mock_network(monkeypatch, responses, resolver=None, abort_event=None):
    connections, resolutions = [], []

    def resolve(host, deadline):
        resolutions.append(host)
        return resolver(host) if resolver else ["8.8.8.8"]

    class Connection:
        def __init__(self, host, addresses, deadline):
            self.host, self.addresses, self.sock, self.closed = host, addresses, None, False
            connections.append(self)

        def request(self, method, path, headers):
            self.request_args = (method, path, headers)

        def getresponse(self):
            return responses.pop(0)

        def close(self):
            self.closed = True

        def abort(self):
            self.closed = True
            if abort_event:
                abort_event.set()

    monkeypatch.setattr("app.url_media.resolve_public", resolve)
    monkeypatch.setattr("app.url_media.PinnedHTTPSConnection", Connection)
    return connections, resolutions


def test_download_redirect_and_hash(cfg, monkeypatch):
    conns, resolutions = mock_network(
        monkeypatch,
        [
            Response(status=302, headers={"Location": "/final.mov"}),
            Response(headers={"Content-Length": "13"}),
        ],
    )
    out = cfg.data_dir / "clip"
    result = download(URL, out, cfg)
    assert out.read_bytes() == b"video fixture"
    assert result["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert result["final_url"] == "https://media.w3.org/final.mov"
    assert result["size_bytes"] == 13
    assert resolutions == ["media.w3.org", "media.w3.org"]
    assert all(c.closed for c in conns) and not out.with_name("clip.part").exists()
    assert conns[0].request_args[2] == {
        "Accept-Encoding": "identity",
        "User-Agent": "ClipContext/0.1",
        "Connection": "close",
    }


def test_redirect_to_private_or_unlisted_is_blocked(cfg, monkeypatch):
    conns, resolutions = mock_network(
        monkeypatch, [Response(status=302, headers={"Location": "https://127.0.0.1/a.mp4"})]
    )
    with pytest.raises(ProcessingError):
        download(URL, cfg.data_dir / "clip", cfg)
    assert len(conns) == 1 and conns[0].closed


def test_redirect_dns_rebinding_blocked(cfg, monkeypatch):
    count = 0

    def resolver(host):
        nonlocal count
        count += 1
        if count == 2:
            raise ProcessingError("unsafe_url", "Blocked rebinding")
        return ["8.8.8.8"]

    conns, _ = mock_network(
        monkeypatch, [Response(status=302, headers={"Location": "/new.mp4"})], resolver
    )
    with pytest.raises(ProcessingError) as exc:
        download(URL, cfg.data_dir / "clip", cfg)
    assert exc.value.code == "unsafe_url" and len(conns) == 1


@pytest.mark.parametrize(
    "response,code",
    [
        (Response(data=b"x" * (1024 * 1024 + 1)), "upload_size_limit"),
        (Response(headers={"Content-Length": "1048577"}), "upload_size_limit"),
        (Response(headers={"Content-Length": "99"}), "url_incomplete"),
        (Response(data=b""), "url_incomplete"),
        (Response(status=403), "url_http"),
        (Response(headers={"Content-Encoding": "gzip"}), "url_encoding"),
        (Response(headers={"Content-Length": "13, 99"}), "url_length"),
    ],
)
def test_download_failure_cleanup(cfg, monkeypatch, response, code):
    conns, _ = mock_network(monkeypatch, [response])
    out = cfg.data_dir / "clip"
    with pytest.raises(ProcessingError) as exc:
        download(URL, out, cfg)
    assert exc.value.code == code
    assert not out.exists() and not out.with_name("clip.part").exists()
    assert all(c.closed for c in conns)


def test_redirect_limit(cfg, monkeypatch):
    conns, _ = mock_network(
        monkeypatch, [Response(status=302, headers={"Location": "/next.mp4"}) for _ in range(4)]
    )
    with pytest.raises(ProcessingError) as exc:
        download(URL, cfg.data_dir / "clip", cfg)
    assert exc.value.code == "url_redirect" and len(conns) == 4


def test_global_watchdog_interrupts_slow_body(cfg, monkeypatch):
    aborted = threading.Event()

    class SlowResponse(Response):
        def read1(self, size):
            assert aborted.wait(2)
            return b""

    mock_network(monkeypatch, [SlowResponse()], abort_event=aborted)
    monkeypatch.setattr("app.url_media.DOWNLOAD_SECONDS", 0.02)
    with pytest.raises(ProcessingError) as exc:
        download(URL, cfg.data_dir / "clip", cfg)
    assert exc.value.code == "url_timeout"
    assert not (cfg.data_dir / "clip.part").exists()


def test_tls_failure(cfg, monkeypatch):
    conns, _ = mock_network(monkeypatch, [])

    def fail(self):
        raise ssl.SSLCertVerificationError("untrusted certificate")

    # Get the mocked class from a deliberate instantiation so no network is touched.
    import app.url_media as module

    monkeypatch.setattr(module.PinnedHTTPSConnection, "getresponse", fail)
    with pytest.raises(ProcessingError) as exc:
        download(URL, cfg.data_dir / "clip", cfg)
    assert exc.value.code == "url_tls" and conns[0].closed


def test_url_job_private_retry_recovery_and_shared_pipeline(cfg, monkeypatch):
    with TestClient(create_app(cfg, run_worker=False)) as client:
        client.get("/")
        assert (
            client.post("/api/analyses/url", json={"url": "http://localhost/x.mp4"}).status_code
            == 422
        )
        assert client.post("/api/analyses/url", content=b"x" * 4097).status_code == 413
        response = client.post("/api/analyses/url", json={"url": URL})
        assert response.status_code == 202
        aid = response.json()["analysis_id"]
        store = client.app.state.store
        assert store.get(aid)["ingestion"] == {"kind": "url", "url": URL}
        assert not (cfg.data_dir / aid / "clip").exists()

        def fail(*args):
            raise ProcessingError("url_download", "Network failed")

        monkeypatch.setattr("app.worker.download", fail)
        tick(store, cfg)
        assert store.get(aid)["error"]["code"] == "url_download"
        assert client.post(f"/api/analyses/{aid}/retry").status_code == 202

        def downloaded(url, out, config):
            out.write_bytes(b"fixture")
            return {"sha256": "test-hash", "size_bytes": 7, "final_url": URL}

        monkeypatch.setattr("app.worker.download", downloaded)
        monkeypatch.setattr("app.worker.inspect", lambda *a: {"duration": 2})
        monkeypatch.setattr("app.worker.extract", lambda src, out, cfg: out.write_bytes(b"audio"))
        monkeypatch.setattr(
            "app.worker.transcribe",
            lambda *a: normalize(
                {
                    "segments": [{"start": 0, "end": 1, "text": "Fixture"}],
                    "words": [{"word": "Fixture", "start": 0.25, "end": 1}],
                },
                2,
            ),
        )
        tick(store, cfg)
        row = client.get(f"/api/analyses/{aid}").json()
        assert row["status"] == "transcribed" and row["sha256"] == "test-hash"
        assert row["investigation_status"] == "not_started" and row["transcript"][
            "transcript_by_second"
        ] == {"0": ["Fixture"], "1": []}
        assert not (cfg.data_dir / aid / "clip").exists()
        client.cookies.clear()
        client.get("/")
        assert client.get(f"/api/analyses/{aid}").status_code == 404
        row = store.get(aid)
        row["status"] = "downloading"
        store.save(row)
        (cfg.data_dir / aid / "clip.part").write_bytes(b"partial")
        recover(store, cfg)
        assert store.get(aid)["error"]["code"] == "interrupted"
        assert not (cfg.data_dir / aid / "clip.part").exists()
