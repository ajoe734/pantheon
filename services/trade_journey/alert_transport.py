"""TJ-E2E-011 governed alert transport for `DataQualityIncident`.

Review feedback on PR #3460 found that `slo_data_quality.evaluate_data_quality`
produced `DataQualityIncident` records that nothing ever consumed: no alert
transport existed, so an emitted incident had no durable path to an operator.

This module closes that gap by publishing each incident onto the same shared
outbox primitive other Pantheon services already use for durable,
at-least-once event delivery (`services/foundation/outbox.py`, see
`CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`) instead of inventing a new
ad-hoc alert bus. The BFF SLO endpoint
(`services/control-plane/bff/trade_journeys.py`) calls
`DataQualityAlertTransport.publish_incidents()` on every evaluation, so every
incident reaching an operator has a corresponding append-only outbox record
an on-call responder or downstream alert consumer can replay.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Sequence, Tuple

from services.foundation import (
    EnvironmentName,
    EnvironmentScope,
    EventEnvelope,
    JsonlOutboxStore,
    OutboxRecord,
    TraceContext,
)
from services.trade_journey.slo_data_quality import DataQualityIncident, incident_to_dict

DATA_DIR_ENV = "PANTHEON_TRADE_JOURNEY_SLO_DATA_DIR"
DEFAULT_DATA_DIR = "/tmp/pantheon/trade_journey_slo"
OUTBOX_FILE_NAME = "slo_alerts_outbox.jsonl"
OWNER_SERVICE = "trade_journey_slo_monitor"
AGGREGATE_TYPE = "trade_journey_slo_incident"

# `EventEnvelope.trace` requires a foundation `EnvironmentName`, but Trade
# Journey incidents carry the Trade Journey environment vocabulary
# (`trade_journeys.py::_ALLOWED_ENVIRONMENTS`), which includes
# `broker_sandbox` — a value foundation has no equivalent for. The mapping
# below is for the trace/envelope scope only; the original Trade Journey
# environment string is preserved untouched inside the payload via
# `incident_to_dict`, so no information is lost by this fallback.
_ENVIRONMENT_NAME_BY_TRADE_JOURNEY_ENVIRONMENT: Mapping[str, EnvironmentName] = {
    "paper": EnvironmentName.PAPER,
    "broker_sandbox": EnvironmentName.SANDBOX,
    "canary": EnvironmentName.CANARY,
    "live": EnvironmentName.LIVE,
}


def _environment_name(environment: str | None) -> EnvironmentName:
    return _ENVIRONMENT_NAME_BY_TRADE_JOURNEY_ENVIRONMENT.get(environment or "", EnvironmentName.SANDBOX)


class DataQualityAlertTransport:
    """Governed alert path: `DataQualityIncident` -> foundation outbox record."""

    def __init__(self, *, data_dir: str | Path | None = None) -> None:
        base_dir = Path(data_dir if data_dir is not None else os.getenv(DATA_DIR_ENV, DEFAULT_DATA_DIR))
        self._store = JsonlOutboxStore(base_dir / OUTBOX_FILE_NAME)
        self._sequence = 0

    @property
    def outbox_path(self) -> Path:
        return self._store.path

    def publish_incident(self, incident: DataQualityIncident) -> OutboxRecord:
        self._sequence += 1
        trace = TraceContext.new(
            environment=EnvironmentScope(name=_environment_name(incident.environment)),
            source_system=OWNER_SERVICE,
        )
        aggregate_id = incident.journey_id or f"aggregate:{incident.environment or 'unknown'}"
        event = EventEnvelope.new(
            event_type=incident.alert_path.event_type,
            aggregate_type=AGGREGATE_TYPE,
            aggregate_id=aggregate_id,
            sequence_no=self._sequence,
            trace=trace,
            payload=incident_to_dict(incident),
            producer_service=OWNER_SERVICE,
        )
        record = OutboxRecord.new(owner_service=OWNER_SERVICE, event=event).mark_published()
        self._store.append(record)
        return record

    def publish_incidents(self, incidents: Sequence[DataQualityIncident]) -> Tuple[OutboxRecord, ...]:
        return tuple(self.publish_incident(incident) for incident in incidents)

    def load_published(self) -> Tuple[OutboxRecord, ...]:
        return tuple(self._store.load())


SLO_ALERT_TRANSPORT = DataQualityAlertTransport()
