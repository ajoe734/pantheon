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

import logging
import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from services.execution.artifact_loader import (
    ArtifactLoadError,
    ArtifactLoader,
    ExecutionMode,
)

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
    return {
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
                if closes and isinstance(closes, Sequence) and len(closes) >= 2:
                    action = evaluate_strategy_action(strategy_artifact, closes)
                    decision["action"] = action
                    if action == "SELL":
                        decision["direction"] = "SHORT"
            except Exception as exc:
                log.warning("Failed to evaluate strategy_artifact for binding %s: %s", binding_id, exc)

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

    def __init__(self) -> None:
        self._seq = 0

    def __call__(self, binding: Any, now_iso: str) -> dict[str, Any]:
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
        if interpreter != self._SUPPORTED_INTERPRETER:
            raise SignalDecisionUnavailable(
                "artifact_interpreter_unsupported",
                f"binding {binding_id} requested unsupported interpreter {interpreter!r}",
            )

        market_input = _market_input_for_binding(b_dict, metadata)
        closes = market_input["closes"]
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
            action = evaluate_strategy_action(strategy_artifact, closes)
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

        self._seq += 1
        persona_capital_binding_id = b_dict.get("persona_capital_binding_id")
        direction = "SHORT" if action == "SELL" else "LONG"
        return {
            "decision_id": f"dec-{binding_id}-{now_iso}-{self._seq}",
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
            "run_id": f"run-{binding_id}-{now_iso}-{self._seq}",
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
                "market_input_ref": market_input.get("source_ref"),
                "market_input_observed_at": market_input.get("observed_at"),
                "is_real_order": False,
                "is_real_capital": False,
            },
        }

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


def _required_binding_text(binding: Mapping[str, Any], field: str) -> str:
    value = str(binding.get(field) or "").strip()
    if not value:
        binding_id = str(binding.get("binding_id") or "<unknown>")
        raise SignalDecisionUnavailable(
            "binding_identity_missing",
            f"binding {binding_id} has no {field}",
        )
    return value


def _market_input_for_binding(
    binding: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    raw = binding.get("market_input") or metadata.get("market_input")
    if raw is None:
        closes = binding.get("recent_closes") or metadata.get("recent_closes")
        if closes is not None:
            raw = {
                "closes": closes,
                "source_ref": binding.get("market_input_ref")
                or metadata.get("market_input_ref")
                or "runtime-binding:recent_closes",
            }
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
    return {
        "closes": list(closes),
        "symbol": raw.get("symbol"),
        "source_ref": raw.get("source_ref"),
        "observed_at": raw.get("observed_at"),
    }


def _strategy_symbol(
    binding: Mapping[str, Any],
    strategy_artifact: Mapping[str, Any],
    market_input: Mapping[str, Any],
) -> str:
    symbol = str(market_input.get("symbol") or binding.get("symbol") or "").strip()
    parameters = strategy_artifact.get("parameters")
    symbols = parameters.get("symbols") if isinstance(parameters, Mapping) else None
    if not symbol and isinstance(symbols, Sequence) and not isinstance(symbols, (str, bytes)):
        symbol = str(symbols[0] if symbols else "").strip()
    if not symbol:
        binding_id = str(binding.get("binding_id") or "<unknown>")
        raise SignalDecisionUnavailable(
            "market_input_missing",
            f"binding {binding_id} has no market symbol",
        )
    if (
        isinstance(symbols, Sequence)
        and not isinstance(symbols, (str, bytes))
        and symbol not in symbols
    ):
        binding_id = str(binding.get("binding_id") or "<unknown>")
        raise SignalDecisionUnavailable(
            "market_input_invalid",
            f"binding {binding_id} market symbol {symbol!r} is outside artifact symbols",
        )
    return symbol


def _binding_dict_from_obj(binding: Any) -> dict[str, Any]:
    if isinstance(binding, dict):
        return binding
    return {
        "binding_id": getattr(binding, "binding_id", ""),
        "runtime_id": getattr(binding, "runtime_id", "rt-default"),
        "capital_pool_id": getattr(binding, "capital_pool_id", "pool-default"),
        "artifact_id": getattr(binding, "artifact_id", "art-default"),
        "artifact_version": getattr(binding, "artifact_version", "1.0.0"),
        "persona_capital_binding_id": getattr(binding, "persona_capital_binding_id", "pcb-default-001"),
        "symbol": getattr(binding, "symbol", "AAPL.US"),
        "strategy_id": getattr(binding, "strategy_id", "smoke-strategy"),
        "tenant_id": getattr(binding, "tenant_id", "default"),
        "environment": getattr(binding, "environment", "paper"),
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
        store = self._store_for(binding)

        enqueued = 0
        if isinstance(out, list):
            # Legacy list-of-signals flow
            for sig in out:
                if "metadata" not in sig:
                    sig["metadata"] = {}
                if isinstance(sig["metadata"], dict):
                    sig["metadata"]["is_real_order"] = False
                    sig["metadata"]["is_real_capital"] = False
                _paper_signal_identity(sig, b_dict, now_iso)
                store.enqueue(sig)
                enqueued += 1
        elif isinstance(out, dict):
            # First-class decision dict flow
            from services.execution.lean_runtime.signal_producer import DecisionSignalProducer
            dsp = DecisionSignalProducer(store)

            if "metadata" not in out:
                out["metadata"] = {}
            if isinstance(out["metadata"], dict):
                out["metadata"]["is_real_order"] = False
                out["metadata"]["is_real_capital"] = False
                binding_metadata = b_dict.get("metadata") if isinstance(b_dict.get("metadata"), Mapping) else {}
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
        bindings = payload.get("bindings", [])
        # Only return eligible active paper bindings
        return [b for b in bindings if b.get("status") == "active"]
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
        return CurrentArtifactStrategy()
    if profile == "smoke":
        return BoundedPaperStrategy()
    raise ValueError(
        "PAPER_SIGNAL_STRATEGY must be 'artifact' (default) or explicit 'smoke'; "
        f"got {profile!r}."
    )


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
    producer = PaperSignalProducer(
        store_for=_redis_store_factory(signal_store_url), strategy=_runner_strategy()
    )
    log.info("paper_signal_producer started; interval=%.0fs", interval)
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
