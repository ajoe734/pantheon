"""Tests for the SA/SD generator (ASST-INTEG-005).

Acceptance gates:
- Requirement capture generated from conversation turns with source citations
- SA includes current_state, roles, flows, data, risk, acceptance_scenarios
- SD includes architecture, api_contract, db_migration, ui_routes, tests, rollout, rollback
- Execution tasks include owner, reviewer, depends_on, artifacts, acceptance
- Archiver writes docs to correct paths and returns archive_locations
- Dict-shaped BFF context packs supported for actor/mode/sources/backend
- Generated task artifact paths align with archive_packet output paths
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

BFF_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BFF_DIR not in sys.path:
    sys.path.insert(0, BFF_DIR)

from assistant.dev_docs_models import (  # noqa: E402
    DevDocGenerateRequest,
    DevDocPacket,
)
from assistant.dev_docs_generator import (  # noqa: E402
    build_requirement_capture,
    build_system_analysis,
    build_system_design,
    build_execution_tasks,
    generate_dev_doc_packet,
)
from assistant.dev_docs_archiver import (  # noqa: E402
    archive_packet,
    requirement_capture_to_md,
    system_analysis_to_md,
    system_design_to_md,
    task_brief_to_md,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_request(
    conversation_id: str = "conv-test-001",
    feature_summary: str = "Extend pricing module to support tiered discounts",
    affected_modules: list | None = None,
    proposed_owner: str | None = None,
    proposed_reviewer: str | None = None,
) -> DevDocGenerateRequest:
    return DevDocGenerateRequest(
        conversationId=conversation_id,
        turnIds=["turn-1", "turn-2"],
        featureSummary=feature_summary,
        affectedModules=affected_modules or ["pricing", "payments"],
        proposedOwner=proposed_owner,
        proposedReviewer=proposed_reviewer,
    )


def _make_turns() -> list:
    return [
        {"turn_id": "turn-1", "role": "user", "content": "Can we add tiered discounts to pricing?"},
        {"turn_id": "turn-2", "role": "assistant", "content": "Yes, I'll analyse the current state."},
        {"turn_id": "turn-3", "role": "user", "content": "How would this affect the payment flow?"},
    ]


def _make_context_pack() -> MagicMock:
    """Object-shaped context pack (MagicMock, simulates Pydantic model attributes)."""
    pack = MagicMock()
    pack.mode.value = "user"
    pack.actor.operator_id = "op-001"
    pack.actor.roles = ["operator"]
    src = MagicMock()
    src.source_id = "control_room"
    src.href = "/bff/v5/control-room"
    src.snapshot_at = "2026-06-03T12:00:00Z"
    src.source_kind = "bff"
    pack.sources = [src]
    pack.backend.control_room = {"status": "ok"}
    pack.backend.jobs = None
    return pack


def _make_dict_context_pack() -> dict:
    """Dict-shaped BFF context pack (as produced by the real ASST-INTEG-002 composer)."""
    return {
        "actor": {"operator_id": "op-dict-001", "roles": ["operator"]},
        "mode": {"value": "user"},
        "sources": [
            {
                "source_id": "control_room",
                "href": "/bff/v5/control-room",
                "snapshot_at": "2026-06-03T12:00:00Z",
                "source_kind": "bff",
            }
        ],
        "backend": {
            "control_room": {"status": "ok"},
            "jobs": None,
        },
    }


# ---------------------------------------------------------------------------
# RequirementCapture tests
# ---------------------------------------------------------------------------

class TestRequirementCapture:
    def test_fields_populated(self):
        req = _make_request()
        turns = _make_turns()
        capture = build_requirement_capture(request=req, turns=turns, context_pack=None)

        assert capture.conversation_id == "conv-test-001"
        assert "tiered discounts" in capture.problem
        assert "pricing" in capture.affected_modules
        assert capture.capture_id.startswith("req_")
        assert capture.generated_at

    def test_source_refs_include_conversation(self):
        req = _make_request()
        capture = build_requirement_capture(request=req, turns=[], context_pack=None)

        ids = [r.source_id for r in capture.source_refs]
        assert "management_nl" in ids

    def test_source_refs_include_context_pack_sources(self):
        req = _make_request()
        pack = _make_context_pack()
        capture = build_requirement_capture(request=req, turns=[], context_pack=pack)

        ids = [r.source_id for r in capture.source_refs]
        assert "control_room" in ids

    def test_user_intent_from_last_user_turn(self):
        req = _make_request()
        turns = _make_turns()
        capture = build_requirement_capture(request=req, turns=turns, context_pack=None)
        assert "payment flow" in capture.user_intent

    def test_open_questions_extracted(self):
        req = _make_request()
        turns = _make_turns()
        capture = build_requirement_capture(request=req, turns=turns, context_pack=None)
        # Both user turns end with "?" → should be captured
        assert len(capture.open_questions) >= 1

    def test_turn_refs_populated(self):
        req = _make_request()
        turns = _make_turns()
        capture = build_requirement_capture(request=req, turns=turns, context_pack=None)
        assert len(capture.source_turn_refs) == 3
        assert capture.source_turn_refs[0].role == "user"

    def test_user_mode_constraints(self):
        req = _make_request()
        pack = _make_context_pack()
        capture = build_requirement_capture(request=req, turns=[], context_pack=pack)
        assert any("shell" in c.lower() for c in capture.constraints)

    def test_empty_turns(self):
        req = _make_request()
        capture = build_requirement_capture(request=req, turns=[], context_pack=None)
        assert capture.user_intent == req.feature_summary
        assert capture.source_turn_refs == []


# ---------------------------------------------------------------------------
# SystemAnalysis tests
# ---------------------------------------------------------------------------

class TestSystemAnalysis:
    def _make_sa(self, context_pack=None):
        req = _make_request()
        turns = _make_turns()
        capture = build_requirement_capture(request=req, turns=turns, context_pack=context_pack)
        return build_system_analysis(capture=capture, context_pack=context_pack)

    def test_required_fields_present(self):
        sa = self._make_sa()
        assert sa.current_state
        assert len(sa.roles) > 0
        assert len(sa.flows) > 0
        assert len(sa.data) > 0
        assert len(sa.risk) > 0
        assert len(sa.acceptance_scenarios) > 0

    def test_title_reflects_problem(self):
        sa = self._make_sa()
        assert "SA:" in sa.title

    def test_source_refs_present(self):
        sa = self._make_sa(_make_context_pack())
        ids = [r.source_id for r in sa.source_refs]
        assert "management_nl" in ids
        assert "control_room" in ids

    def test_flows_include_conversation_step(self):
        sa = self._make_sa()
        flows_text = " ".join(sa.flows).lower()
        assert "management ai" in flows_text or "operator" in flows_text

    def test_acceptance_scenarios_list(self):
        sa = self._make_sa()
        assert len(sa.acceptance_scenarios) >= 4


# ---------------------------------------------------------------------------
# SystemDesign tests
# ---------------------------------------------------------------------------

class TestSystemDesign:
    def _make_sd(self):
        req = _make_request()
        turns = _make_turns()
        pack = _make_context_pack()
        capture = build_requirement_capture(request=req, turns=turns, context_pack=pack)
        sa = build_system_analysis(capture=capture, context_pack=pack)
        return build_system_design(sa=sa, capture=capture, context_pack=pack)

    def test_required_fields_present(self):
        sd = self._make_sd()
        assert sd.architecture
        assert len(sd.api_contract) > 0
        assert len(sd.db_migration) > 0
        assert len(sd.ui_routes) > 0
        assert len(sd.tool_action_contract) > 0
        assert len(sd.tests) > 0
        assert sd.rollout
        assert sd.rollback

    def test_title_reflects_problem(self):
        sd = self._make_sd()
        assert "SD:" in sd.title

    def test_api_contract_includes_generate_endpoint(self):
        sd = self._make_sd()
        endpoints = " ".join(sd.api_contract)
        assert "generate" in endpoints.lower()

    def test_source_refs_present(self):
        sd = self._make_sd()
        assert len(sd.source_refs) > 0


# ---------------------------------------------------------------------------
# ExecutionTask tests
# ---------------------------------------------------------------------------

class TestExecutionTasks:
    def _make_tasks(self, proposed_owner=None, proposed_reviewer=None):
        req = _make_request(proposed_owner=proposed_owner, proposed_reviewer=proposed_reviewer)
        turns = _make_turns()
        pack = _make_context_pack()
        capture = build_requirement_capture(request=req, turns=turns, context_pack=pack)
        sa = build_system_analysis(capture=capture, context_pack=pack)
        sd = build_system_design(sa=sa, capture=capture, context_pack=pack)
        return build_execution_tasks(
            capture=capture, sa=sa, sd=sd, request=req, context_pack=pack,
            packet_id="pkt_test000001",
        )

    def test_at_least_two_tasks(self):
        tasks = self._make_tasks()
        assert len(tasks) >= 2

    def test_each_task_has_required_fields(self):
        tasks = self._make_tasks()
        for t in tasks:
            assert t.task_id.startswith("task_")
            assert t.title
            assert t.owner
            assert t.reviewer
            assert t.owner != t.reviewer
            assert len(t.acceptance) > 0

    def test_second_task_depends_on_first(self):
        tasks = self._make_tasks()
        assert tasks[0].task_id in tasks[1].depends_on

    def test_custom_owner_respected(self):
        tasks = self._make_tasks(proposed_owner="Gemini", proposed_reviewer="Claude")
        assert tasks[0].owner == "Gemini"
        assert tasks[0].reviewer == "Claude"

    def test_artifact_paths_use_docs04(self):
        tasks = self._make_tasks()
        all_artifacts = " ".join(tasks[0].artifacts)
        assert "docs/04/" in all_artifacts

    def test_artifact_paths_include_packet_id(self):
        tasks = self._make_tasks()
        all_artifacts = " ".join(tasks[0].artifacts)
        assert "pkt_test000001" in all_artifacts


# ---------------------------------------------------------------------------
# Dict-shaped BFF context pack tests
# ---------------------------------------------------------------------------

class TestDictContextPack:
    """Verify that dict-shaped context packs (actor/mode/sources/backend) are
    correctly handled alongside object-shaped (MagicMock/Pydantic) packs."""

    def test_source_refs_from_dict_context_pack(self):
        req = _make_request()
        pack = _make_dict_context_pack()
        capture = build_requirement_capture(request=req, turns=[], context_pack=pack)
        ids = [r.source_id for r in capture.source_refs]
        assert "management_nl" in ids
        assert "control_room" in ids

    def test_actor_id_from_dict_context_pack(self):
        req = _make_request()
        pack = _make_dict_context_pack()
        packet = generate_dev_doc_packet(request=req, turns=[], context_pack=pack)
        assert packet.actor_id == "op-dict-001"

    def test_mode_constraints_from_dict_context_pack(self):
        req = _make_request()
        pack = _make_dict_context_pack()
        capture = build_requirement_capture(request=req, turns=[], context_pack=pack)
        assert any("shell" in c.lower() for c in capture.constraints)

    def test_backend_surfaces_from_dict_context_pack(self):
        req = _make_request()
        pack = _make_dict_context_pack()
        capture = build_requirement_capture(request=req, turns=[], context_pack=pack)
        sa = build_system_analysis(capture=capture, context_pack=pack)
        data_text = " ".join(sa.data)
        assert "control_room" in data_text

    def test_source_refs_in_sa_from_dict_pack(self):
        req = _make_request()
        pack = _make_dict_context_pack()
        capture = build_requirement_capture(request=req, turns=[], context_pack=pack)
        sa = build_system_analysis(capture=capture, context_pack=pack)
        ids = [r.source_id for r in sa.source_refs]
        assert "management_nl" in ids
        assert "control_room" in ids

    def test_full_packet_with_dict_context_pack(self):
        req = _make_request()
        turns = _make_turns()
        pack = _make_dict_context_pack()
        packet = generate_dev_doc_packet(request=req, turns=turns, context_pack=pack)
        assert packet.packet_id.startswith("pkt_")
        assert packet.actor_id == "op-dict-001"
        assert len(packet.source_refs) > 0
        ids = [r.source_id for r in packet.source_refs]
        assert "control_room" in ids

    def test_kernel_mode_dict_constraints(self):
        req = _make_request()
        pack = dict(_make_dict_context_pack())
        pack["mode"] = {"value": "kernel"}
        capture = build_requirement_capture(request=req, turns=[], context_pack=pack)
        assert any("branch" in c.lower() or "kernel" in c.lower() for c in capture.constraints)


# ---------------------------------------------------------------------------
# Full packet assembly
# ---------------------------------------------------------------------------

class TestGenerateDevDocPacket:
    def test_full_packet_structure(self):
        req = _make_request()
        turns = _make_turns()
        pack = _make_context_pack()
        packet = generate_dev_doc_packet(request=req, turns=turns, context_pack=pack)

        assert packet.packet_id.startswith("pkt_")
        assert packet.conversation_id == req.conversation_id
        assert packet.requirement_capture.capture_id
        assert packet.system_analysis.sa_id
        assert packet.system_design.sd_id
        assert len(packet.execution_tasks) >= 2
        assert len(packet.source_refs) > 0

    def test_source_refs_include_conversation(self):
        req = _make_request()
        packet = generate_dev_doc_packet(request=req, turns=[], context_pack=None)
        ids = [r.source_id for r in packet.source_refs]
        assert "management_nl" in ids

    def test_packet_id_used_in_task_artifact_paths(self):
        req = _make_request()
        turns = _make_turns()
        pack = _make_context_pack()
        packet = generate_dev_doc_packet(request=req, turns=turns, context_pack=pack)
        # Task artifact paths must reference the packet's own id
        all_artifacts = " ".join(packet.execution_tasks[0].artifacts)
        assert packet.packet_id in all_artifacts


# ---------------------------------------------------------------------------
# Archiver tests
# ---------------------------------------------------------------------------

class TestDevDocsArchiver:
    def _make_packet(self) -> DevDocPacket:
        req = _make_request()
        turns = _make_turns()
        pack = _make_context_pack()
        return generate_dev_doc_packet(request=req, turns=turns, context_pack=pack)

    def test_archive_creates_files(self):
        packet = self._make_packet()
        with tempfile.TemporaryDirectory() as tmp:
            # Create ai-status.json stub so infer_repo_root() finds the root
            Path(tmp, "ai-status.json").write_text("{}", encoding="utf-8")
            locations = archive_packet(packet, repo_root=tmp)

            assert locations.requirement_capture
            assert Path(tmp, locations.requirement_capture).exists()
            assert locations.system_analysis
            assert Path(tmp, locations.system_analysis).exists()
            assert locations.system_design
            assert Path(tmp, locations.system_design).exists()
            assert len(locations.task_briefs) >= 2
            for brief_path in locations.task_briefs:
                assert Path(tmp, brief_path).exists()

    def test_archive_paths_use_docs04(self):
        packet = self._make_packet()
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "ai-status.json").write_text("{}", encoding="utf-8")
            locations = archive_packet(packet, repo_root=tmp)

            assert locations.requirement_capture.startswith("docs/04/")
            assert locations.system_analysis.startswith("docs/04/")
            assert locations.system_design.startswith("docs/04/")

    def test_archive_task_briefs_in_orchestrator(self):
        packet = self._make_packet()
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "ai-status.json").write_text("{}", encoding="utf-8")
            locations = archive_packet(packet, repo_root=tmp)

            for brief_path in locations.task_briefs:
                assert ".orchestrator/task-briefs/" in brief_path

    def test_requirement_capture_md_has_source_citations(self):
        packet = self._make_packet()
        md = requirement_capture_to_md(packet.requirement_capture)
        assert "Source Citations" in md
        assert "management_nl" in md

    def test_system_analysis_md_has_required_sections(self):
        packet = self._make_packet()
        md = system_analysis_to_md(packet.system_analysis)
        for section in ("Current State", "Roles", "Flows", "Data", "Risk", "Acceptance Scenarios"):
            assert section in md

    def test_system_design_md_has_required_sections(self):
        packet = self._make_packet()
        md = system_design_to_md(packet.system_design)
        for section in ("Architecture", "API Contract", "DB / Migration", "UI Routes", "Tests", "Rollout", "Rollback"):
            assert section in md

    def test_task_brief_md_has_required_sections(self):
        packet = self._make_packet()
        task = packet.execution_tasks[0]
        md = task_brief_to_md(task, packet.packet_id)
        for section in ("Task", "Summary", "Dependencies", "Artifacts", "Acceptance"):
            assert section in md
        assert task.owner in md
        assert task.reviewer in md

    def test_packet_json_written(self):
        packet = self._make_packet()
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "ai-status.json").write_text("{}", encoding="utf-8")
            locations = archive_packet(packet, repo_root=tmp)

            bundle_dir = Path(tmp, locations.requirement_capture).parent
            json_file = bundle_dir / "dev_doc_packet.json"
            assert json_file.exists()
            data = json_file.read_text(encoding="utf-8")
            assert packet.packet_id in data

    def test_archive_outputs_are_host_writable(self):
        packet = self._make_packet()
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "ai-status.json").write_text("{}", encoding="utf-8")
            locations = archive_packet(packet, repo_root=tmp)

            bundle_dir = Path(tmp, locations.requirement_capture).parent
            assert stat.S_IMODE(bundle_dir.stat().st_mode) == 0o775
            assert stat.S_IMODE(Path(tmp, locations.requirement_capture).stat().st_mode) == 0o664
            assert stat.S_IMODE(Path(tmp, locations.system_analysis).stat().st_mode) == 0o664
            assert stat.S_IMODE(Path(tmp, locations.system_design).stat().st_mode) == 0o664
            assert stat.S_IMODE(Path(tmp, locations.task_briefs[0]).stat().st_mode) == 0o664

    def test_artifact_paths_align_with_archive_locations(self):
        """Task artifact paths must reference the same files archive_packet() writes."""
        req = _make_request()
        turns = _make_turns()
        pack = _make_context_pack()
        packet = generate_dev_doc_packet(request=req, turns=turns, context_pack=pack)

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "ai-status.json").write_text("{}", encoding="utf-8")
            locations = archive_packet(packet, repo_root=tmp)

        all_artifacts = " ".join(packet.execution_tasks[0].artifacts)
        assert locations.requirement_capture in all_artifacts, (
            f"Expected {locations.requirement_capture!r} in task artifacts"
        )
        assert locations.system_analysis in all_artifacts, (
            f"Expected {locations.system_analysis!r} in task artifacts"
        )
        assert locations.system_design in all_artifacts, (
            f"Expected {locations.system_design!r} in task artifacts"
        )
