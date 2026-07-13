from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy_nonprod_vm.sh"


def test_nonprod_bff_builds_receive_and_verify_requested_source_sha() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert script.count('GIT_SHA="${PANTHEON_DEPLOY_SHA}"') >= 4
    assert "assert_bff_source_sha()" in script
    assert (
        script.count(
            "assert_bff_source_sha http://127.0.0.1:18001/bff/version"
        )
        == 2
    )
    assert (
        "assert_bff_source_sha http://127.0.0.1:38001/bff/version"
        in script
    )
