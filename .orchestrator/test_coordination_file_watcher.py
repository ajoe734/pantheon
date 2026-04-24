#!/usr/bin/env python3
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from common import load_jsonl
from coordination_file_watcher import sync_coordination_files


class CoordinationWatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        self.pantheon = root / "pantheon"
        self.front = root / "front-ai-trading-system"
        for repo_root in (self.pantheon, self.front):
            (repo_root / ".git").mkdir(parents=True, exist_ok=True)
            (repo_root / ".coordination" / "requests").mkdir(parents=True, exist_ok=True)
            (repo_root / ".coordination" / "responses").mkdir(parents=True, exist_ok=True)
            (repo_root / "docs-site").mkdir(parents=True, exist_ok=True)
            (repo_root / "ai-status.json").write_text('{"tasks":[],"handoffs":[]}\n', encoding="utf-8")
            (repo_root / "current-work.md").write_text("# current work\n", encoding="utf-8")
            (repo_root / "ai-activity-log.jsonl").write_text("", encoding="utf-8")
            (repo_root / "docs-site" / "index.html").write_text("<html></html>\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.front, check=True)
        (self.pantheon / "docs" / "bff").mkdir(parents=True, exist_ok=True)
        (self.pantheon / "docs" / "screens").mkdir(parents=True, exist_ok=True)
        (self.pantheon / "docs" / "examples").mkdir(parents=True, exist_ok=True)
        (self.pantheon / "docs" / "bff" / "F-042-promotion-review.md").write_text(
            "# F-042 Promotion Review\n",
            encoding="utf-8",
        )
        (self.pantheon / "docs" / "screens" / "F-042-promotion-review.md").write_text(
            "\n".join(
                [
                    "# F-042 Promotion Review Screen",
                    "",
                    "## Classification",
                    "",
                    "- Workbench: Governance Workbench",
                    "- Screen ID: `screen-governance-promotion-review`",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (self.pantheon / "docs" / "examples" / "F-042-review-page.json").write_text(
            '{"status":"ok"}\n',
            encoding="utf-8",
        )
        (self.pantheon / "docs" / "pantheon-handoffs" / "F-042").mkdir(parents=True, exist_ok=True)
        (self.pantheon / "docs" / "pantheon-handoffs" / "F-042" / "FRONTEND_CHANGE_SPEC.md").write_text(
            "# F-042 Frontend Change Spec\n",
            encoding="utf-8",
        )
        (self.pantheon / ".coordination" / "requests" / "F-042-bff-gap.example.yaml").write_text(
            "feature_id: F-042\nsource_repo: front-ai-trading-system\ntype: bff-gap\n",
            encoding="utf-8",
        )
        (self.pantheon / ".coordination" / "requests" / "F-042-ui-done.example.yaml").write_text(
            "feature_id: F-042\nsource_repo: front-ai-trading-system\ntype: ui-done\n",
            encoding="utf-8",
        )

        self.config = {
            "paths": {
                "status_file": str(self.pantheon / "ai-status.json"),
                "activity_log": str(self.pantheon / "ai-activity-log.jsonl"),
                "current_work": str(self.pantheon / "current-work.md"),
                "dashboard": str(self.pantheon / "docs-site" / "index.html"),
                "event_queue": str(self.pantheon / ".orchestrator" / "event-queue.jsonl"),
            },
            "github_bus": {"repo": "ajoe734/pantheon"},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex", "adapter": "codex"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude", "adapter": "claude_cli"},
            },
            "coordination": {
                "enabled": True,
                "repositories": {
                    "pantheon": {"repo": "ajoe734/pantheon", "local_path": str(self.pantheon)},
                    "front_ai_trading_system": {
                        "repo": "ajoe734/front-ai-trading-system",
                        "local_path": str(self.front),
                    },
                },
                "worker_routes": {
                    "pantheon-bff-worker": {"target_agent": "Codex"},
                    "front-sync-worker": {"target_agent": "Codex"},
                    "engine-worker": {"target_agent": "Claude", "requires_human_approval": True},
                },
                "lovable": {
                    "project_url": "https://lovable.dev/projects/140c41d5-9cd8-4d6b-ba02-66d5941d0dbe",
                },
            },
        }

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _write_f042_backend_delivery_bundle(self) -> None:
        (self.front / ".coordination" / "requests" / "F-042-frontend-feedback.yaml").write_text(
            "feature_id: F-042\nsource_repo: front-ai-trading-system\ntype: frontend-feedback\n",
            encoding="utf-8",
        )
        delivery_dir = self.pantheon / "docs" / "pantheon-delivery" / "F-042"
        delivery_dir.mkdir(parents=True, exist_ok=True)
        (delivery_dir / "DELIVERY_NOTE.md").write_text(
            "# F-042 Delivery Note\n",
            encoding="utf-8",
        )
        (delivery_dir / "CONTRACT_LOCK.json").write_text(
            '{"feature_id":"F-042","source_payload":".coordination/requests/F-042-frontend-feedback.yaml"}\n',
            encoding="utf-8",
        )
        (self.pantheon / ".coordination" / "responses" / "F-042-backend-delivery.yaml").write_text(
            "\n".join(
                [
                    "feature_id: F-042",
                    "type: backend-delivery",
                    "target_repo: ajoe734/front-ai-trading-system",
                    "workbench: governance-workbench",
                    "screen_id: screen-governance-promotion-review",
                    "status: delivered",
                    "backend_commit: 2222222222222222222222222222222222222222",
                    "bff_contract_version: pantheon-bff@2222222222222222222222222222222222222222",
                    "delivery_note_path: docs/pantheon-delivery/F-042/DELIVERY_NOTE.md",
                    "contract_lock_path: docs/pantheon-delivery/F-042/CONTRACT_LOCK.json",
                    "followup_expectation: resume the UI cycle against the updated contract.",
                    "source_payload: .coordination/requests/F-042-frontend-feedback.yaml",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def test_bff_gap_request_queues_pantheon_worker(self) -> None:
        request = self.front / ".coordination" / "requests" / "F-042-bff-gap.yaml"
        request.write_text(
            "\n".join(
                [
                    "feature_id: F-042",
                    "source_repo: front-ai-trading-system",
                    "source_branch: ui/F-042-promotion-review",
                    "screen: promotion-review",
                    "type: bff-gap",
                    "summary: Promotion review page is missing allowedActions.canPromoteToPaper",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        state: dict[str, object] = {}
        changed = sync_coordination_files(self.config, state)

        self.assertTrue(changed)
        feature = state["coordination"]["features"]["F-042"]
        self.assertEqual(feature["worker_kind"], "pantheon-bff-worker")
        self.assertIn("needs-bff", feature["state_labels"])
        queue = load_jsonl(Path(self.config["paths"]["event_queue"]))
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["task_id"], "F-042")
        self.assertEqual(queue[0]["metadata"]["coordination"]["worker_kind"], "pantheon-bff-worker")

    def test_contract_ready_generates_lovable_packet_and_front_sync_dispatch(self) -> None:
        self._write_f042_backend_delivery_bundle()
        response = self.pantheon / ".coordination" / "responses" / "F-042-contract-ready.yaml"
        response.write_text(
            "\n".join(
                [
                    "feature_id: F-042",
                    "type: contract-ready",
                    "source_repo: pantheon",
                    "target_repo: front-ai-trading-system",
                    "screen: promotion-review",
                    "pantheon_pr: 128",
                    "base_url: https://pantheon-dev.example.com",
                    "artifacts:",
                    "  bff_contract: docs/bff/F-042-promotion-review.md",
                    "  screen_spec: docs/screens/F-042-promotion-review.md",
                    "  example_payload: docs/examples/F-042-review-page.json",
                    "  lovable_ui_task: .coordination/responses/F-042-lovable-ui-task.yaml",
                    "  backend_delivery: .coordination/responses/F-042-backend-delivery.yaml",
                    "endpoints:",
                    "  - GET /api/v1/operator/deployment-review/F-042",
                    "front_actions_required:",
                    "  - page renders without mock data",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        state: dict[str, object] = {}
        changed = sync_coordination_files(self.config, state)

        self.assertTrue(changed)
        feature = state["coordination"]["features"]["F-042"]
        self.assertEqual(feature["worker_kind"], "front-sync-worker")
        self.assertIn("contract-ready", feature["responses_by_type"])
        self.assertIn("lovable-ui-task", feature["responses_by_type"])
        self.assertTrue(feature["lovable_task_path"].endswith("F-042-lovable-ui-task.yaml"))
        self.assertTrue(Path(feature["lovable_task_path"]).exists())
        self.assertTrue(Path(feature["lovable_prompt_path"]).exists())
        self.assertTrue(feature["responses_by_type"]["contract-ready"]["path"].endswith(".coordination/responses/F-042-contract-ready.yaml"))
        self.assertTrue(feature["responses_by_type"]["lovable-ui-task"]["path"].endswith(".coordination/responses/F-042-lovable-ui-task.yaml"))
        lovable_task_text = Path(feature["lovable_task_path"]).read_text(encoding="utf-8")
        self.assertIn("type: lovable-ui-task", lovable_task_text)
        self.assertIn("workbench: governance-workbench", lovable_task_text)
        self.assertIn("screen_id: screen-governance-promotion-review", lovable_task_text)
        self.assertIn("frontend_change_spec_path: docs/pantheon-handoffs/F-042/FRONTEND_CHANGE_SPEC.md", lovable_task_text)
        self.assertIn("- .coordination/responses/F-042-backend-delivery.yaml", lovable_task_text)
        mirrored_contract = self.front / ".coordination" / "responses" / "F-042-contract-ready.yaml"
        mirrored_backend_delivery = self.front / ".coordination" / "responses" / "F-042-backend-delivery.yaml"
        mirrored_task = self.front / ".coordination" / "responses" / "F-042-lovable-ui-task.yaml"
        mirrored_prompt = self.front / ".coordination" / "responses" / "F-042-lovable-prompt.md"
        mirrored_handoff_contract = self.front / "docs" / "pantheon-handoffs" / "F-042" / "F-042-contract-ready.yaml"
        mirrored_handoff_task = self.front / "docs" / "pantheon-handoffs" / "F-042" / "F-042-lovable-ui-task.yaml"
        mirrored_handoff_prompt = self.front / "docs" / "pantheon-handoffs" / "F-042" / "F-042-lovable-prompt.md"
        mirrored_delivery_note = self.front / "docs" / "pantheon-delivery" / "F-042" / "DELIVERY_NOTE.md"
        mirrored_contract_lock = self.front / "docs" / "pantheon-delivery" / "F-042" / "CONTRACT_LOCK.json"
        mirrored_bff = self.front / "docs" / "pantheon-handoffs" / "F-042" / "bff" / "F-042-promotion-review.md"
        mirrored_screen = self.front / "docs" / "pantheon-handoffs" / "F-042" / "screens" / "F-042-promotion-review.md"
        mirrored_example = self.front / "docs" / "pantheon-handoffs" / "F-042" / "examples" / "F-042-review-page.json"
        mirrored_frontend_change_spec = self.front / "docs" / "pantheon-handoffs" / "F-042" / "FRONTEND_CHANGE_SPEC.md"
        mirrored_gap_template = self.front / ".coordination" / "requests" / "F-042-bff-gap.example.yaml"
        mirrored_done_template = self.front / ".coordination" / "requests" / "F-042-ui-done.example.yaml"
        self.assertTrue(mirrored_contract.exists())
        self.assertTrue(mirrored_backend_delivery.exists())
        self.assertTrue(mirrored_task.exists())
        self.assertTrue(mirrored_prompt.exists())
        self.assertTrue(mirrored_handoff_contract.exists())
        self.assertTrue(mirrored_handoff_task.exists())
        self.assertTrue(mirrored_handoff_prompt.exists())
        self.assertTrue(mirrored_delivery_note.exists())
        self.assertTrue(mirrored_contract_lock.exists())
        self.assertTrue(mirrored_bff.exists())
        self.assertTrue(mirrored_screen.exists())
        self.assertTrue(mirrored_example.exists())
        self.assertTrue(mirrored_frontend_change_spec.exists())
        self.assertTrue(mirrored_gap_template.exists())
        self.assertTrue(mirrored_done_template.exists())
        self.assertIn("mirror_only: true", mirrored_contract.read_text(encoding="utf-8"))
        self.assertIn("mirror_only: true", mirrored_backend_delivery.read_text(encoding="utf-8"))
        self.assertIn("handoff_bundle_dir: docs/pantheon-handoffs/F-042", mirrored_task.read_text(encoding="utf-8"))
        self.assertIn("Completion handoff:", mirrored_prompt.read_text(encoding="utf-8"))
        queue = load_jsonl(Path(self.config["paths"]["event_queue"]))
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["metadata"]["coordination"]["worker_kind"], "front-sync-worker")

    def test_contract_ready_artifacts_are_mirrored_into_front_repo_paths(self) -> None:
        self._write_f042_backend_delivery_bundle()
        response = self.pantheon / ".coordination" / "responses" / "F-042-contract-ready.yaml"
        response.write_text(
            "\n".join(
                [
                    "feature_id: F-042",
                    "type: contract-ready",
                    "source_repo: pantheon",
                    "target_repo: front-ai-trading-system",
                    "screen: promotion-review",
                    "artifacts:",
                    "  bff_contract: docs/bff/F-042-promotion-review.md",
                    "  screen_spec: docs/screens/F-042-promotion-review.md",
                    "  example_payload: docs/examples/F-042-review-page.json",
                    "  lovable_ui_task: .coordination/responses/F-042-lovable-ui-task.yaml",
                    "  backend_delivery: .coordination/responses/F-042-backend-delivery.yaml",
                    "acceptance:",
                    "  - page renders without mock data",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        state: dict[str, object] = {}
        changed = sync_coordination_files(self.config, state)

        self.assertTrue(changed)
        mirrored_contract = self.front / ".coordination" / "responses" / "F-042-contract-ready.yaml"
        mirrored_backend_delivery = self.front / ".coordination" / "responses" / "F-042-backend-delivery.yaml"
        mirrored_delivery_note = self.front / "docs" / "pantheon-delivery" / "F-042" / "DELIVERY_NOTE.md"
        mirrored_contract_lock = self.front / "docs" / "pantheon-delivery" / "F-042" / "CONTRACT_LOCK.json"
        mirrored_bff = self.front / "docs" / "pantheon-handoffs" / "F-042" / "bff" / "F-042-promotion-review.md"
        mirrored_screen = self.front / "docs" / "pantheon-handoffs" / "F-042" / "screens" / "F-042-promotion-review.md"
        mirrored_example = self.front / "docs" / "pantheon-handoffs" / "F-042" / "examples" / "F-042-review-page.json"
        mirrored_frontend_change_spec = self.front / "docs" / "pantheon-handoffs" / "F-042" / "FRONTEND_CHANGE_SPEC.md"
        mirrored_task = self.front / ".coordination" / "responses" / "F-042-lovable-ui-task.yaml"

        self.assertTrue(mirrored_contract.exists())
        self.assertTrue(mirrored_backend_delivery.exists())
        self.assertTrue(mirrored_delivery_note.exists())
        self.assertTrue(mirrored_contract_lock.exists())
        self.assertTrue(mirrored_bff.exists())
        self.assertTrue(mirrored_screen.exists())
        self.assertTrue(mirrored_example.exists())
        self.assertTrue(mirrored_frontend_change_spec.exists())
        self.assertTrue(mirrored_task.exists())

        mirrored_contract_text = mirrored_contract.read_text(encoding="utf-8")
        self.assertIn("bff_contract: docs/pantheon-handoffs/F-042/bff/F-042-promotion-review.md", mirrored_contract_text)
        self.assertIn("screen_spec: docs/pantheon-handoffs/F-042/screens/F-042-promotion-review.md", mirrored_contract_text)
        self.assertIn("example_payload: docs/pantheon-handoffs/F-042/examples/F-042-review-page.json", mirrored_contract_text)
        self.assertIn("backend_delivery: .coordination/responses/F-042-backend-delivery.yaml", mirrored_contract_text)
        self.assertIn("bff_spec_path: docs/pantheon-handoffs/F-042/bff/F-042-promotion-review.md", mirrored_contract_text)

        mirrored_backend_delivery_text = mirrored_backend_delivery.read_text(encoding="utf-8")
        self.assertIn("delivery_note_path: docs/pantheon-delivery/F-042/DELIVERY_NOTE.md", mirrored_backend_delivery_text)
        self.assertIn("contract_lock_path: docs/pantheon-delivery/F-042/CONTRACT_LOCK.json", mirrored_backend_delivery_text)

        mirrored_task_text = mirrored_task.read_text(encoding="utf-8")
        self.assertIn("bff_spec_path: docs/pantheon-handoffs/F-042/bff/F-042-promotion-review.md", mirrored_task_text)
        self.assertIn("ui_spec_path: docs/pantheon-handoffs/F-042/screens/F-042-promotion-review.md", mirrored_task_text)
        self.assertIn("frontend_change_spec_path: docs/pantheon-handoffs/F-042/FRONTEND_CHANGE_SPEC.md", mirrored_task_text)
        self.assertIn("handoff_bundle_dir: docs/pantheon-handoffs/F-042", mirrored_task_text)
        self.assertIn("- docs/pantheon-handoffs/F-042/examples/F-042-review-page.json", mirrored_task_text)
        self.assertIn("- .coordination/responses/F-042-backend-delivery.yaml", mirrored_task_text)

    def test_contract_ready_artifacts_fields_are_mirrored_into_front_repo(self) -> None:
        (self.pantheon / "docs" / "bff" / "PKT-003-lineage-view.md").write_text(
            "# PKT-003 Lineage View BFF Contract\n",
            encoding="utf-8",
        )
        (self.pantheon / "docs" / "screens" / "PKT-003-lineage-view.md").write_text(
            "\n".join(
                [
                    "# PKT-003 Lineage View",
                    "",
                    "## Classification",
                    "",
                    "- Workbench: Evolution Workbench",
                    "- Screen ID: `screen-evolution-lineage`",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (self.pantheon / "docs" / "examples" / "PKT-003-lineage-view.json").write_text(
            '{"status":"ok","feature":"PKT-003-lineage-view"}\n',
            encoding="utf-8",
        )
        (self.pantheon / "docs" / "pantheon-handoffs" / "PKT-003-lineage-view").mkdir(parents=True, exist_ok=True)
        (self.pantheon / "docs" / "pantheon-handoffs" / "PKT-003-lineage-view" / "FRONTEND_CHANGE_SPEC.md").write_text(
            "\n".join(
                [
                    "# PKT-003 Lineage View — Frontend Change Spec",
                    "",
                    "## Feature",
                    "",
                    "- Feature ID: `PKT-003-lineage-view`",
                    "- Screen ID: `screen-evolution-lineage`",
                    "- Workbench: Evolution Workbench",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        response = self.pantheon / ".coordination" / "responses" / "PKT-003-lineage-view-contract-ready.yaml"
        response.write_text(
            "\n".join(
                [
                    "feature_id: PKT-003-lineage-view",
                    "type: contract-ready",
                    "source_repo: pantheon",
                    "target_repo: front-ai-trading-system",
                    "screen: lineage-view",
                    "artifacts:",
                    "  bff_contract: docs/bff/PKT-003-lineage-view.md",
                    "  screen_spec: docs/screens/PKT-003-lineage-view.md",
                    "  example_payload: docs/examples/PKT-003-lineage-view.json",
                    "  lovable_ui_task: .coordination/responses/PKT-003-lineage-view-lovable-ui-task.yaml",
                    "front_actions_required:",
                    "  - build the Lineage list panel from Pantheon BFF data",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        state: dict[str, object] = {}
        changed = sync_coordination_files(self.config, state)

        self.assertTrue(changed)
        mirrored_contract = self.front / ".coordination" / "responses" / "PKT-003-lineage-view-contract-ready.yaml"
        mirrored_task = self.front / ".coordination" / "responses" / "PKT-003-lineage-view-lovable-ui-task.yaml"
        mirrored_prompt = self.front / ".coordination" / "responses" / "PKT-003-lineage-view-lovable-prompt.md"
        mirrored_handoff_contract = self.front / "docs" / "pantheon-handoffs" / "PKT-003-lineage-view" / "PKT-003-lineage-view-contract-ready.yaml"
        mirrored_handoff_task = self.front / "docs" / "pantheon-handoffs" / "PKT-003-lineage-view" / "PKT-003-lineage-view-lovable-ui-task.yaml"
        mirrored_handoff_prompt = self.front / "docs" / "pantheon-handoffs" / "PKT-003-lineage-view" / "PKT-003-lineage-view-lovable-prompt.md"
        mirrored_bff = self.front / "docs" / "pantheon-handoffs" / "PKT-003-lineage-view" / "bff" / "PKT-003-lineage-view.md"
        mirrored_screen = self.front / "docs" / "pantheon-handoffs" / "PKT-003-lineage-view" / "screens" / "PKT-003-lineage-view.md"
        mirrored_example = self.front / "docs" / "pantheon-handoffs" / "PKT-003-lineage-view" / "examples" / "PKT-003-lineage-view.json"
        mirrored_frontend_change = self.front / "docs" / "pantheon-handoffs" / "PKT-003-lineage-view" / "FRONTEND_CHANGE_SPEC.md"

        self.assertTrue(mirrored_contract.exists())
        self.assertTrue(mirrored_task.exists())
        self.assertTrue(mirrored_prompt.exists())
        self.assertTrue(mirrored_handoff_contract.exists())
        self.assertTrue(mirrored_handoff_task.exists())
        self.assertTrue(mirrored_handoff_prompt.exists())
        self.assertTrue(mirrored_bff.exists())
        self.assertTrue(mirrored_screen.exists())
        self.assertTrue(mirrored_example.exists())
        self.assertTrue(mirrored_frontend_change.exists())
        self.assertIn(
            "bff_contract: docs/pantheon-handoffs/PKT-003-lineage-view/bff/PKT-003-lineage-view.md",
            mirrored_contract.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "screen_spec: docs/pantheon-handoffs/PKT-003-lineage-view/screens/PKT-003-lineage-view.md",
            mirrored_contract.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "example_payload: docs/pantheon-handoffs/PKT-003-lineage-view/examples/PKT-003-lineage-view.json",
            mirrored_contract.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "docs/pantheon-handoffs/PKT-003-lineage-view/bff/PKT-003-lineage-view.md",
            mirrored_task.read_text(encoding="utf-8"),
        )
        self.assertIn("docs/pantheon-handoffs/PKT-003-lineage-view/screens/PKT-003-lineage-view.md", mirrored_task.read_text(encoding="utf-8"))
        self.assertIn("docs/pantheon-handoffs/PKT-003-lineage-view/FRONTEND_CHANGE_SPEC.md", mirrored_task.read_text(encoding="utf-8"))
        self.assertIn("Workbench: `evolution-workbench`.", mirrored_prompt.read_text(encoding="utf-8"))
        self.assertIn("References:", mirrored_prompt.read_text(encoding="utf-8"))
        queue = load_jsonl(Path(self.config["paths"]["event_queue"]))
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["metadata"]["coordination"]["worker_kind"], "front-sync-worker")

    def test_contract_ready_requires_valid_front_repo_checkout(self) -> None:
        shutil.rmtree(self.front / ".git")

        response = self.pantheon / ".coordination" / "responses" / "F-042-contract-ready.yaml"
        response.write_text(
            "\n".join(
                [
                    "feature_id: F-042",
                    "type: contract-ready",
                    "source_repo: pantheon",
                    "target_repo: front-ai-trading-system",
                    "screen: promotion-review",
                    "artifacts:",
                    "  bff_contract: docs/bff/F-042-promotion-review.md",
                    "  screen_spec: docs/screens/F-042-promotion-review.md",
                    "  example_payload: docs/examples/F-042-review-page.json",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        with self.assertRaises(RuntimeError) as exc_info:
            sync_coordination_files(self.config, {})

        self.assertIn("front-ai-trading-system checkout is invalid", str(exc_info.exception))

    def test_ui_done_request_queues_front_sync_worker(self) -> None:
        request = self.front / ".coordination" / "requests" / "F-042-ui-done.yaml"
        request.write_text(
            "\n".join(
                [
                    "feature_id: F-042",
                    "source_repo: front-ai-trading-system",
                    "source_branch: main",
                    "screen: promotion-review",
                    "type: ui-done",
                    "summary: Promotion Review UI implemented and synced back to GitHub",
                    "changed_files:",
                    "  - src/pages/promotion/PromotionReview.tsx",
                    "  - src/pages/promotion/types.ts",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        state: dict[str, object] = {}
        changed = sync_coordination_files(self.config, state)

        self.assertTrue(changed)
        feature = state["coordination"]["features"]["F-042"]
        self.assertEqual(feature["worker_kind"], "front-sync-worker")
        self.assertIn("qa-ready", feature["state_labels"])
        self.assertEqual(feature["latest_request"]["type"], "ui-done")
        self.assertTrue(feature["latest_request_path"].endswith(".coordination/requests/F-042-ui-done.yaml"))
        self.assertTrue(feature["requests_by_type"]["ui-done"]["path"].endswith(".coordination/requests/F-042-ui-done.yaml"))
        queue = load_jsonl(Path(self.config["paths"]["event_queue"]))
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["metadata"]["coordination"]["payload_type"], "ui-done")
        self.assertEqual(queue[0]["metadata"]["coordination"]["worker_kind"], "front-sync-worker")

    def test_frontend_feedback_request_is_recorded_for_closed_loop_visibility(self) -> None:
        request = self.front / ".coordination" / "requests" / "F-042-frontend-feedback.yaml"
        request.write_text(
            "\n".join(
                [
                    "feature_id: F-042",
                    "source_repo: front-ai-trading-system",
                    "source_branch: main",
                    "screen: promotion-review",
                    "type: frontend-feedback",
                    "summary: Promotion Review UI feedback bundle is ready for Pantheon review",
                    "required_feedback:",
                    "  - ux-signoff",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        state: dict[str, object] = {}
        changed = sync_coordination_files(self.config, state)

        self.assertTrue(changed)
        feature = state["coordination"]["features"]["F-042"]
        self.assertIn("feedback-ready", feature["state_labels"])
        self.assertEqual(feature["latest_request"]["type"], "frontend-feedback")
        self.assertTrue(
            feature["requests_by_type"]["frontend-feedback"]["path"].endswith(".coordination/requests/F-042-frontend-feedback.yaml")
        )
        queue = load_jsonl(Path(self.config["paths"]["event_queue"]))
        self.assertEqual(queue, [])

    def test_example_requests_are_ignored(self) -> None:
        request = self.front / ".coordination" / "requests" / "F-042-ui-done.example.yaml"
        request.write_text(
            "\n".join(
                [
                    "feature_id: F-042",
                    "source_repo: front-ai-trading-system",
                    "type: ui-done",
                    "summary: Example only",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        state: dict[str, object] = {}
        changed = sync_coordination_files(self.config, state)

        self.assertFalse(changed)
        self.assertEqual(state["coordination"]["features"], {})
        queue = load_jsonl(Path(self.config["paths"]["event_queue"]))
        self.assertEqual(queue, [])

    def test_resolved_request_is_recorded_but_not_dispatched(self) -> None:
        request = self.front / ".coordination" / "requests" / "F-042-bff-gap.yaml"
        request.write_text(
            "\n".join(
                [
                    "feature_id: F-042",
                    "source_repo: front-ai-trading-system",
                    "source_branch: main",
                    "screen: promotion-review",
                    "type: bff-gap",
                    'summary: "Resolved: command payload schema now published."',
                    'resolved_at: "2026-04-12T09:14:05+08:00"',
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        state: dict[str, object] = {}
        changed = sync_coordination_files(self.config, state)

        self.assertTrue(changed)
        feature = state["coordination"]["features"]["F-042"]
        self.assertEqual(feature["latest_request"]["type"], "bff-gap")
        queue = load_jsonl(Path(self.config["paths"]["event_queue"]))
        self.assertEqual(queue, [])


if __name__ == "__main__":
    unittest.main()
