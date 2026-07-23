#!/usr/bin/env python3
"""Fail-closed hosted verifier for the twelve Trade Journey scenarios.

The verifier is intentionally unable to use the historical fixed bearer
tokens.  It exchanges two dedicated dev-only client credentials for
short-lived tokens, verifies the exact frontend/backend deployment pair, and
writes a redacted artifact for every hosted request.  A summary-row match is
not sufficient: each scenario is checked against detail, timeline, evidence,
and (where applicable) resolve/replay responses.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


TASK_ID = "TJ-E2E-012"
REDACTED = "<redacted>"
SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "client_secret",
    "access_token",
    "refresh_token",
    "id_token",
    "password",
    "passphrase",
    "private_key",
    "credential",
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
EXECUTION_STAGES = frozenset(OBSERVABLE_STAGES)
ORDER_AND_LATER_STAGES = frozenset(
    ("order_submission", "broker_acknowledgement", "fill_management", "ledger_booking", "reconciliation")
)
POST_FILL_STAGES = frozenset(("fill_management", "ledger_booking", "reconciliation"))
RESOLVE_IDENTIFIER_FIELDS = (
    "persona_id",
    "strategy_id",
    "decision_id",
    "client_order_id",
    "broker_order_id",
    "fill_id",
)
LIVE_SENSITIVE_FIELDS = (
    "account_id",
    "capital_account_id",
    "order_id",
    "client_order_id",
    "broker_order_id",
    "quantity",
    "price",
)
VERSION_FIELDS = (
    "persona_version",
    "strategy_version",
    "policy_version",
    "binding_version",
    "artifact_version",
)
LEDGER_SOURCE_ID_FIELDS = (
    "journey_id",
    "research_journey_id",
    "strategy_lifecycle_id",
    "persona_id",
    "strategy_id",
    "candidate_id",
    "decision_id",
    "risk_decision_id",
    "capital_pool_id",
    "runtime_id",
    "signal_id",
    "order_intent_id",
    "client_order_id",
    "broker_order_id",
    "fill_id",
    "fill_ids",
    "reconciliation_id",
    "correlation_id",
    "event_id",
)
PERFORMANCE_DETAIL_SCENARIOS = (1, 4, 7, 11)
PERFORMANCE_SAMPLE_COUNT = 20


class VerificationError(RuntimeError):
    """A required hosted acceptance property could not be proven."""

    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "details": self.details}


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise VerificationError("CONFIG_MISSING", f"{name} is required")
    return value


@dataclasses.dataclass(frozen=True)
class Config:
    bff_base_url: str
    fe_deployment_url: str
    allowed_bff_origin: str
    allowed_fe_origin: str
    tenant_id: str
    forbidden_tenant_id: str
    expected_bff_sha: str
    expected_fe_sha: str
    operator_client_id: str
    operator_client_secret: str
    viewer_client_id: str
    viewer_client_secret: str
    evidence_dir: Path
    github_server_url: str
    github_repository: str
    github_run_id: str
    github_run_attempt: str
    replay_as_of: str
    ambiguity_identifier: str
    timeout_seconds: float = 20.0
    projection_wait_seconds: float = 60.0

    @property
    def run_url(self) -> str:
        return (
            f"{self.github_server_url.rstrip('/')}/{self.github_repository}"
            f"/actions/runs/{self.github_run_id}"
        )

    @classmethod
    def from_env(cls) -> "Config":
        tenant_id = _required_env("TJ_E2E_TENANT_ID")
        forbidden_tenant_id = _required_env("TJ_E2E_FORBIDDEN_TENANT_ID")
        if tenant_id == forbidden_tenant_id:
            raise VerificationError(
                "CONFIG_INVALID",
                "TJ_E2E_FORBIDDEN_TENANT_ID must differ from TJ_E2E_TENANT_ID",
            )
        bff_base_url = _required_env("BFF_BASE").rstrip("/")
        fe_deployment_url = _required_env("TJ_E2E_FE_DEPLOYMENT_URL")
        allowed_bff_origin = _required_env("TJ_E2E_ALLOWED_BFF_ORIGIN").rstrip("/")
        allowed_fe_origin = _required_env("TJ_E2E_ALLOWED_FE_ORIGIN").rstrip("/")
        expected_bff_sha = _required_env("TJ_E2E_EXPECTED_BFF_SHA")
        expected_fe_sha = _required_env("TJ_E2E_EXPECTED_FE_SHA")
        github_repository = _required_env("GITHUB_REPOSITORY")
        for label, sha in (
            ("TJ_E2E_EXPECTED_BFF_SHA", expected_bff_sha),
            ("TJ_E2E_EXPECTED_FE_SHA", expected_fe_sha),
        ):
            if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
                raise VerificationError("CONFIG_INVALID", f"{label} must be a lowercase 40-character commit SHA")
        if github_repository != "ajoe734/pantheon":
            raise VerificationError(
                "CONFIG_INVALID",
                "hosted acceptance credentials may only run in ajoe734/pantheon",
                details={"repository": github_repository},
            )
        for label, actual_url, allowed_origin in (
            ("BFF_BASE", bff_base_url, allowed_bff_origin),
            ("TJ_E2E_FE_DEPLOYMENT_URL", fe_deployment_url, allowed_fe_origin),
        ):
            actual = urllib.parse.urlsplit(actual_url)
            allowed = urllib.parse.urlsplit(allowed_origin)
            if (
                actual.scheme != "https"
                or allowed.scheme != "https"
                or not actual.netloc
                or (actual.scheme, actual.netloc) != (allowed.scheme, allowed.netloc)
            ):
                raise VerificationError(
                    "CONFIG_INVALID",
                    f"{label} is outside its allowlisted HTTPS origin",
                    details={"url": actual_url, "allowed_origin": allowed_origin},
                )
        return cls(
            bff_base_url=bff_base_url,
            fe_deployment_url=fe_deployment_url,
            allowed_bff_origin=allowed_bff_origin,
            allowed_fe_origin=allowed_fe_origin,
            tenant_id=tenant_id,
            forbidden_tenant_id=forbidden_tenant_id,
            expected_bff_sha=expected_bff_sha,
            expected_fe_sha=expected_fe_sha,
            operator_client_id=_required_env("TJ_E2E_OPERATOR_CLIENT_ID"),
            operator_client_secret=_required_env("TJ_E2E_OPERATOR_CLIENT_SECRET"),
            viewer_client_id=_required_env("TJ_E2E_VIEWER_CLIENT_ID"),
            viewer_client_secret=_required_env("TJ_E2E_VIEWER_CLIENT_SECRET"),
            evidence_dir=Path(_required_env("TJ_E2E_EVIDENCE_DIR")),
            github_server_url=_required_env("GITHUB_SERVER_URL"),
            github_repository=github_repository,
            github_run_id=_required_env("GITHUB_RUN_ID"),
            github_run_attempt=_required_env("GITHUB_RUN_ATTEMPT"),
            replay_as_of=_required_env("TJ_E2E_REPLAY_AS_OF"),
            ambiguity_identifier=_required_env("TJ_E2E_AMBIGUITY_IDENTIFIER"),
            timeout_seconds=float(os.getenv("TJ_E2E_HTTP_TIMEOUT_SECONDS", "20")),
            projection_wait_seconds=float(
                os.getenv("TJ_E2E_PROJECTION_WAIT_SECONDS", "60")
            ),
        )


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def redact(value: Any, secrets: Iterable[str] = ()) -> Any:
    secret_values = tuple(secret for secret in secrets if secret)
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if _is_sensitive_key(str(key)) else redact(item, secret_values)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, secret_values) for item in value]
    if isinstance(value, tuple):
        return [redact(item, secret_values) for item in value]
    if isinstance(value, str):
        redacted = value
        for secret in secret_values:
            redacted = redacted.replace(secret, REDACTED)
        if redacted.lower().startswith("bearer "):
            return "Bearer <redacted>"
        return redacted
    return value


def _safe_name(label: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", label).strip("-") or "call"


class EvidenceRecorder:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.calls: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []
        self.scenarios: dict[int, dict[str, Any]] = {}
        self.axes: list[dict[str, Any]] = []
        self.secrets = {
            config.operator_client_secret,
            config.viewer_client_secret,
        }

    def add_secret(self, value: str | None) -> None:
        if value:
            self.secrets.add(value)

    def call(self, label: str, payload: Mapping[str, Any]) -> None:
        self.calls.append({"label": label, "payload": redact(dict(payload), self.secrets)})

    def check(self, name: str, passed: bool, details: Mapping[str, Any] | None = None) -> None:
        self.checks.append(
            {
                "name": name,
                "passed": bool(passed),
                "details": redact(dict(details or {}), self.secrets),
            }
        )

    def require(
        self,
        name: str,
        condition: bool,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.check(name, condition, details)
        if not condition:
            raise VerificationError(name, message, details=details)

    def record_scenario(
        self,
        number: int,
        *,
        journey_id: str,
        actor_identity: str,
        actor_role: str,
        tenant_id: str,
        source_ids: Mapping[str, Any],
        journey_status: str,
        current_stage: str,
        reconciliation: Mapping[str, Any],
        evidence_labels: Sequence[str],
        target_tenants: Sequence[str] = (),
    ) -> None:
        self.scenarios[number] = {
            "scenario_id": f"TJ-E2E-012-S{number:02d}",
            "scenario_number": number,
            "journey_id": journey_id,
            "actor": {"identity": actor_identity, "role": actor_role},
            "tenant_id": tenant_id,
            "target_tenants": list(target_tenants),
            "source_ids": redact(dict(source_ids), self.secrets),
            "terminal_state": {
                "journey_status": journey_status or "unknown",
                "current_stage": current_stage or "unknown",
            },
            "reconciliation_result": redact(dict(reconciliation), self.secrets),
            "evidence_labels": list(dict.fromkeys(evidence_labels)),
            "result": "pending",
        }

    def add_scenario_evidence(self, number: int, *labels: str) -> None:
        row = self.scenarios.get(number)
        if row is None:
            return
        row["evidence_labels"] = list(
            dict.fromkeys([*row.get("evidence_labels", []), *labels])
        )

    def mark_scenario(self, number: int, result: str, error: Mapping[str, Any] | None = None) -> None:
        row = self.scenarios.get(number)
        if row is None:
            return
        row["result"] = result
        if error is not None:
            row["error"] = redact(dict(error), self.secrets)

    def record_axis(
        self,
        name: str,
        *,
        scenario_numbers: Sequence[int],
        evidence_labels: Sequence[str],
        passed: bool,
        details: Mapping[str, Any],
    ) -> None:
        self.axes.append(
            {
                "name": name,
                "scenario_ids": [f"TJ-E2E-012-S{number:02d}" for number in scenario_numbers],
                "evidence_labels": list(dict.fromkeys(evidence_labels)),
                "passed": bool(passed),
                "details": redact(dict(details), self.secrets),
            }
        )

    def write(self, *, passed: bool, error: VerificationError | None = None) -> dict[str, Any]:
        root = self.config.evidence_dir
        calls_dir = root / "calls"
        calls_dir.mkdir(parents=True, exist_ok=True)
        call_index: list[dict[str, Any]] = []
        for position, call in enumerate(self.calls, start=1):
            filename = f"{position:03d}-{_safe_name(call['label'])}.json"
            path = calls_dir / filename
            payload = call["payload"]
            encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            path.write_text(encoded, encoding="utf-8")
            call_index.append(
                {
                    "label": call["label"],
                    "path": f"calls/{filename}",
                    "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                }
            )

        call_by_label = {item["label"]: item for item in call_index}
        ledger_rows: list[dict[str, Any]] = []
        for number, recorded in sorted(self.scenarios.items()):
            row = dict(recorded)
            labels = row.pop("evidence_labels", [])
            evidence_refs = [call_by_label[label] for label in labels if label in call_by_label]
            row["request_response_or_sse_evidence"] = evidence_refs
            row["evidence_digest_sha256"] = sha256_json(
                {
                    "scenario_id": row["scenario_id"],
                    "evidence": [
                        {"label": item["label"], "sha256": item["sha256"]}
                        for item in evidence_refs
                    ],
                }
            )
            ledger_rows.append(row)
        ledger = {
            "schema_version": "pantheon.tj-e2e-012.scenario-ledger.v1",
            "task_id": TASK_ID,
            "run_url": self.config.run_url,
            "deployment": {
                "frontend_sha": self.config.expected_fe_sha,
                "bff_sha": self.config.expected_bff_sha,
            },
            "rows": ledger_rows,
        }
        ledger["ledger_sha256"] = sha256_json(ledger)
        ledger_path = root / "scenario-ledger.json"
        ledger_encoded = json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ledger_path.write_text(ledger_encoded, encoding="utf-8")

        axis_rows: list[dict[str, Any]] = []
        for recorded in self.axes:
            axis = dict(recorded)
            labels = axis.pop("evidence_labels", [])
            axis["evidence"] = [call_by_label[label] for label in labels if label in call_by_label]
            axis_rows.append(axis)
        axis_mapping = {
            "schema_version": "pantheon.tj-e2e-012.axis-mapping.v1",
            "task_id": TASK_ID,
            "run_url": self.config.run_url,
            "axes": axis_rows,
        }
        axis_mapping["mapping_sha256"] = sha256_json(axis_mapping)
        axis_path = root / "axis-mapping.json"
        axis_encoded = json.dumps(axis_mapping, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        axis_path.write_text(axis_encoded, encoding="utf-8")

        summary = {
            "schema_version": "pantheon.tj-e2e-012.hosted-evidence.v1",
            "task_id": TASK_ID,
            "result": "passed" if passed else "failed",
            "error": redact(error.as_dict(), self.secrets) if error else None,
            "run": {
                "url": self.config.run_url,
                "repository": self.config.github_repository,
                "run_id": self.config.github_run_id,
                "run_attempt": self.config.github_run_attempt,
            },
            "deployment": {
                "frontend_deployment_url": self.config.fe_deployment_url,
                "expected_frontend_sha": self.config.expected_fe_sha,
                "bff_base_url": self.config.bff_base_url,
                "expected_bff_sha": self.config.expected_bff_sha,
            },
            "scope": {
                "tenant_id": self.config.tenant_id,
                "forbidden_tenant_id": self.config.forbidden_tenant_id,
            },
            "checks": self.checks,
            "calls": call_index,
            "scenario_ledger": {
                "path": "scenario-ledger.json",
                "row_count": len(ledger_rows),
                "sha256": hashlib.sha256(ledger_encoded.encode("utf-8")).hexdigest(),
                "ledger_sha256": ledger["ledger_sha256"],
            },
            "axis_mapping": {
                "path": "axis-mapping.json",
                "axis_count": len(axis_rows),
                "sha256": hashlib.sha256(axis_encoded.encode("utf-8")).hexdigest(),
                "mapping_sha256": axis_mapping["mapping_sha256"],
            },
        }
        summary["manifest_sha256"] = sha256_json(summary)
        manifest_path = root / "evidence.json"
        encoded = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        manifest_path.write_text(encoded, encoding="utf-8")
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        (root / "evidence.sha256").write_text(f"{digest}  evidence.json\n", encoding="utf-8")
        return summary


def request_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: Mapping[str, Any] | None = None,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    started = time.perf_counter()

    def measured(payload: dict[str, Any]) -> dict[str, Any]:
        payload["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return payload

    headers = {"Accept": "application/json", "User-Agent": "pantheon-tj-e2e-012/1"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = canonical_json(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout_seconds,
            context=ssl.create_default_context(),
        ) as response:
            raw = response.read().decode("utf-8")
            return measured({
                "status": response.status,
                "headers": {
                    key.lower(): value
                    for key, value in response.headers.items()
                    if key.lower() in {"content-type", "date", "x-request-id", "x-correlation-id"}
                },
                "json": json.loads(raw) if raw else None,
            })
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            response_json = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            response_json = {"unparsed_body": raw[:1000]}
        return measured({"status": exc.code, "headers": {}, "json": response_json})
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return measured(
            {"status": None, "headers": {}, "json": None, "transport_error": str(exc)}
        )


def request_sse(
    url: str,
    *,
    token: str,
    last_event_id: str | None = None,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    started = time.perf_counter()
    headers = {
        "Accept": "text/event-stream",
        "Authorization": f"Bearer {token}",
        "Cache-Control": "no-cache",
        "User-Agent": "pantheon-tj-e2e-012/1",
    }
    if last_event_id is not None:
        headers["Last-Event-ID"] = last_event_id
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout_seconds,
            context=ssl.create_default_context(),
        ) as response:
            raw = response.read(65536).decode("utf-8", errors="replace")
            return {
                "status": response.status,
                "headers": {
                    key.lower(): value
                    for key, value in response.headers.items()
                    if key.lower() in {"content-type", "cache-control", "x-accel-buffering"}
                },
                "body": raw,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            }
    except urllib.error.HTTPError as exc:
        return {
            "status": exc.code,
            "headers": {},
            "body": exc.read().decode("utf-8", errors="replace")[:2000],
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    except (OSError, urllib.error.URLError) as exc:
        return {
            "status": None,
            "headers": {},
            "body": "",
            "transport_error": str(exc),
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }


Transport = Callable[..., dict[str, Any]]


def _query(path: str, **params: str) -> str:
    return f"{path}?{urllib.parse.urlencode(params)}"


def _data(response: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = response.get("json")
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), Mapping):
        return {}
    return payload["data"]


def _items(response: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    data = _data(response)
    items = data.get("items")
    return [item for item in items if isinstance(item, Mapping)] if isinstance(items, list) else []


def _values_for_key(value: Any, keys: set[str]) -> list[Any]:
    values: list[Any] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in keys and item not in (None, "", [], {}):
                values.append(item)
            values.extend(_values_for_key(item, keys))
    elif isinstance(value, list):
        for item in value:
            values.extend(_values_for_key(item, keys))
    return values


def _nonempty(value: Any) -> bool:
    return value not in (None, "", [], {})


def _stage_event(detail: Mapping[str, Any], stage: str) -> Mapping[str, Any]:
    stage_events = detail.get("stage_events")
    event = stage_events.get(stage) if isinstance(stage_events, Mapping) else None
    return event if isinstance(event, Mapping) else {}


def _stage_names(detail: Mapping[str, Any]) -> set[str]:
    stages = detail.get("stages")
    return {str(key) for key in stages} if isinstance(stages, Mapping) else set()


def _event_status(detail: Mapping[str, Any], stage: str) -> str:
    event = _stage_event(detail, stage)
    return str(event.get("stage_status") or event.get("status") or "")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _flatten_scalar_values(values: Iterable[Any]) -> list[str]:
    flattened: list[str] = []
    for value in values:
        candidates = value if isinstance(value, (list, tuple, set)) else (value,)
        for candidate in candidates:
            if isinstance(candidate, (str, int, float)) and str(candidate):
                flattened.append(str(candidate))
    return sorted(set(flattened))


def _source_ids(value: Any) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for field in LEDGER_SOURCE_ID_FIELDS:
        values = _flatten_scalar_values(_values_for_key(value, {field}))
        if values:
            result[field] = values
    return result


def _parse_sse_frame(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str):
        return {}
    frame: dict[str, Any] = {}
    data_lines: list[str] = []
    for line in raw.splitlines():
        if not line or line.startswith(":") or ":" not in line:
            continue
        field, value = line.split(":", 1)
        value = value.lstrip()
        if field == "data":
            data_lines.append(value)
        elif field in {"id", "event"}:
            frame[field] = value
    if data_lines:
        encoded = "\n".join(data_lines)
        try:
            frame["data"] = json.loads(encoded)
        except json.JSONDecodeError:
            frame["data"] = encoded
    return frame


def _percentile_95(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("at least one performance sample is required")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


@dataclasses.dataclass(frozen=True)
class ScenarioBundle:
    detail: Mapping[str, Any]
    timeline: Sequence[Mapping[str, Any]]
    evidence: Mapping[str, Any]
    detail_meta: Mapping[str, Any]


class HostedVerifier:
    def __init__(
        self,
        config: Config,
        recorder: EvidenceRecorder,
        *,
        transport: Transport = request_json,
    ) -> None:
        self.config = config
        self.recorder = recorder
        self.transport = transport
        self.operator_token = ""
        self.viewer_token = ""
        self.scenario_bundles: dict[int, ScenarioBundle] = {}

    def _call(
        self,
        label: str,
        method: str,
        url: str,
        *,
        token: str | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self.transport(
            method,
            url,
            token=token,
            body=body,
            timeout_seconds=self.config.timeout_seconds,
        )
        self.recorder.call(
            label,
            {
                "request": {"method": method, "url": url, "body": body, "authorization": token},
                "response": result,
            },
        )
        return result

    def _bff_call(
        self,
        label: str,
        path: str,
        *,
        token: str | None = None,
        method: str = "GET",
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._call(
            label,
            method,
            f"{self.config.bff_base_url}{path}",
            token=token,
            body=body,
        )

    def _bff_sse_call(
        self,
        label: str,
        path: str,
        *,
        last_event_id: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self.config.bff_base_url}{path}"
        result = request_sse(
            url,
            token=self.operator_token,
            last_event_id=last_event_id,
            timeout_seconds=self.config.timeout_seconds,
        )
        self.recorder.call(
            label,
            {
                "request": {
                    "method": "GET",
                    "url": url,
                    "authorization": self.operator_token,
                    "accept": "text/event-stream",
                    "last_event_id": last_event_id,
                },
                "response": result,
            },
        )
        return result

    def _require_status(self, label: str, response: Mapping[str, Any], expected: int = 200) -> None:
        self.recorder.require(
            f"{label}.http_{expected}",
            response.get("status") == expected,
            f"{label} returned HTTP {response.get('status')}, expected {expected}",
            details={"status": response.get("status"), "response": response.get("json")},
        )

    def _login(self, identity: str, client_id: str, client_secret: str) -> str:
        response = self._bff_call(
            f"auth-{identity}",
            "/bff/auth/dev-login",
            method="POST",
            body={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        self._require_status(f"auth-{identity}", response)
        payload = response.get("json")
        token = payload.get("access_token") if isinstance(payload, Mapping) else None
        meta = payload.get("meta") if isinstance(payload, Mapping) else None
        self.recorder.require(
            f"auth-{identity}.server_bound_identity",
            isinstance(token, str)
            and bool(token)
            and isinstance(meta, Mapping)
            and meta.get("identity") == identity,
            f"dev-login did not issue the expected {identity} identity",
            details={"meta": meta},
        )
        self.recorder.add_secret(token)
        return str(token)

    def verify_deployment(self) -> None:
        version = self._bff_call("deployment-bff-version", "/bff/version")
        self._require_status("deployment-bff-version", version)
        version_json = version.get("json") if isinstance(version.get("json"), Mapping) else {}
        posture = version_json.get("config_posture")
        posture = posture if isinstance(posture, Mapping) else version_json
        self.recorder.require(
            "deployment.bff_exact_sha",
            version_json.get("source_commit_sha") == self.config.expected_bff_sha,
            "hosted BFF SHA does not match the requested acceptance SHA",
            details={
                "expected": self.config.expected_bff_sha,
                "observed": version_json.get("source_commit_sha"),
            },
        )
        self.recorder.require(
            "deployment.strict_auth",
            posture.get("auth_mode") == "strict" and posture.get("auth_stub") is False,
            "hosted BFF is not strict-auth with stub auth disabled",
            details={"config_posture": posture},
        )

        fe = self._call("deployment-fe-manifest", "GET", self.config.fe_deployment_url)
        self._require_status("deployment-fe-manifest", fe)
        manifest = fe.get("json") if isinstance(fe.get("json"), Mapping) else {}
        observed_fe_sha = manifest.get("commit") or manifest.get("sourceRef") or manifest.get("source_sha")
        build_mode = manifest.get("buildMode") or manifest.get("build_mode") or {}
        self.recorder.require(
            "deployment.fe_exact_sha",
            observed_fe_sha == self.config.expected_fe_sha,
            "hosted frontend SHA does not match the requested acceptance SHA",
            details={"expected": self.config.expected_fe_sha, "observed": observed_fe_sha},
        )
        self.recorder.require(
            "deployment.fe_strict_live_mode",
            isinstance(build_mode, Mapping)
            and build_mode.get("VITE_BFF_MODE") == "live"
            and build_mode.get("VITE_BFF_FALLBACK") == "strict",
            "hosted frontend is not built in strict live-BFF mode",
            details={"build_mode": build_mode},
        )
        manifest_bff = str(manifest.get("bffHost") or manifest.get("bff_host") or "").rstrip("/")
        self.recorder.require(
            "deployment.fe_bff_host_binding",
            manifest_bff == self.config.bff_base_url,
            "frontend deployment manifest points at a different BFF",
            details={"manifest_bff": manifest_bff, "expected_bff": self.config.bff_base_url},
        )
        manifest_bff_sha = (
            manifest.get("bffCommit")
            or manifest.get("bffSourceCommitSha")
            or manifest.get("bff_commit")
        )
        nested_bff = manifest.get("bff")
        if not manifest_bff_sha and isinstance(nested_bff, Mapping):
            manifest_bff_sha = nested_bff.get("sourceCommitSha") or nested_bff.get("commitSha")
        self.recorder.require(
            "deployment.fe_bff_exact_sha",
            manifest_bff_sha == self.config.expected_bff_sha,
            "frontend deployment manifest is not bound to the accepted hosted BFF SHA",
            details={"expected": self.config.expected_bff_sha, "observed": manifest_bff_sha},
        )
        self.recorder.require(
            "deployment.fe_accepted_safe_defaults",
            manifest.get("deploymentState") == "accepted"
            and str(build_mode.get("VITE_BFF_REAL_WRITES", "false")).lower() == "false"
            and str(build_mode.get("VITE_BFF_ALLOW_DEV_STUB_WRITES", "false")).lower() == "false",
            "frontend deployment is not an accepted release with safe write defaults",
            details={
                "deployment_state": manifest.get("deploymentState"),
                "build_mode": build_mode,
            },
        )

    def authenticate(self) -> None:
        self.operator_token = self._login(
            "operator_a",
            self.config.operator_client_id,
            self.config.operator_client_secret,
        )
        self.viewer_token = self._login(
            "viewer",
            self.config.viewer_client_id,
            self.config.viewer_client_secret,
        )
        self.recorder.require(
            "auth.distinct_tokens",
            self.operator_token != self.viewer_token,
            "operator and viewer dev-login exchanges returned the same token",
        )

    def wait_for_seed_projection(self) -> None:
        path = _query(
            "/bff/management/trade-journeys/tj-scenario-1",
            tenant_id=self.config.tenant_id,
            environment="paper",
        )
        deadline = time.monotonic() + max(0.0, self.config.projection_wait_seconds)
        attempt = 0
        response: Mapping[str, Any] = {}
        while True:
            attempt += 1
            response = self._bff_call(
                f"projection-ready-{attempt:02d}",
                path,
                token=self.operator_token,
            )
            if response.get("status") == 200:
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))
        self.recorder.require(
            "deployment.seed_projection_ready",
            response.get("status") == 200,
            "canonical telemetry fixtures were not visible through the hosted projector before timeout",
            details={"attempts": attempt, "status": response.get("status")},
        )

    def _bundle(self, number: int, journey_id: str, *, environment: str = "paper") -> ScenarioBundle:
        params = {"tenant_id": self.config.tenant_id, "environment": environment}
        detail_response = self._bff_call(
            f"scenario-{number:02d}-detail",
            _query(f"/bff/management/trade-journeys/{journey_id}", **params),
            token=self.operator_token,
        )
        timeline_response = self._bff_call(
            f"scenario-{number:02d}-timeline",
            _query(f"/bff/management/trade-journeys/{journey_id}/timeline", page_size="200", **params),
            token=self.operator_token,
        )
        evidence_response = self._bff_call(
            f"scenario-{number:02d}-evidence",
            _query(f"/bff/management/trade-journeys/{journey_id}/evidence", **params),
            token=self.operator_token,
        )
        for label, response in (
            (f"scenario-{number:02d}-detail", detail_response),
            (f"scenario-{number:02d}-timeline", timeline_response),
            (f"scenario-{number:02d}-evidence", evidence_response),
        ):
            self._require_status(label, response)
        detail = _data(detail_response)
        timeline = _items(timeline_response)
        evidence = _data(evidence_response)
        detail_payload = detail_response.get("json")
        meta = detail_payload.get("meta", {}) if isinstance(detail_payload, Mapping) else {}
        self.recorder.require(
            f"scenario-{number:02d}.nonempty_bundle",
            detail.get("journey_id") == journey_id
            and bool(timeline)
            and evidence.get("journey_id") == journey_id,
            f"scenario {number} is missing detail, timeline, or evidence data",
            details={
                "journey_id": detail.get("journey_id"),
                "timeline_count": len(timeline),
                "evidence_journey_id": evidence.get("journey_id"),
            },
        )
        bundle = ScenarioBundle(detail=detail, timeline=timeline, evidence=evidence, detail_meta=meta)
        self.scenario_bundles[number] = bundle
        reconciliation = _stage_event(detail, "reconciliation")
        reconciliation_status = _event_status(detail, "reconciliation") or "not_applicable"
        self.recorder.record_scenario(
            number,
            journey_id=journey_id,
            actor_identity="operator_a",
            actor_role="operator",
            tenant_id=self.config.tenant_id,
            source_ids=_source_ids({"detail": detail, "timeline": list(timeline)}),
            journey_status=str(detail.get("status") or "unknown"),
            current_stage=str(detail.get("current_stage") or "unknown"),
            reconciliation={
                "status": reconciliation_status,
                "variance_present": bool(
                    _values_for_key(reconciliation, {"delta", "variance", "difference", "mismatch_amount"})
                ),
                "source_present": bool(
                    _values_for_key(reconciliation, {"source", "source_ref", "source_refs"})
                ),
                "remediation_present": bool(
                    _values_for_key(reconciliation, {"remediation", "remediation_ref", "next_action"})
                ),
            },
            evidence_labels=(
                f"scenario-{number:02d}-detail",
                f"scenario-{number:02d}-timeline",
                f"scenario-{number:02d}-evidence",
            ),
        )
        return bundle

    def verify_scenario_1(self) -> None:
        bundle = self._bundle(1, "tj-scenario-1")
        detail = bundle.detail
        stages = _stage_names(detail)
        identifiers = detail.get("identifiers") if isinstance(detail.get("identifiers"), Mapping) else {}
        successful = all(_event_status(detail, stage) == "succeeded" for stage in OBSERVABLE_STAGES)
        evidence_by_stage = bundle.evidence.get("by_stage")
        evidence_by_stage = evidence_by_stage if isinstance(evidence_by_stage, Mapping) else {}
        self.recorder.require(
            "scenario-01.paper_happy_path",
            detail.get("status") == "completed"
            and set(OBSERVABLE_STAGES).issubset(stages)
            and successful
            and _nonempty(identifiers.get("research_journey_id"))
            and _nonempty(identifiers.get("strategy_lifecycle_id"))
            and all(
                isinstance(evidence_by_stage.get(stage), Mapping)
                and _nonempty(evidence_by_stage[stage].get("event_ids"))
                for stage in OBSERVABLE_STAGES
            ),
            "scenario 1 does not prove the full paper research-to-reconciliation chain",
            details={"status": detail.get("status"), "stages": sorted(stages), "identifiers": identifiers},
        )

    def verify_scenario_2(self) -> None:
        bundle = self._bundle(2, "tj-scenario-2")
        detail = bundle.detail
        promotion = _stage_event(detail, "promotion_decision")
        downstream = _stage_names(detail) & EXECUTION_STAGES
        self.recorder.require(
            "scenario-02.candidate_rejected_no_execution",
            detail.get("current_stage") == "promotion_decision"
            and str(promotion.get("stage_status") or promotion.get("status")) in {"rejected", "failed"}
            and not downstream
            and bool(_values_for_key(promotion, {"reason_code", "reason", "summary"})),
            "scenario 2 lacks a reasoned promotion rejection or contains execution records",
            details={"current_stage": detail.get("current_stage"), "downstream_stages": sorted(downstream)},
        )

    def verify_scenario_3(self) -> None:
        bundle = self._bundle(3, "tj-scenario-3")
        detail = bundle.detail
        risk = _stage_event(detail, "risk_evaluation")
        downstream = _stage_names(detail) & ORDER_AND_LATER_STAGES
        self.recorder.require(
            "scenario-03.risk_blocked_no_broker",
            _event_status(detail, "risk_evaluation") in {"blocked", "rejected", "failed"}
            and not downstream
            and bool(_values_for_key(risk, {"reason_code", "failing_check", "failed_check", "checks"}))
            and bool(_values_for_key(risk, {"policy_refs", "policy_version"}))
            and bool(_values_for_key(risk, {"input_refs", "input_snapshot", "input_snapshot_ref"})),
            "scenario 3 lacks failing-check/policy/input proof or contains broker-side records",
            details={"risk_event": risk, "downstream_stages": sorted(downstream)},
        )

    def verify_scenario_4(self) -> None:
        bundle = self._bundle(4, "tj-scenario-4")
        detail = bundle.detail
        ack = _stage_event(detail, "broker_acknowledgement")
        post_fill = _stage_names(detail) & POST_FILL_STAGES
        unfilled_values = _values_for_key(
            ack,
            {"filled_quantity", "remaining_quantity", "unfilled_quantity", "order_state"},
        )
        unfilled = any(value in (0, "0", "unfilled", "rejected") for value in unfilled_values)
        self.recorder.require(
            "scenario-04.broker_rejected",
            "order_submission" in _stage_names(detail)
            and _event_status(detail, "broker_acknowledgement") in {"rejected", "failed"}
            and not post_fill
            and bool(_values_for_key(ack, {"reason_code", "reason", "summary"}))
            and bool(_values_for_key(ack, {"incident_id", "incident_ref", "incident_refs"}))
            and unfilled,
            "scenario 4 lacks broker request/reject/reason/incident/unfilled proof",
            details={"broker_acknowledgement": ack, "post_fill_stages": sorted(post_fill)},
        )

    def verify_scenario_5(self) -> None:
        bundle = self._bundle(5, "tj-scenario-5")
        detail = bundle.detail
        payload = {"detail": detail, "timeline": list(bundle.timeline)}
        order_ids = {
            str(value)
            for value in _values_for_key(payload, {"order_id", "client_order_id", "broker_order_id"})
            if isinstance(value, (str, int))
        }
        fill_ids = _values_for_key(payload, {"fill_id", "broker_trade_id"})
        remaining = _values_for_key(payload, {"remaining_quantity", "remaining_qty"})
        event_types = {str(value).lower() for value in _values_for_key(payload, {"event_type", "action"})}
        self.recorder.require(
            "scenario-05.partial_fill_replace_causation",
            detail.get("status") in {"partially_filled", "cancelled"}
            and len(order_ids) >= 2
            and bool(fill_ids)
            and any(isinstance(value, (int, float)) and value > 0 for value in remaining)
            and any("replace" in value or "cancel" in value for value in event_types)
            and bool(_values_for_key(payload, {"causation_id", "replaced_order_id", "parent_order_id"})),
            "scenario 5 lacks order-chain, fill, remaining-quantity, or cancel/replace causation proof",
            details={
                "status": detail.get("status"),
                "order_ids": sorted(order_ids),
                "event_types": sorted(event_types),
            },
        )

    def verify_scenario_6(self) -> None:
        bundle = self._bundle(6, "tj-scenario-6")
        detail = bundle.detail
        waiting = _stage_event(detail, "trade_decision")
        self.recorder.require(
            "scenario-06.human_waiting_context",
            detail.get("status") == "waiting_human"
            and detail.get("flags", {}).get("waiting_human") is True
            and bool(_values_for_key(waiting, {"owner", "owner_id", "owner_role", "assignee"}))
            and bool(_values_for_key(waiting, {"deadline", "due_at", "expires_at"}))
            and bool(_values_for_key(waiting, {"return_url", "journey_context", "human_inbox_ref"})),
            "scenario 6 lacks owner/deadline/Human Inbox return-context proof",
            details={"status": detail.get("status"), "trade_decision": waiting},
        )

    def verify_scenario_7(self) -> None:
        bundle = self._bundle(7, "tj-scenario-7")
        detail = bundle.detail
        reconciliation = _stage_event(detail, "reconciliation")
        self.recorder.require(
            "scenario-07.reconciliation_mismatch_not_completed",
            detail.get("status") == "completed_with_variance"
            and detail.get("status") != "completed"
            and _event_status(detail, "reconciliation") in {"failed", "mismatch"}
            and bool(_values_for_key(reconciliation, {"delta", "variance", "difference", "mismatch_amount"}))
            and bool(_values_for_key(reconciliation, {"source", "source_ref", "source_refs"}))
            and bool(_values_for_key(reconciliation, {"remediation", "remediation_ref", "next_action"})),
            "scenario 7 must remain completed_with_variance and expose delta/source/remediation",
            details={"status": detail.get("status"), "reconciliation": reconciliation},
        )

    def verify_scenario_8(self) -> None:
        bundle = self._bundle(8, "tj-scenario-8")
        occurred = [_parse_time(event.get("occurred_at")) for event in bundle.timeline]
        recorded = [_parse_time(event.get("recorded_at")) for event in bundle.timeline]
        complete_times = all(value is not None for value in occurred + recorded)
        late_pair = False
        if complete_times:
            for left in range(len(bundle.timeline)):
                for right in range(left + 1, len(bundle.timeline)):
                    if occurred[left] < occurred[right] and recorded[left] > recorded[right]:
                        late_pair = True
                        break
                if late_pair:
                    break
        self.recorder.require(
            "scenario-08.late_event_revision_ordering",
            isinstance(bundle.detail.get("revision"), int)
            and bundle.detail.get("revision") >= len(bundle.timeline)
            and complete_times
            and late_pair,
            "scenario 8 lacks a revised snapshot with preserved occurred/recorded late-event ordering",
            details={"revision": bundle.detail.get("revision"), "timeline_count": len(bundle.timeline)},
        )

    def verify_scenario_9(self) -> None:
        bundle = self._bundle(9, "tj-scenario-9")
        resolve_labels: list[str] = []
        for identifier_type in RESOLVE_IDENTIFIER_FIELDS:
            values = _values_for_key(bundle.detail, {identifier_type})
            scalar = next((value for value in values if isinstance(value, (str, int)) and str(value)), None)
            self.recorder.require(
                f"scenario-09.{identifier_type}.present",
                scalar is not None,
                f"scenario 9 does not expose {identifier_type}",
            )
            response = self._bff_call(
                f"scenario-09-resolve-{identifier_type}",
                _query(
                    "/bff/management/trade-journeys/resolve",
                    q=str(scalar),
                    identifier_type=identifier_type,
                    tenant_id=self.config.tenant_id,
                    environment="paper",
                ),
                token=self.operator_token,
            )
            resolve_labels.append(f"scenario-09-resolve-{identifier_type}")
            self._require_status(f"scenario-09-resolve-{identifier_type}", response)
            data = _data(response)
            self.recorder.require(
                f"scenario-09.{identifier_type}.resolves",
                "tj-scenario-9" in (data.get("journey_ids") or []),
                f"{identifier_type} did not resolve to tj-scenario-9",
                details={"data": data},
            )

        ambiguity = self._bff_call(
            "scenario-09-resolve-ambiguity",
            _query(
                "/bff/management/trade-journeys/resolve",
                q=self.config.ambiguity_identifier,
                tenant_id=self.config.tenant_id,
                environment="paper",
            ),
            token=self.operator_token,
        )
        resolve_labels.append("scenario-09-resolve-ambiguity")
        self._require_status("scenario-09-resolve-ambiguity", ambiguity)
        ambiguity_data = _data(ambiguity)
        self.recorder.require(
            "scenario-09.ambiguity_options",
            ambiguity_data.get("ambiguous") is True
            and len(set(ambiguity_data.get("journey_ids") or [])) >= 2,
            "scenario 9 ambiguity probe did not return multiple explicit journey options",
            details={"data": ambiguity_data},
        )
        self.recorder.add_scenario_evidence(9, *resolve_labels)

    def verify_scenario_10(self) -> None:
        own = self._bff_call(
            "scenario-10-viewer-live-list",
            _query(
                "/bff/management/trade-journeys",
                tenant_id=self.config.tenant_id,
                environment="live",
                page_size="200",
            ),
            token=self.viewer_token,
        )
        self._require_status("scenario-10-viewer-live-list", own)
        row = next((item for item in _items(own) if item.get("journey_id") == "tj-scenario-10"), None)
        row_data = row if isinstance(row, Mapping) else {}
        self.recorder.require(
            "scenario-10.viewer_live_masking",
            isinstance(row, Mapping)
            and row.get("live_capital_masked") is True
            and all(row.get(field) in (None, "", "***", REDACTED) for field in LIVE_SENSITIVE_FIELDS),
            "scenario 10 viewer response exposes live account/order existence or capital values",
            details={"row": row},
        )

        foreign_list = self._bff_call(
            "scenario-10-foreign-list",
            _query(
                "/bff/management/trade-journeys",
                tenant_id=self.config.forbidden_tenant_id,
                environment="live",
            ),
            token=self.viewer_token,
        )
        foreign_resolve = self._bff_call(
            "scenario-10-foreign-resolve",
            _query(
                "/bff/management/trade-journeys/resolve",
                q="non-disclosing-order-probe",
                identifier_type="order_id",
                tenant_id=self.config.forbidden_tenant_id,
                environment="live",
            ),
            token=self.viewer_token,
        )
        self.recorder.record_scenario(
            10,
            journey_id="tj-scenario-10",
            actor_identity="viewer",
            actor_role="viewer",
            tenant_id=self.config.tenant_id,
            target_tenants=(self.config.tenant_id, self.config.forbidden_tenant_id),
            source_ids=_source_ids(row_data),
            journey_status=str(row_data.get("status") or "masked_live_row"),
            current_stage=str(row_data.get("current_stage") or "masked"),
            reconciliation={"status": "masked_or_not_applicable"},
            evidence_labels=(
                "scenario-10-viewer-live-list",
                "scenario-10-foreign-list",
                "scenario-10-foreign-resolve",
            ),
        )
        self.recorder.require(
            "scenario-10.cross_tenant_denied",
            foreign_list.get("status") in {403, 404}
            and foreign_resolve.get("status") in {403, 404},
            "scenario 10 cross-tenant list/resolve boundary did not fail closed",
            details={"list_status": foreign_list.get("status"), "resolve_status": foreign_resolve.get("status")},
        )

    def verify_scenario_11(self) -> None:
        bundle = self._bundle(11, "tj-scenario-11")
        detail = bundle.detail
        read_state = detail.get("read_state") or bundle.detail_meta.get("read_state")
        unavailable = _values_for_key(
            {"detail": detail, "meta": bundle.detail_meta},
            {"unavailable_sources", "source_unavailable", "unavailable", "source_status"},
        )
        self.recorder.require(
            "scenario-11.degraded_source_preserves_execution_truth",
            read_state in {"partial", "degraded"}
            and {
                "order_submission",
                "broker_acknowledgement",
                "fill_management",
                "ledger_booking",
                "reconciliation",
            }.issubset(_stage_names(detail))
            and detail.get("status") in {"completed", "completed_with_variance", "partially_filled"}
            and bool(unavailable)
            and bool(
                _values_for_key(
                    {"detail": detail, "meta": bundle.detail_meta},
                    {"freshness", "updated_at", "lag_seconds"},
                )
            ),
            "scenario 11 lacks preserved execution truth plus explicit unavailable-source/freshness evidence",
            details={"status": detail.get("status"), "read_state": read_state, "unavailable": unavailable},
        )

    def verify_scenario_12(self) -> None:
        bundle = self._bundle(12, "tj-scenario-12")
        replay = self._bff_call(
            "scenario-12-replay",
            _query(
                "/bff/management/trade-journeys/tj-scenario-12/replay",
                tenant_id=self.config.tenant_id,
                environment="paper",
                as_of=self.config.replay_as_of,
            ),
            token=self.operator_token,
        )
        self._require_status("scenario-12-replay", replay)
        self.recorder.add_scenario_evidence(12, "scenario-12-replay")
        historical = _data(replay)
        current_versions = {
            key: tuple(str(value) for value in _values_for_key(bundle.detail, {key}))
            for key in VERSION_FIELDS
        }
        historical_versions = {
            key: tuple(str(value) for value in _values_for_key(historical, {key}))
            for key in VERSION_FIELDS
        }
        changed = any(
            current_versions[key]
            and historical_versions[key]
            and current_versions[key] != historical_versions[key]
            for key in VERSION_FIELDS
        )
        self.recorder.require(
            "scenario-12.as_of_version_isolation",
            historical.get("exists_at_as_of") is True
            and historical.get("as_of") == self.config.replay_as_of
            and changed,
            "scenario 12 replay does not prove isolation from current persona/policy/binding versions",
            details={"current_versions": current_versions, "historical_versions": historical_versions},
        )

    def _duration_for(self, label: str) -> float | None:
        for call in self.recorder.calls:
            if call.get("label") != label:
                continue
            payload = call.get("payload")
            response = payload.get("response") if isinstance(payload, Mapping) else None
            duration = response.get("duration_ms") if isinstance(response, Mapping) else None
            if isinstance(duration, (int, float)):
                return float(duration)
        return None

    def verify_performance_budget(self) -> None:
        detail_warmup_labels: list[str] = []
        detail_labels: list[str] = []
        detail_statuses: list[int | None] = []
        for number in PERFORMANCE_DETAIL_SCENARIOS:
            label = f"axis-performance-detail-s{number:02d}-warmup"
            response = self._bff_call(
                label,
                _query(
                    f"/bff/management/trade-journeys/tj-scenario-{number}",
                    tenant_id=self.config.tenant_id,
                    environment="paper",
                ),
                token=self.operator_token,
            )
            detail_warmup_labels.append(label)
            detail_statuses.append(response.get("status"))

        for sample_index in range(PERFORMANCE_SAMPLE_COUNT):
            number = PERFORMANCE_DETAIL_SCENARIOS[
                sample_index % len(PERFORMANCE_DETAIL_SCENARIOS)
            ]
            label = (
                f"axis-performance-detail-s{number:02d}"
                f"-sample-{sample_index + 1:02d}"
            )
            response = self._bff_call(
                label,
                _query(
                    f"/bff/management/trade-journeys/tj-scenario-{number}",
                    tenant_id=self.config.tenant_id,
                    environment="paper",
                ),
                token=self.operator_token,
            )
            detail_labels.append(label)
            detail_statuses.append(response.get("status"))

        resolve_path = _query(
            "/bff/management/trade-journeys/resolve",
            q=self.config.ambiguity_identifier,
            tenant_id=self.config.tenant_id,
            environment="paper",
        )
        resolve_warmup_label = "axis-performance-resolve-warmup"
        resolve_warmup = self._bff_call(
            resolve_warmup_label,
            resolve_path,
            token=self.operator_token,
        )
        resolve_labels: list[str] = []
        resolve_statuses: list[int | None] = [resolve_warmup.get("status")]
        for sample_index in range(PERFORMANCE_SAMPLE_COUNT):
            label = f"axis-performance-resolve-sample-{sample_index + 1:02d}"
            response = self._bff_call(
                label,
                resolve_path,
                token=self.operator_token,
            )
            resolve_labels.append(label)
            resolve_statuses.append(response.get("status"))

        detail_samples = [
            value for label in detail_labels if (value := self._duration_for(label)) is not None
        ]
        resolve_samples = [
            value for label in resolve_labels if (value := self._duration_for(label)) is not None
        ]
        detail_p95 = _percentile_95(detail_samples) if detail_samples else float("inf")
        resolve_p95 = _percentile_95(resolve_samples) if resolve_samples else float("inf")
        passed = (
            all(status == 200 for status in detail_statuses)
            and all(status == 200 for status in resolve_statuses)
            and len(detail_samples) == PERFORMANCE_SAMPLE_COUNT
            and len(resolve_samples) == PERFORMANCE_SAMPLE_COUNT
            and detail_p95 <= 1500.0
            and resolve_p95 <= 1000.0
        )
        details = {
            "detail": {
                "sample_count": len(detail_samples),
                "required_sample_count": PERFORMANCE_SAMPLE_COUNT,
                "warmup_count": len(detail_warmup_labels),
                "sampled_scenarios": list(PERFORMANCE_DETAIL_SCENARIOS),
                "http_statuses": detail_statuses,
                "p95_ms": detail_p95,
                "budget_ms": 1500.0,
            },
            "resolve": {
                "sample_count": len(resolve_samples),
                "required_sample_count": PERFORMANCE_SAMPLE_COUNT,
                "warmup_count": 1,
                "http_statuses": resolve_statuses,
                "p95_ms": resolve_p95,
                "budget_ms": 1000.0,
            },
        }
        self.recorder.record_axis(
            "performance_budget",
            scenario_numbers=(*PERFORMANCE_DETAIL_SCENARIOS, 9),
            evidence_labels=(
                *detail_warmup_labels,
                *detail_labels,
                resolve_warmup_label,
                *resolve_labels,
            ),
            passed=passed,
            details=details,
        )
        self.recorder.require(
            "performance.hosted_p95_budgets",
            passed,
            "hosted Trade Journey detail or resolve p95 exceeded the acceptance budget",
            details=details,
        )

    def verify_sse_reconnect(self) -> None:
        path = _query(
            "/bff/management/trade-journeys/events",
            tenant_id=self.config.tenant_id,
            environment="paper",
        )
        initial = self._bff_sse_call("axis-sse-initial", path)
        initial_frame = _parse_sse_frame(initial.get("body"))
        cursor = str(initial_frame.get("id") or "")
        reconnect = self._bff_sse_call(
            "axis-sse-reconnect",
            path,
            last_event_id=cursor,
        )
        reconnect_frame = _parse_sse_frame(reconnect.get("body"))
        reconnect_data = reconnect_frame.get("data")
        reconnect_data = reconnect_data if isinstance(reconnect_data, Mapping) else {}
        try:
            initial_revision = int(cursor)
            reconnect_revision = int(str(reconnect_frame.get("id") or ""))
        except ValueError:
            initial_revision = -1
            reconnect_revision = -1
        content_type = str(initial.get("headers", {}).get("content-type") or "")
        passed = (
            initial.get("status") == 200
            and reconnect.get("status") == 200
            and content_type.startswith("text/event-stream")
            and initial_revision >= 0
            and reconnect_revision >= initial_revision
            and reconnect_data.get("previous_revision") == initial_revision
            and reconnect_data.get("revision") == reconnect_revision
            and reconnect_data.get("snapshot_refetch") is True
            and reconnect_frame.get("event") in {"journeys_changed", "snapshot_refetch_required"}
        )
        details = {
            "initial_frame": initial_frame,
            "reconnect_frame": reconnect_frame,
            "last_event_id_sent": cursor,
            "cursor_monotonic": reconnect_revision >= initial_revision >= 0,
        }
        self.recorder.record_axis(
            "sse_reconnect_replay",
            scenario_numbers=(8, 11),
            evidence_labels=("axis-sse-initial", "axis-sse-reconnect"),
            passed=passed,
            details=details,
        )
        self.recorder.require(
            "sse.hosted_reconnect_cursor_refetch",
            passed,
            "hosted Trade Journey SSE did not preserve Last-Event-ID cursor/refetch semantics",
            details=details,
        )

    def verify_rebuild_reload(self) -> None:
        health = self._bff_call("axis-rebuild-healthz", "/healthz")
        self._require_status("axis-rebuild-healthz", health)
        health_json = health.get("json") if isinstance(health.get("json"), Mapping) else {}
        dependencies = health_json.get("dependencies")
        dependencies = dependencies if isinstance(dependencies, Mapping) else {}
        projector = dependencies.get("lifecycle_projector")
        projector = projector if isinstance(projector, Mapping) else {}
        bundle_freshness = {
            number: bundle.detail_meta.get("freshness", {})
            for number, bundle in self.scenario_bundles.items()
        }
        bundle_passed = all(
            isinstance(freshness, Mapping)
            and freshness.get("rebuild_status") == "complete"
            and freshness.get("accepted_live") is True
            and freshness.get("projection_mode") == "live"
            and freshness.get("truth_level") == "canonical_live"
            for freshness in bundle_freshness.values()
        )
        passed = (
            len(bundle_freshness) == 11
            and bundle_passed
            and projector.get("ready") is True
            and projector.get("worker_status") == "ready"
            and projector.get("controller_status") == "ready"
            and projector.get("deployment_sha") == self.config.expected_bff_sha
            and projector.get("accepted_live") is True
            and projector.get("backlog") == 0
        )
        details = {
            "projector": projector,
            "scenario_bundle_count": len(bundle_freshness),
            "all_bundle_rebuild_status_complete": bundle_passed,
        }
        self.recorder.record_axis(
            "rebuild_reload",
            scenario_numbers=(*range(1, 10), 11, 12),
            evidence_labels=("axis-rebuild-healthz",),
            passed=passed,
            details=details,
        )
        self.recorder.require(
            "rebuild.hosted_projector_complete",
            passed,
            "hosted lifecycle projector is not exact-SHA, accepted-live, rebuilt, and caught up",
            details=details,
        )

    def record_existing_axes(self) -> None:
        self.recorder.record_axis(
            "security_rbac",
            scenario_numbers=(10,),
            evidence_labels=(
                "scenario-10-viewer-live-list",
                "scenario-10-foreign-list",
                "scenario-10-foreign-resolve",
            ),
            passed=True,
            details={
                "coverage": "viewer masking plus cross-tenant list/resolve denial",
            },
        )
        self.recorder.record_axis(
            "historical_replay",
            scenario_numbers=(12,),
            evidence_labels=("scenario-12-detail", "scenario-12-replay"),
            passed=True,
            details={"coverage": "as-of persona/policy/binding version isolation"},
        )

    def verify_scenario_ledger(self) -> None:
        rows = self.recorder.scenarios
        passed = (
            sorted(rows) == list(range(1, 13))
            and all(row.get("result") == "passed" for row in rows.values())
            and all(row.get("source_ids") for row in rows.values())
            and all(row.get("evidence_labels") for row in rows.values())
            and all(row.get("terminal_state") for row in rows.values())
            and all(row.get("reconciliation_result") for row in rows.values())
        )
        self.recorder.require(
            "scenario-ledger.twelve_complete_rows",
            passed,
            "hosted evidence did not produce twelve complete scenario ledger rows",
            details={
                "scenario_numbers": sorted(rows),
                "results": {str(number): row.get("result") for number, row in rows.items()},
            },
        )

    def run(self) -> None:
        self.verify_deployment()
        self.authenticate()
        self.wait_for_seed_projection()
        failures: list[dict[str, Any]] = []
        scenario_checks = (
            self.verify_scenario_1,
            self.verify_scenario_2,
            self.verify_scenario_3,
            self.verify_scenario_4,
            self.verify_scenario_5,
            self.verify_scenario_6,
            self.verify_scenario_7,
            self.verify_scenario_8,
            self.verify_scenario_9,
            self.verify_scenario_10,
            self.verify_scenario_11,
            self.verify_scenario_12,
        )
        for number, verify in enumerate(scenario_checks, start=1):
            try:
                verify()
            except VerificationError as exc:
                self.recorder.mark_scenario(number, "failed", exc.as_dict())
                failures.append({"scenario": number, **exc.as_dict()})
            else:
                self.recorder.mark_scenario(number, "passed")
        if failures:
            raise VerificationError(
                "SCENARIOS_FAILED",
                f"{len(failures)} of 12 hosted scenarios failed",
                details={"failures": failures},
            )
        self.record_existing_axes()
        self.verify_performance_budget()
        self.verify_sse_reconnect()
        self.verify_rebuild_reload()
        self.verify_scenario_ledger()


def main() -> int:
    try:
        config = Config.from_env()
    except VerificationError as exc:
        evidence_root = os.getenv("TJ_E2E_EVIDENCE_DIR", "").strip()
        if evidence_root:
            root = Path(evidence_root)
            root.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": "pantheon.tj-e2e-012.configuration-error.v1",
                "task_id": TASK_ID,
                "result": "blocked",
                "error": redact(exc.as_dict()),
            }
            encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            (root / "configuration-error.json").write_text(encoded, encoding="utf-8")
            digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            (root / "configuration-error.sha256").write_text(
                f"{digest}  configuration-error.json\n",
                encoding="utf-8",
            )
        print(f"BLOCKED [{exc.code}]: {exc}", file=sys.stderr)
        return 2

    recorder = EvidenceRecorder(config)
    verifier = HostedVerifier(config, recorder)
    error: VerificationError | None = None
    try:
        verifier.run()
    except VerificationError as exc:
        error = exc
    except Exception as exc:  # fail closed while still producing an artifact
        error = VerificationError("UNEXPECTED_ERROR", str(exc))

    manifest = recorder.write(passed=error is None, error=error)
    if error is not None:
        print(
            f"FAILED [{error.code}]: {error}; evidence={config.evidence_dir / 'evidence.json'}",
            file=sys.stderr,
        )
        return 1
    print(
        "PASS: all twelve hosted Trade Journey scenarios verified against "
        f"FE {config.expected_fe_sha} / BFF {config.expected_bff_sha}; "
        f"evidence={config.evidence_dir / 'evidence.json'}; "
        f"manifest_sha256={manifest['manifest_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
