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
API_KEY_SOURCE_ENV = "PANTHEON_OPENCLAW_ANTHROPIC_API_KEY"
WORKSPACE_SOURCE_ENV = "PANTHEON_OPENCLAW_ANTHROPIC_WORKSPACE_ID"
TOKEN_SOURCE_ENV = "PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN"
OPENAI_OAUTH_TOKEN_SOURCE_ENV = "PANTHEON_OPENCLAW_OPENAI_OAUTH_TOKEN"
OPENAI_API_KEY_SOURCE_ENV = "PANTHEON_OPENCLAW_OPENAI_API_KEY"
REQUIRE_OPENAI_AUTH_ENV = "PANTHEON_OPENCLAW_REQUIRE_OPENAI_AUTH"
OPENAI_CONTAINER_OAUTH_TOKEN_ENV = "OPENCLAW_OPENAI_OAUTH_TOKEN"
OPENAI_CONTAINER_API_KEY_ENV = "OPENAI_API_KEY"

ANTHROPIC_WORKSPACE_BATCH: list[dict[str, object]] = [
    {
        "path": "models.providers.anthropic.headers.anthropic-workspace-id",
        "value": "${ANTHROPIC_WORKSPACE_ID}",
    }
]
OPENAI_OAUTH_TOKEN_BATCH: list[dict[str, object]] = [
    {
        "path": "auth.profiles.openai:default",
        "value": {"provider": "openai", "mode": "token"},
    },
    {
        "path": "models.providers.openai",
        "value": {
            "baseUrl": "https://chatgpt.com/backend-api/codex",
            "api": "openai-chatgpt-responses",
            "auth": "token",
            "apiKey": {
                "source": "env",
                "provider": "default",
                "id": OPENAI_CONTAINER_OAUTH_TOKEN_ENV,
            },
            "models": [],
        },
    },
]
OPENAI_API_KEY_BATCH: list[dict[str, object]] = [
    {
        "path": "auth.profiles.openai:default",
        "value": {"provider": "openai", "mode": "api_key"},
    },
    {
        "path": "models.providers.openai",
        "value": {
            "baseUrl": "https://api.openai.com/v1",
            "api": "openai-responses",
            "auth": "api-key",
            "apiKey": {
                "source": "env",
                "provider": "default",
                "id": OPENAI_CONTAINER_API_KEY_ENV,
            },
            "models": [],
        },
    },
]
STRUCTURED_AGENT = {
    "id": "structured-extraction",
    "tools": {"deny": ["*"], "exec": {"security": "deny", "ask": "always"}},
}
MAIN_AGENT = {"id": "main"}  # existing ordinary consumer identity; native policy

_PLACEHOLDER_EXACT = frozenset(
    {
        "placeholder",
        "dummy",
        "fake",
        "changeme",
        "todo",
        "redacted",
        "null",
        "none",
        "undefined",
        "false",
        "true",
        "test",
        "example",
    }
)
_PLACEHOLDER_SUBSTRINGS = (
    "placeholder",
    "changeme",
    "your_api_key",
    "your-api-key",
    "your_token",
    "your-token",
    "replace_me",
    "replace-me",
    "redacted",
    "example_key",
    "example_token",
    "dummy_key",
    "dummy_token",
    "fake_key",
    "fake_token",
)


class BootstrapError(RuntimeError):
    """The isolated provider bootstrap cannot be rendered fail-closed."""


def _script_batch(source: str, name: str) -> list[dict[str, object]]:
    match = re.search(rf"^{name}='(\[.*?\])'$", source, re.DOTALL | re.MULTILINE)
    if match is None:
        raise BootstrapError(f"{POOL_SCRIPT.name} no longer defines {name}")
    return json.loads(match.group(1))


def _validate_credential(name: str, raw_value: str) -> str:
    if raw_value != "" and not raw_value.strip():
        raise BootstrapError(f"{name} must not be whitespace-only")
    value = raw_value.strip()
    if not value:
        return ""
    if re.search(r"\s", value):
        raise BootstrapError(f"{name} contains invalid whitespace or control characters")
    lower = value.lower()
    if value.startswith(("{", "[")) or "refresh_token" in lower:
        raise BootstrapError(
            f"{name} must be a single bearer token or API key; raw JSON auth stores "
            "and single-use refresh_token values are refused"
        )
    if (
        len(value) < 8
        or value.startswith(("<", "${"))
        or value.endswith(">")
        or lower in _PLACEHOLDER_EXACT
        or any(marker in lower for marker in _PLACEHOLDER_SUBSTRINGS)
    ):
        raise BootstrapError(f"{name} rejected as invalid or placeholder value")
    return value


def _require_openai_enabled(env: dict[str, str] | os._Environ[str]) -> bool:
    raw = str(env.get(REQUIRE_OPENAI_AUTH_ENV) or "").strip().lower()
    if raw in ("", "0", "false", "no"):
        return False
    if raw in ("1", "true", "yes"):
        return True
    raise BootstrapError(
        f"{REQUIRE_OPENAI_AUTH_ENV} must be a boolean flag (0/1/true/false)"
    )


def render_batch(environ: dict[str, str] | None = None) -> list[dict[str, object]]:
    env = os.environ if environ is None else environ
    missing = [
        name
        for name in (API_KEY_SOURCE_ENV, WORKSPACE_SOURCE_ENV)
        if not str(env.get(name) or "").strip()
    ]
    if missing:
        raise BootstrapError(
            f"{', '.join(missing)} required; refusing to qualify without the "
            "approved product provider API credential and workspace"
        )
    _validate_credential(API_KEY_SOURCE_ENV, str(env.get(API_KEY_SOURCE_ENV) or ""))
    _validate_credential(
        WORKSPACE_SOURCE_ENV, str(env.get(WORKSPACE_SOURCE_ENV) or "")
    )
    claude_token = _validate_credential(
        TOKEN_SOURCE_ENV, str(env.get(TOKEN_SOURCE_ENV) or "")
    )
    openai_oauth_token = _validate_credential(
        OPENAI_OAUTH_TOKEN_SOURCE_ENV,
        str(env.get(OPENAI_OAUTH_TOKEN_SOURCE_ENV) or ""),
    )
    openai_api_key = _validate_credential(
        OPENAI_API_KEY_SOURCE_ENV,
        str(env.get(OPENAI_API_KEY_SOURCE_ENV) or ""),
    )
    if _require_openai_enabled(env) and not (openai_oauth_token or openai_api_key):
        raise BootstrapError(
            f"{OPENAI_OAUTH_TOKEN_SOURCE_ENV} or {OPENAI_API_KEY_SOURCE_ENV} required "
            "when OpenAI native fallback qualification auth is enforced"
        )

    source = POOL_SCRIPT.read_text(encoding="utf-8")
    batch = (
        _script_batch(source, "MODEL_POOL_BATCH")
        + ANTHROPIC_WORKSPACE_BATCH
        + _script_batch(source, "CLAUDE_TOOLSEARCH_BATCH")
    )
    if claude_token:
        batch += _script_batch(source, "CLAUDE_TOKEN_BATCH")
    if openai_oauth_token:
        batch += OPENAI_OAUTH_TOKEN_BATCH
    elif openai_api_key:
        batch += OPENAI_API_KEY_BATCH
    # A fresh isolated volume has no agents; register the existing main identity
    # (no tools override) plus the deny-all structured agent, each exactly once.
    batch.append({"path": "agents.list", "value": [MAIN_AGENT, STRUCTURED_AGENT]})
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
