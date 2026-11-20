"""Close a live OpenClaw persona cron dispatch into a persisted OODA packet.

Design decision (OPENCLAW-OODA-PACKET-CLOSURE)
------------------------------------------------
`PersonaCronRegistrar` registers four recurring OpenClaw cron jobs per persona
(ingest/review/retrain/deploy). When one of those jobs fires, the gateway just
wakes the persona's own agent session with a `systemEvent`
(`delivery.mode: "none"`, `wakeMode: "next-heartbeat"`) — by upstream design
that produces no synchronous, visible reply (`cron.runs` entries confirm
`deliveryStatus: "not-requested"`). Nothing in this repo ever turned that wake
into a persisted OODA packet: `services/persona/ooda_cycle_runtime.py` is a
separate, static `AlphaSeedSource`-fed batch generator that never reads cron
runs at all.

Three closure designs were possible (see
`.orchestrator/task-briefs/openclaw_ooda_packet_closure.md`):

  A. Give the persona agent a "write back to Pantheon" tool so its own turn
     persists the packet. Rejected: OpenClaw has no such tool surface wired
     into this repo yet, and building one is a much larger, separate
     integration than "close the loop for the recurring cron jobs already
     registered."
  C. Have `upstream_entrypoint` (e.g. `deployment.plan`) trigger the existing
     Pantheon-side `CronOrchestrator` workflow handler and treat its output as
     the packet source. Rejected: `CronOrchestrator.run()` requires a fully
     formed real payload (e.g. deploy needs `registry_entry` +
     `capital_pool_id` + `approval_decision_id`) that does not exist at an
     arbitrary recurring tick — forcing a payload through it on every tick
     would mean fabricating governance artifacts, which the brief explicitly
     forbids.
  B. **Chosen.** Pantheon-side closure: force-run (or observe) the persona's
     already-registered recurring cron job, read the REAL terminal
     `cron.runs` record (job id, run id, status, timestamps, and the
     dispatch `systemEvent` echoed back verbatim in `entry["summary"]`), then
     drive one REAL synchronous OODA turn on that persona's own OpenClaw
     agent (`integrations/openclaw/persona_ooda_runtime.run_persona_ooda_turn`,
     the same real `/v1/responses` transport `oss_runtime._run_openclaw`
     already uses) scoped to that exact cron dispatch context. The resulting
     packet's `producer` block carries the cron job id / run id / request id
     fingerprint, so the evidence chain is: `cron.run` -> `cron.runs` (status
     `ok`) -> packet with that run id in its refs.

Honesty guardrails this module enforces:

- If the forced cron run does not reach `status: "ok"`, no packet is written
  (fail closed — never fabricate a "success" packet for a failed/timed-out
  run).
- If the live OODA turn errors or returns empty text (no gateway reachable,
  agent error, etc.), the packet is still written — stopped honestly at
  `observing` with an explicit `agent-turn-unavailable` audit ref — instead of
  inventing an observe/orient/decide narrative. A packet only advances past
  `observing` when a real, non-empty decision text was actually returned by
  the persona's own agent.
- `environment` is always `paper` and `act.live_capital_side_effects` is
  always `False`; this module never touches live capital or broker state.

This is intentionally a module separate from `ooda_cycle_runtime.py` (per the
brief: "由 ooda_cycle_runtime 之外的閉環寫 packet") — it does not replace or
call the static batch generator, and it reuses the *same* shared JSONL
packet store contract (`services/control-plane/ooda/jsonl_store.py`) so
`/bff/ooda/packets` picks up its output the same way it already does for
`persona_ooda_bootstrap.py` and `ooda_cycle_runtime.py`.
"""
from __future__ import annotations

import hashlib
import json
import fcntl
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]

from services.control_plane.ooda.jsonl_store import (  # noqa: E402
    DEFAULT_OODA_PACKET_STORE_PATH,
    SCHEMA_VERSION as _OODA_RECORD_SCHEMA_VERSION,
    OodaJsonlAppendStore,
)
from services.control_plane.ooda.ooda_loop_packet import (  # noqa: E402
    ActBundle,
    DecideBundle,
    LearnBundle,
    LoopEnvironment,
    LoopStatus,
    LoopType,
    ObserveBundle,
    OodaLoopPacket,
    OrientBundle,
)
from services.control_plane.ooda.stage_transition import validate_status_transition  # noqa: E402
from services.control_plane.cron.workflows import (  # noqa: E402
    PERSONA_DAILY_CYCLE_WORKFLOW_ID,
    WorkflowDefinition,
    get_workflow_definition,
)

from integrations.openclaw.adapter.cron_transport import force_run_job  # noqa: E402
from integrations.openclaw.persona_ooda_runtime import (  # noqa: E402
    ALWAYS_ELIDED_KEYS,
    PACKET_LIST_BOUND,
    PersonaOodaTurn,
    bound_observe,
    build_ooda_prompt,
    run_persona_ooda_turn,
)


class CronOodaClosureError(RuntimeError):
    """Raised when a cron dispatch cannot be honestly closed into a packet."""


# Coarse workflow -> loop_type mapping. Not a canonical taxonomy claim — just a
# reasonable bucket per workflow so packets are queryable by loop_type.
_WORKFLOW_LOOP_TYPES: dict[str, LoopType] = {
    "pantheon.ingest": LoopType.PAPER_STRATEGY,
    "pantheon.review": LoopType.PAPER_STRATEGY,
    "pantheon.retrain": LoopType.EVOLUTION,
    "pantheon.deploy": LoopType.REBALANCE,
}


@dataclass(frozen=True)
class CronOodaClosureResult:
    packet_id: str
    packet: dict[str, Any]
    cron_job_id: str
    cron_run_id: Optional[str]
    cron_run_status: str
    agent_turn_status: str
    store_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "packet": self.packet,
            "cron_job_id": self.cron_job_id,
            "cron_run_id": self.cron_run_id,
            "cron_run_status": self.cron_run_status,
            "agent_turn_status": self.agent_turn_status,
            "store_path": self.store_path,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in str(value).lower()).strip("-")


def _parse_dispatch_summary(run: Mapping[str, Any]) -> dict[str, Any]:
    """Parse the `pantheon.workflow.dispatch` systemEvent echoed back on the run.

    `cron.runs` entries include `summary`, which is the exact `payload.text`
    JSON string submitted at `cron.add` time (see
    `PersonaCronRegistrar._build_system_event_text`). Real fields only — this
    never invents `persona_id`/`request_id`/`upstream_entrypoint` values.
    """
    raw = run.get("summary")
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def resolve_ooda_packet_store_path() -> Path:
    """Resolve the OODA packet store path the same way the BFF read surface does.

    `services/control-plane/bff/read_store.py` resolves the `ooda_packets`
    dataset from `PANTHEON_BFF_OODA_PACKET_STORE` first, else
    `PANTHEON_OODA_DATA_DIR`/`PANTHEON_CONTROL_PLANE_DATA_DIR` + a filename
    candidate. Mirroring that here (instead of always writing to the
    default `.orchestrator/ooda/ooda_loop_packets.jsonl` path) is what makes a
    packet this module writes actually show up on a running deployment's
    `/bff/ooda/packets` — a real deployment may have that env var pointed
    somewhere else entirely.
    """
    explicit = os.environ.get("PANTHEON_BFF_OODA_PACKET_STORE", "").strip()
    if explicit:
        return Path(explicit)
    for dir_env in ("PANTHEON_OODA_DATA_DIR", "PANTHEON_CONTROL_PLANE_DATA_DIR"):
        base = os.environ.get(dir_env, "").strip()
        if base:
            return Path(base) / "ooda_packets.jsonl"
    return DEFAULT_OODA_PACKET_STORE_PATH


# --- Bounded packet store (PERSONA-OODA-PACKET-STORE-BOUNDED-20261117) -------
# A persisted packet never embeds an unbounded series; the live observe bundle
# handed to the turn is bounded by the same shared rule (persona_ooda_runtime.bound_observe).
DEFAULT_RETENTION_DAYS = 30
DEFAULT_MAX_RECORDS = 1000
_INDEX_SUFFIX = ".index"


def bound_packet_payload(packet_dict: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of *packet_dict* whose observe bundle carries no unbounded list.

    Oversized lists (and always `quarter_equity_observations`) are replaced by
    `<key>_elided = {length, sha256, source_ref}`; scalar summaries stay.
    """
    out = json.loads(json.dumps(packet_dict, ensure_ascii=False))
    cycle = out.get("persona_cycle")
    if isinstance(cycle, dict) and isinstance(cycle.get("observe_bundle"), dict):
        cycle["observe_bundle"] = bound_observe(cycle["observe_bundle"])
    return out


def _build_packet_record(packet_dict: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": _OODA_RECORD_SCHEMA_VERSION,
        "record_type": "packet_snapshot",
        "record_id": f"ooda-rec-{uuid.uuid4()}",
        "packet_id": packet_dict["packet_id"],
        "recorded_at": _utc_now(),
        "payload": bound_packet_payload(packet_dict),
    }


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default
    return value if value > 0 else default


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _index_path(store_path: Path) -> Path:
    return store_path.with_suffix(store_path.suffix + _INDEX_SUFFIX)


def _read_raw_index(store_path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(_index_path(store_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _atomic_write(path: Path, text: str) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _apply_cap(
    records: list[Any], watermarks: dict[str, int], now: datetime
) -> tuple[list[Any], dict[str, int], int]:
    """Dedupe by packet_id (last wins), prune beyond the retention window and the
    record cap, and advance the per-job replay watermark for what is pruned."""
    last_index: dict[str, int] = {}
    for index, record in enumerate(records):
        if isinstance(record, dict):
            last_index[str(record.get("packet_id"))] = index
    deduped = [r for i, r in enumerate(records)
               if not isinstance(r, dict) or last_index.get(str(r.get("packet_id"))) == i]
    cutoff = now - timedelta(days=_env_int("PANTHEON_OODA_PACKET_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))
    cap = _env_int("PANTHEON_OODA_PACKET_MAX_RECORDS", DEFAULT_MAX_RECORDS)
    kept: list[Any] = []
    pruned: list[Any] = []
    for record in deduped:
        stamp = _parse_ts(record.get("recorded_at")) if isinstance(record, dict) else None
        (pruned if stamp is not None and stamp < cutoff else kept).append(record)
    while len(kept) > cap:
        pruned.append(kept.pop(0))
    marks = dict(watermarks)
    for record in pruned:
        producer = (record.get("payload") or {}).get("producer") or {}
        job, at = producer.get("cron_job_id"), producer.get("cron_run_at_ms")
        if isinstance(job, str) and type(at) is int:
            marks[job] = max(marks.get(job, at), at)
    return kept, marks, len(pruned)


def _write_store_locked(store_path: Path, records: list[Any], watermarks: dict[str, int]) -> None:
    _atomic_write(store_path, json.dumps(records, ensure_ascii=False, indent=2))
    stat = store_path.stat()
    identities = [r["packet_id"] for r in records if isinstance(r, dict) and r.get("record_type") == "packet_snapshot"]
    _atomic_write(_index_path(store_path), json.dumps({
        "schema": 1, "store": {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
        "watermarks": watermarks, "identities": identities, "records": len(records)}))


def _load_records(store_path: Path) -> list[Any]:
    if not store_path.exists():
        return []
    text = store_path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    loaded = json.loads(text)
    if not isinstance(loaded, list):
        raise CronOodaClosureError("OODA JSON store must contain record envelopes")
    return loaded


def compact_ooda_packet_store(store_path: Path | str, *, now: datetime | None = None) -> dict[str, Any]:
    """Compact a `.json` packet store in place, atomically, keeping every packet
    identity (pruned ones live on as the per-job replay watermark). The result is
    still a JSON array of record envelopes, so the previous release reads it."""
    store_path = Path(store_path)
    if store_path.suffix.lower() == ".jsonl" or not store_path.exists():
        return {"compacted": False}
    with store_path.with_suffix(store_path.suffix + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        before = store_path.stat().st_size
        records = _load_records(store_path)
        for record in records:
            if isinstance(record, dict) and isinstance(record.get("payload"), dict):
                record["payload"] = bound_packet_payload(record["payload"])
        marks = {k: v for k, v in (_read_raw_index(store_path).get("watermarks") or {}).items()
                 if isinstance(k, str) and type(v) is int}
        kept, marks, pruned = _apply_cap(records, marks, now or datetime.now(timezone.utc))
        _write_store_locked(store_path, kept, marks)
        return {"compacted": True, "bytes_before": before, "bytes_after": store_path.stat().st_size,
                "records_before": len(records), "records_after": len(kept), "pruned": pruned}


def load_packet_index(store_path: Path | str) -> dict[str, Any]:
    """Bounded replay index `{"identities": set, "watermarks": {job: ms}}`.

    Reads only the small sidecar when it matches the store file; otherwise (first
    start, or a previous release wrote the file) compacts once to rebuild it."""
    store_path = Path(store_path)
    if not store_path.exists():
        return {"identities": set(), "watermarks": {}}
    raw = _read_raw_index(store_path)
    stat = store_path.stat()
    if raw.get("store") != {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}:
        compact_ooda_packet_store(store_path)
        raw = _read_raw_index(store_path)
    return {"identities": set(raw.get("identities") or []),
            "watermarks": {k: v for k, v in (raw.get("watermarks") or {}).items() if type(v) is int}}


def append_ooda_packet(store_path: Path, packet_dict: Mapping[str, Any]) -> dict[str, Any]:
    """Persist *packet_dict* to *store_path*, matching whichever on-disk shape
    that path implies.

    `.jsonl` paths use the canonical append-only `OodaJsonlAppendStore` (same
    substrate `ooda_cycle_runtime.py` and `persona_ooda_bootstrap.py` use).
    Any other suffix (e.g. the `.json` single-document shape some deployments
    configure `PANTHEON_BFF_OODA_PACKET_STORE` to) is treated as a JSON array
    of the same record envelopes, appended and rewritten whole — the BFF read
    surface (`_load_record_store_payload`) parses non-`.jsonl` files as one
    JSON document, so newline-delimited appends would not parse there.

    The `.json` store is bounded: the persisted payload drops unbounded series,
    and records past the retention window / record cap are pruned at write time
    while a per-job replay watermark (sidecar index) keeps them from re-running.
    """
    store_path.parent.mkdir(parents=True, exist_ok=True)
    if store_path.suffix.lower() == ".jsonl":
        store = OodaJsonlAppendStore(store_path)
        return store.append_packet(packet_dict)

    record = _build_packet_record(packet_dict)
    with store_path.with_suffix(store_path.suffix + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = _load_records(store_path)
        existing.append(record)
        marks = {k: v for k, v in (_read_raw_index(store_path).get("watermarks") or {}).items()
                 if isinstance(k, str) and type(v) is int}
        kept, marks, _ = _apply_cap(existing, marks, datetime.now(timezone.utc))
        _write_store_locked(store_path, kept, marks)
    return record


def _advance(packet: OodaLoopPacket, status: LoopStatus, at: str) -> None:
    validate_status_transition(LoopStatus(packet.status).value, status.value)
    packet.status = status
    packet.updated_at = at
    if status == LoopStatus.CLOSED:
        packet.closed_at = at


def close_persona_cron_dispatch(
    persona_id: str,
    workflow_id: str,
    *,
    job_id: str,
    runtime: Any,
    store_path: Path | str | None = None,
    turn_runner: Callable[..., PersonaOodaTurn] = run_persona_ooda_turn,
    poll_timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 1.0,
    now: Callable[[], str] = _utc_now,
) -> CronOodaClosureResult:
    """Force-run *job_id* (a persona's already-registered OODA cron job) and
    close it into a real, persisted OODA packet.

    Never raises for an unreachable/erroring persona agent turn — that is
    recorded honestly in the packet instead. Raises `CronOodaClosureError`
    only when the cron run itself does not reach a real `status: "ok"`
    terminal state, since fabricating a packet for a failed dispatch would
    violate the "never fake the producer" requirement this module exists to
    satisfy.
    """
    workflow: WorkflowDefinition = get_workflow_definition(workflow_id)
    if workflow_id == PERSONA_DAILY_CYCLE_WORKFLOW_ID:
        raise CronOodaClosureError(
            "daily-cycle runs must use PersonaCycleController.consume_run; "
            "legacy force-run closure cannot bypass durable claims and proposal validation"
        )

    run, _runs_response = force_run_job(
        runtime,
        job_id,
        poll_timeout_seconds=poll_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    run_status = str(run.get("status") or "")
    run_id = run.get("runId")
    if run_status != "ok":
        raise CronOodaClosureError(
            f"cron job {job_id} for {persona_id}/{workflow_id} did not reach status 'ok' "
            f"(got {run_status!r}); refusing to fabricate a packet for a non-ok run"
        )

    dispatch = _parse_dispatch_summary(run)
    request_id = dispatch.get("request_id")
    upstream_entrypoint = dispatch.get("upstream_entrypoint") or workflow.upstream_entrypoint
    dispatch_persona_id = dispatch.get("persona_id") or persona_id
    if dispatch_persona_id != persona_id:
        raise CronOodaClosureError(
            f"cron job {job_id} dispatch payload is for persona {dispatch_persona_id!r}, "
            f"not the requested {persona_id!r}; refusing to mislabel the packet"
        )

    created_at = now()
    packet_id = (
        f"ooda-cron-{_safe_id(persona_id)}-{_safe_id(workflow_id)}-{_safe_id(str(run_id or uuid.uuid4().hex[:8]))}"
    )[:120]

    packet = OodaLoopPacket(
        packet_id=packet_id,
        loop_type=_WORKFLOW_LOOP_TYPES.get(workflow_id, LoopType.PAPER_STRATEGY),
        status=LoopStatus.OPEN,
        environment=LoopEnvironment.PAPER,
        created_at=created_at,
        updated_at=created_at,
        persona_ids=[persona_id],
        observe=ObserveBundle(),
        orient=OrientBundle(),
        decide=DecideBundle(),
        act=ActBundle(live_capital_side_effects=False),
        learn=LearnBundle(),
        audit_refs=[
            f"audit://cron-ooda-closure/{persona_id}/{workflow_id}/cron-job/{job_id}",
            f"audit://cron-ooda-closure/{persona_id}/{workflow_id}/cron-run/{run_id}",
            "audit://cron-ooda-closure/no-live-capital-side-effects",
        ],
    )
    packet.observe.source_refs.append(f"cron-job://{job_id}")
    packet.observe.telemetry_refs.append(f"cron-run://{run_id}")
    if request_id:
        packet.observe.signal_refs.append(f"systemEvent-request-id://{request_id}")
    packet.observe.market_refs.append(f"cron-dispatch://{workflow_id}/{upstream_entrypoint}")
    _advance(packet, LoopStatus.OBSERVING, created_at)

    turn: Optional[PersonaOodaTurn] = None
    turn_error: Optional[str] = None
    try:
        prompt = build_ooda_prompt(
            {"persona_id": persona_id, "name": persona_id},
            {
                "cron_job_id": job_id,
                "cron_run_id": run_id,
                "workflow_id": workflow_id,
                "upstream_entrypoint": upstream_entrypoint,
                "request_id": request_id,
            },
        )
        turn = turn_runner(persona_id, prompt)
    except Exception as exc:  # noqa: BLE001
        turn_error = str(exc)[:300]

    turn_ref = f"cron-ooda-turn://{persona_id}/{request_id or run_id}"
    if turn is not None and turn.status == "completed" and turn.decision.strip():
        turn_at = now()
        packet.orient.evidence_bundle_refs.append(turn_ref)
        _advance(packet, LoopStatus.ORIENTED, turn_at)

        packet.decide.decision_rationale_ref = turn_ref
        packet.decide.sponsor_persona_id = persona_id
        packet.decide.policy_decision_refs.append(workflow.policy_id)
        _advance(packet, LoopStatus.DECIDED, turn_at)

        packet.act.command_receipt_refs.append(f"cron-run://{run_id}")
        packet.act.safe_mode_refs.append("safe-mode://no-live-capital-side-effects")
        packet.act.live_capital_side_effects = False
        _advance(packet, LoopStatus.ACTED, turn_at)

        packet.learn.telemetry_refs.append(f"cron-run://{run_id}")
        _advance(packet, LoopStatus.CLOSED, turn_at)
        agent_turn_status = "completed"
    else:
        agent_turn_status = turn.status if turn is not None else "error"
        reason = (turn.error if turn is not None and turn.error else turn_error) or "empty decision text"
        packet.audit_refs.append(
            f"audit://cron-ooda-closure/{persona_id}/{workflow_id}/agent-turn-unavailable/{reason}"[:300]
        )

    validation_errors = packet.validate()
    if validation_errors:
        raise CronOodaClosureError(f"invalid OODA packet {packet.packet_id}: {validation_errors}")

    packet_dict = packet.to_dict()
    packet_dict["producer"] = {
        "channel": "openclaw_cron_dispatch",
        "cron_job_id": job_id,
        "cron_run_id": run_id,
        "cron_run_status": run_status,
        "cron_run_at_ms": run.get("runAtMs"),
        "cron_run_duration_ms": run.get("durationMs"),
        "request_id": request_id,
        "workflow_id": workflow_id,
        "upstream_entrypoint": upstream_entrypoint,
        "fabricated": False,
    }
    if turn is not None:
        packet_dict["cron_ooda_turn"] = turn.to_dict()

    resolved_store_path = Path(store_path) if store_path else resolve_ooda_packet_store_path()
    append_ooda_packet(resolved_store_path, packet_dict)

    return CronOodaClosureResult(
        packet_id=packet_id,
        packet=packet_dict,
        cron_job_id=job_id,
        cron_run_id=run_id,
        cron_run_status=run_status,
        agent_turn_status=agent_turn_status,
        store_path=str(resolved_store_path),
    )


def find_persona_cron_job(
    runtime: Any,
    persona_id: str,
    workflow_id: str,
) -> Optional[str]:
    """Look up the job id `PersonaCronRegistrar` registered for *persona_id* /
    *workflow_id* by scanning `cron.list` payload text (the same
    persona_id/workflow_id-in-payload matching `PersonaCronRegistrar.
    _existing_registrations` uses, since the gateway does not persist a
    reliable `sessionTarget`/name -> persona mapping — see
    `persona_cron_registrar.py`'s `_existing_registrations` docstring).

    Returns `None` if no matching job is registered.
    """
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
            if inner.get("persona_id") == persona_id and inner.get("workflow_id") == workflow_id:
                return str(job.get("id") or "") or None
        if not listing.get("hasMore"):
            return None
        offset = listing.get("nextOffset", offset + len(jobs))
