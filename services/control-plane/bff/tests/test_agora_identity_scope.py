"""AG-BE-ID-001: Agora user-private identity scope and servant policy tests."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.agora.identity.scope import (
    AgoraScopeResolutionError,
    filter_agora_user_records,
    resolve_agora_user_scope,
)


_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCHEMA_ROOT = _REPO_ROOT / "services" / "control-plane" / "specs" / "agora"


def _now() -> str:
    return "2026-06-20T00:00:00Z"


def _identity(**overrides):
    payload = {
        "operator_id": "user-alpha",
        "roles": ["operator"],
        "claims": {
            "tenant_id": "tenant-alpha",
            "allowed_tenants": ["tenant-alpha", "tenant-beta"],
        },
        "token_kind": "stub",
    }
    payload.update(overrides)
    return OperatorIdentity(**payload)


def test_resolve_agora_user_scope_requires_backend_predicate() -> None:
    scope = resolve_agora_user_scope(_identity(), utc_now=_now)

    assert scope.tenant_id == "tenant-alpha"
    assert scope.user_id == "user-alpha"
    assert scope.operator_id == "user-alpha"
    assert scope.read_predicate.tenant_id == "tenant-alpha"
    assert scope.read_predicate.user_id == "user-alpha"
    assert scope.read_predicate.required_fields == ["tenant_id", "user_id"]
    assert scope.read_predicate.fail_closed is True
    assert scope.servant_policy.persona_class == "agora_servant"
    assert scope.servant_policy.owner_scope == "user_private"
    assert scope.servant_policy.memory_scope == "private_user"
    assert scope.servant_policy.execution_authority == "none"
    assert all(cap.startswith("agora.") for cap in scope.granted_capabilities)


def test_resolve_agora_user_scope_rejects_cross_tenant() -> None:
    with pytest.raises(AgoraScopeResolutionError, match="Tenant access denied"):
        resolve_agora_user_scope(
            _identity(claims={"tenant_id": "tenant-alpha", "allowed_tenants": ["tenant-alpha"]}),
            utc_now=_now,
            requested_tenant_id="tenant-beta",
        )


def test_filter_agora_user_records_fails_closed_without_tenant_user_predicate() -> None:
    scope = resolve_agora_user_scope(_identity(), utc_now=_now)
    records = [
        {"id": "match", "tenant_id": "tenant-alpha", "user_id": "user-alpha"},
        {"id": "wrong-user", "tenant_id": "tenant-alpha", "user_id": "user-beta"},
        {"id": "wrong-tenant", "tenant_id": "tenant-beta", "user_id": "user-alpha"},
        {"id": "missing-user", "tenant_id": "tenant-alpha"},
        {"id": "missing-tenant", "user_id": "user-alpha"},
        {
            "id": "nested-match",
            "metadata": {"tenant_id": "tenant-alpha"},
            "owner_ref": {"user_id": "user-alpha"},
        },
    ]

    filtered = filter_agora_user_records(records, scope)

    assert [record["id"] for record in filtered] == ["match", "nested-match"]


def test_scope_capabilities_are_agora_only() -> None:
    identity = _identity(
        roles=["viewer"],
        claims={
            "tenant_id": "tenant-alpha",
            "allowed_tenants": ["tenant-alpha"],
            "capabilities": [
                "agora.identity.v1",
                "runtime_binding.write",
                "broker.order",
                "capital.allocate",
            ],
        },
    )
    scope = resolve_agora_user_scope(identity, utc_now=_now)

    assert scope.granted_capabilities == ["agora.identity.v1"]


def _load_schema(name: str) -> dict:
    return json.loads((_SCHEMA_ROOT / name).read_text(encoding="utf-8"))


def test_agora_user_scope_schema_requires_tenant_user_predicate() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = _load_schema("agora_user_scope.schema.json")
    payload = resolve_agora_user_scope(_identity(), utc_now=_now).model_dump()

    jsonschema.Draft7Validator(schema, format_checker=jsonschema.FormatChecker()).validate(payload)

    invalid = dict(payload)
    invalid.pop("user_id")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(schema).validate(invalid)


def test_servant_profile_schema_enforces_user_private_policy() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = _load_schema("servant_profile.schema.json")
    payload = {
        "spec_version": "1.0",
        "persona_id": "persona-agora-user-alpha",
        "display_name": "Agora Servant",
        "status": "active",
        "tenant_id": "tenant-alpha",
        "agora_user_id": "user-alpha",
        "persona_class": "agora_servant",
        "owner_scope": "user_private",
        "visibility_scope": "private",
        "memory_scope": "private_user",
        "capability_summary": {
            "can_ask": True,
            "can_research": True,
            "can_workshop": True,
            "allowed_agora_capabilities": ["agora.identity.v1", "agora.session.v1"],
        },
        "policy": {
            "persona_class": "agora_servant",
            "owner_scope": "user_private",
            "visibility_scope": "private",
            "memory_scope": "private_user",
            "persona_registry_backed": True,
            "execution_authority": "none",
            "prohibited_authority": ["runtime_binding", "broker_order", "capital_binding"],
        },
    }

    jsonschema.Draft7Validator(schema, format_checker=jsonschema.FormatChecker()).validate(payload)

    invalid = dict(payload)
    invalid["capability_summary"] = {
        **payload["capability_summary"],
        "allowed_agora_capabilities": ["broker.order"],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(schema).validate(invalid)
