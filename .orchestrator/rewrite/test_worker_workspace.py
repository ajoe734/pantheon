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

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

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
            "recover_currentbase_worktree",
            "recover_worker_currentbase_delivery",
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

    def test_refresh_rejects_missing_base_instead_of_classifying_it_as_diverged(self) -> None:
        before = self._snapshot()
        missing_base = "f" * 40

        self.assertEqual(
            worker_workspace.worker_worktree_base_relation(self.worktree, missing_base),
            "base_relation_failed",
        )
        ok, status = worker_workspace._refresh_reused_worker_worktree(self.worktree, missing_base)
        self.assertFalse(ok, status)
        self.assertTrue(status.startswith("merge_failed:"), status)
        self.assertEqual(self._snapshot(), before)

    def test_refresh_rejects_either_ancestry_probe_failure(self) -> None:
        self._git(self.repo, "commit", "--allow-empty", "-m", "independent dev commit")
        base_sha = self._git(self.repo, "rev-parse", "HEAD")
        before = self._snapshot()
        real_run = subprocess.run
        for failed_pair in ((base_sha, "HEAD"), ("HEAD", base_sha)):
            for returncode in (128, -9):
                with self.subTest(failed_pair=failed_pair, returncode=returncode):
                    def failed_ancestry(args, **kwargs):
                        if args == ["git", "merge-base", "--is-ancestor", *failed_pair]:
                            return subprocess.CompletedProcess(
                                args, returncode, stdout="", stderr="injected ancestry read failure",
                            )
                        return real_run(args, **kwargs)

                    with mock.patch.object(worker_workspace.subprocess, "run", side_effect=failed_ancestry):
                        self.assertEqual(
                            worker_workspace.worker_worktree_base_relation(self.worktree, base_sha),
                            "base_relation_failed",
                        )
                        ok, status = worker_workspace._refresh_reused_worker_worktree(self.worktree, base_sha)
                    self.assertFalse(ok, status)
                    self.assertTrue(status.startswith("merge_failed:"), status)
                    self.assertEqual(self._snapshot(), before)

    def test_refresh_still_preserves_ahead_and_genuinely_diverged_task_commits(self) -> None:
        before = self._snapshot()
        self.assertEqual(
            worker_workspace.worker_worktree_base_relation(self.worktree, self.base_sha),
            "contains_base",
        )
        ok, status = worker_workspace._refresh_reused_worker_worktree(self.worktree, self.base_sha)
        self.assertTrue(ok, status)
        self._git(self.repo, "commit", "--allow-empty", "-m", "independent dev commit")
        base_sha = self._git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(
            worker_workspace.worker_worktree_base_relation(self.worktree, base_sha), "diverged",
        )
        self.assertEqual(
            worker_workspace._refresh_reused_worker_worktree(self.worktree, base_sha),
            (False, "skipped_non_fast_forward"),
        )
        self.assertEqual(self._snapshot(), before)

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

    def _completed_quarantine_binding(self):
        self._dirty_worktree()
        ok, status, binding = self._quarantine(mock.Mock(return_value=True))
        self.assertTrue(ok, status)
        self.assertEqual(self._git(self.worktree, "status", "--porcelain"), "")
        return binding

    def _assert_changed_head_retry_rejected(self, binding) -> None:
        before = self._snapshot()
        historical_binding = dict(binding)
        manifest_path = Path(binding["archive_path"]) / "manifest.json"
        historical_manifest = manifest_path.read_bytes()
        recovery_refs = self._git(self.repo, "for-each-ref", "refs/pantheon/recovery/")
        publish = mock.Mock(return_value=True)

        ok, status, returned_binding = self._quarantine(publish, existing_archive=binding)

        self.assertFalse(ok, status)
        self.assertEqual(status, "recovery_branch_changed_after_archive")
        publish.assert_not_called()
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(binding, historical_binding)
        self.assertEqual(returned_binding, historical_binding)
        self.assertEqual(manifest_path.read_bytes(), historical_manifest)
        self.assertEqual(
            self._git(self.repo, "for-each-ref", "refs/pantheon/recovery/"), recovery_refs,
        )

    def test_changed_head_retry_rejects_dirty_descendant_without_restoring_wip(self) -> None:
        binding = self._completed_quarantine_binding()
        self._git(self.worktree, "commit", "--allow-empty", "-m", "committed descendant")
        self._git(self.worktree, "merge-base", "--is-ancestor", binding["source_head"], "HEAD")
        self._dirty_worktree()

        self._assert_changed_head_retry_rejected(binding)

    def test_changed_head_retry_rejects_clean_non_descendant(self) -> None:
        binding = self._completed_quarantine_binding()
        # This is the isolated fixture's clean task tree, not a source checkout.
        # Its original dev base is not a descendant of the archived task commit.
        self._git(self.worktree, "reset", "--hard", self.base_sha)
        self.assertEqual(self._git(self.worktree, "status", "--porcelain"), "")

        self._assert_changed_head_retry_rejected(binding)

    def test_changed_head_retry_rejects_ancestry_git_errors_before_publication(self) -> None:
        binding = self._completed_quarantine_binding()
        self._git(self.worktree, "commit", "--allow-empty", "-m", "committed descendant")
        current_head = self._git(self.worktree, "rev-parse", "HEAD")
        ancestry_command = [
            "git", "-C", str(self.worktree), "merge-base", "--is-ancestor",
            binding["source_head"], current_head,
        ]
        real_run = subprocess.run
        for returncode in (128, -9):
            with self.subTest(returncode=returncode):
                def fail_ancestry(args, **kwargs):
                    if args == ancestry_command:
                        return subprocess.CompletedProcess(
                            args, returncode, stdout="", stderr="injected ancestry read failure",
                        )
                    return real_run(args, **kwargs)

                with mock.patch.object(worker_workspace.subprocess, "run", side_effect=fail_ancestry):
                    self._assert_changed_head_retry_rejected(binding)


class RecoveryWorktreeCleanRetryTests(unittest.TestCase):
    """Exercise archive revalidation through the real canonical handoff."""

    def setUp(self) -> None:
        # Reuse the established real-Git / TaskStore integration fixture. Its
        # external fetch and projection seams never reach live state or GitHub.
        import test_supervisor as integration

        self.integration = integration
        self.workspace_module = integration.worker_workspace
        self.fixture = integration.LostLeaseWorkspaceRecoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        directory = tempfile.TemporaryDirectory(prefix="recovery clean retry ")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.repo, _task, self.state, self.workspace = self.fixture._started_workspace(self.root)
        (self.workspace / ".orchestrator/supervisor.py").write_bytes(b"# uncommitted source\n")
        (self.workspace / "draft.txt").write_bytes(b"untracked draft\n")
        self.task, self.receipt_id, self.event_id = self.fixture._fence_and_reassign(self.state)
        request = self._rebuilt_request()
        with mock.patch.object(
            self.workspace_module, "_refresh_reused_worker_worktree",
            return_value=(False, "worktree_status_failed"),
        ):
            ok, error = self.fixture._prepare_replacement(self.state, request, self.event_id)
        self.assertFalse(ok, error)
        self.assertIn("worktree_status_failed", str(error))
        self.assertEqual(self.fixture._git(self.workspace, "status", "--porcelain"), "")
        self.receipt = self._canonical_receipt()
        self.binding = self.receipt["workspace"]
        self.archive = Path(self.binding["archive_path"])
        self.archive_paths = set((self.root / "archive").iterdir())
        self.recovery_refs = self.fixture._git(self.repo, "for-each-ref", "refs/pantheon/recovery/")

    def _canonical_receipt(self):
        status = self.integration.supervisor.load_status(self.fixture.config)
        return status[self.integration.supervisor.WORKER_RECOVERY_RECEIPTS_KEY][self.receipt_id]

    def _rebuilt_request(self):
        status = self.integration.supervisor.load_status(self.fixture.config)
        return self.fixture._replacement_request(status["tasks"][0], self.receipt_id, self.event_id)

    def _assert_same_history(self) -> None:
        self.assertEqual(self._canonical_receipt(), self.receipt)
        self.assertEqual(set((self.root / "archive").iterdir()), self.archive_paths)
        self.assertEqual(
            self.fixture._git(self.repo, "for-each-ref", "refs/pantheon/recovery/"),
            self.recovery_refs,
        )

    def test_clean_retry_revalidates_existing_archive_without_republication(self) -> None:
        request = self._rebuilt_request()
        with mock.patch.object(
            self.workspace_module, "_quarantine_recovery_worktree",
            wraps=self.workspace_module._quarantine_recovery_worktree,
        ) as quarantine:
            ok, error = self.fixture._prepare_replacement(self.state, request, self.event_id)
        self.assertTrue(ok, error)
        quarantine.assert_called_once()
        self.assertEqual(request.metadata["recovery_workspace"], self.binding)
        self._assert_same_history()

    def test_clean_retry_blocks_missing_or_corrupt_archive_before_materialization(self) -> None:
        for relative_path in ("diff.patch", "files/.orchestrator/supervisor.py"):
            for missing in (True, False):
                with self.subTest(relative_path=relative_path, missing=missing):
                    artifact = self.archive / relative_path
                    saved_bytes = artifact.read_bytes()
                    if missing:
                        artifact.unlink()
                    else:
                        artifact.write_bytes(b"corrupt recovery evidence\n")
                    try:
                        request = self._rebuilt_request()
                        with mock.patch.object(
                            self.workspace_module, "materialize_worker_context_files",
                        ) as materialize:
                            ok, error = self.fixture._prepare_replacement(self.state, request, self.event_id)
                        self.assertFalse(ok, error)
                        materialize.assert_not_called()
                        self.assertNotIn("workspace_path", request.metadata)
                        self.assertNotIn("recovery_workspace", request.metadata)
                        self.assertEqual(self.fixture._git(self.workspace, "status", "--porcelain"), "")
                        self._assert_same_history()
                    finally:
                        artifact.write_bytes(saved_bytes)

    def test_clean_retry_after_fast_forward_preserves_historical_archive_binding(self) -> None:
        source_path = ".orchestrator/supervisor.py"
        new_source = b"# committed dev advancement\n"
        (self.repo / source_path).write_bytes(new_source)
        self.fixture._git(self.repo, "add", source_path)
        self.fixture._git(self.repo, "commit", "-m", "advance dev")
        new_base = self.fixture._git(self.repo, "rev-parse", "HEAD")
        self.fixture._git(self.repo, "update-ref", "refs/remotes/origin/dev", new_base)
        first = self._rebuilt_request()
        ok, error = self.fixture._prepare_replacement(self.state, first, self.event_id)
        self.assertTrue(ok, error)
        self.assertEqual(self.fixture._git(self.workspace, "rev-parse", "HEAD"), new_base)
        self.assertNotEqual(new_base, self.binding["source_head"])

        retry = self._rebuilt_request()
        with mock.patch.object(
            self.workspace_module, "_quarantine_recovery_worktree",
            wraps=self.workspace_module._quarantine_recovery_worktree,
        ) as quarantine:
            ok, error = self.fixture._prepare_replacement(self.state, retry, self.event_id)
        self.assertTrue(ok, error)
        quarantine.assert_called_once()
        self.assertEqual(retry.metadata["recovery_workspace"], self.binding)
        self.assertEqual(self.fixture._git(self.workspace, "rev-parse", "HEAD"), new_base)
        self.assertEqual((self.workspace / source_path).read_bytes(), new_source)
        self._assert_same_history()

        # The legitimate source advancement must not turn off the archive
        # integrity barrier on another retry of the same receipt.
        (self.archive / "diff.patch").unlink()
        rejected = self._rebuilt_request()
        with mock.patch.object(self.workspace_module, "materialize_worker_context_files") as materialize:
            ok, error = self.fixture._prepare_replacement(self.state, rejected, self.event_id)
        self.assertFalse(ok, error)
        materialize.assert_not_called()
        self.assertEqual(self.fixture._git(self.workspace, "rev-parse", "HEAD"), new_base)
        self.assertEqual((self.workspace / source_path).read_bytes(), new_source)
        self._assert_same_history()


class RecoveryCurrentbaseDeliveryTests(unittest.TestCase):
    """Current-base scoped recovery to versioned branch preserving old branch byte-identical."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="recovery currentbase ")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self._git(self.repo, "init", "-b", "dev")
        self._git(self.repo, "config", "user.name", "Test")
        self._git(self.repo, "config", "user.email", "test@example.com")
        self._git(self.repo, "remote", "add", "origin", "https://github.com/ajoe734/pantheon.git")

        (self.repo / "scoped_service.py").write_text("def run():\n    return 'base'\n", encoding="utf-8")
        (self.repo / "unrelated.py").write_text("# initial dev code\n", encoding="utf-8")
        self._git(self.repo, "add", ".")
        self._git(self.repo, "commit", "-m", "initial base commit")
        self.old_base = self._git(self.repo, "rev-parse", "HEAD")

        self.task_id = "TEST-REDIS-CONTAINER-LEAK-20261010"
        self.source_branch = f"task/{self.task_id}"
        self.worktree = self.root / "task worktree"
        self._git(self.repo, "worktree", "add", "-b", self.source_branch, str(self.worktree), self.old_base)

        (self.worktree / "scoped_service.py").write_text("def run():\n    return 'redis leak fixed'\n", encoding="utf-8")
        evidence_dir = self.worktree / "docs" / "evidence"
        evidence_dir.mkdir(parents=True)
        (evidence_dir / "evidence.json").write_text('{"status": "verified"}\n', encoding="utf-8")
        self._git(self.worktree, "add", ".")
        self._git(self.worktree, "commit", "-m", "bad trailer task commit that cannot be pushed")
        self.source_head = self._git(self.worktree, "rev-parse", "HEAD")

        (self.repo / "unrelated.py").write_text("# newer dev changes\nprint('new dev')\n", encoding="utf-8")
        (self.repo / "other_feature.py").write_text("x = 42\n", encoding="utf-8")
        self._git(self.repo, "add", ".")
        self._git(self.repo, "commit", "-m", "dev advanced")
        self.current_dev_sha = self._git(self.repo, "rev-parse", "dev")

        self.declared_paths = ["scoped_service.py", "docs/evidence/evidence.json"]
        self.owner = "Antigravity2"
        self.task_generation = 1
        self.lease = {
            "task_id": self.task_id,
            "owner": self.owner,
            "task_generation": self.task_generation,
            "repository_id": "pantheon",
        }
        self.published_provenances: list[dict[str, Any]] = []
        self.publish_provenance = lambda prov: self.published_provenances.append(prov) or True

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

    def test_currentbase_recovery_preserves_old_branch_and_applies_scoped_patch(self) -> None:
        target_branch = f"task/{self.task_id}-v2"
        ok, status, prov = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id,
            base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths,
            source_branch=self.source_branch,
            target_branch=target_branch,
            owner=self.owner,
            task_generation=self.task_generation,
            lease=self.lease,
            publish_provenance=self.publish_provenance,
        )
        self.assertTrue(ok, status)
        self.assertEqual(status, "currentbase_recovered")
        self.assertIsNotNone(prov)
        self.assertEqual(prov["target_branch"], target_branch)
        self.assertEqual(prov["base_sha"], self.current_dev_sha)
        self.assertEqual(prov["source_head"], self.source_head)
        self.assertTrue(self.published_provenances)
        self.assertEqual(self.published_provenances[0]["task_id"], self.task_id)
        self.assertTrue(prov["patch_sha256"])

        old_head_after = self._git(self.repo, "rev-parse", f"refs/heads/{self.source_branch}")
        self.assertEqual(old_head_after, self.source_head)

        curr_b = self._git(self.worktree, "symbolic-ref", "--short", "HEAD")
        self.assertEqual(curr_b, target_branch)

        ancestry = subprocess.run(
            ["git", "-C", str(self.worktree), "merge-base", "--is-ancestor", self.current_dev_sha, "HEAD"],
            capture_output=True,
        )
        self.assertEqual(ancestry.returncode, 0)

        self.assertEqual((self.worktree / "other_feature.py").read_text(encoding="utf-8"), "x = 42\n")
        self.assertIn("newer dev changes", (self.worktree / "unrelated.py").read_text(encoding="utf-8"))

        self.assertEqual((self.worktree / "scoped_service.py").read_text(encoding="utf-8"), "def run():\n    return 'redis leak fixed'\n")
        self.assertEqual((self.worktree / "docs" / "evidence" / "evidence.json").read_text(encoding="utf-8"), '{"status": "verified"}\n')

    def test_recovery_rejects_missing_lease_fail_closed(self) -> None:
        ok, status, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=None,
        )
        self.assertFalse(ok)
        self.assertEqual(status, "lease_required")
        curr_b = self._git(self.worktree, "symbolic-ref", "--short", "HEAD")
        self.assertEqual(curr_b, self.source_branch)

    def test_recovery_rejects_stale_base_fail_closed(self) -> None:
        stale_base = self.old_base
        ok, status, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=stale_base,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=self.lease,
        )
        self.assertFalse(ok)
        self.assertEqual(status, "stale_base")
        curr_b = self._git(self.worktree, "symbolic-ref", "--short", "HEAD")
        self.assertEqual(curr_b, self.source_branch)
        self.assertTrue((self.repo / "other_feature.py").exists())

        bad_base_lease = dict(self.lease, base_ref=self.old_base)
        ok_bad_base, status_bad_base, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.old_base,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=bad_base_lease,
            publish_provenance=self.publish_provenance,
        )
        self.assertFalse(ok_bad_base)
        self.assertEqual(status_bad_base, "stale_base")

    def test_recovery_rejects_missing_or_stale_generation(self) -> None:
        ok, status, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            owner=self.owner, task_generation=0,
            lease=self.lease,
        )
        self.assertFalse(ok)
        self.assertEqual(status, "invalid_task_metadata")

        bad_lease = dict(self.lease, task_generation=999)
        ok, status, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=bad_lease,
        )
        self.assertFalse(ok)
        self.assertEqual(status, "lease_mismatch")

        missing_gen_lease = dict(self.lease)
        missing_gen_lease.pop("task_generation", None)
        ok_miss, status_miss, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=missing_gen_lease,
            publish_provenance=self.publish_provenance,
        )
        self.assertFalse(ok_miss)
        self.assertEqual(status_miss, "lease_mismatch")

    def test_recovery_rejects_owner_mismatch(self) -> None:
        ok, status, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            owner="", task_generation=self.task_generation,
            lease=self.lease,
        )
        self.assertFalse(ok)
        self.assertEqual(status, "invalid_task_metadata")

        bad_lease = dict(self.lease, owner="OtherWorker")
        ok, status, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=bad_lease,
        )
        self.assertFalse(ok)
        self.assertEqual(status, "lease_mismatch")

    def test_recovery_rejects_lease_task_or_repo_mismatch(self) -> None:
        bad_task_lease = dict(self.lease, task_id="OTHER-TASK")
        ok, status, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=bad_task_lease,
        )
        self.assertFalse(ok)
        self.assertEqual(status, "lease_mismatch")

        bad_repo_lease = dict(self.lease, repository_id="other-repo")
        ok, status, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=bad_repo_lease,
        )
        self.assertFalse(ok)
        self.assertEqual(status, "lease_mismatch")

    def test_recovery_rejects_unresolved_base_or_source_branch(self) -> None:
        ok, status, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha="0000000000000000000000000000000000000000",
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=self.lease,
        )
        self.assertFalse(ok)
        self.assertEqual(status, "base_or_source_unresolved")

        ok, status, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch="nonexistent-branch",
            owner=self.owner, task_generation=self.task_generation,
            lease=self.lease,
        )
        self.assertFalse(ok)
        self.assertEqual(status, "base_or_source_unresolved")

    def test_recovery_rejects_target_branch_already_exists_collision(self) -> None:
        target_branch = f"task/{self.task_id}-v2"
        self._git(self.repo, "branch", target_branch, self.old_base)
        ok, status, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            target_branch=target_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=self.lease,
        )
        self.assertFalse(ok)
        self.assertEqual(status, "target_branch_already_exists")

    def test_recovery_rejects_patch_conflict_fail_closed(self) -> None:
        (self.repo / "scoped_service.py").write_text("def completely_different():\n    pass\n", encoding="utf-8")
        self._git(self.repo, "add", "scoped_service.py")
        self._git(self.repo, "commit", "-m", "conflicting dev commit")
        conflicting_dev = self._git(self.repo, "rev-parse", "dev")

        before_branch = self._git(self.worktree, "symbolic-ref", "--short", "HEAD")
        before_head = self._git(self.worktree, "rev-parse", "HEAD")
        before_refs = self._git(self.repo, "show-ref")

        target_branch = f"task/{self.task_id}-v2"
        ok, status, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=conflicting_dev,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            target_branch=target_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=self.lease,
            publish_provenance=self.publish_provenance,
        )
        self.assertFalse(ok)
        self.assertEqual(status, "patch_conflict")
        self.assertEqual(self._git(self.worktree, "symbolic-ref", "--short", "HEAD"), before_branch)
        self.assertEqual(self._git(self.worktree, "rev-parse", "HEAD"), before_head)
        self.assertEqual(self._git(self.repo, "show-ref"), before_refs)
        self.assertNotIn(f"refs/heads/{target_branch}", self._git(self.repo, "for-each-ref", "refs/heads/"))

    def test_recovery_rejects_dirty_worktree(self) -> None:
        (self.worktree / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")
        ok, status, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=self.lease,
            publish_provenance=self.publish_provenance,
        )
        self.assertFalse(ok)
        self.assertEqual(status, "worktree_dirty_cannot_recover")

    def test_recovery_rejects_provenance_publication_failure(self) -> None:
        target_branch = f"task/{self.task_id}-v2"
        ok, status, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            target_branch=target_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=self.lease,
            publish_provenance=lambda _prov: False,
        )
        self.assertFalse(ok)
        self.assertEqual(status, "provenance_publication_failed")
        self.assertNotIn(f"refs/heads/{target_branch}", self._git(self.repo, "for-each-ref", "refs/heads/"))
        curr_b = self._git(self.worktree, "symbolic-ref", "--short", "HEAD")
        self.assertEqual(curr_b, self.source_branch)

        ok_none, status_none, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            target_branch=target_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=self.lease,
            publish_provenance=None,
        )
        self.assertFalse(ok_none)
        self.assertEqual(status_none, "publication_callback_required")

        ok_scope, status_scope, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=[], source_branch=self.source_branch,
            target_branch=target_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=self.lease,
            publish_provenance=self.publish_provenance,
        )
        self.assertFalse(ok_scope)
        self.assertEqual(status_scope, "empty_declared_scope")

    def test_recovery_retry_idempotency_and_provenance_mismatch(self) -> None:
        target_branch = f"task/{self.task_id}-v2"
        ok1, status1, prov1 = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            target_branch=target_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=self.lease,
            publish_provenance=self.publish_provenance,
        )
        self.assertTrue(ok1, status1)
        self.assertEqual(status1, "currentbase_recovered")

        bad_prov = dict(prov1, owner="OtherWorker")
        ok_bad, status_bad, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            target_branch=target_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=self.lease,
            existing_provenance=bad_prov,
            publish_provenance=self.publish_provenance,
        )
        self.assertFalse(ok_bad)
        self.assertEqual(status_bad, "recovery_provenance_mismatch")

        ok_no_prov, status_no_prov, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            target_branch=target_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=self.lease,
            existing_provenance=None,
            publish_provenance=self.publish_provenance,
        )
        self.assertFalse(ok_no_prov)
        self.assertEqual(status_no_prov, "recovery_provenance_mismatch")

        new_gen_lease = dict(self.lease, task_generation=self.task_generation + 1)
        ok_stale_gen, status_stale_gen, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            target_branch=target_branch,
            owner=self.owner, task_generation=self.task_generation + 1,
            lease=new_gen_lease,
            existing_provenance=prov1,
            publish_provenance=self.publish_provenance,
        )
        self.assertFalse(ok_stale_gen)
        self.assertEqual(status_stale_gen, "recovery_provenance_mismatch")

        ok2, status2, prov2 = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            target_branch=target_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=self.lease,
            existing_provenance=prov1,
            publish_provenance=self.publish_provenance,
        )
        self.assertTrue(ok2, status2)
        self.assertEqual(status2, "already_recovered")
        self.assertEqual(prov1["base_sha"], prov2["base_sha"])

    def test_recovery_rejects_foreign_source_worktree_fail_closed(self) -> None:
        foreign_branch = "task/OTHER-OWNER-SOURCE"
        self._git(self.worktree, "checkout", "-b", foreign_branch)
        (self.worktree / "foreign.txt").write_text("foreign\n", encoding="utf-8")
        self._git(self.worktree, "add", "foreign.txt")
        self._git(self.worktree, "commit", "-m", "foreign branch commit")
        foreign_head = self._git(self.worktree, "rev-parse", "HEAD")
        before_refs = self._git(self.repo, "show-ref")

        target_branch = f"task/{self.task_id}-v2"
        ok, status, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            target_branch=target_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=self.lease,
            publish_provenance=self.publish_provenance,
        )
        self.assertFalse(ok)
        self.assertEqual(status, "source_branch_mismatch")
        self.assertEqual(self._git(self.worktree, "symbolic-ref", "--short", "HEAD"), foreign_branch)
        self.assertEqual(self._git(self.worktree, "rev-parse", "HEAD"), foreign_head)
        self.assertEqual(self._git(self.repo, "show-ref"), before_refs)
        self.assertNotIn(f"refs/heads/{target_branch}", self._git(self.repo, "for-each-ref", "refs/heads/"))

    def test_recovery_rejects_physical_shared_root_fail_closed(self) -> None:
        before_branch = self._git(self.repo, "symbolic-ref", "--short", "HEAD")
        before_head = self._git(self.repo, "rev-parse", "HEAD")
        before_refs = self._git(self.repo, "show-ref")

        ok, status, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.repo,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch="dev",
            owner=self.owner, task_generation=self.task_generation,
            lease=self.lease,
            publish_provenance=self.publish_provenance,
        )
        self.assertFalse(ok)
        self.assertEqual(status, "physical_shared_root_cannot_recover")
        self.assertEqual(self._git(self.repo, "symbolic-ref", "--short", "HEAD"), before_branch)
        self.assertEqual(self._git(self.repo, "rev-parse", "HEAD"), before_head)
        self.assertEqual(self._git(self.repo, "show-ref"), before_refs)

    def test_recovery_rejects_current_dev_race_during_publication_fail_closed(self) -> None:
        before_branch = self._git(self.worktree, "symbolic-ref", "--short", "HEAD")
        before_head = self._git(self.worktree, "rev-parse", "HEAD")

        def pub_racing(p: dict[str, Any]) -> bool:
            (self.repo / "racing_feature.py").write_text("concurrent dev change\n", encoding="utf-8")
            self._git(self.repo, "add", ".")
            self._git(self.repo, "commit", "-m", "racing dev commit")
            return True

        target_branch = f"task/{self.task_id}-v2"
        ok, status, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            target_branch=target_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=self.lease,
            publish_provenance=pub_racing,
        )
        self.assertFalse(ok)
        self.assertEqual(status, "stale_base")
        self.assertEqual(self._git(self.worktree, "symbolic-ref", "--short", "HEAD"), before_branch)
        self.assertEqual(self._git(self.worktree, "rev-parse", "HEAD"), before_head)
        self.assertNotIn(f"refs/heads/{target_branch}", self._git(self.repo, "for-each-ref", "refs/heads/"))
        self.assertFalse((self.worktree / "racing_feature.py").exists())

    def test_recovery_rejects_source_head_race_during_publication_fail_closed(self) -> None:
        before_branch = self._git(self.worktree, "symbolic-ref", "--short", "HEAD")
        before_head = self._git(self.worktree, "rev-parse", "HEAD")

        def pub_racing_source(p: dict[str, Any]) -> bool:
            (self.worktree / "scoped_service.py").write_text("concurrent source change\n", encoding="utf-8")
            self._git(self.worktree, "add", "scoped_service.py")
            self._git(self.worktree, "commit", "-m", "racing source commit")
            return True

        target_branch = f"task/{self.task_id}-v2"
        ok, status, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            target_branch=target_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=self.lease,
            publish_provenance=pub_racing_source,
        )
        self.assertFalse(ok)
        self.assertEqual(status, "stale_source")
        self.assertEqual(self._git(self.worktree, "symbolic-ref", "--short", "HEAD"), before_branch)
        self.assertNotIn(f"refs/heads/{target_branch}", self._git(self.repo, "for-each-ref", "refs/heads/"))

    def test_recovery_rejects_lease_generation_change_during_publication_fail_closed(self) -> None:
        before_branch = self._git(self.worktree, "symbolic-ref", "--short", "HEAD")
        before_head = self._git(self.worktree, "rev-parse", "HEAD")

        def pub_racing_lease(p: dict[str, Any]) -> bool:
            self.lease["task_generation"] = self.task_generation + 1
            return True

        target_branch = f"task/{self.task_id}-v2"
        ok, status, _ = worker_workspace.recover_currentbase_worktree(
            self.repo, self.worktree,
            task_id=self.task_id, base_sha=self.current_dev_sha,
            declared_paths=self.declared_paths, source_branch=self.source_branch,
            target_branch=target_branch,
            owner=self.owner, task_generation=self.task_generation,
            lease=self.lease,
            publish_provenance=pub_racing_lease,
        )
        self.assertFalse(ok)
        self.assertEqual(status, "lease_mismatch")
        self.assertEqual(self._git(self.worktree, "symbolic-ref", "--short", "HEAD"), before_branch)
        self.assertEqual(self._git(self.worktree, "rev-parse", "HEAD"), before_head)
        self.assertNotIn(f"refs/heads/{target_branch}", self._git(self.repo, "for-each-ref", "refs/heads/"))

    def test_preflight_index_is_private_and_preserves_preexisting_global_file(self) -> None:
        nonce = worker_workspace.time.time_ns()
        legacy = Path(tempfile.gettempdir()) / f".recov_idx_{worker_workspace.os.getpid()}_{nonce}"
        with legacy.open("xb"):
            pass
        self.addCleanup(lambda: legacy.unlink(missing_ok=True))
        subprocess.run(["git", "-C", str(self.repo), "read-tree", self.old_base],
                       env=dict(worker_workspace.os.environ, GIT_INDEX_FILE=str(legacy)),
                       check=True, capture_output=True)
        original_bytes = legacy.read_bytes()
        original_run = subprocess.run
        indexes = []

        def inspect_index(argv, **kwargs):
            if "read-tree" in argv:
                index = Path(kwargs["env"]["GIT_INDEX_FILE"])
                indexes.append(index)
                self.assertNotEqual(index, legacy)
                self.assertEqual(stat.S_IMODE(index.parent.stat().st_mode), 0o700)
                self.assertFalse(index.parent.is_symlink())
            return original_run(argv, **kwargs)

        with mock.patch.object(worker_workspace.time, "time_ns", return_value=nonce), \
                mock.patch.object(worker_workspace.subprocess, "run", side_effect=inspect_index):
            ok, reason, _ = worker_workspace.recover_currentbase_worktree(
                self.repo, self.worktree, task_id=self.task_id, base_sha=self.current_dev_sha,
                declared_paths=self.declared_paths, source_branch=self.source_branch,
                owner=self.owner, task_generation=self.task_generation, lease=self.lease,
                publish_provenance=lambda prov: False,
            )
        self.assertFalse(ok)
        self.assertEqual(reason, "provenance_publication_failed")
        self.assertEqual(len(indexes), 1)
        self.assertFalse(indexes[0].parent.exists())
        self.assertEqual(legacy.read_bytes(), original_bytes)
        self.assertEqual(self._git(self.worktree, "rev-parse", "HEAD"), self.source_head)

    def test_preflight_failure_removes_private_index_directory_without_git_mutation(self) -> None:
        original_run = subprocess.run
        indexes = []

        def fail_read_tree(argv, **kwargs):
            if "read-tree" in argv:
                index = Path(kwargs["env"]["GIT_INDEX_FILE"])
                indexes.append(index)
                self.assertEqual(stat.S_IMODE(index.parent.stat().st_mode), 0o700)
                index.write_bytes(b"owned partial index")
                return subprocess.CompletedProcess(argv, 1, stdout=b"", stderr=b"owned failure")
            return original_run(argv, **kwargs)

        with mock.patch.object(worker_workspace.subprocess, "run", side_effect=fail_read_tree):
            ok, reason, _ = worker_workspace.recover_currentbase_worktree(
                self.repo, self.worktree, task_id=self.task_id, base_sha=self.current_dev_sha,
                declared_paths=self.declared_paths, source_branch=self.source_branch,
                owner=self.owner, task_generation=self.task_generation, lease=self.lease,
                publish_provenance=self.publish_provenance,
            )
        self.assertFalse(ok)
        self.assertEqual(reason, "read_tree_failed")
        self.assertEqual(len(indexes), 1)
        self.assertFalse(indexes[0].parent.exists())
        self.assertFalse(self.published_provenances)
        self.assertEqual(self._git(self.worktree, "rev-parse", "HEAD"), self.source_head)
        self.assertEqual(self._git(self.repo, "rev-parse", "dev"), self.current_dev_sha)

    def test_prepare_worker_workspace_adopts_versioned_branch(self) -> None:
        config = {
            "paths": {
                "status_file": str(self.root / "ai-status.json"),
                "activity_log": str(self.root / "ai-activity-log.jsonl"),
            },
            "worker_worktrees": {"path": str(self.root / "worktrees")},
            "coordination": {"repositories": {"pantheon": {"local_path": str(self.repo), "default_branch": "dev"}}},
        }
        (self.root / "ai-status.json").write_text("{}", encoding="utf-8")
        target_branch = f"task/{self.task_id}-v2"
        self._git(self.worktree, "checkout", "-b", target_branch)
        state = {"worker_worktrees": {"leases": {}}}
        request = worker_workspace.DeliveryRequest(
            agent_id="Antigravity2",
            provider="test",
            delivery_mode="auto",
            message="launch",
            task_id=self.task_id,
            metadata={"workspace_path": str(self.worktree)},
        )
        ok, error = worker_workspace.prepare_worker_workspace(
            config, state, request, queue_event_id="evt-1", target_agent="Antigravity2",
        )
        self.assertTrue(ok, error)
        self.assertEqual(request.metadata["workspace_branch"], target_branch)
        self.assertEqual(state["worker_worktrees"]["leases"][self.task_id]["branch"], target_branch)

    def test_prepare_worker_workspace_recreates_branch_when_registered_directory_vanished(self) -> None:
        config = {
            "paths": {
                "status_file": str(self.root / "ai-status.json"),
                "activity_log": str(self.root / "ai-activity-log.jsonl"),
            },
            "worker_worktrees": {"path": str(self.root / "worktrees")},
            "coordination": {"repositories": {"pantheon": {"local_path": str(self.repo), "default_branch": "dev"}}},
        }
        config["worker_worktrees"] = {"root": str(self.root / "worktrees")}
        (self.root / "ai-status.json").write_text("{}", encoding="utf-8")
        # Simulate /tmp being cleared by a reboot: Git still registers the
        # worktree, but its directory is gone.
        shutil.rmtree(self.worktree)
        porcelain = self._git(self.repo, "worktree", "list", "--porcelain")
        self.assertIn("prunable", porcelain)
        state = {"worker_worktrees": {"leases": {}}}
        request = worker_workspace.DeliveryRequest(
            agent_id="Antigravity",
            provider="test",
            delivery_mode="auto",
            message="launch",
            task_id=self.task_id,
            metadata={},
        )
        ok, error = worker_workspace.prepare_worker_workspace(
            config, state, request, queue_event_id="evt-1", target_agent="Antigravity",
        )
        self.assertTrue(ok, error)
        new_path = Path(request.metadata["workspace_path"])
        self.assertTrue(new_path.is_dir())
        self.assertNotEqual(new_path.resolve(), self.worktree.resolve())
        self.assertTrue(new_path.resolve().is_relative_to(self.root.resolve()))
        self.assertEqual(request.metadata["workspace_branch"], self.source_branch)
        # The committed task branch is preserved and checked out again.
        self.assertEqual(self._git(self.repo, "rev-parse", self.source_branch), self.source_head)
        self.assertEqual(self._git(new_path, "rev-parse", "HEAD"), self.source_head)
        self.assertNotIn(
            "prunable", self._git(self.repo, "worktree", "list", "--porcelain")
        )


    # -- same-owner reopen WIP resumption -------------------------------------

    def _resume_fixture(self, *, owner: str = "Claude2", reviewer: str = "Codex"):
        config = {
            "paths": {
                "status_file": str(self.root / "ai-status.json"),
                "activity_log": str(self.root / "ai-activity-log.jsonl"),
            },
            "worker_worktrees": {"path": str(self.root / "worktrees")},
            "coordination": {"repositories": {"pantheon": {"local_path": str(self.repo), "default_branch": "dev"}}},
        }
        (self.root / "ai-status.json").write_text("{}", encoding="utf-8")
        # No network fetch in this unit test: pin the remote-tracking base locally.
        self._git(self.repo, "update-ref", "refs/remotes/origin/dev", self.current_dev_sha)
        intent = {
            "schema_version": 1,
            "intent_id": "review-requeue-" + "a" * 64,
            "status": "pending",
            "task_id": self.task_id,
            "task_generation": 1,
            "owner": owner,
            "reviewer": reviewer,
            "reopened_at": "2026-11-12T14:37:57Z",
            "reopened_by": "Human/Ops",
            "reason": "resume",
        }
        task = {
            "id": self.task_id,
            "status": "in_progress",
            "owner": owner,
            "reviewer": reviewer,
            "generation": 1,
            "artifacts": ["scoped_service.py", "docs/evidence/"],
            "review_requeue_intent": intent,
        }
        return config, task

    def _prepare(self, config, task, agent, *, state=None):
        request = worker_workspace.DeliveryRequest(
            agent_id=agent, provider="test", delivery_mode="auto", message="launch",
            task_id=self.task_id, reason=worker_workspace.REASON_OWNED_IN_PROGRESS,
            metadata={
                "workspace_path": str(self.worktree),
                "task_generation": 1,
                "task": {"review_requeue_intent": task.get("review_requeue_intent")},
            },
        )
        state = state if state is not None else {"worker_worktrees": {"leases": {}}}
        sup = worker_workspace._supervisor_module()
        with (
            mock.patch.object(worker_workspace, "load_status", return_value={}),
            mock.patch.object(worker_workspace, "_fetch_worker_base_ref", return_value=(True, None)),
            mock.patch.object(sup, "task_index_from_status", return_value={self.task_id: task}),
            mock.patch.object(sup, "canonical_agent_name", side_effect=lambda c, v: str(v or "")),
            mock.patch.object(sup, "task_current_dispatch_responsibility", return_value="owner"),
            mock.patch.object(worker_workspace, "validate_task_repository_scope", return_value="pantheon"),
        ):
            return worker_workspace.prepare_worker_workspace(
                config, state, request, queue_event_id="evt-1", target_agent=agent,
            ), state

    def _snapshot(self):
        return {
            "head": self._git(self.worktree, "rev-parse", "HEAD"),
            "status": self._git(self.worktree, "status", "--porcelain", "--untracked-files=all"),
            "files": {
                name: (self.worktree / name).read_bytes()
                for name in ("scoped_service.py", "docs/evidence/new.json")
                if (self.worktree / name).exists()
            },
        }

    def _dirty_owned_wip(self):
        (self.worktree / "scoped_service.py").write_text("def run():\n    return 'wip'\n", encoding="utf-8")
        (self.worktree / "docs" / "evidence" / "new.json").write_text('{"wip": 1}\n', encoding="utf-8")

    def test_same_owner_resumes_own_dirty_artifact_wip_byte_for_byte(self) -> None:
        config, task = self._resume_fixture()
        self._dirty_owned_wip()
        before = self._snapshot()
        (ok, error), state = self._prepare(config, task, "Claude2")
        self.assertTrue(ok, error)
        after = self._snapshot()
        self.assertEqual(after["files"], before["files"])
        self.assertEqual(after["status"], before["status"])
        self.assertEqual(self._git(self.worktree, "stash", "list"), "")
        self.assertEqual(self._git(self.repo, "rev-parse", self.source_branch), self.source_head)
        self.assertEqual(state["worker_worktrees"]["leases"][self.task_id]["last_target_agent"], "Claude2")

    def test_reviewer_and_other_agents_never_inherit_dirty_wip(self) -> None:
        config, task = self._resume_fixture()
        self._dirty_owned_wip()
        before = self._snapshot()
        for agent in ("Codex", "Claude"):
            (ok, error), _ = self._prepare(config, task, agent)
            self.assertFalse(ok)
            self.assertIn("refresh failed (skipped_dirty_worktree)", error)
        self.assertEqual(self._snapshot(), before)

    def test_unrelated_file_or_missing_reopen_intent_or_wrong_state_refused(self) -> None:
        config, task = self._resume_fixture()
        self._dirty_owned_wip()
        (self.worktree / "unrelated.py").write_text("# stray\n", encoding="utf-8")
        (ok, error), _ = self._prepare(config, task, "Claude2")
        self.assertFalse(ok)
        self.assertIn("skipped_dirty_worktree", error)
        self._git(self.worktree, "checkout", "--", "unrelated.py")

        no_intent = {k: v for k, v in task.items() if k != "review_requeue_intent"}
        (ok, error), _ = self._prepare(config, no_intent, "Claude2")
        self.assertFalse(ok)

        stale_generation = {**task, "generation": 2}
        (ok, error), _ = self._prepare(config, stale_generation, "Claude2")
        self.assertFalse(ok)

        reviewing = {**task, "status": "review"}
        (ok, error), _ = self._prepare(config, reviewing, "Claude2")
        self.assertFalse(ok)

        (ok, error), _ = self._prepare(
            config, task, "Claude2",
            state={"worker_worktrees": {"leases": {self.task_id: {
                "path": str(self.worktree), "branch": self.source_branch,
                "last_target_agent": "Codex",
            }}}},
        )
        self.assertFalse(ok)
        self.assertEqual(
            (self.worktree / "scoped_service.py").read_text(encoding="utf-8"),
            "def run():\n    return 'wip'\n",
        )
        self.assertEqual(self._git(self.worktree, "rev-parse", "HEAD"), self.source_head)

    def _run_process_queue(self, config, task, agent, *, state):
        """Real process_queue + real prepare + real block-mode tree guard."""
        sup = worker_workspace._supervisor_module()
        config = {**config, "worker_tree_guard": {"enabled": True, "mode": "block"},
                  "ready_dispatcher": {"enabled": True}}
        request = worker_workspace.DeliveryRequest(
            agent_id=agent, provider="test", delivery_mode="auto", message="launch",
            task_id=self.task_id, reason=worker_workspace.REASON_OWNED_IN_PROGRESS,
            metadata={
                "workspace_path": str(self.worktree), "task_generation": 1,
                "task": {"review_requeue_intent": task.get("review_requeue_intent")},
            },
        )
        event = {
            "event_id": "evt-q", "task_id": self.task_id, "task_generation": 1,
            "target_agent": agent, "delivery_endpoint_id": "ep",
            "reason": worker_workspace.REASON_OWNED_IN_PROGRESS, "message": "wake",
        }
        import runtime_state
        if "evt-q" not in (state.get("queue") or {}).get("events", {}):
            runtime_state.store_queue_event(state, event)
        launches = []
        decision = mock.Mock(eligible=True)
        with (
            mock.patch.object(sup, "load_status", return_value={}),
            mock.patch.object(worker_workspace, "load_status", return_value={}),
            mock.patch.object(worker_workspace, "_fetch_worker_base_ref", return_value=(True, None)),
            mock.patch.object(sup, "task_index_from_status", return_value={self.task_id: task}),
            mock.patch.object(sup, "canonical_agent_name", side_effect=lambda c, v: str(v or "")),
            mock.patch.object(sup, "task_current_dispatch_responsibility", return_value="owner"),
            mock.patch.object(worker_workspace, "validate_task_repository_scope", return_value="pantheon"),
            mock.patch.object(sup, "ready_dispatch_settings", return_value={"enabled": True, "active_worker_statuses": []}),
            mock.patch.object(sup, "command_runtime_dispatch_block_reason", return_value=None),
            mock.patch.object(sup, "queue_events", return_value=[event]),
            mock.patch.object(sup, "stale_dispatch_skip_message", return_value=None),
            mock.patch.object(sup, "evaluate_queued_delivery_admission", return_value=decision),
            mock.patch.object(sup, "build_request", return_value=request),
            mock.patch.object(sup, "prepare_worker_workspace", worker_workspace.prepare_worker_workspace),
            mock.patch.object(sup, "start_worker_for_request",
                              side_effect=lambda *a, **k: launches.append(1) or (True, {}, {})),
            mock.patch.object(sup, "write_activity_log"),
            mock.patch.object(sup, "sync_dispatched_task_status", create=True),
        ):
            try:
                sup.process_queue(config, state)
            except Exception as exc:  # surfaced with context for iteration
                raise AssertionError(f"process_queue failed: {exc!r}") from exc
        return launches, state["queue"]["events"].get("evt-q", {})

    def test_process_queue_launches_owner_wip_through_block_guard_but_not_others(self) -> None:
        config, task = self._resume_fixture()
        self._dirty_owned_wip()
        before = self._snapshot()
        launches, record = self._run_process_queue(
            config, task, "Claude2", state={"worker_worktrees": {"leases": {}}}
        )
        self.assertEqual(launches, [1], record)
        self.assertEqual(self._snapshot(), before)
        # Reviewer: refused at preparation, never launched, WIP untouched.
        launches, record = self._run_process_queue(
            config, task, "Codex", state={"worker_worktrees": {"leases": {}}}
        )
        self.assertEqual(launches, [])
        self.assertEqual(record.get("status"), "pending")
        self.assertEqual(self._snapshot(), before)
        # Unrelated dirty file: refused.
        (self.worktree / "unrelated.py").write_text("# stray\n", encoding="utf-8")
        launches, record = self._run_process_queue(
            config, task, "Claude2", state={"worker_worktrees": {"leases": {}}}
        )
        self.assertEqual(launches, [])

    # -- real planner -> reservation -> process_queue reopen regression ------

    def _real_flow_fixture(self, *, owner="Claude2", reviewer="Codex"):
        import test_supervisor as ts
        sup = worker_workspace._supervisor_module()
        config, task = self._resume_fixture(owner=owner, reviewer=reviewer)
        base = ts.config_fixture(self.root)
        for key in ("schema", "ready_dispatcher", "providers", "worker_reassignment"):
            config[key] = base[key]
        config["paths"].update(base["paths"])
        config["ready_dispatcher"] = {
            **base["ready_dispatcher"], "enabled": True,
            "max_concurrent_per_account": {"claude2_account": 2, "codex_account": 2},
        }
        config["agents"] = {
            "claude2": {"id": "claude2", "display_name": "Claude2", "provider": "claude2", "adapter": "codex", "max_parallel": 2},
            "codex": base["agents"]["codex"],
        }
        config["providers"] = {
            "claude2": {"delivery_mode": "codex", "account": "claude2-account"},
            "codex": base["providers"]["codex"],
        }
        config["worker_reassignment"] = {"enabled": False}
        config["worker_tree_guard"] = {"enabled": True, "mode": "block"}
        task = {**task, "depends_on": [], "last_update": "2026-11-12T14:00:00Z"}
        return sup, ts, config, task

    def _assert_wip_preserved(self, before):
        """Owner WIP bytes/head/index are unchanged; only the generated,
        untracked worker task-brief may appear beside them."""
        after = self._snapshot()
        self.assertEqual(after["head"], before["head"])
        self.assertEqual(after["files"], before["files"])
        generated = ".orchestrator/worker-runtime/task-context/"
        extra = [
            line for line in after["status"].splitlines()
            if line not in before["status"].splitlines()
        ]
        self.assertTrue(all(generated in line and line.startswith("??") for line in extra), extra)
        self.assertEqual(self._git(self.worktree, "diff", "--cached", "--name-only"), "")

    def _write_status(self, config, task):
        Path(config["paths"]["status_file"]).write_text(
            json.dumps({"tasks": [task]}), encoding="utf-8"
        )

    def _flow_patches(self, sup, launches, *, health_refresh=None):
        status_path = self.root / "ai-status.json"

        def read_status(*_a, **_k):
            return json.loads(status_path.read_text(encoding="utf-8"))

        return [
            mock.patch.object(sup, "load_status", side_effect=read_status),
            mock.patch.object(worker_workspace, "load_status", side_effect=read_status),
            mock.patch.object(sup, "write_activity_log"),
            mock.patch.object(sup, "sync_dispatched_task_status", create=True),
            mock.patch.object(sup, "command_runtime_dispatch_block_reason", return_value=None),
            mock.patch.object(worker_workspace, "_fetch_worker_base_ref", return_value=(True, None)),
            mock.patch.object(worker_workspace, "validate_task_repository_scope", return_value="pantheon"),
            mock.patch.object(sup, "prepare_worker_workspace", worker_workspace.prepare_worker_workspace),
            mock.patch.object(
                sup, "start_worker_for_request",
                side_effect=lambda *a, **k: launches.append(1) or (True, {}, {}),
            ),
        ]

    def test_shrinking_activity_reopen_wake_survives_real_plan_reserve_process(self) -> None:
        import contextlib
        sup, ts, config, task = self._real_flow_fixture()
        self._write_status(config, task)
        self._dirty_owned_wip()
        before = self._snapshot()
        reopen = {"ts": "2026-11-12T14:37:57Z", "agent": "Human/Ops", "type": "reopen", "task_id": self.task_id}
        state = ts.with_healthy_delivery_health(
            config, {"workers": {}, "queue": {"events": {}}, "worker_worktrees": {"leases": {}}}
        )
        launches: list[int] = []
        with contextlib.ExitStack() as stack:
            for patcher in self._flow_patches(sup, launches):
                stack.enter_context(patcher)
            status = {"tasks": [task]}
            # Plan sees two reopen history rows; the tail then rotates to one.
            plan = sup.build_dispatch_plan(
                config, state, status, [], live_total=0, activity_events=[reopen, reopen]
            )
            self.assertEqual(len(plan["events"]), 1, plan)
            planned = plan["events"][0]
            self.assertEqual(planned["reason"], worker_workspace.REASON_OWNED_IN_PROGRESS)
            self.assertEqual(planned["review_requeue_intent_id"], task["review_requeue_intent"]["intent_id"])
            self.assertNotIn("review_reopen_revision", planned)
            self.assertTrue(sup.reserve_dispatch_plan(config, state, plan, activity_events=[reopen]))
            events = sup.queue_events(state)
            self.assertEqual(len(events), 1)
            event_id = events[0]["event_id"]

            # Pending -> materialized acknowledgement keeps the same wake valid.
            acked = {**task, "review_requeue_intent": {
                **task["review_requeue_intent"], "status": "materialized",
                "queue_event_id": event_id, "event_key": planned["key"],
                "materialized_at": "2026-11-12T14:38:00Z",
            }}
            self._write_status(config, acked)
            task_map = {self.task_id: acked}
            self.assertIsNone(
                sup.stale_dispatch_skip_message(config, events[0], task_map, activity_events=[])
            )

            # Genuine health-refresh wait must leave the event pending.
            healthy = state["delivery_health"]
            state["delivery_health"] = {"version": 1, "endpoints": {}, "accounts": {}}
            demand: list[dict[str, str]] = []
            sup.process_queue(config, state, health_refresh_demand=demand)
            self.assertEqual(launches, [])
            self.assertEqual(state["queue"]["events"][event_id]["status"], "pending")
            self.assertNotEqual(state["queue"]["events"][event_id].get("skip_reason"), "stale_dispatch_event")
            self.assertEqual(self._snapshot(), before)

            # Health recovered: exactly one same-owner launch, WIP untouched.
            state["delivery_health"] = healthy
            sup.process_queue(config, state)
            self.assertEqual(launches, [1], state["queue"]["events"][event_id])
            self._assert_wip_preserved(before)
            self.assertEqual(self._git(self.worktree, "stash", "list"), "")

            # Duplicate/restart replay of the same plan launches nothing more.
            self.assertFalse(sup.reserve_dispatch_plan(config, state, plan, activity_events=[]))
            sup.process_queue(config, state)
            self.assertEqual(launches, [1])
            self._assert_wip_preserved(before)

    def test_real_flow_rejects_stale_identity_and_non_owner_without_launch(self) -> None:
        import contextlib
        sup, ts, config, task = self._real_flow_fixture()
        self._write_status(config, task)
        self._dirty_owned_wip()
        before = self._snapshot()
        state = ts.with_healthy_delivery_health(
            config, {"workers": {}, "queue": {"events": {}}, "worker_worktrees": {"leases": {}}}
        )
        launches: list[int] = []
        with contextlib.ExitStack() as stack:
            for patcher in self._flow_patches(sup, launches):
                stack.enter_context(patcher)
            plan = sup.build_dispatch_plan(
                config, state, {"tasks": [task]}, [], live_total=0, activity_events=[]
            )
            self.assertTrue(sup.reserve_dispatch_plan(config, state, plan, activity_events=[]))
            event = sup.queue_events(state)[0]
            event_id = event["event_id"]
            intent = task["review_requeue_intent"]
            variants = {
                "intent": {**task, "review_requeue_intent": {**intent, "intent_id": "review-requeue-" + "b" * 64}},
                "generation": {**task, "generation": 2, "review_requeue_intent": {**intent, "task_generation": 2}},
                "owner": {**task, "owner": "Codex", "review_requeue_intent": {**intent, "owner": "Codex"}},
                "reviewer": {**task, "reviewer": "Codex3", "review_requeue_intent": {**intent, "reviewer": "Codex3"}},
                "dependency": {**task, "depends_on": ["OTHER-TASK"]},
            }
            for name, variant in variants.items():
                self.assertIsNotNone(
                    sup.stale_dispatch_skip_message(
                        config, event, {self.task_id: variant}, activity_events=[]
                    ),
                    name,
                )
            # Stale in the real queue path: event is completed, never launched.
            self._write_status(config, variants["intent"])
            sup.process_queue(config, state)
            record = state["queue"]["events"][event_id]
            self.assertEqual(launches, [])
            self.assertEqual(record.get("skip_reason"), "stale_dispatch_event")
            self.assertEqual(self._snapshot(), before)

    def test_real_flow_unrelated_file_refuses_launch_and_keeps_pending(self) -> None:
        import contextlib
        sup, ts, config, task = self._real_flow_fixture()
        self._write_status(config, task)
        self._dirty_owned_wip()
        (self.worktree / "unrelated.py").write_text("# stray\n", encoding="utf-8")
        before = self._snapshot()
        state = ts.with_healthy_delivery_health(
            config, {"workers": {}, "queue": {"events": {}}, "worker_worktrees": {"leases": {}}}
        )
        launches: list[int] = []
        with contextlib.ExitStack() as stack:
            for patcher in self._flow_patches(sup, launches):
                stack.enter_context(patcher)
            plan = sup.build_dispatch_plan(
                config, state, {"tasks": [task]}, [], live_total=0, activity_events=[]
            )
            self.assertTrue(sup.reserve_dispatch_plan(config, state, plan, activity_events=[]))
            event_id = sup.queue_events(state)[0]["event_id"]
            sup.process_queue(config, state)
        self.assertEqual(launches, [])
        self.assertEqual(state["queue"]["events"][event_id]["status"], "pending")
        self.assertEqual(self._snapshot(), before)


class OwnerWipResumeAutostashTests(unittest.TestCase):
    """Real-Git regression: WIP resume must never autostash or conflict-mark."""

    def _git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True,
        ).stdout

    def _repo(self, root: Path) -> tuple[Path, str]:
        repo = root / "wt"
        repo.mkdir()
        self._git(repo, "init", "-q", "-b", "task")
        self._git(repo, "config", "user.email", "t@example.invalid")
        self._git(repo, "config", "user.name", "t")
        self._git(repo, "config", "merge.autoStash", "true")
        (repo / "scoped_service.py").write_text("a\nb\nc\n")
        (repo / "other.py").write_text("x\n")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-qm", "base")
        self._git(repo, "checkout", "-qb", "base")
        (repo / "scoped_service.py").write_text("a\nBASE\nc\n")
        self._git(repo, "commit", "-qam", "base edit")
        sha = self._git(repo, "rev-parse", "HEAD").strip()
        self._git(repo, "checkout", "-q", "task")
        return repo, sha

    def test_overlapping_wip_not_autostashed_or_conflicted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, sha = self._repo(Path(tmp))
            (repo / "scoped_service.py").write_text("a\nOWNER\nc\n")
            (repo / "other.py").write_text("y\n")
            self._git(repo, "add", "other.py")
            ok, status = worker_workspace._resume_owner_wip_worktree(repo, sha)
            self.assertTrue(ok, status)
            self.assertEqual(status, "owner_wip_resumed")
            self.assertEqual((repo / "scoped_service.py").read_text(), "a\nOWNER\nc\n")
            self.assertEqual((repo / "other.py").read_text(), "y\n")
            self.assertEqual(self._git(repo, "stash", "list"), "")
            self.assertNotIn("U", "".join(
                e["status"] for e in worker_workspace._git_dirty_entries(repo)))

    def test_non_overlapping_wip_fast_forwards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, sha = self._repo(Path(tmp))
            (repo / "other.py").write_text("y\n")
            ok, status = worker_workspace._resume_owner_wip_worktree(repo, sha)
            self.assertTrue(ok)
            self.assertEqual(status, "owner_wip_resumed+ff")
            self.assertEqual(self._git(repo, "stash", "list"), "")



if __name__ == "__main__":
    unittest.main()
