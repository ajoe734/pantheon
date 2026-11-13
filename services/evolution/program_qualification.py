"""Qualification gate for automatically generated evolution programs.

Operator decision 2026-11-04: an evolution direction may be created by hand or
automatically, but automatic generation must have filter criteria and only what
reaches the standard may go to human review and then real evolution.

This module runs at the end of ``POST /api/evolution/daily-sweep``. It judges
every proposed ``EvolutionDecision`` against six criteria read from
``config/program_qualification_policy.json``. A decision that passes all six
earns exactly one program (``origin=auto_qualified``) through
``ProgramService`` and is submitted for human review once; nothing here
approves, dispatches or executes.

Design rules (see EVOLUTION_REVIEW_AND_THRESHOLDS.md section 17):

* Criteria that read another service run only for decisions that already
  passed ``real_threshold_signal`` and the local criteria, so a sweep over
  hundreds of unrelated proposals makes no per-decision owner reads.
* Every owner read fails closed: an unreadable or malformed owner answer is a
  failed criterion with a reason, never a pass.
* Persistence of breaches is counted from the drift evaluation history dated by
  market mark, not from IncidentCase records (one binding and cluster only
  ever has one IncidentCase, and it keeps its first event).
"""
from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

import httpx

from services.service_token_file import configured_service_token

log = logging.getLogger(__name__)

DEFAULT_POLICY_PATH = str(Path(__file__).resolve().parent / "config" / "program_qualification_policy.json")
QUALIFIER_ACTOR_ID = "evolution-daily-sweep"

CRITERIA = (
    "real_threshold_signal",
    "real_target",
    "persistent_breach",
    "fresh_evidence",
    "executable_action",
    "no_open_duplicate",
)

PASSED = "passed"
FAILED = "failed"
NOT_EVALUATED = "not_evaluated"

_TEST_SCOPE_VALUES = frozenset({"test", "acceptance", "fixture", "synthetic"})
_OPEN_PROGRAM_STATUSES = frozenset({"draft", "under_review", "active", "paused"})
# The Program lifecycle has no ``rejected`` state (evolution-lifecycle.md
# section 2). ``stopped`` is the owner state a program reaches when a human
# ends it without completing, so it is what starts the rejection cooldown.
_COOLDOWN_PROGRAM_STATUSES = frozenset({"stopped"})


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

# --- expression replay gate (INSTRUMENT-DECISION-LEARNING-COUNTERFACTUAL-20261107) ---

#: Controls that change which instrument the policy picks. The two view-strength
#: controls change the view itself, which replay never recomputes.
EXPRESSION_REPLAY_POLICY_CONTROLS = ("lever_min_strength", "notional_fraction_at_full_strength")


def expression_replay_gate(
    parent_parameters: Mapping[str, Any],
    child_parameters: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Evaluate a mutated expression policy on recorded decisions before it is proposed.

    Both parameter sets are replayed over the same recorded decisions
    (``expression_replay.replay``; deterministic, offline, direction never
    recomputed).  The child passes only when there is at least one recorded
    decision, at least one graded (marked) decision and its replayed total PnL
    is not below the parent's.  Qualification never sees a child that fails.
    """
    from services.execution.lean_runtime.expression_replay import replay

    recorded = [dict(r) for r in records or []]
    if not recorded:
        return {"passed": False, "reason": "no_recorded_decisions", "decision_count": 0}
    from services.execution.lean_runtime.expression_policy import POLICY_KIND, TARGET_POLICY_KIND
    if any(r.get("policy_kind", POLICY_KIND) not in {POLICY_KIND, TARGET_POLICY_KIND} for r in recorded):
        return {"passed": False, "reason": "unsupported_expression_policy", "decision_count": len(recorded)}
    parent = replay(recorded, parent_parameters)
    child = replay(recorded, child_parameters)
    graded = child["summary"]["graded_count"] + parent["summary"]["graded_count"]
    base = {
        "decision_count": len(recorded),
        "parent_total_pnl": parent["summary"]["total_pnl"],
        "child_total_pnl": child["summary"]["total_pnl"],
        "parent_no_trade_count": parent["summary"]["no_trade_count"],
        "child_no_trade_count": child["summary"]["no_trade_count"],
        "parent_policy_parameters_digest": parent["policy_parameters_digest"],
        "child_policy_parameters_digest": child["policy_parameters_digest"],
    }
    if graded == 0:
        return {**base, "passed": False, "reason": "no_marked_decisions"}
    if child["summary"]["total_pnl"] < parent["summary"]["total_pnl"]:
        return {**base, "passed": False, "reason": "child_replay_worse_than_parent"}
    return {**base, "passed": True, "reason": "child_replay_not_worse_than_parent"}


class PolicyError(ValueError):
    """The qualification policy is missing or invalid (fail closed)."""


@dataclass(frozen=True)
class QualificationPolicy:
    policy_version: str
    allowed_signal_types: frozenset
    metric_allowlist: frozenset
    denied_metrics: frozenset
    allowed_artifact_types: frozenset
    allowed_target_stages: frozenset
    allow_test_scope: bool
    min_distinct_breach_days: int
    lookback_days: int
    metric_max_age_seconds: int
    latest_evaluation_window_seconds: int
    max_event_reads: int
    owner_read_timeout_seconds: float


def _string_set(raw: Mapping[str, Any], key: str, *, required: bool = True) -> frozenset:
    value = raw.get(key)
    if not isinstance(value, list) or (required and not value):
        raise PolicyError(f"{key} must be a non-empty list of strings")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise PolicyError(f"{key} must contain only non-empty strings")
    return frozenset(item.strip() for item in value)


def _positive_int(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PolicyError(f"{key} must be a positive integer")
    return value


def load_policy(path: Optional[str] = None) -> QualificationPolicy:
    config_path = path or os.getenv("EVOLUTION_PROGRAM_QUALIFICATION_POLICY_PATH") or DEFAULT_POLICY_PATH
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        raise PolicyError(f"policy unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyError("policy must be a JSON object")
    version = raw.get("policy_version")
    if not isinstance(version, str) or not version.strip():
        raise PolicyError("policy_version must be a non-empty string")
    if not isinstance(raw.get("allow_test_scope"), bool):
        raise PolicyError("allow_test_scope must be a boolean")
    timeout = raw.get("owner_read_timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise PolicyError("owner_read_timeout_seconds must be a positive number")
    return QualificationPolicy(
        policy_version=version.strip(),
        allowed_signal_types=_string_set(raw, "allowed_signal_types"),
        metric_allowlist=_string_set(raw, "metric_allowlist"),
        denied_metrics=_string_set(raw, "denied_metrics", required=False),
        allowed_artifact_types=_string_set(raw, "allowed_artifact_types"),
        allowed_target_stages=_string_set(raw, "allowed_target_stages"),
        allow_test_scope=raw["allow_test_scope"],
        min_distinct_breach_days=_positive_int(raw, "min_distinct_breach_days"),
        lookback_days=_positive_int(raw, "lookback_days"),
        metric_max_age_seconds=_positive_int(raw, "metric_max_age_seconds"),
        latest_evaluation_window_seconds=_positive_int(raw, "latest_evaluation_window_seconds"),
        max_event_reads=_positive_int(raw, "max_event_reads"),
        owner_read_timeout_seconds=float(timeout),
    )


# ---------------------------------------------------------------------------
# Owner reads
# ---------------------------------------------------------------------------

class OwnerReadError(RuntimeError):
    """An owner read failed or returned something unusable (fail closed)."""


class OwnerNotFoundError(OwnerReadError):
    """The owner answered 404 for the exact resource."""


class QualificationOwnerReader:
    """Read-only access to the four owners the criteria need.

    One client for the drift, telemetry, registry and persona owners, using the
    configured service-token pattern (``configured_service_token``). Any
    missing URL or token fails closed at read time with the variable named.
    """

    def __init__(
        self,
        *,
        drift_url: str = "",
        drift_token: str = "",
        telemetry_url: str = "",
        telemetry_token: str = "",
        registry_url: str = "",
        registry_token: str = "",
        persona_url: str = "",
        persona_token: str = "",
        http: Any = None,
    ) -> None:
        self._targets = {
            "drift": (drift_url.rstrip("/"), drift_token, "RECONCILIATION_DRIFT_URL / RECONCILIATION_DRIFT_AUTH_TOKEN"),
            "telemetry": (telemetry_url.rstrip("/"), telemetry_token, "PANTHEON_TELEMETRY_API_URL / PANTHEON_TELEMETRY_SERVICE_TOKEN"),
            "registry": (registry_url.rstrip("/"), registry_token, "PANTHEON_REGISTRY_API_URL / EVOLUTION_REGISTRY_SERVICE_TOKEN"),
            "persona": (persona_url.rstrip("/"), persona_token, "PERSONA_URL / EVOLUTION_PERSONA_SERVICE_TOKEN"),
        }
        self._http = http

    @classmethod
    def from_env(cls, *, http: Any = None) -> "QualificationOwnerReader":
        # File-backed credentials rotate on disk (24h TTL, renewed in place), so
        # resolve them at each read instead of once at import.
        def token(name: str) -> Callable[[], str]:
            return lambda: configured_service_token(name)

        return cls(
            drift_url=os.getenv("RECONCILIATION_DRIFT_URL", ""),
            drift_token=os.getenv("RECONCILIATION_DRIFT_AUTH_TOKEN", "").strip(),
            telemetry_url=(
                os.getenv("EVOCHAIN_TELEMETRY_API_URL")
                or os.getenv("PANTHEON_TELEMETRY_API_URL")
                or os.getenv("PANTHEON_TELEMETRY_URL", "")
            ),
            telemetry_token=token("PANTHEON_TELEMETRY_SERVICE_TOKEN"),
            registry_url=os.getenv("PANTHEON_REGISTRY_API_URL", ""),
            registry_token=token("EVOLUTION_REGISTRY_SERVICE_TOKEN"),
            persona_url=os.getenv("PERSONA_URL", ""),
            persona_token=token("EVOLUTION_PERSONA_SERVICE_TOKEN"),
            http=http,
        )

    def _get(
        self,
        owner: str,
        path: str,
        *,
        tenant_id: str,
        timeout: float,
        params: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        base, token_source, variables = self._targets[owner]
        try:
            token = token_source() if callable(token_source) else token_source
        except RuntimeError as exc:
            raise OwnerReadError(f"{owner} owner credential is unreadable ({variables})") from exc
        if not base or not token:
            raise OwnerReadError(f"{owner} owner read is not configured ({variables})")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Tenant-Id": tenant_id,
        }
        try:
            if self._http is not None:
                response = self._http.get(base + path, headers=headers, params=params, timeout=timeout)
            else:
                response = httpx.get(
                    base + path, headers=headers, params=params, timeout=timeout, follow_redirects=False
                )
        except Exception as exc:  # network, DNS, timeout
            raise OwnerReadError(f"{owner} owner read failed: {exc.__class__.__name__}") from exc
        if response.status_code == 404:
            raise OwnerNotFoundError(f"{owner} owner has no {path}")
        if response.status_code != 200:
            raise OwnerReadError(f"{owner} owner read returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise OwnerReadError(f"{owner} owner returned malformed JSON") from exc

    def list_evaluations(
        self,
        binding_id: str,
        *,
        tenant_id: str,
        timeout: float,
        since: str,
        governed_breached_only: bool,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"binding_id": binding_id, "since": since}
        if governed_breached_only:
            params["governed_breached_only"] = "true"
        body = self._get("drift", "/api/reconciliation-drift/evaluations", tenant_id=tenant_id, timeout=timeout, params=params)
        if not isinstance(body, list):
            raise OwnerReadError("drift evaluations response is not a list")
        return [item for item in body if isinstance(item, dict)]

    def get_runtime_summary(self, runtime_id: str, *, tenant_id: str, timeout: float) -> Dict[str, Any]:
        body = self._get("telemetry", f"/api/telemetry/runtime-summaries/{_quote(runtime_id)}", tenant_id=tenant_id, timeout=timeout)
        if not isinstance(body, dict):
            raise OwnerReadError("runtime summary response is not an object")
        return body

    def get_telemetry_event(self, event_id: str, *, tenant_id: str, timeout: float) -> Dict[str, Any]:
        body = self._get("telemetry", f"/api/telemetry/events/{_quote(event_id)}", tenant_id=tenant_id, timeout=timeout)
        if not isinstance(body, dict):
            raise OwnerReadError("telemetry event response is not an object")
        return body

    def get_registry_entry(self, registry_id: str, *, tenant_id: str, timeout: float) -> Dict[str, Any]:
        body = self._get("registry", f"/api/registry/entries/{_quote(registry_id)}", tenant_id=tenant_id, timeout=timeout)
        if not isinstance(body, dict) or not isinstance(body.get("entry"), dict):
            raise OwnerReadError("registry response has no entry")
        if body["entry"].get("registry_id") != registry_id:
            raise OwnerReadError("registry returned a different entry identity")
        return body

    def get_persona(self, persona_id: str, *, tenant_id: str, timeout: float) -> Dict[str, Any]:
        body = self._get("persona", f"/api/personas/{_quote(persona_id)}", tenant_id=tenant_id, timeout=timeout)
        if not isinstance(body, dict):
            raise OwnerReadError("persona response is not an object")
        return body


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(str(value), safe="")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _utc_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _numeric(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


_COMPARATORS: Dict[str, Callable[[float, float], bool]] = {
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "eq": lambda a, b: a == b,
    "neq": lambda a, b: a != b,
}


def _result(criterion: str, result: str, *, observed: Any = None, threshold: Any = None, reason: str = "") -> Dict[str, Any]:
    return {
        "criterion": criterion,
        "result": result,
        "observed": observed,
        "threshold": threshold,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Report persistence
# ---------------------------------------------------------------------------

class QualificationReportStore:
    """Last qualification report per tenant, durable across restarts."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def save(self, tenant_id: str, report: Mapping[str, Any]) -> None:
        with self._lock:
            current = self._read_all()
            current[tenant_id] = dict(report)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), prefix=".qualification-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(current, handle, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, self._path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

    def load(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            value = self._read_all().get(tenant_id)
        return dict(value) if isinstance(value, dict) else None

    def _read_all(self) -> Dict[str, Any]:
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"qualification report store unreadable: {exc}") from exc
        return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Qualifier
# ---------------------------------------------------------------------------

class ProgramQualifier:
    def __init__(
        self,
        *,
        decision_store: Any,
        program_service: Any,
        owner_reader: Any,
        report_store: QualificationReportStore,
        action_paths: Sequence[Mapping[str, Any]],
        summary_field_for_metric: Callable[[str], Optional[str]],
        summary_is_stale_or_degraded: Callable[..., bool],
        metric_is_stale: Callable[..., bool],
        policy_loader: Callable[[], QualificationPolicy] = load_policy,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._decisions = decision_store
        self._programs = program_service
        self._reader = owner_reader
        self._reports = report_store
        self._action_paths = list(action_paths)
        self._summary_field_for_metric = summary_field_for_metric
        self._summary_is_stale_or_degraded = summary_is_stale_or_degraded
        self._metric_is_stale = metric_is_stale
        self._policy_loader = policy_loader
        self._clock = clock

    def report(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        return self._reports.load(tenant_id)

    # -- entry point ---------------------------------------------------

    def run(self, *, tenant_id: str, sweep_id: str = "daily") -> Dict[str, Any]:
        now = self._clock()
        evaluated_at = _utc_iso(now)
        try:
            policy = self._policy_loader()
        except PolicyError as exc:
            report = self._empty_report(
                tenant_id, sweep_id, evaluated_at, policy_version=None,
                failure_reason=f"policy_invalid: {exc}",
            )
            self._reports.save(tenant_id, report)
            return report

        proposed = [
            d for d in self._decisions.list_all()
            if str(getattr(d, "tenant_id", "") or "") == tenant_id
            and str(_enum_value(d.decision_state)) == "proposed"
        ]
        decisions_by_id = {d.decision_id: d for d in self._decisions.list_all() if str(getattr(d, "tenant_id", "") or "") == tenant_id}
        programs = self._programs.list_programs(tenant_id=tenant_id)

        rows: List[Dict[str, Any]] = []
        drafted = 0
        for decision in sorted(proposed, key=lambda d: d.decision_id):
            try:
                criteria, metric = self._evaluate(decision, policy, now, tenant_id, programs, decisions_by_id)
            except Exception as exc:  # one decision never stops the rest
                log.exception("evolution.program_qualification decision=%s failed", decision.decision_id)
                criteria = [_result("real_threshold_signal", FAILED, reason=f"qualifier_error: {exc.__class__.__name__}")]
                criteria += [_result(name, NOT_EVALUATED) for name in CRITERIA[1:]]
                metric = None
            qualified = all(c["result"] == PASSED for c in criteria)
            first_failing = next((c["criterion"] for c in criteria if c["result"] == FAILED), None)
            row: Dict[str, Any] = {
                "decision_id": decision.decision_id,
                "target_id": decision.target_id,
                "action_type": str(_enum_value(decision.action_type)),
                "metric_name": metric,
                "qualified": qualified,
                "first_failing_criterion": first_failing,
                "criteria": criteria,
                "program_id": None,
                "drafted": False,
            }
            if qualified:
                try:
                    program_id, created = self._draft_program(decision, metric, policy, evaluated_at, criteria, tenant_id)
                except Exception as exc:
                    log.exception("evolution.program_qualification draft failed decision=%s", decision.decision_id)
                    row["draft_error"] = exc.__class__.__name__
                else:
                    row["program_id"] = program_id
                    row["drafted"] = created
                    drafted += 1 if created else 0
            rows.append(row)

        counts: Dict[str, int] = {}
        for row in rows:
            if row["first_failing_criterion"]:
                counts[row["first_failing_criterion"]] = counts.get(row["first_failing_criterion"], 0) + 1
        report = {
            "tenant_id": tenant_id,
            "sweep_id": sweep_id,
            "policy_version": policy.policy_version,
            "evaluated_at": evaluated_at,
            "evaluated": len(rows),
            "qualified": sum(1 for r in rows if r["qualified"]),
            "drafted": drafted,
            "counts_by_first_failing_criterion": counts,
            "failure_reason": None,
            "decisions": rows,
        }
        self._reports.save(tenant_id, report)
        return report

    @staticmethod
    def _empty_report(tenant_id, sweep_id, evaluated_at, *, policy_version, failure_reason) -> Dict[str, Any]:
        return {
            "tenant_id": tenant_id,
            "sweep_id": sweep_id,
            "policy_version": policy_version,
            "evaluated_at": evaluated_at,
            "evaluated": 0,
            "qualified": 0,
            "drafted": 0,
            "counts_by_first_failing_criterion": {},
            "failure_reason": failure_reason,
            "decisions": [],
        }

    # -- evaluation ----------------------------------------------------

    def _evaluate(self, decision, policy, now, tenant_id, programs, decisions_by_id):
        signal, snapshot = self._real_threshold_signal(decision, policy)
        metric = str(snapshot.metric_name) if snapshot is not None else None
        executable = self._executable_action(decision)
        duplicate = self._no_open_duplicate(decision, snapshot, programs, decisions_by_id, now)
        results: Dict[str, Dict[str, Any]] = {
            "real_threshold_signal": signal,
            "executable_action": executable,
            "no_open_duplicate": duplicate,
        }
        owner_ready = all(results[name]["result"] == PASSED for name in results)
        if owner_ready:
            target = self._real_target(decision, policy, tenant_id)
            results["real_target"] = target
            if target["result"] == PASSED:
                results["persistent_breach"] = self._persistent_breach(decision, snapshot, policy, now, tenant_id)
                results["fresh_evidence"] = self._fresh_evidence(decision, snapshot, policy, now, tenant_id)
        ordered = [results.get(name) or _result(name, NOT_EVALUATED) for name in CRITERIA]
        return ordered, metric

    def _real_threshold_signal(self, decision, policy):
        snapshots = list(decision.threshold_snapshots or [])
        if not snapshots:
            return _result("real_threshold_signal", FAILED, reason="decision has no threshold snapshot"), None
        first_failure = None
        for snapshot in snapshots:
            outcome = self._judge_snapshot(snapshot, policy)
            if outcome["result"] == PASSED:
                return outcome, snapshot
            first_failure = first_failure or (outcome, snapshot)
        return first_failure

    @staticmethod
    def _judge_snapshot(snapshot, policy) -> Dict[str, Any]:
        signal_type = str(_enum_value(snapshot.signal_type))
        metric = str(snapshot.metric_name)
        comparator = str(_enum_value(snapshot.comparator))
        observed_value, threshold_value = snapshot.observed_value, snapshot.threshold_value
        kwargs = {
            "observed": {"signal_type": signal_type, "metric_name": metric, "observed_value": observed_value},
            "threshold": {"comparator": comparator, "threshold_value": threshold_value},
        }
        if not snapshot.breached:
            return _result("real_threshold_signal", FAILED, reason="snapshot is not breached", **kwargs)
        if signal_type not in policy.allowed_signal_types:
            return _result("real_threshold_signal", FAILED, reason=f"signal_type {signal_type} is not allowed", **kwargs)
        if metric in policy.denied_metrics or metric not in policy.metric_allowlist:
            return _result("real_threshold_signal", FAILED, reason=f"metric {metric} is not in the policy allowlist", **kwargs)
        observed, threshold = _numeric(observed_value), _numeric(threshold_value)
        if observed is None or threshold is None:
            return _result("real_threshold_signal", FAILED, reason="observed_value and threshold_value must be numeric", **kwargs)
        compare = _COMPARATORS.get(comparator)
        if compare is None:
            return _result("real_threshold_signal", FAILED, reason=f"unknown comparator {comparator}", **kwargs)
        if not compare(observed, threshold):
            return _result("real_threshold_signal", FAILED, reason="re-evaluating the comparator does not breach", **kwargs)
        return _result("real_threshold_signal", PASSED, **kwargs)

    def _executable_action(self, decision) -> Dict[str, Any]:
        action = str(_enum_value(decision.action_type))
        path = next(
            (p for p in self._action_paths if p.get("path_key") == f"research_{action}"),
            None,
        )
        plane = path.get("execution_plane") if path else None
        if plane == "research":
            return _result("executable_action", PASSED, observed=action, threshold="execution_plane=research")
        return _result(
            "executable_action", FAILED, observed=action, threshold="execution_plane=research",
            reason="action_type has no research execution path; it stays on its existing decision path",
        )

    def _cooldown_days(self, action: str) -> int:
        path = next((p for p in self._action_paths if p.get("path_key") == f"research_{action}"), None)
        return int(path.get("cooldown_days") or 0) if path else 0

    def _no_open_duplicate(self, decision, snapshot, programs, decisions_by_id, now) -> Dict[str, Any]:
        state = str(_enum_value(decision.decision_state))
        threshold = "decision proposed; no open program for target and metric; rejection cooldown elapsed"
        if state != "proposed":
            return _result("no_open_duplicate", FAILED, observed=state, threshold=threshold, reason="decision is no longer proposed")
        metric = str(snapshot.metric_name) if snapshot is not None else None
        action = str(_enum_value(decision.action_type))
        cooldown = timedelta(days=self._cooldown_days(action))
        for program in programs:
            source_ids = list(program.get("source_decision_ids") or [])
            if decision.decision_id in source_ids:
                continue  # this decision's own program: replay is idempotent
            for source_id in source_ids:
                other = decisions_by_id.get(source_id)
                if other is None or other.target_id != decision.target_id:
                    continue
                if metric is not None and not any(str(s.metric_name) == metric for s in (other.threshold_snapshots or [])):
                    continue
                status = str(program.get("status") or "")
                if status in _OPEN_PROGRAM_STATUSES:
                    return _result(
                        "no_open_duplicate", FAILED, observed=program.get("program_id"), threshold=threshold,
                        reason=f"program {program.get('program_id')} for the same target and metric is {status}",
                    )
                updated = _parse_utc(program.get("updated_at"))
                if status in _COOLDOWN_PROGRAM_STATUSES and updated is not None and now - updated < cooldown:
                    return _result(
                        "no_open_duplicate", FAILED, observed=program.get("program_id"), threshold=threshold,
                        reason=f"program {program.get('program_id')} for the same target and metric was stopped inside the {cooldown.days}-day cooldown",
                    )
        return _result("no_open_duplicate", PASSED, observed=state, threshold=threshold)

    # -- owner-read criteria -------------------------------------------

    def _real_target(self, decision, policy, tenant_id) -> Dict[str, Any]:
        target_id = str(decision.target_id or "")
        threshold = {
            "artifact_types": sorted(policy.allowed_artifact_types),
            "stages": sorted(policy.allowed_target_stages),
            "allow_test_scope": policy.allow_test_scope,
        }
        if "infra-component" in target_id:
            return _result("real_target", FAILED, observed=target_id, threshold=threshold, reason="infra-component targets are not strategy or persona artifacts")
        timeout = policy.owner_read_timeout_seconds
        try:
            view = self._reader.get_registry_entry(target_id, tenant_id=tenant_id, timeout=timeout)
        except OwnerReadError as exc:
            return _result("real_target", FAILED, observed=target_id, threshold=threshold, reason=f"target_unverifiable: {exc}")
        entry = view["entry"]
        artifact_type = str(_enum_value(entry.get("artifact_type")) or "")
        observed = {"target_id": target_id, "artifact_type": artifact_type}
        if artifact_type not in policy.allowed_artifact_types:
            return _result("real_target", FAILED, observed=observed, threshold=threshold, reason=f"artifact_type {artifact_type} is not allowed")
        # The registry does not carry the authoritative stage; the target
        # runtime's current telemetry summary does.
        _binding_id, runtime_id = self._binding_identity(decision)
        if not runtime_id:
            return _result("real_target", FAILED, observed=observed, threshold=threshold, reason="target_unverifiable: decision names no runtime")
        try:
            summary = self._reader.get_runtime_summary(runtime_id, tenant_id=tenant_id, timeout=timeout)
        except OwnerReadError as exc:
            return _result("real_target", FAILED, observed=observed, threshold=threshold, reason=f"target_unverifiable: runtime summary unreadable: {exc}")
        if str(summary.get("artifact_id") or "") != target_id:
            return _result("real_target", FAILED, observed={**observed, "runtime_id": runtime_id}, threshold=threshold, reason="target_unverifiable: runtime summary names another artifact")
        stage = str(_enum_value(summary.get("deployment_stage")) or "")
        observed = {**observed, "runtime_id": runtime_id, "deployment_stage": stage}
        if stage not in policy.allowed_target_stages:
            return _result("real_target", FAILED, observed=observed, threshold=threshold, reason=f"deployment_stage {stage} is not allowed")
        persona_id = decision.persona_id or (entry.get("metadata") or {}).get("persona_id")
        if persona_id:
            try:
                persona = self._reader.get_persona(str(persona_id), tenant_id=tenant_id, timeout=timeout)
            except OwnerReadError as exc:
                return _result("real_target", FAILED, observed=observed, threshold=threshold, reason=f"target_unverifiable: persona unreadable: {exc}")
            scope = str(
                persona.get("data_scope")
                or (persona.get("metadata") or {}).get("data_scope")
                or ""
            ).strip().lower()
            observed = {**observed, "persona_id": persona_id, "data_scope": scope or None}
            if scope in _TEST_SCOPE_VALUES and not policy.allow_test_scope:
                return _result("real_target", FAILED, observed=observed, threshold=threshold, reason=f"persona data_scope {scope} is refused")
        return _result("real_target", PASSED, observed=observed, threshold=threshold)

    @staticmethod
    def _binding_identity(decision):
        metadata = decision.metadata or {}
        return (
            str(metadata.get("runtime_binding_id") or "").strip(),
            str(metadata.get("runtime_id") or "").strip(),
        )

    def _persistent_breach(self, decision, snapshot, policy, now, tenant_id) -> Dict[str, Any]:
        metric = str(snapshot.metric_name)
        threshold = {
            "min_distinct_breach_days": policy.min_distinct_breach_days,
            "lookback_days": policy.lookback_days,
            "metric": metric,
        }
        binding_id, _runtime_id = self._binding_identity(decision)
        if not binding_id:
            return _result("persistent_breach", FAILED, observed=0, threshold=threshold, reason="decision names no runtime binding")
        timeout = policy.owner_read_timeout_seconds
        since_moment = now - timedelta(days=policy.lookback_days)
        try:
            evaluations = self._reader.list_evaluations(
                binding_id, tenant_id=tenant_id, timeout=timeout,
                since=_utc_iso(since_moment), governed_breached_only=True,
            )
            mark_dates = self._breach_mark_dates(
                evaluations, metric, policy, since_moment.date(), tenant_id
            )
        except OwnerReadError as exc:
            return _result("persistent_breach", FAILED, observed=None, threshold=threshold, reason=f"evidence_unreadable: {exc}")
        observed = {"distinct_breach_days": len(mark_dates), "mark_dates": sorted(d.isoformat() for d in mark_dates)}
        if len(mark_dates) >= policy.min_distinct_breach_days:
            return _result("persistent_breach", PASSED, observed=observed, threshold=threshold)
        return _result(
            "persistent_breach", FAILED, observed=observed, threshold=threshold,
            reason=f"{len(mark_dates)} distinct market mark date(s) breached; {policy.min_distinct_breach_days} required",
        )

    def _breach_mark_dates(self, evaluations, metric, policy, earliest: date, tenant_id) -> set:
        dates: set = set()
        event_dates: Dict[str, Optional[date]] = {}
        for evaluation in sorted(evaluations, key=lambda e: str(e.get("evaluated_at") or "")):
            for check in evaluation.get("drift_checks") or []:
                if not isinstance(check, dict):
                    continue
                if check.get("threshold_metric") != metric or check.get("governed_threshold_breached") is not True:
                    continue
                event_id = str(check.get("drawdown_event_id") or "").strip()
                if not event_id:
                    continue  # names no causal event: adds no day
                if event_id not in event_dates:
                    if len(event_dates) >= policy.max_event_reads:
                        raise OwnerReadError(f"more than {policy.max_event_reads} distinct telemetry events in the lookback window")
                    event = self._reader.get_telemetry_event(event_id, tenant_id=tenant_id, timeout=policy.owner_read_timeout_seconds)
                    event_dates[event_id] = self._mark_date(event)
                mark = event_dates[event_id]
                if mark is not None and mark >= earliest:
                    dates.add(mark)
            if len(dates) >= policy.min_distinct_breach_days:
                break
        return dates

    @staticmethod
    def _mark_date(event: Mapping[str, Any]) -> Optional[date]:
        metadata = event.get("metadata")
        refs = metadata.get("mark_refs") if isinstance(metadata, dict) else None
        as_of_values = [
            parsed
            for ref in (refs if isinstance(refs, list) else [])
            if isinstance(ref, dict) and (parsed := _parse_utc(ref.get("as_of"))) is not None
        ]
        return max(as_of_values).astimezone(timezone.utc).date() if as_of_values else None

    def _fresh_evidence(self, decision, snapshot, policy, now, tenant_id) -> Dict[str, Any]:
        metric = str(snapshot.metric_name)
        threshold = {
            "metric_max_age_seconds": policy.metric_max_age_seconds,
            "latest_evaluation_window_seconds": policy.latest_evaluation_window_seconds,
        }
        binding_id, runtime_id = self._binding_identity(decision)
        if not binding_id or not runtime_id:
            return _result("fresh_evidence", FAILED, threshold=threshold, reason="decision names no runtime binding and runtime")
        field = self._summary_field_for_metric(metric)
        if not field:
            return _result("fresh_evidence", FAILED, threshold=threshold, reason=f"no runtime summary field is configured for {metric}")
        timeout = policy.owner_read_timeout_seconds
        try:
            summary = self._reader.get_runtime_summary(runtime_id, tenant_id=tenant_id, timeout=timeout)
            recent = self._reader.list_evaluations(
                binding_id, tenant_id=tenant_id, timeout=timeout,
                since=_utc_iso(now - timedelta(seconds=policy.latest_evaluation_window_seconds)),
                governed_breached_only=False,
            )
        except OwnerReadError as exc:
            return _result("fresh_evidence", FAILED, threshold=threshold, reason=f"evidence_unreadable: {exc}")
        # Heartbeats arrive every 1-3s, so a summary read now is normally newer
        # than the sweep start; judge freshness against a clock read after the
        # fetch, not the run-start ``now`` (which stays the report evaluated_at).
        read_at = self._clock()
        if self._summary_is_stale_or_degraded(summary, now=read_at):
            return _result("fresh_evidence", FAILED, observed={"runtime_id": runtime_id}, threshold=threshold, reason="runtime summary is stale or degraded")
        if self._metric_is_stale(summary, field, now=read_at, max_age_seconds=policy.metric_max_age_seconds):
            return _result("fresh_evidence", FAILED, observed={"runtime_id": runtime_id, "summary_field": field}, threshold=threshold, reason=f"metric field {field} has no fresh as-of time")
        if not recent:
            return _result("fresh_evidence", FAILED, observed={"runtime_id": runtime_id}, threshold=threshold, reason="binding has no drift evaluation inside the latest-evaluation window")
        latest = max(recent, key=lambda e: str(e.get("evaluated_at") or ""))
        breached = any(
            isinstance(check, dict)
            and check.get("threshold_metric") == metric
            and check.get("governed_threshold_breached") is True
            for check in latest.get("drift_checks") or []
        )
        observed = {
            "runtime_id": runtime_id,
            "latest_evaluation_id": latest.get("evaluation_id") or latest.get("id"),
            "latest_evaluated_at": latest.get("evaluated_at"),
            "latest_governed_threshold_breached": breached,
        }
        if not breached:
            return _result("fresh_evidence", FAILED, observed=observed, threshold=threshold, reason="most recent drift evaluation no longer reports a governed breach")
        return _result("fresh_evidence", PASSED, observed=observed, threshold=threshold)

    # -- program creation ----------------------------------------------

    def _draft_program(self, decision, metric, policy, evaluated_at, criteria, tenant_id):
        qualification = {
            "policy_version": policy.policy_version,
            "evaluated_at": evaluated_at,
            "decision_id": decision.decision_id,
            "target_id": decision.target_id,
            "metric_name": metric,
            "criteria": [dict(c) for c in criteria],
        }
        action = str(_enum_value(decision.action_type))
        program, replayed = self._programs.create_auto_qualified_program(
            tenant_id=tenant_id,
            actor_id=QUALIFIER_ACTOR_ID,
            name=f"Auto {action}: {decision.target_id} ({metric})",
            decision_id=decision.decision_id,
            qualification=qualification,
        )
        # A replay returns the originally committed snapshot, so judge the
        # current status, not the snapshot: review is submitted once only.
        current = self._programs.get_program(tenant_id=tenant_id, program_id=program["program_id"]) or program
        if current.get("status") == "draft":
            # Submit for human review once and go no further.
            self._programs.execute_action(
                tenant_id=tenant_id,
                actor_id=QUALIFIER_ACTOR_ID,
                actor_role="operator",
                program_id=program["program_id"],
                action_id="submit_evolution_review",
                expected_revision=current.get("revision"),
                idempotency_key=f"auto-qualify-submit:{decision.decision_id}",
                note=f"Qualified by policy {policy.policy_version}",
            )
        return program["program_id"], not replayed
