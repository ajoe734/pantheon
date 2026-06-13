from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import main as bff_main
from command_queue import CommandStore
from read_store import ReadSurfaceStore
from services.runtime_auth_inbound import encode_jwt_hs256


OPERATOR_HEADERS = {"Authorization": "Bearer dry-run-op:operator,approver,reviewer,admin"}


def _seed_read_store(path: Path) -> ReadSurfaceStore:
    path.write_text(
        json.dumps(
            {
                "agora_signals": {
                    "sig-dry-seed": {
                        "id": "sig-dry-seed",
                        "signal_id": "sig-dry-seed",
                        "title": "Seed signal",
                        "body": "Existing signal for feedback dry-run.",
                        "reviewStatus": "pending_trader_review",
                    }
                },
                "agora_sessions": {
                    "sess-dry-seed": {
                        "id": "sess-dry-seed",
                        "sessionId": "sess-dry-seed",
                        "title": "Seed session",
                        "status": "active",
                        "messages": [],
                    }
                },
                "decision_journal_entries": {},
                "research_notes": {},
                "insight_cards": {},
                "agora_training_examples": {},
                "capital_pools": {},
                "ranking_formulas": {},
                "rebalances": {},
                "runtime_bindings": {},
                "personas": {},
                "strategy_specs": {},
            }
        ),
        encoding="utf-8",
    )
    return ReadSurfaceStore(str(path), allow_local_snapshot_fallback=False)


@contextmanager
def _isolated_bff() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        original_strategy_overlay = dict(bff_main._STRATEGY_BFF_OVERLAY)
        original_persona_overlay = dict(bff_main._PERSONA_BFF_OVERLAY)
        original_skill_registry = dict(bff_main._SKILL_REGISTRY)
        original_strategy_persona_idem = dict(bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY)
        original_capital_idem = dict(bff_main._CAPITAL_BFF_IDEMPOTENCY)
        original_skills_idem = dict(bff_main._SKILLS_BFF_IDEMPOTENCY)
        original_agora_idem = dict(bff_main._AGORA_CORE_BFF_IDEMPOTENCY)
        original_gov_idem = dict(bff_main._GOV_BFF_IDEMPOTENCY)
        original_final_idem = dict(bff_main._FINAL_CONTRACT_IDEMPOTENCY)
        original_sse_buffers = {
            key: deque(value, maxlen=value.maxlen)
            for key, value in bff_main._sse_buffers.items()
        }
        try:
            bff_main.read_store = _seed_read_store(Path(td) / "read_surfaces.json")
            bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
            bff_main._STRATEGY_BFF_OVERLAY.clear()
            bff_main._PERSONA_BFF_OVERLAY.clear()
            bff_main._SKILL_REGISTRY.clear()
            bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
            bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
            bff_main._SKILLS_BFF_IDEMPOTENCY.clear()
            bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()
            bff_main._GOV_BFF_IDEMPOTENCY.clear()
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            for buffer in bff_main._sse_buffers.values():
                buffer.clear()
            yield TestClient(bff_main.app, raise_server_exceptions=False)
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store
            bff_main._STRATEGY_BFF_OVERLAY.clear()
            bff_main._STRATEGY_BFF_OVERLAY.update(original_strategy_overlay)
            bff_main._PERSONA_BFF_OVERLAY.clear()
            bff_main._PERSONA_BFF_OVERLAY.update(original_persona_overlay)
            bff_main._SKILL_REGISTRY.clear()
            bff_main._SKILL_REGISTRY.update(original_skill_registry)
            bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
            bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.update(original_strategy_persona_idem)
            bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
            bff_main._CAPITAL_BFF_IDEMPOTENCY.update(original_capital_idem)
            bff_main._SKILLS_BFF_IDEMPOTENCY.clear()
            bff_main._SKILLS_BFF_IDEMPOTENCY.update(original_skills_idem)
            bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()
            bff_main._AGORA_CORE_BFF_IDEMPOTENCY.update(original_agora_idem)
            bff_main._GOV_BFF_IDEMPOTENCY.clear()
            bff_main._GOV_BFF_IDEMPOTENCY.update(original_gov_idem)
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.update(original_final_idem)
            for key, original in original_sse_buffers.items():
                bff_main._sse_buffers[key].clear()
                bff_main._sse_buffers[key].extend(original)


def _dry_headers(key: str, *, auth: dict[str, str] | None = None) -> dict[str, str]:
    return {
        **(auth or OPERATOR_HEADERS),
        "Idempotency-Key": key,
        "X-Dry-Run": "1",
    }


def _assert_dry_run(response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"]["dryRun"] is True
    assert body["meta"]["durable"] is False
    assert body["meta"]["liveCapitalSideEffects"] is False
    return body


def _error_code(response) -> str:
    body = response.json()
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        return str(detail["error"].get("code") or "")
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return str(body["error"].get("code") or "")
    return ""


def test_dry_run_create_routes_do_not_persist_to_read_surfaces_or_caches() -> None:
    marker = "dry-run-marker-001"
    with _isolated_bff() as client:
        strategy = _assert_dry_run(client.post(
            "/bff/strategies",
            headers=_dry_headers("dry-strategy-001"),
            json={"name": marker, "alpha": "preview"},
        ))
        strategy_id = strategy["data"]["id"]
        assert client.get(f"/bff/strategies/{strategy_id}", headers=OPERATOR_HEADERS).status_code == 404
        assert strategy_id not in bff_main._STRATEGY_BFF_OVERLAY
        assert bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY == {}

        persona = _assert_dry_run(client.post(
            "/bff/personas",
            headers=_dry_headers("dry-persona-001"),
            json={"name": marker, "archetype": "generalist"},
        ))
        persona_id = persona["data"]["id"]
        assert client.get(f"/bff/personas/{persona_id}", headers=OPERATOR_HEADERS).status_code == 404
        assert persona_id not in bff_main._PERSONA_BFF_OVERLAY

        pool = _assert_dry_run(client.post(
            "/bff/capital-pools",
            headers=_dry_headers("dry-pool-001"),
            json={"name": marker, "risk_policy_ref": "rp-dry"},
        ))
        pool_list = client.get("/bff/capital-pools", headers=OPERATOR_HEADERS)
        assert pool_list.status_code == 200, pool_list.text
        assert all((item.get("id") or item.get("pool_id")) != pool["data"]["id"] for item in pool_list.json()["data"])
        assert bff_main._CAPITAL_BFF_IDEMPOTENCY == {}

        formula = _assert_dry_run(client.post(
            "/bff/ranking-formulas",
            headers=_dry_headers("dry-ranking-formula-001"),
            json={"name": marker, "description": "preview formula"},
        ))
        formula_id = formula["data"]["id"]
        formula_list = client.get("/bff/ranking-formulas", headers=OPERATOR_HEADERS)
        assert formula_list.status_code == 200, formula_list.text
        assert all((item.get("id") or item.get("formula_id")) != formula_id for item in formula_list.json()["data"])
        assert bff_main._CAPITAL_BFF_IDEMPOTENCY == {}

        skill = _assert_dry_run(client.post(
            "/bff/skills",
            headers=_dry_headers("dry-skill-001"),
            json={"name": marker},
        ))
        assert client.get(f"/bff/skills/{skill['data']['id']}", headers=OPERATOR_HEADERS).status_code == 404
        assert skill["data"]["id"] not in bff_main._SKILL_REGISTRY
        assert bff_main._SKILLS_BFF_IDEMPOTENCY == {}

        for path, key, payload, list_path, id_field in (
            ("/bff/agora/journal", "dry-journal-001", {"title": marker, "body": "preview"}, "/bff/agora/journal", "id"),
            ("/bff/agora/notes", "dry-note-001", {"title": marker, "body": "preview"}, "/bff/agora/notes", "id"),
            ("/bff/agora/insights", "dry-insight-001", {"summary": marker}, "/bff/agora/insights", "id"),
            ("/bff/agora/sessions", "dry-session-001", {"title": marker}, "/bff/agora/sessions", "id"),
            (
                "/bff/agora/training-examples",
                "dry-training-001",
                {"input": {"text": marker}, "expected": {"label": "preview"}},
                "/bff/agora/training-examples",
                "trainingExampleId",
            ),
        ):
            created = _assert_dry_run(client.post(path, headers=_dry_headers(key), json=payload))
            created_id = created["data"][id_field]
            listed = client.get(list_path, headers=OPERATOR_HEADERS)
            assert listed.status_code == 200, listed.text
            assert all((item.get(id_field) or item.get("id")) != created_id for item in listed.json().get("items", []))
        assert bff_main._AGORA_CORE_BFF_IDEMPOTENCY == {}


def test_dry_run_command_routes_do_not_write_command_store_or_sse() -> None:
    with _isolated_bff() as client:
        deployment = _assert_dry_run(client.post(
            "/bff/deployments",
            headers=_dry_headers("dry-deploy-001"),
            json={"id": "deployment-dry", "name": "deployment preview"},
        ))
        assert deployment["data"]["command"] == "CreateDeployment"

        intervention = _assert_dry_run(client.post(
            "/bff/v5/interventions/intervention-dry/claim",
            headers=_dry_headers("dry-intervention-001"),
            json={"reason": "preview"},
        ))
        assert intervention["data"]["command"] == "V5InterventionAction"

        rebalance = _assert_dry_run(client.post(
            "/bff/rebalances",
            headers=_dry_headers("dry-rebalance-001"),
            json={"capital_pool_id": "pool-dry", "reason": "preview"},
        ))
        assert rebalance["data"]["command"] == "RebalanceAction"
        assert client.get(f"/bff/rebalances/{rebalance['rebalance_id']}", headers=OPERATOR_HEADERS).status_code == 404

        runtime = _assert_dry_run(client.post(
            "/bff/runtimes",
            headers=_dry_headers("dry-runtime-001"),
            json={
                "name": "runtime preview",
                "persona_id": "persona-dry",
                "binding_id": "binding-dry",
                "deployment_plan_id": "plan-dry",
                "runtime_kind": "paper",
            },
        ))
        assert runtime["data"]["id"].startswith("runtime-")
        assert client.get(f"/bff/runtimes/{runtime['data']['id']}", headers=OPERATOR_HEADERS).status_code != 200

        assert bff_main.command_store._get_all_commands() == []
        assert all(len(buffer) == 0 for buffer in bff_main._sse_buffers.values())


def _make_jwt(*, sub: str, roles: list[str]) -> str:
    now = int(time.time())
    claims = {
        "sub": sub,
        "iss": "pantheon-bff-test",
        "aud": "bff-operators",
        "iat": now,
        "exp": now + 3600,
        "roles": roles,
    }
    return encode_jwt_hs256(claims, secret="test-bff-secret-1234")


def test_strict_bearer_jwt_rbac_matrix_viewer_reads_operator_writes() -> None:
    env = {
        "PANTHEON_BFF_AUTH_STUB": "",
        "PANTHEON_BFF_AUTH_MODE": "strict",
        "PANTHEON_BFF_JWT_SECRET": "test-bff-secret-1234",
        "PANTHEON_BFF_JWT_ISSUER": "pantheon-bff-test",
        "PANTHEON_BFF_JWT_AUDIENCE": "bff-operators",
        "PANTHEON_BFF_MFA_REQUIRED": "false",
    }
    viewer_headers = {"Authorization": f"Bearer {_make_jwt(sub='viewer-1', roles=['viewer'])}"}
    operator_headers = {"Authorization": f"Bearer {_make_jwt(sub='operator-1', roles=['operator'])}"}

    with patch.dict(os.environ, env, clear=False):
        with _isolated_bff() as client:
            read_resp = client.get("/bff/strategies", headers=viewer_headers)
            assert read_resp.status_code == 200, read_resp.text
            formula_read = client.get("/bff/ranking-formulas", headers=viewer_headers)
            assert formula_read.status_code == 200, formula_read.text

            denied_write = client.post(
                "/bff/strategies",
                headers=_dry_headers("viewer-write-denied", auth=viewer_headers),
                json={"name": "viewer must not write"},
            )
            assert denied_write.status_code == 403, denied_write.text
            assert _error_code(denied_write) == "FORBIDDEN"

            allowed_write = _assert_dry_run(client.post(
                "/bff/strategies",
                headers=_dry_headers("operator-write-allowed", auth=operator_headers),
                json={"name": "operator preview"},
            ))
            assert allowed_write["data"]["name"] == "operator preview"

            denied_formula_write = client.post(
                "/bff/ranking-formulas",
                headers=_dry_headers("viewer-formula-write-denied", auth=viewer_headers),
                json={"name": "viewer formula write must not pass"},
            )
            assert denied_formula_write.status_code == 403, denied_formula_write.text
            assert _error_code(denied_formula_write) == "FORBIDDEN"

            allowed_formula_write = _assert_dry_run(client.post(
                "/bff/ranking-formulas",
                headers=_dry_headers("operator-formula-write-allowed", auth=operator_headers),
                json={"name": "operator formula preview"},
            ))
            assert allowed_formula_write["data"]["name"] == "operator formula preview"
