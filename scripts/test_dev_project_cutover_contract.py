from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GCP_BASELINE_SCRIPT = REPO_ROOT / "scripts" / "gcp_nonprod_baseline.sh"


def test_gcp_baseline_grants_deploy_sa_compute_access() -> None:
    script = GCP_BASELINE_SCRIPT.read_text(encoding="utf-8")
    role_block = script.split('info "Step 5/6:', maxsplit=1)[0]

    assert (
        'ensure_project_role "serviceAccount:${CLOUD_BUILD_SA}" '
        '"roles/compute.instanceAdmin.v1"'
    ) in role_block
    assert '--member="serviceAccount:${CLOUD_BUILD_SA}"' in role_block
    assert '--role="roles/iam.serviceAccountUser"' in role_block
