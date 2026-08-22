"""Durable canonical telemetry -> Trade Journey and loop-run projector.

The projector consumes committed ``telemetry_events`` rows by the monotonic
``ingested_seq`` assigned by Postgres.  It owns one atomic read-model bundle:

* ``trade_journey_events.json`` -- immutable derived stage events;
* ``loop_runs.json`` -- one loop run for the same lifecycle identity; and
* ``controller_state.json`` -- durable checkpoint and live/repair watermarks.
* ``health_state.json`` -- bounded readiness metadata atomically advanced with
  the active generation.

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
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Any, Callable, Iterable, Mapping, Sequence
import uuid

from services.trade_journey.correlation_envelope import (
    CorrelationEnvelopeError,
    validate_envelope,
)
from services.trade_journey.incremental_materializer import IncrementalLifecycleMaterializer
from services.trade_journey.materializer import (
    IDENTIFIER_FIELDS,
    TERMINAL_STATUSES,
    JourneyMaterializer,
)
from services.trade_journey.projection_store import (
    BatchProjectionMutation,
    EventReceiptRow,
    IdentityLinkRow,
    JourneyRow,
    JourneyStageRow,
    LoopRunRow,
    ProjectionStore,
    QuarantineRow,
)


STATE_SCHEMA = "pantheon.lifecycle-projector-state.v1"
JOURNEY_STORE_SCHEMA = "pantheon.trade-journey-projection.v1"
LOOP_STORE_SCHEMA = "pantheon.loop-run-projection.v1"
PROJECTION_MODES = frozenset({"live", "recovery", "backfill", "replay"})

DEFAULT_ROOT = Path("/data/bff/lifecycle-projection")
DEFAULT_STATE_PATH = DEFAULT_ROOT / "controller_state.json"
DEFAULT_HEALTH_STATE_PATH = DEFAULT_ROOT / "health_state.json"
DEFAULT_CHANNEL = "pantheon_lifecycle_events"
DEFAULT_GENERATION_RETENTION = 32
DEFAULT_STAGING_MAX_AGE_SECONDS = 3600.0
DEFAULT_HEALTH_MAX_AGE_SECONDS = 120.0
DEFAULT_HEALTH_MAX_BACKLOG = 5000
DEFAULT_HEALTH_MIN_FREE_BYTES = 128 * 1024 * 1024
DEFAULT_HEALTH_MIN_FREE_PERCENT = 5.0
DEFAULT_SOURCE_STARTUP_TIMEOUT_SECONDS = 10.0
RELATIONAL_WRITER_BACKEND_ENV = "LIFECYCLE_PROJECTOR_WRITER_BACKEND"
RELATIONAL_WRITER_DSN_ENV = "LIFECYCLE_PROJECTOR_PROJECTION_DSN"
RELATIONAL_WRITER_SCHEMA_ENV = "LIFECYCLE_PROJECTOR_PROJECTION_SCHEMA"
RELATIONAL_WRITER_BACKEND_SHADOW = "shadow"
RELATIONAL_WRITER_DEFAULT_SCHEMA = "trade_journey_projection"
RELATIONAL_CONTROLLER_ID = "canonical-lifecycle-projector"
RELATIONAL_CONTROLLER_TENANT_SCOPE = "*"
RELATIONAL_CONTROLLER_ENVIRONMENT_SCOPE = "*"

LIFECYCLE_EVENT_TYPES = frozenset(
    {
        "trade_journey_fixture",
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
LIFECYCLE_EVENT_TYPE_QUERY = tuple(sorted(LIFECYCLE_EVENT_TYPES))

FIXTURE_EVENT_TYPE = "trade_journey_fixture"
FIXTURE_SCHEMA_VERSION = "pantheon.trade-journey-fixture.v1"
FIXTURE_SOURCE = "tj_e2e_012_hosted_seed_v3"
FIXTURE_TENANT_ID = "tenant-dev"
FIXTURE_STAGES = frozenset(
    {
        "signal_generation",
        "trade_decision",
        "promotion_decision",
        "risk_evaluation",
        "order_submission",
        "broker_acknowledgement",
        "fill_management",
        "ledger_booking",
        "reconciliation",
    }
)
FIXTURE_PASSTHROUGH_FIELDS = frozenset(
    {
        "account_id",
        "artifact_version",
        "binding_version",
        "broker_order_id",
        "broker_trade_id",
        "capital_account_id",
        "causation_id",
        "client_order_id",
        "decision_id",
        "due_at",
        "event_type",
        "evidence_refs",
        "failing_check",
        "fill_id",
        "filled_quantity",
        "graph_edges",
        "human_inbox_ref",
        "incident_id",
        "input_refs",
        "next_action",
        "order_id",
        "order_state",
        "owner_role",
        "parent_order_id",
        "persona_id",
        "persona_version",
        "policy_refs",
        "policy_version",
        "price",
        "quantity",
        "reason_code",
        "recorded_at",
        "replaced_order_id",
        "remaining_quantity",
        "remediation_ref",
        "research_journey_id",
        "return_url",
        "signal_id",
        "source_ref",
        "source_status",
        "source_unavailable",
        "strategy_id",
        "strategy_lifecycle_id",
        "strategy_version",
        "summary",
        "unavailable_sources",
        "unfilled_quantity",
        "variance",
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


def _prepare_atomic_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        return tmp
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _commit_prepared_json(path: Path, tmp: Path) -> None:
    os.replace(tmp, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    tmp = _prepare_atomic_json(path, payload)
    try:
        _commit_prepared_json(path, tmp)
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
        generation_retention: int | None = None,
        staging_max_age_seconds: float | None = None,
        epoch_clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = Path(root)
        self.generations = self.root / "generations"
        self.current = self.root / "current"
        self._before_switch = before_switch
        configured_retention = (
            generation_retention
            if generation_retention is not None
            else int(
                os.getenv(
                    "LIFECYCLE_PROJECTOR_GENERATION_RETENTION",
                    str(DEFAULT_GENERATION_RETENTION),
                )
            )
        )
        configured_staging_age = (
            staging_max_age_seconds
            if staging_max_age_seconds is not None
            else float(
                os.getenv(
                    "LIFECYCLE_PROJECTOR_STAGING_MAX_AGE_SECONDS",
                    str(DEFAULT_STAGING_MAX_AGE_SECONDS),
                )
            )
        )
        self.generation_retention = max(1, int(configured_retention))
        self.staging_max_age_seconds = max(0.0, float(configured_staging_age))
        self._epoch_clock = epoch_clock

    @staticmethod
    def _generation_number(path: Path) -> int | None:
        name = path.name
        if not name.startswith("g") or "-" not in name:
            return None
        raw_generation, suffix = name[1:].split("-", 1)
        if (
            len(raw_generation) != 12
            or not raw_generation.isdigit()
            or len(suffix) != 12
            or any(
                character not in "0123456789abcdef"
                for character in suffix.lower()
            )
        ):
            return None
        return int(raw_generation)

    def _active_generation_name(self) -> str | None:
        if not self.current.is_symlink():
            return None
        try:
            target = (self.root / os.readlink(self.current)).resolve(strict=False)
            generations_root = self.generations.resolve(strict=False)
            relative = target.relative_to(generations_root)
        except (OSError, ValueError):
            return None
        if len(relative.parts) != 1:
            return None
        return relative.name

    def _generation_directories(self) -> list[Path]:
        if not self.generations.is_dir():
            return []
        return [
            path
            for path in self.generations.iterdir()
            if path.is_dir()
            and not path.is_symlink()
            and self._generation_number(path) is not None
        ]

    def _staging_candidates(self) -> list[Path]:
        if not self.generations.is_dir():
            return []
        now = self._epoch_clock()
        candidates: list[Path] = []
        for path in self.generations.iterdir():
            if not path.name.startswith(".g") or not path.name.endswith(".tmp"):
                continue
            if self._generation_number(Path(path.name[1:-4])) is None:
                continue
            try:
                age = now - path.lstat().st_mtime
            except OSError:
                continue
            if age >= self.staging_max_age_seconds:
                candidates.append(path)
        return sorted(candidates, key=lambda path: path.name)

    def _temporary_link_candidates(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        now = self._epoch_clock()
        candidates: list[Path] = []
        for path in self.root.iterdir():
            if not path.name.startswith(".current.") or not path.name.endswith(".tmp"):
                continue
            suffix = path.name[len(".current.") : -len(".tmp")]
            if len(suffix) != 32 or any(
                character not in "0123456789abcdef" for character in suffix.lower()
            ):
                continue
            try:
                age = now - path.lstat().st_mtime
            except OSError:
                continue
            if age >= self.staging_max_age_seconds:
                candidates.append(path)
        return sorted(candidates, key=lambda path: path.name)

    def _retention_removals(self, *, max_count: int) -> list[Path]:
        generations = sorted(
            self._generation_directories(),
            key=lambda path: (self._generation_number(path) or -1, path.name),
            reverse=True,
        )
        active_name = self._active_generation_name()
        retained: set[str] = set()
        if active_name is not None and any(path.name == active_name for path in generations):
            retained.add(active_name)
        for path in generations:
            if len(retained) >= max(1, max_count):
                break
            retained.add(path.name)
        return [path for path in generations if path.name not in retained]

    def maintain(self, *, reserve_for_publish: bool = False) -> dict[str, Any]:
        """Bound debris while preserving the generation referenced by ``current``."""

        self.generations.mkdir(parents=True, exist_ok=True)
        staging = self._staging_candidates()
        temporary_links = self._temporary_link_candidates()
        max_count = (
            max(1, self.generation_retention - 1)
            if reserve_for_publish
            else self.generation_retention
        )
        generation_removals = self._retention_removals(max_count=max_count)
        report = {
            "active_generation": self._active_generation_name(),
            "generation_retention": self.generation_retention,
            "reserve_for_publish": reserve_for_publish,
            "abandoned_staging_count": len(staging),
            "abandoned_staging": [path.name for path in staging[:100]],
            "temporary_link_count": len(temporary_links),
            "temporary_links": [path.name for path in temporary_links[:100]],
            "generation_removal_count": len(generation_removals),
            "oldest_generation_removed": (
                generation_removals[-1].name if generation_removals else None
            ),
            "newest_generation_removed": (
                generation_removals[0].name if generation_removals else None
            ),
        }
        if staging or temporary_links or generation_removals:
            print(
                f"lifecycle projector cleanup plan: {_canonical_json(report)}",
                flush=True,
            )
        for path in staging:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        for path in temporary_links:
            path.unlink()
        for path in generation_removals:
            shutil.rmtree(path)
        return report

    def publish(
        self,
        generation: int,
        journey_payload: Mapping[str, Any],
        loop_payload: Mapping[str, Any],
    ) -> Path:
        self.maintain(reserve_for_publish=True)
        generation_name = f"g{generation:012d}-{uuid.uuid4().hex[:12]}"
        staging = self.generations / f".{generation_name}.tmp"
        final = self.generations / generation_name
        tmp_link: Path | None = None
        staging.mkdir()
        try:
            _atomic_write_json(staging / "trade_journey_events.json", journey_payload)
            _atomic_write_json(staging / "loop_runs.json", loop_payload)
            manifest = {
                "schema_version": "pantheon.lifecycle-projection-bundle.v1",
                "generation": generation,
                "journey_sha256": _fingerprint(journey_payload),
                "loop_runs_sha256": _fingerprint(loop_payload),
            }
            _atomic_write_json(staging / "manifest.json", manifest)
            os.replace(staging, final)
            if self._before_switch is not None:
                self._before_switch(final)
            tmp_link = self.root / f".current.{uuid.uuid4().hex}.tmp"
            os.symlink(str(Path("generations") / generation_name), tmp_link)
            os.replace(tmp_link, self.current)
            directory_fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return final
        except BaseException:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if tmp_link is not None:
                try:
                    tmp_link.unlink()
                except OSError:
                    pass
            raise


def projector_readiness(
    *,
    state_path: str | Path = DEFAULT_HEALTH_STATE_PATH,
    bundle_root: str | Path | None = None,
    max_age_seconds: float | None = None,
    max_backlog: int | None = None,
    min_free_bytes: int | None = None,
    min_free_percent: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return fail-closed worker, projection, freshness, and disk truth."""

    resolved_state_path = Path(state_path)
    resolved_root = Path(bundle_root) if bundle_root is not None else resolved_state_path.parent
    configured_max_age = (
        float(max_age_seconds)
        if max_age_seconds is not None
        else float(
            os.getenv(
                "LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS",
                str(DEFAULT_HEALTH_MAX_AGE_SECONDS),
            )
        )
    )
    configured_max_backlog = (
        int(max_backlog)
        if max_backlog is not None
        else int(
            os.getenv(
                "LIFECYCLE_PROJECTOR_HEALTH_MAX_BACKLOG",
                str(DEFAULT_HEALTH_MAX_BACKLOG),
            )
        )
    )
    configured_min_free_bytes = (
        int(min_free_bytes)
        if min_free_bytes is not None
        else int(
            os.getenv(
                "LIFECYCLE_PROJECTOR_HEALTH_MIN_FREE_BYTES",
                str(DEFAULT_HEALTH_MIN_FREE_BYTES),
            )
        )
    )
    configured_min_free_percent = (
        float(min_free_percent)
        if min_free_percent is not None
        else float(
            os.getenv(
                "LIFECYCLE_PROJECTOR_HEALTH_MIN_FREE_PERCENT",
                str(DEFAULT_HEALTH_MIN_FREE_PERCENT),
            )
        )
    )
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)

    base_payload: dict[str, Any] = {
        "state_path": str(resolved_state_path),
        "bundle_root": str(resolved_root),
        "worker_status": "unavailable",
        "controller_status": "unavailable",
        "mode": None,
        "accepted_live": False,
        "checkpoint": None,
        "source_high_watermark": None,
        "backlog": None,
        "current_generation": None,
        "controller_generation": None,
        "last_poll_at": None,
        "last_successful_publish_at": None,
        "deployment_sha": None,
        "freshness": {
            "age_seconds": None,
            "max_age_seconds": configured_max_age,
            "stale": True,
        },
        "disk": {
            "free_bytes": None,
            "free_percent": None,
            "min_free_bytes": configured_min_free_bytes,
            "min_free_percent": configured_min_free_percent,
            "low": True,
        },
        "retention": {
            "generation_limit": max(
                1,
                int(
                    os.getenv(
                        "LIFECYCLE_PROJECTOR_GENERATION_RETENTION",
                        str(DEFAULT_GENERATION_RETENTION),
                    )
                ),
            ),
            "staging_max_age_seconds": max(
                0.0,
                float(
                    os.getenv(
                        "LIFECYCLE_PROJECTOR_STAGING_MAX_AGE_SECONDS",
                        str(DEFAULT_STAGING_MAX_AGE_SECONDS),
                    )
                ),
            ),
        },
        "stale_reason": "projector_state_unavailable",
        "error_reason": "projector_state_unavailable",
        "reasons": ["projector_state_unavailable"],
        "status": "unavailable",
        "ready": False,
    }
    try:
        payload = json.loads(resolved_state_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping) or payload.get("schema_version") != STATE_SCHEMA:
            raise LifecycleProjectionError("unsupported projector state schema")
        controller = payload.get("controller")
        if not isinstance(controller, Mapping):
            raise LifecycleProjectionError("projector controller state is missing")
    except Exception as exc:  # noqa: BLE001 - readiness must fail closed
        reason = f"projector_state_unavailable: {type(exc).__name__}: {exc}"
        base_payload.update(
            {
                "stale_reason": reason,
                "error_reason": reason,
                "reasons": [reason],
            }
        )
        return base_payload

    reasons: list[str] = []
    last_poll_at = controller.get("last_poll_at")
    age_seconds: float | None = None
    try:
        age_seconds = max(0.0, (observed_at - _parse_iso(last_poll_at)).total_seconds())
        if age_seconds > configured_max_age:
            reasons.append(f"last_poll_stale:{age_seconds:.3f}s")
    except Exception as exc:  # noqa: BLE001 - malformed freshness is unavailable truth
        reasons.append(f"last_poll_invalid:{type(exc).__name__}")

    controller_status = str(controller.get("status") or "unavailable")
    mode = str(controller.get("mode") or "unknown")
    accepted_live = bool(controller.get("accepted_live"))
    backlog = int(controller.get("backlog") or 0)
    controller_generation = int(
        controller.get("generation")
        if controller.get("generation") is not None
        else payload.get("generation") or 0
    )
    if controller.get("last_error"):
        reasons.append(f"last_error:{controller.get('last_error')}")
    if backlog > configured_max_backlog:
        reasons.append(f"backlog_exceeds_policy:{backlog}>{configured_max_backlog}")
    if controller_status != "ready":
        reasons.append(f"controller_not_ready:{controller_status}")
    if mode != "live" or not accepted_live:
        reasons.append(f"live_truth_not_accepted:{mode}:{str(accepted_live).lower()}")

    current_generation: int | None = None
    current = resolved_root / "current"
    generations_root = resolved_root / "generations"
    try:
        if not current.is_symlink():
            raise LifecycleProjectionError("current is not a symlink")
        current_target = current.resolve(strict=True)
        relative = current_target.relative_to(generations_root.resolve(strict=True))
        if len(relative.parts) != 1:
            raise LifecycleProjectionError("current points outside the generation root")
        manifest = json.loads((current_target / "manifest.json").read_text(encoding="utf-8"))
        current_generation = int(manifest["generation"])
        if current_generation != controller_generation:
            reasons.append(
                f"current_generation_mismatch:{current_generation}!={controller_generation}"
            )
    except Exception as exc:  # noqa: BLE001 - a broken active bundle is not ready
        reasons.append(f"current_generation_unavailable:{type(exc).__name__}:{exc}")

    disk_path = resolved_root if resolved_root.exists() else resolved_root.parent
    free_bytes: int | None = None
    free_percent: float | None = None
    disk_low = True
    try:
        disk = shutil.disk_usage(disk_path)
        free_bytes = int(disk.free)
        free_percent = (float(disk.free) / float(disk.total) * 100.0) if disk.total else 0.0
        disk_low = (
            free_bytes < configured_min_free_bytes
            or free_percent < configured_min_free_percent
        )
        if disk_low:
            reasons.append(
                "disk_below_policy:"
                f"{free_bytes}B/{free_percent:.3f}%"
            )
    except OSError as exc:
        reasons.append(f"disk_unavailable:{type(exc).__name__}:{exc}")

    stale_reason = next(
        (
            reason
            for reason in reasons
            if reason.startswith("last_poll_") or reason.startswith("projector_state_")
        ),
        None,
    )
    worker_status = (
        "stale"
        if stale_reason is not None
        else "error"
        if controller.get("last_error")
        else controller_status
    )
    base_payload.update(
        {
            "worker_status": worker_status,
            "controller_status": controller_status,
            "mode": mode,
            "accepted_live": accepted_live,
            "checkpoint": int(payload.get("checkpoint") or 0),
            "source_high_watermark": int(controller.get("source_high_watermark") or 0),
            "backlog": backlog,
            "current_generation": current_generation,
            "controller_generation": controller_generation,
            "last_poll_at": last_poll_at,
            "last_successful_publish_at": (
                controller.get("last_successful_publish_at")
                or controller.get("last_projection_success_at")
            ),
            "deployment_sha": controller.get("deployment_sha"),
            "freshness": {
                "age_seconds": age_seconds,
                "max_age_seconds": configured_max_age,
                "stale": stale_reason is not None,
            },
            "disk": {
                "free_bytes": free_bytes,
                "free_percent": free_percent,
                "min_free_bytes": configured_min_free_bytes,
                "min_free_percent": configured_min_free_percent,
                "low": disk_low,
            },
            "stale_reason": stale_reason,
            "error_reason": reasons[0] if reasons else None,
            "reasons": reasons,
            "status": "ok" if not reasons else "degraded",
            "ready": not reasons,
        }
    )
    return base_payload


class LifecycleProjector:
    """Deterministic durable projector over committed telemetry append rows."""

    def __init__(
        self,
        *,
        state_path: str | Path = DEFAULT_STATE_PATH,
        health_state_path: str | Path | None = None,
        bundle_root: str | Path = DEFAULT_ROOT,
        deployment_sha: str = "unknown",
        clock: Callable[[], str] = _utc_now,
        publisher: AtomicProjectionBundle | None = None,
    ) -> None:
        self.state_path = Path(state_path)
        self.health_state_path = Path(
            health_state_path
            if health_state_path is not None
            else Path(bundle_root) / "health_state.json"
        )
        self.bundle = publisher or AtomicProjectionBundle(bundle_root)
        self.deployment_sha = str(deployment_sha or "unknown")
        self.clock = clock
        self.state = self._load_state()
        self.state["restart_count"] = int(self.state.get("restart_count", 0)) + 1
        # The folded aggregates live in memory for the process lifetime.  They
        # are rebuilt from disk exactly once, at startup, so no poll pays the
        # cost of re-reading the whole read model.
        self._materializer = IncrementalLifecycleMaterializer(
            self.state, journey_events_fn=self._journey_events
        )
        self._serialized_aggregates = self._materializer.serialize_aggregates()
        self.state["aggregates"] = self._serialized_aggregates
        self.state.pop("canonical_events", None)

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
            "aggregates": {},
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
                "last_successful_publish_at": None,
                "last_successful_publish_generation": None,
                "last_live_success_at": None,
                "last_live_event_at": None,
                "last_recovery_at": None,
                "last_backfill_at": None,
                "last_replay_at": None,
                "last_failure_at": None,
                "last_error": None,
                "error_publication_failure": None,
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

    @staticmethod
    def _health_snapshot(state: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA,
            "checkpoint": int(state.get("checkpoint") or 0),
            "generation": int(state.get("generation") or 0),
            "restart_count": int(state.get("restart_count") or 0),
            "controller": copy.deepcopy(state.get("controller") or {}),
        }

    def _publish_candidate(
        self,
        candidate: Mapping[str, Any],
        journey_payload: Mapping[str, Any],
        loop_payload: Mapping[str, Any],
    ) -> None:
        """Pre-fsync state, then commit bundle/state/health with short switch gaps."""

        prepared_state: Path | None = _prepare_atomic_json(self.state_path, candidate)
        prepared_health: Path | None = _prepare_atomic_json(
            self.health_state_path,
            self._health_snapshot(candidate),
        )
        try:
            self.bundle.publish(
                int(candidate.get("generation") or 0),
                journey_payload,
                loop_payload,
            )
            _commit_prepared_json(self.state_path, prepared_state)
            prepared_state = None
            _commit_prepared_json(self.health_state_path, prepared_health)
            prepared_health = None
        finally:
            for prepared in (prepared_state, prepared_health):
                if prepared is not None:
                    try:
                        prepared.unlink()
                    except OSError:
                        pass

    def _persist_health_only(self, candidate: Mapping[str, Any]) -> None:
        _atomic_write_json(
            self.health_state_path,
            self._health_snapshot(candidate),
        )

    def _bounded_state_copy(self) -> dict[str, Any]:
        """Copy the mutable non-aggregate state without duplicating event data.

        Only fixed-size scalars, the controller dict, the bounded quarantine
        ring and a shallow key copy of ``identity_chains`` are duplicated;
        ``_admit_identity`` replaces chain entries rather than mutating them, so
        the shallow copy is safe.  Aggregates are staged separately, which is
        why no poll deep-copies the read model.
        """
        candidate = dict(self.state)
        candidate["controller"] = copy.deepcopy(self.state.get("controller") or {})
        candidate["quarantine"] = list(self.state.get("quarantine") or [])
        candidate["identity_chains"] = dict(self.state.get("identity_chains") or {})
        return candidate

    def project_records(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        mode: str,
        source_high_watermark: int | None = None,
    ) -> ProjectionResult:
        if mode not in PROJECTION_MODES:
            raise ValueError(f"unsupported projection mode: {mode}")
        candidate = self._bounded_state_copy()
        duplicates = ignored = quarantined = 0
        max_seen = int(candidate.get("checkpoint", 0))
        now = self.clock()

        inc_mat = self._materializer
        inc_mat.reset_stats()

        ordered_records = sorted(
            (dict(record) for record in records),
            key=lambda row: (int(row.get("ingested_seq") or 0), str(row.get("event_id") or "")),
        )
        batch_entries: list[dict[str, Any]] = []
        batch_fingerprints: dict[str, str] = {}
        promotions: dict[str, set[str]] = {}
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

            # Idempotency is resolved against committed aggregate state *and*
            # against the entries already accepted from this same batch, so an
            # intra-batch duplicate is never double-counted and an intra-batch
            # conflict fails closed before the controller is touched at all.
            try:
                journey_id = self._identity(event).get("journey_id")
            except InvalidLifecycleEvent:
                journey_id = None
            known_fingerprint = None
            if journey_id is not None:
                aggregate = inc_mat.aggregates.get(journey_id)
                if aggregate is not None:
                    known_fingerprint = aggregate.event_fingerprints.get(event_id)
            if known_fingerprint is None:
                known_fingerprint = batch_fingerprints.get(event_id)
            if known_fingerprint is not None:
                if known_fingerprint != fingerprint:
                    raise ConflictingLifecycleEvent(
                        f"conflicting canonical event_id: {event_id}"
                    )
                duplicates += 1
                if mode == "live" and journey_id is not None:
                    # A recovery/backfill event re-delivered live promotes the
                    # stored aggregate; the promotion is staged, not applied to
                    # committed state, so a failed publish cannot leak it.
                    promotions.setdefault(journey_id, set()).add(event_id)
                continue

            try:
                self._validate_fixture_event(event)
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

            entry = {
                "fingerprint": fingerprint,
                "event": event,
                "identity": identity,
                "sequence_no": source_sequence,
                "ingested_seq": sequence,
                "ingested_at": str(row.get("ingested_at") or now),
                "source_mode": mode,
                "accepted_live": mode == "live",
            }
            batch_entries.append(entry)
            batch_fingerprints[event_id] = fingerprint

        # Fold the batch into deep copies of only the aggregates it touches.
        # Conflicts raise here, before checkpoint/generation/controller move.
        staged, affected, accepted, staged_duplicates = inc_mat.stage_batch(
            batch_entries,
            promotions=promotions,
            journey_events_fn=self._journey_events,
        )
        duplicates += staged_duplicates

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
                "error_publication_failure": None,
                "quarantine_count": len(candidate.get("quarantine") or []),
                "generation": int(candidate.get("generation", 0)),
                "restart_count": int(candidate.get("restart_count", 0)),
            }
        )
        controller["backlog"] = max(
            0,
            int(controller["source_high_watermark"]) - int(candidate.get("checkpoint", 0)),
        )
        live_admissible = self._live_admissible(controller, mode=mode)
        if live_admissible:
            controller["last_live_success_at"] = now
            if accepted:
                controller["last_live_event_at"] = inc_mat.last_live_event_at(staged)
        elif mode == "recovery":
            controller["last_recovery_at"] = now
        elif mode == "backfill":
            controller["last_backfill_at"] = now
        elif mode == "replay":
            controller["last_replay_at"] = now
        historic_live = bool(controller.get("last_live_success_at"))
        controller["accepted_live"] = live_admissible
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
            if controller["accepted_live"]
            else "recovering"
            if mode == "recovery" or controller["backlog"]
            else "repair_only"
        )

        controller["last_successful_publish_at"] = now
        controller["last_successful_publish_generation"] = int(candidate["generation"])

        # Only the staged aggregates are re-serialized; the rest are carried
        # over by reference from the previous poll.
        candidate["aggregates"] = inc_mat.serialize_aggregates(
            base=self._serialized_aggregates, staged=staged
        )

        journey_payload, loop_payload = inc_mat.render_full_payloads(
            schema_version_journey=JOURNEY_STORE_SCHEMA,
            schema_version_loop=LOOP_STORE_SCHEMA,
            generation=int(candidate["generation"]),
            controller=controller,
            staged=staged,
        )

        self._publish_candidate(candidate, journey_payload, loop_payload)
        inc_mat.commit(staged)
        self._serialized_aggregates = candidate["aggregates"]
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
        candidate = self._bounded_state_copy()
        now = self.clock()
        controller = candidate.setdefault("controller", {})
        semantic_fields = (
            "deployment_sha",
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
                "error_publication_failure": None,
            }
        )
        live_admissible = self._live_admissible(controller, mode=mode)
        historic_live = bool(controller.get("last_live_success_at"))
        if live_admissible:
            # A successful zero-backlog read is the controller's authoritative
            # live admission boundary after startup/recovery, even when no new
            # trade arrived during that poll.
            controller["last_live_success_at"] = now
            controller["accepted_live"] = live_admissible
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
            if controller["accepted_live"]
            else "recovering"
            if mode == "recovery" or int(backlog) > 0
            else "repair_only"
        )
        current_semantics = tuple(controller.get(field) for field in semantic_fields)
        if current_semantics != previous_semantics:
            candidate["generation"] = int(candidate.get("generation", 0)) + 1
            controller["generation"] = int(candidate["generation"])
            controller["last_successful_publish_at"] = now
            controller["last_successful_publish_generation"] = int(candidate["generation"])
            journey_payload, loop_payload = self._materializer.render_full_payloads(
                schema_version_journey=JOURNEY_STORE_SCHEMA,
                schema_version_loop=LOOP_STORE_SCHEMA,
                generation=int(candidate["generation"]),
                controller=controller,
            )
            self._publish_candidate(candidate, journey_payload, loop_payload)
        else:
            # Only freshness changed.  Keep that hot-path bounded instead of
            # serializing the full canonical event history every poll.
            self._persist_health_only(candidate)
        self.state = candidate

    @staticmethod
    def _live_admissible(controller: Mapping[str, Any], *, mode: str) -> bool:
        """Return current live truth, never inferred from historic success.

        Historical success is useful audit evidence, but cannot allow a
        controller with backlog or quarantined records to advertise canonical
        live readiness.
        """
        return (
            mode == "live"
            # ``telemetry_events`` is a retained source window.  The durable
            # checkpoint is a global sequence and may be higher than the
            # newest retained lifecycle record after source truncation; that is
            # not a backlog.  ``backlog`` is the only current window lag.
            and int(controller.get("backlog") or 0) == 0
            and int(controller.get("quarantine_count") or 0) == 0
        )

    def record_source_failure(self, error: str, *, backlog: int | None = None) -> None:
        """Record source failure while preserving the last-good read-model bundle."""
        candidate = self._bounded_state_copy()
        controller = candidate.setdefault("controller", {})
        previous_semantics = (
            controller.get("status"),
            controller.get("last_error"),
            int(controller.get("backlog") or 0),
        )
        now = self.clock()
        controller.update(
            {
                "status": "degraded",
                "last_failure_at": now,
                "last_error": str(error),
                "restart_count": int(candidate.get("restart_count", 0)),
            }
        )
        if backlog is not None:
            controller["backlog"] = max(0, int(backlog))
        current_semantics = (
            controller.get("status"),
            controller.get("last_error"),
            int(controller.get("backlog") or 0),
        )
        if current_semantics != previous_semantics:
            candidate["generation"] = int(candidate.get("generation", 0)) + 1
            controller["generation"] = int(candidate["generation"])
            controller["last_successful_publish_at"] = now
            controller["last_successful_publish_generation"] = int(candidate["generation"])
            journey_payload, loop_payload = self._materializer.render_full_payloads(
                schema_version_journey=JOURNEY_STORE_SCHEMA,
                schema_version_loop=LOOP_STORE_SCHEMA,
                generation=int(candidate["generation"]),
                controller=controller,
            )
            self._publish_candidate(candidate, journey_payload, loop_payload)
        else:
            _atomic_write_json(self.state_path, candidate)
            self._persist_health_only(candidate)
        self.state = candidate

    @staticmethod
    def _validate_fixture_event(event: Mapping[str, Any]) -> None:
        if event.get("event_type") != FIXTURE_EVENT_TYPE:
            return
        if os.getenv("PANTHEON_TJ_E2E_FIXTURE_INGEST_ENABLED", "").lower() != "true":
            raise InvalidLifecycleEvent("dev fixture projection is disabled")
        metadata = event.get("metadata")
        envelope = event.get("correlation_envelope")
        if not isinstance(metadata, Mapping) or not isinstance(envelope, Mapping):
            raise InvalidLifecycleEvent("dev fixture metadata and correlation envelope are required")
        if (
            metadata.get("fixture_schema_version") != FIXTURE_SCHEMA_VERSION
            or metadata.get("fixture_source") != FIXTURE_SOURCE
            or metadata.get("fixture_scope") != "dev-only"
            or envelope.get("tenant_id") != FIXTURE_TENANT_ID
        ):
            raise InvalidLifecycleEvent("dev fixture scope is invalid")
        if metadata.get("fixture_stage") not in FIXTURE_STAGES:
            raise InvalidLifecycleEvent("dev fixture stage is invalid")
        if not str(metadata.get("fixture_stage_status") or "").strip():
            raise InvalidLifecycleEvent("dev fixture stage status is required")
        _parse_iso(metadata.get("fixture_occurred_at"))
        _parse_iso(metadata.get("fixture_recorded_at"))
        if not isinstance(metadata.get("fixture_payload"), Mapping):
            raise InvalidLifecycleEvent("dev fixture payload must be an object")

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
            "tenant_id": _first(envelope.get("tenant_id"), event.get("tenant_id"), metadata.get("tenant_id")),
            "environment": _first(envelope.get("environment"), event.get("deployment_stage"), event.get("environment")),
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
        missing = [field for field in STABLE_IDENTITY_FIELDS if _clean(identity.get(field)) is None]
        if missing:
            raise InvalidLifecycleEvent(
                "canonical lifecycle identity missing: " + ", ".join(missing)
            )
        if str(identity["environment"]) != str(envelope["environment"]):
            raise InvalidLifecycleEvent("environment conflicts with correlation envelope")
        return {field: str(identity[field]) for field in STABLE_IDENTITY_FIELDS}

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

    @classmethod
    def _journey_events(cls, entry: Mapping[str, Any]) -> list[dict[str, Any]]:
        source = entry["event"]
        identity = entry["identity"]
        specs = cls._stage_specs(source)
        metadata = source.get("metadata") if isinstance(source.get("metadata"), Mapping) else {}
        fixture_payload = (
            metadata.get("fixture_payload")
            if source.get("event_type") == FIXTURE_EVENT_TYPE
            and isinstance(metadata.get("fixture_payload"), Mapping)
            else {}
        )
        occurred_at = (
            metadata.get("fixture_occurred_at")
            if source.get("event_type") == FIXTURE_EVENT_TYPE
            else source["created_at"]
        )
        recorded_at = (
            metadata.get("fixture_recorded_at")
            if source.get("event_type") == FIXTURE_EVENT_TYPE
            else entry.get("ingested_at") or source["created_at"]
        )
        result: list[dict[str, Any]] = []
        for ordinal, (stage, status) in enumerate(specs, start=1):
            event = {
                "event_id": f"{source['event_id']}:{stage}",
                "event_type": fixture_payload.get("event_type") or source["event_type"],
                "journey_id": identity["journey_id"],
                "tenant_id": identity["tenant_id"],
                "environment": identity["environment"],
                "occurred_at": occurred_at,
                "recorded_at": recorded_at,
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
            if source.get("event_type") == FIXTURE_EVENT_TYPE:
                for field in FIXTURE_PASSTHROUGH_FIELDS:
                    value = fixture_payload.get(field)
                    if value not in (None, "", [], {}):
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
        if event_type == FIXTURE_EVENT_TYPE:
            return [
                (
                    str(metadata.get("fixture_stage")),
                    str(metadata.get("fixture_stage_status")),
                )
            ]
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


class RelationalLifecycleProjector:
    """Bounded relational lifecycle writer used only in explicit shadow mode.

    Unlike :class:`LifecycleProjector`, this worker does not create, read, or
    advance ``controller_state.json``.  Its durable cursor, receipts,
    quarantines, aggregate summaries, and controller revision all live in the
    projection schema and are committed by one ``ProjectionStore`` transaction.
    A poll holds at most its input rows and the already-materialized stage slice
    for the journeys that batch touches.
    """

    def __init__(
        self,
        store: ProjectionStore,
        *,
        deployment_sha: str = "unknown",
        clock: Callable[[], str] = _utc_now,
        controller_id: str = RELATIONAL_CONTROLLER_ID,
        tenant_scope: str = RELATIONAL_CONTROLLER_TENANT_SCOPE,
        environment_scope: str = RELATIONAL_CONTROLLER_ENVIRONMENT_SCOPE,
    ) -> None:
        self.store = store
        self.deployment_sha = str(deployment_sha or "unknown")
        self.clock = clock
        self.controller_id = str(controller_id)
        self.tenant_scope = str(tenant_scope)
        self.environment_scope = str(environment_scope)
        self._controller_state = self.store.get_controller_state(
            self.controller_id, self.tenant_scope, self.environment_scope
        )
        self._materializer = IncrementalLifecycleMaterializer(
            journey_events_fn=LifecycleProjector._journey_events
        )

    @property
    def checkpoint(self) -> int:
        return int(self._controller().checkpoint_seq)

    @property
    def controller(self) -> dict[str, Any]:
        state = self._controller()
        return {
            "controller_id": state.controller_id,
            "checkpoint": state.checkpoint_seq,
            "source_high_watermark": state.source_high_watermark,
            "backlog": state.backlog_count,
            "generation": state.projection_revision,
            "deployment_sha": state.deployment_sha,
            "mode": state.mode,
            "status": state.status,
            "accepted_live": state.accepted_live,
            "quarantine_count": state.unresolved_quarantine_count,
            "last_error": state.last_error_message or None,
        }

    def _controller(self):
        if self._controller_state is None:
            # A read before the first transaction has no durable row yet.  The
            # source cursor is consequently zero until the first atomically
            # committed receipt/controller mutation creates it.
            from services.trade_journey.projection_store import ControllerStateRow

            return ControllerStateRow(
                controller_id=self.controller_id,
                tenant_scope=self.tenant_scope,
                environment_scope=self.environment_scope,
                checkpoint_seq=0,
                source_high_watermark=0,
                backlog_count=0,
                projection_revision=0,
                deployment_sha=self.deployment_sha,
                mode="recovery",
                status="initializing",
                accepted_live=False,
            )
        return self._controller_state

    @staticmethod
    def _row_event_id(row: Mapping[str, Any]) -> str:
        payload = row.get("payload")
        candidate = row.get("event_id")
        if candidate in (None, "") and isinstance(payload, Mapping):
            candidate = payload.get("event_id")
        value = str(candidate or "").strip()
        if not value:
            raise InvalidLifecycleEvent("source row missing event_id")
        return value

    @staticmethod
    def _row_event_type(row: Mapping[str, Any]) -> str:
        payload = row.get("payload")
        candidate = row.get("event_type")
        if candidate in (None, "") and isinstance(payload, Mapping):
            candidate = payload.get("event_type")
        return str(candidate or "unknown")

    def _row_fingerprint(self, row: Mapping[str, Any], event: Mapping[str, Any] | None) -> str:
        if event is not None:
            return _fingerprint(
                {
                    "event_id": event.get("event_id"),
                    "event_type": event.get("event_type"),
                    "created_at": event.get("created_at"),
                    "payload": event,
                }
            )
        return _fingerprint(
            {
                "event_id": self._row_event_id(row),
                "event_type": self._row_event_type(row),
                "created_at": row.get("created_at"),
                "payload": row.get("payload"),
            }
        )

    def _receipt(
        self,
        *,
        event_id: str,
        ingested_seq: int,
        fingerprint: str,
        source_event_type: str,
        created_at: datetime,
        disposition: str,
        identity: Mapping[str, str] | None = None,
    ) -> EventReceiptRow:
        identity = identity or {}
        return EventReceiptRow(
            event_id=event_id,
            ingested_seq=ingested_seq,
            fingerprint=fingerprint,
            tenant_id=str(identity.get("tenant_id") or ""),
            environment=str(identity.get("environment") or ""),
            journey_id=str(identity.get("journey_id") or ""),
            loop_run_id=str(identity.get("loop_run_id") or ""),
            source_event_type=source_event_type,
            created_at=created_at,
            disposition=disposition,
            projection_revision=0,
        )

    def _hydrate_materializer(
        self, entries: Sequence[Mapping[str, Any]]
    ) -> IncrementalLifecycleMaterializer:
        materializer = IncrementalLifecycleMaterializer(
            journey_events_fn=LifecycleProjector._journey_events
        )
        affected = {
            (
                str(entry["identity"]["tenant_id"]),
                str(entry["identity"]["environment"]),
                str(entry["identity"]["journey_id"]),
            )
            for entry in entries
        }
        affected_keys = tuple(sorted(affected))
        prior_events = self.store.load_journey_stage_events_bulk(affected_keys)
        for tenant_id, environment, journey_id in affected_keys:
            materializer.hydrate_aggregate(
                journey_id,
                prior_events[(tenant_id, environment, journey_id)],
            )
        # Hydration is a bounded read of the current batch's aggregates, not
        # reduction work for this poll.  The reported counters begin at the
        # first staged source entry.
        materializer.reset_stats()
        return materializer

    @staticmethod
    def _identity_links_for_aggregate(agg: Any) -> list[IdentityLinkRow]:
        observations: dict[tuple[str, str], list[tuple[int, datetime]]] = {}
        for event in agg.journey_events:
            occurred_at = _parse_iso(event.get("occurred_at"))
            source_offset = int(event.get("source_offset") or 0)
            if source_offset <= 0:
                continue
            values: dict[str, Any] = {
                "journey_id": event.get("journey_id"),
                "run_id": event.get("run_id"),
                "loop_run_id": event.get("loop_run_id"),
                "signal_id": event.get("signal_id"),
                "strategy_id": event.get("strategy_id"),
                "runtime_id": event.get("runtime_id"),
                "binding_id": event.get("binding_id"),
                "capital_pool_id": event.get("capital_pool_id"),
                "persona_id": event.get("persona_id"),
                "persona_capital_binding_id": event.get("persona_capital_binding_id"),
                "artifact_id": event.get("artifact_id"),
                "artifact_version": event.get("artifact_version"),
                "plan_id": event.get("plan_id"),
                "trace_id": event.get("trace_id"),
            }
            values.update(
                {
                    identifier: event.get(identifier)
                    for identifier in IDENTIFIER_FIELDS
                }
            )
            for identifier_type, value in values.items():
                if isinstance(value, str) and value:
                    observations.setdefault((identifier_type, value), []).append(
                        (source_offset, occurred_at)
                    )
        links: list[IdentityLinkRow] = []
        for (identifier_type, identifier_value), values in sorted(observations.items()):
            links.append(
                IdentityLinkRow(
                    tenant_id=agg.tenant_id,
                    environment=agg.environment,
                    identifier_type=identifier_type,
                    identifier_value=identifier_value,
                    journey_id=agg.journey_id,
                    first_ingested_seq=min(value[0] for value in values),
                    last_ingested_seq=max(value[0] for value in values),
                    first_occurred_at=min(value[1] for value in values),
                    last_occurred_at=max(value[1] for value in values),
                )
            )
        return links

    def _aggregate_mutations(
        self,
        staged: Mapping[str, Any],
        entries: Sequence[Mapping[str, Any]],
        *,
        mode: str,
        accepted_live: bool,
    ) -> tuple[list[IdentityLinkRow], list[JourneyRow], list[JourneyStageRow], list[LoopRunRow]]:
        entries_by_event_id = {
            str(entry["event"]["event_id"]): entry for entry in entries
        }
        identity_links: list[IdentityLinkRow] = []
        journeys: list[JourneyRow] = []
        stages: list[JourneyStageRow] = []
        loop_runs: list[LoopRunRow] = []
        for journey_id, agg in sorted(staged.items()):
            source_event_ids = {
                str(entry["event"]["event_id"])
                for entry in entries
                if str(entry["identity"]["journey_id"]) == journey_id
            }
            if not source_event_ids:
                continue
            identity_links.extend(self._identity_links_for_aggregate(agg))
            reducer = JourneyMaterializer()
            reducer.rebuild(agg.journey_events)
            projection = reducer.get(
                journey_id, tenant_id=agg.tenant_id, environment=agg.environment
            )
            if projection is None:
                continue
            snapshot = projection.snapshot
            journeys.append(
                JourneyRow(
                    tenant_id=agg.tenant_id,
                    environment=agg.environment,
                    journey_id=journey_id,
                    status=str(snapshot.get("status") or "incomplete"),
                    stage_coverage=dict(snapshot.get("stages") or {}),
                    is_terminal=str(snapshot.get("status") or "") in TERMINAL_STATUSES,
                    first_occurred_at=_parse_iso(snapshot["created_at"]),
                    last_occurred_at=_parse_iso(snapshot["updated_at"]),
                    first_ingested_seq=min(
                        int(event.get("source_offset") or 0)
                        for event in agg.journey_events
                        if int(event.get("source_offset") or 0) > 0
                    ),
                    last_ingested_seq=max(
                        int(event.get("source_offset") or 0)
                        for event in agg.journey_events
                    ),
                    current_identity_summary={"identifiers": snapshot.get("identifiers") or {}},
                    evidence_summary={
                        "event_count": len(agg.journey_events),
                        "stage_event_ids": [
                            event.get("event_id") for event in projection.timeline
                        ],
                    },
                    diagnostic_summary={"diagnostics": projection.diagnostics},
                    loop_run_id=str(agg.identity.get("loop_run_id") or ""),
                )
            )
            for stage_event in agg.journey_events:
                canonical_event_id = str(stage_event.get("canonical_event_id") or "")
                if canonical_event_id not in source_event_ids:
                    continue
                entry = entries_by_event_id[canonical_event_id]
                source_sequence = int(stage_event.get("source_sequence_no") or 0)
                stage_sequence = int(stage_event.get("sequence_no") or 0)
                evidence_refs = stage_event.get("evidence_refs")
                stages.append(
                    JourneyStageRow(
                        tenant_id=agg.tenant_id,
                        environment=agg.environment,
                        journey_id=journey_id,
                        source_event_id=canonical_event_id,
                        stage_name=str(stage_event.get("stage") or ""),
                        stage_status=str(stage_event.get("stage_status") or "unknown"),
                        stage_ordinal=max(0, stage_sequence - source_sequence * 100),
                        source_ingested_seq=int(stage_event.get("source_offset") or 0),
                        event_sequence=source_sequence,
                        occurred_at=_parse_iso(stage_event["occurred_at"]),
                        contract_fields=dict(stage_event),
                        evidence_references=(
                            list(evidence_refs)
                            if isinstance(evidence_refs, list)
                            else []
                        ),
                        fingerprint=_fingerprint(
                            {
                                "canonical_fingerprint": entry["fingerprint"],
                                "stage": stage_event.get("stage"),
                            }
                        ),
                    )
                )
            if agg.loop_record:
                loop_record = dict(agg.loop_record)
                freshness = {
                    "mode": mode,
                    "accepted_live": accepted_live,
                    "source_modes": loop_record.get("source_modes") or [],
                    "last_source_offset": loop_record.get("last_source_offset"),
                }
                loop_runs.append(
                    LoopRunRow(
                        tenant_id=agg.tenant_id,
                        environment=agg.environment,
                        loop_run_id=str(loop_record["loop_run_id"]),
                        journey_id=journey_id,
                        status=str(loop_record.get("status") or "active"),
                        lifecycle_summary=loop_record,
                        freshness_lineage=freshness,
                        contract_payload=loop_record,
                    )
                )
        return identity_links, journeys, stages, loop_runs

    def _controller_mutation(
        self,
        *,
        source_high_watermark: int,
        mode: str,
        quarantined: int,
        error_message: str = "",
    ) -> BatchProjectionMutation:
        previous = self._controller()
        optimistic_backlog = max(
            0,
            int(source_high_watermark) - int(previous.checkpoint_seq),
        )
        accepted_live = (
            mode == "live"
            and optimistic_backlog == 0
            and quarantined == 0
            and int(previous.unresolved_quarantine_count) == 0
            and not error_message
        )
        status = (
            "failed"
            if error_message
            else "degraded"
            if quarantined or int(previous.unresolved_quarantine_count) > 0
            else "ready"
            if accepted_live
            else "recovering"
            if mode == "recovery" or optimistic_backlog
            else "repair_only"
        )
        return BatchProjectionMutation(
            source_high_watermark=max(0, int(source_high_watermark)),
            backlog_count=optimistic_backlog,
            mode=mode,
            status=status,
            accepted_live=accepted_live,
            deployment_sha=self.deployment_sha,
            error_message=error_message,
        )

    def project_records(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        mode: str,
        source_high_watermark: int | None = None,
    ) -> ProjectionResult:
        if mode not in PROJECTION_MODES:
            raise ValueError(f"unsupported projection mode: {mode}")
        ordered_records = sorted(
            (dict(record) for record in records),
            key=lambda row: (int(row.get("ingested_seq") or 0), self._row_event_id(row)),
        )
        source_high = max(
            int(source_high_watermark or 0),
            int(self._controller().source_high_watermark),
        )
        known_receipts = self.store.get_receipts(
            [self._row_event_id(row) for row in ordered_records]
        )
        receipts: list[EventReceiptRow] = []
        quarantines: list[QuarantineRow] = []
        accepted_entries: list[dict[str, Any]] = []
        batch_fingerprints: dict[str, str] = {}
        duplicates = ignored = quarantined = 0

        for row in ordered_records:
            sequence = int(row.get("ingested_seq") or 0)
            if sequence <= 0:
                raise InvalidLifecycleEvent("committed source row requires positive ingested_seq")
            source_high = max(source_high, sequence)
            event_id = self._row_event_id(row)
            event: dict[str, Any] | None = None
            try:
                event = LifecycleProjector._source_event(row)
                fingerprint = self._row_fingerprint(row, event)
            except InvalidLifecycleEvent as exc:
                fingerprint = self._row_fingerprint(row, None)
                known = known_receipts.get(event_id)
                if known is not None:
                    if known.fingerprint != fingerprint:
                        raise ConflictingLifecycleEvent(
                            f"conflicting canonical event_id: {event_id}"
                        )
                    duplicates += 1
                    continue
                receipts.append(
                    self._receipt(
                        event_id=event_id,
                        ingested_seq=sequence,
                        fingerprint=fingerprint,
                        source_event_type=self._row_event_type(row),
                        created_at=_parse_iso(row.get("created_at") or self.clock()),
                        disposition="quarantined",
                    )
                )
                quarantines.append(
                    QuarantineRow(
                        event_id=event_id,
                        ingested_seq=sequence,
                        reason_code="INVALID_LIFECYCLE_EVENT",
                        reason_detail=str(exc),
                        source_event_type=self._row_event_type(row),
                        fingerprint=fingerprint,
                    )
                )
                quarantined += 1
                continue

            known_fingerprint = batch_fingerprints.get(event_id)
            if known_fingerprint is not None:
                if known_fingerprint != fingerprint:
                    raise ConflictingLifecycleEvent(
                        f"conflicting canonical event_id: {event_id}"
                    )
                duplicates += 1
                continue
            known = known_receipts.get(event_id)
            if known is not None:
                if known.fingerprint != fingerprint:
                    raise ConflictingLifecycleEvent(
                        f"conflicting canonical event_id: {event_id}"
                    )
                duplicates += 1
                continue
            batch_fingerprints[event_id] = fingerprint
            created_at = _parse_iso(event["created_at"])
            if event["event_type"] not in LIFECYCLE_EVENT_TYPES:
                receipts.append(
                    self._receipt(
                        event_id=event_id,
                        ingested_seq=sequence,
                        fingerprint=fingerprint,
                        source_event_type=str(event["event_type"]),
                        created_at=created_at,
                        disposition="ignored",
                    )
                )
                ignored += 1
                continue
            try:
                LifecycleProjector._validate_fixture_event(event)
                identity = LifecycleProjector._identity(event)
                source_sequence = LifecycleProjector._sequence_no(event)
            except InvalidLifecycleEvent as exc:
                receipts.append(
                    self._receipt(
                        event_id=event_id,
                        ingested_seq=sequence,
                        fingerprint=fingerprint,
                        source_event_type=str(event["event_type"]),
                        created_at=created_at,
                        disposition="quarantined",
                    )
                )
                quarantines.append(
                    QuarantineRow(
                        event_id=event_id,
                        ingested_seq=sequence,
                        reason_code="INVALID_LIFECYCLE_EVENT",
                        reason_detail=str(exc),
                        source_event_type=str(event["event_type"]),
                        fingerprint=fingerprint,
                    )
                )
                quarantined += 1
                continue
            accepted_entries.append(
                {
                    "fingerprint": fingerprint,
                    "event": event,
                    "identity": identity,
                    "sequence_no": source_sequence,
                    "ingested_seq": sequence,
                    "ingested_at": str(row.get("ingested_at") or self.clock()),
                    "source_mode": mode,
                    "accepted_live": mode == "live",
                }
            )
            receipts.append(
                self._receipt(
                    event_id=event_id,
                    ingested_seq=sequence,
                    fingerprint=fingerprint,
                    source_event_type=str(event["event_type"]),
                    created_at=created_at,
                    disposition="applied",
                    identity=identity,
                )
            )

        materializer = self._hydrate_materializer(accepted_entries)
        staged, _affected, accepted, staged_duplicates = materializer.stage_batch(
            accepted_entries,
            journey_events_fn=LifecycleProjector._journey_events,
        )
        duplicates += staged_duplicates
        mutation = self._controller_mutation(
            source_high_watermark=source_high,
            mode=mode,
            quarantined=quarantined,
        )
        mutation.receipts = receipts
        mutation.quarantines = quarantines
        (
            mutation.identity_links,
            mutation.journeys,
            mutation.stages,
            mutation.loop_runs,
        ) = self._aggregate_mutations(
            staged,
            accepted_entries,
            mode=mode,
            accepted_live=mutation.accepted_live,
        )
        self._controller_state = self.store.execute_batch_transaction(
            self.controller_id,
            self.tenant_scope,
            self.environment_scope,
            mutation,
        )
        self._materializer = materializer
        current = self._controller()
        return ProjectionResult(
            checkpoint=int(current.checkpoint_seq),
            accepted=accepted,
            duplicates=duplicates,
            ignored=ignored,
            quarantined=quarantined,
            journey_count=len(mutation.journeys),
            loop_run_count=len(mutation.loop_runs),
            generation=int(current.projection_revision),
            mode=mode,
        )

    def record_poll(
        self,
        *,
        source_high_watermark: int,
        backlog: int,
        mode: str,
    ) -> None:
        if mode not in PROJECTION_MODES:
            raise ValueError(f"unsupported projection mode: {mode}")
        mutation = self._controller_mutation(
            source_high_watermark=source_high_watermark,
            mode=mode,
            quarantined=0,
        )
        mutation.backlog_count = max(0, int(backlog))
        self._controller_state = self.store.execute_batch_transaction(
            self.controller_id,
            self.tenant_scope,
            self.environment_scope,
            mutation,
        )

    def record_source_failure(self, error: str, *, backlog: int | None = None) -> None:
        previous = self._controller()
        mutation = self._controller_mutation(
            source_high_watermark=int(previous.source_high_watermark),
            mode=str(previous.mode or "recovery"),
            quarantined=0,
            error_message=str(error),
        )
        if backlog is not None:
            mutation.backlog_count = max(0, int(backlog))
        self._controller_state = self.store.execute_batch_transaction(
            self.controller_id,
            self.tenant_scope,
            self.environment_scope,
            mutation,
        )


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
    """Read the retained lifecycle source window and receive wakeups.

    ``ingested_seq`` is global, but ``telemetry_events`` is retained and may be
    truncated independently of the durable projection.  Both the watermark and
    fetch therefore use the same lifecycle-type window; a historical checkpoint
    greater than an empty retained window is valid, not a source rewind.
    """

    def __init__(
        self,
        dsn: str,
        *,
        channel: str = DEFAULT_CHANNEL,
        include_non_lifecycle: bool = False,
    ) -> None:
        self.dsn = dsn
        self.channel = channel
        self.include_non_lifecycle = bool(include_non_lifecycle)
        self._listener: Any = None
        self._wake = asyncio.Event()

    async def verify_read_contract(self) -> None:
        """Bounded read-only check for the cursor columns this worker consumes.

        Database schema changes belong to ``scripts/db_migrate.sh``.  Running
        DDL or a historic-row backfill in this long-lived read worker makes a
        routine deploy wait indefinitely behind a database lock and prevents
        the new deployment identity from reaching readiness.
        """
        import asyncpg  # type: ignore[import]

        loop = asyncio.get_running_loop()
        deadline = loop.time() + DEFAULT_SOURCE_STARTUP_TIMEOUT_SECONDS
        conn: Any | None = None

        def remaining() -> float:
            return max(0.0, deadline - loop.time())

        try:
            conn = await asyncio.wait_for(asyncpg.connect(self.dsn), timeout=remaining())
            await asyncio.wait_for(
                conn.fetchrow(
                    "SELECT ingested_seq, ingested_at FROM telemetry_events LIMIT 1"
                ),
                timeout=remaining(),
            )
        finally:
            if conn is not None:
                close_timeout = remaining()
                if close_timeout <= 0:
                    self._terminate_connection(conn)
                    raise TimeoutError("telemetry startup read-contract deadline exhausted before close")
                try:
                    await asyncio.wait_for(conn.close(), timeout=close_timeout)
                except BaseException:
                    self._terminate_connection(conn)
                    raise

    @staticmethod
    def _terminate_connection(conn: Any) -> None:
        """Release a timed-out asyncpg connection without extending startup."""
        terminate = getattr(conn, "terminate", None)
        if callable(terminate):
            try:
                terminate()
            except Exception:  # noqa: BLE001 - preserve the source failure
                pass

    async def high_watermark(self) -> int:
        import asyncpg  # type: ignore[import]

        conn = await asyncpg.connect(self.dsn)
        try:
            if self.include_non_lifecycle:
                return int(
                    await conn.fetchval(
                        "SELECT COALESCE(MAX(ingested_seq), 0) FROM telemetry_events"
                    )
                    or 0
                )
            return int(
                await conn.fetchval(
                    "SELECT COALESCE(MAX(ingested_seq), 0) FROM telemetry_events "
                    "WHERE event_type = ANY($1::text[])",
                    list(LIFECYCLE_EVENT_TYPE_QUERY),
                )
                or 0
            )
        finally:
            await conn.close()

    async def fetch_after(self, checkpoint: int, *, limit: int) -> list[dict[str, Any]]:
        import asyncpg  # type: ignore[import]

        conn = await asyncpg.connect(self.dsn)
        try:
            if self.include_non_lifecycle:
                rows = await conn.fetch(
                    "SELECT ingested_seq, ingested_at, event_id, event_type, created_at, payload "
                    "FROM telemetry_events WHERE ingested_seq > $1 "
                    "ORDER BY ingested_seq ASC LIMIT $2",
                    int(checkpoint),
                    int(limit),
                )
            else:
                rows = await conn.fetch(
                    "SELECT ingested_seq, ingested_at, event_id, event_type, created_at, payload "
                    "FROM telemetry_events WHERE ingested_seq > $1 "
                    "AND event_type = ANY($2::text[]) "
                    "ORDER BY ingested_seq ASC LIMIT $3",
                    int(checkpoint),
                    list(LIFECYCLE_EVENT_TYPE_QUERY),
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


def _record_worker_failure(projector: Any, error: BaseException) -> bool:
    """Publish one durable error transition without allowing it to crash the worker."""

    error_message = f"{type(error).__name__}: {error}"
    try:
        projector.record_source_failure(error_message)
        return True
    except Exception as record_error:  # noqa: BLE001 - disk recovery happens in-loop
        if isinstance(projector, LifecycleProjector):
            now = projector.clock()
            controller = projector.state.setdefault("controller", {})
            controller.update(
                {
                    "status": "degraded",
                    "last_failure_at": now,
                    "last_error": error_message,
                    "error_publication_failure": (
                        f"{type(record_error).__name__}: {record_error}"
                    ),
                }
            )
        print(
            "lifecycle projector error publication failed; retaining worker for retry: "
            f"{type(record_error).__name__}: {record_error}",
            file=sys.stderr,
            flush=True,
        )
        return False


def _relational_writer_backend() -> str:
    return os.getenv(RELATIONAL_WRITER_BACKEND_ENV, "shadow").strip().lower()


def _configured_relational_projector() -> RelationalLifecycleProjector:
    """Build the canonical relational lifecycle projector.

    Legacy JSON writer is retired. Only relational writer (backend: 'shadow',
    'postgres', 'relational') is supported.
    """

    backend = _relational_writer_backend()
    if backend in {"disabled", "legacy_json", "json"}:
        raise RuntimeError(
            f"Legacy JSON projector writer is retired; {RELATIONAL_WRITER_BACKEND_ENV} "
            f"must be 'shadow', 'postgres', or 'relational' (got {backend!r})"
        )
    if backend not in {RELATIONAL_WRITER_BACKEND_SHADOW, "postgres", "relational"}:
        raise RuntimeError(
            f"{RELATIONAL_WRITER_BACKEND_ENV} must be 'shadow', 'postgres', or 'relational'"
        )
    dsn = os.getenv(RELATIONAL_WRITER_DSN_ENV, "").strip() or os.getenv("TELEMETRY_DB_DSN", "").strip()
    if not dsn:
        raise RuntimeError(
            f"{RELATIONAL_WRITER_DSN_ENV} or TELEMETRY_DB_DSN is required for relational writing"
        )
    store = ProjectionStore(
        dsn,
        schema=os.getenv(RELATIONAL_WRITER_SCHEMA_ENV, RELATIONAL_WRITER_DEFAULT_SCHEMA),
        bootstrap=False,
    )
    return RelationalLifecycleProjector(
        store,
        deployment_sha=os.getenv("GIT_SHA", "unknown"),
    )


async def run_worker() -> int:
    dsn = os.getenv("TELEMETRY_DB_DSN", "").strip()
    if not dsn:
        raise RuntimeError("TELEMETRY_DB_DSN is required")
    projector = _configured_relational_projector()
    source = PostgresLifecycleSource(dsn, include_non_lifecycle=True)
    interval = max(0.1, float(os.getenv("LIFECYCLE_PROJECTOR_POLL_SECONDS", "1")))
    batch_size = max(1, int(os.getenv("LIFECYCLE_PROJECTOR_BATCH_SIZE", "500")))
    max_ticks = max(0, int(os.getenv("LIFECYCLE_PROJECTOR_MAX_TICKS", "0")))
    tick = 0
    recovery_target = 0
    source_ready = False
    try:
        while True:
            tick += 1
            try:
                if not source_ready:
                    await source.verify_read_contract()
                    recovery_target = await source.high_watermark()
                    await source.start_listener()
                    source_ready = True
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
                _record_worker_failure(projector, exc)
                if not source_ready:
                    await source.close()
            if max_ticks and tick >= max_ticks:
                return 0
            await source.wait(interval)
    finally:
        await source.close()


def healthcheck() -> int:
    relational_projector = _configured_relational_projector()
    controller = relational_projector.controller
    ready = (
        controller["status"] == "ready"
        and bool(controller["accepted_live"])
        and controller["mode"] == "live"
        and int(controller["backlog"]) == 0
        and int(controller["quarantine_count"]) == 0
    )
    payload = {
        "schema_version": "pantheon.lifecycle-projector-relational-health.v1",
        "writer_backend": RELATIONAL_WRITER_BACKEND_SHADOW,
        "ready": ready,
        "controller": controller,
    }
    if not ready:
        print(f"lifecycle relational projector unhealthy: {_canonical_json(payload)}")
        return 1
    print(_canonical_json(payload))
    return 0


def _backfill(input_path: Path, *, mode: str) -> int:
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    records = raw.get("records") if isinstance(raw, Mapping) else raw
    if not isinstance(records, list):
        raise ValueError("backfill input must be a list or {'records': [...]} object")
    projector = _configured_relational_projector()
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
