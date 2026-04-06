"""
RS-001: Research Ingestion Workflow

Implements governed ingestion of research materials from approved structured sources:
- Academic papers via OpenAlex API
- Code repositories via GitHub REST API
- Research notes from governed repositories

Maintains governance compliance and keeps raw research outside live execution paths.

Module exports:
- ResearchIngestionManager: Orchestrates discovery, normalization, and handoff
- ResearchStore: Persistent storage outside live execution paths
- ResearchMaterial: Individual research material with governance tracking
- Related enums: IngestionSourceType, IngestionStatus, ResearchMaterialType, ResearchMaterialStatus
"""

from .ingestion_manager import (
    ResearchIngestionManager,
    IngestionSession,
    IngestionSourceType,
    IngestionStatus,
)
from .research_store import (
    ResearchStore,
    ResearchMaterial,
    ResearchMaterialType,
    ResearchMaterialStatus,
)

__all__ = [
    "ResearchIngestionManager",
    "ResearchStore",
    "ResearchMaterial",
    "IngestionSession",
    "IngestionSourceType",
    "IngestionStatus",
    "ResearchMaterialType",
    "ResearchMaterialStatus",
]
