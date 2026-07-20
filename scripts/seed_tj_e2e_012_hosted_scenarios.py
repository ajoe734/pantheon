#!/usr/bin/env python3
"""Materialize the deterministic TJ-E2E-012 source scenarios on dev.

The script is intentionally write-capable and must only be called from an
explicitly authorized dev workflow step.  It authenticates as the dedicated
operator-A identity, posts canonical events through the BFF event-ingestion
contract, and prints only non-sensitive counts.  Event ids and timestamps are
fixed so reruns are idempotent and replay evidence stays reproducible.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any, Mapping


SEED_SOURCE = "tj_e2e_012_hosted_seed_v3"
TENANT_ID = "tenant-dev"
AMBIGUITY_IDENTIFIER = "ambiguous-scenario-9"
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
        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            payload = response.read().decode("utf-8")
            return int(response.status), json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            parsed: Any = json.loads(payload) if payload else None
        except json.JSONDecodeError:
            parsed = {"error": "non-json response"}
        return int(exc.code), parsed


def main() -> int:
    try:
        base = _required_env("BFF_BASE").rstrip("/")
        allowed = _required_env("TJ_E2E_ALLOWED_BFF_ORIGIN").rstrip("/")
        base_parts = urllib.parse.urlsplit(base)
        allowed_parts = urllib.parse.urlsplit(allowed)
        if (
            base_parts.scheme != "https"
            or (base_parts.scheme, base_parts.netloc) != (allowed_parts.scheme, allowed_parts.netloc)
        ):
            raise SeedError("BFF_BASE is outside the allowlisted HTTPS origin")
        if os.getenv("GITHUB_REPOSITORY", "") != "ajoe734/pantheon":
            raise SeedError("hosted seed credentials may only run in ajoe734/pantheon")
        if os.getenv("TJ_E2E_TENANT_ID", TENANT_ID) != TENANT_ID:
            raise SeedError("hosted seed is restricted to tenant-dev")

        login_status, login = _request_json(
            f"{base}/bff/auth/dev-login",
            body={
                "grant_type": "client_credentials",
                "client_id": _required_env("TJ_E2E_OPERATOR_CLIENT_ID"),
                "client_secret": _required_env("TJ_E2E_OPERATOR_CLIENT_SECRET"),
            },
        )
        token = login.get("access_token") if isinstance(login, Mapping) else None
        meta = login.get("meta") if isinstance(login, Mapping) else None
        if login_status != 200 or not isinstance(token, str) or not token:
            raise SeedError(f"operator-A dev-login failed with HTTP {login_status}")
        if not isinstance(meta, Mapping) or meta.get("identity") != "operator_a":
            raise SeedError("dev-login did not issue the dedicated operator-A identity")

        events = build_scenarios()
        if len({event["event_id"] for event in events}) != len(events):
            raise SeedError("generated event ids are not unique")
        write_status, write = _request_json(
            f"{base}/bff/management/trade-journeys/events",
            body=events,
            token=token,
        )
        if write_status != 200:
            code = None
            if isinstance(write, Mapping):
                error = write.get("error")
                code = error.get("code") if isinstance(error, Mapping) else None
            raise SeedError(f"canonical event publish failed with HTTP {write_status} ({code or 'unknown'})")
        if not isinstance(write, Mapping) or write.get("status") != "ok" or write.get("count") != len(events):
            raise SeedError("canonical event publish returned an unexpected acknowledgement")

        environments = Counter(event["environment"] for event in events)
        journeys = {event["journey_id"] for event in events}
        print(
            json.dumps(
                {
                    "result": "seeded",
                    "source": SEED_SOURCE,
                    "tenant_id": TENANT_ID,
                    "event_count": len(events),
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
