from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.persona.cron_ooda_closure import (  # noqa: E402
    CronOodaClosureError,
    append_ooda_packet,
    close_persona_cron_dispatch,
    find_persona_cron_job,
    resolve_ooda_packet_store_path,
)
from integrations.openclaw.persona_ooda_runtime import PersonaOodaTurn  # noqa: E402


class FakeGatewayRuntime:
    def __init__(self, *, run_status: str = "ok", jobs: list[dict] | None = None):
        self.run_status = run_status
        self.jobs = jobs or []
        self.calls: list[tuple[str, dict | None]] = []

    def gateway_call(self, method: str, params: dict | None = None) -> dict:
        self.calls.append((method, params))
        if method == "cron.run":
            return {"ok": True, "enqueued": True, "runId": "run-abc-1"}
        if method == "cron.runs":
            summary = json.dumps(
                {
                    "kind": "pantheon.workflow.dispatch",
                    "persona_id": "persona-tw-equity",
                    "policy_id": "oc002.cron.deploy",
                    "request_id": "persona-pantheon.deploy-req-1",
                    "upstream_entrypoint": "deployment.plan",
                    "workflow_id": "pantheon.deploy",
                },
                sort_keys=True,
            )
            return {
                "entries": [
                    {
                        "runId": "run-abc-1",
                        "status": self.run_status,
                        "summary": summary,
                        "runAtMs": 1234,
                        "durationMs": 42,
                    }
                ]
            }
        if method == "cron.list":
            return {"jobs": self.jobs, "hasMore": False}
        raise AssertionError(f"unexpected method {method}")


def _completed_turn(persona_id: str, prompt: str) -> PersonaOodaTurn:
    return PersonaOodaTurn(
        persona_id=persona_id,
        status="completed",
        decision="Observe: no market data. Decide: stay flat (paper).",
        elapsed_ms=123,
        model=f"openclaw/{persona_id}",
    )


def _erroring_turn(persona_id: str, prompt: str) -> PersonaOodaTurn:
    return PersonaOodaTurn(
        persona_id=persona_id,
        status="error",
        decision="",
        elapsed_ms=5,
        model=f"openclaw/{persona_id}",
        error="/v1/responses HTTP 503",
    )


class CloseCronDispatchTests(unittest.TestCase):
    def test_completed_turn_closes_packet_with_real_fingerprint(self):
        runtime = FakeGatewayRuntime()
        with _tmp_store() as store_path:
            result = close_persona_cron_dispatch(
                "persona-tw-equity",
                "pantheon.deploy",
                job_id="job-1",
                runtime=runtime,
                store_path=store_path,
                turn_runner=_completed_turn,
                poll_timeout_seconds=1.0,
                poll_interval_seconds=0.01,
            )

        self.assertEqual(result.cron_run_id, "run-abc-1")
        self.assertEqual(result.cron_run_status, "ok")
        self.assertEqual(result.agent_turn_status, "completed")
        self.assertEqual(result.packet["status"], "closed")
        self.assertEqual(result.packet["environment"], "paper")
        self.assertFalse(result.packet["act"]["live_capital_side_effects"])
        self.assertIn("cron-job://job-1", result.packet["observe"]["source_refs"])
        self.assertIn("cron-run://run-abc-1", result.packet["observe"]["telemetry_refs"])
        self.assertIn(
            "systemEvent-request-id://persona-pantheon.deploy-req-1",
            result.packet["observe"]["signal_refs"],
        )
        self.assertEqual(result.packet["producer"]["cron_run_id"], "run-abc-1")
        self.assertEqual(result.packet["producer"]["fabricated"], False)
        self.assertIn("cron_ooda_turn", result.packet)
        self.assertEqual([m for m, _ in runtime.calls][:2], ["cron.run", "cron.runs"])

    def test_erroring_turn_stops_honestly_at_observing(self):
        runtime = FakeGatewayRuntime()
        with _tmp_store() as store_path:
            result = close_persona_cron_dispatch(
                "persona-tw-equity",
                "pantheon.deploy",
                job_id="job-1",
                runtime=runtime,
                store_path=store_path,
                turn_runner=_erroring_turn,
                poll_timeout_seconds=1.0,
                poll_interval_seconds=0.01,
            )

        self.assertEqual(result.agent_turn_status, "error")
        self.assertEqual(result.packet["status"], "observing")
        self.assertIsNone(result.packet["decide"]["decision_rationale_ref"])
        self.assertTrue(
            any("agent-turn-unavailable" in ref for ref in result.packet["audit_refs"])
        )

    def test_non_ok_run_raises_and_writes_nothing(self):
        runtime = FakeGatewayRuntime(run_status="failed")
        with _tmp_store() as store_path:
            with self.assertRaises(CronOodaClosureError):
                close_persona_cron_dispatch(
                    "persona-tw-equity",
                    "pantheon.deploy",
                    job_id="job-1",
                    runtime=runtime,
                    store_path=store_path,
                    turn_runner=_completed_turn,
                    poll_timeout_seconds=1.0,
                    poll_interval_seconds=0.01,
                )
            self.assertFalse(store_path.exists())

    def test_persona_mismatch_raises(self):
        runtime = FakeGatewayRuntime()
        with _tmp_store() as store_path:
            with self.assertRaises(CronOodaClosureError):
                close_persona_cron_dispatch(
                    "some-other-persona",
                    "pantheon.deploy",
                    job_id="job-1",
                    runtime=runtime,
                    store_path=store_path,
                    turn_runner=_completed_turn,
                    poll_timeout_seconds=1.0,
                    poll_interval_seconds=0.01,
                )

    def test_append_ooda_packet_json_array_shape_round_trips(self):
        with _tmp_dir() as tmp_dir:
            store_path = tmp_dir / "ooda_packets.json"
            append_ooda_packet(store_path, {"packet_id": "ooda-a", "status": "open"})
            append_ooda_packet(store_path, {"packet_id": "ooda-b", "status": "closed"})
            payload = json.loads(store_path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload), 2)
            packet_ids = {rec["payload"]["packet_id"] for rec in payload}
            self.assertEqual(packet_ids, {"ooda-a", "ooda-b"})
            self.assertTrue(all(rec["schema_version"] == "ooda_loop_packet_record.v1" for rec in payload))

    def test_resolve_store_path_prefers_explicit_env(self):
        import os

        old = os.environ.get("PANTHEON_BFF_OODA_PACKET_STORE")
        try:
            os.environ["PANTHEON_BFF_OODA_PACKET_STORE"] = "/tmp/example-ooda.json"
            self.assertEqual(resolve_ooda_packet_store_path(), Path("/tmp/example-ooda.json"))
        finally:
            if old is None:
                os.environ.pop("PANTHEON_BFF_OODA_PACKET_STORE", None)
            else:
                os.environ["PANTHEON_BFF_OODA_PACKET_STORE"] = old

    def test_find_persona_cron_job_matches_payload_not_name(self):
        jobs = [
            {
                "id": "job-xyz",
                "payload": {
                    "text": json.dumps(
                        {"persona_id": "persona-crypto", "workflow_id": "pantheon.ingest"}
                    )
                },
            },
            {
                "id": "job-abc",
                "payload": {
                    "text": json.dumps(
                        {"persona_id": "persona-tw-equity", "workflow_id": "pantheon.deploy"}
                    )
                },
            },
        ]
        runtime = FakeGatewayRuntime(jobs=jobs)
        found = find_persona_cron_job(runtime, "persona-tw-equity", "pantheon.deploy")
        self.assertEqual(found, "job-abc")
        missing = find_persona_cron_job(runtime, "persona-nope", "pantheon.deploy")
        self.assertIsNone(missing)


import contextlib
import tempfile


@contextlib.contextmanager
def _tmp_dir():
    with tempfile.TemporaryDirectory(prefix="pantheon-cron-ooda-closure-") as d:
        yield Path(d)


@contextlib.contextmanager
def _tmp_store():
    with _tmp_dir() as d:
        yield d / "ooda_loop_packets.jsonl"


if __name__ == "__main__":
    unittest.main()
