from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_nonprod_vm.sh"


def test_dev_root_deploy_proves_evolution_scheduler_tick() -> None:
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    root_case = deploy[deploy.index("  root)\n") : deploy.index("\n  bff)\n")]

    assert "verify_dev_evolution_daily_sweep()" in deploy
    assert 'logs --no-color --since=10m evolution-daily-sweep-scheduler' in deploy
    assert '"${compose[@]}" exec -T evolution python -c' in deploy
    assert 'os.environ.get("EVOLUTION_AUTH_TOKEN", "").strip()' in deploy
    assert 'os.environ.get("EVOLUTION_DEFAULT_TENANT_ID", "").strip()' in deploy
    assert 'headers["Authorization"] = f"Bearer {token}"' in deploy
    assert 'headers["X-Tenant-Id"] = tenant_id' in deploy
    assert "http://127.0.0.1:8093/api/evolution/sweep-status" in deploy
    assert "http://127.0.0.1:18093/api/evolution/sweep-status" not in deploy
    assert 'payload.get("last_success_at")' in deploy
    assert 'payload.get("total_sweeps_run")' in deploy
    assert root_case.index("docker compose -p pantheon -f docker-compose.yml up -d --build") < root_case.index(
        "verify_dev_evolution_daily_sweep"
    )
