"""Comprehensive unit and contract tests for DevRuntimePaperLifecycleProbe.

Validates:
1. P1: Stripped telemetry event fails closed (negative adversarial test).
2. P1: Missing paper safety flags (is_real_capital, is_real_order) fail closed.
3. P1: Loop 8 read evidence from terminal saga and applied inbox receipt (no synthetic receipts).
4. P1: Authenticated hosted contract with Bearer and X-Tenant-Id headers; 401 for missing auth; 404 for unknown routes.
5. P1: URL path join deduplicates duplicate /api/... segments without mangling query params.
6. P1: Durable reload compares full identity, checksum, safety flags, DEP-003 projection, and separates liveness heartbeat from trade episode receipt.
7. P1: Preflight fail-closed checks on 40-hex SHAs, buildMode, config_posture, environment, pair linkage, and authoritative parent discovery.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch

import pytest

from scripts.probe_dev_runtime_paper_lifecycle import (
    DEFAULT_BFF_BASE_URL,
    DEFAULT_FE_BASE_URL,
    HISTORICAL_FORBIDDEN_IDS,
    SCHEMA_VERSION,
    TASK_ID,
    CanonicalOwnerAdapter,
    CorrelationMismatchError,
    DevRuntimePaperLifecycleProbe,
    HttpResponse,
    MissingConsumerReceiptError,
    PreflightBlockedError,
    ProbeConfig,
    ProbeError,
    ProbeTimeoutError,
    RealCapitalOrOrderWriteForbiddenError,
    ReloadMismatchError,
    RetiredDeployTargetError,
    StaleIdentityError,
    is_executable_binding,
    join_url,
    redact_secrets,
    seal_evidence,
    validate_host_not_retired,
)

FE_SHA_VALID = "a" * 40
BFF_SHA_VALID = "b" * 40
PAIR_ID_VALID = "pair-dev-20260905-001"


class HonestCanonicalOwnerDouble:
    """Rigorous mock implementing the canonical owner contracts with strict auth and exact routes."""

    def __init__(
        self,
        *,
        fe_commit: str = FE_SHA_VALID,
        bff_commit: str = BFF_SHA_VALID,
        pair_id: str = PAIR_ID_VALID,
        bff_pair_id: str | None = None,
        fe_real_writes: str = "false",
        fe_build_mode_valid: bool = True,
        fe_manifest_bff_sha: str | None = None,
        bff_ready: bool = True,
        bff_auth_mode: str = "strict",
        bff_auth_stub: bool = False,
        bff_environment: str = "dev",
        parent_artifact_discovered: bool = True,
        deployment_terminal_status: str = "executed",
        deployment_saga_status: str = "completed",
        inbox_receipt_applied: bool = True,
        dep003_valid: bool = True,
        fleet_desired_accepted: bool = True,
        fleet_worker_running: bool = True,
        telemetry_fill_produced: bool = True,
        telemetry_heartbeat_present: bool = True,
        telemetry_real_capital: bool = False,
        telemetry_real_order: bool = False,
        telemetry_broker_status: str = "filled",
        telemetry_correlation_override: str | None = None,
        telemetry_stripped_mode: bool = False,
        trade_episode_produced: bool = True,
        reload_binding_differs: bool = False,
        reload_event_differs: bool = False,
        reload_summary_differs: bool = False,
        reload_dep003_differs: bool = False,
        reload_checksum_differs: bool = False,
        inbox_override_items: list[dict[str, Any]] | None = None,
        trade_episodes_override: list[dict[str, Any]] | None = None,
        fleet_workers_override: list[dict[str, Any]] | None = None,
        reload_inbox_differs: bool = False,
        reload_trade_episode_differs: bool = False,
        simulate_timeout_on: set[str] | None = None,
        simulate_unavailable_on: set[str] | None = None,
    ) -> None:
        self.fe_commit = fe_commit
        self.bff_commit = bff_commit
        self.pair_id = pair_id
        self.bff_pair_id = bff_pair_id
        self.fe_real_writes = fe_real_writes
        self.fe_build_mode_valid = fe_build_mode_valid
        self.fe_manifest_bff_sha = fe_manifest_bff_sha if fe_manifest_bff_sha is not None else bff_commit
        self.bff_ready = bff_ready
        self.bff_auth_mode = bff_auth_mode
        self.bff_auth_stub = bff_auth_stub
        self.bff_environment = bff_environment
        self.parent_artifact_discovered = parent_artifact_discovered
        self.deployment_terminal_status = deployment_terminal_status
        self.deployment_saga_status = deployment_saga_status
        self.inbox_receipt_applied = inbox_receipt_applied
        self.dep003_valid = dep003_valid
        self.fleet_desired_accepted = fleet_desired_accepted
        self.fleet_worker_running = fleet_worker_running
        self.telemetry_fill_produced = telemetry_fill_produced
        self.telemetry_heartbeat_present = telemetry_heartbeat_present
        self.telemetry_real_capital = telemetry_real_capital
        self.telemetry_real_order = telemetry_real_order
        self.telemetry_broker_status = telemetry_broker_status
        self.telemetry_correlation_override = telemetry_correlation_override
        self.telemetry_stripped_mode = telemetry_stripped_mode
        self.trade_episode_produced = trade_episode_produced
        self.reload_binding_differs = reload_binding_differs
        self.reload_event_differs = reload_event_differs
        self.reload_summary_differs = reload_summary_differs
        self.reload_dep003_differs = reload_dep003_differs
        self.reload_checksum_differs = reload_checksum_differs
        self.inbox_override_items = inbox_override_items
        self.trade_episodes_override = trade_episodes_override
        self.fleet_workers_override = fleet_workers_override
        self.reload_inbox_differs = reload_inbox_differs
        self.reload_trade_episode_differs = reload_trade_episode_differs
        self.is_reloading = False
        self.event_read_count = 0
        self.summary_read_count = 0
        self.dep003_read_count = 0
        self.inbox_read_count = 0
        self.trade_episodes_read_count = 0
        self.simulate_timeout_on = simulate_timeout_on or set()
        self.simulate_unavailable_on = simulate_unavailable_on or set()

        self.recorded_requests: list[dict[str, Any]] = []
        self.created_plans: dict[str, Any] = {}
        self.created_bindings: dict[str, Any] = {}
        self.created_events: dict[str, Any] = {}

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        self.recorded_requests.append({
            "method": method,
            "url": url,
            "headers": dict(headers),
            "payload": payload,
        })

        parsed = urllib.parse.urlsplit(url)
        path = parsed.path

        for unavailable_path in self.simulate_unavailable_on:
            if unavailable_path in url:
                return HttpResponse(502, {"error": "Simulated upstream 502"}, {}, url, method)

        # Static FE manifest does not require API Authorization; sending it is forbidden (Finding 4)
        if path == "/deployment.json":
            if "Authorization" in headers:
                return HttpResponse(
                    400,
                    {"error": "Security violation: Authorization header sent to static FE manifest host"},
                    {},
                    url,
                    method,
                )
            build_mode = {
                "VITE_BFF_MODE": "live",
                "VITE_BFF_FALLBACK": "strict",
                "VITE_BFF_REAL_WRITES": self.fe_real_writes,
            } if self.fe_build_mode_valid else {"VITE_BFF_MODE": "invalid"}
            return HttpResponse(
                200,
                {
                    "commit": self.fe_commit,
                    "frontendSha": self.fe_commit,
                    "bffCommit": self.fe_manifest_bff_sha,
                    "backendSha": self.fe_manifest_bff_sha,
                    "releaseName": "20260905T000000Z-release",
                    "pairId": self.pair_id,
                    "profile": "read-only",
                    "buildMode": build_mode,
                },
                {},
                url,
                method,
            )

        # Strict authentication contract check for all other owner routes (Finding 3)
        auth = headers.get("Authorization")
        tenant = headers.get("X-Tenant-Id")
        if not auth or not auth.strip():
            return HttpResponse(401, {"error": "Unauthorized: missing Authorization header"}, {}, url, method)
        if not tenant or not tenant.strip():
            return HttpResponse(400, {"error": "Bad Request: missing X-Tenant-Id header"}, {}, url, method)

        # BFF version (public BFF has no pair_id unless explicitly configured, Finding 4)
        if path == "/bff/version":
            bff_payload = {
                "source_commit_sha": self.bff_commit,
                "commit": self.bff_commit,
                "environment": self.bff_environment,
                "config_posture": {"auth_stub": self.bff_auth_stub, "auth_mode": self.bff_auth_mode},
            }
            if self.bff_pair_id is not None:
                bff_payload["pair_id"] = self.bff_pair_id
            return HttpResponse(
                200,
                bff_payload,
                {},
                url,
                method,
            )

        # Readyz health
        if path == "/readyz":
            return HttpResponse(
                200,
                {
                    "ready": self.bff_ready,
                    "dependencies": {
                        "database": "ok" if self.bff_ready else "unhealthy",
                    },
                },
                {},
                url,
                method,
            )

        # Loop health
        if path == "/bff/v5/loop-health":
            return HttpResponse(200, {"status": "ok"}, {}, url, method)

        # Registry strategy artifacts query (authoritative parent discovery)
        if path == "/api/registry/strategies/tw_session_momentum/strategy-artifacts":
            if not self.parent_artifact_discovered:
                return HttpResponse(200, [], {}, url, method)
            return HttpResponse(
                200,
                [
                    {
                        "entry": {
                            "registry_id": "artifact-tw-session-momentum-v1",
                            "strategy_id": "tw_session_momentum",
                            "version": "1.0.0",
                            "artifact_state": "approved",
                            "checksum": "sha256:parent12345",
                            "metadata": {
                                "strategy_artifact": {
                                    "artifact_id": "artifact-tw-session-momentum-v1",
                                    "version": "1.0.0",
                                }
                            },
                        }
                    }
                ],
                {},
                url,
                method,
            )

        if path == "/api/registry/strategy-artifacts/artifact-tw-session-momentum-v1":
            if not self.parent_artifact_discovered:
                return HttpResponse(404, {"error": "not found"}, {}, url, method)
            return HttpResponse(
                200,
                {
                    "entry": {
                        "registry_id": "artifact-tw-session-momentum-v1",
                        "strategy_id": "tw_session_momentum",
                        "version": "1.0.0",
                        "artifact_state": "approved",
                        "checksum": "sha256:parent12345",
                        "metadata": {
                            "strategy_artifact": {
                                "artifact_id": "artifact-tw-session-momentum-v1",
                                "version": "1.0.0",
                            }
                        },
                    }
                },
                {},
                url,
                method,
            )

        # Registry mutate
        if path.endswith("/mutate"):
            return HttpResponse(
                201,
                {
                    "entry": {
                        "registry_id": (payload or {}).get("new_artifact_id"),
                        "version": (payload or {}).get("new_version"),
                        "artifact_state": "draft",
                        "metadata": {
                            "strategy_artifact": {
                                "artifact_id": (payload or {}).get("new_artifact_id"),
                                "version": (payload or {}).get("new_version"),
                                "strategy_id": "tw_session_momentum",
                                "parameters": (payload or {}).get("parameter_updates"),
                                "source_run_ids": (payload or {}).get("source_run_ids"),
                            }
                        },
                    }
                },
                {},
                url,
                method,
            )

        # Capital pools
        if path == "/api/capital-pools":
            return HttpResponse(201, {"pool_id": (payload or {}).get("pool_id"), "status": "active"}, {}, url, method)

        # Capital bindings
        if path == "/api/bindings":
            return HttpResponse(201, {"binding_id": (payload or {}).get("binding_id"), "status": "created"}, {}, url, method)

        if path.startswith("/api/bindings/") and path.endswith("/activate"):
            return HttpResponse(200, {"status": "active"}, {}, url, method)

        # Governance approvals
        if path == "/api/governance/approvals":
            return HttpResponse(201, {"decision_id": (payload or {}).get("decision_id"), "status": "submitted"}, {}, url, method)

        if path.startswith("/api/governance/approvals/") and path.endswith("/decide"):
            decision_id = path.split("/")[-2]
            return HttpResponse(200, {"decision_id": decision_id, "status": "approved", "outcome": "approved"}, {}, url, method)

        # Registry advance
        if path.startswith("/api/registry/strategy-artifacts/") and path.endswith("/advance"):
            artifact_id = path.split("/")[-2]
            return HttpResponse(200, {"entry": {"registry_id": artifact_id, "version": "2.1.0", "artifact_state": "approved"}}, {}, url, method)

        # Deployment plan validate
        if path == "/api/deployment/plans/validate":
            return HttpResponse(200, {"valid": True}, {}, url, method)

        # Deployment plan create
        if path == "/api/deployment/plans":
            plan_id = (payload or {}).get("plan_id") or "plan-unknown"
            self.created_plans[plan_id] = payload
            return HttpResponse(201, {"plan_id": plan_id, "status": "approved"}, {}, url, method)

        # Deployment plan dispatch
        if path.startswith("/api/deployment/plans/") and path.endswith("/dispatch"):
            plan_id = path.split("/")[-2]
            saga_id = f"deployment-saga-{plan_id}"
            return HttpResponse(
                202,
                {
                    "deployment_saga": {
                        "saga_id": saga_id,
                        "saga": {"saga_id": saga_id, "status": "awaiting_binding"},
                    },
                    "status": "dispatch_accepted",
                },
                {},
                url,
                method,
            )

        # Deployment plan detail query
        if path.startswith("/api/deployment/plans/"):
            plan_id = path.split("/")[-1]
            if "plan_terminal" in self.simulate_timeout_on:
                return HttpResponse(200, {"plan_id": plan_id, "status": "running"}, {}, url, method)
            return HttpResponse(
                200,
                {
                    "plan_id": plan_id,
                    "status": self.deployment_terminal_status,
                    "deployment_saga_id": f"deployment-saga-{plan_id}",
                },
                {},
                url,
                method,
            )

        # Deployment saga query
        if path.startswith("/api/deployment/sagas/"):
            saga_id = path.split("/")[-1]
            if "saga_terminal" in self.simulate_timeout_on:
                return HttpResponse(200, {"saga_id": saga_id, "status": "running"}, {}, url, method)
            return HttpResponse(
                200,
                {
                    "saga_id": saga_id,
                    "status": self.deployment_saga_status,
                    "metadata": {
                        "foundation": {
                            "command_envelope": {
                                "actor_ref": {
                                    "actor_id": "deployment-dispatcher",
                                    "service": "pantheon-deployment",
                                }
                            }
                        }
                    },
                },
                {},
                url,
                method,
            )

        # Deployment inbox query (applied receipt)
        if path == "/api/deployment/inbox":
            self.inbox_read_count += 1
            if (self.is_reloading or self.inbox_read_count > 1) and self.reload_inbox_differs:
                return HttpResponse(200, [], {}, url, method)
            if self.inbox_override_items is not None:
                agg_id = urllib.parse.parse_qs(parsed.query).get("aggregate_id", [""])[0]
                items = copy.deepcopy(self.inbox_override_items)
                for it in items:
                    if it.get("aggregate_id") == "{saga_id}":
                        it["aggregate_id"] = agg_id
                return HttpResponse(200, items, {}, url, method)
            if not self.inbox_receipt_applied:
                return HttpResponse(200, [], {}, url, method)
            agg_id = urllib.parse.parse_qs(parsed.query).get("aggregate_id", [""])[0]
            return HttpResponse(
                200,
                [
                    {
                        "aggregate_id": agg_id,
                        "aggregate_type": "deployment_saga",
                        "consumer_name": "deployment-outbox-consumer",
                        "event_id": f"evt-{agg_id}-0002",
                        "idempotency_key": f"{agg_id}:2:runtime.load.requested",
                        "sequence_no": 2,
                        "status": "applied",
                        "trace_id": "trace-inbox-001",
                        "processed_at": "2026-09-05T00:00:00Z",
                    }
                ],
                {},
                url,
                method,
            )

        # Deployment projection query (DEP-003)
        if path.startswith("/api/deployment/projections/"):
            self.dep003_read_count += 1
            plan_id = path.split("/")[-1]
            binding_id = f"rb-{plan_id.removeprefix('plan-')}"
            if not self.dep003_valid:
                return HttpResponse(200, {"projection_contract": "invalid"}, {}, url, method)
            if (self.is_reloading or self.dep003_read_count > 1) and self.reload_dep003_differs:
                return HttpResponse(
                    200,
                    {
                        "projection_contract": "DEP-003",
                        "lifecycle_state": "failed",
                        "plan_status": "failed",
                        "runtime_binding_id": binding_id,
                        "deployment_saga_status": "failed",
                    },
                    {},
                    url,
                    method,
                )
            return HttpResponse(
                200,
                {
                    "projection_contract": "DEP-003",
                    "lifecycle_state": "active",
                    "plan_status": "executed",
                    "actual_stage": "paper",
                    "runtime_binding_id": binding_id,
                    "deployment_saga_status": "completed",
                    "plan_id": plan_id,
                },
                {},
                url,
                method,
            )

        # Query RuntimeBinding
        if path == "/api/runtime-bindings":
            if method == "GET" and parsed.query and "plan_id=" in parsed.query:
                if "binding_created" in self.simulate_timeout_on:
                    return HttpResponse(200, {"bindings": []}, {}, url, method)
                plan_id = urllib.parse.parse_qs(parsed.query)["plan_id"][0]
                binding_id = f"rb-{plan_id.removeprefix('plan-')}"
                plan = self.created_plans.get(plan_id, {})
                artifact_id = (
                    plan.get("registry_entry", {}).get("registry_id")
                    or (plan.get("metadata", {}) or {}).get("registry_id")
                    or f"artifact-{plan_id.removeprefix('plan-')}"
                )
                pool_id = plan.get("capital_pool_id") or f"pool-{plan_id.removeprefix('plan-')}"

                strat_id = "tw_session_momentum"
                version = "2.1.0"
                base_key = f"openclaw/registry/{strat_id}/{version}"

                # Calculate deterministic checksum matching stimulus child artifact
                suffix = plan_id.removeprefix("plan-devprobe-")
                child_artifact = {
                    "artifact_id": artifact_id,
                    "parameters": {"momentum_threshold": 0.015},
                    "source_run_ids": [TASK_ID, f"stimulus-{suffix}"],
                    "strategy_id": strat_id,
                    "version": version,
                }
                artifact_raw = json.dumps(child_artifact, sort_keys=True, separators=(",", ":"))
                checksum = f"sha256:{hashlib.sha256(artifact_raw.encode('utf-8')).hexdigest()}"

                binding_obj = {
                    "binding_id": binding_id,
                    "runtime_id": f"rt-{binding_id}",
                    "capital_pool_id": pool_id,
                    "artifact_id": artifact_id,
                    "artifact_version": version,
                    "plan_id": plan_id,
                    "status": "active",
                    "symbol": "2330.TW",
                    "deployment_mode": "paper",
                    "market_data_policy": {"owner": "source-ingest"},
                    "metadata": {
                        "strategy_id": strat_id,
                        "symbol": "2330.TW",
                        "object_store": {
                            f"{base_key}/metadata.json": {
                                "checksum": checksum,
                                "version": version,
                            },
                            f"{base_key}/artifact.bin": artifact_raw,
                        },
                    },
                }
                self.created_bindings[binding_id] = binding_obj
                return HttpResponse(200, {"bindings": [binding_obj]}, {}, url, method)

        if path.startswith("/api/runtime-bindings/"):
            binding_id = path.split("/")[-1]
            if self.reload_binding_differs:
                return HttpResponse(
                    200,
                    {"binding_id": binding_id, "status": "inactive"},
                    {},
                    url,
                    method,
                )
            binding_obj = copy.deepcopy(self.created_bindings.get(binding_id, {
                "binding_id": binding_id,
                "status": "active",
                "plan_id": f"plan-{binding_id.removeprefix('rb-')}",
                "runtime_id": f"rt-{binding_id}",
                "artifact_id": f"artifact-{binding_id.removeprefix('rb-')}",
                "artifact_version": "2.1.0",
                "capital_pool_id": f"pool-{binding_id.removeprefix('rb-')}",
                "symbol": "2330.TW",
                "market_data_policy": {"owner": "source-ingest"},
                "metadata": {
                    "strategy_id": "tw_session_momentum",
                    "symbol": "2330.TW",
                    "object_store": {
                        "openclaw/registry/tw_session_momentum/2.1.0/metadata.json": {
                            "checksum": "sha256:valid",
                            "version": "2.1.0",
                        }
                    }
                }
            }))
            if self.reload_checksum_differs:
                base_key = "openclaw/registry/tw_session_momentum/2.1.0"
                binding_obj["metadata"]["object_store"][f"{base_key}/metadata.json"]["checksum"] = "sha256:corrupted"
            return HttpResponse(200, binding_obj, {}, url, method)

        # Fleet desired state
        if path == "/api/runtime-fleet/desired-state":
            if not self.fleet_desired_accepted:
                return HttpResponse(200, {"bindings": []}, {}, url, method)
            active_bindings = [{"binding_id": b_id} for b_id in self.created_bindings]
            return HttpResponse(200, {"bindings": active_bindings}, {}, url, method)

        # Fleet state
        if path == "/api/fleet/state":
            if self.fleet_workers_override is not None:
                return HttpResponse(200, {"workers": self.fleet_workers_override}, {}, url, method)
            workers = []
            for b_id in self.created_bindings:
                workers.append({
                    "binding_id": b_id,
                    "worker_id": f"worker-{b_id}",
                    "service": "paper-runtime-worker",
                    "status": "running" if self.fleet_worker_running else "stopped",
                    "pid": 1234,
                })
            return HttpResponse(200, {"workers": workers}, {}, url, method)

        # Telemetry runtime summary
        if path.startswith("/api/telemetry/runtime-summaries/"):
            runtime_id = path.split("/")[-1]
            self.summary_read_count += 1
            if (self.is_reloading or self.summary_read_count > 1) and self.reload_summary_differs:
                return HttpResponse(200, {"runtime_id": "rt-other"}, {}, url, method)
            event_id = f"ev-{runtime_id}"
            heartbeat_id = f"hb-{runtime_id}" if self.telemetry_heartbeat_present else ""
            summary = {
                "runtime_id": runtime_id,
                "state": "active",
                "last_heartbeat_event_id": heartbeat_id,
                "recent_lifecycle_event_ids": [event_id] if self.telemetry_fill_produced else [],
            }
            return HttpResponse(200, summary, {}, url, method)

        # Telemetry events
        if path.startswith("/api/telemetry/events/"):
            event_id = path.split("/")[-1]
            if "telemetry_event" in self.simulate_timeout_on:
                return HttpResponse(404, {"error": "not found"}, {}, url, method)

            # Liveness heartbeat route
            if event_id.startswith("hb-"):
                return HttpResponse(200, {"event_id": event_id, "event_type": "heartbeat", "status": "healthy"}, {}, url, method)

            self.event_read_count += 1
            if (self.is_reloading or self.event_read_count > 1) and self.reload_event_differs:
                return HttpResponse(200, {"event_id": "ev-different"}, {}, url, method)

            # ADVERSARIAL NEGATIVE TEST MODE (Finding 1): Return stripped event
            if self.telemetry_stripped_mode:
                return HttpResponse(
                    200,
                    {
                        "event_id": event_id,
                        "event_type": "paper_fill_simulated",
                    },
                    {},
                    url,
                    method,
                )

            runtime_id = event_id.removeprefix("ev-")
            binding_id = runtime_id.removeprefix("rt-")
            binding_obj = self.created_bindings.get(binding_id, {})
            artifact_id = binding_obj.get("artifact_id") or f"artifact-{binding_id.removeprefix('rb-')}"
            runtime_id = binding_obj.get("runtime_id") or f"rt-{binding_id}"
            plan_id = binding_obj.get("plan_id") or f"plan-{binding_id.removeprefix('rb-')}"
            correlation_id = (
                self.telemetry_correlation_override
                or f"correlation-{plan_id}"
            )
            ev = {
                "event_id": event_id,
                "event_type": "paper_fill_simulated",
                "binding_id": binding_id,
                "artifact_id": artifact_id,
                "runtime_id": runtime_id,
                "correlation_envelope": {"correlation_id": correlation_id},
                "metadata": {
                    "is_real_capital": self.telemetry_real_capital,
                    "is_real_order": self.telemetry_real_order,
                    "broker_submission_status": self.telemetry_broker_status,
                    "artifact_signal_not_smoke": True,
                    "source_snapshot_driven": True,
                    "correlation_envelope": {"correlation_id": correlation_id},
                },
            }
            self.created_events[event_id] = ev
            return HttpResponse(200, ev, {}, url, method)

        # Trade episodes query (independent causal consumer receipt for fill, Findings 2 & 4)
        if path == "/api/telemetry/trade-episodes":
            self.trade_episodes_read_count += 1
            if (self.is_reloading or self.trade_episodes_read_count > 1) and self.reload_trade_episode_differs:
                return HttpResponse(200, {"projections": []}, {}, url, method)
            qs = urllib.parse.parse_qs(parsed.query)
            runtime_id = qs.get("runtime_id", ["rt-unknown"])[0]
            binding_id = runtime_id.removeprefix("rt-")
            if self.trade_episodes_override is not None:
                projs = copy.deepcopy(self.trade_episodes_override)
                for pr in projs:
                    if pr.get("runtime_id") == "{runtime_id}":
                        pr["runtime_id"] = runtime_id
                    if pr.get("binding_id") == "{binding_id}":
                        pr["binding_id"] = binding_id
                    if pr.get("event_id") == "{event_id}":
                        pr["event_id"] = f"ev-{runtime_id}"
                    if pr.get("fill_ids") == ["{event_id}"]:
                        pr["fill_ids"] = [f"ev-{runtime_id}"]
                return HttpResponse(200, {"projections": projs}, {}, url, method)
            if not self.trade_episode_produced:
                return HttpResponse(200, {"projections": []}, {}, url, method)
            return HttpResponse(
                200,
                {
                    "projections": [
                        {
                            "episode_id": f"ep-{runtime_id}",
                            "trade_episode_id": f"ep-{runtime_id}",
                            "runtime_id": runtime_id,
                            "binding_id": binding_id,
                            "runtime_binding_id": binding_id,
                            "event_id": f"ev-{runtime_id}",
                            "fill_ids": [f"ev-{runtime_id}"],
                            "status": "closed",
                            "fill_count": 1,
                            "filled_quantity": 10.0,
                        }
                    ]
                },
                {},
                url,
                method,
            )

        # Trade episode detail query
        if path.startswith("/api/telemetry/trade-episodes/"):
            ep_id = path.split("/")[-1]
            if (self.is_reloading or getattr(self, "trade_episodes_read_count", 0) >= 1) and self.reload_trade_episode_differs:
                return HttpResponse(404, {"error": "not found"}, {}, url, method)
            if not self.trade_episode_produced:
                return HttpResponse(404, {"error": "not found"}, {}, url, method)
            runtime_id = ep_id.removeprefix("ep-")
            binding_id = runtime_id.removeprefix("rt-")
            return HttpResponse(
                200,
                {
                    "episode_id": ep_id,
                    "trade_episode_id": ep_id,
                    "runtime_id": runtime_id,
                    "binding_id": binding_id,
                    "runtime_binding_id": binding_id,
                    "event_id": f"ev-{runtime_id}",
                    "fill_ids": [f"ev-{runtime_id}"],
                    "status": "closed",
                    "fill_count": 1,
                    "filled_quantity": 10.0,
                },
                {},
                url,
                method,
            )

        # Reject all unknown routes with HTTP 404 (Finding 3: never default to HTTP 200)
        return HttpResponse(404, {"error": f"Unknown route: {method} {path}"}, {}, url, method)


def _default_test_config(**kwargs: Any) -> ProbeConfig:
    defaults: dict[str, Any] = {
        "auth_token": "test-secret-token",
        "tenant_id": "default",
        "bff_base_url": DEFAULT_BFF_BASE_URL,
        "fe_base_url": DEFAULT_FE_BASE_URL,
        "expected_fe_sha": FE_SHA_VALID,
        "expected_bff_sha": BFF_SHA_VALID,
        "deployment_url": DEFAULT_BFF_BASE_URL,
        "runtime_url": DEFAULT_BFF_BASE_URL,
        "fleet_url": DEFAULT_BFF_BASE_URL,
        "telemetry_url": DEFAULT_BFF_BASE_URL,
        "capital_url": DEFAULT_BFF_BASE_URL,
        "governance_url": DEFAULT_BFF_BASE_URL,
        "registry_url": DEFAULT_BFF_BASE_URL,
        "source_ingest_url": DEFAULT_BFF_BASE_URL,
        "execute_paper_lifecycle": False,
        "paper_only": True,
        "poll_timeout_seconds": 2.0,
        "poll_interval_seconds": 0.05,
        "output_path": Path("/tmp/test-evidence-devprobe.json"),
    }
    defaults.update(kwargs)
    return ProbeConfig(**defaults)


def test_read_only_preflight_default() -> None:
    """Verifies that default mode executes read-only preflight and passes."""
    double = HonestCanonicalOwnerDouble()
    config = _default_test_config(execute_paper_lifecycle=False)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    evidence = probe.run()

    assert evidence["schema_version"] == SCHEMA_VERSION
    assert evidence["task_id"] == TASK_ID
    assert evidence["status"] == "preflight_passed"
    assert evidence["mode"] == "read_only_preflight"
    assert evidence["served_identity"]["fe"]["commit"] == FE_SHA_VALID
    assert evidence["served_identity"]["bff"]["source_commit_sha"] == BFF_SHA_VALID
    assert evidence["served_identity"]["pair_consistent"] is True
    assert evidence["audit"]["bearer_credentials_redacted"] is True
    assert "artifact_digest_sha256" in evidence
    assert len(evidence["artifact_digest_sha256"]) == 64
    assert len(double.created_plans) == 0
    assert len(double.created_bindings) == 0


def test_stale_identity_fe_rejection() -> None:
    """Verifies that mismatched FE commit raises StaleIdentityError."""
    double = HonestCanonicalOwnerDouble(fe_commit="c" * 40)
    config = _default_test_config(expected_fe_sha=FE_SHA_VALID)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(StaleIdentityError, match="Served FE commit"):
        probe.run()


def test_stale_identity_bff_rejection() -> None:
    """Verifies that mismatched BFF commit raises StaleIdentityError."""
    double = HonestCanonicalOwnerDouble(bff_commit="d" * 40, fe_manifest_bff_sha=BFF_SHA_VALID)
    config = _default_test_config(expected_bff_sha=BFF_SHA_VALID)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(StaleIdentityError, match="Served BFF commit"):
        probe.run()


def test_stale_identity_manifest_bff_sha_mismatch() -> None:
    """Verifies that FE manifest's bffCommit mismatching expected BFF SHA raises StaleIdentityError (Finding 5)."""
    double = HonestCanonicalOwnerDouble(fe_manifest_bff_sha="e" * 40)
    config = _default_test_config()
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(StaleIdentityError, match="FE manifest BFF SHA"):
        probe.run()


def test_preflight_fail_closed_on_missing_expected_fe_sha() -> None:
    """Verifies that missing or invalid expected_fe_sha raises PreflightBlockedError (Finding 5)."""
    double = HonestCanonicalOwnerDouble()
    config = _default_test_config(expected_fe_sha="")
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(PreflightBlockedError, match="expected_fe_sha must be a valid lowercase 40-hex commit SHA"):
        probe.run_preflight()

    evidence = probe.run()
    assert evidence["status"] == "blocked"
    assert "expected_fe_sha" in evidence["blocked_reason"]


def test_preflight_fail_closed_on_missing_expected_bff_sha() -> None:
    """Verifies that missing or invalid expected_bff_sha raises PreflightBlockedError (Finding 5)."""
    double = HonestCanonicalOwnerDouble()
    config = _default_test_config(expected_bff_sha=None)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(PreflightBlockedError, match="expected_bff_sha must be a valid lowercase 40-hex commit SHA"):
        probe.run_preflight()

    evidence = probe.run()
    assert evidence["status"] == "blocked"
    assert "expected_bff_sha" in evidence["blocked_reason"]


def test_preflight_fail_closed_on_malformed_build_mode() -> None:
    """Verifies that FE manifest with invalid buildMode raises PreflightBlockedError (Finding 5)."""
    double = HonestCanonicalOwnerDouble(fe_build_mode_valid=False)
    config = _default_test_config()
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(PreflightBlockedError, match="FE buildMode VITE_BFF_MODE must be 'live'"):
        probe.run_preflight()

    evidence = probe.run()
    assert evidence["status"] == "blocked"
    assert "buildMode" in evidence["blocked_reason"]


def test_preflight_fail_closed_on_unverified_auth_posture() -> None:
    """Verifies that unverified auth posture (auth_stub=True) raises PreflightBlockedError (Finding 5)."""
    double = HonestCanonicalOwnerDouble(bff_auth_stub=True)
    config = _default_test_config()
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(PreflightBlockedError, match="BFF config_posture.auth_stub must be False"):
        probe.run_preflight()

    evidence = probe.run()
    assert evidence["status"] == "blocked"
    assert "auth_stub" in evidence["blocked_reason"]


def test_preflight_fail_closed_on_permissive_auth_mode() -> None:
    """Verifies that non-strict auth mode raises PreflightBlockedError (Finding 5)."""
    double = HonestCanonicalOwnerDouble(bff_auth_mode="permissive")
    config = _default_test_config()
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(PreflightBlockedError, match="BFF config_posture.auth_mode must be 'strict'"):
        probe.run_preflight()

    evidence = probe.run()
    assert evidence["status"] == "blocked"
    assert "auth_mode" in evidence["blocked_reason"]


def test_preflight_fail_closed_on_missing_environment() -> None:
    """Verifies that missing environment in BFF version raises PreflightBlockedError (Finding 5)."""
    double = HonestCanonicalOwnerDouble(bff_environment="")
    config = _default_test_config()
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(PreflightBlockedError, match="BFF /bff/version is missing environment"):
        probe.run_preflight()

    evidence = probe.run()
    assert evidence["status"] == "blocked"
    assert "environment" in evidence["blocked_reason"]


def test_parent_artifact_discovery_fail_closed() -> None:
    """Verifies that missing approved parent strategy artifact raises PreflightBlockedError (Finding 5)."""
    double = HonestCanonicalOwnerDouble(parent_artifact_discovered=False)
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    evidence = probe.run()
    assert evidence["status"] == "blocked"
    assert "Failed authoritative discovery" in evidence["blocked_reason"]


def test_adversarial_stripped_telemetry_fails_closed() -> None:
    """P1 adversarial test: stripped telemetry event containing only event_id and event_type must fail closed (Finding 1)."""
    double = HonestCanonicalOwnerDouble(telemetry_stripped_mode=True)
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(ProbeError, match="Telemetry event binding_id .* != stimulus binding_id"):
        probe.run()


def test_telemetry_missing_false_paper_safety_fails_closed() -> None:
    """Verifies that telemetry event with is_real_capital=True fails closed immediately (Finding 1)."""
    double = HonestCanonicalOwnerDouble(telemetry_real_capital=True)
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(RealCapitalOrOrderWriteForbiddenError, match="metadata.is_real_capital must be explicitly False"):
        probe.run()


def test_telemetry_missing_broker_status_fails_closed() -> None:
    """Verifies that telemetry event without broker_submission_status='filled' fails closed (Finding 1)."""
    double = HonestCanonicalOwnerDouble(telemetry_broker_status="submitted")
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(ProbeError, match="broker_submission_status must be 'filled'"):
        probe.run()


def test_wrong_correlation_pair_id_rejection() -> None:
    """Verifies that FE/BFF pair ID mismatch raises CorrelationMismatchError."""
    double = HonestCanonicalOwnerDouble(pair_id="pair-fe-111", bff_pair_id="pair-bff-222")
    config = _default_test_config()
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(CorrelationMismatchError, match="FE pairId .* does not match BFF pair_id"):
        probe.run()


def test_wrong_correlation_telemetry_envelope_rejection() -> None:
    """Verifies that telemetry event correlation_id mismatch raises CorrelationMismatchError."""
    double = HonestCanonicalOwnerDouble(
        telemetry_correlation_override="correlation-wrong-12345"
    )
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(CorrelationMismatchError, match="Telemetry event missing or mismatched correlation envelope"):
        probe.run()


def test_loop8_terminal_saga_retrieval_and_inbox_receipt() -> None:
    """Verifies that Loop 8 retrieves terminal saga and applied inbox receipt from outbox consumer (Finding 2)."""
    double = HonestCanonicalOwnerDouble()
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    evidence = probe.run()
    loop8 = evidence["loops"]["loop_08_promotion_deployment"]
    assert loop8["next_consumer_receipt_id"].startswith("evt-deployment-saga-")
    assert loop8["next_consumer_receipt_id"] != loop8["terminal_output_id"]
    assert loop8["owner_worker_identity"]["service"] == "deployment-outbox-consumer"
    assert loop8["next_consumer_readback"]["dep003_projection"]["projection_contract"] == "DEP-003"


def test_missing_consumer_receipt_failure_loop8() -> None:
    """Verifies that missing Loop 8 applied inbox receipt raises MissingConsumerReceiptError (Finding 2)."""
    double = HonestCanonicalOwnerDouble(inbox_receipt_applied=False)
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(MissingConsumerReceiptError, match="Deployment outbox consumer inbox receipt .* missing or not applied"):
        probe.run()


def test_missing_trade_episode_receipt_failure_loop9() -> None:
    """Verifies that missing independent trade episode receipt raises MissingConsumerReceiptError (Finding 4)."""
    double = HonestCanonicalOwnerDouble(trade_episode_produced=False)
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(MissingConsumerReceiptError, match="Independent trade episode consumer receipt .* missing or not produced"):
        probe.run()


def test_missing_heartbeat_failure_loop9() -> None:
    """Verifies that missing Loop 9 heartbeat receipt raises MissingConsumerReceiptError."""
    double = HonestCanonicalOwnerDouble(telemetry_heartbeat_present=False)
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(MissingConsumerReceiptError, match="Loop 9 liveness heartbeat ID missing"):
        probe.run()


def test_authenticated_headers_sent_and_verified() -> None:
    """Verifies that adapter sends Bearer authorization and X-Tenant-Id headers (Finding 3)."""
    double = HonestCanonicalOwnerDouble()
    config = _default_test_config(auth_token="test-secret-token", tenant_id="tenant-prod")
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    probe.run()

    # Find authenticated requests (e.g. /bff/version)
    bff_reqs = [r for r in double.recorded_requests if "/bff/version" in r["url"]]
    assert len(bff_reqs) == 1
    assert bff_reqs[0]["headers"]["Authorization"] == "Bearer test-secret-token"
    assert bff_reqs[0]["headers"]["X-Tenant-Id"] == "tenant-prod"


def test_unknown_routes_fail_with_404() -> None:
    """Verifies that unknown routes on the test double return 404, never 200 (Finding 3)."""
    double = HonestCanonicalOwnerDouble()
    resp = double(
        "GET",
        "https://api.dev.mvl-cap.tw/api/unknown-service/foo",
        {"Authorization": "Bearer tok", "X-Tenant-Id": "default"},
        None,
        5.0,
    )
    assert resp.status == 404
    assert "Unknown route" in str(resp.payload)


def test_join_url_deduplicates_overlapping_segments() -> None:
    """Verifies that join_url does not produce duplicate /api/... segments (Finding 3)."""
    # Base ending with /api/deployment and path starting with /api/deployment/plans
    url1 = join_url("https://api.dev.mvl-cap.tw/api/deployment", "/api/deployment/plans")
    assert url1 == "https://api.dev.mvl-cap.tw/api/deployment/plans"

    # Base without path and path starting with /api/deployment/plans
    url2 = join_url("https://api.dev.mvl-cap.tw", "/api/deployment/plans")
    assert url2 == "https://api.dev.mvl-cap.tw/api/deployment/plans"

    # Base with query string preserved
    url3 = join_url("http://deployment:8095", "/api/deployment/inbox?aggregate_id=123")
    assert url3 == "http://deployment:8095/api/deployment/inbox?aggregate_id=123"


def test_timeout_waiting_for_deployment_terminal() -> None:
    """Verifies that timeout waiting for deployment plan raises ProbeTimeoutError."""
    double = HonestCanonicalOwnerDouble(simulate_timeout_on={"plan_terminal"})
    config = _default_test_config(execute_paper_lifecycle=True, poll_timeout_seconds=0.3)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(ProbeTimeoutError, match="Timed out waiting for DeploymentPlan"):
        probe.run()


def test_reload_mismatch_binding() -> None:
    """Verifies that fresh-client reload returning different binding state raises ReloadMismatchError."""
    double = HonestCanonicalOwnerDouble(reload_binding_differs=True)
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(ReloadMismatchError, match="Reloaded binding status .* is not active"):
        probe.run()


def test_reload_mismatch_checksum() -> None:
    """Verifies that reload with mismatched projection checksum raises ReloadMismatchError (Finding 4)."""
    double = HonestCanonicalOwnerDouble(reload_checksum_differs=True)
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(ReloadMismatchError, match="Reloaded binding projection checksum"):
        probe.run()


def test_reload_mismatch_dep003() -> None:
    """Verifies that reload with corrupted DEP-003 projection raises ReloadMismatchError (Finding 4)."""
    double = HonestCanonicalOwnerDouble(reload_dep003_differs=True)
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(ReloadMismatchError, match="Reloaded DEP-003 projection failed verification"):
        probe.run()


def test_fresh_client_isolated_flag_truth() -> None:
    """Verifies fresh_client_isolated is False without factory and True with factory (Finding 4)."""
    double = HonestCanonicalOwnerDouble()
    config = _default_test_config(execute_paper_lifecycle=True)

    # 1. Run without fresh_transport_factory
    probe1 = DevRuntimePaperLifecycleProbe(config, transport=double)
    evidence1 = probe1.run()
    assert evidence1["durable_fresh_client_reload"]["fresh_client_isolated"] is False
    assert evidence1["durable_fresh_client_reload"]["verified"] is False
    assert "incomplete_reason" in evidence1["durable_fresh_client_reload"]

    # 2. Run with fresh_transport_factory
    def transport_factory() -> HonestCanonicalOwnerDouble:
        fresh_double = HonestCanonicalOwnerDouble()
        fresh_double.created_bindings = double.created_bindings
        fresh_double.created_events = double.created_events
        return fresh_double

    probe2 = DevRuntimePaperLifecycleProbe(config, transport=double)
    evidence2 = probe2.run(fresh_transport_factory=transport_factory)
    assert evidence2["durable_fresh_client_reload"]["fresh_client_isolated"] is True
    assert evidence2["durable_fresh_client_reload"]["verified"] is True
    assert "incomplete_reason" not in evidence2["durable_fresh_client_reload"]


def test_full_successful_paper_lifecycle_run() -> None:
    """Verifies complete execution of Loops 8 and 9 with fresh stimulus, distinct receipts, and reload."""
    double = HonestCanonicalOwnerDouble()
    config = _default_test_config(execute_paper_lifecycle=True)

    def transport_factory() -> HonestCanonicalOwnerDouble:
        fresh_double = HonestCanonicalOwnerDouble()
        fresh_double.created_bindings = double.created_bindings
        fresh_double.created_events = double.created_events
        return fresh_double

    probe = DevRuntimePaperLifecycleProbe(config, transport=double)
    evidence = probe.run(fresh_transport_factory=transport_factory)

    assert evidence["status"] == "passed"
    assert evidence["mode"] == "paper_lifecycle"

    # Loop 8 assertions (Finding 2)
    loop8 = evidence["loops"]["loop_08_promotion_deployment"]
    assert loop8["trigger_id"].startswith("plan-devprobe-")
    assert loop8["terminal_output_id"].startswith("rb-devprobe-")
    assert loop8["next_consumer_receipt_id"].startswith("evt-deployment-saga-")
    assert loop8["next_consumer_receipt_id"] != loop8["terminal_output_id"]
    assert loop8["owner_worker_identity"]["service"] == "deployment-outbox-consumer"
    assert loop8["assertions"]["executable_runtime_binding"] is True
    assert loop8["assertions"]["saga_completed"] is True
    assert loop8["assertions"]["dep003_active"] is True
    assert loop8["durable_reload"]["verified"] is True

    # Loop 9 assertions (Findings 1 & 4)
    loop9 = evidence["loops"]["loop_09_capital_artifact_execution"]
    assert loop9["trigger_id"] == loop8["terminal_output_id"]
    assert loop9["terminal_output_id"].startswith("ev-rt-rb-")
    assert loop9["next_consumer_receipt_id"].startswith("ep-rt-rb-")
    assert loop9["next_consumer_receipt_id"] != loop9["terminal_output_id"]
    assert loop9["owner_worker_identity"]["service"] == "paper-runtime-worker"
    assert loop9["assertions"]["is_real_capital"] is False
    assert loop9["assertions"]["is_real_order"] is False
    assert loop9["assertions"]["broker_submission_status"] == "filled"
    assert loop9["assertions"]["source_snapshot_driven"] is True
    assert loop9["assertions"]["artifact_signal_not_smoke"] is True
    assert loop9["durable_reload"]["verified"] is True

    # Durable reload assertions (Finding 4)
    reload_sec = evidence["durable_fresh_client_reload"]
    assert reload_sec["verified"] is True
    assert reload_sec["fresh_client_isolated"] is True
    assert reload_sec["loop_08_dep003_projection"]["projection_contract"] == "DEP-003"
    assert reload_sec["loop_08_inbox_receipt"]["aggregate_id"] == f"deployment-saga-{loop8['trigger_id']}"
    assert reload_sec["loop_08_inbox_receipt"]["consumer_name"] == "deployment-outbox-consumer"
    assert reload_sec["loop_09_heartbeat_id"].startswith("hb-rt-rb-")
    assert reload_sec["loop_09_trade_episode_receipt"]["episode_id"] == f"ep-rt-{loop8['terminal_output_id']}"

    # Verify that stimulus IDs were fresh and did not use historical forbidden IDs
    for hist_id in HISTORICAL_FORBIDDEN_IDS:
        assert hist_id != loop8["trigger_id"]
        assert hist_id != loop8["terminal_output_id"]
        assert hist_id != loop9["terminal_output_id"]


def test_missing_auth_token_fails_closed() -> None:
    """Verifies that missing auth token fails closed and does not use dummy fallback (Finding 3)."""
    double = HonestCanonicalOwnerDouble()
    config = _default_test_config(auth_token="")
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(ProbeError, match="Authentication token is required"):
        probe.run()


def test_cross_origin_credential_transmission_blocked() -> None:
    """Verifies that sending credentials to unauthorized origins is blocked (Finding 4)."""
    double = HonestCanonicalOwnerDouble()
    config = _default_test_config()
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(ProbeError, match="Cross-origin credential transmission blocked"):
        probe.adapter.request(
            "GET",
            "https://unauthorized-attacker.example.com",
            "/api/deployment/plans",
        )


def test_static_fe_manifest_receives_no_auth_header() -> None:
    """Verifies that static FE manifest request never receives Authorization header (Finding 4)."""
    double = HonestCanonicalOwnerDouble()
    config = _default_test_config(auth_token="super-secret-token")
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    probe.run()

    fe_reqs = [r for r in double.recorded_requests if "/deployment.json" in r["url"]]
    assert len(fe_reqs) == 1
    assert "Authorization" not in fe_reqs[0]["headers"]


def test_loop8_inbox_receipt_wrong_aggregate_id_fails_closed() -> None:
    """Loop 8 query filter is not assertion: aggregate_id mismatch must fail closed (Finding 1)."""
    double = HonestCanonicalOwnerDouble(
        inbox_override_items=[
            {
                "aggregate_id": "deployment-saga-wrong-id",
                "aggregate_type": "deployment_saga",
                "consumer_name": "deployment-outbox-consumer",
                "event_id": "evt-001",
                "sequence_no": 1,
                "status": "applied",
            }
        ]
    )
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(MissingConsumerReceiptError, match="aggregate_id mismatch"):
        probe.run()


def test_loop8_inbox_receipt_wrong_consumer_name_fails_closed() -> None:
    """Loop 8 inbox receipt consumer_name must strictly match deployment-outbox-consumer (Finding 1)."""
    double = HonestCanonicalOwnerDouble(
        inbox_override_items=[
            {
                "aggregate_id": "{saga_id}",
                "aggregate_type": "deployment_saga",
                "consumer_name": "unauthorized-consumer",
                "event_id": "evt-001",
                "sequence_no": 1,
                "status": "applied",
            }
        ]
    )
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(MissingConsumerReceiptError, match="consumer_name mismatch"):
        probe.run()


def test_loop8_inbox_receipt_not_applied_fails_closed() -> None:
    """Loop 8 inbox receipt status must be 'applied' (Finding 1)."""
    double = HonestCanonicalOwnerDouble(
        inbox_override_items=[
            {
                "aggregate_id": "{saga_id}",
                "aggregate_type": "deployment_saga",
                "consumer_name": "deployment-outbox-consumer",
                "event_id": "evt-001",
                "sequence_no": 1,
                "status": "pending",
            }
        ]
    )
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(MissingConsumerReceiptError, match="status is 'pending', expected 'applied'"):
        probe.run()


def test_loop8_inbox_receipt_missing_sequence_no_fails_closed() -> None:
    """Loop 8 inbox receipt must contain a valid sequence_no (Finding 1)."""
    double = HonestCanonicalOwnerDouble(
        inbox_override_items=[
            {
                "aggregate_id": "{saga_id}",
                "aggregate_type": "deployment_saga",
                "consumer_name": "deployment-outbox-consumer",
                "event_id": "evt-001",
                "status": "applied",
            }
        ]
    )
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(MissingConsumerReceiptError, match="missing sequence_no"):
        probe.run()


def test_loop9_trade_episode_open_zero_fill_fails_closed() -> None:
    """Loop 9 open zero-fill episodes sharing runtime_id must never be accepted as fill receipt (Finding 2)."""
    double = HonestCanonicalOwnerDouble(
        trade_episodes_override=[
            {
                "episode_id": "ep-open-zero",
                "trade_episode_id": "ep-open-zero",
                "runtime_id": "{runtime_id}",
                "binding_id": "{binding_id}",
                "runtime_binding_id": "{binding_id}",
                "event_id": "{event_id}",
                "fill_ids": ["{event_id}"],
                "status": "open",
                "fill_count": 0,
                "filled_quantity": 0.0,
            }
        ]
    )
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(MissingConsumerReceiptError, match="Independent trade episode consumer receipt"):
        probe.run()


def test_loop9_trade_episode_wrong_fill_id_fails_closed() -> None:
    """Loop 9 episode not linked to the stimulus event_id must fail closed (Finding 2)."""
    double = HonestCanonicalOwnerDouble(
        trade_episodes_override=[
            {
                "episode_id": "ep-unrelated",
                "trade_episode_id": "ep-unrelated",
                "runtime_id": "{runtime_id}",
                "binding_id": "{binding_id}",
                "runtime_binding_id": "{binding_id}",
                "event_id": "ev-unrelated-fill",
                "fill_ids": ["ev-unrelated-fill"],
                "status": "closed",
                "fill_count": 1,
                "filled_quantity": 10.0,
            }
        ]
    )
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(MissingConsumerReceiptError, match="Independent trade episode consumer receipt"):
        probe.run()


def test_loop9_missing_worker_identity_fails_closed() -> None:
    """Loop 9 must extract actual worker identity and fail closed if absent (Finding 2)."""
    double = HonestCanonicalOwnerDouble(fleet_workers_override=[])
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(MissingConsumerReceiptError, match="Loop 9 missing authoritative worker identity"):
        probe.run()


def test_reload_mismatch_inbox_receipt() -> None:
    """Verifies that missing or altered inbox receipt on reload raises ReloadMismatchError (Finding 4)."""
    double = HonestCanonicalOwnerDouble(reload_inbox_differs=True)
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(ReloadMismatchError, match="missing or mutated on fresh readback"):
        probe.run()


def test_reload_mismatch_trade_episode() -> None:
    """Verifies that missing or altered trade episode receipt on reload raises ReloadMismatchError (Finding 4)."""
    double = HonestCanonicalOwnerDouble(reload_trade_episode_differs=True)
    config = _default_test_config(execute_paper_lifecycle=True)
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    with pytest.raises(ReloadMismatchError, match="Reloaded Loop 9 trade episode .* missing on fresh readback"):
        probe.run()


def test_preflight_bff_version_without_pair_id_accepts_release_manifest() -> None:
    """Verifies that preflight passes when public BFF omits pair_id but FE release manifest specifies it (Finding 4)."""
    double = HonestCanonicalOwnerDouble(bff_pair_id=None)
    config = _default_test_config()
    probe = DevRuntimePaperLifecycleProbe(config, transport=double)

    evidence = probe.run()
    assert evidence["status"] == "preflight_passed"
    assert evidence["served_identity"]["pair_consistent"] is True
    assert evidence["served_identity"]["fe"]["pairId"] == PAIR_ID_VALID
    assert evidence["served_identity"]["bff"]["pair_id"] == PAIR_ID_VALID
