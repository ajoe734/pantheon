"""Smoke test for ensuring capital binding checks are enforced."""

import sys
from pathlib import Path

# Add services directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Add runtime-manager directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "runtime-manager"))

from service import RuntimeManagerError, RuntimeManagerService


DISABLED_BINDING_STATUS = "suspended"


def _deploy_with_disabled_capital_binding():
    # Instantiate in-memory store
    service = RuntimeManagerService(store_path=None)

    # Deploy request with a canonical non-active capital binding.
    request = {
        "plan_id": "test-plan",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "art-1",
        "artifact_version": "1.0",
        "capital_pool_id": "pool-1",
        "persona_capital_binding_id": "pcb-1",
        "persona_capital_binding_status": DISABLED_BINDING_STATUS,
        "allowed_deployment_scope": "paper",
        "loader_checks_passed": True,
    }

    try:
        service.deploy(request)
    except RuntimeManagerError as e:
        return e

    raise AssertionError("Expected RuntimeManagerError, but deploy() succeeded")


def test_capital_binding_disabled():
    error = _deploy_with_disabled_capital_binding()
    message = str(error)

    assert DISABLED_BINDING_STATUS in message
    assert "not 'active'" in message


def main():
    print("Running capital binding disabled smoke test...")
    try:
        test_capital_binding_disabled()
    except AssertionError as e:
        print(f"FAIL: {e}")
        return 1
    except Exception as e:
        print(f"FAIL: Caught unexpected exception: {e}")
        return 1

    print("PASS: Suspended capital binding was rejected before RuntimeBinding creation")
    return 0

if __name__ == "__main__":
    sys.exit(main())
