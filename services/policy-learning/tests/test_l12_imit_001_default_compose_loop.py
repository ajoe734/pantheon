"""L12-IMIT-001 proof: the *default compose* imitation loop runs through auth.

The other proof modules configure the service through fixtures.  That is enough
to show the code is correct and not enough to show the shipped stack is wired,
which is a different claim: ``docker-compose.yml`` previously handed neither
``policy-learning-svc`` nor the ``policy-learning-shadow-eval-scheduler``
sidecar a service credential or a tenant scope, so on a default ``docker
compose up`` the sidecar sent no ``Authorization`` and no ``X-Tenant-Id``, every
tick answered 401, and the product loop discovered nothing.

This module proves the compose default itself:

  1. ``docker compose --profile policy-learning-shadow-eval-scheduler config``
     renders one shared credential and one tenant scope for both services;
  2. the offline renderer used by the end-to-end proofs below agrees with that
     command, so those proofs really run on the compose default;
  3. the scheduler sidecar, configured *only* from the rendered compose
     environment, drives restart recovery, a tick, and a claim cycle through
     the authenticated tenant-bound routes without a single 401 or 403;
  4. with a real Postgres, that same compose environment reaches real
     tenant-scoped ``agora.agora_dataset_records`` rows and trains on them;
  5. the published compose token is refused once the deployment posture is
     staging or production, so it cannot become a real deployment's credential.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping
from unittest import mock

import pytest
import yaml


SERVICE_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = SERVICE_DIR.parents[1]
COMPOSE_FILE = _REPO_ROOT / "docker-compose.yml"

API_SERVICE = "policy-learning-svc"
SCHEDULER_SERVICE = "policy-learning-shadow-eval-scheduler"
SCHEDULER_PROFILE = "policy-learning-shadow-eval-scheduler"

POSTGRES_DSN = os.getenv("PANTHEON_TEST_POSTGRES_DSN", "").strip()

# Environment prefixes compose interpolates from.  They are stripped before the
# `docker compose config` subprocess runs so the rendering under test is the
# default one an operator gets from a clean checkout, not this worker's env.
_INTERPOLATED_PREFIXES = (
    "POLICY_LEARNING_",
    "AGORA_",
    "PANTHEON_",
    "SHADOW_EVAL_",
    "DATABASE_URL",
)

SEED_MARKERS = ("alpha-mean-reversion", "traj-smoke-2026-05-16", "trader-01")

# The direct routes inbound authority protects.  Kept here rather than imported
# from the sibling proof module so this module stays runnable on its own.
PROTECTED_ROUTES = (
    ("POST", "/api/policy-learning/shadow-eval-tick", {"tick_id": "tick-published-credential"}),
    ("GET", "/api/policy-learning/candidates", None),
    ("GET", "/api/policy-learning/candidates/sic-anything", None),
    ("GET", "/api/policy-learning/candidates/sic-anything/governance", None),
    ("POST", "/api/policy-learning/candidates/sic-anything/promote", None),
    ("GET", "/api/policy-learning/worker/backlog", None),
    ("GET", "/api/policy-learning/worker/claims", None),
    ("GET", "/api/policy-learning/worker/degraded", None),
    ("GET", "/api/policy-learning/worker/dlq", None),
    ("GET", "/api/policy-learning/worker/readback/sic-anything", None),
    ("POST", "/api/policy-learning/worker/claim", {}),
    ("POST", "/api/policy-learning/worker/process", {}),
    ("POST", "/api/policy-learning/worker/restart", {}),
    ("POST", "/api/policy-learning/worker/retry/sic-anything", {}),
    ("POST", "/api/policy-learning/worker/dlq/sic-anything/replay", {}),
)


def _inbound_authority_module():
    """Load ``inbound_authority`` under a private name.

    ``_compose_service`` deliberately evicts the shared ``inbound_authority``
    entry from ``sys.modules``, so the constant-level proofs load their own copy
    instead of depending on whether a compose proof ran first.  The module reads
    its environment on every call, so a separate instance behaves identically.
    """

    name = "policy_learning_compose_test_inbound_authority"
    spec = importlib.util.spec_from_file_location(name, SERVICE_DIR / "inbound_authority.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    # Registered before execution because the module defines dataclasses, which
    # resolve their annotations through ``sys.modules[cls.__module__]``.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def policy_learning_service_auth() -> Iterator[None]:
    """Cancel the package-wide test-credential fixture for this module.

    ``tests/conftest.py`` injects a service token and tenant list into the
    environment of every policy-learning test.  That is the very thing this
    module must not inherit: the claim under proof is that *compose* configures
    the service, so the only credentials in scope here are the rendered ones.
    """

    yield


# ---------------------------------------------------------------------------
# Compose rendering
# ---------------------------------------------------------------------------


def _interpolate(value: str, env: Mapping[str, str]) -> str:
    """Resolve ``${VAR}`` / ``${VAR:-default}``, including nested defaults.

    Compose's own substitution rules, implemented here so the end-to-end proofs
    below can run the compose default without requiring the docker CLI.
    ``test_offline_compose_renderer_matches_docker_compose_config`` keeps this
    honest by diffing it against the real command.
    """

    out: list[str] = []
    index = 0
    while index < len(value):
        if value.startswith("${", index):
            depth = 1
            cursor = index + 2
            while cursor < len(value) and depth:
                if value[cursor] == "{":
                    depth += 1
                elif value[cursor] == "}":
                    depth -= 1
                cursor += 1
            name, separator, default = value[index + 2 : cursor - 1].partition(":-")
            resolved = env.get(name, "")
            if not resolved and separator:
                resolved = _interpolate(default, env)
            out.append(resolved)
            index = cursor
        else:
            out.append(value[index])
            index += 1
    return "".join(out)


def render_compose_environment(service: str, env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment compose gives one service, with no operator overrides."""

    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    declared = compose["services"][service].get("environment") or {}
    return {key: _interpolate(str(value), env or {}) for key, value in declared.items()}


def _docker_compose_config() -> dict[str, Any] | None:
    """``docker compose config`` for the scheduler profile, or None if absent."""

    if shutil.which("docker") is None:
        return None
    clean_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(_INTERPOLATED_PREFIXES)
    }
    try:
        completed = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                os.devnull,
                "--profile",
                SCHEDULER_PROFILE,
                "config",
                "--format",
                "json",
            ],
            cwd=_REPO_ROOT,
            env=clean_env,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return json.loads(completed.stdout)


# ---------------------------------------------------------------------------
# Service under the compose environment
# ---------------------------------------------------------------------------


@contextmanager
def _compose_service(overrides: Mapping[str, str]) -> Iterator[Any]:
    """Load policy-learning under the rendered compose environment.

    Only the two values a container gets from outside the image are replaced:
    the mounted data directory, and whatever the individual proof supplies for
    the database.  Every credential, tenant, and backend setting is the compose
    default.
    """

    environment = render_compose_environment(API_SERVICE)
    environment.update(overrides)

    for key in list(sys.modules):
        if "policy_learning_compose_test" in key:
            del sys.modules[key]
    for key in ("store", "agora_dataset_authority", "inbound_authority"):
        sys.modules.pop(key, None)
    for path in (str(SERVICE_DIR), str(_REPO_ROOT)):
        if path not in sys.path:
            sys.path.insert(0, path)

    stale = {
        key: ""
        for key in os.environ
        if key.startswith(_INTERPOLATED_PREFIXES) and key not in environment
    }
    try:
        with mock.patch.dict("os.environ", {**stale, **environment}):
            for key in stale:
                os.environ.pop(key, None)
            spec = importlib.util.spec_from_file_location(
                "policy_learning_compose_test_main", SERVICE_DIR / "main.py"
            )
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules["policy_learning_compose_test_main"] = module
            spec.loader.exec_module(module)
            yield module
    finally:
        sys.modules.pop("store", None)


def _load_scheduler_module():
    sys.modules.pop("policy_learning_compose_test_scheduler", None)
    spec = importlib.util.spec_from_file_location(
        "policy_learning_compose_test_scheduler", SERVICE_DIR / "scheduler_worker.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["policy_learning_compose_test_scheduler"] = module
    spec.loader.exec_module(module)
    return module


class _SidecarTransport:
    """Route ``urllib`` calls from the scheduler into the service under test.

    The scheduler builds its own request — URL, method, body, and crucially its
    ``Authorization`` and ``X-Tenant-Id`` headers — and this transport hands
    that request to the real FastAPI app unchanged.  A missing or wrong
    credential therefore fails here exactly as it would over the network.
    """

    def __init__(self, app: Any) -> None:
        from fastapi.testclient import TestClient

        self.client = TestClient(app)
        self.exchanges: list[dict[str, Any]] = []

    def __call__(self, request: urllib.request.Request, timeout: float | None = None):
        path = urllib.parse.urlsplit(request.full_url).path
        headers = dict(request.header_items())
        response = self.client.request(
            request.get_method(), path, content=request.data, headers=headers
        )
        self.exchanges.append(
            {
                "method": request.get_method(),
                "path": path,
                "headers": headers,
                "status": response.status_code,
            }
        )
        if response.status_code >= 400:
            raise urllib.error.HTTPError(
                request.full_url,
                response.status_code,
                response.reason_phrase,
                dict(response.headers),
                io.BytesIO(response.content),
            )
        return _TransportResponse(response.content)

    @property
    def statuses(self) -> list[int]:
        return [exchange["status"] for exchange in self.exchanges]


class _TransportResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_TransportResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _agora_record(
    dataset_version_id: str,
    *,
    tenant_id: str,
    user_id: str = "",
    order: int = 0,
) -> dict[str, Any]:
    """One row shaped like ``agora.agora_dataset_records``."""

    evidence_id = f"ev-{tenant_id}-{dataset_version_id}"
    return {
        "evidence_id": evidence_id,
        "dataset_version_id": dataset_version_id,
        "dataset_kind": "learn",
        "interaction_kind": "feedback",
        "persona_id": "persona-1",
        "session_id": "session-1",
        "tenant_id": tenant_id,
        "user_id": user_id,
        "content": {
            "steps": [
                {
                    "observation": [0.9, 0.1, -0.2],
                    "action": "buy_small",
                    "reward": 0.3,
                    "feedback_event_id": f"{evidence_id}-a",
                },
                {
                    "observation": [-0.8, 0.2, 0.55],
                    "action": "reduce_risk",
                    "reward": 0.1,
                    "feedback_event_id": f"{evidence_id}-b",
                },
            ]
        },
        "source_refs": ["artifact://source-1"],
        "learning_eligible": True,
        "captured_at": "2026-07-26T00:00:00Z",
        "extracted_at": f"2026-07-26T00:00:{order:02d}Z",
        "version": 1,
    }


def _run_sidecar_cycle(svc, scheduler) -> dict[str, Any]:
    """Run the sidecar's start-up recovery, tick, and claim cycle."""

    api_url = render_compose_environment(SCHEDULER_SERVICE)["POLICY_LEARNING_API_URL"]
    _, tenant_id = scheduler.require_caller_configuration()
    transport = _SidecarTransport(svc.app)
    with mock.patch("urllib.request.urlopen", transport):
        recovery = scheduler.run_recovery(
            api_url=api_url, worker="shadow-eval-compose", tenant_id=tenant_id
        )
        tick = scheduler.run_tick(
            api_url=api_url,
            tick_id="tick-default-compose",
            eval_type="imitation",
            tenant_id=tenant_id,
        )
        cycle = scheduler.run_claim_cycle(
            api_url=api_url, worker="shadow-eval-compose", tenant_id=tenant_id
        )
    return {
        "api_url": api_url,
        "tenant_id": tenant_id,
        "recovery": recovery,
        "tick": tick,
        "cycle": cycle,
        "transport": transport,
    }


def _assert_no_seed_leak(payload: Any) -> None:
    serialized = json.dumps(payload, default=str)
    for marker in SEED_MARKERS:
        assert marker not in serialized, f"seed fixture leaked into product output: {marker}"


# ---------------------------------------------------------------------------
# Proof 1 — the compose default wires one credential and one tenant scope
# ---------------------------------------------------------------------------


def test_default_compose_gives_api_and_scheduler_one_credential_and_tenant_scope() -> None:
    """The exact independent command a reviewer runs must show the wiring."""

    config = _docker_compose_config()
    if config is None:
        pytest.skip("docker compose is not available")

    api = config["services"][API_SERVICE]["environment"]
    scheduler = config["services"][SCHEDULER_SERVICE]["environment"]

    token = api["POLICY_LEARNING_SERVICE_TOKEN"]
    tenant_id = api["POLICY_LEARNING_AGORA_TENANT_ID"]
    assert token, "policy-learning-svc has no service credential"
    assert tenant_id, "policy-learning-svc has no tenant scope"

    # One credential: the sidecar cannot authenticate against a different one.
    assert scheduler["POLICY_LEARNING_SERVICE_TOKEN"] == token
    # One tenant scope, and the API authorizes the tenant the sidecar sends.
    assert scheduler["POLICY_LEARNING_AGORA_TENANT_ID"] == tenant_id
    assert tenant_id in api["POLICY_LEARNING_SERVICE_TENANTS"].replace(",", " ").split()

    # The posture that decides whether the published default token is usable is
    # rendered for both services, so they agree about it.
    assert scheduler["PANTHEON_PERSISTENCE_POSTURE"] == api["PANTHEON_PERSISTENCE_POSTURE"]


def test_operator_tenant_override_widens_both_services_together() -> None:
    """Renaming the tenant must not leave the API authorizing the old one."""

    rendered_api = render_compose_environment(API_SERVICE, {"POLICY_LEARNING_AGORA_TENANT_ID": "acme"})
    rendered_scheduler = render_compose_environment(
        SCHEDULER_SERVICE, {"POLICY_LEARNING_AGORA_TENANT_ID": "acme"}
    )
    assert rendered_api["POLICY_LEARNING_AGORA_TENANT_ID"] == "acme"
    assert rendered_api["POLICY_LEARNING_SERVICE_TENANTS"] == "acme"
    assert rendered_scheduler["POLICY_LEARNING_AGORA_TENANT_ID"] == "acme"

    # An explicit tenant list still wins over the derived default.
    widened = render_compose_environment(
        API_SERVICE,
        {"POLICY_LEARNING_AGORA_TENANT_ID": "acme", "POLICY_LEARNING_SERVICE_TENANTS": "acme,beta"},
    )
    assert widened["POLICY_LEARNING_SERVICE_TENANTS"] == "acme,beta"


def test_offline_compose_renderer_matches_docker_compose_config() -> None:
    """The renderer the end-to-end proofs run on is the real compose default."""

    config = _docker_compose_config()
    if config is None:
        pytest.skip("docker compose is not available")

    for service in (API_SERVICE, SCHEDULER_SERVICE):
        rendered = render_compose_environment(service)
        official = {key: str(value) for key, value in config["services"][service]["environment"].items()}
        assert rendered == official, service


# ---------------------------------------------------------------------------
# Proof 2 — the sidecar authenticates end to end on the compose default
# ---------------------------------------------------------------------------


def test_default_compose_environment_authenticates_the_whole_sidecar_loop() -> None:
    """Restart recovery, tick, and claim all succeed with no fixture auth.

    Nothing here supplies a token or a tenant: both come from the rendered
    compose environment, exactly as they would inside the two containers.
    """

    scheduler = _load_scheduler_module()
    with tempfile.TemporaryDirectory() as data_dir:
        with _compose_service(
            {
                "POLICY_LEARNING_DATA_DIR": data_dir,
                # This proof isolates the credential and tenant wiring from the
                # database; the Postgres proof below runs the same compose
                # environment against real Agora rows.
                "POLICY_LEARNING_CANDIDATE_AUTHORITY": "json",
                "DATABASE_URL": "",
            }
        ) as svc:
            tenant_id = os.environ["POLICY_LEARNING_AGORA_TENANT_ID"]
            authority_module = sys.modules["agora_dataset_authority"]
            svc.DATASET_AUTHORITY = authority_module.AgoraDatasetAuthority(
                records=[
                    _agora_record("dsv-compose-1", tenant_id=tenant_id, order=1),
                    _agora_record("dsv-compose-2", tenant_id=tenant_id, order=2),
                ]
            )

            configuration = svc.authority_configuration()
            assert configuration["configured"] is True
            assert configuration["service_token_configured"] is True
            assert configuration["service_tenants_configured"] is True

            result = _run_sidecar_cycle(svc, scheduler)

    # Every exchange was authenticated and tenant-bound, and none was refused.
    assert result["transport"].exchanges, "the sidecar made no requests"
    for exchange in result["transport"].exchanges:
        headers = {key.lower(): value for key, value in exchange["headers"].items()}
        assert headers.get("authorization", "").startswith("Bearer ")
        assert headers["x-tenant-id"] == result["tenant_id"]
        assert exchange["status"] < 400, exchange

    assert result["recovery"]["tenant_id"] == result["tenant_id"]
    assert result["tick"]["dataset_source"] == "agora_dataset_version"
    assert result["tick"]["dataset_mode"] == "product"
    assert result["tick"]["seed_fallback_used"] is False
    assert result["tick"]["candidate_count"] == 2
    assert result["cycle"]["processed_count"] == 2
    assert result["cycle"]["degraded_count"] == 0
    assert result["cycle"]["failed_count"] == 0
    _assert_no_seed_leak(result)


def test_a_compose_scheduler_without_the_wiring_is_refused_before_it_ticks() -> None:
    """The regression this task closes, asserted as a negative.

    Before the compose wiring the sidecar started with no credential and no
    tenant and scheduled 401s forever.  It now refuses to start, and if it were
    started anyway the route surface still refuses it.
    """

    scheduler = _load_scheduler_module()
    with tempfile.TemporaryDirectory() as data_dir:
        with _compose_service(
            {
                "POLICY_LEARNING_DATA_DIR": data_dir,
                "POLICY_LEARNING_CANDIDATE_AUTHORITY": "json",
                "DATABASE_URL": "",
            }
        ) as svc:
            tenant_id = os.environ["POLICY_LEARNING_AGORA_TENANT_ID"]
            authority_module = sys.modules["agora_dataset_authority"]
            svc.DATASET_AUTHORITY = authority_module.AgoraDatasetAuthority(
                records=[_agora_record("dsv-compose-1", tenant_id=tenant_id, order=1)]
            )

            with mock.patch.dict(
                os.environ,
                {"POLICY_LEARNING_SERVICE_TOKEN": "", "POLICY_LEARNING_API_TOKEN": ""},
            ):
                with pytest.raises(scheduler.SchedulerConfigurationError):
                    scheduler.require_caller_configuration()
                assert scheduler.main() == 2

                transport = _SidecarTransport(svc.app)
                with mock.patch("urllib.request.urlopen", transport):
                    unauthenticated = scheduler.run_tick(
                        api_url="http://policy-learning-svc:8100",
                        tick_id="tick-unwired",
                        tenant_id=tenant_id,
                    )
            assert unauthenticated["status"] == "error"
            assert unauthenticated["code"] == 401
            assert svc.store.list_candidates() == []

            with mock.patch.dict(os.environ, {"POLICY_LEARNING_AGORA_TENANT_ID": ""}):
                with pytest.raises(scheduler.SchedulerConfigurationError):
                    scheduler.require_caller_configuration()


# ---------------------------------------------------------------------------
# Proof 3 — the compose default reaches real tenant-scoped Agora data
# ---------------------------------------------------------------------------


def _create_agora_table(conn, schema: str) -> None:
    conn.execute(f'CREATE SCHEMA "{schema}"')
    conn.execute(
        f"""
        CREATE TABLE "{schema}"."agora_dataset_records" (
            evidence_id TEXT PRIMARY KEY,
            dataset_version_id TEXT,
            dataset_kind TEXT,
            interaction_kind TEXT,
            persona_id TEXT,
            session_id TEXT,
            tenant_id TEXT,
            user_id TEXT,
            content JSONB,
            source_refs JSONB,
            learning_eligible BOOLEAN,
            captured_at TEXT,
            extracted_at TEXT,
            version INTEGER
        )
        """
    )


def _insert_agora_record(conn, schema: str, record: Mapping[str, Any]) -> None:
    conn.execute(
        f'INSERT INTO "{schema}"."agora_dataset_records" '
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s)",
        (
            record["evidence_id"],
            record["dataset_version_id"],
            record["dataset_kind"],
            record["interaction_kind"],
            record["persona_id"],
            record["session_id"],
            record["tenant_id"],
            record["user_id"],
            json.dumps(record["content"]),
            json.dumps(record["source_refs"]),
            record["learning_eligible"],
            record["captured_at"],
            record["extracted_at"],
            record["version"],
        ),
    )


@pytest.mark.skipif(not POSTGRES_DSN, reason="PANTHEON_TEST_POSTGRES_DSN is not configured")
def test_default_compose_environment_trains_on_real_tenant_scoped_agora_rows() -> None:
    """The full product claim, end to end, on the compose default.

    Only the database location is supplied by the test — the same two values a
    deployment supplies from its own cluster.  The credential, the tenant
    scope, the dataset mode, and the candidate authority are all the compose
    defaults, and the data is real rows in a real
    ``<schema>.agora_dataset_records`` table read over a real connection.
    """

    import psycopg

    schema = f"agora_compose_it_{uuid.uuid4().hex[:16]}"
    candidate_schema = f"pl_compose_it_{uuid.uuid4().hex[:16]}"
    compose_tenant = render_compose_environment(API_SERVICE)["POLICY_LEARNING_AGORA_TENANT_ID"]
    other_tenant = f"tenant-{uuid.uuid4().hex[:8]}"

    scheduler = _load_scheduler_module()
    try:
        with psycopg.connect(POSTGRES_DSN) as conn:
            _create_agora_table(conn, schema)
            for index in range(2):
                _insert_agora_record(
                    conn, schema, _agora_record(f"dsv-{index}", tenant_id=compose_tenant, order=index)
                )
            # A neighbouring tenant's rows sit in the same table; the compose
            # tenant scope is what must keep them out of this loop.
            _insert_agora_record(
                conn, schema, _agora_record("dsv-neighbour", tenant_id=other_tenant, order=9)
            )
            conn.execute(f'CREATE SCHEMA "{candidate_schema}"')

        with tempfile.TemporaryDirectory() as data_dir:
            with _compose_service(
                {
                    "POLICY_LEARNING_DATA_DIR": data_dir,
                    "DATABASE_URL": POSTGRES_DSN,
                    "AGORA_DATASET_STORE_SCHEMA": schema,
                    "POLICY_LEARNING_CANDIDATE_STORE_TABLE": f"{candidate_schema}.candidates",
                }
            ) as svc:
                described = svc.DATASET_AUTHORITY.describe()
                assert described["backend"] == "postgres"
                assert described["authenticated"] is True
                assert described["seed_fallback"] == "none"
                # The candidate backlog is the durable authority, not the
                # JSON volume, on the compose default.
                assert svc.store.authority_resolution["candidate_authority"] == "postgres"

                result = _run_sidecar_cycle(svc, scheduler)

                assert result["tenant_id"] == compose_tenant
                assert result["tick"]["dataset_source"] == "agora_dataset_version"
                assert result["tick"]["seed_fallback_used"] is False
                assert result["tick"]["candidate_count"] == 2
                assert result["cycle"]["processed_count"] == 2
                assert result["cycle"]["degraded_count"] == 0

                candidates = svc.store.list_candidates()
                assert len(candidates) == 2
                for candidate in candidates:
                    lineage = candidate["dataset_lineage"]
                    assert candidate["status"] == "processed"
                    assert lineage["authority"] == "agora.dataset.v1"
                    assert lineage["tenant_id"] == compose_tenant
                    assert lineage["authoritative"] is True
                    assert lineage["seed_fallback_used"] is False
                    assert candidate["training_evaluation"]["action_match_rate"] is not None
                    assert candidate["artifact_checksum"]
                assert {
                    candidate["dataset_ref"]["dataset_version_id"] for candidate in candidates
                } == {"dsv-0", "dsv-1"}
                _assert_no_seed_leak(candidates)

                for exchange in result["transport"].exchanges:
                    assert exchange["status"] < 400, exchange
    finally:
        with psycopg.connect(POSTGRES_DSN) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.execute(f'DROP SCHEMA IF EXISTS "{candidate_schema}" CASCADE')


# ---------------------------------------------------------------------------
# Proof 4 — the published compose credential cannot become a real secret
# ---------------------------------------------------------------------------


def test_the_published_compose_token_is_refused_on_a_deployment_posture() -> None:
    """A staging/prod stack that forgot to set its own token fails closed."""

    with tempfile.TemporaryDirectory() as data_dir:
        with _compose_service(
            {
                "POLICY_LEARNING_DATA_DIR": data_dir,
                "POLICY_LEARNING_CANDIDATE_AUTHORITY": "json",
                "DATABASE_URL": "",
            }
        ) as svc:
            from fastapi.testclient import TestClient

            compose_token = os.environ["POLICY_LEARNING_SERVICE_TOKEN"]
            tenant_id = os.environ["POLICY_LEARNING_AGORA_TENANT_ID"]
            inbound = sys.modules["inbound_authority"]
            assert compose_token == inbound.LOCAL_DEV_SERVICE_TOKEN

            # On the compose dev posture it is a working in-cluster credential.
            assert svc.authority_configuration()["service_token_scope"] == "local_dev_default"

            client = TestClient(svc.app)
            headers = {
                "Authorization": f"Bearer {compose_token}",
                "X-Tenant-Id": tenant_id,
            }
            for posture in ("staging", "prod"):
                with mock.patch.dict(os.environ, {"PANTHEON_PERSISTENCE_POSTURE": posture}):
                    configuration = svc.authority_configuration()
                    assert configuration["service_token_configured"] is False
                    assert configuration["service_token_scope"] == "none"
                    assert configuration["configured"] is False

                    refused = client.post(
                        "/api/policy-learning/shadow-eval-tick",
                        json={"tick_id": f"tick-{posture}"},
                        headers=headers,
                    )
                    assert refused.status_code == 401, posture

                    # A deployment that supplies its own secret still works.
                    with mock.patch.dict(
                        os.environ, {"POLICY_LEARNING_SERVICE_TOKEN": "deployment-owned-secret"}
                    ):
                        assert (
                            svc.authority_configuration()["service_token_scope"]
                            == "deployment_secret"
                        )

            assert svc.store.list_candidates() == []


# ---------------------------------------------------------------------------
# Proof 5 — no repository-published credential authenticates a deployment
# ---------------------------------------------------------------------------
#
# The compose default is not the only credential this repository publishes.
# ``.env.example`` ships a fill-me-in ``POLICY_LEARNING_SERVICE_TOKEN``, and an
# operator who copies that file into a staging or production stack without
# editing the line is running on a value anyone with a checkout can read.  The
# two proofs below cover that: the first pins what the repository actually
# publishes to what the service refuses, the second drives the exact published
# ``.env.example`` value through every protected route on an enforced posture
# and then shows an operator-minted secret still works on the same routes.


def _env_example_service_token() -> str:
    """The literal ``POLICY_LEARNING_SERVICE_TOKEN`` shipped in .env.example."""

    for line in (_REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == "POLICY_LEARNING_SERVICE_TOKEN":
            return value.strip()
    raise AssertionError(".env.example no longer declares POLICY_LEARNING_SERVICE_TOKEN")


def _compose_service_token_defaults() -> set[str]:
    """Every ``${POLICY_LEARNING_SERVICE_TOKEN:-...}`` default in compose."""

    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    defaults: set[str] = set()
    for service in compose["services"].values():
        declared = service.get("environment") or {}
        raw = declared.get("POLICY_LEARNING_SERVICE_TOKEN")
        if raw is None:
            continue
        # Render with an empty environment: that is the default an operator who
        # sets nothing actually gets.
        rendered = _interpolate(str(raw), {})
        if rendered:
            defaults.add(rendered)
    assert defaults, "docker-compose.yml no longer declares POLICY_LEARNING_SERVICE_TOKEN"
    return defaults


def test_every_repository_published_service_token_is_one_the_service_refuses() -> None:
    """The refusal list cannot drift behind what the repository ships.

    ``inbound_authority`` has to carry these values as constants — a container
    has no ``.env.example`` to consult at runtime — so the guard against a new
    published default quietly becoming an accepted deployment secret is this
    test reading the tracked files and checking them against that list.
    """

    inbound_authority = _inbound_authority_module()

    published = set(_compose_service_token_defaults()) | {_env_example_service_token()}

    assert published, "no published policy-learning credential found to check"
    for value in sorted(published):
        assert value in inbound_authority.PUBLISHED_SERVICE_TOKENS, (
            f"{value!r} is published in this repository but is not in "
            "PUBLISHED_SERVICE_TOKENS, so a staging/prod deployment would "
            "accept it as a real credential"
        )
        assert inbound_authority.is_published_service_token(value)

    # The constants are the published values, not a superset someone widened.
    assert set(inbound_authority.PUBLISHED_SERVICE_TOKENS) == published
    assert inbound_authority.ENV_EXAMPLE_SERVICE_TOKEN == _env_example_service_token()

    # An operator-minted secret is untouched by the rule.
    assert inbound_authority.is_published_service_token("s3cr3t-minted-by-ops") is False
    assert inbound_authority.is_published_service_token("") is False


def test_the_env_example_placeholder_cannot_authenticate_a_deployment() -> None:
    """The exact published .env.example value is refused on staging/prod.

    This is the deployment an operator most plausibly ships by accident: they
    copied ``.env.example``, filled in the database and the tenant, and left the
    credential line as published.  Every protected route must answer 401, and a
    real minted secret must still work on those same routes so the refusal is
    the placeholder being rejected, not the service being broken.
    """

    example_token = _env_example_service_token()

    with tempfile.TemporaryDirectory() as data_dir:
        with _compose_service(
            {
                "POLICY_LEARNING_DATA_DIR": data_dir,
                "POLICY_LEARNING_CANDIDATE_AUTHORITY": "json",
                "DATABASE_URL": "",
                # The operator's own .env, copied from the example unedited.
                "POLICY_LEARNING_SERVICE_TOKEN": example_token,
            }
        ) as svc:
            from fastapi.testclient import TestClient

            tenant_id = os.environ["POLICY_LEARNING_AGORA_TENANT_ID"]
            inbound = sys.modules["inbound_authority"]
            assert example_token == inbound.ENV_EXAMPLE_SERVICE_TOKEN
            assert example_token != inbound.LOCAL_DEV_SERVICE_TOKEN

            client = TestClient(svc.app)
            placeholder_headers = {
                "Authorization": f"Bearer {example_token}",
                "X-Tenant-Id": tenant_id,
            }

            # On the dev posture it is still a working local credential, but the
            # configuration names it for what it is rather than calling it a
            # deployment secret.
            assert svc.authority_configuration()["service_token_scope"] == "published_placeholder"
            accepted = client.post(
                "/api/policy-learning/worker/restart",
                json={},
                headers=placeholder_headers,
            )
            assert accepted.status_code < 400, accepted.text

            minted = "ops-minted-policy-learning-6f2c1d9a"
            for posture in ("staging", "prod"):
                with mock.patch.dict(os.environ, {"PANTHEON_PERSISTENCE_POSTURE": posture}):
                    configuration = svc.authority_configuration()
                    assert configuration["service_token_configured"] is False, posture
                    assert configuration["service_token_scope"] == "none", posture
                    assert configuration["configured"] is False, posture

                    # Negative: the published placeholder authenticates nothing.
                    for method, path, body in PROTECTED_ROUTES:
                        refused = client.request(
                            method, path, json=body, headers=placeholder_headers
                        )
                        assert refused.status_code == 401, (
                            f"{posture} {method} {path} answered {refused.status_code} "
                            "for the published .env.example credential"
                        )

                    # Positive control: the same routes on the same posture, with
                    # an operator-minted secret, authenticate normally.
                    with mock.patch.dict(
                        os.environ, {"POLICY_LEARNING_SERVICE_TOKEN": minted}
                    ):
                        configuration = svc.authority_configuration()
                        assert configuration["service_token_configured"] is True, posture
                        assert configuration["service_token_scope"] == "deployment_secret", posture

                        for method, path, body in PROTECTED_ROUTES:
                            answered = client.request(
                                method,
                                path,
                                json=body,
                                headers={
                                    "Authorization": f"Bearer {minted}",
                                    "X-Tenant-Id": tenant_id,
                                },
                            )
                            assert answered.status_code != 401, (
                                f"{posture} {method} {path} refused an operator-minted "
                                "secret with 401"
                            )

                        # And the placeholder is still refused while the minted
                        # secret is the configured one, so the two are not
                        # interchangeable.
                        still_refused = client.post(
                            "/api/policy-learning/shadow-eval-tick",
                            json={"tick_id": f"tick-{posture}-placeholder"},
                            headers=placeholder_headers,
                        )
                        assert still_refused.status_code == 401, posture


@pytest.mark.parametrize(
    "unedited",
    [
        "REPLACE_ME_POLICY_LEARNING_SERVICE_TOKEN",
        "changeme",
        "change-me-please",
        "your-service-token-here",
        "example-token",
        "placeholder",
        "secret",
        "TBD",
    ],
)
def test_unedited_placeholder_shapes_are_refused_on_a_deployment_posture(unedited: str) -> None:
    """Fill-me-in values other than the two this repository ships are refused.

    A template copied out of a runbook or a chart is the same failure as the
    ``.env.example`` copy above; the posture rule is not a list of two strings.
    """

    inbound_authority = _inbound_authority_module()

    assert inbound_authority.is_published_service_token(unedited) is True

    with mock.patch.dict(
        os.environ,
        {
            "POLICY_LEARNING_SERVICE_TOKEN": unedited,
            "PANTHEON_PERSISTENCE_POSTURE": "prod",
        },
    ):
        assert inbound_authority.service_token() == ""
        assert inbound_authority.authority_configuration()["service_token_configured"] is False

    # The same value is still usable on a developer posture: this rule is about
    # what a deployment may authenticate on, not about breaking local work.
    with mock.patch.dict(
        os.environ,
        {
            "POLICY_LEARNING_SERVICE_TOKEN": unedited,
            "PANTHEON_PERSISTENCE_POSTURE": "dev",
        },
    ):
        assert inbound_authority.service_token() == unedited
