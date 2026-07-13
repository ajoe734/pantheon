#!/usr/bin/env python3
"""E2E verifier for all 12 Trade Journey E2E acceptance scenarios on the hosted stack.
"""
import os
import sys
import ssl
import json
import urllib.request
import urllib.error

def _ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def _get(base, path, token=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base.rstrip("/") + path, headers=headers)
    ctx = _ctx()
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except:
            return e.code, None
    except Exception as e:
        print(f"Connection error to {path}: {e}")
        return None, None

def main():
    base = os.environ.get("BFF_BASE")
    if not base:
        print("ERROR: BFF_BASE environment variable is required.")
        return 2

    # Scenarios config
    expected = {
        "tj-scenario-1": {"status": "completed", "current_stage": "reconciliation", "read_state": "formal"},
        "tj-scenario-2": {"status": "open", "current_stage": "promotion_decision", "rejected": True},
        "tj-scenario-3": {"status": "failed", "current_stage": "risk_evaluation", "rejected": True},
        "tj-scenario-4": {"status": "failed", "current_stage": "broker_acknowledgement", "rejected": True},
        "tj-scenario-5": {"status": "cancelled", "current_stage": "fill_management"},
        "tj-scenario-6": {"status": "waiting_human", "current_stage": "trade_decision", "waiting_human": True},
        "tj-scenario-7": {"status": "completed", "current_stage": "reconciliation"},
        "tj-scenario-8": {"status": "incomplete", "current_stage": "reconciliation", "read_state": "degraded"},
        "tj-scenario-11": {"status": "completed", "current_stage": "reconciliation", "read_state": "partial"},
        "tj-scenario-12": {"status": "open", "current_stage": "trade_decision"},
    }

    # 1. Tenant Isolation & Metric Masking (Scenario 10 / RBAC)
    print("Scenario 10: RBAC Tenant Isolation check...")
    admin_token = "lupin:admin::pantheon-dev"
    tenant_a_token = "viewer-a:viewer::tenant-a"

    # List own tenant (expected 200)
    st, res = _get(base, "/bff/management/trade-journeys?tenant_id=pantheon-dev&environment=paper", admin_token)
    if st != 200:
        print(f"FAIL: Scoped token failed to access own tenant (status={st})")
        return 1
    print("  Query own tenant: 200 OK")

    # List other tenant (expected 403)
    st, res = _get(base, "/bff/management/trade-journeys?tenant_id=pantheon-dev&environment=paper", tenant_a_token)
    if st != 403:
        print(f"FAIL: Cross-tenant list access was NOT forbidden (status={st}, expected 403)")
        return 1
    print("  Cross-tenant query: 403 Forbidden (Isolated)")

    # Metrics own tenant (expected 200)
    st, res = _get(base, "/bff/management/trade-journeys/metrics?tenant_id=pantheon-dev&environment=paper", admin_token)
    if st != 200:
        print(f"FAIL: Access to own metrics failed (status={st})")
        return 1
    print("  Query own metrics: 200 OK")

    # Metrics other tenant (expected 403 / Masked)
    st, res = _get(base, "/bff/management/trade-journeys/metrics?tenant_id=pantheon-dev&environment=paper", tenant_a_token)
    if st != 403:
        print(f"FAIL: Cross-tenant metrics access was NOT forbidden (status={st}, expected 403)")
        return 1
    print("  Cross-tenant metrics query: 403 Forbidden (Masked)")

    # 2. Get list and verify scenario properties
    st, list_res = _get(base, "/bff/management/trade-journeys?tenant_id=pantheon-dev&environment=paper", admin_token)
    items = {item["journey_id"]: item for item in list_res["data"]["items"]}

    for j_id, spec in expected.items():
        if j_id not in items:
            print(f"FAIL: Scenario {j_id} not found in the list")
            return 1
        item = items[j_id]
        print(f"Verifying {j_id}...")
        for key, val in spec.items():
            if key in ("rejected", "waiting_human"):
                actual = item["flags"].get(key)
            else:
                actual = item.get(key)
            if actual != val:
                print(f"  FAIL: {j_id} property '{key}' expected {val}, got {actual}")
                return 1
            print(f"  ✓ {key} = {actual}")

    # Verify Scenario 10 in live env
    st, live_res = _get(base, "/bff/management/trade-journeys?tenant_id=pantheon-dev&environment=live", admin_token)
    live_items = {item["journey_id"]: item for item in live_res["data"]["items"]}
    if "tj-scenario-10" not in live_items:
        print("FAIL: Scenario tj-scenario-10 not found in live environment list")
        return 1
    print("Scenario 10: Verified tj-scenario-10 in live env")

    # 3. Arbitrary ID Resolution (Scenario 9)
    print("Scenario 9: Arbitrary ID Resolution check...")
    st, resolve_res = _get(base, "/bff/management/trade-journeys/resolve?q=co-scen-9&tenant_id=pantheon-dev&environment=paper", admin_token)
    if st != 200:
        print(f"FAIL: Resolution endpoint returned status {st}")
        return 1
    journey_ids = resolve_res["data"].get("journey_ids", [])
    if "tj-scenario-9" not in journey_ids:
        print(f"FAIL: Resolution for 'co-scen-9' did not find 'tj-scenario-9'")
        return 1
    print("  ✓ co-scen-9 resolved to tj-scenario-9 successfully")

    # 4. Scrubber Replay (Scenario 12)
    print("Scenario 12: Scrubber Replay check...")
    # Replay as-of 2026-07-12T12:01:30Z (after signal_generation but before trade_decision)
    st, replay_res = _get(base, "/bff/management/trade-journeys/tj-scenario-12/replay?tenant_id=pantheon-dev&environment=paper&as_of=2026-07-12T12:01:30Z", admin_token)
    if st != 200:
        print(f"FAIL: Scrubber replay returned status {st}")
        return 1
    replay_stage = replay_res["data"].get("current_stage")
    if replay_stage != "signal_generation":
        print(f"FAIL: Replay as-of 12:01:30Z expected stage 'signal_generation', got '{replay_stage}'")
        return 1
    print(f"  ✓ Replay as-of 12:01:30Z current_stage = '{replay_stage}'")

    print("\nALL 12 SCENARIOS VERIFIED SUCCESSFULLY ON HOSTED BFF!")
    return 0

if __name__ == "__main__":
    sys.exit(main())
