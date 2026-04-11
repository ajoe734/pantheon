#!/usr/bin/env python3
from __future__ import annotations

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
            (repo_root / ".coordination" / "requests").mkdir(parents=True, exist_ok=True)
            (repo_root / ".coordination" / "responses").mkdir(parents=True, exist_ok=True)
            (repo_root / "docs-site").mkdir(parents=True, exist_ok=True)
            (repo_root / "ai-status.json").write_text('{"tasks":[],"handoffs":[]}\n', encoding="utf-8")
            (repo_root / "current-work.md").write_text("# current work\n", encoding="utf-8")
            (repo_root / "ai-activity-log.jsonl").write_text("", encoding="utf-8")
            (repo_root / "docs-site" / "index.html").write_text("<html></html>\n", encoding="utf-8")
        (self.pantheon / "docs" / "bff").mkdir(parents=True, exist_ok=True)
        (self.pantheon / "docs" / "examples").mkdir(parents=True, exist_ok=True)
        (self.pantheon / "docs" / "bff" / "F-042-promotion-review.md").write_text(
            "# F-042 Promotion Review\n",
            encoding="utf-8",
        )
        (self.pantheon / "docs" / "examples" / "F-042-review-page.json").write_text(
            '{"status":"ok"}\n',
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
                    "endpoint:",
                    "  - GET /api/v1/operator/deployment-review/F-042",
                    "bff_spec_path: docs/bff/F-042-promotion-review.md",
                    "examples:",
                    "  - docs/examples/F-042-review-page.json",
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
        feature = state["coordination"]["features"]["F-042"]
        self.assertEqual(feature["worker_kind"], "front-sync-worker")
        self.assertTrue(feature["lovable_task_path"].endswith("F-042-lovable-ui-task.yaml"))
        self.assertTrue(Path(feature["lovable_task_path"]).exists())
        self.assertTrue(Path(feature["lovable_prompt_path"]).exists())
        lovable_task_text = Path(feature["lovable_task_path"]).read_text(encoding="utf-8")
        self.assertIn("type: lovable-ui-task", lovable_task_text)
        mirrored_contract = self.front / ".coordination" / "responses" / "F-042-contract-ready.yaml"
        mirrored_task = self.front / ".coordination" / "responses" / "F-042-lovable-ui-task.yaml"
        mirrored_prompt = self.front / ".coordination" / "responses" / "F-042-lovable-prompt.md"
        mirrored_bff = self.front / "docs" / "pantheon-handoffs" / "F-042" / "F-042-promotion-review.md"
        mirrored_example = self.front / "docs" / "pantheon-handoffs" / "F-042" / "F-042-review-page.json"
        mirrored_gap_template = self.front / ".coordination" / "requests" / "F-042-bff-gap.example.yaml"
        mirrored_done_template = self.front / ".coordination" / "requests" / "F-042-ui-done.example.yaml"
        self.assertTrue(mirrored_contract.exists())
        self.assertTrue(mirrored_task.exists())
        self.assertTrue(mirrored_prompt.exists())
        self.assertTrue(mirrored_bff.exists())
        self.assertTrue(mirrored_example.exists())
        self.assertTrue(mirrored_gap_template.exists())
        self.assertTrue(mirrored_done_template.exists())
        self.assertIn("mirror_only: true", mirrored_contract.read_text(encoding="utf-8"))
        self.assertIn("Completion handoff:", mirrored_prompt.read_text(encoding="utf-8"))
        queue = load_jsonl(Path(self.config["paths"]["event_queue"]))
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["metadata"]["coordination"]["worker_kind"], "front-sync-worker")

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
        queue = load_jsonl(Path(self.config["paths"]["event_queue"]))
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["metadata"]["coordination"]["payload_type"], "ui-done")
        self.assertEqual(queue[0]["metadata"]["coordination"]["worker_kind"], "front-sync-worker")

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


if __name__ == "__main__":
    unittest.main()
