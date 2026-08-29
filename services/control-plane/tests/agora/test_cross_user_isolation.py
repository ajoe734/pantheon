"""AG-TEST-ID-001: route-level cross-user and Management isolation.

These tests pin the §F3/§F4 isolation acceptance from the v1.3 design pack:
cross-user reads fail with CROSS_USER_ACCESS_FORBIDDEN, Agora-scoped tokens
cannot call /bff/management/*, and Management projections do not receive raw
private prompts.
"""
from __future__ import annotations

import os
import sys
import importlib.util
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[4]
BFF_ROOT = REPO_ROOT / "services" / "control-plane" / "bff"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(BFF_ROOT))

os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")
os.environ.setdefault("PANTHEON_BFF_AUTH_MODE", "permissive")


def _install_bff_agora_package() -> None:
    loaded = sys.modules.get("agora")
    loaded_file = str(getattr(loaded, "__file__", "") or "")
    if loaded is not None and "services/control-plane/tests/agora" in loaded_file:
        del sys.modules["agora"]
    if "agora" in sys.modules:
        return

    package_dir = BFF_ROOT / "agora"
    spec = importlib.util.spec_from_file_location(
        "agora",
        package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["agora"] = module
    spec.loader.exec_module(module)


_install_bff_agora_package()

import main as bff_main  # noqa: E402
from agora.dashboard import router as dashboard_router  # noqa: E402
from agora.servant import router as servant_router  # noqa: E402
from ports import ReadSurfacePorts  # noqa: E402


class CrossUserTestStore(ReadSurfacePorts):
    def __init__(self) -> None:
        super().__init__()
        self._personas: dict[str, dict[str, Any]] = {}
        self._capability_snapshots: dict[str, dict[str, Any]] = {}
        self._decision_journal: dict[str, dict[str, Any]] = {}

    def create_persona(self, **kwargs: Any) -> dict[str, Any]:
        pid = kwargs.get("persona_id") or f"persona-{len(self._personas)+1}"
        record = dict(kwargs)
        record["persona_id"] = pid
        record["id"] = pid
        self._personas[pid] = record
        return record

    def update_persona(self, persona_id: str, **kwargs: Any) -> Optional[dict[str, Any]]:
        record = self._personas.get(persona_id)
        if record is None:
            return None
        for k, v in kwargs.items():
            if v is not None:
                record[k] = v
        return dict(record)

    def get_persona(self, persona_id: str) -> Optional[dict[str, Any]]:
        return self._personas.get(persona_id)

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._personas.values())

    def upsert_persona_capability_snapshot(self, **kwargs: Any) -> dict[str, Any]:
        sid = kwargs.get("snapshot_id") or f"snap-{len(self._capability_snapshots)+1}"
        record = dict(kwargs)
        record["snapshot_id"] = sid
        record["id"] = sid
        self._capability_snapshots[sid] = record
        return record

    def get_persona_capability_snapshot(self, snapshot_id: str) -> Optional[dict[str, Any]]:
        return self._capability_snapshots.get(snapshot_id)

    def create_decision_journal_entry(self, **kwargs: Any) -> dict[str, Any]:
        eid = kwargs.get("entry_id") or f"entry-{len(self._decision_journal)+1}"
        record = dict(kwargs)
        record["id"] = eid
        record["entry_id"] = eid
        record["actorId"] = kwargs.get("actor_id")
        record["actor_id"] = kwargs.get("actor_id")
        record["createdBy"] = kwargs.get("actor_id")
        record["created_by"] = kwargs.get("actor_id")
        record["operator_id"] = kwargs.get("actor_id")
        record["userId"] = kwargs.get("actor_id")
        record["user_id"] = kwargs.get("actor_id")
        record["visibility"] = kwargs.get("visibility", "private")
        self._decision_journal[eid] = record
        return dict(record)

    def list_decision_journal_entries(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._decision_journal.values())

    def record_agora_audit_event(self, event: dict[str, Any]) -> dict[str, Any]:
        return dict(event)


def _auth(user: str, *, agora_caps: bool = False) -> dict[str, str]:
    if agora_caps:
        return {
            "Authorization": (
                f"Bearer {user}:operator::"
                "agora.identity.v1,agora.session.v1,agora.workshop.v1"
            )
        }
    return {"Authorization": f"Bearer {user}:operator"}


def _assert_reason(response, status_code: int, reason: str) -> dict[str, Any]:
    assert response.status_code == status_code, response.text
    body = response.json()
    assert body["error"]["details"]["reason"] == reason
    return body


class FakeOpenClawClient:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}

    def create_session(
        self,
        *,
        agent_id: str,
        session_type: str,
        operator_id: str,
        idempotency_key: str,
        context_bundle: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_id = f"oc-session-{len(self.sessions) + 1}"
        record = {
            "session_id": session_id,
            "agent_id": agent_id,
            "session_type": session_type,
            "state": "active",
            "operator_id": operator_id,
            "created_at": "2026-06-22T00:00:00Z",
            "updated_at": "2026-06-22T00:00:00Z",
            "idempotency_key": idempotency_key,
            "context_bundle": context_bundle or {},
            "upstream_session_id": f"upstream-{session_id}",
            "last_error": None,
        }
        self.sessions[session_id] = record
        return {"status": "ok", "session": record}

    def get_session(self, *, session_id: str, operator_id: str | None = None) -> dict[str, Any]:
        return {"status": "ok", "session": self.sessions[session_id]}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, FakeOpenClawClient]:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    original_store = bff_main.read_store
    bff_main.read_store = CrossUserTestStore()

    fake_openclaw = FakeOpenClawClient()
    monkeypatch.setattr(servant_router, "OpenClawOpsClient", lambda: fake_openclaw)
    monkeypatch.setattr(
        bff_main,
        "_ensure_agora_servant_openclaw_agent",
        lambda persona: {
            "status": "updated",
            "agent_id": persona["persona_id"],
            "model_id": f"openclaw/{persona['persona_id']}",
            "model": "openclaw/agora-servant",
            "workspace_ref": f"workspace://{persona['persona_id']}",
        },
    )
    servant_router._SERVANT_SESSION_EVENTS.clear()
    dashboard_router._recipe_identity.clear()
    dashboard_router._recipe_versions.clear()
    dashboard_router._seen_idempotency_keys.clear()

    monkeypatch.setattr(
        "agora.strategy_workshop.operations.WorkshopCanonicalOperations.get_strategy_spec",
        lambda _self, registry_id: {
            "entry": {
                "registry_id": registry_id,
                "strategy_id": f"strategy-family-for-{registry_id}",
                "version": "1.0.0",
                "artifact_state": "draft",
                "lineage": {"source_run_ids": ["test-source"]},
                "metadata": {
                    "strategy_spec": {
                        "spec_version": "1.0",
                        "strategy_id": f"strategy-family-for-{registry_id}",
                    },
                },
            },
            "deployment_stage": "none",
        },
    )

    try:
        yield TestClient(bff_main.app, raise_server_exceptions=False), fake_openclaw
    finally:
        bff_main.read_store = original_store


def test_cross_user_servant_strategy_and_journal_are_isolated(
    client: tuple[TestClient, FakeOpenClawClient],
) -> None:
    test_client, _fake_openclaw = client

    for user in ("user-alpha", "user-beta"):
        response = test_client.post(
            "/bff/agora/servant/ensure",
            headers={
                **_auth(user),
                "Idempotency-Key": f"ensure-{user}",
                "X-Request-Id": f"req-ensure-{user}",
            },
        )
        assert response.status_code == 200, response.text

    session = test_client.post(
        "/bff/agora/servant/sessions",
        headers={
            **_auth("user-alpha"),
            "Idempotency-Key": "create-alpha-session",
            "X-Request-Id": "req-create-alpha-session",
        },
        json={"intent": "Alpha private servant session"},
    )
    assert session.status_code == 201, session.text
    session_id = session.json()["data"]["session_id"]

    cross_servant = test_client.get(
        f"/bff/agora/servant/sessions/{session_id}",
        headers=_auth("user-beta"),
    )
    _assert_reason(cross_servant, 403, "CROSS_USER_ACCESS_FORBIDDEN")

    workshop = test_client.post(
        "/bff/agora/workshops",
        headers={
            **_auth("user-alpha"),
            "Idempotency-Key": "create-alpha-workshop",
        },
        json={
            "initial_message": "raw private winner-branch prompt",
            "strategy_spec_ref": "strategy-alpha-private",
        },
    )
    assert workshop.status_code == 201, workshop.text
    workshop_id = workshop.json()["data"]["workshop_id"]

    cross_workshop = test_client.get(
        f"/bff/agora/workshops/{workshop_id}",
        headers=_auth("user-beta"),
    )
    _assert_reason(cross_workshop, 403, "CROSS_USER_ACCESS_FORBIDDEN")

    recipe = test_client.post(
        "/bff/agora/strategies/strategy-alpha-private/dashboard-recipes/proposals",
        headers={
            **_auth("user-alpha"),
            "Idempotency-Key": "recipe-alpha-private",
        },
        json={
            "strategy_version_id": "strategy-alpha-private:v1",
            "workspace": "trading_room",
            "phase": "candidate_review",
        },
    )
    assert recipe.status_code == 201, recipe.text
    recipe_id = recipe.json()["data"]["recipe_id"]

    cross_recipe = test_client.get(
        f"/bff/agora/dashboard-recipes/{recipe_id}",
        headers=_auth("user-beta"),
    )
    _assert_reason(cross_recipe, 403, "CROSS_USER_ACCESS_FORBIDDEN")

    beta_recipe_list = test_client.get(
        "/bff/agora/strategies/strategy-alpha-private/dashboard-recipes",
        headers=_auth("user-beta"),
    )
    assert beta_recipe_list.status_code == 200, beta_recipe_list.text
    assert beta_recipe_list.json()["data"] == []

    journal = test_client.post(
        "/bff/agora/journal",
        headers={**_auth("user-alpha"), "Idempotency-Key": "journal-alpha-private"},
        json={
            "title": "Alpha private journal",
            "body": "private journal body",
            "visibility": "private",
        },
    )
    assert journal.status_code == 201, journal.text
    journal_id = journal.json()["data"]["id"]

    beta_journal = test_client.get("/bff/agora/journal", headers=_auth("user-beta"))
    assert beta_journal.status_code == 200, beta_journal.text
    assert journal_id not in {item["id"] for item in beta_journal.json()["items"]}

    cross_journal_patch = test_client.patch(
        f"/bff/agora/journal/{journal_id}",
        headers={
            **_auth("user-beta"),
            "Content-Type": "application/merge-patch+json",
            "Idempotency-Key": "patch-alpha-journal-from-beta",
        },
        json={"title": "cross-user mutation attempt"},
    )
    _assert_reason(cross_journal_patch, 403, "CROSS_USER_ACCESS_FORBIDDEN")


def test_agora_token_cannot_call_management_routes(
    client: tuple[TestClient, FakeOpenClawClient],
) -> None:
    test_client, _fake_openclaw = client

    response = test_client.get(
        "/bff/management/sentinel-pulse",
        headers=_auth("agora-only-user", agora_caps=True),
    )

    _assert_reason(response, 403, "AGORA_SCOPE_VIOLATION")


def test_management_projection_excludes_raw_prompt(
    client: tuple[TestClient, FakeOpenClawClient],
) -> None:
    test_client, _fake_openclaw = client
    raw_prompt = "raw private winner-branch prompt must not leave Agora"

    response = test_client.post(
        "/bff/agora/management/consult-bundle",
        headers=_auth("user-alpha"),
        json={
            "strategy_spec_draft_ref": "strategy-alpha-private:v1",
            "question": "Assess this strategy with redacted context only.",
            "symbols": ["AAPL"],
            "evidence_refs": ["evidence://redacted-alpha"],
            "data_cutoff": "2026-06-22T00:00:00Z",
            "required_output_schema": "ConsultMemo",
            "raw_prompt": raw_prompt,
            "private_content_ref": "privobj_alpha_secret",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    response_text = response.text
    manifest = body["context_bundle"]["privacy_manifest"]
    assert manifest["raw_prompt_included"] is False
    assert "raw_prompt" not in body["context_bundle"]
    assert "private_content_ref" not in body["context_bundle"]
    assert raw_prompt not in response_text
