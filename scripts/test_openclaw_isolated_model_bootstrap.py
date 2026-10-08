import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import openclaw_isolated_model_bootstrap as bootstrap  # noqa: E402

TOKEN = bootstrap.TOKEN_SOURCE_ENV


def test_missing_credential_fails_closed() -> None:
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.render_batch({})
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.render_batch({TOKEN: "  "})
    result = subprocess.run(
        [sys.executable, bootstrap.__file__], env={}, capture_output=True, text=True
    )
    assert result.returncode == 1 and result.stdout == ""


def test_batch_reuses_approved_pool_and_never_embeds_credential() -> None:
    batch = bootstrap.render_batch({TOKEN: "sentinel-secret-value"})
    values = {str(item["path"]): item["value"] for item in batch}
    assert values["agents.defaults.model.primary"] == "anthropic/claude-opus-4-8"
    # Unresolved env reference only; clearEnv is never disabled.
    assert (
        values['agents.defaults.cliBackends["claude-cli"].env.CLAUDE_CODE_OAUTH_TOKEN']
        == "${CLAUDE_CODE_OAUTH_TOKEN}"
    )
    assert not any("clearEnv" in path for path in values)
    assert values["agents.list"] == [
        {"id": "structured-extraction", "tools": {"deny": ["*"]}}
    ]
    assert "sentinel-secret-value" not in json.dumps(batch)
