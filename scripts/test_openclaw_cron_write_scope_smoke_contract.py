from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "openclaw-cron-write-scope-smoke.sh"


def test_cron_scope_smoke_uses_strict_service_auth_and_persona_envelope() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN" in source
    assert 'X-Pantheon-Service-Token: ${SERVICE_TOKEN}' in source
    assert 'WORKFLOW_ID="pantheon.persona.first-evaluation"' in source
    assert 'kind: "pantheon.workflow.dispatch"' in source
    assert 'policy_id: "oc002.cron.persona-first-evaluation"' in source
    assert 'upstream_entrypoint: "evaluation.persona.first"' in source
    assert 'schedule: {kind: "cron", expr: "*/15 * * * *"}' in source
    assert "enabled: true" in source
    assert "deleteAfterRun: false" in source
    assert 'delivery: {mode: "none"}' in source
    assert "cron.remove" in source
    assert "enabled: false" not in source
    assert 'kind: "at"' not in source
