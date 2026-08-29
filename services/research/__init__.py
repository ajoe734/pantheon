"""Research service package with independent persistent write owner."""

from services.research.write_owner import (
    ResearchWriteOwner,
    build_research_write_owner,
)

__all__ = [
    "ResearchWriteOwner",
    "build_research_write_owner",
]
