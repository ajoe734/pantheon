"""Research owner-binding readback of a recorded backtest run (POOL-MEMBER-BASELINE-20261112).

Local unit fixtures only (recorded Taiwan backtest evaluation, HS256 test JWTs); no hosted claim.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
SECRET = "backtest-owner-readback-test-secret-0123456789"
FIXTURE = REPO_ROOT / "services/research/replication/fixtures/recorded_backtest_tw_2330.json"


def _bearer(tenant: str, roles) -> dict:
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode().rstrip("=")
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps({"sub": "caller", "roles": list(roles), "tenant_id": tenant}).encode())
    sig = b64(hmac.new(SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    return {"Authorization": f"Bearer {head}.{body}.{sig}", "X-Tenant-Id": tenant}


@pytest.fixture
def research(tmp_path):
    env = {"RESEARCH_ORCHESTRATOR_DATA_DIR": str(tmp_path / "r"), "ALPHA_REPLICATION_DATA_DIR": str(tmp_path / "a"),
           "PANTHEON_RESEARCH_AUTH_MODE": "strict", "PANTHEON_RESEARCH_JWT_SECRET": SECRET}
    name = "research_main_owner_readback"
    with mock.patch.dict(os.environ, env):
        spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "services/research/main.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        yield module, TestClient(module.app), tmp_path / "a"


def _record(module, data_dir: Path, tenant: str | None):
    evaluation = json.loads(FIXTURE.read_text(encoding="utf-8"))
    from services.research.alpha_replication.shared_stores import SharedBacktestRunStore
    run_id = SharedBacktestRunStore(data_dir / "backtest_runs.json").record(evaluation)
    if tenant:
        module._bind_backtest_run_owner(data_dir, run_id, tenant_id=tenant, decision_id="dec-1")
    return evaluation, run_id


def _url(run_id: str) -> str:
    return f"/api/research-orchestrator/backtest-runs/{run_id}/owner-binding"


def test_registry_writer_reads_own_tenant_run_with_exact_digest(research):
    module, client, data_dir = research
    evaluation, run_id = _record(module, data_dir, "tenant-a")
    resp = client.get(_url(run_id), headers=_bearer("tenant-a", ["registry-writer"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tenant_id"] == "tenant-a" and body["decision_id"] == "dec-1" and body["status"] == "completed"
    assert body["artifact_checksum"] == evaluation["strategy_artifact"]["checksum"]
    # Same canonical digest the Registry owner recomputes from the full evaluation.
    expected = "sha256:" + hashlib.sha256(
        json.dumps(evaluation, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    assert body["evaluation_digest"] == expected
    assert "authorization" not in json.dumps(body).lower()


def test_foreign_unbound_unknown_and_unauthorised_callers_fail(research):
    module, client, data_dir = research
    _, run_id = _record(module, data_dir, "tenant-a")
    writer = ["registry-writer"]
    # foreign tenant cannot read tenant-a's run (404, not disclosed); header/token mismatch is refused
    assert client.get(_url(run_id), headers=_bearer("tenant-b", writer)).status_code == 404
    mismatch = {**_bearer("tenant-b", writer), "X-Tenant-Id": "tenant-a"}
    assert client.get(_url(run_id), headers=mismatch).status_code in (401, 403)
    # viewer is not admitted; missing/forged bearer is refused
    assert client.get(_url(run_id), headers=_bearer("tenant-a", ["viewer"])).status_code == 403
    assert client.get(_url(run_id), headers={"X-Tenant-Id": "tenant-a"}).status_code == 401
    forged = {**_bearer("tenant-a", writer)}
    forged["Authorization"] = forged["Authorization"][:-4] + "AAAA"
    assert client.get(_url(run_id), headers=forged).status_code == 401
    assert client.get(_url("bt-unknown"), headers=_bearer("tenant-a", writer)).status_code == 404


def test_historical_run_without_owner_binding_is_not_served(research):
    module, client, data_dir = research
    _, run_id = _record(module, data_dir, None)
    assert client.get(_url(run_id), headers=_bearer("tenant-a", ["registry-writer"])).status_code == 404
