from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _deploy_clean_functions_harness(function_name: str) -> str:
    deploy = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    start = deploy.index("preserve_known_deploy_runtime_state() {")
    end = deploy.index("\n}\n\ngit_fetch_origin()", start) + 3
    function_source = deploy[start:end]
    return f"""set -euo pipefail
info() {{ printf '[test] %s\\n' "$*"; }}
error() {{ printf '[test] ERROR: %s\\n' "$*" >&2; exit 1; }}
{function_source}
{function_name}
"""


def _init_runtime_repo(repo: Path) -> tuple[Path, Path, str]:
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "deploy-test@example.invalid")
    _git(repo, "config", "user.name", "Deploy Test")

    validator = repo / "scripts/deploy_planning_runtime_paths.py"
    validator.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/deploy_planning_runtime_paths.py", validator)
    session = (
        repo
        / "docs/02-architecture/consensus/sessions/phase9-test-session/planning-session.json"
    )
    session.parent.mkdir(parents=True)
    session.write_text('{"revision":1}\n', encoding="utf-8")
    pointer = repo / ".orchestrator/planning-session-pointer.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(
        json.dumps(
            {
                "session_id": "phase9-test-session",
                "planning_dir": (
                    "docs/02-architecture/consensus/sessions/phase9-test-session"
                ),
                "session_file": session.relative_to(repo).as_posix(),
                "updated_at": "before",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (repo / "unrelated.txt").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", "scripts", ".orchestrator", "docs", "unrelated.txt")
    _git(repo, "commit", "-qm", "test baseline")
    return pointer, session, _git(repo, "rev-parse", "HEAD").stdout.strip()


def _run_deploy_clean_function(
    repo: Path,
    deploy_sha: str,
    function_name: str = "preserve_known_deploy_runtime_state",
    allow_dirty: str = "false",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", _deploy_clean_functions_harness(function_name)],
        cwd=repo,
        env={
            "PATH": os.environ["PATH"],
            "HOME": os.environ.get("HOME", str(repo)),
            "PANTHEON_DEPLOY_ENV": "dev",
            "PANTHEON_DEPLOY_COMPONENT": "root",
            "PANTHEON_DEPLOY_SHA": deploy_sha,
            "PANTHEON_ALLOW_DIRTY_DEPLOY": allow_dirty,
        },
        capture_output=True,
        text=True,
    )


def test_trade_journey_runtime_store_is_ignored_at_repo_root() -> None:
    patterns = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "/trade_journey_events.json" in patterns


def test_nonprod_deploy_excludes_unreadable_untracked_runtime_state() -> None:
    deploy = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")

    assert '"trade_journey_events.json"' in deploy
    assert 'git ls-files --error-unmatch -- "$path"' in deploy
    assert 'git rev-parse --git-path info/exclude' in deploy
    assert 'printf \'/%s\\n\' "$path" >>"$exclude_file"' in deploy
    assert 'mkdir -p .git/info' not in deploy


def test_nonprod_deploy_preserves_untracked_paths_tracked_by_target_commit() -> None:
    deploy = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")

    assert "preserve_target_tracked_untracked_paths" in deploy
    assert "git status --porcelain -z" in deploy
    assert 'git cat-file -e "${PANTHEON_DEPLOY_SHA}:${path}"' in deploy
    assert "deploy-target-tracked-untracked-${PANTHEON_DEPLOY_ENV}" in deploy
    assert "preserving untracked paths that target commit tracks before checkout" in deploy
    assert (
        'git stash push --include-untracked -m "$stash_label" '
        '-- "${target_tracked_paths[@]}"'
    ) in deploy
    assert (
        deploy.index("  preserve_known_deploy_runtime_state")
        < deploy.index("  preserve_target_tracked_untracked_paths")
        < deploy.index('  status="$(git status --porcelain)"')
    )


def test_nonprod_deploy_preserves_pointer_referenced_planning_runtime_state() -> None:
    deploy = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")

    assert 'planning_pointer_path=".orchestrator/planning-session-pointer.json"' in deploy
    assert 'scripts/deploy_planning_runtime_paths.py' in deploy
    assert 'git show "${PANTHEON_DEPLOY_SHA}:scripts/deploy_planning_runtime_paths.py"' in deploy
    assert 'known_paths+=("$planning_pointer_path" "$planning_session_path")' in deploy
    assert deploy.index('planning_session_path="$({') < deploy.index(
        'known_paths+=("$planning_pointer_path"'
    )
    assert deploy.index('known_paths+=("$planning_pointer_path"') < deploy.index(
        'git stash push --include-untracked -m "$stash_label" -- "${present_paths[@]}"'
    )


def test_preserve_behavior_stashes_and_restores_exact_pointer_and_session(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    pointer, session, deploy_sha = _init_runtime_repo(repo)
    pointer_bytes = pointer.read_bytes().replace(b'"before"', b'"runtime-update"')
    session_bytes = b'{"revision":2,"runtime":"in-flight"}\n'
    pointer.write_bytes(pointer_bytes)
    session.write_bytes(session_bytes)

    completed = _run_deploy_clean_function(repo, deploy_sha)

    assert completed.returncode == 0, completed.stderr
    assert _git(repo, "status", "--porcelain").stdout == ""
    assert "deploy-runtime-state-dev-root-" in _git(repo, "stash", "list").stdout
    _git(repo, "stash", "pop", "-q")
    assert pointer.read_bytes() == pointer_bytes
    assert session.read_bytes() == session_bytes


def test_preserve_behavior_rejects_pointer_escape_without_stashing_other_paths(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    pointer, _, deploy_sha = _init_runtime_repo(repo)
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    payload["session_file"] = "docs/02-architecture/consensus/sessions/../../../../unrelated.txt"
    payload.pop("planning_dir")
    pointer.write_text(json.dumps(payload), encoding="utf-8")
    unrelated = repo / "unrelated.txt"
    unrelated.write_text("must-not-be-stashed\n", encoding="utf-8")

    completed = _run_deploy_clean_function(repo, deploy_sha)

    assert completed.returncode != 0
    assert "failed path validation" in completed.stderr
    assert _git(repo, "stash", "list").stdout == ""
    assert unrelated.read_text(encoding="utf-8") == "must-not-be-stashed\n"
    status = _git(repo, "status", "--porcelain").stdout
    assert ".orchestrator/planning-session-pointer.json" in status
    assert "unrelated.txt" in status


def test_clean_gate_uses_target_sha_validator_and_preserves_target_tracked_untracked(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    pointer, session, _ = _init_runtime_repo(repo)
    validator = repo / "scripts/deploy_planning_runtime_paths.py"
    validator.write_text("raise SystemExit(97)\n", encoding="utf-8")
    _git(repo, "add", validator.relative_to(repo).as_posix())
    _git(repo, "commit", "-qm", "current checkout with stale validator")
    current_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    shutil.copy2(ROOT / "scripts/deploy_planning_runtime_paths.py", validator)
    target_tracked = repo / "target-tracked-runtime.json"
    target_tracked.write_text('{"target":"baseline"}\n', encoding="utf-8")
    _git(repo, "add", validator.relative_to(repo).as_posix(), target_tracked.name)
    _git(repo, "commit", "-qm", "target checkout with validator and tracked path")
    target_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert current_sha != target_sha
    _git(repo, "checkout", "-q", current_sha)

    pointer_bytes = pointer.read_bytes().replace(b'"before"', b'"current-runtime"')
    session_bytes = b'{"revision":7,"runtime":"current-checkout"}\n'
    target_runtime_bytes = b'{"target":"runtime-untracked"}\n'
    pointer.write_bytes(pointer_bytes)
    session.write_bytes(session_bytes)
    target_tracked.write_bytes(target_runtime_bytes)

    completed = _run_deploy_clean_function(
        repo, target_sha, function_name="require_clean_checkout", allow_dirty="false"
    )

    assert completed.returncode == 0, completed.stderr
    assert _git(repo, "status", "--porcelain").stdout == ""
    stash_list = _git(repo, "stash", "list").stdout
    assert "deploy-runtime-state-dev-root-" in stash_list
    assert "deploy-target-tracked-untracked-dev-root-" in stash_list
    assert "deploy-dirty-" not in stash_list

    _git(repo, "stash", "pop", "-q", "stash@{0}")
    assert target_tracked.read_bytes() == target_runtime_bytes
    _git(repo, "stash", "pop", "-q", "stash@{0}")
    assert pointer.read_bytes() == pointer_bytes
    assert session.read_bytes() == session_bytes


def test_clean_gate_keeps_unrelated_dirty_path_fail_closed_without_allow_dirty(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    pointer, _, deploy_sha = _init_runtime_repo(repo)
    pointer.write_bytes(pointer.read_bytes().replace(b'"before"', b'"runtime"'))
    unrelated = repo / "unrelated.txt"
    unrelated.write_text("still-dirty\n", encoding="utf-8")

    completed = _run_deploy_clean_function(
        repo, deploy_sha, function_name="require_clean_checkout", allow_dirty="false"
    )

    assert completed.returncode != 0
    assert "refusing deploy without --allow-dirty" in completed.stderr
    assert unrelated.read_text(encoding="utf-8") == "still-dirty\n"
    assert "deploy-dirty-" not in _git(repo, "stash", "list").stdout
