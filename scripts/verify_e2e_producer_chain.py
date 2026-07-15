#!/usr/bin/env python3
"""E2E producer-chain live verifier.

Business flow under test:
  real threshold breach -> incident (deduped) -> sweep proposal -> formal Evolution Journal entry -> Persona Fleet recent MUTATION links to that formal entry.

Stages:
  1. Fetch active paper bindings from BFF.
  2. Ensure the selected binding's artifact has a registered expected drawdown baseline.
  3. Ingest a heartbeat telemetry event to ensure the summary is active and fresh.
  4. Ingest a drawdown_snapshot telemetry event with a threshold-breaching drawdown.
  5. Run a threshold sweep worker tick locally (interfacing with live telemetry/incidents services).
  6. Verify that the incident is successfully created and retrieve its ID.
  7. Run a daily sweep on the live evolution service to generate the proposal.
  8. Verify that the evolution journal contains the formal entry (proposed decision/mutation review).
  9. Verify that the Persona Fleet mutation projection links correctly to the proposal.
"""
from __future__ import annotations

import os
import sys
import json
import time
import uuid
import ssl
import datetime
import urllib.request
import urllib.error
import traceback
from typing import Any, Mapping

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import run_tick from the local threshold sweep worker
from services.evolution.threshold_sweep_worker import run_tick, DEFAULT_BASELINES_PATH


def _ctx():
    ctx = ssl.create_default_context()
    if os.environ.get("BFF_INSECURE", "1") == "1":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _http_request(url: str, headers: dict | None = None, method: str = "GET", data: Any = None, ctx: ssl.SSLContext | None = None) -> tuple[int, Any]:
    if headers is None:
        headers = {}
    if data is not None:
        if isinstance(data, dict):
            headers.setdefault("Content-Type", "application/json")
            body = json.dumps(data).encode("utf-8")
        else:
            body = data
    else:
        body = None
    req = urllib.request.Request(url, headers=headers, method=method, data=body)
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            res_body = r.read().decode("utf-8")
            return r.status, json.loads(res_body) if res_body else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            err_json = json.loads(err_body)
        except Exception:
            err_json = err_body
        return e.code, err_json
    except Exception as e:
        return 0, str(e)


def _items(payload):
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("items"), list):
            return payload["data"]["items"]
        for k in ("items", "data"):
            if isinstance(payload.get(k), list):
                return payload[k]
    elif isinstance(payload, list):
        return payload
    return []


def main() -> int:
    base = os.environ.get("BFF_BASE")
    token = os.environ.get("BFF_TOKEN", "op-dev:admin:mfa")
    if not base:
        print("ERROR: set BFF_BASE", file=sys.stderr)
        return 2

    ctx = _ctx()
    headers = {"Authorization": f"Bearer {token}"}

    # Resolve service URLs
    telemetry_url = os.environ.get("TELEMETRY_API_URL", "http://localhost:18083")
    incidents_url = os.environ.get("INCIDENTS_API_URL", "http://localhost:18090")
    evolution_url = os.environ.get("EVOLUTION_API_URL", "http://localhost:18093")

    print("== Starting Producer-Chain Live Verification (EVOCHAIN-010) ==")

    # 1. Fetch active paper bindings from BFF
    print("[1/9] Fetching active runtimes from BFF...")
    status, runtimes_payload = _http_request(base.rstrip("/") + "/bff/runtimes?page_size=100", headers=headers, ctx=ctx)
    if status != 200:
        print(f"FAIL: Failed to fetch runtimes. Status: {status}, Error: {runtimes_payload}", file=sys.stderr)
        return 1

    # Fetch existing proposals to filter out cooldown-blocked targets
    p_status, proposals = _http_request(f"{evolution_url}/api/evolution/proposals", ctx=ctx)
    blocked_targets = set()
    if p_status == 200 and isinstance(proposals, list):
        for p in proposals:
            if p.get("target_id"):
                blocked_targets.add(p["target_id"])

    bindings = _items(runtimes_payload)
    active_paper = [
        b for b in bindings
        if b.get("status") in ("active", "running")
        and b.get("deployment_mode") == "paper"
        and b.get("artifact_id")
        and b.get("persona_id")
        and b.get("artifact_id") not in blocked_targets
        and b.get("persona_id") not in blocked_targets
    ]
    if not active_paper:
        print("  Warning: No non-cooldown-blocked active bindings found. Falling back to all active paper bindings.")
        active_paper = [
            b for b in bindings
            if b.get("status") in ("active", "running")
            and b.get("deployment_mode") == "paper"
            and b.get("artifact_id")
            and b.get("persona_id")
        ]

    if not active_paper:
        print("FAIL: No active paper runtime bindings found.", file=sys.stderr)
        return 1

    selected_binding = active_paper[0]
    binding_id = selected_binding["binding_id"]
    artifact_id = selected_binding["artifact_id"]
    persona_id = selected_binding["persona_id"]
    print(f"  Selected active binding: {binding_id} for persona: {persona_id} (artifact: {artifact_id})")

    # 2. Ensure baseline registered
    print("[2/9] Ensuring expected drawdown baseline is registered for artifact...")
    original_baselines_content = None
    baselines_modified = False
    try:
        with open(DEFAULT_BASELINES_PATH, "r", encoding="utf-8") as f:
            original_baselines_content = f.read()
            baselines_data = json.loads(original_baselines_content)

        baselines = baselines_data.setdefault("baselines", {})
        if artifact_id not in baselines:
            print(f"  Registering baseline expected_drawdown=0.0303 for {artifact_id}...")
            baselines[artifact_id] = {
                "expected_drawdown": 0.0303,
                "policy_source": "verify_e2e_producer_chain.py dynamic registration"
            }
            # Write back
            with open(DEFAULT_BASELINES_PATH, "w", encoding="utf-8") as f:
                json.dump(baselines_data, f, indent=2)
            baselines_modified = True
        else:
            print(f"  Artifact {artifact_id} already has a registered baseline: {baselines[artifact_id]}")
    except Exception as exc:
        print(f"FAIL: Failed to read/write baselines config: {exc}", file=sys.stderr)
        return 1

    try:
        # 3. Ingest a heartbeat telemetry event
        print("[3/9] Ingesting heartbeat event to ensure summary is active and fresh...")
        trace_id = str(uuid.uuid4())
        heartbeat_event = {
            "event_id": str(uuid.uuid4()),
            "event_type": "heartbeat",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "execution_mode": "paper",
            "binding_id": binding_id,
            "runtime_id": selected_binding["runtime_id"],
            "capital_pool_id": selected_binding["capital_pool_id"],
            "artifact_id": artifact_id,
            "artifact_version": selected_binding["artifact_version"],
            "deployment_stage": "paper",
            "plan_id": selected_binding.get("plan_id") or selected_binding.get("deployment_plan_id"),
            "persona_capital_binding_id": selected_binding["persona_capital_binding_id"],
            "trace_id": trace_id,
            "target": {"strategy_id": artifact_id},
            "metrics": {"heartbeat": 1},
            "metadata": {
                "connectivity_status": "connected"
            }
        }
        h_status, h_res = _http_request(f"{telemetry_url}/api/telemetry/ingest", data=heartbeat_event, method="POST", ctx=ctx)
        if h_status != 202:
            print(f"FAIL: Heartbeat ingest failed. Status: {h_status}, Error: {h_res}", file=sys.stderr)
            return 1
        print("  Heartbeat accepted.")

        # 4. Ingest a drawdown_snapshot telemetry event with a threshold-breaching drawdown
        print("[4/9] Ingesting drawdown_snapshot telemetry event with threshold breach...")
        # Baseline = 0.0303. Breach threshold is 1.25. Let's set drawdown to 0.05 (0.05/0.0303 = 1.65 > 1.25)
        breached_event = {
            "event_id": str(uuid.uuid4()),
            "event_type": "drawdown_snapshot",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "execution_mode": "paper",
            "binding_id": binding_id,
            "runtime_id": selected_binding["runtime_id"],
            "capital_pool_id": selected_binding["capital_pool_id"],
            "artifact_id": artifact_id,
            "artifact_version": selected_binding["artifact_version"],
            "deployment_stage": "paper",
            "plan_id": selected_binding.get("plan_id") or selected_binding.get("deployment_plan_id"),
            "persona_capital_binding_id": selected_binding["persona_capital_binding_id"],
            "trace_id": str(uuid.uuid4()),
            "target": {"strategy_id": artifact_id},
            "metrics": {"drawdown_pct": 0.05},
            "metadata": {}
        }
        b_status, b_res = _http_request(f"{telemetry_url}/api/telemetry/ingest", data=breached_event, method="POST", ctx=ctx)
        if b_status != 202:
            print(f"FAIL: Telemetry breach ingest failed. Status: {b_status}, Error: {b_res}", file=sys.stderr)
            return 1
        print("  Telemetry breach accepted.")

        # 5. Run threshold sweep worker tick locally
        print("[5/9] Running local threshold sweep tick...")
        temp_state_path = f"/tmp/verify_e2e_producer_chain_state_{int(time.time())}.json"
        if os.path.exists(temp_state_path):
            os.remove(temp_state_path)

        try:
            tick_result = run_tick(
                telemetry_api_url=telemetry_url,
                incidents_api_url=incidents_url,
                state_path=temp_state_path,
                now=datetime.datetime.now(datetime.timezone.utc),
            )
            print(f"  Tick completed: {json.dumps(tick_result)}")
            if tick_result.get("errors", 0) > 0:
                print(f"FAIL: Tick executed with errors: {tick_result.get('diagnostics')}", file=sys.stderr)
                return 1
            if tick_result.get("incidents_created", 0) == 0 and tick_result.get("incidents_deduped", 0) == 0:
                print("FAIL: Tick did not create or deduplicate any incidents.", file=sys.stderr)
                return 1
        finally:
            if os.path.exists(temp_state_path):
                try:
                    os.remove(temp_state_path)
                except Exception:
                    pass

        # 6. Verify that the incident is successfully created and retrieve its ID
        print("[6/9] Verifying incident exists in BFF /bff/incidents...")
        incident_id = None
        for _ in range(10):
            time.sleep(1.0)
            i_status, incidents_payload = _http_request(base.rstrip("/") + "/bff/incidents?page_size=100", headers=headers, ctx=ctx)
            if i_status == 200:
                inc_list = _items(incidents_payload)
                matching = [
                    inc for inc in inc_list
                    if inc.get("binding_id") == binding_id
                    and inc.get("status") == "open"
                    and "drawdown" in str(inc.get("title", "")).lower()
                ]
                if matching:
                    incident_id = matching[0].get("incident_id") or matching[0].get("id")
                    break
        if not incident_id:
            print("FAIL: Open incident for this breach was not found in BFF incidents.", file=sys.stderr)
            return 1
        print(f"  Incident found: {incident_id}")

        # 7. Run daily sweep to generate proposal
        print("[7/9] Triggering daily sweep on live evolution service...")
        sweep_payload = {
            "incident_ids": [incident_id],
            "sweep_id": "e2e-verifier-sweep"
        }
        s_status, s_res = _http_request(f"{evolution_url}/api/evolution/daily-sweep", method="POST", data=sweep_payload, ctx=ctx)
        if s_status != 200:
            print(f"FAIL: Daily sweep failed. Status: {s_status}, Error: {s_res}", file=sys.stderr)
            return 1
        print(f"  Sweep completed. Created proposals: {s_res.get('created_decisions')}, Blocked: {s_res.get('cooldown_blocked')}")
        if s_res.get("created_decisions", 0) == 0 and s_res.get("existing_decisions", 0) == 0:
            print("FAIL: Sweep did not create or find any decisions.", file=sys.stderr)
            return 1

        decision_id = None
        if s_res.get("items"):
            decision_id = s_res["items"][0].get("decision_id")
        else:
            # Look up proposals
            p_status, p_res = _http_request(f"{evolution_url}/api/evolution/proposals", ctx=ctx)
            if p_status == 200:
                matching_dec = [d for d in p_res if d.get("linked_incident_id") == incident_id]
                if matching_dec:
                    decision_id = matching_dec[0].get("decision_id")
        if not decision_id:
            print("FAIL: Decision/proposal ID not resolved.", file=sys.stderr)
            return 1
        print(f"  Decision ID: {decision_id}")

        # 8. Verify that the evolution journal contains the formal entry
        print("[8/9] Verifying evolution journal entries exist in BFF...")
        journal_found = False
        for _ in range(10):
            time.sleep(1.0)
            j_status, journal_payload = _http_request(base.rstrip("/") + f"/bff/management/evolution-journal?persona={persona_id}", headers=headers, ctx=ctx)
            if j_status == 200:
                journal_items = _items(journal_payload)
                matching_journal = [
                    ji for ji in journal_items
                    if decision_id in (ji.get("id") or "") or decision_id in (ji.get("source_id") or "")
                ]
                if matching_journal:
                    journal_found = True
                    print(f"  Journal entry verified: {matching_journal[0].get('id')}")
                    break
        if not journal_found:
            print(f"FAIL: Evolution journal entry for decision {decision_id} not found.", file=sys.stderr)
            return 1

        # 9. Verify that the Persona Fleet mutation projection links correctly to the proposal
        print("[9/9] Verifying Persona Fleet mutation links to the proposal entry...")
        fleet_linked = False
        for _ in range(10):
            time.sleep(1.0)
            f_status, fleet_payload = _http_request(base.rstrip("/") + "/bff/management/persona-fleet?page_size=100", headers=headers, ctx=ctx)
            if f_status == 200:
                fleet_items = _items(fleet_payload)
                matching_persona = [item for item in fleet_items if item.get("id") == persona_id]
                if matching_persona:
                    persona_item = matching_persona[0]
                    if (
                        persona_item.get("last_mutation_kind") == "formal_mutation"
                        and persona_item.get("mutation_entry_id") == decision_id
                        and persona_item.get("evolution_entry_id") == decision_id
                        and persona_item.get("mutation_confidence") == "formal"
                    ):
                        fleet_linked = True
                        print("  Persona Fleet mutation links successfully verified!")
                        print(f"    mutation_entry_id: {persona_item.get('mutation_entry_id')}")
                        print(f"    evolution_entry_id: {persona_item.get('evolution_entry_id')}")
                        break
        if not fleet_linked:
            print(f"FAIL: Persona Fleet mutation projection did not link correctly to decision {decision_id}.", file=sys.stderr)
            # Print fleet response for debugging
            _s, _res = _http_request(base.rstrip("/") + "/bff/management/persona-fleet?page_size=100", headers=headers, ctx=ctx)
            if _s == 200:
                print(f"    Debug fleet items count: {len(_items(_res))}")
                matching_persona = [item for item in _items(_res) if item.get("id") == persona_id]
                if matching_persona:
                    print(f"    Debug persona item details: {json.dumps(matching_persona[0], indent=2)}")
            return 1

    finally:
        # Restore baselines file to avoid leaving uncommitted modifications in the repo
        if baselines_modified and original_baselines_content is not None:
            print("  Restoring original threshold_sweep_baselines.json...")
            try:
                with open(DEFAULT_BASELINES_PATH, "w", encoding="utf-8") as f:
                    f.write(original_baselines_content)
                print("  Restored successfully.")
            except Exception as exc:
                print(f"ERROR: Failed to restore baselines config: {exc}", file=sys.stderr)

    print("\nOK: Producer-Chain Live Verification passed successfully!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
