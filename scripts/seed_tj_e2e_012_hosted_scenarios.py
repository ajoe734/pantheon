#!/usr/bin/env python3
"""Materialize deterministic TJ-E2E-012 fixtures through canonical telemetry.

The script is intentionally write-capable and must only run on the Pantheon
dev VM from the explicitly authorized hosted-acceptance workflow.  It resolves
one real active paper RuntimeBinding, publishes a dev-only fixture batch to the
loopback telemetry ingest service, and prints only non-sensitive counts.  The
telemetry service and lifecycle projector independently reject this fixture
type unless their dev-only gate is enabled.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


SEED_SOURCE = "tj_e2e_012_hosted_seed_v3"
FIXTURE_EVENT_TYPE = "trade_journey_fixture"
FIXTURE_SCHEMA_VERSION = "pantheon.trade-journey-fixture.v1"
TENANT_ID = "tenant-dev"
AMBIGUITY_IDENTIFIER = "ambiguous-scenario-9"
ALLOWED_TELEMETRY_ORIGINS = frozenset(
    {"http://127.0.0.1:18083", "http://localhost:18083"}
)
ALLOWED_RUNTIME_MANAGER_ORIGINS = frozenset(
    {"http://127.0.0.1:18081", "http://localhost:18081"}
)
ALLOWED_BFF_ORIGINS = frozenset(
    {"http://127.0.0.1:18001", "http://localhost:18001"}
)
REQUIRED_BINDING_FIELDS = (
    "binding_id",
    "runtime_id",
    "capital_pool_id",
    "artifact_id",
    "artifact_version",
    "plan_id",
    "persona_capital_binding_id",
    "effective_at",
)
OBSERVABLE_STAGES = (
    "signal_generation",
    "trade_decision",
    "risk_evaluation",
    "order_submission",
    "broker_acknowledgement",
    "fill_management",
    "ledger_booking",
    "reconciliation",
)


class SeedError(RuntimeError):
    pass


def _event(
    journey_id: str,
    sequence_no: int,
    stage: str,
    stage_status: str,
    occurred_at: str,
    *,
    environment: str = "paper",
    recorded_at: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "event_id": f"{SEED_SOURCE}-{journey_id}-{sequence_no:02d}",
        "journey_id": journey_id,
        "tenant_id": TENANT_ID,
        "environment": environment,
        "occurred_at": occurred_at,
        "recorded_at": recorded_at or occurred_at,
        "source": SEED_SOURCE,
        "sequence_no": sequence_no,
        "stage": stage,
        "stage_status": stage_status,
        "schema_version": "1",
        **extra,
    }


def build_scenarios() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    common_ids = {
        "research_journey_id": "research-scenario-1",
        "strategy_lifecycle_id": "strategy-lifecycle-scenario-1",
    }
    for sequence_no, stage in enumerate(OBSERVABLE_STAGES, start=1):
        events.append(
            _event(
                "tj-scenario-1",
                sequence_no,
                stage,
                "succeeded",
                f"2026-07-12T10:{sequence_no:02d}:00Z",
                evidence_refs=[f"evidence://tj-scenario-1/{stage}"],
                **common_ids,
            )
        )

    events.append(
        _event(
            "tj-scenario-2",
            1,
            "promotion_decision",
            "rejected",
            "2026-07-12T11:02:00Z",
            reason_code="candidate_quality_floor",
            summary="Candidate rejected before execution",
        )
    )
    events.append(
        _event(
            "tj-scenario-3",
            1,
            "risk_evaluation",
            "blocked",
            "2026-07-12T11:03:00Z",
            reason_code="risk_limit_exceeded",
            failing_check="max_position_notional",
            policy_refs=["policy://risk/dev-v3"],
            input_refs=["snapshot://risk/tj-scenario-3"],
        )
    )
    events.extend(
        [
            _event(
                "tj-scenario-4",
                1,
                "order_submission",
                "succeeded",
                "2026-07-12T11:04:00Z",
                client_order_id="client-scenario-4",
                order_id="order-scenario-4",
            ),
            _event(
                "tj-scenario-4",
                2,
                "broker_acknowledgement",
                "rejected",
                "2026-07-12T11:04:30Z",
                reason_code="broker_price_band",
                incident_id="incident-scenario-4",
                filled_quantity=0,
                remaining_quantity=10,
                unfilled_quantity=10,
                order_state="rejected",
            ),
        ]
    )
    events.extend(
        [
            _event(
                "tj-scenario-5",
                1,
                "order_submission",
                "succeeded",
                "2026-07-12T11:05:00Z",
                client_order_id="client-scenario-5-a",
                order_id="order-scenario-5-a",
            ),
            _event(
                "tj-scenario-5",
                2,
                "broker_acknowledgement",
                "succeeded",
                "2026-07-12T11:05:20Z",
                event_type="order_replace",
                order_id="order-scenario-5-b",
                broker_order_id="broker-scenario-5-b",
                replaced_order_id="order-scenario-5-a",
                parent_order_id="order-scenario-5-a",
                causation_id="replace-causation-scenario-5",
                graph_edges=[
                    {
                        "from": "order-scenario-5-a",
                        "to": "order-scenario-5-b",
                        "type": "replaced_by",
                    }
                ],
            ),
            _event(
                "tj-scenario-5",
                3,
                "fill_management",
                "partially_succeeded",
                "2026-07-12T11:05:40Z",
                event_type="partial_fill",
                fill_id="fill-scenario-5",
                broker_trade_id="broker-trade-scenario-5",
                remaining_quantity=4,
                filled_quantity=6,
                causation_id="replace-causation-scenario-5",
            ),
        ]
    )
    events.append(
        _event(
            "tj-scenario-6",
            1,
            "trade_decision",
            "waiting_human",
            "2026-07-12T11:06:00Z",
            owner_role="operator",
            due_at="2026-07-12T13:06:00Z",
            return_url="/human-inbox?journey_id=tj-scenario-6",
            human_inbox_ref="human-inbox://tj-scenario-6",
        )
    )
    events.append(
        _event(
            "tj-scenario-7",
            1,
            "reconciliation",
            "failed",
            "2026-07-12T11:07:00Z",
            variance=12.5,
            source_ref="ledger://scenario-7",
            remediation_ref="runbook://reconciliation-variance",
            next_action="reconciliation_retry",
        )
    )
    events.extend(
        [
            _event(
                "tj-scenario-8",
                1,
                "signal_generation",
                "succeeded",
                "2026-07-12T11:08:00Z",
                recorded_at="2026-07-12T11:20:00Z",
                signal_id="signal-scenario-8",
            ),
            _event(
                "tj-scenario-8",
                2,
                "trade_decision",
                "succeeded",
                "2026-07-12T11:09:00Z",
                recorded_at="2026-07-12T11:10:00Z",
                decision_id="decision-scenario-8",
            ),
        ]
    )
    events.append(
        _event(
            "tj-scenario-9",
            1,
            "fill_management",
            "partially_succeeded",
            "2026-07-12T11:09:30Z",
            persona_id="persona-scenario-9",
            strategy_id="strategy-scenario-9",
            decision_id=AMBIGUITY_IDENTIFIER,
            client_order_id="client-scenario-9",
            broker_order_id="broker-scenario-9",
            fill_id="fill-scenario-9",
        )
    )
    events.append(
        _event(
            "tj-scenario-9-ambiguity-peer",
            1,
            "trade_decision",
            "succeeded",
            "2026-07-12T11:09:31Z",
            decision_id=AMBIGUITY_IDENTIFIER,
        )
    )
    events.append(
        _event(
            "tj-scenario-10",
            1,
            "order_submission",
            "succeeded",
            "2026-07-12T11:10:00Z",
            environment="live",
            account_id="live-account-scenario-10",
            capital_account_id="live-capital-scenario-10",
            client_order_id="live-client-scenario-10",
            order_id="live-order-scenario-10",
            broker_order_id="live-broker-scenario-10",
            quantity=17,
            price=123.45,
        )
    )
    for sequence_no, stage in enumerate(
        (
            "order_submission",
            "broker_acknowledgement",
            "fill_management",
            "ledger_booking",
            "reconciliation",
        ),
        start=1,
    ):
        extra: dict[str, Any] = {}
        if stage == "reconciliation":
            extra = {
                "source_unavailable": True,
                "unavailable_sources": ["research_archive"],
                "source_status": "partial",
            }
        events.append(
            _event(
                "tj-scenario-11",
                sequence_no,
                stage,
                "succeeded",
                f"2026-07-12T11:{10 + sequence_no:02d}:00Z",
                **extra,
            )
        )
    events.extend(
        [
            _event(
                "tj-scenario-12",
                1,
                "trade_decision",
                "succeeded",
                "2026-07-12T12:01:00Z",
                persona_version="persona-v1",
                strategy_version="strategy-v1",
                policy_version="policy-v1",
                binding_version="binding-v1",
                artifact_version="artifact-v1",
            ),
            _event(
                "tj-scenario-12",
                2,
                "trade_decision",
                "succeeded",
                "2026-07-12T12:02:00Z",
                persona_version="persona-v2",
                strategy_version="strategy-v2",
                policy_version="policy-v2",
                binding_version="binding-v2",
                artifact_version="artifact-v2",
            ),
        ]
    )
    return events


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SeedError(f"{name} is required")
    return value


def _origin(value: str) -> str:
    parts = urllib.parse.urlsplit(value.rstrip("/"))
    return f"{parts.scheme}://{parts.netloc}"


def _require_loopback_origin(name: str, allowed: frozenset[str]) -> str:
    value = _required_env(name).rstrip("/")
    if _origin(value) not in allowed:
        raise SeedError(f"{name} is outside the dev VM loopback allowlist")
    return value


def _read_json(
    url: str,
    *,
    token: str | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
            return int(response.status), json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            parsed: Any = json.loads(payload) if payload else None
        except json.JSONDecodeError:
            parsed = {"error": "non-json response"}
        return int(exc.code), parsed


def _request_json(
    url: str,
    *,
    body: Any,
    token: str | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        data=json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
            return int(response.status), json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            parsed: Any = json.loads(payload) if payload else None
        except json.JSONDecodeError:
            parsed = {"error": "non-json response"}
        return int(exc.code), parsed


def _parse_time(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise SeedError("active RuntimeBinding has an invalid effective_at") from exc
    if parsed.tzinfo is None:
        raise SeedError("active RuntimeBinding effective_at must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _active_paper_binding(runtime_manager_base: str) -> dict[str, Any]:
    status, payload = _read_json(
        f"{runtime_manager_base}/api/runtime-fleet/desired-state?stage=paper",
        token=os.getenv("TJ_E2E_RUNTIME_MANAGER_TOKEN", "runtime-control-internal"),
    )
    bindings = payload.get("bindings") if isinstance(payload, Mapping) else None
    if status != 200 or not isinstance(bindings, list):
        raise SeedError(f"runtime-manager desired-state failed with HTTP {status}")
    for raw in bindings:
        if not isinstance(raw, Mapping):
            continue
        binding = dict(raw)
        metadata = binding.get("metadata") if isinstance(binding.get("metadata"), Mapping) else {}
        stage = str(
            binding.get("deployment_mode")
            or binding.get("deployment_stage")
            or binding.get("environment")
            or metadata.get("environment")
            or ""
        ).lower()
        if str(binding.get("status") or "").lower() != "active" or stage != "paper":
            continue
        if all(binding.get(field) not in (None, "") for field in REQUIRED_BINDING_FIELDS):
            return binding
    raise SeedError("no identity-complete active paper RuntimeBinding is available")


def _uuid_for(binding_id: str, value: str) -> str:
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, f"pantheon://{SEED_SOURCE}/{binding_id}")
    return str(uuid.uuid5(namespace, value))


def build_telemetry_fixtures(
    binding: Mapping[str, Any],
    events: list[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Wrap source scenarios in binding-valid canonical telemetry envelopes."""

    missing = [field for field in REQUIRED_BINDING_FIELDS if binding.get(field) in (None, "")]
    if missing:
        raise SeedError("RuntimeBinding missing: " + ", ".join(missing))
    binding_id = str(binding["binding_id"])
    effective_at = _parse_time(binding["effective_at"])
    source_events = list(events or build_scenarios())
    previous_by_journey: dict[str, str] = {}
    fixtures: list[dict[str, Any]] = []
    for position, source in enumerate(source_events, start=1):
        journey_id = str(source["journey_id"])
        source_event_id = str(source["event_id"])
        event_id = _uuid_for(binding_id, source_event_id)
        correlation_id = _uuid_for(binding_id, f"{journey_id}:correlation")
        trace_id = _uuid_for(binding_id, f"{journey_id}:trace")
        created_at = (effective_at + timedelta(seconds=position)).isoformat().replace(
            "+00:00", "Z"
        )
        environment = str(source.get("environment") or "paper")
        fixture_payload = {
            key: value
            for key, value in source.items()
            if key
            not in {
                "event_id",
                "journey_id",
                "tenant_id",
                "environment",
                "occurred_at",
                "recorded_at",
                "source",
                "sequence_no",
                "stage",
                "stage_status",
                "schema_version",
            }
        }
        strategy_id = str(
            fixture_payload.get("strategy_id")
            or binding.get("strategy_id")
            or f"strategy-{journey_id}"
        )
        persona_id = str(
            fixture_payload.get("persona_id")
            or binding.get("persona_id")
            or f"persona-{journey_id}"
        )
        envelope = {
            "schema_version": "trade-journey-envelope/1",
            "tenant_id": TENANT_ID,
            "environment": environment,
            "journey_id": journey_id,
            "correlation_id": correlation_id,
            "trace_id": trace_id,
            "event_id": event_id,
            "causation_event_id": previous_by_journey.get(journey_id, event_id),
            "producer": "pantheon.tj-e2e-012.dev-fixture",
            "event_time": str(source["occurred_at"]),
            "received_at": str(source.get("recorded_at") or source["occurred_at"]),
            "producer_revision": int(source["sequence_no"]),
        }
        for key in ("research_journey_id", "strategy_lifecycle_id"):
            if fixture_payload.get(key) not in (None, ""):
                envelope[key] = str(fixture_payload[key])
        fixture = {
            "event_id": event_id,
            "event_type": FIXTURE_EVENT_TYPE,
            "created_at": created_at,
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": binding_id,
            "runtime_id": str(binding["runtime_id"]),
            "capital_pool_id": str(binding["capital_pool_id"]),
            "artifact_id": str(binding["artifact_id"]),
            "artifact_version": str(binding["artifact_version"]),
            "plan_id": str(binding["plan_id"]),
            "persona_capital_binding_id": str(binding["persona_capital_binding_id"]),
            "run_id": f"run-{journey_id}",
            "signal_id": f"signal-{journey_id}",
            "loop_run_id": f"lr-{journey_id}",
            "trace_id": trace_id,
            "sequence_no": int(source["sequence_no"]),
            "source_mode": "live",
            "authority_refs": {
                "write_owner": "tj-e2e-012-dev-fixture",
                "authority_source": "runtime_binding",
                "runtime_role": "hosted_acceptance_fixture",
                "runtime_mode": "paper",
                "persona_id": persona_id,
                "trace_id": trace_id,
            },
            "target": {
                "strategy_id": strategy_id,
                "artifact_version": str(binding["artifact_version"]),
                "promotion_state": "paper",
            },
            "metrics": {"action": str(source["stage"])},
            "metadata": {
                "fixture_schema_version": FIXTURE_SCHEMA_VERSION,
                "fixture_source": SEED_SOURCE,
                "fixture_scope": "dev-only",
                "fixture_stage": str(source["stage"]),
                "fixture_stage_status": str(source["stage_status"]),
                "fixture_occurred_at": str(source["occurred_at"]),
                "fixture_recorded_at": str(source.get("recorded_at") or source["occurred_at"]),
                "fixture_payload": fixture_payload,
                "persona_id": persona_id,
                "strategy_id": strategy_id,
                "run_id": f"run-{journey_id}",
                "signal_id": f"signal-{journey_id}",
                "sequence_no": int(source["sequence_no"]),
            },
            "correlation_envelope": envelope,
        }
        fixtures.append(fixture)
        previous_by_journey[journey_id] = event_id
    return fixtures


def main() -> int:
    try:
        telemetry_base = _require_loopback_origin(
            "TJ_E2E_TELEMETRY_BASE", ALLOWED_TELEMETRY_ORIGINS
        )
        runtime_manager_base = _require_loopback_origin(
            "TJ_E2E_RUNTIME_MANAGER_BASE", ALLOWED_RUNTIME_MANAGER_ORIGINS
        )
        bff_base = _require_loopback_origin("TJ_E2E_SEED_BFF_BASE", ALLOWED_BFF_ORIGINS)
        if os.getenv("GITHUB_REPOSITORY", "") != "ajoe734/pantheon":
            raise SeedError("hosted seed may only run in ajoe734/pantheon")
        if os.getenv("TJ_E2E_TENANT_ID", TENANT_ID) != TENANT_ID:
            raise SeedError("hosted seed is restricted to tenant-dev")
        expected_sha = _required_env("TJ_E2E_EXPECTED_BFF_SHA")
        if re.fullmatch(r"[0-9a-f]{40}", expected_sha) is None:
            raise SeedError("TJ_E2E_EXPECTED_BFF_SHA must be a lowercase 40-character SHA")
        version_status, version = _read_json(f"{bff_base}/bff/version")
        observed_sha = version.get("source_commit_sha") if isinstance(version, Mapping) else None
        if version_status != 200 or observed_sha != expected_sha:
            raise SeedError("loopback BFF does not match the requested acceptance SHA")

        binding = _active_paper_binding(runtime_manager_base)
        events = build_scenarios()
        fixtures = build_telemetry_fixtures(binding, events)
        if len({event["event_id"] for event in fixtures}) != len(fixtures):
            raise SeedError("generated telemetry event ids are not unique")
        write_status, write = _request_json(
            f"{telemetry_base}/api/telemetry/ingest/batch",
            body={"events": fixtures},
        )
        if (
            write_status != 202
            or not isinstance(write, Mapping)
            or write.get("ingested") != len(fixtures)
            or write.get("rejected") != 0
        ):
            raise SeedError(f"canonical telemetry publish failed with HTTP {write_status}")

        # The ingest acknowledgement means the batch is accepted into the
        # durable writer.  Give the writer/projector one bounded interval;
        # the public verifier independently retries and proves readback.
        time.sleep(2)

        environments = Counter(event["environment"] for event in events)
        journeys = {event["journey_id"] for event in events}
        print(
            json.dumps(
                {
                    "result": "seeded",
                    "source": SEED_SOURCE,
                    "tenant_id": TENANT_ID,
                    "event_count": len(events),
                    "telemetry_event_count": len(fixtures),
                    "journey_count": len(journeys),
                    "environment_counts": dict(sorted(environments.items())),
                },
                sort_keys=True,
            )
        )
        return 0
    except SeedError as exc:
        message = re.sub(r"(?i)(secret|token)=[^\s]+", r"\1=<redacted>", str(exc))
        print(f"SEED_FAILED: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
