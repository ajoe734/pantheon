import json
import os
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIGURE_SCRIPT = REPO_ROOT / "scripts" / "openclaw-configure-shared-model-pool.sh"


def _model_pool_batch(source: str) -> list[dict[str, object]]:
    match = re.search(r"MODEL_POOL_BATCH='(\[.*?\])'", source, re.DOTALL)
    assert match is not None
    return json.loads(match.group(1))


def _has_valid_failover_contract(batch: list[dict[str, object]]) -> bool:
    values = {str(item["path"]): item["value"] for item in batch}
    primary = values.get("agents.defaults.model.primary")
    fallbacks = values.get("agents.defaults.model.fallbacks")
    registered = {
        path.removeprefix('agents.defaults.models["').removesuffix('"]')
        for path in values
        if path.startswith('agents.defaults.models["')
    }
    return (
        primary == "anthropic/claude-opus-4-8"
        and fallbacks == ["openai/gpt-5.6-sol", "openai/gpt-5.5"]
        and primary not in fallbacks
        and all(model in registered for model in fallbacks)
    )


def test_shared_model_pool_has_ordered_cross_provider_failover() -> None:
    source = CONFIGURE_SCRIPT.read_text(encoding="utf-8")
    batch = _model_pool_batch(source)

    assert _has_valid_failover_contract(batch)
    assert 'config get agents.defaults.model.fallbacks --json' in source
    assert (
        'jq -e \'. == ["openai/gpt-5.6-sol", "openai/gpt-5.5"]\''
        in source
    )


def test_failover_contract_rejects_missing_self_or_unregistered_fallbacks() -> None:
    batch = _model_pool_batch(CONFIGURE_SCRIPT.read_text(encoding="utf-8"))

    missing = [
        item for item in batch if item["path"] != "agents.defaults.model.fallbacks"
    ]
    assert not _has_valid_failover_contract(missing)

    self_fallback = [dict(item) for item in batch]
    next(
        item
        for item in self_fallback
        if item["path"] == "agents.defaults.model.fallbacks"
    )["value"] = ["anthropic/claude-opus-4-8", "openai/gpt-5.5"]
    assert not _has_valid_failover_contract(self_fallback)

    unregistered = [
        item
        for item in batch
        if item["path"] != 'agents.defaults.models["openai/gpt-5.6-sol"]'
    ]
    assert not _has_valid_failover_contract(unregistered)


def test_claude_token_binding_is_narrow_and_reference_only() -> None:
    source = CONFIGURE_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"CLAUDE_TOKEN_BATCH='(\[.*?\])'", source, re.DOTALL)
    assert match is not None
    assert json.loads(match.group(1)) == [
        {"path": 'agents.defaults.cliBackends["claude-cli"].command', "value": "claude"},
        {
            "path": 'agents.defaults.cliBackends["claude-cli"].env.CLAUDE_CODE_OAUTH_TOKEN',
            "value": "${CLAUDE_CODE_OAUTH_TOKEN}",
        },
    ]
    assert "OPENCLAW_LIVE_CLI_BACKEND_PRESERVE_ENV" not in source
    assert '"clearEnv"' not in source
    assert "auth-profiles.json" not in source


def _run_model_pool_script(tmp_path, *, token_present: bool, reject_binding: bool = False):
    command_log = tmp_path / "docker-commands.jsonl"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        f"#!{sys.executable}\n" + r'''
import json, os, sys
args = sys.argv[1:]
with open(os.environ["TEST_DOCKER_LOG"], "a") as stream:
    stream.write(json.dumps(args) + "\n")
if "-e" in args:
    sys.exit(0 if os.environ["TEST_TOKEN_PRESENT"] == "1" else 1)
if "dist/index.js" not in args:
    sys.exit(0)
cli = args[args.index("dist/index.js") + 1:]
if cli[:2] == ["config", "set"]:
    batch = json.loads(cli[cli.index("--batch-json") + 1])
    if any("cliBackends" in op["path"] for op in batch):
        if os.environ["TEST_REJECT_BINDING"] == "1":
            sys.exit(33)
elif cli[:2] == ["config", "get"]:
    path = cli[2]
    if path == "agents.defaults.models":
        print(json.dumps({name: {} for name in [
            "openai/gpt-5.6-sol", "openai/gpt-5.5",
            "anthropic/claude-opus-4-8", "anthropic/claude-sonnet-4-6",
            "google/gemini-3.1-pro-preview"]}))
    elif path == "agents.defaults.model.primary":
        print(json.dumps("anthropic/claude-opus-4-8"))
    elif path == "agents.defaults.model.fallbacks":
        print(json.dumps(["openai/gpt-5.6-sol", "openai/gpt-5.5"]))
    else:
        print("true")
''', encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    result = subprocess.run(
        ["bash", str(CONFIGURE_SCRIPT)],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "TEST_DOCKER_LOG": str(command_log),
            "TEST_TOKEN_PRESENT": "1" if token_present else "0",
            "TEST_REJECT_BINDING": "1" if reject_binding else "0",
            "CLAUDE_CODE_OAUTH_TOKEN": "test-secret-never-in-argv-or-output",
        },
        capture_output=True, text=True, timeout=10, check=False,
    )
    raw_log = command_log.read_text(encoding="utf-8")
    assert "test-secret-never-in-argv-or-output" not in raw_log + result.stdout + result.stderr
    return result, [json.loads(line) for line in raw_log.splitlines()]


def test_optional_claude_token_binds_before_validate_and_restart(tmp_path) -> None:
    result, calls = _run_model_pool_script(tmp_path, token_present=True)
    assert result.returncode == 0, result.stderr
    binding = next(i for i, args in enumerate(calls) if any("cliBackends" in arg for arg in args))
    validation = next(i for i, args in enumerate(calls) if args[-2:] == ["config", "validate"])
    restart = next(i for i, args in enumerate(calls) if "restart" in args)
    assert binding < validation < restart


def test_missing_token_preserves_native_cli_login_path(tmp_path) -> None:
    result, calls = _run_model_pool_script(tmp_path, token_present=False)
    assert result.returncode == 0, result.stderr
    assert not any("cliBackends" in arg for args in calls for arg in args)
    assert any("restart" in args for args in calls)


def test_invalid_token_binding_stops_before_gateway_restart(tmp_path) -> None:
    result, calls = _run_model_pool_script(tmp_path, token_present=True, reject_binding=True)
    assert result.returncode == 33
    assert not any("restart" in args for args in calls)
