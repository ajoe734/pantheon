import json
import re
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
