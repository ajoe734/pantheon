"""
Test suite for RS-001 Research Ingestion Workflow

Tests cover:
- ResearchIngestionManager discovery and normalization
- ResearchStore persistence and retrieval
- Integration with verified adapters
- Governance compliance and error handling
"""

import unittest
import json
import os
import tempfile
import shutil
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch

from ingestion_manager import (
    ResearchIngestionManager,
    IngestionSourceType,
    IngestionStatus,
    IngestionSession,
)
from research_store import (
    ResearchStore,
    ResearchMaterial,
    ResearchMaterialType,
    ResearchMaterialStatus,
)


class TestIngestionSession(unittest.TestCase):
    """Test IngestionSession data structure."""

    def test_session_initialization(self):
        """Test creating a new ingestion session."""
        session = IngestionSession(session_id="test-session")
        self.assertEqual(session.session_id, "test-session")
        self.assertEqual(session.status, IngestionStatus.INITIALIZED)
        self.assertEqual(len(session.discovered_items), 0)
        self.assertIsNotNone(session.created_at)

    def test_session_to_dict(self):
        """Test converting session to dictionary."""
        session = IngestionSession(session_id="test-session")
        session.discovered_items = [{"id": "paper1"}]
        session.status = IngestionStatus.SEARCHING

        session_dict = session.to_dict()
        self.assertEqual(session_dict["session_id"], "test-session")
        self.assertEqual(session_dict["status"], "searching")
        self.assertEqual(session_dict["discovered_items_count"], 1)


class TestResearchIngestionManager(unittest.TestCase):
    """Test ResearchIngestionManager workflow."""

    def setUp(self):
        """Set up test fixtures."""
        self.manager = ResearchIngestionManager(session_id="test-rs001")

    def test_manager_initialization(self):
        """Test manager initialization."""
        self.assertIsNotNone(self.manager.session_id)
        self.assertEqual(self.manager.session.status, IngestionStatus.INITIALIZED)

    def test_discover_academic_papers_success(self):
        """Test successful academic paper discovery."""
        # Mock OpenAlex adapter
        mock_client = Mock()
        mock_paper1 = Mock()
        mock_paper1.to_dict.return_value = {
            "id": "paper1",
            "title": "ML Trading",
            "governance_metadata": {"source": "OpenAlex"},
        }
        mock_paper2 = Mock()
        mock_paper2.to_dict.return_value = {
            "id": "paper2",
            "title": "RL Finance",
            "governance_metadata": {"source": "OpenAlex"},
        }
        mock_client.search_and_normalize.return_value = [mock_paper1, mock_paper2]

        # Execute discovery
        success, papers, errors = self.manager.discover_academic_papers(
            openalex_client=mock_client,
            search_query={"title": "machine learning trading"},
            limit=5,
        )

        # Verify success
        self.assertTrue(success)
        self.assertEqual(len(papers), 2)
        self.assertEqual(len(errors), 0)
        self.assertEqual(self.manager.session.status, IngestionStatus.SEARCHING)
        self.assertEqual(
            self.manager.session.source_type, IngestionSourceType.ACADEMIC_PAPER
        )

    def test_discover_academic_papers_no_results(self):
        """Test paper discovery with no results."""
        mock_client = Mock()
        mock_client.search_and_normalize.return_value = []

        success, papers, errors = self.manager.discover_academic_papers(
            openalex_client=mock_client,
            search_query={"title": "nonexistent"},
            limit=5,
        )

        self.assertFalse(success)
        self.assertEqual(len(papers), 0)
        self.assertGreater(len(errors), 0)

    def test_discover_academic_papers_adapter_error(self):
        """Test handling adapter error during discovery."""
        mock_client = Mock()
        mock_client.search_and_normalize.side_effect = RuntimeError("API Error")

        success, papers, errors = self.manager.discover_academic_papers(
            openalex_client=mock_client,
            search_query={"title": "test"},
            limit=5,
        )

        self.assertFalse(success)
        self.assertEqual(self.manager.session.status, IngestionStatus.ERROR)
        self.assertGreater(len(errors), 0)

    def test_discover_code_repositories_success(self):
        """Test successful code repository discovery."""
        # Mock GitHub adapter
        mock_client = Mock()

        # Mock repo data
        mock_repo = Mock()
        normalized_repo = Mock()
        normalized_repo.to_dict.return_value = {
            "id": "lean-repo",
            "owner": "QuantConnect",
            "repo": "Lean",
            "governance_metadata": {"source": "GitHub", "approval": "whitelisted"},
        }

        mock_client.get_repository.return_value = mock_repo
        mock_client.normalize_repository.return_value = normalized_repo

        # Execute discovery
        success, repos, errors = self.manager.discover_code_repositories(
            github_client=mock_client,
            repo_specs=[{"owner": "QuantConnect", "repo": "Lean"}],
        )

        # Verify success
        self.assertTrue(success)
        self.assertEqual(len(repos), 1)
        self.assertEqual(len(errors), 0)
        self.assertEqual(
            self.manager.session.source_type, IngestionSourceType.CODE_REPOSITORY
        )

    def test_discover_code_repositories_approval_failure(self):
        """Test repository discovery with approval failure."""
        mock_client = Mock()
        mock_client.get_repository.side_effect = ValueError("Repository not approved")

        success, repos, errors = self.manager.discover_code_repositories(
            github_client=mock_client,
            repo_specs=[{"owner": "evil-corp", "repo": "malware"}],
        )

        self.assertFalse(success)
        self.assertGreater(len(errors), 0)

    def test_normalize_and_handoff_academic_papers(self):
        """Test normalizing discovered papers for handoff."""
        # Setup: add discovered papers
        self.manager.session.source_type = IngestionSourceType.ACADEMIC_PAPER
        self.manager.session.discovered_items = [
            {
                "id": "paper1",
                "title": "ML Trading",
                "governance_metadata": {"source": "OpenAlex"},
            },
        ]

        # Mock handoff builder
        mock_builder = Mock()
        mock_handoff = Mock()
        mock_handoff.to_dict.return_value = {
            "task_id": "RS-001",
            "source_type": "academic_paper",
        }

        mock_builder.build_academic_paper_handoff.return_value = mock_handoff
        mock_builder.validate_handoff.return_value = (True, [])

        # Execute normalization
        success, handoffs, errors = self.manager.normalize_and_handoff(
            handoff_builder=mock_builder,
        )

        # Verify
        self.assertTrue(success)
        self.assertEqual(len(handoffs), 1)
        self.assertEqual(len(errors), 0)
        self.assertEqual(self.manager.session.status, IngestionStatus.HANDOFF_READY)

    def test_normalize_and_handoff_validation_failure(self):
        """Test handoff validation failure."""
        self.manager.session.source_type = IngestionSourceType.ACADEMIC_PAPER
        self.manager.session.discovered_items = [
            {"id": "paper1", "title": "Test", "governance_metadata": {}},
        ]

        mock_builder = Mock()
        mock_handoff = Mock()
        mock_builder.build_academic_paper_handoff.return_value = mock_handoff
        mock_builder.validate_handoff.return_value = (
            False,
            ["Missing required field: strategy_spec"],
        )

        success, handoffs, errors = self.manager.normalize_and_handoff(
            handoff_builder=mock_builder,
        )

        self.assertFalse(success)
        self.assertEqual(len(handoffs), 0)
        self.assertGreater(len(errors), 0)

    def test_get_session_summary(self):
        """Test retrieving session summary."""
        self.manager.session.source_type = IngestionSourceType.ACADEMIC_PAPER
        self.manager.session.discovered_items = [{"id": "paper1"}]
        self.manager.session.handoff_items = [{"task_id": "RS-001"}]

        summary = self.manager.get_session_summary()

        self.assertEqual(summary["session_id"], "test-rs001")
        self.assertEqual(summary["source_type"], "academic_paper")
        self.assertEqual(summary["discovered_count"], 1)
        self.assertEqual(summary["handoff_count"], 1)

    def test_export_handoffs(self):
        """Test exporting handoffs to JSON."""
        self.manager.session.handoff_items = [
            {
                "task_id": "RS-001",
                "source_type": "academic_paper",
                "normalized_findings": {"strategy": "test"},
            },
        ]

        json_str = self.manager.export_handoffs()
        data = json.loads(json_str)

        self.assertIn("session", data)
        self.assertIn("handoffs", data)
        self.assertEqual(len(data["handoffs"]), 1)


class TestResearchMaterial(unittest.TestCase):
    """Test ResearchMaterial data class."""

    def test_material_initialization(self):
        """Test creating a research material."""
        material = ResearchMaterial(
            material_id="mat-001",
            title="Research Paper",
            material_type=ResearchMaterialType.ACADEMIC_PAPER,
        )

        self.assertEqual(material.material_id, "mat-001")
        self.assertEqual(material.title, "Research Paper")
        self.assertEqual(material.status, ResearchMaterialStatus.INGESTED)

    def test_material_to_dict(self):
        """Test converting material to dictionary."""
        material = ResearchMaterial(
            material_id="mat-001",
            title="Test Paper",
            material_type=ResearchMaterialType.ACADEMIC_PAPER,
        )

        mat_dict = material.to_dict()
        self.assertEqual(mat_dict["material_id"], "mat-001")
        self.assertEqual(mat_dict["title"], "Test Paper")
        self.assertEqual(mat_dict["material_type"], "academic_paper")

    def test_material_from_dict(self):
        """Test creating material from dictionary."""
        data = {
            "material_id": "mat-001",
            "title": "Test",
            "material_type": "academic_paper",
            "status": "ingested",
        }

        material = ResearchMaterial.from_dict(data)
        self.assertEqual(material.material_id, "mat-001")
        self.assertEqual(material.material_type, ResearchMaterialType.ACADEMIC_PAPER)


class TestResearchStore(unittest.TestCase):
    """Test ResearchStore persistence."""

    def setUp(self):
        """Set up temporary store directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.store = ResearchStore(store_dir=self.temp_dir)

    def tearDown(self):
        """Clean up temporary directory."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_store_material(self):
        """Test storing a research material."""
        material = ResearchMaterial(
            material_id="mat-001",
            title="Test Paper",
            ingestion_session_id="session-001",
            material_type=ResearchMaterialType.ACADEMIC_PAPER,
        )

        success, path = self.store.store_material(material)

        self.assertTrue(success)
        self.assertTrue(os.path.exists(path))
        self.assertIn("mat-001.json", path)

    def test_retrieve_material(self):
        """Test retrieving a stored material."""
        # Store material
        material = ResearchMaterial(
            material_id="mat-001",
            title="Test Paper",
            ingestion_session_id="session-001",
            material_type=ResearchMaterialType.ACADEMIC_PAPER,
        )
        self.store.store_material(material)

        # Retrieve it
        retrieved = self.store.retrieve_material("mat-001")

        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.material_id, "mat-001")
        self.assertEqual(retrieved.title, "Test Paper")

    def test_list_materials_by_session(self):
        """Test listing materials from a session."""
        # Store multiple materials
        for i in range(3):
            material = ResearchMaterial(
                material_id=f"mat-{i:03d}",
                title=f"Paper {i}",
                ingestion_session_id="session-001",
                material_type=ResearchMaterialType.ACADEMIC_PAPER,
            )
            self.store.store_material(material)

        materials = self.store.list_materials_by_session("session-001")
        self.assertEqual(len(materials), 3)

    def test_list_materials_by_type(self):
        """Test filtering materials by type."""
        # Store papers and repos
        paper = ResearchMaterial(
            material_id="paper-001",
            title="Paper",
            ingestion_session_id="session-001",
            material_type=ResearchMaterialType.ACADEMIC_PAPER,
        )
        repo = ResearchMaterial(
            material_id="repo-001",
            title="Repository",
            ingestion_session_id="session-001",
            material_type=ResearchMaterialType.CODE_REPOSITORY,
        )

        self.store.store_material(paper)
        self.store.store_material(repo)

        # List by type
        papers = self.store.list_materials_by_type(ResearchMaterialType.ACADEMIC_PAPER)
        repos = self.store.list_materials_by_type(ResearchMaterialType.CODE_REPOSITORY)

        self.assertEqual(len(papers), 1)
        self.assertEqual(len(repos), 1)

    def test_list_materials_by_status(self):
        """Test filtering materials by status."""
        # Store materials with different statuses
        material1 = ResearchMaterial(
            material_id="mat-001",
            title="Paper 1",
            ingestion_session_id="session-001",
            status=ResearchMaterialStatus.INGESTED,
        )
        material2 = ResearchMaterial(
            material_id="mat-002",
            title="Paper 2",
            ingestion_session_id="session-001",
            status=ResearchMaterialStatus.NORMALIZED,
        )

        self.store.store_material(material1)
        self.store.store_material(material2)

        # List by status
        ingested = self.store.list_materials_by_status(ResearchMaterialStatus.INGESTED)
        normalized = self.store.list_materials_by_status(
            ResearchMaterialStatus.NORMALIZED
        )

        self.assertEqual(len(ingested), 1)
        self.assertEqual(len(normalized), 1)

    def test_update_material_status(self):
        """Test updating material status."""
        material = ResearchMaterial(
            material_id="mat-001",
            title="Test",
            ingestion_session_id="session-001",
            status=ResearchMaterialStatus.INGESTED,
        )
        self.store.store_material(material)

        success, path = self.store.update_material_status(
            "mat-001", ResearchMaterialStatus.NORMALIZED
        )

        self.assertTrue(success)

        # Verify update
        updated = self.store.retrieve_material("mat-001")
        self.assertEqual(updated.status, ResearchMaterialStatus.NORMALIZED)

    def test_export_store_summary(self):
        """Test exporting store summary."""
        # Add some materials
        for i in range(2):
            material = ResearchMaterial(
                material_id=f"mat-{i:03d}",
                title=f"Paper {i}",
                ingestion_session_id="session-001",
                material_type=ResearchMaterialType.ACADEMIC_PAPER,
                status=ResearchMaterialStatus.INGESTED,
            )
            self.store.store_material(material)

        summary = self.store.export_store_summary()

        self.assertIn("total_materials", summary)
        self.assertEqual(summary["total_materials"], 2)
        self.assertIn("session-001", summary["by_session"])


if __name__ == "__main__":
    unittest.main()
