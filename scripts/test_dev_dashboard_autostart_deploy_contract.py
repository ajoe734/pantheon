from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy_nonprod_vm.sh"


def test_dev_root_deploy_provisions_persistent_dashboard_recovery() -> None:
    script = DEPLOY.read_text(encoding="utf-8")

    function = script.split("provision_dev_dashboard_autostart() {", 1)[1].split("\n}", 1)[0]
    root_case = script.split("  root)", 1)[1].split("\n    ;;", 1)[0]
    bff_case = script.split("  bff)", 1)[1].split("\n    ;;", 1)[0]

    assert (
        'local command_root="${PANTHEON_DEV_SUPERVISOR_COMMAND_ROOT:'
        '-/home/lupin/pantheon-ci-deploy/dev-root}"'
    ) in function
    assert '"${command_root}/scripts/dashboard_autostart_install.py"' in function
    assert '--repo "${PANTHEON_STATUS_ROOT_HOST}"' in function
    assert "--method auto" in function
    assert "--start-now" in function
    assert "pantheon-dashboard-autostart.timer" in function
    assert "# pantheon-dashboard-autostart" in function
    assert "http://127.0.0.1:4180/index.html" in function
    assert "協作看板" in function
    assert "provision_dev_dashboard_autostart" in root_case
    assert "provision_dev_dashboard_autostart" not in bff_case
