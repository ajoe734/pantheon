"""JSONL store for PersonaAllocationProposal records.

MGMT-SYN-002 owns durable proposal ingestion/replay for the optimizer service.
The store is intentionally narrow: it records immutable proposal snapshots and
returns ``portfolio_synthesis.PersonaAllocationProposal`` instances that can be
fed directly into the existing synthesizer.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from portfolio_synthesis import PersonaAllocationProposal, SynthesisError


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROPOSAL_STORE_PATH = ROOT / ".orchestrator" / "optimizer" / "persona_allocation_proposals.jsonl"

PERSONA_ALLOCATION_PROPOSAL_RECORD_SCHEMA_VERSION = "persona_allocation_proposal_record.v1"
PROPOSAL_SNAPSHOT = "proposal_snapshot"
SUPPORTED_RECORD_TYPES = {PROPOSAL_SNAPSHOT}


class PersonaAllocationProposalStoreError(ValueError):
    """Raised when the proposal store cannot accept or replay a record."""


@dataclass(frozen=True)
class PersonaAllocationProposalQuery:
    proposal_ids: Sequence[str] | None = None
    persona_id: str | None = None
    capital_pool_id: str | None = None
    scope_ref: str | None = None
    target_type: str | None = None
    limit: int | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _require_text(value: Any, field_name: str) -> str:
    if value in (None, ""):
        raise PersonaAllocationProposalStoreError(f"{field_name} is required")
    normalized = str(value).strip()
    if not normalized:
        raise PersonaAllocationProposalStoreError(f"{field_name} is required")
    return normalized


def _proposal_to_payload(proposal: PersonaAllocationProposal | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(proposal, PersonaAllocationProposal) or is_dataclass(proposal):
        payload = asdict(proposal)
    elif isinstance(proposal, Mapping):
        payload = _json_copy(proposal)
    else:
        raise PersonaAllocationProposalStoreError("proposal must be a PersonaAllocationProposal or mapping")

    try:
        normalized = PersonaAllocationProposal(**payload)
    except SynthesisError as exc:
        raise PersonaAllocationProposalStoreError(str(exc)) from exc
    except TypeError as exc:
        raise PersonaAllocationProposalStoreError(str(exc)) from exc

    return _json_copy(asdict(normalized))


def _proposal_from_payload(payload: Mapping[str, Any]) -> PersonaAllocationProposal:
    try:
        return PersonaAllocationProposal(**_json_copy(payload))
    except SynthesisError as exc:
        raise PersonaAllocationProposalStoreError(str(exc)) from exc
    except TypeError as exc:
        raise PersonaAllocationProposalStoreError(str(exc)) from exc


class PersonaAllocationProposalJsonlStore:
    """Append/replay store for immutable PersonaAllocationProposal snapshots."""

    def __init__(self, path: str | Path = DEFAULT_PROPOSAL_STORE_PATH) -> None:
        self.path = Path(path)
        self._records: list[dict[str, Any]] = []
        self._record_by_proposal_id: dict[str, dict[str, Any]] = {}
        self._proposal_payloads: dict[str, dict[str, Any]] = {}
        self._proposal_sequences: dict[str, int] = {}
        self.reload()

    def reload(self) -> None:
        self._records.clear()
        self._record_by_proposal_id.clear()
        self._proposal_payloads.clear()
        self._proposal_sequences.clear()

        if not self.path.exists():
            return

        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            payload = line.strip()
            if not payload:
                continue
            try:
                record = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise PersonaAllocationProposalStoreError(
                    f"Invalid proposal JSONL at {self.path}:{line_number}: {exc.msg}"
                ) from exc
            try:
                self._apply_record(record)
            except PersonaAllocationProposalStoreError as exc:
                raise PersonaAllocationProposalStoreError(
                    f"Invalid proposal record at {self.path}:{line_number}: {exc}"
                ) from exc

    def append_proposal(self, proposal: PersonaAllocationProposal | Mapping[str, Any]) -> dict[str, Any]:
        """Append a proposal snapshot and return its durable envelope.

        Replays are idempotent for byte-equivalent proposal payloads. A reused
        ``proposal_id`` with different content is rejected as a write conflict.
        """

        proposal_payload = _proposal_to_payload(proposal)
        proposal_id = _require_text(proposal_payload.get("proposal_id"), "proposal.proposal_id")

        existing_payload = self._proposal_payloads.get(proposal_id)
        if existing_payload is not None:
            if existing_payload == proposal_payload:
                return _json_copy(self._record_by_proposal_id[proposal_id])
            raise PersonaAllocationProposalStoreError(
                f"proposal_id {proposal_id!r} already exists with different payload"
            )

        record = self._build_record(proposal_payload)
        self._append_record(record)
        return _json_copy(record)

    def get_proposal(self, proposal_id: str) -> PersonaAllocationProposal | None:
        payload = self._proposal_payloads.get(proposal_id)
        if payload is None:
            return None
        return _proposal_from_payload(payload)

    def require_proposals(self, proposal_ids: Sequence[str]) -> list[PersonaAllocationProposal]:
        proposals: list[PersonaAllocationProposal] = []
        missing: list[str] = []
        for proposal_id in proposal_ids:
            proposal = self.get_proposal(proposal_id)
            if proposal is None:
                missing.append(proposal_id)
                continue
            proposals.append(proposal)
        if missing:
            raise PersonaAllocationProposalStoreError(f"Unknown proposal_id(s): {', '.join(missing)}")
        return proposals

    def list_proposals(
        self,
        query: PersonaAllocationProposalQuery | None = None,
    ) -> list[PersonaAllocationProposal]:
        query = query or PersonaAllocationProposalQuery()
        requested_ids = list(query.proposal_ids or [])

        if requested_ids:
            candidates = [
                self._proposal_payloads[proposal_id]
                for proposal_id in requested_ids
                if proposal_id in self._proposal_payloads
            ]
        else:
            candidates = sorted(
                self._proposal_payloads.values(),
                key=lambda payload: self._proposal_sequences.get(str(payload.get("proposal_id")), -1),
                reverse=True,
            )

        matched: list[PersonaAllocationProposal] = []
        for payload in candidates:
            if not self._matches(payload, query):
                continue
            matched.append(_proposal_from_payload(payload))
            if query.limit is not None and len(matched) >= query.limit:
                break
        return matched

    def iter_records(self) -> Iterator[dict[str, Any]]:
        for record in self._records:
            yield _json_copy(record)

    def _append_record(self, record: Mapping[str, Any]) -> None:
        record_payload = _json_copy(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        self._apply_record(record_payload)

    def _build_record(self, proposal_payload: Mapping[str, Any]) -> dict[str, Any]:
        proposal_id = _require_text(proposal_payload.get("proposal_id"), "proposal.proposal_id")
        return {
            "schema_version": PERSONA_ALLOCATION_PROPOSAL_RECORD_SCHEMA_VERSION,
            "record_type": PROPOSAL_SNAPSHOT,
            "record_id": f"pap-rec-{uuid.uuid4()}",
            "proposal_id": proposal_id,
            "persona_id": _require_text(proposal_payload.get("persona_id"), "proposal.persona_id"),
            "capital_pool_id": _require_text(proposal_payload.get("capital_pool_id"), "proposal.capital_pool_id"),
            "scope_ref": _require_text(proposal_payload.get("scope_ref"), "proposal.scope_ref"),
            "recorded_at": utc_now(),
            "payload": {"proposal": _json_copy(proposal_payload)},
        }

    def _apply_record(self, record: Mapping[str, Any]) -> None:
        if record.get("schema_version") != PERSONA_ALLOCATION_PROPOSAL_RECORD_SCHEMA_VERSION:
            raise PersonaAllocationProposalStoreError(
                f"schema_version must be {PERSONA_ALLOCATION_PROPOSAL_RECORD_SCHEMA_VERSION}"
            )
        record_type = _require_text(record.get("record_type"), "record_type")
        if record_type not in SUPPORTED_RECORD_TYPES:
            raise PersonaAllocationProposalStoreError(f"Unsupported record_type: {record_type}")
        proposal_id = _require_text(record.get("proposal_id"), "proposal_id")
        _require_text(record.get("record_id"), "record_id")
        _require_text(record.get("recorded_at"), "recorded_at")

        payload = record.get("payload")
        if not isinstance(payload, Mapping):
            raise PersonaAllocationProposalStoreError("payload must be an object")
        proposal_payload = payload.get("proposal")
        if not isinstance(proposal_payload, Mapping):
            raise PersonaAllocationProposalStoreError("payload.proposal must be an object")

        normalized_payload = _proposal_to_payload(proposal_payload)
        if normalized_payload["proposal_id"] != proposal_id:
            raise PersonaAllocationProposalStoreError("payload.proposal.proposal_id must match proposal_id")
        for field_name in ("persona_id", "capital_pool_id", "scope_ref"):
            record_value = _require_text(record.get(field_name), field_name)
            proposal_value = _require_text(normalized_payload.get(field_name), f"payload.proposal.{field_name}")
            if record_value != proposal_value:
                raise PersonaAllocationProposalStoreError(
                    f"payload.proposal.{field_name} must match {field_name}"
                )

        existing_payload = self._proposal_payloads.get(proposal_id)
        if existing_payload is not None and existing_payload != normalized_payload:
            raise PersonaAllocationProposalStoreError(
                f"proposal_id {proposal_id!r} already exists with different payload"
            )

        record_payload = _json_copy(record)
        sequence = len(self._records)
        self._records.append(record_payload)
        self._record_by_proposal_id[proposal_id] = record_payload
        self._proposal_payloads[proposal_id] = normalized_payload
        self._proposal_sequences[proposal_id] = sequence

    def _matches(self, payload: Mapping[str, Any], query: PersonaAllocationProposalQuery) -> bool:
        if query.persona_id is not None and payload.get("persona_id") != query.persona_id:
            return False
        if query.capital_pool_id is not None and payload.get("capital_pool_id") != query.capital_pool_id:
            return False
        if query.scope_ref is not None and payload.get("scope_ref") != query.scope_ref:
            return False
        if query.target_type is not None and payload.get("target_type") != query.target_type:
            return False
        return True


def ingest_persona_proposal(
    proposal: PersonaAllocationProposal | Mapping[str, Any],
    *,
    store: PersonaAllocationProposalJsonlStore | None = None,
) -> dict[str, Any]:
    """Persist a proposal using the default optimizer-svc proposal store."""

    target_store = store or PersonaAllocationProposalJsonlStore()
    return target_store.append_proposal(proposal)
