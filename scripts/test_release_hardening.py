#!/usr/bin/env python3
from __future__ import annotations

import json
import tarfile
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_hardening


class ReleaseHardeningTests(unittest.TestCase):
    def test_is_generated_ephemeral_classifies_runtime_artifacts(self) -> None:
        self.assertTrue(release_hardening.is_generated_ephemeral(".orchestrator/state.json"))
        self.assertTrue(release_hardening.is_generated_ephemeral("docs-site/dashboard-bundle.json"))
        self.assertTrue(release_hardening.is_generated_ephemeral("foo/__pycache__/bar.pyc"))
        self.assertTrue(release_hardening.is_generated_ephemeral("README.md:Zone.Identifier"))
        self.assertFalse(release_hardening.is_generated_ephemeral("ai-status.json"))
        self.assertFalse(release_hardening.is_generated_ephemeral("docs-site/index.html"))

    def test_build_release_cleanup_report_flags_tracked_generated_and_dirty_non_generated(self) -> None:
        with self._git_repo() as repo:
            self._write(repo / "AI_COLLABORATION_GUIDE.md", "guide\n")
            self._write(repo / "ai-status.json", json.dumps({"canonical_files": ["AI_COLLABORATION_GUIDE.md"]}) + "\n")
            self._write(repo / "ai-activity-log.jsonl", "")
            self._write(repo / "current-work.md", "work\n")
            self._write(repo / ".orchestrator" / "state.json", "{}\n")
            self._git(repo, "add", ".")
            self._git(repo, "commit", "-m", "baseline")

            self._write(repo / "current-work.md", "work updated\n")
            report = release_hardening.build_release_cleanup_report(repo)

            self.assertIn(".orchestrator/state.json", report["tracked_generated_files"])
            dirty_non_generated_paths = {entry["path"] for entry in report["dirty_non_generated"]}
            self.assertIn("current-work.md", dirty_non_generated_paths)
            self.assertFalse(report["ok"])

    def test_copy_release_tree_excludes_generated_tracked_files(self) -> None:
        with self._git_repo() as repo, tempfile.TemporaryDirectory(prefix="release-stage-") as stage_dir:
            self._write(repo / "AI_COLLABORATION_GUIDE.md", "guide\n")
            self._write(repo / "ai-status.json", json.dumps({"canonical_files": ["AI_COLLABORATION_GUIDE.md"]}) + "\n")
            self._write(repo / "ai-activity-log.jsonl", "")
            self._write(repo / "current-work.md", "work\n")
            self._write(repo / ".orchestrator" / "state.json", "{}\n")
            self._git(repo, "add", ".")
            self._git(repo, "commit", "-m", "baseline")

            included = release_hardening.copy_release_tree(repo, Path(stage_dir))
            self.assertIn("AI_COLLABORATION_GUIDE.md", included)
            self.assertNotIn(".orchestrator/state.json", included)
            self.assertTrue((Path(stage_dir) / "AI_COLLABORATION_GUIDE.md").exists())
            self.assertFalse((Path(stage_dir) / ".orchestrator" / "state.json").exists())

    def test_canonical_revision_set_reads_tracked_blob_hashes(self) -> None:
        with self._git_repo() as repo:
            self._write(repo / "AI_COLLABORATION_GUIDE.md", "guide\n")
            self._write(repo / "current-work.md", "work\n")
            self._write(
                repo / "ai-status.json",
                json.dumps({"canonical_files": ["AI_COLLABORATION_GUIDE.md", "current-work.md"]}) + "\n",
            )
            self._write(repo / "ai-activity-log.jsonl", "")
            self._git(repo, "add", ".")
            self._git(repo, "commit", "-m", "baseline")

            revisions = release_hardening.canonical_revision_set(repo)
            self.assertEqual(
                [entry["path"] for entry in revisions],
                ["AI_COLLABORATION_GUIDE.md", "current-work.md"],
            )
            self.assertTrue(all(len(entry["blob"]) == 40 for entry in revisions))

    def test_create_release_artifacts_builds_tarball_and_reports(self) -> None:
        with self._git_repo() as repo:
            self._write(repo / "AI_COLLABORATION_GUIDE.md", "guide\n")
            self._write(repo / "current-work.md", "work\n")
            self._write(repo / "ai-activity-log.jsonl", "")
            self._write(repo / "docs-site" / "index.html", "<html></html>\n")
            self._write(
                repo / "ai-status.json",
                json.dumps(
                    {
                        "canonical_files": [
                            "AI_COLLABORATION_GUIDE.md",
                            "current-work.md",
                            "docs-site/index.html",
                        ]
                    }
                )
                + "\n",
            )
            self._git(repo, "add", ".")
            self._git(repo, "commit", "-m", "baseline")

            output_dir = repo / "release-out"
            with mock.patch.object(
                release_hardening,
                "run_bff_verification",
                return_value={"status": "passed", "verified_at": "2026-04-12T00:00:00Z", "venv": {}, "steps": []},
            ), mock.patch.object(
                release_hardening,
                "sync_docs_site_state",
                return_value={"status": "passed", "synced_at": "2026-04-12T00:00:00Z", "generated_paths": []},
            ):
                result = release_hardening.create_release_artifacts(
                    root=repo,
                    output_dir=output_dir,
                    verbose=False,
                )

            self.assertEqual(result["status"], "passed")
            tarball_path = Path(result["tarball_path"])
            manifest_path = Path(result["manifest_path"])
            verification_path = Path(result["verification_path"])
            self.assertTrue(tarball_path.exists())
            self.assertTrue(manifest_path.exists())
            self.assertTrue(verification_path.exists())

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["bff_verification"]["status"], "passed")
            self.assertEqual(manifest["docs_site_sync"]["status"], "passed")

            with tarfile.open(tarball_path, "r:gz") as archive:
                names = archive.getnames()
            self.assertTrue(any(name.endswith("AI_COLLABORATION_GUIDE.md") for name in names))
            self.assertTrue(any(name.endswith("RELEASE_MANIFEST.json") for name in names))
            self.assertTrue(any(name.endswith("VERIFICATION.md") for name in names))

    def _git_repo(self):
        return _GitRepoContext()

    def _git(self, repo: Path, *args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)

    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


class _GitRepoContext:
    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(prefix="release-hardening-")
        repo = Path(self._tmp.name)
        subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "tests@example.com"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Release Hardening Tests"],
            check=True,
            capture_output=True,
            text=True,
        )
        return repo

    def __exit__(self, exc_type, exc, tb) -> None:
        self._tmp.cleanup()
