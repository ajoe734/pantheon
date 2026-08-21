#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BFF_DIR = ROOT / "services" / "control-plane" / "bff"
BFF_REQUIREMENTS = BFF_DIR / "requirements.txt"
DEFAULT_VENV_DIR = ROOT / ".venv-bff"
VENV_MARKER = ".pantheon-bff-requirements.sha256"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

PY_COMPILE_TARGETS = [
    "services/control-plane/bff/main.py",
    "services/control-plane/bff/read_store.py",
    "services/frontend/sse_reconciler.py",
    "services/frontend/adapter.py",
]

BFF_VERIFICATION_STEPS = [
    {
        "name": "py_compile",
        "command": ["python", "-m", "py_compile", *PY_COMPILE_TARGETS],
    },
    {
        "name": "test_command_executor",
        "command": ["python", "services/control-plane/bff/test_command_executor.py"],
    },
    {
        "name": "test_read_store_deployment",
        "command": ["python", "services/control-plane/bff/test_read_store_deployment.py"],
    },
    {
        "name": "test_read_store_incident",
        "command": ["python", "services/control-plane/bff/test_read_store_incident.py"],
    },
    {
        "name": "test_persona_management",
        "command": ["python", "services/control-plane/bff/test_persona_management.py"],
    },
    {
        "name": "test_w3_surfaces",
        "command": ["python", "services/control-plane/bff/test_w3_surfaces.py"],
    },
    {
        "name": "test_w4_remaining_catalog",
        "command": ["python", "services/control-plane/bff/test_w4_remaining_catalog.py"],
    },
    {
        "name": "smoke_test",
        "command": ["python", "services/control-plane/bff/smoke_test.py"],
    },
    {
        "name": "smoke_test_incident",
        "command": ["python", "services/control-plane/bff/smoke_test_incident.py"],
    },
    {
        "name": "http_smoke_test",
        "command": ["python", "services/control-plane/bff/http_smoke_test.py"],
    },
]

GENERATED_EXACT_PATHS = {
    ".orchestrator/approval-queue.json",
    ".orchestrator/approval-queue.lock",
    ".orchestrator/provider_capabilities.json",
    ".orchestrator/state.json",
    ".orchestrator/supervisor.pid",
    "dashboard-bundle.json",
    "docs-site/ai-activity-log.jsonl",
    "docs-site/ai-status.json",
    "docs-site/approval-queue.json",
    "docs-site/current-work.md",
    "docs-site/dashboard-bundle.json",
    "docs-site/orchestrator-state.json",
}

GENERATED_PREFIXES = (
    ".orchestrator/backups/",
    ".orchestrator/logs/",
    ".venv-bff/",
    "dist/",
)

GENERATED_MARKER_PARTS = {"__pycache__"}
GENERATED_SUFFIXES = (":Zone.Identifier",)
CANONICAL_ROOT_FILES = {
    "AI_COLLABORATION_GUIDE.md",
    "ai-status.json",
    "ai-activity-log.jsonl",
    "current-work.md",
}
EXCLUDED_RELEASE_CATEGORIES = [
    "orchestrator_runtime_state",
    "docs_site_mirrors",
    "dashboard_bundles",
    "local_virtualenv",
    "build_outputs",
    "python_bytecode",
    "provider_local_state",
    "zone_identifier_sidecars",
]
INCLUDED_RELEASE_CATEGORIES = [
    "canonical_docs",
    "machine_readable_state",
    "source_code",
    "dashboard_static_assets",
    "release_docs",
]


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_relpath(path: str | Path) -> str:
    return PurePosixPath(str(path)).as_posix()


def is_generated_ephemeral(path: str | Path) -> bool:
    normalized = normalize_relpath(path)
    if normalized in GENERATED_EXACT_PATHS:
        return True
    if any(normalized.startswith(prefix) for prefix in GENERATED_PREFIXES):
        return True
    if any(normalized.endswith(suffix) for suffix in GENERATED_SUFFIXES):
        return True
    parts = PurePosixPath(normalized).parts
    if any(part in GENERATED_MARKER_PARTS for part in parts):
        return True
    if normalized.endswith((".pyc", ".pyo", ".pyd")):
        return True
    return False


def run_command(
    command: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    verbose: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    if verbose:
        print(f"$ {' '.join(command)}")
    return subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def git_command(
    args: list[str],
    *,
    root: Path = ROOT,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        text=True,
        capture_output=capture_output,
    )


def git_status_entries(root: Path = ROOT) -> list[dict[str, Any]]:
    result = git_command(["status", "--porcelain=v1", "--untracked-files=all"], root=root)
    entries: list[dict[str, Any]] = []
    for raw_line in result.stdout.splitlines():
        if not raw_line:
            continue
        status = raw_line[:2]
        path_text = raw_line[3:]
        if " -> " in path_text:
            path_text = path_text.split(" -> ", 1)[1]
        path_text = normalize_relpath(path_text)
        entries.append(
            {
                "status": status,
                "path": path_text,
                "generated": is_generated_ephemeral(path_text),
                "tracked": status != "??",
            }
        )
    return entries


def tracked_files(root: Path = ROOT) -> list[str]:
    result = git_command(["ls-files", "-z"], root=root)
    return [normalize_relpath(item) for item in result.stdout.split("\x00") if item]


def tracked_generated_files(root: Path = ROOT) -> list[str]:
    return [path for path in tracked_files(root) if is_generated_ephemeral(path)]


def build_release_cleanup_report(root: Path = ROOT) -> dict[str, Any]:
    dirty_entries = git_status_entries(root)
    dirty_generated = [entry for entry in dirty_entries if entry["generated"]]
    dirty_non_generated = [entry for entry in dirty_entries if not entry["generated"]]
    tracked_generated = tracked_generated_files(root)
    return {
        "root": str(root),
        "dirty_entries": dirty_entries,
        "dirty_generated": dirty_generated,
        "dirty_non_generated": dirty_non_generated,
        "tracked_generated_files": tracked_generated,
        "ok": not dirty_entries and not tracked_generated,
    }


def ensure_clean_release_state(root: Path = ROOT) -> dict[str, Any]:
    report = build_release_cleanup_report(root)
    if report["ok"]:
        return report
    raise RuntimeError(format_release_cleanup_report(report))


def format_release_cleanup_report(report: dict[str, Any]) -> str:
    parts: list[str] = ["Release cleanup check failed."]
    tracked_generated = report.get("tracked_generated_files") or []
    dirty_generated = report.get("dirty_generated") or []
    dirty_non_generated = report.get("dirty_non_generated") or []

    if tracked_generated:
        parts.append("Tracked generated files:")
        parts.extend(f"  - {path}" for path in tracked_generated)
    if dirty_generated:
        parts.append("Dirty generated artifacts:")
        parts.extend(f"  - {entry['status']} {entry['path']}" for entry in dirty_generated)
    if dirty_non_generated:
        parts.append("Dirty non-generated worktree entries:")
        parts.extend(f"  - {entry['status']} {entry['path']}" for entry in dirty_non_generated)
    return "\n".join(parts)


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def bootstrap_pip(python_path: Path, *, cwd: Path = ROOT, verbose: bool = True) -> None:
    with tempfile.TemporaryDirectory(prefix="pantheon-get-pip-") as temp_dir:
        get_pip_path = Path(temp_dir) / "get-pip.py"
        if verbose:
            print(f"$ download {GET_PIP_URL} -> {get_pip_path}")
        with urllib.request.urlopen(GET_PIP_URL) as response:
            get_pip_path.write_bytes(response.read())
        run_command([str(python_path), str(get_pip_path)], cwd=cwd, verbose=verbose)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def requirements_hash(path: Path = BFF_REQUIREMENTS) -> str:
    return sha256_text(path.read_text(encoding="utf-8"))


def venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def ensure_bff_venv(
    *,
    root: Path = ROOT,
    venv_dir: Path = DEFAULT_VENV_DIR,
    reinstall: bool = False,
    verbose: bool = True,
) -> dict[str, Any]:
    python_path = venv_python(venv_dir)
    if not python_path.exists():
        try:
            run_command([sys.executable, "-m", "venv", str(venv_dir)], cwd=root, verbose=verbose)
        except subprocess.CalledProcessError:
            run_command(
                [sys.executable, "-m", "venv", "--without-pip", str(venv_dir)],
                cwd=root,
                verbose=verbose,
            )
            bootstrap_pip(python_path, cwd=root, verbose=verbose)
    marker_path = venv_dir / VENV_MARKER
    current_hash = requirements_hash()
    if reinstall or not marker_path.exists():
        install_required = True
    else:
        install_required = marker_path.read_text(encoding="utf-8").strip() != current_hash

    if install_required:
        run_command([str(python_path), "-m", "pip", "install", "--upgrade", "pip"], cwd=root, verbose=verbose)
        run_command([str(python_path), "-m", "pip", "install", "-r", str(BFF_REQUIREMENTS)], cwd=root, verbose=verbose)
        marker_path.write_text(current_hash + "\n", encoding="utf-8")

    return {
        "venv_dir": str(venv_dir),
        "python": str(python_path),
        "requirements_hash": current_hash,
        "installed": install_required,
    }


def run_bff_verification(
    *,
    root: Path = ROOT,
    venv_dir: Path = DEFAULT_VENV_DIR,
    reinstall: bool = False,
    verbose: bool = True,
) -> dict[str, Any]:
    venv_info = ensure_bff_venv(root=root, venv_dir=venv_dir, reinstall=reinstall, verbose=verbose)
    python_bin = venv_info["python"]
    steps: list[dict[str, Any]] = []

    for step in BFF_VERIFICATION_STEPS:
        command = [python_bin if item == "python" else item for item in step["command"]]
        run_command(command, cwd=root, verbose=verbose)
        steps.append(
            {
                "name": step["name"],
                "command": command,
                "status": "passed",
            }
        )

    return {
        "status": "passed",
        "verified_at": iso_now(),
        "venv": venv_info,
        "steps": steps,
    }


def sync_docs_site_state(*, root: Path = ROOT, verbose: bool = True) -> dict[str, Any]:
    run_command(["bash", str(root / "scripts" / "sync-state.sh")], cwd=root, verbose=verbose)
    generated_paths = [
        root / "dashboard-bundle.json",
        root / "docs-site" / "current-work.md",
        root / "docs-site" / "dashboard-bundle.json",
        root / "docs-site" / "orchestrator-state.json",
    ]
    return {
        "status": "passed",
        "synced_at": iso_now(),
        "generated_paths": [str(path.relative_to(root)) for path in generated_paths if path.exists()],
    }


def tracked_blob_hash(root: Path, rel_path: str) -> str | None:
    result = git_command(["ls-files", "-s", "--", rel_path], root=root)
    line = result.stdout.strip()
    if not line:
        return None
    parts = line.split()
    if len(parts) < 2:
        return None
    return parts[1]


def canonical_revision_set(root: Path = ROOT) -> list[dict[str, str]]:
    ai_status_path = root / "ai-status.json"
    payload = json.loads(ai_status_path.read_text(encoding="utf-8"))
    revisions: list[dict[str, str]] = []
    for rel_path in payload.get("canonical_files", []):
        normalized = normalize_relpath(rel_path)
        blob_hash = tracked_blob_hash(root, normalized)
        if blob_hash is None:
            continue
        revisions.append({"path": normalized, "blob": blob_hash})
    return revisions


def copy_release_tree(root: Path, stage_dir: Path) -> list[str]:
    included: list[str] = []
    for rel_path in tracked_files(root):
        if is_generated_ephemeral(rel_path):
            continue
        source = root / rel_path
        if not source.exists():
            continue
        destination = stage_dir / rel_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        included.append(rel_path)
    return included


def build_verification_markdown(
    *,
    release_id: str,
    commit: str,
    verification: dict[str, Any],
    sync_result: dict[str, Any],
    manifest_name: str,
    tarball_name: str,
) -> str:
    lines = [
        "# Pantheon Local Release Verification",
        "",
        f"- Release ID: `{release_id}`",
        f"- Git Commit: `{commit}`",
        f"- Verified At: `{verification['verified_at']}`",
        f"- Manifest: `{manifest_name}`",
        f"- Tarball: `{tarball_name}`",
        "",
        "## BFF Verification",
        "",
    ]
    for step in verification["steps"]:
        lines.append(f"- `{step['name']}`: PASS")
    lines.extend(
        [
            "",
            "## Docs-Site Sync",
            "",
            f"- Status: `{sync_result['status']}`",
            f"- Synced At: `{sync_result['synced_at']}`",
            "- Generated Paths:",
        ]
    )
    for rel_path in sync_result["generated_paths"]:
        lines.append(f"  - `{rel_path}`")
    lines.extend(
        [
            "",
            "## Local Re-Verification",
            "",
            "1. Create or reuse the local BFF venv:",
            "   `python3 scripts/verify_bff_local_release.py`",
            "2. Run the release cleanup gate:",
            "   `python3 scripts/check_release_cleanliness.py`",
            "3. Regenerate dashboard mirrors:",
            "   `bash scripts/sync-state.sh`",
        ]
    )
    return "\n".join(lines) + "\n"


def create_release_artifacts(
    *,
    root: Path = ROOT,
    output_dir: Path | None = None,
    venv_dir: Path = DEFAULT_VENV_DIR,
    verbose: bool = True,
) -> dict[str, Any]:
    ensure_clean_release_state(root)

    verification = run_bff_verification(root=root, venv_dir=venv_dir, verbose=verbose)
    sync_result = sync_docs_site_state(root=root, verbose=verbose)
    ensure_clean_release_state(root)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    release_id = f"pantheon-local-release-{timestamp}"
    output_root = output_dir or (root / "dist")
    output_root.mkdir(parents=True, exist_ok=True)
    tarball_path = output_root / f"{release_id}.tar.gz"
    manifest_path = output_root / f"{release_id}-RELEASE_MANIFEST.json"
    verification_path = output_root / f"{release_id}-VERIFICATION.md"

    commit = git_command(["rev-parse", "HEAD"], root=root).stdout.strip()
    with tempfile.TemporaryDirectory(prefix=f"{release_id}-") as temp_dir:
        stage_dir = Path(temp_dir) / release_id
        stage_dir.mkdir(parents=True, exist_ok=True)
        included_files = copy_release_tree(root, stage_dir)

        manifest = {
            "release_id": release_id,
            "created_at": iso_now(),
            "git_commit": commit,
            "canonical_docs_revision_set": canonical_revision_set(root),
            "bff_verification": verification,
            "docs_site_sync": sync_result,
            "included_artifact_categories": INCLUDED_RELEASE_CATEGORIES,
            "excluded_artifact_categories": EXCLUDED_RELEASE_CATEGORIES,
            "included_files": len(included_files),
            "manifest_path": manifest_path.name,
            "verification_path": verification_path.name,
            "tarball_path": tarball_path.name,
        }
        manifest_json = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        verification_markdown = build_verification_markdown(
            release_id=release_id,
            commit=commit,
            verification=verification,
            sync_result=sync_result,
            manifest_name=manifest_path.name,
            tarball_name=tarball_path.name,
        )

        (stage_dir / "RELEASE_MANIFEST.json").write_text(manifest_json, encoding="utf-8")
        (stage_dir / "VERIFICATION.md").write_text(verification_markdown, encoding="utf-8")

        with tarfile.open(tarball_path, "w:gz") as archive:
            archive.add(stage_dir, arcname=release_id)

        manifest["tarball_sha256"] = file_sha256(tarball_path)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        verification_path.write_text(verification_markdown, encoding="utf-8")

    return {
        "status": "passed",
        "release_id": release_id,
        "tarball_path": str(tarball_path),
        "manifest_path": str(manifest_path),
        "verification_path": str(verification_path),
        "tarball_sha256": manifest["tarball_sha256"],
        "git_commit": commit,
    }
