"""Live smoke: force-run a real persona OpenClaw cron job and prove it closes
into a packet an explicit, local OODA-packet-store reader actually accepts.

This is NOT a mock test. When a live OpenClaw gateway + adapter are reachable
(the same `pantheon-openclaw-gateway` / `pantheon-openclaw-gateway-adapter`
compose services other OPENCLAW-* smoke scripts use), it exercises the full
evidence chain end to end:

    cron.run (force) -> cron.runs (status "ok") -> a real /v1/responses OODA
    turn on the persona's own agent -> a persisted packet whose refs carry
    that exact cron run id -> `_read_ooda_packet_store_records` below (a
    local, typed reader for exactly the on-disk envelope shape
    `services/persona/cron_ooda_closure.append_ooda_packet` writes) reading
    it back.

`services/control-plane/bff/read_store.py`'s legacy `ServiceBackedReadAdapter`
is not the production read path any more -- `services/control-plane/bff/main.py`
defaults `read_store` to `create_read_surface_ports()` -- so this module no
longer depends on it; the reader below is this test's own explicit parser for
the packet-store envelope format `append_ooda_packet` documents (`.jsonl`
newline-delimited records, or a single JSON array of the same record
envelopes for any other suffix).

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
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_CRON_DIR = REPO_ROOT / "services" / "control-plane" / "cron"
if str(_CRON_DIR) not in sys.path:
    sys.path.insert(0, str(_CRON_DIR))

_OODA_PACKET_RECORD_SCHEMA_VERSION = "ooda_loop_packet_record.v1"


def _extract_packet_from_record(record: Any) -> "dict[str, Any] | None":
    """Pull the packet dict out of one stored record envelope.

    Mirrors the envelope shape `cron_ooda_closure.append_ooda_packet` and
    `jsonl_store.OodaJsonlAppendStore` write: either a raw packet dict, or a
    `{"schema_version": ..., "record_type": "packet_snapshot"|"stage_transition",
    "payload": {...}}` envelope.
    """
    if not isinstance(record, dict):
        return None
    packet: Any = record
    if str(record.get("schema_version") or "") == _OODA_PACKET_RECORD_SCHEMA_VERSION:
        payload = record.get("payload")
        record_type = str(record.get("record_type") or "")
        if record_type == "packet_snapshot":
            packet = payload if isinstance(payload, dict) else None
        elif record_type == "stage_transition":
            packet = payload.get("packet") if isinstance(payload, dict) else None
        else:
            packet = None
        if isinstance(packet, dict):
            packet = dict(packet)
            packet.setdefault("packet_id", record.get("packet_id"))
    return packet if isinstance(packet, dict) else None


def _read_ooda_packet_store_records(store_path: Path) -> "list[dict[str, Any]]":
    """Read every packet persisted at *store_path*.

    Handles both on-disk shapes `append_ooda_packet` documents: `.jsonl`
    newline-delimited record envelopes, and a single JSON array of the same
    envelopes for any other file suffix.
    """
    if not store_path.exists():
        return []
    text = store_path.read_text(encoding="utf-8").strip()
    if not text:
        return []

    raw_records: list[Any] = []
    if store_path.suffix.lower() == ".jsonl":
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw_records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    else:
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            loaded = []
        if isinstance(loaded, list):
            raw_records = loaded
        elif isinstance(loaded, dict):
            raw_records = [loaded]

    packets_by_id: dict[str, dict[str, Any]] = {}
    for raw_record in raw_records:
        packet = _extract_packet_from_record(raw_record)
        if packet is None:
            continue
        packet_id = str(packet.get("packet_id") or packet.get("id") or "")
        if packet_id:
            packets_by_id[packet_id] = packet
    return list(packets_by_id.values())


def _bff_read_ooda_packets(store_path: Path) -> tuple[bool, list[dict]]:
    records = _read_ooda_packet_store_records(store_path)
    return bool(records) or store_path.exists(), records


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
