"""
LIN-003 live lineage write path — default-wiring integration test.

Round-3 review of EVOCHAIN-001 (PR #3509) found that the deployed telemetry
-> incidents lineage-resolution gap could not be closed by fixtures alone:
"Exit evidence must be a real default-wiring test with no fake lookups:
telemetry ingest 202 -> incident 201 -> same replay 200."

This proves the telemetry-side half of that contract with real components,
independent of any specific incident producer, in two layers:

TestLiveLineageWritePathDefaultWiring — direct component proof:
1. A real `TelemetryIngestService.ingest()` call (no mocked buffer/writer)
   accepts an event whose binding_id and event_id were never in any static
   corpus.
2. The *same* `LineageReadService` instance the deployed
   `/api/telemetry/lineage/*` HTTP routes serve resolves that event and its
   RuntimeBinding immediately — no corpus reload.
3. A real `CanonicalReferenceValidator.validate_incident()` call, through a
   thin adapter that forwards to that live `LineageReadService.query()`
   (never a canned dict), accepts an `IncidentCase` referencing that live
   data.
4. Replaying the same event_id is idempotent and re-validation still
   succeeds.

TestLiveLineageWritePathFullStackHTTPRoute — literal HTTP-status proof:
5. A real RuntimeManagerClient, opted into local transport explicitly with
   ``allow_local=True`` and pointed at a per-test temporary binding store,
   deploys a live RuntimeBinding — governance preconditions and all.
6. Real telemetry ingest admits an event referencing that binding.
7. The real `services.incidents.main` FastAPI app's
   `POST /api/incidents/consume-threshold` route — with its
   CanonicalReferenceValidator wired to this test's live runtime-manager
   client and lineage service, no other lookups replaced — returns 201 on
   first delivery and 200 on idempotent replay.

The full-stack layer owns its entire runtime-manager configuration
(OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001): it never inherits
``PANTHEON_RUNTIME_MANAGER_URL``, token, or store-path values from the
ambient environment, and it does not relax RuntimeManagerClient's
fail-closed default — see
``test_default_runtime_manager_client_stays_fail_closed_without_url``.

See docs/decisions/LIN-003-live-lineage-write-path.md for the full root
cause and scope.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock

_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from services.incident.incident import IncidentCase
from services.incident.reference_validation import (
    CanonicalReferenceValidator,
    _RuntimeBindingLookup,  # the real production default binding_lookup, not a fixture
)
from services.telemetry.ingest_svc import TelemetryIngestService
from services.telemetry.lineage_read import LineageReadService

# reference_validation's module import already inserts services/runtime-manager
# onto sys.path (see its _RUNTIME_MANAGER_DIR bootstrap), so this becomes
# importable afterwards — the same bare-module import reference_validation
# itself uses.
from runtime_manager_client import (  # noqa: E402
    RuntimeManagerClient,
    RuntimeManagerClientError,
)


class _FakeBinding:
    """Minimal RuntimeBinding stand-in — same attribute shape
    TelemetryIngestService expects from binding_store.get_binding()."""

    def __init__(self, **overrides):
        self.binding_id = overrides.get("binding_id", "rb-live-lin003")
        self.runtime_id = overrides.get("runtime_id", "runtime-lin003")
        self.capital_pool_id = overrides.get("capital_pool_id", "pool-lin003")
        self.artifact_id = overrides.get("artifact_id", "artifact-lin003")
        self.artifact_version = overrides.get("artifact_version", "1.0.0")
        self.deployment_mode = overrides.get("deployment_mode", "live")
        self.execution_mode = overrides.get("execution_mode", self.deployment_mode)
        self.effective_at = overrides.get("effective_at", "2026-07-01T00:00:00Z")
        self.retired_at = overrides.get("retired_at", None)
        self.plan_id = overrides.get("plan_id", "plan-lin003")
        self.persona_capital_binding_id = overrides.get(
            "persona_capital_binding_id", "pcb-lin003"
        )


class _FakeBindingStore:
    """The authoritative RuntimeBinding source telemetry ingest resolves
    against — stands in for runtime-manager in this in-process test."""

    def __init__(self, binding: _FakeBinding) -> None:
        self._binding = binding

    def get_binding(self, binding_id: str):
        if binding_id == self._binding.binding_id:
            return self._binding
        return None


class _LiveLineageTelemetryLookup:
    """Satisfies CanonicalReferenceValidator's telemetry_lookup interface by
    forwarding straight to a live LineageReadService.query() — the same
    call the deployed telemetry HTTP lineage routes make. Not a fixture."""

    def __init__(self, lineage_service: LineageReadService) -> None:
        self._service = lineage_service

    def telemetry_event_trace(self, event_id: str):
        result = self._service.query("telemetry_event_trace", event_id=event_id)
        if any(m.get("code") == "node_not_found" for m in result["conflict_markers"]):
            return None
        return result

    def runtime_binding_projection(self, binding_id: str):
        result = self._service.query("runtime_binding_projection", binding_id=binding_id)
        if any(m.get("code") == "node_not_found" for m in result["conflict_markers"]):
            return None
        return result


class _LiveRuntimeBindingLookup:
    """CanonicalReferenceValidator's binding_lookup, resolved from the same
    authoritative binding_store telemetry ingest already used — mirrors
    runtime-manager acting as the shared identity source of truth."""

    def __init__(self, binding_store: _FakeBindingStore) -> None:
        self._store = binding_store

    def get_binding(self, binding_id: str):
        binding = self._store.get_binding(binding_id)
        if binding is None:
            return None
        return {
            "binding_id": binding.binding_id,
            "runtime_id": binding.runtime_id,
            "capital_pool_id": binding.capital_pool_id,
            "artifact_id": binding.artifact_id,
            "artifact_version": binding.artifact_version,
            "deployment_mode": binding.deployment_mode,
            "effective_at": binding.effective_at,
            "retired_at": binding.retired_at,
            "plan_id": binding.plan_id,
            "persona_capital_binding_id": binding.persona_capital_binding_id,
        }


def _make_event(**overrides) -> dict:
    event = {
        "event_id": "evt-lin003-live",
        "event_type": "pnl_snapshot",
        "created_at": "2026-07-13T12:00:00Z",
        "execution_mode": "live",
        "environment": "live",
        "deployment_stage": "live",
        "binding_id": "rb-live-lin003",
        "runtime_id": "runtime-lin003",
        "capital_pool_id": "pool-lin003",
        "artifact_id": "artifact-lin003",
        "artifact_version": "1.0.0",
        "plan_id": "plan-lin003",
        "persona_capital_binding_id": "pcb-lin003",
        "trace_id": "trace-lin003",
        "metrics": {"pnl": -42.0},
    }
    event.update(overrides)
    return event


def _make_incident(**overrides) -> IncidentCase:
    payload = {
        "incident_id": "inc-lin003-live",
        "title": "LIN-003 default-wiring proof",
        "status": "open",
        "severity": "high",
        "created_at": "2026-07-13T12:00:01Z",
        "binding_id": "rb-live-lin003",
        "deployment_stage": "live",
        "deployment_plan_id": "plan-lin003",
        "capital_pool_id": "pool-lin003",
        "persona_capital_binding_id": "pcb-lin003",
        "artifact_id": "artifact-lin003",
        "artifact_version": "1.0.0",
        "runtime_id": "runtime-lin003",
        "trace_id": "trace-lin003",
        "telemetry_event_ids": ["evt-lin003-live"],
        "lineage_ref": "artifact-lin003@1.0.0",
    }
    payload.update(overrides)
    return IncidentCase(**payload)


class TestLiveLineageWritePathDefaultWiring(unittest.IsolatedAsyncioTestCase):
    """Real ingest + real lineage graph + real validator, no fixtures
    standing in for the lineage resolution itself."""

    async def test_ingest_then_incident_validation_succeeds_without_corpus_reload(self):
        binding = _FakeBinding()
        binding_store = _FakeBindingStore(binding)
        lineage_service = LineageReadService()  # empty graph — no static corpus loaded

        svc = TelemetryIngestService(
            binding_store=binding_store,
            lineage_write_store=lineage_service,
            batch_size=10,
            batch_interval=0.1,
        )
        await svc.start()
        try:
            # telemetry ingest 202-equivalent
            accepted = await svc.ingest(_make_event())
            self.assertTrue(accepted)

            # Neither the event nor its binding were ever in a corpus —
            # before LIN-003 both queries below would 404 (node_not_found).
            trace = lineage_service.query("telemetry_event_trace", event_id="evt-lin003-live")
            self.assertFalse(
                any(m.get("code") == "node_not_found" for m in trace["conflict_markers"])
            )
            projection = lineage_service.query(
                "runtime_binding_projection", binding_id="rb-live-lin003"
            )
            self.assertFalse(
                any(m.get("code") == "node_not_found" for m in projection["conflict_markers"])
            )

            validator = CanonicalReferenceValidator(
                binding_lookup=_LiveRuntimeBindingLookup(binding_store),
                telemetry_lookup=_LiveLineageTelemetryLookup(lineage_service),
            )
            incident = _make_incident()
            # incident 201-equivalent: must not raise CanonicalReferenceError
            validator.validate_incident(incident)

            # same replay 200-equivalent: idempotent re-ingest, re-validation
            # still succeeds against the same live graph.
            replayed = await svc.ingest(_make_event())
            self.assertTrue(replayed)
            validator.validate_incident(incident)
        finally:
            await svc.stop(graceful=True)

    async def test_without_lineage_write_store_the_gap_reproduces(self):
        """Control case: with no lineage_write_store wired (the pre-LIN-003
        state), the same event/binding never resolve — proving the fix
        above is actually doing something, not passing vacuously."""
        binding = _FakeBinding()
        binding_store = _FakeBindingStore(binding)
        lineage_service = LineageReadService()

        svc = TelemetryIngestService(
            binding_store=binding_store,
            batch_size=10,
            batch_interval=0.1,
        )
        await svc.start()
        try:
            accepted = await svc.ingest(_make_event())
            self.assertTrue(accepted)

            trace = lineage_service.query("telemetry_event_trace", event_id="evt-lin003-live")
            self.assertTrue(
                any(m.get("code") == "node_not_found" for m in trace["conflict_markers"])
            )
        finally:
            await svc.stop(graceful=True)


def _valid_deploy_request(**overrides) -> dict:
    request = {
        "plan_id": "plan-lin003-fullstack",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "artifact-lin003-fullstack",
        "artifact_version": "1.0.0",
        "capital_pool_id": "pool-lin003-fullstack",
        "persona_capital_binding_id": "pcb-lin003-fullstack",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "live",
        "loader_checks_passed": True,
        "runtime_id": "rt-lin003-fullstack",
    }
    request.update(overrides)
    return request


# Deliberately unroutable: discard port on loopback, with a path no Pantheon
# route serves. Bound only while `services.incidents.main` is imported, so the
# module's import-time default validator can be constructed without inheriting
# operator config; the test replaces that validator before any request runs, so
# nothing ever dials this URL.
_INERT_RUNTIME_MANAGER_URL = "http://127.0.0.1:9/lin003-inert-never-contacted"

# Ambient values that would otherwise silently decide this test's transport,
# credentials, or storage location. Cleared per test; mock.patch.dict restores
# whatever the environment actually had on teardown.
_AMBIENT_RUNTIME_MANAGER_ENV = (
    "PANTHEON_RUNTIME_MANAGER_URL",
    "PANTHEON_RUNTIME_MANAGER_TOKEN_FILE",
    "PANTHEON_RUNTIME_MANAGER_TIMEOUT_SECONDS",
)


class _RuntimeManagerClientAdapter:
    """Satisfies TelemetryIngestService's RuntimeBindingProtocol (attribute
    access) from RuntimeManagerClient.get() (dict access) — the same
    normalization services/telemetry/main.py's _RuntimeBindingAdapter does
    for the HTTP transport, reused here for the client's local transport."""

    def __init__(self, client: RuntimeManagerClient) -> None:
        self._client = client

    def get_binding(self, binding_id: str):
        data = self._client.get(binding_id)
        return types.SimpleNamespace(**data) if data else None


class TestLiveLineageWritePathFullStackHTTPRoute(unittest.IsolatedAsyncioTestCase):
    """The literal acceptance form: a real runtime-manager deploy, a real
    telemetry ingest + live lineage write, and the real
    POST /api/incidents/consume-threshold FastAPI route (with the module's
    own CanonicalReferenceValidator swapped for one wired to this test's
    live runtime-manager client and lineage service — no other lookups
    faked) proving 201 on first delivery and 200 on idempotent replay."""

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        root = Path(self._tempdir.name)
        self._binding_store_path = root / "bindings.json"
        self._env_patch = mock.patch.dict(
            os.environ,
            {
                "PANTHEON_RUNTIME_BINDING_STORE_PATH": str(self._binding_store_path),
                "PANTHEON_SINGLE_RUNTIME_ENFORCED": "true",
                # Pin the bearer token so a broken or absent ambient token file
                # cannot fail the client constructor for an unrelated reason.
                "PANTHEON_RUNTIME_MANAGER_TOKEN": "lin003-isolated-test-token",
                # Keep services.incidents.main's import-time store bootstrap
                # inside this test's tempdir instead of the shared
                # /tmp/pantheon/incidents default.
                "INCIDENTS_DATA_DIR": str(root / "incidents-data"),
            },
            clear=False,
        )
        self._env_patch.start()
        for name in _AMBIENT_RUNTIME_MANAGER_ENV:
            os.environ.pop(name, None)

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tempdir.cleanup()

    def _isolated_runtime_manager_client(self) -> RuntimeManagerClient:
        """This test's own runtime-manager surface, declared explicitly.

        ``allow_local=True`` is RuntimeManagerClient's documented test/local
        opt-in. It is required here *because* the production default fails
        closed when ``PANTHEON_RUNTIME_MANAGER_URL`` is unset; the test states
        its transport rather than hoping the ambient environment supplies one.
        The store path is per-test, so no state survives between runs or leaks
        into a developer's or CI runner's shared runtime-manager store.
        """
        self.assertNotIn(
            "PANTHEON_RUNTIME_MANAGER_URL",
            os.environ,
            "this test must not resolve a runtime-manager transport from ambient config",
        )
        return RuntimeManagerClient(allow_local=True)

    def test_default_runtime_manager_client_stays_fail_closed_without_url(self):
        """Guard rail for the isolation above.

        The full-stack test must never be repaired by letting
        RuntimeManagerClient fall back to an in-process RuntimeManager when no
        URL is configured. Isolation here is an explicit ``allow_local`` opt-in
        confined to the test; it is not a relaxation of the deployed default.
        """
        self.assertNotIn("PANTHEON_RUNTIME_MANAGER_URL", os.environ)
        with self.assertRaises(RuntimeManagerClientError):
            RuntimeManagerClient()
        with self.assertRaises(RuntimeManagerClientError):
            RuntimeManagerClient(require_remote=True)

    def test_isolated_runtime_manager_fixture_writes_only_to_tempdir(self):
        """The fixture is self-contained: deploy/readback round-trips through
        the per-test store and nothing else."""
        self.assertFalse(self._binding_store_path.exists())
        client = self._isolated_runtime_manager_client()
        binding = client.deploy(_valid_deploy_request())

        self.assertTrue(self._binding_store_path.exists())
        readback = client.get(binding["binding_id"])
        self.assertIsNotNone(readback)
        self.assertEqual(readback["binding_id"], binding["binding_id"])

    async def test_ingest_then_consume_threshold_route_201_then_replay_200(self):
        from fastapi.testclient import TestClient
        from services.incident.pg_store import build_incident_store

        # services.incidents.main constructs a module-level
        # CanonicalReferenceValidator at import time, whose default
        # _RuntimeBindingLookup builds a RuntimeManagerClient with no
        # allow_local opt-in — so importing it in a clean environment fails
        # closed. Bind an explicit inert URL for the import only. The route's
        # validator is replaced below with this test's live one, so that URL is
        # never dialled; it exists so the test owns the configuration instead of
        # inheriting whatever the operator happened to export.
        with mock.patch.dict(
            os.environ,
            {"PANTHEON_RUNTIME_MANAGER_URL": _INERT_RUNTIME_MANAGER_URL},
            clear=False,
        ):
            from services.incidents import main as incidents_main

        rm_client = self._isolated_runtime_manager_client()
        binding = rm_client.deploy(_valid_deploy_request())
        binding_id = binding["binding_id"]

        lineage_service = LineageReadService()
        ingest_svc = TelemetryIngestService(
            binding_store=_RuntimeManagerClientAdapter(rm_client),
            lineage_write_store=lineage_service,
            batch_size=10,
            batch_interval=0.1,
        )
        await ingest_svc.start()

        live_validator = CanonicalReferenceValidator(
            binding_lookup=_RuntimeBindingLookup(client=rm_client),
            telemetry_lookup=_LiveLineageTelemetryLookup(lineage_service),
        )

        test_store = build_incident_store(Path(self._tempdir.name) / "incidents.json")

        try:
            with mock.patch.object(incidents_main, "store", test_store), \
                 mock.patch.object(incidents_main, "reference_validator", live_validator):
                event_id = f"evt-lin003-fullstack-{uuid.uuid4().hex[:8]}"
                event = {
                    "event_id": event_id,
                    "event_type": "pnl_snapshot",
                    # RuntimeManagerService.deploy() stamps effective_at with
                    # the real wall-clock time it runs at; this must be after
                    # that, so pin far enough in the future to always satisfy
                    # the E-4 temporal-window check regardless of when the
                    # test executes.
                    "created_at": "2099-01-01T00:00:00Z",
                    "execution_mode": "paper",
                    "environment": "paper",
                    "deployment_stage": "paper",
                    "binding_id": binding_id,
                    "runtime_id": binding["runtime_id"],
                    "capital_pool_id": binding["capital_pool_id"],
                    "artifact_id": binding["artifact_id"],
                    "artifact_version": binding["artifact_version"],
                    "plan_id": binding["plan_id"],
                    "persona_capital_binding_id": binding["persona_capital_binding_id"],
                    "trace_id": f"trace-{event_id}",
                    "metrics": {"pnl": -315.75, "rolling_drawdown_multiple": 1.42},
                }
                # telemetry ingest 202-equivalent
                accepted = await ingest_svc.ingest(event)
                self.assertTrue(accepted)

                threshold_payload = {
                    "incident_id": f"inc-{event_id}",
                    "title": "LIN-003 full-stack drawdown breach",
                    "telemetry_event": {
                        "event_id": event_id,
                        "event_type": "pnl_snapshot",
                        "created_at": event["created_at"],
                        "severity": "high",
                        "runtime_binding_id": binding_id,
                        "deployment_stage": "paper",
                        "deployment_plan_id": binding["plan_id"],
                        "capital_pool_id": binding["capital_pool_id"],
                        "persona_capital_binding_id": binding["persona_capital_binding_id"],
                        "artifact_id": binding["artifact_id"],
                        "artifact_version": binding["artifact_version"],
                        "runtime_id": binding["runtime_id"],
                        "trace_id": event["trace_id"],
                        "metrics": event["metrics"],
                    },
                    "threshold_snapshot": {
                        "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.5",
                        "signal_type": "performance_degradation",
                        "metric_name": "rolling_drawdown_multiple",
                        "comparator": "gt",
                        "observed_value": 1.42,
                        "threshold_value": 1.25,
                        "window": "paper-session",
                        "breached": True,
                        "note": "LIN-003 full-stack default-wiring proof",
                    },
                }

                client = TestClient(incidents_main.app)
                first = client.post("/api/incidents/consume-threshold", json=threshold_payload)
                self.assertEqual(first.status_code, 201, first.text)

                replay = client.post("/api/incidents/consume-threshold", json=threshold_payload)
                self.assertEqual(replay.status_code, 200, replay.text)
                self.assertEqual(replay.json()["incident_id"], first.json()["incident_id"])
        finally:
            await ingest_svc.stop(graceful=True)


# Hostile ambient configuration: every value the full-stack fixture must
# override rather than inherit. The URL host is reserved for documentation
# (RFC 5737) and the paths do not exist, so anything that actually leaked
# through would fail loudly instead of silently passing.
_HOSTILE_AMBIENT_ENV = {
    "PANTHEON_RUNTIME_MANAGER_URL": "http://198.51.100.7:8099",
    "PANTHEON_RUNTIME_MANAGER_TOKEN": "ambient-operator-token",
    "PANTHEON_RUNTIME_MANAGER_TOKEN_FILE": "/nonexistent/ambient-token",
    "PANTHEON_RUNTIME_BINDING_STORE_PATH": "/nonexistent/ambient/bindings.json",
    "PANTHEON_SINGLE_RUNTIME_ENFORCED": "false",
    "INCIDENTS_DATA_DIR": "/nonexistent/ambient/incidents",
}


class TestFullStackFixtureIsolation(unittest.TestCase):
    """OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001.

    The full-stack case above must own its runtime-manager configuration in
    both directions: ambient values must not reach it, and its own values must
    not survive it. Both halves are asserted here rather than assumed, because
    a leak in either direction reintroduces exactly the ordering-dependent
    green/red flip this task exists to remove.
    """

    def test_fixture_overrides_ambient_config_then_restores_it(self):
        with mock.patch.dict(os.environ, _HOSTILE_AMBIENT_ENV, clear=False):
            before = dict(os.environ)

            case = TestLiveLineageWritePathFullStackHTTPRoute(
                "test_isolated_runtime_manager_fixture_writes_only_to_tempdir"
            )
            case.setUp()
            try:
                tempdir = Path(case._tempdir.name)
                # Ambient transport is removed, not merely shadowed.
                self.assertNotIn("PANTHEON_RUNTIME_MANAGER_URL", os.environ)
                self.assertNotIn("PANTHEON_RUNTIME_MANAGER_TOKEN_FILE", os.environ)
                # Ambient storage and enforcement are replaced by test-owned values.
                self.assertEqual(
                    os.environ["PANTHEON_RUNTIME_BINDING_STORE_PATH"],
                    str(tempdir / "bindings.json"),
                )
                self.assertEqual(os.environ["PANTHEON_SINGLE_RUNTIME_ENFORCED"], "true")
                self.assertTrue(
                    os.environ["INCIDENTS_DATA_DIR"].startswith(str(tempdir))
                )
                self.assertTrue(tempdir.exists())
            finally:
                case.tearDown()

            # No process-global residue: environment byte-identical, tempdir gone.
            self.assertEqual(dict(os.environ), before)
            self.assertFalse(tempdir.exists())

    def test_full_stack_case_passes_under_hostile_ambient_config(self):
        """End-to-end: run the whole full-stack case with ambient runtime-manager
        config pointed somewhere unreachable. It must still pass, and must leave
        the environment exactly as it found it."""
        with mock.patch.dict(os.environ, _HOSTILE_AMBIENT_ENV, clear=False):
            before = dict(os.environ)

            suite = unittest.TestLoader().loadTestsFromTestCase(
                TestLiveLineageWritePathFullStackHTTPRoute
            )
            result = unittest.TextTestRunner(
                stream=io.StringIO(), verbosity=0
            ).run(suite)

            self.assertTrue(
                result.wasSuccessful(),
                f"failures={result.failures!r} errors={result.errors!r}",
            )
            self.assertEqual(dict(os.environ), before)


if __name__ == "__main__":
    unittest.main()
