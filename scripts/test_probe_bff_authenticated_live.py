from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
import threading
import urllib.error
from pathlib import Path
from typing import Any


def _load_probe_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "probe_bff_authenticated_live.py"
    spec = importlib.util.spec_from_file_location("probe_bff_authenticated_live", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, *, status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._body = json.dumps(body).encode("utf-8")
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        return False

    def read(self) -> bytes:
        return self._body


def test_request_json_validates_required_values_and_extracts_body_paths(monkeypatch) -> None:
    probe = _load_probe_module()

    def fake_urlopen(_request, timeout: float):
        assert timeout == 7.0
        return _FakeResponse(
            status=200,
            body={
                "data": {"id": "strategy-dry-run-001"},
                "meta": {
                    "dryRun": True,
                    "durable": False,
                    "liveCapitalSideEffects": False,
                },
            },
            headers={"X-Correlation-Id": "cid-test"},
        )

    monkeypatch.setattr(probe.urllib.request, "urlopen", fake_urlopen)
    result = probe.request_json(
        base_url="https://bff.example.test",
        probe=probe.Probe(
            "POST",
            "/bff/strategies",
            "dry-run-strategy",
            body={"name": "preview"},
            required_values=probe.DRY_RUN_META_VALUES,
            extract_paths=(("data", "id"),),
            extra_headers=(("X-Dry-Run", "1"),),
        ),
        token="token-123",
        timeout=7.0,
        idempotency_prefix="idem-test",
    )

    assert result["ok"] is True
    assert result["extracted"] == {"data.id": "strategy-dry-run-001"}
    assert result["response_headers"] == {"X-Correlation-Id": "cid-test"}


def test_request_json_accepts_expected_bff_error_envelope(monkeypatch) -> None:
    probe = _load_probe_module()

    def fake_urlopen(request, timeout: float):
        assert timeout == 3.0
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            {},
            io.BytesIO(
                json.dumps(
                    {
                        "error": {
                            "code": "FORBIDDEN",
                            "details": {"precondition_failed": "role_check"},
                        }
                    }
                ).encode("utf-8")
            ),
        )

    monkeypatch.setattr(probe.urllib.request, "urlopen", fake_urlopen)
    result = probe.request_json(
        base_url="https://bff.example.test",
        probe=probe.Probe(
            "POST",
            "/bff/strategies",
            "viewer-write-denied",
            body={"name": "must-not-write"},
            expect_status={403},
            expect_error_envelope=True,
            allowed_error_codes=("FORBIDDEN",),
            extra_headers=(("X-Dry-Run", "1"),),
        ),
        token="viewer-token",
        timeout=3.0,
        idempotency_prefix="idem-test",
    )

    assert result["ok"] is True
    assert result["error_envelope"] is True
    assert result["error_code"] == "FORBIDDEN"


def test_build_dry_run_results_attaches_per_probe_side_effect_proofs(monkeypatch) -> None:
    probe = _load_probe_module()

    def fake_request_json(*, base_url: str, probe: Any, token: str, timeout: float, idempotency_prefix: str):
        assert base_url == "https://bff.example.test"
        assert token == "operator-token"
        assert timeout == 4.0
        assert idempotency_prefix == "idem-dry"
        result = {
            "family": probe.family,
            "method": probe.method,
            "path": probe.path,
            "status": 200,
            "ok": True,
            "error_envelope": False,
        }
        if probe.family in {"dry-run-strategy-create", "dry-run-ranking-formula-create"}:
            result["extracted"] = {
                "meta.dryRun": True,
                "meta.durable": False,
                "meta.liveCapitalSideEffects": False,
                "data.id": f"{probe.family}-id",
            }
        elif probe.family == "dry-run-v5-intervention-claim":
            result["extracted"] = {
                "meta.dryRun": True,
                "meta.durable": False,
                "meta.liveCapitalSideEffects": False,
            }
        elif probe.family.endswith("-readback-not-persisted"):
            result.update({"status": 404, "error_envelope": True, "error_code": "RESOURCE_NOT_FOUND"})
        elif probe.family.startswith("dry-run-invalid-"):
            result.update({"status": 422, "error_envelope": True, "error_code": "VALIDATION_FAILED"})
        return result

    monkeypatch.setattr(probe, "request_json", fake_request_json)

    results = probe.build_dry_run_results(
        base_url="https://bff.example.test",
        token="operator-token",
        timeout=4.0,
        idempotency_prefix="idem-dry",
    )

    assert len(results) == 7
    assert all(result["side_effect_check"]["ok"] is True for result in results)
    assert [result["side_effect_check"]["kind"] for result in results] == [
        "dry_run_preview_meta",
        "readback_not_persisted",
        "dry_run_preview_meta",
        "readback_not_persisted",
        "dry_run_command_meta",
        "validation_rejected_before_persistence",
        "validation_rejected_before_persistence",
    ]
    readback_checks = [
        result["side_effect_check"]
        for result in results
        if result["side_effect_check"]["kind"] == "readback_not_persisted"
    ]
    assert all("target_id_sha256_12" in check for check in readback_checks)
    assert all("target_id" not in check for check in readback_checks)


def test_build_rbac_matrix_results_attaches_write_side_effect_proofs(monkeypatch) -> None:
    probe = _load_probe_module()
    labels = ("viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown")
    monkeypatch.setenv(
        "PANTHEON_BFF_RBAC_TOKENS_JSON",
        json.dumps({label: f"{label}-provided-secret" for label in labels}),
    )

    def fake_request_json(*, base_url: str, probe: Any, token: str | None, timeout: float, idempotency_prefix: str):
        assert base_url == "https://bff.example.test"
        assert timeout == 4.0
        assert idempotency_prefix == "idem-rbac"
        result = {
            "family": probe.family,
            "method": probe.method,
            "path": probe.path,
            "status": 403 if probe.expect_error_envelope else 200,
            "ok": True,
            "error_envelope": probe.expect_error_envelope,
        }
        if probe.method == "POST" and probe.expect_error_envelope:
            result["error_code"] = "FORBIDDEN"
        elif probe.method == "POST":
            result["extracted"] = {
                "meta.dryRun": True,
                "meta.durable": False,
                "meta.liveCapitalSideEffects": False,
            }
        return result

    monkeypatch.setattr(probe, "request_json", fake_request_json)
    args = argparse.Namespace(
        subject="op-live-smoke",
        issuer="pantheon-dev",
        audience="bff-operators",
        ttl_seconds=3600,
        require_provided_rbac_tokens=True,
    )

    results, _source = probe.build_rbac_matrix_results(
        args=args,
        base_url="https://bff.example.test",
        timeout=4.0,
        idempotency_prefix="idem-rbac",
    )

    write_results = [result for result in results if result["family"].startswith("rbac-write-")]
    assert len(results) == 56
    assert len(write_results) == 32
    assert all(result["side_effect_check"]["ok"] is True for result in write_results)
    assert [result["side_effect_check"]["kind"] for result in write_results].count("rbac_dry_run_write_meta") == 16
    assert [result["side_effect_check"]["kind"] for result in write_results].count(
        "authorization_rejected_before_persistence"
    ) == 16
    assert all("target_marker_sha256_12" in result["side_effect_check"] for result in write_results)
    assert "live-rbac-" not in json.dumps([result["side_effect_check"] for result in write_results])


def test_make_rbac_tokens_mints_full_matrix_without_leaking_tokens(monkeypatch) -> None:
    probe = _load_probe_module()
    monkeypatch.delenv("PANTHEON_BFF_RBAC_TOKENS_JSON", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_SMOKE_JWT_SECRET", "unit-test-secret")
    args = argparse.Namespace(
        subject="op-live-smoke",
        issuer="pantheon-dev",
        audience="bff-operators",
        ttl_seconds=3600,
        require_provided_rbac_tokens=False,
    )

    tokens, source = probe.make_rbac_tokens(args)

    assert tokens["anonymous"] is None
    for label in ("viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"):
        assert isinstance(tokens[label], str) and tokens[label]
        assert source["cases"][label]["kind"] == "minted_hs256_jwt"
    assert source["cases"]["empty"]["roles"] == []
    assert source["cases"]["unknown"]["roles"] == ["auditor"]
    assert all(str(token) not in json.dumps(source) for token in tokens.values() if token)


def test_make_rbac_tokens_accepts_distinct_provided_bearer_matrix_without_leaking_tokens(monkeypatch) -> None:
    probe = _load_probe_module()
    labels = ("viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown")
    rbac_tokens = {label: f"{label}-provided-secret" for label in labels}
    monkeypatch.setenv("PANTHEON_BFF_RBAC_TOKENS_JSON", json.dumps(rbac_tokens))
    args = argparse.Namespace(
        subject="op-live-smoke",
        issuer="pantheon-dev",
        audience="bff-operators",
        ttl_seconds=3600,
        require_provided_rbac_tokens=True,
    )

    tokens, source = probe.make_rbac_tokens(args)

    assert tokens["anonymous"] is None
    assert source["provided_bearer_count"] == len(labels)
    assert source["distinct_provided_bearer_count"] == len(labels)
    assert source["distinct_provided_bearers"] is True
    assert source["duplicate_bearer_label_groups"] == []
    assert len({source["cases"][label]["sha256_12"] for label in labels}) == len(labels)
    assert all(str(token) not in json.dumps(source) for token in rbac_tokens.values())


def test_make_rbac_tokens_rejects_duplicate_provided_bearers_for_strict_matrix(monkeypatch) -> None:
    probe = _load_probe_module()
    labels = ("viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown")
    rbac_tokens = {label: f"{label}-provided-secret" for label in labels}
    rbac_tokens["operator"] = rbac_tokens["viewer"]
    monkeypatch.setenv("PANTHEON_BFF_RBAC_TOKENS_JSON", json.dumps(rbac_tokens))
    args = argparse.Namespace(
        subject="op-live-smoke",
        issuer="pantheon-dev",
        audience="bff-operators",
        ttl_seconds=3600,
        require_provided_rbac_tokens=True,
    )

    try:
        probe.make_rbac_tokens(args)
    except SystemExit as exc:
        assert "distinct bearer tokens per RBAC label" in str(exc)
        assert "viewer/operator" in str(exc)
    else:
        raise AssertionError("strict RBAC matrix must reject reused bearer tokens")


def test_strict_live_evidence_forces_real_bearer_matrix_and_race_tokens(monkeypatch) -> None:
    probe = _load_probe_module()
    monkeypatch.setenv("PANTHEON_BFF_SMOKE_BEARER_TOKEN", "live-primary-token")
    monkeypatch.setenv("PANTHEON_BFF_APPROVAL_RACE_TOKEN_A", "live-race-token-a")
    monkeypatch.setenv("PANTHEON_BFF_APPROVAL_RACE_TOKEN_B", "live-race-token-b")
    args = argparse.Namespace(
        strict_live_evidence=True,
        include_rbac_matrix=False,
        include_dry_run=False,
        include_approval_race=False,
        include_two_man_race=False,
        require_provided_rbac_tokens=False,
        require_provided_approval_race_tokens=False,
        approval_race_id="approval-strict-race",
        two_man_race_id="intervention-strict-race",
    )

    probe.apply_strict_live_evidence(args)

    assert args.include_rbac_matrix is True
    assert args.include_dry_run is True
    assert args.include_approval_race is True
    assert args.include_two_man_race is True
    assert args.require_provided_rbac_tokens is True
    assert args.require_provided_approval_race_tokens is True


def test_strict_live_evidence_rejects_dev_jwt_without_primary_bearer(monkeypatch) -> None:
    probe = _load_probe_module()
    monkeypatch.delenv("PANTHEON_BFF_SMOKE_BEARER_TOKEN", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_SMOKE_JWT_SECRET", "dev-secret-must-not-count")
    monkeypatch.setenv("PANTHEON_BFF_APPROVAL_RACE_TOKEN_A", "live-race-token-a")
    monkeypatch.setenv("PANTHEON_BFF_APPROVAL_RACE_TOKEN_B", "live-race-token-b")
    args = argparse.Namespace(
        strict_live_evidence=True,
        include_rbac_matrix=False,
        include_dry_run=False,
        include_approval_race=False,
        include_two_man_race=False,
        require_provided_rbac_tokens=False,
        require_provided_approval_race_tokens=False,
        approval_race_id="approval-strict-race",
        two_man_race_id="intervention-strict-race",
    )

    try:
        probe.apply_strict_live_evidence(args)
    except SystemExit as exc:
        assert "PANTHEON_BFF_SMOKE_BEARER_TOKEN" in str(exc)
    else:
        raise AssertionError("strict live evidence must reject dev JWT-only auth")


def test_strict_live_evidence_rejects_same_approval_race_bearer(monkeypatch) -> None:
    probe = _load_probe_module()
    monkeypatch.setenv("PANTHEON_BFF_SMOKE_BEARER_TOKEN", "live-primary-token")
    monkeypatch.setenv("PANTHEON_BFF_APPROVAL_RACE_TOKEN_A", "Bearer same-race-token")
    monkeypatch.setenv("PANTHEON_BFF_APPROVAL_RACE_TOKEN_B", "same-race-token")
    args = argparse.Namespace(
        strict_live_evidence=True,
        include_rbac_matrix=False,
        include_dry_run=False,
        include_approval_race=False,
        include_two_man_race=False,
        require_provided_rbac_tokens=False,
        require_provided_approval_race_tokens=False,
        approval_race_id="approval-strict-race",
        two_man_race_id="intervention-strict-race",
    )

    try:
        probe.apply_strict_live_evidence(args)
    except SystemExit as exc:
        assert "distinct bearer tokens" in str(exc)
    else:
        raise AssertionError("strict live evidence must reject same approval race bearer")


def test_build_approval_race_accepts_single_winner_plus_conflict(monkeypatch) -> None:
    probe = _load_probe_module()
    calls: list[str] = []
    lock = threading.Lock()

    monkeypatch.setattr(
        probe,
        "approval_race_tokens",
        lambda _args, _primary_token: ("token-a", "token-b", {"kind": "unit-test"}),
    )

    def fake_urlopen(request, timeout: float):
        assert timeout == 5.0
        with lock:
            calls.append(request.headers["Authorization"])
            call_number = len(calls)
        if call_number == 1:
            return _FakeResponse(
                status=202,
                body={
                    "data": {"command_id": "cmd-race-winner"},
                    "meta": {"idempotency": {"replayed": False}},
                },
            )
        raise urllib.error.HTTPError(
            request.full_url,
            409,
            "Conflict",
            {},
            io.BytesIO(
                json.dumps(
                    {
                        "error": {
                            "code": "STATE_CONFLICT",
                            "details": {"precondition_failed": "approval_state"},
                        }
                    }
                ).encode("utf-8")
            ),
        )

    monkeypatch.setattr(probe.urllib.request, "urlopen", fake_urlopen)
    args = argparse.Namespace(
        approval_race_id="approval-race-unit",
        approval_race_decision="approve",
        subject="op-live-smoke",
        issuer="pantheon-dev",
        audience="bff-operators",
        ttl_seconds=3600,
        require_provided_approval_race_tokens=False,
        allow_single_token_approval_race=False,
    )

    result = probe.build_approval_race_results(
        args=args,
        base_url="https://bff.example.test",
        token="primary",
        timeout=5.0,
        idempotency_prefix="idem-unit",
    )

    assert result["ok"] is True
    assert result["bounded"] is True
    assert result["accepted_count"] == 1
    assert result["safe_error_count"] == 1
    assert result["duplicate_winners"] is False
    assert sorted(calls) == ["Bearer token-a", "Bearer token-b"]


def test_build_two_man_race_accepts_two_operator_scoped_signatures(monkeypatch) -> None:
    probe = _load_probe_module()
    calls: list[str] = []
    lock = threading.Lock()

    monkeypatch.setattr(
        probe,
        "approval_race_tokens",
        lambda _args, _primary_token: ("token-a", "token-b", {"kind": "provided_bearer_pair"}),
    )

    def fake_urlopen(request, timeout: float):
        assert timeout == 5.0
        with lock:
            calls.append(request.headers["Authorization"])
            call_number = len(calls)
        return _FakeResponse(
            status=202,
            body={
                "data": {"command_id": f"cmd-two-man-{call_number}"},
                "meta": {"idempotency": {"replayed": False}},
            },
        )

    monkeypatch.setattr(probe.urllib.request, "urlopen", fake_urlopen)
    args = argparse.Namespace(
        two_man_race_id="intervention-race-unit",
        approval_race_id="approval-race-unit",
        approval_race_decision="approve",
        subject="op-live-smoke",
        issuer="pantheon-dev",
        audience="bff-operators",
        ttl_seconds=3600,
        require_provided_approval_race_tokens=False,
        allow_single_token_approval_race=False,
    )

    result = probe.build_two_man_race_results(
        args=args,
        base_url="https://bff.example.test",
        token="primary",
        timeout=5.0,
        idempotency_prefix="idem-unit",
    )

    assert result["ok"] is True
    assert result["operator_scoped"] is True
    assert result["accepted_count"] == 2
    assert result["replayed_count"] == 0
    assert result["distinct_command_ids"] is True
    assert result["command_id_count"] == 2
    assert sorted(calls) == ["Bearer token-a", "Bearer token-b"]


def test_build_two_man_race_rejects_idempotency_replay(monkeypatch) -> None:
    probe = _load_probe_module()

    monkeypatch.setattr(
        probe,
        "approval_race_tokens",
        lambda _args, _primary_token: ("token-a", "token-b", {"kind": "provided_bearer_pair"}),
    )

    def fake_urlopen(_request, timeout: float):
        assert timeout == 5.0
        return _FakeResponse(
            status=202,
            body={
                "data": {"command_id": "cmd-two-man-replayed"},
                "meta": {"idempotency": {"replayed": True}},
            },
        )

    monkeypatch.setattr(probe.urllib.request, "urlopen", fake_urlopen)
    args = argparse.Namespace(
        two_man_race_id="intervention-race-unit",
        approval_race_id="approval-race-unit",
        approval_race_decision="approve",
        subject="op-live-smoke",
        issuer="pantheon-dev",
        audience="bff-operators",
        ttl_seconds=3600,
        require_provided_approval_race_tokens=False,
        allow_single_token_approval_race=False,
    )

    result = probe.build_two_man_race_results(
        args=args,
        base_url="https://bff.example.test",
        token="primary",
        timeout=5.0,
        idempotency_prefix="idem-unit",
    )

    assert result["ok"] is False
    assert result["operator_scoped"] is False
    assert result["accepted_count"] == 2
    assert result["replayed_count"] == 2


def test_build_approval_race_rejects_two_safe_errors_without_winner(monkeypatch) -> None:
    probe = _load_probe_module()

    monkeypatch.setattr(
        probe,
        "approval_race_tokens",
        lambda _args, _primary_token: ("token-a", "token-b", {"kind": "unit-test"}),
    )

    def fake_urlopen(request, timeout: float):
        assert timeout == 5.0
        raise urllib.error.HTTPError(
            request.full_url,
            404,
            "Not Found",
            {},
            io.BytesIO(
                json.dumps(
                    {
                        "error": {
                            "code": "NOT_FOUND",
                            "details": {"precondition_failed": "approval_missing"},
                        }
                    }
                ).encode("utf-8")
            ),
        )

    monkeypatch.setattr(probe.urllib.request, "urlopen", fake_urlopen)
    args = argparse.Namespace(
        approval_race_id="approval-race-unit",
        approval_race_decision="approve",
        subject="op-live-smoke",
        issuer="pantheon-dev",
        audience="bff-operators",
        ttl_seconds=3600,
        require_provided_approval_race_tokens=False,
        allow_single_token_approval_race=False,
    )

    result = probe.build_approval_race_results(
        args=args,
        base_url="https://bff.example.test",
        token="primary",
        timeout=5.0,
        idempotency_prefix="idem-unit",
    )

    assert result["ok"] is False
    assert result["bounded"] is False
    assert result["accepted_count"] == 0
    assert result["safe_error_count"] == 2
    assert result["duplicate_winners"] is False


def test_build_approval_race_fails_duplicate_winners(monkeypatch) -> None:
    probe = _load_probe_module()

    monkeypatch.setattr(
        probe,
        "approval_race_tokens",
        lambda _args, _primary_token: ("token-a", "token-b", {"kind": "unit-test"}),
    )

    def fake_urlopen(_request, timeout: float):
        assert timeout == 5.0
        return _FakeResponse(
            status=202,
            body={
                "data": {"command_id": "cmd-race-accepted"},
                "meta": {"idempotency": {"replayed": False}},
            },
        )

    monkeypatch.setattr(probe.urllib.request, "urlopen", fake_urlopen)
    args = argparse.Namespace(
        approval_race_id="approval-race-unit",
        approval_race_decision="approve",
        subject="op-live-smoke",
        issuer="pantheon-dev",
        audience="bff-operators",
        ttl_seconds=3600,
        require_provided_approval_race_tokens=False,
        allow_single_token_approval_race=False,
    )

    result = probe.build_approval_race_results(
        args=args,
        base_url="https://bff.example.test",
        token="primary",
        timeout=5.0,
        idempotency_prefix="idem-unit",
    )

    assert result["ok"] is False
    assert result["bounded"] is False
    assert result["accepted_count"] == 2
    assert result["safe_error_count"] == 0
    assert result["duplicate_winners"] is True
