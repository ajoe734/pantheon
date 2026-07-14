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
    assert 'printf \'/%s\\n\' "$path" >>.git/info/exclude' in deploy
