from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_autostart_waits_for_current_url_file_not_stale_log_history() -> None:
    source = (ROOT / "scripts" / "dashboard_autostart.sh").read_text(encoding="utf-8")
    tunnel_block = source.split('if [[ "${MANAGE_TUNNEL}" == "1" ]]', 1)[1]

    assert '[[ -s "${URL_FILE}" ]]' in tunnel_block
    assert 'tr -d \'\\r\\n\' < "${URL_FILE}"' in tunnel_block
    assert "grep -oE 'https://[-a-z0-9]+" not in tunnel_block
    assert "no current URL was captured" in tunnel_block


def test_autostart_does_not_leak_singleton_lock_into_tmux_children() -> None:
    source = (ROOT / "scripts" / "dashboard_autostart.sh").read_text(encoding="utf-8")

    assert source.count("9>&-") == 2
    assert source.count("tmux new-session") == 2


def test_manage_tunnel_defaults_off_without_operator_opt_in() -> None:
    source = (ROOT / "scripts" / "dashboard_autostart.sh").read_text(encoding="utf-8")

    assert 'MANAGE_TUNNEL="${PANTHEON_DASHBOARD_MANAGE_TUNNEL:-0}"' in source
