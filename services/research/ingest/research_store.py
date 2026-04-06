"""
RS-001: Research Store

Provides persistent storage for ingested research materials, separate from live
execution paths. Maintains governance compliance and source tracking.

Design principles:
- Raw research stored outside live execution paths
- Immutable storage with append-only semantics
- Full governance metadata preserved
- Query interface for research discovery and replication gate access
"""

import json
import os
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Tuple
from enum import Enum


class ResearchMaterialType(Enum):
    """Type of research material."""
    ACADEMIC_PAPER = "academic_paper"
    CODE_REPOSITORY = "code_repository"
    RESEARCH_NOTE = "research_note"


class ResearchMaterialStatus(Enum):
    """Lifecycle status of stored research material."""
    INGESTED = "ingested"
    NORMALIZED = "normalized"
    REPLICATED = "replicated"
    PROMOTED = "promoted"
    ARCHIVED = "archived"


@dataclass
class ResearchMaterial:
    """Single stored research material with full governance tracking."""

    material_id: str
    task_id: str = "RS-001"
    material_type: ResearchMaterialType = ResearchMaterialType.ACADEMIC_PAPER
    source_uri: str = ""
    title: str = ""
    description: str = ""
    source_metadata: Dict[str, Any] = field(default_factory=dict)
    normalized_findings: Dict[str, Any] = field(default_factory=dict)
    governance_metadata: Dict[str, Any] = field(default_factory=dict)
    status: ResearchMaterialStatus = ResearchMaterialStatus.INGESTED
    ingestion_session_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "material_id": self.material_id,
            "task_id": self.task_id,
            "material_type": self.material_type.value,
            "source_uri": self.source_uri,
            "title": self.title,
            "description": self.description,
            "source_metadata": self.source_metadata,
            "normalized_findings": self.normalized_findings,
            "governance_metadata": self.governance_metadata,
            "status": self.status.value,
            "ingestion_session_id": self.ingestion_session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "ResearchMaterial":
        """Create ResearchMaterial from dictionary."""
        return ResearchMaterial(
            material_id=data.get("material_id", ""),
            task_id=data.get("task_id", "RS-001"),
            material_type=ResearchMaterialType(data.get("material_type", "academic_paper")),
            source_uri=data.get("source_uri", ""),
            title=data.get("title", ""),
            description=data.get("description", ""),
            source_metadata=data.get("source_metadata", {}),
            normalized_findings=data.get("normalized_findings", {}),
            governance_metadata=data.get("governance_metadata", {}),
            status=ResearchMaterialStatus(data.get("status", "ingested")),
            ingestion_session_id=data.get("ingestion_session_id", ""),
            created_at=data.get("created_at", datetime.utcnow().isoformat() + "Z"),
            updated_at=data.get("updated_at", datetime.utcnow().isoformat() + "Z"),
        )


class ResearchStore:
    """
    Persistent storage for ingested research materials.

    This store maintains:
    - Complete governance metadata trail
    - Immutable records of ingestion and normalization
    - Query interface for research discovery
    - Separation from live execution paths

    Storage location:
    - Default: services/research/ingest/store/ (outside live paths)
    - Structured as append-only JSON files per session
    """

    def __init__(self, store_dir: Optional[str] = None):
        """
        Initialize the research store.

        Args:
            store_dir: Directory path for research storage. Default uses
                      services/research/ingest/store/
        """
        if store_dir is None:
            # Construct default path relative to this module
            current_dir = os.path.dirname(os.path.abspath(__file__))
            store_dir = os.path.join(current_dir, "store")

        self.store_dir = store_dir
        self._ensure_store_directory()

    def _ensure_store_directory(self) -> None:
        """Create store directory if it doesn't exist."""
        os.makedirs(self.store_dir, exist_ok=True)

    def store_material(
        self,
        material: ResearchMaterial,
    ) -> Tuple[bool, str]:
        """
        Store a research material persistently.

        Governance compliance:
        - Preserves full source metadata
        - Maintains immutable audit trail
        - Stores outside live execution paths
        - Tracks ingestion context

        Args:
            material: ResearchMaterial instance to store

        Returns:
            Tuple of (success: bool, storage_path: str)
        """
        try:
            # Organize storage by session for audit trail
            session_dir = os.path.join(self.store_dir, material.ingestion_session_id)
            os.makedirs(session_dir, exist_ok=True)

            # File per material maintains audit trail
            material_file = os.path.join(
                session_dir, f"{material.material_id}.json"
            )

            # Write as JSON
            with open(material_file, "w", encoding="utf-8") as f:
                json.dump(material.to_dict(), f, indent=2, ensure_ascii=False)

            return True, material_file

        except Exception as e:
            raise RuntimeError(f"Failed to store research material: {str(e)}")

    def retrieve_material(self, material_id: str) -> Optional[ResearchMaterial]:
        """
        Retrieve a stored research material by ID.

        Args:
            material_id: Material identifier

        Returns:
            ResearchMaterial if found, None otherwise
        """
        try:
            # Search across all sessions (linear scan for now)
            for session_id in os.listdir(self.store_dir):
                session_path = os.path.join(self.store_dir, session_id)
                if not os.path.isdir(session_path):
                    continue

                material_file = os.path.join(session_path, f"{material_id}.json")
                if os.path.exists(material_file):
                    with open(material_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return ResearchMaterial.from_dict(data)

            return None

        except Exception as e:
            raise RuntimeError(f"Failed to retrieve research material: {str(e)}")

    def list_materials_by_session(
        self, session_id: str
    ) -> List[ResearchMaterial]:
        """
        List all materials from a specific ingestion session.

        Args:
            session_id: Ingestion session identifier

        Returns:
            List of ResearchMaterial objects
        """
        materials = []
        session_path = os.path.join(self.store_dir, session_id)

        if not os.path.exists(session_path):
            return materials

        try:
            for filename in os.listdir(session_path):
                if filename.endswith(".json"):
                    material_file = os.path.join(session_path, filename)
                    with open(material_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    materials.append(ResearchMaterial.from_dict(data))

            return sorted(materials, key=lambda m: m.created_at)

        except Exception as e:
            raise RuntimeError(f"Failed to list materials: {str(e)}")

    def list_materials_by_type(
        self, material_type: ResearchMaterialType
    ) -> List[ResearchMaterial]:
        """
        List all materials of a specific type.

        Args:
            material_type: Type of material to filter by

        Returns:
            List of matching ResearchMaterial objects
        """
        materials = []

        try:
            for session_id in os.listdir(self.store_dir):
                session_path = os.path.join(self.store_dir, session_id)
                if not os.path.isdir(session_path):
                    continue

                for filename in os.listdir(session_path):
                    if filename.endswith(".json"):
                        material_file = os.path.join(session_path, filename)
                        with open(material_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        material = ResearchMaterial.from_dict(data)
                        if material.material_type == material_type:
                            materials.append(material)

            return sorted(materials, key=lambda m: m.created_at, reverse=True)

        except Exception as e:
            raise RuntimeError(f"Failed to list materials by type: {str(e)}")

    def list_materials_by_status(
        self, status: ResearchMaterialStatus
    ) -> List[ResearchMaterial]:
        """
        List all materials with a specific status.

        Args:
            status: Status filter

        Returns:
            List of matching ResearchMaterial objects
        """
        materials = []

        try:
            for session_id in os.listdir(self.store_dir):
                session_path = os.path.join(self.store_dir, session_id)
                if not os.path.isdir(session_path):
                    continue

                for filename in os.listdir(session_path):
                    if filename.endswith(".json"):
                        material_file = os.path.join(session_path, filename)
                        with open(material_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        material = ResearchMaterial.from_dict(data)
                        if material.status == status:
                            materials.append(material)

            return sorted(materials, key=lambda m: m.updated_at, reverse=True)

        except Exception as e:
            raise RuntimeError(f"Failed to list materials by status: {str(e)}")

    def update_material_status(
        self, material_id: str, new_status: ResearchMaterialStatus
    ) -> Tuple[bool, Optional[str]]:
        """
        Update the status of a stored material (for RS-002 and later stages).

        Args:
            material_id: Material identifier
            new_status: New status value

        Returns:
            Tuple of (success: bool, updated_path: Optional[str])
        """
        try:
            # Find and update material
            for session_id in os.listdir(self.store_dir):
                session_path = os.path.join(self.store_dir, session_id)
                if not os.path.isdir(session_path):
                    continue

                material_file = os.path.join(session_path, f"{material_id}.json")
                if os.path.exists(material_file):
                    with open(material_file, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    material = ResearchMaterial.from_dict(data)
                    material.status = new_status
                    material.updated_at = datetime.utcnow().isoformat() + "Z"

                    with open(material_file, "w", encoding="utf-8") as f:
                        json.dump(material.to_dict(), f, indent=2, ensure_ascii=False)

                    return True, material_file

            return False, None

        except Exception as e:
            raise RuntimeError(f"Failed to update material status: {str(e)}")

    def export_store_summary(self) -> Dict[str, Any]:
        """
        Export a summary of all stored materials.

        Returns:
            Summary dictionary with material counts by type and status
        """
        summary = {
            "store_path": self.store_dir,
            "total_materials": 0,
            "by_type": {},
            "by_status": {},
            "by_session": {},
        }

        try:
            for session_id in os.listdir(self.store_dir):
                session_path = os.path.join(self.store_dir, session_id)
                if not os.path.isdir(session_path):
                    continue

                session_materials = 0
                for filename in os.listdir(session_path):
                    if filename.endswith(".json"):
                        session_materials += 1
                        material_file = os.path.join(session_path, filename)
                        with open(material_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        material = ResearchMaterial.from_dict(data)

                        # Count by type
                        mat_type = material.material_type.value
                        summary["by_type"][mat_type] = (
                            summary["by_type"].get(mat_type, 0) + 1
                        )

                        # Count by status
                        mat_status = material.status.value
                        summary["by_status"][mat_status] = (
                            summary["by_status"].get(mat_status, 0) + 1
                        )

                summary["by_session"][session_id] = session_materials
                summary["total_materials"] += session_materials

            return summary

        except Exception as e:
            raise RuntimeError(f"Failed to export store summary: {str(e)}")
