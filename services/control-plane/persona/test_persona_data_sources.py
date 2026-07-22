"""
Unit tests for RequiredDataSource and Persona.required_data_sources.

Task: LOOP-AUTO-SRC-001
Owner: Claude
Reviewer: Codex

Acceptance criteria:
  1. Persona declares required_data_sources with dataset, market, cadence, source_class.
  2. Schema carries connector_candidates and policy_gates.
  3. seed_only entries must not count as live data source binding.

Run:
    python3 -m unittest discover -s services/control-plane/persona -p 'test_*.py'
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from persona_registry import (
    DataSourceCadence,
    DataSourceClass,
    Persona,
    PersonaRegistryError,
    RequiredDataSource,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rds(**kwargs) -> RequiredDataSource:
    defaults = dict(
        dataset="tw_price_daily",
        market="TW",
        cadence="daily",
        source_class="live_pull",
    )
    defaults.update(kwargs)
    return RequiredDataSource(**defaults)


def _make_persona(**kwargs) -> Persona:
    defaults = dict(
        persona_id="persona-alpha",
        name="Alpha Momentum",
        mandate="Execute momentum strategies on TW equities",
        lifecycle_state="research_only",
        created_at="2026-06-01T00:00:00Z",
    )
    defaults.update(kwargs)
    return Persona(**defaults)


# ---------------------------------------------------------------------------
# DataSourceClass
# ---------------------------------------------------------------------------

class TestDataSourceClass(unittest.TestCase):

    def test_live_push_is_live_binding(self):
        self.assertTrue(DataSourceClass.LIVE_PUSH.is_live_binding())

    def test_live_pull_is_live_binding(self):
        self.assertTrue(DataSourceClass.LIVE_PULL.is_live_binding())

    def test_seed_only_is_not_live_binding(self):
        self.assertFalse(DataSourceClass.SEED_ONLY.is_live_binding())


# ---------------------------------------------------------------------------
# RequiredDataSource construction
# ---------------------------------------------------------------------------

class TestRequiredDataSourceConstruction(unittest.TestCase):

    def test_minimal_valid_entry(self):
        rds = _make_rds()
        self.assertEqual(rds.dataset, "tw_price_daily")
        self.assertEqual(rds.market, "TW")
        self.assertEqual(rds.cadence, "daily")
        self.assertEqual(rds.source_class, "live_pull")
        self.assertEqual(rds.connector_candidates, [])
        self.assertEqual(rds.policy_gates, [])

    def test_with_connector_candidates_and_gates(self):
        rds = _make_rds(
            connector_candidates=["twse-http-v1", "finmind-http-v1"],
            policy_gates=["require_connector_approved", "require_schedule_active"],
        )
        self.assertEqual(rds.connector_candidates, ["twse-http-v1", "finmind-http-v1"])
        self.assertEqual(rds.policy_gates, ["require_connector_approved", "require_schedule_active"])

    def test_all_cadences_accepted(self):
        for cadence in DataSourceCadence:
            rds = _make_rds(cadence=cadence.value)
            self.assertEqual(rds.cadence, cadence.value)

    def test_all_source_classes_accepted(self):
        for sc in DataSourceClass:
            rds = _make_rds(source_class=sc.value)
            self.assertEqual(rds.source_class, sc.value)

    def test_invalid_cadence_raises(self):
        with self.assertRaises(PersonaRegistryError):
            _make_rds(cadence="fortnightly")

    def test_invalid_source_class_raises(self):
        with self.assertRaises(PersonaRegistryError):
            _make_rds(source_class="batch")

    def test_blank_dataset_raises(self):
        with self.assertRaises(PersonaRegistryError):
            _make_rds(dataset="   ")

    def test_blank_market_raises(self):
        with self.assertRaises(PersonaRegistryError):
            _make_rds(market="")

    def test_is_live_binding_true_for_live_push(self):
        rds = _make_rds(source_class="live_push")
        self.assertTrue(rds.is_live_binding())

    def test_is_live_binding_true_for_live_pull(self):
        rds = _make_rds(source_class="live_pull")
        self.assertTrue(rds.is_live_binding())

    def test_is_live_binding_false_for_seed_only(self):
        rds = _make_rds(source_class="seed_only")
        self.assertFalse(rds.is_live_binding())

    def test_immutable(self):
        rds = _make_rds()
        with self.assertRaises((AttributeError, TypeError)):
            rds.dataset = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# RequiredDataSource serialization
# ---------------------------------------------------------------------------

class TestRequiredDataSourceSerialization(unittest.TestCase):

    def test_to_dict_roundtrip(self):
        rds = _make_rds(
            connector_candidates=["twse-http-v1"],
            policy_gates=["require_connector_approved"],
        )
        d = rds.to_dict()
        self.assertEqual(d["dataset"], "tw_price_daily")
        self.assertEqual(d["connector_candidates"], ["twse-http-v1"])
        restored = RequiredDataSource.from_dict(d)
        self.assertEqual(rds, restored)

    def test_from_dict_ignores_unknown_keys(self):
        d = dict(
            dataset="tw_price_daily",
            market="TW",
            cadence="daily",
            source_class="live_pull",
            unknown_future_field="ignored",
        )
        rds = RequiredDataSource.from_dict(d)
        self.assertEqual(rds.dataset, "tw_price_daily")


# ---------------------------------------------------------------------------
# Persona.required_data_sources field
# ---------------------------------------------------------------------------

class TestPersonaRequiredDataSources(unittest.TestCase):

    def test_default_required_data_sources_is_empty(self):
        p = _make_persona()
        self.assertEqual(p.required_data_sources, [])

    def test_persona_with_required_data_sources(self):
        rds_live = _make_rds(source_class="live_pull")
        rds_seed = _make_rds(dataset="tw_sector_tags", source_class="seed_only")
        p = _make_persona(required_data_sources=[rds_live, rds_seed])
        self.assertEqual(len(p.required_data_sources), 2)

    def test_live_data_sources_excludes_seed(self):
        rds_live = _make_rds(source_class="live_pull")
        rds_seed = _make_rds(dataset="tw_sector_tags", source_class="seed_only")
        p = _make_persona(required_data_sources=[rds_live, rds_seed])
        live = p.live_data_sources()
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0].source_class, "live_pull")

    def test_seed_only_sources_returns_seed_entries(self):
        rds_live = _make_rds(source_class="live_pull")
        rds_seed = _make_rds(dataset="tw_sector_tags", source_class="seed_only")
        p = _make_persona(required_data_sources=[rds_live, rds_seed])
        seeds = p.seed_only_sources()
        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0].dataset, "tw_sector_tags")

    def test_seed_only_source_not_counted_as_live_binding(self):
        rds_seed = _make_rds(source_class="seed_only")
        p = _make_persona(required_data_sources=[rds_seed])
        self.assertEqual(len(p.live_data_sources()), 0,
            "seed_only entries must not count as live data source binding")

    def test_persona_from_dict_deserializes_required_data_sources(self):
        rds_dict = dict(
            dataset="tw_price_daily",
            market="TW",
            cadence="daily",
            source_class="live_pull",
            connector_candidates=["twse-http-v1"],
            policy_gates=[],
        )
        p_dict = dict(
            persona_id="persona-beta",
            name="Beta Reversal",
            mandate="Mean reversion on TW large cap",
            lifecycle_state="research_only",
            created_at="2026-06-01T00:00:00Z",
            required_data_sources=[rds_dict],
        )
        p = Persona.from_dict(p_dict)
        self.assertEqual(len(p.required_data_sources), 1)
        self.assertIsInstance(p.required_data_sources[0], RequiredDataSource)
        self.assertEqual(p.required_data_sources[0].dataset, "tw_price_daily")

    def test_persona_to_dict_includes_required_data_sources(self):
        rds = _make_rds(connector_candidates=["twse-http-v1"])
        p = _make_persona(required_data_sources=[rds])
        d = p.to_dict()
        self.assertIn("required_data_sources", d)
        self.assertEqual(len(d["required_data_sources"]), 1)
        self.assertEqual(d["required_data_sources"][0]["dataset"], "tw_price_daily")

    def test_persona_to_dict_includes_empty_required_data_sources(self):
        p = _make_persona()
        d = p.to_dict()
        # Empty list is valid serialized state; to_dict includes it (filter only drops None and {})
        self.assertIn("required_data_sources", d)
        self.assertEqual(d["required_data_sources"], [])

    def test_non_required_data_source_instance_raises(self):
        with self.assertRaises(PersonaRegistryError):
            _make_persona(required_data_sources=[{"dataset": "bad", "source_class": "live_pull"}])


# ---------------------------------------------------------------------------
# Acceptance: multiple source classes in one persona
# ---------------------------------------------------------------------------

class TestPersonaMultiSourceClass(unittest.TestCase):

    def test_persona_with_push_pull_and_seed(self):
        sources = [
            _make_rds(dataset="tw_price_realtime", cadence="realtime", source_class="live_push",
                      connector_candidates=["twse-ws-v1"]),
            _make_rds(dataset="tw_price_daily", cadence="daily", source_class="live_pull",
                      connector_candidates=["twse-http-v1", "finmind-http-v1"],
                      policy_gates=["require_connector_approved"]),
            _make_rds(dataset="tw_sector_tags", cadence="on_demand", source_class="seed_only"),
        ]
        p = _make_persona(required_data_sources=sources)
        self.assertEqual(len(p.live_data_sources()), 2)
        self.assertEqual(len(p.seed_only_sources()), 1)
        self.assertFalse(p.seed_only_sources()[0].is_live_binding())


if __name__ == "__main__":
    unittest.main()
