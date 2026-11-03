#!/usr/bin/env python3
"""Prune stale release-artifacts and enforce pre-build disk floor on the dev VM.

Derives the kept set of ``pantheon-ci-deploy/release-artifacts`` entries from
the manifests already written by ``capture_dev_artifact_baseline.py`` and
``dev_release_artifact_driver.py`` without introducing a second release
registry:

1. Currently served release (live FE ``deployment.json`` / rollback baseline).
2. Last N accepted releases (default 3; baselines with ``candidate-frontend.json``
   or explicit accepted status).
3. In-flight candidate release (current deploy run's baseline folder and SHAs).
4. Latest captured rollback baseline (most recent ``baseline-*`` with ``manifest.json``).

Every image archive in ``images/``, compose bundle in ``compose/``, and
``baseline-*`` directory referenced by any kept release is retained, including
archives or compose bundles shared with stale releases. All other entries under
``images/``, ``compose/``, and ``baseline-*`` are deleted (or reported only in
``--dry-run`` mode) with deleted counts and byte totals logged.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Any, Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import dev_release_artifacts as artifacts  # noqa: E402, F401

VM_HOME = Path("/home/chloe_ong_dev_cctech_support_com")
ROOT = VM_HOME / "pantheon-ci-deploy" / "release-artifacts"
REPO = VM_HOME / "pantheon"
FE_LINK = Path("/var/www/pantheon-dev-fe")

RETENTION_SCHEMA_VERSION = "pantheon.dev-release-artifact-retention.v1"
DISK_PREFLIGHT_SCHEMA_VERSION = "pantheon.dev-deploy-disk-preflight.v1"
DEFAULT_KEEP_ACCEPTED_RELEASES = 3
DEFAULT_MIN_FREE_DISK_GB = 25.0
GIB_BYTES = 1024 * 1024 * 1024

_BASELINE_DIR_RE = re.compile(r"^baseline-(?:([0-9]+)-([0-9]+)-(.+)|.+)$")


def _parse_iso_timestamp(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).timestamp()


def _safe_load_json(path: Path) -> dict[str, Any] | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _entry_size_bytes(path: Path) -> int:
    try:
        info = path.lstat()
    except OSError:
        return 0
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return int(info.st_size)
    total = 0
    for root_dir, dirnames, filenames in os.walk(path, followlinks=False):
        root_path = Path(root_dir)
        for name in filenames:
            child = root_path / name
            try:
                total += int(child.lstat().st_size)
            except OSError:
                pass
        for dname in dirnames:
            child = root_path / dname
            try:
                dinfo = child.lstat()
                if stat.S_ISLNK(dinfo.st_mode):
                    total += int(dinfo.st_size)
            except OSError:
                pass
    return total


def _rmtree_force(path: Path) -> None:
    def _onerror(func: Callable[..., Any], target: str, _exc_info: Any) -> None:
        try:
            target_path = Path(target)
            if not target_path.is_symlink():
                os.chmod(target, 0o700)
            parent = target_path.parent
            if parent.exists() and not parent.is_symlink():
                os.chmod(parent, 0o700)
        except OSError:
            pass
        func(target)

    try:
        if not path.is_symlink():
            os.chmod(path, 0o700)
    except OSError:
        pass
    shutil.rmtree(path, onerror=_onerror)


def _extract_archive_names_from_obj(
    obj: Any,
    existing_images: set[str],
    out: set[str],
) -> None:
    if isinstance(obj, str):
        val = obj.strip()
        if not val:
            return
        name = Path(val).name
        if name.endswith(".tar"):
            out.add(name)
        elif val.startswith("sha256:") and len(val) == 71:
            prefix = val.split(":", 1)[1] + "-"
            for img in existing_images:
                if img.startswith(prefix) and img.endswith(".tar"):
                    out.add(img)
        return

    if isinstance(obj, list):
        for item in obj:
            _extract_archive_names_from_obj(item, existing_images, out)
        return

    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in {"name", "archive", "archive_name", "file", "filename"} and isinstance(value, str):
                name = Path(value.strip()).name
                if name:
                    out.add(name)
            elif key == "path" and isinstance(value, str):
                name = Path(value.strip()).name
                if name.endswith(".tar"):
                    out.add(name)
            else:
                _extract_archive_names_from_obj(value, existing_images, out)


def _extract_compose_names_from_obj(obj: Any, out: set[str]) -> None:
    if isinstance(obj, str):
        val = obj.strip()
        if not val:
            return
        name = Path(val).name
        if name:
            out.add(name)
        return
    if isinstance(obj, list):
        for item in obj:
            _extract_compose_names_from_obj(item, out)
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in {"sha", "source_sha", "backend_sha", "name", "compose_sha", "path", "bundle"} and isinstance(value, str):
                name = Path(value.strip()).name
                if name:
                    out.add(name)
            else:
                _extract_compose_names_from_obj(value, out)


@dataclass
class _BaselineRecord:
    path: Path
    name: str
    run_id: int = 0
    attempt: int = 0
    candidate_id: str = ""
    mtime_ns: int = 0
    manifest: dict[str, Any] | None = None
    candidate_images: dict[str, Any] | None = None
    candidate_bundle: dict[str, Any] | None = None
    candidate_frontend_captured: dict[str, Any] | None = None
    candidate_frontend: dict[str, Any] | None = None
    captured_at_ts: float | None = None
    sealed_at_ts: float | None = None
    accepted_at_ts: float | None = None

    @property
    def has_baseline_manifest(self) -> bool:
        return self.manifest is not None

    @property
    def has_candidate_artifacts(self) -> bool:
        return (
            self.candidate_images is not None
            or self.candidate_bundle is not None
            or self.candidate_frontend_captured is not None
            or self.candidate_frontend is not None
        )

    @property
    def identity(self) -> dict[str, Any]:
        for doc in (self.manifest, self.candidate_images):
            if isinstance(doc, dict) and isinstance(doc.get("identity"), dict):
                return doc["identity"]
        return {}

    @property
    def candidate_backend_sha(self) -> str:
        ident = self.identity
        if isinstance(ident.get("candidate_backend_sha"), str) and ident["candidate_backend_sha"]:
            return ident["candidate_backend_sha"]
        for doc in (self.candidate_frontend, self.candidate_frontend_captured, self.manifest, self.candidate_images):
            if isinstance(doc, dict):
                for key in ("candidate_backend_sha", "backend_sha"):
                    if isinstance(doc.get(key), str) and doc[key]:
                        return doc[key]
        if isinstance(self.candidate_bundle, dict) and isinstance(self.candidate_bundle.get("source_sha"), str):
            return self.candidate_bundle["source_sha"]
        return ""

    @property
    def candidate_frontend_sha(self) -> str:
        ident = self.identity
        if isinstance(ident.get("candidate_frontend_sha"), str) and ident["candidate_frontend_sha"]:
            return ident["candidate_frontend_sha"]
        for doc in (self.candidate_frontend, self.candidate_frontend_captured, self.manifest, self.candidate_images):
            if isinstance(doc, dict):
                for key in ("candidate_frontend_sha", "frontend_sha"):
                    if isinstance(doc.get(key), str) and doc[key]:
                        return doc[key]
        return ""

    @property
    def previous_backend_sha(self) -> str:
        ident = self.identity
        if isinstance(ident.get("previous_backend_sha"), str) and ident["previous_backend_sha"]:
            return ident["previous_backend_sha"]
        if isinstance(self.manifest, dict):
            if isinstance(self.manifest.get("previous_backend_sha"), str) and self.manifest["previous_backend_sha"]:
                return self.manifest["previous_backend_sha"]
            img_bundle = self.manifest.get("image_bundle")
            if isinstance(img_bundle, dict) and isinstance(img_bundle.get("source_sha"), str):
                return img_bundle["source_sha"]
            fe = self.manifest.get("frontend")
            if isinstance(fe, dict) and isinstance(fe.get("backend_sha"), str):
                return fe["backend_sha"]
        return ""

    @property
    def previous_frontend_sha(self) -> str:
        ident = self.identity
        if isinstance(ident.get("previous_frontend_sha"), str) and ident["previous_frontend_sha"]:
            return ident["previous_frontend_sha"]
        if isinstance(self.manifest, dict):
            if isinstance(self.manifest.get("previous_frontend_sha"), str) and self.manifest["previous_frontend_sha"]:
                return self.manifest["previous_frontend_sha"]
            fe = self.manifest.get("frontend")
            if isinstance(fe, dict) and isinstance(fe.get("frontend_sha"), str):
                return fe["frontend_sha"]
        return ""

    @property
    def candidate_fe_target(self) -> str:
        for doc in (self.candidate_frontend, self.candidate_frontend_captured):
            if isinstance(doc, dict) and isinstance(doc.get("target"), str) and doc["target"]:
                return doc["target"]
        return ""

    @property
    def baseline_fe_target(self) -> str:
        if isinstance(self.manifest, dict):
            fe = self.manifest.get("frontend")
            if isinstance(fe, dict) and isinstance(fe.get("target"), str) and fe["target"]:
                return fe["target"]
        return ""

    @property
    def is_accepted(self) -> bool:
        if self.candidate_frontend is not None:
            return True
        for doc in (self.manifest, self.candidate_images):
            if isinstance(doc, dict):
                if doc.get("accepted") is True or doc.get("status") == "accepted":
                    return True
        return False

    @property
    def is_explicitly_served(self) -> bool:
        for doc in (self.manifest, self.candidate_images, self.candidate_frontend):
            if isinstance(doc, dict):
                if (
                    doc.get("currently_served") is True
                    or doc.get("served") is True
                    or doc.get("status") in {"served", "currently_served"}
                ):
                    return True
        return False

    @property
    def is_explicitly_in_flight(self) -> bool:
        for doc in (self.manifest, self.candidate_images):
            if isinstance(doc, dict):
                if doc.get("in_flight") is True or doc.get("status") in {"in_flight", "in-flight", "candidate"}:
                    return True
        return False

    @property
    def baseline_sort_key(self) -> tuple[float, int, int, int, str]:
        ts = self.captured_at_ts if self.captured_at_ts is not None else -1.0
        return (ts, self.run_id, self.attempt, self.mtime_ns, self.name)

    @property
    def accepted_sort_key(self) -> tuple[float, int, int, int, str]:
        ts = (
            self.accepted_at_ts
            if self.accepted_at_ts is not None
            else (
                self.sealed_at_ts
                if self.sealed_at_ts is not None
                else (self.captured_at_ts if self.captured_at_ts is not None else -1.0)
            )
        )
        return (ts, self.run_id, self.attempt, self.mtime_ns, self.name)

    def referenced_images(self, existing_images: set[str]) -> set[str]:
        images: set[str] = set()
        if isinstance(self.manifest, dict):
            img_bundle = self.manifest.get("image_bundle")
            if isinstance(img_bundle, dict):
                if "archives" in img_bundle:
                    _extract_archive_names_from_obj(img_bundle["archives"], existing_images, images)
                elif "services" in img_bundle:
                    _extract_archive_names_from_obj(img_bundle["services"], existing_images, images)
            for key in ("archives", "image_archives", "images"):
                if key in self.manifest:
                    _extract_archive_names_from_obj(self.manifest[key], existing_images, images)

        if isinstance(self.candidate_bundle, dict):
            if "archives" in self.candidate_bundle:
                _extract_archive_names_from_obj(self.candidate_bundle["archives"], existing_images, images)
            elif "services" in self.candidate_bundle:
                _extract_archive_names_from_obj(self.candidate_bundle["services"], existing_images, images)

        if isinstance(self.candidate_images, dict):
            for key in ("archives", "image_archives", "images", "image_bundle"):
                if key in self.candidate_images:
                    _extract_archive_names_from_obj(self.candidate_images[key], existing_images, images)
            if self.candidate_bundle is None and "services" in self.candidate_images:
                _extract_archive_names_from_obj(self.candidate_images["services"], existing_images, images)

        return images

    def referenced_compose_bundles(self) -> set[str]:
        bundles: set[str] = set()
        for sha in (
            self.previous_backend_sha,
            self.candidate_backend_sha,
        ):
            if sha:
                bundles.add(sha)

        for doc in (self.manifest, self.candidate_bundle, self.candidate_images, self.candidate_frontend, self.candidate_frontend_captured):
            if not isinstance(doc, dict):
                continue
            for key in ("source_sha", "backend_sha", "previous_backend_sha", "candidate_backend_sha"):
                val = doc.get(key)
                if isinstance(val, str) and val.strip():
                    bundles.add(Path(val.strip()).name)
            img_bundle = doc.get("image_bundle")
            if isinstance(img_bundle, dict) and isinstance(img_bundle.get("source_sha"), str) and img_bundle["source_sha"].strip():
                bundles.add(Path(img_bundle["source_sha"].strip()).name)
            fe = doc.get("frontend")
            if isinstance(fe, dict) and isinstance(fe.get("backend_sha"), str) and fe["backend_sha"].strip():
                bundles.add(Path(fe["backend_sha"].strip()).name)
            for key in ("compose", "compose_bundle", "compose_bundles", "compose_sha"):
                if key in doc:
                    _extract_compose_names_from_obj(doc[key], bundles)

        return bundles


@dataclass
class RetentionResult:
    schema_version: str = RETENTION_SCHEMA_VERSION
    artifact_root: str = ""
    dry_run: bool = False
    keep_accepted_count: int = DEFAULT_KEEP_ACCEPTED_RELEASES
    kept_by_reason: dict[str, list[str]] = field(default_factory=dict)
    kept_baselines: list[str] = field(default_factory=list)
    kept_images: list[str] = field(default_factory=list)
    kept_compose: list[str] = field(default_factory=list)
    deleted_baselines: list[str] = field(default_factory=list)
    deleted_baselines_count: int = 0
    deleted_baselines_bytes: int = 0
    deleted_images: list[str] = field(default_factory=list)
    deleted_images_count: int = 0
    deleted_images_bytes: int = 0
    deleted_compose: list[str] = field(default_factory=list)
    deleted_compose_count: int = 0
    deleted_compose_bytes: int = 0
    deleted_count: int = 0
    deleted_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "artifact_root": self.artifact_root,
            "dry_run": self.dry_run,
            "keep_accepted_count": self.keep_accepted_count,
            "kept_by_reason": self.kept_by_reason,
            "kept_baselines": self.kept_baselines,
            "kept_baselines_count": len(self.kept_baselines),
            "kept_images": self.kept_images,
            "kept_images_count": len(self.kept_images),
            "kept_compose": self.kept_compose,
            "kept_compose_count": len(self.kept_compose),
            "deleted_baselines": self.deleted_baselines,
            "deleted_baselines_count": self.deleted_baselines_count,
            "deleted_baselines_bytes": self.deleted_baselines_bytes,
            "deleted_images": self.deleted_images,
            "deleted_images_count": self.deleted_images_count,
            "deleted_images_bytes": self.deleted_images_bytes,
            "deleted_compose": self.deleted_compose,
            "deleted_compose_count": self.deleted_compose_count,
            "deleted_compose_bytes": self.deleted_compose_bytes,
            "deleted_count": self.deleted_count,
            "deleted_bytes": self.deleted_bytes,
        }

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "artifact_root": self.artifact_root,
            "dry_run": self.dry_run,
            "keep_accepted_count": self.keep_accepted_count,
            "kept_by_reason": self.kept_by_reason,
            "kept_baselines_count": len(self.kept_baselines),
            "kept_images_count": len(self.kept_images),
            "kept_compose_count": len(self.kept_compose),
            "deleted_baselines_count": self.deleted_baselines_count,
            "deleted_baselines_bytes": self.deleted_baselines_bytes,
            "deleted_images_count": self.deleted_images_count,
            "deleted_images_bytes": self.deleted_images_bytes,
            "deleted_compose_count": self.deleted_compose_count,
            "deleted_compose_bytes": self.deleted_compose_bytes,
            "deleted_count": self.deleted_count,
            "deleted_bytes": self.deleted_bytes,
        }


def _scan_baseline_record(entry: Path) -> _BaselineRecord:
    match = _BASELINE_DIR_RE.match(entry.name)
    run_id = int(match.group(1)) if match and match.group(1) else 0
    attempt = int(match.group(2)) if match and match.group(2) else 0
    candidate_id = match.group(3) if match and match.group(3) else ""
    try:
        mtime_ns = int(entry.lstat().st_mtime_ns)
    except OSError:
        mtime_ns = 0

    manifest = _safe_load_json(entry / "manifest.json")
    candidate_images = _safe_load_json(entry / "candidate-images.json")
    candidate_bundle = _safe_load_json(entry / "candidate-bundle.json")
    candidate_frontend_captured = _safe_load_json(entry / "candidate-frontend-captured.json")
    candidate_frontend = _safe_load_json(entry / "candidate-frontend.json")

    for doc in (manifest, candidate_images):
        if isinstance(doc, dict) and isinstance(doc.get("identity"), dict):
            ident = doc["identity"]
            if not run_id and str(ident.get("run_id", "")).isdigit():
                run_id = int(ident["run_id"])
            if not attempt and str(ident.get("attempt", "")).isdigit():
                attempt = int(ident["attempt"])
            if not candidate_id and isinstance(ident.get("candidate_id"), str):
                candidate_id = ident["candidate_id"]

    captured_at_ts = _parse_iso_timestamp(manifest.get("captured_at")) if isinstance(manifest, dict) else None
    sealed_at_ts = _parse_iso_timestamp(candidate_images.get("sealed_at")) if isinstance(candidate_images, dict) else None
    accepted_at_ts = None
    if isinstance(candidate_frontend, dict):
        accepted_at_ts = _parse_iso_timestamp(
            candidate_frontend.get("accepted_at")
            or candidate_frontend.get("sealed_at")
            or candidate_frontend.get("verified_at")
        )
    if accepted_at_ts is None and isinstance(manifest, dict):
        accepted_at_ts = _parse_iso_timestamp(manifest.get("accepted_at") or manifest.get("sealed_at"))

    return _BaselineRecord(
        path=entry,
        name=entry.name,
        run_id=run_id,
        attempt=attempt,
        candidate_id=candidate_id,
        mtime_ns=mtime_ns,
        manifest=manifest,
        candidate_images=candidate_images,
        candidate_bundle=candidate_bundle,
        candidate_frontend_captured=candidate_frontend_captured,
        candidate_frontend=candidate_frontend,
        captured_at_ts=captured_at_ts,
        sealed_at_ts=sealed_at_ts,
        accepted_at_ts=accepted_at_ts,
    )


def _read_served_release_identities(
    *,
    artifact_root: Path,
    fe_live_link: Path | None,
    served_manifest_path: Path | None,
    served_backend_sha: str,
    served_frontend_sha: str,
    served_fe_target: str,
    served_baseline: str,
) -> tuple[list[tuple[str, str, str]], set[str]]:
    identities: list[tuple[str, str, str]] = []
    explicit_baselines: set[str] = set()
    if served_baseline:
        explicit_baselines.add(Path(served_baseline).name)

    def _add_identity(be: str, fe: str, target: str) -> None:
        item = (be.strip(), fe.strip(), target.strip())
        if any(item) and item not in identities:
            identities.append(item)

    _add_identity(served_backend_sha, served_frontend_sha, served_fe_target)

    candidate_manifests: list[Path] = []
    if served_manifest_path is not None:
        candidate_manifests.append(served_manifest_path)
    for fallback_name in ("served-manifest.json", "current-release.json", "currently-served.json"):
        fallback_path = artifact_root / fallback_name
        if fallback_path.exists():
            candidate_manifests.append(fallback_path)

    for mpath in candidate_manifests:
        data = _safe_load_json(mpath)
        if isinstance(data, dict):
            if isinstance(data.get("baseline"), str) and data["baseline"].strip():
                explicit_baselines.add(Path(data["baseline"].strip()).name)
            be = str(
                data.get("backend_sha")
                or data.get("bffCommit")
                or data.get("bffSourceCommitSha")
                or data.get("previous_backend_sha")
                or ""
            )
            fe = str(
                data.get("frontend_sha")
                or data.get("frontendSha")
                or data.get("commit")
                or data.get("previous_frontend_sha")
                or ""
            )
            target = str(data.get("target") or "")
            _add_identity(be, fe, target)

    link_candidates: list[Path] = []
    if fe_live_link is not None:
        link_candidates.append(fe_live_link)
    elif (artifact_root / "fe-live").exists() or (artifact_root / "fe-live").is_symlink():
        link_candidates.append(artifact_root / "fe-live")
    elif artifact_root == ROOT:
        link_candidates.append(FE_LINK)

    for link in link_candidates:
        try:
            if not link.exists() and not link.is_symlink():
                continue
            if link.is_file() and not link.is_dir():
                data = _safe_load_json(link)
                if isinstance(data, dict):
                    be = str(data.get("bffCommit") or data.get("bffSourceCommitSha") or data.get("backend_sha") or "")
                    fe = str(data.get("frontendSha") or data.get("commit") or data.get("frontend_sha") or "")
                    target = str(data.get("target") or "")
                    _add_identity(be, fe, target)
                continue
            resolved_target = str(link.resolve())
            deploy_json = _safe_load_json(link / "deployment.json")
            if isinstance(deploy_json, dict):
                be = str(
                    deploy_json.get("bffCommit")
                    or deploy_json.get("bffSourceCommitSha")
                    or deploy_json.get("backend_sha")
                    or ""
                )
                fe = str(
                    deploy_json.get("frontendSha")
                    or deploy_json.get("commit")
                    or deploy_json.get("frontend_sha")
                    or ""
                )
                _add_identity(be, fe, resolved_target)
            else:
                _add_identity("", "", resolved_target)
        except OSError:
            continue

    return identities, explicit_baselines


def apply_release_artifact_retention(
    artifact_root: Path = ROOT,
    *,
    fe_live_link: Path | None = None,
    served_manifest_path: Path | None = None,
    served_backend_sha: str = "",
    served_frontend_sha: str = "",
    served_fe_target: str = "",
    served_baseline: str = "",
    in_flight_baseline: str = "",
    in_flight_manifest: Path | None = None,
    in_flight_run_id: str = "",
    in_flight_attempt: str = "",
    in_flight_candidate_id: str = "",
    in_flight_backend_sha: str = "",
    in_flight_previous_backend_sha: str = "",
    keep_accepted: int = DEFAULT_KEEP_ACCEPTED_RELEASES,
    dry_run: bool = False,
    repo_root: Path | None = None,
) -> RetentionResult:
    if keep_accepted < 0:
        raise ValueError("keep_accepted must be non-negative")

    result = RetentionResult(
        artifact_root=str(artifact_root),
        dry_run=dry_run,
        keep_accepted_count=keep_accepted,
        kept_by_reason={
            "currently_served": [],
            "last_accepted": [],
            "in_flight_candidate": [],
            "latest_rollback_baseline": [],
        },
    )

    if not artifact_root.exists() and not artifact_root.is_symlink():
        return result
    if artifact_root.is_symlink() or not artifact_root.is_dir():
        raise RuntimeError(f"release-artifacts root is not a regular directory: {artifact_root}")

    images_dir = artifact_root / "images"
    compose_dir = artifact_root / "compose"

    existing_images: set[str] = set()
    if images_dir.exists() and not images_dir.is_symlink() and images_dir.is_dir():
        existing_images = {p.name for p in images_dir.iterdir()}

    records_by_name: dict[str, _BaselineRecord] = {}
    all_baseline_entries: list[Path] = []
    for child in sorted(artifact_root.iterdir(), key=lambda p: p.name):
        if not child.name.startswith("baseline-"):
            continue
        all_baseline_entries.append(child)
        try:
            info = child.lstat()
        except OSError:
            continue
        if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
            rec = _scan_baseline_record(child)
            if rec.has_baseline_manifest or rec.has_candidate_artifacts:
                records_by_name[rec.name] = rec

    all_records = list(records_by_name.values())

    # 1. Last N accepted releases
    accepted_records = sorted(
        (rec for rec in all_records if rec.is_accepted),
        key=lambda r: r.accepted_sort_key,
    )
    kept_accepted = accepted_records[-keep_accepted:] if keep_accepted > 0 else []
    result.kept_by_reason["last_accepted"] = [r.name for r in kept_accepted]

    # 2. Latest captured rollback baseline
    baseline_records = sorted(
        (rec for rec in all_records if rec.has_baseline_manifest),
        key=lambda r: r.baseline_sort_key,
    )
    kept_latest_baseline = [baseline_records[-1]] if baseline_records else []
    result.kept_by_reason["latest_rollback_baseline"] = [r.name for r in kept_latest_baseline]

    # 3. In-flight candidate
    in_flight_names: set[str] = set()
    if in_flight_baseline:
        in_flight_names.add(Path(in_flight_baseline).name)
    if in_flight_manifest is not None:
        try:
            if in_flight_manifest.parent.name.startswith("baseline-"):
                in_flight_names.add(in_flight_manifest.parent.name)
        except Exception:
            pass
    if in_flight_run_id and in_flight_attempt and in_flight_candidate_id:
        in_flight_names.add(f"baseline-{in_flight_run_id}-{in_flight_attempt}-{in_flight_candidate_id}")

    kept_in_flight: list[_BaselineRecord] = []
    for rec in all_records:
        if rec.name in in_flight_names or rec.is_explicitly_in_flight:
            kept_in_flight.append(rec)
        elif in_flight_candidate_id and rec.candidate_id == in_flight_candidate_id:
            if (not in_flight_run_id or str(rec.run_id) == str(in_flight_run_id)) and (
                not in_flight_attempt or str(rec.attempt) == str(in_flight_attempt)
            ):
                kept_in_flight.append(rec)

    if not kept_in_flight and not in_flight_names:
        # Automatic discovery when no explicit in-flight parameters were passed:
        # keep the newest unaccepted candidate if it is newer than the newest accepted release.
        unaccepted = sorted(
            (rec for rec in all_records if not rec.is_accepted),
            key=lambda r: r.baseline_sort_key,
        )
        if unaccepted:
            newest_unaccepted = unaccepted[-1]
            if not accepted_records or newest_unaccepted.baseline_sort_key >= accepted_records[-1].accepted_sort_key:
                kept_in_flight.append(newest_unaccepted)

    # Also ensure any explicitly named in_flight_names directory on disk is preserved even if its
    # manifest is still being populated.
    kept_in_flight_names = sorted({r.name for r in kept_in_flight} | {
        name for name in in_flight_names if (artifact_root / name).exists()
    })
    result.kept_by_reason["in_flight_candidate"] = kept_in_flight_names

    # 4. Currently served release
    served_identities, explicit_served_baselines = _read_served_release_identities(
        artifact_root=artifact_root,
        fe_live_link=fe_live_link,
        served_manifest_path=served_manifest_path,
        served_backend_sha=served_backend_sha,
        served_frontend_sha=served_frontend_sha,
        served_fe_target=served_fe_target,
        served_baseline=served_baseline,
    )

    # If no explicit served identity was provided, fall back to the in-flight or latest
    # rollback baseline's previous pair, or the newest accepted release.
    if not served_identities and not explicit_served_baselines:
        reference_baselines = kept_in_flight or kept_latest_baseline
        for ref in reference_baselines:
            if ref.previous_backend_sha or ref.previous_frontend_sha or ref.baseline_fe_target:
                served_identities.append(
                    (ref.previous_backend_sha, ref.previous_frontend_sha, ref.baseline_fe_target)
                )
        if not served_identities and accepted_records:
            newest_acc = accepted_records[-1]
            served_identities.append(
                (newest_acc.candidate_backend_sha, newest_acc.candidate_frontend_sha, newest_acc.candidate_fe_target)
            )

    kept_served_names: set[str] = {
        name for name in explicit_served_baselines if (artifact_root / name).exists()
    }
    for rec in all_records:
        if rec.is_explicitly_served:
            kept_served_names.add(rec.name)

    for srv_be, srv_fe, srv_target in served_identities:
        # Match accepted/candidate releases that deployed the served identity
        matching_candidates = [
            rec
            for rec in all_records
            if (
                (srv_target and rec.candidate_fe_target == srv_target)
                or (
                    srv_be
                    and rec.candidate_backend_sha == srv_be
                    and (not srv_fe or not rec.candidate_frontend_sha or rec.candidate_frontend_sha == srv_fe)
                )
            )
        ]
        matching_accepted = [rec for rec in matching_candidates if rec.is_accepted]
        if matching_accepted:
            for rec in matching_accepted:
                kept_served_names.add(rec.name)
        elif matching_candidates:
            newest_cand = max(matching_candidates, key=lambda r: r.accepted_sort_key)
            kept_served_names.add(newest_cand.name)

        # Also match the newest baseline manifest that captured the currently served release
        matching_baselines = [
            rec
            for rec in baseline_records
            if (
                (srv_target and rec.baseline_fe_target == srv_target)
                or (
                    srv_be
                    and rec.previous_backend_sha == srv_be
                    and (not srv_fe or not rec.previous_frontend_sha or rec.previous_frontend_sha == srv_fe)
                )
            )
        ]
        if matching_baselines:
            kept_served_names.add(matching_baselines[-1].name)

    result.kept_by_reason["currently_served"] = sorted(kept_served_names)

    # Union of all kept baseline folders
    kept_baseline_set: set[str] = set()
    for names in result.kept_by_reason.values():
        kept_baseline_set.update(names)
    result.kept_baselines = sorted(kept_baseline_set)

    # Union of all image archives and compose bundles referenced by ANY kept baseline
    kept_image_set: set[str] = set()
    kept_compose_set: set[str] = set()
    for name in result.kept_baselines:
        rec = records_by_name.get(name)
        if rec is None:
            continue
        kept_image_set.update(rec.referenced_images(existing_images))
        kept_compose_set.update(rec.referenced_compose_bundles())

    # Also retain compose bundles for explicitly passed served / in-flight backend SHAs
    for srv_be, _srv_fe, _srv_target in served_identities:
        if srv_be:
            kept_compose_set.add(Path(srv_be).name)
    for extra_sha in (served_backend_sha, in_flight_backend_sha, in_flight_previous_backend_sha):
        if extra_sha and extra_sha.strip():
            kept_compose_set.add(Path(extra_sha.strip()).name)

    result.kept_images = sorted(kept_image_set)
    result.kept_compose = sorted(kept_compose_set)

    # Prune unreferenced image archives in images/
    if images_dir.exists() and not images_dir.is_symlink() and images_dir.is_dir():
        for entry in sorted(images_dir.iterdir(), key=lambda p: p.name):
            if entry.name in kept_image_set:
                continue
            size = _entry_size_bytes(entry)
            result.deleted_images.append(entry.name)
            result.deleted_images_count += 1
            result.deleted_images_bytes += size
            if not dry_run:
                if entry.is_symlink() or not entry.is_dir():
                    entry.unlink()
                else:
                    _rmtree_force(entry)

    # Prune unreferenced compose bundles in compose/
    if compose_dir.exists() and not compose_dir.is_symlink() and compose_dir.is_dir():
        for entry in sorted(compose_dir.iterdir(), key=lambda p: p.name):
            if entry.name in kept_compose_set:
                continue
            size = _entry_size_bytes(entry)
            result.deleted_compose.append(entry.name)
            result.deleted_compose_count += 1
            result.deleted_compose_bytes += size
            if not dry_run:
                if entry.is_symlink() or not entry.is_dir():
                    entry.unlink()
                else:
                    _rmtree_force(entry)

    # Prune unreferenced baseline-* folders in artifact_root
    for entry in all_baseline_entries:
        if entry.name in kept_baseline_set:
            continue
        size = _entry_size_bytes(entry)
        result.deleted_baselines.append(entry.name)
        result.deleted_baselines_count += 1
        result.deleted_baselines_bytes += size
        if not dry_run:
            if entry.is_symlink() or not entry.is_dir():
                entry.unlink()
            else:
                _rmtree_force(entry)

    if not dry_run and result.deleted_compose_count > 0:
        git_repo = repo_root if repo_root is not None else (REPO if artifact_root == ROOT else None)
        if git_repo is not None and (git_repo / ".git").exists():
            try:
                subprocess.run(
                    ["git", "-C", str(git_repo), "worktree", "prune"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except Exception:
                pass

    result.deleted_count = (
        result.deleted_images_count
        + result.deleted_compose_count
        + result.deleted_baselines_count
    )
    result.deleted_bytes = (
        result.deleted_images_bytes
        + result.deleted_compose_bytes
        + result.deleted_baselines_bytes
    )
    return result


def parse_disk_size_bytes(value: str | int | float) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if float(value) < 0:
            raise ValueError(f"disk floor cannot be negative: {value}")
        return int(float(value) * GIB_BYTES)
    text = str(value).strip()
    if not text:
        raise ValueError("disk floor value is empty")
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)?", text)
    if not match:
        raise ValueError(f"invalid disk size format: {value!r}")
    amount = float(match.group(1))
    unit = (match.group(2) or "gb").lower()
    multipliers = {
        "b": 1,
        "k": 1024,
        "kb": 1024,
        "kib": 1024,
        "m": 1024 * 1024,
        "mb": 1024 * 1024,
        "mib": 1024 * 1024,
        "g": GIB_BYTES,
        "gb": GIB_BYTES,
        "gib": GIB_BYTES,
        "t": 1024 * GIB_BYTES,
        "tb": 1024 * GIB_BYTES,
        "tib": 1024 * GIB_BYTES,
    }
    if unit not in multipliers:
        raise ValueError(f"unsupported disk size unit {unit!r} in {value!r}")
    return int(amount * multipliers[unit])


@dataclass
class DiskPreflightResult:
    schema_version: str = DISK_PREFLIGHT_SCHEMA_VERSION
    path: str = ""
    free_bytes: int = 0
    free_gb: float = 0.0
    min_free_bytes: int = 0
    min_free_gb: float = 0.0
    ok: bool = False
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "path": self.path,
            "free_bytes": self.free_bytes,
            "free_gb": round(self.free_gb, 2),
            "min_free_bytes": self.min_free_bytes,
            "min_free_gb": round(self.min_free_gb, 2),
            "ok": self.ok,
            "message": self.message,
        }


def check_disk_preflight(
    path: Path = Path("."),
    *,
    min_free_gb: str | int | float = DEFAULT_MIN_FREE_DISK_GB,
    free_bytes_override: int | None = None,
) -> DiskPreflightResult:
    min_free_bytes = parse_disk_size_bytes(min_free_gb)
    target = path if path.exists() else path.parent
    resolved = target.resolve() if target.exists() else path

    if free_bytes_override is not None:
        free_bytes = int(free_bytes_override)
    else:
        st = os.statvfs(resolved)
        free_bytes = int(st.f_bavail * st.f_frsize)

    free_gb_val = free_bytes / GIB_BYTES
    min_free_gb_val = min_free_bytes / GIB_BYTES
    ok = free_bytes >= min_free_bytes
    if ok:
        msg = (
            f"disk preflight passed on {resolved}: "
            f"available={free_gb_val:.2f}GB ({free_bytes} bytes) >= "
            f"floor={min_free_gb_val:.2f}GB ({min_free_bytes} bytes)"
        )
    else:
        msg = (
            f"insufficient disk space on deploy filesystem ({resolved}): "
            f"available={free_gb_val:.2f}GB ({free_bytes} bytes) is below required "
            f"floor={min_free_gb_val:.2f}GB ({min_free_bytes} bytes); "
            f"aborting deploy before docker build"
        )
    return DiskPreflightResult(
        path=str(resolved),
        free_bytes=free_bytes,
        free_gb=free_gb_val,
        min_free_bytes=min_free_bytes,
        min_free_gb=min_free_gb_val,
        ok=ok,
        message=msg,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dev_release_artifact_retention.py",
        description="Apply retention policy to pantheon-ci-deploy/release-artifacts and check deploy disk floor.",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path(os.environ.get("PANTHEON_DEV_ARTIFACT_ROOT", str(ROOT))),
        help="Path to pantheon-ci-deploy/release-artifacts root directory.",
    )
    parser.add_argument(
        "--fe-live-link",
        type=Path,
        default=None,
        help="Optional live frontend symlink path (defaults to /var/www/pantheon-dev-fe when using default artifact root).",
    )
    parser.add_argument(
        "--served-manifest",
        type=Path,
        default=None,
        help="Optional path to a JSON manifest describing the currently served release.",
    )
    parser.add_argument(
        "--served-backend-sha",
        default=os.environ.get("PANTHEON_DEV_SERVED_BACKEND_SHA", ""),
        help="Currently served backend SHA.",
    )
    parser.add_argument(
        "--served-frontend-sha",
        default=os.environ.get("PANTHEON_DEV_SERVED_FRONTEND_SHA", ""),
        help="Currently served frontend SHA.",
    )
    parser.add_argument(
        "--served-fe-target",
        default=os.environ.get("PANTHEON_DEV_SERVED_FE_TARGET", ""),
        help="Currently served frontend release directory target.",
    )
    parser.add_argument(
        "--served-baseline",
        default=os.environ.get("PANTHEON_DEV_SERVED_BASELINE", ""),
        help="Explicit baseline folder name for the currently served release.",
    )
    parser.add_argument(
        "--in-flight-baseline",
        default=os.environ.get("PANTHEON_DEV_IN_FLIGHT_BASELINE", ""),
        help="Explicit baseline folder name for the in-flight candidate.",
    )
    parser.add_argument(
        "--in-flight-manifest",
        type=Path,
        default=(
            Path(os.environ["PANTHEON_DEV_ARTIFACT_MANIFEST_PATH"])
            if os.environ.get("PANTHEON_DEV_ARTIFACT_MANIFEST_PATH")
            else None
        ),
        help="Path to the in-flight baseline manifest.json.",
    )
    parser.add_argument(
        "--in-flight-run-id",
        default=os.environ.get("PANTHEON_DEV_ARTIFACT_RUN_ID", ""),
        help="In-flight GitHub Actions run ID.",
    )
    parser.add_argument(
        "--in-flight-attempt",
        default=os.environ.get("PANTHEON_DEV_ARTIFACT_ATTEMPT", ""),
        help="In-flight GitHub Actions run attempt.",
    )
    parser.add_argument(
        "--in-flight-candidate-id",
        default=os.environ.get("PANTHEON_DEV_ARTIFACT_CANDIDATE_ID", ""),
        help="In-flight release candidate ID.",
    )
    parser.add_argument(
        "--in-flight-backend-sha",
        default=(
            os.environ.get("PANTHEON_DEV_ARTIFACT_CANDIDATE_BACKEND_SHA")
            or os.environ.get("PANTHEON_DEPLOY_SHA")
            or ""
        ),
        help="In-flight candidate backend SHA.",
    )
    parser.add_argument(
        "--in-flight-previous-backend-sha",
        default=(
            os.environ.get("PANTHEON_DEV_ARTIFACT_PREVIOUS_BACKEND_SHA")
            or os.environ.get("PANTHEON_DEV_ROLLBACK_BACKEND_SHA")
            or ""
        ),
        help="In-flight rollback baseline backend SHA.",
    )
    parser.add_argument(
        "--keep-accepted",
        type=int,
        default=int(os.environ.get("PANTHEON_DEV_ARTIFACT_KEEP_ACCEPTED", str(DEFAULT_KEEP_ACCEPTED_RELEASES))),
        help="Number of most recent accepted releases to keep (default: 3).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.environ.get("PANTHEON_DEV_ARTIFACT_RETENTION_DRY_RUN", "").lower() in {"1", "true", "yes"},
        help="Calculate and log deletions without removing any files.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit full JSON RetentionResult to stdout.",
    )
    parser.add_argument(
        "--check-disk-path",
        type=Path,
        default=None,
        help="Optional filesystem path to run post-retention free disk preflight against.",
    )
    parser.add_argument(
        "--min-free-disk-gb",
        default=os.environ.get("PANTHEON_DEV_DEPLOY_MIN_FREE_DISK_GB", str(DEFAULT_MIN_FREE_DISK_GB)),
        help="Minimum free disk space floor on deploy filesystem (default: 25GB).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    fe_live_link = args.fe_live_link
    if fe_live_link is None and os.environ.get("PANTHEON_DEV_FE_ROOT"):
        fe_live_link = Path(os.environ["PANTHEON_DEV_FE_ROOT"])

    served_backend_sha = args.served_backend_sha or args.in_flight_previous_backend_sha
    served_frontend_sha = (
        args.served_frontend_sha
        or os.environ.get("PANTHEON_DEV_ARTIFACT_PREVIOUS_FRONTEND_SHA", "")
    )

    retention = apply_release_artifact_retention(
        args.artifact_root,
        fe_live_link=fe_live_link,
        served_manifest_path=args.served_manifest,
        served_backend_sha=served_backend_sha,
        served_frontend_sha=served_frontend_sha,
        served_fe_target=args.served_fe_target,
        served_baseline=args.served_baseline,
        in_flight_baseline=args.in_flight_baseline,
        in_flight_manifest=args.in_flight_manifest,
        in_flight_run_id=args.in_flight_run_id,
        in_flight_attempt=args.in_flight_attempt,
        in_flight_candidate_id=args.in_flight_candidate_id,
        in_flight_backend_sha=args.in_flight_backend_sha,
        in_flight_previous_backend_sha=args.in_flight_previous_backend_sha,
        keep_accepted=args.keep_accepted,
        dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps(retention.to_dict(), sort_keys=True))
    else:
        mode = "dry-run" if retention.dry_run else "applied"
        print(
            f"[release-artifact-retention] mode={mode} "
            f"deleted_count={retention.deleted_count} deleted_bytes={retention.deleted_bytes} "
            f"(images={retention.deleted_images_count}/{retention.deleted_images_bytes}B "
            f"compose={retention.deleted_compose_count}/{retention.deleted_compose_bytes}B "
            f"baselines={retention.deleted_baselines_count}/{retention.deleted_baselines_bytes}B) "
            f"kept_baselines={len(retention.kept_baselines)} "
            f"kept_images={len(retention.kept_images)} "
            f"kept_compose={len(retention.kept_compose)}"
        )
        print(json.dumps(retention.to_summary_dict(), sort_keys=True))

    if args.check_disk_path is not None:
        override_raw = os.environ.get("PANTHEON_DEV_DEPLOY_FREE_DISK_BYTES", "").strip()
        override_bytes = int(override_raw) if override_raw else None
        preflight = check_disk_preflight(
            args.check_disk_path,
            min_free_gb=args.min_free_disk_gb,
            free_bytes_override=override_bytes,
        )
        if not preflight.ok:
            print(f"[release-artifact-retention] ERROR: {preflight.message}", file=sys.stderr)
            return 1
        if not args.json:
            print(f"[release-artifact-retention] {preflight.message}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
