from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
DEPLOY_SCRIPT = REPO_ROOT / "scripts/deploy_nonprod_vm.sh"


def test_dev_root_and_bff_deploys_pin_durable_workshop_store() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    root_case, remainder = script.split("  root)", 1)[1].split("  bff)", 1)
    bff_case = remainder.split("  exec)", 1)[0]

    expected = (
        "AGORA_WORKSHOP_STORE_BACKEND=postgres",
        "AGORA_WORKSHOP_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon",
        "AGORA_WORKSHOP_STORE_SCHEMA=agora",
        "AGORA_TRADING_ROOM_STORE_BACKEND=postgres",
        "AGORA_TRADING_ROOM_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon",
        "AGORA_TRADING_ROOM_STORE_SCHEMA=agora",
    )
    for setting in expected:
        assert setting in root_case
        assert setting in bff_case
