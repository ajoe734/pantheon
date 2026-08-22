from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _prune_block(deploy: str) -> str:
    start = deploy.index("prune_dev_management_ai_telemetry_for_disk() {")
    end = deploy.index("dump_dev_root_failure_diagnostics() {", start)
    return deploy[start:end]


def test_telemetry_prune_scopes_truncate_to_management_ai_schema_only() -> None:
    block = _prune_block(_read("scripts/deploy_nonprod_vm.sh"))

    assert "AND n.nspname = target_schema" in block
    assert "n.nspname IN (target_schema, 'public')" not in block
    assert "TRUNCATE TABLE %I.%I" in block


def test_telemetry_prune_refuses_when_schema_resolves_to_public() -> None:
    block = _prune_block(_read("scripts/deploy_nonprod_vm.sh"))

    assert "IF target_schema = 'public' THEN" in block
    assert "refusing to prune telemetry_events" in block
    assert (
        block.index("IF target_schema = 'public' THEN")
        < block.index("FOR item IN")
    )


def test_telemetry_prune_still_gated_by_dev_root_and_postgres_backend() -> None:
    block = _prune_block(_read("scripts/deploy_nonprod_vm.sh"))

    assert '[[ "${PANTHEON_DEPLOY_ENV}" != "dev" || "${PANTHEON_DEPLOY_COMPONENT}" != "root" ]]' in block
    assert '"${MANAGEMENT_AI_STORE_BACKEND:-}" != "postgres"' in block
    assert "PANTHEON_DEV_POSTGRES_TELEMETRY_PRUNE" in block
