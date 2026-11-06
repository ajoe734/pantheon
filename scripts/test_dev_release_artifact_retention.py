"""Tests for dev release-artifacts retention, Docker builder cache bound, and disk preflight."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import pytest
import yaml

from scripts import dev_release_artifact_retention as retention

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_nonprod_vm.sh"
RETENTION_SCRIPT = ROOT / "scripts" / "dev_release_artifact_retention.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "nonprod-deploy.yml"


def _extract_shell_function(name: str) -> str:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = source.index(f"{name}() {{")
    following = re.search(r"\n[A-Za-z_][A-Za-z_0-9]*\(\) \{", source[start:])
    assert following is not None, f"next function boundary after {name} not found"
    chunk = source[start : start + following.start()]
    return chunk[: chunk.rindex("\n}\n") + 3]


def _write_archive(images_dir: Path, name: str, size_bytes: int) -> Path:
    path = images_dir / name
    path.write_bytes(b"x" * size_bytes)
    path.chmod(0o600)
    return path


def _write_compose_bundle(compose_dir: Path, sha: str, payload: bytes, *, readonly_pack: bool = False) -> Path:
    bundle = compose_dir / sha
    bundle.mkdir(parents=True, exist_ok=True)
    compose_file = bundle / "docker-compose.yml"
    compose_file.write_bytes(payload)
    if readonly_pack:
        git_dir = bundle / ".git" / "objects" / "pack"
        git_dir.mkdir(parents=True, exist_ok=True)
        pack = git_dir / "pack-stale.pack"
        pack.write_bytes(b"p" * 128)
        pack.chmod(0o400)
    return bundle


def _make_baseline_folder(
    artifact_root: Path,
    *,
    run_id: int,
    attempt: int = 1,
    candidate_id: str,
    captured_at: str,
    previous_be: str,
    previous_fe: str,
    candidate_be: str,
    candidate_fe: str,
    baseline_archives: dict[str, str],
    candidate_archives: dict[str, str] | None = None,
    accepted: bool = False,
    accepted_at: str | None = None,
    baseline_fe_target: str = "",
    candidate_fe_target: str = "",
) -> Path:
    folder = artifact_root / f"baseline-{run_id}-{attempt}-{candidate_id}"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    identity = {
        "candidate_id": candidate_id,
        "run_id": str(run_id),
        "attempt": str(attempt),
        "controller_sha": candidate_be,
        "candidate_backend_sha": candidate_be,
        "candidate_frontend_sha": candidate_fe,
        "previous_backend_sha": previous_be,
        "previous_frontend_sha": previous_fe,
    }
    manifest = {
        "schema_version": "pantheon.dev-release-artifact-baseline.v1",
        "captured_at": captured_at,
        "identity": identity,
        "image_bundle": {
            "schema_version": "pantheon.dev-bff-image-bundle.v1",
            "source_sha": previous_be,
            "services": {
                svc: {"image_id": img_id}
                for svc, img_id in zip(
                    ("operator-bff", "agora-interaction-worker", "loop-run-projector-scheduler"),
                    baseline_archives.keys(),
                )
            },
            "archives": {
                img_id: {"name": archive_name, "sha256": "a" * 64, "size": 100}
                for img_id, archive_name in baseline_archives.items()
            },
        },
        "frontend": {
            "target": baseline_fe_target or f"/var/www/pantheon-dev-fe-releases/{previous_fe}",
            "dist_sha256": "b" * 64,
            "manifest_sha256": "c" * 64,
            "frontend_sha": previous_fe,
            "backend_sha": previous_be,
        },
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    (folder / "baseline-compose.yml").write_text("services: {}\n", encoding="utf-8")

    if candidate_archives is not None:
        cand_bundle = {
            "schema_version": "pantheon.dev-bff-image-bundle.v1",
            "source_sha": candidate_be,
            "services": {
                svc: {"image_id": img_id}
                for svc, img_id in zip(
                    ("operator-bff", "agora-interaction-worker", "loop-run-projector-scheduler"),
                    candidate_archives.keys(),
                )
            },
            "archives": {
                img_id: {"name": archive_name, "sha256": "d" * 64, "size": 100}
                for img_id, archive_name in candidate_archives.items()
            },
        }
        (folder / "candidate-bundle.json").write_text(json.dumps(cand_bundle, sort_keys=True), encoding="utf-8")
        cand_images = {
            "schema_version": "pantheon.dev-candidate-image-admission.v2",
            "sealed_at": accepted_at or captured_at,
            "identity": identity,
            "services": cand_bundle["services"],
            "candidate_bundle_sha256": "e" * 64,
        }
        (folder / "candidate-images.json").write_text(json.dumps(cand_images, sort_keys=True), encoding="utf-8")

    if accepted:
        cand_fe = {
            "target": candidate_fe_target or f"/var/www/pantheon-dev-fe-releases/{candidate_fe}",
            "dist_sha256": "f" * 64,
            "manifest_sha256": "1" * 64,
            "frontend_sha": candidate_fe,
            "backend_sha": candidate_be,
        }
        if accepted_at:
            cand_fe["accepted_at"] = accepted_at
        (folder / "candidate-frontend.json").write_text(json.dumps(cand_fe, sort_keys=True), encoding="utf-8")

    return folder


def _build_retention_fixture(tmp_path: Path) -> dict[str, Any]:
    artifact_root = tmp_path / "release-artifacts"
    images_dir = artifact_root / "images"
    compose_dir = artifact_root / "compose"
    controllers_dir = artifact_root / "controllers" / ("9" * 40)
    for d in (artifact_root, images_dir, compose_dir, controllers_dir):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)

    # Controller file must never be touched by retention
    controller_driver = controllers_dir / "dev_release_artifact_driver.py"
    controller_driver.write_text("# pinned controller\n", encoding="utf-8")
    controller_driver.chmod(0o400)

    # Live FE symlink pointing to currently served release (release 101)
    fe_releases = tmp_path / "fe-releases"
    served_fe_dir = fe_releases / "fe-served-101"
    served_fe_dir.mkdir(parents=True, exist_ok=True)
    fe_live_link = tmp_path / "pantheon-dev-fe"
    fe_live_link.symlink_to(served_fe_dir)

    shas = {
        "be_090": "0" * 39 + "9",
        "be_100": "1" * 39 + "0",
        "be_101_served": "1" * 39 + "1",
        "be_102_stale_acc": "1" * 39 + "2",
        "be_102b_failed": "2" * 39 + "b",
        "be_103_acc1": "1" * 39 + "3",
        "be_104_acc2": "1" * 39 + "4",
        "be_104b_failed": "4" * 39 + "b",
        "be_105_acc3": "1" * 39 + "5",
        "be_106_inflight": "1" * 39 + "6",
        "be_107_latest_base": "1" * 39 + "7",
        "be_orphan": "8" * 39 + "8",
    }
    fe_shas = {k: "f" + v[1:] for k, v in shas.items()}

    (served_fe_dir / "deployment.json").write_text(
        json.dumps(
            {
                "bffCommit": shas["be_101_served"],
                "frontendSha": fe_shas["be_101_served"],
                "target": str(served_fe_dir.resolve()),
            }
        ),
        encoding="utf-8",
    )

    # Create compose bundles for all SHAs
    compose_sizes: dict[str, int] = {}
    for key, sha in shas.items():
        readonly = key in {"be_102_stale_acc", "be_102b_failed", "be_orphan"}
        bundle = _write_compose_bundle(
            compose_dir,
            sha,
            f"# compose for {key} ({sha})\nservices: {{}}\n".encode("utf-8"),
            readonly_pack=readonly,
        )
        compose_sizes[sha] = retention._entry_size_bytes(bundle)

    # Create image archives: some shared between kept and stale manifests, some kept-only, some stale-only
    archive_specs = {
        "shared-base-100.tar": 400,         # shared by served (101) AND stale (102)
        "served-101-cand.tar": 510,         # referenced by served (101)
        "stale-102-only.tar": 620,          # referenced ONLY by stale accepted (102)
        "stale-102b-failed-only.tar": 730,  # referenced ONLY by failed candidate (102b)
        "shared-acc-103-and-102b.tar": 840, # shared by kept accepted (103) AND stale failed (102b)
        "acc-103-cand.tar": 950,            # referenced by kept accepted (103)
        "acc-104-cand.tar": 1060,           # referenced by kept accepted (104)
        "stale-104b-failed-only.tar": 1170, # referenced ONLY by failed candidate (104b)
        "acc-105-cand.tar": 1280,           # referenced by kept accepted (105)
        "inflight-106-cand.tar": 1390,      # referenced by in-flight candidate (106)
        "latest-base-107.tar": 1500,        # referenced by latest rollback baseline (107)
        "orphan-unreferenced.tar": 1610,    # referenced by no manifest at all
    }
    for name, size in archive_specs.items():
        _write_archive(images_dir, name, size)

    # 1. Currently served release (101): accepted release older than last 3 accepted, but currently live!
    b101 = _make_baseline_folder(
        artifact_root,
        run_id=101,
        candidate_id="1" * 64,
        captured_at="2026-10-20T01:00:00Z",
        previous_be=shas["be_100"],
        previous_fe=fe_shas["be_100"],
        candidate_be=shas["be_101_served"],
        candidate_fe=fe_shas["be_101_served"],
        baseline_archives={"sha256:" + "1" * 64: "shared-base-100.tar"},
        candidate_archives={"sha256:" + "2" * 64: "served-101-cand.tar"},
        accepted=True,
        accepted_at="2026-10-20T01:15:00Z",
        candidate_fe_target=str(served_fe_dir.resolve()),
    )

    # 2. Stale accepted release (102): 4th newest accepted release (older than 103, 104, 105), not served
    b102 = _make_baseline_folder(
        artifact_root,
        run_id=102,
        candidate_id="2" * 64,
        captured_at="2026-10-21T01:00:00Z",
        previous_be=shas["be_100"],
        previous_fe=fe_shas["be_100"],
        candidate_be=shas["be_102_stale_acc"],
        candidate_fe=fe_shas["be_102_stale_acc"],
        baseline_archives={"sha256:" + "1" * 64: "shared-base-100.tar"},
        candidate_archives={"sha256:" + "3" * 64: "stale-102-only.tar"},
        accepted=True,
        accepted_at="2026-10-21T01:15:00Z",
    )

    # 3. Stale failed release (102b): unaccepted candidate from an old run, shares an archive with 103
    b102b = _make_baseline_folder(
        artifact_root,
        run_id=1020,
        attempt=2,
        candidate_id="a" * 64,
        captured_at="2026-10-21T05:00:00Z",
        previous_be=shas["be_090"],
        previous_fe=fe_shas["be_090"],
        candidate_be=shas["be_102b_failed"],
        candidate_fe=fe_shas["be_102b_failed"],
        baseline_archives={"sha256:" + "4" * 64: "shared-acc-103-and-102b.tar"},
        candidate_archives={"sha256:" + "5" * 64: "stale-102b-failed-only.tar"},
        accepted=False,
    )

    # 4. Accepted release #1 of last 3 (103)
    b103 = _make_baseline_folder(
        artifact_root,
        run_id=103,
        candidate_id="3" * 64,
        captured_at="2026-10-25T01:00:00Z",
        previous_be=shas["be_101_served"],
        previous_fe=fe_shas["be_101_served"],
        candidate_be=shas["be_103_acc1"],
        candidate_fe=fe_shas["be_103_acc1"],
        baseline_archives={"sha256:" + "4" * 64: "shared-acc-103-and-102b.tar"},
        candidate_archives={"sha256:" + "6" * 64: "acc-103-cand.tar"},
        accepted=True,
        accepted_at="2026-10-25T01:15:00Z",
    )

    # 5. Accepted release #2 of last 3 (104)
    b104 = _make_baseline_folder(
        artifact_root,
        run_id=104,
        candidate_id="4" * 64,
        captured_at="2026-10-28T01:00:00Z",
        previous_be=shas["be_103_acc1"],
        previous_fe=fe_shas["be_103_acc1"],
        candidate_be=shas["be_104_acc2"],
        candidate_fe=fe_shas["be_104_acc2"],
        baseline_archives={"sha256:" + "6" * 64: "acc-103-cand.tar"},
        candidate_archives={"sha256:" + "7" * 64: "acc-104-cand.tar"},
        accepted=True,
        accepted_at="2026-10-28T01:15:00Z",
    )

    # 6. Stale failed release (104b): unaccepted candidate between 104 and 105
    b104b = _make_baseline_folder(
        artifact_root,
        run_id=1040,
        candidate_id="b" * 64,
        captured_at="2026-10-29T01:00:00Z",
        previous_be=shas["be_090"],
        previous_fe=fe_shas["be_090"],
        candidate_be=shas["be_104b_failed"],
        candidate_fe=fe_shas["be_104b_failed"],
        baseline_archives={"sha256:" + "7" * 64: "acc-104-cand.tar"},
        candidate_archives={"sha256:" + "8" * 64: "stale-104b-failed-only.tar"},
        accepted=False,
    )

    # 7. Accepted release #3 of last 3 (105)
    b105 = _make_baseline_folder(
        artifact_root,
        run_id=105,
        candidate_id="5" * 64,
        captured_at="2026-11-01T01:00:00Z",
        previous_be=shas["be_104_acc2"],
        previous_fe=fe_shas["be_104_acc2"],
        candidate_be=shas["be_105_acc3"],
        candidate_fe=fe_shas["be_105_acc3"],
        baseline_archives={"sha256:" + "7" * 64: "acc-104-cand.tar"},
        candidate_archives={"sha256:" + "9" * 64: "acc-105-cand.tar"},
        accepted=True,
        accepted_at="2026-11-01T01:15:00Z",
    )

    # 8. In-flight candidate release (106)
    b106 = _make_baseline_folder(
        artifact_root,
        run_id=106,
        candidate_id="6" * 64,
        captured_at="2026-11-03T02:00:00Z",
        previous_be=shas["be_105_acc3"],
        previous_fe=fe_shas["be_105_acc3"],
        candidate_be=shas["be_106_inflight"],
        candidate_fe=fe_shas["be_106_inflight"],
        baseline_archives={"sha256:" + "9" * 64: "acc-105-cand.tar"},
        candidate_archives={"sha256:" + "a" * 64: "inflight-106-cand.tar"},
        accepted=False,
    )

    # 9. Latest captured rollback baseline (107)
    b107 = _make_baseline_folder(
        artifact_root,
        run_id=107,
        candidate_id="7" * 64,
        captured_at="2026-11-03T03:00:00Z",
        previous_be=shas["be_101_served"],
        previous_fe=fe_shas["be_101_served"],
        candidate_be=shas["be_107_latest_base"],
        candidate_fe=fe_shas["be_107_latest_base"],
        baseline_archives={"sha256:" + "b" * 64: "latest-base-107.tar"},
        candidate_archives=None,
        accepted=False,
    )

    # 10. Corrupt/incomplete stale baseline directory
    b_corrupt = artifact_root / ("baseline-99-1-" + "0" * 64)
    b_corrupt.mkdir(mode=0o700)
    (b_corrupt / "manifest.json").write_text("{corrupt json", encoding="utf-8")

    expected_kept_baselines = {b101.name, b103.name, b104.name, b105.name, b106.name, b107.name}
    expected_deleted_baselines = {b102.name, b102b.name, b104b.name, b_corrupt.name}

    expected_kept_images = {
        "shared-base-100.tar",
        "served-101-cand.tar",
        "shared-acc-103-and-102b.tar",
        "acc-103-cand.tar",
        "acc-104-cand.tar",
        "acc-105-cand.tar",
        "inflight-106-cand.tar",
        "latest-base-107.tar",
    }
    expected_deleted_images = {
        "stale-102-only.tar",
        "stale-102b-failed-only.tar",
        "stale-104b-failed-only.tar",
        "orphan-unreferenced.tar",
    }

    expected_kept_compose = {
        shas["be_100"],
        shas["be_101_served"],
        shas["be_103_acc1"],
        shas["be_104_acc2"],
        shas["be_105_acc3"],
        shas["be_106_inflight"],
        shas["be_107_latest_base"],
    }
    expected_deleted_compose = {
        shas["be_090"],
        shas["be_102_stale_acc"],
        shas["be_102b_failed"],
        shas["be_104b_failed"],
        shas["be_orphan"],
    }

    expected_deleted_bytes = (
        sum(archive_specs[name] for name in expected_deleted_images)
        + sum(compose_sizes[sha] for sha in expected_deleted_compose)
        + sum(retention._entry_size_bytes(artifact_root / name) for name in expected_deleted_baselines)
    )

    return {
        "artifact_root": artifact_root,
        "fe_live_link": fe_live_link,
        "controller_driver": controller_driver,
        "in_flight_manifest": b106 / "manifest.json",
        "b101": b101,
        "b103": b103,
        "b104": b104,
        "b105": b105,
        "b106": b106,
        "b107": b107,
        "expected_kept_baselines": expected_kept_baselines,
        "expected_deleted_baselines": expected_deleted_baselines,
        "expected_kept_images": expected_kept_images,
        "expected_deleted_images": expected_deleted_images,
        "expected_kept_compose": expected_kept_compose,
        "expected_deleted_compose": expected_deleted_compose,
        "expected_deleted_bytes": expected_deleted_bytes,
    }


def test_retention_keeps_served_last3_accepted_inflight_and_latest_baseline_and_preserves_shared_archives(
    tmp_path: Path,
) -> None:
    fx = _build_retention_fixture(tmp_path)
    artifact_root: Path = fx["artifact_root"]
    images_dir = artifact_root / "images"
    compose_dir = artifact_root / "compose"

    # 1. Dry-run mode must report exact deletions while deleting nothing on disk.
    dry = retention.apply_release_artifact_retention(
        artifact_root,
        fe_live_link=fx["fe_live_link"],
        in_flight_manifest=fx["in_flight_manifest"],
        keep_accepted=3,
        dry_run=True,
    )
    assert dry.dry_run is True
    assert set(dry.kept_baselines) == fx["expected_kept_baselines"]
    assert set(dry.deleted_baselines) == fx["expected_deleted_baselines"]
    assert set(dry.kept_images) == fx["expected_kept_images"]
    assert set(dry.deleted_images) == fx["expected_deleted_images"]
    assert set(dry.kept_compose) == fx["expected_kept_compose"]
    assert set(dry.deleted_compose) == fx["expected_deleted_compose"]
    assert dry.deleted_count == (
        len(fx["expected_deleted_images"])
        + len(fx["expected_deleted_compose"])
        + len(fx["expected_deleted_baselines"])
    )
    assert dry.deleted_bytes == fx["expected_deleted_bytes"]

    # Verify dry-run deleted nothing on disk
    for img_name in fx["expected_kept_images"] | fx["expected_deleted_images"]:
        assert (images_dir / img_name).exists(), f"dry-run must not delete image {img_name}"
    for comp_sha in fx["expected_kept_compose"] | fx["expected_deleted_compose"]:
        assert (compose_dir / comp_sha).exists(), f"dry-run must not delete compose {comp_sha}"
    for base_name in fx["expected_kept_baselines"] | fx["expected_deleted_baselines"]:
        assert (artifact_root / base_name).exists(), f"dry-run must not delete baseline {base_name}"

    # 2. Apply retention for real: only unreferenced entries are removed; shared archives survive.
    applied = retention.apply_release_artifact_retention(
        artifact_root,
        fe_live_link=fx["fe_live_link"],
        in_flight_manifest=fx["in_flight_manifest"],
        keep_accepted=3,
        dry_run=False,
    )
    assert applied.dry_run is False
    assert applied.deleted_count == dry.deleted_count
    assert applied.deleted_bytes == dry.deleted_bytes
    assert fx["b101"].name in applied.kept_by_reason["currently_served"]
    assert applied.kept_by_reason["last_accepted"] == [
        fx["b103"].name,
        fx["b104"].name,
        fx["b105"].name,
    ]
    assert fx["b106"].name in applied.kept_by_reason["in_flight_candidate"]
    assert applied.kept_by_reason["latest_rollback_baseline"] == [fx["b107"].name]

    # Verify filesystem post-conditions
    remaining_images = {p.name for p in images_dir.iterdir()}
    assert remaining_images == fx["expected_kept_images"]
    assert "shared-base-100.tar" in remaining_images
    assert "shared-acc-103-and-102b.tar" in remaining_images

    remaining_compose = {p.name for p in compose_dir.iterdir()}
    assert remaining_compose == fx["expected_kept_compose"]

    remaining_baselines = {p.name for p in artifact_root.iterdir() if p.name.startswith("baseline-")}
    assert remaining_baselines == fx["expected_kept_baselines"]

    # Controllers directory and 0400 driver must remain untouched
    assert fx["controller_driver"].exists()
    assert fx["controller_driver"].stat().st_mode & 0o777 == 0o400

    # 3. Second run immediately after is an idempotent no-op (0 deletions)
    second = retention.apply_release_artifact_retention(
        artifact_root,
        fe_live_link=fx["fe_live_link"],
        in_flight_manifest=fx["in_flight_manifest"],
        keep_accepted=3,
        dry_run=False,
    )
    assert second.deleted_count == 0
    assert second.deleted_bytes == 0


def test_retention_cli_dry_run_and_apply_logging_and_disk_preflight(tmp_path: Path) -> None:
    fx = _build_retention_fixture(tmp_path)
    artifact_root: Path = fx["artifact_root"]

    # CLI dry-run
    proc_dry = subprocess.run(
        [
            sys.executable,
            str(RETENTION_SCRIPT),
            "--artifact-root",
            str(artifact_root),
            "--fe-live-link",
            str(fx["fe_live_link"]),
            "--in-flight-manifest",
            str(fx["in_flight_manifest"]),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_dry.returncode == 0, proc_dry.stderr
    assert "mode=dry-run" in proc_dry.stdout
    assert f"deleted_count={4 + 5 + 4}" in proc_dry.stdout
    assert f"deleted_bytes={fx['expected_deleted_bytes']}" in proc_dry.stdout
    # Nothing deleted yet
    assert len(list((artifact_root / "images").iterdir())) == 12

    # CLI apply with passing disk preflight (30 GiB available >= 25 GiB floor)
    env_pass = {
        **os.environ,
        "PANTHEON_DEV_DEPLOY_FREE_DISK_BYTES": str(30 * 1024 * 1024 * 1024),
    }
    proc_apply = subprocess.run(
        [
            sys.executable,
            str(RETENTION_SCRIPT),
            "--artifact-root",
            str(artifact_root),
            "--fe-live-link",
            str(fx["fe_live_link"]),
            "--in-flight-manifest",
            str(fx["in_flight_manifest"]),
            "--check-disk-path",
            str(artifact_root),
            "--min-free-disk-gb",
            "25",
        ],
        env=env_pass,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_apply.returncode == 0, proc_apply.stderr
    assert "mode=applied" in proc_apply.stdout
    assert f"deleted_count={4 + 5 + 4}" in proc_apply.stdout
    assert f"deleted_bytes={fx['expected_deleted_bytes']}" in proc_apply.stdout
    assert "disk preflight passed" in proc_apply.stdout
    assert len(list((artifact_root / "images").iterdir())) == 8

    # CLI with failing disk preflight (753 MiB free < 25 GiB floor, matching the 2026-11-03 incident)
    env_fail = {
        **os.environ,
        "PANTHEON_DEV_DEPLOY_FREE_DISK_BYTES": str(753 * 1024 * 1024),
    }
    proc_fail = subprocess.run(
        [
            sys.executable,
            str(RETENTION_SCRIPT),
            "--artifact-root",
            str(artifact_root),
            "--fe-live-link",
            str(fx["fe_live_link"]),
            "--in-flight-manifest",
            str(fx["in_flight_manifest"]),
            "--check-disk-path",
            str(artifact_root),
            "--min-free-disk-gb",
            "25",
        ],
        env=env_fail,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_fail.returncode != 0
    assert "insufficient disk space on deploy filesystem" in proc_fail.stderr
    assert "floor=25.00GB" in proc_fail.stderr


def _write_basic_docker_stub(stub_path: Path, log_path: Path, *, wrap_tool: bool = False) -> None:
    stub_path.write_text(
        f"""#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
payload = {{"tool": "docker", "args": args}} if {wrap_tool!r} else args
with open({str(log_path)!r}, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(payload) + "\\n")
img_id = "sha256:" + ("1" * 64)
if args[:2] == ["ps", "-a"]:
    if "-q" in args:
        print("cid-bff-1")
    else:
        print("cid-bff-1|operator-bff|pantheon-operator-bff-1")
elif args[:1] == ["inspect"]:
    print(img_id)
elif args[:2] == ["image", "inspect"]:
    print(img_id)
elif args[:2] == ["image", "ls"]:
    print(f"pantheon-protect/operator-bff:{{'1'*12}}|{{img_id}}")
""",
        encoding="utf-8",
    )
    stub_path.chmod(0o755)


def test_deploy_nonprod_vm_docker_builder_cache_bounded_by_default_and_image_prune_opt_in(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    docker_log = tmp_path / "docker_calls.jsonl"

    stub_docker = bin_dir / "docker"
    _write_basic_docker_stub(stub_docker, docker_log, wrap_tool=False)

    funcs = "\n\n".join(
        _extract_shell_function(name)
        for name in (
            "docker_storage_diagnostics",
            "run_bounded_docker_prune",
            "protect_dev_compose_container_images_for_builder_prune",
            "prune_dev_docker_storage_for_build",
        )
    )
    runner = tmp_path / "run_prune.sh"
    runner.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}
{funcs}
prune_dev_docker_storage_for_build
""",
        encoding="utf-8",
    )
    runner.chmod(0o755)

    # Case 1: Default posture (PANTHEON_DEV_DOCKER_PRUNE unset/false, keep-storage default 20GB)
    base_env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "PANTHEON_DEPLOY_ENV": "dev",
        "PANTHEON_DEPLOY_COMPONENT": "root",
    }
    base_env.pop("PANTHEON_DEV_DOCKER_PRUNE", None)
    base_env.pop("PANTHEON_DEV_DOCKER_BUILDER_KEEP_STORAGE", None)
    base_env.pop("PANTHEON_DEV_DOCKER_DANGLING_IMAGE_PRUNE", None)

    res_default = subprocess.run(["bash", str(runner)], env=base_env, capture_output=True, text=True, check=False)
    assert res_default.returncode == 0, res_default.stderr
    calls_default = [json.loads(line) for line in docker_log.read_text(encoding="utf-8").splitlines()]
    assert ["tag", "sha256:" + "1" * 64, "pantheon-protect/operator-bff:" + "1" * 12] in calls_default
    assert ["builder", "prune", "-f", "--keep-storage", "20GB"] in calls_default
    # Dangling-only image prune runs by default, after container images are protected.
    assert ["image", "prune", "-f"] in calls_default
    assert calls_default.index(["image", "prune", "-f"]) > calls_default.index(
        ["tag", "sha256:" + "1" * 64, "pantheon-protect/operator-bff:" + "1" * 12]
    )
    assert ["image", "prune", "-af"] not in calls_default
    assert not any(call[:2] == ["container", "prune"] for call in calls_default)
    assert not any(call[:2] == ["system", "prune"] for call in calls_default)
    assert "unused-image prune disabled before root build (PANTHEON_DEV_DOCKER_PRUNE=false)" in res_default.stdout

    # Case 2: Custom keep-storage (e.g. 15GB) with unused-image prune still off
    docker_log.unlink()
    env_custom = {**base_env, "PANTHEON_DEV_DOCKER_BUILDER_KEEP_STORAGE": "15GB"}
    res_custom = subprocess.run(["bash", str(runner)], env=env_custom, capture_output=True, text=True, check=False)
    assert res_custom.returncode == 0, res_custom.stderr
    calls_custom = [json.loads(line) for line in docker_log.read_text(encoding="utf-8").splitlines()]
    assert ["builder", "prune", "-f", "--keep-storage", "15GB"] in calls_custom
    assert ["image", "prune", "-af"] not in calls_custom

    # Case 2b: Dangling-image prune can be switched off on its own
    docker_log.unlink()
    env_no_dangling = {**base_env, "PANTHEON_DEV_DOCKER_DANGLING_IMAGE_PRUNE": "false"}
    res_no_dangling = subprocess.run(
        ["bash", str(runner)], env=env_no_dangling, capture_output=True, text=True, check=False
    )
    assert res_no_dangling.returncode == 0, res_no_dangling.stderr
    calls_no_dangling = [json.loads(line) for line in docker_log.read_text(encoding="utf-8").splitlines()]
    assert ["builder", "prune", "-f", "--keep-storage", "20GB"] in calls_no_dangling
    assert not any(call[:2] == ["image", "prune"] for call in calls_no_dangling)
    assert "dev Docker dangling-image prune disabled before root build" in res_no_dangling.stdout

    # Case 3: Opt-in PANTHEON_DEV_DOCKER_PRUNE=true runs bounded builder prune AND unused-image prune
    docker_log.unlink()
    env_opt_in = {**base_env, "PANTHEON_DEV_DOCKER_PRUNE": "true"}
    res_opt_in = subprocess.run(["bash", str(runner)], env=env_opt_in, capture_output=True, text=True, check=False)
    assert res_opt_in.returncode == 0, res_opt_in.stderr
    calls_opt_in = [json.loads(line) for line in docker_log.read_text(encoding="utf-8").splitlines()]
    assert ["builder", "prune", "-f", "--keep-storage", "20GB"] in calls_opt_in
    # Whole-stack compensation: Compose containers (incl. exited one-shot init
    # containers) must survive the opt-in prune so their images stay in use.
    keep_compose = "label!=com.docker.compose.project=pantheon"
    assert ["container", "prune", "-f", "--filter", keep_compose] in calls_opt_in
    assert ["image", "prune", "-af"] in calls_opt_in
    assert ["system", "prune", "-f", "--filter", keep_compose] in calls_opt_in


def test_builder_prune_protects_untagged_compose_container_images_and_skips_when_unprotectable(
    tmp_path: Path,
) -> None:
    # 1. Static guard: deploy_nonprod_vm.sh must never invoke `docker builder prune -a` or `-af`.
    script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "docker builder prune -a" not in script_text
    assert "docker builder prune -af" not in script_text

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    state_path = tmp_path / "containerd_store.json"
    calls_path = tmp_path / "docker_calls.jsonl"

    img_running = "sha256:" + "1" * 64
    img_stopped = "sha256:" + "2" * 64
    img_stale_protected = "sha256:" + "3" * 64
    img_untagged_cache = "sha256:" + "4" * 64
    img_partial_build_latest = "sha256:" + "9" * 64

    initial_state = {
        "fail_tag": False,
        "containers": [
            {
                "id": "c_running_bff",
                "service": "operator-bff",
                "name": "pantheon-operator-bff-1",
                "status": "running",
                "image": img_running,
                "compose": True,
            },
            {
                "id": "c_stopped_pg",
                "service": "postgres",
                "name": "pantheon-postgres-1",
                "status": "exited",
                "image": img_stopped,
                "compose": True,
            },
        ],
        "images": {
            # Both running and stopped containers lost their :latest tags to a partial build,
            # leaving them completely untagged in the containerd image store before protection!
            img_running: [],
            img_stopped: [],
            img_stale_protected: ["pantheon-protect/operator-bff:" + "3" * 12],
            img_untagged_cache: [],
            img_partial_build_latest: ["pantheon-operator-bff:latest"],
        },
    }
    state_path.write_text(json.dumps(initial_state), encoding="utf-8")

    stub_docker = bin_dir / "docker"
    stub_docker.write_text(
        f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path

state_file = Path({str(state_path)!r})
calls_file = Path({str(calls_path)!r})
args = sys.argv[1:]
with calls_file.open("a", encoding="utf-8") as h:
    h.write(json.dumps(args) + "\\n")

state = json.loads(state_file.read_text(encoding="utf-8"))
containers = state["containers"]
images = state["images"]

if args[:2] == ["ps", "-a"]:
    if "-q" in args:
        for c in containers:
            print(c["id"])
    else:
        for c in containers:
            if c.get("compose"):
                print(f"{{c['id']}}|{{c['service']}}|{{c['name']}}")
    sys.exit(0)

if args[:1] == ["inspect"]:
    cid = args[-1]
    fmt = args[args.index("--format") + 1] if "--format" in args else ""
    for c in containers:
        if c["id"] == cid:
            if fmt == "{{{{.Image}}}}":
                print(c["image"])
            else:
                print(c["service"])
            sys.exit(0)
    sys.exit(1)

if args[:2] == ["image", "inspect"]:
    target = args[-1]
    fmt = args[args.index("--format") + 1] if "--format" in args else ""
    for img_id, tags in images.items():
        if target == img_id or target in tags:
            if fmt == "{{{{.Id}}}}":
                print(img_id)
            else:
                print(json.dumps([{{"Id": img_id, "RepoTags": tags}}]))
            sys.exit(0)
    sys.exit(1)

if args[:1] == ["tag"]:
    if state.get("fail_tag"):
        sys.exit(1)
    src, tag = args[1], args[2]
    if src not in images:
        sys.exit(1)
    if tag not in images[src]:
        images[src].append(tag)
    state_file.write_text(json.dumps(state), encoding="utf-8")
    sys.exit(0)

if args[:2] == ["image", "ls"]:
    for img_id, tags in images.items():
        short_id = img_id.split(":", 1)[1][:12]
        for t in tags:
            print(f"{{t}}|{{short_id}}")
    sys.exit(0)

if args[:2] == ["image", "rm"]:
    target_tag = args[2]
    for img_id, tags in list(images.items()):
        if target_tag in tags:
            tags.remove(target_tag)
    state_file.write_text(json.dumps(state), encoding="utf-8")
    sys.exit(0)

if args[:2] == ["builder", "prune"]:
    # Containerd image store behavior: builder prune deletes every untagged image!
    for img_id in [k for k, tags in images.items() if not tags]:
        del images[img_id]
    state_file.write_text(json.dumps(state), encoding="utf-8")
    sys.exit(0)

if args == ["image", "prune", "-f"]:
    # Dangling-only prune: untagged images that no container references.
    in_use = {{c["image"] for c in containers}}
    for img_id in [k for k, tags in images.items() if not tags and k not in in_use]:
        del images[img_id]
    state_file.write_text(json.dumps(state), encoding="utf-8")
    sys.exit(0)

sys.exit(0)
""",
        encoding="utf-8",
    )
    stub_docker.chmod(0o755)

    funcs = "\n\n".join(
        _extract_shell_function(name)
        for name in (
            "docker_storage_diagnostics",
            "run_bounded_docker_prune",
            "protect_dev_compose_container_images_for_builder_prune",
            "prune_dev_docker_storage_for_build",
        )
    )
    runner = tmp_path / "run_protect_prune.sh"
    runner.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}
{funcs}
prune_dev_docker_storage_for_build
""",
        encoding="utf-8",
    )
    runner.chmod(0o755)

    base_env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "PANTHEON_DEPLOY_ENV": "dev",
        "PANTHEON_DEPLOY_COMPONENT": "root",
    }
    base_env.pop("PANTHEON_DEV_DOCKER_PRUNE", None)
    base_env.pop("PANTHEON_DEV_DOCKER_BUILDER_KEEP_STORAGE", None)
    base_env.pop("PANTHEON_DEV_DOCKER_DANGLING_IMAGE_PRUNE", None)

    # 1a. Dangling-image prune alone (builder prune off) removes the previous build's untagged
    # images but keeps every container image, because protection tags them first.
    env_dangling_only = {**base_env, "PANTHEON_DEV_DOCKER_BUILDER_PRUNE": "false"}
    res_dangling = subprocess.run(
        ["bash", str(runner)], env=env_dangling_only, capture_output=True, text=True, check=False
    )
    assert res_dangling.returncode == 0, res_dangling.stderr
    dangling_state = json.loads(state_path.read_text(encoding="utf-8"))["images"]
    assert img_running in dangling_state
    assert img_stopped in dangling_state
    assert img_partial_build_latest in dangling_state
    assert img_stale_protected not in dangling_state
    assert img_untagged_cache not in dangling_state
    dangling_calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    assert not any(call[:2] == ["builder", "prune"] for call in dangling_calls)
    assert ["image", "prune", "-f"] in dangling_calls

    calls_path.unlink()
    state_path.write_text(json.dumps(initial_state), encoding="utf-8")
    res = subprocess.run(["bash", str(runner)], env=base_env, capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr
    assert "protected 2 compose container image(s) before builder cache prune (stale_protect_tags_removed=1)" in res.stdout

    after_state = json.loads(state_path.read_text(encoding="utf-8"))
    remaining_images = after_state["images"]
    # Both running and stopped containers' previously-untagged images MUST survive builder prune!
    assert img_running in remaining_images
    assert f"pantheon-protect/operator-bff:{'1' * 12}" in remaining_images[img_running]
    assert img_stopped in remaining_images
    assert f"pantheon-protect/postgres:{'2' * 12}" in remaining_images[img_stopped]
    # Unused image with stale protect tag had its stale protect tag removed and was pruned!
    assert img_stale_protected not in remaining_images
    # Unused untagged build cache image was pruned!
    assert img_untagged_cache not in remaining_images

    # 2. Verify keep-storage=0GB is rejected so unbounded prune cannot be triggered via config.
    env_zero = {**base_env, "PANTHEON_DEV_DOCKER_BUILDER_KEEP_STORAGE": "0GB"}
    res_zero = subprocess.run(["bash", str(runner)], env=env_zero, capture_output=True, text=True, check=False)
    assert res_zero.returncode != 0
    assert "unbounded builder prune -a/-af is forbidden" in res_zero.stderr

    # 3. Verify fail-safe skip: if any container image cannot be protected (e.g. docker tag fails
    # or image is uninspectable), builder prune is skipped with a logged reason.
    calls_path.unlink()
    unprotectable_state = {
        "fail_tag": True,
        "containers": initial_state["containers"],
        "images": {img_running: [], img_stopped: []},
    }
    state_path.write_text(json.dumps(unprotectable_state), encoding="utf-8")
    res_skip = subprocess.run(["bash", str(runner)], env=base_env, capture_output=True, text=True, check=False)
    assert res_skip.returncode == 0, res_skip.stderr
    assert "skipping builder cache prune: failed to tag" in res_skip.stdout
    assert (
        "skipped dev Docker builder cache and dangling-image prune because container image protection did not complete"
        in res_skip.stdout
    )
    calls_skip = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    assert not any(call[:2] == ["builder", "prune"] for call in calls_skip)
    assert not any(call[:2] == ["image", "prune"] for call in calls_skip)
    skip_state = json.loads(state_path.read_text(encoding="utf-8"))["images"]
    assert img_running in skip_state and img_stopped in skip_state


def test_retention_never_deletes_currently_served_release_image_tars_across_fe_only_deploy_or_baseline_only_capture(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "release-artifacts"
    images_dir = artifact_root / "images"
    compose_dir = artifact_root / "compose"
    for d in (artifact_root, images_dir, compose_dir):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)

    served_be = "1" * 39 + "1"
    old_fe_at_be_deploy = "f" * 39 + "0"
    new_fe_from_fe_only_deploy = "f" * 39 + "9"

    # Live FE deployment.json was updated by an execute-plans FE-only deploy:
    # bffCommit is still `served_be`, but frontendSha and target moved to `new_fe_from_fe_only_deploy`.
    fe_dir = tmp_path / "fe-releases" / new_fe_from_fe_only_deploy
    fe_dir.mkdir(parents=True, exist_ok=True)
    fe_live_link = tmp_path / "pantheon-dev-fe"
    fe_live_link.symlink_to(fe_dir)
    (fe_dir / "deployment.json").write_text(
        json.dumps(
            {
                "bffCommit": served_be,
                "frontendSha": new_fe_from_fe_only_deploy,
                "target": str(fe_dir.resolve()),
            }
        ),
        encoding="utf-8",
    )

    served_cand_tar = "served-cand-tar.tar"
    served_baseline_only_tar = "served-captured-in-failed-baseline-only.tar"
    stale_failed_cand_tar = "stale-failed-cand-only.tar"
    for name in (served_cand_tar, served_baseline_only_tar, stale_failed_cand_tar):
        _write_archive(images_dir, name, 512)

    # Baseline 101 deployed `served_be` with `old_fe_at_be_deploy`
    b101 = _make_baseline_folder(
        artifact_root,
        run_id=101,
        candidate_id="1" * 64,
        captured_at="2026-10-01T01:00:00Z",
        previous_be="0" * 40,
        previous_fe="0" * 40,
        candidate_be=served_be,
        candidate_fe=old_fe_at_be_deploy,
        baseline_archives={"sha256:" + "a" * 64: served_cand_tar},
        candidate_archives={"sha256:" + "b" * 64: served_cand_tar},
        accepted=True,
        accepted_at="2026-10-01T01:10:00Z",
    )

    # Baseline 102 is a failed deploy that ran while `served_be` was live, capturing
    # `served_baseline_only_tar` in its `manifest.json` image_bundle (`previous_be=served_be`),
    # and producing `stale_failed_cand_tar` in its unaccepted `candidate-bundle.json`.
    b102_failed = _make_baseline_folder(
        artifact_root,
        run_id=102,
        candidate_id="2" * 64,
        captured_at="2026-10-02T01:00:00Z",
        previous_be=served_be,
        previous_fe=old_fe_at_be_deploy,
        candidate_be="2" * 40,
        candidate_fe="e" * 40,
        baseline_archives={"sha256:" + "c" * 64: served_baseline_only_tar},
        candidate_archives={"sha256:" + "d" * 64: stale_failed_cand_tar},
        accepted=False,
    )

    # 4 newer accepted releases (201..204) so 101 is well outside `keep_accepted=3`
    for idx in range(1, 5):
        sha = str(idx + 2) * 40
        tar_name = f"acc-20{idx}.tar"
        _write_archive(images_dir, tar_name, 256)
        _make_baseline_folder(
            artifact_root,
            run_id=200 + idx,
            candidate_id=str(idx + 2) * 64,
            captured_at=f"2026-10-1{idx}T01:00:00Z",
            previous_be=str(idx + 1) * 40,
            previous_fe=str(idx + 1) * 40,
            candidate_be=sha,
            candidate_fe=sha,
            baseline_archives={"sha256:" + str(idx + 2) * 64: tar_name},
            candidate_archives={"sha256:" + str(idx + 2) * 64: tar_name},
            accepted=True,
            accepted_at=f"2026-10-1{idx}T01:10:00Z",
        )

    res = retention.apply_release_artifact_retention(
        artifact_root,
        fe_live_link=fe_live_link,
        keep_accepted=3,
        dry_run=False,
    )

    # Both `b101` (candidate_be == served_be) and `b102_failed` (newest baseline with previous_be == served_be)
    # are protected in `currently_served` despite `deployment.json` having a newer `frontendSha`!
    assert b101.name in res.kept_by_reason["currently_served"]
    assert b102_failed.name in res.kept_by_reason["currently_served"]
    # Both `served_cand_tar` and `served_baseline_only_tar` MUST survive!
    assert served_cand_tar in res.kept_images
    assert served_baseline_only_tar in res.kept_images
    assert (images_dir / served_cand_tar).exists()
    assert (images_dir / served_baseline_only_tar).exists()


def test_deploy_nonprod_vm_pre_build_sequence_stops_before_build_on_insufficient_disk(
    tmp_path: Path,
) -> None:
    # Verify static ordering in deploy_nonprod_vm.sh root block
    script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    root_block = script_text.split("\n  root)\n", 1)[1].split("\n  bff)\n", 1)[0]
    idx_retention = root_block.index("prune_dev_release_artifacts_for_build")
    idx_docker_prune = root_block.index("prune_dev_docker_storage_for_build")
    idx_disk_preflight = root_block.index("check_dev_deploy_disk_preflight")
    idx_build = root_block.index("docker compose -p pantheon -f docker-compose.yml build")
    assert idx_retention < idx_docker_prune < idx_disk_preflight < idx_build

    # Execute the real pre-build shell functions in order and verify build is blocked when disk < floor
    fx = _build_retention_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    events_log = tmp_path / "events.jsonl"

    stub_docker = bin_dir / "docker"
    _write_basic_docker_stub(stub_docker, events_log, wrap_tool=True)

    # Stub df returning 10 GiB available (< 25 GiB default floor)
    avail_10gib = 10 * 1024 * 1024 * 1024
    stub_df = bin_dir / "df"
    stub_df.write_text(
        f"""#!/usr/bin/env bash
if [[ "${{1:-}}" == "-PB1" ]]; then
  printf "Filesystem 1B-blocks Used Available Use%% Mounted on\\n/dev/sda1 207232188416 196494770176 {avail_10gib} 95%% /\\n"
  exit 0
fi
printf "Filesystem Size Used Avail Use%% Mounted on\\n/dev/sda1 193G 183G 10G 95%% /\\n"
""",
        encoding="utf-8",
    )
    stub_df.chmod(0o755)

    funcs = "\n\n".join(
        _extract_shell_function(name)
        for name in (
            "docker_storage_diagnostics",
            "run_bounded_docker_prune",
            "prune_dev_release_artifacts_for_build",
            "protect_dev_compose_container_images_for_builder_prune",
            "prune_dev_docker_storage_for_build",
            "check_dev_deploy_disk_preflight",
        )
    )
    runner = tmp_path / "run_pre_build.sh"
    runner.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[remote-deploy] $*"; }}
error() {{ echo "[remote-deploy] ERROR: $*" >&2; exit 1; }}
{funcs}
prune_dev_release_artifacts_for_build
prune_dev_docker_storage_for_build
check_dev_deploy_disk_preflight
docker compose -p pantheon -f docker-compose.yml build
""",
        encoding="utf-8",
    )
    runner.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "PANTHEON_DEPLOY_ENV": "dev",
        "PANTHEON_DEPLOY_COMPONENT": "root",
        "PANTHEON_DEV_ARTIFACT_ROOT": str(fx["artifact_root"]),
        "PANTHEON_DEV_FE_ROOT": str(fx["fe_live_link"]),
        "PANTHEON_DEV_ARTIFACT_MANIFEST_PATH": str(fx["in_flight_manifest"]),
        "PANTHEON_DEV_ARTIFACT_RETENTION_SCRIPT": str(RETENTION_SCRIPT),
        "PANTHEON_DEV_DEPLOY_DISK_CHECK_PATH": str(tmp_path),
    }
    env.pop("PANTHEON_DEV_DEPLOY_FREE_DISK_BYTES", None)
    env.pop("PANTHEON_DEV_DEPLOY_MIN_FREE_DISK_GB", None)
    env.pop("PANTHEON_DEV_DOCKER_PRUNE", None)

    res_fail = subprocess.run(["bash", str(runner)], env=env, capture_output=True, text=True, check=False)
    assert res_fail.returncode != 0
    assert "deleted_count=13" in res_fail.stdout
    assert "insufficient disk space on deploy filesystem" in res_fail.stderr
    assert "available=10.00GB" in res_fail.stderr
    assert "floor=25.00GB" in res_fail.stderr

    # Verify builder prune ran, but `docker compose ... build` NEVER ran!
    recorded = [json.loads(line)["args"] for line in events_log.read_text(encoding="utf-8").splitlines()]
    assert ["builder", "prune", "-f", "--keep-storage", "20GB"] in recorded
    assert not any("build" in args for args in recorded)

    # Now raise available space in df stub to 28 GiB (>= 25 GiB floor) and prove build proceeds
    avail_28gib = 28 * 1024 * 1024 * 1024
    stub_df.write_text(
        f"""#!/usr/bin/env bash
if [[ "${{1:-}}" == "-PB1" ]]; then
  printf "Filesystem 1B-blocks Used Available Use%% Mounted on\\n/dev/sda1 207232188416 177167400960 {avail_28gib} 86%% /\\n"
  exit 0
fi
printf "Filesystem Size Used Avail Use%% Mounted on\\n/dev/sda1 193G 165G 28G 86%% /\\n"
""",
        encoding="utf-8",
    )
    events_log.unlink()
    res_pass = subprocess.run(["bash", str(runner)], env=env, capture_output=True, text=True, check=False)
    assert res_pass.returncode == 0, res_pass.stderr
    assert "disk preflight passed" in res_pass.stdout
    recorded_pass = [json.loads(line)["args"] for line in events_log.read_text(encoding="utf-8").splitlines()]
    assert ["compose", "-p", "pantheon", "-f", "docker-compose.yml", "build"] in recorded_pass


def test_nonprod_deploy_workflow_keeps_unused_image_prune_opt_in() -> None:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    deploy_steps = workflow["jobs"]["deploy-dev"]["steps"]
    deploy_step = next(step for step in deploy_steps if step.get("id") == "deploy")
    env = deploy_step["env"]
    assert env["PANTHEON_DEV_DOCKER_PRUNE"] == "${{ vars.PANTHEON_DEV_DOCKER_PRUNE || 'false' }}"
    assert env["PANTHEON_DEV_DOCKER_BUILDER_KEEP_STORAGE"] == "${{ vars.PANTHEON_DEV_DOCKER_BUILDER_KEEP_STORAGE || '20GB' }}"
    assert env["PANTHEON_DEV_DEPLOY_MIN_FREE_DISK_GB"] == "${{ vars.PANTHEON_DEV_DEPLOY_MIN_FREE_DISK_GB || '25' }}"
    assert env["PANTHEON_DEV_ARTIFACT_KEEP_ACCEPTED"] == "${{ vars.PANTHEON_DEV_ARTIFACT_KEEP_ACCEPTED || '3' }}"


def test_retention_nonexistent_root_and_symlink_root_guard(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    res = retention.apply_release_artifact_retention(missing)
    assert res.deleted_count == 0
    assert res.deleted_bytes == 0

    real_dir = tmp_path / "real"
    real_dir.mkdir()
    sym_dir = tmp_path / "sym"
    sym_dir.symlink_to(real_dir)
    with pytest.raises(RuntimeError, match="not a regular directory"):
        retention.apply_release_artifact_retention(sym_dir)


def test_retention_v1_candidate_images_without_bundle_keeps_matching_prefix_tar(tmp_path: Path) -> None:
    artifact_root = tmp_path / "release-artifacts"
    images_dir = artifact_root / "images"
    compose_dir = artifact_root / "compose"
    for d in (artifact_root, images_dir, compose_dir):
        d.mkdir(mode=0o700, parents=True)

    img_hex = "a" * 64
    v1_tar = f"{img_hex}-" + "b" * 64 + ".tar"
    stale_tar = "c" * 64 + "-" + "d" * 64 + ".tar"
    _write_archive(images_dir, v1_tar, 256)
    _write_archive(images_dir, stale_tar, 512)

    folder = artifact_root / ("baseline-500-1-" + "e" * 64)
    folder.mkdir(mode=0o700)
    cand_v1 = {
        "schema_version": "pantheon.dev-candidate-image-admission.v1",
        "sealed_at": "2026-11-03T01:00:00Z",
        "identity": {
            "candidate_id": "e" * 64,
            "run_id": "500",
            "attempt": "1",
            "controller_sha": "1" * 40,
            "candidate_backend_sha": "1" * 40,
            "candidate_frontend_sha": "2" * 40,
            "previous_backend_sha": "3" * 40,
            "previous_frontend_sha": "4" * 40,
        },
        "services": {
            "operator-bff": {"image_id": f"sha256:{img_hex}"},
        },
    }
    (folder / "candidate-images.json").write_text(json.dumps(cand_v1), encoding="utf-8")
    (folder / "candidate-frontend.json").write_text(
        json.dumps({"backend_sha": "1" * 40, "frontend_sha": "2" * 40, "target": "/var/www/fe"}),
        encoding="utf-8",
    )

    res = retention.apply_release_artifact_retention(artifact_root)
    assert v1_tar in res.kept_images
    assert stale_tar in res.deleted_images
    assert (images_dir / v1_tar).exists()
    assert not (images_dir / stale_tar).exists()


def test_parse_disk_size_bytes_and_preflight_validation(tmp_path: Path) -> None:
    assert retention.parse_disk_size_bytes(25) == 25 * 1024**3
    assert retention.parse_disk_size_bytes("25GB") == 25 * 1024**3
    assert retention.parse_disk_size_bytes("25GiB") == 25 * 1024**3
    assert retention.parse_disk_size_bytes("512MB") == 512 * 1024**2
    with pytest.raises(ValueError):
        retention.parse_disk_size_bytes(-1)
    with pytest.raises(ValueError):
        retention.parse_disk_size_bytes("invalid-size")

    ok_res = retention.check_disk_preflight(
        tmp_path,
        min_free_gb="25GB",
        free_bytes_override=26 * 1024**3,
    )
    assert ok_res.ok is True
    assert "disk preflight passed" in ok_res.message

    fail_res = retention.check_disk_preflight(
        tmp_path,
        min_free_gb="25GB",
        free_bytes_override=24 * 1024**3,
    )
    assert fail_res.ok is False
    assert "insufficient disk space on deploy filesystem" in fail_res.message


def test_deploy_nonprod_vm_syntax_and_dry_run_defaults() -> None:
    syntax = subprocess.run(["bash", "-n", str(DEPLOY_SCRIPT)], capture_output=True, text=True, check=False)
    assert syntax.returncode == 0, syntax.stderr

    dry_run = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "dev",
            "--sha",
            "95a1455e3dc1a275b8d541fd2c432c3971013308",
            "--project-id",
            "pantheon-dev-20260902",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert dry_run.returncode == 0, dry_run.stderr
    assert "dev_docker_prune=false" in dry_run.stdout
    assert "dev_docker_builder_keep_storage=20GB" in dry_run.stdout
    assert "dev_docker_dangling_image_prune=true" in dry_run.stdout
    assert "dev_deploy_min_free_disk_gb=25" in dry_run.stdout

