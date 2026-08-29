"""Focused tests for the typed Management/PPL/SEM test doubles.

Validates acceptance criteria for ACG-RS-TYPED-TEST-SEAM-20260829:
1. Fixture doubles model Management (OODA) and PPL (Persona/Capital/Runtime)
   mutable test data without importing or constructing `ReadSurfaceStore`.
2. `ManagementFixtureBuilder.to_kwargs()` / `PplFixtureBuilder.to_kwargs()`
   produce dicts that `ports.create_in_memory_read_surface_ports` accepts.
3. `SemDatasetReaderTestDouble` satisfies the `dataset_source` /
   `read_dataset_records` contract for every named SEM caller dataset, and
   rejects any dataset name that has no named caller.
4. No generic forwarding/compatibility facade: unknown domains/datasets raise
   explicit errors instead of silently returning empty/default data.
"""
from __future__ import annotations

import ast
from pathlib import Path
import sys
import unittest

BFF_DIR = Path(__file__).resolve().parent.parent
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from management_projection_test_doubles import (
    KNOWN_SEM_DATASETS,
    ManagementFixtureBuilder,
    PplFixtureBuilder,
    SemDatasetReaderTestDouble,
)
from ports import create_in_memory_read_surface_ports


class TestManagementFixtureBuilder(unittest.TestCase):
    def test_builds_typed_ooda_management_records(self) -> None:
        builder = ManagementFixtureBuilder()
        builder.add_ooda_packet("pkt-1", strategy_id="strat-1", runtime_id="rt-1")
        builder.add_intervention("int-1", action="pause")
        builder.add_synthesis_conflict_log("log-1", conflict_type="divergence")
        builder.add_approval_decision("app-1", state="pending")
        builder.add_deployment_diff("dp-1", diff="allocated +50k")

        kwargs = builder.to_kwargs()
        self.assertEqual(kwargs["ooda_packets"][0]["id"], "pkt-1")
        self.assertEqual(kwargs["interventions"][0]["id"], "int-1")
        self.assertEqual(kwargs["synthesis_conflict_logs"][0]["id"], "log-1")
        self.assertEqual(kwargs["approval_decisions"][0]["id"], "app-1")
        self.assertEqual(kwargs["deployment_diffs"]["dp-1"]["diff"], "allocated +50k")

    def test_to_kwargs_feeds_read_surface_ports(self) -> None:
        builder = ManagementFixtureBuilder()
        builder.add_ooda_packet("pkt-1")
        builder.add_approval_decision("app-1", state="pending")

        ports = create_in_memory_read_surface_ports(ooda_management_kwargs=builder.to_kwargs())
        self.assertEqual(len(ports.list_ooda_packets()), 1)
        self.assertEqual(len(ports.list_governance_review_queue_items()), 1)

    def test_to_kwargs_returns_independent_snapshots(self) -> None:
        builder = ManagementFixtureBuilder()
        builder.add_ooda_packet("pkt-1")
        first = builder.to_kwargs()
        builder.add_ooda_packet("pkt-2")
        second = builder.to_kwargs()
        self.assertEqual(len(first["ooda_packets"]), 1)
        self.assertEqual(len(second["ooda_packets"]), 2)


class TestPplFixtureBuilder(unittest.TestCase):
    def test_builds_typed_persona_capital_runtime_records(self) -> None:
        builder = PplFixtureBuilder()
        builder.add_persona("p-1", name="Alpha")
        builder.add_capital_pool("cp-1", name="Main Pool")
        builder.add_binding("b-1", "p-1", "cp-1")
        builder.add_runtime_binding("rt-1", "b-1", status="running")
        builder.add_ranking("rk-1", score=98.5)
        builder.add_persona_league_entry("p-1", tier="gold")
        builder.add_rebalance("reb-1", status="executed")
        builder.add_capital_allocation("ca-1", amount=500000)
        builder.add_containment("ct-1", status="contained")

        kwargs = builder.to_kwargs()
        self.assertEqual(kwargs["personas"][0]["persona_id"], "p-1")
        self.assertEqual(kwargs["capital_pools"][0]["pool_id"], "cp-1")
        self.assertEqual(kwargs["bindings"][0]["binding_id"], "b-1")
        self.assertEqual(kwargs["runtime_bindings"][0]["runtime_id"], "rt-1")
        self.assertEqual(kwargs["rankings"][0]["id"], "rk-1")
        self.assertEqual(kwargs["persona_league"][0]["persona_id"], "p-1")
        self.assertEqual(kwargs["rebalances"][0]["id"], "reb-1")
        self.assertEqual(kwargs["capital_allocations"][0]["id"], "ca-1")
        self.assertEqual(kwargs["containments"][0]["id"], "ct-1")

    def test_to_kwargs_feeds_read_surface_ports(self) -> None:
        builder = PplFixtureBuilder()
        builder.add_persona("p-1", name="Alpha")
        builder.add_capital_pool("cp-1")

        ports = create_in_memory_read_surface_ports(persona_capital_runtime_kwargs=builder.to_kwargs())
        personas = ports.list_personas()
        self.assertEqual(len(personas), 1)
        self.assertEqual(personas[0]["persona_id"], "p-1")
        self.assertEqual(len(ports.list_capital_pools()), 1)


class TestSemDatasetReaderTestDouble(unittest.TestCase):
    def test_known_datasets_cover_every_named_main_py_caller(self) -> None:
        expected = {
            "agora_sessions",
            "agora_skill_coaching_sessions",
            "agora_persona_lab_runs",
            "postmortems",
            "agora_evaluation_suites",
            "agora_evaluation_runs",
            "insight_cards",
            "agora_signals",
            "research_tickets",
        }
        self.assertEqual(KNOWN_SEM_DATASETS, frozenset(expected))

    def test_dataset_source_missing_before_any_records(self) -> None:
        reader = SemDatasetReaderTestDouble()
        self.assertEqual(reader.dataset_source("agora_sessions"), "missing")
        self.assertEqual(reader.read_dataset_records("agora_sessions"), [])

    def test_dataset_source_local_snapshot_after_records_set(self) -> None:
        reader = SemDatasetReaderTestDouble()
        reader.set_dataset_records("insight_cards", [{"id": "card-1", "summary": "Momentum breakdown"}])
        self.assertEqual(reader.dataset_source("insight_cards"), "local_snapshot")
        records = reader.read_dataset_records("insight_cards")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["id"], "card-1")

    def test_read_dataset_records_returns_copies_not_live_references(self) -> None:
        reader = SemDatasetReaderTestDouble()
        reader.set_dataset_records("agora_signals", [{"id": "sig-1"}])
        records = reader.read_dataset_records("agora_signals")
        records[0]["id"] = "mutated"
        self.assertEqual(reader.read_dataset_records("agora_signals")[0]["id"], "sig-1")

    def test_unknown_dataset_rejected_by_every_method(self) -> None:
        reader = SemDatasetReaderTestDouble()
        with self.assertRaises(KeyError):
            reader.set_dataset_records("not_a_real_dataset", [])
        with self.assertRaises(KeyError):
            reader.dataset_source("not_a_real_dataset")
        with self.assertRaises(KeyError):
            reader.read_dataset_records("not_a_real_dataset")


class TestNoReadSurfaceStoreCoupling(unittest.TestCase):
    def test_module_has_no_read_surface_store_import_or_usage(self) -> None:
        module_path = TESTS_DIR / "management_projection_test_doubles.py"
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "read_store":
                for alias in node.names:
                    self.assertNotEqual(alias.name, "ReadSurfaceStore")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(alias.name, "read_store")
            elif isinstance(node, ast.Name) and node.id == "ReadSurfaceStore":
                self.fail(f"Forbidden ReadSurfaceStore identifier at line {node.lineno}")


if __name__ == "__main__":
    unittest.main()
