from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy_nonprod_vm.sh"


def test_nonprod_deploy_defaults_to_strict_bff_auth() -> None:
    """The dev deploy script always passes an explicit AUTH_STUB/AUTH_MODE
    value into the compose environment, which overrides docker-compose.yml's
    own strict default regardless of what that file says. Regression guard
    for LOOP-PROD-AUTH-001: the script's own default must also be strict, or
    every dev deploy silently re-forces stub/permissive auth."""
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'DEV_BFF_AUTH_STUB="${DEV_BFF_AUTH_STUB:-false}"' in script
    assert 'DEV_BFF_AUTH_MODE="${DEV_BFF_AUTH_MODE:-strict}"' in script
    assert 'DEV_BFF_AUTH_STUB="${DEV_BFF_AUTH_STUB:-true}"' not in script
    assert 'DEV_BFF_AUTH_MODE="${DEV_BFF_AUTH_MODE:-permissive}"' not in script
