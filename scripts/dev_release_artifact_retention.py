#!/usr/bin/env python3
"""Prune stale release-artifacts and enforce pre-build disk floor on the dev VM.

Derives the kept set of ``pantheon-ci-deploy/release-artifacts`` entries strictly
from the existing manifests written by ``capture_dev_artifact_baseline.py``,
``dev_release_artifact_driver.py``, and the hosted frontend ``deployment.json``:
1. Currently served release (live FE ``deployment.json`` / rollback baseline).
   Image tars of the currently served backend release are never deleted,
   including after FE-only deploys or when only captured in ``manifest.json``.
2. Last N accepted releases (default 3; baselines with ``candidate-frontend.json``).
3. In-flight candidate release (current deploy run's baseline folder and SHAs).
4. Latest captured rollback baseline (most recent ``baseline-*`` with ``manifest.json``).
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
_BASELINE_DIR_RE = re.compile(r"^baseline-([0-9]+)-([0-9]+)-(.+)$")


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
        if not stat.S_ISREG(info.st_mode):
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


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
            try:
                total += int((root_path / name).lstat().st_size)
            except OSError:
                pass
        for dname in dirnames:
            try:
                dinfo = (root_path / dname).lstat()
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
            if target_path.parent.exists() and not target_path.parent.is_symlink():
                os.chmod(target_path.parent, 0o700)
        except OSError:
            pass
        func(target)

    try:
        if not path.is_symlink():
            os.chmod(path, 0o700)
    except OSError:
        pass
    shutil.rmtree(path, onerror=_onerror)


def _add_image_id_prefix_matches(image_id: Any, existing_images: set[str], out: set[str]) -> None:
    if isinstance(image_id, str) and image_id.strip().startswith("sha256:") and len(image_id.strip()) == 71:
        prefix = image_id.strip().split(":", 1)[1] + "-"
        out.update(img for img in existing_images if img.startswith(prefix) and img.endswith(".tar"))


def _extract_bundle_images(bundle: Any, existing_images: set[str], out: set[str]) -> None:
    if not isinstance(bundle, dict):
        return
    archives = bundle.get("archives")
    if isinstance(archives, dict):
        for img_id, meta in archives.items():
            _add_image_id_prefix_matches(img_id, existing_images, out)
            if isinstance(meta, dict) and isinstance(meta.get("name"), str) and meta["name"].strip():
                out.add(Path(meta["name"].strip()).name)
    elif isinstance(archives, list):
        for item in archives:
            if isinstance(item, dict):
                if isinstance(item.get("name"), str) and item["name"].strip():
                    out.add(Path(item["name"].strip()).name)
                _add_image_id_prefix_matches(item.get("image_id"), existing_images, out)
    services = bundle.get("services")
    if isinstance(services, dict):
        for svc_meta in services.values():
            if isinstance(svc_meta, dict):
                for key in ("archive", "name"):
                    if isinstance(svc_meta.get(key), str) and svc_meta[key].strip():
                        out.add(Path(svc_meta[key].strip()).name)
                _add_image_id_prefix_matches(svc_meta.get("image_id"), existing_images, out)


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
        return any(
            d is not None
            for d in (self.candidate_images, self.candidate_bundle, self.candidate_frontend_captured, self.candidate_frontend)
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
        for doc in (self.candidate_frontend, self.candidate_frontend_captured):
            if isinstance(doc, dict) and isinstance(doc.get("backend_sha"), str) and doc["backend_sha"]:
                return doc["backend_sha"]
        if isinstance(self.candidate_bundle, dict) and isinstance(self.candidate_bundle.get("source_sha"), str):
            return self.candidate_bundle["source_sha"]
        return ""

    @property
    def candidate_frontend_sha(self) -> str:
        ident = self.identity
        if isinstance(ident.get("candidate_frontend_sha"), str) and ident["candidate_frontend_sha"]:
            return ident["candidate_frontend_sha"]
        for doc in (self.candidate_frontend, self.candidate_frontend_captured):
            if isinstance(doc, dict) and isinstance(doc.get("frontend_sha"), str) and doc["frontend_sha"]:
                return doc["frontend_sha"]
        return ""

    @property
    def previous_backend_sha(self) -> str:
        ident = self.identity
        if isinstance(ident.get("previous_backend_sha"), str) and ident["previous_backend_sha"]:
            return ident["previous_backend_sha"]
        if isinstance(self.manifest, dict):
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
        fe = (self.manifest or {}).get("frontend")
        return fe["frontend_sha"] if isinstance(fe, dict) and isinstance(fe.get("frontend_sha"), str) else ""

    @property
    def candidate_fe_target(self) -> str:
        for doc in (self.candidate_frontend, self.candidate_frontend_captured):
            if isinstance(doc, dict) and isinstance(doc.get("target"), str) and doc["target"]:
                return doc["target"]
        return ""

    @property
    def baseline_fe_target(self) -> str:
        fe = (self.manifest or {}).get("frontend")
        return fe["target"] if isinstance(fe, dict) and isinstance(fe.get("target"), str) else ""

    @property
    def is_accepted(self) -> bool:
        return self.candidate_frontend is not None

    @property
    def baseline_sort_key(self) -> tuple[float, int, int, int, str]:
        return (self.captured_at_ts if self.captured_at_ts is not None else -1.0, self.run_id, self.attempt, self.mtime_ns, self.name)

    @property
    def accepted_sort_key(self) -> tuple[float, int, int, int, str]:
        ts = self.accepted_at_ts or self.sealed_at_ts or self.captured_at_ts or -1.0
        return (ts, self.run_id, self.attempt, self.mtime_ns, self.name)

    def baseline_images(self, existing_images: set[str]) -> set[str]:
        images: set[str] = set()
        if isinstance(self.manifest, dict):
            _extract_bundle_images(self.manifest.get("image_bundle"), existing_images, images)
        return images

    def candidate_only_images(self, existing_images: set[str]) -> set[str]:
        images: set[str] = set()
        _extract_bundle_images(self.candidate_bundle, existing_images, images)
        if isinstance(self.candidate_images, dict):
            _extract_bundle_images(self.candidate_images.get("image_bundle"), existing_images, images)
            _extract_bundle_images(self.candidate_images, existing_images, images)
        return images

    def referenced_images(self, existing_images: set[str]) -> set[str]:
        return self.baseline_images(existing_images) | self.candidate_only_images(existing_images)

    def referenced_compose_bundles(self) -> set[str]:
        bundles = {Path(s).name for s in (self.previous_backend_sha, self.candidate_backend_sha) if s}
        compose = (self.manifest or {}).get("compose")
        if isinstance(compose, dict):
            for key in ("archive", "sha256", "source_sha"):
                val = compose.get(key)
                if isinstance(val, str) and val.strip():
                    bundles.add(Path(val.strip()).name)
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

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.to_summary_dict(),
            "kept_baselines": self.kept_baselines,
            "kept_images": self.kept_images,
            "kept_compose": self.kept_compose,
            "deleted_baselines": self.deleted_baselines,
            "deleted_images": self.deleted_images,
            "deleted_compose": self.deleted_compose,
        }


def _scan_baseline_record(entry: Path) -> _BaselineRecord:
    match = _BASELINE_DIR_RE.match(entry.name)
    run_id = int(match.group(1)) if match else 0
    attempt = int(match.group(2)) if match else 0
    candidate_id = match.group(3) if match else ""
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
        ident = (doc or {}).get("identity")
        if isinstance(ident, dict):
            if not run_id and str(ident.get("run_id", "")).isdigit():
                run_id = int(ident["run_id"])
            if not attempt and str(ident.get("attempt", "")).isdigit():
                attempt = int(ident["attempt"])
            if not candidate_id and isinstance(ident.get("candidate_id"), str):
                candidate_id = ident["candidate_id"]
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
        captured_at_ts=_parse_iso_timestamp((manifest or {}).get("captured_at")),
        sealed_at_ts=_parse_iso_timestamp((candidate_images or {}).get("sealed_at")),
        accepted_at_ts=_parse_iso_timestamp(
            (candidate_frontend or {}).get("accepted_at")
            or (candidate_frontend or {}).get("sealed_at")
            or (candidate_frontend or {}).get("verified_at")
        ),
    )


def _read_served_release_identities(
    *,
    artifact_root: Path,
    fe_live_link: Path | None,
    served_backend_sha: str,
    served_frontend_sha: str,
    served_fe_target: str,
) -> list[tuple[str, str, str]]:
    identities: list[tuple[str, str, str]] = []

    def _add(be: str, fe: str, target: str) -> None:
        item = (be.strip(), fe.strip(), target.strip())
        if any(item) and item not in identities:
            identities.append(item)

    _add(served_backend_sha, served_frontend_sha, served_fe_target)
    link = fe_live_link if fe_live_link is not None else (FE_LINK if artifact_root == ROOT else None)
    if link is not None:
        try:
            if link.exists() or link.is_symlink():
                resolved_target = str(link.resolve()) if not (link.is_file() and not link.is_dir()) else ""
                deploy_json = _safe_load_json(link if (link.is_file() and not link.is_dir()) else (link / "deployment.json"))
                if isinstance(deploy_json, dict):
                    be = str(deploy_json.get("bffCommit") or deploy_json.get("bffSourceCommitSha") or deploy_json.get("backend_sha") or "")
                    fe = str(deploy_json.get("frontendSha") or deploy_json.get("commit") or deploy_json.get("frontend_sha") or "")
                    _add(be, fe, str(deploy_json.get("target") or resolved_target))
                elif resolved_target:
                    _add("", "", resolved_target)
        except OSError:
            pass
    return identities


def _prune_entries(entries: list[Path], kept_names: set[str], *, dry_run: bool) -> tuple[list[str], int, int]:
    deleted: list[str] = []
    total_bytes = 0
    for entry in entries:
        if entry.name in kept_names:
            continue
        size = _entry_size_bytes(entry)
        deleted.append(entry.name)
        total_bytes += size
        if not dry_run:
            if entry.is_symlink() or not entry.is_dir():
                entry.unlink()
            else:
                _rmtree_force(entry)
    return deleted, len(deleted), total_bytes


def apply_release_artifact_retention(
    artifact_root: Path = ROOT,
    *,
    fe_live_link: Path | None = None,
    served_backend_sha: str = "",
    served_frontend_sha: str = "",
    served_fe_target: str = "",
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
        kept_by_reason={"currently_served": [], "last_accepted": [], "in_flight_candidate": [], "latest_rollback_baseline": []},
    )
    if not artifact_root.exists() and not artifact_root.is_symlink():
        return result
    if artifact_root.is_symlink() or not artifact_root.is_dir():
        raise RuntimeError(f"release-artifacts root is not a regular directory: {artifact_root}")

    images_dir = artifact_root / "images"
    compose_dir = artifact_root / "compose"
    existing_images = (
        {p.name for p in images_dir.iterdir()}
        if images_dir.exists() and not images_dir.is_symlink() and images_dir.is_dir()
        else set()
    )

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
    accepted_records = sorted((r for r in all_records if r.is_accepted), key=lambda r: r.accepted_sort_key)
    kept_accepted = accepted_records[-keep_accepted:] if keep_accepted > 0 else []
    result.kept_by_reason["last_accepted"] = [r.name for r in kept_accepted]

    baseline_records = sorted((r for r in all_records if r.has_baseline_manifest), key=lambda r: r.baseline_sort_key)
    kept_latest_baseline = [baseline_records[-1]] if baseline_records else []
    result.kept_by_reason["latest_rollback_baseline"] = [r.name for r in kept_latest_baseline]

    in_flight_names: set[str] = set()
    if in_flight_manifest is not None and in_flight_manifest.parent.name.startswith("baseline-"):
        in_flight_names.add(in_flight_manifest.parent.name)
    if in_flight_run_id and in_flight_attempt and in_flight_candidate_id:
        in_flight_names.add(f"baseline-{in_flight_run_id}-{in_flight_attempt}-{in_flight_candidate_id}")

    kept_in_flight = [
        rec
        for rec in all_records
        if rec.name in in_flight_names
        or (
            in_flight_candidate_id
            and rec.candidate_id == in_flight_candidate_id
            and (not in_flight_run_id or str(rec.run_id) == str(in_flight_run_id))
            and (not in_flight_attempt or str(rec.attempt) == str(in_flight_attempt))
        )
    ]
    if not kept_in_flight and not in_flight_names:
        unaccepted = sorted((r for r in all_records if not r.is_accepted), key=lambda r: r.baseline_sort_key)
        if unaccepted and (not accepted_records or unaccepted[-1].baseline_sort_key >= accepted_records[-1].accepted_sort_key):
            kept_in_flight.append(unaccepted[-1])
    result.kept_by_reason["in_flight_candidate"] = sorted(
        {r.name for r in kept_in_flight} | {n for n in in_flight_names if (artifact_root / n).exists()}
    )

    served_identities = _read_served_release_identities(
        artifact_root=artifact_root,
        fe_live_link=fe_live_link,
        served_backend_sha=served_backend_sha,
        served_frontend_sha=served_frontend_sha,
        served_fe_target=served_fe_target,
    )
    if not served_identities:
        for ref in kept_in_flight or kept_latest_baseline:
            if ref.previous_backend_sha or ref.previous_frontend_sha or ref.baseline_fe_target:
                served_identities.append((ref.previous_backend_sha, ref.previous_frontend_sha, ref.baseline_fe_target))
        if not served_identities and accepted_records:
            acc = accepted_records[-1]
            served_identities.append((acc.candidate_backend_sha, acc.candidate_frontend_sha, acc.candidate_fe_target))

    kept_served_names: set[str] = set()
    served_backend_shas: set[str] = set()
    for srv_be, _srv_fe, srv_target in served_identities:
        if srv_be:
            served_backend_shas.add(srv_be)
        matching_candidates = [
            r for r in all_records if (srv_target and r.candidate_fe_target == srv_target) or (srv_be and r.candidate_backend_sha == srv_be)
        ]
        matching_accepted = [r for r in matching_candidates if r.is_accepted]
        for rec in matching_accepted or ([max(matching_candidates, key=lambda r: r.accepted_sort_key)] if matching_candidates else []):
            kept_served_names.add(rec.name)
            if rec.candidate_backend_sha:
                served_backend_shas.add(rec.candidate_backend_sha)
        matching_baselines = [
            r for r in baseline_records if (srv_target and r.baseline_fe_target == srv_target) or (srv_be and r.previous_backend_sha == srv_be)
        ]
        if matching_baselines:
            kept_served_names.add(matching_baselines[-1].name)
            if matching_baselines[-1].previous_backend_sha:
                served_backend_shas.add(matching_baselines[-1].previous_backend_sha)

    result.kept_by_reason["currently_served"] = sorted(kept_served_names)
    kept_baseline_set = {name for names in result.kept_by_reason.values() for name in names}
    result.kept_baselines = sorted(kept_baseline_set)

    kept_image_set: set[str] = set()
    kept_compose_set: set[str] = set()
    for name in result.kept_baselines:
        rec = records_by_name.get(name)
        if rec is not None:
            kept_image_set.update(rec.referenced_images(existing_images))
            kept_compose_set.update(rec.referenced_compose_bundles())
    for rec in all_records:
        if rec.previous_backend_sha and rec.previous_backend_sha in served_backend_shas:
            kept_image_set.update(rec.baseline_images(existing_images))
        if rec.candidate_backend_sha and rec.candidate_backend_sha in served_backend_shas:
            kept_image_set.update(rec.candidate_only_images(existing_images))
    for extra_sha in (*served_backend_shas, served_backend_sha, in_flight_backend_sha, in_flight_previous_backend_sha):
        if extra_sha and extra_sha.strip():
            kept_compose_set.add(Path(extra_sha.strip()).name)

    result.kept_images = sorted(kept_image_set)
    result.kept_compose = sorted(kept_compose_set)

    if images_dir.exists() and not images_dir.is_symlink() and images_dir.is_dir():
        result.deleted_images, result.deleted_images_count, result.deleted_images_bytes = _prune_entries(
            sorted(images_dir.iterdir(), key=lambda p: p.name), kept_image_set, dry_run=dry_run
        )
    if compose_dir.exists() and not compose_dir.is_symlink() and compose_dir.is_dir():
        result.deleted_compose, result.deleted_compose_count, result.deleted_compose_bytes = _prune_entries(
            sorted(compose_dir.iterdir(), key=lambda p: p.name), kept_compose_set, dry_run=dry_run
        )
    result.deleted_baselines, result.deleted_baselines_count, result.deleted_baselines_bytes = _prune_entries(
        all_baseline_entries, kept_baseline_set, dry_run=dry_run
    )

    if not dry_run and result.deleted_compose_count > 0:
        git_repo = repo_root if repo_root is not None else (REPO if artifact_root == ROOT else None)
        if git_repo is not None and (git_repo / ".git").exists():
            try:
                subprocess.run(["git", "-C", str(git_repo), "worktree", "prune"], check=False, capture_output=True, text=True, timeout=30)
            except Exception:
                pass

    result.deleted_count = result.deleted_images_count + result.deleted_compose_count + result.deleted_baselines_count
    result.deleted_bytes = result.deleted_images_bytes + result.deleted_compose_bytes + result.deleted_baselines_bytes
    return result


def parse_disk_size_bytes(value: str | int | float) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if float(value) < 0:
            raise ValueError(f"disk floor cannot be negative: {value}")
        return int(float(value) * GIB_BYTES)
    text = str(value).strip()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)?", text)
    if not match:
        raise ValueError(f"invalid disk size format: {value!r}")
    unit = (match.group(2) or "gb").lower()
    multipliers = {
        "b": 1, "k": 1024, "kb": 1024, "kib": 1024,
        "m": 1024**2, "mb": 1024**2, "mib": 1024**2,
        "g": GIB_BYTES, "gb": GIB_BYTES, "gib": GIB_BYTES,
        "t": 1024 * GIB_BYTES, "tb": 1024 * GIB_BYTES, "tib": 1024 * GIB_BYTES,
    }
    if unit not in multipliers:
        raise ValueError(f"unsupported disk size unit {unit!r} in {value!r}")
    return int(float(match.group(1)) * multipliers[unit])


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
    free_bytes = (
        int(free_bytes_override)
        if free_bytes_override is not None
        else int(os.statvfs(resolved).f_bavail * os.statvfs(resolved).f_frsize)
    )
    free_gb_val, min_free_gb_val = free_bytes / GIB_BYTES, min_free_bytes / GIB_BYTES
    ok = free_bytes >= min_free_bytes
    msg = (
        f"disk preflight passed on {resolved}: available={free_gb_val:.2f}GB ({free_bytes} bytes) >= floor={min_free_gb_val:.2f}GB ({min_free_bytes} bytes)"
        if ok
        else f"insufficient disk space on deploy filesystem ({resolved}): available={free_gb_val:.2f}GB ({free_bytes} bytes) is below required floor={min_free_gb_val:.2f}GB ({min_free_bytes} bytes); aborting deploy before docker build"
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
    parser = argparse.ArgumentParser(prog="dev_release_artifact_retention.py")
    parser.add_argument("--artifact-root", type=Path, default=Path(os.environ.get("PANTHEON_DEV_ARTIFACT_ROOT", str(ROOT))))
    parser.add_argument("--fe-live-link", type=Path, default=None)
    parser.add_argument("--served-backend-sha", default=os.environ.get("PANTHEON_DEV_SERVED_BACKEND_SHA", ""))
    parser.add_argument("--served-frontend-sha", default=os.environ.get("PANTHEON_DEV_SERVED_FRONTEND_SHA", ""))
    parser.add_argument("--served-fe-target", default=os.environ.get("PANTHEON_DEV_SERVED_FE_TARGET", ""))
    parser.add_argument(
        "--in-flight-manifest",
        type=Path,
        default=Path(os.environ["PANTHEON_DEV_ARTIFACT_MANIFEST_PATH"]) if os.environ.get("PANTHEON_DEV_ARTIFACT_MANIFEST_PATH") else None,
    )
    parser.add_argument("--in-flight-run-id", default=os.environ.get("PANTHEON_DEV_ARTIFACT_RUN_ID", ""))
    parser.add_argument("--in-flight-attempt", default=os.environ.get("PANTHEON_DEV_ARTIFACT_ATTEMPT", ""))
    parser.add_argument("--in-flight-candidate-id", default=os.environ.get("PANTHEON_DEV_ARTIFACT_CANDIDATE_ID", ""))
    parser.add_argument(
        "--in-flight-backend-sha",
        default=os.environ.get("PANTHEON_DEV_ARTIFACT_CANDIDATE_BACKEND_SHA") or os.environ.get("PANTHEON_DEPLOY_SHA") or "",
    )
    parser.add_argument(
        "--in-flight-previous-backend-sha",
        default=os.environ.get("PANTHEON_DEV_ARTIFACT_PREVIOUS_BACKEND_SHA") or os.environ.get("PANTHEON_DEV_ROLLBACK_BACKEND_SHA") or "",
    )
    parser.add_argument("--keep-accepted", type=int, default=int(os.environ.get("PANTHEON_DEV_ARTIFACT_KEEP_ACCEPTED", str(DEFAULT_KEEP_ACCEPTED_RELEASES))))
    parser.add_argument("--dry-run", action="store_true", default=os.environ.get("PANTHEON_DEV_ARTIFACT_RETENTION_DRY_RUN", "").lower() in {"1", "true", "yes"})
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check-disk-path", type=Path, default=None)
    parser.add_argument("--min-free-disk-gb", default=os.environ.get("PANTHEON_DEV_DEPLOY_MIN_FREE_DISK_GB", str(DEFAULT_MIN_FREE_DISK_GB)))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    fe_live_link = args.fe_live_link or (Path(os.environ["PANTHEON_DEV_FE_ROOT"]) if os.environ.get("PANTHEON_DEV_FE_ROOT") else None)
    retention = apply_release_artifact_retention(
        args.artifact_root,
        fe_live_link=fe_live_link,
        served_backend_sha=args.served_backend_sha or args.in_flight_previous_backend_sha,
        served_frontend_sha=args.served_frontend_sha or os.environ.get("PANTHEON_DEV_ARTIFACT_PREVIOUS_FRONTEND_SHA", ""),
        served_fe_target=args.served_fe_target,
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
        preflight = check_disk_preflight(
            args.check_disk_path,
            min_free_gb=args.min_free_disk_gb,
            free_bytes_override=int(override_raw) if override_raw else None,
        )
        if not preflight.ok:
            print(f"[release-artifact-retention] ERROR: {preflight.message}", file=sys.stderr)
            return 1
        if not args.json:
            print(f"[release-artifact-retention] {preflight.message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
