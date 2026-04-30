"""In-memory ingest manager for the first SD-03 governed source slice."""

from __future__ import annotations

from typing import Dict

from .connectors.base import (
    IngestRun,
    IngestRunStatus,
    SourceConnector,
    SourceEvidenceError,
)


class IngestManager:
    """Registers source connectors and tracks governed ingest-run lifecycles."""

    def __init__(self) -> None:
        self._connectors: Dict[str, SourceConnector] = {}
        self._runs: Dict[str, IngestRun] = {}

    def register_connector(self, connector: SourceConnector) -> SourceConnector:
        if connector.connector_id in self._connectors:
            raise SourceEvidenceError(f"Connector already registered: {connector.connector_id}")
        self._connectors[connector.connector_id] = connector
        return connector

    def upsert_connector(self, connector: SourceConnector) -> SourceConnector:
        self._connectors[connector.connector_id] = connector
        return connector

    def get_connector(self, connector_id: str) -> SourceConnector | None:
        return self._connectors.get(connector_id)

    def list_connectors(self) -> list[SourceConnector]:
        return list(self._connectors.values())

    def start_ingest_run(
        self,
        *,
        connector_id: str,
        trigger_type: str,
        trace_id: str,
    ) -> IngestRun:
        connector = self.get_connector(connector_id)
        if connector is None:
            raise SourceEvidenceError(f"Unknown connector: {connector_id}")
        run = IngestRun.new(
            connector_id=connector.connector_id,
            source_type=connector.source_type,
            trigger_type=trigger_type,
            trace_id=trace_id,
        )
        run.transition(IngestRunStatus.FETCHING, message="Ingest run started")
        self._runs[run.ingest_run_id] = run
        return run

    def get_run(self, ingest_run_id: str) -> IngestRun | None:
        return self._runs.get(ingest_run_id)

    def advance_run(self, ingest_run_id: str, next_status: IngestRunStatus | str, *, message: str | None = None) -> IngestRun:
        run = self.get_run(ingest_run_id)
        if run is None:
            raise SourceEvidenceError(f"Unknown ingest run: {ingest_run_id}")
        run.transition(next_status, message=message)
        return run

    def complete_run(
        self,
        ingest_run_id: str,
        *,
        raw_count: int,
        normalized_count: int,
        rejected_count: int = 0,
    ) -> IngestRun:
        run = self.get_run(ingest_run_id)
        if run is None:
            raise SourceEvidenceError(f"Unknown ingest run: {ingest_run_id}")
        run.raw_count = raw_count
        run.normalized_count = normalized_count
        run.rejected_count = rejected_count
        if run.status == IngestRunStatus.FETCHING:
            run.transition(IngestRunStatus.NORMALIZING, message="Raw source fetched")
        if run.status == IngestRunStatus.NORMALIZING:
            run.transition(IngestRunStatus.INDEXING, message="Source normalized")
        run.transition(IngestRunStatus.COMPLETED, message="Evidence indexed")
        return run

    def fail_run(self, ingest_run_id: str, *, message: str) -> IngestRun:
        run = self.get_run(ingest_run_id)
        if run is None:
            raise SourceEvidenceError(f"Unknown ingest run: {ingest_run_id}")
        run.transition(IngestRunStatus.FAILED, message=message)
        return run
