#!/usr/bin/env python3
"""
Smoke test for the deployable capital service boundary.

Run:
    python3 services/capital/smoke_test.py
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile

from fastapi.testclient import TestClient

PASS = 0
FAIL = 0


def check(label: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"[PASS] {label}")
    else:
        FAIL += 1
        print(f"[FAIL] {label}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="capital_smoke_") as tempdir:
        os.environ["CAPITAL_DATA_DIR"] = tempdir
        os.environ["PANTHEON_GOVERNANCE_DATA_DIR"] = tempdir
        sys.modules.pop("services.capital.main", None)
        module = importlib.import_module("services.capital.main")
        module = importlib.reload(module)
        client = TestClient(module.app)

        health = client.get("/health")
        check("health endpoint responds", health.status_code == 200)

        pool = client.post(
            "/api/capital-pools",
            json={
                "actor_id": "capital-admin-1",
                "actor_role": "capital.admin",
                "pool_id": "pool-smoke-001",
                "name": "Smoke Pool",
                "owner_id": "fund-smoke",
                "owner_type": "fund",
            },
        )
        check("capital pool create succeeds", pool.status_code == 201)

        binding = client.post(
            "/api/bindings",
            json={
                "actor_id": "persona-admin-1",
                "actor_role": "persona.admin",
                "binding_id": "binding-smoke-001",
                "persona_id": "persona-smoke",
                "capital_pool_id": "pool-smoke-001",
                "role": "live_owner",
                "allowed_deployment_scope": "canary",
            },
        )
        check("binding create succeeds", binding.status_code == 201)

        activate = client.post(
            "/api/bindings/binding-smoke-001/activate",
            json={
                "actor_id": "persona-admin-1",
                "actor_role": "persona.admin",
                "approval_decision_id": "approval-smoke-001",
            },
        )
        check("binding activation succeeds", activate.status_code == 200)

        admissibility = client.get(
            "/api/bindings/admissibility",
            params={
                "persona_id": "persona-smoke",
                "capital_pool_id": "pool-smoke-001",
                "target_stage": "paper",
            },
        )
        check(
            "binding read path permits paper deployment",
            admissibility.status_code == 200 and admissibility.json()["permitted"] is True,
        )

        live = client.get(
            "/api/bindings/admissibility",
            params={
                "persona_id": "persona-smoke",
                "capital_pool_id": "pool-smoke-001",
                "target_stage": "live",
            },
        )
        check(
            "binding read path blocks stage above ceiling",
            live.status_code == 200 and live.json()["permitted"] is False,
        )

        unauthorized = client.post(
            "/api/capital-pools",
            json={
                "actor_id": "persona-admin-1",
                "actor_role": "persona.admin",
                "pool_id": "pool-unauthorized",
                "name": "Nope",
                "owner_id": "fund-nope",
                "owner_type": "fund",
            },
        )
        check("write authority rejects wrong role", unauthorized.status_code == 403)

        audit = client.get("/api/capital/audit")
        check("audit endpoint returns mutation events", audit.status_code == 200 and len(audit.json()) >= 3)

    total = PASS + FAIL
    print()
    print(f"SUMMARY {PASS}/{total} checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
