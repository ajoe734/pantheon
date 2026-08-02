from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy_nonprod_vm.sh"


def test_dev_root_deploy_provisions_split_root_persistent_watchdog() -> None:
    script = DEPLOY.read_text(encoding="utf-8")

    materialize = script.split(
        "materialize_dev_supervisor_command_runtime() {", 1
    )[1].split("\n}", 1)[0]
    function = script.split("provision_dev_supervisor_watchdog() {", 1)[1].split("\n}", 1)[0]
    root_case = script.split("  root)", 1)[1].split("\n    ;;", 1)[0]
    bff_case = script.split("  bff)", 1)[1].split("\n    ;;", 1)[0]

    assert 'DEV_SUPERVISOR_COMMAND_RUNTIME_PARENT="/home/lupin/pantheon-ci-deploy/command-runtimes"' in script
    assert 'local destination="${parent}/${sha}"' in materialize
    assert "git clone --quiet --no-local --no-checkout" in materialize
    assert "update-ref refs/remotes/origin/dev" in materialize
    assert "--validate-command-root-only" in materialize
    assert 'command_root="$(materialize_dev_supervisor_command_runtime "$source_root")"' in function
    assert 'local staging_root="${PANTHEON_DEV_SUPERVISOR_COMMAND_ROOT:' in function
    assert "performing first-install supervisor config provisioning with no incumbent" in function
    assert '"${command_root}/scripts/provision_live_supervisor_config.py"' in function
    assert '--repo-config "${command_root}/.orchestrator/config.json"' in function
    assert '--command-root "$command_root"' in function
    assert "--status-root \"${PANTHEON_STATUS_ROOT_HOST}\"" in function
    assert '"${command_root}/scripts/promote-supervisor-runtime.sh"' in function
    assert "--bootstrap-mutable-incumbent" in function
    assert '"${command_root}/scripts/check_config_drift.py"' in function
    assert "--fix" not in function
    assert "sudo -n loginctl enable-linger" in function
    assert '[[ "$linger_state" == "yes" ]]' in function
    assert '"${command_root}/scripts/supervisor_watchdog_install.py"' in function
    assert '--repo "$command_root"' in function
    assert "--config \"$live_config\"" in function
    assert "--method auto" in function
    assert '"${command_root}/scripts/supervisor_runtime_health.py"' in function
    assert "--require-watchdog" in function
    assert "$(pwd)" not in function
    assert "provision_dev_supervisor_watchdog" in root_case
    assert "provision_dev_supervisor_watchdog" not in bff_case
