"""Smoke test for ensuring capital binding checks are enforced."""

import os
import sys
from pathlib import Path

# Add services directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Add runtime-manager directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "runtime-manager"))

from service import RuntimeManagerService, RuntimeManagerError

def test_capital_binding_disabled():
    # Instantiate in-memory store
    service = RuntimeManagerService(store_path=None)

    # Deploy request with inactive capital binding
    request = {
        "plan_id": "test-plan",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "art-1",
        "artifact_version": "1.0",
        "capital_pool_id": "pool-1",
        "persona_capital_binding_id": "pcb-1",
        "persona_capital_binding_status": "inactive", # Should fail
        "allowed_deployment_scope": "paper",
        "loader_checks_passed": True
    }

    print("Running capital binding disabled smoke test...")
    try:
        service.deploy(request)
        print("FAIL: Expected RuntimeManagerError, but deploy() succeeded")
        sys.exit(1)
    except RuntimeManagerError as e:
        print(f"PASS: Caught expected error: {e}")
        if "not 'active'" in str(e):
            print("PASS: Error message contains expected reason")
            sys.exit(0)
        else:
            print(f"FAIL: Error message does not contain expected reason. Got: {e}")
            sys.exit(1)
    except Exception as e:
        print(f"FAIL: Caught unexpected exception: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_capital_binding_disabled()
