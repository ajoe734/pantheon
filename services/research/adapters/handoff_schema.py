"""Schema definitions for research ingestion and governance handoff."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class GovernanceContext:
    """Governance context for all ingested research."""
    source_type: str  # openalex, github_api, research_notes
    source_url: str
    retrieved_at: str
    governance_status: str = "approved_catalog_source"
    metadata_preservation: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "source_type": self.source_type,
            "source_url": self.source_url,
            "retrieved_at": self.retrieved_at,
            "governance_status": self.governance_status,
            "metadata_preservation": self.metadata_preservation,
        }


@dataclass
class ResearchHandoff:
    """Handoff structure for research findings ready for downstream consumption."""
    task_id: str
    source_type: str  # academic_paper|code_repository|research_note
    source_metadata: dict[str, Any]
    normalized_findings: dict[str, Any]
    grok_processing_notes: dict[str, Any]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "task_id": self.task_id,
            "source_type": self.source_type,
            "source_metadata": self.source_metadata,
            "normalized_findings": self.normalized_findings,
            "grok_processing_notes": self.grok_processing_notes,
            "created_at": self.created_at,
        }
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2)


class HandoffBuilder:
    """Builder for constructing governance-compliant research handoffs."""
    
    @staticmethod
    def build_academic_paper_handoff(
        task_id: str,
        paper: dict[str, Any],
        governance_metadata: dict[str, Any],
        strategy_spec: Optional[dict[str, Any]] = None,
        confidence: str = "high",
        downstream_readiness: str = "ready_for_replication",
    ) -> ResearchHandoff:
        """Build a handoff for academic paper research.
        
        Args:
            task_id: Research task ID (e.g., "RS-001")
            paper: Normalized paper response from OpenAlex adapter
            governance_metadata: Governance context from adapter
            strategy_spec: Normalized StrategySpec (optional)
            confidence: Normalization confidence level
            downstream_readiness: Status for downstream processing
            
        Returns:
            Complete handoff structure ready for review and processing
        """
        return ResearchHandoff(
            task_id=task_id,
            source_type="academic_paper",
            source_metadata={
                "api_endpoint": governance_metadata.get("api_endpoint"),
                "retrieved_at": governance_metadata.get("retrieved_at"),
                "governance_context": governance_metadata.get("governance_context"),
            },
            normalized_findings={
                "strategy_spec": strategy_spec or {
                    "name": paper.get("title"),
                    "description": paper.get("abstract"),
                    "source_paper": paper.get("work_id"),
                    "authors": paper.get("authors", []),
                    "doi": paper.get("doi"),
                    "signals": [],
                    "parameters": {}
                },
                "replication_notes": "See full paper for implementation details",
                "evaluation_hypotheses": "Expected metrics from paper review"
            },
            grok_processing_notes={
                "normalization_confidence": confidence,
                "governance_compliance": "verified",
                "downstream_readiness": downstream_readiness,
                "processing_timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
    
    @staticmethod
    def build_code_repository_handoff(
        task_id: str,
        repository: dict[str, Any],
        governance_metadata: dict[str, Any],
        key_files: list[dict[str, Any]] = None,
        confidence: str = "high",
        downstream_readiness: str = "ready_for_replication",
    ) -> ResearchHandoff:
        """Build a handoff for code repository research.
        
        Args:
            task_id: Research task ID
            repository: Normalized repository response from GitHub adapter
            governance_metadata: Governance context from adapter
            key_files: List of key files extracted from repository
            confidence: Normalization confidence level
            downstream_readiness: Status for downstream processing
            
        Returns:
            Complete handoff structure ready for review and processing
        """
        return ResearchHandoff(
            task_id=task_id,
            source_type="code_repository",
            source_metadata={
                "api_endpoint": governance_metadata.get("api_endpoint"),
                "retrieved_at": governance_metadata.get("retrieved_at"),
                "governance_context": governance_metadata.get("governance_context"),
                "repository_url": repository.get("url"),
            },
            normalized_findings={
                "strategy_spec": {
                    "name": repository.get("name"),
                    "description": repository.get("description"),
                    "source_repository": repository.get("full_name"),
                    "key_files": key_files or [],
                    "language": repository.get("language"),
                    "signals": [],
                    "parameters": {}
                },
                "replication_notes": "See repository README and key implementation files",
                "evaluation_hypotheses": "Expected results from running repository examples"
            },
            grok_processing_notes={
                "normalization_confidence": confidence,
                "governance_compliance": "verified",
                "downstream_readiness": downstream_readiness,
                "processing_timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
    
    @staticmethod
    def validate_handoff(handoff: ResearchHandoff) -> tuple[bool, list[str]]:
        """Validate a handoff for completeness and governance compliance.
        
        Args:
            handoff: Handoff to validate
            
        Returns:
            Tuple of (is_valid, error_messages)
        """
        errors = []
        
        # Check required fields
        if not handoff.task_id:
            errors.append("Missing task_id")
        if not handoff.source_type:
            errors.append("Missing source_type")
        if handoff.source_type not in ["academic_paper", "code_repository", "research_note"]:
            errors.append(f"Invalid source_type: {handoff.source_type}")
        
        # Check metadata
        if not handoff.source_metadata.get("governance_context"):
            errors.append("Missing governance context in metadata")
        if not handoff.source_metadata.get("retrieved_at"):
            errors.append("Missing retrieval timestamp")
        
        # Check findings
        if not handoff.normalized_findings.get("strategy_spec"):
            errors.append("Missing strategy_spec in findings")
        
        # Check processing notes
        notes = handoff.grok_processing_notes
        if notes.get("governance_compliance") != "verified":
            errors.append("Governance compliance not verified")
        if notes.get("downstream_readiness") not in [
            "ready_for_replication", "needs_clarification", "blocked"
        ]:
            errors.append(f"Invalid downstream_readiness: {notes.get('downstream_readiness')}")
        
        return len(errors) == 0, errors
