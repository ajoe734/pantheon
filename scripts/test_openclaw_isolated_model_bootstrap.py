import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import openclaw_isolated_model_bootstrap as bootstrap  # noqa: E402

API_KEY = bootstrap.API_KEY_SOURCE_ENV
WORKSPACE = bootstrap.WORKSPACE_SOURCE_ENV
TOKEN = bootstrap.TOKEN_SOURCE_ENV
OPENAI_OAUTH = bootstrap.OPENAI_OAUTH_TOKEN_SOURCE_ENV
OPENAI_KEY = bootstrap.OPENAI_API_KEY_SOURCE_ENV
REQUIRE_OPENAI = bootstrap.REQUIRE_OPENAI_AUTH_ENV


def test_missing_credential_fails_closed() -> None:
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.render_batch({})
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.render_batch({API_KEY: "sk-ant-test-valid", WORKSPACE: "  "})
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.render_batch({API_KEY: "  ", WORKSPACE: "wrkspc_test_valid"})
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.render_batch(
            {
                API_KEY: "sk-ant-test-valid",
                WORKSPACE: "wrkspc_test_valid",
                REQUIRE_OPENAI: "1",
            }
        )
    result = subprocess.run(
        [sys.executable, bootstrap.__file__], env={}, capture_output=True, text=True
    )
    assert result.returncode == 1 and result.stdout == ""


def test_batch_reuses_approved_pool_and_never_embeds_credential() -> None:
    batch = bootstrap.render_batch(
        {
            API_KEY: "sentinel-api-key-value",
            WORKSPACE: "wrkspc_sentinel_value",
            TOKEN: "sentinel-secret-value",
        }
    )
    values = {str(item["path"]): item["value"] for item in batch}
    assert values["agents.defaults.model.primary"] == "anthropic/claude-opus-4-8"
    assert values["agents.defaults.model.fallbacks"] == [
        "openai/gpt-5.6-sol",
        "openai/gpt-5.5",
    ]
    assert values['agents.defaults.models["anthropic/claude-opus-4-8"]'] == {
        "alias": "opus",
        "agentRuntime": {"id": "openclaw"},
    }
    assert values['agents.defaults.models["openai/gpt-5.6-sol"]'] == {
        "alias": "codex-sol",
        "agentRuntime": {"id": "openclaw"},
    }
    assert values['agents.defaults.models["openai/gpt-5.5"]'] == {
        "alias": "codex-5.5",
        "agentRuntime": {"id": "openclaw"},
    }
    # Unresolved env references only; clearEnv is never disabled.
    assert (
        values["models.providers.anthropic.headers.anthropic-workspace-id"]
        == "${ANTHROPIC_WORKSPACE_ID}"
    )
    assert (
        values['agents.defaults.cliBackends["claude-cli"].env.CLAUDE_CODE_OAUTH_TOKEN']
        == "${CLAUDE_CODE_OAUTH_TOKEN}"
    )
    assert "models.providers.openai" not in values
    assert "auth.profiles.openai:default" not in values
    assert not any("clearEnv" in path for path in values)
    # Both real consumer identities, once each; main keeps native tool policy.
    assert values["agents.list"] == [
        {"id": "main"},
        {"id": "structured-extraction",
         "tools": {"deny": ["*"], "exec": {"security": "deny", "ask": "always"}}},
    ]
    # Same global ToolSearch-only launch as the shared deploy path.
    assert values['agents.defaults.cliBackends["claude-cli"].args'][-2:] == ["--tools", "ToolSearch"]
    assert values['agents.defaults.cliBackends["claude-cli"].resumeArgs'][-4:-2] == ["--tools", "ToolSearch"]
    assert [item["path"] for item in batch].count("agents.list") == 1
    serialized = json.dumps(batch)
    assert "sentinel-api-key-value" not in serialized
    assert "wrkspc_sentinel_value" not in serialized
    assert "sentinel-secret-value" not in serialized


def test_openai_oauth_token_batch_wires_native_secret_ref_without_embedding_secret() -> None:
    batch = bootstrap.render_batch(
        {
            API_KEY: "sentinel-api-key-value",
            WORKSPACE: "wrkspc_sentinel_value",
            OPENAI_OAUTH: "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.sentinel-oauth-access",
            REQUIRE_OPENAI: "true",
        }
    )
    values = {str(item["path"]): item["value"] for item in batch}
    assert values["agents.defaults.model.primary"] == "anthropic/claude-opus-4-8"
    assert values["agents.defaults.model.fallbacks"] == [
        "openai/gpt-5.6-sol",
        "openai/gpt-5.5",
    ]
    assert values["auth.profiles.openai:default"] == {
        "provider": "openai",
        "mode": "token",
    }
    assert values["models.providers.openai"] == {
        "baseUrl": "https://chatgpt.com/backend-api/codex",
        "api": "openai-chatgpt-responses",
        "auth": "token",
        "apiKey": {
            "source": "env",
            "provider": "default",
            "id": "OPENCLAW_OPENAI_OAUTH_TOKEN",
        },
        "models": [],
    }
    assert values["agents.list"] == [
        {"id": "main"},
        {
            "id": "structured-extraction",
            "tools": {"deny": ["*"], "exec": {"security": "deny", "ask": "always"}},
        },
    ]
    serialized = json.dumps(batch)
    assert "sentinel-oauth-access" not in serialized


def test_openai_api_key_batch_wires_native_secret_ref_without_embedding_secret() -> None:
    batch = bootstrap.render_batch(
        {
            API_KEY: "sentinel-api-key-value",
            WORKSPACE: "wrkspc_sentinel_value",
            OPENAI_KEY: "sk-proj-sentinel-openai-key-value",
            REQUIRE_OPENAI: "1",
        }
    )
    values = {str(item["path"]): item["value"] for item in batch}
    assert values["auth.profiles.openai:default"] == {
        "provider": "openai",
        "mode": "api_key",
    }
    assert values["models.providers.openai"] == {
        "baseUrl": "https://api.openai.com/v1",
        "api": "openai-responses",
        "auth": "api-key",
        "apiKey": {
            "source": "env",
            "provider": "default",
            "id": "OPENAI_API_KEY",
        },
        "models": [],
    }
    serialized = json.dumps(batch)
    assert "sk-proj-sentinel-openai-key-value" not in serialized


@pytest.mark.parametrize(
    "bad_value",
    [
        "   ",
        "short",
        "placeholder",
        "your_api_key_here",
        "dummy_token_value",
        "<openai-oauth-token>",
        "${OPENAI_API_KEY}",
        "token with spaces",
        '{"tokens":{"access_token":"abc","refresh_token":"rt_single_use"}}',
        "rt_refresh_token_leak",
    ],
)
def test_invalid_or_placeholder_or_refresh_token_auth_fails_closed(
    bad_value: str,
) -> None:
    base = {
        API_KEY: "sentinel-api-key-value",
        WORKSPACE: "wrkspc_sentinel_value",
    }
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.render_batch({**base, OPENAI_OAUTH: bad_value})
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.render_batch({**base, OPENAI_KEY: bad_value})
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.render_batch({**base, REQUIRE_OPENAI: "invalid-flag"})
