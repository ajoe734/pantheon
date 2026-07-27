from __future__ import annotations

from pathlib import Path
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "nightly-publish-cut.yml"


def test_nightly_publish_cut_never_dispatches_deploy() -> None:
    """Publish snapshots are inputs to, not substitutes for, pair admission."""
    content = WORKFLOW_PATH.read_text(encoding="utf-8")
    
    # Parse YAML to ensure valid syntax
    data = yaml.safe_load(content)
    assert isinstance(data, dict)
    assert "jobs" in data
    assert "cut" in data["jobs"]
    
    steps = data["jobs"]["cut"]["steps"]
    names = [step.get("name") for step in steps]
    assert "Deploy snapshot to dev" not in names
    assert "Admit exact accepted FE/BFF pair" not in names
    assert "gh workflow run" not in content
    assert "actions: write" not in content
    assert "deploy_dispatched: false" in content
