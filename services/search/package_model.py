"""Build-time: fetch the manifest-pinned model files into one flat directory and verify sha256.

    python -m services.search.package_model /opt/search/models

Runs only while building the image; the running service stays offline.
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

MANIFEST = Path(__file__).with_name("model-manifest.json")


def package(dest: Path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    dest.mkdir(parents=True, exist_ok=True)
    for name, spec in manifest["files"].items():
        url = manifest["source_url_template"].format(revision=manifest["revision"], source_path=spec["source_path"])
        digest = hashlib.sha256()
        target = dest / name
        with urllib.request.urlopen(url, timeout=600) as resp, target.open("wb") as out:
            while chunk := resp.read(1 << 20):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != spec["sha256"]:
            target.unlink()
            raise SystemExit(f"checksum mismatch for {name}")


if __name__ == "__main__":
    package(Path(sys.argv[1]))
