from __future__ import annotations

from pathlib import Path
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "nightly-publish-cut.yml"


def test_nightly_publish_cut_admits_exact_pair_before_dispatch() -> None:
    """Only an exact accepted FE/BFF pair may reach the deploy dispatch."""
    content = WORKFLOW_PATH.read_text(encoding="utf-8")
    
    # Parse YAML to ensure valid syntax
    data = yaml.safe_load(content)
    assert isinstance(data, dict)
    assert "jobs" in data
    assert "cut" in data["jobs"]
    
    steps = data["jobs"]["cut"]["steps"]
    names = [step.get("name") for step in steps]
    admission_index = names.index("Admit exact accepted FE/BFF pair")
    deploy_index = names.index("Deploy snapshot to dev")
    assert admission_index < deploy_index

    admission_step = steps[admission_index]
    admission_script = admission_step.get("run", "")
    assert "scripts/agora_compat_manifest.py deployment-gate" in admission_script
    assert '--backend-runtime-commit "$candidate_sha"' in admission_script
    assert '--frontend-runtime-commit "$frontend_sha"' in admission_script
    assert "admitted=false" in admission_script

    deploy_step = steps[deploy_index]
    assert deploy_step.get("if") == "steps.pair_admission.outputs.admitted == 'true'"
    run_script = deploy_step.get("run", "")

    assert "gh workflow run nonprod-deploy.yml" in run_script, "gh workflow run nonprod-deploy.yml invocation missing"
    assert "--ref dev" in run_script, "gh workflow run invocation must explicitly carry '--ref dev'"
    assert '-f ref="${branch}"' in run_script or '-f ref="${{ steps.cut.outputs.publish_branch }}"' in run_script, (
        "gh workflow run invocation must still pass ref payload parameter for snapshot branch"
    )
