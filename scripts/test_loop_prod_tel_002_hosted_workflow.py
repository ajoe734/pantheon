from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "nonprod-deploy.yml"
HOSTED_PROBE_SCRIPT = ROOT / "scripts" / "run_loop_prod_tel_002_hosted_probe.sh"


def test_nonprod_workflow_has_opt_in_read_only_canonical_lifecycle_probe() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    script = HOSTED_PROBE_SCRIPT.read_text(encoding="utf-8")
    parsed = yaml.safe_load(source)
    triggers = parsed.get("on") or parsed.get(True)
    dispatch = triggers["workflow_dispatch"]["inputs"]
    probe_input = dispatch["run_loop_prod_tel_002_probe"]

    assert probe_input["default"] is False
    assert probe_input["type"] == "boolean"
    assert "inputs.run_loop_prod_tel_002_probe" in source
    assert "scripts/run_loop_prod_tel_002_hosted_probe.sh" in source
    assert "--container-output" in source
    assert "--remote-output" in source
    assert "loop-run-projector-scheduler" in script
    assert "services.trade_journey.hosted_lifecycle_probe" in script
    assert "services.trade_journey.hosted_lifecycle_stimulus" in script
    assert "--print-high-watermark" in script
    assert "--baseline-high-watermark" in script
    assert "--worker-ready-timeout-seconds" in script
    assert "--worker-heartbeat-max-age-seconds" in script
    assert "--allow-ambiguous-reconciliation" in script
    assert "paper-signal-producer" in script
    assert "services.trade_journey.hosted_bff_readback" in source
    assert "--expected-sha" in source
    assert "--timeout-seconds 420" in source
    assert "docker cp" in script
    assert "hosted_probe_transport_error" in source
    assert "hosted_stimulus_failed" in script
    assert "baseline_high_watermark_failed" in script
    assert 'exit "${scp_status}"' in source
    assert 'exit "${readback_status}"' in source
    assert "DEV_BFF_OIDC_CLIENT_SECRET: ${{ secrets.DEV_BFF_OIDC_CLIENT_SECRET }}" in source
    assert "LOOP-PROD-TEL-002 hosted proof requires dev/root with strict auth." in source
    assert "loop-prod-tel-002-hosted-${{ github.run_id }}" in source


def test_hosted_probe_artifact_upload_runs_even_when_probe_fails_closed() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    upload = source.index("- name: Upload canonical paper lifecycle hosted evidence")
    tail = source[upload : upload + 800]

    assert "always()" in tail
    assert "readback_artifact_path" in tail
    assert "if-no-files-found: error" in tail
    assert "retention-days: 30" in tail
