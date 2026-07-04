"""Live smoke: force-run a real persona OpenClaw cron job and prove it closes
into a packet the BFF `/bff/ooda/packets` read surface actually accepts.

This is NOT a mock test. When a live OpenClaw gateway + adapter are reachable
(the same `pantheon-openclaw-gateway` / `pantheon-openclaw-gateway-adapter`
compose services other OPENCLAW-* smoke scripts use), it exercises the full
evidence chain end to end:

    cron.run (force) -> cron.runs (status "ok") -> a real /v1/responses OODA
    turn on the persona's own agent -> a persisted packet whose refs carry
    that exact cron run id -> the BFF's own file-parsing code
    (`ServiceBackedReadAdapter.list_records("ooda_packets")`) reading it back.

If no live gateway is configured, this SKIPS with an explicit reason instead
of silently passing — never "skip as green". See
`services/persona/cron_ooda_closure.py` for the design rationale.

Run directly:

    PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL=http://127.0.0.1:18104 \\
    OPENCLAW_GATEWAY_URL=http://127.0.0.1:18789 \\
    OPENCLAW_GATEWAY_TOKEN=pantheon-local-token \\
    OPENCLAW_OODA_CRON_LIVE_SMOKE=1 \\
      python3 -m pytest services/persona/test_cron_ooda_closure_live_smoke.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_CRON_DIR = REPO_ROOT / "services" / "control-plane" / "cron"
if str(_CRON_DIR) not in sys.path:
    sys.path.insert(0, str(_CRON_DIR))

_BFF_DIR = REPO_ROOT / "services" / "control-plane" / "bff"

# `services/control-plane/cron/models.py` and `services/control-plane/bff/models.py`
# are two unrelated modules that both import under the bare name `models` (an
# existing convention in this repo's cross-service sys.path imports). Importing
# both in the same interpreter would silently shadow one with the other via
# `sys.modules`, so the BFF read-surface check below runs in a fresh
# subprocess instead of importing `read_store` inline here.
_BFF_READ_CHECK_SCRIPT = """
import json, os, sys
sys.path.insert(0, {bff_dir!r})
from read_store import ServiceBackedReadAdapter

os.environ["PANTHEON_BFF_OODA_PACKET_STORE"] = {store_path!r}
adapter = ServiceBackedReadAdapter(allow_snapshot_fallback=False)
available, records = adapter.list_records("ooda_packets")
print(json.dumps({{"available": available, "records": records}}))
"""


def _bff_read_ooda_packets(store_path: Path) -> tuple[bool, list[dict]]:
    script = _BFF_READ_CHECK_SCRIPT.format(bff_dir=str(_BFF_DIR), store_path=str(store_path))
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"BFF read-surface subprocess failed: {completed.stderr[-2000:]}")
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    return bool(payload["available"]), list(payload["records"])


def _live_gateway_configured() -> bool:
    if not os.environ.get("OPENCLAW_OODA_CRON_LIVE_SMOKE"):
        return False
    has_adapter = bool(os.environ.get("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "").strip())
    has_docker_exec = os.environ.get("OPENCLAW_PAPER_ADAPTER_ENABLED", "").lower() == "true"
    return bool(os.environ.get("OPENCLAW_GATEWAY_URL")) and (has_adapter or has_docker_exec)


@unittest.skipUnless(
    _live_gateway_configured(),
    "Requires OPENCLAW_OODA_CRON_LIVE_SMOKE=1 plus a reachable OpenClaw gateway "
    "(OPENCLAW_GATEWAY_URL + PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL or "
    "OPENCLAW_PAPER_ADAPTER_ENABLED=true). Skipped, not faked, when unset.",
)
class CronToOodaPacketLiveSmokeTest(unittest.TestCase):
    def test_force_run_persona_cron_job_closes_into_bff_readable_packet(self):
        from persona_cron_registrar import PersonaCronRegistrar
        from services.persona.cron_ooda_closure import (
            close_persona_cron_dispatch,
            find_persona_cron_job,
        )

        persona_id = os.environ.get("OPENCLAW_OODA_CRON_LIVE_SMOKE_PERSONA", "").strip()
        workflow_id = os.environ.get("OPENCLAW_OODA_CRON_LIVE_SMOKE_WORKFLOW", "pantheon.deploy")

        runtime = PersonaCronRegistrar()._get_runtime()  # noqa: SLF001
        self.assertIsNotNone(runtime, "PersonaCronRegistrar could not resolve a live gateway runtime")

        job_id = None
        if persona_id:
            job_id = find_persona_cron_job(runtime, persona_id, workflow_id)
            self.assertIsNotNone(
                job_id, f"No registered {workflow_id} cron job found for {persona_id}"
            )
        else:
            persona_id, job_id = _any_registered_job(runtime, workflow_id)
            self.assertIsNotNone(
                job_id, f"No persona has a registered {workflow_id} cron job on this gateway"
            )

        with tempfile.TemporaryDirectory(prefix="pantheon-cron-ooda-live-smoke-") as tmp_dir:
            store_path = Path(tmp_dir) / "ooda_packets.json"

            result = close_persona_cron_dispatch(
                persona_id,
                workflow_id,
                job_id=job_id,
                runtime=runtime,
                store_path=store_path,
                poll_timeout_seconds=45.0,
            )

            self.assertEqual(result.cron_run_status, "ok")
            self.assertTrue(result.cron_run_id)
            self.assertEqual(result.packet["producer"]["cron_run_id"], result.cron_run_id)
            self.assertEqual(result.packet["producer"]["fabricated"], False)
            self.assertFalse(result.packet["act"]["live_capital_side_effects"])
            self.assertEqual(result.packet["environment"], "paper")

            # Prove the BFF's OWN file-parsing/read-surface code -- not a
            # reimplementation of it -- accepts what this module wrote.
            available, records = _bff_read_ooda_packets(store_path)

            self.assertTrue(available, "BFF read surface reported the ooda_packets dataset unavailable")
            by_id = {str(r.get("packet_id") or r.get("id")): r for r in records}
            self.assertIn(result.packet_id, by_id)
            bff_packet = by_id[result.packet_id]
            self.assertEqual(bff_packet.get("producer", {}).get("cron_run_id"), result.cron_run_id)


def _any_registered_job(runtime, workflow_id: str) -> tuple[str | None, str | None]:
    """Scan `cron.list` for any persona with a registered *workflow_id* job.

    Real lookup only -- reads the persona_id/workflow_id straight out of each
    job's own systemEvent payload text (never invents one).
    """
    import json

    offset = 0
    while True:
        listing = runtime.gateway_call("cron.list", {"limit": 200, "offset": offset}) or {}
        jobs = listing.get("jobs") or []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            text = (job.get("payload") or {}).get("text")
            if not text:
                continue
            try:
                inner = json.loads(text)
            except (TypeError, ValueError):
                continue
            if inner.get("workflow_id") == workflow_id and inner.get("persona_id"):
                return str(inner["persona_id"]), str(job.get("id") or "")
        if not listing.get("hasMore"):
            return None, None
        offset = listing.get("nextOffset", offset + len(jobs))


if __name__ == "__main__":
    unittest.main()
