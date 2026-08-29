"""Research, Knowledge, Memory, Search, and Source narrow domain ports.

Re-exports typed domain ports, protocols, and factory functions for Research,
Knowledge workbench, Institutional memory, External search, and Source ingestion.
"""
from __future__ import annotations

try:
    from domain_ports.research_knowledge_source import (
        ResearchKnowledgeSourcePort,
        DefaultResearchKnowledgeSourcePort,
    )
except ImportError:
    from services.control_plane.bff.domain_ports.research_knowledge_source import (  # type: ignore[no-redef]
        ResearchKnowledgeSourcePort,
        DefaultResearchKnowledgeSourcePort,
    )

__all__ = [
    "ResearchKnowledgeSourcePort",
    "DefaultResearchKnowledgeSourcePort",
]
