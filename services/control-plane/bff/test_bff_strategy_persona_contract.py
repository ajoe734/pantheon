"""
Contract tests for BFF-LUV-GAP-002: strategy and persona BFF
compatibility surfaces consumed by execute-plans.

Covers:
  * list / detail / patch happy paths and overlay round-trip,
  * sub-resource routes (specs, experiments, artifacts, lineage, audit,
    route-policy, activity, evaluations, memory),
  * action endpoints — happy path and precondition errors,
  * dry-run and test-prompt stubs,
  * /bff/search and /bff/types compatibility surface,
  * 404 behavior for missing strategy / persona ids.
"""
from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from action_catalog import get_catalog_entry
from models import CommandType
from read_store import ReadSurfaceStore

OPERATOR_TOKEN = "Bearer op-2:operator"
HEADERS = {"Authorization": OPERATOR_TOKEN}


def _fresh_client(td: str) -> TestClient:
    bff_main.read_store = ReadSurfaceStore(
        os.path.join(td, "read_surfaces.json"),
        allow_local_snapshot_fallback=True,
    )
    bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
    bff_main._STRATEGY_BFF_OVERLAY.clear()
    bff_main._PERSONA_BFF_OVERLAY.clear()
    return TestClient(bff_main.app)


# ---------------------------------------------------------------------------
# /bff/strategies
# ---------------------------------------------------------------------------


def test_bff_strategies_list_returns_200_and_dto_shape() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/strategies", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body and "page_info" in body
            for item in body["data"]:
                assert "id" in item
                assert "name" in item
                assert "state" in item
                assert "risk" in item
                assert "personaIds" in item
                assert "capitalPoolId" in item
        finally:
            bff_main.read_store = original


def test_bff_strategies_create_requires_idempotency_and_name() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            missing_key = client.post(
                "/bff/strategies", json={"name": "Alpha"}, headers=HEADERS,
            )
            assert missing_key.status_code == 400, missing_key.text
            assert missing_key.json()["detail"]["error"]["code"] == "INVALID_PARAMS"

            missing_name = client.post(
                "/bff/strategies",
                json={},
                headers={**HEADERS, "Idempotency-Key": "create-strategy-002"},
            )
            assert missing_name.status_code == 422, missing_name.text
            assert missing_name.json()["detail"]["error"]["code"] == "INVALID_PARAMS"
        finally:
            bff_main.read_store = original


def test_bff_strategies_create_then_get_then_patch_round_trip() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            create = client.post(
                "/bff/strategies",
                json={"name": "Mean Reversion", "alpha": "alpha-meanrev"},
                headers={**HEADERS, "Idempotency-Key": "create-strategy-001"},
            )
            assert create.status_code == 201, create.text
            strategy_id = create.json()["data"]["id"]

            get_resp = client.get(f"/bff/strategies/{strategy_id}", headers=HEADERS)
            assert get_resp.status_code == 200, get_resp.text
            assert get_resp.json()["data"]["name"] == "Mean Reversion"

            patch_resp = client.patch(
                f"/bff/strategies/{strategy_id}",
                json={"name": "Mean Reversion v2", "state": "review"},
                headers={**HEADERS, "Idempotency-Key": "patch-strategy-001"},
            )
            assert patch_resp.status_code == 200, patch_resp.text
            assert patch_resp.json()["data"]["name"] == "Mean Reversion v2"
            assert patch_resp.json()["data"]["state"] == "review"
        finally:
            bff_main.read_store = original


def test_bff_strategies_subresources_return_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            create = client.post(
                "/bff/strategies",
                json={"name": "Carry"},
                headers={**HEADERS, "Idempotency-Key": "create-strategy-003"},
            )
            strategy_id = create.json()["data"]["id"]

            for subpath in ("specs", "experiments", "artifacts", "lineage", "audit"):
                resp = client.get(f"/bff/strategies/{strategy_id}/{subpath}", headers=HEADERS)
                assert resp.status_code == 200, f"{subpath}: {resp.text}"
                body = resp.json()
                assert "data" in body or "items" in body
                assert "meta" in body

            specs_post = client.post(
                f"/bff/strategies/{strategy_id}/specs",
                json={"version": "1.0"},
                headers={**HEADERS, "Idempotency-Key": "spec-001"},
            )
            assert specs_post.status_code == 201, specs_post.text
            assert specs_post.json()["data"]["strategy_id"] == strategy_id
        finally:
            bff_main.read_store = original


def test_bff_strategies_actions_use_final_envelope_and_precondition() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            create = client.post(
                "/bff/strategies",
                json={"name": "Momentum"},
                headers={**HEADERS, "Idempotency-Key": "create-strategy-004"},
            )
            strategy_id = create.json()["data"]["id"]

            missing_key = client.post(
                f"/bff/strategies/{strategy_id}/actions/edit",
                json={},
                headers=HEADERS,
            )
            assert missing_key.status_code == 400, missing_key.text
            err = missing_key.json()["detail"]["error"]
            assert err["code"] == "INVALID_PARAMS"
            assert err["details"]["precondition_failed"] == "idempotency_key"

            ok = client.post(
                f"/bff/strategies/{strategy_id}/actions/edit",
                json={"reason": "operator review"},
                headers={**HEADERS, "Idempotency-Key": "strategy-action-001"},
            )
            assert ok.status_code == 202, ok.text
            assert get_catalog_entry(CommandType.STRATEGY_ACTION.value) is not None
            assert ok.json()["data"]["command"] == CommandType.STRATEGY_ACTION.value
            assert ok.json()["data"]["receipt"]["command"] == CommandType.STRATEGY_ACTION.value
        finally:
            bff_main.read_store = original


def test_bff_strategies_dry_run_returns_run_handle() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            create = client.post(
                "/bff/strategies",
                json={"name": "Carry"},
                headers={**HEADERS, "Idempotency-Key": "create-strategy-005"},
            )
            strategy_id = create.json()["data"]["id"]

            dry = client.post(
                f"/bff/strategies/{strategy_id}/dry-run",
                json={"params": {"window": "30d"}},
                headers={**HEADERS, "Idempotency-Key": "dry-run-001"},
            )
            assert dry.status_code == 202, dry.text
            assert dry.json()["data"]["strategy_id"] == strategy_id
            assert "run_id" in dry.json()["data"]
        finally:
            bff_main.read_store = original


def test_bff_strategies_404_for_unknown_id() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/strategies/strategy-does-not-exist", headers=HEADERS)
            assert resp.status_code == 404, resp.text
            assert resp.json()["detail"]["error"]["code"] == "OBJECT_NOT_FOUND"
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# /bff/personas
# ---------------------------------------------------------------------------


def test_bff_personas_list_returns_200_and_dto_shape() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body and "page_info" in body
            for item in body["data"]:
                assert "id" in item
                assert "name" in item
                assert "state" in item
                assert "archetype" in item
                assert "routedStrategies" in item
                assert "successRate" in item
        finally:
            bff_main.read_store = original


def test_bff_personas_create_requires_idempotency_and_name() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            missing_key = client.post(
                "/bff/personas", json={"name": "Quant"}, headers=HEADERS,
            )
            assert missing_key.status_code == 400, missing_key.text

            missing_name = client.post(
                "/bff/personas",
                json={},
                headers={**HEADERS, "Idempotency-Key": "create-persona-002"},
            )
            assert missing_name.status_code == 422, missing_name.text
        finally:
            bff_main.read_store = original


def test_bff_personas_create_then_subresources_round_trip() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            create = client.post(
                "/bff/personas",
                json={"name": "Macro Macro", "archetype": "macro"},
                headers={**HEADERS, "Idempotency-Key": "create-persona-001"},
            )
            assert create.status_code == 201, create.text
            persona_id = create.json()["data"]["id"]

            bff_main._PERSONA_BFF_OVERLAY.clear()
            bff_main.read_store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=True,
            )
            detail = client.get(f"/bff/personas/{persona_id}", headers=HEADERS)
            assert detail.status_code == 200, detail.text
            assert detail.json()["data"]["id"] == persona_id

            for subpath in ("route-policy", "activity", "evaluations", "memory", "audit"):
                resp = client.get(f"/bff/personas/{persona_id}/{subpath}", headers=HEADERS)
                assert resp.status_code == 200, f"{subpath}: {resp.text}"
                body = resp.json()
                assert "data" in body or "items" in body
                assert "meta" in body
        finally:
            bff_main.read_store = original


def test_bff_personas_actions_route_through_command_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            create = client.post(
                "/bff/personas",
                json={"name": "Risk Officer"},
                headers={**HEADERS, "Idempotency-Key": "create-persona-003"},
            )
            persona_id = create.json()["data"]["id"]

            precondition = client.post(
                f"/bff/personas/{persona_id}/actions/retire",
                json={},
                headers=HEADERS,
            )
            assert precondition.status_code == 400, precondition.text
            assert precondition.json()["detail"]["error"]["code"] == "INVALID_PARAMS"

            ok = client.post(
                f"/bff/personas/{persona_id}/actions/retire",
                json={"reason": "decommission"},
                headers={**HEADERS, "Idempotency-Key": "persona-action-001"},
            )
            assert ok.status_code == 202, ok.text
            assert get_catalog_entry(CommandType.PERSONA_ACTION.value) is not None
            assert ok.json()["data"]["command"] == CommandType.PERSONA_ACTION.value
            assert ok.json()["data"]["receipt"]["command"] == CommandType.PERSONA_ACTION.value
        finally:
            bff_main.read_store = original


def test_bff_personas_test_prompt_requires_prompt() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            create = client.post(
                "/bff/personas",
                json={"name": "Trader"},
                headers={**HEADERS, "Idempotency-Key": "create-persona-004"},
            )
            persona_id = create.json()["data"]["id"]

            empty = client.post(
                f"/bff/personas/{persona_id}/test-prompt",
                json={},
                headers={**HEADERS, "Idempotency-Key": "test-prompt-001"},
            )
            assert empty.status_code == 422, empty.text
            assert empty.json()["detail"]["error"]["details"]["precondition_failed"] == "prompt"

            ok = client.post(
                f"/bff/personas/{persona_id}/test-prompt",
                json={"prompt": "What's the macro view?"},
                headers={**HEADERS, "Idempotency-Key": "test-prompt-002"},
            )
            assert ok.status_code == 202, ok.text
            assert ok.json()["data"]["persona_id"] == persona_id
            assert ok.json()["data"]["prompt"] == "What's the macro view?"
        finally:
            bff_main.read_store = original


def test_bff_personas_404_for_unknown_id() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas/persona-does-not-exist", headers=HEADERS)
            assert resp.status_code == 404, resp.text
            assert resp.json()["detail"]["error"]["code"] == "OBJECT_NOT_FOUND"
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# Platform helpers — /bff/search and /bff/types
# ---------------------------------------------------------------------------


def test_bff_search_returns_results_for_overlay_strategy() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            client.post(
                "/bff/strategies",
                json={"name": "Liquidity Premium"},
                headers={**HEADERS, "Idempotency-Key": "create-strategy-search"},
            )
            resp = client.get("/bff/search?q=liquidity", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body
            assert any(r.get("type") == "strategy" for r in body["data"])
        finally:
            bff_main.read_store = original


def test_bff_types_compat_lists_canonical_entities() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/types", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["data"]["types_source"].endswith("types.ts")
            assert "Strategy" in body["data"]["exported_entities"]
            assert "Persona" in body["data"]["exported_entities"]
            assert "compatibility_decision" in body["data"]
        finally:
            bff_main.read_store = original
