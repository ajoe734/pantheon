"""The adapter routes persona_own_agent turns to the persona's own agent."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

_ADAPTER_DIR = Path(__file__).resolve().parents[2] / "openclaw-gateway-adapter"
if str(_ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_DIR))

from consultation_provider import (  # noqa: E402
    CONSULTATION_CONTRIBUTION_PATH,
    REQUIRED_OUTPUT,
    create_app,
)  # noqa: E402

from services.consultation.tests.test_current_provider_handoff import (  # noqa: E402
    _default_contribution,
)

TENANT = "t-own"
TOKEN = "tok"
ACTOR = "consultation-workflow-executor"


class _Fake:
    def __init__(self, *, answer_as: str, agent_id: str | None = None) -> None:
        self.answer_as = answer_as
        self.agent_id = agent_id
        self.invoked: list[str | None] = []
        self.structured = 0

    def invoke_structured(self, prompt: str, **kwargs: Any) -> Any:
        self.structured += 1
        raise AssertionError("persona turn must not use structured-extraction")

    def invoke(self, prompt: str, **kwargs: Any) -> Any:
        self.invoked.append(kwargs.get("agent_id"))
        contribution = _default_contribution(TENANT, "req-own")
        contribution.update(
            participant_type="persona",
            participant_ref=self.answer_as,
            author_type="persona",
        )
        return SimpleNamespace(
            status="completed",
            output={
                "agent_id": self.agent_id or kwargs.get("agent_id"),
                "json_events": [{"item": {"text": json.dumps(contribution)}}],
            },
        )


def _post(fake: _Fake, tmp_path: Path, persona: str) -> Any:
    app = create_app(
        provider=fake,
        provider_token=TOKEN,
        expected_service_actor=ACTOR,
        replay_path=tmp_path / "r.sqlite3",
    )
    return TestClient(app).post(
        CONSULTATION_CONTRIBUTION_PATH,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "X-Pantheon-Service-Actor": ACTOR,
            "X-Pantheon-Tenant-Id": TENANT,
            "Idempotency-Key": "k1",
        },
        json={
            "tenant_id": TENANT,
            "request_id": "req-own",
            "required_output": REQUIRED_OUTPUT,
            "request": {
                "tenant_id": TENANT,
                "request_id": "req-own",
                "participant": {
                    "persona_id": persona,
                    "role": "risk_reviewer",
                    "agent": "persona_own_agent",
                },
            },
        },
    )


def test_persona_turn_runs_on_the_personas_own_agent(tmp_path: Path) -> None:
    fake = _Fake(answer_as="persona-risk-officer")
    resp = _post(fake, tmp_path, "persona-risk-officer")
    assert resp.status_code == 200, resp.text
    assert fake.invoked == ["persona-risk-officer"]
    assert fake.structured == 0
    assert resp.json()["contribution"]["participant_ref"] == "persona-risk-officer"


def test_persona_turn_from_another_agent_is_refused(tmp_path: Path) -> None:
    fake = _Fake(answer_as="persona-risk-officer", agent_id="structured-extraction")
    assert _post(fake, tmp_path, "persona-risk-officer").status_code >= 400


def test_persona_turn_attribution_must_match_asked_persona(tmp_path: Path) -> None:
    fake = _Fake(answer_as="persona-someone-else")
    assert _post(fake, tmp_path, "persona-risk-officer").status_code >= 400
