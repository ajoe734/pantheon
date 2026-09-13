from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
DEPLOY_SCRIPT = REPO_ROOT / "scripts/deploy_nonprod_vm.sh"


def test_dev_root_and_bff_deploys_pin_durable_agora_stores() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    root_case, remainder = script.split("  root)", 1)[1].split("  bff)", 1)
    bff_case = remainder.split("  exec)", 1)[0]
    bff_env = script.split("with_dev_bff_runtime_env() {", 1)[1].split("\n}\n", 1)[0]
    assert 'with_dev_bff_runtime_env "${PANTHEON_DEPLOY_SHA}"' in bff_case

    expected = (
        "AGORA_WORKSHOP_STORE_BACKEND=postgres",
        "AGORA_WORKSHOP_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon",
        "AGORA_WORKSHOP_STORE_SCHEMA=agora",
        "AGORA_RESEARCH_STORE_BACKEND=postgres",
        "AGORA_RESEARCH_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon",
        "AGORA_RESEARCH_STORE_SCHEMA=agora_research",
        "AGORA_TRADING_ROOM_STORE_BACKEND=postgres",
        "AGORA_TRADING_ROOM_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon",
        "AGORA_TRADING_ROOM_STORE_SCHEMA=agora",
    )
    for setting in expected:
        assert setting in root_case
        assert setting in bff_env


def test_private_content_key_uses_existing_secret_transport_and_only_consumer() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github/workflows/nonprod-deploy.yml").read_text()
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    assert 'DEV_AGORA_PRIVATE_CONTENT_DEV_KEK="${DEV_AGORA_PRIVATE_CONTENT_DEV_KEK:-}"' in script
    assert '  DEV_AGORA_PRIVATE_CONTENT_DEV_KEK\n' in script
    assert 'AGORA_PRIVATE_CONTENT_DEV_KEK=$(shell_quote "${DEV_AGORA_PRIVATE_CONTENT_DEV_KEK:-}")' in script
    assert workflow.count('DEV_AGORA_PRIVATE_CONTENT_DEV_KEK: ${{ secrets.DEV_AGORA_PRIVATE_CONTENT_DEV_KEK }}') == 3
    assert compose.count('AGORA_PRIVATE_CONTENT_DEV_KEK: ${AGORA_PRIVATE_CONTENT_DEV_KEK:-}') == 1
