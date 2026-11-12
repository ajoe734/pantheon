"""Run the real repository Caddyfile in a caddy container and prove token check precedes file serving."""

from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

COLLECTOR_DIR = Path(__file__).resolve().parent.parent
IMAGE = "caddy:2.8-alpine"
TOKEN = "test-token-0123456789"


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True)
    return r.returncode == 0


pytestmark = pytest.mark.skipif(not _docker_ok(), reason=f"docker with {IMAGE} unavailable")


_CONTAINER: list[str] = []


def _req(path: str, token: str | None = None, method: str = "GET") -> tuple[int, str]:
    """Request from inside the container (busybox wget) so the suite's host network guard stays intact."""
    cmd = ["docker", "exec", _CONTAINER[0], "wget", "-q", "-S", "-O", "-", "-T", "5"]
    if token is not None:
        cmd += ["--header", f"Authorization: Bearer {token}"]
    if method == "POST":
        cmd += ["--post-data", ""]
    cmd.append(f"http://127.0.0.1:8080{path}")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    m = re.search(r"HTTP/1\.\d (\d{3})", r.stderr)
    if not m:
        raise OSError(r.stderr)
    return int(m.group(1)), r.stdout


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data = tmp_path_factory.mktemp("srv")
    (data / "status.json").write_text('{"ok":true}')
    (data / "bundles").mkdir()
    (data / "bundles" / "b1.json").write_text('{"bundle":1}')
    (data / "secret.txt").write_text("nope")
    for p in [data, data / "bundles", *data.rglob("*")]:
        p.chmod(0o755 if p.is_dir() else 0o644)
    name = f"caddy-auth-test-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", name,
            "-e", "COLLECTOR_DOMAIN=http://:8080",
            "-e", f"COLLECTOR_BUNDLE_TOKEN={TOKEN}",
            "-v", f"{COLLECTOR_DIR / 'Caddyfile'}:/etc/caddy/Caddyfile:ro",
            "-v", f"{data}:/srv/collector:ro",
            IMAGE,
        ],
        check=True, capture_output=True,
    )
    _CONTAINER[:] = [name]
    try:
        for _ in range(100):
            try:
                _req("/nothing")
                break
            except OSError:
                continue
        yield
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.mark.parametrize("path", ["/status.json", "/bundles/b1.json"])
def test_token_enforced_before_file_server(server, path):
    assert _req(path)[0] == 401
    assert _req(path, "wrong")[0] == 401
    status, body = _req(path, TOKEN)
    assert status == 200 and body.startswith("{")


def test_unauthenticated_never_leaks_content(server):
    status, body = _req("/status.json")
    assert status == 401 and "ok" not in body


def test_other_paths_404_and_writes_405(server):
    assert _req("/secret.txt", TOKEN)[0] == 404
    assert _req("/", TOKEN)[0] == 404
    assert _req("/status.json", TOKEN, "POST")[0] == 405
