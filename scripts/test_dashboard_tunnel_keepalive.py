#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_inline_capture_writes_quick_tunnel_url(tmp_path: Path) -> None:
    source = (ROOT / "scripts" / "dashboard_tunnel_keepalive.sh").read_text(encoding="utf-8")
    program = source.split("python3 -c '\n", 1)[1].split("\n' \"${URL_FILE}\"", 1)[0]
    url_file = tmp_path / "dashboard.url"

    result = subprocess.run(
        [sys.executable, "-c", program, str(url_file), "/dashboard/"],
        input="Tunnel ready at https://quiet-river.trycloudflare.com\n",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert url_file.read_text(encoding="utf-8") == (
        "https://quiet-river.trycloudflare.com/dashboard/"
    )
