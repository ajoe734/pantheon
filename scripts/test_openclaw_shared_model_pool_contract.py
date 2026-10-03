import json
import os
import re
import subprocess
import time
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


def _run_model_pool_script(
    tmp_path, *, token_present: bool, reject_binding: bool = False,
    agents=None, get_fail: bool = False, raw_state=None,
):
    state = tmp_path / "agents-state.json"
    state.write_text(raw_state if raw_state is not None else json.dumps(agents) if agents is not None else "")
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
state = os.environ["TEST_STATE"]
if cli[:3] == ["config", "set", "agents.list"]:
    with open(state, "w") as out:
        out.write(cli[3])
elif cli[:2] == ["config", "set"]:
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
    elif path in ("agents", "agents.list"):
        if os.environ["TEST_AGENTS_GET_FAIL"] == "1":
            sys.exit(44)
        raw = open(state).read()
        lst = json.loads(raw) if raw else None
        if path == "agents.list":
            print(json.dumps(lst))
        else:
            print(json.dumps({"defaults": {}, **({} if lst is None else {"list": lst})}))
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
            "TEST_STATE": str(state),
            "TEST_AGENTS_GET_FAIL": "1" if get_fail else "0",
            "CLAUDE_CODE_OAUTH_TOKEN": "test-secret-never-in-argv-or-output",
        },
        capture_output=True, text=True, timeout=10, check=False,
    )
    raw_log = command_log.read_text(encoding="utf-8")
    assert "test-secret-never-in-argv-or-output" not in raw_log + result.stdout + result.stderr
    calls = [json.loads(line) for line in raw_log.splitlines()]
    result.final_agents = json.loads(state.read_text() or "null")
    return result, calls


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


def _admission(agents):
    """Run the unchanged adapter admission guard against a rendered config."""
    sys.path.insert(0, str(REPO_ROOT / "services" / "openclaw-gateway-adapter"))
    import main as adapter
    from assistant_openclaw_provider import STRUCTURED_AGENT_ID

    class Provider:
        def _gateway_call(self, *_a, **_k):
            return {"valid": True, "config": {"agents": {"list": agents}}}

    saved = adapter._OPENCLAW_AGENT_PROVIDER
    adapter._OPENCLAW_AGENT_PROVIDER = Provider()
    try:
        adapter._assert_structured_gateway_policy(STRUCTURED_AGENT_ID, deadline=time.monotonic() + 5)
    finally:
        adapter._OPENCLAW_AGENT_PROVIDER = saved


def test_provisioned_config_passes_admission_and_preserves_agents(tmp_path) -> None:
    existing = [{"id": "main", "default": True}, {"id": "persona-x", "tools": {"allow": ["exec"]}}]
    result, _ = _run_model_pool_script(tmp_path, token_present=False, agents=existing)
    assert result.returncode == 0, result.stderr
    _admission(result.final_agents)
    assert result.final_agents[:2] == existing
    # Rerun on its own output: idempotent, no duplicates.
    result2, _ = _run_model_pool_script(tmp_path, token_present=False, agents=result.final_agents)
    assert result2.final_agents == result.final_agents


def test_admission_still_rejects_agent_without_deny_all() -> None:
    import pytest

    for agents in ([{"id": "structured-extraction"}], [{"id": "main", "tools": {"deny": ["*"]}}]):
        with pytest.raises(Exception, match="verified Gateway agent"):
            _admission(agents)


def test_agents_read_failure_or_bad_shape_fails_closed(tmp_path) -> None:
    for kwargs in ({"get_fail": True, "agents": [{"id": "main"}]}, {"raw_state": '{"id": "main"}'}):
        result, calls = _run_model_pool_script(tmp_path, token_present=False, **kwargs)
        assert result.returncode != 0
        assert not any(a[-3:-1] == ["set", "agents.list"] or "agents.list" in a[:5] and "set" in a for a in calls)
        assert not any("restart" in a for a in calls)
        assert json.loads((tmp_path / "agents-state.json").read_text() or "null") in (
            kwargs.get("agents"), {"id": "main"})
