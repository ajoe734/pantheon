"""Paper signal producer.

Fills the gap where active paper ``RuntimeBinding``s have a healthy execution
runtime (the paper ``SignalConsumer`` drain loop) but **no upstream signal
source**, so the loop never closes.

Discovers eligible paper bindings from the runtime-manager and uses a
first-class ``DecisionSignalProducer`` to validate and enqueue identity-complete
signals onto their binding-scoped Redis queues.

Fail-closed by construction: this only ever enqueues *paper* signals that the
runtime matches with simulated fills (``submitted_to_broker=false``). It never
enables live broker connect or order placement; those remain gated by the
runtime activation guard.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from services.execution.artifact_loader import (
    ArtifactLoadError,
    ArtifactLoader,
    ExecutionMode,
)
from services.execution.ibkr_adapter import normalize_us_symbol
from services.execution.lean_runtime.expression_inputs import (
    ExpressionInputsUnavailable,
    build_production_expression_inputs,
)
from services.execution.lean_runtime.symbol_parser import (
    SymbolParseError,
    _CRYPTO_QUOTES,
    _TAIWAN_VENUE_SUFFIXES,
    is_executable_symbol,
    is_taiwan_venue_symbol,
    validate_executable_symbol,
)
from services.execution.market_snapshot_admission import (
    SnapshotAdmissionDecision,
    admit_market_snapshot,
)
from services.execution.shioaji_adapter import normalize_taiwan_symbol

from services.worker_health import healthcheck as check_worker_health
from services.worker_health import write_health
from services.trade_journey.correlation_envelope import (
    CorrelationEnvelopeError,
    mint_trade_envelope,
    validate_envelope,
)

log = logging.getLogger(__name__)

SIGNAL_SCHEMA_VERSION = "1.0"
_WORKER_NAME = "paper-signal-producer"


@dataclass(frozen=True)
class BindingRef:
    """The minimal binding context a producer needs to emit a signal."""

    binding_id: str
    strategy_id: str
    symbol: str = "AAPL.US"
    tenant_id: str = "default"
    environment: str = "paper"
    market: str | None = None
    venue: str | None = None
    market_code: str | None = None
    exchange: str | None = None
    country: str | None = None
    metadata: Mapping[str, Any] | None = None
    runtime_id: str | None = None


# A strategy turns a binding + wall-clock into zero or more signal payloads.
Strategy = Callable[[BindingRef, str], list[dict[str, Any]]]


def build_smoke_signal(
    binding: BindingRef,
    now_iso: str,
    *,
    action: str = "BUY",
    direction: str = "LONG",
    quantity: float = 7,
    quantity_type: str = "SHARES",
    seq: int = 0,
) -> dict[str, Any]:
    """Build one schema-valid signal payload for *binding*.

    The shape satisfies both ``validate_signal_payload_minimal`` (store side)
    and the canonical ``services/research/schema.json`` consumer validation.
    """
    signal_id = f"sig-{binding.binding_id}-{now_iso}-{seq}"
    run_id = f"run-{binding.binding_id}-{now_iso}-{seq}"
    envelope = mint_trade_envelope(
        {
            "tenant_id": binding.tenant_id,
            "environment": binding.environment,
        },
        producer="execution.paper-signal-producer",
        event_id=f"signal:{signal_id}",
        now=now_iso,
    )
    signal = {
        "version": SIGNAL_SCHEMA_VERSION,
        "signal_id": signal_id,
        "strategy_id": binding.strategy_id,
        "timestamp": now_iso,
        "symbol": binding.symbol,
        "action": action,
        "direction": direction,
        "quantity": quantity,
        "quantity_type": quantity_type,
        "binding_id": binding.binding_id,
        "run_id": run_id,
        "tenant_id": binding.tenant_id,
        "environment": binding.environment,
        "journey_id": envelope["journey_id"],
        "correlation_envelope": envelope,
        "metadata": {
            "tenant_id": binding.tenant_id,
            "environment": binding.environment,
            "journey_id": envelope["journey_id"],
            "is_real_order": False,
            "is_real_capital": False,
        },
    }
    if binding.runtime_id:
        signal["runtime_id"] = binding.runtime_id
    return signal


class SmokeStrategy:
    """Minimal deterministic strategy: one BUY signal per tick.

    Useful for proving a paper binding closes the loop and for soak/smoke runs.
    Not an alpha source — real strategies replace this callable.
    """

    def __init__(self, *, quantity: float = 7) -> None:
        self._quantity = quantity
        self._seq = 0

    def __call__(self, binding: BindingRef, now_iso: str) -> list[dict[str, Any]]:
        self._seq += 1
        return [build_smoke_signal(binding, now_iso, quantity=self._quantity, seq=self._seq)]


class BoundedPaperStrategy:
    """Explicit smoke/profile-only bounded paper strategy.

    Generates identity-complete decision dictionary carrying tenant, persona,
    binding, runtime, and pool identities, asserting simulated order constraints.
    The deployable runner never selects this strategy unless the operator sets
    ``PAPER_SIGNAL_STRATEGY=smoke``.
    """

    def __init__(self, *, quantity: float = 7, symbol: str = "AAPL.US") -> None:
        self._quantity = quantity
        self._symbol = symbol
        self._seq = 0

    def __call__(self, binding: dict[str, Any] | BindingRef, now_iso: str) -> dict[str, Any]:
        self._seq += 1
        b_dict = _binding_dict_from_obj(binding)
        binding_id = b_dict["binding_id"]
        runtime_id = b_dict["runtime_id"]
        capital_pool_id = b_dict["capital_pool_id"]
        artifact_id = b_dict["artifact_id"]
        artifact_version = b_dict["artifact_version"]
        pcb_id = b_dict.get("persona_capital_binding_id")

        # Extract persona_id from pcb_id (e.g. pcb-smoke-001 -> smoke)
        persona_id = "default-persona"
        if pcb_id and pcb_id.startswith("pcb-"):
            parts = pcb_id.split("-")
            if len(parts) >= 2:
                persona_id = parts[1]

        binding_metadata = b_dict.get("metadata") if isinstance(b_dict.get("metadata"), Mapping) else {}
        tenant_id = (
            b_dict.get("tenant_id")
            or binding_metadata.get("tenant_id")
            or os.getenv("PANTHEON_TENANT_ID", "default")
        )
        environment = str(
            b_dict.get("environment")
            or b_dict.get("deployment_stage")
            or b_dict.get("deployment_mode")
            or binding_metadata.get("environment")
            or "paper"
        ).strip().lower()
        strategy_id = (
            binding_metadata.get("strategy_id")
            or b_dict.get("strategy_id")
            or "smoke-strategy"
        )
        symbol = b_dict.get("symbol") or self._symbol

        decision = {
            "decision_id": f"dec-{binding_id}-{now_iso}-{self._seq}",
            "strategy_id": strategy_id,
            "timestamp": now_iso,
            "symbol": symbol,
            "action": "BUY",
            "direction": "LONG",
            "quantity": self._quantity,
            "quantity_type": "SHARES",
            "binding_id": binding_id,
            "runtime_id": runtime_id,
            "capital_pool_id": capital_pool_id,
            "run_id": f"run-{binding_id}-{now_iso}-{self._seq}",
            "tenant_id": tenant_id,
            "environment": environment,
            "source_worker": "paper-signal-producer",
            "metadata": {
                "tenant_id": tenant_id,
                "environment": environment,
                "persona_id": persona_id,
                "persona_capital_binding_id": pcb_id,
                "capital_pool_id": capital_pool_id,
                "artifact_id": artifact_id,
                "artifact_version": artifact_version,
                "is_real_order": False,
                "is_real_capital": False,
            }
        }

        # Check if binding carries a pinned strategy_artifact or Object Store reference
        strategy_artifact = binding_metadata.get("strategy_artifact") or b_dict.get("strategy_artifact")
        object_store = binding_metadata.get("object_store") or b_dict.get("object_store")

        if not strategy_artifact and object_store and artifact_id and artifact_version:
            try:
                from services.execution.artifact_loader import ArtifactLoader, ExecutionMode
                loader = ArtifactLoader.from_runtime(object_store)
                loaded = loader.load(
                    strategy_id=artifact_id,
                    version=artifact_version,
                    execution_mode=ExecutionMode.PAPER,
                )
                if loaded and loaded.metadata:
                    strategy_artifact = loaded.metadata.get("strategy_artifact") or loaded.metadata
            except Exception as exc:
                log.warning("ArtifactLoader failed to load artifact for binding %s: %s", binding_id, exc)

        if strategy_artifact and isinstance(strategy_artifact, Mapping):
            try:
                from services.registry.strategy_artifact import evaluate_strategy_action
                closes = binding_metadata.get("recent_closes") or b_dict.get("recent_closes")
                features = binding_metadata.get("features") or b_dict.get("features")
                if closes and isinstance(closes, Sequence) and len(closes) >= 2:
                    action = evaluate_strategy_action(
                        strategy_artifact,
                        closes,
                        features=features if isinstance(features, Mapping) else None,
                        decision_time=now_iso,
                    )
                    decision["action"] = action
                    if action == "SELL":
                        decision["direction"] = "SHORT"
            except Exception as exc:
                log.warning("Failed to evaluate strategy_artifact for binding %s: %s", binding_id, exc)

        return decision


EXPRESSION_LAYER_ENV = "PANTHEON_PAPER_EXPRESSION_LAYER"
SSF_NORMALIZED_ROOT_ENV = "PANTHEON_TAIFEX_SSF_NORMALIZED_ROOT"


def expression_layer_enabled() -> bool:
    """Global switch for layer C; anything but an explicit on-value is off."""
    return os.getenv(EXPRESSION_LAYER_ENV, "off").strip().lower() in {"on", "1", "true", "yes"}


def apply_expression_record(
    decision: dict[str, Any],
    view: Mapping[str, Any],
    stored: Mapping[str, Any],
) -> dict[str, Any]:
    """Carry a persisted Chosen/NoTrade record in the decision envelope.

    ``stored`` is ``{"record", "kind", "multiplier"}`` as persisted before any
    order.  The view fields always ride on the decision metadata.  A Chosen
    record rewrites action/direction/quantity to the chosen instrument and
    units; a NoTrade becomes a HOLD so the runtime publishes it as a noop and
    consumes the bar.
    """
    from services.execution.lean_runtime.expression_policy import is_no_trade
    from services.execution.lean_runtime.futures_session_inputs import (
        attach_futures_session_inputs,
    )
    from services.execution.lean_runtime.instrument_candidates import (
        parse_instrument_id,
    )

    record = dict(stored["record"])
    metadata = decision.setdefault("metadata", {})
    metadata["strength"] = view.get("strength")
    metadata["momentum"] = view.get("momentum")
    metadata["view_direction"] = view.get("direction")
    metadata["decision_id"] = record.get("decision_id") or decision.get("decision_id")
    metadata["expression_decision"] = record
    decision_day = str(decision.get("timestamp") or "")[:10]
    if decision_day:
        metadata.setdefault("trade_date", decision_day)
        metadata.setdefault("as_of_trading_day", decision_day)
    candidates_list: list[dict[str, Any]] = []
    book_before: Mapping[str, Any] | None = None
    replay_input = stored.get("replay_input")
    if isinstance(replay_input, Mapping):
        metadata["expression_replay_input"] = dict(replay_input)
        raw_cands = replay_input.get("candidates")
        if isinstance(raw_cands, Sequence) and not isinstance(raw_cands, (str, bytes)):
            candidates_list = [dict(c) for c in raw_cands if isinstance(c, Mapping)]
        if isinstance(replay_input.get("book_before"), Mapping):
            book_before = replay_input["book_before"]
    metadata["reason_code"] = record.get("reason_code")
    attach_futures_session_inputs(
        decision,
        book_before,
        trade_date=metadata.get("trade_date") or decision_day,
    )
    if is_no_trade(record):
        decision["action"] = "HOLD"
        return decision
    side = str(record["side"])
    closing = record.get("reason_code") == "close_leg"
    decision["action"] = "BUY" if side == "buy" else "SELL"
    # A closing leg takes the direction of the position it closes.
    decision["direction"] = "LONG" if (side == "buy") != closing else "SHORT"
    decision["quantity"] = int(record["units"])
    decision["quantity_type"] = "SHARES"
    inst_id = str(record["instrument_id"])
    metadata["instrument_id"] = inst_id
    chosen_candidate = next(
        (c for c in candidates_list if c.get("instrument_id") == inst_id),
        {},
    )
    effective_kind = stored.get("kind") or chosen_candidate.get("kind")
    if effective_kind is None and inst_id.startswith("ssf:"):
        effective_kind = "stock_future"
    metadata["kind"] = effective_kind
    metadata["units"] = int(record["units"])
    metadata["multiplier"] = (
        stored.get("multiplier")
        or chosen_candidate.get("multiplier")
        or 1
    )
    if candidates_list:
        admitted = list(candidates_list)
        if closing and not any(c.get("instrument_id") == inst_id for c in admitted):
            admitted.append(
                {
                    "instrument_id": inst_id,
                    "kind": effective_kind,
                    "multiplier": metadata["multiplier"],
                }
            )
        metadata["candidates"] = admitted
    if effective_kind == "stock_future" or inst_id.startswith("ssf:"):
        parsed_inst: dict[str, Any] = {}
        if inst_id.startswith("ssf:"):
            try:
                parsed_inst = parse_instrument_id(inst_id)
            except ValueError:
                parsed_inst = {}
        contract_val = chosen_candidate.get("contract") or parsed_inst.get("contract")
        month_val = chosen_candidate.get("contract_month") or parsed_inst.get("contract_month")
        if contract_val is not None:
            metadata["contract"] = contract_val
        if month_val is not None:
            metadata["contract_month"] = month_val
        if chosen_candidate.get("price") is not None:
            metadata["futures_settlement_price"] = chosen_candidate["price"]
        for cand_key in (
            "price_basis",
            "price_date",
            "initial_margin_per_unit",
            "maintenance_margin_per_unit",
            "fee_per_unit_per_side",
            "tax_rate_per_side",
            "expiry",
            "last_trading_day",
        ):
            if chosen_candidate.get(cand_key) is not None:
                metadata[cand_key] = chosen_candidate[cand_key]
    return decision


class SignalDecisionUnavailable(RuntimeError):
    """A binding cannot produce a governed paper decision on this tick."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = str(code)
        self.detail = str(detail)
        super().__init__(f"{self.code}: {self.detail}")


class CurrentArtifactStrategy:
    """Evaluate the exact approved artifact pinned by the current binding.

    This is the normal paper runner strategy.  It has no BUY/SELL fallback: an
    unavailable artifact, identity mismatch, invalid checksum, or absent market
    snapshot yields a degraded binding tick and zero queued signals.
    """

    _SUPPORTED_INTERPRETER = (
        "services.registry.strategy_artifact:evaluate_strategy_action"
    )
    _SUPPORTED_INTERPRETERS: frozenset[str] = frozenset(
        {
            "services.registry.strategy_artifact:evaluate_strategy_action",
            "services.registry.strategy_artifact:evaluate_strategy_view",
            "services.registry.factor_rule:evaluate_factor_rule_action",
            "services.registry.factor_rule:evaluate_factor_rule_view",
        }
    )
    _SUPPORTED_STRATEGY_KINDS: frozenset[str] = frozenset(
        {
            "close_to_close_momentum",
            "factor_rule_v1",
            "persona_portfolio_v1",
        }
    )

    def __init__(
        self,
        *,
        expression_layer: bool | None = None,
        candidates_builder: Callable[..., list[dict[str, Any]]] | None = None,
        decider: Callable[..., Any] | None = None,
        expression_inputs: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    ) -> None:
        self._expression_layer = expression_layer
        self._candidates_builder = candidates_builder
        self._decider = decider
        self._expression_inputs = expression_inputs

    def _expression_on(self) -> bool:
        if self._expression_layer is not None:
            return bool(self._expression_layer)
        return expression_layer_enabled()

    def _expression_step(
        self,
        *,
        view: dict[str, Any],
        decision_id: str,
        symbol: str,
        closes: Sequence[float],
        market_input: Mapping[str, Any],
        strategy_artifact: Mapping[str, Any],
        b_dict: Mapping[str, Any],
        metadata: Mapping[str, Any],
        now_iso: str,
    ) -> dict[str, Any]:
        from services.execution.lean_runtime import expression_policy
        from services.execution.lean_runtime.instrument_candidates import (
            build_candidates,
            normalize_underlying_code,
        )

        underlying = normalize_underlying_code(symbol)
        unavailable: ExpressionInputsUnavailable | None = None
        if self._expression_inputs is not None:
            try:
                inputs = dict(self._expression_inputs(b_dict) or {})
            except ExpressionInputsUnavailable as exc:
                log.warning("expression inputs unavailable for %s: %s", symbol, exc)
                unavailable = exc
                inputs = {}
        else:
            inputs = dict(metadata.get("expression_inputs") or {})
        book = dict(inputs.get("persona_book") or {})
        budget = dict(inputs.get("risk_budget") or {})
        event_time = str(market_input.get("event_time") or "").strip()
        price_date = event_time[:10] or None
        builder = self._candidates_builder or build_candidates
        candidates: list[dict[str, Any]] = []
        try:
            if unavailable is not None:
                raise unavailable
            candidates = builder(
                underlying,
                now_iso[:10],
                book,
                budget,
                cash_price=float(closes[-1]),
                cash_price_date=price_date,
                normalized_root=os.getenv(SSF_NORMALIZED_ROOT_ENV) or None,
                decision_id=decision_id,
            )
        except ExpressionInputsUnavailable:
            candidates = []
        except Exception as exc:  # noqa: BLE001 - candidate failure fails closed to a NoTrade
            log.warning("expression candidates unavailable for %s: %s", symbol, exc)
            candidates = []
        decision_view = {
            "decision_id": decision_id,
            "underlying": underlying,
            "direction": view["direction"],
            "strength": view.get("strength"),
        }
        params = dict(strategy_artifact["expression_policy"].get("parameters") or {})
        if unavailable is not None:
            # The limits or the book could not be read: a real NoTrade naming
            # the missing input, never a misleading gross_limit binding.
            record = expression_policy.budget_unavailable_no_trade(
                decision_view, unavailable.missing, unavailable.detail
            )
        else:
            record = expression_policy.decide_validated(
                decision_view,
                candidates,
                book,
                budget,
                params,
                decider=self._decider or expression_policy.decide,
            )
        if view.get("exit_reason") and record.get("reason_code") != "close_leg":
            # An exit may only reduce an actual held instrument. A stale feed
            # position must never turn removal from a pool into a fresh short.
            record = expression_policy.budget_unavailable_no_trade(
                decision_view, ["held_position"], "candidate pool exit has no validated close leg",
            )
        chosen_candidate = next(
            (c for c in candidates if c.get("instrument_id") == record.get("instrument_id")),
            {},
        )
        return {
            "view": {
                **decision_view,
                "momentum": view.get("momentum"),
                "kind": chosen_candidate.get("kind"),
                "multiplier": chosen_candidate.get("multiplier"),
            },
            "decision": record,
            # Exactly what the policy was given, so expression_replay input can
            # be rebuilt from recorded events (dev plan section 6).
            "replay_input": {
                "decision_id": decision_id,
                "view": dict(decision_view),
                "candidates": [dict(c) for c in candidates],
                "book_before": book,
                "budget": budget,
                "parameters": params,
            },
        }

    @staticmethod
    def _expression_base_view(view: Mapping[str, Any], decision_id: str, symbol: str) -> dict[str, Any]:
        from services.execution.lean_runtime.instrument_candidates import normalize_underlying_code

        return {
            "decision_id": decision_id,
            "underlying": normalize_underlying_code(symbol),
            "direction": view["direction"],
            "strength": view.get("strength"),
            "momentum": view.get("momentum"),
        }

    def __call__(self, binding: Any, now_iso: str, *, _portfolio_symbol: str | None = None,
                 _screen_view: Mapping[str, Any] | None = None) -> dict[str, Any]:
        b_dict = _binding_dict_from_obj(binding)
        binding_id = _required_binding_text(b_dict, "binding_id")
        metadata = (
            b_dict.get("metadata")
            if isinstance(b_dict.get("metadata"), Mapping)
            else {}
        )
        artifact_id = _required_binding_text(b_dict, "artifact_id")
        artifact_version = _required_binding_text(b_dict, "artifact_version")
        strategy_id = str(
            b_dict.get("strategy_id") or metadata.get("strategy_id") or ""
        ).strip()
        if not strategy_id:
            raise SignalDecisionUnavailable(
                "artifact_identity_missing",
                f"binding {binding_id} has no strategy_id for artifact projection",
            )

        object_store = b_dict.get("object_store") or metadata.get("object_store")
        if object_store is None:
            raise SignalDecisionUnavailable(
                "artifact_store_missing",
                f"binding {binding_id} has no Object Store artifact projection",
            )

        expected_checksum = b_dict.get("artifact_checksum")
        if expected_checksum is None:
            expected_checksum = metadata.get("artifact_checksum")
        try:
            loaded = ArtifactLoader.from_runtime(object_store).load_exact(
                registry_id=artifact_id,
                strategy_id=strategy_id,
                version=artifact_version,
                execution_mode=ExecutionMode.PAPER,
                expected_checksum=expected_checksum,
            )
            payload = loaded.json_payload()
        except ArtifactLoadError as exc:
            raise SignalDecisionUnavailable(
                "artifact_unavailable",
                f"binding {binding_id} cannot load its approved artifact: {exc}",
            ) from exc

        strategy_artifact = payload.get("strategy_artifact", payload)
        if not isinstance(strategy_artifact, Mapping):
            raise SignalDecisionUnavailable(
                "artifact_payload_invalid",
                f"binding {binding_id} artifact payload has no StrategyArtifact object",
            )
        self._validate_artifact_identity(
            strategy_artifact,
            binding_id=binding_id,
            artifact_id=artifact_id,
            artifact_version=artifact_version,
            strategy_id=strategy_id,
        )

        algorithm_ref = strategy_artifact.get("algorithm_ref")
        interpreter = (
            str(algorithm_ref.get("logic_interpreter") or "").strip()
            if isinstance(algorithm_ref, Mapping)
            else ""
        )
        if interpreter not in self._SUPPORTED_INTERPRETERS:
            raise SignalDecisionUnavailable(
                "artifact_interpreter_unsupported",
                f"binding {binding_id} requested unsupported interpreter {interpreter!r}",
            )
        strategy_logic = strategy_artifact.get("strategy_logic")
        logic_kind = (
            str(strategy_logic.get("kind") or "").strip()
            if isinstance(strategy_logic, Mapping)
            else ""
        )
        if logic_kind not in self._SUPPORTED_STRATEGY_KINDS:
            raise SignalDecisionUnavailable(
                "artifact_interpreter_unsupported",
                f"binding {binding_id} requested unsupported strategy_logic kind {logic_kind!r}",
            )

        screened = logic_kind == "factor_rule_v1" and "screen" in strategy_logic
        if screened and _portfolio_symbol is None:
            from services.registry.candidate_pool import (
                MARKET_FEATURE_KEY, _market, canonical_symbol, evaluate_screened_views,
            )

            snapshots = b_dict.get("market_inputs") or metadata.get("market_inputs")
            if not isinstance(snapshots, Mapping) or not snapshots:
                raise SignalDecisionUnavailable("market_input_missing", "screen requires market_inputs for the governed market")
            features = {**(metadata.get("features") or {}), **(b_dict.get("features") or {})}
            try:
                _, market_symbols = _market(features.get(MARKET_FEATURE_KEY))
                keyed = {canonical_symbol(s): s for s in snapshots}
                if set(keyed) != set(market_symbols):
                    raise ValueError("screen market_inputs must cover the complete governed market")
                stamps = {snapshots[s].get("event_time") for s in snapshots}
                if len(stamps) != 1 or not next(iter(stamps)):
                    raise ValueError("screen market_inputs require one common decision bar")
                positions = {s: snapshots[keyed[s]] for s in keyed}
                views = evaluate_screened_views(
                    strategy_artifact, features[MARKET_FEATURE_KEY], next(iter(stamps)), positions=positions,
                )
            except (ValueError, TypeError) as exc:
                raise SignalDecisionUnavailable("market_input_invalid", str(exc)) from exc
            decisions = []
            for symbol in sorted(keyed):
                member_binding = {**b_dict, "symbol": symbol, "features": features,
                                  "market_input": {**snapshots[keyed[symbol]], "symbol": symbol}}
                decisions.append(self(member_binding, now_iso, _portfolio_symbol=symbol, _screen_view=views[symbol]))
            return {"portfolio_decisions": decisions}

        if logic_kind == "persona_portfolio_v1":
            from services.registry.strategy_artifact import (
                StrategyArtifactValidationError, validate_strategy_artifact,
            )

            try:
                validate_strategy_artifact(strategy_artifact)
            except (StrategyArtifactValidationError, ValueError, TypeError) as exc:
                raise SignalDecisionUnavailable("artifact_payload_invalid", str(exc)) from exc
            symbols = strategy_artifact["parameters"]["symbols"]
            if len(symbols) > 1 and _portfolio_symbol is None:
                decisions = []
                snapshots = b_dict.get("market_inputs") or metadata.get("market_inputs")
                for underlying in symbols:
                    member_binding = {**b_dict, "symbol": underlying}
                    if isinstance(snapshots, Mapping):
                        if underlying not in snapshots:
                            raise SignalDecisionUnavailable(
                                "market_input_missing", f"portfolio snapshot missing for {underlying}"
                            )
                        member_binding["market_input"] = snapshots[underlying]
                    decisions.append(self(member_binding, now_iso, _portfolio_symbol=underlying))
                return {"portfolio_decisions": decisions}

        market_input = _market_input_for_binding(
            b_dict, metadata, strategy_artifact=strategy_artifact, now_iso=now_iso
        )
        if logic_kind == "persona_portfolio_v1" and (
            not market_input.get("event_time") or not market_input.get("source_ref")
        ):
            raise SignalDecisionUnavailable(
                "market_input_missing", "portfolio virtual marks require event_time and source_ref"
            )
        closes = market_input["closes"]
        features = market_input.get("features")
        decision_time = (
            market_input.get("event_time")
            or market_input.get("observed_at")
            or now_iso
        )
        holding_bars = market_input.get("holding_bars")
        current_direction = market_input.get("current_direction")
        try:
            from services.registry.strategy_artifact import (
                StrategyArtifactValidationError,
                evaluate_strategy_action,
                validate_strategy_artifact,
            )

            validate_strategy_artifact(strategy_artifact)
        except (StrategyArtifactValidationError, ValueError, TypeError) as exc:
            raise SignalDecisionUnavailable(
                "artifact_payload_invalid",
                f"binding {binding_id} StrategyArtifact validation failed: {exc}",
            ) from exc
        try:
            from services.registry.factor_rule import (
                FactorRuleValidationError,
                assert_governed_feature_inputs,
                assert_governed_feature_observations,
            )

            assert_governed_feature_inputs(strategy_logic)
            if not screened:
                assert_governed_feature_observations(strategy_logic, features, decision_time)
        except FactorRuleValidationError as exc:
            raise SignalDecisionUnavailable(
                "feature_not_in_catalogue",
                f"binding {binding_id} factor rule input rejected: {exc}",
            ) from exc
        try:
            expression_enabled = (
                self._expression_on()
                and strategy_artifact.get("expression_policy") is not None
            )
            portfolio_result = None
            if logic_kind == "persona_portfolio_v1":
                from services.registry.portfolio_envelope import evaluate_portfolio_envelope

                portfolio_result = evaluate_portfolio_envelope(
                    strategy_artifact, closes, features=features, decision_time=decision_time,
                    symbol=market_input["symbol"],
                    holding_bars=holding_bars, current_direction=current_direction,
                )
                view = portfolio_result["view"]
                action = view["action"]
            elif screened and _screen_view is not None:
                view = dict(_screen_view)
                action = view["action"]
            elif expression_enabled or screened:
                from services.registry.strategy_artifact import evaluate_strategy_view

                view = evaluate_strategy_view(
                    strategy_artifact,
                    closes,
                    features=features,
                    decision_time=decision_time,
                    holding_bars=holding_bars,
                    current_direction=current_direction,
                    symbol=market_input["symbol"],
                )
                action = view["action"]
            else:
                view = None
                action = evaluate_strategy_action(
                    strategy_artifact,
                    closes,
                    features=features,
                    decision_time=decision_time,
                    holding_bars=holding_bars,
                    current_direction=current_direction,
                )
        except (StrategyArtifactValidationError, ValueError, TypeError) as exc:
            raise SignalDecisionUnavailable(
                "market_input_invalid",
                f"binding {binding_id} market input cannot be evaluated: {exc}",
            ) from exc

        parameters = strategy_artifact["parameters"]
        quantity = parameters.get("order_quantity")
        quantity_type = str(parameters.get("quantity_type") or "").strip().upper()
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or quantity <= 0:
            raise SignalDecisionUnavailable(
                "artifact_payload_invalid",
                f"binding {binding_id} artifact requires positive numeric order_quantity",
            )
        if not quantity_type:
            raise SignalDecisionUnavailable(
                "artifact_payload_invalid",
                f"binding {binding_id} artifact requires quantity_type",
            )

        symbol = _strategy_symbol(b_dict, strategy_artifact, market_input)
        tenant_id = str(
            b_dict.get("tenant_id")
            or metadata.get("tenant_id")
            or os.getenv("PANTHEON_TENANT_ID", "default")
        ).strip()
        environment = str(
            b_dict.get("environment")
            or b_dict.get("deployment_stage")
            or b_dict.get("deployment_mode")
            or metadata.get("environment")
            or "paper"
        ).strip().lower()
        if environment != "paper":
            raise SignalDecisionUnavailable(
                "binding_mode_invalid",
                f"binding {binding_id} is {environment!r}, expected 'paper'",
            )

        persona_capital_binding_id = b_dict.get("persona_capital_binding_id")
        direction = "SHORT" if action == "SELL" else "LONG"
        # The artifact declares its bar cadence, so one admitted bar of one exact
        # model is one decision regardless of how often the poll observes it.
        bar_key = _bar_identity(market_input, closes, parameters.get("bar_frequency"))
        decision_key = {
            "binding_id": binding_id,
            "artifact_registry_id": loaded.metadata["registry_id"],
            "artifact_version": artifact_version,
            "artifact_checksum": loaded.metadata["checksum"],
            "symbol": symbol,
            "bar": bar_key,
        }
        if screened:
            decision_key["candidate_pool_digest"] = view["candidate_pool_digest"]
        decision_digest = hashlib.sha256(
            json.dumps(decision_key, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        decision_id = f"dec-{binding_id}-{decision_digest[:24]}"

        def decision_id_for_bar(bar: str) -> str:
            """Id this binding's artifact gave the decision of an earlier daily bar."""
            digest = hashlib.sha256(
                json.dumps(
                    {**decision_key, "bar": bar}, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
            return f"dec-{binding_id}-{digest[:24]}"

        out = {
            "decision_id": decision_id,
            "signal_id": f"sig-{decision_digest[:32]}",
            "strategy_id": strategy_id,
            "timestamp": now_iso,
            "symbol": symbol,
            "action": action,
            "direction": direction,
            "quantity": quantity,
            "quantity_type": quantity_type,
            "binding_id": binding_id,
            "runtime_id": _required_binding_text(b_dict, "runtime_id"),
            "capital_pool_id": _required_binding_text(b_dict, "capital_pool_id"),
            "run_id": f"run-{binding_id}-{decision_digest[:24]}",
            "tenant_id": tenant_id,
            "environment": environment,
            "source_worker": "paper-signal-producer",
            "metadata": {
                "tenant_id": tenant_id,
                "environment": environment,
                "persona_capital_binding_id": persona_capital_binding_id,
                "capital_pool_id": b_dict["capital_pool_id"],
                "artifact_id": artifact_id,
                "artifact_version": artifact_version,
                "artifact_checksum": loaded.metadata["checksum"],
                "artifact_registry_id": loaded.metadata["registry_id"],
                "artifact_interpreter": interpreter,
                "raw_symbol": str(market_input.get("symbol") or b_dict.get("symbol") or symbol),
                "market_input_ref": market_input.get("source_ref"),
                "market_input_observed_at": market_input.get("observed_at"),
                "market_input_snapshot_id": market_input.get("snapshot_id"),
                "market_input_event_time": market_input.get("event_time"),
                "market_input_bar": bar_key,
                "market_input_lineage": market_input.get("lineage"),
                **(
                    {"market_input_feature_keys": sorted(str(k) for k in features.keys())}
                    if isinstance(features, Mapping) and features
                    else {}
                ),
                "market_data": {
                    "close": closes[-1],
                    "event_time": market_input.get("event_time"),
                    "source_ref": market_input.get("source_ref"),
                },
                "is_real_order": False,
                "is_real_capital": False,
            },
        }
        if portfolio_result is not None:
            out["metadata"]["portfolio_member_views"] = {
                member["member_id"]: {
                    **{key: member[key] for key in (
                        "artifact_id", "version", "checksum", "risk_weight", "virtual_only"
                    )},
                    "view": portfolio_result["member_views"][member["member_id"]],
                    "underlying_count": len(member["artifact"]["parameters"]["symbols"]),
                }
                for member in strategy_logic["members"]
                if member["member_id"] in portfolio_result["member_views"]
            }
            out["metadata"]["portfolio_net_view"] = dict(portfolio_result["view"])
            out["metadata"]["portfolio_bar_frequency"] = parameters["bar_frequency"]
        if screened:
            out["metadata"]["candidate_pool_snapshot"] = view["candidate_pool_snapshot"]
            out["metadata"]["candidate_pool_digest"] = view["candidate_pool_digest"]
            out["metadata"]["candidate_pool_view"] = {
                k: v for k, v in view.items() if k != "candidate_pool_snapshot"
            }
        if expression_enabled and view is not None and is_taiwan_venue_symbol(symbol):
            # Lazy: the producer evaluates only after admission and a stored
            # record lookup, so a restart never re-runs builder or decider.
            out["expression"] = {
                "view": self._expression_base_view(view, decision_id, symbol),
                # Earlier daily bars are addressable by id; other cadences are
                # not graded because their bars are not one session each.
                "grading": (
                    {"closes": list(closes), "decision_id_for_bar": decision_id_for_bar}
                    if not screened and str(parameters.get("bar_frequency") or "").strip().lower()
                    in {"1d", "d", "1day", "daily"}
                    else None
                ),
                "evaluate": lambda: self._expression_step(
                    view=view,
                    decision_id=decision_id,
                    symbol=symbol,
                    closes=closes,
                    market_input=market_input,
                    strategy_artifact=strategy_artifact,
                    b_dict=b_dict,
                    metadata=metadata,
                    now_iso=now_iso,
                ),
            }
        return out

    @staticmethod
    def _validate_artifact_identity(
        artifact: Mapping[str, Any],
        *,
        binding_id: str,
        artifact_id: str,
        artifact_version: str,
        strategy_id: str,
    ) -> None:
        expected = {
            "artifact_id": artifact_id,
            "version": artifact_version,
            "strategy_id": strategy_id,
        }
        for field, expected_value in expected.items():
            actual = str(artifact.get(field) or "").strip()
            if actual != expected_value:
                raise SignalDecisionUnavailable(
                    "artifact_identity_mismatch",
                    f"binding {binding_id} expected {field}={expected_value!r}, got {actual or None!r}",
                )



def _bar_identity(market_input: Mapping[str, Any], closes: Sequence[float], bar_frequency: Any) -> str:
    """Identity of the latest admitted bar, independent of when it was observed.

    Daily artifacts key on the bar's calendar date; other cadences key on the
    exact ``event_time``.  Inline inputs without an ``event_time`` are keyed by
    their close series, so identical observations stay one bar.
    """

    event_time = str(market_input.get("event_time") or "").strip()
    if not event_time:
        series = json.dumps([float(close) for close in closes], separators=(",", ":"))
        return f"closes:{hashlib.sha256(series.encode('utf-8')).hexdigest()[:16]}"
    if str(bar_frequency or "").strip().lower() in {"1d", "d", "1day", "daily"}:
        try:
            return datetime.fromisoformat(event_time.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return event_time
    return event_time


def _required_binding_text(binding: Mapping[str, Any], field: str) -> str:
    value = str(binding.get(field) or "").strip()
    if not value:
        binding_id = str(binding.get("binding_id") or "<unknown>")
        raise SignalDecisionUnavailable(
            "binding_identity_missing",
            f"binding {binding_id} has no {field}",
        )
    return value


_US_MARKET_TOKENS = {
    "US",
    "USA",
    "UNITED STATES",
    "UNITED_STATES",
    "SMART",
    "NASDAQ",
    "NYSE",
    "ARCA",
    "BATS",
    "AMEX",
}

_TW_MARKET_TOKENS = {
    "TW",
    "TWSE",
    "TAIWAN",
    "TPEX",
    "TWO",
    "TSE",
    "OTC",
    "TAIFEX",
}

_CRYPTO_MARKET_TOKENS = {
    "CRYPTO",
    "KRAKEN",
    "BINANCE",
    "COINBASE",
}

_FX_MARKET_TOKENS = {
    "FX",
    "FOREX",
    "OANDA",
}


def _classify_market_token(token: Any) -> str | None:
    """Classify an explicit market token or venue into a canonical market category."""
    if not token or not isinstance(token, str):
        return None
    raw = token.strip().lstrip(".").upper()
    if not raw:
        return None
    if raw in _US_MARKET_TOKENS:
        return "US"
    if raw in _TW_MARKET_TOKENS:
        return "TW"
    if raw in _CRYPTO_MARKET_TOKENS:
        return "CRYPTO"
    if raw in _FX_MARKET_TOKENS:
        return "FX"
    return None


def _intrinsic_symbol_market(symbol: str) -> str | None:
    """Detect intrinsic market from dotted suffix or known crypto pair format."""
    s = str(symbol or "").strip()
    if "." in s:
        suffix = s.rsplit(".", 1)[1]
        classified = _classify_market_token(suffix)
        if classified is not None:
            return classified
        if is_taiwan_venue_symbol(s):
            return "TW"

    upper = s.upper().replace("/", "")
    for quote in _CRYPTO_QUOTES:
        if upper.endswith(quote) and len(upper) > len(quote):
            return "CRYPTO"

    return None


def _extract_explicit_venue(*sources: Mapping[str, Any] | None) -> str | None:
    """Extract an explicit venue code from market contexts if present."""
    for src in sources:
        if not src or not isinstance(src, Mapping):
            continue
        for key in ("venue", "exchange", "market_code"):
            val = src.get(key)
            if val and isinstance(val, str) and val.strip():
                return val.strip().upper()
        meta = src.get("metadata")
        if isinstance(meta, Mapping):
            for key in ("venue", "exchange", "market_code"):
                val = meta.get(key)
                if val and isinstance(val, str) and val.strip():
                    return val.strip().upper()
        params = src.get("parameters")
        if isinstance(params, Mapping):
            for key in ("venue", "exchange", "market_code"):
                val = params.get(key)
                if val and isinstance(val, str) and val.strip():
                    return val.strip().upper()
    return None


def _collect_explicit_market_contexts(
    binding: Mapping[str, Any],
    strategy_artifact: Mapping[str, Any],
    market_input: Mapping[str, Any],
    *,
    binding_id: str = "<unknown>",
) -> tuple[dict[str, list[str]], str | None]:
    """Collect authoritative explicit market fields ('market', 'venue', 'market_code', 'exchange', 'country')."""
    market_origins: dict[str, list[str]] = {}

    def _process_field(val: Any, origin_desc: str) -> None:
        if val is None:
            return
        if isinstance(val, str):
            clean = val.strip()
            if not clean:
                return
            market_cat = _classify_market_token(clean)
            if market_cat is None:
                raise SignalDecisionUnavailable(
                    "market_context_unsupported",
                    f"binding {binding_id} has unsupported explicit market context: {origin_desc}={clean!r}",
                )
            market_origins.setdefault(market_cat, []).append(f"{origin_desc}={clean!r}")
        else:
            raise SignalDecisionUnavailable(
                "market_context_unsupported",
                f"binding {binding_id} has invalid explicit market context: {origin_desc}={val!r}",
            )

    # 1. From binding (top-level and metadata)
    for k in ("market", "venue", "market_code", "exchange", "country"):
        if k in binding:
            _process_field(binding.get(k), f"binding.{k}")

    b_meta = binding.get("metadata")
    if isinstance(b_meta, Mapping):
        for k in ("market", "venue", "market_code", "exchange", "country"):
            if k in b_meta:
                _process_field(b_meta.get(k), f"binding.metadata.{k}")

    # 2. From strategy_artifact (parameters and metadata)
    params = strategy_artifact.get("parameters")
    if isinstance(params, Mapping):
        for k in ("market", "venue", "market_code", "exchange", "country"):
            if k in params:
                _process_field(params.get(k), f"artifact.parameters.{k}")

    art_meta = strategy_artifact.get("metadata")
    if isinstance(art_meta, Mapping):
        for k in ("market", "venue", "market_code", "exchange", "country"):
            if k in art_meta:
                _process_field(art_meta.get(k), f"artifact.metadata.{k}")

    # 3. From market_input (top-level)
    for k in ("market", "venue", "market_code", "exchange", "country"):
        if k in market_input:
            _process_field(market_input.get(k), f"market_input.{k}")

    explicit_venue = _extract_explicit_venue(market_input, binding, params, b_meta, art_meta)
    return market_origins, explicit_venue


def _market_input_for_binding(
    binding: Mapping[str, Any],
    metadata: Mapping[str, Any],
    *,
    strategy_artifact: Mapping[str, Any] | None = None,
    now_iso: str,
) -> dict[str, Any]:
    """Resolve a paper market input without a provider or static-close fallback.

    A RuntimeBinding that asks the worker to fetch market input must name the
    Source-owned ``latest_stored_normalized`` policy.  The only network request
    is then to Source's single read-only snapshot endpoint.  Inline
    ``market_input`` remains an explicit test/compatibility input, but the old
    ``recent_closes`` fallback is intentionally not accepted.
    """

    raw = binding.get("market_input") or metadata.get("market_input")
    policy = _market_input_policy(binding, metadata)
    if raw is None:
        if policy is None:
            binding_id = str(binding.get("binding_id") or "<unknown>")
            raise SignalDecisionUnavailable(
                "market_input_missing",
                f"binding {binding_id} has no RuntimeBinding market_data_policy",
            )
        source_ingest_url = (
            os.getenv("PANTHEON_SOURCE_INGEST_URL")
            or os.getenv("PANTHEON_SOURCE_INGEST_API_URL")
            or ""
        ).rstrip("/")
        symbol = str(binding.get("symbol") or metadata.get("symbol") or "").strip()
        if not symbol and strategy_artifact:
            params = strategy_artifact.get("parameters")
            if isinstance(params, Mapping):
                syms = params.get("symbols")
                if isinstance(syms, Sequence) and not isinstance(syms, (str, bytes)) and syms:
                    symbol = str(syms[0]).strip()
        if not source_ingest_url:
            raise SignalDecisionUnavailable(
                "market_input_unavailable",
                "PANTHEON_SOURCE_INGEST_URL is required by the RuntimeBinding market_data_policy",
            )
        if not symbol:
            raise SignalDecisionUnavailable(
                "market_input_missing",
                f"binding {binding.get('binding_id') or '<unknown>'} has no market symbol",
            )
        try:
            import json
            import urllib.parse
            import urllib.request

            url = (
                f"{source_ingest_url}/api/source-ingest/snapshots/latest"
                f"?symbol={urllib.parse.quote(symbol, safe='')}"
            )
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise SignalDecisionUnavailable(
                "market_input_unavailable",
                f"Source stored snapshot is unavailable for {symbol}: {exc}",
            ) from exc
        if not isinstance(payload, Mapping):
            raise SignalDecisionUnavailable(
                "market_input_invalid",
                f"Source stored snapshot for {symbol} is not an object",
            )
        raw = payload

    if not isinstance(raw, Mapping):
        binding_id = str(binding.get("binding_id") or "<unknown>")
        raise SignalDecisionUnavailable(
            "market_input_missing",
            f"binding {binding_id} has no market_input.closes snapshot",
        )
    closes = raw.get("closes")
    if (
        not isinstance(closes, Sequence)
        or isinstance(closes, (str, bytes))
        or not closes
    ):
        binding_id = str(binding.get("binding_id") or "<unknown>")
        raise SignalDecisionUnavailable(
            "market_input_missing",
            f"binding {binding_id} has no market_input.closes snapshot",
        )
    minimum_closes = policy["minimum_closes"] if policy is not None else 1
    if len(closes) < minimum_closes:
        binding_id = str(binding.get("binding_id") or "<unknown>")
        raise SignalDecisionUnavailable(
            "market_input_insufficient",
            f"binding {binding_id} requires {minimum_closes} closes, got {len(closes)}",
        )
    try:
        normalized_closes = [float(close) for close in closes]
    except (TypeError, ValueError) as exc:
        raise SignalDecisionUnavailable(
            "market_input_invalid",
            f"binding {binding.get('binding_id') or '<unknown>'} market closes are not numeric",
        ) from exc
    if any(not math.isfinite(close) or close <= 0 for close in normalized_closes):
        raise SignalDecisionUnavailable(
            "market_input_invalid",
            f"binding {binding.get('binding_id') or '<unknown>'} market closes must be positive",
        )

    requested_symbol = str(binding.get("symbol") or metadata.get("symbol") or "").strip()
    if not requested_symbol and strategy_artifact:
        params = strategy_artifact.get("parameters")
        if isinstance(params, Mapping):
            syms = params.get("symbols")
            if isinstance(syms, Sequence) and not isinstance(syms, (str, bytes)) and syms:
                requested_symbol = str(syms[0]).strip()
    snapshot_symbol = str(raw.get("symbol") or requested_symbol).strip()
    if requested_symbol and snapshot_symbol and snapshot_symbol != requested_symbol:
        raise SignalDecisionUnavailable(
            "market_input_invalid",
            f"Source snapshot symbol {snapshot_symbol!r} does not match binding symbol {requested_symbol!r}",
        )
    if policy is not None:
        decision = admit_market_snapshot(
            raw,
            expected_symbol=requested_symbol or None,
            max_age_seconds=policy["max_age_seconds"],
            minimum_closes=policy["minimum_closes"],
            now_iso=now_iso,
            binding_id=str(binding.get("binding_id") or "<unknown>"),
        )
        if not decision.admitted:
            raise SignalDecisionUnavailable(
                decision.reason_code or "market_input_invalid",
                decision.detail or f"Snapshot admission rejected: {decision.reason_code}",
            )
    feature_map: dict[str, Any] = {}
    for feat_source in (
        metadata.get("features"),
        binding.get("features"),
        raw.get("features"),
    ):
        if isinstance(feat_source, Mapping):
            feature_map.update(dict(feat_source))
    for pv_key in (
        "open",
        "high",
        "low",
        "volume",
        "vwap",
        "TradeValue",
        "TradeVolume",
    ):
        if pv_key not in feature_map and raw.get(pv_key) is not None:
            feature_map[pv_key] = raw.get(pv_key)
    feature_map.setdefault("close", normalized_closes)

    return {
        "closes": normalized_closes,
        "features": feature_map,
        "holding_bars": raw.get("holding_bars", binding.get("holding_bars", metadata.get("holding_bars"))),
        "current_direction": raw.get(
            "current_direction",
            binding.get("current_direction", metadata.get("current_direction")),
        ),
        "symbol": snapshot_symbol or raw.get("symbol"),
        "source_ref": raw.get("source_ref"),
        "observed_at": raw.get("observed_at"),
        "snapshot_id": raw.get("snapshot_id"),
        "event_time": raw.get("event_time"),
        "lineage": raw.get("lineage"),
        "market": raw.get("market"),
        "venue": raw.get("venue"),
        "market_code": raw.get("market_code"),
        "exchange": raw.get("exchange"),
        "country": raw.get("country"),
    }


def _market_input_policy(
    binding: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, int] | None:
    raw = binding.get("market_data_policy") or metadata.get("market_data_policy")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise SignalDecisionUnavailable(
            "market_input_policy_invalid",
            "RuntimeBinding market_data_policy must be an object",
        )
    owner = str(raw.get("owner") or raw.get("source") or "").strip()
    if owner != "source-ingest":
        raise SignalDecisionUnavailable(
            "market_input_policy_invalid",
            "RuntimeBinding market_data_policy must select owner 'source-ingest'",
        )
    contract = str(raw.get("contract") or "latest_stored_normalized").strip()
    if contract != "latest_stored_normalized":
        raise SignalDecisionUnavailable(
            "market_input_policy_invalid",
            "RuntimeBinding market_data_policy must select contract 'latest_stored_normalized'",
        )
    max_age_seconds = raw.get("max_age_seconds")
    minimum_closes = raw.get("minimum_closes", 2)
    if (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, int)
        or max_age_seconds < 1
    ):
        raise SignalDecisionUnavailable(
            "market_input_policy_invalid",
            "RuntimeBinding market_data_policy.max_age_seconds must be a positive integer",
        )
    if (
        isinstance(minimum_closes, bool)
        or not isinstance(minimum_closes, int)
        or minimum_closes < 2
    ):
        raise SignalDecisionUnavailable(
            "market_input_policy_invalid",
            "RuntimeBinding market_data_policy.minimum_closes must be an integer >= 2",
        )
    return {
        "max_age_seconds": max_age_seconds,
        "minimum_closes": minimum_closes,
    }


def _strategy_symbol(
    binding: Mapping[str, Any],
    strategy_artifact: Mapping[str, Any],
    market_input: Mapping[str, Any],
) -> str:
    binding_id = str(binding.get("binding_id") or "<unknown>")

    # 1. Determine raw requested symbol
    symbol = str(market_input.get("symbol") or binding.get("symbol") or "").strip()
    parameters = strategy_artifact.get("parameters")
    symbols = parameters.get("symbols") if isinstance(parameters, Mapping) else None
    if not symbol and isinstance(symbols, Sequence) and not isinstance(symbols, (str, bytes)):
        symbol = str(symbols[0] if symbols else "").strip()
    if not symbol:
        raise SignalDecisionUnavailable(
            "market_input_missing",
            f"binding {binding_id} has no market symbol",
        )

    # 2. Raw symbol membership check against approved artifact parameters.symbols
    screened = "screen" in (strategy_artifact.get("strategy_logic") or {})
    if not screened and isinstance(symbols, Sequence) and not isinstance(symbols, (str, bytes)):
        if symbol not in symbols:
            raise SignalDecisionUnavailable(
                "market_input_invalid",
                f"binding {binding_id} market symbol {symbol!r} is outside artifact symbols {list(symbols)!r}",
            )

    # 3. Check intrinsic symbol market and collect explicit market contexts
    intrinsic_market = _intrinsic_symbol_market(symbol)
    explicit_origins, explicit_venue = _collect_explicit_market_contexts(
        binding, strategy_artifact, market_input, binding_id=binding_id
    )

    if len(explicit_origins) > 1:
        details = "; ".join(f"{cat}: [{', '.join(origins)}]" for cat, origins in sorted(explicit_origins.items()))
        raise SignalDecisionUnavailable(
            "market_context_contradictory",
            f"binding {binding_id} has contradictory explicit market contexts: {details}",
        )

    explicit_l1 = next(iter(explicit_origins.keys())) if explicit_origins else None

    # 4. Determine target market
    if intrinsic_market is not None:
        if explicit_l1 and explicit_l1 != intrinsic_market:
            raise SignalDecisionUnavailable(
                "market_context_mismatch",
                f"binding {binding_id} symbol {symbol!r} intrinsic market {intrinsic_market!r} "
                f"contradicts explicit market context {explicit_l1!r}",
            )
        target_market = intrinsic_market
    else:
        # Bare symbol (no intrinsic market suffix): check explicit context
        if not explicit_l1:
            # Bare ticker with no explicit market context anywhere -> fail closed!
            raise SignalDecisionUnavailable(
                "market_context_missing",
                f"binding {binding_id} symbol {symbol!r} has no explicit market context and no intrinsic market suffix",
            )
        target_market = explicit_l1

    # 5. Normalize symbol based on target market using existing adapters
    resolved_symbol = symbol
    if target_market == "US":
        if "." in symbol:
            resolved_symbol = symbol
        else:
            try:
                native, venue = normalize_us_symbol(symbol, venue=explicit_venue)
                if venue in ("NASDAQ", "NYSE", "ARCA", "BATS", "AMEX"):
                    resolved_symbol = f"{native}.{venue}"
                else:
                    resolved_symbol = f"{native}.US"
            except ValueError as err:
                raise SignalDecisionUnavailable(
                    "market_symbol_unexecutable",
                    f"Failed to normalize US symbol {symbol!r}: {err}",
                ) from err
    elif target_market == "TW":
        if "." in symbol:
            resolved_symbol = symbol
        else:
            try:
                native, _ = normalize_taiwan_symbol(symbol, venue=explicit_venue)
                if explicit_venue and explicit_venue.upper() in _TAIWAN_VENUE_SUFFIXES:
                    resolved_symbol = f"{native}.{explicit_venue.upper()}"
                else:
                    resolved_symbol = f"{native}.TW"
            except ValueError as err:
                raise SignalDecisionUnavailable(
                    "market_symbol_unexecutable",
                    f"Failed to normalize Taiwan symbol {symbol!r}: {err}",
                ) from err
    elif target_market == "CRYPTO":
        clean = symbol.replace("/", "").strip().upper()
        if "." in clean:
            resolved_symbol = clean
        else:
            if explicit_venue and explicit_venue.upper() in ("KRAKEN", "COINBASE", "BINANCE"):
                resolved_symbol = f"{clean}.{explicit_venue.upper()}"
            else:
                resolved_symbol = clean
    elif target_market == "FX":
        clean = symbol.replace("/", "").strip().upper()
        if "." in clean:
            resolved_symbol = clean
        else:
            resolved_symbol = f"{clean}.FX"

    # 6. Verify executable by execution plane (Shioaji or LEAN symbol_parser)
    try:
        validate_executable_symbol(resolved_symbol)
    except (SymbolParseError, ValueError) as err:
        raise SignalDecisionUnavailable(
            "market_symbol_unexecutable",
            f"binding {binding_id} resolved symbol {resolved_symbol!r} is not executable: {err}",
        ) from err

    return resolved_symbol


def _binding_dict_from_obj(binding: Any) -> dict[str, Any]:
    if isinstance(binding, dict):
        return binding
    return {
        "binding_id": getattr(binding, "binding_id", ""),
        "runtime_id": getattr(binding, "runtime_id", None) or "rt-default",
        "capital_pool_id": getattr(binding, "capital_pool_id", "pool-default"),
        "artifact_id": getattr(binding, "artifact_id", "art-default"),
        "artifact_version": getattr(binding, "artifact_version", "1.0.0"),
        "persona_capital_binding_id": getattr(binding, "persona_capital_binding_id", "pcb-default-001"),
        "symbol": getattr(binding, "symbol", "AAPL.US"),
        "strategy_id": getattr(binding, "strategy_id", "smoke-strategy"),
        "tenant_id": getattr(binding, "tenant_id", "default"),
        "environment": getattr(binding, "environment", "paper"),
        "market": getattr(binding, "market", None),
        "venue": getattr(binding, "venue", None),
        "market_code": getattr(binding, "market_code", None),
        "exchange": getattr(binding, "exchange", None),
        "country": getattr(binding, "country", None),
        "metadata": getattr(binding, "metadata", None),
    }


def _paper_signal_identity(
    signal: dict[str, Any],
    binding: Mapping[str, Any],
    now_iso: str,
) -> None:
    """Attach a valid paper journey envelope to a legacy pre-built signal."""
    metadata = signal.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("paper signal metadata must be an object")
    binding_metadata = binding.get("metadata") if isinstance(binding.get("metadata"), Mapping) else {}
    tenant_id = str(
        signal.get("tenant_id")
        or metadata.get("tenant_id")
        or binding.get("tenant_id")
        or binding_metadata.get("tenant_id")
        or os.getenv("PANTHEON_TENANT_ID", "default")
    ).strip()
    environment = str(
        signal.get("environment")
        or metadata.get("environment")
        or binding.get("environment")
        or binding.get("deployment_stage")
        or binding.get("deployment_mode")
        or binding_metadata.get("environment")
        or "paper"
    ).strip().lower()
    signal_id = str(signal.get("signal_id") or "").strip()
    if not signal_id:
        raise ValueError("paper signal_id is required")

    raw_envelope = signal.get("correlation_envelope") or metadata.get("correlation_envelope")
    try:
        if isinstance(raw_envelope, Mapping) and raw_envelope.get("journey_id"):
            envelope = validate_envelope(raw_envelope)
            if envelope["tenant_id"] != tenant_id:
                raise CorrelationEnvelopeError(
                    "tenant_id conflicts with correlation_envelope.tenant_id"
                )
            if envelope["environment"] != environment:
                raise CorrelationEnvelopeError(
                    "environment conflicts with correlation_envelope.environment"
                )
        else:
            if raw_envelope is not None and not isinstance(raw_envelope, Mapping):
                raise CorrelationEnvelopeError("correlation_envelope must be an object")
            upstream = dict(raw_envelope or {})
            upstream["tenant_id"] = tenant_id
            upstream["environment"] = environment
            envelope = mint_trade_envelope(
                upstream,
                producer="execution.paper-signal-producer",
                event_id=f"signal:{signal_id}",
                journey_id=str(signal.get("journey_id") or "").strip() or None,
                now=now_iso,
            )
    except CorrelationEnvelopeError as exc:
        raise ValueError(f"invalid paper signal correlation envelope: {exc}") from exc

    binding_id = str(signal.get("binding_id") or binding.get("binding_id") or "").strip()
    signal["tenant_id"] = tenant_id
    signal["environment"] = environment
    signal["journey_id"] = envelope["journey_id"]
    signal["correlation_envelope"] = envelope
    if not str(signal.get("run_id") or "").strip():
        signal["run_id"] = f"run-{binding_id}-{now_iso}"
    metadata.setdefault("tenant_id", tenant_id)
    metadata.setdefault("environment", environment)
    metadata.setdefault("journey_id", envelope["journey_id"])


COUNTERFACTUAL_ROWS_KEY = "instrument_counterfactual_rows"
COUNTERFACTUAL_GRADING_KEY = "instrument_counterfactual_grading"
GRADING_PENDING = "pending"
GRADING_DONE = "done"
GRADING_NOT_GRADABLE = "not_gradable"


def _grading_record_id(decision_id: str) -> str:
    """Terminal grading record (first write wins) of one recorded decision."""
    return f"{decision_id}#grading"


def _grading_attempt_id(decision_id: str, bar_day: "date") -> str:
    """One immutable pending-attempt record per decision and grading bar."""
    return f"{decision_id}#grading-attempt@{bar_day.isoformat()}"


def _bar_date(event_time: Any) -> "date | None":
    """Calendar date of an admitted bar's ``event_time`` in its own offset."""
    text = str(event_time or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _trading_days_between(first: "date", last: "date") -> int:
    """Number of trading-day steps from ``first`` forward to ``last``."""
    from services.execution.lean_runtime.instrument_candidates import is_trading_day

    steps = 0
    day = first
    while day < last:
        day += timedelta(days=1)
        if is_trading_day(day):
            steps += 1
    return steps


def _next_trading_day(day: "date") -> "date":
    from services.execution.lean_runtime.instrument_candidates import is_trading_day

    following = day + timedelta(days=1)
    while not is_trading_day(following):
        following += timedelta(days=1)
    return following


def _grade_one_decision(
    decision_id: str,
    stored: Mapping[str, Any],
    *,
    bar_day: "date",
    closes: Sequence[float],
    symbol_underlying: str,
    now_iso: str,
    normalized_root: str | None,
) -> tuple[str, str | None, list[dict[str, Any]]]:
    """Return ``(status, reason, rows)`` for one recorded decision.

    ``pending`` keeps the decision open for a later bar; ``not_gradable`` closes
    it with a reason; ``done`` carries the rows.  The rows come from
    ``expression_replay`` (one pnl formula, shared with the chosen leg).

    The grading session is the first trading session after the session whose
    close the decision priced from.  Its underlying close is the snapshot close
    of that session (``closes`` are consecutive daily closes ending at
    ``bar_day``) and each candidate contract's mark is the dataset daily
    settlement whose ``price_date`` is that session.
    """
    from services.execution.lean_runtime import expression_replay
    from services.execution.lean_runtime.expression_policy import is_no_trade
    from services.execution.lean_runtime.futures_session_inputs import (
        open_stock_future_positions,
        read_session_settlement_prices_for_open_futures,
    )

    replay_input = stored.get("replay_input")
    if not isinstance(replay_input, Mapping):
        return GRADING_NOT_GRADABLE, "replay_input_missing", []
    candidates = [dict(c) for c in replay_input.get("candidates") or [] if isinstance(c, Mapping)]
    view = dict(replay_input.get("view") or {})
    recorded = stored.get("record")
    if not candidates or not isinstance(recorded, Mapping):
        return GRADING_NOT_GRADABLE, "replay_input_incomplete", []
    if view.get("underlying") != symbol_underlying:
        return GRADING_NOT_GRADABLE, "underlying_differs_from_this_binding", []
    if not is_no_trade(recorded) and recorded.get("reason_code") == "close_leg":
        return GRADING_NOT_GRADABLE, "close_leg_has_no_entry_to_grade", []
    underlying_leg = next(
        (c for c in candidates if str(c.get("instrument_id", "")).startswith("eq:")), None
    )
    try:
        entry_day = date.fromisoformat(str((underlying_leg or {}).get("price_date") or ""))
    except ValueError:
        entry_day = None
    if underlying_leg is None or entry_day is None or underlying_leg.get("price") is None:
        return GRADING_NOT_GRADABLE, "underlying_entry_missing", []
    grading_day = _next_trading_day(entry_day)
    if bar_day < grading_day:
        return GRADING_PENDING, "grading_session_close_not_yet_admitted", []
    offset = _trading_days_between(grading_day, bar_day)
    if offset >= len(closes):
        return GRADING_NOT_GRADABLE, "grading_session_close_outside_snapshot_window", []
    underlying_close = float(closes[-1 - offset])

    marks: dict[str, list[float]] = {str(underlying_leg["instrument_id"]): [underlying_close]}
    futures = [c for c in candidates if str(c.get("instrument_id", "")).startswith("ssf:")]
    if futures:
        book = [{**c, "kind": "stock_future", "units": 1} for c in futures]
        session = read_session_settlement_prices_for_open_futures(
            open_stock_future_positions(book),
            grading_day,
            normalized_root=normalized_root,
            partition_horizon=now_iso[:10],
            historical_session=True,
        )
        prices = session["session_settlement_prices"]
        missing = [str(c["instrument_id"]) for c in futures if str(c["instrument_id"]) not in prices]
        if missing:
            reasons = session["session_settlement_price_reasons"]
            detail = "; ".join(f"{m}:{reasons.get(m, 'settlement_price_missing')}" for m in sorted(missing))
            return GRADING_PENDING, f"contract_settlement_unavailable: {detail}", []
        for candidate in futures:
            marks[str(candidate["instrument_id"])] = [float(prices[str(candidate["instrument_id"])])]

    record = {
        "decision_id": decision_id,
        "view": view,
        "candidates": candidates,
        "book_before": dict(replay_input.get("book_before") or {}),
        "budget": dict(replay_input.get("budget") or {}),
        "marks": marks,
    }
    result = expression_replay.replay([record], replay_input.get("parameters"))["decisions"][0]
    replayed = result["record"]
    if is_no_trade(replayed) != is_no_trade(recorded) or (
        not is_no_trade(recorded)
        and (
            replayed.get("instrument_id") != recorded.get("instrument_id")
            or int(replayed.get("units") or 0) != int(recorded.get("units") or 0)
        )
    ):
        return GRADING_NOT_GRADABLE, "replay_diverged_from_recorded_decision", []
    return GRADING_DONE, None, expression_replay.counterfactual_rows(record, result)


def collect_counterfactual_grading(
    store: Any,
    *,
    event_time: Any,
    closes: Sequence[float],
    symbol_underlying: str,
    now_iso: str,
    decision_id_for_bar: Callable[[str], str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[tuple[str, dict[str, Any]]]]:
    """Rows for every recorded decision that becomes gradable on this bar.

    Earlier decisions are looked up by the ids their bars deterministically
    produced (one per earlier trading session inside the snapshot window), so
    no new index or store is needed.  Returns ``(rows, diagnostics, to_mark)``:
    ``diagnostics`` lists pending/not-gradable reasons for telemetry and
    ``to_mark`` the terminal ``done`` records the caller writes once the
    carrying signal is durably enqueued.  ``not_gradable`` terminal records
    are written here (nothing is emitted for them).
    """
    getter = getattr(store, "get_decision_record", None)
    putter = getattr(store, "put_decision_record", None)
    if not callable(getter) or not callable(putter):
        return [], [], []
    bar_day = _bar_date(event_time)
    clean_closes = [float(c) for c in closes]
    if (
        bar_day is None
        or not clean_closes
        or any(not math.isfinite(c) or c <= 0 for c in clean_closes)
    ):
        return [], [], []
    from services.execution.lean_runtime.instrument_candidates import previous_trading_day

    normalized_root = os.getenv(SSF_NORMALIZED_ROOT_ENV) or None
    rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    to_mark: list[tuple[str, dict[str, Any]]] = []
    earlier = bar_day
    for _ in range(len(clean_closes)):
        earlier = previous_trading_day(earlier)
        decision_id = decision_id_for_bar(earlier.isoformat())
        if getter(_grading_record_id(decision_id)) is not None:
            continue
        stored = getter(decision_id)
        if not isinstance(stored, Mapping):
            continue
        try:
            status, reason, decision_rows = _grade_one_decision(
                decision_id,
                stored,
                bar_day=bar_day,
                closes=clean_closes,
                symbol_underlying=symbol_underlying,
                now_iso=now_iso,
                normalized_root=normalized_root,
            )
        except Exception as exc:  # noqa: BLE001 - grading never blocks signal production
            log.warning("counterfactual grading failed for %s: %s", decision_id, exc)
            status, reason, decision_rows = GRADING_PENDING, f"grading_error: {type(exc).__name__}: {exc}", []
        state = {
            "status": status,
            "reason": reason,
            "graded_on_bar": bar_day.isoformat(),
            "recorded_at": now_iso,
        }
        diagnostics.append({"decision_id": decision_id, **state})
        if status == GRADING_DONE:
            rows.extend(decision_rows)
            to_mark.append((decision_id, state))
        elif status == GRADING_NOT_GRADABLE:
            putter(_grading_record_id(decision_id), state)
        else:
            putter(_grading_attempt_id(decision_id, bar_day), state)
    return rows, diagnostics, to_mark


class PaperSignalProducer:
    """Generate signals for paper bindings and enqueue them onto their queues.

    Drains or ticks across all eligible paper bindings, setting up isolated Redis
    keys and validation per binding.
    """

    def __init__(self, store_for: Callable[[Any], Any], strategy: Any) -> None:
        self._store_for = store_for
        self._strategy = strategy
        self._degraded_by_binding: dict[str, str] = {}

    @property
    def degraded_bindings(self) -> dict[str, str]:
        """Return binding-scoped reasons that prevented the latest tick."""

        return dict(self._degraded_by_binding)

    def produce(self, binding: Any, now_iso: str) -> int:
        """Emit + enqueue signals for one binding; return the count enqueued."""
        b_dict = _binding_dict_from_obj(binding)
        binding_id = b_dict["binding_id"]

        # Generate strategy output
        try:
            out = self._strategy(binding, now_iso)
        except SignalDecisionUnavailable as exc:
            self._degraded_by_binding[binding_id] = str(exc)
            log.warning(
                "paper_signal_producer degraded binding %s: %s",
                binding_id,
                exc,
            )
            return 0

        self._degraded_by_binding.pop(binding_id, None)
        if isinstance(out, dict) and "portfolio_decisions" in out:
            return sum(self._enqueue_output(binding, now_iso, decision)
                       for decision in out["portfolio_decisions"])
        return self._enqueue_output(binding, now_iso, out)

    def _enqueue_output(self, binding: Any, now_iso: str, out: Any) -> int:
        b_dict = _binding_dict_from_obj(binding)
        binding_id = b_dict["binding_id"]
        store = self._store_for(binding)

        enqueued = 0
        binding_metadata = b_dict.get("metadata") if isinstance(b_dict.get("metadata"), Mapping) else {}
        inline_book = (
            b_dict.get("persona_book")
            or binding_metadata.get("persona_book")
            or (
                binding_metadata.get("expression_inputs", {}).get("persona_book")
                if isinstance(binding_metadata.get("expression_inputs"), Mapping)
                else None
            )
        )
        if isinstance(out, list):
            # Legacy list-of-signals flow
            from services.execution.lean_runtime.futures_session_inputs import (
                attach_futures_session_inputs,
            )
            from services.execution.lean_runtime.signal_producer import is_signal_already_admitted
            for sig in out:
                if "metadata" not in sig:
                    sig["metadata"] = {}
                if isinstance(sig["metadata"], dict):
                    sig["metadata"]["is_real_order"] = False
                    sig["metadata"]["is_real_capital"] = False
                    sig_book = sig["metadata"].get("persona_book") or inline_book
                    if sig_book is not None and "session_settlement_prices" not in sig["metadata"]:
                        attach_futures_session_inputs(
                            sig,
                            sig_book,
                            trade_date=sig["metadata"].get("trade_date")
                            or str(sig.get("timestamp") or now_iso)[:10],
                        )
                _paper_signal_identity(sig, b_dict, now_iso)
                if is_signal_already_admitted(store, str(sig.get("signal_id") or "")):
                    continue
                store.enqueue(sig)
                enqueued += 1
        elif isinstance(out, dict):
            # First-class decision dict flow
            from services.execution.lean_runtime.futures_session_inputs import (
                attach_futures_session_inputs,
            )
            from services.execution.lean_runtime.signal_producer import (
                DecisionSignalProducer,
                is_signal_already_admitted,
            )
            dsp = DecisionSignalProducer(store)

            pool_snapshot = (out.get("metadata") or {}).get("candidate_pool_snapshot")
            if pool_snapshot is not None:
                putter = getattr(store, "put_decision_record", None)
                if not callable(putter):
                    self._degraded_by_binding[binding_id] = "candidate_pool_store_unsupported"
                    return 0
                # Store HOLD days as well as trades, before deduplication or
                # expression selection can return early.
                putter(f"candidate-pool-{binding_id}-{pool_snapshot['digest']}", pool_snapshot)

            # An explicit signal_id marks a decision whose identity is already
            # deterministic; reuse the store's processed or execution-fence record
            # instead of rebuilding and re-enqueueing an already-admitted decision.
            explicit_signal_id = str(out.get("signal_id") or "").strip()
            if explicit_signal_id and is_signal_already_admitted(store, explicit_signal_id):
                log.info(
                    "paper_signal_producer skipped completed decision %s for binding %s",
                    explicit_signal_id,
                    binding_id,
                )
                return 0

            expression = out.pop("expression", None)
            if isinstance(expression, Mapping):
                # The instrument decision is durable before any order exists. A
                # restart reuses the stored record instead of deciding again.
                decision_id = str(out.get("decision_id") or "")
                getter = getattr(store, "get_decision_record", None)
                putter = getattr(store, "put_decision_record", None)
                if not callable(getter) or not callable(putter):
                    self._degraded_by_binding[binding_id] = (
                        "expression_store_unsupported: pending store cannot persist decisions"
                    )
                    return 0
                stored = getter(decision_id)
                expression_view = dict(expression["view"])
                if stored is None:
                    evaluated = expression["evaluate"]()
                    expression_view = dict(evaluated["view"])
                    stored = putter(
                        decision_id,
                        {
                            "record": dict(evaluated["decision"]),
                            "kind": expression_view.get("kind"),
                            "multiplier": expression_view.get("multiplier"),
                            "replay_input": evaluated.get("replay_input"),
                        },
                    )
                apply_expression_record(out, expression_view, stored)

            if "metadata" not in out:
                out["metadata"] = {}
            if isinstance(out["metadata"], dict):
                out["metadata"]["is_real_order"] = False
                out["metadata"]["is_real_capital"] = False
                out_book = out["metadata"].get("persona_book") or inline_book
                if out_book is not None and "session_settlement_prices" not in out["metadata"]:
                    attach_futures_session_inputs(
                        out,
                        out_book,
                        trade_date=out["metadata"].get("trade_date")
                        or str(out.get("timestamp") or now_iso)[:10],
                    )
                tenant_id = (
                    out.get("tenant_id")
                    or out["metadata"].get("tenant_id")
                    or b_dict.get("tenant_id")
                    or binding_metadata.get("tenant_id")
                    or os.getenv("PANTHEON_TENANT_ID", "default")
                )
                environment = (
                    out.get("environment")
                    or out["metadata"].get("environment")
                    or b_dict.get("environment")
                    or b_dict.get("deployment_stage")
                    or b_dict.get("deployment_mode")
                    or binding_metadata.get("environment")
                    or "paper"
                )
                out["tenant_id"] = str(tenant_id)
                out["environment"] = str(environment).strip().lower()
                out["metadata"].setdefault("tenant_id", str(tenant_id))
                out["metadata"].setdefault(
                    "environment", str(environment).strip().lower()
                )

            grading_to_mark: list[tuple[str, dict[str, Any]]] = []
            grading_input = expression.get("grading") if isinstance(expression, Mapping) else None
            if isinstance(grading_input, Mapping) and isinstance(out.get("metadata"), dict):
                from services.execution.lean_runtime.instrument_candidates import (
                    normalize_underlying_code,
                )

                try:
                    grading_underlying = normalize_underlying_code(str(out.get("symbol") or ""))
                except ValueError:
                    grading_underlying = ""
                grading_rows, grading_diagnostics, grading_to_mark = collect_counterfactual_grading(
                    store,
                    event_time=out["metadata"].get("market_input_event_time"),
                    closes=grading_input["closes"],
                    symbol_underlying=grading_underlying,
                    now_iso=now_iso,
                    decision_id_for_bar=grading_input["decision_id_for_bar"],
                )
                if grading_rows:
                    out["metadata"][COUNTERFACTUAL_ROWS_KEY] = grading_rows
                if grading_diagnostics:
                    out["metadata"][COUNTERFACTUAL_GRADING_KEY] = grading_diagnostics

            batch = dsp.produce(
                out,
                now_iso=now_iso,
                strategy_id=out.get("strategy_id"),
                source_worker=out.get("source_worker", "paper-signal-producer"),
                binding_id=binding_id,
                runtime_id=b_dict.get("runtime_id"),
                run_id=out.get("run_id"),
            )
            enqueued = batch.count
            if enqueued:
                for graded_id, graded_state in grading_to_mark:
                    store.put_decision_record(
                        _grading_record_id(graded_id),
                        {**graded_state, "carried_by_signal_id": out.get("signal_id")},
                    )

        if enqueued:
            log.info(
                "paper_signal_producer enqueued %d signal(s) for binding %s",
                enqueued,
                binding_id,
            )
        return enqueued

    def tick(self, bindings: Sequence[Any], now_iso: str) -> dict[str, int]:
        """Produce for every binding; return {binding_id: enqueued_count}."""
        binding_ids = {
            _binding_dict_from_obj(binding)["binding_id"] for binding in bindings
        }
        self._degraded_by_binding = {
            binding_id: reason
            for binding_id, reason in self._degraded_by_binding.items()
            if binding_id in binding_ids
        }
        return {
            _binding_dict_from_obj(b)["binding_id"]: self.produce(b, now_iso)
            for b in bindings
        }


# ---------------------------------------------------------------------------
# Deployable runner / Binding Discovery
# ---------------------------------------------------------------------------

def fetch_eligible_paper_bindings(
    runtime_manager_url: str,
    token: str | None = None,
    *,
    raise_on_error: bool = False,
) -> list[dict[str, Any]]:
    """Fetch active paper bindings from runtime-manager desired state."""
    import urllib.parse
    import urllib.request
    import json

    if not runtime_manager_url:
        return []

    url = f"{runtime_manager_url.rstrip('/')}/api/runtime-fleet/desired-state?stage=paper"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        bindings = [
            binding
            for binding in payload.get("bindings", [])
            if binding.get("status") == "active"
        ]
        hydrated: list[dict[str, Any]] = []
        for binding in bindings:
            binding_id = str(binding.get("binding_id") or "").strip()
            if not binding_id:
                raise ValueError("desired-state binding_id is required")
            detail_url = (
                f"{runtime_manager_url.rstrip('/')}/api/runtime-bindings/"
                f"{urllib.parse.quote(binding_id, safe='')}"
            )
            detail_req = urllib.request.Request(
                detail_url,
                headers=headers,
                method="GET",
            )
            with urllib.request.urlopen(detail_req, timeout=10) as detail_resp:
                detail = json.loads(detail_resp.read().decode("utf-8"))
            if detail.get("binding_id") != binding_id or detail.get("status") != "active":
                raise ValueError(
                    f"authoritative RuntimeBinding {binding_id!r} is not active"
                )
            hydrated.append(detail)
        return hydrated
    except Exception as e:
        log.warning("Failed to fetch active bindings from runtime-manager: %s", e)
        if raise_on_error:
            raise
        return []


def parse_bindings_env(raw: str) -> list[BindingRef]:
    """Parse ``PANTHEON_PAPER_PRODUCER_BINDINGS`` JSON into BindingRefs.

    Expected: ``[{"binding_id": "...", "strategy_id": "...", "symbol": "AAPL.US"}, ...]``
    ``symbol`` is optional (defaults to the BindingRef default).
    """
    import json

    if not raw or not raw.strip():
        return []
    items = json.loads(raw)
    if not isinstance(items, list):
        raise ValueError("PANTHEON_PAPER_PRODUCER_BINDINGS must be a JSON list")
    bindings: list[BindingRef] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("each binding entry must be an object")
        binding_id = str(item["binding_id"]).strip()
        strategy_id = str(item["strategy_id"]).strip()
        if not binding_id or not strategy_id:
            raise ValueError("binding_id and strategy_id are required")
        symbol = str(item.get("symbol") or "AAPL.US").strip()
        bindings.append(BindingRef(binding_id=binding_id, strategy_id=strategy_id, symbol=symbol))
    return bindings


def _redis_store_factory(signal_store_url: str):
    """Return a store_for that builds a binding-scoped redis pending store."""
    from services.execution.lean_runtime.pending_signal_store import (
        RedisPendingSignalStore,
        binding_queue_key,
    )

    def store_for(binding_or_id: Any):
        if isinstance(binding_or_id, str):
            bid = binding_or_id
        else:
            bid = getattr(binding_or_id, "binding_id", "")
            if not bid and isinstance(binding_or_id, dict):
                bid = binding_or_id.get("binding_id", "")
        return RedisPendingSignalStore(
            signal_store_url, queue_key=binding_queue_key(bid)
        )

    return store_for


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _require_paper_only_configuration() -> None:
    unsafe = [
        name
        for name in (
            "PANTHEON_LIVE_BROKER_ENABLED",
            "PANTHEON_CANARY_EXECUTION_ENABLED",
        )
        if _env_flag(name)
    ]
    if unsafe:
        raise RuntimeError(
            "paper-signal-producer refuses live/canary execution flags: "
            + ", ".join(unsafe)
        )


def _runner_strategy() -> Any:
    """Resolve the deployable strategy profile without a hard-coded signal path."""

    profile = os.getenv("PAPER_SIGNAL_STRATEGY", "artifact").strip().lower()
    if profile in {"artifact", "current-artifact", "current_artifact"}:
        return CurrentArtifactStrategy(
            expression_inputs=build_production_expression_inputs()
        )
    if profile == "smoke":
        return BoundedPaperStrategy()
    raise ValueError(
        "PAPER_SIGNAL_STRATEGY must be 'artifact' (default) or explicit 'smoke'; "
        f"got {profile!r}."
    )


def expression_startup_report(
    provider: Any, *, layer_on: bool
) -> tuple[str, str | None]:
    """One startup log line and the degraded reason for a missing capital reader.

    Names only whether each reader was configured; never a credential or URL.
    The reason is ``None`` unless the expression layer is on and the capital
    reader is absent, in which case every decision would be budget_unavailable.
    """

    capital = bool(getattr(provider, "capital_reader_configured", False))
    ledger = bool(getattr(provider, "ledger_reader_configured", False))
    line = (
        "expression layer %s; capital reader %s; ledger reader %s"
        % (
            "on" if layer_on else "off",
            "configured" if capital else "not configured",
            "configured" if ledger else "not configured",
        )
    )
    reason = None
    if layer_on and not capital:
        reason = (
            "capital_pool: capital reader is not configured; "
            "expression decisions will be budget_unavailable"
        )
    return line, reason


def healthcheck() -> int:
    """Validate a recent successful paper-only producer tick."""

    return check_worker_health(
        health_file=os.getenv("PAPER_PRODUCER_HEALTH_FILE", ""),
        interval_seconds=max(
            1,
            int(float(os.getenv("PAPER_PRODUCER_INTERVAL_SECONDS", "60"))),
        ),
        worker_name=_WORKER_NAME,
        expected={
            "execution_mode": "paper",
            "live_capital_enabled": False,
            "live_order_submission_enabled": False,
        },
    )


def main() -> int:  # pragma: no cover
    import time
    from datetime import datetime, timezone

    logging.basicConfig(level=logging.INFO)
    _require_paper_only_configuration()
    signal_store_url = os.getenv("SIGNAL_STORE_URL", "").strip()
    if not signal_store_url:
        raise SystemExit("SIGNAL_STORE_URL is required (redis://signal-store:6379)")

    runtime_manager_url = os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "").strip()
    runtime_manager_token = os.getenv("PANTHEON_RUNTIME_MANAGER_TOKEN", "").strip()

    interval = float(os.getenv("PAPER_PRODUCER_INTERVAL_SECONDS", "60"))
    max_ticks = int(os.getenv("PAPER_PRODUCER_MAX_TICKS", "0"))
    if interval <= 0:
        raise ValueError("PAPER_PRODUCER_INTERVAL_SECONDS must be > 0")
    if max_ticks < 0:
        raise ValueError("PAPER_PRODUCER_MAX_TICKS must be >= 0")
    health_file = os.getenv("PAPER_PRODUCER_HEALTH_FILE", "").strip()
    strategy = _runner_strategy()
    producer = PaperSignalProducer(
        store_for=_redis_store_factory(signal_store_url), strategy=strategy
    )
    log.info("paper_signal_producer started; interval=%.0fs", interval)
    startup_line, startup_gap = expression_startup_report(
        getattr(strategy, "_expression_inputs", None),
        layer_on=expression_layer_enabled(),
    )
    log.info(startup_line)
    health: dict[str, Any] = {
        "worker_name": _WORKER_NAME,
        "status": "starting",
        "ticks": 0,
        "last_tick_at": None,
        "last_success_at": None,
        "last_failure_at": None,
        "last_failure_reason": None,
        "active_binding_count": 0,
        "enqueued_signal_count": 0,
        "degraded_binding_count": 0,
        "degraded_bindings": {},
        "execution_mode": "paper",
        "live_capital_enabled": False,
        "live_order_submission_enabled": False,
    }
    if startup_gap:
        health["status"] = "degraded"
        health["last_failure_reason"] = startup_gap
    write_health(health_file, health)

    tick = 0
    while True:
        tick += 1
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            # Dynamic active binding resolution
            bindings = []
            if runtime_manager_url:
                bindings = fetch_eligible_paper_bindings(
                    runtime_manager_url,
                    runtime_manager_token,
                    raise_on_error=True,
                )
                log.info(
                    "Discovered %d active paper bindings from runtime-manager",
                    len(bindings),
                )
            else:
                raw_bindings = os.getenv("PANTHEON_PAPER_PRODUCER_BINDINGS", "")
                if raw_bindings:
                    bindings = parse_bindings_env(raw_bindings)
                    log.info(
                        "Discovered %d bindings from "
                        "PANTHEON_PAPER_PRODUCER_BINDINGS env",
                        len(bindings),
                    )

            counts = producer.tick(bindings, now_iso) if bindings else {}
            degraded_bindings = producer.degraded_bindings
            if degraded_bindings:
                health["status"] = "degraded"
                health["last_failure_at"] = now_iso
                health["last_failure_reason"] = "; ".join(
                    f"{binding_id}: {reason}"
                    for binding_id, reason in sorted(degraded_bindings.items())
                )
            else:
                if startup_gap:
                    health["status"] = "degraded"
                    health["last_failure_at"] = now_iso
                    health["last_failure_reason"] = startup_gap
                else:
                    health["status"] = "ok"
                    health["last_success_at"] = now_iso
                    health["last_failure_reason"] = None
            health["active_binding_count"] = len(bindings)
            health["enqueued_signal_count"] = sum(counts.values())
            health["degraded_binding_count"] = len(degraded_bindings)
            health["degraded_bindings"] = degraded_bindings
        except Exception as exc:
            log.warning("paper_signal_producer tick failed: %s", exc)
            health["status"] = "degraded"
            health["last_failure_at"] = now_iso
            health["last_failure_reason"] = str(exc)
            health["active_binding_count"] = 0
            health["enqueued_signal_count"] = 0
            health["degraded_binding_count"] = 0
            health["degraded_bindings"] = {}
        health["ticks"] = tick
        health["last_tick_at"] = now_iso
        write_health(health_file, health)

        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval)


if __name__ == "__main__":  # pragma: no cover
    if sys.argv[1:] == ["healthcheck"]:
        raise SystemExit(healthcheck())
    if sys.argv[1:]:
        print(
            "usage: python -m services.execution.lean_runtime."
            "paper_signal_producer [healthcheck]",
            file=sys.stderr,
        )
        raise SystemExit(2)
    raise SystemExit(main())
