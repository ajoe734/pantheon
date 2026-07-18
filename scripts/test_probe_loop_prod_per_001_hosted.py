import scripts.probe_loop_prod_per_001_hosted as probe
from scripts.probe_loop_prod_per_001_hosted import is_canonical_strategy_artifact_id


def test_strategy_artifact_id_accepts_persona_artifact_authority_metadata():
    record = {
        "metadata": {
            "strategy_artifact_id": "artifact-persona-paper-abc123",
            "strategy_spec_registry_id": "reg-strategy-spec-persona-abc123",
            "authoritative_loader_attestation": {
                "artifact_id": "artifact-persona-paper-abc123",
            },
        }
    }

    assert is_canonical_strategy_artifact_id("artifact-persona-paper-abc123", record)


def test_strategy_artifact_id_rejects_strategy_spec_registry_id():
    record = {
        "metadata": {
            "strategy_artifact_id": "artifact-persona-paper-abc123",
            "strategy_spec_registry_id": "reg-strategy-spec-persona-abc123",
        }
    }

    assert not is_canonical_strategy_artifact_id("reg-strategy-spec-persona-abc123", record)


def test_strategy_artifact_id_requires_authority_match_when_metadata_present():
    record = {
        "metadata": {
            "strategy_artifact_id": "artifact-persona-paper-abc123",
        }
    }

    assert not is_canonical_strategy_artifact_id("artifact-persona-paper-other", record)


def test_strategy_artifact_id_accepts_legacy_strategy_artifact_prefix_without_metadata():
    assert is_canonical_strategy_artifact_id("reg-strategy-artifact-abc123")


def test_request_json_records_transport_timeout(monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise TimeoutError("read operation timed out")

    monkeypatch.setattr(probe.urllib.request, "urlopen", raise_timeout)

    response = probe.request_json("GET", "https://example.test/readyz", timeout=3)

    assert response["status"] is None
    assert response["ok"] is False
    assert response["json"]["error"]["code"] == "request_failed"
    assert response["json"]["error"]["details"] == {
        "exception": "TimeoutError",
        "timeout_seconds": 3,
    }


def test_request_json_with_retries_returns_success_after_retryable_failure(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if len(calls) == 1:
            return {"status": None, "ok": False, "json": {"error": {"code": "request_failed"}}}
        return {"status": 201, "ok": True, "json": {"data": {"id": "persona-1"}}}

    monkeypatch.setattr(probe, "request_json", fake_request)
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: None)

    response = probe.request_json_with_retries(
        "POST",
        "https://example.test/bff/personas",
        attempts=2,
        retry_delay_seconds=5,
        timeout=120,
    )

    assert response["status"] == 201
    assert len(response["retry_attempts"]) == 2
    assert [attempt["attempt"] for attempt in response["retry_attempts"]] == [1, 2]
