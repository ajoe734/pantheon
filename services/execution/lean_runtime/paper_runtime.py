"""Truthful paper execution runtime package for the VM-2 execution plane."""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

try:  # Taiwan symbol normalization lives at the shioaji adapter boundary
    from ..shioaji_adapter import normalize_taiwan_symbol
except Exception:  # pragma: no cover - fallback if package path differs at runtime
    def normalize_taiwan_symbol(symbol, venue=None):  # type: ignore[misc]
        s = str(symbol).strip().upper()
        ticker = s.rsplit(".", 1)[0] if "." in s else s
        suffix = s.rsplit(".", 1)[1] if "." in s else "TW"
        return ticker, {"TPEX": "OTC", "TWO": "OTC"}.get(suffix, "TSE")
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import parse_qs, urlparse

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_RUNTIME_MANAGER_DIR = Path(__file__).resolve().parents[2] / "runtime-manager"
if str(_RUNTIME_MANAGER_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_MANAGER_DIR))

from runtime_manager_client import RuntimeManagerClient
from services.execution.lean_runtime.pending_signal_store import (
    BINDING_QUEUE_KEY_PREFIX,
    PendingSignalStore,
    binding_queue_key,
    build_pending_signal_store,
)
from services.execution.lean_runtime.performance_telemetry import (
    MarketMark,
    RollingDrawdownTracker,
    SourceIngestMarkProvider,
    value_portfolio,
)
from services.execution.lean_runtime.futures_session_inputs import (
    DatasetFuturesMarkProvider,
)
from services.execution.lean_runtime.bootstrap_contract import ALLOWED_ENGINE_BRIDGE_REMOTES, ALLOWED_ENGINE_BRIDGE_SOURCE_PATHS
from services.execution.lean_runtime.instrument_candidates import (
    governed_twse_holidays,
    is_trading_day,
    last_trading_day,
    lookup_final_settlement_price,
    normalize_underlying_code,
    parse_instrument_id,
    previous_trading_day,
)
from services.execution.lean_runtime.runtime_context import PantheonRuntimeContext, RuntimeContextError
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
from services.execution.lean_runtime.signal_consumer import SignalConsumer
from services.execution.lean_runtime.taifex_fee_schedule import (
    compute_open_or_close_cost,
    compute_settlement_cost,
)
from services.broker.paper_simulation import simulate_paper_order
from services.capital.risk_policy import (
    RiskPolicy,
    RiskPolicyDecision,
    RiskPolicyError,
    RiskPolicyEvaluationContext,
    RiskPolicyEvaluator,
    RiskPolicyLoadError,
    RiskPolicyTargetType,
    load_risk_policy,
    load_risk_policy_model,
    normalize_risk_policy,
    resolve_bound_pool_risk_policy,
)
from services.trade_journey.correlation_envelope import (
    CorrelationEnvelopeError,
    propagate_envelope,
    validate_envelope,
)

log = logging.getLogger(__name__)


_HALT_BINDING_STATUSES = frozenset({"paused", "pending_pause", "failed", "retired"})
_CANONICAL_LIFECYCLE_EVENT_TYPES = frozenset(
    {
        "signal_generation",
        "trade_decision",
        "risk_evaluation",
        "order_submitted",
        "order_accepted",
        "paper_order_simulated",
        "paper_fill_simulated",
        "order_rejection",
        "position_snapshot",
        "paper_futures_settled",
        "paper_margin_call",
        "paper_forced_liquidation",
    }
)
_PILOT_FUTURES_PROFILES: dict[str, dict[str, Any]] = {
    "QFF": {
        "underlying": "2330",
        "multiplier": 100.0,
        "margin_tier": "pct_tier_1",
        "initial_rate": 0.135,
        "maintenance_rate": 0.1035,
        "underlying_kind": "stock",
    },
    "DHF": {
        "underlying": "2317",
        "multiplier": 2000.0,
        "margin_tier": "pct_tier_1",
        "initial_rate": 0.135,
        "maintenance_rate": 0.1035,
        "underlying_kind": "stock",
    },
    "SRF": {
        "underlying": "0050",
        "multiplier": 1000.0,
        "margin_tier": "fixed_20261105",
        "fixed_initial_margin": 8700.0,
        "fixed_maintenance_margin": 6700.0,
        "underlying_kind": "etf",
    },
}
_PILOT_UNDERLYING_TO_CONTRACT: dict[str, str] = {
    "2330": "QFF",
    "2317": "DHF",
    "0050": "SRF",
}
_LIFECYCLE_UUID_NAMESPACE = uuid.UUID("1760784c-c9e0-47eb-b0aa-d37f58d892df")
"""Binding statuses at which the paper runtime must NOT execute signals.

A binding moved to paused / pending_pause / failed / retired (e.g. by an operator
pause or the kill-switch / safe-mode path) must halt order execution as
defense-in-depth — otherwise the execution loop keeps filling orders for a
halted binding. Signals are left on the queue so they can replay on resume.
"""


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_float(value: str | None, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _finite_float(value: Any, *, field: str, positive: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    if positive and result <= 0:
        raise ValueError(f"{field} must be positive")
    return result


def _parse_ledger_timestamp(value: Any, *, field: str) -> tuple[str, datetime]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"paper performance ledger {field} is missing")
    normalized = value.strip()
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"paper performance ledger {field} is not a valid timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(
            f"paper performance ledger {field} must include a timezone"
        )
    return normalized, parsed.astimezone(timezone.utc)


def _parse_iso_utc(value: Any) -> datetime | None:
    try:
        p = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        return (p if p.tzinfo else p.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except Exception:
        return None



def _runtime_context_identity_env(
    context: PantheonRuntimeContext,
    base_env: Mapping[str, str],
) -> dict[str, str]:
    env = dict(base_env)
    updates = {
        "PANTHEON_RUNTIME_BINDING_ID": context.runtime_binding_id,
        "PANTHEON_RUNTIME_ID": context.runtime_id,
        "PANTHEON_DEPLOYMENT_PLAN_ID": context.deployment_plan_id,
        "PANTHEON_DEPLOYMENT_STAGE": context.deployment_stage,
        "PANTHEON_RUNTIME_MODE": context.deployment_stage,
        "PANTHEON_RUNTIME_ROLE": context.runtime_role,
        "PANTHEON_ARTIFACT_ID": context.artifact.artifact_id,
        "PANTHEON_ARTIFACT_VERSION": context.artifact.artifact_version,
        "PANTHEON_ARTIFACT_CHECKSUM": context.artifact.artifact_checksum,
        "PANTHEON_STRATEGY_ID": context.artifact.strategy_id,
        "PANTHEON_CAPITAL_POOL_ID": context.capital.capital_pool_id,
        "PANTHEON_PERSONA_CAPITAL_BINDING_ID": context.capital.persona_capital_binding_id,
        "PANTHEON_ENGINE_BRIDGE_REMOTE": context.bridge.repo,
        "PANTHEON_ENGINE_BRIDGE_SOURCE_PATH": context.bridge.path,
        "PANTHEON_ENGINE_BRIDGE_COMMIT": context.bridge.commit,
        "PANTHEON_RUNTIME_ADAPTER_VERSION": context.bridge.runtime_adapter_version,
        "PANTHEON_TRACE_ID": context.trace.trace_id,
        "PANTHEON_REQUEST_ID": context.trace.correlation_id,
    }
    env.update({key: value for key, value in updates.items() if value})
    return env


def _binding_from_runtime_context(context: PantheonRuntimeContext) -> dict[str, Any]:
    return {
        "binding_id": context.runtime_binding_id,
        "runtime_binding_id": context.runtime_binding_id,
        "runtime_id": context.runtime_id,
        "status": "context_loaded",
        "deployment_mode": context.deployment_stage,
        "deployment_stage": context.deployment_stage,
        "capital_pool_id": context.capital.capital_pool_id,
        "plan_id": context.deployment_plan_id,
        "deployment_plan_id": context.deployment_plan_id,
        "artifact_id": context.artifact.artifact_id,
        "artifact_version": context.artifact.artifact_version,
        "persona_capital_binding_id": context.capital.persona_capital_binding_id,
        "engine_bridge_repo": context.bridge.repo,
        "engine_bridge_path": context.bridge.path,
        "engine_bridge_commit": context.bridge.commit,
        "runtime_adapter_version": context.bridge.runtime_adapter_version,
        "context_source": context.context_source.value,
    }


def _runtime_context_snapshot(context: PantheonRuntimeContext | None) -> dict[str, Any]:
    if context is None:
        return {
            "loaded": False,
            "context_source": "unavailable",
            "runtime_binding_id": None,
            "deployment_stage": None,
        }
    return {
        "loaded": True,
        "context_source": context.context_source.value,
        "runtime_binding_id": context.runtime_binding_id,
        "runtime_id": context.runtime_id,
        "deployment_plan_id": context.deployment_plan_id,
        "deployment_stage": context.deployment_stage,
        "runtime_role": context.runtime_role,
        "artifact_id": context.artifact.artifact_id,
        "artifact_version": context.artifact.artifact_version,
        "capital_pool_id": context.capital.capital_pool_id,
        "persona_capital_binding_id": context.capital.persona_capital_binding_id,
        "bridge_repo": context.bridge.repo,
        "bridge_path": context.bridge.path,
        "bridge_commit": context.bridge.commit,
        "runtime_adapter_version": context.bridge.runtime_adapter_version,
        "trace_id": context.trace.trace_id,
        "correlation_id": context.trace.correlation_id,
    }


def _is_installed_bridge_path(p: Any) -> bool:
    return False


def _extract_verified_bridge(binding: Mapping[str, Any]) -> dict[str, str] | None:
    return None


class _Holding:
    def __init__(self, quantity: float = 0.0) -> None:
        self.Quantity = quantity


class _Security:
    def __init__(self, price: float = 100.0) -> None:
        self.Price = price
        self.MarkAsOf: str | None = None
        self.MarkSource: str | None = None
        self.MarkAuthoritative = False


@dataclass
class OrderEvent:
    event_type: str
    symbol: str
    quantity: float
    fill_price: float
    action: str
    submitted_to_broker: bool = False
    broker_submission_status: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_iso_now)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "symbol": self.symbol,
            "quantity": self.quantity,
            "fill_price": self.fill_price,
            "action": self.action,
            "submitted_to_broker": self.submitted_to_broker,
            "created_at": self.created_at,
        }
        if self.broker_submission_status:
            payload["broker_submission_status"] = self.broker_submission_status
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


class PaperExecutionAlgorithm:
    """Small LEAN-like surface used by the executor for paper fills."""

    def __init__(
        self,
        *,
        initial_cash: float = 100_000.0,
        default_price: float = 100.0,
        event_sink: Callable[[OrderEvent], None] | None = None,
        deployment_stage: str = "paper",
        bracket_order_execution_enabled: bool = True,
        state_path: str | None = None,
        durable_fill_publication: bool = False,
        persona_binding_id: str | None = None,
        risk_policy: RiskPolicy | Mapping[str, Any] | None = None,
        risk_policy_id: str | None = None,
        risk_policy_ref: str | None = None,
        risk_policy_loader: Callable[[str], RiskPolicy | Mapping[str, Any]] | None = None,
        capital_pool_id: str | None = None,
        capital_pool_reader: Callable[[str], Any] | None = None,
        binding_resolver: Any = None,
        broker_fee_per_contract_per_side: float = 0.0,
        futures_costs_enabled: bool = True,
        in_process_broker: bool = False,
        holidays: Iterable[Any] | None = None,
    ) -> None:
        self._durable_fill_publication = bool(durable_fill_publication)
        self._pending_fill_publications: list[dict[str, Any]] = []
        self._initial_cash = _finite_float(
            initial_cash,
            field="initial_cash",
            positive=True,
        )
        self._cash = self._initial_cash
        self._default_price = _finite_float(
            default_price,
            field="default_price",
            positive=True,
        )
        self._event_sink = event_sink
        self.DeploymentStage = str(deployment_stage or "paper").strip().lower()
        self.BracketOrderExecutionEnabled = bool(bracket_order_execution_enabled)
        self.Portfolio: dict[str, _Holding] = {}
        self.Securities: dict[str, _Security] = {}
        self._open_bracket_orders: list[dict[str, Any]] = []
        self._current_signal_metadata: dict[str, Any] = {}
        self._drained_signal_contexts: list[dict[str, Any]] = []
        self._after_signal_context_hook: Callable[[], None] | None = None
        self._last_futures_mark_diagnostic: dict[str, Any] | None = None
        self._active_episodes: dict[str, str] = {}
        self._last_fill_episode_id: str | None = None
        self._reversal_info: dict[str, Any] | None = None
        self._fill_count = 0
        self._ledger_started_at = _iso_now()
        self._first_fill_at: str | None = None
        self._last_fill_at: str | None = None
        self._performance_window_state: dict[str, Any] = {}
        self._pending_performance_pair: dict[str, Any] | None = None
        self._performance_binding_id: str | None = None
        self._default_persona_binding_id: str | None = (
            str(persona_binding_id).strip() if persona_binding_id else None
        )
        self._instrument_positions: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._realized_pnl: float = 0.0
        self._total_costs: float = 0.0
        self._injected_equity_capital: float = 0.0
        self._margin_call_active: bool = False
        self._margin_call_since: str | None = None
        self._last_futures_mark_session_date: str | None = None
        self._settled_futures_contracts: set[str] = set()
        self._broker_fee_per_contract_per_side: float = float(
            broker_fee_per_contract_per_side
        )
        self._futures_costs_enabled: bool = bool(futures_costs_enabled)
        self._in_process_broker: bool = bool(in_process_broker)
        self._holidays_override: tuple[Any, ...] | None = (
            tuple(holidays) if holidays is not None else None
        )
        self._configured_risk_policy: RiskPolicy | None = None
        resolved_init_ref = str(risk_policy_ref or risk_policy_id or "").strip()
        self._configured_risk_policy_id: str | None = resolved_init_ref or None
        self._risk_policy_loader: Callable[[str], RiskPolicy | Mapping[str, Any]] = (
            risk_policy_loader or load_risk_policy
        )
        self._capital_pool_id: str | None = (
            str(capital_pool_id).strip() if capital_pool_id else None
        )
        self._capital_pool_reader: Callable[[str], Any] | None = capital_pool_reader
        self._binding_resolver: Any = binding_resolver
        self._bound_runtime_binding: dict[str, Any] | None = None
        self._last_risk_gate_result: dict[str, Any] | None = None
        if risk_policy is not None:
            self.SetRiskPolicy(risk_policy)
        self._state_binding_error: str | None = None
        self._loaded_state_missing_binding_id = False
        self._state_path = Path(state_path) if state_path else None
        self._state_error: str | None = None
        self._state_load_error: str | None = None
        self._load_state()

    @staticmethod
    def _taiwan_underlying_key(symbol: str) -> tuple[str, str] | None:
        raw = str(symbol or "").strip()
        if "." not in raw:
            return None
        try:
            return normalize_taiwan_symbol(raw)
        except Exception:
            return None

    def _resolve_holding_symbol(self, symbol: str) -> str:
        target = str(symbol)
        tw_key = self._taiwan_underlying_key(target)
        if tw_key is None:
            return target
        for existing_symbol, holding in self.Portfolio.items():
            if self._taiwan_underlying_key(existing_symbol) == tw_key and holding.Quantity != 0:
                return existing_symbol
        if target in self.Portfolio:
            return target
        for existing_symbol in self.Portfolio:
            if self._taiwan_underlying_key(existing_symbol) == tw_key:
                return existing_symbol
        return target

    def _taiwan_underlying_holding_quantity(self, native: str, exchange: str) -> float:
        total = 0.0
        for existing_symbol, holding in self.Portfolio.items():
            if self._taiwan_underlying_key(existing_symbol) == (native, exchange):
                total += _finite_float(
                    holding.Quantity,
                    field="Taiwan current holding quantity",
                )
        return total

    def _holding(self, symbol: str) -> _Holding:
        resolved = self._resolve_holding_symbol(symbol)
        return self.Portfolio.setdefault(resolved, _Holding())

    def _security(self, symbol: str) -> _Security:
        return self.Securities.setdefault(symbol, _Security(price=self._default_price))

    def EnsureSecurity(self, symbol: str) -> _Security:  # noqa: N802
        """Expose deterministic paper pricing for executor price lookups."""
        return self._security(str(symbol))

    def SetSecurityPrice(  # noqa: N802
        self,
        symbol: str,
        price: float,
        *,
        as_of: str | None = None,
        source: str = "runtime_price",
        authoritative: bool = False,
    ) -> None:
        validated_price = _finite_float(
            price,
            field="security price",
            positive=True,
        )
        security = self._security(str(symbol))
        security.Price = validated_price
        security.MarkAsOf = str(as_of) if as_of else None
        security.MarkSource = str(source) if source else None
        security.MarkAuthoritative = bool(authoritative and as_of and source)
        self._update_instrument_marks_for_symbol(
            str(symbol),
            validated_price,
            as_of=as_of,
            source=source,
        )

    def SetSecurityMark(  # noqa: N802
        self,
        symbol: str,
        price: float,
        *,
        as_of: str | None,
        source: str,
    ) -> None:
        self.SetSecurityPrice(
            symbol,
            price,
            as_of=as_of,
            source=source,
            authoritative=True,
        )

    def _has_open_or_pending_stock_futures(self) -> bool:
        return any(
            pos.get("kind") == "stock_future"
            and (
                abs(float(pos.get("quantity", 0.0))) > 1e-12
                or pos.get("status") == "settlement_pending"
            )
            for pos in self._instrument_positions.values()
        )

    def _open_active_stock_future_contracts(self) -> list[str]:
        return sorted(
            inst_id
            for (_b_id, _norm_u, inst_id), pos in self._instrument_positions.items()
            if pos.get("kind") == "stock_future"
            and abs(float(pos.get("quantity", 0.0))) > 1e-12
            and pos.get("status") != "settlement_pending"
        )

    def futures_mark_diagnostic(self) -> dict[str, Any] | None:
        if self._last_futures_mark_diagnostic is None:
            return None
        return json.loads(json.dumps(self._last_futures_mark_diagnostic))

    def SetCurrentSignalContext(self, metadata: dict[str, Any] | None) -> None:  # noqa: N802
        ctx = dict(metadata or {})
        self._current_signal_metadata = ctx
        if ctx:
            recorded = dict(ctx)
            recorded["_had_open_futures_before_signal"] = (
                self._has_open_or_pending_stock_futures()
            )
            self._drained_signal_contexts.append(recorded)
            if len(self._drained_signal_contexts) > 256:
                self._drained_signal_contexts = self._drained_signal_contexts[-256:]

    def ClearCurrentSignalContext(self) -> None:  # noqa: N802
        self._current_signal_metadata = {}
        if callable(self._after_signal_context_hook):
            self._after_signal_context_hook()

    def SetRiskPolicy(self, policy: RiskPolicy | Mapping[str, Any] | None) -> None:  # noqa: N802
        if policy is None:
            self._configured_risk_policy = None
            return
        if isinstance(policy, RiskPolicy):
            self._configured_risk_policy = policy
            return
        if isinstance(policy, Mapping):
            self._configured_risk_policy = RiskPolicy.from_mapping(policy)
            return
        raise TypeError(f"Unsupported risk_policy type: {type(policy).__name__}")

    def SetRiskPolicyRef(self, ref: str | None) -> None:  # noqa: N802
        cleaned = str(ref or "").strip()
        self._configured_risk_policy_id = cleaned or None

    def SetCapitalPoolReader(self, reader: Callable[[str], Any] | None) -> None:  # noqa: N802
        self._capital_pool_reader = reader

    def BindRuntimeBinding(self, binding: Mapping[str, Any] | None) -> None:  # noqa: N802
        if not isinstance(binding, Mapping):
            self._bound_runtime_binding = None
            return
        self._bound_runtime_binding = dict(binding)
        pool_id = str(binding.get("capital_pool_id") or "").strip()
        if pool_id:
            self._capital_pool_id = pool_id

    def _active_holidays(self) -> tuple[Any, ...]:
        if self._holidays_override is not None:
            return self._holidays_override
        ctx_holidays = self._current_signal_metadata.get("holidays")
        if isinstance(ctx_holidays, (list, tuple, set, frozenset)):
            return tuple(ctx_holidays)
        return tuple(governed_twse_holidays())

    def _resolve_persona_binding_id(self, explicit_binding_id: str | None = None) -> str:
        for candidate in (
            explicit_binding_id,
            self._current_signal_metadata.get("persona_capital_binding_id"),
            self._current_signal_metadata.get("persona_binding_id"),
            self._current_signal_metadata.get("binding_id"),
            self._performance_binding_id,
            self._default_persona_binding_id,
        ):
            cleaned = str(candidate or "").strip()
            if cleaned:
                return cleaned
        return "default_binding"

    @staticmethod
    def _try_parse_instrument_id(value: Any) -> dict[str, Any] | None:
        text = str(value or "").strip()
        if not text or ":" not in text:
            return None
        try:
            return parse_instrument_id(text)
        except Exception:
            return None

    @staticmethod
    def _normalise_underlying_symbol(
        symbol: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        meta = metadata or {}
        for key in ("underlying", "underlying_symbol", "contract_symbol"):
            val = str(meta.get(key) or "").strip()
            if val:
                try:
                    return normalize_underlying_code(val)
                except Exception:
                    return val
        raw = str(symbol or "").strip()
        parsed = PaperExecutionAlgorithm._try_parse_instrument_id(raw)
        if parsed is not None:
            if parsed["kind"] == "cash_share":
                return str(parsed["underlying"])
            contract = str(parsed["contract"])
            if contract in _PILOT_FUTURES_PROFILES:
                return str(_PILOT_FUTURES_PROFILES[contract]["underlying"])
            return contract
        try:
            return normalize_underlying_code(raw)
        except Exception:
            return raw

    @staticmethod
    def _futures_contract_profile(
        contract: str | None,
        *,
        underlying: str | None = None,
        multiplier: float | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        meta = dict(metadata or {})
        clean_contract = str(contract or "").strip().upper()
        clean_underlying = str(underlying or "").strip().upper()
        if not clean_contract and clean_underlying in _PILOT_UNDERLYING_TO_CONTRACT:
            clean_contract = _PILOT_UNDERLYING_TO_CONTRACT[clean_underlying]
        base = dict(_PILOT_FUTURES_PROFILES.get(clean_contract, {}))
        if not clean_underlying and base.get("underlying"):
            clean_underlying = str(base["underlying"])
        resolved_mult = float(
            multiplier
            if multiplier is not None and float(multiplier) > 0
            else meta.get("multiplier")
            or base.get("multiplier")
            or 2000.0
        )
        margin_tier = str(
            meta.get("margin_tier") or base.get("margin_tier") or "pct_tier_1"
        ).strip()
        fixed_init = meta.get("fixed_initial_margin")
        if fixed_init is None and margin_tier.startswith("fixed"):
            fixed_init = meta.get("initial_margin") or base.get("fixed_initial_margin")
        fixed_maint = meta.get("fixed_maintenance_margin")
        if fixed_maint is None and margin_tier.startswith("fixed"):
            fixed_maint = meta.get("maintenance_margin") or base.get("fixed_maintenance_margin")
        init_rate = meta.get("initial_margin_rate")
        if init_rate is None:
            init_rate = meta.get("initial_rate") or base.get("initial_rate") or 0.135
        maint_rate = meta.get("maintenance_margin_rate")
        if maint_rate is None:
            maint_rate = meta.get("maintenance_rate") or base.get("maintenance_rate") or 0.1035
        underlying_kind = str(
            meta.get("underlying_kind")
            or base.get("underlying_kind")
            or ("etf" if clean_underlying.startswith("00") or clean_contract == "SRF" else "stock")
        )
        return {
            "contract": clean_contract or None,
            "underlying": clean_underlying or None,
            "multiplier": resolved_mult,
            "margin_tier": margin_tier,
            "fixed_initial_margin": float(fixed_init) if fixed_init is not None else None,
            "fixed_maintenance_margin": float(fixed_maint) if fixed_maint is not None else None,
            "initial_rate": float(init_rate),
            "maintenance_rate": float(maint_rate),
            "underlying_kind": underlying_kind,
        }

    @classmethod
    def compute_futures_margin_per_contract(
        cls,
        contract: str | None,
        price: float,
        *,
        multiplier: float | None = None,
        underlying: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, float]:
        """Compute initial and maintenance margin per contract at ``price``."""
        prof = cls._futures_contract_profile(
            contract,
            underlying=underlying,
            multiplier=multiplier,
            metadata=metadata,
        )
        mult = float(prof["multiplier"])
        notional = float(price) * mult
        if prof["fixed_initial_margin"] is not None:
            init_m = float(prof["fixed_initial_margin"])
        else:
            init_m = round(notional * float(prof["initial_rate"]), 6)
        if prof["fixed_maintenance_margin"] is not None:
            maint_m = float(prof["fixed_maintenance_margin"])
        else:
            maint_m = round(notional * float(prof["maintenance_rate"]), 6)
        return {
            "multiplier": mult,
            "notional_per_contract": notional,
            "initial_margin_per_contract": init_m,
            "maintenance_margin_per_contract": maint_m,
        }

    @classmethod
    def max_contracts_for_capital(
        cls,
        contract: str,
        price: float,
        capital: float,
        *,
        multiplier: float | None = None,
        underlying: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        margin_info = cls.compute_futures_margin_per_contract(
            contract,
            price,
            multiplier=multiplier,
            underlying=underlying,
            metadata=metadata,
        )
        init_m = margin_info["initial_margin_per_contract"]
        if init_m <= 0 or capital <= 0:
            return 0
        return int(math.floor(float(capital) / init_m))

    def _update_instrument_marks_for_symbol(
        self,
        symbol: str,
        price: float,
        *,
        as_of: str | None = None,
        source: str | None = None,
    ) -> None:
        raw = str(symbol or "").strip()
        parsed = self._try_parse_instrument_id(raw)
        try:
            norm_u = self._normalise_underlying_symbol(raw)
        except Exception:
            norm_u = raw
        for (b_id, u_code, inst_id), pos in self._instrument_positions.items():
            if abs(float(pos.get("quantity", 0.0))) <= 1e-12 and pos.get("status") != "settlement_pending":
                continue
            if parsed is not None and inst_id == parsed["instrument_id"]:
                self._apply_mark_to_position(pos, price, as_of=as_of, source=source)
            elif parsed is None and pos.get("kind") == "cash_share" and (
                u_code == norm_u or pos.get("symbol") == raw or inst_id == f"eq:{norm_u}"
            ):
                self._apply_mark_to_position(pos, price, as_of=as_of, source=source)

    def _apply_mark_to_position(
        self,
        pos: dict[str, Any],
        price: float,
        *,
        as_of: str | None = None,
        source: str | None = None,
    ) -> None:
        pos["mark_price"] = float(price)
        if as_of is not None:
            pos["mark_as_of"] = str(as_of)
        if source is not None:
            pos["mark_source"] = str(source)
        qty = float(pos.get("quantity", 0.0))
        mult = float(pos.get("multiplier", 1.0))
        avg_entry = float(pos.get("average_entry_price", 0.0))
        if abs(qty) <= 1e-12:
            pos["unrealized_pnl"] = 0.0
            pos["initial_margin_reserved"] = 0.0
            pos["maintenance_margin_required"] = 0.0
            return
        pos["unrealized_pnl"] = qty * mult * (float(price) - avg_entry)
        if pos.get("kind") == "stock_future":
            margin_info = self.compute_futures_margin_per_contract(
                pos.get("contract"),
                float(price),
                multiplier=mult,
                underlying=pos.get("normalised_underlying"),
                metadata=pos,
            )
            pos["initial_margin_per_contract"] = margin_info["initial_margin_per_contract"]
            pos["maintenance_margin_per_contract"] = margin_info["maintenance_margin_per_contract"]
            pos["initial_margin_reserved"] = abs(qty) * margin_info["initial_margin_per_contract"]
            pos["maintenance_margin_required"] = abs(qty) * margin_info["maintenance_margin_per_contract"]

    @property
    def instrument_positions(self) -> dict[tuple[str, str, str], dict[str, Any]]:
        """Active positions keyed by ``(persona_binding_id, normalised_underlying, instrument_id)``."""
        return {
            key: dict(pos)
            for key, pos in self._instrument_positions.items()
            if abs(float(pos.get("quantity", 0.0))) > 1e-12
            or pos.get("status") == "settlement_pending"
        }

    def get_instrument_position(
        self,
        instrument_id: str,
        *,
        underlying: str | None = None,
        persona_binding_id: str | None = None,
    ) -> dict[str, Any] | None:
        target_inst = str(instrument_id or "").strip()
        binding_filter = (
            str(persona_binding_id).strip() if persona_binding_id is not None else None
        )
        underlying_filter = (
            self._normalise_underlying_symbol(underlying)
            if underlying is not None
            else None
        )
        for (b_id, norm_u, inst_id), pos in self._instrument_positions.items():
            if inst_id != target_inst:
                continue
            if binding_filter is not None and b_id != binding_filter:
                continue
            if underlying_filter is not None and norm_u != underlying_filter:
                continue
            return dict(pos)
        return None

    def exposure_by_underlying(
        self,
        underlying: str,
        *,
        persona_binding_id: str | None = None,
    ) -> dict[str, Any]:
        """Report per-instrument exposure and net delta in shares (``Σ q × multiplier``)."""
        norm_u = self._normalise_underlying_symbol(underlying)
        binding_filter = (
            str(persona_binding_id).strip() if persona_binding_id is not None else None
        )
        instruments: dict[str, dict[str, Any]] = {}
        net_delta_shares = 0.0
        gross_notional = 0.0
        net_notional = 0.0
        for (b_id, u_code, inst_id), pos in sorted(self._instrument_positions.items()):
            if u_code != norm_u:
                continue
            if binding_filter is not None and b_id != binding_filter:
                continue
            qty = float(pos.get("quantity", 0.0))
            if abs(qty) <= 1e-12 and pos.get("status") != "settlement_pending":
                continue
            mult = float(pos.get("multiplier", 1.0))
            mark = float(pos.get("mark_price", 0.0))
            delta_shares = qty * mult
            signed_notional = delta_shares * mark
            abs_notional = abs(delta_shares) * mark
            net_delta_shares += delta_shares
            gross_notional += abs_notional
            net_notional += signed_notional
            instruments[inst_id] = {
                "key": (b_id, u_code, inst_id),
                "persona_binding_id": b_id,
                "normalised_underlying": u_code,
                "instrument_id": inst_id,
                "kind": pos.get("kind"),
                "contract": pos.get("contract"),
                "contract_month": pos.get("contract_month"),
                "quantity": qty,
                "multiplier": mult,
                "delta_shares": delta_shares,
                "average_entry_price": float(pos.get("average_entry_price", 0.0)),
                "mark_price": mark,
                "notional": abs_notional,
                "signed_notional": signed_notional,
                "unrealized_pnl": float(pos.get("unrealized_pnl", 0.0)),
                "realized_pnl": float(pos.get("realized_pnl", 0.0)),
                "costs": float(pos.get("costs", 0.0)),
                "initial_margin_reserved": float(pos.get("initial_margin_reserved", 0.0)),
                "maintenance_margin_required": float(pos.get("maintenance_margin_required", 0.0)),
                "status": pos.get("status", "open"),
            }
        return {
            "persona_binding_id": binding_filter or self._resolve_persona_binding_id(),
            "normalised_underlying": norm_u,
            "instruments": instruments,
            "net_delta_shares": net_delta_shares,
            "gross_notional": gross_notional,
            "net_notional": net_notional,
        }

    def underlying_exposure_summary(
        self,
        underlying: str,
        *,
        persona_binding_id: str | None = None,
    ) -> dict[str, Any]:
        return self.exposure_by_underlying(
            underlying,
            persona_binding_id=persona_binding_id,
        )

    def ledger_reconciliation_snapshot(self) -> dict[str, Any]:
        """Compute cash, realized, unrealized, costs, equity, and margin totals."""
        equity_holdings_value = 0.0
        futures_unrealized = 0.0
        total_unrealized = 0.0
        margin_reserved = 0.0
        maintenance_required = 0.0
        gross_notional = 0.0
        signed_net_notional = 0.0
        for pos in self._instrument_positions.values():
            qty = float(pos.get("quantity", 0.0))
            if abs(qty) <= 1e-12:
                continue
            mult = float(pos.get("multiplier", 1.0))
            mark = float(pos.get("mark_price", 0.0))
            avg_entry = float(pos.get("average_entry_price", 0.0))
            unreal = qty * mult * (mark - avg_entry)
            pos["unrealized_pnl"] = unreal
            total_unrealized += unreal
            signed_notional = qty * mult * mark
            gross_notional += abs(signed_notional)
            signed_net_notional += signed_notional
            if pos.get("kind") == "stock_future":
                futures_unrealized += unreal
                margin_reserved += float(pos.get("initial_margin_reserved", 0.0))
                maintenance_required += float(pos.get("maintenance_margin_required", 0.0))
            else:
                equity_holdings_value += qty * mult * mark

        effective_cash = float(self._cash) - float(self._injected_equity_capital)
        equity = effective_cash + equity_holdings_value + futures_unrealized
        realized = float(self._realized_pnl)
        costs = float(self._total_costs)
        total_pnl = realized + total_unrealized - costs
        free_margin = equity - margin_reserved
        return {
            "initial_cash": float(self._initial_cash),
            "cash": float(self._cash),
            "realized_pnl": realized,
            "unrealized_pnl": total_unrealized,
            "costs": costs,
            "total_pnl": total_pnl,
            "equity": equity,
            "equity_minus_initial_cash": equity - float(self._initial_cash),
            "margin_reserved": margin_reserved,
            "free_margin": free_margin,
            "maintenance_margin_required": maintenance_required,
            "margin_to_equity": (margin_reserved / equity) if equity > 0 else 0.0,
            "gross_notional": gross_notional,
            "net_notional": abs(signed_net_notional),
            "signed_net_notional": signed_net_notional,
            "margin_call_active": bool(self._margin_call_active),
            "futures_mark_diagnostic": self.futures_mark_diagnostic(),
        }

    def assert_ledger_reconciliation(self) -> dict[str, Any]:
        snap = self.ledger_reconciliation_snapshot()
        lhs = snap["equity"] - snap["initial_cash"]
        rhs = snap["realized_pnl"] + snap["unrealized_pnl"] - snap["costs"]
        if not math.isclose(lhs, rhs, rel_tol=0.0, abs_tol=1e-5):
            raise AssertionError(
                f"Paper ledger reconciliation failed: equity - initial_cash ({lhs:.6f}) != "
                f"realized + unrealized - costs ({rhs:.6f}); snapshot={snap}"
            )
        return snap

    def _resolve_active_risk_policy(self) -> RiskPolicy:
        if self._configured_risk_policy is not None:
            return self._configured_risk_policy
        binding: Mapping[str, Any] | None = self._bound_runtime_binding
        if self._binding_resolver is not None:
            resolver_fn = getattr(self._binding_resolver, "resolve", None)
            if callable(resolver_fn):
                resolved_b = resolver_fn()
                if isinstance(resolved_b, Mapping):
                    binding = resolved_b
            elif callable(self._binding_resolver):
                resolved_b = self._binding_resolver()
                if isinstance(resolved_b, Mapping):
                    binding = resolved_b
        bound_policy = resolve_bound_pool_risk_policy(
            capital_pool_id=self._capital_pool_id,
            binding=binding,
            capital_pool_reader=self._capital_pool_reader,
            risk_policy_ref=self._configured_risk_policy_id,
            risk_policy_loader=self._risk_policy_loader,
        )
        if bound_policy is not None:
            return bound_policy
        return RiskPolicy(risk_policy_id="paper-unbound")

    @staticmethod
    def _parse_strict_iso_date(raw: Any) -> Any:
        if raw is None or isinstance(raw, bool):
            return None
        if hasattr(raw, "year") and hasattr(raw, "month") and hasattr(raw, "day") and not isinstance(raw, datetime):
            return raw
        text = str(raw).strip()
        if len(text) < 10:
            return None
        date_part = text[:10]
        if len(text) > 10 and text[10] not in ("T", " "):
            return None
        try:
            return datetime.fromisoformat(date_part).date()
        except ValueError:
            return None

    def EvaluateOrderRiskGate(  # noqa: N802
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        price: float | None,
        instrument_id: str,
        kind: str = "cash_share",
        multiplier: float = 1.0,
        contract: str | None = None,
        contract_month: str | None = None,
        price_basis: str | None = None,
        price_source: str | None = None,
        price_as_of: str | None = None,
        persona_binding_id: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate the pre-trade order-level risk gate without mutating the ledger."""
        binding_id = self._resolve_persona_binding_id(persona_binding_id)
        norm_u = self._normalise_underlying_symbol(
            symbol, self._current_signal_metadata
        )
        clean_kind = str(kind or "cash_share").strip().lower() or "cash_share"
        signed_order_qty = float(quantity) if str(side).lower() == "buy" else -float(quantity)
        key = (binding_id, norm_u, instrument_id)
        existing_pos = self._instrument_positions.get(key)
        q_before = float(existing_pos.get("quantity", 0.0)) if existing_pos else 0.0
        if clean_kind == "cash_share" and existing_pos is None:
            try:
                native, exchange = normalize_taiwan_symbol(symbol)
                q_before = self._taiwan_underlying_holding_quantity(native, exchange)
            except Exception:
                q_before = float(self._holding(symbol).Quantity)
        q_after = q_before + signed_order_qty
        is_sign_reversal = (
            abs(q_before) > 1e-12
            and abs(q_after) > 1e-12
            and ((q_before > 0 and q_after < 0) or (q_before < 0 and q_after > 0))
        )
        is_opening_or_increasing = (abs(q_after) > abs(q_before) + 1e-12) or is_sign_reversal

        raw_decision_id = str(
            self._current_signal_metadata.get("decision_id") or ""
        ).strip()
        try:
            policy = self._resolve_active_risk_policy()
        except (RiskPolicyError, RiskPolicyLoadError, OSError, KeyError, ValueError, TypeError) as exc:
            decision_id = raw_decision_id or f"rg-{uuid.uuid4()}"
            return {
                "accepted": False,
                "risk_status": "rejected",
                "reason": "risk_policy_unavailable",
                "required": str(self._configured_risk_policy_id or "bound_pool_risk_policy"),
                "available": str(exc),
                "limit": "valid_active_risk_policy",
                "instrument_id": instrument_id,
                "decision_id": decision_id,
            }
        holidays = self._active_holidays()

        # 1. Check settlement_pending on this underlying
        for (b_id, u_code, _inst_id), pos in self._instrument_positions.items():
            if b_id == binding_id and u_code == norm_u and pos.get("status") == "settlement_pending":
                decision_id = raw_decision_id or f"rg-{uuid.uuid4()}"
                return {
                    "accepted": False,
                    "risk_status": "rejected",
                    "reason": "settlement_pending",
                    "required": 0.0,
                    "available": 0.0,
                    "limit": 0.0,
                    "instrument_id": instrument_id,
                    "decision_id": decision_id,
                }

        # 2. Check margin_call_active (blocks opening/increasing/reversing; allows pure reducing)
        if self._margin_call_active and is_opening_or_increasing:
            snap = self.ledger_reconciliation_snapshot()
            decision_id = raw_decision_id or f"rg-{uuid.uuid4()}"
            return {
                "accepted": False,
                "risk_status": "rejected",
                "reason": "margin_call_active",
                "required": float(snap["maintenance_margin_required"]),
                "available": float(snap["equity"]),
                "limit": float(snap["maintenance_margin_required"]),
                "instrument_id": instrument_id,
                "decision_id": decision_id,
            }

        # 3. Check candidates membership & expiring contract on last_trading_day
        candidates_raw = self._current_signal_metadata.get("candidates")
        if candidates_raw is None:
            candidates_raw = self._current_signal_metadata.get("admitted_candidates")
        if isinstance(candidates_raw, (list, tuple, set, frozenset)):
            admitted_ids: list[str] = []
            for item in candidates_raw:
                if isinstance(item, Mapping):
                    cid = str(item.get("instrument_id") or "").strip()
                    if cid:
                        admitted_ids.append(cid)
                elif isinstance(item, str) and item.strip():
                    admitted_ids.append(item.strip())
            if instrument_id not in admitted_ids:
                decision_id = raw_decision_id or f"rg-{uuid.uuid4()}"
                return {
                    "accepted": False,
                    "risk_status": "rejected",
                    "reason": "instrument_not_in_candidates",
                    "required": instrument_id,
                    "available": admitted_ids,
                    "limit": admitted_ids,
                    "instrument_id": instrument_id,
                    "decision_id": decision_id,
                }

        # ``trade_date`` is the D-1 settlement session on a signal carrying open
        # futures; the entry gate needs the decision day, which stays in
        # ``as_of_trading_day``.
        trade_date_raw = (
            self._current_signal_metadata.get("as_of_trading_day")
            or self._current_signal_metadata.get("trade_date")
            or self._current_signal_metadata.get("as_of_date")
            or self._current_signal_metadata.get("signal_date")
        )
        parsed_trade_date = self._parse_strict_iso_date(trade_date_raw)

        if clean_kind == "stock_future" and contract_month and parsed_trade_date is not None:
            try:
                ltd = last_trading_day(str(contract_month), holidays)
                if is_opening_or_increasing and parsed_trade_date >= ltd:
                    decision_id = raw_decision_id or f"rg-{uuid.uuid4()}"
                    return {
                        "accepted": False,
                        "risk_status": "rejected",
                        "reason": "instrument_not_in_candidates",
                        "required": instrument_id,
                        "available": [],
                        "limit": ltd.isoformat(),
                        "instrument_id": instrument_id,
                        "decision_id": decision_id,
                    }
            except Exception:
                decision_id = raw_decision_id or f"rg-{uuid.uuid4()}"
                return {
                    "accepted": False,
                    "risk_status": "rejected",
                    "reason": "instrument_not_in_candidates",
                    "required": instrument_id,
                    "available": [],
                    "limit": str(contract_month),
                    "instrument_id": instrument_id,
                    "decision_id": decision_id,
                }

        # 4. Check price freshness and futures price basis (strict D-1 settlement requirement)
        if clean_kind == "stock_future":
            src_lower = str(price_source or "").strip().lower()
            basis_lower = str(price_basis or "").strip().lower()
            disallowed_stock_sources = {
                "stock_close",
                "equity_close",
                "twse_close",
                "tpex_close",
                "underlying_close",
            }
            if (
                self._current_signal_metadata.get("price_stale") is True
                or price is None
                or not math.isfinite(float(price))
                or float(price) <= 0
                or basis_lower != "futures_daily_settlement_prev_session"
                or src_lower in disallowed_stock_sources
            ):
                decision_id = raw_decision_id or f"rg-{uuid.uuid4()}"
                return {
                    "accepted": False,
                    "risk_status": "rejected",
                    "reason": "price_stale",
                    "required": "futures_daily_settlement_prev_session",
                    "available": basis_lower or src_lower or "missing",
                    "limit": "futures_daily_settlement_prev_session",
                    "instrument_id": instrument_id,
                    "decision_id": decision_id,
                }
            price_date_raw = (
                self._current_signal_metadata.get("price_date")
                or self._current_signal_metadata.get("settlement_date")
                or price_as_of
            )
            parsed_price_date = self._parse_strict_iso_date(price_date_raw)
            if parsed_trade_date is None or parsed_price_date is None:
                decision_id = raw_decision_id or f"rg-{uuid.uuid4()}"
                return {
                    "accepted": False,
                    "risk_status": "rejected",
                    "reason": "price_stale",
                    "required": "valid_d_minus_1_settlement_and_trade_date",
                    "available": f"trade_date={trade_date_raw!r},price_date={price_date_raw!r}",
                    "limit": "valid_d_minus_1_settlement_and_trade_date",
                    "instrument_id": instrument_id,
                    "decision_id": decision_id,
                }
            expected_prev = previous_trading_day(parsed_trade_date, holidays)
            if parsed_price_date != expected_prev:
                decision_id = raw_decision_id or f"rg-{uuid.uuid4()}"
                return {
                    "accepted": False,
                    "risk_status": "rejected",
                    "reason": "price_stale",
                    "required": expected_prev.isoformat(),
                    "available": parsed_price_date.isoformat(),
                    "limit": expected_prev.isoformat(),
                    "instrument_id": instrument_id,
                    "decision_id": decision_id,
                }

        ref_price = float(price or 0.0)
        # 5. Compute post-order exposure and margin marked at ref_price
        equity_holdings_after = 0.0
        futures_unrealized_after = 0.0
        gross_notional_after = 0.0
        signed_net_notional_after = 0.0
        margin_reserved_after = 0.0
        underlying_notional_after: dict[str, float] = {}

        seen_target = False
        for pos_key, pos in self._instrument_positions.items():
            if pos_key == key:
                seen_target = True
                qty_i = q_after
                mult_i = float(multiplier)
                mark_i = ref_price
                avg_i = float(pos.get("average_entry_price", ref_price))
                if abs(q_before) <= 1e-12:
                    avg_i = ref_price
                elif (q_before > 0 and signed_order_qty > 0) or (q_before < 0 and signed_order_qty < 0):
                    avg_i = (abs(q_before) * avg_i + abs(signed_order_qty) * ref_price) / abs(q_after)
                elif abs(signed_order_qty) > abs(q_before):
                    avg_i = ref_price
                kind_i = clean_kind
                contract_i = contract or pos.get("contract")
                u_i = norm_u
                meta_i = {**pos, **self._current_signal_metadata}
            else:
                qty_i = float(pos.get("quantity", 0.0))
                mult_i = float(pos.get("multiplier", 1.0))
                mark_i = float(pos.get("mark_price", 0.0))
                avg_i = float(pos.get("average_entry_price", mark_i))
                kind_i = str(pos.get("kind", "cash_share"))
                contract_i = pos.get("contract")
                u_i = str(pos.get("normalised_underlying", ""))
                meta_i = pos

            if abs(qty_i) <= 1e-12:
                continue
            signed_notional_i = qty_i * mult_i * mark_i
            gross_notional_after += abs(signed_notional_i)
            signed_net_notional_after += signed_notional_i
            if u_i:
                underlying_notional_after[u_i] = underlying_notional_after.get(u_i, 0.0) + abs(signed_notional_i)
            unreal_i = qty_i * mult_i * (mark_i - avg_i)
            if kind_i == "stock_future":
                futures_unrealized_after += unreal_i
                m_info = self.compute_futures_margin_per_contract(
                    contract_i,
                    mark_i,
                    multiplier=mult_i,
                    underlying=u_i,
                    metadata=meta_i,
                )
                margin_reserved_after += abs(qty_i) * m_info["initial_margin_per_contract"]
            else:
                equity_holdings_after += qty_i * mult_i * mark_i

        if not seen_target and abs(q_after) > 1e-12:
            signed_notional_t = q_after * float(multiplier) * ref_price
            gross_notional_after += abs(signed_notional_t)
            signed_net_notional_after += signed_notional_t
            if norm_u:
                underlying_notional_after[norm_u] = underlying_notional_after.get(norm_u, 0.0) + abs(signed_notional_t)
            if clean_kind == "stock_future":
                m_info = self.compute_futures_margin_per_contract(
                    contract,
                    ref_price,
                    multiplier=multiplier,
                    underlying=norm_u,
                    metadata=self._current_signal_metadata,
                )
                margin_reserved_after += abs(q_after) * m_info["initial_margin_per_contract"]
            else:
                equity_holdings_after += q_after * float(multiplier) * ref_price

        # For cash_share orders, cash changes by -signed_order_qty * ref_price, which cancels
        # with the change in equity_holdings_after at ref_price; so equity_after marked at
        # ref_price equals current cash (adjusted for cash_share delta) + equity_holdings_after + futures_unrealized_after.
        cash_after_order = float(self._cash) - float(self._injected_equity_capital)
        if clean_kind == "cash_share":
            cash_after_order -= signed_order_qty * float(multiplier) * ref_price
        elif existing_pos is not None and abs(q_before) > 1e-12:
            # If reducing/closing/flipping an existing future at ref_price, realized + unrealized
            # at ref_price equals q_before * mult * (ref_price - avg_before)
            avg_before = float(existing_pos.get("average_entry_price", ref_price))
            total_pos_pnl_at_ref = q_before * float(multiplier) * (ref_price - avg_before)
            futures_unrealized_after = (
                futures_unrealized_after
                - (
                    q_after * float(multiplier) * (ref_price - (
                        avg_before
                        if (q_before > 0 and q_after > 0) or (q_before < 0 and q_after < 0)
                        else ref_price
                    ))
                    if abs(q_after) > 1e-12 and not ((q_before > 0 and signed_order_qty > 0) or (q_before < 0 and signed_order_qty < 0))
                    else 0.0
                )
                + (
                    total_pos_pnl_at_ref
                    if not ((q_before > 0 and signed_order_qty > 0) or (q_before < 0 and signed_order_qty < 0))
                    else 0.0
                )
            )
        equity_after = cash_after_order + equity_holdings_after + futures_unrealized_after
        if equity_after > 0:
            gross_mult = gross_notional_after / equity_after
            net_mult = abs(signed_net_notional_after) / equity_after
            leverage_mult = gross_notional_after / equity_after
            target_weights = {
                u_key: val / equity_after for u_key, val in underlying_notional_after.items()
            }
        else:
            gross_mult = float("inf")
            net_mult = float("inf")
            leverage_mult = float("inf")
            target_weights = {u_key: 1e9 for u_key in underlying_notional_after}

        order_asset_classes = ["future"] if clean_kind == "stock_future" else ["equity"]
        eval_ctx = RiskPolicyEvaluationContext(
            target_type=RiskPolicyTargetType.RUNTIME_ACTION.value,
            target_id=str(
                raw_decision_id
                or self._current_signal_metadata.get("signal_id")
                or instrument_id
            ),
            capital_pool_id=str(
                self._capital_pool_id
                or self._current_signal_metadata.get("capital_pool_id")
                or self._taiwan_capital_pool_id()
            ),
            target_weights=target_weights,
            gross_exposure=gross_mult if math.isfinite(gross_mult) else 1e9,
            net_exposure=net_mult if math.isfinite(net_mult) else 1e9,
            leverage=leverage_mult if math.isfinite(leverage_mult) else 1e9,
            asset_classes=order_asset_classes,
            decision_id=raw_decision_id or None,
            instrument_id=instrument_id,
        )
        evaluation = RiskPolicyEvaluator().evaluate(policy, eval_ctx)
        decision_id = raw_decision_id or evaluation.trace_id or evaluation.target_id

        failed_checks = [chk for chk in evaluation.checks if chk.status == "failed"]
        failed_codes = {chk.code for chk in failed_checks}
        failed_names = {chk.check_name for chk in failed_checks}
        if (
            "asset_class_not_allowed" in failed_codes
            or "forbidden_asset_class" in failed_codes
            or "asset_class" in failed_names
        ):
            return {
                "accepted": False,
                "risk_status": "rejected",
                "reason": "asset_class_not_allowed",
                "required": order_asset_classes,
                "available": list(policy.allowed_asset_classes),
                "limit": list(policy.allowed_asset_classes or policy.forbidden_asset_classes),
                "instrument_id": instrument_id,
                "decision_id": decision_id,
                "evaluation": evaluation.to_dict(),
            }

        if (
            clean_kind == "stock_future"
            and is_opening_or_increasing
            and margin_reserved_after > equity_after + 1e-9
        ):
            return {
                "accepted": False,
                "risk_status": "rejected",
                "reason": "insufficient_initial_margin",
                "required": float(margin_reserved_after),
                "available": float(equity_after),
                "limit": float(equity_after),
                "instrument_id": instrument_id,
                "decision_id": decision_id,
                "evaluation": evaluation.to_dict(),
            }

        if evaluation.rejected:
            max_single_w = max(target_weights.values(), default=0.0)
            code_to_reason = {
                "pool_risk_policy": (
                    "single_name_limit",
                    float(max_single_w),
                    float(policy.max_single_name_weight) if policy.max_single_name_weight is not None else 0.0,
                ),
                "max_single_name_weight_limit_exceeded": (
                    "single_name_limit",
                    float(max_single_w),
                    float(policy.max_single_name_weight) if policy.max_single_name_weight is not None else 0.0,
                ),
                "leverage_limit_exceeded": (
                    "leverage_limit",
                    float(leverage_mult),
                    float(policy.max_leverage) if policy.max_leverage is not None else 0.0,
                ),
                "gross_exposure_limit_exceeded": (
                    "gross_limit",
                    float(gross_mult),
                    float(policy.gross_limit) if policy.gross_limit is not None else 0.0,
                ),
                "net_exposure_limit_exceeded": (
                    "net_limit",
                    float(net_mult),
                    float(policy.net_limit) if policy.net_limit is not None else 0.0,
                ),
            }
            for chk in failed_checks:
                if chk.check_name == "max_single_name_weight" or chk.code in code_to_reason:
                    reason_code, req_val, lim_val = code_to_reason.get(
                        chk.code,
                        (
                            "single_name_limit",
                            float(max_single_w),
                            float(policy.max_single_name_weight or 0.0),
                        ),
                    )
                    return {
                        "accepted": False,
                        "risk_status": "rejected",
                        "reason": reason_code,
                        "required": req_val,
                        "available": float(equity_after),
                        "limit": lim_val,
                        "instrument_id": instrument_id,
                        "decision_id": decision_id,
                        "evaluation": evaluation.to_dict(),
                    }
            return {
                "accepted": False,
                "risk_status": "rejected",
                "reason": "leverage_limit",
                "required": float(leverage_mult),
                "available": float(equity_after),
                "limit": float(policy.max_leverage or 0.0),
                "instrument_id": instrument_id,
                "decision_id": decision_id,
                "evaluation": evaluation.to_dict(),
            }

        return {
            "accepted": True,
            "risk_status": "succeeded",
            "reason": None,
            "required": float(margin_reserved_after if clean_kind == "stock_future" else gross_mult),
            "available": float(equity_after),
            "limit": float(policy.max_leverage or policy.gross_limit or 0.0),
            "instrument_id": instrument_id,
            "decision_id": decision_id,
            "evaluation": evaluation.to_dict(),
        }

    def _reject_invalid_paper_fill(
        self,
        symbol: str,
        action: str,
        *,
        reason: str,
        broker_submission_status: str = "invalid_paper_fill",
        submitted_to_broker: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        diagnostic = {
            "reject_reason": reason,
            "rejection_status": "rejected",
            "order_status": "rejected",
            "rejected_order_count": 1,
        }
        if metadata:
            diagnostic.update(dict(metadata))
        self._publish(
            "order_rejection",
            str(symbol),
            0.0,
            action,
            broker_submission_status=broker_submission_status,
            submitted_to_broker=submitted_to_broker,
            metadata=diagnostic,
        )

    def SubmitTaiwanBrokerOrder(  # noqa: N802
        self,
        symbol: str,
        *,
        signal_id: str,
        side: str,
        quantity: float,
        quantity_type: str,
        action: str,
        order_type: str = "MARKET",
        limit_price: float | None = None,
        instrument_id: str | None = None,
        kind: str | None = None,
        multiplier: float | None = None,
        contract_month: str | None = None,
        price_basis: str | None = None,
    ) -> None:
        """Place a Taiwan paper order (cash_share or stock_future) via risk gate and broker."""
        parsed_sym = self._try_parse_instrument_id(str(symbol))
        resolved_inst_id = str(
            instrument_id
            or self._current_signal_metadata.get("instrument_id")
            or (parsed_sym["instrument_id"] if parsed_sym else "")
        ).strip()
        parsed_inst = self._try_parse_instrument_id(resolved_inst_id) if resolved_inst_id else None
        resolved_kind = str(
            kind
            or self._current_signal_metadata.get("kind")
            or self._current_signal_metadata.get("instrument_kind")
            or (parsed_inst["kind"] if parsed_inst else "cash_share")
        ).strip().lower()
        if resolved_inst_id.startswith("ssf:"):
            resolved_kind = "stock_future"

        if parsed_sym is not None and parsed_sym["kind"] == "stock_future":
            contract_code = str(parsed_sym["contract"])
            native = str(
                self._current_signal_metadata.get("underlying")
                or _PILOT_FUTURES_PROFILES.get(contract_code, {}).get("underlying")
                or contract_code
            )
            exchange = "TAIFEX"
        else:
            native, exchange = normalize_taiwan_symbol(symbol)

        if not resolved_inst_id:
            resolved_inst_id = f"eq:{native}"

        matched_cand: Mapping[str, Any] = {}
        raw_cands = (
            self._current_signal_metadata.get("candidates")
            or self._current_signal_metadata.get("admitted_candidates")
        )
        if isinstance(raw_cands, (list, tuple)):
            for cand in raw_cands:
                if not isinstance(cand, Mapping):
                    continue
                if str(cand.get("instrument_id") or "").strip() == resolved_inst_id:
                    matched_cand = cand
                    break

        contract_code = None
        resolved_month = str(
            contract_month
            or self._current_signal_metadata.get("contract_month")
            or matched_cand.get("contract_month")
            or (parsed_inst.get("contract_month") if parsed_inst else "")
            or ""
        ).strip() or None
        if resolved_kind == "stock_future":
            contract_code = str(
                (parsed_inst.get("contract") if parsed_inst else None)
                or self._current_signal_metadata.get("contract")
                or matched_cand.get("contract")
                or _PILOT_UNDERLYING_TO_CONTRACT.get(native, "")
            ).strip().upper() or None
            prof = self._futures_contract_profile(
                contract_code,
                underlying=native,
                multiplier=multiplier or matched_cand.get("multiplier"),
                metadata={** dict(matched_cand), **self._current_signal_metadata},
            )
            resolved_multiplier = float(prof["multiplier"])
            exchange = "TAIFEX"
            sec_type = "future"
        else:
            resolved_multiplier = 1.0
            sec_type = "equity"

        base_metadata: dict[str, Any] = {
            "signal_id": signal_id,
            "adapter": "shioaji",
            "broker": "shioaji_paper",
            "venue": "shioaji_paper",
            "exchange": exchange,
            "contract_symbol": contract_code or native,
            "sec_type": sec_type,
            "currency": "TWD",
            "side": side,
            "instrument_id": resolved_inst_id,
            "kind": resolved_kind,
        }
        if resolved_kind == "stock_future":
            base_metadata["multiplier"] = resolved_multiplier
            if resolved_month:
                base_metadata["contract_month"] = resolved_month

        allowed_qtypes = ("SHARES", "CONTRACTS") if resolved_kind == "stock_future" else ("SHARES",)
        if quantity_type not in allowed_qtypes:
            self._publish(
                "order_rejection", str(symbol), 0.0, action,
                broker_submission_status="tw_unsupported_quantity_type",
                submitted_to_broker=False,
                metadata={**base_metadata, "rejected_order_count": 1,
                          "quantity_type": quantity_type},
            )
            log.warning("[%s] TW order rejected: unsupported quantity_type=%s", signal_id, quantity_type)
            return

        is_market = str(order_type or "MARKET").lower() == "market"
        market_price: float | None = None
        market_price_as_of = ""
        market_price_source = ""
        resolved_price_basis = str(
            price_basis
            or self._current_signal_metadata.get("price_basis")
            or matched_cand.get("price_basis")
            or ""
        ).strip() or None

        if resolved_kind == "stock_future":
            try:
                qty = _finite_float(
                    quantity,
                    field="Taiwan futures order quantity",
                    positive=True,
                )
            except ValueError as exc:
                self._reject_invalid_paper_fill(
                    str(symbol),
                    action,
                    reason="invalid_taiwan_order_input",
                    broker_submission_status="tw_invalid_order_input",
                    metadata={**base_metadata, "diagnostic": str(exc)},
                )
                return
            cand_settlement_price = (
                matched_cand.get("futures_settlement_price")
                if matched_cand.get("futures_settlement_price") is not None
                else matched_cand.get("settlement_price")
            )
            if (
                cand_settlement_price is None
                and str(matched_cand.get("price_basis") or "").strip()
                == "futures_daily_settlement_prev_session"
            ):
                cand_settlement_price = matched_cand.get("price")
            raw_fut_price = (
                self._current_signal_metadata.get("futures_settlement_price")
                if self._current_signal_metadata.get("futures_settlement_price") is not None
                else (
                    self._current_signal_metadata.get("settlement_price")
                    if self._current_signal_metadata.get("settlement_price") is not None
                    else cand_settlement_price
                )
            )
            try:
                market_price = (
                    _finite_float(raw_fut_price, field="futures settlement price", positive=True)
                    if raw_fut_price is not None
                    else None
                )
            except ValueError:
                market_price = None
            market_price_as_of = str(
                self._current_signal_metadata.get("price_date")
                or self._current_signal_metadata.get("settlement_date")
                or matched_cand.get("price_date")
                or matched_cand.get("settlement_date")
                or ""
            ).strip()
            market_price_source = str(
                self._current_signal_metadata.get("price_source")
                or self._current_signal_metadata.get("market_price_source")
                or matched_cand.get("price_source")
                or resolved_price_basis
                or ""
            ).strip()
            holding_quantity = 0.0
        else:
            try:
                qty = _finite_float(
                    quantity,
                    field="Taiwan order quantity",
                    positive=True,
                )
                if is_market:
                    market_price = _finite_float(
                        self._current_signal_metadata.get("market_price"),
                        field="Taiwan admitted market_price",
                        positive=True,
                    )
                    market_price_as_of = str(
                        self._current_signal_metadata.get("market_price_as_of") or ""
                    ).strip()
                    market_price_source = str(
                        self._current_signal_metadata.get("market_price_source") or ""
                    ).strip()
                    if not market_price_as_of or not market_price_source:
                        raise ValueError(
                            "Taiwan MARKET order requires an admitted market price "
                            "(market_price, market_price_as_of, market_price_source) "
                            "from the signal"
                        )
                if limit_price is not None:
                    limit_price = _finite_float(
                        limit_price,
                        field="Taiwan limit price",
                        positive=True,
                    )
                holding_quantity = self._taiwan_underlying_holding_quantity(native, exchange)
            except ValueError as exc:
                self._reject_invalid_paper_fill(
                    str(symbol),
                    action,
                    reason="invalid_taiwan_order_input",
                    broker_submission_status="tw_invalid_order_input",
                    metadata={**base_metadata, "diagnostic": str(exc)},
                )
                log.warning("[%s] TW order rejected before broker: %s", signal_id, exc)
                return

        requested_quantity = float(qty)
        partial_clamped = False
        if resolved_kind == "cash_share" and side == "sell":
            if holding_quantity <= 0:
                self._publish(
                    "order_rejection",
                    str(symbol),
                    0.0,
                    action,
                    broker_submission_status="sell_without_holdings",
                    submitted_to_broker=False,
                    metadata={
                        **base_metadata,
                        "reason": "sell_without_holdings",
                        "reject_reason": "sell_without_holdings",
                        "rejection_status": "rejected",
                        "order_status": "rejected",
                        "risk_status": "rejected",
                        "risk_reason": "sell_without_holdings",
                        "rejected_order_count": 1,
                        "requested_quantity": requested_quantity,
                        "holding_quantity": float(holding_quantity),
                    },
                )
                log.warning(
                    "[%s] TW sell rejected without holdings for %s (requested=%s, holding=%s)",
                    signal_id,
                    symbol,
                    requested_quantity,
                    holding_quantity,
                )
                return
            if qty > holding_quantity:
                qty = float(holding_quantity)
                partial_clamped = True

        gate_result = self.EvaluateOrderRiskGate(
            symbol=str(symbol),
            side=side,
            quantity=qty,
            price=market_price if market_price is not None else limit_price,
            instrument_id=resolved_inst_id,
            kind=resolved_kind,
            multiplier=resolved_multiplier,
            contract=contract_code,
            contract_month=resolved_month,
            price_basis=resolved_price_basis,
            price_source=market_price_source,
            price_as_of=market_price_as_of,
        )
        self._last_risk_gate_result = gate_result
        if not gate_result["accepted"]:
            reason = str(gate_result["reason"])
            self._publish(
                "order_rejection",
                str(symbol),
                0.0,
                action,
                broker_submission_status=reason,
                submitted_to_broker=False,
                metadata={
                    **base_metadata,
                    "reason": reason,
                    "reject_reason": reason,
                    "rejection_status": "rejected",
                    "order_status": "rejected",
                    "risk_status": "rejected",
                    "risk_reason": reason,
                    "risk_gate_evaluated": True,
                    "required": gate_result["required"],
                    "available": gate_result["available"],
                    "limit": gate_result["limit"],
                    "instrument_id": resolved_inst_id,
                    "decision_id": gate_result["decision_id"],
                    "risk_decision_id": gate_result["decision_id"],
                    "rejected_order_count": 1,
                    "requested_quantity": requested_quantity,
                },
            )
            log.warning(
                "[%s] TW order rejected by risk gate for %s (%s): reason=%s required=%s available=%s limit=%s",
                signal_id,
                symbol,
                resolved_inst_id,
                reason,
                gate_result["required"],
                gate_result["available"],
                gate_result["limit"],
            )
            return

        broker_url = (
            "in-process"
            if self._in_process_broker
            else os.getenv("PANTHEON_BROKER_PAPER_URL", "http://broker:8102").rstrip("/")
        )
        payload: dict[str, Any] = {
            "capital_pool_id": os.getenv("PANTHEON_CAPITAL_POOL_ID", "") or self._taiwan_capital_pool_id(),
            "strategy_id": os.getenv("PANTHEON_STRATEGY_ID", "") or "strategy-tw-session-momentum",
            "client_order_id": str(signal_id),
            "symbol": native,
            "qty": qty,
            "side": side,
            "order_type": str(order_type or "MARKET").lower(),
            "instrument_id": resolved_inst_id,
            "kind": resolved_kind,
            "multiplier": resolved_multiplier,
        }
        if resolved_month:
            payload["contract_month"] = resolved_month
        if resolved_price_basis:
            payload["price_basis"] = resolved_price_basis
        correlation_envelope = self._current_signal_metadata.get("correlation_envelope")
        if correlation_envelope is not None:
            payload["correlation_envelope"] = correlation_envelope
        if limit_price is not None:
            payload["limit_price"] = float(limit_price)
        if (is_market or resolved_kind == "stock_future") and market_price is not None:
            payload["market_price"] = float(market_price)
        try:
            order = self._post_broker_paper_order(broker_url, payload)
        except Exception as exc:
            self._publish(
                "order_rejection", str(symbol), 0.0, action,
                broker_submission_status="taiwan_broker_error",
                submitted_to_broker=True,
                metadata={**base_metadata, "rejected_order_count": 1,
                          "execution_error_message": str(exc)},
            )
            log.error("[%s] TW broker order failed for %s: %s", signal_id, symbol, exc)
            return

        try:
            fill_price = _finite_float(
                order.get("fill_price"),
                field="Taiwan broker fill_price",
                positive=True,
            )
            fill_qty = _finite_float(
                order.get("fill_qty"),
                field="Taiwan broker fill_qty",
                positive=True,
            )
            if resolved_kind == "cash_share":
                current_quantity = self._taiwan_underlying_holding_quantity(native, exchange)
                if side == "sell" and fill_qty > current_quantity:
                    fill_qty = float(current_quantity)
                    partial_clamped = True
                signed_fill_qty = fill_qty if side == "buy" else -fill_qty
                current_cash = _finite_float(self._cash, field="Taiwan current cash")
                next_quantity = current_quantity + signed_fill_qty
                if side == "sell" and (next_quantity < 0 or abs(next_quantity) <= 1e-12):
                    next_quantity = 0.0
                next_cash = current_cash - (signed_fill_qty * fill_price)
                if not math.isfinite(next_quantity) or not math.isfinite(next_cash):
                    raise ValueError("Taiwan broker fill produces non-finite ledger values")
            else:
                signed_fill_qty = fill_qty if side == "buy" else -fill_qty
        except ValueError as exc:
            self._reject_invalid_paper_fill(
                str(symbol),
                action,
                reason="invalid_taiwan_broker_fill",
                broker_submission_status="tw_invalid_broker_fill",
                submitted_to_broker=True,
                metadata={
                    **base_metadata,
                    "diagnostic": str(exc),
                    "broker_fill_qty": repr(order.get("fill_qty")),
                    "broker_fill_price": repr(order.get("fill_price")),
                },
            )
            log.error("[%s] TW broker returned invalid fill for %s: %s", signal_id, symbol, exc)
            return

        order_id = str(order.get("order_id") or "")
        if resolved_kind == "stock_future":
            fut_fill_info = self._commit_futures_fill(
                symbol=str(symbol),
                underlying=native,
                instrument_id=resolved_inst_id,
                contract=contract_code,
                contract_month=resolved_month,
                multiplier=resolved_multiplier,
                signed_fill_qty=signed_fill_qty,
                fill_price=fill_price,
                price_as_of=market_price_as_of,
                price_source=market_price_source or "futures_daily_settlement_prev_session",
            )
            fill_metadata = {
                **base_metadata,
                "broker_order_id": order_id,
                "shioaji_trade_id": order_id,
                "instrument_id": resolved_inst_id,
                "kind": "stock_future",
                "multiplier": resolved_multiplier,
                "contract_month": resolved_month,
                "fee": fut_fill_info["fee"],
                "tax": fut_fill_info["tax"],
                "initial_margin_reserved": fut_fill_info["initial_margin_reserved"],
                "price_basis": "futures_daily_settlement_prev_session",
                "position_quantity": fut_fill_info["position_quantity"],
                "realized_pnl_delta": fut_fill_info["realized_delta"],
                "risk_status": gate_result["risk_status"],
                "risk_gate_status": gate_result["risk_status"],
                "risk_decision_id": gate_result["decision_id"],
                "decision_id": gate_result["decision_id"],
            }
        else:
            self._commit_fill(
                str(symbol),
                next_quantity=next_quantity,
                next_cash=next_cash,
                execution_price=fill_price,
                price_as_of=(
                    market_price_as_of
                    if is_market
                    else str(order.get("filled_at") or order.get("updated_at") or _iso_now())
                ),
                price_source=market_price_source if is_market else "paper_limit_fill",
            )
            fill_metadata = {
                **base_metadata,
                "broker_order_id": order_id,
                "shioaji_trade_id": order_id,
                "instrument_id": resolved_inst_id,
                "kind": "cash_share",
                "multiplier": 1.0,
                "risk_status": gate_result["risk_status"],
                "risk_gate_status": gate_result["risk_status"],
                "risk_decision_id": gate_result["decision_id"],
                "decision_id": gate_result["decision_id"],
            }
            if partial_clamped:
                fill_metadata["paper_order_partial_clamped"] = True
                fill_metadata["requested_quantity"] = float(requested_quantity)
                fill_metadata["clamped_quantity"] = float(fill_qty)
        self._publish(
            "paper_fill_simulated", str(symbol), signed_fill_qty, action,
            broker_submission_status="filled",
            submitted_to_broker=True,
            metadata=fill_metadata,
        )
        log.info("[%s] TW paper fill %s %s %.0f @ %.4f (order=%s)",
                 signal_id, side, resolved_inst_id, fill_qty, fill_price, order_id)

    def _commit_futures_fill(
        self,
        *,
        symbol: str,
        underlying: str,
        instrument_id: str,
        contract: str | None,
        contract_month: str | None,
        multiplier: float,
        signed_fill_qty: float,
        fill_price: float,
        price_as_of: str,
        price_source: str,
    ) -> dict[str, Any]:
        binding_id = self._resolve_persona_binding_id()
        norm_u = self._normalise_underlying_symbol(underlying, self._current_signal_metadata)
        key = (binding_id, norm_u, instrument_id)
        existing = self._instrument_positions.get(key)
        q_prev = float(existing.get("quantity", 0.0)) if existing else 0.0
        avg_prev = float(existing.get("average_entry_price", 0.0)) if existing else 0.0
        u = float(signed_fill_qty)
        m = float(multiplier)
        p_fill = float(fill_price)

        if abs(q_prev) <= 1e-12 or (q_prev > 0 and u > 0) or (q_prev < 0 and u < 0):
            realized_delta = 0.0
            q_next = q_prev + u
            avg_next = (
                (abs(q_prev) * avg_prev + abs(u) * p_fill) / abs(q_next)
                if abs(q_next) > 1e-12
                else 0.0
            )
        elif abs(u) <= abs(q_prev) + 1e-12:
            closed_c = min(abs(q_prev), abs(u))
            sign_prev = 1.0 if q_prev > 0 else -1.0
            realized_delta = closed_c * m * (p_fill - avg_prev) * sign_prev
            q_next = q_prev + u
            if abs(q_next) <= 1e-12:
                q_next = 0.0
                avg_next = 0.0
            else:
                avg_next = avg_prev
        else:
            closed_c = abs(q_prev)
            sign_prev = 1.0 if q_prev > 0 else -1.0
            realized_delta = closed_c * m * (p_fill - avg_prev) * sign_prev
            q_next = q_prev + u
            avg_next = p_fill

        include_costs = bool(
            self._current_signal_metadata.get(
                "futures_costs_enabled", self._futures_costs_enabled
            )
        )
        broker_fee = float(
            self._current_signal_metadata.get(
                "broker_fee_per_contract_per_side",
                self._broker_fee_per_contract_per_side,
            )
        )
        if include_costs:
            cost_info = compute_open_or_close_cost(
                contract,
                p_fill,
                abs(u),
                multiplier=m,
                underlying=norm_u,
                broker_fee_per_contract_per_side=broker_fee,
            )
            fee = float(cost_info["fee"])
            tax = float(cost_info["tax"])
            cost_delta = float(cost_info["total_cost"])
        else:
            fee = 0.0
            tax = 0.0
            cost_delta = 0.0

        margin_info = self.compute_futures_margin_per_contract(
            contract,
            p_fill,
            multiplier=m,
            underlying=norm_u,
            metadata={**(existing or {}), **self._current_signal_metadata},
        )
        init_reserved = abs(q_next) * margin_info["initial_margin_per_contract"]
        maint_required = abs(q_next) * margin_info["maintenance_margin_per_contract"]
        unrealized_next = (
            q_next * m * (p_fill - avg_next) if abs(q_next) > 1e-12 else 0.0
        )

        pos_record: dict[str, Any] = dict(existing or {})
        pos_record.update(
            {
                "persona_binding_id": binding_id,
                "normalised_underlying": norm_u,
                "instrument_id": instrument_id,
                "symbol": symbol,
                "kind": "stock_future",
                "contract": contract,
                "contract_month": contract_month,
                "quantity": q_next,
                "multiplier": m,
                "average_entry_price": avg_next,
                "mark_price": p_fill,
                "mark_as_of": price_as_of,
                "mark_source": price_source,
                "initial_margin_per_contract": margin_info["initial_margin_per_contract"],
                "maintenance_margin_per_contract": margin_info["maintenance_margin_per_contract"],
                "initial_margin_reserved": init_reserved,
                "maintenance_margin_required": maint_required,
                "unrealized_pnl": unrealized_next,
                "realized_pnl": float(pos_record.get("realized_pnl", 0.0)) + realized_delta,
                "costs": float(pos_record.get("costs", 0.0)) + cost_delta,
                "status": "open" if abs(q_next) > 1e-12 else "closed",
            }
        )
        self._instrument_positions[key] = pos_record
        self._cash = float(self._cash) + realized_delta - cost_delta
        self._realized_pnl += realized_delta
        self._total_costs += cost_delta

        self.SetSecurityPrice(
            instrument_id,
            p_fill,
            as_of=price_as_of,
            source=price_source,
            authoritative=False,
        )
        self._record_fill()
        snap = self.assert_ledger_reconciliation()
        if self._margin_call_active and snap["equity"] >= snap["maintenance_margin_required"]:
            self._margin_call_active = False
            self._margin_call_since = None
        self._persist_state()
        return {
            "fee": fee,
            "tax": tax,
            "cost_delta": cost_delta,
            "realized_delta": realized_delta,
            "initial_margin_reserved": init_reserved,
            "maintenance_margin_required": maint_required,
            "position_quantity": q_next,
        }

    def MarkFuturesSession(  # noqa: N802
        self,
        trade_date: Any,
        settlement_prices: Mapping[str, float] | None = None,
        *,
        holidays: Iterable[Any] | None = None,
        check_maintenance: bool = True,
    ) -> dict[str, Any]:
        """Mark open futures positions at session settlement and evaluate maintenance margin."""
        parsed_day = self._parse_strict_iso_date(trade_date)
        if parsed_day is None:
            raise ValueError(
                f"MarkFuturesSession requires a strict YYYY-MM-DD trade_date (got {trade_date!r})"
            )
        date_str = parsed_day.isoformat()
        hol = tuple(holidays) if holidays is not None else self._active_holidays()

        already_marked_this_session = False
        if self._last_futures_mark_session_date:
            prev_mark_day = self._parse_strict_iso_date(self._last_futures_mark_session_date)
            if prev_mark_day is not None:
                if parsed_day < prev_mark_day:
                    return self.ledger_reconciliation_snapshot()
                if parsed_day == prev_mark_day:
                    already_marked_this_session = True

        prices = dict(settlement_prices or {})
        pending_updates: list[tuple[dict[str, Any], str, float]] = []
        for (b_id, norm_u, inst_id), pos in list(self._instrument_positions.items()):
            if pos.get("kind") != "stock_future":
                continue
            qty = float(pos.get("quantity", 0.0))
            if abs(qty) <= 1e-12 and pos.get("status") != "settlement_pending":
                continue
            contract = str(pos.get("contract") or "")
            month = str(pos.get("contract_month") or "")
            contract_month_key = f"{contract}:{month}" if contract and month else ""
            raw_mark = (
                prices.get(inst_id)
                if inst_id in prices
                else (
                    prices.get(contract_month_key)
                    if contract_month_key and contract_month_key in prices
                    else prices.get(contract)
                )
            )
            if raw_mark is None:
                if abs(qty) > 1e-12:
                    raise ValueError(
                        f"MarkFuturesSession requires a settlement price for open futures contract {inst_id!r}"
                    )
                continue
            mark_val = _finite_float(
                raw_mark,
                field=f"futures settlement price for {inst_id}",
                positive=True,
            )
            pending_updates.append((pos, inst_id, mark_val))

        for pos, inst_id, mark_val in pending_updates:
            self._apply_mark_to_position(
                pos,
                mark_val,
                as_of=date_str,
                source="futures_daily_settlement_prev_session",
            )
            self._security(inst_id).Price = mark_val

        self._last_futures_mark_diagnostic = {
            "status": "marked",
            "trade_date": date_str,
            "reason": None,
            "missing_contracts": {},
            "marked_contracts": {
                inst_id: mark_val for _pos, inst_id, mark_val in pending_updates
            },
        }
        snap = self.assert_ledger_reconciliation()
        if not check_maintenance or already_marked_this_session:
            if check_maintenance:
                self._persist_state()
            return snap

        self._last_futures_mark_session_date = date_str
        req_maint = float(snap["maintenance_margin_required"])
        equity = float(snap["equity"])
        if req_maint > 0 and equity + 1e-9 < req_maint:
            if not self._margin_call_active:
                self._margin_call_active = True
                self._margin_call_since = date_str
                self._publish(
                    "paper_margin_call",
                    "TAIFEX_FUTURES",
                    0.0,
                    "margin_call",
                    broker_submission_status="not_submitted_margin_call",
                    submitted_to_broker=False,
                    metadata={
                        "trade_date": date_str,
                        "equity": equity,
                        "maintenance_margin_required": req_maint,
                        "initial_margin_reserved": float(snap["margin_reserved"]),
                        "shortfall": req_maint - equity,
                        "decision_status": "no_order",
                        "order_status": "not_submitted",
                        "risk_status": "skipped",
                    },
                )
            else:
                call_since_day = self._parse_strict_iso_date(self._margin_call_since)
                is_next_trading_session = (
                    call_since_day is not None
                    and parsed_day > call_since_day
                    and is_trading_day(parsed_day, hol)
                )
                if is_next_trading_session:
                    # Persistent maintenance breach at the next full trading session mark -> force-close
                    for key, pos in list(self._instrument_positions.items()):
                        if pos.get("kind") != "stock_future":
                            continue
                        qty = float(pos.get("quantity", 0.0))
                        if abs(qty) <= 1e-12:
                            continue
                        m = float(pos.get("multiplier", 1.0))
                        p_liq = float(pos.get("mark_price", 0.0))
                        avg_entry = float(pos.get("average_entry_price", p_liq))
                        realized_delta = qty * m * (p_liq - avg_entry)
                        if self._futures_costs_enabled:
                            c_info = compute_open_or_close_cost(
                                pos.get("contract"),
                                p_liq,
                                abs(qty),
                                multiplier=m,
                                underlying=pos.get("normalised_underlying"),
                                broker_fee_per_contract_per_side=self._broker_fee_per_contract_per_side,
                            )
                            fee = float(c_info["fee"])
                            tax = float(c_info["tax"])
                            cost_delta = float(c_info["total_cost"])
                        else:
                            fee = 0.0
                            tax = 0.0
                            cost_delta = 0.0
                        pos["quantity"] = 0.0
                        pos["average_entry_price"] = 0.0
                        pos["unrealized_pnl"] = 0.0
                        pos["initial_margin_reserved"] = 0.0
                        pos["maintenance_margin_required"] = 0.0
                        pos["realized_pnl"] = float(pos.get("realized_pnl", 0.0)) + realized_delta
                        pos["costs"] = float(pos.get("costs", 0.0)) + cost_delta
                        pos["status"] = "closed"
                        self._cash = float(self._cash) + realized_delta - cost_delta
                        self._realized_pnl += realized_delta
                        self._total_costs += cost_delta
                        self._security(str(pos["instrument_id"])).Price = p_liq
                        self._publish(
                            "paper_forced_liquidation",
                            str(pos.get("symbol") or pos["instrument_id"]),
                            0.0,
                            "forced_liquidation",
                            broker_submission_status="not_submitted_forced_liquidation",
                            submitted_to_broker=False,
                            metadata={
                                "trade_date": date_str,
                                "instrument_id": pos["instrument_id"],
                                "kind": "stock_future",
                                "contract": pos.get("contract"),
                                "contract_month": pos.get("contract_month"),
                                "multiplier": m,
                                "liquidated_quantity": -qty,
                                "liquidation_price": p_liq,
                                "realized_pnl": realized_delta,
                                "fee": fee,
                                "tax": tax,
                                "equity_before_liquidation": equity,
                                "maintenance_margin_required": req_maint,
                                "reason": "persistent_maintenance_margin_breach",
                                "decision_status": "no_order",
                                "order_status": "not_submitted",
                                "risk_status": "skipped",
                            },
                        )
                    self._margin_call_active = False
                    self._margin_call_since = None
                    snap = self.assert_ledger_reconciliation()
        elif self._margin_call_active and equity + 1e-9 >= req_maint:
            self._margin_call_active = False
            self._margin_call_since = None

        self._persist_state()
        return self.ledger_reconciliation_snapshot()

    def mark_futures_session(
        self,
        trade_date: Any,
        settlement_prices: Mapping[str, float] | None = None,
        *,
        holidays: Iterable[Any] | None = None,
        check_maintenance: bool = True,
    ) -> dict[str, Any]:
        return self.MarkFuturesSession(
            trade_date,
            settlement_prices,
            holidays=holidays,
            check_maintenance=check_maintenance,
        )

    def SettleExpiringFutures(  # noqa: N802
        self,
        trade_date: Any,
        final_settlement_source: Any = None,
        *,
        holidays: Iterable[Any] | None = None,
        partition_horizon: Any = None,
    ) -> list[dict[str, Any]]:
        """Cash-settle open futures contracts whose ``last_trading_day <= trade_date``.

        ``trade_date`` is the session being processed. ``partition_horizon`` is
        the newest dataset partition date that may be read (the decision day):
        the final settlement of session S lands in a later partition, so a
        horizon capped at S would never see it.
        """
        parsed_day = self._parse_strict_iso_date(trade_date)
        if parsed_day is None:
            raise ValueError(
                f"SettleExpiringFutures requires a strict YYYY-MM-DD trade_date (got {trade_date!r})"
            )
        hol = tuple(holidays) if holidays is not None else self._active_holidays()
        horizon_day = (
            self._parse_strict_iso_date(partition_horizon)
            if partition_horizon is not None
            else None
        )
        if horizon_day is not None and horizon_day < parsed_day:
            horizon_day = parsed_day
        outcomes: list[dict[str, Any]] = []
        state_mutated = False
        for (b_id, _norm_u, inst_id), pos in list(self._instrument_positions.items()):
            if pos.get("kind") != "stock_future":
                continue
            month = str(pos.get("contract_month") or "").strip()
            contract = str(pos.get("contract") or "").strip()
            if not month or not contract:
                continue
            settle_key = f"{b_id}:{inst_id}:{month}"
            if settle_key in self._settled_futures_contracts:
                continue
            qty = float(pos.get("quantity", 0.0))
            if abs(qty) <= 1e-12 and pos.get("status") != "settlement_pending":
                continue
            ltd = last_trading_day(month, hol)
            if parsed_day < ltd:
                continue
            lookup = lookup_final_settlement_price(
                contract,
                month,
                final_settlement_source,
                settlement_day=ltd,
                as_of_trading_day=horizon_day or parsed_day,
            )
            if lookup is None:
                pos["status"] = "settlement_pending"
                state_mutated = True
                outcomes.append(
                    {
                        "instrument_id": pos["instrument_id"],
                        "contract": contract,
                        "contract_month": month,
                        "status": "settlement_pending",
                        "last_trading_day": ltd.isoformat(),
                    }
                )
                continue

            p_final = float(lookup["final_settlement_price"])
            m = float(pos.get("multiplier", 1.0))
            avg_entry = float(pos.get("average_entry_price", p_final))
            realized_delta = qty * m * (p_final - avg_entry)
            if self._futures_costs_enabled:
                s_cost = compute_settlement_cost(
                    contract,
                    p_final,
                    abs(qty),
                    multiplier=m,
                    underlying=pos.get("normalised_underlying"),
                    broker_fee_per_contract_per_side=self._broker_fee_per_contract_per_side,
                )
                settlement_fee = float(s_cost["settlement_fee"])
                fee = float(s_cost["fee"])
                tax = float(s_cost["tax"])
                cost_delta = float(s_cost["total_cost"])
            else:
                settlement_fee = 0.0
                fee = 0.0
                tax = 0.0
                cost_delta = 0.0

            pos["quantity"] = 0.0
            pos["average_entry_price"] = 0.0
            pos["mark_price"] = p_final
            pos["unrealized_pnl"] = 0.0
            pos["initial_margin_reserved"] = 0.0
            pos["maintenance_margin_required"] = 0.0
            pos["realized_pnl"] = float(pos.get("realized_pnl", 0.0)) + realized_delta
            pos["costs"] = float(pos.get("costs", 0.0)) + cost_delta
            pos["status"] = "closed"

            self._cash = float(self._cash) + realized_delta - cost_delta
            self._realized_pnl += realized_delta
            self._total_costs += cost_delta
            sym_for_event = str(pos.get("symbol") or pos["instrument_id"])
            self._security(str(pos["instrument_id"])).Price = p_final
            self._settled_futures_contracts.add(settle_key)
            state_mutated = True
            self.assert_ledger_reconciliation()

            settled_meta = {
                "instrument_id": pos["instrument_id"],
                "kind": "stock_future",
                "contract": contract,
                "contract_month": month,
                "multiplier": m,
                "settled_quantity": qty,
                "final_settlement_price": p_final,
                "settlement_day": ltd.isoformat(),
                "realized_pnl": realized_delta,
                "settlement_fee": settlement_fee,
                "fee": fee,
                "tax": tax,
                "price_basis": "FinalSettlementPriceSSF",
                "decision_status": "no_order",
                "order_status": "not_submitted",
                "risk_status": "skipped",
            }
            self._publish(
                "paper_futures_settled",
                sym_for_event,
                0.0,
                "futures_expiry_settled",
                broker_submission_status="not_submitted_expiry_settlement",
                submitted_to_broker=False,
                metadata=settled_meta,
            )
            outcomes.append(
                {
                    **settled_meta,
                    "status": "settled",
                }
            )
        if state_mutated:
            self._persist_state()
        return outcomes

    def settle_expiring_futures(
        self,
        trade_date: Any,
        final_settlement_source: Any = None,
        *,
        holidays: Iterable[Any] | None = None,
    ) -> list[dict[str, Any]]:
        return self.SettleExpiringFutures(
            trade_date,
            final_settlement_source,
            holidays=holidays,
        )

    def _taiwan_capital_pool_id(self) -> str:
        binding = getattr(self, "_cached_binding", None) or {}
        return str(binding.get("capital_pool_id") or "pool-tw-equity-paper")

    @staticmethod
    def _post_broker_paper_order(broker_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        if broker_url == "in-process":
            sim_order = simulate_paper_order(
                capital_pool_id=str(payload.get("capital_pool_id") or "pool-tw-equity-paper"),
                strategy_id=str(payload.get("strategy_id") or "strategy-tw-session-momentum"),
                client_order_id=str(payload.get("client_order_id") or ""),
                correlation_envelope=payload.get("correlation_envelope"),
                symbol=str(payload["symbol"]),
                qty=float(payload["qty"]),
                side=str(payload["side"]),
                order_type=str(payload.get("order_type") or "market"),
                limit_price=payload.get("limit_price"),
                market_price=payload.get("market_price"),
                instrument_id=payload.get("instrument_id"),
                kind=str(payload.get("kind") or "cash_share"),
                multiplier=float(payload.get("multiplier") or 1.0),
                contract_month=payload.get("contract_month"),
                price_basis=payload.get("price_basis"),
            )
            return sim_order.to_dict()
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            broker_url + "/api/broker/paper/orders",
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            timeout = int(os.getenv("PANTHEON_BROKER_TIMEOUT_SECONDS", "5"))
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:300]
            raise RuntimeError(f"broker HTTP {exc.code}: {detail}") from exc
        order = body.get("order")
        if not isinstance(order, dict):
            raise RuntimeError(f"broker response missing order: {str(body)[:200]}")
        return order

    def _publish(
        self,
        event_type: str,
        symbol: str,
        quantity: float,
        action: str,
        *,
        broker_submission_status: str | None = None,
        submitted_to_broker: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self._event_sink is None:
            self._reversal_info = None
            return
        event_metadata = dict(self._current_signal_metadata)
        if metadata:
            event_metadata.update(metadata)
        price_lookup_symbol = (
            str(event_metadata["instrument_id"])
            if event_metadata.get("kind") == "stock_future" and event_metadata.get("instrument_id")
            else symbol
        )
        security = self._security(price_lookup_symbol)
        try:
            event_quantity = _finite_float(quantity, field="event quantity")
        except ValueError:
            event_quantity = 0.0
        try:
            event_price = _finite_float(security.Price, field="event fill price")
        except ValueError:
            event_price = 0.0
        if event_type == "paper_fill_simulated":
            # All fill publishers call `_commit_fill` first.  This marker makes
            # the ordering explicit so downstream code never emits a position
            # snapshot for an uncommitted or rejected paper fill.
            event_metadata["ledger_committed"] = True
            event_metadata["ledger_fill_count"] = int(self._fill_count)
            event_metadata.setdefault(
                "position_quantity",
                float(self._holding(str(symbol)).Quantity),
            )
            event_metadata["ledger_cash"] = float(self._cash)
            if self._reversal_info:
                rev = self._reversal_info
                self._reversal_info = None
                event_metadata["reversal"] = {
                    "closed_episode_id": rev["old_episode_id"],
                    "close_quantity": float(rev["close_qty"]),
                    "opened_episode_id": rev["new_episode_id"],
                    "open_quantity": float(rev["open_qty"]),
                }
                if rev["new_episode_id"]:
                    event_metadata["trade_episode_id"] = rev["new_episode_id"]
            elif self._last_fill_episode_id:
                event_metadata["trade_episode_id"] = self._last_fill_episode_id
            else:
                event_metadata.pop("trade_episode_id", None)
        order_event = OrderEvent(
            event_type=event_type,
            symbol=str(symbol),
            quantity=event_quantity,
            fill_price=event_price,
            action=action,
            submitted_to_broker=submitted_to_broker,
            broker_submission_status=broker_submission_status,
            metadata=event_metadata,
        )
        if (
            event_type == "paper_fill_simulated"
            and self._durable_fill_publication
            and not self._stage_fill_publication(order_event)
        ):
            # The fill is already committed; keep going so the event is still
            # published in-process, but surface the staging failure.
            log.error(
                "committed paper fill %s could not be staged for durable "
                "lifecycle replay: %s",
                order_event.event_id,
                self._state_error,
            )
        self._event_sink(order_event)

    def SetHoldings(self, symbol: str, target_percent: float) -> None:  # noqa: N802
        security = self._security(symbol)
        holding = self._holding(symbol)
        try:
            target_percent_value = _finite_float(
                target_percent,
                field="target percent",
            )
            price = _finite_float(
                security.Price,
                field="paper fill price",
                positive=True,
            )
            current_quantity = _finite_float(
                holding.Quantity,
                field="current holding quantity",
            )
            current_cash = _finite_float(self._cash, field="current cash")
            target_quantity = (self._initial_cash * target_percent_value) / price
            delta = target_quantity - current_quantity
            next_cash = current_cash - (delta * price)
            if not all(math.isfinite(value) for value in (target_quantity, delta, next_cash)):
                raise ValueError("SetHoldings produced non-finite ledger values")
        except ValueError as exc:
            self._reject_invalid_paper_fill(
                str(symbol),
                "set_holdings",
                reason="invalid_set_holdings_fill",
                metadata={"diagnostic": str(exc)},
            )
            return
        if abs(delta) <= 1e-12:
            metadata: dict[str, Any] = {
                "noop_reason": "set_holdings_no_delta",
                "decision_status": "no_order",
                "order_status": "not_submitted",
                "computed_quantity": 0.0,
                "position_quantity": float(current_quantity),
                "target_quantity": float(target_quantity),
                "target_percent": target_percent_value,
                "price": price,
            }
            for field in ("signal_id", "requested_quantity", "quantity_type", "order_type"):
                value = self._current_signal_metadata.get(field)
                if value not in (None, "", [], {}):
                    metadata[field] = value
            self._publish(
                "paper_order_simulated",
                symbol,
                0.0,
                "set_holdings_no_delta_noop",
                broker_submission_status="not_submitted_signal_noop",
                submitted_to_broker=False,
                metadata=metadata,
            )
            return
        self._commit_fill(
            str(symbol),
            next_quantity=target_quantity,
            next_cash=next_cash,
        )
        self._publish("paper_fill_simulated", symbol, delta, "set_holdings")

    def MarketOrder(self, symbol: str, quantity: float) -> None:  # noqa: N802
        security = self._security(symbol)
        holding = self._holding(symbol)
        try:
            fill_quantity = _finite_float(quantity, field="market fill quantity")
            if fill_quantity == 0:
                raise ValueError("market fill quantity must be non-zero")
            price = _finite_float(
                security.Price,
                field="market fill price",
                positive=True,
            )
            current_quantity = _finite_float(
                holding.Quantity,
                field="current holding quantity",
            )
            current_cash = _finite_float(self._cash, field="current cash")
            next_quantity = current_quantity + fill_quantity
            next_cash = current_cash - (fill_quantity * price)
            if not math.isfinite(next_quantity) or not math.isfinite(next_cash):
                raise ValueError("market fill produces non-finite ledger values")
        except ValueError as exc:
            self._reject_invalid_paper_fill(
                str(symbol),
                "market_order",
                reason="invalid_market_fill",
                metadata={"diagnostic": str(exc)},
            )
            return
        self._commit_fill(
            str(symbol),
            next_quantity=next_quantity,
            next_cash=next_cash,
        )
        self._publish("paper_fill_simulated", symbol, fill_quantity, "market_order")

    def LimitOrder(self, symbol: str, quantity: float, limit_price: float) -> None:  # noqa: N802
        security = self._security(symbol)
        holding = self._holding(symbol)
        try:
            fill_quantity = _finite_float(quantity, field="limit fill quantity")
            if fill_quantity == 0:
                raise ValueError("limit fill quantity must be non-zero")
            fill_price = _finite_float(
                limit_price,
                field="limit fill price",
                positive=True,
            )
            current_quantity = _finite_float(
                holding.Quantity,
                field="current holding quantity",
            )
            current_cash = _finite_float(self._cash, field="current cash")
            next_quantity = current_quantity + fill_quantity
            next_cash = current_cash - (fill_quantity * fill_price)
            if not math.isfinite(next_quantity) or not math.isfinite(next_cash):
                raise ValueError("limit fill produces non-finite ledger values")
        except ValueError as exc:
            self._reject_invalid_paper_fill(
                str(symbol),
                "limit_order",
                reason="invalid_limit_fill",
                metadata={"diagnostic": str(exc)},
            )
            return
        self._commit_fill(
            str(symbol),
            next_quantity=next_quantity,
            next_cash=next_cash,
            execution_price=fill_price,
            price_as_of=_iso_now(),
            price_source="paper_limit_fill",
        )
        self._publish("paper_fill_simulated", symbol, fill_quantity, "limit_order")

    def Liquidate(self, symbol: str) -> None:  # noqa: N802
        security = self._security(symbol)
        holding = self._holding(symbol)
        try:
            quantity = _finite_float(
                holding.Quantity,
                field="liquidation quantity",
            )
        except ValueError as exc:
            self._reject_invalid_paper_fill(
                str(symbol),
                "liquidate",
                reason="invalid_liquidation_fill",
                metadata={"diagnostic": str(exc)},
            )
            return
        if quantity == 0:
            metadata: dict[str, Any] = {
                "noop_reason": "liquidate_without_position",
                "decision_status": "no_order",
                "order_status": "not_submitted",
                "computed_quantity": 0.0,
                "position_quantity": 0.0,
                "price": float(security.Price),
            }
            for field in ("signal_id", "requested_quantity", "quantity_type", "order_type"):
                value = self._current_signal_metadata.get(field)
                if value not in (None, "", [], {}):
                    metadata[field] = value
            self._publish(
                "paper_order_simulated",
                symbol,
                0.0,
                "liquidate_without_position_noop",
                broker_submission_status="not_submitted_signal_noop",
                submitted_to_broker=False,
                metadata=metadata,
            )
            return
        try:
            price = _finite_float(
                security.Price,
                field="liquidation fill price",
                positive=True,
            )
            current_cash = _finite_float(self._cash, field="current cash")
            next_cash = current_cash + (quantity * price)
            if not math.isfinite(next_cash):
                raise ValueError("liquidation produces non-finite cash")
        except ValueError as exc:
            self._reject_invalid_paper_fill(
                str(symbol),
                "liquidate",
                reason="invalid_liquidation_fill",
                metadata={"diagnostic": str(exc)},
            )
            return
        self._commit_fill(
            str(symbol),
            next_quantity=0.0,
            next_cash=next_cash,
        )
        self._publish("paper_fill_simulated", symbol, -quantity, "liquidate")

    def SubmitBracketOrder(  # noqa: N802
        self,
        symbol: str,
        *,
        signal_id: str,
        legs: list[dict[str, Any]],
        guard_stage: str,
        broker_submission_status: str,
        submitted_to_broker: bool,
    ) -> dict[str, Any]:
        bracket_order_id = uuid.uuid4().hex
        stored_legs: list[dict[str, Any]] = []
        for index, leg in enumerate(legs, start=1):
            stored_legs.append(
                {
                    "bracket_order_id": bracket_order_id,
                    "leg_id": f"{bracket_order_id}-{index}",
                    "symbol": str(symbol),
                    "signal_id": signal_id,
                    "deployment_stage": guard_stage,
                    "submitted_to_broker": bool(submitted_to_broker),
                    "broker_submission_status": broker_submission_status,
                    "status": "open",
                    "created_at": _iso_now(),
                    **dict(leg),
                }
            )
        self._open_bracket_orders.extend(stored_legs)
        return {
            "bracket_order_id": bracket_order_id,
            "leg_count": len(stored_legs),
            "legs": stored_legs,
        }

    def RecordBracketOrderLogged(  # noqa: N802
        self,
        symbol: str,
        *,
        signal_id: str,
        stop_loss_pct: float,
        take_profit_pct: float,
        broker_submission_status: str,
        submitted_to_broker: bool,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        event_metadata = {
            "signal_id": signal_id,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
        }
        if metadata:
            event_metadata.update(metadata)
        self._publish(
            "bracket_order_logged",
            str(symbol),
            0.0,
            "bracket_submitted_to_broker" if submitted_to_broker else "bracket_logged_only",
            broker_submission_status=broker_submission_status,
            submitted_to_broker=submitted_to_broker,
            metadata=event_metadata,
        )

    def RecordOrderRejected(  # noqa: N802
        self,
        symbol: str,
        *,
        signal_id: str,
        reject_reason: str,
        requested_quantity: float,
        computed_quantity: float,
        quantity_type: str,
        order_type: str,
        broker_submission_status: str,
        submitted_to_broker: bool,
        price: float | None = None,
    ) -> None:
        event_metadata = {
            "signal_id": signal_id,
            "reject_reason": reject_reason,
            "rejection_status": "rejected",
            "order_status": "rejected",
            "requested_quantity": float(requested_quantity),
            "computed_quantity": float(computed_quantity),
            "quantity_type": quantity_type,
            "order_type": order_type,
        }
        if price is not None:
            event_metadata["price"] = float(price)
        self._publish(
            "order_rejection",
            str(symbol),
            0.0,
            "order_rejected",
            broker_submission_status=broker_submission_status,
            submitted_to_broker=submitted_to_broker,
            metadata=event_metadata,
        )

    def RecordSignalNoop(  # noqa: N802
        self,
        symbol: str,
        *,
        signal_id: str,
        noop_reason: str,
        requested_quantity: float,
        quantity_type: str,
        order_type: str,
        broker_submission_status: str,
        submitted_to_broker: bool,
        computed_quantity: float | None = None,
        price: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        event_metadata = {
            "signal_id": signal_id,
            "noop_reason": noop_reason,
            "decision_status": "no_order",
            "order_status": "not_submitted",
            "requested_quantity": float(requested_quantity),
            "quantity_type": quantity_type,
            "order_type": order_type,
        }
        if computed_quantity is not None:
            event_metadata["computed_quantity"] = float(computed_quantity)
        if price is not None:
            event_metadata["price"] = float(price)
        if metadata:
            event_metadata.update(metadata)
        self._publish(
            "paper_order_simulated",
            str(symbol),
            0.0,
            f"{noop_reason}_noop",
            broker_submission_status=broker_submission_status,
            submitted_to_broker=submitted_to_broker,
            metadata=event_metadata,
        )

    def RecordSignalProcessed(self, signal: dict[str, Any]) -> None:  # noqa: N802
        if self._event_sink is None:
            return
        self._event_sink(
            OrderEvent(
                event_type="signal_generation",
                symbol=signal["symbol"],
                quantity=float(signal.get("quantity") or 0.0),
                fill_price=0.0,
                action=signal["action"],
                submitted_to_broker=False,
                metadata=signal,
            )
        )

    def positions(self) -> list[dict[str, Any]]:
        positions: list[dict[str, Any]] = []
        for symbol, holding in sorted(self.Portfolio.items()):
            if holding.Quantity == 0:
                continue
            positions.append(
                {
                    "symbol": symbol,
                    "quantity": holding.Quantity,
                    "price": self._security(symbol).Price,
                }
            )
        for (_b_id, norm_u, inst_id), pos in sorted(
            self._instrument_positions.items(), key=lambda item: item[0][2]
        ):
            if pos.get("kind") != "stock_future":
                continue
            qty = float(pos.get("quantity", 0.0))
            if abs(qty) <= 1e-12:
                continue
            sec_price = float(self._security(inst_id).Price)
            mark_price = sec_price if sec_price > 0 else float(pos.get("mark_price", 0.0))
            positions.append(
                {
                    "symbol": inst_id,
                    "underlying_symbol": str(pos.get("symbol") or norm_u),
                    "instrument_id": inst_id,
                    "kind": "stock_future",
                    "contract": pos.get("contract"),
                    "contract_month": pos.get("contract_month"),
                    "quantity": qty,
                    "multiplier": float(pos.get("multiplier", 1.0)),
                    "average_entry_price": float(pos.get("average_entry_price", 0.0)),
                    "price": mark_price,
                    "initial_margin_reserved": float(pos.get("initial_margin_reserved", 0.0)),
                    "maintenance_margin_required": float(pos.get("maintenance_margin_required", 0.0)),
                    "unrealized_pnl": float(pos.get("unrealized_pnl", 0.0)),
                }
            )
        return positions

    def mark_symbols(self) -> list[str]:
        symbols = {
            symbol
            for symbol, holding in self.Portfolio.items()
            if abs(float(holding.Quantity)) > 1e-12
        }
        for (_b_id, _norm_u, inst_id), pos in self._instrument_positions.items():
            if pos.get("kind") == "stock_future" and abs(float(pos.get("quantity", 0.0))) > 1e-12:
                symbols.add(inst_id)
        return sorted(symbols)

    def open_stock_future_position_records(self) -> list[dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        for (_b_id, norm_u, inst_id), pos in self._instrument_positions.items():
            qty = abs(float(pos.get("quantity", 0.0)))
            if pos.get("kind") != "stock_future" or qty <= 1e-12:
                continue
            records[inst_id] = {
                "instrument_id": inst_id,
                "kind": "stock_future",
                "contract": pos.get("contract"),
                "contract_month": pos.get("contract_month"),
                "underlying": pos.get("normalised_underlying") or norm_u,
                "units": qty,
            }
        return [records[key] for key in sorted(records)]

    def apply_market_marks(self, marks: Mapping[str, MarketMark]) -> None:
        for symbol, mark in marks.items():
            self.SetSecurityMark(
                symbol,
                mark.price,
                as_of=mark.as_of,
                source=mark.source_ref,
            )
            for (_b_id, _norm_u, inst_id), pos in self._instrument_positions.items():
                if inst_id == symbol and pos.get("kind") == "stock_future":
                    self._apply_mark_to_position(
                        pos,
                        mark.price,
                        as_of=mark.as_of,
                        source=mark.source_ref,
                    )

    def authoritative_marks(self) -> dict[str, MarketMark]:
        marks: dict[str, MarketMark] = {}
        for symbol in self.mark_symbols():
            security = self._security(symbol)
            if not security.MarkAuthoritative or not security.MarkAsOf or not security.MarkSource:
                continue
            marks[symbol] = MarketMark(
                symbol=symbol,
                price=float(security.Price),
                as_of=security.MarkAsOf,
                source_ref=security.MarkSource,
            )
        return marks

    def performance_ledger(self) -> dict[str, Any]:
        pending = self._pending_performance_pair or {}
        return {
            "binding_id": self._performance_binding_id,
            "initial_cash": float(self._initial_cash),
            "cash": float(self._cash),
            "positions": self.positions(),
            "instrument_positions": self.instrument_positions,
            "realized_pnl": float(self._realized_pnl),
            "total_costs": float(self._total_costs),
            "margin_call_active": bool(self._margin_call_active),
            "margin_call_since": self._margin_call_since,
            "futures_mark_diagnostic": self.futures_mark_diagnostic(),
            "fill_count": int(self._fill_count),
            "ledger_started_at": self._ledger_started_at,
            "first_fill_at": self._first_fill_at,
            "last_fill_at": self._last_fill_at,
            "pending_performance_pair_id": pending.get("pair_id"),
            "state_path": str(self._state_path) if self._state_path else None,
            "state_error": self._state_error,
            "state_load_error": self._state_load_error,
            "state_binding_error": self._state_binding_error,
        }

    def _rekey_default_instrument_positions(self, binding_id: str) -> None:
        if not binding_id or binding_id == "default":
            return
        rekeyed: dict[tuple[str, str, str], dict[str, Any]] = {}
        for (b_id, norm_u, inst_id), pos in self._instrument_positions.items():
            eff_b = binding_id if b_id in ("", "default") else b_id
            rec = dict(pos)
            rec["persona_binding_id"] = eff_b
            rekeyed[(eff_b, norm_u, inst_id)] = rec
        self._instrument_positions = rekeyed

    def BindPerformanceBinding(self, binding_id: str | None) -> bool:  # noqa: N802
        if self._state_load_error:
            return False
        if self._loaded_state_missing_binding_id:
            return False
        candidate = str(binding_id or "").strip()
        if not candidate:
            self._state_binding_error = "performance binding identity is missing"
            return False
        if self._performance_binding_id:
            if self._performance_binding_id != candidate:
                self._state_binding_error = (
                    "performance ledger binding mismatch: "
                    f"state={self._performance_binding_id} runtime={candidate}"
                )
                return False
            self._state_binding_error = None
            self._rekey_default_instrument_positions(candidate)
            if self._state_error:
                self._persist_state()
            return self._state_error is None
        self._performance_binding_id = candidate
        self._state_binding_error = None
        self._rekey_default_instrument_positions(candidate)
        self._persist_state()
        return self._state_error is None

    def performance_window_state(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._performance_window_state))

    def pending_performance_pair(self) -> dict[str, Any] | None:
        if self._pending_performance_pair is None:
            return None
        return json.loads(json.dumps(self._pending_performance_pair))

    @staticmethod
    def _validated_performance_pair(
        payload: Mapping[str, Any],
        *,
        binding_id: str,
    ) -> dict[str, Any]:
        try:
            candidate = json.loads(json.dumps(dict(payload), allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"pending performance pair is not strict JSON: {exc}") from exc
        if candidate.get("schema_version") != "paper_performance_pair.v1":
            raise ValueError("unsupported pending performance pair schema")
        pair_id = str(candidate.get("pair_id") or "").strip()
        try:
            uuid.UUID(pair_id)
        except (ValueError, AttributeError) as exc:
            raise ValueError("pending performance pair_id must be a UUID") from exc
        if str(candidate.get("binding_id") or "").strip() != binding_id:
            raise ValueError("pending performance pair binding mismatch")
        valuation_as_of, _ = _parse_ledger_timestamp(
            candidate.get("valuation_as_of"),
            field="pending_performance_pair.valuation_as_of",
        )
        staged_at, _ = _parse_ledger_timestamp(
            candidate.get("staged_at"),
            field="pending_performance_pair.staged_at",
        )
        next_window = candidate.get("next_window")
        if not isinstance(next_window, dict):
            raise ValueError("pending performance pair next_window must be an object")
        # Validate the staged window before it can ever replace the committed
        # drawdown state. This also rejects malformed/non-finite observations.
        staged_tracker = RollingDrawdownTracker()
        staged_tracker.restore(next_window)
        if str(next_window.get("latest_as_of") or "") != valuation_as_of:
            raise ValueError("pending performance pair next_window as-of mismatch")
        events = candidate.get("events")
        expected_types = ("pnl_snapshot", "drawdown_snapshot")
        if not isinstance(events, dict) or set(events) != set(expected_types):
            raise ValueError("pending performance pair must contain exactly two event legs")
        event_ids: set[str] = set()
        validated_events: dict[str, Any] = {}
        for event_type in expected_types:
            leg = events.get(event_type)
            if not isinstance(leg, dict) or not isinstance(leg.get("acked"), bool):
                raise ValueError(f"pending {event_type} leg must contain boolean acked")
            event_payload = leg.get("payload")
            if not isinstance(event_payload, dict):
                raise ValueError(f"pending {event_type} payload must be an object")
            if event_payload.get("event_type") != event_type:
                raise ValueError(f"pending {event_type} payload type mismatch")
            event_created_at, _ = _parse_ledger_timestamp(
                event_payload.get("created_at"),
                field=f"pending_performance_pair.{event_type}.created_at",
            )
            if event_created_at != staged_at:
                raise ValueError(f"pending {event_type} created_at mismatch")
            event_id = str(event_payload.get("event_id") or "").strip()
            try:
                uuid.UUID(event_id)
            except (ValueError, AttributeError) as exc:
                raise ValueError(f"pending {event_type} event_id must be a UUID") from exc
            if event_id in event_ids:
                raise ValueError("pending performance pair event IDs must be distinct")
            event_ids.add(event_id)
            if str(event_payload.get("binding_id") or "").strip() != binding_id:
                raise ValueError(f"pending {event_type} payload binding mismatch")
            if str(event_payload.get(f"{'pnl' if event_type == 'pnl_snapshot' else 'drawdown'}_as_of") or "") != valuation_as_of:
                raise ValueError(f"pending {event_type} as-of mismatch")
            metadata = event_payload.get("metadata")
            if not isinstance(metadata, dict) or metadata.get("performance_pair_id") != pair_id:
                raise ValueError(f"pending {event_type} pair metadata mismatch")
            if metadata.get("performance_pair_leg") != event_type:
                raise ValueError(f"pending {event_type} leg metadata mismatch")
            metrics = event_payload.get("metrics")
            if not isinstance(metrics, dict):
                raise ValueError(f"pending {event_type} metrics must be an object")
            if event_type == "pnl_snapshot":
                if "pnl" not in metrics or "drawdown_pct" in metrics or "drawdown" in metrics:
                    raise ValueError("pending pnl leg has invalid primary metrics")
                _finite_float(metrics["pnl"], field="pending pnl metric")
            else:
                if "drawdown_pct" not in metrics or "pnl" in metrics:
                    raise ValueError("pending drawdown leg has invalid primary metrics")
                drawdown = _finite_float(
                    metrics["drawdown_pct"],
                    field="pending drawdown metric",
                )
                if not 0 <= drawdown <= 1:
                    raise ValueError("pending drawdown metric must be in [0, 1]")
            validated_events[event_type] = leg
        candidate.update(
            {
                "pair_id": pair_id,
                "binding_id": binding_id,
                "valuation_as_of": valuation_as_of,
                "staged_at": staged_at,
                "events": validated_events,
            }
        )
        return candidate

    def stage_performance_pair(self, payload: Mapping[str, Any]) -> bool:
        if self._pending_performance_pair is not None:
            self._state_error = "pending performance pair already exists"
            return False
        binding_id = str(self._performance_binding_id or "").strip()
        if not binding_id:
            self._state_error = "pending performance pair requires a bound ledger"
            return False
        try:
            candidate = self._validated_performance_pair(payload, binding_id=binding_id)
        except (TypeError, ValueError) as exc:
            self._state_error = f"{type(exc).__name__}: {exc}"
            return False
        self._pending_performance_pair = candidate
        if not self._persist_state():
            self._pending_performance_pair = None
            return False
        return True

    def ack_performance_pair_leg(self, pair_id: str, event_type: str) -> bool:
        pending = self._pending_performance_pair
        if pending is None or pending.get("pair_id") != pair_id:
            self._state_error = "pending performance pair identity mismatch"
            return False
        leg = pending.get("events", {}).get(event_type)
        if not isinstance(leg, dict):
            self._state_error = "pending performance pair leg is missing"
            return False
        if leg.get("acked") is True:
            return True
        previous = json.loads(json.dumps(pending))
        leg["acked"] = True
        if not self._persist_state():
            self._pending_performance_pair = previous
            return False
        return True

    def finalize_performance_pair(self, pair_id: str) -> bool:
        pending = self._pending_performance_pair
        if pending is None or pending.get("pair_id") != pair_id:
            self._state_error = "pending performance pair identity mismatch"
            return False
        events = pending.get("events", {})
        if not all(
            isinstance(events.get(event_type), dict)
            and events[event_type].get("acked") is True
            for event_type in ("pnl_snapshot", "drawdown_snapshot")
        ):
            self._state_error = "pending performance pair is not fully acknowledged"
            return False
        previous_window = self._performance_window_state
        previous_pending = pending
        self._performance_window_state = json.loads(json.dumps(pending["next_window"]))
        self._pending_performance_pair = None
        if not self._persist_state():
            self._performance_window_state = previous_window
            self._pending_performance_pair = previous_pending
            return False
        return True

    def save_performance_window(self, payload: Mapping[str, Any]) -> bool:
        try:
            serialized = json.dumps(dict(payload), allow_nan=False)
            candidate = json.loads(serialized)
        except (TypeError, ValueError) as exc:
            self._state_error = f"{type(exc).__name__}: {exc}"
            return False
        previous = self._performance_window_state
        self._performance_window_state = candidate
        if not self._persist_state():
            self._performance_window_state = previous
            return False
        return self._state_error is None

    def pending_fill_publications(self) -> list[dict[str, Any]]:
        """Committed fills whose lifecycle events are not yet durably admitted."""
        return json.loads(json.dumps(self._pending_fill_publications))

    def _stage_fill_publication(self, event: "OrderEvent") -> bool:
        record = json.loads(json.dumps(event.to_dict(), allow_nan=False))
        previous = self._pending_fill_publications
        self._pending_fill_publications = [*previous, record]
        if not self._persist_state():
            self._pending_fill_publications = previous
            return False
        return True

    def ack_fill_publication(self, event_id: str) -> bool:
        previous = self._pending_fill_publications
        remaining = [r for r in previous if r.get("event_id") != event_id]
        if len(remaining) == len(previous):
            return True
        self._pending_fill_publications = remaining
        if not self._persist_state():
            self._pending_fill_publications = previous
            return False
        return True

    def _record_fill(self) -> None:
        filled_at = _iso_now()
        if self._fill_count == 0:
            self._first_fill_at = filled_at
        self._fill_count += 1
        self._last_fill_at = filled_at
        if not self._persist_state():
            raise RuntimeError(
                "paper fill ledger persistence failed: "
                f"{self._state_error or 'unknown persistence error'}"
            )

    def _commit_fill(
        self,
        symbol: str,
        *,
        next_quantity: float,
        next_cash: float,
        execution_price: float | None = None,
        price_as_of: str | None = None,
        price_source: str = "paper_fill",
    ) -> None:
        """Atomically mutate and persist a fill before any fill event is published."""
        holding_symbol = self._resolve_holding_symbol(symbol)
        holding = self._holding(holding_symbol)
        security = self._security(symbol)
        holding_security = self._security(holding_symbol)
        tw_key = self._taiwan_underlying_key(symbol)
        alias_quantities: dict[str, float] = {}
        if tw_key is not None:
            for existing_symbol, existing_holding in self.Portfolio.items():
                if existing_symbol != holding_symbol and self._taiwan_underlying_key(existing_symbol) == tw_key:
                    alias_quantities[existing_symbol] = existing_holding.Quantity
        checkpoint = {
            "quantity": holding.Quantity,
            "alias_quantities": dict(alias_quantities),
            "cash": self._cash,
            "fill_count": self._fill_count,
            "first_fill_at": self._first_fill_at,
            "last_fill_at": self._last_fill_at,
            "price": security.Price,
            "mark_as_of": security.MarkAsOf,
            "mark_source": security.MarkSource,
            "mark_authoritative": security.MarkAuthoritative,
            "holding_price": holding_security.Price,
            "holding_mark_as_of": holding_security.MarkAsOf,
            "holding_mark_source": holding_security.MarkSource,
            "holding_mark_authoritative": holding_security.MarkAuthoritative,
            "active_episodes": dict(self._active_episodes),
            "last_fill_episode_id": self._last_fill_episode_id,
            "reversal_info": self._reversal_info,
            "instrument_positions": {k: dict(v) for k, v in self._instrument_positions.items()},
            "realized_pnl": self._realized_pnl,
            "total_costs": self._total_costs,
            "injected_equity_capital": self._injected_equity_capital,
        }
        try:
            prev_qty = float(holding.Quantity) + sum(float(v) for v in alias_quantities.values())
            new_qty = float(next_quantity)
            cand_ep = str(self._current_signal_metadata.get("trade_episode_id") or "").strip() or None
            self._reversal_info = None

            if abs(prev_qty) > 1e-12 and holding_symbol not in self._active_episodes:
                is_rev = (prev_qty > 0 and new_qty < 0) or (prev_qty < 0 and new_qty > 0)
                new_ep_id = (cand_ep or str(uuid.uuid4())) if is_rev else None
                if is_rev:
                    self._active_episodes[holding_symbol] = new_ep_id
                    self._reversal_info = {
                        "old_episode_id": None,
                        "new_episode_id": new_ep_id,
                        "close_qty": -prev_qty,
                        "open_qty": new_qty,
                    }
                self._last_fill_episode_id = new_ep_id
            elif abs(new_qty) <= 1e-12:
                self._last_fill_episode_id = self._active_episodes.pop(holding_symbol, None)
            elif abs(prev_qty) <= 1e-12:
                ep_id = cand_ep or str(uuid.uuid4())
                self._active_episodes[holding_symbol] = ep_id
                self._last_fill_episode_id = ep_id
            elif (prev_qty > 0 and new_qty > 0) or (prev_qty < 0 and new_qty < 0):
                self._last_fill_episode_id = self._active_episodes.get(holding_symbol)
            else:
                old_ep_id = self._active_episodes.pop(holding_symbol, None)
                new_ep_id = cand_ep if cand_ep and cand_ep != old_ep_id else str(uuid.uuid4())
                self._active_episodes[holding_symbol] = new_ep_id
                self._last_fill_episode_id = new_ep_id
                self._reversal_info = {
                    "old_episode_id": old_ep_id,
                    "new_episode_id": new_ep_id,
                    "close_qty": -prev_qty,
                    "open_qty": new_qty,
                }

            eff_price = float(
                execution_price if execution_price is not None else security.Price
            )
            pre_entry_price = (
                float(holding_security.Price)
                if (
                    math.isfinite(float(holding_security.Price))
                    and float(holding_security.Price) > 0
                )
                else eff_price
            )
            binding_id = self._resolve_persona_binding_id()
            norm_u = self._normalise_underlying_symbol(
                holding_symbol, self._current_signal_metadata
            )
            eq_inst_id = f"eq:{norm_u}"
            eq_key = (binding_id, norm_u, eq_inst_id)
            eq_pos = self._instrument_positions.get(eq_key)
            if eq_pos is None and abs(prev_qty) > 1e-12:
                eq_pos = {
                    "persona_binding_id": binding_id,
                    "normalised_underlying": norm_u,
                    "instrument_id": eq_inst_id,
                    "symbol": holding_symbol,
                    "kind": "cash_share",
                    "contract": None,
                    "contract_month": None,
                    "quantity": prev_qty,
                    "multiplier": 1.0,
                    "average_entry_price": pre_entry_price,
                    "mark_price": pre_entry_price,
                    "initial_margin_reserved": 0.0,
                    "maintenance_margin_required": 0.0,
                    "unrealized_pnl": 0.0,
                    "realized_pnl": 0.0,
                    "costs": 0.0,
                    "status": "open",
                }
                self._instrument_positions[eq_key] = eq_pos
                pre_snap = self.ledger_reconciliation_snapshot()
                pre_gap = (pre_snap["equity"] - pre_snap["initial_cash"]) - (
                    pre_snap["realized_pnl"] + pre_snap["unrealized_pnl"] - pre_snap["costs"]
                )
                if not math.isclose(pre_gap, 0.0, rel_tol=0.0, abs_tol=1e-9):
                    self._injected_equity_capital += pre_gap
            elif eq_pos is not None and not math.isclose(
                float(eq_pos.get("quantity", 0.0)), prev_qty, rel_tol=0.0, abs_tol=1e-9
            ):
                eq_pos["quantity"] = prev_qty
                if abs(prev_qty) > 1e-12 and float(eq_pos.get("average_entry_price", 0.0)) <= 0:
                    eq_pos["average_entry_price"] = pre_entry_price
                eq_pos["mark_price"] = pre_entry_price
                pre_snap = self.ledger_reconciliation_snapshot()
                pre_gap = (pre_snap["equity"] - pre_snap["initial_cash"]) - (
                    pre_snap["realized_pnl"] + pre_snap["unrealized_pnl"] - pre_snap["costs"]
                )
                if not math.isclose(pre_gap, 0.0, rel_tol=0.0, abs_tol=1e-9):
                    self._injected_equity_capital += pre_gap

            u_eq = new_qty - prev_qty
            avg_prev_eq = float(eq_pos.get("average_entry_price", 0.0)) if eq_pos else 0.0
            if abs(prev_qty) <= 1e-12 or (prev_qty > 0 and u_eq > 0) or (prev_qty < 0 and u_eq < 0):
                realized_delta_eq = 0.0
                avg_next_eq = (
                    (abs(prev_qty) * avg_prev_eq + abs(u_eq) * eff_price) / abs(new_qty)
                    if abs(new_qty) > 1e-12
                    else 0.0
                )
            elif abs(u_eq) <= abs(prev_qty) + 1e-12:
                closed_c = min(abs(prev_qty), abs(u_eq))
                sign_prev = 1.0 if prev_qty > 0 else -1.0
                realized_delta_eq = closed_c * (eff_price - avg_prev_eq) * sign_prev
                avg_next_eq = avg_prev_eq if abs(new_qty) > 1e-12 else 0.0
            else:
                closed_c = abs(prev_qty)
                sign_prev = 1.0 if prev_qty > 0 else -1.0
                realized_delta_eq = closed_c * (eff_price - avg_prev_eq) * sign_prev
                avg_next_eq = eff_price

            eq_record = dict(eq_pos or {})
            eq_record.update(
                {
                    "persona_binding_id": binding_id,
                    "normalised_underlying": norm_u,
                    "instrument_id": eq_inst_id,
                    "symbol": holding_symbol,
                    "kind": "cash_share",
                    "contract": None,
                    "contract_month": None,
                    "quantity": new_qty,
                    "multiplier": 1.0,
                    "average_entry_price": avg_next_eq,
                    "mark_price": eff_price,
                    "mark_as_of": price_as_of,
                    "mark_source": price_source,
                    "initial_margin_reserved": 0.0,
                    "maintenance_margin_required": 0.0,
                    "unrealized_pnl": 0.0,
                    "realized_pnl": float(eq_record.get("realized_pnl", 0.0)) + realized_delta_eq,
                    "costs": float(eq_record.get("costs", 0.0)),
                    "status": "open" if abs(new_qty) > 1e-12 else "closed",
                }
            )
            self._instrument_positions[eq_key] = eq_record
            self._realized_pnl += realized_delta_eq

            if execution_price is not None:
                self.SetSecurityPrice(
                    symbol,
                    execution_price,
                    as_of=price_as_of,
                    source=price_source,
                    authoritative=False,
                )
                if holding_symbol != symbol:
                    self.SetSecurityPrice(
                        holding_symbol,
                        execution_price,
                        as_of=price_as_of,
                        source=price_source,
                        authoritative=False,
                    )
            for alias_sym in alias_quantities:
                self.Portfolio[alias_sym].Quantity = 0.0
            holding.Quantity = next_quantity
            self._cash = next_cash
            self._record_fill()
            self.assert_ledger_reconciliation()
        except Exception:
            holding.Quantity = checkpoint["quantity"]
            for alias_sym, alias_qty in checkpoint["alias_quantities"].items():
                self.Portfolio[alias_sym].Quantity = alias_qty
            self._cash = checkpoint["cash"]
            self._fill_count = checkpoint["fill_count"]
            self._first_fill_at = checkpoint["first_fill_at"]
            self._last_fill_at = checkpoint["last_fill_at"]
            security.Price = checkpoint["price"]
            security.MarkAsOf = checkpoint["mark_as_of"]
            security.MarkSource = checkpoint["mark_source"]
            security.MarkAuthoritative = checkpoint["mark_authoritative"]
            holding_security.Price = checkpoint["holding_price"]
            holding_security.MarkAsOf = checkpoint["holding_mark_as_of"]
            holding_security.MarkSource = checkpoint["holding_mark_source"]
            holding_security.MarkAuthoritative = checkpoint["holding_mark_authoritative"]
            self._active_episodes = dict(checkpoint["active_episodes"])
            self._last_fill_episode_id = checkpoint["last_fill_episode_id"]
            self._reversal_info = checkpoint["reversal_info"]
            self._instrument_positions = checkpoint["instrument_positions"]
            self._realized_pnl = checkpoint["realized_pnl"]
            self._total_costs = checkpoint["total_costs"]
            self._injected_equity_capital = checkpoint["injected_equity_capital"]
            raise

    def _load_state(self) -> None:
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("paper performance ledger must be an object")
            if payload.get("schema_version") != "paper_performance_ledger.v1":
                raise ValueError("unsupported paper performance ledger schema")
            initial_cash = _finite_float(
                payload["initial_cash"],
                field="paper performance ledger initial_cash",
                positive=True,
            )
            cash = _finite_float(
                payload["cash"],
                field="paper performance ledger cash",
            )
            raw_fill_count = payload["fill_count"]
            if isinstance(raw_fill_count, bool) or not isinstance(raw_fill_count, int):
                raise ValueError("paper performance ledger fill_count must be an integer")
            fill_count = raw_fill_count
            if fill_count < 0:
                raise ValueError("paper performance ledger fill_count must be non-negative")
            holdings = payload.get("holdings")
            if not isinstance(holdings, dict):
                raise ValueError("paper performance ledger holdings must be an object")
            restored: dict[str, _Holding] = {}
            for symbol, raw_quantity in holdings.items():
                if not isinstance(symbol, str) or not symbol.strip():
                    raise ValueError("paper performance ledger contains an empty symbol")
                normalized_symbol = symbol.strip()
                quantity = _finite_float(
                    raw_quantity,
                    field=f"paper performance ledger quantity for {normalized_symbol}",
                )
                if quantity == 0:
                    raise ValueError(
                        f"paper performance ledger stores zero quantity for {normalized_symbol}"
                    )
                if normalized_symbol in restored:
                    raise ValueError(
                        f"paper performance ledger contains duplicate symbol {normalized_symbol}"
                    )
                restored[normalized_symbol] = _Holding(quantity)
            execution_prices = payload.get("execution_prices", {})
            if not isinstance(execution_prices, dict):
                raise ValueError("paper performance ledger execution_prices must be an object")
            restored_prices: dict[str, float] = {}
            for symbol, raw_price in execution_prices.items():
                if not isinstance(symbol, str) or not symbol.strip():
                    raise ValueError(
                        "paper performance ledger execution_prices contains an empty symbol"
                    )
                normalized_symbol = symbol.strip()
                price = _finite_float(
                    raw_price,
                    field=f"paper performance ledger execution price for {normalized_symbol}",
                    positive=True,
                )
                if normalized_symbol in restored_prices:
                    raise ValueError(
                        "paper performance ledger execution_prices contains duplicate symbol "
                        f"{normalized_symbol}"
                    )
                restored_prices[normalized_symbol] = price

            ledger_started_at, ledger_started_dt = _parse_ledger_timestamp(
                payload.get("ledger_started_at"),
                field="ledger_started_at",
            )
            first_fill_at_raw = payload.get("first_fill_at")
            last_fill_at_raw = payload.get("last_fill_at")
            binding_value = payload.get("binding_id")
            if binding_value is not None and not isinstance(binding_value, str):
                raise ValueError("paper performance ledger binding_id must be a string")
            normalized_binding_id = str(binding_value or "").strip()

            raw_instrument_positions = payload.get("instrument_positions")
            restored_instrument_positions: dict[tuple[str, str, str], dict[str, Any]] = {}
            open_futures_count = 0
            if raw_instrument_positions is not None:
                if not isinstance(raw_instrument_positions, list):
                    raise ValueError("paper performance ledger instrument_positions must be a list")
                for raw_pos in raw_instrument_positions:
                    if not isinstance(raw_pos, dict):
                        raise ValueError("paper performance ledger instrument_position entry must be an object")
                    json.dumps(raw_pos, allow_nan=False)
                    inst_id = str(raw_pos.get("instrument_id") or "").strip()
                    norm_u = str(raw_pos.get("normalised_underlying") or "").strip()
                    if not inst_id or not norm_u:
                        raise ValueError("paper performance ledger instrument_position lacks instrument_id or normalised_underlying")
                    b_id = str(raw_pos.get("persona_binding_id") or normalized_binding_id or "default").strip()
                    rec = dict(raw_pos)
                    rec["persona_binding_id"] = b_id
                    rec["normalised_underlying"] = norm_u
                    rec["instrument_id"] = inst_id
                    qty_val = _finite_float(rec.get("quantity", 0.0), field=f"instrument_position quantity for {inst_id}")
                    rec["quantity"] = qty_val
                    mark_val = _finite_float(rec.get("mark_price", 0.0), field=f"instrument_position mark_price for {inst_id}")
                    rec["mark_price"] = mark_val
                    if rec.get("kind") == "stock_future" and abs(qty_val) > 1e-12:
                        open_futures_count += 1
                    if mark_val > 0 and inst_id not in restored_prices:
                        restored_prices[inst_id] = mark_val
                    restored_instrument_positions[(b_id, norm_u, inst_id)] = rec

            restored_realized_pnl = _finite_float(
                payload.get("realized_pnl", 0.0),
                field="paper performance ledger realized_pnl",
            )
            restored_total_costs = _finite_float(
                payload.get("total_costs", 0.0),
                field="paper performance ledger total_costs",
            )
            restored_injected_equity = _finite_float(
                payload.get("injected_equity_capital", 0.0),
                field="paper performance ledger injected_equity_capital",
            )
            raw_margin_call_active = payload.get("margin_call_active", False)
            if not isinstance(raw_margin_call_active, bool):
                raise ValueError("paper performance ledger margin_call_active must be a boolean")
            raw_margin_call_since = payload.get("margin_call_since")
            if raw_margin_call_since is not None and not isinstance(raw_margin_call_since, str):
                raise ValueError("paper performance ledger margin_call_since must be a string or null")
            raw_last_futures_mark = payload.get("last_futures_mark_session_date")
            if raw_last_futures_mark is not None and not isinstance(raw_last_futures_mark, str):
                raise ValueError("paper performance ledger last_futures_mark_session_date must be a string or null")
            raw_settled_contracts = payload.get("settled_futures_contracts", [])
            if not isinstance(raw_settled_contracts, list) or not all(
                isinstance(item, str) for item in raw_settled_contracts
            ):
                raise ValueError("paper performance ledger settled_futures_contracts must be a list of strings")

            if fill_count == 0:
                if first_fill_at_raw is not None or last_fill_at_raw is not None:
                    raise ValueError(
                        "paper performance ledger without fills must not have fill timestamps"
                    )
                if restored or open_futures_count > 0:
                    raise ValueError(
                        "paper performance ledger without fills cannot contain holdings"
                    )
                if not math.isclose(
                    cash,
                    initial_cash + restored_realized_pnl - restored_total_costs,
                    rel_tol=0.0,
                    abs_tol=1e-9,
                ):
                    raise ValueError(
                        "paper performance ledger without fills must retain initial cash"
                    )
                first_fill_at = None
                last_fill_at = None
            else:
                first_fill_at, first_fill_dt = _parse_ledger_timestamp(
                    first_fill_at_raw,
                    field="first_fill_at",
                )
                last_fill_at, last_fill_dt = _parse_ledger_timestamp(
                    last_fill_at_raw,
                    field="last_fill_at",
                )
                if ledger_started_dt > first_fill_dt:
                    raise ValueError(
                        "paper performance ledger ledger_started_at is after first_fill_at"
                    )
                if first_fill_dt > last_fill_dt:
                    raise ValueError(
                        "paper performance ledger first_fill_at is after last_fill_at"
                    )
                missing_execution_prices = sorted(set(restored) - set(restored_prices))
                if missing_execution_prices:
                    raise ValueError(
                        "paper performance ledger holdings lack execution prices: "
                        f"{missing_execution_prices}"
                    )
                if len(restored) + open_futures_count > fill_count:
                    raise ValueError(
                        "paper performance ledger has more open symbols than recorded fills"
                    )

            if raw_instrument_positions is None and (restored or fill_count > 0):
                b_id_legacy = normalized_binding_id or "default"
                legacy_mv = 0.0
                for sym, holding_obj in restored.items():
                    qty_val = float(holding_obj.Quantity)
                    px_val = float(restored_prices.get(sym, 0.0))
                    legacy_mv += qty_val * px_val
                    norm_u = self._normalise_underlying_symbol(sym)
                    eq_inst_id = f"eq:{norm_u}"
                    restored_instrument_positions[(b_id_legacy, norm_u, eq_inst_id)] = {
                        "persona_binding_id": b_id_legacy,
                        "normalised_underlying": norm_u,
                        "instrument_id": eq_inst_id,
                        "symbol": sym,
                        "kind": "cash_share",
                        "contract": None,
                        "contract_month": None,
                        "quantity": qty_val,
                        "multiplier": 1.0,
                        "average_entry_price": px_val,
                        "mark_price": px_val,
                        "initial_margin_reserved": 0.0,
                        "maintenance_margin_required": 0.0,
                        "unrealized_pnl": 0.0,
                        "realized_pnl": 0.0,
                        "costs": 0.0,
                        "status": "open" if abs(qty_val) > 1e-12 else "closed",
                    }
                if "realized_pnl" not in payload and "injected_equity_capital" not in payload:
                    restored_realized_pnl = (cash + legacy_mv) - initial_cash

            performance_window = payload.get("performance_window", {})
            if not isinstance(performance_window, dict):
                raise ValueError("paper performance window must be an object")
            # Reject Python's permissive NaN/Infinity JSON extensions anywhere
            # in the persisted auxiliary state before mutating the live ledger.
            json.dumps(performance_window, allow_nan=False)

            pending_performance_pair = payload.get("pending_performance_pair")
            if pending_performance_pair is not None:
                if not normalized_binding_id:
                    raise ValueError("pending performance pair requires ledger binding identity")
                if not isinstance(pending_performance_pair, dict):
                    raise ValueError("pending performance pair must be an object")
                pending_performance_pair = self._validated_performance_pair(
                    pending_performance_pair,
                    binding_id=normalized_binding_id,
                )

            raw_pending_publications = payload.get("pending_fill_publications", [])
            if not isinstance(raw_pending_publications, list):
                raise ValueError("pending fill publications must be a list")
            restored_publications: list[dict[str, Any]] = []
            for record in raw_pending_publications:
                if (
                    not isinstance(record, dict)
                    or not isinstance(record.get("event_id"), str)
                    or record.get("event_type") != "paper_fill_simulated"
                ):
                    raise ValueError("pending fill publication record is malformed")
                restored_publications.append(json.loads(json.dumps(record, allow_nan=False)))

            raw_open_episodes = payload.get("open_episodes")
            restored_episodes: dict[str, str] = {}
            if raw_open_episodes is not None:
                if not isinstance(raw_open_episodes, dict):
                    raise ValueError("paper performance ledger open_episodes must be an object")
                for symbol, ep_id in raw_open_episodes.items():
                    if not isinstance(symbol, str) or not symbol.strip():
                        raise ValueError("paper performance ledger open_episodes contains an empty symbol")
                    if not isinstance(ep_id, str) or not ep_id.strip():
                        raise ValueError("paper performance ledger open_episodes contains an empty episode id")
                    normalized_symbol = symbol.strip()
                    if normalized_symbol in restored:
                        restored_episodes[normalized_symbol] = ep_id.strip()

            self._initial_cash = initial_cash
            self._cash = cash
            self.Portfolio = restored
            self._instrument_positions = restored_instrument_positions
            self._realized_pnl = restored_realized_pnl
            self._total_costs = restored_total_costs
            self._injected_equity_capital = restored_injected_equity
            self._margin_call_active = bool(raw_margin_call_active)
            self._margin_call_since = str(raw_margin_call_since).strip() if raw_margin_call_since else None
            self._last_futures_mark_session_date = (
                str(raw_last_futures_mark).strip() if raw_last_futures_mark else None
            )
            raw_mark_diag = payload.get("futures_mark_diagnostic")
            if raw_mark_diag is not None:
                if not isinstance(raw_mark_diag, dict):
                    raise ValueError("paper performance ledger futures_mark_diagnostic must be an object or null")
                json.dumps(raw_mark_diag, allow_nan=False)
                self._last_futures_mark_diagnostic = dict(raw_mark_diag)
            else:
                self._last_futures_mark_diagnostic = None
            self._settled_futures_contracts = set(raw_settled_contracts)
            self._active_episodes = restored_episodes
            self.Securities = {}
            for symbol, price in restored_prices.items():
                # Restored fill/execution prices are deliberately not
                # authoritative marks; source-ingest must refresh them.
                self._security(symbol).Price = price
            self._fill_count = fill_count
            self._last_fill_at = last_fill_at
            self._first_fill_at = first_fill_at
            self._ledger_started_at = ledger_started_at
            self._performance_window_state = dict(performance_window)
            self._pending_performance_pair = pending_performance_pair
            self._pending_fill_publications = restored_publications
            self._performance_binding_id = normalized_binding_id or None
            if self._performance_binding_id is None:
                self._loaded_state_missing_binding_id = True
                self._state_binding_error = (
                    "loaded performance ledger is missing binding identity"
                )
            self._state_error = None
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self._state_error = f"{type(exc).__name__}: {exc}"
            self._state_load_error = self._state_error

    def _persist_state(self) -> bool:
        if self._state_path is None:
            return True
        if self._state_load_error:
            # Never replace a ledger that failed validation during load.  The
            # operator needs the original bytes for diagnosis/recovery.
            self._state_error = self._state_load_error
            return False
        payload = {
            "schema_version": "paper_performance_ledger.v1",
            "binding_id": self._performance_binding_id,
            "initial_cash": float(self._initial_cash),
            "cash": float(self._cash),
            "fill_count": int(self._fill_count),
            "ledger_started_at": self._ledger_started_at,
            "first_fill_at": self._first_fill_at,
            "last_fill_at": self._last_fill_at,
            "performance_window": self._performance_window_state,
            "pending_performance_pair": self._pending_performance_pair,
            "pending_fill_publications": self._pending_fill_publications,
            "holdings": {
                symbol: float(holding.Quantity)
                for symbol, holding in sorted(self.Portfolio.items())
                if abs(float(holding.Quantity)) > 1e-12
            },
            "open_episodes": {
                symbol: str(ep_id)
                for symbol, ep_id in sorted(self._active_episodes.items())
                if symbol in self.Portfolio and abs(float(self.Portfolio[symbol].Quantity)) > 1e-12
            },
            "execution_prices": {
                symbol: float(security.Price)
                for symbol, security in sorted(self.Securities.items())
                if math.isfinite(float(security.Price)) and float(security.Price) > 0
            },
            "instrument_positions": [
                dict(pos)
                for _key, pos in sorted(
                    self._instrument_positions.items(),
                    key=lambda item: item[0],
                )
            ],
            "realized_pnl": float(self._realized_pnl),
            "total_costs": float(self._total_costs),
            "injected_equity_capital": float(self._injected_equity_capital),
            "margin_call_active": bool(self._margin_call_active),
            "margin_call_since": self._margin_call_since,
            "last_futures_mark_session_date": self._last_futures_mark_session_date,
            "futures_mark_diagnostic": self._last_futures_mark_diagnostic,
            "settled_futures_contracts": sorted(self._settled_futures_contracts),
        }
        temporary: Path | None = None
        directory_fd: int | None = None
        try:
            serialized = json.dumps(payload, sort_keys=True, allow_nan=False)
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            directory_fd = os.open(
                self._state_path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            temporary = self._state_path.with_name(f"{self._state_path.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_text(serialized, encoding="utf-8")
            with temporary.open("rb") as state_file:
                os.fsync(state_file.fileno())
            os.replace(temporary, self._state_path)
            os.fsync(directory_fd)
            self._state_error = None
            return True
        except (OSError, TypeError, ValueError) as exc:
            self._state_error = f"{type(exc).__name__}: {exc}"
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            return False
        finally:
            if directory_fd is not None:
                try:
                    os.close(directory_fd)
                except OSError:
                    pass

    def open_bracket_orders(self) -> list[dict[str, Any]]:
        return [dict(order) for order in self._open_bracket_orders]

    @property
    def open_episodes(self) -> dict[str, str]:
        return dict(self._active_episodes)

    def pnl(self) -> float:
        portfolio_value = self._cash
        for symbol, holding in self.Portfolio.items():
            portfolio_value += holding.Quantity * self._security(symbol).Price
        for (_b_id, _norm_u, inst_id), pos in self._instrument_positions.items():
            if pos.get("kind") != "stock_future":
                continue
            qty = float(pos.get("quantity", 0.0))
            if abs(qty) <= 1e-12:
                continue
            m = float(pos.get("multiplier", 1.0))
            avg_entry = float(pos.get("average_entry_price", 0.0))
            sec_price = float(self._security(inst_id).Price)
            mark_p = sec_price if sec_price > 0 else float(pos.get("mark_price", avg_entry))
            portfolio_value += qty * m * (mark_p - avg_entry)
        return float(portfolio_value - self._initial_cash)



class SyntheticMarketData:
    """Opt-in deterministic synthetic price source for PAPER runtimes only.

    Paper fills are marked at the fill price, so without any market-data feed a
    position's PnL stays flat at 0 and the telemetry -> reconcile -> evolution
    feedback half never has a signal to react to. When
    ``PANTHEON_PAPER_SYNTHETIC_MARKET_DATA`` is enabled this nudges held-symbol
    prices along a bounded, deterministic path (anchored at each symbol's first
    observed price) so paper PnL moves and the right half lights up.

    Paper-only by construction: it only calls ``algo.SetSecurityPrice`` on the
    in-process simulated book. It never connects a broker, never touches a real
    market-data feed, and is never wired for canary/live.
    """

    def __init__(self, *, amplitude: float = 0.05, freq: float = 0.5) -> None:
        self._amplitude = float(amplitude)
        self._freq = float(freq)
        self._step = 0
        self._anchor: dict[str, float] = {}

    @staticmethod
    def _phase_offset(symbol: str) -> float:
        return (sum(ord(c) for c in symbol) % 360) * math.pi / 180.0

    def advance(self, algo: Any) -> dict[str, float]:
        """Move every held symbol's price one bounded step; return new prices."""
        self._step += 1
        updated: dict[str, float] = {}
        portfolio = getattr(algo, "Portfolio", {}) or {}
        for symbol in list(portfolio.keys()):
            try:
                current = float(algo._security(symbol).Price)
            except Exception:  # noqa: BLE001
                current = 100.0
            anchor = self._anchor.setdefault(symbol, current if current else 100.0)
            price = round(
                anchor * (1.0 + self._amplitude * math.sin(self._step * self._freq + self._phase_offset(symbol))),
                4,
            )
            try:
                algo.SetSecurityPrice(
                    symbol,
                    price,
                    as_of=_iso_now(),
                    source="synthetic_market_data",
                    authoritative=False,
                )
            except TypeError:
                # Compatibility for the deliberately tiny fake used by the
                # isolated synthetic-source unit tests.  Canonical runtime
                # valuation never treats this source as authoritative.
                algo.SetSecurityPrice(symbol, price)
            updated[symbol] = price
        return updated


class RuntimeBindingResolver:
    """Resolve the current binding context for this runtime id."""

    def __init__(
        self,
        client: RuntimeManagerClient,
        runtime_id: str | None,
        runtime_context: PantheonRuntimeContext | None = None,
        runtime_binding_id: str | None = None,
    ) -> None:
        self._client = client
        self._runtime_id = runtime_id
        self._runtime_binding_id = str(
            runtime_binding_id
            or (
                runtime_context.runtime_binding_id
                if runtime_context is not None
                else ""
            )
            or ""
        ).strip()
        self._context_binding = (
            _binding_from_runtime_context(runtime_context) if runtime_context is not None else None
        )
        self._cached_binding: dict[str, Any] | None = (
            dict(self._context_binding) if self._context_binding is not None else None
        )
        self._binding_source: str | None = "runtime_context" if self._cached_binding else None
        self._last_sync_at: str | None = None
        self._last_error: str | None = None

    def resolve(self) -> dict[str, Any] | None:
        if not self._runtime_id:
            return None
        try:
            bindings = self._client.list_all()
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"{type(exc).__name__}: {exc}"
            log.warning("runtime-manager binding lookup failed: %s", exc)
            return self._cached_binding

        statuses = {"active": 0, "paused": 1, "pending_pause": 2, "failed": 3, "retired": 4}
        matches = [
            binding
            for binding in bindings
            if binding.get("runtime_id") == self._runtime_id
        ]
        if self._runtime_binding_id:
            exact_matches = [
                binding
                for binding in matches
                if str(
                    binding.get("binding_id")
                    or binding.get("runtime_binding_id")
                    or ""
                ).strip()
                == self._runtime_binding_id
            ]
            if exact_matches:
                matches = exact_matches
            elif self._context_binding is not None:
                self._cached_binding = dict(self._context_binding)
                self._binding_source = "runtime_context"
                self._last_sync_at = _iso_now()
                self._last_error = None
                return self._cached_binding
            else:
                self._cached_binding = None
                self._binding_source = None
                self._last_sync_at = _iso_now()
                self._last_error = (
                    "runtime binding lookup found no exact binding_id "
                    f"{self._runtime_binding_id!r} for runtime_id "
                    f"{self._runtime_id!r}"
                )
                return None
        if not matches:
            self._cached_binding = dict(self._context_binding) if self._context_binding else None
            self._binding_source = "runtime_context" if self._context_binding else None
            self._last_sync_at = _iso_now()
            self._last_error = None
            return self._cached_binding

        matches.sort(key=lambda item: statuses.get(str(item.get("status")), 99))
        self._cached_binding = matches[0]
        self._binding_source = "runtime_manager"
        self._last_sync_at = _iso_now()
        self._last_error = None
        return self._cached_binding

    def snapshot(self) -> dict[str, Any]:
        binding = self._cached_binding or {}
        return {
            "resolved": self._cached_binding is not None,
            "binding_id": binding.get("binding_id"),
            "status": binding.get("status"),
            "deployment_mode": binding.get("deployment_mode"),
            "capital_pool_id": binding.get("capital_pool_id"),
            "plan_id": binding.get("plan_id"),
            "artifact_id": binding.get("artifact_id"),
            "artifact_version": binding.get("artifact_version"),
            "persona_capital_binding_id": binding.get("persona_capital_binding_id"),
            "source": self._binding_source,
            "last_sync_at": self._last_sync_at,
            "last_error": self._last_error,
        }


class RuntimeTelemetryEmitter:
    """Emit paper-only canonical telemetry envelopes to the ingest surface."""

    def __init__(
        self,
        identity: RuntimeIdentity,
        binding_resolver: RuntimeBindingResolver,
        runtime_context: PantheonRuntimeContext | None = None,
    ) -> None:
        self._identity = identity
        self._binding_resolver = binding_resolver
        self._runtime_context = runtime_context
        self._url = str(self._identity.telemetry_url or os.getenv("PANTHEON_TELEMETRY_URL", "")).strip().rstrip("/")
        self._timeout = int(os.getenv("PANTHEON_TELEMETRY_TIMEOUT_SECONDS", "5"))
        self._service_token = os.getenv(
            "PANTHEON_TELEMETRY_SERVICE_TOKEN", ""
        ).strip()
        self._tenant_id = (
            os.getenv("PANTHEON_TENANT_ID", "default").strip() or "default"
        )
        self._enabled = bool(self._url)
        self._sent = 0
        self._failed = 0
        self._last_error: str | None = None
        self.last_delivery_lag_ms: float | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def build_event(
        self,
        event_type: str,
        metrics: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        *,
        event_id: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any] | None:
        """Build a schema-valid paper TelemetryEvent or return None on invariant failure."""
        binding = self._binding_resolver.resolve()
        if not binding:
            return self._fail_build("binding context unresolved")

        deployment_stage = str(
            binding.get("deployment_stage")
            or binding.get("deployment_mode")
            or self._identity.deployment_stage
            or self._identity.runtime_mode
            or "paper"
        ).strip().lower()
        if deployment_stage != "paper":
            return self._fail_build(
                f"paper runtime telemetry must use deployment_stage='paper', got {deployment_stage!r}"
            )

        binding_id = str(binding.get("binding_id") or binding.get("runtime_binding_id") or "")
        runtime_id = str(binding.get("runtime_id") or self._identity.runtime_id or "")
        capital_pool_id = str(binding.get("capital_pool_id") or "")
        artifact_id = str(binding.get("artifact_id") or "")
        artifact_version = str(binding.get("artifact_version") or "")
        plan_id = str(binding.get("plan_id") or binding.get("deployment_plan_id") or "")
        persona_capital_binding_id = str(binding.get("persona_capital_binding_id") or "")
        required = {
            "binding_id": binding_id,
            "runtime_id": runtime_id,
            "capital_pool_id": capital_pool_id,
            "artifact_id": artifact_id,
            "artifact_version": artifact_version,
            "plan_id": plan_id,
            "persona_capital_binding_id": persona_capital_binding_id,
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            return self._fail_build(f"binding context missing required fields: {missing}")

        supplied_metadata = dict(metadata or {})
        strategy_id = str(
            supplied_metadata.get("strategy_id")
            or os.getenv("PANTHEON_STRATEGY_ID")
            or (self._runtime_context.artifact.strategy_id if self._runtime_context else "")
            or artifact_id
            or "paper-runtime"
        )
        artifact_type = str(os.getenv("PANTHEON_ARTIFACT_TYPE", "execution_bundle"))
        event_metadata = self._base_metadata(binding)
        event_metadata.update(supplied_metadata)
        authority_refs = self._identity.authority_refs()
        persona_id = self._resolve_persona_id(binding, event_metadata)
        if persona_id:
            authority_refs["persona_id"] = persona_id
            event_metadata["persona_id"] = persona_id
        incoming_envelope = event_metadata.get("correlation_envelope")
        event_metrics = dict(metrics)
        metric_as_of: str | None = None
        if event_type == "pnl_snapshot":
            raw_as_of = event_metrics.pop("pnl_as_of", None)
            metric_as_of = str(raw_as_of) if raw_as_of not in (None, "") else None
        elif event_type == "drawdown_snapshot":
            raw_as_of = event_metrics.pop("drawdown_as_of", None)
            metric_as_of = str(raw_as_of) if raw_as_of not in (None, "") else None
        payload = {
            "event_id": event_id or str(uuid.uuid4()),
            "event_type": event_type,
            "created_at": created_at or _iso_now(),
            "execution_mode": "paper",
            "environment": deployment_stage,
            "deployment_stage": deployment_stage,
            "binding_id": binding_id,
            "runtime_id": runtime_id,
            "capital_pool_id": capital_pool_id,
            "artifact_id": artifact_id,
            "artifact_version": artifact_version,
            "plan_id": plan_id,
            "persona_capital_binding_id": persona_capital_binding_id,
            "authority_refs": authority_refs,
            "target": {
                "registry_id": artifact_id,
                "strategy_id": strategy_id,
                "artifact_version": artifact_version,
                "artifact_type": artifact_type,
                "promotion_state": "paper",
            },
            "metrics": event_metrics,
            "metadata": event_metadata,
        }
        if metric_as_of is not None:
            payload[f"{'pnl' if event_type == 'pnl_snapshot' else 'drawdown'}_as_of"] = metric_as_of
        lineage_ref = os.getenv("PANTHEON_LINEAGE_REF", "").strip()
        if lineage_ref:
            payload["target"]["lineage_ref"] = lineage_ref
        if self._identity.trace_id:
            payload["trace_id"] = self._identity.trace_id
        if isinstance(incoming_envelope, Mapping):
            try:
                outgoing_envelope = propagate_envelope(
                    incoming_envelope,
                    producer="execution.paper_runtime",
                    event_id=str(payload["event_id"]),
                    event_time=str(payload["created_at"]),
                )
            except CorrelationEnvelopeError as exc:
                return self._fail_build(f"invalid correlation envelope: {exc}")
            payload["correlation_envelope"] = outgoing_envelope
            payload["tenant_id"] = outgoing_envelope["tenant_id"]
            payload["journey_id"] = outgoing_envelope["journey_id"]
            payload["trace_id"] = outgoing_envelope["trace_id"]

        if event_type in _CANONICAL_LIFECYCLE_EVENT_TYPES and isinstance(
            payload.get("correlation_envelope"), Mapping
        ):
            run_id = str(event_metadata.get("run_id") or "").strip()
            signal_id = str(event_metadata.get("signal_id") or "").strip()
            raw_sequence = event_metadata.get("sequence_no")
            causal_parent_id = str(
                event_metadata.get("causal_parent_id") or ""
            ).strip()
            if isinstance(raw_sequence, bool) or not isinstance(raw_sequence, int):
                return self._fail_build(
                    "canonical lifecycle sequence_no must be a positive integer"
                )
            if raw_sequence < 1:
                return self._fail_build(
                    "canonical lifecycle sequence_no must be a positive integer"
                )
            if not run_id or not signal_id or not causal_parent_id:
                return self._fail_build(
                    "canonical lifecycle telemetry requires run_id, signal_id, and causal_parent_id"
                )
            outgoing_envelope = payload["correlation_envelope"]
            if outgoing_envelope["causation_event_id"] != causal_parent_id:
                return self._fail_build(
                    "causal_parent_id must match correlation envelope causation_event_id"
                )
            journey_id = str(outgoing_envelope["journey_id"])
            loop_run_id = str(
                event_metadata.get("loop_run_id") or f"lr-{run_id}"
            )
            payload.update(
                {
                    "aggregate_type": "trade_journey",
                    "aggregate_id": journey_id,
                    "sequence_no": raw_sequence,
                    "causal_parent_id": causal_parent_id,
                    "source_mode": "live",
                    "run_id": run_id,
                    "loop_run_id": loop_run_id,
                    "signal_id": signal_id,
                }
            )
            event_metadata.setdefault("journey_id", journey_id)
            event_metadata.setdefault("loop_run_id", loop_run_id)
            event_metadata.setdefault("source_mode", "live")
            for field in (
                "decision_id",
                "risk_decision_id",
                "client_order_id",
                "order_id",
                "fill_id",
                "position_id",
                "symbol",
            ):
                value = event_metadata.get(field)
                if value not in (None, ""):
                    payload[field] = value
            if event_type == "position_snapshot":
                position_qty = event_metrics.get("position_qty")
                if position_qty is not None:
                    payload["position_qty"] = position_qty
                price = event_metrics.get("price")
                if price is not None:
                    payload["price"] = price
        return payload

    def _resolve_persona_id(
        self,
        binding: Mapping[str, Any],
        event_metadata: Mapping[str, Any],
    ) -> str:
        binding_metadata = (
            binding.get("metadata") if isinstance(binding.get("metadata"), Mapping) else {}
        )
        candidates = (
            self._identity.persona_id,
            binding.get("persona_id"),
            binding.get("sponsor_persona_id"),
            binding_metadata.get("persona_id"),
            binding_metadata.get("sponsor_persona_id"),
            event_metadata.get("persona_id"),
            event_metadata.get("sponsor_persona_id"),
            os.getenv("PANTHEON_PERSONA_ID"),
        )
        for candidate in candidates:
            cleaned = _clean_text(candidate)
            if cleaned:
                return cleaned
        return ""

    def emit(
        self,
        event_type: str,
        metrics: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if not self._enabled:
            return False

        payload = self.build_event(event_type, metrics, metadata)
        if payload is None:
            return False

        return self.emit_payload(payload)

    def emit_payload(self, payload: Mapping[str, Any]) -> bool:
        """Deliver an already-built immutable event for exact retry.

        Important producer events are staged durably before this method is
        called.  Retrying the stored payload preserves its event ID, metrics,
        metadata, and correlation envelope so telemetry idempotency can safely
        collapse an accepted response that was lost in transit.
        """
        if not self._enabled:
            return False

        try:
            body = json.dumps(dict(payload), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            self._failed += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            return False
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Tenant-Id": self._tenant_id,
        }
        if self._service_token:
            headers["Authorization"] = f"Bearer {self._service_token}"
        request = urllib.request.Request(
            f"{self._url}/api/telemetry/ingest",
            data=body,
            headers=headers,
            method="POST",
        )
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                status_code = getattr(response, "status", 200)
                if status_code not in {200, 202}:
                    raise RuntimeError(f"telemetry ingest returned HTTP {status_code}")
            self.last_delivery_lag_ms = round((time.monotonic() - t0) * 1000.0, 3)
        except urllib.error.HTTPError as exc:
            self._failed += 1
            self._last_error = f"HTTPError {exc.code}: {exc.reason}"
            return False
        except Exception as exc:  # noqa: BLE001
            self._failed += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            return False

        self._sent += 1
        self._last_error = None
        return True

    def emit_deploy_started(self) -> bool:
        return self.emit(
            "deploy_started",
            {"action": "deploy_started"},
            metadata={"runtime_package": "paper_execution_runtime"},
        )

    def emit_deploy_completed(self) -> bool:
        return self.emit(
            "deploy_completed",
            {"action": "deploy_completed"},
            metadata={"runtime_package": "paper_execution_runtime"},
        )

    def emit_heartbeat(
        self, metadata: dict[str, Any] | None = None, extra_metrics: dict[str, Any] | None = None
    ) -> bool:
        return self.emit("heartbeat", {"heartbeat": 1, **(extra_metrics or {})}, metadata=metadata)

    def emit_pnl_snapshot(
        self,
        pnl: float,
        metadata: dict[str, Any] | None = None,
        extra_metrics: dict[str, Any] | None = None,
    ) -> bool:
        metrics: dict[str, Any] = {"pnl": float(pnl)}
        if extra_metrics:
            metrics.update(extra_metrics)
        return self.emit("pnl_snapshot", metrics, metadata=metadata)

    def emit_drawdown_snapshot(
        self,
        drawdown_pct: float,
        metadata: dict[str, Any] | None = None,
        extra_metrics: dict[str, Any] | None = None,
    ) -> bool:
        metrics: dict[str, Any] = {"drawdown_pct": float(drawdown_pct)}
        if extra_metrics:
            metrics.update(extra_metrics)
        return self.emit("drawdown_snapshot", metrics, metadata=metadata)

    def _base_metadata(self, binding: dict[str, Any]) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "runtime_role": self._identity.runtime_role,
        }
        binding_effective_at = binding.get("effective_at") or binding.get(
            "binding_effective_at"
        )
        if binding_effective_at not in (None, ""):
            metadata["runtime_binding_effective_at"] = str(binding_effective_at)
        ctx = self._runtime_context
        b_src = {"engine_bridge_repo": ctx.bridge.repo, "engine_bridge_path": ctx.bridge.path, "engine_bridge_commit": ctx.bridge.commit, "runtime_adapter_version": ctx.bridge.runtime_adapter_version, "context_source": ctx.context_source.value} if ctx is not None else binding
        if bridge_meta := _extract_verified_bridge(b_src):
            metadata.update(bridge_meta)
        return metadata

    def _fail_build(self, message: str) -> None:
        self._failed += 1
        self._last_error = message
        return None

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "url": self._url or None,
            "sent": self._sent,
            "failed": self._failed,
            "last_error": self._last_error,
            "delivery_lag_ms": self.last_delivery_lag_ms,
            "event_delivery_lag_ms": self.last_delivery_lag_ms,
        }


class _LifecycleBatch:
    """Ordered canonical lifecycle payloads staged for one atomic admission."""

    def __init__(self, chains: Mapping[str, Mapping[str, Any]]) -> None:
        self.chains: dict[str, dict[str, Any]] = {
            key: dict(value) for key, value in chains.items()
        }
        self.items: list[dict[str, Any]] = []


class LifecycleTelemetryOutboxError(RuntimeError):
    """Raised when canonical lifecycle durability cannot be guaranteed."""


class LifecycleTelemetryOutbox:
    """Crash-safe exact-payload outbox for canonical lifecycle telemetry."""

    _SCHEMA_VERSION = "paper_lifecycle_telemetry_outbox.v1"

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._state = self._empty_state()
        self._load_error: str | None = None
        self._last_error: str | None = None
        self._load()

    @classmethod
    def _empty_state(cls) -> dict[str, Any]:
        return {
            "schema_version": cls._SCHEMA_VERSION,
            "next_admission_no": 1,
            "chains": {},
            "pending": [],
        }

    @staticmethod
    def _strict_copy(value: Any) -> Any:
        return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            candidate = self._strict_copy(raw)
            self._validate_state(candidate)
            self._state = candidate
        except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            self._load_error = f"{type(exc).__name__}: {exc}"
            self._last_error = self._load_error

    @classmethod
    def _validate_state(cls, state: Mapping[str, Any]) -> None:
        if state.get("schema_version") != cls._SCHEMA_VERSION:
            raise ValueError("unsupported lifecycle telemetry outbox schema")
        next_admission_no = state.get("next_admission_no")
        if (
            isinstance(next_admission_no, bool)
            or not isinstance(next_admission_no, int)
            or next_admission_no < 1
        ):
            raise ValueError("lifecycle telemetry next_admission_no must be positive")
        chains = state.get("chains")
        pending = state.get("pending")
        if not isinstance(chains, dict) or not isinstance(pending, list):
            raise ValueError("lifecycle telemetry outbox chains/pending are malformed")
        checkpoint_envelopes: dict[str, dict[str, Any]] = {}
        for journey_id, checkpoint in chains.items():
            if not isinstance(journey_id, str) or not journey_id:
                raise ValueError("lifecycle telemetry chain journey_id is missing")
            if not isinstance(checkpoint, dict):
                raise ValueError("lifecycle telemetry chain checkpoint is malformed")
            sequence_no = checkpoint.get("sequence_no")
            if (
                isinstance(sequence_no, bool)
                or not isinstance(sequence_no, int)
                or sequence_no < 1
            ):
                raise ValueError("lifecycle telemetry chain sequence_no must be positive")
            event_id = str(checkpoint.get("event_id") or "")
            uuid.UUID(event_id)
            envelope = validate_envelope(checkpoint.get("correlation_envelope"))
            if envelope["journey_id"] != journey_id or envelope["event_id"] != event_id:
                raise ValueError("lifecycle telemetry chain envelope does not match checkpoint")
            checkpoint_envelopes[journey_id] = envelope

        seen_admissions: set[int] = set()
        seen_events: set[str] = set()
        last_pending_by_journey: dict[str, dict[str, Any]] = {}
        previous_admission = 0
        for record in pending:
            if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
                raise ValueError("lifecycle telemetry pending record is malformed")
            admission_no = record.get("admission_no")
            if (
                isinstance(admission_no, bool)
                or not isinstance(admission_no, int)
                or admission_no < 1
                or admission_no in seen_admissions
                or admission_no <= previous_admission
            ):
                raise ValueError("lifecycle telemetry admission order is invalid")
            previous_admission = admission_no
            seen_admissions.add(admission_no)
            journey_id = str(record.get("journey_id") or "")
            event_id = str(record.get("event_id") or "")
            sequence_no = record.get("sequence_no")
            payload = record["payload"]
            if journey_id not in chains or event_id in seen_events:
                raise ValueError("lifecycle telemetry pending identity is invalid")
            uuid.UUID(event_id)
            if payload.get("event_id") != event_id:
                raise ValueError("lifecycle telemetry pending payload event_id mismatch")
            if (
                isinstance(sequence_no, bool)
                or not isinstance(sequence_no, int)
                or sequence_no < 1
            ):
                raise ValueError("lifecycle telemetry pending sequence_no must be positive")
            if payload.get("aggregate_type") != "trade_journey":
                raise ValueError("lifecycle telemetry pending aggregate_type mismatch")
            if payload.get("aggregate_id") != journey_id:
                raise ValueError("lifecycle telemetry pending aggregate_id mismatch")
            if payload.get("sequence_no") != sequence_no:
                raise ValueError("lifecycle telemetry pending payload sequence_no mismatch")
            causal_parent_id = str(payload.get("causal_parent_id") or "")
            if not causal_parent_id:
                raise ValueError("lifecycle telemetry pending causal_parent_id is missing")
            payload_envelope = validate_envelope(payload.get("correlation_envelope"))
            if (
                payload_envelope["event_id"] != event_id
                or payload_envelope["journey_id"] != journey_id
                or payload_envelope["causation_event_id"] != causal_parent_id
            ):
                raise ValueError(
                    "lifecycle telemetry pending envelope does not match payload identity"
                )
            prior = last_pending_by_journey.get(journey_id)
            if prior is not None and (
                sequence_no != int(prior["sequence_no"]) + 1
                or causal_parent_id != prior["event_id"]
            ):
                raise ValueError("lifecycle telemetry pending journey chain is not contiguous")
            last_pending_by_journey[journey_id] = {
                "sequence_no": sequence_no,
                "event_id": event_id,
                "correlation_envelope": payload_envelope,
            }
            seen_events.add(event_id)
        if seen_admissions and next_admission_no <= max(seen_admissions):
            raise ValueError("lifecycle telemetry next_admission_no is stale")
        for journey_id, latest in last_pending_by_journey.items():
            checkpoint = chains[journey_id]
            if (
                checkpoint["sequence_no"] != latest["sequence_no"]
                or checkpoint["event_id"] != latest["event_id"]
                or checkpoint_envelopes[journey_id]
                != latest["correlation_envelope"]
            ):
                raise ValueError(
                    "lifecycle telemetry pending payload does not match chain checkpoint"
                )

    def _require_healthy(self) -> None:
        if self._load_error:
            raise LifecycleTelemetryOutboxError(
                f"lifecycle telemetry outbox load failed: {self._load_error}"
            )

    def _write_state(self, state: Mapping[str, Any]) -> None:
        self._require_healthy()
        temporary: Path | None = None
        directory_fd: int | None = None
        try:
            serialized = json.dumps(state, sort_keys=True, allow_nan=False)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            directory_fd = os.open(
                self._path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            temporary = self._path.with_name(
                f"{self._path.name}.{uuid.uuid4().hex}.tmp"
            )
            with temporary.open("w", encoding="utf-8") as state_file:
                state_file.write(serialized)
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(temporary, self._path)
            os.fsync(directory_fd)
            self._last_error = None
        except (OSError, TypeError, ValueError) as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            raise LifecycleTelemetryOutboxError(
                f"lifecycle telemetry outbox persistence failed: {self._last_error}"
            ) from exc
        finally:
            if directory_fd is not None:
                try:
                    os.close(directory_fd)
                except OSError:
                    pass

    def verify_writable(self) -> None:
        """Fail before queue consumption when the durable store is unavailable."""
        with self._lock:
            self._write_state(self._state)

    def chains(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            self._require_healthy()
            return self._strict_copy(self._state["chains"])

    def pending(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            self._require_healthy()
            pending = self._state["pending"]
            if limit is not None:
                if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                    raise ValueError("lifecycle telemetry pending limit must be positive")
                pending = pending[:limit]
            return self._strict_copy(pending)

    def admit(
        self,
        *,
        payload: Mapping[str, Any],
        journey_id: str,
        checkpoint: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Atomically persist an exact payload and its advanced chain checkpoint."""
        with self._lock:
            self._require_healthy()
            exact_payload = self._strict_copy(dict(payload))
            exact_checkpoint = self._strict_copy(dict(checkpoint))
            event_id = str(exact_payload.get("event_id") or "")
            sequence_no = exact_checkpoint.get("sequence_no")
            if not journey_id or exact_checkpoint.get("event_id") != event_id:
                raise LifecycleTelemetryOutboxError(
                    "lifecycle telemetry admission identity is invalid"
                )
            uuid.UUID(event_id)
            envelope = validate_envelope(exact_checkpoint.get("correlation_envelope"))
            if envelope["journey_id"] != journey_id or envelope["event_id"] != event_id:
                raise LifecycleTelemetryOutboxError(
                    "lifecycle telemetry admission envelope is invalid"
                )
            for record in self._state["pending"]:
                if record["event_id"] == event_id:
                    if (
                        record["journey_id"] != journey_id
                        or record["sequence_no"] != sequence_no
                        or record["payload"] != exact_payload
                    ):
                        raise LifecycleTelemetryOutboxError(
                            "lifecycle telemetry event_id payload conflict"
                        )
                    return self._strict_copy(record["payload"])
            current = self._state["chains"].get(journey_id)
            expected_sequence = 1 if current is None else int(current["sequence_no"]) + 1
            if sequence_no != expected_sequence:
                raise LifecycleTelemetryOutboxError(
                    "lifecycle telemetry admission sequence is not contiguous"
                )

            candidate = self._strict_copy(self._state)
            admission_no = int(candidate["next_admission_no"])
            candidate["next_admission_no"] = admission_no + 1
            candidate["chains"][journey_id] = exact_checkpoint
            candidate["pending"].append(
                {
                    "admission_no": admission_no,
                    "journey_id": journey_id,
                    "event_id": event_id,
                    "sequence_no": sequence_no,
                    "payload": exact_payload,
                }
            )
            self._validate_state(candidate)
            self._write_state(candidate)
            self._state = candidate
            return self._strict_copy(exact_payload)

    def admit_many(
        self,
        items: Iterable[Mapping[str, Any]],
        *,
        batch_id: str,
    ) -> list[dict[str, Any]]:
        """Atomically persist an ordered lifecycle batch in one durable write.

        Each item carries ``payload``, ``journey_id`` and ``checkpoint`` exactly
        as ``admit`` does. Either every payload is admitted or none is, so a
        committed paper fill can never leave a half-written event chain.
        ``batch_id`` is remembered so replaying the same batch after a crash
        between admission and acknowledgement is an idempotent no-op even once
        the payloads were already delivered and removed from ``pending``.
        """
        with self._lock:
            self._require_healthy()
            batch = list(items)
            if not batch or not batch_id:
                raise LifecycleTelemetryOutboxError(
                    "lifecycle telemetry batch admission requires items and a batch id"
                )
            admitted_batches = list(self._state.get("admitted_batches") or [])
            if batch_id in admitted_batches:
                return [
                    self._strict_copy(dict(item["payload"])) for item in batch
                ]
            candidate = self._strict_copy(self._state)
            exact_payloads: list[dict[str, Any]] = []
            for item in batch:
                payload = self._strict_copy(dict(item["payload"]))
                journey_id = str(item["journey_id"])
                checkpoint = self._strict_copy(dict(item["checkpoint"]))
                event_id = str(payload.get("event_id") or "")
                sequence_no = checkpoint.get("sequence_no")
                if not journey_id or checkpoint.get("event_id") != event_id:
                    raise LifecycleTelemetryOutboxError(
                        "lifecycle telemetry admission identity is invalid"
                    )
                uuid.UUID(event_id)
                envelope = validate_envelope(checkpoint.get("correlation_envelope"))
                if envelope["journey_id"] != journey_id or envelope["event_id"] != event_id:
                    raise LifecycleTelemetryOutboxError(
                        "lifecycle telemetry admission envelope is invalid"
                    )
                current = candidate["chains"].get(journey_id)
                expected_sequence = 1 if current is None else int(current["sequence_no"]) + 1
                if sequence_no != expected_sequence:
                    raise LifecycleTelemetryOutboxError(
                        "lifecycle telemetry admission sequence is not contiguous"
                    )
                admission_no = int(candidate["next_admission_no"])
                candidate["next_admission_no"] = admission_no + 1
                candidate["chains"][journey_id] = checkpoint
                candidate["pending"].append(
                    {
                        "admission_no": admission_no,
                        "journey_id": journey_id,
                        "event_id": event_id,
                        "sequence_no": sequence_no,
                        "payload": payload,
                    }
                )
                exact_payloads.append(payload)
            admitted_batches.append(batch_id)
            # Never evicted by count: an id must outlive outbox delivery until the
            # ledger acknowledges the fill (release_batch), otherwise a replay
            # would re-publish the fill with a new sequence_no.
            candidate["admitted_batches"] = admitted_batches
            self._validate_state(candidate)
            self._write_state(candidate)
            self._state = candidate
            return [self._strict_copy(payload) for payload in exact_payloads]

    def release_batch(self, batch_id: str) -> None:
        """Forget an admitted batch id once its producer durably acknowledged it."""
        with self._lock:
            self._require_healthy()
            batches = list(self._state.get("admitted_batches") or [])
            if batch_id not in batches:
                return
            candidate = dict(self._state)
            candidate["admitted_batches"] = [b for b in batches if b != batch_id]
            self._validate_state(candidate)
            self._write_state(candidate)
            self._state = candidate

    def acknowledge(self, event_id: str) -> None:
        """Remove a pending record only after a positive remote acknowledgement."""
        self.acknowledge_many((event_id,))

    def acknowledge_many(self, event_ids: Iterable[str]) -> None:
        """Persist one removal for a positively acknowledged delivery batch."""
        with self._lock:
            self._require_healthy()
            acknowledged = {str(event_id) for event_id in event_ids if event_id}
            if not acknowledged:
                return
            remaining = [
                record
                for record in self._state["pending"]
                if record["event_id"] not in acknowledged
            ]
            if len(remaining) == len(self._state["pending"]):
                return
            candidate = dict(self._state)
            candidate["pending"] = remaining
            self._validate_state(candidate)
            self._write_state(candidate)
            self._state = candidate

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            pending = self._state["pending"]
            return {
                "schema_version": self._SCHEMA_VERSION,
                "path": str(self._path),
                "status": "degraded" if self._last_error else "ok",
                "pending_count": len(pending),
                "chain_count": len(self._state["chains"]),
                "oldest_admission_no": (
                    pending[0]["admission_no"] if pending else None
                ),
                "last_error": self._last_error,
            }


class PaperRuntimeService:
    """Background execution loop plus HTTP-readable runtime state."""

    def __init__(
        self,
        *,
        store: PendingSignalStore | None = None,
        identity: RuntimeIdentity | None = None,
        runtime_manager_client: RuntimeManagerClient | None = None,
        telemetry_emitter: RuntimeTelemetryEmitter | None = None,
        mark_provider: SourceIngestMarkProvider | None = None,
        futures_mark_provider: DatasetFuturesMarkProvider | None = None,
        runtime_context: PantheonRuntimeContext | None = None,
        poll_interval_seconds: float | None = None,
        max_batch_size: int | None = None,
        lifecycle_outbox_path: str | os.PathLike[str] | None = None,
        final_settlement_source: Any = None,
        capital_pool_reader: Callable[[str], Mapping[str, Any] | None] | None = None,
    ) -> None:
        if final_settlement_source is None:
            from services.execution.lean_runtime.futures_session_inputs import (
                final_settlement_source_from_env,
            )

            self._final_settlement_source = final_settlement_source_from_env()
        else:
            self._final_settlement_source = final_settlement_source
        if runtime_context is None:
            mf, stg = os.getenv("PANTHEON_RUNTIME_CONTEXT_MANIFEST"), os.getenv("PANTHEON_DEPLOYMENT_STAGE") or os.getenv("PANTHEON_RUNTIME_MODE")
            try:
                runtime_context = PantheonRuntimeContext.from_manifest(mf, expected_stage=stg) if mf and os.path.exists(mf) else PantheonRuntimeContext.from_env(os.environ, expected_stage=stg)
            except RuntimeContextError:
                runtime_context = None
        self._runtime_context = runtime_context
        identity_env = (
            _runtime_context_identity_env(runtime_context, os.environ)
            if runtime_context is not None
            else None
        )
        self._identity = identity or RuntimeIdentity.from_env(identity_env)
        # Resolve queue key: explicit env > binding-scoped > default.
        # The reconciler sets PANTHEON_SIGNAL_QUEUE_KEY for each worker subprocess;
        # direct invocations fall back to PANTHEON_RUNTIME_BINDING_ID if available.
        _explicit_key = os.getenv("PANTHEON_SIGNAL_QUEUE_KEY", "").strip()
        _binding_for_key = (self._identity.binding_id or "").strip()
        if _explicit_key:
            _resolved_queue_key = _explicit_key
        elif _binding_for_key:
            _resolved_queue_key = binding_queue_key(_binding_for_key)
        else:
            _resolved_queue_key = BINDING_QUEUE_KEY_PREFIX
        self._store = store or build_pending_signal_store(
            os.getenv("SIGNAL_STORE_URL", "redis://signal-store:6379"),
            queue_key=_resolved_queue_key,
            default_batch_size=int(os.getenv("PANTHEON_SIGNAL_BATCH_SIZE", "100")),
        )
        self._runtime_manager_client = runtime_manager_client or RuntimeManagerClient(
            base_url=self._identity.runtime_manager_url,
            bearer_token=self._identity.runtime_manager_auth.token,
        )
        self._binding_resolver = RuntimeBindingResolver(
            self._runtime_manager_client,
            self._identity.runtime_id,
            runtime_context=runtime_context,
            runtime_binding_id=self._identity.binding_id,
        )
        self._telemetry = telemetry_emitter or RuntimeTelemetryEmitter(
            self._identity,
            self._binding_resolver,
            runtime_context=runtime_context,
        )
        if _as_bool(os.getenv("PANTHEON_LEGACY_JOURNEY_BFF_PUBLISH_ENABLED")):
            raise RuntimeError(
                "PANTHEON_LEGACY_JOURNEY_BFF_PUBLISH_ENABLED is retired and cannot be enabled. "
                "Direct legacy trade journey publishing to /bff/management/trade-journeys/events "
                "has been removed. Canonical telemetry events via /api/telemetry/events are required."
            )
        self._legacy_journey_publish_enabled = False
        performance_state_path = os.getenv("PANTHEON_PERFORMANCE_STATE_PATH") or None
        lifecycle_scope = "".join(
            character
            if character.isalnum() or character in {"-", "_", "."}
            else "_"
            for character in (
                self._identity.runtime_id
                or self._identity.binding_id
                or "paper-runtime"
            )
        )
        configured_lifecycle_path = (
            str(lifecycle_outbox_path)
            if lifecycle_outbox_path is not None
            else os.getenv("PANTHEON_LIFECYCLE_OUTBOX_PATH")
        )
        if configured_lifecycle_path:
            resolved_lifecycle_path = configured_lifecycle_path
        elif performance_state_path:
            ledger_path = Path(performance_state_path)
            resolved_lifecycle_path = str(
                ledger_path.with_name(f"{ledger_path.name}.lifecycle-outbox.json")
            )
        else:
            resolved_lifecycle_path = str(
                Path("/data/runtime/lifecycle-outbox")
                / f"{lifecycle_scope}.json"
            )
        self._lifecycle_outbox = LifecycleTelemetryOutbox(resolved_lifecycle_path)
        try:
            self._lifecycle_chains = self._lifecycle_outbox.chains()
            lifecycle_load_error = None
        except LifecycleTelemetryOutboxError as exc:
            self._lifecycle_chains = {}
            lifecycle_load_error = str(exc)
        self._blocked_lifecycle_chains: set[str] = set()
        self._lifecycle_outbox_verified = False
        self._lifecycle_outbox_persistence_error = lifecycle_load_error
        self._lifecycle_delivery_error: str | None = None
        self._fill_publication_error: str | None = None
        self._lifecycle_replay_batch_size = max(
            int(os.getenv("PANTHEON_LIFECYCLE_REPLAY_BATCH_SIZE", "512")),
            1,
        )
        self._mark_provider = mark_provider or SourceIngestMarkProvider()
        # One clock for the futures mark session and the freshness check.
        self._valuation_now: Callable[[], datetime] = lambda: datetime.now(
            timezone.utc
        )
        self._futures_mark_provider = futures_mark_provider or (
            DatasetFuturesMarkProvider(now=lambda: self._valuation_now())
        )
        self._drawdown_tracker = RollingDrawdownTracker(
            window_days=int(os.getenv("PANTHEON_PERFORMANCE_WINDOW_DAYS", "20"))
        )
        self._algo = PaperExecutionAlgorithm(
            event_sink=self._on_order_event,
            deployment_stage=self._identity.deployment_stage or self._identity.runtime_mode or "paper",
            bracket_order_execution_enabled=_as_bool(
                os.getenv("PANTHEON_BRACKET_ORDER_EXECUTION_ENABLED"),
                default=True,
            ),
            state_path=performance_state_path,
            durable_fill_publication=True,
            capital_pool_id=self._identity.capital_pool_id or None,
            binding_resolver=self._binding_resolver,
            capital_pool_reader=capital_pool_reader,
        )
        self._algo._after_signal_context_hook = self._maybe_process_futures_lifecycle
        self._performance_state_restore_error: str | None = None
        try:
            self._drawdown_tracker.restore(self._algo.performance_window_state())
        except (TypeError, ValueError) as exc:
            self._performance_state_restore_error = f"{type(exc).__name__}: {exc}"
        self._consumer = SignalConsumer(
            store_client=self._store,
            binding_id=self._identity.binding_id or None,
            runtime_id=self._identity.runtime_id or None,
            capital_pool_id=self._identity.capital_pool_id or None,
        )
        self._poll_interval_seconds = poll_interval_seconds or _as_float(
            os.getenv("PANTHEON_RUNTIME_POLL_INTERVAL_SECONDS"),
            1.0,
        )
        self._max_batch_size = max(int(max_batch_size or os.getenv("PANTHEON_SIGNAL_BATCH_SIZE", "100")), 1)
        self._lock = threading.RLock()
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._started_at = _iso_now()
        self._started_at_dt = datetime.now(timezone.utc)
        self._synthetic_market = (
            SyntheticMarketData()
            if _as_bool(os.getenv("PANTHEON_PAPER_SYNTHETIC_MARKET_DATA"))
            else None
        )
        self._last_poll_at: str | None = None
        self._last_drain_at: str | None = None
        self._last_skipped_status: str | None = None
        self._last_error: str | None = None
        self._last_heartbeat_at: str | None = None
        self._poll_count = 0
        self._processed_signal_count = 0
        self._execution_event_count = 0
        self._fill_event_count = 0
        self._recent_order_events: list[dict[str, Any]] = []
        self._performance_telemetry: dict[str, Any] = {
            "status": "not_evaluated",
            "code": "performance_not_evaluated",
        }

    def start(self) -> None:
        if self._thread is not None:
            return
        # A durable replay backlog must not delay the HTTP server or heartbeat
        # loop. The normal drain cycle replays a bounded batch before consuming
        # new signals, preserving admission order without blocking startup.
        self._emit_deploy_started()
        self._maybe_emit_heartbeat()

        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="paper-runtime-loop")
        self._thread.start()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="paper-runtime-heartbeat",
        )
        self._heartbeat_thread.start()
        self._emit_deploy_completed()

    def stop(self) -> None:
        self._shutdown.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=5)

    def drain_once(self) -> dict[str, Any]:
        # Lifecycle replay performs remote I/O and a durable JSON rewrite.  It
        # must not hold the service-state lock: readiness/heartbeat readers
        # need to remain responsive while an old outbox is draining.
        with self._lock:
            self._last_poll_at = _iso_now()
        lifecycle_replay_error: BaseException | None = None
        try:
            self._ensure_lifecycle_outbox_ready()
            self._flush_lifecycle_outbox()
        except Exception as exc:  # noqa: BLE001
            lifecycle_replay_error = exc

        with self._lock:
            before = len(self._consumer._processed_signal_ids)
            binding = self._binding_resolver.resolve()
            ledger_binding_failed = False
            try:
                if lifecycle_replay_error is not None:
                    raise lifecycle_replay_error
                if not binding:
                    raise RuntimeError(
                        "RuntimeBinding is required before paper execution can drain signals"
                    )
                self._algo.BindRuntimeBinding(binding)
                binding_id = str(
                    binding.get("binding_id")
                    or binding.get("runtime_binding_id")
                    or ""
                )
                if not self._algo.BindPerformanceBinding(binding_id):
                    ledger = self._algo.performance_ledger()
                    ledger_binding_failed = True
                    self._performance_telemetry = {
                        "status": "invalid_ledger",
                        "code": "performance_ledger_load_failed",
                        "attempted_at": _iso_now(),
                        "state_path": ledger.get("state_path"),
                        "detail": (
                            ledger.get("state_load_error")
                            or ledger.get("state_binding_error")
                            or ledger.get("state_error")
                            or "paper performance ledger binding failed"
                        ),
                    }
                    raise RuntimeError(
                        ledger.get("state_binding_error")
                        or ledger.get("state_error")
                        or "paper performance ledger binding failed"
                    )
                self._replay_pending_fill_publications()
                binding_status = str(binding.get("status") or "").lower()
                if binding_status in _HALT_BINDING_STATUSES:
                    # Safety gate (KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY):
                    # do not execute while the binding is halted; hold signals on
                    # the queue so they replay when the binding returns to active.
                    self._last_skipped_status = binding_status
                    self._last_drain_at = _iso_now()
                    self._last_error = None
                else:
                    self._last_skipped_status = None
                    self._algo._drained_signal_contexts.clear()
                    self._consumer.drain(algo=self._algo)
                    self._maybe_process_futures_lifecycle()
                    self._last_drain_at = _iso_now()
                    self._last_error = None
            except Exception as exc:  # noqa: BLE001
                self._last_error = f"{type(exc).__name__}: {exc}"
                log.exception("paper runtime drain failed")
            after = len(self._consumer._processed_signal_ids)
            self._processed_signal_count += max(after - before, 0)
            self._poll_count += 1
            if self._last_error is None:
                if self._synthetic_market is not None:
                    self._synthetic_market.advance(self._algo)
                self._maybe_emit_performance_snapshots()
                self._maybe_emit_heartbeat()
            elif ledger_binding_failed:
                # Execution remains blocked, but surface the durable-ledger
                # failure through the normal heartbeat diagnostics path.
                self._maybe_emit_heartbeat()
            return self.snapshot()

    def _maybe_process_futures_lifecycle(self) -> None:
        contexts = list(self._algo._drained_signal_contexts)
        self._algo._drained_signal_contexts.clear()
        if not contexts:
            contexts = [dict(self._algo._current_signal_metadata)]

        for raw_meta in contexts:
            had_open_before = bool(raw_meta.get("_had_open_futures_before_signal"))
            if not had_open_before and not self._algo._has_open_or_pending_stock_futures():
                continue
            meta = {
                k: v
                for k, v in raw_meta.items()
                if k != "_had_open_futures_before_signal"
            }
            session_date = (
                os.getenv("PANTHEON_FUTURES_SESSION_DATE", "").strip()
                or str(
                    meta.get("trade_date")
                    or meta.get("as_of_trading_day")
                    or meta.get("as_of_date")
                    or meta.get("signal_date")
                    or ""
                ).strip()
            )
            if not session_date or self._algo._parse_strict_iso_date(session_date) is None:
                continue

            prev_ctx = self._algo._current_signal_metadata
            self._algo._current_signal_metadata = dict(meta)
            try:
                final_source = (
                    meta.get("final_settlement_source")
                    if meta.get("final_settlement_source") is not None
                    else self._final_settlement_source
                )
                if self._algo._has_open_or_pending_stock_futures():
                    self._algo.SettleExpiringFutures(
                        session_date,
                        final_source,
                        partition_horizon=meta.get("as_of_trading_day"),
                    )

                open_active = self._algo._open_active_stock_future_contracts()
                if not open_active:
                    continue

                has_mark_inputs = (
                    had_open_before
                    or "session_settlement_prices" in meta
                    or "session_settlement_price_reasons" in meta
                    or "futures_mark_unavailable_reason" in meta
                    or "open_futures_contracts" in meta
                )
                if not has_mark_inputs:
                    continue

                raw_prices = meta.get("session_settlement_prices")
                settlement_prices = (
                    dict(raw_prices) if isinstance(raw_prices, Mapping) else {}
                )
                raw_reasons = meta.get("session_settlement_price_reasons")
                reasons_map = (
                    dict(raw_reasons) if isinstance(raw_reasons, Mapping) else {}
                )
                missing_by_contract: dict[str, str] = {}
                validated_prices: dict[str, float] = {}
                for inst_id in open_active:
                    pos = self._algo.get_instrument_position(inst_id) or {}
                    contract = str(pos.get("contract") or "")
                    month = str(pos.get("contract_month") or "")
                    cm_key = f"{contract}:{month}" if contract and month else ""
                    raw_val = (
                        settlement_prices.get(inst_id)
                        if inst_id in settlement_prices
                        else (
                            settlement_prices.get(cm_key)
                            if cm_key and cm_key in settlement_prices
                            else settlement_prices.get(contract)
                        )
                    )
                    price_ok = False
                    if raw_val is not None and not isinstance(raw_val, bool):
                        try:
                            f_val = float(raw_val)
                            if math.isfinite(f_val) and f_val > 0:
                                validated_prices[inst_id] = f_val
                                price_ok = True
                        except (TypeError, ValueError):
                            price_ok = False
                    if not price_ok:
                        reason_val = str(
                            reasons_map.get(inst_id)
                            or meta.get("futures_mark_unavailable_reason")
                            or "settlement_price_missing"
                        ).strip()
                        missing_by_contract[inst_id] = reason_val or "settlement_price_missing"

                if missing_by_contract:
                    summary_reason = (
                        str(meta.get("futures_mark_unavailable_reason") or "").strip()
                        or "; ".join(
                            f"{cid}:{missing_by_contract[cid]}"
                            for cid in sorted(missing_by_contract)
                        )
                    )
                    self._algo._last_futures_mark_diagnostic = {
                        "status": "skipped_missing_settlement_price",
                        "trade_date": session_date,
                        "reason": summary_reason,
                        "missing_contracts": dict(missing_by_contract),
                        "marked_contracts": {},
                    }
                    self._algo._persist_state()
                elif self._algo._last_futures_mark_session_date == session_date:
                    # One mark and one maintenance margin check per session.
                    continue
                else:
                    self._algo.MarkFuturesSession(session_date, validated_prices)
            finally:
                self._algo._current_signal_metadata = prev_ctx

    def process_futures_session(
        self,
        trade_date: Any,
        settlement_prices: Mapping[str, float] | None = None,
        *,
        final_settlement_source: Any = None,
        holidays: Iterable[Any] | None = None,
        check_maintenance: bool = True,
    ) -> dict[str, Any]:
        """Run deterministic futures expiry settlement and session mark-to-market on the runtime ledger."""
        with self._lock:
            binding = self._binding_resolver.resolve()
            if binding:
                self._algo.BindRuntimeBinding(binding)
                binding_id = str(
                    binding.get("binding_id")
                    or binding.get("runtime_binding_id")
                    or ""
                )
                if binding_id:
                    self._algo.BindPerformanceBinding(binding_id)
            effective_source = (
                final_settlement_source
                if final_settlement_source is not None
                else self._final_settlement_source
            )
            settled = self._algo.SettleExpiringFutures(
                trade_date,
                effective_source,
                holidays=holidays,
            )
            has_open_futures = any(
                pos.get("kind") == "stock_future"
                and abs(float(pos.get("quantity", 0.0))) > 1e-12
                for pos in self._algo._instrument_positions.values()
            )
            if settlement_prices is not None or has_open_futures:
                reconciliation = self._algo.MarkFuturesSession(
                    trade_date,
                    settlement_prices,
                    holidays=holidays,
                    check_maintenance=check_maintenance,
                )
            else:
                reconciliation = self._algo.ledger_reconciliation_snapshot()
            return {
                "settled_contracts": settled,
                "reconciliation": reconciliation,
                "positions": self._algo.positions(),
            }

    def pool_access_violation(self, requested_pool_id: str | None) -> dict[str, Any] | None:
        requested = str(requested_pool_id or "").strip()
        if not requested:
            return None
        current = self._current_capital_pool_id()
        if current and requested == current:
            return None
        return {
            "status": "blocked",
            "error": "capital_pool_scope_mismatch",
            "requested_capital_pool_id": requested,
            "runtime_capital_pool_id": current,
            "message": "Runtime state, credentials, positions, and PnL are scoped to the active RuntimeBinding capital pool.",
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            lifecycle_outbox = self._lifecycle_outbox.snapshot()
            lifecycle_outbox.update(
                {
                    "persistence_error": self._lifecycle_outbox_persistence_error,
                    "delivery_error": self._lifecycle_delivery_error,
                }
            )
            d_lag = getattr(self._telemetry, "last_delivery_lag_ms", None)
            lags = {"queue_lag_ms": self._current_queue_lag_ms(), "event_delivery_lag_ms": d_lag, "delivery_lag_ms": d_lag}
            return {
                **self._identity.to_health_payload(),
                "status": (
                    "ok"
                    if self._last_error is None
                    and self._lifecycle_outbox_persistence_error is None
                    else "degraded"
                ),
                "runtime_package": "paper_execution_runtime",
                "runtime_package_version": "ep4",
                "paper_execution_ready": True,
                "signal_consumer_ready": True,
                "signal_store": {
                    "kind": getattr(self._store, "kind", type(self._store).__name__),
                    "queue_depth": self._safe_queue_depth(),
                    "batch_size": self._max_batch_size,
                },
                "runtime_context": _runtime_context_snapshot(self._runtime_context),
                "binding_lookup": self._binding_resolver.snapshot(),
                "telemetry": self._telemetry.snapshot(),
                **lags,
                "lifecycle_outbox": lifecycle_outbox,
                "pending_fill_publication_count": len(
                    self._algo.pending_fill_publications()
                ),
                "fill_publication_error": self._fill_publication_error,
                "paper_state": {
                    "started_at": self._started_at,
                    "last_poll_at": self._last_poll_at,
                    "last_drain_at": self._last_drain_at,
                    "last_skipped_status": self._last_skipped_status,
                    "last_heartbeat_at": self._last_heartbeat_at,
                    "poll_count": self._poll_count,
                    "processed_signal_count": self._processed_signal_count,
                    "execution_event_count": self._execution_event_count,
                    **lags,
                    "bracket_order_execution_enabled": bool(
                        getattr(self._algo, "BracketOrderExecutionEnabled", False)
                    ),
                    "bracket_order_execution_stage": getattr(self._algo, "DeploymentStage", "unknown"),
                    "positions": self._algo.positions(),
                    "open_bracket_orders": self._algo.open_bracket_orders(),
                    "recent_order_events": list(self._recent_order_events),
                    "futures_mark_diagnostic": self._algo.futures_mark_diagnostic(),
                    "performance_telemetry": dict(self._performance_telemetry),
                    "last_error": self._last_error,
                },
                "stub_mode": False,
            }

    def _run_loop(self) -> None:
        while not self._shutdown.is_set():
            self.drain_once()
            self._shutdown.wait(self._poll_interval_seconds)

    def _heartbeat_loop(self) -> None:
        interval = max(
            1.0,
            _as_float(os.getenv("PANTHEON_RUNTIME_HEARTBEAT_INTERVAL_SECONDS"), 15.0),
        )
        while not self._shutdown.wait(interval):
            self._maybe_emit_heartbeat()

    @staticmethod
    def _signal_lifecycle_metadata(signal: Mapping[str, Any]) -> dict[str, Any]:
        nested = signal.get("metadata")
        metadata = dict(nested) if isinstance(nested, Mapping) else {}
        for field in (
            "signal_id",
            "strategy_id",
            "run_id",
            "binding_id",
            "runtime_id",
            "source_worker",
            "tenant_id",
            "environment",
            "journey_id",
            "decision_id",
            "correlation_envelope",
            "trade_episode_id",
        ):
            value = signal.get(field)
            if value not in (None, "", [], {}):
                metadata[field] = value
        return metadata

    @staticmethod
    def _canonical_lifecycle_identity(
        metadata: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        raw_envelope = metadata.get("correlation_envelope")
        run_id = str(metadata.get("run_id") or "").strip()
        signal_id = str(metadata.get("signal_id") or "").strip()
        if not isinstance(raw_envelope, Mapping) or not run_id or not signal_id:
            return None
        try:
            envelope = validate_envelope(raw_envelope)
        except CorrelationEnvelopeError as exc:
            log.warning("Skipping non-canonical lifecycle telemetry: %s", exc)
            return None
        normalized = dict(metadata)
        normalized["correlation_envelope"] = envelope
        normalized["run_id"] = run_id
        normalized["signal_id"] = signal_id
        normalized["journey_id"] = envelope["journey_id"]
        normalized.setdefault("tenant_id", envelope["tenant_id"])
        normalized.setdefault("environment", envelope["environment"])
        normalized.setdefault("loop_run_id", f"lr-{run_id}")
        return normalized, envelope

    def _record_lifecycle_persistence_failure(
        self,
        exc: BaseException,
        *,
        journey_id: str | None = None,
    ) -> None:
        self._lifecycle_outbox_persistence_error = f"{type(exc).__name__}: {exc}"
        self._lifecycle_outbox_verified = False
        if journey_id:
            self._blocked_lifecycle_chains.add(journey_id)

    def _ensure_lifecycle_outbox_ready(self) -> None:
        if self._lifecycle_outbox_verified:
            return
        try:
            self._lifecycle_outbox.verify_writable()
        except LifecycleTelemetryOutboxError as exc:
            self._record_lifecycle_persistence_failure(exc)
            raise
        self._lifecycle_outbox_verified = True
        self._lifecycle_outbox_persistence_error = None
        self._blocked_lifecycle_chains.clear()

    def _flush_lifecycle_outbox(self) -> bool:
        """Replay one bounded batch in durable admission order."""
        try:
            pending = self._lifecycle_outbox.pending(
                limit=self._lifecycle_replay_batch_size
            )
        except LifecycleTelemetryOutboxError as exc:
            self._record_lifecycle_persistence_failure(exc)
            return False
        if not pending:
            self._lifecycle_delivery_error = None
            return True
        sender = getattr(self._telemetry, "emit_payload", None)
        if not callable(sender):
            self._lifecycle_delivery_error = (
                "canonical lifecycle telemetry emitter lacks exact-payload delivery"
            )
            return False
        acknowledged_event_ids: list[str] = []
        delivery_error: str | None = None
        next_heartbeat_at = time.monotonic() + 10.0
        for record in pending:
            payload = record["payload"]
            try:
                acknowledged = bool(sender(payload))
            except Exception as exc:  # noqa: BLE001
                delivery_error = f"{type(exc).__name__}: {exc}"
                break
            if not acknowledged:
                delivery_error = (
                    f"remote acknowledgement missing for lifecycle event "
                    f"{record['event_id']}"
                )
                break
            acknowledged_event_ids.append(str(record["event_id"]))
            if time.monotonic() >= next_heartbeat_at:
                # A slow remote drain must never make the live runtime appear
                # dead to fleet monitoring.  Heartbeats use the independent
                # telemetry path and do not alter outbox admission order.
                self._maybe_emit_heartbeat()
                next_heartbeat_at = time.monotonic() + 10.0

        if acknowledged_event_ids:
            try:
                self._lifecycle_outbox.acknowledge_many(acknowledged_event_ids)
            except LifecycleTelemetryOutboxError as exc:
                # Exact payloads remain pending on disk. A subsequent replay may
                # duplicate remotely accepted events; ingest idempotency
                # collapses them by event_id.
                self._record_lifecycle_persistence_failure(
                    exc,
                    journey_id=str(pending[0]["journey_id"]),
                )
                return False
        self._lifecycle_delivery_error = delivery_error
        if delivery_error is not None:
            return False
        return self._lifecycle_outbox.snapshot()["pending_count"] == 0

    def _emit_lifecycle_telemetry(
        self,
        event_type: str,
        metrics: dict[str, Any],
        metadata: Mapping[str, Any],
        *,
        event_id: str,
        created_at: str,
        batch: "_LifecycleBatch | None" = None,
    ) -> dict[str, Any] | None:
        canonical = self._canonical_lifecycle_identity(metadata)
        if canonical is None:
            return None
        lifecycle_metadata, incoming_envelope = canonical
        journey_id = str(incoming_envelope["journey_id"])
        chains = self._lifecycle_chains if batch is None else batch.chains
        chain = chains.get(journey_id)
        if chain is None:
            sequence_no = 1
            causal_parent_id = str(incoming_envelope["event_id"])
        else:
            sequence_no = int(chain["sequence_no"]) + 1
            causal_parent_id = str(chain["event_id"])
            previous_envelope = chain.get("correlation_envelope")
            if isinstance(previous_envelope, Mapping):
                lifecycle_metadata["correlation_envelope"] = dict(previous_envelope)

        lifecycle_metadata["sequence_no"] = sequence_no
        lifecycle_metadata["causal_parent_id"] = causal_parent_id
        lifecycle_metadata["source_mode"] = "live"

        builder = getattr(self._telemetry, "build_event", None)
        sender = getattr(self._telemetry, "emit_payload", None)
        if not callable(builder) or not callable(sender):
            exc = LifecycleTelemetryOutboxError(
                "canonical lifecycle telemetry requires exact-payload build/delivery"
            )
            self._record_lifecycle_persistence_failure(exc, journey_id=journey_id)
            raise exc
        payload = builder(
            event_type,
            metrics,
            metadata=lifecycle_metadata,
            event_id=event_id,
            created_at=created_at,
        )
        if payload is None:
            exc = LifecycleTelemetryOutboxError(
                f"canonical lifecycle payload build failed for {event_type}"
            )
            self._record_lifecycle_persistence_failure(exc, journey_id=journey_id)
            raise exc

        outgoing_envelope = payload.get("correlation_envelope")
        if not isinstance(outgoing_envelope, Mapping):
            outgoing_envelope = propagate_envelope(
                lifecycle_metadata["correlation_envelope"],
                producer="execution.paper_runtime",
                event_id=event_id,
                event_time=created_at,
            )
        checkpoint = {
            "sequence_no": sequence_no,
            "event_id": event_id,
            "correlation_envelope": dict(outgoing_envelope),
        }
        if batch is not None:
            # Staged: nothing is durable until the whole batch is admitted.
            batch.chains[journey_id] = checkpoint
            batch.items.append(
                {
                    "payload": payload,
                    "journey_id": journey_id,
                    "checkpoint": checkpoint,
                }
            )
            return payload
        try:
            had_pending = bool(self._lifecycle_outbox.pending())
            exact_payload = self._lifecycle_outbox.admit(
                payload=payload,
                journey_id=journey_id,
                checkpoint=checkpoint,
            )
        except (LifecycleTelemetryOutboxError, CorrelationEnvelopeError) as exc:
            self._record_lifecycle_persistence_failure(exc, journey_id=journey_id)
            if isinstance(exc, LifecycleTelemetryOutboxError):
                raise
            raise LifecycleTelemetryOutboxError(str(exc)) from exc

        # A canonical payload is causally usable after local admission, not
        # after remote acknowledgement. This lets later fill/position payloads
        # join the same durable chain during an ingest outage.
        self._lifecycle_chains[journey_id] = checkpoint
        self._lifecycle_outbox_verified = True
        self._lifecycle_outbox_persistence_error = None
        self._blocked_lifecycle_chains.discard(journey_id)
        if not had_pending:
            self._flush_lifecycle_outbox()
        return exact_payload

    def _on_order_event(self, event: OrderEvent) -> None:
        """Event sink: a committed paper fill is never undone by publication."""
        if event.event_type != "paper_fill_simulated":
            self._handle_order_event(event)
            return
        try:
            self._handle_order_event(event)
        except Exception as exc:  # noqa: BLE001
            # The ledger already committed this fill. Raising would DLQ the
            # signal and leave its execution fence in_progress forever, so the
            # financial state would be stranded with no lifecycle record.
            # The staged record in the ledger is replayed by the next drain.
            self._fill_publication_error = f"{type(exc).__name__}: {exc}"
            log.exception(
                "committed paper fill %s lifecycle publication deferred for replay",
                event.event_id,
            )
            return
        self._fill_publication_error = None
        self._ack_fill_publication(event.event_id)

    def _ack_fill_publication(self, event_id: str) -> None:
        if not self._algo.ack_fill_publication(event_id):
            return
        try:
            self._lifecycle_outbox.release_batch(event_id)
        except LifecycleTelemetryOutboxError:
            log.exception("admitted batch %s release deferred", event_id)

    def _replay_pending_fill_publications(self) -> None:
        """Re-publish committed fills whose lifecycle chain was interrupted."""
        for record in self._algo.pending_fill_publications():
            try:
                event = OrderEvent(**record)
                self._handle_order_event(event, replay=True)
            except Exception as exc:  # noqa: BLE001
                self._fill_publication_error = f"{type(exc).__name__}: {exc}"
                log.exception(
                    "pending committed fill %s replay failed", record.get("event_id")
                )
                return
            self._fill_publication_error = None
            self._ack_fill_publication(str(record["event_id"]))

    def _emit_instrument_counterfactuals(
        self, rows: Any, *, created_at: str
    ) -> int:
        """Publish ``persona_instrument_counterfactual`` rows, one event each.

        The event id is derived from ``(decision_id, instrument_id)`` so a
        redelivery collapses at ingest, and the telemetry runtime summary keys
        its fold the same way.  Delivery is best effort and never raises into
        the signal path: a failed row is logged and counted by the emitter.
        """
        if not isinstance(rows, (list, tuple)):
            return 0
        builder = getattr(self._telemetry, "build_event", None)
        sender = getattr(self._telemetry, "emit_payload", None)
        if not callable(builder) or not callable(sender):
            return 0
        delivered = 0
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            decision_id = _clean_text(row.get("decision_id"))
            instrument_id = _clean_text(row.get("instrument_id"))
            if not decision_id or not instrument_id:
                continue
            metadata = {
                "runtime_package": "paper_execution_runtime",
                "sim_fill_flag": False,
                "is_real_order": False,
                "is_real_capital": False,
                "submitted_to_broker": False,
                "capital_scale_pct": 0,
            }
            metadata.update(dict(row))
            try:
                payload = builder(
                    "persona_instrument_counterfactual",
                    {"action": "instrument_counterfactual_graded"},
                    metadata=metadata,
                    event_id=str(
                        uuid.uuid5(
                            _LIFECYCLE_UUID_NAMESPACE,
                            f"persona_instrument_counterfactual:{decision_id}:{instrument_id}",
                        )
                    ),
                    created_at=created_at,
                )
                if payload is not None and sender(payload):
                    delivered += 1
                else:
                    log.warning(
                        "instrument counterfactual row %s/%s was not delivered",
                        decision_id,
                        instrument_id,
                    )
            except Exception:  # noqa: BLE001 - grading rows never block the signal path
                log.exception(
                    "instrument counterfactual row %s/%s delivery failed",
                    decision_id,
                    instrument_id,
                )
        return delivered

    def _handle_order_event(self, event: OrderEvent, *, replay: bool = False) -> None:
        if event.event_type == "signal_generation":
            signal_metadata = self._signal_lifecycle_metadata(event.metadata)
            signal_metadata.setdefault("symbol", event.symbol)
            signal_metadata.setdefault("order_type", event.metadata.get("order_type", "MARKET"))
            occurred_at = str(event.metadata.get("timestamp") or event.created_at)
            # The view fields belong to signal_generation; the instrument
            # decision record belongs to trade_decision.
            decision_metadata = dict(signal_metadata)
            signal_metadata.pop("expression_decision", None)
            # Counterfactual grading rows for earlier decisions ride on this
            # signal; they are published as their own events, not as lifecycle
            # metadata of this journey.
            counterfactual_rows = decision_metadata.pop(
                "instrument_counterfactual_rows", None
            )
            signal_metadata.pop("instrument_counterfactual_rows", None)
            try:
                signal_payload = self._emit_lifecycle_telemetry(
                    "signal_generation",
                    {
                        "action": "signal_generated",
                        "signal_quantity": event.quantity,
                    },
                    signal_metadata,
                    event_id=event.event_id,
                    created_at=occurred_at,
                )
            except LifecycleTelemetryOutboxError:
                # Pending stores remove on claim. Put the exact signal back when
                # local admission fails so execution cannot continue without a
                # durable lifecycle record.
                enqueue = getattr(self._store, "enqueue", None)
                if callable(enqueue):
                    try:
                        enqueue(dict(event.metadata))
                    except Exception as enqueue_exc:  # noqa: BLE001
                        log.exception(
                            "failed to requeue signal after lifecycle admission failure: %s",
                            enqueue_exc,
                        )
                raise
            if signal_payload is not None:
                decision_event_id = str(
                    uuid.uuid5(
                        _LIFECYCLE_UUID_NAMESPACE,
                        f"{event.event_id}:trade_decision",
                    )
                )
                self._emit_lifecycle_telemetry(
                    "trade_decision",
                    {"action": "trade_decision_recorded"},
                    decision_metadata,
                    event_id=decision_event_id,
                    created_at=occurred_at,
                )
                self._emit_instrument_counterfactuals(
                    counterfactual_rows, created_at=occurred_at
                )
            return

        event_payload = event.to_dict()
        if not replay:
            self._execution_event_count += 1
            if event.event_type == "paper_fill_simulated":
                self._fill_event_count += 1
            self._recent_order_events.append(event_payload)
            self._recent_order_events = self._recent_order_events[-20:]
        telemetry_metadata = {
            "runtime_package": "paper_execution_runtime",
            "symbol": event.symbol,
            "sim_fill_flag": event.event_type == "paper_fill_simulated",
            "is_real_order": False,
            "is_real_capital": False,
            "submitted_to_broker": event.submitted_to_broker,
            "capital_scale_pct": 0,
        }
        if event.broker_submission_status:
            telemetry_metadata["broker_submission_status"] = event.broker_submission_status
        telemetry_metadata.update(event.metadata)
        if event.event_type == "bracket_order_logged":
            metrics: dict[str, Any] = {
                "action": (
                    "bracket_submitted_to_broker"
                    if event.submitted_to_broker
                    else "bracket_logged_only"
                ),
                "submitted_to_broker": event.submitted_to_broker,
            }
        elif event.event_type == "order_rejection":
            metrics = {
                "rejected_order_count": 1,
                "fill_quantity": 0.0,
                "fill_rate": 0.0,
                "action": event.action,
                "submitted_to_broker": event.submitted_to_broker,
            }
            for field in ("requested_quantity", "computed_quantity", "holding_quantity"):
                if field in event.metadata:
                    metrics[field] = event.metadata[field]
            is_pre_broker_risk_rejection = (
                not event.submitted_to_broker
                and (
                    telemetry_metadata.get("risk_status") == "rejected"
                    or telemetry_metadata.get("risk_gate_evaluated") is True
                    or telemetry_metadata.get("reason") == "sell_without_holdings"
                    or telemetry_metadata.get("reject_reason") == "sell_without_holdings"
                    or event.broker_submission_status == "sell_without_holdings"
                )
            )
            if is_pre_broker_risk_rejection:
                risk_scope = (
                    _clean_text(telemetry_metadata.get("signal_id")) or event.event_id
                )
                telemetry_metadata.setdefault(
                    "risk_decision_id",
                    _clean_text(telemetry_metadata.get("decision_id"))
                    or f"paper-risk-{risk_scope}",
                )
                telemetry_metadata.setdefault("risk_status", "rejected")
        elif event.event_type == "paper_order_simulated":
            metrics = {
                "noop_count": 1,
                "fill_quantity": 0.0,
                "fill_rate": 0.0,
                "action": event.action,
                "submitted_to_broker": event.submitted_to_broker,
            }
            for field in ("requested_quantity", "computed_quantity"):
                if field in event.metadata:
                    metrics[field] = event.metadata[field]
            if not event.submitted_to_broker and (
                telemetry_metadata.get("decision_status") == "no_order"
                or telemetry_metadata.get("order_status") == "not_submitted"
                or bool(telemetry_metadata.get("noop_reason"))
            ):
                telemetry_metadata.setdefault("risk_status", "skipped")
        else:
            metrics = {
                "fill_quantity": event.quantity,
                "fill_price": event.fill_price,
                "action": event.action,
                "submitted_to_broker": event.submitted_to_broker,
            }
        lifecycle_payload = None
        canonical_lifecycle = (
            event.event_type in _CANONICAL_LIFECYCLE_EVENT_TYPES
            and self._canonical_lifecycle_identity(telemetry_metadata) is not None
        )
        committed_fill = (
            event.event_type == "paper_fill_simulated"
            and event.metadata.get("ledger_committed") is True
        )
        # A committed fill's whole canonical chain is staged and admitted in one
        # durable write, so a build/persistence failure cannot strand a partial
        # chain (e.g. risk_evaluation without the fill and position snapshot).
        batch = (
            _LifecycleBatch(self._lifecycle_chains)
            if committed_fill and canonical_lifecycle
            else None
        )
        if committed_fill and canonical_lifecycle:
            risk_event_id = str(
                uuid.uuid5(
                    _LIFECYCLE_UUID_NAMESPACE,
                    f"{event.event_id}:risk_evaluation",
                )
            )
            risk_metadata = dict(telemetry_metadata)
            risk_metadata.pop("reversal", None)
            risk_scope = (
                _clean_text(telemetry_metadata.get("signal_id")) or event.event_id
            )
            algo_gate = getattr(self._algo, "_last_risk_gate_result", None) or {}
            gate_decision_id = (
                _clean_text(event.metadata.get("risk_decision_id"))
                or _clean_text(telemetry_metadata.get("risk_decision_id"))
                or _clean_text(algo_gate.get("decision_id"))
                or f"paper-risk-{risk_scope}"
            )
            gate_risk_status = (
                _clean_text(event.metadata.get("risk_gate_status"))
                or _clean_text(telemetry_metadata.get("risk_status"))
                or _clean_text(algo_gate.get("risk_status"))
                or "succeeded"
            )
            risk_metadata["risk_decision_id"] = gate_decision_id
            risk_metadata["risk_status"] = gate_risk_status
            self._emit_lifecycle_telemetry(
                "risk_evaluation",
                {
                    "action": "paper_risk_accepted",
                    "risk_quantity": abs(event.quantity),
                    "risk_price": event.fill_price,
                    "submitted_to_broker": False,
                },
                risk_metadata,
                event_id=risk_event_id,
                created_at=event.created_at,
                batch=batch,
            )
            order_event_id = str(
                uuid.uuid5(
                    _LIFECYCLE_UUID_NAMESPACE,
                    f"{event.event_id}:order_submitted",
                )
            )
            telemetry_metadata.setdefault("order_id", f"paper-order-{order_event_id}")
            telemetry_metadata.setdefault("fill_id", f"paper-fill-{event.event_id}")
            order_metadata = dict(telemetry_metadata)
            order_metadata.pop("reversal", None)
            self._emit_lifecycle_telemetry(
                "order_submitted",
                {
                    "action": "paper_order_submitted",
                    "order_quantity": abs(event.quantity),
                    "order_price": event.fill_price,
                },
                order_metadata,
                event_id=order_event_id,
                created_at=event.created_at,
                batch=batch,
            )
            accepted_event_id = str(
                uuid.uuid5(
                    _LIFECYCLE_UUID_NAMESPACE,
                    f"{event.event_id}:order_accepted",
                )
            )
            accepted_metadata = dict(order_metadata)
            accepted_metadata.setdefault(
                "broker_order_id", f"paper-accepted-{order_event_id}"
            )
            accepted_metadata["broker_submission_status"] = "simulated_accepted"
            self._emit_lifecycle_telemetry(
                "order_accepted",
                {
                    "action": "paper_order_accepted",
                    "order_quantity": abs(event.quantity),
                    "order_price": event.fill_price,
                    "submitted_to_broker": False,
                },
                accepted_metadata,
                event_id=accepted_event_id,
                created_at=event.created_at,
                batch=batch,
            )
        if canonical_lifecycle and (
            event.event_type != "paper_fill_simulated" or committed_fill
        ):
            lifecycle_payload = self._emit_lifecycle_telemetry(
                event.event_type,
                metrics,
                telemetry_metadata,
                event_id=event.event_id,
                created_at=event.created_at,
                batch=batch,
            )
        if lifecycle_payload is None and not canonical_lifecycle:
            self._telemetry.emit(event.event_type, metrics, metadata=telemetry_metadata)

        if (
            committed_fill
            and lifecycle_payload is not None
        ):
            fill_count = int(event.metadata.get("ledger_fill_count") or 0)
            position_event_id = str(
                uuid.uuid5(
                    _LIFECYCLE_UUID_NAMESPACE,
                    f"{event.event_id}:position_snapshot",
                )
            )
            position_metadata = dict(telemetry_metadata)
            position_metadata.pop("reversal", None)
            position_metadata.update(
                {
                    "ledger_committed": True,
                    "ledger_fill_count": fill_count,
                    "source_fill_event_id": event.event_id,
                    "position_id": (
                        f"{self._identity.runtime_id or 'paper-runtime'}:"
                        f"{event.symbol}:{fill_count}"
                    ),
                }
            )
            # Use the values committed with the fill, not live algorithm state,
            # so a replay after restart builds the identical position payload.
            position_quantity = event.metadata.get("position_quantity")
            if position_quantity is None:
                position_quantity = self._algo._holding(event.symbol).Quantity
            position_quantity = float(position_quantity)
            position_price = float(event.fill_price)
            position_cash = event.metadata.get("ledger_cash")
            if position_cash is None:
                position_cash = float(self._algo._cash)
            self._emit_lifecycle_telemetry(
                "position_snapshot",
                {
                    "action": "position_snapshot_committed",
                    "position_qty": position_quantity,
                    "price": position_price,
                    "cash": float(position_cash),
                },
                position_metadata,
                event_id=position_event_id,
                created_at=event.created_at,
                batch=batch,
            )
        if batch is not None and batch.items:
            self._admit_lifecycle_batch(batch, batch_id=event.event_id)

    def _admit_lifecycle_batch(self, batch: _LifecycleBatch, *, batch_id: str) -> None:
        journeys = {str(item["journey_id"]) for item in batch.items}
        try:
            had_pending = bool(self._lifecycle_outbox.pending())
            self._lifecycle_outbox.admit_many(batch.items, batch_id=batch_id)
        except (LifecycleTelemetryOutboxError, CorrelationEnvelopeError) as exc:
            for journey_id in journeys:
                self._record_lifecycle_persistence_failure(exc, journey_id=journey_id)
            if isinstance(exc, LifecycleTelemetryOutboxError):
                raise
            raise LifecycleTelemetryOutboxError(str(exc)) from exc
        # The durable chain is authoritative. A replay of an already-admitted
        # batch is a no-op on disk, so adopting the re-staged (further advanced)
        # in-memory chain would desynchronise the next event's sequence_no.
        durable_chains = self._lifecycle_outbox.chains()
        for journey_id in journeys:
            durable = durable_chains.get(journey_id)
            self._lifecycle_chains[journey_id] = (
                durable if durable is not None else batch.chains[journey_id]
            )
            self._blocked_lifecycle_chains.discard(journey_id)
        self._lifecycle_outbox_verified = True
        self._lifecycle_outbox_persistence_error = None
        if not had_pending:
            self._flush_lifecycle_outbox()

    def _maybe_emit_heartbeat(self) -> None:
        if not self._telemetry.enabled:
            return
        now = _iso_now()
        if self._last_heartbeat_at == now:
            return
        q_lag, d_lag = self._current_queue_lag_ms(), getattr(self._telemetry, "last_delivery_lag_ms", None)
        lags = {k: v for k, v in (("queue_lag_ms", q_lag), ("event_delivery_lag_ms", d_lag), ("delivery_lag_ms", d_lag)) if v is not None}
        hb_meta = {
            "runtime_package": "paper_execution_runtime",
            "queue_depth": self._safe_queue_depth(),
            "is_real_order": False,
            "is_real_capital": False,
            "sim_fill_flag": False,
            "capital_scale_pct": 0,
            "performance_telemetry": dict(self._performance_telemetry),
            "runtime_heartbeat": {"connectivity_status": "connected" if self._last_error is None else "degraded", "broker_status": "not_applicable", **lags},
            **lags,
        }
        try:
            emitted = self._telemetry.emit_heartbeat(metadata=hb_meta, extra_metrics=lags)
        except TypeError:
            emitted = self._telemetry.emit_heartbeat(metadata=hb_meta)
        if emitted:
            self._last_heartbeat_at = now

    def _maybe_emit_performance_snapshots(self) -> None:
        if not self._telemetry.enabled:
            return

        if self._algo.pending_performance_pair() is not None:
            self._flush_pending_performance_pair()
            return

        all_symbols = self._algo.mark_symbols()
        # A stock future has no source-ingest snapshot; its mark is the
        # dataset D-1 daily settlement, the value MarkFuturesSession applies.
        symbols = [s for s in all_symbols if not s.startswith("ssf:")]
        futures_symbols = [s for s in all_symbols if s.startswith("ssf:")]
        provider_diagnostic: dict[str, Any]
        provider_marks: dict[str, MarketMark] = {}
        if symbols:
            provider_marks, provider_diagnostic = self._mark_provider.resolve(symbols)
        else:
            provider_diagnostic = self._mark_provider.snapshot(requested_symbols=[])
        if futures_symbols:
            wanted = set(futures_symbols)
            futures_marks, futures_diagnostic = self._futures_mark_provider.resolve(
                [
                    rec
                    for rec in self._algo.open_stock_future_position_records()
                    if rec["instrument_id"] in wanted
                ]
            )
            provider_marks = {**provider_marks, **futures_marks}
            provider_diagnostic = {
                **provider_diagnostic,
                "futures_settlement": futures_diagnostic,
            }
        if provider_marks:
            self._algo.apply_market_marks(provider_marks)

        ledger = self._algo.performance_ledger()
        if (
            ledger.get("state_load_error")
            or ledger.get("state_binding_error")
            or self._performance_state_restore_error
        ):
            self._performance_telemetry = {
                "status": "invalid_ledger",
                "code": "performance_ledger_load_failed",
                "attempted_at": _iso_now(),
                "state_path": ledger.get("state_path"),
                "detail": (
                    ledger.get("state_load_error")
                    or ledger.get("state_binding_error")
                    or self._performance_state_restore_error
                ),
            }
            return
        if ledger.get("state_error"):
            self._performance_telemetry = {
                "status": "invalid_ledger",
                "code": "performance_ledger_persist_failed",
                "attempted_at": _iso_now(),
                "state_path": ledger.get("state_path"),
                "detail": ledger.get("state_error"),
            }
            return
        valuation = value_portfolio(
            initial_cash=ledger["initial_cash"],
            cash=ledger["cash"],
            positions=ledger["positions"],
            # Use only this resolve cycle.  A prior algorithm/security mark
            # must not survive a source timeout or a newly missing symbol.
            marks=provider_marks,
            fill_count=ledger["fill_count"],
            last_fill_at=ledger["last_fill_at"],
            mark_diagnostic=provider_diagnostic,
            max_mark_age_seconds=_as_float(
                os.getenv("PANTHEON_PERFORMANCE_MARK_MAX_AGE_SECONDS"),
                172800.0,
            ),
            now=self._valuation_now(),
        )
        self._performance_telemetry = {
            "status": valuation.status,
            **valuation.diagnostic,
        }
        if valuation.sample is None:
            return

        sample = valuation.sample
        tracker_checkpoint = self._drawdown_tracker.export_state()
        try:
            drawdown_metrics = self._drawdown_tracker.observe(
                sample,
                initial_equity_as_of=(
                    ledger.get("first_fill_at") or ledger.get("ledger_started_at")
                ),
            )
            if drawdown_metrics is None:
                drawdown_metrics = (
                    self._drawdown_tracker.observe_unchanged_provenance(sample)
                )
        except ValueError as exc:
            self._drawdown_tracker.restore(tracker_checkpoint)
            self._performance_telemetry.update(
                {
                    "status": "invalid_drawdown_series",
                    "code": "invalid_drawdown_series",
                    "detail": str(exc),
                }
            )
            return
        if drawdown_metrics is None:
            self._performance_telemetry.update(
                {
                    "status": "unchanged",
                    "code": "performance_sample_unchanged",
                    "as_of": sample.as_of,
                }
            )
            return

        mark_refs = [mark.to_dict() for mark in sample.marks]
        metadata = {
            "runtime_package": "paper_execution_runtime",
            "runtime_binding_id": ledger.get("binding_id"),
            "queue_depth": self._safe_queue_depth(),
            "is_real_order": False,
            "is_real_capital": False,
            "capital_scale_pct": 0,
            "valuation_method": "fill_cash_ledger_mark_to_market",
            "valuation_as_of": sample.as_of,
            "mark_refs": mark_refs,
        }
        common_metrics = {
            "portfolio_value": sample.portfolio_value,
            "initial_cash": sample.initial_cash,
            "cash": sample.cash,
            "fill_count": sample.fill_count,
            "valuation_mark_count": len(sample.marks),
            **self._performance_snapshot_metrics(),
        }
        pair_id = str(uuid.uuid4())
        staged_at = _iso_now()
        pnl_metadata = {
            **metadata,
            "performance_pair_id": pair_id,
            "performance_pair_leg": "pnl_snapshot",
        }
        drawdown_metadata = {
            **metadata,
            "performance_pair_id": pair_id,
            "performance_pair_leg": "drawdown_snapshot",
        }
        pnl_payload = self._build_performance_event_payload(
            "pnl_snapshot",
            {
                "pnl": sample.pnl,
                **common_metrics,
                "pnl_as_of": sample.as_of,
            },
            pnl_metadata,
            event_id=str(uuid.uuid4()),
            created_at=staged_at,
        )
        drawdown_payload = self._build_performance_event_payload(
            "drawdown_snapshot",
            {
                "drawdown_pct": drawdown_metrics["drawdown_pct"],
                **common_metrics,
                **{
                    key: value
                    for key, value in drawdown_metrics.items()
                    if key != "drawdown_pct"
                },
            },
            drawdown_metadata,
            event_id=str(uuid.uuid4()),
            created_at=staged_at,
        )
        if pnl_payload is None or drawdown_payload is None:
            self._drawdown_tracker.restore(tracker_checkpoint)
            self._performance_telemetry.update(
                {
                    "status": "emit_failed",
                    "code": "performance_snapshot_build_failed",
                    "detail": getattr(self._telemetry, "snapshot", lambda: {})().get(
                        "last_error"
                    ),
                }
            )
            return
        pair = {
            "schema_version": "paper_performance_pair.v1",
            "pair_id": pair_id,
            "binding_id": str(ledger.get("binding_id") or ""),
            "valuation_as_of": sample.as_of,
            "staged_at": staged_at,
            "next_window": self._drawdown_tracker.export_state(),
            "events": {
                "pnl_snapshot": {"payload": pnl_payload, "acked": False},
                "drawdown_snapshot": {"payload": drawdown_payload, "acked": False},
            },
        }
        if not self._algo.stage_performance_pair(pair):
            self._drawdown_tracker.restore(tracker_checkpoint)
            self._performance_telemetry.update(
                {
                    "status": "state_persist_failed",
                    "code": "performance_pair_stage_failed",
                    "detail": self._algo.performance_ledger().get("state_error"),
                }
            )
            return
        # The staged next window is not committed until both immutable event
        # legs have been acknowledged. A crash resumes from the pending pair.
        self._drawdown_tracker.restore(tracker_checkpoint)
        self._flush_pending_performance_pair()

    def _build_performance_event_payload(
        self,
        event_type: str,
        metrics: dict[str, Any],
        metadata: dict[str, Any],
        *,
        event_id: str,
        created_at: str,
    ) -> dict[str, Any] | None:
        builder = getattr(self._telemetry, "build_event", None)
        if callable(builder):
            return builder(
                event_type,
                metrics,
                metadata,
                event_id=event_id,
                created_at=created_at,
            )
        return None

    def _emit_staged_performance_payload(self, payload: Mapping[str, Any]) -> bool:
        sender = getattr(self._telemetry, "emit_payload", None)
        if callable(sender):
            return bool(sender(payload))
        return False

    def _flush_pending_performance_pair(self) -> bool:
        pending = self._algo.pending_performance_pair()
        if pending is None:
            return True
        pair_id = str(pending["pair_id"])
        events = pending["events"]
        for event_type in ("pnl_snapshot", "drawdown_snapshot"):
            leg = events[event_type]
            if leg["acked"]:
                continue
            if not self._emit_staged_performance_payload(leg["payload"]):
                self._performance_telemetry = {
                    "status": "emit_failed",
                    "code": "performance_snapshot_emit_failed",
                    "attempted_at": _iso_now(),
                    "pair_id": pair_id,
                    "failed_leg": event_type,
                    "as_of": pending["valuation_as_of"],
                    "pnl_snapshot_sent": bool(events["pnl_snapshot"]["acked"]),
                    "drawdown_snapshot_sent": bool(
                        events["drawdown_snapshot"]["acked"]
                    ),
                }
                return False
            if not self._algo.ack_performance_pair_leg(pair_id, event_type):
                self._performance_telemetry = {
                    "status": "state_persist_failed",
                    "code": "performance_pair_ack_persist_failed",
                    "attempted_at": _iso_now(),
                    "pair_id": pair_id,
                    "failed_leg": event_type,
                    "detail": self._algo.performance_ledger().get("state_error"),
                }
                return False
            # Refresh the durable ack state before deciding whether to send the
            # next leg. This makes a process crash resume at the exact boundary.
            pending = self._algo.pending_performance_pair()
            assert pending is not None
            events = pending["events"]

        next_window = pending["next_window"]
        pnl = float(events["pnl_snapshot"]["payload"]["metrics"]["pnl"])
        drawdown = float(
            events["drawdown_snapshot"]["payload"]["metrics"]["drawdown_pct"]
        )
        if not self._algo.finalize_performance_pair(pair_id):
            self._performance_telemetry = {
                "status": "state_persist_failed",
                "code": "performance_pair_finalize_failed",
                "attempted_at": _iso_now(),
                "pair_id": pair_id,
                "detail": self._algo.performance_ledger().get("state_error"),
            }
            return False
        try:
            self._drawdown_tracker.restore(next_window)
        except ValueError as exc:
            self._performance_state_restore_error = f"{type(exc).__name__}: {exc}"
            self._performance_telemetry = {
                "status": "invalid_drawdown_series",
                "code": "performance_window_restore_failed",
                "attempted_at": _iso_now(),
                "pair_id": pair_id,
                "detail": self._performance_state_restore_error,
            }
            return False
        self._performance_telemetry = {
            "status": "emitted",
            "code": "performance_snapshots_emitted",
            "attempted_at": _iso_now(),
            "pair_id": pair_id,
            "as_of": pending["valuation_as_of"],
            "pnl": pnl,
            "drawdown_pct": drawdown,
            "pnl_snapshot_sent": True,
            "drawdown_snapshot_sent": True,
        }
        return True

    # Backward-compatible private hook retained for narrow callers/tests.  It
    # now enforces the paired, fail-closed performance contract.
    def _maybe_emit_pnl_snapshot(self) -> None:
        self._maybe_emit_performance_snapshots()

    def _performance_snapshot_metrics(self) -> dict[str, Any]:
        processed = int(self._processed_signal_count)
        fill_rate = (float(self._fill_event_count) / processed) if processed > 0 else 0.0
        return {
            "processed_signal_count": processed,
            "execution_event_count": int(self._execution_event_count),
            "fill_event_count": int(self._fill_event_count),
            "fill_rate": round(fill_rate, 6),
            "open_position_count": len(self._algo.positions()),
            "open_bracket_order_count": len(self._algo.open_bracket_orders()),
            "avg_slippage_bps": 0.0,
        }

    def _emit_deploy_started(self) -> None:
        if self._telemetry.enabled:
            self._telemetry.emit_deploy_started()

    def _emit_deploy_completed(self) -> None:
        if self._telemetry.enabled:
            self._telemetry.emit_deploy_completed()

    def _current_queue_lag_ms(self) -> float | None:
        """0 when idle, else age of the oldest pending signal from max(enqueued_at, runtime start)."""
        depth = self._safe_queue_depth()
        if depth is None:
            return None
        if depth <= 0:
            return 0.0
        try:
            raw_ts = self._store.oldest_pending_enqueued_at()
        except Exception:  # noqa: BLE001
            return None
        enq_dt = _parse_iso_utc(raw_ts) if isinstance(raw_ts, str) else None
        if enq_dt is None:
            return None
        wait_start = max(enq_dt, self._started_at_dt)
        lag = (datetime.now(timezone.utc) - wait_start).total_seconds() * 1000.0
        return round(lag, 3) if lag >= 0 else None

    def _safe_queue_depth(self) -> int | None:
        try:
            return int(self._store.queue_depth())
        except Exception:  # noqa: BLE001
            return None

    def _current_capital_pool_id(self) -> str | None:
        binding_snapshot = self._binding_resolver.snapshot()
        if not binding_snapshot.get("capital_pool_id"):
            try:
                self._binding_resolver.resolve()
                binding_snapshot = self._binding_resolver.snapshot()
            except Exception:  # noqa: BLE001
                pass
        for value in (
            binding_snapshot.get("capital_pool_id"),
            self._identity.capital_pool_id,
            self._runtime_context.capital.capital_pool_id if self._runtime_context else None,
        ):
            text = str(value or "").strip()
            if text:
                return text
        return None


_SERVICE: PaperRuntimeService | None = None


def get_service(
    runtime_context: PantheonRuntimeContext | None = None,
    capital_pool_reader: Callable[[str], Mapping[str, Any] | None] | None = None,
    final_settlement_source: Any = None,
) -> PaperRuntimeService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = PaperRuntimeService(
            runtime_context=runtime_context,
            capital_pool_reader=capital_pool_reader,
            final_settlement_source=final_settlement_source,
        )
    return _SERVICE


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _write_json(self, status_code: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _runtime_health_response(self) -> tuple[int, dict[str, Any]]:
        snapshot = get_service().snapshot()
        ready = snapshot.get("status") == "ok"
        parsed_path = urlparse(self.path).path
        body = {
            **snapshot,
            "live": True,
            "ready": ready,
            "health_contract": {
                "healthz": "/healthz",
                "livez": "/livez",
                "readyz": "/readyz",
                "legacy": ["/health", "/__health__"],
            },
        }
        if parsed_path in {"/healthz", "/livez"}:
            return 200, body
        return (200 if ready else 503), body

    def _requested_pool_id(self) -> str | None:
        query = parse_qs(urlparse(self.path).query)
        for key in ("capital_pool_id", "pool_id"):
            values = query.get(key) or []
            if values and str(values[0]).strip():
                return str(values[0]).strip()
        return None

    def _pool_guard(self, service: PaperRuntimeService) -> dict[str, Any] | None:
        return service.pool_access_violation(self._requested_pool_id())

    def do_GET(self) -> None:  # noqa: N802
        parsed_path = urlparse(self.path).path
        if parsed_path in {"/", "/__health__", "/health", "/healthz", "/livez", "/readyz"}:
            status_code, body = self._runtime_health_response()
            self._write_json(status_code, body)
            return
        if parsed_path == "/api/runtime/state":
            service = get_service()
            violation = self._pool_guard(service)
            if violation:
                self._write_json(403, violation)
                return
            self._write_json(200, service.snapshot())
            return
        if parsed_path == "/api/runtime/orders":
            service = get_service()
            violation = self._pool_guard(service)
            if violation:
                self._write_json(403, violation)
                return
            snapshot = service.snapshot()
            self._write_json(200, {"orders": snapshot["paper_state"]["recent_order_events"]})
            return
        self._write_json(404, {"status": "not_found", "path": parsed_path})

    def do_POST(self) -> None:  # noqa: N802
        parsed_path = urlparse(self.path).path
        if parsed_path == "/api/runtime/drain":
            service = get_service()
            violation = self._pool_guard(service)
            if violation:
                self._write_json(403, violation)
                return
            self._write_json(200, service.drain_once())
            return
        self._write_json(404, {"status": "not_found", "path": parsed_path})


def main(
    runtime_context: PantheonRuntimeContext | None = None,
    capital_pool_reader: Callable[[str], Mapping[str, Any] | None] | None = None,
    final_settlement_source: Any = None,
) -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    if capital_pool_reader is None:
        # The reconciler launches this module directly as the worker script, so
        # the order gate's pool-policy reader is resolved here as well as in
        # runtime_bootstrap. ``None`` (unconfigured) keeps the legacy path.
        from services.execution.lean_runtime.expression_inputs import (
            capital_pool_reader_from_env,
        )

        capital_pool_reader = capital_pool_reader_from_env()
    if final_settlement_source is None:
        from services.execution.lean_runtime.futures_session_inputs import (
            final_settlement_source_from_env,
        )

        final_settlement_source = final_settlement_source_from_env()
    service = get_service(
        runtime_context=runtime_context,
        capital_pool_reader=capital_pool_reader,
        final_settlement_source=final_settlement_source,
    )
    service.start()
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8010"))
    server = ThreadingHTTPServer((host, port), _Handler)
    print(
        json.dumps(
            {
                "message": "paper execution runtime ready",
                "role": service.snapshot().get("runtime_role"),
                "runtime_package": "paper_execution_runtime",
                "port": port,
                "stub_mode": False,
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        service.stop()


if __name__ == "__main__":
    main()
