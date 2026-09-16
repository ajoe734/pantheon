"""Typed knowledge-read fixtures shared by the KW-01 through KW-05 contracts.

The contract modules exercise the BFF through :class:`ReadSurfacePorts` while
keeping their records and source-state decisions test-owned.  The environment
factory intentionally understands only the owner-store paths used by these
five contracts; it is not a production store resolver.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

_BFF_DIR = Path(__file__).resolve().parent.parent
if str(_BFF_DIR) not in sys.path:
    sys.path.insert(0, str(_BFF_DIR))

from ports import (  # noqa: E402
    DefaultResearchKnowledgeSourcePort,
    ReadSurfacePorts,
    create_in_memory_persona_capital_runtime_port,
)


class _InstitutionalMemoryStoreDouble:
    """Small owner-store double matching the typed memory read interface."""

    def __init__(self, records: Mapping[str, Dict[str, Any]]) -> None:
        self._records = _clone_records(records)

    def list(self, *, active_only: bool = False) -> list[Dict[str, Any]]:
        del active_only
        return [_clone(record) for record in self._records.values()]

    def get(self, entry_id: str) -> Optional[Dict[str, Any]]:
        record = self._records.get(str(entry_id))
        return _clone(record) if record is not None else None


class KnowledgeReadPortsDouble(ReadSurfacePorts):
    """Typed read-port container with explicit dataset truth for contract tests."""

    def __init__(
        self,
        *,
        research_port: DefaultResearchKnowledgeSourcePort,
        dataset_sources: Mapping[str, str],
        personas: Iterable[Dict[str, Any]] = (),
    ) -> None:
        super().__init__(
            research_knowledge_source=research_port,
            persona_capital_runtime=create_in_memory_persona_capital_runtime_port(
                personas=list(personas)
            ),
        )
        self._knowledge_dataset_sources = dict(dataset_sources)

    def dataset_source(self, dataset: str, **options: Any) -> str:
        source = self._knowledge_dataset_sources.get(dataset)
        if source is None:
            return super().dataset_source(dataset)
        if source == "local_snapshot" and (
            options.get("include_snapshot_fallback") is False
            or options.get("include_local_fallback") is False
        ):
            return "missing"
        return source

    def get_institutional_memory_entry(
        self,
        entry_id: Optional[str],
        **options: Any,
    ) -> Optional[Dict[str, Any]]:
        if (
            self._knowledge_dataset_sources.get("institutional_memory_entries")
            == "local_snapshot"
            and options.get("include_snapshot_fallback") is False
        ):
            return None
        return self.research_knowledge_source.get_institutional_memory_entry(entry_id)

    def create_research_note(self, note: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Expose the KW-02 write only on this test double."""
        return self.research_knowledge_source.create_research_note(note)


def create_knowledge_read_ports(
    *,
    dataset_sources: Optional[Mapping[str, str]] = None,
    research_notes: Optional[Mapping[str, Dict[str, Any]]] = None,
    evidence_refs: Optional[Mapping[str, Dict[str, Any]]] = None,
    insight_cards: Optional[Mapping[str, Dict[str, Any]]] = None,
    strategy_specs: Optional[Mapping[str, Dict[str, Any]]] = None,
    research_tickets: Optional[Mapping[str, Dict[str, Any]]] = None,
    research_experiments: Optional[Mapping[str, Dict[str, Any]]] = None,
    institutional_memory_entries: Optional[Mapping[str, Dict[str, Any]]] = None,
    personas: Optional[Mapping[str, Dict[str, Any]]] = None,
) -> KnowledgeReadPortsDouble:
    """Build a typed test container from explicitly supplied owner records."""
    memory_store = (
        _InstitutionalMemoryStoreDouble(institutional_memory_entries)
        if institutional_memory_entries is not None
        else None
    )
    research_port = DefaultResearchKnowledgeSourcePort(
        institutional_memory_store=memory_store,
        research_notes_store=_clone_records(research_notes or {}),
        evidence_refs_store=_clone_records(evidence_refs or {}),
        insight_cards_store=_clone_records(insight_cards or {}),
        strategy_specs_store=_clone_records(strategy_specs or {}),
        research_tickets_store=_clone_records(research_tickets or {}),
        research_experiments_store=_clone_records(research_experiments or {}),
    )
    return KnowledgeReadPortsDouble(
        research_port=research_port,
        dataset_sources=dataset_sources or {},
        personas=(personas or {}).values(),
    )


_ENV_DATASETS = {
    "research_notes": ("PANTHEON_BFF_RESEARCH_NOTES_STORE", ("note_id", "id")),
    "evidence_refs": ("PANTHEON_BFF_EVIDENCE_REF_STORE", ("ref_id", "id")),
    "insight_cards": ("PANTHEON_BFF_INSIGHT_CARD_STORE", ("insight_id", "id")),
    "strategy_specs": ("PANTHEON_BFF_STRATEGY_SPEC_STORE", ("strategy_id", "id")),
    "research_tickets": ("PANTHEON_BFF_RESEARCH_TICKET_STORE", ("ticket_id", "id")),
    "research_experiments": (
        "PANTHEON_BFF_RESEARCH_EXPERIMENT_STORE",
        ("experiment_id", "id"),
    ),
    "personas": ("PANTHEON_BFF_PERSONA_REGISTRY_STORE", ("persona_id", "id")),
}


def create_environment_knowledge_read_ports() -> KnowledgeReadPortsDouble:
    """Load the owner-store files declared by a KW contract into typed ports."""
    datasets: Dict[str, Dict[str, Dict[str, Any]]] = {}
    sources: Dict[str, str] = {}
    for dataset, (env_name, key_fields) in _ENV_DATASETS.items():
        raw_path = os.getenv(env_name)
        if not raw_path:
            continue
        datasets[dataset] = _load_records(Path(raw_path), dataset, key_fields)
        if dataset != "personas":
            sources[dataset] = "typed_store"

    memory_path = _institutional_memory_path()
    memory_records: Optional[Dict[str, Dict[str, Any]]] = None
    if memory_path is not None:
        memory_records = _load_records(
            memory_path,
            "institutional_memory_entries",
            ("entry_id", "id"),
        )
        sources["institutional_memory_entries"] = "typed_store"

    return create_knowledge_read_ports(
        dataset_sources=sources,
        research_notes=datasets.get("research_notes"),
        evidence_refs=datasets.get("evidence_refs"),
        insight_cards=datasets.get("insight_cards"),
        strategy_specs=datasets.get("strategy_specs"),
        research_tickets=datasets.get("research_tickets"),
        research_experiments=datasets.get("research_experiments"),
        institutional_memory_entries=memory_records,
        personas=datasets.get("personas"),
    )


def _institutional_memory_path() -> Optional[Path]:
    explicit = os.getenv("PANTHEON_BFF_INSTITUTIONAL_MEMORY_STORE")
    if explicit:
        return Path(explicit)
    data_dir = os.getenv("PANTHEON_MEMORY_DATA_DIR")
    if not data_dir:
        return None
    root = Path(data_dir)
    for name in ("institutional_memory_entries.json", "institutional_memory.json"):
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def _load_records(
    path: Path,
    dataset: str,
    key_fields: tuple[str, ...],
) -> Dict[str, Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get(dataset), (dict, list)):
        payload = payload[dataset]
    if isinstance(payload, dict):
        return {
            str(key): _clone(record)
            for key, record in payload.items()
            if isinstance(record, dict)
        }
    if isinstance(payload, list):
        records: Dict[str, Dict[str, Any]] = {}
        for record in payload:
            if not isinstance(record, dict):
                continue
            key = next((str(record.get(field)) for field in key_fields if record.get(field)), "")
            if key:
                records[key] = _clone(record)
        return records
    raise ValueError(f"{path} does not contain an object or list for {dataset}")


def create_seeded_knowledge_read_ports() -> KnowledgeReadPortsDouble:
    """Return explicit local-snapshot fixtures used by the five KW contracts."""
    sources = {
        dataset: "local_snapshot"
        for dataset in (
            "research_notes",
            "evidence_refs",
            "insight_cards",
            "strategy_specs",
            "research_tickets",
            "research_experiments",
            "institutional_memory_entries",
        )
    }
    return create_knowledge_read_ports(
        dataset_sources=sources,
        research_notes=_SEEDED_RESEARCH_NOTES,
        evidence_refs=_SEEDED_EVIDENCE_REFS,
        insight_cards=_SEEDED_INSIGHT_CARDS,
        strategy_specs=_SEEDED_STRATEGY_SPECS,
        research_tickets=_SEEDED_RESEARCH_TICKETS,
        institutional_memory_entries=_SEEDED_MEMORY,
    )


_SEEDED_RESEARCH_TICKETS = {
    "tkt-7a8b9c0d-1234-5678-abcd-ef0123456789": {
        "ticket_id": "tkt-7a8b9c0d-1234-5678-abcd-ef0123456789",
        "title": "RW-Ticket: MOM-v3 slippage investigation (Apr 14)",
        "status": "open",
    }
}

_SEEDED_RESEARCH_NOTES = {
    "note-a1b2c3d4-e5f6-7890-abcd-ef1234567890": {
        "note_id": "note-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "title": "Momentum regime - observed slippage above 2sigma",
        "body": (
            "## Observation\n\nDuring the April 14-16 high-volatility window, strategy "
            "**MOM-v3** showed consistent bid-ask slippage above the 2sigma threshold."
        ),
        "attachment_type": "research_ticket",
        "attachment_ref": "tkt-7a8b9c0d-1234-5678-abcd-ef0123456789",
        "owner_ref": {
            "owner_type": "operator",
            "owner_id": "op-001",
            "display_name": "Alice Chen",
        },
        "tags": ["slippage", "momentum", "high-volatility"],
        "linked_evidence_refs": [
            "evref-c3d4e5f6-a7b8-9012-cdef-012345678901",
            "evref-d4e5f6a7-b8c9-0123-defa-123456789012",
        ],
        "linked_memory_anchors": ["mem-e5f6a7b8-c9d0-1234-efab-234567890123"],
        "created_at": "2026-04-16T14:22:00Z",
        "updated_at": "2026-04-17T09:05:00Z",
    }
}

_SEEDED_MEMORY = {
    "mem-e5f6a7b8-c9d0-1234-efab-234567890123": {
        "entry_id": "mem-e5f6a7b8-c9d0-1234-efab-234567890123",
        "knowledge_type": "regime_pattern",
        "content": {
            "headline": "High-volatility momentum slippage - pattern observed Q1 2026",
            "body": "Reference pattern used by the KW contracts.",
            "tags": ["momentum", "slippage", "regime"],
        },
        "source_event": {
            "type": "research_ticket_closed",
            "id": "tkt-7a8b9c0d-1234-5678-abcd-ef0123456789",
            "href": "/research/tickets/tkt-7a8b9c0d-1234-5678-abcd-ef0123456789",
        },
        "contributing_persona_ids": ["persona-HAWK-001"],
        "written_at": "2026-04-15T12:00:00Z",
        "write_authority": "research-svc",
        "scope": {"type": "strategy_family", "filter": "momentum"},
        "lifecycle": {"status": "active", "superseded_by": None},
        "usage": {"reuse_count": 3, "last_cited_at": "2026-04-17T09:05:00Z"},
    }
}

_SEEDED_EVIDENCE_REFS = {
    "evref-c3d4e5f6-a7b8-9012-cdef-012345678901": {
        "ref_id": "evref-c3d4e5f6-a7b8-9012-cdef-012345678901",
        "display_label": "ES Futures Slippage Distribution - Apr 14-16 Backtrace",
        "source_document": {
            "title": "ES Futures Slippage Distribution - Apr 14-16 Backtrace",
            "source_type": "experiment_artifact",
            "source_ref": "artifact://artifact-abc123/slippage-distribution.png",
            "excerpt": "Opening-auction slippage distribution for the high-volatility replay window.",
            "storage_preview": {
                "available": True,
                "preview_type": "image",
                "preview_token": "prev-local-slippage",
            },
            "captured_at": "2026-04-16T13:15:00Z",
            "captured_by": "Operator: Alice Chen",
        },
        "link_type": "supporting_evidence",
        "credibility": {
            "tier": "primary",
            "verified": True,
            "last_verified_at": "2026-04-17T09:00:00Z",
            "verification_method": "operator_review",
        },
        "linked_object_summary": {
            "entity_type": "memory_entry",
            "entity_ref": "mem-e5f6a7b8-c9d0-1234-efab-234567890123",
            "display_label": "High-volatility momentum slippage - pattern observed Q1 2026",
        },
        "resolved_link": {
            "availability": "available",
            "route_href": "/research/artifacts/artifact-abc123",
            "display_label": "Open experiment artifact",
            "open_in_new_tab": False,
        },
        "linked_decisions": [
            {
                "entity_type": "memory_entry",
                "entity_ref": "mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                "display_label": "High-volatility momentum slippage - pattern observed Q1 2026",
                "route_href": "/knowledge/memory/mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                "link_type": "supporting_evidence",
                "relationship_note": "Histogram reinforces the standing slippage pattern.",
            }
        ],
        "source_note_context": {
            "note_id": "note-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "title": "Momentum regime - observed slippage above 2sigma",
            "excerpt": "Observed persistent slippage above the 2sigma threshold.",
            "route_href": "/knowledge/notes/note-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        },
        "source_memory_context": {
            "entry_id": "mem-e5f6a7b8-c9d0-1234-efab-234567890123",
            "headline": "High-volatility momentum slippage - pattern observed Q1 2026",
            "knowledge_type": "regime_pattern",
            "lifecycle_status": "active",
            "route_href": "/knowledge/memory/mem-e5f6a7b8-c9d0-1234-efab-234567890123",
        },
        "created_at": "2026-04-16T13:15:00Z",
    }
}

_SEEDED_INSIGHT_CARDS = {
    "ins-7a3f2c91-e4b8-4d12-9f65-0c8e1a234567": {
        "insight_id": "ins-7a3f2c91-e4b8-4d12-9f65-0c8e1a234567",
        "summary": "Momentum strategies underperform during high-volatility regimes.",
        "scope": "strategy",
        "scope_ref": "strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a",
        "status": "active",
        "superseded_by_id": None,
        "confidence": {"score": 0.82, "label": "high", "basis": "Primary replay evidence."},
        "tags": ["momentum", "volatility-regime"],
        "source_ref": "agg-ref:seed-active",
        "supporting_evidence_refs": [
            {
                "ref_id": "evref-c3d4e5f6-a7b8-9012-cdef-012345678901",
                "source_document_title": "ES Futures Slippage Distribution",
                "link_type": "supporting_evidence",
                "credibility_tier": "primary",
                "resolved_link": {
                    "availability": "available",
                    "route_href": "/knowledge/evidence/evref-c3d4e5f6-a7b8-9012-cdef-012345678901",
                    "display_label": "View evidence reference",
                    "open_in_new_tab": False,
                },
            }
        ],
        "linked_sources": [
            {
                "entity_type": "experiment",
                "entity_ref": "exp-20260419-012",
                "display_label": "Momentum decay replay",
                "route_href": "/research/experiments/exp-20260419-012",
                "relationship_note": "Primary aggregation input",
            }
        ],
        "aggregation_provenance": {
            "memory_entry_count": 1,
            "note_count": 1,
            "evidence_ref_count": 1,
            "primary_evidence_count": 1,
            "aggregated_at": "2026-04-19T14:32:10Z",
            "aggregation_version": "v2.3.1",
        },
        "created_at": "2026-04-15T10:00:00Z",
        "updated_at": "2026-04-19T14:32:10Z",
    },
    "ins-b5d8e3f2-1a7c-4e09-8d56-f2c3a4b5d6e7": {
        "insight_id": "ins-b5d8e3f2-1a7c-4e09-8d56-f2c3a4b5d6e7",
        "summary": "Mean-reversion signals weaken around the session open.",
        "scope": "global",
        "scope_ref": None,
        "status": "active",
        "superseded_by_id": None,
        "confidence": {"score": 0.67, "label": "medium"},
        "tags": ["mean-reversion"],
        "supporting_evidence_refs": [],
        "linked_sources": [],
        "aggregation_provenance": {
            "memory_entry_count": 1,
            "note_count": 0,
            "evidence_ref_count": 0,
            "primary_evidence_count": 0,
            "aggregated_at": "2026-04-18T22:11:45Z",
            "aggregation_version": "v2.3.0",
        },
        "created_at": "2026-04-18T22:11:45Z",
        "updated_at": "2026-04-18T22:11:45Z",
    },
}

_FALLBACK_STRATEGY_ID = "strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a"
_SEEDED_STRATEGY_SPECS = {
    _FALLBACK_STRATEGY_ID: {
        "strategy_id": _FALLBACK_STRATEGY_ID,
        "current_spec_version_id": "specver-0a1b2c3d-0003-0003-0003-000000000003",
        "title": "Momentum Regime Response",
        "source_kind": "paper",
        "persona_ids": ["persona-HAWK-001"],
        "updated_at": "2026-04-18T09:00:00Z",
        "versions": [
            {
                "spec_version_id": "specver-0a1b2c3d-0001-0001-0001-000000000001",
                "spec_version": "v1",
                "lifecycle_state": "retired",
                "title": "Momentum Regime Response v1",
                "hypothesis": "Baseline momentum response loses edge after volatility breaks.",
                "objective": "Establish the baseline response.",
                "execution_profile": {"execution_mode_hint": "research"},
                "evaluation_plan": {"metrics": ["sharpe_ratio"]},
                "citation_bundle": {"evidence_refs": [], "memory_anchors": [], "insight_citations": []},
                "created_at": "2026-03-01T10:00:00Z",
                "created_by": "Operator: Alice Chen",
            },
            {
                "spec_version_id": "specver-0a1b2c3d-0002-0002-0002-000000000002",
                "spec_version": "v2",
                "parent_spec_version_id": "specver-0a1b2c3d-0001-0001-0001-000000000001",
                "lifecycle_state": "candidate",
                "title": "Momentum Regime Response v2",
                "hypothesis": "A shorter decay half-life should recover faster.",
                "objective": "Reduce post-break decay lag.",
                "execution_profile": {"execution_mode_hint": "research"},
                "evaluation_plan": {"metrics": ["sharpe_ratio", "max_drawdown"]},
                "citation_bundle": {
                    "evidence_refs": [
                        {"ref_id": "evref-a1b2c3d4-e5f6-7890-abcd-ef1234567890"}
                    ],
                    "memory_anchors": [
                        {
                            "entry_id": "mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                            "route_href": "/knowledge/memory/mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                        }
                    ],
                    "insight_citations": [],
                },
                "created_at": "2026-04-02T09:30:00Z",
                "created_by": "Persona: Momentum-alpha",
            },
            {
                "spec_version_id": "specver-0a1b2c3d-0003-0003-0003-000000000003",
                "spec_version": "v3",
                "parent_spec_version_id": "specver-0a1b2c3d-0002-0002-0002-000000000002",
                "lifecycle_state": "approved",
                "title": "Momentum Regime Response v3",
                "hypothesis": "Shorter decay and stricter paper gates improve recovery.",
                "objective": "Promote the response into governed paper-ready shape.",
                "execution_profile": {"execution_mode_hint": "paper"},
                "evaluation_plan": {"metrics": ["sharpe_ratio", "max_drawdown"]},
                "citation_bundle": {
                    "evidence_refs": [
                        {"ref_id": "evref-a1b2c3d4-e5f6-7890-abcd-ef1234567890"}
                    ],
                    "memory_anchors": [],
                    "insight_citations": [],
                },
                "created_at": "2026-04-18T09:00:00Z",
                "created_by": "Operator: Alice Chen",
            },
        ],
    }
}


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _clone_records(records: Mapping[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(key): _clone(record) for key, record in records.items()}
