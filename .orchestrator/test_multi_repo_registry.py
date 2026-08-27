#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import multi_repo_registry


class MultiRepoRegistryTests(unittest.TestCase):
    def test_default_registry_has_canonical_pantheon_github_slug(self) -> None:
        repo = multi_repo_registry.resolve_repository({}, "pantheon")

        self.assertEqual(repo["repo"], "ajoe734/pantheon")
        self.assertEqual(repo["default_branch"], "dev")

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

    def test_deployment_registry_path_overrides_status_root_for_source_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status_root = root / "coordination-root"
            source_root = root / "dev-root"
            config = {
                "paths": {"status_file": str(status_root / "ai-status.json")},
                "coordination": {
                    "repositories": {"pantheon": {"local_path": str(source_root)}}
                },
            }

            repo = multi_repo_registry.resolve_repository(config, "pantheon")

        self.assertEqual(repo["resolved_local_path"], source_root.resolve())

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

    def test_artifact_repository_id_propagates_default_repo_when_unprefixed(self) -> None:
        unprefixed = "support/sidecars/AG-FE-DB-002/evidence.json"
        explicit_pantheon = "pantheon:services/bff/main.py"
        explicit_execute = "execute-plans:src/App.tsx"

        self.assertEqual(
            multi_repo_registry.artifact_repository_id({}, unprefixed, "execute_plans"),
            "execute_plans",
        )
        self.assertEqual(
            multi_repo_registry.artifact_repository_id({}, explicit_pantheon, "execute_plans"),
            "pantheon",
        )
        self.assertEqual(
            multi_repo_registry.artifact_repository_id({}, explicit_execute, "pantheon"),
            "execute_plans",
        )

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

    def test_unprefixed_artifacts_follow_explicit_target_repo(self) -> None:
        task = {
            "id": "FE-SIDECAR-627",
            "target_repo": "execute-plans",
            "artifacts": [
                "support/sidecars/AG-FE-DB-002/evidence.json",
                "docs/review.md",
            ],
        }

        self.assertEqual(
            multi_repo_registry.task_artifact_repository_ids({}, task),
            ["execute_plans"],
        )
        self.assertEqual(
            multi_repo_registry.task_primary_repository_id({}, task),
            "execute_plans",
        )
        self.assertEqual(
            multi_repo_registry.validate_task_repository_scope({}, task),
            "execute_plans",
        )

    def test_explicit_execute_plans_prefix_authoritative_without_target_repo(self) -> None:
        task_slash = {
            "id": "FE-SLASH",
            "artifacts": ["execute-plans/src/App.tsx"],
        }
        task_colon = {
            "id": "FE-COLON",
            "artifacts": ["execute-plans:src/App.tsx"],
        }

        self.assertEqual(
            multi_repo_registry.task_primary_repository_id({}, task_slash),
            "execute_plans",
        )
        self.assertEqual(
            multi_repo_registry.task_primary_repository_id({}, task_colon),
            "execute_plans",
        )

    def test_conflicting_target_repo_and_artifact_prefix_rejected(self) -> None:
        task = {
            "id": "CONFLICT-001",
            "target_repo": "pantheon",
            "artifacts": ["execute-plans/src/App.tsx"],
        }

        self.assertIsNone(multi_repo_registry.task_primary_repository_id({}, task))
        with self.assertRaises(ValueError) as ctx:
            multi_repo_registry.validate_task_repository_scope({}, task)
        self.assertIn("conflicting repository scope", str(ctx.exception))
        self.assertIn("execute_plans", str(ctx.exception))

    def test_ambiguous_compound_target_repo_rejected(self) -> None:
        task = {
            "id": "MULTI-001",
            "target_repo": "pantheon+execute-plans",
            "artifacts": ["src/App.tsx"],
        }

        self.assertIsNone(multi_repo_registry.task_primary_repository_id({}, task))
        with self.assertRaises(ValueError) as ctx:
            multi_repo_registry.validate_task_repository_scope({}, task)
        self.assertIn("ambiguous multi-repository target_repo", str(ctx.exception))

    def test_unrecognized_target_repo_rejected(self) -> None:
        task = {
            "id": "UNKNOWN-001",
            "target_repo": "bogus-repository-xyz",
            "artifacts": ["src/App.tsx"],
        }

        self.assertIsNone(multi_repo_registry.task_primary_repository_id({}, task))
        with self.assertRaises(ValueError) as ctx:
            multi_repo_registry.validate_task_repository_scope({}, task)
        self.assertIn("unrecognized target_repo", str(ctx.exception))

    def test_target_repo_in_source_ref_or_metadata_resolved(self) -> None:
        task_source_ref = {
            "id": "FE-SR-001",
            "source_ref": {"target_repo": "execute-plans"},
            "artifacts": ["support/sidecars/evidence.json"],
        }
        task_metadata = {
            "id": "FE-MD-001",
            "metadata": {"target_repo": "ajoe734/execute-plans"},
            "artifacts": ["support/sidecars/evidence.json"],
        }

        self.assertEqual(
            multi_repo_registry.task_primary_repository_id({}, task_source_ref),
            "execute_plans",
        )
        self.assertEqual(
            multi_repo_registry.task_primary_repository_id({}, task_metadata),
            "execute_plans",
        )


if __name__ == "__main__":
    unittest.main()
