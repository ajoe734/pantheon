#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import planning_state
import task_archive


class PlanningStateTests(unittest.TestCase):
    def patch_paths(self, root: Path):
        planning_dir = root / "docs" / "02-architecture" / "consensus" / "phase1"
        return mock.patch.multiple(
            planning_state,
            ROOT=root,
            PLANNING_POINTER_FILE=root / ".orchestrator" / "planning-session-pointer.json",
            ORCHESTRATOR_STATE_FILE=root / ".orchestrator" / "state.json",
            PLANNING_DIR=planning_dir,
            SESSION_FILE=planning_dir / "planning-session.json",
            README_FILE=planning_dir / "README.md",
            READOUT_TEMPLATE_FILE=planning_dir / "LLM_READOUT_TEMPLATE.md",
            CHECKLIST_FILE=planning_dir / "pantheon-backend-completion-checklist.md",
            STARTER_DRAFT_FILE=planning_dir / "starter-draft.md",
            BATON_LOG_FILE=planning_dir / "baton-log.md",
            SUPERVISOR_QUEUE_FILE=planning_dir / "supervisor-queue.md",
            CONSENSUS_PACKET_FILE=planning_dir / "consensus-packet.md",
            DERIVED_STATE_FILE=root / ".orchestrator" / "planning-state.json",
            PLANNING_LOCK_FILE=root / ".orchestrator" / "planning-state.lock",
        )

    def patch_ai_status_paths(self, root: Path):
        archive_dir = root / "ai-task-archive"
        stack = ExitStack()
        stack.enter_context(
            mock.patch.multiple(
                planning_state.ai_status,
                ROOT=root,
                STATUS_ROOT=root,
                STATUS_FILE=root / "ai-status.json",
                LOG_FILE=root / "ai-activity-log.jsonl",
                CURRENT_WORK_FILE=root / "current-work.md",
                DOCS_SITE_DIR=root / "docs-site",
                CONFIG_FILE=root / ".orchestrator" / "config.json",
                PLANNING_STATE_FILE=root / ".orchestrator" / "planning-state.json",
                ORCHESTRATOR_STATE_FILE=root / ".orchestrator" / "state.json",
                APPROVAL_QUEUE_FILE=root / ".orchestrator" / "approval-queue.json",
                DASHBOARD_BUNDLE_FILE=root / "dashboard-bundle.json",
                ARCHIVE_TASKS_DIR=archive_dir / "tasks",
                archived_task_snapshot=lambda _task_id: None,
            )
        )
        stack.enter_context(
            mock.patch.multiple(
                task_archive,
                STATUS_ROOT=root,
                STATUS_FILE=root / "ai-status.json",
                ARCHIVE_DIR=archive_dir,
                ARCHIVE_TASKS_DIR=archive_dir / "tasks",
                ARCHIVE_INDEX_FILE=archive_dir / "index.json",
            )
        )
        return stack

    def test_sync_creates_templates_and_derived_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planning-state-sync-") as temp_dir:
            root = Path(temp_dir)
            with self.patch_paths(root):
                session = planning_state.default_session()
                planning_state.sync_all(session)

                self.assertTrue((root / "docs" / "02-architecture" / "consensus" / "phase1" / "README.md").exists())
                self.assertTrue((root / "docs" / "02-architecture" / "consensus" / "phase1" / "pantheon-backend-completion-checklist.md").exists())
                self.assertTrue((root / ".orchestrator" / "planning-state.json").exists())

                derived = json.loads((root / ".orchestrator" / "planning-state.json").read_text(encoding="utf-8"))
                self.assertEqual(derived["status"], "inactive")
                self.assertEqual(derived["planning_mode"], "discussion_planning")
                self.assertFalse(derived["switch_gate"]["ready_to_materialize"])
                self.assertEqual(derived["materialization_contract"]["source_plane"], "planning")
                self.assertEqual(derived["materialization_contract"]["session_id"], session["session_id"])

    def test_blueprint_gap_brief_files_excludes_current_work(self) -> None:
        files = planning_state.blueprint_gap_brief_files()

        self.assertIn("ai-status.json", files)
        self.assertNotIn("current-work.md", files)

    def test_command_flow_advances_switch_gate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planning-state-flow-") as temp_dir:
            root = Path(temp_dir)
            with self.patch_paths(root):
                session = planning_state.default_session()
                planning_state.command_start(session, ["phase1-2026-04-11", "Kick off planning"])
                planning_state.command_reconcile_docs(session, ["completed", "Canonical docs reconciled"])
                for agent in planning_state.AGENT_ORDER:
                    planning_state.command_readout(session, [agent, "submitted", f"{agent} readout ready"])
                planning_state.command_round(session, ["1", "completed", "Round 1 closed"])
                planning_state.command_consensus(session, ["ready_for_human", "Consensus packet drafted"])
                planning_state.command_human_gate(session, ["approved", "Human accepted the packet"])

                derived = planning_state.build_derived_state(session)

        self.assertTrue(derived["switch_gate"]["document_reconciliation_complete"])
        self.assertTrue(derived["switch_gate"]["all_readouts_submitted"])
        self.assertTrue(derived["switch_gate"]["cross_review_round_present"])
        self.assertTrue(derived["switch_gate"]["consensus_packet_drafted"])
        self.assertTrue(derived["switch_gate"]["human_approved"])
        self.assertTrue(derived["switch_gate"]["ready_to_materialize"])

    def test_prereqs_ready_for_human_but_not_ready_to_materialize_without_approval(self) -> None:
        session = planning_state.default_session()
        for agent in planning_state.AGENT_ORDER:
            session["readouts"][agent]["status"] = "submitted"
        session["cross_review_rounds"] = [
            {
                "round": 1,
                "status": "open",
                "reviewers": planning_state.REVIEW_SEQUENCE,
                "updated_at": "2026-04-15T14:00:00Z",
                "summary": "Cross-review in progress.",
            }
        ]
        session["document_reconciliation_status"] = "completed"
        session["consensus_status"] = "draft"
        session["human_gate_status"] = "not_requested"

        derived = planning_state.build_derived_state(session)

        self.assertTrue(derived["switch_gate"]["all_readouts_submitted"])
        self.assertTrue(derived["switch_gate"]["cross_review_round_present"])
        self.assertTrue(derived["switch_gate"]["ready_for_human"])
        self.assertFalse(derived["switch_gate"]["human_approved"])
        self.assertFalse(derived["switch_gate"]["ready_to_materialize"])

    def test_sync_auto_detects_markdown_activity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planning-state-autodetect-") as temp_dir:
            root = Path(temp_dir)
            with self.patch_paths(root):
                planning_state.ensure_artifact_files()
                planning_state.STARTER_DRAFT_FILE.write_text(
                    "# Starter Draft\n\n## Shared Draft\n\n- Objective: Real planning has started.\n",
                    encoding="utf-8",
                )
                planning_state.readout_file_for("Codex").write_text(
                    "# Codex Readout\n\n- Architecture summary: drafted.\n",
                    encoding="utf-8",
                )

                session = planning_state.load_session()
                derived = planning_state.build_derived_state(session)

        self.assertEqual(derived["status"], "active")
        self.assertEqual(derived["consensus_status"], "draft")
        self.assertEqual(derived["artifacts"]["starter_draft"]["status"], "active")
        self.assertEqual(derived["readouts"]["Codex"]["status"], "submitted")
        self.assertGreaterEqual(derived["counts"]["readouts_submitted"], 1)

    def test_main_readout_roundtrip_keeps_session_json_valid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planning-state-main-readout-") as temp_dir:
            root = Path(temp_dir)
            with self.patch_paths(root):
                session = planning_state.default_session("phase4-2026-04-15-service-layer-completion", "phase4")
                planning_state.sync_all(session)

                planning_state.main(["planning_state.py", "readout", "Claude", "submitted", "Claude readout ready"])
                planning_state.main(["planning_state.py", "readout", "Gemini", "submitted", "Gemini readout ready"])

                saved = json.loads((root / "docs" / "02-architecture" / "consensus" / "sessions" / "phase4-2026-04-15-service-layer-completion" / "planning-session.json").read_text(encoding="utf-8"))

        self.assertEqual(saved["readouts"]["Claude"]["status"], "submitted")
        self.assertEqual(saved["readouts"]["Gemini"]["status"], "submitted")
        self.assertEqual(saved["recent_events"][-2]["owner"], "Claude")
        self.assertEqual(saved["recent_events"][-1]["owner"], "Gemini")

    def test_sync_persists_accepted_consensus_artifact_statuses(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planning-state-accepted-") as temp_dir:
            root = Path(temp_dir)
            with self.patch_paths(root):
                session = planning_state.default_session()
                planning_state.command_start(session, ["phase1-2026-04-11", "Kick off planning"])
                planning_state.command_reconcile_docs(session, ["completed", "Canonical docs reconciled"])
                planning_state.command_consensus(session, ["ready_for_human", "Consensus packet drafted"])
                planning_state.command_human_gate(session, ["approved", "Human accepted the packet"])
                planning_state.command_round(session, ["1", "completed", "Round 1 closed"])
                planning_state.sync_all(session)

                pointer = json.loads((root / ".orchestrator" / "planning-session-pointer.json").read_text(encoding="utf-8"))
                saved = json.loads((root / pointer["session_file"]).read_text(encoding="utf-8"))
                derived = json.loads((root / ".orchestrator" / "planning-state.json").read_text(encoding="utf-8"))

        self.assertEqual(saved["status"], "accepted")
        self.assertEqual(saved["document_reconciliation_status"], "completed")
        self.assertEqual(saved["consensus_status"], "accepted")
        self.assertEqual(saved["human_gate_status"], "approved")
        self.assertEqual(saved["artifacts"]["document_reconciliation"]["status"], "completed")
        self.assertEqual(saved["artifacts"]["consensus_packet"]["status"], "accepted")
        self.assertEqual(saved["artifacts"]["review_rounds"]["status"], "completed")
        self.assertTrue(derived["switch_gate"]["ready_to_materialize"])

    def test_start_new_session_uses_archive_path_and_keeps_history(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planning-state-archive-") as temp_dir:
            root = Path(temp_dir)
            with self.patch_paths(root):
                legacy = planning_state.default_session("phase1-2026-04-11-backend-completion", "phase1")
                planning_state.command_reconcile_docs(legacy, ["not_needed", "Legacy session predated the new gate"])
                planning_state.command_human_gate(legacy, ["approved", "Legacy accepted"])
                planning_state.sync_all(legacy)

                active = planning_state.load_session()
                planning_state.command_start(active, ["phase3-2026-04-12-runtime-replan", "New runtime replan"])
                planning_state.sync_all(active)

                pointer = json.loads((root / ".orchestrator" / "planning-session-pointer.json").read_text(encoding="utf-8"))
                derived = json.loads((root / ".orchestrator" / "planning-state.json").read_text(encoding="utf-8"))

        self.assertEqual(pointer["session_id"], "phase3-2026-04-12-runtime-replan")
        self.assertEqual(pointer["planning_dir"], "docs/02-architecture/consensus/sessions/phase3-2026-04-12-runtime-replan")
        self.assertEqual(derived["active_session"]["planning_dir"], pointer["planning_dir"])
        self.assertEqual(derived["profile"], planning_state.SESSION_PROFILE_GENERIC)
        recent_ids = [entry["session_id"] for entry in derived["recent_sessions"]]
        self.assertEqual(recent_ids[0], "phase3-2026-04-12-runtime-replan")
        self.assertIn("phase1-2026-04-11-backend-completion", recent_ids)

    def test_start_new_session_is_allowed_while_execution_has_inflight_work(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planning-state-inflight-start-") as temp_dir:
            root = Path(temp_dir)
            orchestrator_dir = root / ".orchestrator"
            orchestrator_dir.mkdir(parents=True, exist_ok=True)
            runtime_state = {
                "supervisor": {"focus_mode": "execution"},
                "workers": [
                    {
                        "task_id": "DEPTH-EVO004",
                        "status": "running",
                        "bucket": "running",
                        "pid": 1234,
                    }
                ],
                "queue": [
                    {"id": "evt-1", "task_id": "DEPTH-EVO004", "status": "started"}
                ],
            }
            (orchestrator_dir / "state.json").write_text(json.dumps(runtime_state), encoding="utf-8")
            with self.patch_paths(root), self.patch_ai_status_paths(root):
                active = planning_state.default_session("phase6-accepted", "phase6")
                planning_state.sync_all(active)

                session = planning_state.load_session()
                planning_state.command_start(session, ["phase7-2026-04-18-ep4-ep5-execution-proof", "EP4/EP5 replan"])
                planning_state.sync_all(session)

                pointer = json.loads((root / ".orchestrator" / "planning-session-pointer.json").read_text(encoding="utf-8"))
                derived = json.loads((root / ".orchestrator" / "planning-state.json").read_text(encoding="utf-8"))

        self.assertEqual(pointer["session_id"], "phase7-2026-04-18-ep4-ep5-execution-proof")
        self.assertEqual(derived["active_session"]["session_id"], "phase7-2026-04-18-ep4-ep5-execution-proof")

    def test_switch_gate_treats_waived_readouts_and_tracking_items_as_terminal(self) -> None:
        session = planning_state.default_session()
        for agent in planning_state.AGENT_ORDER:
            session["readouts"][agent]["status"] = "waived"
        session["cross_review_rounds"] = [
            {
                "round": 1,
                "status": "completed",
                "reviewers": planning_state.REVIEW_SEQUENCE,
                "updated_at": "2026-04-11T12:00:00Z",
                "summary": "Closed with synthesized packet",
            }
        ]
        session["unresolved_items"] = [
            {
                "id": "DISC-001",
                "severity": "low",
                "status": "tracking",
                "summary": "Provider instability tracked separately.",
            }
        ]
        session["document_reconciliation_status"] = "not_needed"
        session["consensus_status"] = "accepted"
        session["human_gate_status"] = "approved"

        derived = planning_state.build_derived_state(session)

        self.assertTrue(derived["switch_gate"]["all_readouts_submitted"])
        self.assertTrue(derived["switch_gate"]["divergence_resolved_or_escalated"])
        self.assertTrue(derived["switch_gate"]["ready_to_materialize"])
        self.assertEqual(derived["counts"]["readouts_resolved"], len(planning_state.AGENT_ORDER))
        self.assertEqual(derived["counts"]["open_items"], 0)

    def test_start_phase2_preserves_phase1_and_seeds_phase2_metadata(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planning-state-phase2-") as temp_dir:
            root = Path(temp_dir)
            with self.patch_paths(root):
                phase1 = planning_state.default_session("phase1-2026-04-11-backend-completion", "phase1")
                planning_state.command_reconcile_docs(phase1, ["not_needed", "Canonical docs already aligned"])
                planning_state.command_consensus(phase1, ["accepted", "Phase1 accepted"])
                planning_state.command_human_gate(phase1, ["approved", "Human accepted phase1"])
                planning_state.sync_all(phase1)
                phase1_file = root / "docs" / "02-architecture" / "consensus" / "phase1" / "planning-session.json"
                phase1_saved = json.loads(phase1_file.read_text(encoding="utf-8"))

                active = planning_state.load_session()
                planning_state.command_start(active, [planning_state.PHASE2_SESSION_ID, "Blueprint gap convergence"])
                planning_state.sync_all(active)

                phase2_file = root / "docs" / "02-architecture" / "consensus" / "phase2" / "planning-session.json"
                phase2_saved = json.loads(phase2_file.read_text(encoding="utf-8"))
                pointer = json.loads((root / ".orchestrator" / "planning-session-pointer.json").read_text(encoding="utf-8"))

        self.assertEqual(phase1_saved["session_id"], "phase1-2026-04-11-backend-completion")
        self.assertEqual(phase1_saved["status"], "accepted")
        self.assertEqual(phase2_saved["session_id"], planning_state.PHASE2_SESSION_ID)
        self.assertEqual(phase2_saved["phase"], "phase2")
        self.assertEqual(phase2_saved["profile"], planning_state.SESSION_PROFILE_BLUEPRINT_GAP)
        self.assertIn("Pantheon_Blueprint_Gap_Review_v1.md", phase2_saved["brief_files"])
        self.assertIn("Pantheon_Market_Data_Scope_and_Source_Plan_v1.md", phase2_saved["brief_files"])
        self.assertEqual(phase2_saved["baton_owner"], "Codex")
        self.assertEqual(pointer["session_id"], planning_state.PHASE2_SESSION_ID)
        self.assertEqual(pointer["planning_dir"], "docs/02-architecture/consensus/phase2")

    def test_non_blueprint_phase2_session_uses_generic_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planning-state-generic-phase2-") as temp_dir:
            root = Path(temp_dir)
            with self.patch_paths(root):
                active = planning_state.load_session()
                planning_state.command_start(active, ["phase2-2026-04-13-risk-replan", "Generic phase2 session"])
                planning_state.sync_all(active)

                session_file = root / "docs" / "02-architecture" / "consensus" / "sessions" / "phase2-2026-04-13-risk-replan" / "planning-session.json"
                saved = json.loads(session_file.read_text(encoding="utf-8"))

        self.assertEqual(saved["profile"], planning_state.SESSION_PROFILE_GENERIC)
        self.assertEqual(saved["planning_dir"], "docs/02-architecture/consensus/sessions/phase2-2026-04-13-risk-replan")
        self.assertEqual([entry["id"] for entry in saved["expected_outputs"]], ["document_reconciliation", "consensus_packet"])
        self.assertNotIn("Pantheon_Blueprint_Gap_Review_v1.md", saved["brief_files"])
        self.assertEqual(saved["proposed_execution_tasks"], [])

    def test_generic_session_materialization_contract_uses_expected_output_path(self) -> None:
        session = planning_state.default_session("phase3-2026-04-14-custom", "phase3")
        session["expected_outputs"] = [
            {
                "id": "consensus_packet",
                "path": "docs/02-architecture/consensus/sessions/phase3-2026-04-14-custom/consensus-packet.md",
                "owner": "Claude",
                "status": "not_started",
            },
            {
                "id": "execution_materialization",
                "path": "docs/02-architecture/consensus/sessions/phase3-2026-04-14-custom/execution-materialization.md",
                "owner": "Codex",
                "status": "not_started",
            },
        ]

        derived = planning_state.build_derived_state(session)

        self.assertEqual(
            derived["materialization_contract"]["execution_materialization"],
            "docs/02-architecture/consensus/sessions/phase3-2026-04-14-custom/execution-materialization.md",
        )

    def test_materialize_writes_phase2_tasks_after_human_gate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planning-state-materialize-") as temp_dir:
            root = Path(temp_dir)
            with self.patch_paths(root), self.patch_ai_status_paths(root):
                session = planning_state.default_session(planning_state.PHASE2_SESSION_ID, "phase2")
                planning_state.command_reconcile_docs(session, ["completed", "Blueprint docs reconciled"])
                planning_state.command_human_gate(session, ["approved", "Human accepted execution plan"])
                planning_state.sync_all(session)
                planning_state.command_materialize(session, [])

                status = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
                task_map = {task["id"]: task for task in status["tasks"]}

        self.assertIn("PLAN-002", task_map)
        self.assertIn("BG-005", task_map)
        self.assertEqual(task_map["PLAN-002"]["owner"], "Codex")
        self.assertEqual(task_map["BG-005"]["depends_on"], ["BG-000", "BG-001", "BG-003"])
        self.assertEqual(task_map["BG-007"]["reviewer"], "Codex")
        self.assertEqual(task_map["PLAN-002"]["source_plane"], "planning")
        self.assertEqual(task_map["PLAN-002"]["source_ref"]["session_id"], planning_state.PHASE2_SESSION_ID)
        self.assertEqual(task_map["PLAN-002"]["source_ref"]["consensus_packet"], "docs/02-architecture/consensus/phase2/consensus-packet.md")
        self.assertEqual(
            task_map["PLAN-002"]["source_ref"]["execution_materialization"],
            "docs/02-architecture/consensus/phase2/execution-materialization.md",
        )
        self.assertEqual(task_map["PLAN-002"]["materialization_ref"]["human_gate_status"], "approved")

    def test_materialize_holds_canonical_task_lock_around_complete_body(self) -> None:
        events: list[str] = []
        lock = mock.MagicMock()
        lock.__enter__.side_effect = lambda: events.append("lock-enter")
        lock.__exit__.side_effect = lambda *_args: events.append("lock-exit")

        session = {"session_id": "phase-test"}
        args = ["ignored"]
        with (
            mock.patch.object(
                planning_state.ai_status,
                "canonical_task_state_lock",
                return_value=lock,
            ) as acquire_lock,
            mock.patch.object(
                planning_state,
                "_command_materialize_locked",
                side_effect=lambda actual_session, actual_args: events.append(
                    f"body:{actual_session['session_id']}:{actual_args[0]}"
                ),
            ) as materialize_locked,
        ):
            planning_state.command_materialize(session, args)

        acquire_lock.assert_called_once_with(shared=False, nonblocking=False)
        materialize_locked.assert_called_once_with(session, args)
        self.assertEqual(
            events,
            ["lock-enter", "body:phase-test:ignored", "lock-exit"],
        )

    def test_materialize_rejects_non_null_program_activity_outbox_before_writes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planning-state-outbox-guard-") as temp_dir:
            root = Path(temp_dir)
            with self.patch_paths(root), self.patch_ai_status_paths(root):
                session = planning_state.default_session("phase-test", "phase3")
                session["document_reconciliation_status"] = "not_needed"
                session["human_gate_status"] = "approved"
                session["proposed_execution_tasks"] = [
                    {
                        "id": "PLAN-TEST-001",
                        "title": "Must not materialize",
                        "owner": "Codex",
                        "reviewer": "Claude",
                        "phase": "Phase 3",
                        "summary_zh": "outbox 尚未恢復時不可寫入。",
                        "depends_on": [],
                        "artifacts": [],
                        "acceptance": [],
                    }
                ]
                state = planning_state.ai_status.default_state()
                state["program_activity_outbox"] = {}
                status_file = root / "ai-status.json"
                status_file.write_text(
                    json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                before = status_file.read_bytes()

                with (
                    mock.patch.object(planning_state, "save_derived_state") as save_derived,
                    mock.patch.object(planning_state, "save_session") as save_session,
                    mock.patch.object(planning_state.ai_status, "sync_all") as sync_status,
                    self.assertRaisesRegex(SystemExit, "program_activity_outbox"),
                ):
                    planning_state.command_materialize(session, [])

                save_derived.assert_not_called()
                save_session.assert_not_called()
                sync_status.assert_not_called()
                self.assertEqual(status_file.read_bytes(), before)

    def test_materialize_rejects_sequencing_parked_task_before_upsert(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planning-state-sequencing-guard-") as temp_dir:
            root = Path(temp_dir)
            with self.patch_paths(root), self.patch_ai_status_paths(root):
                session = planning_state.default_session("phase-test", "phase3")
                session["document_reconciliation_status"] = "not_needed"
                session["human_gate_status"] = "approved"
                session["proposed_execution_tasks"] = [
                    {
                        "id": "LOOP-PROD-AUTH-001",
                        "title": "Sequencing-owned task",
                        "owner": "Codex",
                        "reviewer": "Claude",
                        "phase": "Phase 3",
                        "summary_zh": "只能由 authoritative dispatcher 釋放。",
                        "depends_on": [],
                        "artifacts": [],
                        "acceptance": [],
                    }
                ]
                state = planning_state.ai_status.default_state()
                status_file = root / "ai-status.json"
                status_file.write_text(
                    json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                before = status_file.read_bytes()

                with (
                    mock.patch.object(planning_state, "save_derived_state") as save_derived,
                    mock.patch.object(planning_state, "save_session") as save_session,
                    mock.patch.object(planning_state.ai_status, "sync_all") as sync_status,
                    self.assertRaisesRegex(SystemExit, "sequencing-governed task"),
                ):
                    planning_state.command_materialize(session, [])

                save_derived.assert_not_called()
                save_session.assert_not_called()
                sync_status.assert_not_called()
                self.assertEqual(status_file.read_bytes(), before)

    def test_upsert_rejects_valid_pre_g2_epoch_task_before_provenance_overwrite(self) -> None:
        authority = planning_state.sequencing_gate
        null_sha256 = authority.canonical_sha256(None)

        def source_ref(task_id: str) -> dict:
            return {
                "program_id": authority.PROGRAM_ID,
                "catalog_sha256": authority.EFFECTIVE_CATALOG_SHA256,
                "source_catalog_sha256": authority.SOURCE_CATALOG_SHA256,
                "sequencing_addendum_sha256": authority.SEQUENCING_ADDENDUM_SHA256,
                "merge_pr_3737_sha": authority.MERGE_PR_3737_SHA,
                "sequencing_overlay_sha256": authority.SEQUENCING_OVERLAY_SHA256,
                "release_gate_id": authority.RELEASE_GATE_ID,
                "sequencing_classification": (
                    authority.EXPECTED_CLASSIFICATION_BY_TASK_ID[task_id]
                ),
            }

        transitions = []
        for index, task_id in enumerate(authority.EXPECTED_TASK_IDS, start=1):
            gated = task_id in authority.EXPECTED_GATED_TASK_IDS
            transitions.append(
                {
                    "task_id": task_id,
                    "before_task_snapshot_sha256": f"{index:064x}",
                    "after_task_snapshot_sha256": f"{index + 100:064x}",
                    "before_task_contract_sha256": "1" * 64,
                    "after_task_contract_sha256": "2" * 64,
                    "before_source_ref_sha256": "3" * 64,
                    "after_source_ref_sha256": authority.canonical_sha256(
                        source_ref(task_id)
                    ),
                    "before_status": "todo",
                    "after_status": "blocked" if gated else "todo",
                    "acceptance_deferral_sha256": "4" * 64,
                    "gate_marker_sha256": "5" * 64 if gated else null_sha256,
                }
            )
        epoch = {
            "schema_version": 1,
            "program_id": authority.PROGRAM_ID,
            "source_catalog_sha256": authority.SOURCE_CATALOG_SHA256,
            "effective_catalog_sha256": authority.EFFECTIVE_CATALOG_SHA256,
            "sequencing_overlay_sha256": authority.SEQUENCING_OVERLAY_SHA256,
            "release_gate_id": authority.RELEASE_GATE_ID,
            "install_mode": "base_epoch_migration",
            "applied_at": "2026-07-16T00:00:00Z",
            "source_graph_projection_sha256": "6" * 64,
            "effective_graph_projection_sha256": "7" * 64,
            "task_count": authority.EXPECTED_TASK_COUNT,
            "task_transitions": transitions,
            "task_transition_set_sha256": authority.canonical_sha256(transitions),
        }
        task = {
            "id": "LOOP-PROD-000",
            "status": "todo",
            "source_ref": source_ref("LOOP-PROD-000"),
        }
        state = {
            "tasks": [task],
            "program_sequencing_epochs": {authority.PROGRAM_ID: epoch},
        }
        self.assertFalse(authority.task_is_sequencing_parked(task, state))

        with self.assertRaisesRegex(SystemExit, "sequencing-governed task"):
            planning_state.upsert_materialized_task(
                state,
                {
                    "id": task["id"],
                    "title": "Must not overwrite sequencing provenance",
                    "owner": "Codex",
                    "reviewer": "Codex2",
                    "phase": "Phase 3",
                    "summary_zh": "不得覆寫 sequencing source_ref。",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                },
                materialization_ref={"session_id": "planning-session"},
            )

        self.assertEqual(task["source_ref"], source_ref(task["id"]))

    def test_materialize_preserves_existing_execution_truth_and_only_backfills_source_refs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planning-state-backfill-") as temp_dir:
            root = Path(temp_dir)
            with self.patch_paths(root), self.patch_ai_status_paths(root):
                session = planning_state.default_session(planning_state.PHASE2_SESSION_ID, "phase2")
                planning_state.command_reconcile_docs(session, ["completed", "Blueprint docs reconciled"])
                planning_state.command_human_gate(session, ["approved", "Human accepted execution plan"])
                state = planning_state.ai_status.default_state()
                state["tasks"] = [
                    {
                        "id": "BG-000",
                        "title": "Operationally reassigned title",
                        "summary_zh": "保留目前 execution truth。",
                        "phase": "Blueprint Gap P0",
                        "owner": "Codex2",
                        "reviewer": "Codex",
                        "status": "in_progress",
                        "depends_on": ["PLAN-002"],
                        "artifacts": [],
                        "acceptance": [],
                        "next": "Keep current execution moving",
                        "last_update": "2026-04-13T07:00:00Z",
                    }
                ]
                state["agents"] = []
                state["handoffs"] = []
                state["blockers"] = []
                state["workload"] = {}
                state["workload_summary"] = {}
                (root / "ai-status.json").write_text(
                    json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

                planning_state.command_materialize(session, [])
                status = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
                task_map = {task["id"]: task for task in status["tasks"]}

        self.assertEqual(task_map["BG-000"]["title"], "Operationally reassigned title")
        self.assertEqual(task_map["BG-000"]["owner"], "Codex2")
        self.assertEqual(task_map["BG-000"]["status"], "in_progress")
        self.assertEqual(task_map["BG-000"]["last_update"], "2026-04-13T07:00:00Z")
        self.assertEqual(task_map["BG-000"]["source_plane"], "planning")
        self.assertEqual(task_map["BG-000"]["source_ref"]["session_id"], planning_state.PHASE2_SESSION_ID)

    def test_materialize_respects_initial_materialization_task_ids(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planning-state-initial-batch-") as temp_dir:
            root = Path(temp_dir)
            with self.patch_paths(root), self.patch_ai_status_paths(root):
                session = planning_state.default_session("phase7-2026-04-18-ep4-ep5-execution-proof", "phase7")
                session["status"] = "accepted"
                session["document_reconciliation_status"] = "not_needed"
                session["consensus_status"] = "accepted"
                session["human_gate_status"] = "approved"
                session["proposed_execution_tasks"] = [
                    {
                        "id": "OSS-004A",
                        "title": "EP4 initial batch task",
                        "owner": "Gemini",
                        "reviewer": "Codex",
                        "phase": "Phase 7",
                        "summary_zh": "首批 materialization task。",
                        "depends_on": [],
                        "artifacts": [],
                        "acceptance": [],
                    },
                    {
                        "id": "EP5-001",
                        "title": "Deferred task",
                        "owner": "Gemini",
                        "reviewer": "Claude",
                        "phase": "Phase 7",
                        "summary_zh": "延後的 task。",
                        "depends_on": ["OSS-004A"],
                        "artifacts": [],
                        "acceptance": [],
                    },
                ]
                session["initial_materialization_task_ids"] = ["OSS-004A"]

                planning_state.command_materialize(session, [])

                status = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
                task_map = {task["id"]: task for task in status["tasks"]}
                persisted_session = json.loads((planning_state.SESSION_FILE).read_text(encoding="utf-8"))

        self.assertIn("OSS-004A", task_map)
        self.assertNotIn("EP5-001", task_map)
        self.assertEqual(
            task_map["OSS-004A"]["materialization_ref"]["initial_materialization_task_ids"],
            "OSS-004A",
        )
        self.assertIsNotNone(persisted_session.get("materialized_at"))

    def test_materialize_skips_archived_terminal_tasks_without_reviving_them(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planning-state-archived-skip-") as temp_dir:
            root = Path(temp_dir)
            with self.patch_paths(root), self.patch_ai_status_paths(root):
                session = planning_state.default_session("phase3-2026-04-14-archived-skip", "phase3")
                session["status"] = "accepted"
                session["document_reconciliation_status"] = "not_needed"
                session["consensus_status"] = "accepted"
                session["human_gate_status"] = "approved"
                session["proposed_execution_tasks"] = [
                    {
                        "id": "PLAN-ARCH",
                        "title": "Archived task should stay terminal",
                        "owner": "Codex",
                        "reviewer": "Claude",
                        "phase": "Planning Materialized",
                        "summary_zh": "不要把 archive 裡的 task 再復活。",
                        "depends_on": [],
                        "artifacts": [],
                        "acceptance": [],
                    }
                ]
                state = planning_state.ai_status.default_state()
                (root / "ai-status.json").write_text(
                    json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

                with mock.patch.object(
                    planning_state.ai_status,
                    "archived_task_snapshot",
                    side_effect=lambda task_id: (
                        {"task_id": "PLAN-ARCH"}
                        if task_id == "PLAN-ARCH"
                        else None
                    ),
                ):
                    planning_state.command_materialize(session, [])

                status = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))

        task_ids = {task["id"] for task in status["tasks"]}
        self.assertNotIn("PLAN-ARCH", task_ids)

    def test_consensus_ready_for_human_requires_document_reconciliation(self) -> None:
        session = planning_state.default_session()
        with self.assertRaises(SystemExit):
            planning_state.command_consensus(session, ["ready_for_human", "Attempted to skip reconciliation"])

    def test_human_gate_approval_requires_document_reconciliation(self) -> None:
        session = planning_state.default_session()
        with self.assertRaises(SystemExit):
            planning_state.command_human_gate(session, ["approved", "Attempted to approve without reconciliation"])

    def test_materialize_requires_document_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planning-state-materialize-gate-") as temp_dir:
            root = Path(temp_dir)
            with self.patch_paths(root), self.patch_ai_status_paths(root):
                session = planning_state.default_session(planning_state.PHASE2_SESSION_ID, "phase2")
                session["consensus_status"] = "accepted"
                session["human_gate_status"] = "approved"
                session["status"] = "accepted"
                with self.assertRaises(SystemExit):
                    planning_state.command_materialize(session, [])


if __name__ == "__main__":
    unittest.main()
