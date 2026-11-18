"""The documented host layout (deploy/collector/. copied flat into /opt/pantheon-collector) can build the image."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

COLLECTOR_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = COLLECTOR_DIR.parent.parent
SHARED = REPO_ROOT / "services" / "source_ingestion" / "security_listing.py"


def _host(tmp_path: Path) -> Path:
    """Reproduce `cp -a deploy/collector/. /opt/pantheon-collector/` (tests/ and caches are not needed but harmless)."""
    host = tmp_path / "opt" / "pantheon-collector"
    shutil.copytree(COLLECTOR_DIR, host, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    return host


def _compose_build(host: Path) -> tuple[Path, Path]:
    text = (host / "docker-compose.yml").read_text(encoding="utf-8")
    block = re.search(r"collector-worker:\n\s+build:\n(.*?)\n\s+image:", text, re.S)
    assert block, "collector-worker build block missing"
    ctx = re.search(r"^\s+context:\s*(\S+)", block.group(1), re.M).group(1)
    df = re.search(r"^\s+dockerfile:\s*(\S+)", block.group(1), re.M).group(1)
    return (host / ctx).resolve(), Path(df)


def test_build_context_is_inside_host_dir(tmp_path: Path) -> None:
    host = _host(tmp_path)
    ctx, df = _compose_build(host)
    assert ctx == host.resolve(), "build context must be /opt/pantheon-collector itself, not a parent"
    assert (ctx / df).is_file()
    assert (host / "systemd" / "pantheon-collector@.service").read_text().count("WorkingDirectory=/opt/pantheon-collector") == 1


def test_every_dockerfile_copy_source_exists_in_host_layout(tmp_path: Path) -> None:
    host = _host(tmp_path)
    ctx, df = _compose_build(host)
    sources = []
    for line in (ctx / df).read_text(encoding="utf-8").splitlines():
        m = re.match(r"COPY (?!--)(.+)$", line)
        if m:
            *srcs, _dest = m.group(1).split()
            sources.extend(srcs)
    assert sources
    for s in sources:
        assert not s.startswith("/") and ".." not in Path(s).parts, f"COPY source escapes context: {s}"
        assert (ctx / s).exists(), f"COPY source missing in host layout: {s}"


def test_host_layout_listing_module_identical_to_shared(tmp_path: Path) -> None:
    host = _host(tmp_path)
    ctx, df = _compose_build(host)
    m = re.search(r"^COPY (\S+security_listing\.py) /app/services/source_ingestion/security_listing\.py$", (ctx / df).read_text(), re.M)
    assert m
    assert (ctx / m.group(1)).read_bytes() == SHARED.read_bytes()


def test_drift_is_detected(tmp_path: Path) -> None:
    host = _host(tmp_path)
    vendored = host / "vendor" / "security_listing.py"
    vendored.write_bytes(vendored.read_bytes() + b"\n# drift\n")
    assert vendored.read_bytes() != SHARED.read_bytes()


def test_flat_star_copy_would_miss_vendor_only_if_not_dot_copy(tmp_path: Path) -> None:
    # `cp -a deploy/collector/.` copies subdirectories (vendor/) recursively
    out = tmp_path / "dest"
    out.mkdir()
    subprocess.run(["cp", "-a", f"{COLLECTOR_DIR}/.", str(out)], check=True)
    assert (out / "vendor" / "security_listing.py").is_file()
