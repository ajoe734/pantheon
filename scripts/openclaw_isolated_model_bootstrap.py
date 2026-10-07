#!/usr/bin/env python3
"""Render the isolated-qualification OpenClaw model bootstrap batch.

Reuses the approved shared-model-pool policies from
``openclaw-configure-shared-model-pool.sh`` (model catalog, unresolved
``${CLAUDE_CODE_OAUTH_TOKEN}`` env reference, deny-all structured-extraction
agent) so the isolated namespace never grows a second catalog. The batch is
applied by the gateway container before it starts serving, only when the
isolated compose namespace opts in. It carries no credential value.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

POOL_SCRIPT = Path(__file__).with_name("openclaw-configure-shared-model-pool.sh")
TOKEN_SOURCE_ENV = "PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN"
STRUCTURED_AGENT = {"id": "structured-extraction", "tools": {"deny": ["*"]}}


class BootstrapError(RuntimeError):
    """The isolated provider bootstrap cannot be rendered fail-closed."""


def _script_batch(source: str, name: str) -> list[dict[str, object]]:
    match = re.search(rf"^{name}='(\[.*?\])'$", source, re.DOTALL | re.MULTILINE)
    if match is None:
        raise BootstrapError(f"{POOL_SCRIPT.name} no longer defines {name}")
    return json.loads(match.group(1))


def render_batch(environ: dict[str, str] | None = None) -> list[dict[str, object]]:
    env = os.environ if environ is None else environ
    if not str(env.get(TOKEN_SOURCE_ENV) or "").strip():
        raise BootstrapError(
            f"{TOKEN_SOURCE_ENV} is required; refusing to qualify without the "
            "approved product provider credential"
        )
    source = POOL_SCRIPT.read_text(encoding="utf-8")
    batch = _script_batch(source, "MODEL_POOL_BATCH") + _script_batch(
        source, "CLAUDE_TOKEN_BATCH"
    )
    # A fresh isolated volume has no agents; main and others stay unchanged in
    # the shared script, which only upserts this one deny-all agent.
    batch.append({"path": "agents.list", "value": [STRUCTURED_AGENT]})
    return batch


def main() -> int:
    try:
        print(json.dumps(render_batch(), separators=(",", ":")))
    except (BootstrapError, OSError, ValueError) as exc:
        print(f"[-] isolated model bootstrap failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
