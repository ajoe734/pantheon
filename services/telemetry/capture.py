"""
Core telemetry capture logic for execution events.

TelemetryCapture manages the capture, validation, and storage of execution
telemetry events including pnl snapshots, drawdown observations, slippage,
and fill information. It maintains separate streams for paper and live execution.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from enum import Enum

try:
    import jsonschema
except ImportError:
    jsonschema = None

log = logging.getLogger(__name__)


class DeploymentStage(str, Enum):
    """Enum for deployment stages — mapped 1:1 from RuntimeBinding.deployment_mode."""
    PAPER = "paper"
    CANARY = "canary"
    LIVE = "live"
    FROZEN = "frozen"


class RollbackActionType(str, Enum):
    """Enum for canonical rollback action types."""
    REPLACE = "replace"
    PAUSE_THEN_REPLACE = "pause_then_replace"
    LIQUIDATE_THEN_REPLACE = "liquidate_then_replace"


class ExecutionMode(str, Enum):
    """Enum for execution modes (paper vs live)."""
    PAPER = "paper"
    LIVE = "live"


class EventType(str, Enum):
    """Enum for telemetry event types."""
    PNL_SNAPSHOT = "pnl_snapshot"
    DRAWDOWN_SNAPSHOT = "drawdown_snapshot"
    SLIPPAGE_OBSERVATION = "slippage_observation"
    FILL_OBSERVATION = "fill_observation"
    ORDER_REJECTION = "order_rejection"
    DEPLOY_STARTED = "deploy_started"
    DEPLOY_COMPLETED = "deploy_completed"
    ROLLBACK_STARTED = "rollback_started"
    ROLLBACK_COMPLETED = "rollback_completed"
    PAUSE_TRIGGERED = "pause_triggered"
    LIQUIDATE_TRIGGERED = "liquidate_triggered"
    GOVERNANCE_DECISION = "governance_decision"
    APPROVAL_ACTION = "approval_action"
    MANUAL_OVERRIDE = "manual_override"
    KILL_SWITCH_ACTION = "kill_switch_action"
    HEARTBEAT = "heartbeat"
    TELEMETRY_MIRROR_MISMATCH = "telemetry_mirror_mismatch"


class TelemetryCapture:
    """
    Captures and validates execution telemetry events.

    Maintains in-memory event buffers for paper and live modes separately,
    with optional persistence to disk. Each event is validated against
    the telemetry_event.schema.json before storage.

    TEL-001: Every event carries deployment-stage and runtime-binding evidence
    fields (binding_id, deployment_stage, plan_id, persona_capital_binding_id,
    rollback_parent, rollback_action_type) as defined in the TEL-001A evidence
    contract (E-1 through E-6).
    """

    def __init__(
        self,
        schema_path: Optional[str] = None,
        storage_dir: Optional[str] = None,
        binding_context: Optional[dict[str, Any]] = None,
    ):
        """
        Initialize telemetry capture.

        Parameters
        ----------
        schema_path : str, optional
            Path to telemetry_event.schema.json. If not provided,
            schema validation is skipped.
        storage_dir : str, optional
            Directory for persistent event storage. If provided, events are
            written to JSON files after validation.
        binding_context : dict, optional
            Active RuntimeBinding context to inject into every telemetry event.
            Expected keys: binding_id, runtime_id, capital_pool_id, artifact_id,
            artifact_version, deployment_stage, plan_id, persona_capital_binding_id.
            If not provided, binding fields will be omitted from events (legacy mode).
        """
        self.schema_path = schema_path
        self.storage_dir = Path(storage_dir) if storage_dir else None
        self.binding_context = binding_context or {}
        self.schema = None

        if self.storage_dir:
            self.storage_dir.mkdir(parents=True, exist_ok=True)

        if self.schema_path:
            self._load_schema()

        # In-memory buffers by mode
        self.events = {
            ExecutionMode.PAPER: [],
            ExecutionMode.LIVE: [],
        }

    def _load_schema(self) -> None:
        """Load and parse JSON schema."""
        try:
            with open(self.schema_path, "r") as f:
                self.schema = json.load(f)
            log.info(f"Loaded execution telemetry schema from {self.schema_path}")
        except FileNotFoundError:
            log.warning(f"Schema file not found: {self.schema_path}")
        except json.JSONDecodeError as e:
            log.error(f"Failed to parse schema: {e}")

    def _validate_event(self, event: dict[str, Any]) -> bool:
        """
        Validate event against schema.
        
        Parameters
        ----------
        event : dict
            Event payload to validate
            
        Returns
        -------
        bool
            True if valid or schema not loaded, False if invalid
        """
        if not self.schema or not jsonschema:
            return True
        
        try:
            jsonschema.validate(instance=event, schema=self.schema)
            return True
        except jsonschema.ValidationError as e:
            log.error(f"Event validation failed: {e.message}")
            return False
        except jsonschema.SchemaError as e:
            log.error(f"Schema error: {e.message}")
            return True

    def capture_pnl(
        self,
        mode: ExecutionMode,
        strategy_id: str,
        pnl_value: float,
        signal_id: Optional[str] = None,
        run_id: Optional[str] = None,
        broker: Optional[str] = None,
        account_ref: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Capture a PnL snapshot.
        
        Parameters
        ----------
        mode : ExecutionMode
            Paper or Live
        strategy_id : str
            ID of the strategy
        pnl_value : float
            PnL value
        signal_id : str, optional
            Associated signal ID
        run_id : str, optional
            Run/execution ID
        broker : str, optional
            Broker name
        account_ref : str, optional
            Account reference
        metadata : dict, optional
            Additional metadata
            
        Returns
        -------
        bool
            True if captured successfully
        """
        event = self._build_event(
            event_type=EventType.PNL_SNAPSHOT,
            mode=mode,
            strategy_id=strategy_id,
            signal_id=signal_id,
            run_id=run_id,
            broker=broker,
            account_ref=account_ref,
            metrics={"pnl": pnl_value},
            metadata=metadata,
        )
        return self._store_event(event, mode)

    def capture_drawdown(
        self,
        mode: ExecutionMode,
        strategy_id: str,
        drawdown_pct: float,
        signal_id: Optional[str] = None,
        run_id: Optional[str] = None,
        broker: Optional[str] = None,
        account_ref: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Capture a drawdown snapshot.
        
        Parameters
        ----------
        mode : ExecutionMode
            Paper or Live
        strategy_id : str
            ID of the strategy
        drawdown_pct : float
            Drawdown percentage
        signal_id : str, optional
            Associated signal ID
        run_id : str, optional
            Run/execution ID
        broker : str, optional
            Broker name
        account_ref : str, optional
            Account reference
        metadata : dict, optional
            Additional metadata
            
        Returns
        -------
        bool
            True if captured successfully
        """
        event = self._build_event(
            event_type=EventType.DRAWDOWN_SNAPSHOT,
            mode=mode,
            strategy_id=strategy_id,
            signal_id=signal_id,
            run_id=run_id,
            broker=broker,
            account_ref=account_ref,
            metrics={"drawdown_pct": drawdown_pct},
            metadata=metadata,
        )
        return self._store_event(event, mode)

    def capture_slippage(
        self,
        mode: ExecutionMode,
        strategy_id: str,
        slippage_bps: float,
        signal_id: Optional[str] = None,
        run_id: Optional[str] = None,
        broker: Optional[str] = None,
        account_ref: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Capture a slippage observation.
        
        Slippage is measured in basis points (bps), representing the difference
        between expected execution price and actual fill price.
        
        Parameters
        ----------
        mode : ExecutionMode
            Paper or Live
        strategy_id : str
            ID of the strategy
        slippage_bps : float
            Slippage in basis points
        signal_id : str, optional
            Associated signal ID
        run_id : str, optional
            Run/execution ID
        broker : str, optional
            Broker name
        account_ref : str, optional
            Account reference
        metadata : dict, optional
            Additional metadata
            
        Returns
        -------
        bool
            True if captured successfully
        """
        event = self._build_event(
            event_type=EventType.SLIPPAGE_OBSERVATION,
            mode=mode,
            strategy_id=strategy_id,
            signal_id=signal_id,
            run_id=run_id,
            broker=broker,
            account_ref=account_ref,
            metrics={"slippage_bps": slippage_bps},
            metadata=metadata,
        )
        return self._store_event(event, mode)

    def capture_fill(
        self,
        mode: ExecutionMode,
        strategy_id: str,
        fill_quantity: float,
        fill_price: float,
        signal_id: Optional[str] = None,
        run_id: Optional[str] = None,
        broker: Optional[str] = None,
        account_ref: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Capture a fill observation.
        
        Fill events record actual order executions with quantity and price.
        This is a key metric for evaluating execution quality and slippage.
        
        Parameters
        ----------
        mode : ExecutionMode
            Paper or Live
        strategy_id : str
            ID of the strategy
        fill_quantity : float
            Quantity filled
        fill_price : float
            Price at which filled
        signal_id : str, optional
            Associated signal ID
        run_id : str, optional
            Run/execution ID
        broker : str, optional
            Broker name
        account_ref : str, optional
            Account reference
        metadata : dict, optional
            Additional metadata
            
        Returns
        -------
        bool
            True if captured successfully
        """
        event = self._build_event(
            event_type=EventType.FILL_OBSERVATION,
            mode=mode,
            strategy_id=strategy_id,
            signal_id=signal_id,
            run_id=run_id,
            broker=broker,
            account_ref=account_ref,
            metrics={
                "fill_quantity": fill_quantity,
                "fill_price": fill_price,
            },
            metadata=metadata,
        )
        return self._store_event(event, mode)

    def capture_order_rejection(
        self,
        mode: ExecutionMode,
        strategy_id: str,
        reject_reason: str,
        signal_id: Optional[str] = None,
        run_id: Optional[str] = None,
        broker: Optional[str] = None,
        account_ref: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Capture an order rejection.
        
        Parameters
        ----------
        mode : ExecutionMode
            Paper or Live
        strategy_id : str
            ID of the strategy
        reject_reason : str
            Reason for rejection
        signal_id : str, optional
            Associated signal ID
        run_id : str, optional
            Run/execution ID
        broker : str, optional
            Broker name
        account_ref : str, optional
            Account reference
        metadata : dict, optional
            Additional metadata
            
        Returns
        -------
        bool
            True if captured successfully
        """
        event = self._build_event(
            event_type=EventType.ORDER_REJECTION,
            mode=mode,
            strategy_id=strategy_id,
            signal_id=signal_id,
            run_id=run_id,
            broker=broker,
            account_ref=account_ref,
            metrics={"reject_reason": reject_reason},
            metadata=metadata,
        )
        return self._store_event(event, mode)

    # ── TEL-001: Deployment / Rollback / Governance event capture ──

    def capture_deploy_started(
        self,
        mode: ExecutionMode,
        strategy_id: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Capture a deployment start event."""
        event = self._build_event(
            event_type=EventType.DEPLOY_STARTED,
            mode=mode,
            strategy_id=strategy_id,
            signal_id=None,
            run_id=None,
            broker=None,
            account_ref=None,
            metrics={"action": "deploy_started"},
            metadata=metadata,
        )
        return self._store_event(event, mode)

    def capture_deploy_completed(
        self,
        mode: ExecutionMode,
        strategy_id: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Capture a deployment completion event."""
        event = self._build_event(
            event_type=EventType.DEPLOY_COMPLETED,
            mode=mode,
            strategy_id=strategy_id,
            signal_id=None,
            run_id=None,
            broker=None,
            account_ref=None,
            metrics={"action": "deploy_completed"},
            metadata=metadata,
        )
        return self._store_event(event, mode)

    def capture_rollback_started(
        self,
        mode: ExecutionMode,
        strategy_id: str,
        rollback_parent: str,
        rollback_action_type: RollbackActionType,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Capture a rollback start event with lineage evidence (E-5)."""
        event = self._build_event(
            event_type=EventType.ROLLBACK_STARTED,
            mode=mode,
            strategy_id=strategy_id,
            signal_id=None,
            run_id=None,
            broker=None,
            account_ref=None,
            metrics={"action": "rollback_started"},
            metadata=metadata,
            rollback_parent=rollback_parent,
            rollback_action_type=rollback_action_type,
        )
        return self._store_event(event, mode)

    def capture_rollback_completed(
        self,
        mode: ExecutionMode,
        strategy_id: str,
        rollback_parent: str,
        rollback_action_type: RollbackActionType,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Capture a rollback completion event with lineage evidence (E-5)."""
        event = self._build_event(
            event_type=EventType.ROLLBACK_COMPLETED,
            mode=mode,
            strategy_id=strategy_id,
            signal_id=None,
            run_id=None,
            broker=None,
            account_ref=None,
            metrics={"action": "rollback_completed"},
            metadata=metadata,
            rollback_parent=rollback_parent,
            rollback_action_type=rollback_action_type,
        )
        return self._store_event(event, mode)

    def capture_kill_switch(
        self,
        mode: ExecutionMode,
        strategy_id: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Capture a kill switch activation event."""
        event = self._build_event(
            event_type=EventType.KILL_SWITCH_ACTION,
            mode=mode,
            strategy_id=strategy_id,
            signal_id=None,
            run_id=None,
            broker=None,
            account_ref=None,
            metrics={"action": "kill_switch_action"},
            metadata=metadata,
        )
        return self._store_event(event, mode)

    def capture_heartbeat(
        self,
        mode: ExecutionMode,
        strategy_id: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Capture a runtime heartbeat event."""
        event = self._build_event(
            event_type=EventType.HEARTBEAT,
            mode=mode,
            strategy_id=strategy_id,
            signal_id=None,
            run_id=None,
            broker=None,
            account_ref=None,
            metrics={"heartbeat": 1},
            metadata=metadata,
        )
        return self._store_event(event, mode)

    def _build_event(
        self,
        event_type: EventType,
        mode: ExecutionMode,
        strategy_id: str,
        signal_id: Optional[str],
        run_id: Optional[str],
        broker: Optional[str],
        account_ref: Optional[str],
        metrics: dict[str, Any],
        metadata: Optional[dict[str, Any]],
        rollback_parent: Optional[str] = None,
        rollback_action_type: Optional[RollbackActionType] = None,
    ) -> dict[str, Any]:
        """Build event object from parameters.

        TEL-001: Injects binding/stage evidence fields from the active
        RuntimeBinding context (binding_id, deployment_stage, plan_id, etc.)
        into every telemetry event envelope.

        TEL-001 Fix #2: When binding_context is present, ALL required evidence
        fields MUST be populated. Missing fields cause explicit rejection rather
        than backfilling with defaults/empty strings, which would fabricate
        evidence and violate the TEL-001A evidence contract (E-1).
        """
        # Derive deployment_stage from binding_context or fall back to mode
        binding_deployment_stage = self.binding_context.get("deployment_stage")
        if binding_deployment_stage:
            deployment_stage = binding_deployment_stage
        else:
            # Legacy fallback: derive from execution_mode
            deployment_stage = mode.value

        # Derive execution_mode alias from deployment_stage for backward compat
        if deployment_stage in ("canary", "live"):
            execution_mode_alias = "live"
        elif deployment_stage == "frozen":
            # frozen is a governance stage, not an execution mode;
            # map to "paper" since frozen bindings are not actively trading
            execution_mode_alias = "paper"
        else:
            execution_mode_alias = deployment_stage

        event: dict[str, Any] = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type.value,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "execution_mode": execution_mode_alias,
            "environment": deployment_stage,
            "deployment_stage": deployment_stage,
            "target": {
                "strategy_id": strategy_id,
            },
            "metrics": metrics,
        }

        # TEL-001: Inject binding/stage evidence fields from active RuntimeBinding context
        if self.binding_context:
            binding_id = self.binding_context.get("binding_id")
            runtime_id = self.binding_context.get("runtime_id")
            capital_pool_id = self.binding_context.get("capital_pool_id")
            artifact_id = self.binding_context.get("artifact_id")
            artifact_version = self.binding_context.get("artifact_version")
            plan_id = self.binding_context.get("plan_id")
            persona_capital_binding_id = self.binding_context.get("persona_capital_binding_id")
            ctx_deployment_stage = self.binding_context.get("deployment_stage", deployment_stage)

            # Strict validation: require minimal binding identity (E-1)
            required_evidence = {
                "binding_id": binding_id,
                "runtime_id": runtime_id,
                "capital_pool_id": capital_pool_id,
                "artifact_id": artifact_id,
                "artifact_version": artifact_version,
                "deployment_stage": ctx_deployment_stage,
                "plan_id": plan_id,
                "persona_capital_binding_id": persona_capital_binding_id,
            }
            missing = [k for k, v in required_evidence.items() if not v]
            if missing:
                log.error(
                    f"binding_context present but missing required evidence fields: {missing}. "
                    f"Rejecting event {event_type.value} — no fabricated defaults."
                )
                return None  # Signal rejection

            event["binding_id"] = binding_id
            event["runtime_id"] = runtime_id
            event["capital_pool_id"] = capital_pool_id
            event["artifact_id"] = artifact_id
            event["artifact_version"] = artifact_version
            event["deployment_stage"] = ctx_deployment_stage
            event["plan_id"] = plan_id
            event["persona_capital_binding_id"] = persona_capital_binding_id
            # environment MUST equal deployment_stage per TEL-001A evidence contract
            event["environment"] = event["deployment_stage"]
            # Update execution_mode alias to match deployment_stage
            if event["deployment_stage"] in ("canary", "live"):
                event["execution_mode"] = "live"
            elif event["deployment_stage"] == "frozen":
                event["execution_mode"] = "paper"
            else:
                event["execution_mode"] = event["deployment_stage"]

        # Rollback lineage evidence (Evidence E-5)
        if rollback_parent is not None:
            event["rollback_parent"] = rollback_parent
        if rollback_action_type is not None:
            event["rollback_action_type"] = rollback_action_type.value

        if signal_id:
            event["signal_id"] = signal_id
        if run_id:
            event["run_id"] = run_id
        if broker:
            event["broker"] = broker
        if account_ref:
            event["account_ref"] = account_ref

        if metadata:
            # Merge additional metrics if provided via metadata (non-mutating)
            if "metrics" in metadata:
                event["metrics"].update(metadata["metrics"])

            # Preserve governed linkage fields in target object (non-mutating)
            # (registry_id, artifact_version, artifact_type, promotion_state, lineage_ref)
            for linkage_field in ["registry_id", "artifact_version", "artifact_type", "promotion_state", "lineage_ref"]:
                if linkage_field in metadata:
                    event["target"][linkage_field] = metadata[linkage_field]

        return event

    def _store_event(self, event: Optional[dict[str, Any]], mode: ExecutionMode) -> bool:
        """
        Validate and store event.

        Returns True if stored successfully, False otherwise.
        Returns False immediately if event is None (build-time rejection).
        """
        if event is None:
            return False

        if not self._validate_event(event):
            log.error(f"Event validation failed; skipping storage: {event.get('event_id')}")
            return False
        
        self.events[mode].append(event)
        
        if self.storage_dir:
            self._persist_event(event)
        
        log.debug(f"Captured {event['event_type']} in {mode.value} mode")
        return True

    def _persist_event(self, event: dict[str, Any]) -> None:
        """Write event to persistent storage."""
        try:
            mode = event["execution_mode"]
            mode_dir = self.storage_dir / mode
            mode_dir.mkdir(parents=True, exist_ok=True)
            
            event_file = mode_dir / f"{event['event_id']}.json"
            with open(event_file, "w") as f:
                json.dump(event, f, indent=2)
        except Exception as e:
            log.error(f"Failed to persist event: {e}")

    def get_events(self, mode: Optional[ExecutionMode] = None) -> list[dict[str, Any]]:
        """
        Get captured events.
        
        Parameters
        ----------
        mode : ExecutionMode, optional
            If provided, return only events from this mode. Otherwise return all.
            
        Returns
        -------
        list[dict]
            List of event dictionaries
        """
        if mode is not None:
            return self.events[mode]
        
        return self.events[ExecutionMode.PAPER] + self.events[ExecutionMode.LIVE]

    def get_paper_events(self) -> list[dict[str, Any]]:
        """Get paper trading events."""
        return self.events[ExecutionMode.PAPER]

    def get_live_events(self) -> list[dict[str, Any]]:
        """Get live trading events."""
        return self.events[ExecutionMode.LIVE]

    def clear_events(self, mode: Optional[ExecutionMode] = None) -> None:
        """
        Clear captured events.
        
        Parameters
        ----------
        mode : ExecutionMode, optional
            If provided, clear only this mode. Otherwise clear all.
        """
        if mode is not None:
            self.events[mode].clear()
        else:
            self.events[ExecutionMode.PAPER].clear()
            self.events[ExecutionMode.LIVE].clear()
