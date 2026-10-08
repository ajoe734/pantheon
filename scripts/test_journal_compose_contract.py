#!/usr/bin/env python3
"""Render the real Compose contract; optionally prove it with local paper fixtures.

No Compose deployment, hosted endpoint, or credentials are used. --runtime
starts a disposable network-isolated Postgres and runs three fresh BFF factory
processes with the rendered journal settings and actual read-only consumer
mount. A fourth process verifies database failure without local fallback.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parent.parent
PROBE = "scripts/test_journal_compose_contract.py"


def run(*args: str, env=None, timeout=60) -> str:
    result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # Fixtures contain only public dummy configuration, never host env secrets.
        raise RuntimeError(f"command failed ({result.returncode}): {args[0:3]}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def render_compose(control: bool = False, overrides=None) -> dict:
    # Never consume a host .env or inherited product/service credentials.
    env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"],
           "PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL": "http://adapter.invalid",
           "PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN": "local-render-fixture-only",
           "PANTHEON_PERSONA_SERVICE_TOKEN": "local-render-fixture-only"}
    env.update(overrides or {})
    args = ["docker", "compose", "--profile", "root", "--env-file", "/dev/null", "-f", "docker-compose.yml"]
    if control:
        args += ["-f", "docker-compose.control.yml"]
    return json.loads(run(*args, "config", "--format", "json", env=env))


def validate_compose_contract(repo_root: Path = ROOT) -> dict:
    assert repo_root.resolve() == ROOT
    for control in (False, True):
        rendered = render_compose(control)
        bff = rendered["services"]["operator-bff"]
        env = bff["environment"]
        assert env["GOVERNANCE_STORE_BACKEND"] == "postgres"
        assert env["GOVERNANCE_STORE_DSN"] == rendered["services"]["governance"]["environment"]["DATABASE_URL"]
        assert env["GOVERNANCE_STORE_BOOTSTRAP"] == "1"
        assert env["PANTHEON_DECISION_JOURNAL_REQUIRED_BACKEND"] == "postgres"
        assert env["PANTHEON_DECISION_JOURNAL_DATA_DIR"] == "/data/bff/decision_journal"
        assert "PANTHEON_BFF_DECISION_JOURNAL_STORE" not in env
        mounts = {row["target"]: row for row in bff["volumes"]}
        assert mounts["/data/governance"]["read_only"] is True
        assert not mounts["/data/bff"].get("read_only", False)
        # Exact interpolation behavior, not substring matches against raw YAML.
        custom = "postgresql://fixture:fixture@database.invalid/paper"
        for variable in ("DATABASE_URL", "GOVERNANCE_STORE_DSN"):
            changed = render_compose(control, {variable: custom})
            assert changed["services"]["operator-bff"]["environment"]["GOVERNANCE_STORE_DSN"] == custom
    print("PASS: base/control rendered Compose defaults, DSN overrides, and read-only consumer mount")
    return rendered


def runtime_contract(rendered: dict, bff_image: str) -> None:
    name = "journal-contract-" + uuid.uuid4().hex[:10]
    network, database = name + "-net", name + "-pg"
    bff_env = rendered["services"]["operator-bff"]["environment"]
    with tempfile.TemporaryDirectory(prefix=name) as tmp:
        consumer = Path(tmp) / "consumer"
        writer = Path(tmp) / "writer"
        consumer.mkdir()
        writer.mkdir()
        network_created = database_created = False
        try:
            run("docker", "network", "create", "--internal", network)
            network_created = True
            run("docker", "run", "-d", "--name", database, "--network", network,
                "--network-alias", "postgres", "-e", "POSTGRES_USER=pantheon_app",
                "-e", "POSTGRES_PASSWORD=pantheon_app", "-e", "POSTGRES_DB=pantheon", "postgres:16-alpine")
            database_created = True
            for _ in range(30):
                probe = subprocess.run(["docker", "exec", database, "pg_isready", "-U", "pantheon_app"],
                                       capture_output=True, timeout=10)
                if probe.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Disposable Postgres did not become ready")

            def paper(phase: str):
                container = name + "-" + phase
                args = ["docker", "run", "--rm", "--name", container, "--network", network, "--read-only", "--cap-drop", "ALL",
                        "--security-opt", "no-new-privileges:true", "--user", f"{os.getuid()}:{os.getgid()}",
                        "--tmpfs", "/tmp", "-v", f"{ROOT}:/workspace:ro",
                        "-v", f"{consumer}:/data/governance:ro", "-v", f"{writer}:/data/bff:rw",
                        "-w", "/workspace", "-e", "PYTHONPATH=/workspace", "-e", "PYTHONDONTWRITEBYTECODE=1",
                        "-e", "BFF_DATA_DIR=/data/bff", "-e", "PANTHEON_ENV=test",
                        "-e", "JOURNAL_REQUIRE_RO_MOUNT=1",
                        "-e", f"DATABASE_URL={bff_env['GOVERNANCE_STORE_DSN']}?connect_timeout=1",
                        "-e", "RANKING_STORE_BOOTSTRAP=0",
                        "-e", "PANTHEON_STRATEGY_STORE_BOOTSTRAP=0"]
                for key in ("GOVERNANCE_STORE_BACKEND", "GOVERNANCE_STORE_DSN", "GOVERNANCE_STORE_BOOTSTRAP",
                            "PANTHEON_DECISION_JOURNAL_REQUIRED_BACKEND",
                            "PANTHEON_DECISION_JOURNAL_DATA_DIR", "PANTHEON_GOVERNANCE_DATA_DIR"):
                    value = bff_env[key]
                    if key == "GOVERNANCE_STORE_DSN":
                        value += "?connect_timeout=1"
                    if phase == "unavailable" and key == "GOVERNANCE_STORE_BOOTSTRAP":
                        value = "0"
                    args += ["-e", f"{key}={value}"]
                try:
                    print(run(*args, "--entrypoint", "python", bff_image, PROBE, "--paper-probe", phase, timeout=90))
                finally:
                    # A killed CLI must not leave its task-owned container running.
                    subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=30)

            paper("create")
            paper("restart")
            paper("reread")
            run("docker", "stop", "--time", "10", database)
            paper("unavailable")
            assert list(consumer.iterdir()) == [], "Read consumer mount was modified"
            assert not list(writer.rglob("decision_journal*.json")), "Unexpected local authority"
            print("PASS: mounted BFF paper create/restart/replay/tenant/CAS/unavailable; no JSON fallback")
        finally:
            if database_created:
                run("docker", "rm", "-f", database)
            if network_created:
                run("docker", "network", "rm", network)


def paper_factory_probe(phase: str) -> None:
    """Run in separate processes/containers against only disposable local storage.

    Identity is injected at the documented composition seam, not by changing
    production authentication. All journal owners, read ports and handlers are
    the real default factory objects; no test journal owner is injected.
    """
    import errno
    import json
    from fastapi.testclient import TestClient
    # Production resolves composition dependencies from the loaded main module;
    # importing it (not a stand-in resolver) makes a missing dependency fail the probe.
    import services.control_plane.bff.main  # noqa: F401
    from services.control_plane.bff.core.app_factory import compose_bff_app
    from services.control_plane.bff.models import OperatorIdentity
    from services.governance.record_store import PostgresGovernanceRecordStore
    from services.control_plane.bff.governance.decision_journal_write_owner import build_decision_journal_write_owner
    from services.control_plane.bff.bootstrap.dependencies import AppDependencies

    def identity(authorization=None, **kwargs):
        tenant = "tenant-b" if authorization == "Bearer tenant-b" else "tenant-a"
        return OperatorIdentity(
            operator_id="paper-reviewer", roles=["operator"],
            claims={"tenant_id": tenant, "allowed_tenants": [tenant]},
        )

    deps = AppDependencies.create_default()
    owner = deps.decision_journal_write_owner
    app = compose_bff_app(app_deps=deps, _extract_identity=identity)
    assert app.state.decision_journal_write_owner is owner
    if phase != "unavailable":
        assert app.state.agora_router.agora_service.journal_write_owner is owner
    assert isinstance(owner.stores.entries, PostgresGovernanceRecordStore)
    # The actual read-only consumer mount remains unwritable, even while the
    # distinct Postgres authority accepts writes. Do not substitute chmod.
    if os.getenv("JOURNAL_REQUIRE_RO_MOUNT") == "1":
        try:
            Path("/data/governance/forbidden-write").write_text("must not write")
        except OSError as exc:
            assert exc.errno == errno.EROFS, exc
        else:
            raise AssertionError("governance consumer mount is not read-only")

    payload = {"title": "Paper runtime contract", "body": "No live execution", "visibility": "private"}
    headers = {"Idempotency-Key": "paper-runtime-create"}
    with TestClient(app, raise_server_exceptions=False) as client:
        if phase == "unavailable":
            assert not owner.is_storage_healthy
            response = client.post("/bff/agora/journal", json=payload, headers=headers)
            assert response.status_code == 503, response.text
            assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE", response.text
            assert not list(Path(os.environ["PANTHEON_DECISION_JOURNAL_DATA_DIR"]).glob("*.json"))
            print(json.dumps({"phase": phase, "status": response.status_code, "no_json_fallback": True}))
            return

        assert owner.is_storage_healthy
        response = client.post("/bff/agora/journal", json=payload, headers=headers)
        assert response.status_code == 201, response.text
        created = response.json()["data"]
        entry_id = created["id"]
        listed = client.get("/bff/agora/journal")
        assert listed.status_code == 200, listed.text
        assert any(row["id"] == entry_id for row in listed.json()["data"]), listed.text
        if phase == "restart":
            assert response.json()["meta"]["idempotency"]["replayed"] is True
            conflict = client.post("/bff/agora/journal", json={**payload, "body": "changed"}, headers=headers)
            assert conflict.status_code == 409, conflict.text
            other = client.get("/bff/agora/journal", headers={"Authorization": "Bearer tenant-b"})
            assert other.status_code == 200 and other.json()["data"] == [], other.text
            denied = client.patch(f"/bff/agora/journal/{entry_id}", json={"title": "cross-tenant"},
                                  headers={"Authorization": "Bearer tenant-b", "Idempotency-Key": "paper-patch", "Content-Type": "application/merge-patch+json"})
            assert denied.status_code in (403, 404), denied.text
            patched = client.patch(f"/bff/agora/journal/{entry_id}", json={"title": "Paper reviewed"},
                                   headers={"Idempotency-Key": "paper-patch", "Content-Type": "application/merge-patch+json"})
            assert patched.status_code == 200, patched.text
            assert patched.json()["data"]["version"] == 2, patched.text
            replay = client.patch(f"/bff/agora/journal/{entry_id}", json={"title": "Paper reviewed"},
                                  headers={"Idempotency-Key": "paper-patch", "Content-Type": "application/merge-patch+json"})
            assert replay.status_code == 200 and replay.json()["meta"]["idempotency"]["replayed"], replay.text
            # The selected durable authority must reject a stale CAS from an
            # independent store instance, without replacing the committed row.
            second = build_decision_journal_write_owner()
            current = second.stores.entries.get(entry_id)
            stale = {**current, "version": 1}
            accepted, canonical = second.stores.entries.compare_and_set(stale, {**current, "title": "stale"})
            assert not accepted and canonical == current
            # Same raw key in another tenant has independent durable identity.
            other_create = client.post("/bff/agora/journal", json=payload,
                                       headers={**headers, "Authorization": "Bearer tenant-b"})
            assert other_create.status_code == 201, other_create.text
            assert other_create.json()["data"]["id"] != entry_id
        elif phase == "reread":
            row = next(row for row in listed.json()["data"] if row["id"] == entry_id)
            assert row["title"] == "Paper reviewed" and row["version"] == 2
        else:
            assert phase == "create"
            assert created["version"] == 1
        print(json.dumps({"phase": phase, "entry_id": entry_id, "backend": "postgres", "passed": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", action="store_true")
    parser.add_argument("--bff-image", default="pantheon-bff-test")
    parser.add_argument("--paper-probe", choices=["create", "restart", "reread", "unavailable"], default=None)
    args = parser.parse_args()
    if args.paper_probe:
        paper_factory_probe(args.paper_probe)
    else:
        config = validate_compose_contract()
        if args.runtime:
            runtime_contract(config, args.bff_image)
