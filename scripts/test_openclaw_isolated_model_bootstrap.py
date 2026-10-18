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


def test_missing_credential_fails_closed() -> None:
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.render_batch({})
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.render_batch({API_KEY: "sk-ant-test", WORKSPACE: "  "})
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.render_batch({API_KEY: "  ", WORKSPACE: "wrkspc_test"})
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
