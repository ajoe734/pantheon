"""AG-WS-OPS-002 — durable operation-lifecycle contract and restart proof.

Covers the v1.11 additive contract leaf for the three Workshop operations
deferred by AG-GAP-005 (research-runs, consultations, conclude) plus the
Postgres restart-persistence proof that downstream IDs, receipts, and the
terminal conclusion survive a BFF restart without re-dispatching downstream.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft7Validator, FormatChecker

from .store import PostgresWorkshopStore


ROOT = Path(__file__).resolve().parents[5]
AGORA_SPECS = ROOT / "services/control-plane/specs/agora"
OPERATION_SCHEMA = AGORA_SPECS / "v12/workshop_operation_lifecycle.schema.json"
OPERATION_MANIFEST = AGORA_SPECS / "v12/capability_manifest_v1_11.json"
OPERATION_BUNDLE = AGORA_SPECS / "bundle_index.v1_11.json"
OPERATION_OPENAPI = ROOT / "services/control-plane/openapi/agora_v1_11.openapi.yaml"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def test_v1_11_contract_bundle_is_additive_typed_and_hash_locked() -> None:
    schema = json.loads(OPERATION_SCHEMA.read_text(encoding="utf-8"))
    manifest = json.loads(OPERATION_MANIFEST.read_text(encoding="utf-8"))
    bundle = json.loads(OPERATION_BUNDLE.read_text(encoding="utf-8"))
    openapi = yaml.safe_load(OPERATION_OPENAPI.read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)

    assert bundle["extends"] == {
        "bundle_path": "services/control-plane/specs/agora/bundle_index.v1_10.json",
        "bundle_version": "1.10",
        "bundle_index_sha256": _sha256(AGORA_SPECS / "bundle_index.v1_10.json"),
    }
    for relative_path, expected_hash in bundle["files"].items():
        assert _sha256(ROOT / "services/control-plane" / relative_path) == expected_hash
    assert bundle["openapi"] == {
        "path": "services/control-plane/openapi/agora_v1_11.openapi.yaml",
        "sha256": _sha256(OPERATION_OPENAPI),
    }
    assert bundle["required_definition_checksums"] == manifest[
        "required_definition_checksums"
    ]
    assert set(bundle["required_definition_checksums"]) == set(schema["definitions"])
    for name, expected_hash in bundle["required_definition_checksums"].items():
        actual_hash = hashlib.sha256(
            _stable_json(schema["definitions"][name]).encode("utf-8")
        ).hexdigest()
        assert actual_hash == expected_hash, name

    expected_routes = {
        "POST /bff/agora/workshops/{workshop_id}/research-runs",
        "POST /bff/agora/workshops/{workshop_id}/consultations",
        "POST /bff/agora/workshops/{workshop_id}/conclude",
    }
    openapi_routes = {
        f"{method.upper()} {path}"
        for path, path_item in openapi["paths"].items()
        for method in path_item
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    }
    manifest_routes = {
        route
        for capability in manifest["capabilities"]
        for route in capability["routes"]
    }
    assert openapi_routes == manifest_routes == expected_routes
    assert openapi["info"]["x-extends-contract"].endswith("bundle_index.v1_10.json")
    assert openapi["info"]["x-implementation-status"] == "implemented"
    for path in (
        "/bff/agora/workshops/{workshop_id}/research-runs",
        "/bff/agora/workshops/{workshop_id}/consultations",
        "/bff/agora/workshops/{workshop_id}/conclude",
    ):
        refs = {
            parameter["$ref"]
            for parameter in openapi["paths"][path]["post"]["parameters"]
        }
        assert "#/components/parameters/IfMatch" in refs
        assert "#/components/parameters/IdempotencyKey" in refs
        assert "#/components/parameters/XRequestId" in refs
        assert "#/components/parameters/XMfaToken" in refs
        assert openapi["paths"][path]["post"]["requestBody"]["required"] is True

    capability = manifest["capabilities"][0]
    assert capability["implementation_status"] == "implemented"
    assert capability["execution_authority"] == "none"
    assert capability["durability"]["restart_persistent"] is True
    assert capability["conclusion_rules"]["final_version_must_be_selected"] is True

    receipt_validator = Draft7Validator(
        schema["definitions"]["WorkshopOperationCommandReceipt"],
        format_checker=FormatChecker(),
    )
    valid_receipt = {
        "receipt_id": "cmd-contract",
        "operation": "dispatch_research",
        "status": "completed",
        "command_terminal": True,
        "idempotency_key": "contract-key",
        "request_hash": "a" * 64,
        "expected_lock_version": 2,
        "resulting_lock_version": 3,
        "canonical_refs": {
            "research_task_id": "research-task-001",
            "research_run_id": "research-run-001",
        },
    }
    assert list(receipt_validator.iter_errors(valid_receipt)) == []
    assert list(
        receipt_validator.iter_errors({**valid_receipt, "operation": "create_version"})
    )

    run_validator = Draft7Validator(
        schema["definitions"]["WorkshopResearchRunResource"],
        format_checker=FormatChecker(),
    )
    valid_run = {
        "task": {"task_id": "research-task-001", "status": "accepted"},
        "run": {
            "run_id": "research-run-001",
            "task_id": "research-task-001",
            "status": "queued",
        },
        "downstream_status": "queued",
        "downstream_terminal": False,
    }
    assert list(run_validator.iter_errors(valid_run)) == []
    assert list(
        run_validator.iter_errors(
            {**valid_run, "run": {"task_id": "research-task-001", "status": "queued"}}
        )
    )


def test_postgres_operation_receipts_and_conclusion_survive_store_restart(
    request: pytest.FixtureRequest,
) -> None:
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    schema = f"agora_ws_ops2_{uuid.uuid4().hex[:12]}"
    import psycopg

    def cleanup_schema() -> None:
        with psycopg.connect(dsn) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    request.addfinalizer(cleanup_schema)
    workshop_id = f"ws-{uuid.uuid4().hex}"
    registry_id = f"registry-{uuid.uuid4().hex}"
    version_id = "wsv-lifecycle-final"
    digest = "b" * 64
    research_refs = {
        "research_task_id": "research-task-restart",
        "research_run_id": "research-run-restart",
        "workshop_version_id": version_id,
        "approval_decision_id": "approval-restart",
    }

    first_store = PostgresWorkshopStore(dsn=dsn, schema=schema)
    first_store.create_session(
        {
            "workshop_id": workshop_id,
            "tenant_id": "tenant-lifecycle",
            "user_id": "user-lifecycle",
            "strategy_id": "strategy-lifecycle",
            "active_strategy_spec_registry_id": registry_id,
            "status": "open",
        }
    )
    first_store.ensure_current_version_link(
        workshop_id=workshop_id,
        strategy_id="strategy-lifecycle",
        strategy_spec_registry_id=registry_id,
        document_sha256=digest,
    )

    admitted_research = first_store.admit_command(
        workshop_id=workshop_id,
        tenant_id="tenant-lifecycle",
        user_id="user-lifecycle",
        operation="dispatch_research",
        idempotency_key="restart-research",
        request_hash="c" * 64,
        expected_lock_version=1,
    )
    assert admitted_research["outcome"] == "admitted"
    completed_research = first_store.complete_command(
        workshop_id=workshop_id,
        tenant_id="tenant-lifecycle",
        user_id="user-lifecycle",
        operation="dispatch_research",
        idempotency_key="restart-research",
        request_hash="c" * 64,
        result={
            "run": {"run_id": "research-run-restart", "status": "queued"},
            "downstream_terminal": False,
        },
        canonical_refs=research_refs,
    )
    assert completed_research["outcome"] == "completed"

    admitted_conclude = first_store.admit_command(
        workshop_id=workshop_id,
        tenant_id="tenant-lifecycle",
        user_id="user-lifecycle",
        operation="conclude",
        idempotency_key="restart-conclude",
        request_hash="d" * 64,
        expected_lock_version=2,
    )
    assert admitted_conclude["outcome"] == "admitted"
    concluded = first_store.complete_command(
        workshop_id=workshop_id,
        tenant_id="tenant-lifecycle",
        user_id="user-lifecycle",
        operation="conclude",
        idempotency_key="restart-conclude",
        request_hash="d" * 64,
        result={"workshop": {"status": "concluded"}},
        canonical_refs={
            "workshop_version_id": version_id,
            "strategy_spec_registry_id": registry_id,
            "approval_decision_id": "approval-restart",
        },
        session_updates={
            "selected_version_id": version_id,
            "final_workshop_version_id": version_id,
            "final_strategy_spec_registry_id": registry_id,
            "status": "concluded",
            "concluded_at": "2026-07-22T20:00:00Z",
        },
        event={
            "event_id": "wsevt-conclude-restart",
            "actor_type": "operator",
            "event_type": "concluded",
            "redacted_summary": "Workshop concluded with approved final version",
            "payload_refs_json": {"final_workshop_version_id": version_id},
        },
    )
    assert concluded["outcome"] == "completed"

    # --- simulated BFF restart: a brand-new store over the same DSN ---
    restarted = PostgresWorkshopStore(dsn=dsn, schema=schema)

    research_receipt = restarted.get_command_receipt(
        workshop_id=workshop_id,
        tenant_id="tenant-lifecycle",
        user_id="user-lifecycle",
        operation="dispatch_research",
        idempotency_key="restart-research",
    )
    assert research_receipt is not None
    assert research_receipt["status"] == "completed"
    assert research_receipt["canonical_refs"] == research_refs
    assert research_receipt["result"]["run"]["run_id"] == "research-run-restart"

    session = restarted.get_session(workshop_id)
    assert session is not None
    assert session["status"] == "concluded"
    assert session["final_workshop_version_id"] == version_id
    assert session["final_strategy_spec_registry_id"] == registry_id
    assert session["concluded_at"] is not None
    events = restarted.list_events(workshop_id)
    assert [event["event_type"] for event in events] == ["concluded"]

    # Exact duplicate after restart replays the recorded downstream IDs
    # instead of admitting a second dispatch.
    replay = restarted.admit_command(
        workshop_id=workshop_id,
        tenant_id="tenant-lifecycle",
        user_id="user-lifecycle",
        operation="dispatch_research",
        idempotency_key="restart-research",
        request_hash="c" * 64,
        expected_lock_version=3,
    )
    assert replay["outcome"] == "replay"
    assert replay["receipt"]["canonical_refs"] == research_refs

    conclude_replay = restarted.admit_command(
        workshop_id=workshop_id,
        tenant_id="tenant-lifecycle",
        user_id="user-lifecycle",
        operation="conclude",
        idempotency_key="restart-conclude",
        request_hash="d" * 64,
        expected_lock_version=3,
    )
    assert conclude_replay["outcome"] == "replay"
    assert conclude_replay["receipt"]["status"] == "completed"

    # A fresh command key against the terminal workshop is refused without
    # creating a new receipt.
    new_command = restarted.admit_command(
        workshop_id=workshop_id,
        tenant_id="tenant-lifecycle",
        user_id="user-lifecycle",
        operation="dispatch_research",
        idempotency_key="post-conclude-research",
        request_hash="e" * 64,
        expected_lock_version=3,
    )
    assert new_command["outcome"] == "terminal"
    assert new_command["workshop_status"] == "concluded"
