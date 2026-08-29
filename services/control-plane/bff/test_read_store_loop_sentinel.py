
"""Unit tests for ServiceBackedReadAdapter loop run and sentinel finding methods."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

BFF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BFF_DIR))

# Deliberate exception: this module is a genuine unit test of
# ServiceBackedReadAdapter's own loop-run/sentinel-finding derivation logic
# (parallel to the CanonicalSnapshotAdapter internals carve-out), not a
# composite-contract test that merely uses read_store as fixture backing.
from read_store import ServiceBackedReadAdapter  # noqa: E402

# Mock data for underlying 'incidents' dataset
MOCK_INCIDENTS_DATA = {
    "inc-loop-1": {
        "incident_id": "inc-loop-1",
        "title": "Loop Anomaly Detected",
        "status": "open",
        "severity": "high",
        "runtime_id": "rt-loop-1",
        "binding_id": "binding-loop-1",
        "capital_pool_id": "pool-main",
        "artifact_id": "artifact-loop-1",
        "artifact_version": "1.0.0",
        "created_at": "2026-05-09T10:00:00Z",
        "resolved_at": None,
    },
    "inc-sentinel-1": {
        "incident_id": "inc-sentinel-1",
        "title": "Sentinel Finding Triggered",
        "status": "open",
        "severity": "medium",
        "runtime_id": "rt-sentinel-1",
        "binding_id": "binding-sentinel-1",
        "capital_pool_id": "pool-secondary",
        "artifact_id": "artifact-sentinel-1",
        "artifact_version": "2.0.0",
        "created_at": "2026-05-09T11:00:00Z",
        "resolved_at": None,
    },
    "inc-normal-1": {
        "incident_id": "inc-normal-1",
        "title": "Normal Incident",
        "status": "resolved",
        "severity": "low",
        "runtime_id": "rt-normal-1",
        "binding_id": "binding-normal-1",
        "capital_pool_id": "pool-main",
        "artifact_id": "artifact-normal-1",
        "artifact_version": "1.1.0",
        "created_at": "2026-05-09T12:00:00Z",
        "resolved_at": "2026-05-09T12:30:00Z",
    },
}

# Mock data for loop runs and sentinel findings directly
MOCK_LOOP_RUNS = {
    "loop-run-1": {
        "id": "loop-run-1",
        "status": "active",
        "activePeriod": {"start": "2026-05-09T10:00:00Z", "end": None},
        "derived_from_incident_id": "inc-loop-1",
    },
    "loop-run-2": {
        "id": "loop-run-2",
        "status": "completed",
        "activePeriod": {"start": "2026-05-08T09:00:00Z", "end": "2026-05-08T09:30:00Z"},
        "derived_from_incident_id": "inc-normal-1",
    },
}

MOCK_SENTINEL_FINDINGS = {
    "sentinel-finding-1": {
        "id": "sentinel-finding-1",
        "status": "active",
        "derived_from_incident_id": "inc-sentinel-1",
    },
    "sentinel-finding-2": {
        "id": "sentinel-finding-2",
        "status": "resolved",
        "derived_from_incident_id": "inc-normal-1",
    },
}


# Helper function to create a temporary JSON file
@pytest.fixture
def temp_read_store_file(tmp_path: Path) -> Path:
    file_path = tmp_path / "read_surfaces.json"
    data = {
        "incidents": MOCK_INCIDENTS_DATA,
        "loop_runs": MOCK_LOOP_RUNS,
        "sentinel_findings": MOCK_SENTINEL_FINDINGS,
    }
    with open(file_path, "w") as f:
        json.dump(data, f)
    return file_path


# Mocking ServiceBackedReadAdapter._load_dataset to return controlled data
@pytest.fixture
def mock_read_store(temp_read_store_file: Path):
    # Instantiate ServiceBackedReadAdapter with the temporary file
    adapter = ServiceBackedReadAdapter(snapshot_path=temp_read_store_file)

    # Monkeypatch _load_dataset to return specific data for 'incidents'
    original_load_dataset = adapter._load_dataset

    def _mocked_load_dataset(dataset: str, *, include_snapshot_fallback: bool = True) -> tuple[bool, Dict[str, Dict[str, Any]]]:
        if dataset == "incidents":
            return True, MOCK_INCIDENTS_DATA
        elif dataset == "loop_runs":
            return True, MOCK_LOOP_RUNS
        elif dataset == "sentinel_findings":
            return True, MOCK_SENTINEL_FINDINGS
        else:
            return original_load_dataset(dataset, include_snapshot_fallback=include_snapshot_fallback)

    adapter._load_dataset = _mocked_load_dataset
    return adapter

# --- Unit Tests for ServiceBackedReadAdapter methods ---

def test_list_loop_runs_derivation_from_incidents(mock_read_store: ServiceBackedReadAdapter):
    """Test list_loop_runs correctly derives data from available incidents."""
    # Mock _load_dataset to return incidents and ensure derivation occurs
    original_load_dataset = mock_read_store._load_dataset
    def _mock_incidents_only_load_dataset(dataset: str, *, include_snapshot_fallback: bool = True) -> tuple[bool, Dict[str, Dict[str, Any]]]:
        if dataset == "incidents":
            return True, MOCK_INCIDENTS_DATA
        elif dataset == "loop_runs": # Mock loop_runs as unavailable to test derivation
            return False, {}
        return original_load_dataset(dataset, include_snapshot_fallback=include_snapshot_fallback)

    mock_read_store._load_dataset = _mock_incidents_only_load_dataset

    available, loop_runs = mock_read_store.list_loop_runs()
    assert available is True
    assert isinstance(loop_runs, list)
    # Expecting loop_runs derived from inc-loop-1 and inc-normal-1
    assert len(loop_runs) == 2

    # Check one derived loop run
    derived_loop = next((lr for lr in loop_runs if lr.get("derived_from_incident_id") == "inc-loop-1"), None)
    assert derived_loop is not None
    assert derived_loop["id"] == "inc-loop-1" # ID might be derived from incident_id directly if no other source
    assert derived_loop["status"] == "open"
    assert derived_loop["activePeriod"]["start"] == "2026-05-09T10:00:00Z"
    assert derived_loop["derived_from_incident_id"] == "inc-loop-1"
    assert derived_loop["runtime_id"] == "rt-loop-1"
    assert derived_loop["binding_id"] == "binding-loop-1"

    # Check another derived loop run
    derived_loop_normal = next((lr for lr in loop_runs if lr.get("derived_from_incident_id") == "inc-normal-1"), None)
    assert derived_loop_normal is not None
    assert derived_loop_normal["id"] == "inc-normal-1"
    assert derived_loop_normal["status"] == "resolved"
    assert derived_loop_normal["activePeriod"]["end"] == "2026-05-09T12:30:00Z"

    # Restore original method
    mock_read_store._load_dataset = original_load_dataset

def test_list_loop_runs_fallback_to_mock_data(mock_read_store: ServiceBackedReadAdapter):
    """Test list_loop_runs falls back to mock_loop_runs if incidents are unavailable."""
    # Mock _load_dataset to make incidents unavailable, but loop_runs (mock) available
    original_load_dataset = mock_read_store._load_dataset
    def _mock_no_incidents_load_dataset(dataset: str, *, include_snapshot_fallback: bool = True) -> tuple[bool, Dict[str, Dict[str, Any]]]:
        if dataset == "incidents":
            return False, {}
        elif dataset == "loop_runs": # Ensure mock loop_runs are available
            return True, MOCK_LOOP_RUNS
        return original_load_dataset(dataset, include_snapshot_fallback=include_snapshot_fallback)

    mock_read_store._load_dataset = _mock_no_incidents_load_dataset

    available, loop_runs = mock_read_store.list_loop_runs()
    assert available is True
    assert isinstance(loop_runs, list)
    # Expecting loop_runs from MOCK_LOOP_RUNS
    assert len(loop_runs) == 2

    # Check one loop run from mock data
    mock_loop = next((lr for lr in loop_runs if lr.get("id") == "loop-run-1"), None)
    assert mock_loop is not None
    assert mock_loop["id"] == "loop-run-1"
    assert mock_loop["status"] == "active"
    assert mock_loop["derived_from_incident_id"] == "inc-loop-1"

    # Restore original method
    mock_read_store._load_dataset = original_load_dataset

def test_list_loop_runs_unavailable(mock_read_store: ServiceBackedReadAdapter):
    """Test list_loop_runs returns unavailable if no sources are found."""
    # Mock _load_dataset to make both incidents and loop_runs unavailable
    original_load_dataset = mock_read_store._load_dataset
    def _mock_all_unavailable_load_dataset(dataset: str, *, include_snapshot_fallback: bool = True) -> tuple[bool, Dict[str, Dict[str, Any]]]:
        if dataset in ("incidents", "loop_runs"):
            return False, {}
        return original_load_dataset(dataset, include_snapshot_fallback=include_snapshot_fallback)

    mock_read_store._load_dataset = _mock_all_unavailable_load_dataset

    available, loop_runs = mock_read_store.list_loop_runs()
    assert available is False
    assert loop_runs == []

    # Restore original method
    mock_read_store._load_dataset = original_load_dataset

def test_get_loop_run_derivation_from_incidents(mock_read_store: ServiceBackedReadAdapter):
    """Test get_loop_run correctly derives a specific loop run from incidents."""
    # Mock _load_dataset to return incidents and ensure derivation occurs
    original_load_dataset = mock_read_store._load_dataset
    def _mock_incidents_only_load_dataset(dataset: str, *, include_snapshot_fallback: bool = True) -> tuple[bool, Dict[str, Dict[str, Any]]]:
        if dataset == "incidents":
            return True, MOCK_INCIDENTS_DATA
        elif dataset == "loop_runs": # Mock loop_runs as unavailable
            return False, {}
        return original_load_dataset(dataset, include_snapshot_fallback=include_snapshot_fallback)

    mock_read_store._load_dataset = _mock_incidents_only_load_dataset

    # Test with an existing incident that should derive a loop run
    available, loop_run = mock_read_store.get_loop_run("inc-loop-1") # Test with incident ID directly
    assert available is True
    assert loop_run is not None
    assert loop_run["id"] == "inc-loop-1" # ID should be incident_id when derived
    assert loop_run["status"] == "open"
    assert loop_run["derived_from_incident_id"] == "inc-loop-1"

    # Test with a generated loop_run ID, expecting it to map back to incident
    available, loop_run = mock_read_store.get_loop_run("loop-run-1") # Test with generated loop_run ID
    assert available is True
    assert loop_run is not None
    assert loop_run["id"] == "loop-run-1" # Should return the expected loop run ID format
    assert loop_run["derived_from_incident_id"] == "inc-loop-1"

    # Test for a non-existent loop run
    available, loop_run_not_found = mock_read_store.get_loop_run("non-existent-loop-run")
    assert available is True
    assert loop_run_not_found is None

    # Restore original method
    mock_read_store._load_dataset = original_load_dataset

def test_get_loop_run_fallback_to_mock_data(mock_read_store: ServiceBackedReadAdapter):
    """Test get_loop_run falls back to mock_loop_runs if incidents are unavailable."""
    # Mock _load_dataset to make incidents unavailable, but loop_runs (mock) available
    original_load_dataset = mock_read_store._load_dataset
    def _mock_no_incidents_load_dataset(dataset: str, *, include_snapshot_fallback: bool = True) -> tuple[bool, Dict[str, Dict[str, Any]]]:
        if dataset == "incidents":
            return False, {}
        elif dataset == "loop_runs": # Ensure mock loop_runs are available
            return True, MOCK_LOOP_RUNS
        return original_load_dataset(dataset, include_snapshot_fallback=include_snapshot_fallback)

    mock_read_store._load_dataset = _mock_no_incidents_load_dataset

    # Test with an ID that exists in MOCK_LOOP_RUNS
    available, loop_run = mock_read_store.get_loop_run("loop-run-1")
    assert available is True
    assert loop_run is not None
    assert loop_run["id"] == "loop-run-1"
    assert loop_run["status"] == "active"

    # Test for a non-existent loop run in mock data
    available, loop_run_not_found = mock_read_store.get_loop_run("non-existent-loop-run")
    assert available is True
    assert loop_run_not_found is None

    # Restore original method
    mock_read_store._load_dataset = original_load_dataset

def test_get_loop_run_unavailable(mock_read_store: ServiceBackedReadAdapter):
    """Test get_loop_run returns unavailable if no sources are found."""
    # Mock _load_dataset to make both incidents and loop_runs unavailable
    original_load_dataset = mock_read_store._load_dataset
    def _mock_all_unavailable_load_dataset(dataset: str, *, include_snapshot_fallback: bool = True) -> tuple[bool, Dict[str, Dict[str, Any]]]:
        if dataset in ("incidents", "loop_runs"):
            return False, {}
        return original_load_dataset(dataset, include_snapshot_fallback=include_snapshot_fallback)

    mock_read_store._load_dataset = _mock_all_unavailable_load_dataset

    available, loop_run = mock_read_store.get_loop_run("loop-run-1")
    assert available is False
    assert loop_run is None

    # Restore original method
    mock_read_store._load_dataset = original_load_dataset

def test_list_sentinel_findings_derivation_from_incidents(mock_read_store: ServiceBackedReadAdapter):
    """Test list_sentinel_findings correctly derives data from available incidents."""
    # Mock _load_dataset to return incidents and ensure derivation occurs
    original_load_dataset = mock_read_store._load_dataset
    def _mock_incidents_only_load_dataset(dataset: str, *, include_snapshot_fallback: bool = True) -> tuple[bool, Dict[str, Dict[str, Any]]]:
        if dataset == "incidents":
            return True, MOCK_INCIDENTS_DATA
        elif dataset == "sentinel_findings": # Mock findings as unavailable to test derivation
            return False, {}
        return original_load_dataset(dataset, include_snapshot_fallback=include_snapshot_fallback)

    mock_read_store._load_dataset = _mock_incidents_only_load_dataset

    available, findings = mock_read_store.list_sentinel_findings()
    assert available is True
    assert isinstance(findings, list)
    # Expecting findings derived from inc-sentinel-1 and inc-normal-1
    assert len(findings) == 2

    # Check one derived finding
    derived_finding = next((f for f in findings if f.get("derived_from_incident_id") == "inc-sentinel-1"), None)
    assert derived_finding is not None
    assert derived_finding["id"] == "inc-sentinel-1" # ID might be incident_id directly
    assert derived_finding["status"] == "open"
    assert derived_finding["derived_from_incident_id"] == "inc-sentinel-1"
    assert derived_finding["runtime_id"] == "rt-sentinel-1"
    assert derived_finding["binding_id"] == "binding-sentinel-1"

    # Check another derived finding
    derived_finding_normal = next((f for f in findings if f.get("derived_from_incident_id") == "inc-normal-1"), None)
    assert derived_finding_normal is not None
    assert derived_finding_normal["id"] == "inc-normal-1"
    assert derived_finding_normal["status"] == "resolved"

    # Restore original method
    mock_read_store._load_dataset = original_load_dataset

def test_list_sentinel_findings_fallback_to_mock_data(mock_read_store: ServiceBackedReadAdapter):
    """Test list_sentinel_findings falls back to mock_sentinel_findings if incidents are unavailable."""
    # Mock _load_dataset to make incidents unavailable, but sentinel_findings (mock) available
    original_load_dataset = mock_read_store._load_dataset
    def _mock_no_incidents_load_dataset(dataset: str, *, include_snapshot_fallback: bool = True) -> tuple[bool, Dict[str, Dict[str, Any]]]:
        if dataset == "incidents":
            return False, {}
        elif dataset == "sentinel_findings": # Ensure mock findings are available
            return True, MOCK_SENTINEL_FINDINGS
        return original_load_dataset(dataset, include_snapshot_fallback=include_snapshot_fallback)

    mock_read_store._load_dataset = _mock_no_incidents_load_dataset

    available, findings = mock_read_store.list_sentinel_findings()
    assert available is True
    assert isinstance(findings, list)
    # Expecting findings from MOCK_SENTINEL_FINDINGS
    assert len(findings) == 2

    # Check one finding from mock data
    mock_finding = next((f for f in findings if f.get("id") == "sentinel-finding-1"), None)
    assert mock_finding is not None
    assert mock_finding["id"] == "sentinel-finding-1"
    assert mock_finding["status"] == "active"
    assert mock_finding["derived_from_incident_id"] == "inc-sentinel-1"

    # Restore original method
    mock_read_store._load_dataset = original_load_dataset

def test_list_sentinel_findings_unavailable(mock_read_store: ServiceBackedReadAdapter):
    """Test list_sentinel_findings returns unavailable if no sources are found."""
    # Mock _load_dataset to make both incidents and sentinel_findings unavailable
    original_load_dataset = mock_read_store._load_dataset
    def _mock_all_unavailable_load_dataset(dataset: str, *, include_snapshot_fallback: bool = True) -> tuple[bool, Dict[str, Dict[str, Any]]]:
        if dataset in ("incidents", "sentinel_findings"):
            return False, {}
        return original_load_dataset(dataset, include_snapshot_fallback=include_snapshot_fallback)

    mock_read_store._load_dataset = _mock_all_unavailable_load_dataset

    available, findings = mock_read_store.list_sentinel_findings()
    assert available is False
    assert findings == []

    # Restore original method
    mock_read_store._load_dataset = original_load_dataset

def test_get_sentinel_finding_derivation_from_incidents(mock_read_store: ServiceBackedReadAdapter):
    """Test get_sentinel_finding correctly derives a specific finding from incidents."""
    # Mock _load_dataset to return incidents and ensure derivation occurs
    original_load_dataset = mock_read_store._load_dataset
    def _mock_incidents_only_load_dataset(dataset: str, *, include_snapshot_fallback: bool = True) -> tuple[bool, Dict[str, Dict[str, Any]]]:
        if dataset == "incidents":
            return True, MOCK_INCIDENTS_DATA
        elif dataset == "sentinel_findings": # Mock findings as unavailable
            return False, {}
        return original_load_dataset(dataset, include_snapshot_fallback=include_snapshot_fallback)

    mock_read_store._load_dataset = _mock_incidents_only_load_dataset

    # Test with an existing incident that should derive a finding
    available, finding = mock_read_store.get_sentinel_finding("inc-sentinel-1") # Test with incident ID
    assert available is True
    assert finding is not None
    assert finding["id"] == "inc-sentinel-1" # ID should be incident_id when derived
    assert finding["status"] == "open"
    assert finding["derived_from_incident_id"] == "inc-sentinel-1"
    assert finding["runtime_id"] == "rt-sentinel-1"
    assert finding["binding_id"] == "binding-sentinel-1"

    # Test with a generated finding ID, expecting it to map back to incident
    available, finding = mock_read_store.get_sentinel_finding("sentinel-finding-1") # Test with generated finding ID
    assert available is True
    assert finding is not None
    assert finding["id"] == "sentinel-finding-1" # Should return the expected finding ID format
    assert finding["derived_from_incident_id"] == "inc-sentinel-1"

    # Test for a non-existent finding
    available, finding_not_found = mock_read_store.get_sentinel_finding("non-existent-finding")
    assert available is True
    assert finding_not_found is None

    # Restore original method
    mock_read_store._load_dataset = original_load_dataset

def test_get_sentinel_finding_fallback_to_mock_data(mock_read_store: ServiceBackedReadAdapter):
    """Test get_sentinel_finding falls back to mock_sentinel_findings if incidents are unavailable."""
    # Mock _load_dataset to make incidents unavailable, but sentinel_findings (mock) available
    original_load_dataset = mock_read_store._load_dataset
    def _mock_no_incidents_load_dataset(dataset: str, *, include_snapshot_fallback: bool = True) -> tuple[bool, Dict[str, Dict[str, Any]]]:
        if dataset == "incidents":
            return False, {}
        elif dataset == "sentinel_findings": # Ensure mock findings are available
            return True, MOCK_SENTINEL_FINDINGS
        return original_load_dataset(dataset, include_snapshot_fallback=include_snapshot_fallback)

    mock_read_store._load_dataset = _mock_no_incidents_load_dataset

    # Test with an ID that exists in MOCK_SENTINEL_FINDINGS
    available, finding = mock_read_store.get_sentinel_finding("sentinel-finding-1")
    assert available is True
    assert finding is not None
    assert finding["id"] == "sentinel-finding-1"
    assert finding["status"] == "active"

    # Test for a non-existent finding in mock data
    available, finding_not_found = mock_read_store.get_sentinel_finding("non-existent-finding")
    assert available is True
    assert finding_not_found is None

    # Restore original method
    mock_read_store._load_dataset = original_load_dataset

def test_get_sentinel_finding_unavailable(mock_read_store: ServiceBackedReadAdapter):
    """Test get_sentinel_finding returns unavailable if no sources are found."""
    # Mock _load_dataset to make both incidents and sentinel_findings unavailable
    original_load_dataset = mock_read_store._load_dataset
    def _mock_all_unavailable_load_dataset(dataset: str, *, include_snapshot_fallback: bool = True) -> tuple[bool, Dict[str, Dict[str, Any]]]:
        if dataset in ("incidents", "sentinel_findings"):
            return False, {}
        return original_load_dataset(dataset, include_snapshot_fallback=include_snapshot_fallback)

    mock_read_store._load_dataset = _mock_all_unavailable_load_dataset

    available, finding = mock_read_store.get_sentinel_finding("sentinel-finding-1")
    assert available is False
    assert finding is None

    # Restore original method
    mock_read_store._load_dataset = original_load_dataset


def test_list_loop_runs_empty_incidents_source_returns_available(mock_read_store: ServiceBackedReadAdapter):
    """When incidents is available but empty, list_loop_runs must return (True, []).
    Regression for: if avail_inc and incidents (falsy) falling through to loop_runs when
    incidents source exists but has zero records."""
    original_load_dataset = mock_read_store._load_dataset

    def _mock_empty_incidents(dataset: str, *, include_snapshot_fallback: bool = True) -> tuple[bool, Dict[str, Dict[str, Any]]]:
        if dataset == "incidents":
            return True, {}  # available but empty
        if dataset == "loop_runs":
            return False, {}
        return original_load_dataset(dataset, include_snapshot_fallback=include_snapshot_fallback)

    mock_read_store._load_dataset = _mock_empty_incidents

    available, loop_runs = mock_read_store.list_loop_runs()
    assert available is True, "incidents source is available; must report available even when empty"
    assert loop_runs == []

    mock_read_store._load_dataset = original_load_dataset


def test_list_sentinel_findings_empty_incidents_source_returns_available(mock_read_store: ServiceBackedReadAdapter):
    """When incidents is available but empty, list_sentinel_findings must return (True, []).
    Regression for: if avail_inc and incidents (falsy) falling through to sentinel_findings when
    incidents source exists but has zero records."""
    original_load_dataset = mock_read_store._load_dataset

    def _mock_empty_incidents(dataset: str, *, include_snapshot_fallback: bool = True) -> tuple[bool, Dict[str, Dict[str, Any]]]:
        if dataset == "incidents":
            return True, {}  # available but empty
        if dataset == "sentinel_findings":
            return False, {}
        return original_load_dataset(dataset, include_snapshot_fallback=include_snapshot_fallback)

    mock_read_store._load_dataset = _mock_empty_incidents

    available, findings = mock_read_store.list_sentinel_findings()
    assert available is True, "incidents source is available; must report available even when empty"
    assert findings == []

    mock_read_store._load_dataset = original_load_dataset

# --- Integration Tests for BFF Endpoints ---

# NOTE: These tests are conceptual. Actual integration tests would need to mock
# the ServiceBackedReadAdapter or run against a test instance of the BFF app
# that uses a controlled read_store.

# Mocking the ServiceBackedReadAdapter for integration tests
# This requires more sophisticated patching or a dependency injection setup.
# For now, we'll outline the conceptual tests.

# @pytest.fixture(scope="module")
# def test_client():
#     # Create a test client for the FastAPI app
#     # This might involve overriding dependencies to use a mock read_store
#     with TestClient(bff_app) as client:
#         yield client

# def test_get_v5_control_room_with_mocked_read_store(test_client: TestClient):
#     """Test GET /bff/v5/control-room uses the new read_store methods."""
#     # Mock ServiceBackedReadAdapter.list_loop_runs and ServiceBackedReadAdapter.list_sentinel_findings
#     # to return specific data (e.g., derived from incidents, or mock data, or unavailable).
#     # Expected behavior:
#     # - If read_store returns data, use that data for 'loops' and 'sentinelFindings'.
#     # - If read_store returns unavailable, use placeholder/empty data and set status to 'degraded'.
#     pass # Placeholder

# def test_get_v5_loop_runs_endpoint(test_client: TestClient):
#     """Test GET /bff/v5/loop-runs uses the read_store method and handles availability."""
#     # Mock ServiceBackedReadAdapter.list_loop_runs
#     # Assert response status and body content.
#     pass # Placeholder

# def test_get_v5_loop_run_endpoint(test_client: TestClient):
#     """Test GET /bff/v5/loop-runs/{loop_id} handles found and not found cases."""
#     # Mock ServiceBackedReadAdapter.get_loop_run
#     # Assert 200 OK for found, 404 for not found.
#     pass # Placeholder

# def test_get_v5_sentinel_findings_endpoint(test_client: TestClient):
#     """Test GET /bff/v5/sentinel/findings uses the read_store method and handles availability."""
#     # Mock ServiceBackedReadAdapter.list_sentinel_findings
#     # Assert response status and body content.
#     pass # Placeholder

# def test_get_v5_sentinel_finding_endpoint(test_client: TestClient):
#     """Test GET /bff/v5/sentinel/findings/{finding_id} handles found and not found cases."""
#     # Mock ServiceBackedReadAdapter.get_sentinel_finding
#     # Assert 200 OK for found, 404 for not found.
#     pass # Placeholder
