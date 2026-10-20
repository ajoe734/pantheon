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


def test_missing_credential_fails_closed() -> None:
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.render_batch({})
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.render_batch({API_KEY: "sk-ant-test", WORKSPACE: "  ", OPENAI_OAUTH: "oauth-test"})
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.render_batch({API_KEY: "  ", WORKSPACE: "wrkspc_test", OPENAI_OAUTH: "oauth-test"})
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.render_batch({API_KEY: "sk-ant-test", WORKSPACE: "wrkspc_test"})
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.render_batch({API_KEY: "sk-ant-test", WORKSPACE: "wrkspc_test", OPENAI_OAUTH: "  "})
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
            OPENAI_OAUTH: "sentinel-openai-oauth-value",
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
    assert "auth.profiles.openai:default" not in values
    # Unresolved env references only; clearEnv is never disabled.
    assert (
        values["models.providers.anthropic.headers.anthropic-workspace-id"]
        == "${ANTHROPIC_WORKSPACE_ID}"
    )
    assert (
        values['agents.defaults.cliBackends["claude-cli"].env.CLAUDE_CODE_OAUTH_TOKEN']
        == "${CLAUDE_CODE_OAUTH_TOKEN}"
    )
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
    assert "sentinel-openai-oauth-value" not in serialized


@pytest.mark.parametrize(
    "bad_oauth",
    [
        "   ",
        "token with spaces",
        '{"tokens":{"access_token":"abc","refresh_token":"rt_single_use"}}',
        "rt_refresh_token_leak",
    ],
)
def test_invalid_or_refresh_token_openai_oauth_fails_closed(bad_oauth: str) -> None:
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.render_batch(
            {API_KEY: "sk-ant-test", WORKSPACE: "wrkspc_test", OPENAI_OAUTH: bad_oauth}
        )
