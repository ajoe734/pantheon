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


class TelemetryCapture:
    """
    Captures and validates execution telemetry events.
    
    Maintains in-memory event buffers for paper and live modes separately,
    with optional persistence to disk. Each event is validated against
    the execution_telemetry_event.schema.json before storage.
    """

    def __init__(self, schema_path: Optional[str] = None, storage_dir: Optional[str] = None):
        """
        Initialize telemetry capture.
        
        Parameters
        ----------
        schema_path : str, optional
            Path to execution_telemetry_event.schema.json. If not provided,
            schema validation is skipped.
        storage_dir : str, optional
            Directory for persistent event storage. If provided, events are
            written to JSON files after validation.
        """
        self.schema_path = schema_path
        self.storage_dir = Path(storage_dir) if storage_dir else None
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
    ) -> dict[str, Any]:
        """Build event object from parameters."""
        event = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type.value,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "execution_mode": mode.value,
            "target": {
                "strategy_id": strategy_id,
            },
            "metrics": metrics,
        }
        
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

    def _store_event(self, event: dict[str, Any], mode: ExecutionMode) -> bool:
        """
        Validate and store event.
        
        Returns True if stored successfully, False otherwise.
        """
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
