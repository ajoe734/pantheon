from __future__ import annotations

import os
import sys
import tempfile
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from command_queue import CommandStore
from read_store import ReadSurfaceStore


APPROVER_TOKEN = "Bearer op-6:approver"
OPERATOR_TOKEN = "Bearer op-2:operator"


async def _noop_process_command(_command_id: str) -> None:
    return None


def test_submit_command_accepts_approval_queue_command_types() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/api/v1/operator/commands",
                headers={"Authorization": APPROVER_TOKEN},
                json={
                    "command": "ApproveDecision",
                    "target": {"type": "ApprovalDecision", "id": "appr-001"},
                    "action": "approve",
                    "params": {
                        "decision_id": "appr-001",
                        "approval_notes": "Proceed to approval",
                    },
                    "audit_context": {"reason": "Policy checks passed"},
                },
            )
            assert response.status_code == 202, response.text
            payload = response.json()
            assert payload["command"] == "ApproveDecision"
            assert payload["status"] == "accepted"
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def test_submit_command_accepts_escalate_diff() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/api/v1/operator/commands",
                headers={"Authorization": OPERATOR_TOKEN},
                json={
                    "command": "EscalateDiff",
                    "target": {"type": "DeploymentPlan", "id": "plan-dp-001"},
                    "action": "escalate_diff",
                    "params": {
                        "plan_id": "plan-dp-001",
                        "escalation_reason": "Binding move requires manual committee review",
                    },
                    "audit_context": {"reason": "Committee review needed"},
                },
            )
            assert response.status_code == 202, response.text
            payload = response.json()
            assert payload["command"] == "EscalateDiff"
            assert payload["status"] == "accepted"
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def test_submit_command_accepts_approve_mutation_published_payload() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_read_store = bff_main.read_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/api/v1/operator/commands",
                headers={"Authorization": APPROVER_TOKEN},
                json={
                    "command_type": "ApproveMutation",
                    "decision_id": "evo-dec-88f3a2c1",
                    "note": "Risk review complete",
                },
            )
            assert response.status_code == 202, response.text
            payload = response.json()
            assert payload["command"] == "ApproveMutation"
            assert payload["status"] == "accepted"
        finally:
            bff_main.command_store = original_store
            bff_main.read_store = original_read_store
            bff_main._process_command_stub = original_worker


def test_submit_command_accepts_reject_mutation_published_payload() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_read_store = bff_main.read_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/api/v1/operator/commands",
                headers={"Authorization": OPERATOR_TOKEN},
                json={
                    "command_type": "RejectMutation",
                    "decision_id": "evo-dec-88f3a2c1",
                    "note": "Evidence is still incomplete",
                },
            )
            assert response.status_code == 202, response.text
            payload = response.json()
            assert payload["command"] == "RejectMutation"
            assert payload["status"] == "accepted"
        finally:
            bff_main.command_store = original_store
            bff_main.read_store = original_read_store
            bff_main._process_command_stub = original_worker


def test_submit_command_accepts_record_sponsor_decision_published_payload() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_read_store = bff_main.read_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/api/v1/operator/commands",
                headers={"Authorization": OPERATOR_TOKEN},
                json={
                    "command_type": "RecordSponsorDecision",
                    "committee_id": "committee-regime-risk-20260419-081",
                    "sponsor_decision": "approved",
                    "rationale_ref": "workspace://committee-rationales/committee-regime-risk-20260419-081/final",
                    "note": "Sponsor resolved the split decision",
                },
            )
            assert response.status_code == 202, response.text
            payload = response.json()
            assert payload["command"] == "RecordSponsorDecision"
            assert payload["status"] == "accepted"
        finally:
            bff_main.command_store = original_store
            bff_main.read_store = original_read_store
            bff_main._process_command_stub = original_worker
