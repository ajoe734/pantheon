"""Packaging/composition regressions; not hosted twelve-loop acceptance."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ("research", "alpha-replication-worker", "services.research.alpha_replication.replication_controller", "build_loop_writer"),
    ("policy-learning", "policy-learning-shadow-eval-scheduler", "services.policy-learning.scheduler_worker", "_build_loop_writer"),
    ("training-session", "training-session-preview-worker", "services.training-session.preview_eval_worker", "build_loop_writer"),
    ("consultation", "consultation-svc", "services.consultation.workflow_executor", "_build_loop_writer"),
    ("reconciliation-drift", "reconciliation-drift-scheduler", "services.reconciliation-drift.scheduler_worker", "_build_loop_writer"),
    ("control-plane-bff", "agora-interaction-worker", "services.control-plane.bff.agora.interaction.worker", "build_loop_writer"),
    ("deployment", "deployment-outbox-consumer", "services.deployment.outbox_consumer_worker", "build_loop_writer"),
    ("paper_fleet_reconciler", "paper-fleet-reconciler", "services.paper_fleet_reconciler.paper_fleet_reconciler", "_build_loop_writer"),
    ("evolution", "evolution-dispatch-worker", "services.evolution.dispatch_worker", "build_loop_writer"),
]
# Compose env the Loop 5/8/9/11 services must carry: only the DSN (and tenant)
# variable their merged writer reads, with no release-identity env added.
COMPOSE_ENV_KEYS = {
    "control-plane-bff": ("DATABASE_URL", "PANTHEON_TENANT_ID"),
    "deployment": ("DATABASE_URL", "PANTHEON_DEPLOYMENT_TENANT_ID"),
    "paper_fleet_reconciler": ("RECONCILER_LOOP_CONTROLLER_DSN", "PANTHEON_TENANT_ID"),
    "evolution": ("DATABASE_URL", "PANTHEON_TENANT_ID"),
}
REQUIREMENTS_PATH = {"control-plane-bff": "control-plane/bff"}
DSN = "postgresql://image-contract@unused.invalid/isolated"
SHA = "1" * 40


def _builder(case, monkeypatch, *, configured=True):
    service, _, module_name, builder_name = case
    module = importlib.import_module(module_name)
    dsn = DSN if configured else ""
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setenv("PANTHEON_TENANT_ID", "tenant-image-test")
    monkeypatch.setenv("TRAINING_SESSION_TENANT_ID", "tenant-image-test")
    monkeypatch.setenv("PANTHEON_ENV", "image-test")
    monkeypatch.setenv("GIT_SHA", SHA)
    monkeypatch.delenv("PANTHEON_DEPLOYMENT_SHA", raising=False)
    kwargs = {"dsn": dsn, "tenant_id": "tenant-image-test"}
    if service == "research":
        from services.source_ingestion.controller_state import ControllerState
        kwargs = {"dsn": dsn, "state": ControllerState(
            controller_id="test-process", controller_name="alpha-test", environment="image-test",
            tenant_id="tenant-image-test", deployment={"git_sha": SHA},
        )}
    elif service == "training-session":
        kwargs = {}
    elif service == "control-plane-bff":
        kwargs = {}
    elif service == "deployment":
        kwargs = {"consumer_name": "deployment-outbox-consumer"}
    elif service == "paper_fleet_reconciler":
        kwargs = {
            "dsn": dsn, "tenant_id": "tenant-image-test",
            "controller_id": "image-test", "lease_duration_seconds": 120,
        }
    elif service == "evolution":
        kwargs = {"lease_duration_seconds": 120}
    return module, lambda: getattr(module, builder_name)(**kwargs)


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_actual_worker_image_lock_includes_the_shared_writer_driver(case):
    service, compose_service, *_ = case
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    dockerfile = ROOT / compose["services"][compose_service]["build"]["dockerfile"]
    lock = f"dependencies/locks/services-{service}.txt"
    assert lock in dockerfile.read_text()
    assert "asyncpg==0.31.0" in (ROOT / lock).read_text()
    assert "jsonschema==" in (ROOT / lock).read_text()
    requirement = "runtime-requirements.txt" if service == "research" else "requirements.txt"
    requirements = (
        ROOT / "services" / REQUIREMENTS_PATH.get(service, service) / requirement
    ).read_text()
    # Some newer service requirement files leave the driver unpinned; the lock
    # above still pins it.
    assert ("asyncpg" if service in COMPOSE_ENV_KEYS else "asyncpg==0.31.0") in requirements


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_configured_builder_constructs_real_scoped_shared_writer(case, monkeypatch):
    _, build = _builder(case, monkeypatch)
    writer = build()
    assert isinstance(writer, importlib.import_module("services.loop-control").LoopControllerWriter)
    assert writer.store.dsn == DSN
    assert writer.tenant_id == "tenant-image-test"
    assert writer.environment == "image-test"
    assert writer.deployment_sha == SHA


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_configured_image_missing_driver_fails_instead_of_silent_none(case, monkeypatch):
    module, build = _builder(case, monkeypatch)

    def missing(_):
        raise ModuleNotFoundError("asyncpg", name="asyncpg")

    with monkeypatch.context() as patch:
        patch.setattr(module.importlib, "import_module", missing)
        with pytest.raises(ModuleNotFoundError, match="asyncpg"):
            build()


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_unconfigured_local_writer_remains_optional(case, monkeypatch):
    _, build = _builder(case, monkeypatch, configured=False)
    assert build() is None


def test_compose_supplies_existing_dsn_scope_and_release_identity():
    if not shutil.which("docker"):
        pytest.skip("Docker Compose unavailable; image/config acceptance not established")
    result = subprocess.run(
        ["docker", "compose", "--profile", "root", "-f", "docker-compose.yml", "config", "--format", "json"],
        cwd=ROOT, capture_output=True, text=True, timeout=45,
        env={
            "PATH": os.environ["PATH"], "HOME": os.environ["HOME"],
            "DATABASE_URL": DSN, "PANTHEON_BFF_TENANT_ID": "tenant-image-test",
            "PANTHEON_ENV": "image-test", "GIT_SHA": SHA,
        },
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    for service, name, *_ in CASES:
        env = services[name]["environment"]
        if service in COMPOSE_ENV_KEYS:
            for key in COMPOSE_ENV_KEYS[service]:
                expected = DSN if key in {"DATABASE_URL", "RECONCILER_LOOP_CONTROLLER_DSN"} else None
                assert key in env, (name, key)
                if expected:
                    assert env[key] == expected, (name, key)
            continue
        assert env["DATABASE_URL"] == DSN, name
        assert env["PANTHEON_ENV"] == "image-test", name
        assert env["GIT_SHA"] == SHA, name
        tenant_key = {
            "training-session": "TRAINING_SESSION_TENANT_ID",
            "policy-learning": "POLICY_LEARNING_AGORA_TENANT_ID",
        }.get(service, "PANTHEON_TENANT_ID")
        assert env[tenant_key] == "tenant-image-test", name
