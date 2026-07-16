from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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
    assert 'git stash push --include-untracked -m "$stash_label" -- "${target_tracked_paths[@]}"' in deploy
    assert (
        deploy.index("  preserve_known_deploy_runtime_state")
        < deploy.index("  preserve_target_tracked_untracked_paths")
        < deploy.index('  status="$(git status --porcelain)"')
    )
