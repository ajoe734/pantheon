"""Durable canonical telemetry -> Trade Journey and loop-run projector.

The projector consumes committed ``telemetry_events`` rows by the monotonic
``ingested_seq`` assigned by Postgres.  It owns one atomic read-model bundle:

* ``trade_journey_events.json`` -- immutable derived stage events;
* ``loop_runs.json`` -- one loop run for the same lifecycle identity; and
* ``controller_state.json`` -- durable checkpoint and live/repair watermarks.

Only records consumed in ``live`` mode advance live freshness.  Startup catch-
up, replay, and manual backfill can repair the read model, but remain explicitly
labelled and can never make a backfill-only projection look live.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time
from typing import Any, Callable, Iterable, Mapping, Sequence
import uuid

from services.trade_journey.correlation_envelope import (
    CorrelationEnvelopeError,
    validate_envelope,
)
from services.trade_journey.materializer import JourneyMaterializer


STATE_SCHEMA = "pantheon.lifecycle-projector-state.v1"
JOURNEY_STORE_SCHEMA = "pantheon.trade-journey-projection.v1"
LOOP_STORE_SCHEMA = "pantheon.loop-run-projection.v1"
PROJECTION_MODES = frozenset({"live", "recovery", "backfill", "replay"})

DEFAULT_ROOT = Path("/data/bff/lifecycle-projection")
DEFAULT_STATE_PATH = DEFAULT_ROOT / "controller_state.json"
DEFAULT_CHANNEL = "pantheon_lifecycle_events"

LIFECYCLE_EVENT_TYPES = frozenset(
    {
        "signal_generation",
        "trade_decision",
        "risk_evaluation",
        "paper_order_simulated",
        "order_submitted",
        "order_accepted",
        "order_partially_filled",
        "paper_fill_simulated",
        "fill_received",
        "order_filled",
        "order_rejection",
        "order_rejection_simulated",
        "order_canceled",
        "order_cancelled",
        "position_snapshot",
        "position_snapshot_received",
        "broker_position_snapshot",
        "reconciliation_completed",
        "reconciliation_failed",
    }
)

STABLE_IDENTITY_FIELDS = (
    "tenant_id",
    "environment",
    "journey_id",
    "run_id",
    "loop_run_id",
    "signal_id",
    "strategy_id",
    "runtime_id",
    "binding_id",
    "capital_pool_id",
    "persona_id",
    "persona_capital_binding_id",
    "artifact_id",
    "artifact_version",
    "plan_id",
    "trace_id",
)

_PASSTHROUGH_FIELDS = (
    "decision_id",
    "risk_decision_id",
    "client_order_id",
    "order_id",
    "broker_order_id",
    "broker_trade_id",
    "ledger_entry_id",
    "reconciliation_id",
    "symbol",
    "side",
    "quantity",
    "price",
)


class LifecycleProjectionError(RuntimeError):
    """Base error for projection failures that must fail closed."""


class ConflictingLifecycleEvent(LifecycleProjectionError):
    """An existing canonical event id was reused with different content."""


class InvalidLifecycleEvent(LifecycleProjectionError):
    """A lifecycle event lacks canonical identity or ordering evidence."""


@dataclass(frozen=True)
class ProjectionResult:
    checkpoint: int
    accepted: int
    duplicates: int
    ignored: int
    quarantined: int
    journey_count: int
    loop_run_count: int
    generation: int
    mode: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _parse_iso(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise InvalidLifecycleEvent(f"invalid timezone-aware timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise InvalidLifecycleEvent(f"invalid timezone-aware timestamp: {value!r}")
    return parsed.astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _clean(value: Any) -> Any:
    return None if value in (None, "", [], {}) else value


def _first(*values: Any) -> Any:
    for value in values:
        cleaned = _clean(value)
        if cleaned is not None:
            return cleaned
    return None


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


class AtomicProjectionBundle:
    """Publish two read models with one atomic ``current`` symlink switch."""

    def __init__(
        self,
        root: str | Path,
        *,
        before_switch: Callable[[Path], None] | None = None,
    ) -> None:
        self.root = Path(root)
        self.generations = self.root / "generations"
        self.current = self.root / "current"
        self._before_switch = before_switch

    @staticmethod
    def _validate_immutable_generation(
        final: Path,
        expected: Mapping[str, Mapping[str, Any]],
    ) -> None:
        try:
            metadata = final.lstat()
            children = {path.name for path in final.iterdir()}
        except OSError as exc:
            raise LifecycleProjectionError(
                "content-addressed projection generation is unreadable"
            ) from exc
        if (
            final.is_symlink()
            or not final.is_dir()
            or metadata.st_mode & 0o222
            or children != set(expected)
        ):
            raise LifecycleProjectionError(
                "content-addressed projection generation is not immutable"
            )
        for name, payload in expected.items():
            path = final / name
            expected_raw = (
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            ).encode("utf-8")
            try:
                file_metadata = path.lstat()
                raw = path.read_bytes()
            except OSError as exc:
                raise LifecycleProjectionError(
                    "content-addressed projection file is unreadable"
                ) from exc
            if (
                path.is_symlink()
                or not path.is_file()
                or file_metadata.st_mode & 0o222
                or raw != expected_raw
            ):
                raise LifecycleProjectionError(
                    "content-addressed projection generation digest collision"
                )

    def publish(
        self,
        generation: int,
        journey_payload: Mapping[str, Any],
        loop_payload: Mapping[str, Any],
    ) -> Path:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o755)
        try:
            root_metadata = self.root.lstat()
        except OSError as exc:
            raise LifecycleProjectionError(
                "projection bundle root is unreadable"
            ) from exc
        if (
            self.root.is_symlink()
            or not stat.S_ISDIR(root_metadata.st_mode)
            or root_metadata.st_mode & 0o022
        ):
            raise LifecycleProjectionError(
                "projection bundle root is not canonical"
            )
        lock_path = self.root / ".projection-publish.lock"
        lock_flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            lock_flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            lock_flags |= os.O_NOFOLLOW
        lock_fd: int | None = None
        try:
            lock_fd = os.open(lock_path, lock_flags, 0o600)
            lock_metadata = os.fstat(lock_fd)
            lock_path_metadata = lock_path.lstat()
        except OSError as exc:
            if lock_fd is not None:
                os.close(lock_fd)
            raise LifecycleProjectionError(
                "projection publish lock is unavailable"
            ) from exc
        assert lock_fd is not None
        try:
            if (
                lock_path.is_symlink()
                or not stat.S_ISREG(lock_metadata.st_mode)
                or not stat.S_ISREG(lock_path_metadata.st_mode)
                or (lock_metadata.st_dev, lock_metadata.st_ino)
                != (lock_path_metadata.st_dev, lock_path_metadata.st_ino)
                or lock_metadata.st_uid != root_metadata.st_uid
                or lock_metadata.st_mode & 0o077
            ):
                raise LifecycleProjectionError(
                    "projection publish lock is not canonical"
                )
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            return self._publish_locked(
                generation,
                journey_payload,
                loop_payload,
            )
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)

    def _publish_locked(
        self,
        generation: int,
        journey_payload: Mapping[str, Any],
        loop_payload: Mapping[str, Any],
    ) -> Path:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o755)
        try:
            root_metadata = self.root.lstat()
        except OSError as exc:
            raise LifecycleProjectionError(
                "projection bundle root is unreadable"
            ) from exc
        if (
            self.root.is_symlink()
            or not self.root.is_dir()
            or root_metadata.st_mode & 0o022
        ):
            raise LifecycleProjectionError(
                "projection bundle root is not canonical"
            )
        try:
            self.generations.mkdir(mode=0o755)
        except FileExistsError:
            pass
        try:
            generations_metadata = self.generations.lstat()
            generations_resolved = self.generations.resolve(strict=True)
        except OSError as exc:
            raise LifecycleProjectionError(
                "projection generations root is unreadable"
            ) from exc
        if (
            self.generations.is_symlink()
            or not self.generations.is_dir()
            or generations_metadata.st_mode & 0o022
            or generations_resolved != self.root.resolve(strict=True) / "generations"
        ):
            raise LifecycleProjectionError(
                "projection generations root is not canonical"
            )
        manifest = {
            "schema_version": "pantheon.lifecycle-projection-bundle.v1",
            "generation": generation,
            "journey_sha256": _fingerprint(journey_payload),
            "loop_runs_sha256": _fingerprint(loop_payload),
        }
        bundle_sha256 = _fingerprint(
            {
                "manifest": manifest,
                "trade_journey_events": journey_payload,
                "loop_runs": loop_payload,
            }
        )
        generation_name = f"g{generation:012d}-{bundle_sha256}"
        staging = self.generations / f".{generation_name}.{uuid.uuid4().hex}.tmp"
        final = self.generations / generation_name
        payloads = {
            "manifest.json": manifest,
            "trade_journey_events.json": journey_payload,
            "loop_runs.json": loop_payload,
        }
        tmp_link: Path | None = None
        previous_current_target: Path | None = None
        switched = False
        installed_current_identity: tuple[int, int] | None = None
        final_metadata: os.stat_result | None = None

        def validate_publish_identity() -> None:
            if final_metadata is None:
                raise LifecycleProjectionError(
                    "projection generation identity was not captured"
                )
            try:
                current_root = self.root.lstat()
                current_generations = self.generations.lstat()
                current_final_before = final.lstat()
                current_generations_resolved = self.generations.resolve(
                    strict=True
                )
            except OSError as exc:
                raise LifecycleProjectionError(
                    "projection publish identity became unreadable"
                ) from exc
            if (
                self.root.is_symlink()
                or self.generations.is_symlink()
                or final.is_symlink()
                or not stat.S_ISDIR(current_root.st_mode)
                or not stat.S_ISDIR(current_generations.st_mode)
                or not stat.S_ISDIR(current_final_before.st_mode)
                or current_root.st_mode & 0o022
                or current_generations.st_mode & 0o022
                or current_final_before.st_mode & 0o222
                or (current_root.st_dev, current_root.st_ino)
                != (root_metadata.st_dev, root_metadata.st_ino)
                or (current_generations.st_dev, current_generations.st_ino)
                != (generations_metadata.st_dev, generations_metadata.st_ino)
                or (current_final_before.st_dev, current_final_before.st_ino)
                != (final_metadata.st_dev, final_metadata.st_ino)
                or current_generations_resolved
                != self.root.resolve(strict=True) / "generations"
            ):
                raise LifecycleProjectionError(
                    "projection publish identity changed during switch"
                )
            self._validate_immutable_generation(final, payloads)
            try:
                current_generations_after = self.generations.lstat()
                current_final_after = final.lstat()
            except OSError as exc:
                raise LifecycleProjectionError(
                    "projection publish identity changed during validation"
                ) from exc
            if (
                (current_generations_after.st_dev, current_generations_after.st_ino)
                != (current_generations.st_dev, current_generations.st_ino)
                or (current_final_after.st_dev, current_final_after.st_ino)
                != (current_final_before.st_dev, current_final_before.st_ino)
            ):
                raise LifecycleProjectionError(
                    "projection publish identity changed during validation"
                )

        try:
            if final.exists() or final.is_symlink():
                self._validate_immutable_generation(final, payloads)
            else:
                staging.mkdir()
                for name, payload in payloads.items():
                    _atomic_write_json(staging / name, payload)
                for path in staging.iterdir():
                    path.chmod(0o444)
                staging.chmod(0o555)
                try:
                    os.rename(staging, final)
                except OSError:
                    if not final.exists() and not final.is_symlink():
                        raise
                    staging.chmod(0o755)
                    for path in staging.iterdir():
                        path.chmod(0o644)
                    shutil.rmtree(staging)
                    self._validate_immutable_generation(final, payloads)
            final_metadata = final.lstat()
            if self.current.is_symlink():
                previous_current_target = self.current.readlink()
                try:
                    previous_resolved = self.current.resolve(strict=True)
                    previous_resolved.relative_to(
                        self.generations.resolve(strict=True)
                    )
                except (OSError, ValueError) as exc:
                    raise LifecycleProjectionError(
                        "current projection link is not canonical"
                    ) from exc
                if previous_current_target != (
                    Path("generations") / previous_current_target.name
                ):
                    raise LifecycleProjectionError(
                        "current projection link is not canonical"
                    )
            elif self.current.exists():
                raise LifecycleProjectionError(
                    "current projection path is not a symlink"
                )
            directory_flags = os.O_RDONLY
            if hasattr(os, "O_DIRECTORY"):
                directory_flags |= os.O_DIRECTORY
            if hasattr(os, "O_CLOEXEC"):
                directory_flags |= os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                directory_flags |= os.O_NOFOLLOW
            generations_fd = os.open(self.generations, directory_flags)
            try:
                opened_generations = os.fstat(generations_fd)
                if (
                    opened_generations.st_dev,
                    opened_generations.st_ino,
                ) != (
                    generations_metadata.st_dev,
                    generations_metadata.st_ino,
                ):
                    raise LifecycleProjectionError(
                        "projection generations root changed during publish"
                    )
                os.fsync(generations_fd)
            finally:
                os.close(generations_fd)
            validate_publish_identity()
            if self._before_switch is not None:
                self._before_switch(final)
            validate_publish_identity()
            tmp_link = self.root / f".current.{uuid.uuid4().hex}.tmp"
            os.symlink(str(Path("generations") / generation_name), tmp_link)
            os.replace(tmp_link, self.current)
            switched = True
            try:
                current_metadata = self.current.lstat()
                installed_current_identity = (
                    current_metadata.st_dev,
                    current_metadata.st_ino,
                )
                validate_publish_identity()
                current_target = self.current.readlink()
                current_resolved = self.current.resolve(strict=True)
                current_resolved_metadata = current_resolved.lstat()
            except OSError as exc:
                raise LifecycleProjectionError(
                    "current projection switch is unreadable"
                ) from exc
            if (
                not stat.S_ISLNK(current_metadata.st_mode)
                or current_target != Path("generations") / generation_name
                or current_resolved != final
                or (current_resolved_metadata.st_dev, current_resolved_metadata.st_ino)
                != (final_metadata.st_dev, final_metadata.st_ino)
            ):
                raise LifecycleProjectionError(
                    "current projection switch identity mismatch"
                )
            directory_fd = os.open(self.root, directory_flags)
            try:
                opened_root = os.fstat(directory_fd)
                if (opened_root.st_dev, opened_root.st_ino) != (
                    root_metadata.st_dev,
                    root_metadata.st_ino,
                ):
                    raise LifecycleProjectionError(
                        "projection bundle root changed during publish"
                    )
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            validate_publish_identity()
            return final
        except BaseException:
            rollback_error: BaseException | None = None
            should_rollback = False
            if switched and installed_current_identity is not None:
                try:
                    rollback_current_metadata = self.current.lstat()
                    should_rollback = (
                        stat.S_ISLNK(rollback_current_metadata.st_mode)
                        and (
                            rollback_current_metadata.st_dev,
                            rollback_current_metadata.st_ino,
                        )
                        == installed_current_identity
                        and self.current.readlink()
                        == Path("generations") / generation_name
                    )
                except OSError:
                    should_rollback = False
            if should_rollback:
                try:
                    if previous_current_target is None:
                        self.current.unlink(missing_ok=True)
                    else:
                        rollback_link = (
                            self.root / f".current.rollback.{uuid.uuid4().hex}.tmp"
                        )
                        try:
                            os.symlink(str(previous_current_target), rollback_link)
                            os.replace(rollback_link, self.current)
                        finally:
                            rollback_link.unlink(missing_ok=True)
                    rollback_flags = os.O_RDONLY
                    if hasattr(os, "O_DIRECTORY"):
                        rollback_flags |= os.O_DIRECTORY
                    if hasattr(os, "O_CLOEXEC"):
                        rollback_flags |= os.O_CLOEXEC
                    if hasattr(os, "O_NOFOLLOW"):
                        rollback_flags |= os.O_NOFOLLOW
                    rollback_fd = os.open(self.root, rollback_flags)
                    try:
                        os.fsync(rollback_fd)
                    finally:
                        os.close(rollback_fd)
                except BaseException as rollback_exc:
                    rollback_error = rollback_exc
            if tmp_link is not None:
                try:
                    tmp_link.unlink()
                except FileNotFoundError:
                    pass
            if staging.exists():
                staging.chmod(0o755)
                for path in staging.iterdir():
                    path.chmod(0o644)
                shutil.rmtree(staging, ignore_errors=True)
            if rollback_error is not None:
                raise LifecycleProjectionError(
                    "projection switch failed and rollback was unsuccessful"
                ) from rollback_error
            raise


class LifecycleProjector:
    """Deterministic durable projector over committed telemetry append rows."""

    def __init__(
        self,
        *,
        state_path: str | Path = DEFAULT_STATE_PATH,
        bundle_root: str | Path = DEFAULT_ROOT,
        deployment_sha: str = "unknown",
        clock: Callable[[], str] = _utc_now,
        publisher: AtomicProjectionBundle | None = None,
    ) -> None:
        self.state_path = Path(state_path)
        self.bundle = publisher or AtomicProjectionBundle(bundle_root)
        self.deployment_sha = str(deployment_sha or "unknown")
        self.clock = clock
        self.state = self._load_state()
        self.state["restart_count"] = int(self.state.get("restart_count", 0)) + 1

    @property
    def checkpoint(self) -> int:
        return int(self.state.get("checkpoint", 0))

    @property
    def controller(self) -> dict[str, Any]:
        return copy.deepcopy(self.state.get("controller") or {})

    def _new_state(self) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA,
            "checkpoint": 0,
            "generation": 0,
            "restart_count": 0,
            "canonical_events": {},
            "identity_chains": {},
            "quarantine": [],
            "controller": {
                "controller_id": "canonical-lifecycle-projector",
                "controller_name": "canonical-lifecycle-projector",
                "deployment_sha": self.deployment_sha,
                "status": "initializing",
                "mode": "recovery",
                "truth_level": "unavailable",
                "accepted_live": False,
                "checkpoint": 0,
                "source_high_watermark": 0,
                "backlog": 0,
                "quarantine_count": 0,
                "last_poll_at": None,
                "last_projection_success_at": None,
                "last_live_success_at": None,
                "last_live_event_at": None,
                "last_recovery_at": None,
                "last_backfill_at": None,
                "last_replay_at": None,
                "last_failure_at": None,
                "last_error": None,
            },
        }

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._new_state()
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LifecycleProjectionError(
                f"projector state is unreadable; refusing destructive reset: {self.state_path}"
            ) from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != STATE_SCHEMA:
            raise LifecycleProjectionError(
                f"unsupported projector state; refusing destructive reset: {self.state_path}"
            )
        return payload

    def project_records(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        mode: str,
        source_high_watermark: int | None = None,
    ) -> ProjectionResult:
        if mode not in PROJECTION_MODES:
            raise ValueError(f"unsupported projection mode: {mode}")
        candidate = copy.deepcopy(self.state)
        accepted = duplicates = ignored = quarantined = 0
        max_seen = int(candidate.get("checkpoint", 0))
        now = self.clock()

        ordered_records = sorted(
            (dict(record) for record in records),
            key=lambda row: (int(row.get("ingested_seq") or 0), str(row.get("event_id") or "")),
        )
        for row in ordered_records:
            sequence = int(row.get("ingested_seq") or 0)
            if mode in {"live", "recovery"} and sequence <= 0:
                raise InvalidLifecycleEvent("committed source row requires positive ingested_seq")
            max_seen = max(max_seen, sequence)
            event = self._source_event(row)
            if event["event_type"] not in LIFECYCLE_EVENT_TYPES:
                ignored += 1
                continue
            event_id = event["event_id"]
            fingerprint = _fingerprint(
                {
                    "event_id": event_id,
                    "event_type": event["event_type"],
                    "created_at": event["created_at"],
                    "payload": event,
                }
            )
            previous = (candidate.get("canonical_events") or {}).get(event_id)
            if previous is not None:
                if previous.get("fingerprint") != fingerprint:
                    raise ConflictingLifecycleEvent(
                        f"conflicting canonical event_id: {event_id}"
                    )
                duplicates += 1
                if mode == "live" and previous.get("source_mode") != "live":
                    previous["source_mode"] = "live"
                    previous["accepted_live"] = True
                continue
            try:
                identity = self._identity(event)
                source_sequence = self._sequence_no(event)
                self._admit_identity(candidate, identity)
            except InvalidLifecycleEvent as exc:
                quarantined += 1
                candidate.setdefault("quarantine", []).append(
                    {
                        "event_id": event_id,
                        "ingested_seq": sequence,
                        "event_type": event["event_type"],
                        "reason": str(exc),
                        "quarantined_at": now,
                        "source_mode": mode,
                    }
                )
                candidate["quarantine"] = candidate["quarantine"][-1000:]
                continue
            candidate.setdefault("canonical_events", {})[event_id] = {
                "fingerprint": fingerprint,
                "event": event,
                "identity": identity,
                "sequence_no": source_sequence,
                "ingested_seq": sequence,
                "ingested_at": str(row.get("ingested_at") or now),
                "source_mode": mode,
                "accepted_live": mode == "live",
            }
            accepted += 1

        if mode in {"live", "recovery"}:
            candidate["checkpoint"] = max_seen
        candidate["generation"] = int(candidate.get("generation", 0)) + 1
        controller = candidate.setdefault("controller", {})
        previous_high_watermark = int(controller.get("source_high_watermark") or 0)
        next_high_watermark = (
            int(source_high_watermark)
            if source_high_watermark is not None
            else previous_high_watermark
            if mode in {"backfill", "replay"}
            else max(max_seen, previous_high_watermark)
        )
        controller.update(
            {
                "controller_id": "canonical-lifecycle-projector",
                "controller_name": "canonical-lifecycle-projector",
                "deployment_sha": self.deployment_sha,
                "mode": mode,
                "checkpoint": int(candidate.get("checkpoint", 0)),
                "source_high_watermark": next_high_watermark,
                "last_poll_at": now,
                "last_projection_success_at": now,
                "last_error": None,
                "quarantine_count": len(candidate.get("quarantine") or []),
                "generation": int(candidate.get("generation", 0)),
                "restart_count": int(candidate.get("restart_count", 0)),
            }
        )
        controller["backlog"] = max(
            0,
            int(controller["source_high_watermark"]) - int(candidate.get("checkpoint", 0)),
        )
        if mode == "live" and accepted:
            controller["last_live_success_at"] = now
            live_times = [
                entry["event"]["created_at"]
                for entry in candidate.get("canonical_events", {}).values()
                if entry.get("source_mode") == "live"
            ]
            controller["last_live_event_at"] = max(live_times) if live_times else None
        elif mode == "recovery":
            controller["last_recovery_at"] = now
        elif mode == "backfill":
            controller["last_backfill_at"] = now
        elif mode == "replay":
            controller["last_replay_at"] = now
        historic_live = bool(controller.get("last_live_success_at"))
        controller["accepted_live"] = mode == "live" and historic_live
        controller["truth_level"] = (
            "canonical_live"
            if controller["accepted_live"]
            else f"{mode}_with_historic_live"
            if historic_live
            else f"{mode}_only"
        )
        controller["status"] = (
            "degraded"
            if controller["quarantine_count"]
            else "ready"
            if mode == "live" and controller["accepted_live"] and controller["backlog"] == 0
            else "recovering"
            if mode == "recovery" or controller["backlog"]
            else "repair_only"
        )

        journey_payload, loop_payload = self._render(candidate)
        self.bundle.publish(candidate["generation"], journey_payload, loop_payload)
        _atomic_write_json(self.state_path, candidate)
        self.state = candidate
        return ProjectionResult(
            checkpoint=int(candidate["checkpoint"]),
            accepted=accepted,
            duplicates=duplicates,
            ignored=ignored,
            quarantined=quarantined,
            journey_count=len(journey_payload["events"]),
            loop_run_count=len(loop_payload["records"]),
            generation=int(candidate["generation"]),
            mode=mode,
        )

    def record_poll(
        self,
        *,
        source_high_watermark: int,
        backlog: int,
        mode: str,
    ) -> None:
        """Persist a semantic health heartbeat without advancing live truth."""
        candidate = copy.deepcopy(self.state)
        now = self.clock()
        controller = candidate.setdefault("controller", {})
        semantic_fields = (
            "status",
            "mode",
            "truth_level",
            "accepted_live",
            "checkpoint",
            "source_high_watermark",
            "backlog",
            "last_error",
        )
        previous_semantics = tuple(controller.get(field) for field in semantic_fields)
        controller.update(
            {
                "deployment_sha": self.deployment_sha,
                "last_poll_at": now,
                "source_high_watermark": int(source_high_watermark),
                "backlog": max(0, int(backlog)),
                "mode": mode,
                "checkpoint": int(candidate.get("checkpoint", 0)),
                "last_error": None,
            }
        )
        historic_live = bool(controller.get("last_live_success_at"))
        if mode == "live" and int(backlog) == 0:
            # A successful zero-backlog read is the controller's authoritative
            # live admission boundary after startup/recovery, even when no new
            # trade arrived during that poll.
            controller["last_live_success_at"] = now
            controller["accepted_live"] = True
            controller["truth_level"] = "canonical_live"
        else:
            controller["accepted_live"] = False
            controller["truth_level"] = (
                f"{mode}_with_historic_live" if historic_live else f"{mode}_only"
            )
        controller["status"] = (
            "degraded"
            if int(controller.get("quarantine_count") or 0) > 0
            else "ready"
            if mode == "live" and int(backlog) == 0
            else "recovering"
            if mode == "recovery" or int(backlog) > 0
            else "repair_only"
        )
        current_semantics = tuple(controller.get(field) for field in semantic_fields)
        if current_semantics != previous_semantics:
            candidate["generation"] = int(candidate.get("generation", 0)) + 1
            controller["generation"] = int(candidate["generation"])
            journey_payload, loop_payload = self._render(candidate)
            self.bundle.publish(candidate["generation"], journey_payload, loop_payload)
        _atomic_write_json(self.state_path, candidate)
        self.state = candidate

    def record_source_failure(self, error: str, *, backlog: int | None = None) -> None:
        """Record source failure while preserving the last-good read-model bundle."""
        candidate = copy.deepcopy(self.state)
        candidate["generation"] = int(candidate.get("generation", 0)) + 1
        controller = candidate.setdefault("controller", {})
        controller.update(
            {
                "status": "degraded",
                "last_failure_at": self.clock(),
                "last_error": str(error),
                "generation": int(candidate["generation"]),
                "restart_count": int(candidate.get("restart_count", 0)),
            }
        )
        if backlog is not None:
            controller["backlog"] = max(0, int(backlog))
        journey_payload, loop_payload = self._render(candidate)
        self.bundle.publish(candidate["generation"], journey_payload, loop_payload)
        _atomic_write_json(self.state_path, candidate)
        self.state = candidate

    @staticmethod
    def _source_event(row: Mapping[str, Any]) -> dict[str, Any]:
        payload = row.get("payload")
        event = dict(payload) if isinstance(payload, Mapping) else dict(row)
        for field in ("event_id", "event_type", "created_at"):
            row_value = _clean(row.get(field))
            event_value = _clean(event.get(field))
            if row_value is not None and event_value is not None and str(row_value) != str(event_value):
                raise InvalidLifecycleEvent(f"source row/payload {field} mismatch")
            if event_value is None and row_value is not None:
                event[field] = row_value
            if _clean(event.get(field)) is None:
                raise InvalidLifecycleEvent(f"source event missing {field}")
        _parse_iso(event["created_at"])
        return event

    @staticmethod
    def _sequence_no(event: Mapping[str, Any]) -> int:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        raw = _first(event.get("sequence_no"), metadata.get("sequence_no"))
        if isinstance(raw, bool):
            raise InvalidLifecycleEvent("sequence_no must be a positive integer")
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise InvalidLifecycleEvent("sequence_no must be a positive integer") from exc
        if value < 1:
            raise InvalidLifecycleEvent("sequence_no must be a positive integer")
        return value

    @staticmethod
    def _identity(event: Mapping[str, Any]) -> dict[str, str]:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        authority = event.get("authority_refs") if isinstance(event.get("authority_refs"), Mapping) else {}
        target = event.get("target") if isinstance(event.get("target"), Mapping) else {}
        raw_envelope = event.get("correlation_envelope")
        if not isinstance(raw_envelope, Mapping):
            raise InvalidLifecycleEvent("correlation_envelope is required")
        try:
            envelope = validate_envelope(raw_envelope)
        except CorrelationEnvelopeError as exc:
            raise InvalidLifecycleEvent(f"invalid correlation_envelope: {exc}") from exc
        run_id = _first(event.get("run_id"), metadata.get("run_id"))
        identity: dict[str, Any] = {
            "tenant_id": envelope.get("tenant_id"),
            "environment": envelope.get("environment"),
            "journey_id": envelope.get("journey_id"),
            "run_id": run_id,
            "loop_run_id": _first(event.get("loop_run_id"), metadata.get("loop_run_id"), f"lr-{run_id}" if run_id else None),
            "signal_id": _first(event.get("signal_id"), metadata.get("signal_id")),
            "strategy_id": _first(target.get("strategy_id"), event.get("strategy_id"), metadata.get("strategy_id")),
            "runtime_id": event.get("runtime_id"),
            "binding_id": event.get("binding_id"),
            "capital_pool_id": event.get("capital_pool_id"),
            "persona_id": _first(authority.get("persona_id"), metadata.get("persona_id"), event.get("persona_id")),
            "persona_capital_binding_id": event.get("persona_capital_binding_id"),
            "artifact_id": event.get("artifact_id"),
            "artifact_version": event.get("artifact_version"),
            "plan_id": _first(event.get("plan_id"), event.get("deployment_plan_id")),
            "trace_id": _first(envelope.get("trace_id"), event.get("trace_id")),
        }
        missing = [
            field
            for field in STABLE_IDENTITY_FIELDS
            if not isinstance(identity.get(field), str)
            or _clean(identity.get(field)) is None
        ]
        if missing:
            raise InvalidLifecycleEvent(
                "canonical lifecycle identity missing: " + ", ".join(missing)
            )
        if str(identity["environment"]) != str(envelope["environment"]):
            raise InvalidLifecycleEvent("environment conflicts with correlation envelope")
        return {field: identity[field] for field in STABLE_IDENTITY_FIELDS}

    @staticmethod
    def _admit_identity(state: dict[str, Any], identity: Mapping[str, str]) -> None:
        journey_id = identity["journey_id"]
        chains = state.setdefault("identity_chains", {})
        previous = chains.get(journey_id)
        if previous is None:
            chains[journey_id] = dict(identity)
            return
        mismatched = [
            field
            for field in STABLE_IDENTITY_FIELDS
            if str(previous.get(field)) != str(identity.get(field))
        ]
        if mismatched:
            raise InvalidLifecycleEvent(
                f"identity chain conflict for {journey_id}: {', '.join(mismatched)}"
            )

    def _render(self, state: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        canonical_entries = list((state.get("canonical_events") or {}).values())
        canonical_entries.sort(key=self._entry_sort_key)
        journey_events: list[dict[str, Any]] = []
        for entry in canonical_entries:
            journey_events.extend(self._journey_events(entry))
        journey_events.sort(key=JourneyMaterializer._sort_key)
        materializer = JourneyMaterializer()
        materializer.rebuild(journey_events)
        controller = copy.deepcopy(state.get("controller") or {})
        controller["generation"] = int(state.get("generation", 0))
        controller["restart_count"] = int(state.get("restart_count", 0))
        loop_records = self._loop_records(canonical_entries, materializer, controller)
        journey_payload = {
            "schema_version": JOURNEY_STORE_SCHEMA,
            "projector_owned": True,
            "generation": int(state.get("generation", 0)),
            "projection_mode": controller.get("mode"),
            "accepted_live": bool(controller.get("accepted_live")),
            "controller": controller,
            "events": journey_events,
        }
        loop_payload = {
            "schema_version": LOOP_STORE_SCHEMA,
            "projector_owned": True,
            "generation": int(state.get("generation", 0)),
            "projection_mode": controller.get("mode"),
            "accepted_live": bool(controller.get("accepted_live")),
            "controller": controller,
            "records": {record["id"]: record for record in loop_records},
        }
        return journey_payload, loop_payload

    @staticmethod
    def _entry_sort_key(entry: Mapping[str, Any]) -> tuple[str, int, str, str]:
        identity = entry.get("identity") or {}
        event = entry.get("event") or {}
        return (
            str(identity.get("journey_id") or ""),
            int(entry.get("sequence_no") or 0),
            str(event.get("created_at") or ""),
            str(event.get("event_id") or ""),
        )

    @classmethod
    def _journey_events(cls, entry: Mapping[str, Any]) -> list[dict[str, Any]]:
        source = entry["event"]
        identity = entry["identity"]
        specs = cls._stage_specs(source)
        metadata = source.get("metadata") if isinstance(source.get("metadata"), Mapping) else {}
        result: list[dict[str, Any]] = []
        for ordinal, (stage, status) in enumerate(specs, start=1):
            event = {
                "event_id": f"{source['event_id']}:{stage}",
                "event_type": source["event_type"],
                "journey_id": identity["journey_id"],
                "tenant_id": identity["tenant_id"],
                "environment": identity["environment"],
                "occurred_at": source["created_at"],
                "recorded_at": entry.get("ingested_at") or source["created_at"],
                "source": f"canonical_telemetry_{entry['source_mode']}",
                "source_mode": entry["source_mode"],
                "accepted_live": bool(entry.get("accepted_live")),
                "canonical_event_id": source["event_id"],
                "source_offset": entry.get("ingested_seq"),
                "source_sequence_no": int(entry["sequence_no"]),
                "sequence_no": int(entry["sequence_no"]) * 100 + ordinal,
                "sequence": int(entry["sequence_no"]) * 100 + ordinal,
                "causal_parent_id": _first(
                    source.get("causal_parent_id"),
                    metadata.get("causal_parent_id"),
                    (source.get("correlation_envelope") or {}).get("causation_event_id"),
                ),
                "stage": stage,
                "stage_status": status,
                "correlation_envelope": source.get("correlation_envelope"),
                **identity,
            }
            for field in _PASSTHROUGH_FIELDS:
                value = _first(source.get(field), metadata.get(field))
                if value is not None:
                    event[field] = value
            metrics = source.get("metrics") if isinstance(source.get("metrics"), Mapping) else {}
            if "quantity" not in event:
                quantity = _first(metrics.get("fill_quantity"), source.get("position_qty"))
                if quantity is not None:
                    event["quantity"] = abs(quantity) if isinstance(quantity, (int, float)) else quantity
            if "price" not in event and _clean(metrics.get("fill_price")) is not None:
                event["price"] = metrics["fill_price"]
            result.append({key: value for key, value in event.items() if value is not None})
        return result

    @staticmethod
    def _stage_specs(event: Mapping[str, Any]) -> list[tuple[str, str]]:
        event_type = str(event["event_type"])
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        if event_type == "signal_generation":
            return [("signal_generation", "succeeded")]
        if event_type == "trade_decision":
            return [("trade_decision", _stage_status(metadata.get("decision_status") or "succeeded"))]
        if event_type == "risk_evaluation":
            return [("risk_evaluation", _stage_status(metadata.get("risk_status") or "succeeded"))]
        if event_type == "paper_order_simulated":
            return [
                ("order_submission", _stage_status(metadata.get("order_status") or "succeeded")),
            ]
        if event_type == "order_submitted":
            return [("order_submission", "succeeded")]
        if event_type == "order_accepted":
            return [("broker_acknowledgement", "succeeded")]
        if event_type == "order_partially_filled":
            return [("fill_management", "partially_succeeded")]
        if event_type in {"paper_fill_simulated", "fill_received", "order_filled"}:
            return [("fill_management", "succeeded")]
        if event_type in {"order_rejection", "order_rejection_simulated"}:
            return [("order_submission", "failed")]
        if event_type in {"order_canceled", "order_cancelled"}:
            return [("fill_management", "cancelled")]
        if event_type in {"position_snapshot", "position_snapshot_received", "broker_position_snapshot"}:
            return [("ledger_booking", "succeeded")]
        if event_type == "reconciliation_completed":
            return [("reconciliation", "succeeded")]
        if event_type == "reconciliation_failed":
            return [("reconciliation", "failed")]
        return []

    @staticmethod
    def _loop_records(
        entries: Sequence[Mapping[str, Any]],
        materializer: JourneyMaterializer,
        controller: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for entry in entries:
            grouped.setdefault(entry["identity"]["journey_id"], []).append(entry)
        records: list[dict[str, Any]] = []
        for journey_id, lifecycle in sorted(grouped.items()):
            lifecycle.sort(key=LifecycleProjector._entry_sort_key)
            identity = dict(lifecycle[0]["identity"])
            projection = materializer.get(
                journey_id,
                tenant_id=identity["tenant_id"],
                environment=identity["environment"],
            )
            if projection is None:
                continue
            event_types = [entry["event"]["event_type"] for entry in lifecycle]
            source_modes = sorted({str(entry["source_mode"]) for entry in lifecycle})
            accepted_live = any(bool(entry.get("accepted_live")) for entry in lifecycle)
            status = projection.snapshot.get("status") or "open"
            records.append(
                {
                    "id": identity["loop_run_id"],
                    "loop_run_id": identity["loop_run_id"],
                    "journey_id": journey_id,
                    "loop_type": "paper_execution",
                    "status": "active" if status in {"open", "executing", "partially_filled"} else status,
                    "activePeriod": {
                        "start": projection.snapshot.get("created_at"),
                        "end": projection.snapshot.get("updated_at")
                        if status in {"completed", "completed_with_variance", "failed", "cancelled"}
                        else None,
                    },
                    **identity,
                    "source": "canonical_telemetry_lifecycle_projector",
                    "source_modes": source_modes,
                    "accepted_live": accepted_live,
                    "projection_mode": "live" if accepted_live else "+".join(source_modes),
                    "canonical_event_count": len(lifecycle),
                    "fill_event_count": sum(
                        event_type
                        in {"paper_fill_simulated", "fill_received", "order_partially_filled", "order_filled"}
                        for event_type in event_types
                    ),
                    "position_event_count": sum("position_snapshot" in event_type for event_type in event_types),
                    "reconciliation_event_count": sum(event_type.startswith("reconciliation_") for event_type in event_types),
                    "last_canonical_event_id": lifecycle[-1]["event"]["event_id"],
                    "last_source_offset": lifecycle[-1].get("ingested_seq"),
                    "last_projected_at": controller.get("last_projection_success_at"),
                    "controller_id": controller.get("controller_id"),
                    "controller_generation": controller.get("generation"),
                    "deployment_sha": controller.get("deployment_sha"),
                }
            )
        return records


def _stage_status(value: Any) -> str:
    token = str(value or "unknown").strip().lower()
    if token in {"ok", "accepted", "submitted", "filled", "resolved", "complete", "completed", "succeeded"}:
        return "succeeded"
    if token in {"partial", "partially_filled", "partially_succeeded"}:
        return "partially_succeeded"
    if token in {"rejected", "failed", "error"}:
        return "failed"
    if token in {"cancelled", "canceled"}:
        return "cancelled"
    if token in {"noop", "no_order", "not_submitted", "skipped"}:
        return "skipped"
    return token


class PostgresLifecycleSource:
    """Read committed telemetry rows and receive transaction-scoped wakeups."""

    def __init__(self, dsn: str, *, channel: str = DEFAULT_CHANNEL) -> None:
        self.dsn = dsn
        self.channel = channel
        self._listener: Any = None
        self._wake = asyncio.Event()

    async def ensure_schema(self) -> None:
        import asyncpg  # type: ignore[import]

        conn = await asyncpg.connect(self.dsn)
        try:
            await conn.execute(
                """
                CREATE SEQUENCE IF NOT EXISTS telemetry_events_ingested_seq_seq AS BIGINT;
                ALTER TABLE telemetry_events ADD COLUMN IF NOT EXISTS ingested_seq BIGINT;
                ALTER TABLE telemetry_events ADD COLUMN IF NOT EXISTS
                    ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp();
                ALTER TABLE telemetry_events ALTER COLUMN ingested_seq
                    SET DEFAULT nextval('telemetry_events_ingested_seq_seq');
                UPDATE telemetry_events
                    SET ingested_seq = nextval('telemetry_events_ingested_seq_seq')
                    WHERE ingested_seq IS NULL;
                ALTER TABLE telemetry_events ALTER COLUMN ingested_seq SET NOT NULL;
                ALTER SEQUENCE telemetry_events_ingested_seq_seq
                    OWNED BY telemetry_events.ingested_seq;
                CREATE UNIQUE INDEX IF NOT EXISTS idx_telemetry_events_ingested_seq
                    ON telemetry_events (ingested_seq);
                CREATE INDEX IF NOT EXISTS idx_telemetry_events_ingested_at
                    ON telemetry_events (ingested_at DESC);
                """
            )
        finally:
            await conn.close()

    async def high_watermark(self) -> int:
        import asyncpg  # type: ignore[import]

        conn = await asyncpg.connect(self.dsn)
        try:
            return int(await conn.fetchval("SELECT COALESCE(MAX(ingested_seq), 0) FROM telemetry_events") or 0)
        finally:
            await conn.close()

    async def fetch_after(self, checkpoint: int, *, limit: int) -> list[dict[str, Any]]:
        import asyncpg  # type: ignore[import]

        conn = await asyncpg.connect(self.dsn)
        try:
            rows = await conn.fetch(
                "SELECT ingested_seq, ingested_at, event_id, event_type, created_at, payload "
                "FROM telemetry_events WHERE ingested_seq > $1 "
                "ORDER BY ingested_seq ASC LIMIT $2",
                int(checkpoint),
                int(limit),
            )
        finally:
            await conn.close()
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            result.append(
                {
                    "ingested_seq": int(row["ingested_seq"]),
                    "ingested_at": row["ingested_at"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "created_at": row["created_at"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "payload": dict(payload),
                }
            )
        return result

    async def start_listener(self) -> None:
        import asyncpg  # type: ignore[import]

        if self._listener is not None:
            return
        self._listener = await asyncpg.connect(self.dsn)

        def _notified(*_: Any) -> None:
            self._wake.set()

        await self._listener.add_listener(self.channel, _notified)

    async def wait(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=max(0.05, timeout))
        except asyncio.TimeoutError:
            pass
        self._wake.clear()

    async def close(self) -> None:
        if self._listener is not None:
            await self._listener.close()
            self._listener = None


async def run_worker() -> int:
    dsn = os.getenv("TELEMETRY_DB_DSN", "").strip()
    if not dsn:
        raise RuntimeError("TELEMETRY_DB_DSN is required")
    root = Path(os.getenv("LIFECYCLE_PROJECTION_ROOT", str(DEFAULT_ROOT)))
    state_path = Path(os.getenv("LIFECYCLE_PROJECTOR_STATE_PATH", str(root / "controller_state.json")))
    projector = LifecycleProjector(
        state_path=state_path,
        bundle_root=root,
        deployment_sha=os.getenv("GIT_SHA", "unknown"),
    )
    source = PostgresLifecycleSource(dsn)
    interval = max(0.1, float(os.getenv("LIFECYCLE_PROJECTOR_POLL_SECONDS", "1")))
    batch_size = max(1, int(os.getenv("LIFECYCLE_PROJECTOR_BATCH_SIZE", "500")))
    max_ticks = max(0, int(os.getenv("LIFECYCLE_PROJECTOR_MAX_TICKS", "0")))
    tick = 0
    recovery_target = 0
    try:
        await source.ensure_schema()
        recovery_target = await source.high_watermark()
        await source.start_listener()
        while True:
            tick += 1
            try:
                high = await source.high_watermark()
                rows = await source.fetch_after(projector.checkpoint, limit=batch_size)
                mode = "recovery" if projector.checkpoint < recovery_target else "live"
                if rows:
                    projector.project_records(rows, mode=mode, source_high_watermark=high)
                else:
                    projector.record_poll(
                        source_high_watermark=high,
                        backlog=max(0, high - projector.checkpoint),
                        mode=mode,
                    )
                if projector.checkpoint >= recovery_target:
                    recovery_target = projector.checkpoint
            except Exception as exc:  # noqa: BLE001 - durable controller records failure
                projector.record_source_failure(f"{type(exc).__name__}: {exc}")
            if max_ticks and tick >= max_ticks:
                return 0
            await source.wait(interval)
    finally:
        await source.close()


def healthcheck() -> int:
    state_path = Path(
        os.getenv(
            "LIFECYCLE_PROJECTOR_STATE_PATH",
            str(Path(os.getenv("LIFECYCLE_PROJECTION_ROOT", str(DEFAULT_ROOT))) / "controller_state.json"),
        )
    )
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        controller = payload["controller"]
        last_poll = _parse_iso(controller["last_poll_at"])
        max_age = float(os.getenv("LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS", "30"))
        age = (datetime.now(timezone.utc) - last_poll).total_seconds()
        if age > max_age:
            raise RuntimeError(f"last poll is stale ({age:.3f}s)")
        if controller.get("last_error"):
            raise RuntimeError(str(controller["last_error"]))
        if int(controller.get("backlog") or 0) > int(
            os.getenv("LIFECYCLE_PROJECTOR_HEALTH_MAX_BACKLOG", "5000")
        ):
            raise RuntimeError("projector backlog exceeds health policy")
        if controller.get("status") not in {"ready", "recovering", "repair_only"}:
            raise RuntimeError(f"invalid controller status: {controller.get('status')}")
    except Exception as exc:  # noqa: BLE001
        print(f"lifecycle projector unhealthy: {exc}")
        return 1
    print(
        _canonical_json(
            {
                "status": controller.get("status"),
                "mode": controller.get("mode"),
                "checkpoint": controller.get("checkpoint"),
                "backlog": controller.get("backlog"),
                "accepted_live": controller.get("accepted_live"),
                "deployment_sha": controller.get("deployment_sha"),
            }
        )
    )
    return 0


def _backfill(input_path: Path, *, mode: str) -> int:
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    records = raw.get("records") if isinstance(raw, Mapping) else raw
    if not isinstance(records, list):
        raise ValueError("backfill input must be a list or {'records': [...]} object")
    root = Path(os.getenv("LIFECYCLE_PROJECTION_ROOT", str(DEFAULT_ROOT)))
    projector = LifecycleProjector(
        state_path=os.getenv("LIFECYCLE_PROJECTOR_STATE_PATH", str(root / "controller_state.json")),
        bundle_root=root,
        deployment_sha=os.getenv("GIT_SHA", "unknown"),
    )
    result = projector.project_records(records, mode=mode)
    print(_canonical_json(result.__dict__))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run")
    subparsers.add_parser("healthcheck")
    for command in ("backfill", "replay"):
        child = subparsers.add_parser(command)
        child.add_argument("--input", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "run":
        return asyncio.run(run_worker())
    if args.command == "healthcheck":
        return healthcheck()
    return _backfill(args.input, mode=args.command)


if __name__ == "__main__":
    raise SystemExit(main())
