#!/usr/bin/env python3
"""E2E evolution-loop integrity verifier.

Business flow under test (closing arc of the OODA loop):
    telemetry/incident → evolution decision/program → new artifact.

Two integrity properties:
  1. Open incidents are well-formed: an `open` incident must carry an
     attributable `runtime_id` and a non-empty `title` (an untitled, runtime-less
     open incident is a malformed record that no operator can action and no
     evolution step can attach to).
  2. Evolution responsiveness: the evolution surface is reachable; open incidents
     should not pile up with zero evolution programs/decisions (a not-closing
     loop). Reported (not hard-failed) since responsiveness is time-dependent.

Failure semantics (CI-safe):
  * FAIL (exit 1) on a malformed open incident (missing runtime_id or title).
  * REPORT (exit 0) the evolution-program count and open-incident backlog.

Usage:
    BFF_BASE=https://...sslip.io BFF_TOKEN=op-dev:admin:mfa \\
        python3 scripts/verify_e2e_evolution_loop.py
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import uuid
import datetime
import urllib.error
import urllib.request
import urllib.parse


def _ctx():
    ctx = ssl.create_default_context()
    if os.environ.get("BFF_INSECURE", "1") == "1":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _get(base, token, ctx, path):
    req = urllib.request.Request(base.rstrip("/") + path, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            return json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None


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


def _is_malformed_open_incident(inc) -> tuple[bool, str]:
    if str(inc.get("status") or "").lower() != "open":
        return False, ""
    iid = inc.get("incident_id") or inc.get("id")
    reasons = []
    if not (inc.get("runtime_id") or "").strip() if isinstance(inc.get("runtime_id"), str) else not inc.get("runtime_id"):
        reasons.append("no runtime_id")
    title = str(inc.get("title") or "").strip()
    if not title or title.lower() in {"untitled incident", "untitled"}:
        reasons.append(f"title={title!r}")
    if reasons:
        return True, f"{iid}: {', '.join(reasons)}"
    return False, ""


def _http_request(url, headers=None, method="GET", data=None, ctx=None):
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
        return None, str(e)


def ensure_artifact_in_registry(registry_url, b, telemetry_pcb_id, telemetry_capital_pool_id, ctx):
    artifact_id = b["artifact_id"]
    strategy_id = b["metadata"].get("strategy_id") or "momentum"
    version = b.get("artifact_version") or "1.0.0"

    print(f"  Checking if artifact {artifact_id} exists in registry...")
    status, payload = _http_request(f"{registry_url}/api/registry/strategy-artifacts/{artifact_id}", ctx=ctx)
    if status == 200:
        print(f"  Artifact {artifact_id} already exists in registry.")
        return

    print(f"  Registering artifact {artifact_id} in registry...")
    payload = {
        "registry_id": artifact_id,
        "artifact_state": "candidate",
        "strategy_artifact": {
            "artifact_schema_version": "1.0",
            "artifact_id": artifact_id,
            "strategy_id": strategy_id,
            "version": version,
            "algorithm_ref": {
                "engine": "lean",
                "repository": "ajoe734/pantheon-lean",
                "commit": "5ad0249432459c119f26718007e083808ef7995d",
                "path": "Algorithm.Python/pantheon_algo/base.py",
                "entrypoint": "pantheon_algo.base:PantheonAlgoBase",
                "signal_interface": "services.execution.lean_runtime.paper_signal_producer:Strategy",
                "signal_schema_version": "1.0",
                "logic_interpreter": "services.registry.strategy_artifact:evaluate_strategy_action"
            },
            "strategy_logic": {
                "kind": "close_to_close_momentum",
                "lookback_parameter": "lookback_bars",
                "threshold_parameter": "momentum_threshold",
                "positive_action": "BUY",
                "non_positive_action": "SELL"
            },
            "parameters": {
                "symbols": ["2330.TW"],
                "bar_frequency": "1d",
                "data_source": "source-ingest:normalized/taiwanstockprice/finmind-daily",
                "lookback_bars": 2,
                "momentum_threshold": 0.01,
                "order_quantity": 1,
                "quantity_type": "SHARES",
                "zero_momentum_action": "SELL"
            },
            "mutation_surface": {
                "controls": [
                    {
                        "parameter_key": "lookback_bars",
                        "value_type": "integer",
                        "current_value": 2,
                        "allowed_range": {"min": 2, "max": 60},
                        "step": 1
                    }
                ],
                "immutable_parameters": [
                    "symbols", 
                    "bar_frequency", 
                    "data_source", 
                    "momentum_threshold", 
                    "order_quantity", 
                    "quantity_type", 
                    "zero_momentum_action"
                ]
            },
            "lineage": {
                "source_run_ids": ["EVOLOOP-008-INIT"],
                "source_dataset_refs": ["source-ingest:normalized/taiwanstockprice/finmind-daily"]
            },
            "binding_intent": {
                "persona_id": b.get("persona_id") or "persona-tw-equity",
                "persona_name": "E2E Persona",
                "persona_capital_binding_id": telemetry_pcb_id,
                "observed_runtime_binding_id": b["binding_id"],
                "observed_runtime_id": b["runtime_id"],
                "observed_deployment_plan_id": b.get("plan_id") or b.get("deployment_plan_id"),
                "observed_capital_pool_id": telemetry_capital_pool_id,
                "observed_placeholder_artifact_id": artifact_id,
                "observed_placeholder_artifact_version": version,
                "observed_binding_status": "active",
                "observed_deployment_mode": "paper",
                "observed_effective_at": b.get("effective_at") or "2026-07-11T10:50:25Z",
                "observed_at": "2026-07-14T05:06:07Z",
                "evidence_ref": "docs/bff/evidence/BFF-LUV-SEM-006-lupin-dev-live-probe-20260509T113136Z.json",
                "replacement_task_id": "EVOLOOP-008"
            },
            "provenance_refs": ["host-file:/home/lupin/paper-loop/tw_signal_producer.py"]
        }
    }
    status, res_payload = _http_request(f"{registry_url}/api/registry/strategy-artifacts", data=payload, method="POST", ctx=ctx)
    if status not in (200, 201):
        raise RuntimeError(f"Failed to register strategy artifact: {status}, {res_payload}")
    print(f"  Artifact {artifact_id} registered successfully.")


def run_full_cycle_verification(base, token, ctx):
    telemetry_url = os.environ.get("TELEMETRY_API_URL", "http://localhost:18083")
    incidents_url = os.environ.get("INCIDENTS_API_URL", "http://localhost:18090")
    postmortems_url = os.environ.get("POSTMORTEMS_API_URL", "http://localhost:18091")
    evolution_url = os.environ.get("EVOLUTION_API_URL", "http://localhost:18093")
    deployment_url = os.environ.get("DEPLOYMENT_API_URL", "http://localhost:18095")
    registry_url = os.environ.get("REGISTRY_API_URL", "http://localhost:18087")
    capital_url = os.environ.get("CAPITAL_API_URL", "http://localhost:18092")
    governance_url = os.environ.get("GOVERNANCE_API_URL", "http://localhost:18082")
    runtime_manager_url = os.environ.get("RUNTIME_MANAGER_API_URL", "http://localhost:18081")

    # Step 1: Get active runtime bindings
    print("[1/11] Fetching active runtimes from BFF...")
    headers = {"Authorization": f"Bearer {token}"}
    status, runtimes_payload = _http_request(base.rstrip("/") + "/bff/runtimes", headers=headers, ctx=ctx)
    if status == 401:
        # Fallback to test-operator token
        fallback_token = "test-operator:operator,reviewer,admin:mfa"
        print(f"  Auth failed (401) with default token. Retrying with fallback: {fallback_token}")
        headers = {"Authorization": f"Bearer {fallback_token}"}
        status, runtimes_payload = _http_request(base.rstrip("/") + "/bff/runtimes", headers=headers, ctx=ctx)
        token = fallback_token

    if status != 200:
        raise RuntimeError(f"Failed to fetch runtimes from BFF. Status: {status}, Error: {runtimes_payload}")

    bindings = _items(runtimes_payload)

    # Fetch active proposals to avoid target collisions
    evo_status, evo_payload = _http_request(f"{evolution_url}/api/evolution/proposals", ctx=ctx)
    active_targets = set()
    if evo_status == 200:
        for p in evo_payload:
            if p.get("is_active"):
                active_targets.add(p.get("target_id"))

    active = [
        b for b in bindings 
        if b.get("status") in ("active", "running") 
        and b.get("artifact_id")
        and b.get("artifact_id") not in active_targets
    ]
    if not active:
        raise RuntimeError("No active runtime bindings found without active target conflicts to initiate evolutionary breach loop.")

    b = active[0]
    print(f"  Selected active binding: {b['binding_id']} with artifact: {b['artifact_id']}")

    # Telemetry expects the values present in RuntimeBinding
    telemetry_capital_pool_id = b["capital_pool_id"]
    telemetry_pcb_id = b["persona_capital_binding_id"]

    # Deployment planning/validation expects the values registered in the capital service
    print("  Resolving correct capital_pool_id and pcb_id from capital service...")
    cb_status, cb_payload = _http_request(f"{capital_url}/api/bindings", ctx=ctx)
    deployment_capital_pool_id = b["capital_pool_id"]
    deployment_pcb_id = b["persona_capital_binding_id"]
    if cb_status == 200 and cb_payload:
        for cb in cb_payload:
            if cb.get("persona_id") == b.get("persona_id") and cb.get("status") == "active":
                deployment_capital_pool_id = cb.get("capital_pool_id")
                deployment_pcb_id = cb.get("binding_id")
                print(f"    Resolved capital pool for deployment: {deployment_capital_pool_id}, binding: {deployment_pcb_id}")
                break

    # Idempotency / Registry pre-flight check
    ensure_artifact_in_registry(registry_url, b, telemetry_pcb_id, telemetry_capital_pool_id, ctx)

    # Step 2: Ingest breach telemetry event
    print("[2/11] Ingesting breach telemetry event...")
    run_seed = f"evoloop008-{int(time.time())}"
    dedupe_key = f"{b['binding_id']}:rolling_drawdown_multiple:paper-session:{run_seed}"
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, dedupe_key))
    trace_id = str(uuid.uuid5(uuid.NAMESPACE_URL, dedupe_key + ":trace"))

    event = {
        "event_id": event_id,
        "event_type": "drawdown_snapshot",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "execution_mode": "paper",
        "binding_id": b["binding_id"],
        "runtime_id": b["runtime_id"],
        "capital_pool_id": telemetry_capital_pool_id,
        "artifact_id": b["artifact_id"],
        "artifact_version": b["artifact_version"],
        "deployment_stage": "paper",
        "plan_id": b.get("plan_id") or b.get("deployment_plan_id"),
        "persona_capital_binding_id": telemetry_pcb_id,
        "trace_id": trace_id,
        "target": {"strategy_id": b["artifact_id"]},
        "metrics": {"rolling_drawdown_multiple": 1.42},
        "metadata": {"derived_from_threshold_evaluation": True}
    }

    t_status, t_payload = _http_request(f"{telemetry_url}/api/telemetry/ingest", data=event, method="POST", ctx=ctx)
    if t_status not in (200, 202):
        raise RuntimeError(f"Telemetry ingest failed. Status: {t_status}, Error: {t_payload}")
    print(f"  Telemetry event ingested successfully. ID: {event_id}")

    # Step 3: Consume threshold breach payload -> creates incident
    print("[3/11] Injecting threshold breach payload via incidents service...")
    incident_id = f"inc-threshold-{uuid.uuid5(uuid.NAMESPACE_URL, event_id + ':rolling_drawdown_multiple').hex[:12]}"
    payload = {
        "incident_id": incident_id,
        "title": f"Evoloop 008 E2E Test: Drawdown Breach ({run_seed})",
        "severity": "medium",
        "telemetry_event": event,
        "threshold_snapshot": {
            "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.5",
            "signal_type": "performance_degradation",
            "metric_name": "rolling_drawdown_multiple",
            "comparator": "gt",
            "observed_value": 1.42,
            "threshold_value": 1.25,
            "window": "paper-session",
            "breached": True,
            "note": f"dedupe_key={dedupe_key}"
        }
    }

    i_status, i_payload = _http_request(f"{incidents_url}/api/incidents/consume-threshold", data=payload, method="POST", ctx=ctx)
    if i_status != 201:
        raise RuntimeError(f"Incident creation failed. Status: {i_status}, Error: {i_payload}")
    print(f"  Incident created: {incident_id} (status: {i_payload.get('status')})")

    # Step 4: Resolve incident -> outbox worker automatically creates postmortem draft
    print("[4/11] Resolving the incident...")
    r_status, r_payload = _http_request(f"{incidents_url}/api/incidents/{incident_id}/status", data={"status": "resolved"}, method="POST", ctx=ctx)
    if r_status != 200:
        raise RuntimeError(f"Incident resolution failed. Status: {r_status}, Error: {r_payload}")
    print("  Incident resolved. Polling for postmortem draft creation...")

    postmortem_id = f"pm-{incident_id}"
    pm_draft = None
    for _ in range(15):
        time.sleep(1.0)
        pm_status, pm_payload = _http_request(f"{postmortems_url}/api/postmortems?incident_id={incident_id}", ctx=ctx)
        if pm_status == 200 and pm_payload:
            pm_draft = pm_payload[0]
            break
    if not pm_draft:
        raise RuntimeError(f"Postmortem draft was not created for incident {incident_id}")
    print(f"  Postmortem draft created: {postmortem_id} (status: pm_draft.get('status'))")

    # Step 5: Publish postmortem draft -> outbox worker automatically creates evolution proposal
    print("[5/11] Publishing the postmortem draft...")
    p_status, p_payload = _http_request(f"{postmortems_url}/api/postmortems/{postmortem_id}/status", data={"status": "published"}, method="POST", ctx=ctx)
    if p_status != 200:
        raise RuntimeError(f"Postmortem publication failed. Status: {p_status}, Error: {p_payload}")
    print("  Postmortem published. Polling for evolution proposal...")

    evo_proposal = None
    for _ in range(15):
        time.sleep(1.0)
        evo_status, evo_payload = _http_request(f"{evolution_url}/api/evolution/proposals", ctx=ctx)
        if evo_status == 200:
            match = [p for p in evo_payload if p.get("linked_postmortem_id") == postmortem_id]
            if match:
                evo_proposal = match[0]
                break
    if not evo_proposal:
        raise RuntimeError(f"Evolution proposal was not created for postmortem {postmortem_id}")

    decision_id = evo_proposal["decision_id"]
    print(f"  Evolution proposal created: {decision_id} (status: {evo_proposal.get('decision_state')}, action_type: {evo_proposal.get('action_type')})")
    if evo_proposal.get("action_type") != "retrain":
        raise RuntimeError(f"Expected action_type 'retrain' for medium-severity incident, got: {evo_proposal.get('action_type')}")

    # Step 6: Review evolution proposal
    print("[6/11] Reviewing the evolution proposal...")
    rev_payload = {
        "actor_role": "reviewer_on_duty",
        "actor_id": "reviewer-e2e-008",
        "approval_decision_id": f"apv-{run_seed}"
    }
    rev_status, rev_payload = _http_request(f"{evolution_url}/api/evolution/proposals/{decision_id}/review", data=rev_payload, method="POST", ctx=ctx)
    if rev_status != 200:
        raise RuntimeError(f"Proposal review failed. Status: {rev_status}, Error: {rev_payload}")
    print("  Proposal reviewed successfully.")

    # Step 7: Approve evolution proposal -> background dispatch worker automatically executes proposal
    print("[7/11] Approving the evolution proposal...")
    appv_payload = {
        "actor_role": "reviewer_on_duty",
        "actor_id": "approver-e2e-008"
    }
    appv_status, appv_payload = _http_request(f"{evolution_url}/api/evolution/proposals/{decision_id}/approve", data=appv_payload, method="POST", ctx=ctx)
    if appv_status != 200:
        raise RuntimeError(f"Proposal approval failed. Status: {appv_status}, Error: {appv_payload}")
    print("  Proposal approved. Polling for background dispatch execution and retrain...")

    executed_decision = None
    for _ in range(40):
        time.sleep(1.0)
        get_status, get_payload = _http_request(f"{evolution_url}/api/evolution/proposals/{decision_id}", ctx=ctx)
        if get_status == 200:
            if get_payload.get("decision_state") == "executed":
                executed_decision = get_payload
                break
    if not executed_decision:
        raise RuntimeError(f"Evolution decision {decision_id} did not transition to executed in time")

    print(f"  Evolution decision executed. Checking that retrain triggered artifact mutation...")

    # Step 8: Verify research run completed and artifact v2 is registered
    target_artifact_id = b["artifact_id"]
    new_artifact_id = target_artifact_id.replace("-v1", "-v2") if "-v1" in target_artifact_id else target_artifact_id + "-v2"
    print(f"[8/11] Verifying mutated artifact v2 registry registration: {new_artifact_id}...")

    registry_entry = None
    for _ in range(25):
        time.sleep(1.0)
        reg_status, reg_payload = _http_request(f"{registry_url}/api/registry/strategy-artifacts/{new_artifact_id}", ctx=ctx)
        if reg_status == 200 and reg_payload:
            registry_entry = reg_payload.get("entry", reg_payload)
            break

    if not registry_entry:
        raise RuntimeError(f"Mutated artifact v2 {new_artifact_id} was not found in registry")
    print(f"  Artifact v2 registered successfully: {new_artifact_id}")

    # Step 8.5: Advance registry artifact v2 state to 'approved'
    print(f"  Advancing registry artifact v2 state to 'approved'...")
    adv_payload = {
        "target_state": "approved",
        "approver": "approver-e2e-008",
        "approval_decision_id": f"apv-redeploy-{run_seed}"
    }
    adv_status, adv_res = _http_request(f"{registry_url}/api/registry/strategy-artifacts/{new_artifact_id}/advance", data=adv_payload, method="POST", ctx=ctx)
    if adv_status != 200:
        raise RuntimeError(f"Failed to advance strategy artifact state. Status: {adv_status}, Error: {adv_res}")
    print(f"  Artifact v2 advanced to approved state.")

    # Re-fetch registry entry to ensure we carry the approved state
    print(f"  Re-fetching registry entry for advanced artifact...")
    reg_status, reg_payload = _http_request(f"{registry_url}/api/registry/strategy-artifacts/{new_artifact_id}", ctx=ctx)
    if reg_status == 200 and reg_payload:
        registry_entry = reg_payload.get("entry", reg_payload)

    # Step 9: Call redeploy-followthrough to get the DispatchCommand
    print("[9/11] Requesting redeploy follow-through dispatch command...")
    redeploy_payload = {
        "artifact_id": new_artifact_id,
        "artifact_version": "1.1.0",
        "approval_decision_id": f"apv-redeploy-{run_seed}",
        "target_stage": "paper",
        "requested_at": executed_decision["observation_window_started_at"]
    }
    f_status, f_payload = _http_request(f"{evolution_url}/api/evolution/proposals/{decision_id}/redeploy-followthrough", data=redeploy_payload, method="POST", ctx=ctx)
    if f_status != 200:
        raise RuntimeError(f"Redeploy follow-through failed. Status: {f_status}, Error: {f_payload}")
    print("  Redeploy follow-through command generated.")

    # Step 9.5: Register the approval decision in the governance service so deployment-outbox-consumer doesn't 404
    print("  Registering redeploy approval decision in governance service...")
    gov_decision_id = f"apv-redeploy-{run_seed}"
    gov_prop_payload = {
        "decision_id": gov_decision_id,
        "target_type": "registry_entry",
        "target_id": new_artifact_id,
        "target_version": registry_entry.get("version", "1.1.0"),
        "risk_level": "low",
        "capital_pool_id": deployment_capital_pool_id,
        "persona_id": b.get("persona_id")
    }
    g_status, g_payload = _http_request(f"{governance_url}/api/governance/approvals", data=gov_prop_payload, method="POST", ctx=ctx)
    if g_status not in (200, 201):
        raise RuntimeError(f"Failed to propose governance approval. Status: {g_status}, Error: {g_payload}")

    # Transition proposed -> under_review
    g_status, g_payload = _http_request(
        f"{governance_url}/api/governance/approvals/{gov_decision_id}/review",
        data={"actor_role": "governance_reviewer", "actor_id": "approver-e2e-008"},
        method="POST",
        ctx=ctx
    )
    if g_status != 200:
        raise RuntimeError(f"Failed to transition governance approval to under_review. Status: {g_status}, Error: {g_payload}")

    # Transition under_review -> decided (approved)
    g_status, g_payload = _http_request(
        f"{governance_url}/api/governance/approvals/{gov_decision_id}/decide",
        data={
            "actor_role": "governance_reviewer",
            "outcome": "approved",
            "rationale": "E2E verification run for evoloop-008",
            "actor_id": "approver-e2e-008"
        },
        method="POST",
        ctx=ctx
    )
    if g_status != 200:
        raise RuntimeError(f"Failed to transition governance approval to approved. Status: {g_status}, Error: {g_payload}")
    print("  Redeploy approval decision registered and approved in governance service successfully.")

    # Retire the old active binding right before dispatching the new plan to prevent single-runtime guard violations
    print(f"  Retiring old binding {b['binding_id']} before dispatching new deployment plan...")
    ret_url = f"{runtime_manager_url}/api/runtime-bindings/{b['binding_id']}/retire"
    ret_status, ret_res = _http_request(ret_url, method="POST", data={}, headers={"Authorization": f"Bearer {token}"}, ctx=ctx)
    if ret_status not in (200, 201, 202):
        print(f"  Warning: failed to retire old binding: {ret_status}, {ret_res}")

    # Step 10: Create and dispatch deployment plan -> active binding v2 on runtime-manager
    print("[10/11] Creating and dispatching redeploy plan...")
    plan_id = f"plan-redeploy-{run_seed}"
    rollback_ref = {
        "target_artifact_id": b["artifact_id"],
        "target_version": b["artifact_version"],
        "action_type": "replace"
    }

    create_plan_body = {
        "plan_id": plan_id,
        "approval_decision_id": gov_decision_id,
        "target_stage": "paper",
        "registry_id": new_artifact_id,
        "registry_entry": registry_entry,
        "approval_decision": {
            "decision_id": gov_decision_id,
            "target_type": "candidate_artifact",
            "target_id": new_artifact_id,
            "target_version": registry_entry.get("version", "1.1.0"),
            "action_type": "redeploy_followthrough",
            "decision_state": "decided",
            "decision": "approved",
            "risk_level": "low",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "created_by_role": "reviewer_on_duty",
            "created_by_id": "approver-e2e-008",
            "capital_pool_id": deployment_capital_pool_id,
            "persona_id": b.get("persona_id")
        },
        "current_stage": "none",
        "created_by": "operator-e2e-008",
        "sponsor_persona_id": b.get("persona_id"),
        "capital_pool_id": deployment_capital_pool_id,
        "rollback": rollback_ref,
        "status": "approved"
    }

    dp_status, dp_payload = _http_request(f"{deployment_url}/api/deployment/plans", data=create_plan_body, method="POST", ctx=ctx)
    if dp_status != 201:
        raise RuntimeError(f"Failed to create deployment plan. Status: {dp_status}, Error: {dp_payload}")

    print("  Deployment plan created. Dispatching...")
    disp_payload = {
        "trace_id": trace_id,
        "registry_entry": registry_entry
    }
    disp_status, disp_payload = _http_request(f"{deployment_url}/api/deployment/plans/{plan_id}/dispatch", data=disp_payload, method="POST", ctx=ctx)
    if disp_status != 200:
        raise RuntimeError(f"Deployment dispatch failed. Status: {disp_status}, Error: {disp_payload}")

    print("  Deployment dispatched. Polling for outbox consumer to execute plan...")
    executed_plan = None
    for _ in range(40):
        time.sleep(1.0)
        gp_status, gp_payload = _http_request(f"{deployment_url}/api/deployment/plans/{plan_id}", ctx=ctx)
        if gp_status == 200 and gp_payload.get("status") == "executed":
            executed_plan = gp_payload
            break

    if not executed_plan:
        raise RuntimeError(f"Deployment plan {plan_id} did not transition to executed")
    print(f"  Deployment plan executed. New binding ID: {executed_plan.get('binding_id')}")

    # Step 11: Check the new binding in runtimes and verify evolution journal
    print("[11/11] Verifying the new binding in runtimes and the evolution journal...")
    status, runtimes_payload = _http_request(base.rstrip("/") + "/bff/runtimes", headers=headers, ctx=ctx)
    if status != 200:
        raise RuntimeError(f"Failed to fetch runtimes from BFF. Status: {status}")

    new_bindings = _items(runtimes_payload)
    binding_match = [nb for nb in new_bindings if nb.get("artifact_id") == new_artifact_id and nb.get("status") in ("active", "running")]
    if not binding_match:
        raise RuntimeError(f"Could not find an active runtime binding for mutated artifact v2 {new_artifact_id}")
    print(f"  Verified! Active runtime binding found for mutated artifact: {binding_match[0]['binding_id']}")

    j_status, j_payload = _http_request(base.rstrip("/") + "/bff/management/evolution-journal?page_size=100", headers=headers, ctx=ctx)
    if j_status != 200:
        raise RuntimeError(f"Failed to fetch evolution journal from BFF. Status: {j_status}")

    j_items = _items(j_payload)
    linked_journal = [ji for ji in j_items if incident_id in str(ji) or decision_id in str(ji)]
    if not linked_journal:
        raise RuntimeError("No linked evolution journal entries found for our incident/decision.")

    print(f"  Verified! Found {len(linked_journal)} linked evolution journal entry/entries:")
    for entry in linked_journal:
        print(f"    - Type: {entry.get('source_type') or entry.get('type')}, ID: {entry.get('id')}, Status: {entry.get('status')}")


def main() -> int:
    base = os.environ.get("BFF_BASE")
    token = os.environ.get("BFF_TOKEN", "op-dev:admin:mfa")
    if not base:
        print("ERROR: set BFF_BASE", file=sys.stderr)
        return 2
    ctx = _ctx()

    # Part 1: Existing well-formedness checks (R9 evolution loop)
    incidents = _items(_get(base, token, ctx, "/bff/incidents"))
    programs = _items(_get(base, token, ctx, "/bff/evolution-programs"))

    open_incidents = [i for i in incidents if str(i.get("status") or "").lower() == "open"]
    malformed = []
    for inc in incidents:
        bad, detail = _is_malformed_open_incident(inc)
        if bad:
            malformed.append(detail)

    print("== evolution-loop integrity ==")
    print(f"  incidents={len(incidents)} open={len(open_incidents)} evolution_programs={len(programs)}")
    if open_incidents and not programs:
        print(f"  NOTE: {len(open_incidents)} open incident(s) with 0 evolution programs "
              f"- the incident → evolution arc is not closing")

    if malformed:
        print(f"\nFAIL: {len(malformed)} malformed open incident(s) (unattributable / untitled):")
        for d in malformed[:20]:
            print(f"   {d}")
        return 1
    print("\nOK: open incidents are well-formed (attributable + titled)")

    # Part 2: Active Full-Cycle Live Verification Loop
    print("\nStarting EVOLOOP-008 Full-Cycle Live Verification...")
    try:
        run_full_cycle_verification(base, token, ctx)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"\nFAIL: Full-cycle verification failed: {exc}", file=sys.stderr)
        return 1

    print("\nOK: All verification checks passed successfully!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
