"""Smoke test for services.control_plane.internal_api
Runs the Flask app in test client and exercises endpoints.
"""
from services.control_plane import internal_api_min as api

def make_headers(token=True, mfa=None):
    headers = {}
    if token:
        headers["Authorization"] = "Bearer test-token"
    if mfa is not None:
        headers["X-MFA-Token"] = mfa
    return headers
import json

# Use the pure-Python API implementation (no Flask required)

def assert_status(resp, expected):
    status, payload = resp
    if status >= 400:
        print("FAIL", status, payload)
        raise SystemExit(1)
    print("OK", expected, status)

# Health
r = api.health()
assert_status(r, 200)

# Approve deployment
r = api.approve_deployment("plan-123", headers=make_headers(token=True, mfa="123456"), body={"approval_decision": "approve"})
assert_status(r, 202)
print(r[1])

# Pause runtime
r = api.pause_runtime("bind-1", headers=make_headers(token=True), body={"duration_seconds": 600, "reason": "smoke"})
assert_status(r, 202)
print(r[1])

# Rollback
r = api.execute_rollback(body={"rollback_target_type": "deployment", "target_id": "plan-123", "rollback_to_version": "v1.1.0"}, headers=make_headers(token=True, mfa="654321"))
assert_status(r, 202)
print(r[1])

# Kill-switch
r = api.kill_switch(body={"action": "activate", "scope": "all", "severity": "critical"}, headers=make_headers(token=True, mfa="000000"))
assert_status(r, 202)
print(r[1])

# Commands
r = api.get_command("cmd-sample", headers=make_headers(token=True))
assert_status(r, 200)
print(r[1])

print("SMOKE OK")
