"""Fabricated fixtures and mocked adapters only; no real-world verification claims."""

import copy
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.alignment import align
from app.config import Settings
from app.context_analysis import analyze, validate_interpretation
from app.corpus import load_corpus, validate_source
from app.db import Store
from app.demo import source, transcript
from app.errors import ProcessingError
from app.investigation import investigate
from app.main import create_app
from app.providers import interpret, semantic_scores
from app.report import assemble
from app.source_search import search
from app.transcript import normalize
from app.worker import recover, tick


@pytest.fixture
def cfg(tmp_path):
    return Settings(
        data_dir=tmp_path,
        store="sqlite",
        cookie_secure=False,
        openai_key="",
        semantic_provider="none",
        demo_enabled=True,
    )


def permitted_fixture():
    # Real-kind contract path is exercised with fabricated test data, not a production import.
    value = source()
    value["kind"] = "real"
    value["provenance"] = "FABRICATED TEST FIXTURE for real-kind contract; not real evidence."
    return validate_source(value)


def model_fixture():
    return {
        "assessment": "context_changes_meaning",
        "clip_impression": "The excerpt favors closure.",
        "full_context": "Closure is conditional on safe housing.",
        "findings": [
            {
                "finding": "A qualification is absent from the excerpt.",
                "severity": "moderate",
                "support_ids": ["clip:c0", "source:s1"],
                "contradiction_ids": ["source:s2"],
                "quotes": [
                    {
                        "evidence_id": "source:s1",
                        "text": "only if every resident has safe permanent housing",
                    }
                ],
            }
        ],
        "uncertainty": ["Fabricated contract test; recording identity is unverified."],
    }


def row():
    return {
        "id": "a" * 32,
        "owner": "test-owner",
        "status": "transcribed",
        "created_at": time.time(),
        "transcript": transcript(),
        "ingestion": {"kind": "upload"},
        "investigation_status": "queued",
    }


def test_source_revision_import_and_duplicate(cfg):
    cfg.corpus_dir.mkdir()
    value = source()
    path = cfg.corpus_dir / "one.json"
    path.write_text(json.dumps(value))
    assert load_corpus(cfg.corpus_dir) == [value]
    updated = copy.deepcopy(value)
    updated["segments"][0]["text"] += " Updated."
    assert validate_source(updated)["revision"] != value["revision"]
    (cfg.corpus_dir / "duplicate.json").write_text(json.dumps(value))
    with pytest.raises(ProcessingError, match="duplicated"):
        load_corpus(cfg.corpus_dir)


@pytest.mark.parametrize(
    "mutation",
    [
        {"id": "../escape"},
        {"permission": ""},
        {"kind": "unknown"},
        {"url": "javascript:alert(1)"},
        {"duration": True},
        {"duration": float("inf")},
        {"segments": []},
        {"segments": [{"start": 2, "end": 1, "text": "bad"}]},
        {"segments": [{"start": 0, "end": 1, "text": " "}]},
    ],
)
def test_invalid_source(mutation):
    value = source()
    value.update(mutation)
    with pytest.raises(ProcessingError):
        validate_source(value)


def test_alignment_omission_and_separate_clocks(cfg):
    result = align(transcript(), source())
    passage = result["passages"][0]
    assert (
        passage["match"] == "exact"
        and passage["clip_start"] == 0
        and passage["source_start"] == 100
    )
    assert result["coverage"] == 1 and not result["ambiguous_passage"]
    omission = next(o for o in result["omitted"] if o["evidence_id"] == "source:s1")
    assert "only if" in omission["text"] and omission["language_cues"] == ["if", "only"]
    assert (
        source()["segments"][1]["text"][omission["char_start"] : omission["char_end"]]
        == omission["text"]
    )
    assert "word" not in passage


def test_multiple_candidates_and_abstention(cfg):
    correct = source()
    wrong = validate_source(
        {
            **source(),
            "id": "wrong",
            "segments": [
                {
                    "start": 0,
                    "end": 2,
                    "text": "Banana harvests depend on sunshine humidity and fertile soil.",
                }
            ],
        }
    )
    result = search(transcript(), [wrong, correct], cfg)
    assert result["status"] == "matched" and result["selected"]["source"]["id"] == correct["id"]
    assert result["candidates"][0]["rank_score"] > result["candidates"][1]["rank_score"]
    assert result["semantic_status"].startswith("unavailable")
    assert search(transcript(), [wrong], cfg)["status"] == "source_not_found"
    overlapping = validate_source({**correct, "id": "overlap"})
    assert search(transcript(), [correct, overlapping], cfg)["status"] == "inconclusive"
    short = normalize({"segments": [{"start": 0, "end": 1, "text": "the city"}]}, 1)
    assert search(short, [correct], cfg)["status"] == "source_not_found"


def test_repeated_passage_is_ambiguous(cfg):
    value = source()
    text = transcript()["text"]
    value["segments"] = [
        {
            "start": i * 10,
            "end": i * 10 + 5,
            "text": text if i in (0, 5) else "Filler unrelated observation.",
        }
        for i in range(6)
    ]
    assert search(transcript(), [validate_source(value)], cfg)["status"] == "inconclusive"


def test_asr_difference_and_reordered_disjoint():
    clip = transcript()
    clip["segments"][0]["text"] = clip["text"] = clip["text"].replace("city", "citty")
    result = align(clip, source())
    assert result["passages"][0]["match"] == "approximate" and 0.8 < result["coverage"] < 1
    original = source()
    original["segments"] = [
        {
            "start": i * 10,
            "end": i * 10 + 8,
            "text": f"Unique passage {i} includes distinctive alpha{i} beta{i} gamma{i} delta{i} epsilon{i}.",
        }
        for i in range(10)
    ]
    original = validate_source(original)
    clip = normalize(
        {
            "segments": [
                {"start": j * 5, "end": j * 5 + 4, "text": original["segments"][i]["text"]}
                for j, i in enumerate((8, 0))
            ]
        },
        10,
    )
    result = align(clip, original)
    assert result["reordered"] and result["disjoint"] and result["gap_segments_not_shown"]
    assert [p["source_start"] for p in result["passages"]] == [80, 0]


@pytest.mark.parametrize(
    "case", ["unknown", "fake_quote", "no_clip", "no_quote", "intent", "overlap", "extra"]
)
def test_model_rejects_unsupported_findings(case):
    value = model_fixture()
    finding = value["findings"][0]
    if case == "unknown":
        finding["support_ids"].append("source:invented")
    if case == "fake_quote":
        finding["quotes"][0]["text"] = "Invented words never spoken"
    if case == "no_clip":
        finding["support_ids"].remove("clip:c0")
    if case == "no_quote":
        finding["quotes"] = []
    if case == "intent":
        finding["finding"] = "The uploader intended to deceive."
    if case == "overlap":
        finding["contradiction_ids"].append("source:s1")
    if case == "extra":
        value["truth"] = "fake"
    with pytest.raises(ProcessingError, match="validation"):
        validate_interpretation(value, align(transcript(), source())["evidence"])


def test_context_missing_key_and_mocked_model(cfg, monkeypatch):
    original = permitted_fixture()
    aligned = align(transcript(), original)
    assert analyze(aligned, original, cfg)["status"] == "setup_required"
    cfg.openai_key = "mock-only"
    monkeypatch.setattr("app.context_analysis.interpret", lambda *args: model_fixture())
    context = analyze(aligned, original, cfg)
    assert context["status"] == "completed"
    report = assemble(
        search(transcript(), [original], Settings(data_dir=cfg.data_dir, openai_key="")), context
    )
    assert report["context_risk_score"] == 50 and report["evidence_coverage"] == 1
    assert "not calibrated" in report["source_match_quality_label"]
    monkeypatch.setattr("app.context_analysis.interpret", lambda *args: {"invalid": True})
    assert analyze(aligned, original, cfg)["status"] == "unavailable"


def test_semantic_adapter_and_bad_vectors(cfg, monkeypatch):
    cfg.openai_key = "mock-only"
    cfg.semantic_provider = "openai"
    calls = []

    def post(path, key, body):
        calls.append((path, body))
        return {"data": [{"index": i, "embedding": [1.0] + [0.0] * 255} for i in range(3)]}

    monkeypatch.setattr("app.providers.post", post)
    scores, status = semantic_scores("query", ["first", "second"], cfg)
    assert scores == [1, 1] and "embeddings" in status and calls[0][0] == "embeddings"
    monkeypatch.setattr(
        "app.providers.post",
        lambda *args: {"data": [{"index": 0, "embedding": [float("nan")] * 256}]},
    )
    with pytest.raises(ProcessingError):
        semantic_scores("query", ["doc"], cfg)


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"status": "incomplete"}, "invalid_interpretation"),
        (
            {
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "refusal"}]}],
            },
            "model_refusal",
        ),
        (
            {
                "status": "completed",
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": "not json"}]}
                ],
            },
            "invalid_interpretation",
        ),
    ],
)
def test_responses_failures(cfg, monkeypatch, payload, code):
    monkeypatch.setattr("app.providers.post", lambda *a: payload)
    with pytest.raises(ProcessingError) as exc:
        interpret({}, cfg, {})
    assert exc.value.code == code


def test_responses_request_contract(cfg, monkeypatch):
    def post(path, key, body):
        assert path == "responses" and body["store"] is False
        assert body["text"]["format"]["strict"] is True
        return {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(model_fixture())}],
                }
            ],
        }

    monkeypatch.setattr("app.providers.post", post)
    assert interpret({}, cfg, {}) == model_fixture()


def test_provider_error_does_not_leak(monkeypatch):
    from app.providers import post

    monkeypatch.setattr(
        httpx.Client, "post", lambda *a, **kw: httpx.Response(401, json={"secret": "hidden"})
    )
    with pytest.raises(ProcessingError) as exc:
        post("responses", "mock-only", {})
    assert "hidden" not in str(exc.value)


def test_synthetic_demo_no_provider_and_real_segregation(cfg, monkeypatch):
    def forbidden(*args):
        pytest.fail("Synthetic demo must never call a provider")

    monkeypatch.setattr("app.providers.post", forbidden)
    cfg.openai_key = "mock-only"
    cfg.semantic_provider = "openai"
    store = Store(cfg)
    value = row()
    value["ingestion"] = {"kind": "synthetic_demo"}
    store.save(value)
    tick(store, cfg)
    saved = store.get(value["id"])
    assert saved["investigation_status"] == "demo_only" and saved["report"]["synthetic"]
    assert saved["report"]["context_risk_score"] is None
    cfg.openai_key = ""
    value = row()
    investigate(value, store, cfg, [source()])
    assert value["investigation_status"] == "source_not_found" and value["report"]["source"] is None


def test_durable_full_mocked_pipeline_and_recovery(cfg, monkeypatch):
    cfg.corpus_dir.mkdir()
    (cfg.corpus_dir / "source.json").write_text(json.dumps(permitted_fixture()))
    store = Store(cfg)
    value = row()
    store.save(value)
    tick(store, cfg)
    saved = store.get(value["id"])
    assert saved["investigation_status"] == "setup_required" and saved["report"]["source"]
    assert saved["status"] == "transcribed"
    cfg.openai_key = "mock-only"
    monkeypatch.setattr("app.context_analysis.interpret", lambda *a: model_fixture())
    saved["investigation_status"] = "queued"
    store.save(saved)
    tick(store, cfg)
    saved = store.get(value["id"])
    assert saved["status"] == "completed" and saved["report"]["context_risk_score"] == 50
    saved["investigation_status"] = "interpreting"
    store.save(saved)
    recover(store, cfg)
    assert store.get(value["id"])["investigation_error"]["code"] == "interrupted"
    assert store.get(value["id"])["transcript"] == transcript()


def test_demo_api_history_retry_legacy_and_privacy(cfg):
    with TestClient(create_app(cfg, run_worker=False)) as client:
        client.get("/")
        assert client.get("/api/config").json()["synthetic_demo"]
        response = client.post("/api/analyses/demo")
        assert response.status_code == 202
        aid = response.json()["analysis_id"]
        store = client.app.state.store
        assert client.delete(f"/api/analyses/{aid}").status_code == 409
        tick(store, cfg)
        saved = client.get(f"/api/analyses/{aid}").json()
        assert saved["report"]["synthetic"] and "owner" not in saved
        assert client.get("/api/analyses").json()[0]["report"] == saved["report"]
        assert client.post(f"/api/analyses/{aid}/investigate").status_code == 202
        assert client.post(f"/api/analyses/{aid}/investigate").status_code == 409
        tick(store, cfg)
        saved = store.get(aid)
        saved.pop("investigation_attempts")
        saved.pop("report")
        saved["investigation_status"] = "not_started"
        store.save(saved)
        assert client.post(f"/api/analyses/{aid}/investigate").status_code == 202
        tick(store, cfg)
        saved = store.get(aid)
        saved["investigation_attempts"] = 5
        store.save(saved)
        assert client.post(f"/api/analyses/{aid}/investigate").status_code == 409
        client.cookies.clear()
        client.get("/")
        assert client.get(f"/api/analyses/{aid}").status_code == 404
        assert client.post(f"/api/analyses/{aid}/investigate").status_code == 404
        assert client.get("/api/analyses").json() == []
        cfg.demo_enabled = False
        assert client.post("/api/analyses/demo").status_code == 404


def test_corrupt_corpus_preserves_transcript(cfg):
    cfg.corpus_dir.mkdir()
    (cfg.corpus_dir / "bad.json").write_text("invalid json")
    store = Store(cfg)
    value = row()
    investigate(value, store, cfg)
    assert value["investigation_status"] == "unavailable" and value["report"] is None
    assert value["transcript"] == transcript()


def test_upload_to_persisted_report_with_mocked_providers(cfg, monkeypatch):
    cfg.corpus_dir.mkdir()
    (cfg.corpus_dir / "source.json").write_text(json.dumps(permitted_fixture()))
    cfg.openai_key = "mock-only"
    monkeypatch.setattr("app.worker.inspect", lambda *a: {"duration": 6})
    monkeypatch.setattr("app.worker.extract", lambda path, out, cfg: out.write_bytes(b"mock-audio"))
    monkeypatch.setattr("app.worker.transcribe", lambda *a: transcript())
    monkeypatch.setattr("app.context_analysis.interpret", lambda *a: model_fixture())
    with TestClient(create_app(cfg, run_worker=False)) as client:
        client.get("/")
        aid = client.post(
            "/api/analyses", content=b"mock-video", headers={"Content-Type": "video/mp4"}
        ).json()["analysis_id"]
        store = client.app.state.store
        tick(store, cfg)
        assert store.get(aid)["investigation_status"] == "queued"
        assert not (cfg.data_dir / aid / "clip").exists()
        assert not (cfg.data_dir / aid / "audio.wav").exists()
        tick(store, cfg)
        result = client.get(f"/api/analyses/{aid}").json()
        assert result["status"] == "completed" and result["report"]["context_risk_score"] == 50
        assert result["transcript"] == transcript()
        assert client.get("/api/analyses").json()[0]["report"] == result["report"]
        assert client.delete(f"/api/analyses/{aid}").status_code == 204


def test_semantic_reranking_and_fallback(cfg, monkeypatch):
    monkeypatch.setattr("app.source_search.semantic_scores", lambda *a: ([0.8], "mock embeddings"))
    result = search(transcript(), [source()], cfg)
    assert result["candidates"][0]["semantic_similarity"] == 0.8
    assert result["selected"]["rank_score"] == pytest.approx(0.97)

    def failure(*args):
        raise ProcessingError("invalid_embeddings", "Mock failure")

    monkeypatch.setattr("app.source_search.semantic_scores", failure)
    result = search(transcript(), [source()], cfg)
    assert (
        result["status"] == "matched"
        and result["semantic_status"] == "invalid_embeddings: lexical fallback"
    )


def test_inconclusive_and_consistent_score(cfg):
    value = model_fixture()
    value["assessment"] = "inconclusive"
    value["findings"] = []
    context = {
        "status": "completed",
        **validate_interpretation(value, align(transcript(), source())["evidence"]),
    }
    assert assemble(search(transcript(), [source()], cfg), context)["context_risk_score"] is None
    value = model_fixture()
    value["assessment"] = "context_consistent"
    with pytest.raises(ProcessingError):
        validate_interpretation(value, align(transcript(), source())["evidence"])
    value["findings"][0]["severity"] = "none"
    context = {
        "status": "completed",
        **validate_interpretation(value, align(transcript(), source())["evidence"]),
    }
    assert assemble(search(transcript(), [source()], cfg), context)["context_risk_score"] == 0


def test_unstructured_narrative_quote_is_rejected():
    value = model_fixture()
    value["clip_impression"] = 'The speaker says "invented alien invasion".'
    with pytest.raises(ProcessingError):
        validate_interpretation(value, align(transcript(), source())["evidence"])


def test_alignment_computation_bound(monkeypatch):
    clock = iter([0, 6])
    monkeypatch.setattr("app.alignment.time.monotonic", lambda: next(clock))
    with pytest.raises(ProcessingError, match="computation budget"):
        align(transcript(), source())


def test_import_cli_atomic_revision(cfg):
    import os
    import subprocess
    import sys

    original = cfg.data_dir / "import.json"
    original.write_text(json.dumps(source()), encoding="utf-8")
    env = {**os.environ, "CORPUS_DIR": str(cfg.corpus_dir)}
    command = [sys.executable, "-m", "app.corpus", str(original)]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0 and "kind synthetic" in result.stdout
    assert load_corpus(cfg.corpus_dir) == [source()]
    assert subprocess.run(command, env=env, capture_output=True, timeout=20).returncode != 0
    changed = source()
    changed["permission"] = "Revised fabricated fixture permission"
    original.write_text(json.dumps(changed), encoding="utf-8")
    assert (
        subprocess.run(command + ["--replace"], env=env, capture_output=True, timeout=20).returncode
        == 0
    )
    assert load_corpus(cfg.corpus_dir)[0]["revision"] != source()["revision"]


def test_retry_capacity_and_origin(cfg):
    with TestClient(create_app(cfg, run_worker=False)) as client:
        client.get("/")
        aid = client.post("/api/analyses/demo").json()["analysis_id"]
        tick(client.app.state.store, cfg)
        assert (
            client.post(
                f"/api/analyses/{aid}/investigate", headers={"Origin": "https://other.test"}
            ).status_code
            == 403
        )
        cfg.max_pending = 1
        client.post("/api/analyses/demo")
        assert client.post(f"/api/analyses/{aid}/investigate").status_code == 429
