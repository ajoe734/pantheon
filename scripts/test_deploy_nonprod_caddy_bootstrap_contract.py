from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy_nonprod_vm.sh"


def test_dev_deploy_bootstraps_versioned_caddy_ingress() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "ensure_dev_caddy_ingress()" in script
    assert script.count("ensure_dev_caddy_ingress \\") == 2
    assert "deploy/caddy/dev.Caddyfile.tmpl" in script
    assert "apt-get install -y caddy" in script
    assert "caddy validate --config /etc/caddy/Caddyfile" in script
    assert "systemctl enable --now caddy" in script
    assert 'curl_with_retry "https://${bff_host}/health"' in script


def test_dev_public_ingress_coordinates_are_forwarded_to_vm() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    for name in (
        "PANTHEON_DEV_BFF_PUBLIC_HOST",
        "PANTHEON_DEV_FE_PUBLIC_HOST",
        "PANTHEON_DEV_FE_STATIC_ROOT",
    ):
        assert f'command_prefix+=" {name}=' in script
