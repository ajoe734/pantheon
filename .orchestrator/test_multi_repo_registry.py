#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import multi_repo_registry


class MultiRepoRegistryTests(unittest.TestCase):
    def test_default_registry_has_canonical_pantheon_github_slug(self) -> None:
        repo = multi_repo_registry.resolve_repository({}, "pantheon")

        self.assertEqual(repo["repo"], "ajoe734/pantheon")

    def test_coordination_registry_overrides_pantheon_github_slug(self) -> None:
        config = {
            "coordination": {
                "repositories": {
                    "pantheon": {"repo": "example/pantheon-fork"}
                }
            }
        }

        repo = multi_repo_registry.resolve_repository(config, "pantheon")

        self.assertEqual(repo["repo"], "example/pantheon-fork")

    def test_default_registry_includes_execute_plans_checkout(self) -> None:
        repo = multi_repo_registry.resolve_repository({}, "execute_plans")

        self.assertEqual(repo["display_name"], "execute-plans")
        self.assertEqual(repo["repo"], "ajoe734/execute-plans")
        self.assertEqual(repo["default_branch"], "dev")
        self.assertEqual(
            repo["resolved_local_path"],
            (Path(multi_repo_registry.__file__).resolve().parents[1] / "../code/execute-plans").resolve(),
        )

    def test_relative_repository_path_is_anchored_to_status_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            status_root = Path(directory) / "pantheon-status"
            config = {
                "paths": {"status_file": str(status_root / "ai-status.json")},
                "coordination": {
                    "repositories": {
                        "execute_plans": {"local_path": "../delivery/execute-plans"}
                    }
                },
            }

            repo = multi_repo_registry.resolve_repository(config, "execute_plans")

        self.assertEqual(
            repo["resolved_local_path"],
            (status_root / "../delivery/execute-plans").resolve(),
        )

    def test_execute_plans_artifact_prefix_routes_to_sibling_repo(self) -> None:
        artifact = "execute-plans/e2e/dummy.spec.ts"

        self.assertEqual(multi_repo_registry.artifact_repository_id({}, artifact), "execute_plans")
        self.assertEqual(
            multi_repo_registry.repository_relative_artifact_path({}, artifact),
            Path("e2e/dummy.spec.ts"),
        )

    def test_execute_plans_colon_artifact_prefix_routes_to_sibling_repo(self) -> None:
        artifact = "execute-plans:e2e/dummy.spec.ts"

        self.assertEqual(multi_repo_registry.artifact_repository_id({}, artifact), "execute_plans")
        self.assertEqual(
            multi_repo_registry.repository_relative_artifact_path({}, artifact),
            Path("e2e/dummy.spec.ts"),
        )

    def test_unregistered_colon_path_remains_a_pantheon_artifact(self) -> None:
        artifact = "services/control-plane:bff/main.py"

        self.assertEqual(multi_repo_registry.artifact_repository_id({}, artifact), "pantheon")

    def test_task_primary_repository_prefers_single_non_pantheon_artifact_repo(self) -> None:
        task = {
            "id": "FE-INT-GATE-DUMMY",
            "artifacts": [
                "execute-plans/e2e/dummy.spec.ts",
                "support/evidence/FE-INT-GATE-DUMMY.json",
            ],
        }

        self.assertEqual(multi_repo_registry.task_artifact_repository_ids({}, task), ["execute_plans", "pantheon"])
        self.assertEqual(multi_repo_registry.task_primary_repository_id({}, task), "execute_plans")

    def test_task_primary_repository_rejects_multiple_non_pantheon_repos(self) -> None:
        task = {
            "id": "CROSS-REPO",
            "artifacts": [
                "execute-plans/e2e/dummy.spec.ts",
                "lean-platform/src/runtime.py",
            ],
        }

        self.assertIsNone(multi_repo_registry.task_primary_repository_id({}, task))


if __name__ == "__main__":
    unittest.main()
