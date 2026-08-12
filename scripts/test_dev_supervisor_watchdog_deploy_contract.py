from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy_nonprod_vm.sh"
WATCHDOG_WRAPPER = ROOT / "scripts" / "run-supervisor-watchdog.sh"


def _remote_deploy_payload(script: str) -> str:
    marker = '    --command="${command_prefix}" <<\'REMOTE\'\n'
    start = script.index(marker) + len(marker)
    end = script.index("\nREMOTE\n", start)
    return script[start:end]


def test_dev_root_deploy_provisions_split_root_persistent_watchdog() -> None:
    script = DEPLOY.read_text(encoding="utf-8")

    materialize = script.split(
        "materialize_dev_supervisor_command_runtime() {", 1
    )[1].split("\n}", 1)[0]
    first_install_guard = script.split(
        "assert_no_live_supervisor_incumbent() {", 1
    )[1].split("\n}", 1)[0]
    function = script.split("provision_dev_supervisor_watchdog() {", 1)[1].split("\n}", 1)[0]
    root_case = script.split("  root)", 1)[1].split("\n    ;;", 1)[0]
    bff_case = script.split("  bff)", 1)[1].split("\n    ;;", 1)[0]

    assert 'DEV_SUPERVISOR_COMMAND_RUNTIME_PARENT="/home/lupin/pantheon-ci-deploy/command-runtimes"' in script
    assert 'local destination="${parent}/${sha}"' in materialize
    assert "git clone --quiet --no-local --no-checkout" in materialize
    assert "update-ref refs/remotes/origin/dev" in materialize
    assert "--validate-command-root-only" in materialize
    assert (
        'python3 -B "$destination/scripts/provision_live_supervisor_config.py"'
        in materialize
    )
    assert 'local pid_file="${PANTHEON_SUPERVISOR_PID:' in first_install_guard
    assert 'Path("/proc").glob("[0-9]*/cmdline")' in first_install_guard
    assert 'PurePosixPath(argument).name == "supervisor.py"' in first_install_guard
    assert 'command_root="$(materialize_dev_supervisor_command_runtime "$source_root")"' in function
    assert 'local staging_root="${PANTHEON_DEV_SUPERVISOR_COMMAND_ROOT:' in function
    assert "performing first-install supervisor config provisioning with no incumbent" in function
    assert 'assert_no_live_supervisor_incumbent "${PANTHEON_STATUS_ROOT_HOST}"' in function
    assert '"${command_root}/scripts/provision_live_supervisor_config.py"' in function
    assert (
        'python3 -B "${command_root}/scripts/provision_live_supervisor_config.py"'
        in function
    )
    assert '--repo-config "${command_root}/.orchestrator/config.json"' in function
    assert '--command-root "$command_root"' in function
    assert "--status-root \"${PANTHEON_STATUS_ROOT_HOST}\"" in function
    assert '"${command_root}/scripts/promote-supervisor-runtime.sh"' in function
    assert "--bootstrap-mutable-incumbent" not in function
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


def test_remote_deploy_payload_is_valid_bash_after_removed_incumbent_flag() -> None:
    """The VM receives the REMOTE heredoc, not the outer deploy wrapper.

    Removing the retired bootstrap flag must not leave an empty `if` in that
    payload: outer `bash -n` cannot detect syntax errors inside a quoted
    heredoc.
    """
    remote = _remote_deploy_payload(DEPLOY.read_text(encoding="utf-8"))

    parsed = subprocess.run(
        ["bash", "-n"],
        input=remote,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert parsed.returncode == 0, parsed.stdout + parsed.stderr
    assert 'if [[ ! "$configured_root" =~ ^${DEV_SUPERVISOR_COMMAND_RUNTIME_PARENT}/[0-9a-f]{40}$ ]]; then' not in remote
    assert 'promotion_args=(--promote --repo "$command_root")' in remote


def test_persistent_watchdog_wrapper_disables_inherited_bytecode_writes() -> None:
    wrapper = WATCHDOG_WRAPPER.read_text(encoding="utf-8")

    assert "export PYTHONDONTWRITEBYTECODE=1" in wrapper
    assert 'exec python3 -B "$ROOT_DIR/.orchestrator/supervisor_watchdog.py"' in wrapper
