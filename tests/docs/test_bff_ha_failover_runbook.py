from __future__ import annotations

import json
from pathlib import Path


RUNBOOK_DOC = Path("docs/operations/bff_ha_failover_runbook.md")
SLA_TARGETS = Path("services/bff/ha/sla_targets.json")


def _runbook_text() -> str:
    return RUNBOOK_DOC.read_text(encoding="utf-8")


def test_failover_runbook_documents_happy_path_and_sla_targets() -> None:
    text = _runbook_text()
    targets = json.loads(SLA_TARGETS.read_text(encoding="utf-8"))["targets"]

    for environment, target in targets.items():
        expected_row = (
            f"| `{environment}` | {target['rto_seconds']} | "
            f"{target['rpo_seconds']} |"
        )
        assert expected_row in text

    required_happy_path_terms = [
        "active-passive control-plane failover path",
        "The primary BFF handles LB traffic before the drill.",
        "A passive BFF replica is healthy, warm, and ready to receive traffic.",
        "RTO timer starts",
        "RTO timer stops",
        "### 4. Shift Traffic To Passive BFF",
        "### 5. Validate New Active BFF",
        "### 6. Assert RPO And Command Safety",
        "`/api/v1/operator/health-status`",
        "`Last-Event-ID`",
    ]

    for term in required_happy_path_terms:
        assert term in text


def test_failover_runbook_documents_fail_closed_command_posture() -> None:
    text = _runbook_text()
    normalized_text = " ".join(text.split())

    required_fail_closed_terms = [
        "`503 IDEMPOTENCY_UNAVAILABLE`; No command dispatch.",
        "`503 AUDIT_UNAVAILABLE`; No command dispatch.",
        "`503 RUNTIME_MANAGER_UNAVAILABLE`; No runtime lifecycle command dispatch.",
        "`503 REGISTRY_GOVERNANCE_UNAVAILABLE`; no approval/deployment/capital command dispatch.",
        "`503 TELEMETRY_UNAVAILABLE` with stale read metadata.",
        "Do not replay in-flight commands during the freeze.",
        "No command was dispatched without audit handoff.",
        "RPO fails if any shared cursor is unavailable or cannot be compared.",
        "Production remains blocked until the HA PoC, evidence, and `HA-PROD-001-V2` human gate approve it.",
    ]

    for term in required_fail_closed_terms:
        assert term in normalized_text
