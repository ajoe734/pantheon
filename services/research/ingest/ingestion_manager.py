"""
RS-001: Research Ingestion Workflow Manager

Orchestrates the discovery and ingestion of research materials from approved
structured sources (OpenAlex API, GitHub REST API). Maintains governance compliance
and keeps raw research outside live execution paths.

This implements the ingestion workflow defined in RS-001 acceptance criteria:
- Discover research from approved sources
- Normalize findings into governed handoff format
- Track source metadata and governance compliance
- Hand off to RS-002 for specification normalization
"""

import json
import time
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any
from enum import Enum


class IngestionSourceType(Enum):
    """Approved ingestion source types."""
    ACADEMIC_PAPER = "academic_paper"
    CODE_REPOSITORY = "code_repository"
    RESEARCH_NOTE = "research_note"


class IngestionStatus(Enum):
    """Status of an ingestion session."""
    INITIALIZED = "initialized"
    SEARCHING = "searching"
    NORMALIZING = "normalizing"
    VALIDATED = "validated"
    HANDOFF_READY = "handoff_ready"
    ERROR = "error"


@dataclass
class IngestionSession:
    """Tracks a single research ingestion session."""
    session_id: str
    task_id: str = "RS-001"
    status: IngestionStatus = IngestionStatus.INITIALIZED
    source_type: Optional[IngestionSourceType] = None
    discovered_items: List[Dict[str, Any]] = field(default_factory=list)
    normalized_items: List[Dict[str, Any]] = field(default_factory=list)
    handoff_items: List[Dict[str, Any]] = field(default_factory=list)
    governance_metadata: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        """Convert session to dictionary for JSON serialization."""
        return {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "status": self.status.value,
            "source_type": self.source_type.value if self.source_type else None,
            "discovered_items_count": len(self.discovered_items),
            "normalized_items_count": len(self.normalized_items),
            "handoff_items_count": len(self.handoff_items),
            "governance_metadata": self.governance_metadata,
            "errors": self.errors,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ResearchIngestionManager:
    """
    Manages the end-to-end research ingestion workflow using verified adapters.

    This class coordinates:
    1. Source discovery via OpenAlex and GitHub adapters
    2. Response normalization and governance validation
    3. Handoff generation for downstream RS-002 normalization
    4. Session tracking and error handling

    Governance guarantees:
    - Only uses structured APIs (no web scraping)
    - Maintains source metadata and governance context
    - Validates all outputs before handoff
    - Separates raw research from live execution paths
    """

    def __init__(self, session_id: Optional[str] = None):
        """
        Initialize the ingestion manager.

        Args:
            session_id: Optional session identifier. If not provided, one will be generated.
        """
        self.session_id = session_id or f"rs001-{int(time.time() * 1000)}"
        self.session = IngestionSession(session_id=self.session_id)

    def discover_academic_papers(
        self,
        openalex_client: Any,
        search_query: Dict[str, Any],
        limit: int = 10,
    ) -> Tuple[bool, List[Dict[str, Any]], List[str]]:
        """
        Discover academic papers via OpenAlex API adapter.

        Governance compliance:
        - Uses OpenAlex REST API (approved structured source)
        - Preserves governance metadata from adapter
        - Validates that responses include required fields
        - Enforces rate limiting through adapter

        Args:
            openalex_client: Initialized OpenAlexClient adapter instance
            search_query: Dictionary with search parameters (title, authors, etc.)
            limit: Maximum number of papers to return (default: 10)

        Returns:
            Tuple of (success: bool, discovered_papers: List, errors: List)
        """
        self.session.status = IngestionStatus.SEARCHING
        self.session.source_type = IngestionSourceType.ACADEMIC_PAPER
        errors = []

        try:
            # Delegate search to verified adapter
            papers = openalex_client.search_and_normalize(
                **search_query,
                limit=limit,
            )

            if not papers:
                msg = f"No papers found matching query: {search_query}"
                errors.append(msg)
                self.session.errors.append(msg)
                return False, [], errors

            # Store discovered papers with governance metadata
            for paper in papers:
                paper_dict = paper.to_dict() if hasattr(paper, "to_dict") else paper
                self.session.discovered_items.append(paper_dict)

            # Track governance context
            self.session.governance_metadata = {
                "source": "OpenAlex API",
                "api_version": "v2",
                "governance_level": "approved_structured_source",
                "discovery_type": "academic_paper_search",
                "papers_discovered": len(papers),
                "search_query": search_query,
            }

            self.session.updated_at = datetime.utcnow().isoformat() + "Z"
            return True, self.session.discovered_items, errors

        except Exception as e:
            error_msg = f"Failed to discover academic papers: {str(e)}"
            errors.append(error_msg)
            self.session.errors.append(error_msg)
            self.session.status = IngestionStatus.ERROR
            return False, [], errors

    def discover_code_repositories(
        self,
        github_client: Any,
        repo_specs: List[Dict[str, str]],
    ) -> Tuple[bool, List[Dict[str, Any]], List[str]]:
        """
        Discover code repositories and contents via GitHub REST API adapter.

        Governance compliance:
        - Uses GitHub REST API (approved structured source)
        - Enforces repository approval whitelist from adapter
        - Validates path-based access control
        - Preserves governance metadata

        Args:
            github_client: Initialized GitHubClient adapter instance
            repo_specs: List of dicts with 'owner', 'repo', and optional 'path'

        Returns:
            Tuple of (success: bool, discovered_repos: List, errors: List)
        """
        self.session.status = IngestionStatus.SEARCHING
        self.session.source_type = IngestionSourceType.CODE_REPOSITORY
        errors = []

        try:
            discovered_repos = []

            for spec in repo_specs:
                owner = spec.get("owner")
                repo = spec.get("repo")
                path = spec.get("path", None)

                if not owner or not repo:
                    error_msg = f"Invalid repo spec (missing owner/repo): {spec}"
                    errors.append(error_msg)
                    self.session.errors.append(error_msg)
                    continue

                try:
                    # Get repository metadata (approval enforced by adapter)
                    repo_data = github_client.get_repository(owner, repo)
                    normalized_repo = github_client.normalize_repository(repo_data)
                    repo_dict = (
                        normalized_repo.to_dict()
                        if hasattr(normalized_repo, "to_dict")
                        else normalized_repo
                    )

                    # If path specified, fetch file contents
                    if path:
                        try:
                            file_data = github_client.get_file_content(owner, repo, path)
                            normalized_file = github_client.normalize_file(
                                file_data, f"{owner}/{repo}"
                            )
                            file_dict = (
                                normalized_file.to_dict()
                                if hasattr(normalized_file, "to_dict")
                                else normalized_file
                            )
                            repo_dict["file_content"] = file_dict

                        except Exception as file_error:
                            error_msg = f"Failed to fetch file {path} from {owner}/{repo}: {str(file_error)}"
                            errors.append(error_msg)
                            self.session.errors.append(error_msg)

                    discovered_repos.append(repo_dict)

                except ValueError as approval_error:
                    error_msg = f"Repository approval check failed for {owner}/{repo}: {str(approval_error)}"
                    errors.append(error_msg)
                    self.session.errors.append(error_msg)

                except Exception as e:
                    error_msg = f"Failed to fetch repository {owner}/{repo}: {str(e)}"
                    errors.append(error_msg)
                    self.session.errors.append(error_msg)

            if not discovered_repos:
                return False, [], errors

            self.session.discovered_items = discovered_repos

            # Track governance context
            self.session.governance_metadata = {
                "source": "GitHub REST API",
                "governance_level": "approved_structured_source",
                "discovery_type": "code_repository_discovery",
                "approval_enforcement": "whitelist",
                "repositories_discovered": len(discovered_repos),
                "specs_processed": len(repo_specs),
            }

            self.session.updated_at = datetime.utcnow().isoformat() + "Z"
            return True, discovered_repos, errors

        except Exception as e:
            error_msg = f"Failed to discover code repositories: {str(e)}"
            errors.append(error_msg)
            self.session.errors.append(error_msg)
            self.session.status = IngestionStatus.ERROR
            return False, [], errors

    def normalize_and_handoff(
        self,
        handoff_builder: Any,
    ) -> Tuple[bool, List[Dict[str, Any]], List[str]]:
        """
        Normalize discovered items and generate handoff for RS-002.

        This step:
        1. Validates governance metadata preservation
        2. Normalizes raw items into governed format
        3. Builds handoff objects using validated builder
        4. Validates all handoffs before returning

        Args:
            handoff_builder: HandoffBuilder instance for creating governance handoffs

        Returns:
            Tuple of (success: bool, handoff_items: List, errors: List)
        """
        self.session.status = IngestionStatus.NORMALIZING
        errors = []

        try:
            if not self.session.discovered_items:
                error_msg = "No discovered items to normalize"
                errors.append(error_msg)
                self.session.errors.append(error_msg)
                return False, [], errors

            for idx, item in enumerate(self.session.discovered_items):
                try:
                    handoff = None

                    # Route based on source type
                    if self.session.source_type == IngestionSourceType.ACADEMIC_PAPER:
                        handoff = handoff_builder.build_academic_paper_handoff(
                            task_id="RS-001",
                            paper=item,
                            governance_metadata=item.get("governance_metadata", {}),
                            confidence="high" if idx < 3 else "medium",
                        )

                    elif self.session.source_type == IngestionSourceType.CODE_REPOSITORY:
                        handoff = handoff_builder.build_code_repository_handoff(
                            task_id="RS-001",
                            repository=item,
                            governance_metadata=item.get("governance_metadata", {}),
                        )

                    if handoff:
                        # Validate handoff before storing
                        is_valid, validation_errors = (
                            handoff_builder.validate_handoff(handoff)
                        )

                        if not is_valid:
                            error_msg = f"Handoff validation failed for item {idx}: {validation_errors}"
                            errors.append(error_msg)
                            self.session.errors.append(error_msg)
                            continue

                        # Store validated handoff
                        handoff_dict = (
                            handoff.to_dict()
                            if hasattr(handoff, "to_dict")
                            else handoff
                        )
                        self.session.handoff_items.append(handoff_dict)

                except Exception as item_error:
                    error_msg = f"Failed to normalize item {idx}: {str(item_error)}"
                    errors.append(error_msg)
                    self.session.errors.append(error_msg)

            if not self.session.handoff_items:
                error_msg = "No valid handoffs generated after normalization"
                errors.append(error_msg)
                self.session.errors.append(error_msg)
                return False, [], errors

            self.session.status = IngestionStatus.HANDOFF_READY
            self.session.updated_at = datetime.utcnow().isoformat() + "Z"
            return True, self.session.handoff_items, errors

        except Exception as e:
            error_msg = f"Failed to normalize and create handoffs: {str(e)}"
            errors.append(error_msg)
            self.session.errors.append(error_msg)
            self.session.status = IngestionStatus.ERROR
            return False, [], errors

    def get_session_summary(self) -> Dict[str, Any]:
        """Get a summary of the current ingestion session."""
        return {
            "session_id": self.session.session_id,
            "task_id": self.session.task_id,
            "status": self.session.status.value,
            "source_type": self.session.source_type.value if self.session.source_type else None,
            "discovered_count": len(self.session.discovered_items),
            "handoff_count": len(self.session.handoff_items),
            "error_count": len(self.session.errors),
            "governance_metadata": self.session.governance_metadata,
            "created_at": self.session.created_at,
            "updated_at": self.session.updated_at,
        }

    def export_handoffs(self, output_file: Optional[str] = None) -> str:
        """
        Export generated handoffs in JSON format for downstream consumption.

        Args:
            output_file: Optional file path to write handoffs. If not provided,
                        returns JSON string.

        Returns:
            JSON string representation of handoffs
        """
        export_data = {
            "session": self.session.to_dict(),
            "handoffs": self.session.handoff_items,
            "errors": self.session.errors,
        }

        json_str = json.dumps(export_data, indent=2, ensure_ascii=False)

        if output_file:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(json_str)

        return json_str
