"""Real source materialization, evaluation authority and paper admission replay.

Only owner HTTP responses are faked. Bars are committed TWSE recordings; the
controller envelope and bounded historical policy are explicit test fixtures.
"""
from __future__ import annotations

import copy
import importlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess

import pytest

from services.foundation.reliable_delivery import AtomicJsonRecordStore
from services.persona.pool_autonomy_cycle import (
    HttpPoolCyclePorts, PoolAutonomyCycle, apply_to_packet, candidate_virtual_book,
)
from services.persona.pool_dataset_authority import governed_dataset_loader
from services.research.tests.test_pool_admission import entries_and_members, recorded_dataset

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "docs/deployment/evidence/PERSONA-POOL-DATASET-AUTHORITY-20261113/fixtures"
NOW = datetime(2024, 12, 31, 12, tzinfo=timezone.utc)
BASE_URL = "http://source-ingest.test:8097"
READBACK = BASE_URL + "/api/source-ingest/controller/readback"
PERSONA = {"tenant_id": "tenant-a", "persona_id": "pool-source-test", "mandate": "Taiwan momentum"}


@pytest.fixture
def source_case(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "services/control-plane/persona"))
    source_root = tmp_path / "source"
    source_root.mkdir()
    for name in ("raw", "normalized", "features"):
        (source_root / name).mkdir()
    normalized = (FIXTURES / "normalized.jsonl").read_text()
    (source_root / "normalized/ingest-authority-001.jsonl").write_text(normalized)
    # Raw and feature storage retain source lineage; no generated prices.
    (source_root / "raw/ingest-authority-001.jsonl").write_text(normalized)
    rows = [json.loads(line) for line in normalized.splitlines()]
    (source_root / "features/ingest-authority-001.jsonl").write_text("".join(json.dumps({
        "source_id": r["source_id"], "connector_id": r["connector_id"],
        "source_dataset": "tw_price_daily", "feature_dataset": "returns",
        "feature_as_of_time": r["metadata"]["date"],
    }) + "\n" for r in rows))
    responses = json.loads((FIXTURES / "source-owner-http.json").read_text().replace("/fixture/source", str(source_root)))
    token = tmp_path / "source-read-token"
    token.write_text("fixture-source-read-token")
    token.chmod(0o600)
    env = {
        "SOURCE_INGEST_API_URL": BASE_URL,
        "PERSONA_POOL_SOURCE_CONNECTOR_ID": "tw-twse-tpex-official-market",
        "PERSONA_POOL_SOURCE_DATASET_ID": "tw_price_daily",
        "PERSONA_POOL_SOURCE_VOLUME_ROOT": str(source_root),
        "PERSONA_POOL_DATASET_AUTHORITY_OUTPUT_ROOT": str(tmp_path / "authority"),
        "PERSONA_POOL_SOURCE_READ_TOKEN_FILE": str(token),
        "PERSONA_POOL_SOURCE_READ_TENANT_ID": "tenant-dev",
        "PERSONA_POOL_THRESHOLD_POLICY_PATH": str(FIXTURES / "policy.json"),
    }
    calls = []

    def get(url, *, timeout_seconds, headers):
        assert headers == {"Authorization": "Bearer fixture-source-read-token", "X-Tenant-Id": "tenant-dev"}
        assert timeout_seconds == 5
        calls.append(url)
        return copy.deepcopy(responses[url])

    source = importlib.import_module("services.training-session.source_dataset_authority")
    monkeypatch.setattr(source, "urllib_json_get", get)
    return env, responses, calls


def materialized(env):
    paths = list(Path(env["PERSONA_POOL_DATASET_AUTHORITY_OUTPUT_ROOT"]).glob("dataset-version-*.json"))
    assert len(paths) == 1
    return paths[0], json.loads(paths[0].read_text())


def test_loader_pins_recorded_source_dataset_and_lineage(source_case):
    env, _, calls = source_case
    dataset = governed_dataset_loader(env, clock=lambda: NOW)(PERSONA, {"artifact_ref": "candidate"})
    _, payload = materialized(env)
    assert dataset["dataset_id"] == payload["dataset_version_id"]
    assert dataset["strategy_id"] == "candidate"
    key = lambda r: (r["instrument"], r["date"])
    assert sorted(dataset["records"], key=key) == sorted(recorded_dataset()["records"], key=key)
    assert payload["metadata_json"]["source_connector_id"] == "tw-twse-tpex-official-market"
    assert payload["metadata_json"]["source_dataset_id"] == "tw_price_daily"
    assert payload["metadata_json"]["source_ingest_run_id"] == "ingest-authority-001"
    assert len(calls) >= 3 and calls[0] == READBACK
    assert payload["metadata_json"]["source_storage_binding_sha256"]
    assert payload["metadata_json"]["canonical_ohlcv_sha256"]
    # A later failure must not silently reuse this successful snapshot.
    source_case[1][READBACK]["connectors"][0]["source_health"]["status"] = "error"
    with pytest.raises(ValueError, match="authority rejected"):
        governed_dataset_loader(env, clock=lambda: NOW)(PERSONA, {"artifact_ref": "candidate"})


def run_cycle(tmp_path, source_case):
    env, _, _ = source_case
    pool_module = importlib.import_module("services.control_plane.persona.persona_strategy_pool")
    policy = importlib.import_module("services.control_plane.persona.pool_policy").load_policy()
    entries, current, candidate = entries_and_members()
    # Current approved universe includes both symbols. Candidate is the genuine
    # approved single-symbol fixture; its real admission may still be refused.
    current, candidate = candidate, current
    candidate_entry = entries[candidate["artifact_ref"]]
    candidate_entry["artifact_type"] = "execution_bundle"
    candidate_entry["lineage"]["source_strategy_spec_id"] = "spec-fixture"
    family = candidate_entry["metadata"]["strategy_artifact"]["strategy_id"]
    http = []

    def transport(method, service, path, tenant, body=None):
        assert tenant == PERSONA["tenant_id"]
        http.append((method, service, path))
        if service == "registry" and "/strategies/" in path:
            return [{"entry": candidate_entry, "deployment_stage": None}]
        if service == "registry":
            return {"entry": entries[path.rsplit("/", 1)[1]]}
        if service == "research" and path.endswith("/reconcile"):
            return {"demands": []}
        if service == "research" and "alpha-replication" in path:
            return [{"admission_status": "admitted", "queue_status": "completed",
                     "strategy_spec_id": "spec-fixture", "strategy_id": family}]
        if service == "memory":
            return {"entries": []} if method == "GET" else {"created": True}
        raise AssertionError((method, service, path))

    pool = pool_module.PersonaStrategyPoolStore(
        AtomicJsonRecordStore(tmp_path / "pool.json"),
        registry_reader=lambda ref, tenant: entries[ref], owner_lister=lambda pid, tenant: [],
    )
    pool.append(PERSONA["persona_id"], PERSONA["tenant_id"], pool_module.AppendPoolVersionRequest(
        expected_version=0, members=[pool_module.PoolMember.model_validate(current)], change_reason="fixture"), actor_id="fixture")
    initial = pool.current(PERSONA["persona_id"], PERSONA["tenant_id"])
    book = candidate_virtual_book(dataset=recorded_dataset(), artifact=entries[current["artifact_ref"]]["metadata"]["strategy_artifact"],
                                  checksum=current["checksum"], policy=policy)
    ports = HttpPoolCyclePorts(transport, pool_store=pool, dataset_loader=governed_dataset_loader(env, clock=lambda: NOW), env=env)
    result = PoolAutonomyCycle(ports, now=lambda: NOW.isoformat()).run(PERSONA, {
        "pool": initial, "pool_health": {"demand_reasons": ["low_risk_budget_utilisation"]},
        "books": {current["artifact_ref"]: book}, "observations": [], "unavailable": [],
    }, "packet-source-test")
    packet = {"packet_id": "packet-source-test", "audit_refs": [], "observe": {"telemetry_refs": []},
              "orient": {"evidence_bundle_refs": []}, "decide": {}, "act": {}, "learn": {"telemetry_refs": []}}
    apply_to_packet(packet, result)
    return packet, pool.current(PERSONA["persona_id"], PERSONA["tenant_id"]), http


def test_cycle_real_loader_and_candidate_replay_use_source_dataset_version(tmp_path, source_case):
    packet, pool, _ = run_cycle(tmp_path, source_case)
    result = packet["pool_autonomy"]
    assert result["decide"]["kind"] == "admit_trial", result
    assert result["act"]["receipts"][0]["status"] in {"applied", "refused"}, result
    _, payload = materialized(source_case[0])
    candidate = pool["decision"]["candidate"]
    replay = pool["decision"]["book_sources"][candidate["artifact_ref"]]
    assert replay["source_ref"] == "governed-replay:" + payload["dataset_version_id"]
    assert replay["checksum"] == candidate["checksum"]
    assert replay["dates"][0] == "2024-11-28" and replay["dates"][-1] == "2024-12-31"
    # Recorded prices genuinely trigger the unchanged risk policy. This is
    # evidence of a real admission attempt, not a fabricated successful admit.
    assert pool["decision"]["reasons"] == ["candidate_virtual_book_drawdown_exceeds_removal_policy"]
    assert not packet["act"]["live_capital_side_effects"]


@pytest.mark.parametrize("failure", ["missing_config", "missing_token", "source_rejected", "stale_readback", "missing_storage", "invalid_policy"])
def test_rejected_authority_records_ooda_reason_without_admission(tmp_path, source_case, failure):
    env, responses, _ = source_case
    if failure == "missing_config":
        del env["PERSONA_POOL_SOURCE_CONNECTOR_ID"]
    elif failure == "missing_token":
        Path(env["PERSONA_POOL_SOURCE_READ_TOKEN_FILE"]).unlink()
    elif failure == "source_rejected":
        responses[READBACK]["connectors"][0]["source_health"]["status"] = "error"
    elif failure == "stale_readback":
        responses[READBACK]["captured_at"] = "2024-12-30T12:00:00Z"
    elif failure == "missing_storage":
        (Path(env["PERSONA_POOL_SOURCE_VOLUME_ROOT"]) / "normalized/ingest-authority-001.jsonl").unlink()
    else:
        env["PERSONA_POOL_THRESHOLD_POLICY_PATH"] = str(tmp_path / "absent-policy.json")
    packet, pool, http = run_cycle(tmp_path, source_case)
    receipt = packet["pool_autonomy"]["act"]["receipts"][0]
    assert receipt["status"] == "not_attempted", packet
    assert receipt["reason"].startswith("governed_dataset_unavailable:")
    assert pool["pool_version"] == 1
    assert not any(method == "POST" and service == "memory" for method, service, _ in http)
    assert not packet["act"]["live_capital_side_effects"]


def test_explicit_dev_override_delegates_to_existing_path_loader(monkeypatch):
    import services.persona.pool_autonomy_cycle as cycle
    env = {"PERSONA_POOL_CANONICAL_DATASET_PATH": "/dev/dataset.json"}
    observed = []
    def path_loader(config):
        observed.append(config)
        return lambda p, c: {"dataset_id": "explicit-dev-override"}
    monkeypatch.setattr(cycle, "governed_dataset_loader", path_loader)
    assert governed_dataset_loader(env)(PERSONA, {"artifact_ref": "candidate"})["dataset_id"] == "explicit-dev-override"
    assert observed == [env]


@pytest.mark.parametrize("profile", ["core", "root"])
def test_rendered_compose_persona_uses_training_source_authority_and_reader(profile):
    # Ignore host .env and interpolation overrides so deployed defaults are
    # actually rendered with the profiles that contain persona.
    result = subprocess.run(["docker", "compose", "--env-file", "/dev/null", "--profile", profile, "config", "--format", "json"],
                            cwd=ROOT, env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]},
                            text=True, capture_output=True, timeout=60, check=True)
    services = json.loads(result.stdout)["services"]
    # Training is in the data profile, so render it independently when core
    # excludes it rather than silently dropping persona from the assertion.
    training_config = subprocess.run(["docker", "compose", "--env-file", "/dev/null", "--profile", "*", "config", "--format", "json"],
                                     cwd=ROOT, env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]},
                                     text=True, capture_output=True, timeout=60, check=True)
    persona = services["persona"]
    training = json.loads(training_config.stdout)["services"]["training-session-svc"]
    env = persona["environment"]
    assert env["PERSONA_POOL_CANONICAL_DATASET_PATH"] == ""
    assert not env.get("TRAINING_SESSION_CANONICAL_DATASET_PATH")
    assert env["SOURCE_INGEST_API_URL"] == training["environment"]["SOURCE_INGEST_API_URL"]
    for name, value in {"CONNECTOR_ID": "tw-twse-tpex-official-market", "DATASET_ID": "tw_price_daily", "VOLUME_ROOT": "/data/source-ingest"}.items():
        assert env["PERSONA_POOL_SOURCE_" + name] == training["environment"]["TRAINING_SESSION_SOURCE_" + name] == value
    assert env["PERSONA_POOL_SOURCE_READ_TOKEN_FILE"] == "/run/pantheon-principals/PERSONA_POOL_SOURCE_READ_TOKEN"
    assert env["PERSONA_POOL_SOURCE_READ_TENANT_ID"] == "tenant-dev"
    assert any(v["source"] == "source-ingest-data" and v["target"] == "/data/source-ingest" and v["read_only"] for v in persona["volumes"])
    assert any(v["source"] == "dev-paper-persona-tokens" and v["target"] == "/run/pantheon-principals" and v["read_only"] for v in persona["volumes"])
    assert env["PERSONA_POOL_DATASET_AUTHORITY_OUTPUT_ROOT"].startswith("/data/bff/")
    from scripts.issue_dev_paper_principals import READERS, CONSUMER_FILES
    assert READERS["PERSONA_POOL_SOURCE_READ_TOKEN"] == ("pantheon-dev-persona-pool-source-reader", "source_ingest_reader")
    assert "PERSONA_POOL_SOURCE_READ_TOKEN" in CONSUMER_FILES["persona"]
