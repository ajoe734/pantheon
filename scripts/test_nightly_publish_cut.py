from __future__ import annotations

from pathlib import Path
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "nightly-publish-cut.yml"


def test_nightly_publish_cut_pins_nonprod_deploy_dispatch_to_dev() -> None:
    """Verify that gh workflow run nonprod-deploy.yml includes --ref dev flag."""
    content = WORKFLOW_PATH.read_text(encoding="utf-8")
    
    # Parse YAML to ensure valid syntax
    data = yaml.safe_load(content)
    assert isinstance(data, dict)
    assert "jobs" in data
    assert "cut" in data["jobs"]
    
    # Find the deploy step in the workflow file
    steps = data["jobs"]["cut"]["steps"]
    deploy_step = None
    for step in steps:
        if step.get("name") == "Deploy snapshot to dev":
            deploy_step = step
            break
            
    assert deploy_step is not None, "Deploy snapshot to dev step missing in workflow"
    run_script = deploy_step.get("run", "")
    
    assert "gh workflow run nonprod-deploy.yml" in run_script, "gh workflow run nonprod-deploy.yml invocation missing"
    assert "--ref dev" in run_script, "gh workflow run invocation must explicitly carry '--ref dev'"
    assert '-f ref="${branch}"' in run_script or '-f ref="${{ steps.cut.outputs.publish_branch }}"' in run_script, (
        "gh workflow run invocation must still pass ref payload parameter for snapshot branch"
    )
