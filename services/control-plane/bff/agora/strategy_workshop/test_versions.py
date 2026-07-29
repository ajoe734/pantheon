from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft7Validator, FormatChecker

from .store import (
    MemoryWorkshopStore,
    PostgresWorkshopStore,
    WorkshopVersionProjectionConflict,
)


WORKSHOP_ID = "ws-legacy-version-projection"
STRATEGY_ID = "strategy-legacy-version-projection"
REGISTRY_ID = "registry-legacy-version-projection"
DIGEST = "a" * 64
CREATED_AT = "2026-07-22T12:00:00Z"
ROOT = Path(__file__).resolve().parents[5]
AGORA_SPECS = ROOT / "services/control-plane/specs/agora"
VERSION_SCHEMA = AGORA_SPECS / "v11/workshop_version_operations.schema.json"
VERSION_MANIFEST = AGORA_SPECS / "v11/capability_manifest_v1_10.json"
VERSION_BUNDLE = AGORA_SPECS / "bundle_index.v1_10.json"
VERSION_OPENAPI = ROOT / "services/control-plane/openapi/agora_v1_10.openapi.yaml"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _legacy_session() -> dict[str, str]:
    return {
        "workshop_id": WORKSHOP_ID,
        "tenant_id": "tenant-version-projection",
        "user_id": "user-version-projection",
        "strategy_id": STRATEGY_ID,
        "active_strategy_spec_registry_id": REGISTRY_ID,
        "status": "open",
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
    }


def test_memory_legacy_backfill_is_deterministic_and_etag_stable() -> None:
    store = MemoryWorkshopStore()
    before = store.create_session(_legacy_session())

    first = store.ensure_current_version_link(
        workshop_id=WORKSHOP_ID,
        strategy_id=STRATEGY_ID,
        strategy_spec_registry_id=REGISTRY_ID,
        document_sha256=DIGEST,
    )
    replay = store.ensure_current_version_link(
        workshop_id=WORKSHOP_ID,
        strategy_id=STRATEGY_ID,
        strategy_spec_registry_id=REGISTRY_ID,
        document_sha256=DIGEST,
    )

    assert replay == first
    assert first["workshop_version_id"].startswith("wsv-legacy-")
    assert first["parent_workshop_version_id"] is None
    assert first["sequence_no"] == 1
    assert first["document_sha256"] == DIGEST
    assert first["created_by"] == before["user_id"]
    assert first["created_at"] == before["created_at"]
    assert store.list_version_links(WORKSHOP_ID) == [first]

    after = store.get_session(WORKSHOP_ID)
    assert after is not None
    assert after["selected_version_id"] == first["workshop_version_id"]
    assert after["active_workshop_version_id"] == first["workshop_version_id"]
    for stable_field in (
        "workshop_id",
        "tenant_id",
        "user_id",
        "strategy_id",
        "active_strategy_spec_registry_id",
        "status",
        "lock_version",
        "created_at",
        "updated_at",
    ):
        assert after[stable_field] == before[stable_field]


def test_memory_version_digest_is_write_once() -> None:
    store = MemoryWorkshopStore()
    store.create_session(_legacy_session())
    link = store.ensure_current_version_link(
        workshop_id=WORKSHOP_ID,
        strategy_id=STRATEGY_ID,
        strategy_spec_registry_id=REGISTRY_ID,
        document_sha256=DIGEST,
    )

    with pytest.raises(
        WorkshopVersionProjectionConflict,
        match="immutable version",
    ):
        store.ensure_version_link_digest(
            workshop_id=WORKSHOP_ID,
            workshop_version_id=link["workshop_version_id"],
            strategy_id=STRATEGY_ID,
            document_sha256="b" * 64,
        )


def test_v1_10_contract_bundle_is_additive_typed_and_hash_locked() -> None:
    schema = json.loads(VERSION_SCHEMA.read_text(encoding="utf-8"))
    manifest = json.loads(VERSION_MANIFEST.read_text(encoding="utf-8"))
    bundle = json.loads(VERSION_BUNDLE.read_text(encoding="utf-8"))
    openapi = yaml.safe_load(VERSION_OPENAPI.read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)

    assert bundle["extends"] == {
        "bundle_path": "services/control-plane/specs/agora/bundle_index.v1_9.json",
        "bundle_version": "1.9",
        "bundle_index_sha256": _sha256(AGORA_SPECS / "bundle_index.v1_9.json"),
    }
    for relative_path, expected_hash in bundle["files"].items():
        assert _sha256(ROOT / "services/control-plane" / relative_path) == expected_hash
    assert bundle["openapi"] == {
        "path": "services/control-plane/openapi/agora_v1_10.openapi.yaml",
        "sha256": _sha256(VERSION_OPENAPI),
    }
    assert bundle["required_definition_checksums"] == manifest[
        "required_definition_checksums"
    ]
    assert set(schema["definitions"]) <= set(bundle["required_definition_checksums"])
    for name in schema["definitions"]:
        expected_hash = bundle["required_definition_checksums"][name]
        actual_hash = hashlib.sha256(
            _stable_json(schema["definitions"][name]).encode("utf-8")
        ).hexdigest()
        assert actual_hash == expected_hash, name

    expected_routes = {
        "GET /bff/agora/workshops/{workshop_id}/versions",
        "POST /bff/agora/workshops/{workshop_id}/versions",
        "POST /bff/agora/workshops/{workshop_id}/versions/{version_id}/select",
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
    assert openapi_routes == manifest_routes
    assert expected_routes <= openapi_routes
    assert openapi["info"]["x-extends-contract"].endswith("bundle_index.v1_9.json")
    assert openapi["info"]["x-implementation-status"] == "implemented"
    create_parameters = openapi["paths"][
        "/bff/agora/workshops/{workshop_id}/versions"
    ]["post"]["parameters"]
    select_parameters = openapi["paths"][
        "/bff/agora/workshops/{workshop_id}/versions/{version_id}/select"
    ]["post"]["parameters"]
    for parameters in (create_parameters, select_parameters):
        refs = {parameter["$ref"] for parameter in parameters}
        assert "#/components/parameters/IfMatch" in refs
        assert "#/components/parameters/IdempotencyKey" in refs

    link_validator = Draft7Validator(
        schema["definitions"]["WorkshopVersionLink"],
        format_checker=FormatChecker(),
    )
    valid_link = {
        "workshop_version_id": "wsv-contract",
        "workshop_id": "ws-contract",
        "strategy_id": "strategy-contract",
        "strategy_spec_registry_id": "registry-contract",
        "parent_workshop_version_id": None,
        "source_event_id": None,
        "sequence_no": 1,
        "document_sha256": "f" * 64,
        "created_by": "operator-contract",
        "created_at": CREATED_AT,
    }
    assert list(link_validator.iter_errors(valid_link)) == []
    assert list(
        link_validator.iter_errors({**valid_link, "document_sha256": "mutable"})
    )


def test_postgres_versions_and_selection_survive_store_restart(
    request: pytest.FixtureRequest,
) -> None:
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    schema = f"agora_ws_ops_{uuid.uuid4().hex[:12]}"
    import psycopg

    def cleanup_schema() -> None:
        with psycopg.connect(dsn) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    request.addfinalizer(cleanup_schema)
    workshop_id = f"ws-{uuid.uuid4().hex}"
    base_registry_id = f"registry-{uuid.uuid4().hex}"
    next_registry_id = f"registry-{uuid.uuid4().hex}"
    base_digest = "c" * 64
    next_digest = "d" * 64

    first_store = PostgresWorkshopStore(dsn=dsn, schema=schema)
    first_store.create_session(
        {
            "workshop_id": workshop_id,
            "tenant_id": "tenant-restart",
            "user_id": "user-restart",
            "strategy_id": "strategy-restart",
            "active_strategy_spec_registry_id": base_registry_id,
            "status": "open",
        }
    )
    base = first_store.ensure_current_version_link(
        workshop_id=workshop_id,
        strategy_id="strategy-restart",
        strategy_spec_registry_id=base_registry_id,
        document_sha256=base_digest,
    )
    admitted = first_store.admit_command(
        workshop_id=workshop_id,
        tenant_id="tenant-restart",
        user_id="user-restart",
        operation="create_version",
        idempotency_key="restart-version-create",
        request_hash="e" * 64,
        expected_lock_version=1,
    )
    assert admitted["outcome"] == "admitted"
    completed = first_store.complete_command(
        workshop_id=workshop_id,
        tenant_id="tenant-restart",
        user_id="user-restart",
        operation="create_version",
        idempotency_key="restart-version-create",
        request_hash="e" * 64,
        result={"strategy_spec_registry_id": next_registry_id},
        version_link={
            "workshop_version_id": "wsv-restart-next",
            "strategy_id": "strategy-restart",
            "strategy_spec_registry_id": next_registry_id,
            "parent_workshop_version_id": base["workshop_version_id"],
            "sequence_no": 2,
            "document_sha256": next_digest,
            "created_by": "user-restart",
        },
        session_updates={
            "active_strategy_spec_registry_id": next_registry_id,
            "active_workshop_version_id": "wsv-restart-next",
        },
    )
    assert completed["outcome"] == "completed"

    restarted_store = PostgresWorkshopStore(dsn=dsn, schema=schema)
    assert [
        row["document_sha256"]
        for row in restarted_store.list_version_links(workshop_id)
    ] == [base_digest, next_digest]
    restarted_session = restarted_store.get_session(workshop_id)
    assert restarted_session is not None
    assert restarted_session["selected_version_id"] == "wsv-restart-next"
    assert restarted_session["active_workshop_version_id"] == "wsv-restart-next"
    assert restarted_session["active_strategy_spec_registry_id"] == next_registry_id
