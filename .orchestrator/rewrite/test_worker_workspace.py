"""DTG-CLEAN-M4 characterization tests for the standalone worker-workspace
filesystem module -- not a re-test of .orchestrator/test_supervisor.py's
extensive coverage (which already exercises this exact code through
supervisor.py's re-export and continues to pass unchanged), but proof that
this module is genuinely usable on its own: no circular import, and the
lazy supervisor handback resolves for the handful of symbols supervisor.py
still owns.
"""
from __future__ import annotations

import sys
import json
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import worker_workspace


class WorkerWorkspaceModuleTests(unittest.TestCase):
    def test_module_imports_with_no_circular_dependency(self) -> None:
        # supervisor.py imports this module at its own top level; importing
        # supervisor here (a second, independent path into the same
        # dependency graph) must not raise, proving the graph is a DAG
        # (supervisor -> worker_workspace -> {common, dispatch_policy,
        # multi_repo_registry, adapters.base}, with the reverse edge only
        # ever taken lazily, at call time, via _supervisor_module()).
        import supervisor  # noqa: F401

    def test_lazy_supervisor_handback_resolves(self) -> None:
        supervisor = worker_workspace._supervisor_module()
        self.assertTrue(hasattr(supervisor, "write_activity_log"))
        self.assertTrue(hasattr(supervisor, "pid_is_alive"))
        self.assertTrue(hasattr(supervisor, "parse_runtime_timestamp"))
        self.assertTrue(hasattr(supervisor, "materialize_worker_context_files"))
        self.assertTrue(hasattr(supervisor, "bind_external_worker_context"))

    def test_pure_helpers_work_standalone(self) -> None:
        self.assertEqual(worker_workspace._task_id_slug("REG-002"), "reg-002")
        self.assertEqual(worker_workspace._task_id_slug(None), "unknown-task")
        self.assertEqual(
            worker_workspace.worker_task_branch({}, "REG-002"), "task/REG-002"
        )
        clean, dirty_paths = worker_workspace._classify_worktree_dirt("")
        self.assertEqual(clean, "clean")
        self.assertEqual(dirty_paths, [])

    def test_settings_helpers_apply_defaults(self) -> None:
        settings = worker_workspace.worktree_cleanup_settings({})
        self.assertTrue(settings["enabled"])
        self.assertTrue(settings["cleanup_inactive_leases"])
        self.assertGreater(settings["orphan_prune_interval_seconds"], 0)

    def test_entry_points_are_exported(self) -> None:
        for name in (
            "prepare_worker_workspace",
            "cleanup_inactive_worker_worktrees",
            "prune_orphan_worktrees",
            "active_worker_workspace_roots",
            "validate_worker_workspace_binding",
        ):
            self.assertTrue(callable(getattr(worker_workspace, name)), name)


class RecoveryWorktreeQuarantineTests(unittest.TestCase):
    """Recovery archives WIP without replacing the committed task source."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="recovery source ")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self._git(self.repo, "init", "-b", "dev")
        self._git(self.repo, "config", "user.name", "Test")
        self._git(self.repo, "config", "user.email", "test@example.com")
        (self.repo / "source.txt").write_text("base\n", encoding="utf-8")
        (self.repo / "deleted source.txt").write_text("keep in source\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text("*.cache\n", encoding="utf-8")
        self._git(self.repo, "add", ".")
        self._git(self.repo, "commit", "-m", "initial")
        self.base_sha = self._git(self.repo, "rev-parse", "HEAD")
        self.branch = "task/TASK-RECOVERY"
        self.worktree = self.root / "task worktree"
        self._git(
            self.repo, "worktree", "add", "-b", self.branch,
            str(self.worktree), self.base_sha,
        )
        (self.worktree / "source.txt").write_text("committed task source\n", encoding="utf-8")
        self._git(self.worktree, "add", "source.txt")
        self._git(self.worktree, "commit", "-m", "task source ahead of dev")
        self.source_head = self._git(self.worktree, "rev-parse", "HEAD")
        self.archive_root = self.root / "archive"

    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout.strip()

    def _dirty_worktree(self) -> None:
        (self.worktree / "source.txt").write_text("staged task WIP\n", encoding="utf-8")
        (self.worktree / "staged new.txt").write_text("staged addition\n", encoding="utf-8")
        self._git(self.worktree, "add", "source.txt", "staged new.txt")
        (self.worktree / "source.txt").write_text("unstaged task WIP\n", encoding="utf-8")
        (self.worktree / "staged new.txt").write_text("unstaged addition\n", encoding="utf-8")
        (self.worktree / "deleted source.txt").unlink()
        (self.worktree / "draft notes.txt").write_bytes(b"untracked WIP\x00\n")
        (self.worktree / "local.cache").write_bytes(b"ignored local state\n")

    def _snapshot(self, worktree: Path | None = None) -> dict:
        path = worktree or self.worktree
        return {
            "head": self._git(path, "rev-parse", "HEAD"),
            "status": self._git(path, "status", "--porcelain", "--untracked-files=all"),
            "staged": self._git(path, "diff", "--cached", "--binary"),
            "unstaged": self._git(path, "diff", "--binary"),
            "files": {
                str(file.relative_to(path)): (
                    ("symlink", str(file.readlink()))
                    if file.is_symlink() else ("file", file.read_bytes())
                )
                for file in path.rglob("*")
                if file.name != ".git" and (file.is_file() or file.is_symlink())
            },
        }

    def _quarantine(
        self, publish_archive, *, max_file_bytes: int = 1024 * 1024, existing_archive=None
    ):
        return worker_workspace._quarantine_recovery_worktree(
            self.repo,
            self.worktree,
            branch=self.branch,
            archive_root=self.archive_root,
            task_id="TASK-RECOVERY",
            repository_id="pantheon",
            max_file_bytes=max_file_bytes,
            publish_archive=publish_archive,
            existing_archive=existing_archive,
        )

    def _assert_quarantine_preserves_committed_source(self) -> None:
        self._dirty_worktree()
        original = self._snapshot()
        worktree_inode = self.worktree.stat().st_ino
        git_registration = (self.worktree / ".git").read_bytes()
        commit_count = self._git(self.repo, "rev-list", "--all", "--count")
        published = []

        def publish(binding):
            # Canonical publication is the boundary before any WIP is removed.
            self.assertEqual(self._snapshot(), original)
            self.assertEqual(binding["repository_id"], "pantheon")
            self.assertEqual(binding["workspace_path"], str(self.worktree))
            self.assertEqual(binding["branch"], self.branch)
            self.assertEqual(binding["source_head"], self.source_head)
            archive = Path(binding["archive_path"])
            self.assertTrue((archive / "manifest.json").is_file())
            self.assertEqual(
                self._git(self.repo, "rev-parse", binding["preserved_branch_ref"]),
                self.source_head,
            )
            published.append(dict(binding))
            return True

        ok, status, binding = self._quarantine(publish)
        self.assertTrue(ok, status)
        self.assertEqual(published, [binding])
        assert binding is not None
        self.assertEqual(self._git(self.worktree, "rev-parse", "HEAD"), self.source_head)
        self.assertEqual(self._git(self.repo, "rev-parse", self.branch), self.source_head)
        self.assertEqual(self._git(self.repo, "rev-list", "--all", "--count"), commit_count)
        self.assertEqual(self._git(self.worktree, "status", "--porcelain"), "")
        self.assertEqual(self.worktree.stat().st_ino, worktree_inode)
        self.assertEqual((self.worktree / ".git").read_bytes(), git_registration)
        self.assertIn(
            f"worktree {self.worktree}\n", self._git(self.repo, "worktree", "list", "--porcelain")
        )
        self.assertEqual(
            (self.worktree / "source.txt").read_text(encoding="utf-8"), "committed task source\n"
        )
        self.assertEqual(
            (self.worktree / "deleted source.txt").read_text(encoding="utf-8"), "keep in source\n"
        )
        self.assertEqual((self.worktree / "local.cache").read_bytes(), b"ignored local state\n")
        self.assertFalse((self.worktree / "draft notes.txt").exists())
        self.assertFalse((self.worktree / "staged new.txt").exists())

        archive = Path(binding["archive_path"])
        manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["preserved_branch_ref"], binding["preserved_branch_ref"])
        for name in ("source.txt", "staged new.txt", "draft notes.txt"):
            self.assertEqual((archive / "files" / name).read_bytes(), original["files"][name][1])

        # Restoring both patches on the preserved source reproduces the index
        # and working tree, including a tracked deletion and paths with spaces.
        replay = self.root / "replayed worktree"
        self._git(self.repo, "worktree", "add", "--detach", str(replay), binding["source_head"])
        self._git(replay, "apply", "--index", str(archive / "diff-staged.patch"))
        self._git(replay, "apply", str(archive / "diff.patch"))
        shutil.copy2(archive / "files" / "draft notes.txt", replay / "draft notes.txt")
        shutil.copy2(self.worktree / "local.cache", replay / "local.cache")
        self.assertEqual(self._snapshot(replay), original)

    def test_quarantine_preserves_ahead_task_head_and_recoverable_wip(self) -> None:
        self.assertNotEqual(self.source_head, self.base_sha)
        self._assert_quarantine_preserves_committed_source()

    def test_quarantine_preserves_diverged_task_head(self) -> None:
        (self.repo / "dev only.txt").write_text("new dev source\n", encoding="utf-8")
        self._git(self.repo, "add", "dev only.txt")
        self._git(self.repo, "commit", "-m", "dev advances independently")
        dev_head = self._git(self.repo, "rev-parse", "dev")
        self.assertEqual(self._git(self.repo, "merge-base", "dev", self.branch), self.base_sha)
        self._assert_quarantine_preserves_committed_source()
        self.assertEqual(self._git(self.repo, "rev-parse", "dev"), dev_head)
        self.assertFalse((self.worktree / "dev only.txt").exists())

    def test_quarantine_replays_distinct_crlf_index_and_worktree_bytes(self) -> None:
        staged_bytes = b"only in index\r\nsecond staged line\r\n"
        working_bytes = b"only in worktree\r\nsecond unstaged line\r\n"
        source = self.worktree / "source.txt"
        source.write_bytes(staged_bytes)
        self._git(self.worktree, "add", "source.txt")
        source.write_bytes(working_bytes)

        ok, status, binding = self._quarantine(mock.Mock(return_value=True))
        self.assertTrue(ok, status)
        archive = Path(binding["archive_path"])
        replay = self.root / "crlf replay"
        self._git(self.repo, "worktree", "add", "--detach", str(replay), binding["source_head"])
        self._git(replay, "apply", "--index", str(archive / "diff-staged.patch"))
        self._git(replay, "apply", str(archive / "diff.patch"))
        restored_index = subprocess.run(
            ["git", "show", ":source.txt"], cwd=replay,
            capture_output=True, check=True,
        ).stdout
        self.assertEqual(restored_index, staged_bytes)
        self.assertEqual((replay / "source.txt").read_bytes(), working_bytes)
        self.assertEqual((archive / "files" / "source.txt").read_bytes(), working_bytes)

    def _assert_publication_mutation_preserved(self, mutation, expected_status) -> None:
        self._dirty_worktree()
        changed = []

        def publish(binding):
            mutation()
            changed.append(self._snapshot())
            return True

        ok, status, binding = self._quarantine(publish)
        self.assertFalse(ok)
        self.assertEqual(status, expected_status)
        self.assertEqual(len(changed), 1)
        self.assertEqual(self._snapshot(), changed[0])
        self.assertTrue((Path(binding["archive_path"]) / "manifest.json").is_file())

    def test_tracked_change_during_publication_is_not_overwritten(self) -> None:
        self._assert_publication_mutation_preserved(
            lambda: (self.worktree / "source.txt").write_bytes(b"late tracked WIP\n"),
            "recovery_wip_changed_after_publication",
        )

    def test_index_change_during_publication_is_not_overwritten(self) -> None:
        def mutate_index():
            source = self.worktree / "source.txt"
            original_working_bytes = source.read_bytes()
            source.write_bytes(b"late staged WIP\n")
            self._git(self.worktree, "add", "source.txt")
            source.write_bytes(original_working_bytes)

        self._assert_publication_mutation_preserved(
            mutate_index, "recovery_wip_changed_after_publication",
        )

    def test_head_change_during_publication_is_not_overwritten(self) -> None:
        self._assert_publication_mutation_preserved(
            lambda: self._git(self.worktree, "commit", "-m", "late committed source"),
            "recovery_branch_changed_after_publication",
        )

    def test_publication_denied_leaves_index_working_tree_and_source_unchanged(self) -> None:
        self._dirty_worktree()
        original = self._snapshot()
        publish = mock.Mock(return_value=False)
        ok, status, _binding = self._quarantine(publish)
        self.assertFalse(ok, status)
        publish.assert_called_once()
        archived_binding = publish.call_args.args[0]
        self.assertTrue(Path(archived_binding["archive_path"]).is_dir())
        self.assertEqual(self._snapshot(), original)
        self.assertEqual(self._git(self.repo, "rev-parse", self.branch), self.source_head)

    def test_archive_failure_does_not_publish_or_change_wip(self) -> None:
        self._dirty_worktree()
        original = self._snapshot()
        publish = mock.Mock(return_value=True)
        with mock.patch.object(worker_workspace, "_archive_dirty_worktree", return_value=None):
            ok, status, _binding = self._quarantine(publish)
        self.assertFalse(ok, status)
        publish.assert_not_called()
        self.assertEqual(self._snapshot(), original)

    def test_missing_archive_patch_does_not_publish_or_change_wip(self) -> None:
        self._dirty_worktree()
        original = self._snapshot()
        publish = mock.Mock(return_value=True)
        real_archive = worker_workspace._archive_dirty_worktree

        def incomplete_archive(*args, **kwargs):
            archive = real_archive(*args, **kwargs)
            assert archive is not None
            (archive / "diff-staged.patch").unlink()
            return archive

        with mock.patch.object(worker_workspace, "_archive_dirty_worktree", side_effect=incomplete_archive):
            ok, status, _binding = self._quarantine(publish)
        self.assertFalse(ok, status)
        publish.assert_not_called()
        self.assertEqual(self._snapshot(), original)

    def test_oversized_untracked_file_does_not_publish_or_change_wip(self) -> None:
        self._dirty_worktree()
        (self.worktree / "oversized draft.txt").write_bytes(b"x" * 2048)
        original = self._snapshot()
        publish = mock.Mock(return_value=True)
        ok, status, _binding = self._quarantine(publish, max_file_bytes=1024)
        self.assertFalse(ok, status)
        publish.assert_not_called()
        self.assertEqual(self._snapshot(), original)

    def test_untracked_symlink_does_not_publish_or_change_wip(self) -> None:
        self._dirty_worktree()
        outside = self.root / "outside source.txt"
        outside.write_bytes(b"external contents\n")
        (self.worktree / "linked draft.txt").symlink_to(outside)
        original = self._snapshot()
        publish = mock.Mock(return_value=True)
        ok, status, _binding = self._quarantine(publish)
        self.assertFalse(ok, status)
        publish.assert_not_called()
        self.assertEqual(self._snapshot(), original)
        self.assertEqual(outside.read_bytes(), b"external contents\n")

    def test_unreadable_untracked_file_does_not_publish_or_change_wip(self) -> None:
        self._dirty_worktree()
        original = self._snapshot()
        publish = mock.Mock(return_value=True)
        real_read = worker_workspace.read_regular_file_snapshot

        def read_readable(path, *args, **kwargs):
            if Path(path) == self.worktree / "draft notes.txt":
                raise PermissionError("untracked source cannot be read")
            return real_read(path, *args, **kwargs)

        with mock.patch.object(worker_workspace, "read_regular_file_snapshot", side_effect=read_readable):
            ok, status, _binding = self._quarantine(publish)
        self.assertFalse(ok, status)
        publish.assert_not_called()
        self.assertEqual(self._snapshot(), original)

    def _assert_retry_reuses_archive(self, binding) -> None:
        archive_paths = set(self.archive_root.iterdir())
        recovery_refs = self._git(self.repo, "for-each-ref", "refs/pantheon/recovery/")
        publish = mock.Mock(return_value=True)
        with mock.patch.object(worker_workspace, "_archive_dirty_worktree") as archive:
            ok, status, replayed_binding = self._quarantine(publish, existing_archive=binding)
        self.assertTrue(ok, status)
        archive.assert_not_called()
        publish.assert_called_once_with(binding)
        self.assertEqual(replayed_binding, binding)
        self.assertEqual(set(self.archive_root.iterdir()), archive_paths)
        self.assertEqual(self._git(self.repo, "for-each-ref", "refs/pantheon/recovery/"), recovery_refs)
        self.assertEqual(self._git(self.worktree, "status", "--porcelain"), "")
        self.assertEqual(self._git(self.worktree, "rev-parse", "HEAD"), self.source_head)
        self.assertEqual((self.worktree / "local.cache").read_bytes(), b"ignored local state\n")

    def test_retry_after_publication_before_restore_reuses_the_same_archive(self) -> None:
        self._dirty_worktree()
        original = self._snapshot()
        publish = mock.Mock(return_value=True)
        real_run = subprocess.run

        def run_until_restore(args, **kwargs):
            if "restore" in args:
                return subprocess.CompletedProcess(args, 1, "", "interrupted restore")
            return real_run(args, **kwargs)

        with mock.patch.object(worker_workspace.subprocess, "run", side_effect=run_until_restore):
            ok, status, binding = self._quarantine(publish)
        self.assertFalse(ok, status)
        publish.assert_called_once_with(binding)
        self.assertEqual(self._snapshot(), original)
        self._assert_retry_reuses_archive(binding)

    def test_retry_after_restore_before_untracked_cleanup_reuses_the_same_archive(self) -> None:
        self._dirty_worktree()
        publish = mock.Mock(return_value=True)
        real_unlink = Path.unlink

        def unlink_until_interruption(path, *args, **kwargs):
            if path == self.worktree / "draft notes.txt":
                raise PermissionError("interrupted before untracked cleanup")
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", new=unlink_until_interruption):
            ok, status, binding = self._quarantine(publish)
        self.assertFalse(ok, status)
        publish.assert_called_once_with(binding)
        self.assertEqual(self._git(self.worktree, "diff", "--cached"), "")
        self.assertEqual(self._git(self.worktree, "diff"), "")
        self.assertEqual((self.worktree / "draft notes.txt").read_bytes(), b"untracked WIP\x00\n")
        self._assert_retry_reuses_archive(binding)
        # A second dispatch sees the same already-completed quarantine.
        self._assert_retry_reuses_archive(binding)

    def test_retry_rejects_wip_changed_since_the_archive(self) -> None:
        self._dirty_worktree()
        ok, status, binding = self._quarantine(mock.Mock(return_value=False))
        self.assertFalse(ok, status)
        (self.worktree / "draft notes.txt").write_bytes(b"new work since archive\n")
        changed = self._snapshot()
        publish = mock.Mock(return_value=True)
        with mock.patch.object(worker_workspace, "_archive_dirty_worktree") as archive:
            ok, status, _binding = self._quarantine(publish, existing_archive=binding)
        self.assertFalse(ok, status)
        archive.assert_not_called()
        publish.assert_not_called()
        self.assertEqual(self._snapshot(), changed)

    def test_incomplete_archive_returns_none_to_every_cleanup_caller(self) -> None:
        self._dirty_worktree()
        original = self._snapshot()
        archive = worker_workspace._archive_dirty_worktree(
            self.worktree, self.archive_root, reason="ordinary_cleanup", max_file_bytes=1,
        )
        self.assertIsNone(archive)
        manifests = list(self.archive_root.glob("*/manifest.json"))
        self.assertEqual(len(manifests), 1)
        self.assertFalse(json.loads(manifests[0].read_text(encoding="utf-8"))["complete"])
        self.assertEqual(self._snapshot(), original)

    def test_archive_preserves_untracked_executable_mode(self) -> None:
        self._dirty_worktree()
        source = self.worktree / "draft notes.txt"
        source.chmod(0o751)
        ok, status, binding = self._quarantine(mock.Mock(return_value=True))
        self.assertTrue(ok, status)
        archived = Path(binding["archive_path"]) / "files" / "draft notes.txt"
        self.assertEqual(stat.S_IMODE(archived.stat().st_mode), 0o751)

    def test_durable_archive_write_failure_leaves_wip_unpublished_and_unchanged(self) -> None:
        self._dirty_worktree()
        original = self._snapshot()
        real_write = worker_workspace.durable_write_bytes

        for failed_name in ("diff.patch", "draft notes.txt", "manifest.json"):
            with self.subTest(failed_name=failed_name):
                publish = mock.Mock(return_value=True)

                def fail_write(path, payload, **kwargs):
                    if path.name == failed_name:
                        raise OSError("durable archive write failed")
                    return real_write(path, payload, **kwargs)

                with mock.patch.object(worker_workspace, "durable_write_bytes", side_effect=fail_write):
                    ok, status, _binding = self._quarantine(publish)
                self.assertFalse(ok, status)
                publish.assert_not_called()
                self.assertEqual(self._snapshot(), original)

    def test_completed_retry_rejects_corrupt_archived_patch_or_file(self) -> None:
        self._dirty_worktree()
        ok, status, binding = self._quarantine(mock.Mock(return_value=True))
        self.assertTrue(ok, status)
        clean = self._snapshot()
        archive = Path(binding["archive_path"])
        for path in (archive / "diff.patch", archive / "files" / "source.txt"):
            with self.subTest(path=path.relative_to(archive)):
                original_bytes = path.read_bytes()
                path.write_bytes(b"corrupted archive payload\n")
                publish = mock.Mock(return_value=True)
                ok, status, _binding = self._quarantine(publish, existing_archive=binding)
                self.assertFalse(ok, status)
                publish.assert_not_called()
                self.assertEqual(self._snapshot(), clean)
                path.write_bytes(original_bytes)


if __name__ == "__main__":
    unittest.main()
