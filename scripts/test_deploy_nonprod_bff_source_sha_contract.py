from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy_nonprod_vm.sh"


def _root_force_recreate_inherits_exact_sha(script: str) -> bool:
    root_case = script.split("  root)\n", 1)[1].split("  bff)\n", 1)[0]
    export = 'export GIT_SHA="${PANTHEON_DEPLOY_SHA}"'
    force_recreate = (
        "docker compose -p pantheon -f docker-compose.yml up -d "
        "--force-recreate --no-deps loop-run-projector-scheduler"
    )
    return (
        root_case.count(export) == 1
        and root_case.find(export) < root_case.find(force_recreate)
    )


def _bff_prebuild_and_recreate_inherits_exact_sha(script: str) -> bool:
    bff_case = script.split("  bff)\n", 1)[1].split("  exec)\n", 1)[0]
    export = 'export GIT_SHA="${PANTHEON_DEPLOY_SHA}"'
    build = (
        "docker compose -p pantheon -f docker-compose.yml build operator-bff agora-interaction-worker loop-run-projector-scheduler"
    )
    up = (
        "docker compose -p pantheon -f docker-compose.yml up -d "
        "--force-recreate --no-deps operator-bff agora-interaction-worker loop-run-projector-scheduler"
    )
    return (
        bff_case.count(export) == 1
        and bff_case.find(export) < bff_case.find(build) < bff_case.find(up)
    )


def test_nonprod_bff_builds_receive_and_verify_requested_source_sha() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert script.count('GIT_SHA="${PANTHEON_DEPLOY_SHA}"') >= 4
    assert _root_force_recreate_inherits_exact_sha(script)
    assert _bff_prebuild_and_recreate_inherits_exact_sha(script)
    assert "assert_bff_source_sha()" in script
    assert (
        script.count(
            "assert_bff_source_sha http://127.0.0.1:18001/bff/version"
        )
        == 2
    )
    assert (
        "assert_bff_source_sha http://127.0.0.1:38001/bff/version"
        in script
    )


def test_root_force_recreate_contract_rejects_missing_or_late_sha_export() -> None:
    valid = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    export = '    export GIT_SHA="${PANTHEON_DEPLOY_SHA}"\n'

    assert not _root_force_recreate_inherits_exact_sha(valid.replace(export, "", 1))

    late = valid.replace(export, "", 1).replace(
        "    verify_bounded_source_refresh_readback",
        f"{export}    verify_bounded_source_refresh_readback",
        1,
    )
    assert not _root_force_recreate_inherits_exact_sha(late)
