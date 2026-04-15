"""
Unit tests for canonical incident reference validation.

These tests exercise BP5-SVC-011's service-level foreign-reference guards
without requiring FastAPI or external services.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from services.incident.incident import IncidentCase
from services.incident.reference_validation import (
    CanonicalReferenceError,
    CanonicalReferenceValidator,
)


def _make_incident(**overrides) -> IncidentCase:
    payload = {
        "incident_id": "inc-001",
        "title": "Drawdown threshold breached",
        "status": "open",
        "severity": "high",
        "created_at": "2026-04-15T12:00:00Z",
        "binding_id": "binding-001",
        "deployment_stage": "live",
        "deployment_plan_id": "plan-001",
        "capital_pool_id": "pool-001",
        "persona_capital_binding_id": "pcb-001",
        "artifact_id": "artifact-001",
        "artifact_version": "1.2.3",
        "runtime_id": "runtime-001",
        "trace_id": "trace-001",
        "telemetry_event_ids": ["evt-001"],
        "lineage_ref": "artifact-001@1.2.3",
    }
    payload.update(overrides)
    return IncidentCase(**payload)


class _FakeBindingLookup:
    def __init__(self, binding: dict[str, str] | None) -> None:
        self._binding = binding

    def get_binding(self, binding_id: str):
        if self._binding is None:
            return None
        if self._binding.get("binding_id") != binding_id:
            return None
        return dict(self._binding)


class _FakeTelemetryLookup:
    def __init__(
        self,
        *,
        event_trace: dict | None = None,
        binding_projection: dict | None = None,
    ) -> None:
        self._event_trace = event_trace
        self._binding_projection = binding_projection

    def telemetry_event_trace(self, event_id: str):
        if self._event_trace is None:
            return None
        if self._event_trace.get("target_id") != event_id:
            return None
        return self._event_trace

    def runtime_binding_projection(self, binding_id: str):
        if self._binding_projection is None:
            return None
        if self._binding_projection.get("target_id") != binding_id:
            return None
        return self._binding_projection


def _binding_record(**overrides) -> dict[str, str]:
    payload = {
        "binding_id": "binding-001",
        "runtime_id": "runtime-001",
        "capital_pool_id": "pool-001",
        "artifact_id": "artifact-001",
        "artifact_version": "1.2.3",
        "deployment_mode": "live",
        "effective_at": "2026-04-10T00:00:00Z",
        "retired_at": None,
        "plan_id": "plan-001",
        "persona_capital_binding_id": "pcb-001",
    }
    payload.update(overrides)
    return payload


def _event_trace(**overrides) -> dict:
    refs = {
        "runtime_binding_ids": ["binding-001"],
        "deployment_plan_ids": ["plan-001"],
        "capital_pool_ids": ["pool-001"],
        "persona_capital_binding_ids": ["pcb-001"],
        "artifact_refs": ["artifact-001@1.2.3"],
        "trace_ids": ["trace-001"],
    }
    payload = {
        "target_type": "telemetry_event",
        "target_id": "evt-001",
        "refs": refs,
        "upstream_chain": [],
        "downstream_chain": [{"type": "runtime_ref", "id": "runtime-001"}],
        "conflict_markers": [],
    }
    payload.update(overrides)
    if "refs" not in overrides:
        payload["refs"] = refs
    return payload


def _binding_projection(**overrides) -> dict:
    refs = {
        "artifact_refs": ["artifact-001@1.2.3"],
        "deployment_plan_ids": ["plan-001"],
        "capital_pool_ids": ["pool-001"],
        "persona_capital_binding_ids": ["pcb-001"],
        "runtime_binding_ids": ["binding-001"],
        "trace_ids": [],
        "strategy_ids": [],
        "registry_ids": [],
    }
    payload = {
        "target_type": "runtime_binding",
        "target_id": "binding-001",
        "refs": refs,
        "upstream_chain": [],
        "downstream_chain": [],
        "conflict_markers": [],
    }
    payload.update(overrides)
    if "refs" not in overrides:
        payload["refs"] = refs
    return payload


class TestCanonicalReferenceValidator(unittest.TestCase):
    def test_valid_incident_passes(self):
        validator = CanonicalReferenceValidator(
            binding_lookup=_FakeBindingLookup(_binding_record()),
            telemetry_lookup=_FakeTelemetryLookup(
                event_trace=_event_trace(),
                binding_projection=_binding_projection(),
            ),
        )
        validator.validate_incident(_make_incident())

    def test_unknown_binding_rejected(self):
        validator = CanonicalReferenceValidator(
            binding_lookup=_FakeBindingLookup(None),
            telemetry_lookup=_FakeTelemetryLookup(
                event_trace=_event_trace(),
                binding_projection=_binding_projection(),
            ),
        )
        with self.assertRaises(CanonicalReferenceError) as ctx:
            validator.validate_incident(_make_incident())
        self.assertIn("binding_id 'binding-001' does not resolve", str(ctx.exception))

    def test_binding_field_mismatch_rejected(self):
        validator = CanonicalReferenceValidator(
            binding_lookup=_FakeBindingLookup(_binding_record(runtime_id="runtime-other")),
            telemetry_lookup=_FakeTelemetryLookup(
                event_trace=_event_trace(),
                binding_projection=_binding_projection(),
            ),
        )
        with self.assertRaises(CanonicalReferenceError) as ctx:
            validator.validate_incident(_make_incident())
        self.assertIn("runtime_id mismatch", str(ctx.exception))

    def test_telemetry_trace_mismatch_rejected(self):
        bad_trace = _event_trace(
            refs={
                "runtime_binding_ids": ["binding-other"],
                "deployment_plan_ids": ["plan-001"],
                "capital_pool_ids": ["pool-001"],
                "persona_capital_binding_ids": ["pcb-001"],
                "artifact_refs": ["artifact-001@1.2.3"],
                "trace_ids": ["trace-001"],
            }
        )
        validator = CanonicalReferenceValidator(
            binding_lookup=_FakeBindingLookup(_binding_record()),
            telemetry_lookup=_FakeTelemetryLookup(
                event_trace=bad_trace,
                binding_projection=_binding_projection(),
            ),
        )
        with self.assertRaises(CanonicalReferenceError) as ctx:
            validator.validate_incident(_make_incident())
        self.assertIn("telemetry_event_id 'evt-001' does not carry runtime_binding_ids='binding-001'", str(ctx.exception))

    def test_lineage_ref_must_match_artifact_snapshot(self):
        validator = CanonicalReferenceValidator(
            binding_lookup=_FakeBindingLookup(_binding_record()),
            telemetry_lookup=_FakeTelemetryLookup(
                event_trace=_event_trace(),
                binding_projection=_binding_projection(),
            ),
        )
        with self.assertRaises(CanonicalReferenceError) as ctx:
            validator.validate_incident(_make_incident(lineage_ref="artifact-001@9.9.9"))
        self.assertIn("lineage_ref 'artifact-001@9.9.9' must match artifact snapshot", str(ctx.exception))

    def test_binding_temporal_window_enforced(self):
        validator = CanonicalReferenceValidator(
            binding_lookup=_FakeBindingLookup(_binding_record(effective_at="2026-04-16T00:00:00Z")),
            telemetry_lookup=_FakeTelemetryLookup(
                event_trace=_event_trace(),
                binding_projection=_binding_projection(),
            ),
        )
        with self.assertRaises(CanonicalReferenceError) as ctx:
            validator.validate_incident(_make_incident())
        self.assertIn("is before RuntimeBinding 'binding-001' effective_at", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
