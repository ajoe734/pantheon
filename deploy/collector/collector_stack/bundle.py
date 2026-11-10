"""Bundle writer, manifest generator, and integrity verifier (SD §6.5, §6.6, §7.2)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .config import SourceFetchPolicy
from .privacy import assert_privacy_compliant, scrub_item_pii

BUNDLE_SCHEMA_VERSION = "collector_bundle.v1"
VALID_BUNDLE_STATUSES = frozenset({"complete", "partial", "paused"})


class BundleIntegrityError(ValueError):
    """Raised when a bundle manifest or raw file fails integrity verification."""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_bundle_sha256(content_hashes: Sequence[str]) -> str:
    """Compute bundle_sha256 over the sorted item content_sha256 values joined by newline."""
    normalized = sorted(str(h).strip().lower() for h in content_hashes)
    return hashlib.sha256("\n".join(normalized).encode("utf-8")).hexdigest()


def utc_iso(dt: datetime | None = None) -> str:
    target = dt or datetime.now(timezone.utc)
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    return target.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class CollectedItem:
    item_id: str
    url: str
    fetched_at: str
    published_at: str | None
    text: str | None
    lang: str
    hashed_author: str | None
    metrics: Mapping[str, Any] = field(default_factory=dict)
    raw_bytes: bytes = b""
    raw_extension: str = "bin"
    http_status: int = 200
    extra_fields: Mapping[str, Any] = field(default_factory=dict)
    forbidden_literals: tuple[str, ...] = ()
    require_hashed_author: bool = False

    def to_jsonl_dict(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "id": self.item_id,
            "url": self.url,
            "fetched_at": self.fetched_at,
            "published_at": self.published_at,
            "text": self.text,
            "lang": self.lang,
            "hashed_author": self.hashed_author,
            "metrics": dict(self.metrics),
        }
        for k, v in self.extra_fields.items():
            if k not in base:
                base[k] = v
        scrubbed = scrub_item_pii(base)
        assert_privacy_compliant(
            scrubbed,
            raw_bytes=self.raw_bytes,
            forbidden_literals=self.forbidden_literals,
            require_hashed_author=self.require_hashed_author,
        )
        return scrubbed


@dataclass(frozen=True)
class WrittenBundle:
    source_id: str
    date: str
    run_id: str
    bundle_dir: Path
    manifest_path: Path
    items_path: Path
    manifest: Mapping[str, Any]


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


class BundleWriter:
    """Writes collector bundles and maintains bundles/index.json and status.json."""

    def __init__(self, root_dir: Path | str, *, collector_version: str = "1.0.0") -> None:
        self.root_dir = Path(root_dir)
        self.bundles_dir = self.root_dir / "bundles"
        self.collector_version = collector_version

    def write_bundle(
        self,
        *,
        policy: SourceFetchPolicy,
        run_id: str,
        items: Sequence[CollectedItem],
        status: str = "complete",
        paused_reason: str | None = None,
        started_at: datetime | str | None = None,
        finished_at: datetime | str | None = None,
        run_date: str | None = None,
    ) -> WrittenBundle:
        if status not in VALID_BUNDLE_STATUSES:
            raise ValueError(f"bundle status must be one of {sorted(VALID_BUNDLE_STATUSES)}, got {status!r}")
        if "/" in run_id or ".." in run_id or not run_id.strip():
            raise ValueError(f"invalid run_id: {run_id!r}")

        started_iso = started_at if isinstance(started_at, str) else utc_iso(started_at)
        finished_iso = finished_at if isinstance(finished_at, str) else utc_iso(finished_at)
        date_str = run_date or finished_iso[:10]

        bundle_dir = self.bundles_dir / policy.source_id / date_str / run_id
        raw_dir = bundle_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        manifest_items: list[dict[str, Any]] = []
        jsonl_lines: list[str] = []
        seen_ids: set[str] = set()

        for item in items:
            item_id = str(item.item_id).strip()
            if not item_id or item_id in seen_ids:
                raise ValueError(f"duplicate or empty item_id in bundle: {item_id!r}")
            seen_ids.add(item_id)

            row_dict = item.to_jsonl_dict()
            ext = str(item.raw_extension or "bin").lstrip(".").lower()
            content_hash = sha256_hex(item.raw_bytes)
            raw_rel_path = f"raw/{content_hash}.{ext}"
            raw_file_path = bundle_dir / raw_rel_path
            raw_file_path.write_bytes(item.raw_bytes)

            manifest_items.append(
                {
                    "item_id": item_id,
                    "url": item.url,
                    "http_status": int(item.http_status),
                    "content_sha256": content_hash,
                    "raw_path": raw_rel_path,
                    "bytes": len(item.raw_bytes),
                }
            )
            jsonl_lines.append(json.dumps(row_dict, sort_keys=True, ensure_ascii=False))

        bundle_hash = compute_bundle_sha256([m["content_sha256"] for m in manifest_items])

        manifest: dict[str, Any] = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "source_id": policy.source_id,
            "run_id": run_id,
            "collector_version": self.collector_version,
            "started_at": started_iso,
            "finished_at": finished_iso,
            "fetch_policy_hash": policy.policy_hash,
            "robots_decision": policy.robots_decision,
            "terms_ref": policy.terms_ref,
            "attribution": policy.attribution,
            "items": manifest_items,
            "status": status,
            "bundle_sha256": bundle_hash,
        }
        if paused_reason:
            manifest["paused_reason"] = paused_reason

        items_path = bundle_dir / "items.jsonl"
        items_path.write_text(
            ("\n".join(jsonl_lines) + "\n") if jsonl_lines else "",
            encoding="utf-8",
        )
        manifest_path = bundle_dir / "manifest.json"
        _atomic_write_json(manifest_path, manifest)

        self._update_index(
            source_id=policy.source_id,
            date_str=date_str,
            run_id=run_id,
            finished_at=finished_iso,
            status=status,
            bundle_sha256=bundle_hash,
        )
        self.update_host_status(
            source_id=policy.source_id,
            status=status,
            last_run_at=finished_iso,
            last_run_id=run_id,
            paused_reason=paused_reason,
            cadence_seconds=policy.cadence_seconds,
        )

        return WrittenBundle(
            source_id=policy.source_id,
            date=date_str,
            run_id=run_id,
            bundle_dir=bundle_dir,
            manifest_path=manifest_path,
            items_path=items_path,
            manifest=manifest,
        )

    def _update_index(
        self,
        *,
        source_id: str,
        date_str: str,
        run_id: str,
        finished_at: str,
        status: str,
        bundle_sha256: str,
    ) -> None:
        index_path = self.bundles_dir / "index.json"
        existing: list[dict[str, Any]] = []
        if index_path.is_file():
            try:
                loaded = json.loads(index_path.read_text(encoding="utf-8"))
                if isinstance(loaded, Mapping) and isinstance(loaded.get("bundles"), list):
                    existing = [
                        dict(entry)
                        for entry in loaded["bundles"]
                        if isinstance(entry, Mapping)
                        and not (
                            entry.get("source_id") == source_id
                            and entry.get("date") == date_str
                            and entry.get("run_id") == run_id
                        )
                    ]
            except ValueError:
                existing = []

        existing.append(
            {
                "source_id": source_id,
                "date": date_str,
                "run_id": run_id,
                "finished_at": finished_at,
                "status": status,
                "bundle_sha256": bundle_sha256,
            }
        )
        existing.sort(key=lambda e: (str(e.get("finished_at") or ""), str(e.get("run_id") or "")))
        _atomic_write_json(index_path, {"bundles": existing})

    def update_host_status(
        self,
        *,
        source_id: str,
        status: str,
        last_run_at: str,
        last_run_id: str,
        paused_reason: str | None = None,
        cadence_seconds: int = 86400,
        consecutive_blocks: int = 0,
    ) -> dict[str, Any]:
        status_path = self.root_dir / "status.json"
        sources_map: dict[str, Any] = {}
        if status_path.is_file():
            try:
                loaded = json.loads(status_path.read_text(encoding="utf-8"))
                if isinstance(loaded, Mapping) and isinstance(loaded.get("sources"), Mapping):
                    sources_map = dict(loaded["sources"])
            except ValueError:
                sources_map = {}

        sources_map[source_id] = {
            "source_id": source_id,
            "status": status,
            "last_run_at": last_run_at,
            "last_run_id": last_run_id,
            "paused_reason": paused_reason,
            "cadence_seconds": cadence_seconds,
            "consecutive_blocks": consecutive_blocks,
        }
        payload = {
            "updated_at": last_run_at,
            "collector_version": self.collector_version,
            "sources": sources_map,
        }
        _atomic_write_json(status_path, payload)
        _atomic_write_json(self.bundles_dir / "status.json", payload)
        return payload


def verify_bundle_directory(bundle_dir: Path | str) -> dict[str, Any]:
    """Recompute raw file SHA-256 hashes and bundle_sha256 from disk; fail on any tampering."""

    bdir = Path(bundle_dir)
    manifest_path = bdir / "manifest.json"
    if not manifest_path.is_file():
        raise BundleIntegrityError(f"missing manifest.json in {bdir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    required_fields = (
        "schema_version",
        "source_id",
        "run_id",
        "collector_version",
        "started_at",
        "finished_at",
        "fetch_policy_hash",
        "robots_decision",
        "terms_ref",
        "attribution",
        "items",
        "status",
        "bundle_sha256",
    )
    for field_name in required_fields:
        if field_name not in manifest:
            raise BundleIntegrityError(f"manifest missing required §6.5 field: {field_name}")

    recomputed_hashes: list[str] = []
    for item in manifest["items"]:
        raw_path = bdir / str(item["raw_path"])
        if not raw_path.is_file():
            raise BundleIntegrityError(f"missing raw file: {raw_path}")
        raw_bytes = raw_path.read_bytes()
        actual_hash = sha256_hex(raw_bytes)
        declared_hash = str(item["content_sha256"]).lower()
        if actual_hash != declared_hash:
            raise BundleIntegrityError(
                f"content_sha256 mismatch for {item['item_id']}: declared={declared_hash} actual={actual_hash}"
            )
        if int(item["bytes"]) != len(raw_bytes):
            raise BundleIntegrityError(
                f"byte length mismatch for {item['item_id']}: declared={item['bytes']} actual={len(raw_bytes)}"
            )
        recomputed_hashes.append(actual_hash)

    actual_bundle_hash = compute_bundle_sha256(recomputed_hashes)
    declared_bundle_hash = str(manifest["bundle_sha256"]).lower()
    if actual_bundle_hash != declared_bundle_hash:
        raise BundleIntegrityError(
            f"bundle_sha256 mismatch: declared={declared_bundle_hash} actual={actual_bundle_hash}"
        )

    return manifest


def recompute_bundle_hashes_from_disk(bundle_dir: Path | str) -> tuple[dict[str, str], str]:
    """Return ({item_id: actual_content_sha256}, actual_bundle_sha256) from current raw files on disk."""

    bdir = Path(bundle_dir)
    manifest = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
    item_hashes: dict[str, str] = {}
    for item in manifest["items"]:
        raw_path = bdir / str(item["raw_path"])
        item_hashes[str(item["item_id"])] = sha256_hex(raw_path.read_bytes())
    return item_hashes, compute_bundle_sha256(list(item_hashes.values()))


def prune_expired_bundles(
    root_dir: Path | str,
    *,
    max_age_days: int = 90,
    now: datetime | None = None,
) -> list[Path]:
    """Prune bundle directories older than max_age_days (SD §6.4, §6.6)."""

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff_date = (current - timedelta(days=max_age_days)).date()
    bundles_dir = Path(root_dir) / "bundles"
    removed: list[Path] = []
    if not bundles_dir.is_dir():
        return removed

    for source_dir in sorted(bundles_dir.iterdir()):
        if not source_dir.is_dir():
            continue
        for date_dir in sorted(source_dir.iterdir()):
            if not date_dir.is_dir():
                continue
            try:
                dir_date = datetime.strptime(date_dir.name, "%Y-%m-%d").date()
            except ValueError:
                continue
            if dir_date < cutoff_date:
                shutil.rmtree(date_dir)
                removed.append(date_dir)
    return removed
