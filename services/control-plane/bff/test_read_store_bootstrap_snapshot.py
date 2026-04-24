from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from read_store import ReadSurfaceStore, _default_read_data


def test_store_reads_canonical_and_service_datasets_from_bff_snapshot_when_env_stores_are_absent() -> None:
    with tempfile.TemporaryDirectory() as td:
        bff_dir = Path(td)
        snapshot_path = bff_dir / "read_surfaces.json"
        snapshot_path.write_text(
            json.dumps(_default_read_data(), indent=2, ensure_ascii=True),
            encoding="utf-8",
        )

        with mock.patch.dict(
            os.environ,
            {
                "BFF_DATA_DIR": str(bff_dir),
                "PANTHEON_GOVERNANCE_DATA_DIR": "",
                "PANTHEON_RUNTIME_DATA_DIR": "",
                "INCIDENTS_DATA_DIR": "",
                "POSTMORTEMS_DATA_DIR": "",
                "EVOLUTION_DATA_DIR": "",
            },
            clear=False,
        ):
            store = ReadSurfaceStore(str(snapshot_path), allow_local_snapshot_fallback=False)

            plan = store.get_deployment_plan("plan-F-042")
            assert plan is not None
            assert plan["plan_id"] == "plan-F-042"

            bindings = store.list_bindings(persona_id="persona-alpha")
            assert [binding["id"] for binding in bindings] == ["binding-042"]

            incident = store.get_incident("inc-20260410-001")
            assert incident is not None
            assert incident["incident_id"] == "inc-20260410-001"

            postmortem = store.get_postmortem_by_incident("inc-20260409-002")
            assert postmortem is not None
            assert postmortem["postmortem_id"] == "pm-20260409-002"
