"""Research write service compatibility module.

Re-exports ResearchWriteOwner and builder for callers expecting write_service naming.
"""
from __future__ import annotations

from services.research.write_owner import (
    ResearchWriteOwner,
    build_research_write_owner,
)

# Aliases for backwards compatibility
ResearchWriteService = ResearchWriteOwner
build_research_write_service = build_research_write_owner

__all__ = [
    "ResearchWriteOwner",
    "ResearchWriteService",
    "build_research_write_owner",
    "build_research_write_service",
]
