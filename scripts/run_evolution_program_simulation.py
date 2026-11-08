#!/usr/bin/env python3
"""Isolated local simulation of one synthetic Evolution stimulus through approval and research.

Operator-authorized synthetic acceptance simulation (EVOLUTION-ISOLATED-STIMULUS-20261107).
This runner exercises the real Evolution qualifier, real Governance approval owner,
real Evolution outbox dispatch worker, real Research Orchestrator retrain executor,
real Training Session evaluation service (with real local deterministic vectorbt),
real Registry owner, real Persona owner, real Telemetry projection/event owner, and
real Reconciliation-Drift + Incident consumer owners against isolated temporary
stores on 127.0.0.1.

Evidence level: local_integration_simulation (NOT natural-market historical qualification).
"""
from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Iterator, Mapping
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import uvicorn

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SHARED_POLICY_PATH = (
    REPO_ROOT / "services" / "evolution" / "config" / "program_qualification_policy.json"
)
SIMULATION_CASE_ID = "sim-case-isolated-stimulus-20261107"
DEFAULT_SYNTHETIC_TENANT = "tenant-isolated-sim-20261107"
DEFAULT_SYNTHETIC_PERSONA_ID = "persona-sim-isolated-20261107"
DEFAULT_SYNTHETIC_ARTIFACT_ID = "artifact-sim-isolated-20261107-v1"
DEFAULT_SYNTHETIC_STRATEGY_ID = "strategy-sim-isolated-20261107"
DEFAULT_SYNTHETIC_BINDING_ID = "bind-sim-isolated-20261107"
DEFAULT_SYNTHETIC_RUNTIME_ID = "rt-sim-isolated-20261107"
DEFAULT_SYNTHETIC_POOL_ID = "pool-sim-isolated-20261107"
DEFAULT_SYNTHETIC_PLAN_ID = "plan-sim-isolated-20261107"
DEFAULT_SYNTHETIC_PCB_ID = "pcb-sim-isolated-20261107"
SIMULATION_PROPOSER_ID = "sim-human-operator-proposer-20261107"
SIMULATION_REVIEWER_ID = "sim-human-governance-reviewer-20261107"
SIMULATION_POLICY_VERSION = "2026-11-05.1-isolated-simulation-20261107"
INSTRUMENTS = ("2330.TW", "2317.TW")
BARS = 60

_FORBIDDEN_TENANTS = frozenset(
    {
        "default",
        "tenant-dev",
        "pantheon-system",
        "prod",
        "production",
        "live",
        "staging",
    }
)
_ALLOWED_SYNTHETIC_SCOPES = frozenset({"synthetic", "test"})
_FORBIDDEN_PATH_PREFIXES = (
    "/tmp/pantheon/",
    "/var/www/",
    "/var/lib/",
    "/etc/",
    "/srv/",
    "/opt/",
)


class SimulationRefusalError(RuntimeError):
    """Raised when a simulation guard refuses a non-isolated or non-synthetic target."""


@dataclass(frozen=True)
class SimulationConfig:
    tenant_id: str = DEFAULT_SYNTHETIC_TENANT
    data_scope: str = "synthetic"
    deployment_stage: str = "paper"
    execution_mode: str = "paper"
    target_url: str | None = None
    work_dir: Path | None = None
    allow_broker_orders: bool = False
    allow_capital_writes: bool = False
    allow_shared_policy_mutation: bool = False
    retrain_variant: str = "better"  # "better" (lookback=2 -> 3) or "worse" (lookback=3 -> 2)


def validate_simulation_config(config: SimulationConfig) -> None:
    """Fail closed before touching any store if a target or flag violates isolation."""
    if config.allow_broker_orders:
        raise SimulationRefusalError("broker_capital_refused: broker orders are forbidden in simulation")
    if config.allow_capital_writes:
        raise SimulationRefusalError("broker_capital_refused: capital writes are forbidden in simulation")
    if config.allow_shared_policy_mutation:
        raise SimulationRefusalError(
            "shared_policy_mutation_refused: shared program_qualification_policy.json must not be modified"
        )

    tenant = str(config.tenant_id or "").strip()
    if not tenant or tenant.lower() in _FORBIDDEN_TENANTS:
        raise SimulationRefusalError(
            f"non_synthetic_tenant_refused: tenant_id {tenant!r} is a shared or production tenant"
        )
    if not any(marker in tenant.lower() for marker in ("sim", "synthetic", "isolated")):
        raise SimulationRefusalError(
            f"non_synthetic_tenant_refused: tenant_id {tenant!r} must carry an explicit synthetic/simulation marker"
        )

    scope = str(config.data_scope or "").strip().lower()
    if scope not in _ALLOWED_SYNTHETIC_SCOPES:
        raise SimulationRefusalError(
            f"non_synthetic_data_scope_refused: data_scope {config.data_scope!r} must be in {sorted(_ALLOWED_SYNTHETIC_SCOPES)}"
        )

    if str(config.deployment_stage or "").strip().lower() != "paper":
        raise SimulationRefusalError(
            f"non_paper_stage_refused: deployment_stage {config.deployment_stage!r} must be 'paper'"
        )
    if str(config.execution_mode or "").strip().lower() != "paper":
        raise SimulationRefusalError(
            f"non_paper_mode_refused: execution_mode {config.execution_mode!r} must be 'paper'"
        )

    if config.target_url is not None:
        parsed = urlsplit(str(config.target_url).strip())
        host = (parsed.hostname or "").lower()
        if host not in {"127.0.0.1", "localhost"}:
            raise SimulationRefusalError(
                f"non_local_target_refused: target_url {config.target_url!r} points outside local loopback"
            )

    if config.work_dir is not None:
        resolved = str(Path(config.work_dir).resolve())
        if resolved == "/tmp/pantheon" or any(
            resolved.startswith(prefix) for prefix in _FORBIDDEN_PATH_PREFIXES
        ):
            raise SimulationRefusalError(
                f"shared_state_path_refused: work_dir {resolved!r} is inside a shared runtime directory"
            )
        repo_resolved = str(REPO_ROOT.resolve())
        if resolved == repo_resolved or resolved.startswith(repo_resolved + "/"):
            raise SimulationRefusalError(
                f"shared_state_path_refused: work_dir {resolved!r} must not be inside the repository checkout"
            )


def _verify_shared_policy_untouched(expected_sha256: str | None = None) -> dict[str, Any]:
    raw_bytes = SHARED_POLICY_PATH.read_bytes()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    parsed = json.loads(raw_bytes.decode("utf-8"))
    if parsed.get("allow_test_scope") is not False:
        raise SimulationRefusalError(
            "shared_policy_invalid: services/evolution/config/program_qualification_policy.json has allow_test_scope != false"
        )
    if expected_sha256 is not None and digest != expected_sha256:
        raise SimulationRefusalError(
            "shared_policy_modified: services/evolution/config/program_qualification_policy.json digest changed during simulation"
        )
    return {
        "path": str(SHARED_POLICY_PATH.relative_to(REPO_ROOT)),
        "sha256": digest,
        "policy_version": parsed.get("policy_version"),
        "allow_test_scope": parsed.get("allow_test_scope"),
        "min_distinct_breach_days": parsed.get("min_distinct_breach_days"),
    }


def _mint_hs256_jwt(
    secret: str,
    *,
    sub: str,
    tenant_id: str,
    roles: list[str],
    issuer: str,
    audience: str,
    scope: str = "pantheon:isolated-simulation",
    service: str | None = None,
    now_epoch: int | None = None,
) -> str:
    issued = int(time.time()) if now_epoch is None else int(now_epoch)
    claims: dict[str, Any] = {
        "sub": sub,
        "service": service or sub,
        "tenant_id": tenant_id,
        "allowed_tenants": [tenant_id],
        "roles": list(roles),
        "scope": scope,
        "iss": issuer,
        "aud": audience,
        "iat": issued,
        "nbf": issued,
        "exp": issued + 3600,
        "jti": secrets.token_hex(16),
    }
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=")
    header_b64 = encode(b'{"alg":"HS256","typ":"JWT"}')
    payload_b64 = encode(
        json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signing_input = header_b64 + b"." + payload_b64
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return (signing_input + b"." + encode(signature)).decode("ascii")


class _UvicornThreadServer:
    """Bounded in-process uvicorn HTTP server bound to 127.0.0.1:0."""

    def __init__(self, app: Any) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = int(self.sock.getsockname()[1])
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self.server = uvicorn.Server(config)
        self.server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self.thread = threading.Thread(
            target=self.server.run,
            kwargs={"sockets": [self.sock]},
            daemon=True,
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "_UvicornThreadServer":
        self.thread.start()
        deadline = time.monotonic() + 10.0
        while not self.server.started and time.monotonic() < deadline:
            if not self.thread.is_alive():
                raise RuntimeError("Local simulation HTTP server exited before startup")
            time.sleep(0.02)
        if not self.server.started:
            raise RuntimeError("Local simulation HTTP server timed out starting")
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5.0)
        try:
            self.sock.close()
        except OSError:
            pass


def _http_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    tenant_id: str | None = None,
    body: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    req_headers: dict[str, str] = {"Accept": "application/json"}
    if token:
        req_headers["Authorization"] = f"Bearer {token}"
    if tenant_id:
        req_headers["X-Tenant-Id"] = tenant_id
    if body is not None:
        req_headers["Content-Type"] = "application/json"
    if headers:
        req_headers.update(headers)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(url, data=data, headers=req_headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return exc.code, parsed


def _deterministic_closes() -> list[float]:
    """Deterministic local 60-bar close series where lookback=3 beats lookback=2."""
    prices = [100.0]
    pattern = [0.008, 0.009, 0.011, -0.024, 0.013, 0.010, -0.004, 0.009, 0.012, -0.021]
    for idx in range(BARS - 1):
        prices.append(round(prices[-1] * (1.0 + pattern[idx % len(pattern)]), 4))
    return prices


def _build_strategy_artifact(
    *,
    artifact_id: str,
    strategy_id: str,
    persona_id: str,
    pcb_id: str,
    lookback_bars: int = 2,
) -> tuple[dict[str, Any], str]:
    from services.registry.strategy_artifact import (
        BUILTIN_STRATEGY_ARTIFACT_PATHS,
        load_strategy_artifact_registration,
        strategy_artifact_checksum,
    )

    artifact = json.loads(
        json.dumps(
            load_strategy_artifact_registration(BUILTIN_STRATEGY_ARTIFACT_PATHS[0])[
                "strategy_artifact"
            ]
        )
    )
    artifact["artifact_id"] = artifact_id
    artifact["strategy_id"] = strategy_id
    artifact["version"] = "1.0.0"
    artifact["parameters"]["symbols"] = list(INSTRUMENTS)
    artifact["parameters"]["lookback_bars"] = lookback_bars
    artifact["binding_intent"]["persona_id"] = persona_id
    artifact["binding_intent"]["persona_capital_binding_id"] = pcb_id
    for control in artifact["mutation_surface"]["controls"]:
        if control["parameter_key"] == "lookback_bars":
            control["current_value"] = lookback_bars
    return artifact, strategy_artifact_checksum(artifact)


def _load_isolated_module(module_name: str, file_path: Path, service_dir: Path) -> Any:
    service_prefix = str(service_dir.resolve())
    for bare in ("store", "models", "consumer", "telemetry_client"):
        sys.modules.pop(bare, None)
    sys.path.insert(0, str(service_dir))
    try:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load module spec for {file_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for mod_key, mod_obj in list(sys.modules.items()):
            if mod_key == module_name or "." in mod_key:
                continue
            mod_file = getattr(mod_obj, "__file__", None)
            if mod_file and str(Path(mod_file).resolve()).startswith(service_prefix):
                sys.modules.pop(mod_key, None)
        try:
            sys.path.remove(str(service_dir))
        except ValueError:
            pass


def _build_telemetry_owner_app(
    *,
    summary_store: Any,
    events_by_id: dict[str, dict[str, Any]],
    expected_token: str,
    expected_tenant: str,
) -> Any:
    from fastapi import FastAPI, Header, HTTPException, Query

    app = FastAPI(title="Isolated Telemetry Owner Surface")

    def _require_auth(authorization: str | None, x_tenant_id: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        supplied = authorization[len("Bearer ") :].strip()
        if not hmac.compare_digest(supplied, expected_token):
            raise HTTPException(status_code=401, detail="invalid telemetry bearer token")
        tenant = str(x_tenant_id or expected_tenant).strip()
        if tenant != expected_tenant:
            raise HTTPException(status_code=403, detail="telemetry tenant mismatch")
        return tenant

    @app.get("/api/telemetry/runtime-summaries")
    def list_runtime_summaries(
        include_retired: bool = Query(default=False),
        authorization: str | None = Header(default=None),
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    ) -> dict[str, Any]:
        tenant = _require_auth(authorization, x_tenant_id)
        summaries = summary_store.list(
            tenant_id=tenant, include_retired=include_retired
        )
        return {"summaries": summaries, "count": len(summaries)}

    @app.get("/api/telemetry/runtime-summaries/{runtime_id}")
    def get_runtime_summary(
        runtime_id: str,
        authorization: str | None = Header(default=None),
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    ) -> dict[str, Any]:
        tenant = _require_auth(authorization, x_tenant_id)
        summary = summary_store.get(runtime_id, tenant_id=tenant)
        if summary is None:
            raise HTTPException(status_code=404, detail="runtime summary not found")
        return summary

    @app.get("/api/telemetry/events/{event_id}")
    def get_event(
        event_id: str,
        authorization: str | None = Header(default=None),
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    ) -> dict[str, Any]:
        tenant = _require_auth(authorization, x_tenant_id)
        event = events_by_id.get(event_id)
        if event is None or str(event.get("tenant_id") or "") != tenant:
            raise HTTPException(status_code=404, detail="telemetry event not found")
        return event

    return app


@contextmanager
def _patched_environ(overrides: Mapping[str, str | None]) -> Iterator[None]:
    saved: dict[str, str | None] = {key: os.environ.get(key) for key in overrides}
    try:
        for key, value in overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)
        yield
    finally:
        for key, old_val in saved.items():
            if old_val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_val


def run_simulation(config: SimulationConfig | None = None) -> dict[str, Any]:
    """Execute one isolated synthetic Evolution stimulus end-to-end and return evidence."""
    cfg = config or SimulationConfig()
    validate_simulation_config(cfg)
    shared_policy_before = _verify_shared_policy_untouched()

    if cfg.work_dir is not None:
        Path(cfg.work_dir).mkdir(parents=True, exist_ok=True)
        temp_root = Path(
            tempfile.mkdtemp(prefix="pantheon-evolution-sim-", dir=str(cfg.work_dir))
        ).resolve()
    else:
        temp_root = Path(tempfile.mkdtemp(prefix="pantheon-evolution-sim-")).resolve()

    servers: list[_UvicornThreadServer] = []
    from services.registry.storage import reset_store as reset_registry_store

    try:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        now_epoch = int(now.timestamp())
        tenant_id = cfg.tenant_id
        persona_id = DEFAULT_SYNTHETIC_PERSONA_ID
        artifact_id = DEFAULT_SYNTHETIC_ARTIFACT_ID
        strategy_id = DEFAULT_SYNTHETIC_STRATEGY_ID
        binding_id = DEFAULT_SYNTHETIC_BINDING_ID
        runtime_id = DEFAULT_SYNTHETIC_RUNTIME_ID
        pool_id = DEFAULT_SYNTHETIC_POOL_ID
        plan_id = DEFAULT_SYNTHETIC_PLAN_ID
        pcb_id = DEFAULT_SYNTHETIC_PCB_ID

        jwt_secret = secrets.token_hex(32)
        jwt_issuer = "isolated-evolution-simulation-20261107"
        jwt_audience = "isolated-pantheon-simulation"
        drift_token = "sim-drift-token-" + secrets.token_hex(12)
        telemetry_token = "sim-telemetry-token-" + secrets.token_hex(12)

        tokens = {
            "EVOLUTION_GOVERNANCE_SERVICE_TOKEN": _mint_hs256_jwt(
                jwt_secret,
                sub="sim-evolution-approval-reader",
                tenant_id=tenant_id,
                roles=["approval_reader"],
                issuer=jwt_issuer,
                audience=jwt_audience,
                scope="pantheon:dev-owner-read",
                now_epoch=now_epoch,
            ),
            "EVOLUTION_REGISTRY_SERVICE_TOKEN": _mint_hs256_jwt(
                jwt_secret,
                sub="sim-evolution-registry-reader",
                tenant_id=tenant_id,
                roles=["registry-reader"],
                issuer=jwt_issuer,
                audience=jwt_audience,
                scope="pantheon:dev-owner-read",
                now_epoch=now_epoch,
            ),
            "EVOLUTION_PERSONA_SERVICE_TOKEN": _mint_hs256_jwt(
                jwt_secret,
                sub="sim-evolution-persona-reader",
                tenant_id=tenant_id,
                roles=["viewer"],
                issuer=jwt_issuer,
                audience=jwt_audience,
                scope="pantheon:dev-owner-read",
                now_epoch=now_epoch,
            ),
            "RESEARCH_REGISTRY_SERVICE_TOKEN": _mint_hs256_jwt(
                jwt_secret,
                sub="sim-research-registry-writer",
                tenant_id=tenant_id,
                roles=["registry-writer"],
                issuer=jwt_issuer,
                audience=jwt_audience,
                scope="pantheon:dev-owner-write",
                now_epoch=now_epoch,
            ),
            "RESEARCH_TRAINING_SERVICE_TOKEN": _mint_hs256_jwt(
                jwt_secret,
                sub="research-orchestrator",
                service="research-orchestrator",
                tenant_id=tenant_id,
                roles=["training-service"],
                issuer=jwt_issuer,
                audience=jwt_audience,
                scope="pantheon:dev-owner-read",
                now_epoch=now_epoch,
            ),
            "TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN": _mint_hs256_jwt(
                jwt_secret,
                sub="sim-training-persona-authority",
                tenant_id=tenant_id,
                roles=["persona.admin", "approval_reader"],
                issuer=jwt_issuer,
                audience=jwt_audience,
                scope="pantheon:dev-owner-read",
                now_epoch=now_epoch,
            ),
            "proposer_operator": _mint_hs256_jwt(
                jwt_secret,
                sub=SIMULATION_PROPOSER_ID,
                tenant_id=tenant_id,
                roles=["operator"],
                issuer=jwt_issuer,
                audience=jwt_audience,
                now_epoch=now_epoch,
            ),
            "governance_reviewer": _mint_hs256_jwt(
                jwt_secret,
                sub=SIMULATION_REVIEWER_ID,
                tenant_id=tenant_id,
                roles=["governance_reviewer"],
                issuer=jwt_issuer,
                audience=jwt_audience,
                now_epoch=now_epoch,
            ),
            "registry_operator": _mint_hs256_jwt(
                jwt_secret,
                sub="sim-registry-seed-operator",
                tenant_id=tenant_id,
                roles=["operator", "registry-writer", "registry-reader"],
                issuer=jwt_issuer,
                audience=jwt_audience,
                scope="pantheon:dev-owner-write",
                now_epoch=now_epoch,
            ),
        }

        principals_dir = temp_root / "principals"
        principals_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        token_files: dict[str, str] = {}
        for key in (
            "EVOLUTION_GOVERNANCE_SERVICE_TOKEN",
            "EVOLUTION_REGISTRY_SERVICE_TOKEN",
            "EVOLUTION_PERSONA_SERVICE_TOKEN",
            "RESEARCH_REGISTRY_SERVICE_TOKEN",
            "RESEARCH_TRAINING_SERVICE_TOKEN",
        ):
            fpath = principals_dir / key
            fpath.write_text(tokens[key], encoding="utf-8")
            fpath.chmod(0o600)
            token_files[f"{key}_FILE"] = str(fpath)

        # Prepare isolated baselines file for reconciliation-drift
        baselines_path = temp_root / "threshold_sweep_baselines.simulation.json"
        baselines_path.write_text(
            json.dumps(
                {
                    "baselines": {
                        artifact_id: {
                            "expected_drawdown": 0.05,
                            "policy_source": "simulation://EVOLUTION-ISOLATED-STIMULUS-20261107/baseline",
                        }
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        # Prepare isolated policy copy with allow_test_scope=True
        shared_policy_obj = json.loads(SHARED_POLICY_PATH.read_text(encoding="utf-8"))
        isolated_policy_obj = dict(shared_policy_obj)
        isolated_policy_obj["allow_test_scope"] = True
        isolated_policy_obj["policy_version"] = SIMULATION_POLICY_VERSION
        isolated_policy_path = (
            temp_root / "program_qualification_policy.simulation.json"
        )
        isolated_policy_path.write_text(
            json.dumps(isolated_policy_obj, indent=2) + "\n", encoding="utf-8"
        )

        lookback_initial = 3 if cfg.retrain_variant == "better" else 2
        strategy_artifact, strategy_artifact_digest = _build_strategy_artifact(
            artifact_id=artifact_id,
            strategy_id=strategy_id,
            persona_id=persona_id,
            pcb_id=pcb_id,
            lookback_bars=lookback_initial,
        )

        # 1. Start isolated Persona write-owner server
        from services.persona.write_owner import (
            CreatePersonaRequest,
            PersistentCapabilitySnapshotOwner,
            PersistentPersonaOwner,
            create_app as create_persona_app,
        )

        persona_dir = temp_root / "persona"
        persona_dir.mkdir(parents=True, exist_ok=True)
        persona_owner = PersistentPersonaOwner.from_json_path(
            persona_dir / "personas.json"
        )
        cap_owner = PersistentCapabilitySnapshotOwner.from_json_path(
            persona_dir / "capabilities.json"
        )

        # 2. Start isolated Telemetry projection/event owner server
        from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore

        telemetry_dir = temp_root / "telemetry"
        telemetry_dir.mkdir(parents=True, exist_ok=True)
        summary_store = RuntimeSummaryProjectionStore(
            path=telemetry_dir / "runtime_summaries.json",
            heartbeat_stale_after_seconds=86400 * 7,
        )
        telemetry_events_by_id: dict[str, dict[str, Any]] = {}
        telemetry_app = _build_telemetry_owner_app(
            summary_store=summary_store,
            events_by_id=telemetry_events_by_id,
            expected_token=telemetry_token,
            expected_tenant=tenant_id,
        )
        telemetry_server = _UvicornThreadServer(telemetry_app).start()
        servers.append(telemetry_server)

        # 3. Prepare deterministic Training Session authority fixture
        training_dir = REPO_ROOT / "services" / "training-session"
        sys.path.insert(0, str(training_dir / "tests"))
        try:
            import strict_test_support as support
        finally:
            try:
                sys.path.remove(str(training_dir / "tests"))
            except ValueError:
                pass

        closes = _deterministic_closes()
        orig_instruments = support._INSTRUMENTS
        orig_canonical_records = support._canonical_records

        def _sim_canonical_records(last_date: date, bars: int) -> list[dict[str, Any]]:
            first = last_date - timedelta(days=bars - 1)
            rows: list[dict[str, Any]] = []
            for offset in range(bars):
                for idx, inst in enumerate(INSTRUMENTS):
                    close = round(closes[offset] + 10.0 * idx, 2)
                    rows.append(
                        {
                            "instrument": inst,
                            "date": (first + timedelta(days=offset)).isoformat(),
                            "open": close,
                            "high": close,
                            "low": close,
                            "close": close,
                            "volume": 1000.0,
                        }
                    )
            return rows

        support._INSTRUMENTS = INSTRUMENTS
        support._canonical_records = _sim_canonical_records
        try:
            training_fixture = support.materialize_strict_authority(
                temp_root / "training_authority",
                trusted_now=now,
                bars_per_instrument=BARS,
            )
        finally:
            support._INSTRUMENTS = orig_instruments
            support._canonical_records = orig_canonical_records

        env_overrides: dict[str, str | None] = {
            **{
                k: v
                for k, v in training_fixture.environment().items()
                if k not in {"TRAINING_SESSION_AUTH_DISABLED", "TRAINING_SESSION_TEST_TENANT_ID"}
            },
            **token_files,
            "EVOLUTION_GOVERNANCE_SERVICE_TOKEN": None,
            "EVOLUTION_REGISTRY_SERVICE_TOKEN": None,
            "EVOLUTION_PERSONA_SERVICE_TOKEN": None,
            "RESEARCH_REGISTRY_SERVICE_TOKEN": None,
            "RESEARCH_TRAINING_SERVICE_TOKEN": None,
            "PANTHEON_REGISTRY_AUTH_MODE": "strict",
            "PANTHEON_REGISTRY_JWT_SECRET": jwt_secret,
            "PANTHEON_REGISTRY_JWT_ISSUER": jwt_issuer,
            "PANTHEON_REGISTRY_JWT_AUDIENCE": jwt_audience,
            "REGISTRY_STORE_BACKEND": "memory",
            "PERSONA_AUTH_MODE": "strict",
            "PERSONA_JWT_SECRET": jwt_secret,
            "PERSONA_JWT_ISSUER": jwt_issuer,
            "PERSONA_JWT_AUDIENCE": jwt_audience,
            "PERSONA_DEFAULT_TENANT_ID": tenant_id,
            "PANTHEON_PERSONA_SERVICE_TOKEN": None,
            "PERSONA_SERVICE_TOKEN": None,
            "PANTHEON_GOVERNANCE_AUTH_MODE": "strict",
            "PANTHEON_GOVERNANCE_JWT_SECRET": jwt_secret,
            "PANTHEON_GOVERNANCE_JWT_ISSUER": jwt_issuer,
            "PANTHEON_GOVERNANCE_JWT_AUDIENCE": jwt_audience,
            "GOVERNANCE_DATA_DIR": str(temp_root / "governance"),
            "GOVERNANCE_STORE_BACKEND": "json",
            "RECONCILIATION_DRIFT_DATA_DIR": str(temp_root / "reconciliation_drift"),
            "RECONCILIATION_DRIFT_AUTH_MODE": "token",
            "RECONCILIATION_DRIFT_AUTH_TOKEN": drift_token,
            "RECONCILIATION_DRIFT_DEFAULT_TENANT_ID": tenant_id,
            "RECONCILIATION_DRIFT_APPROVED_BASELINES_PATH": str(baselines_path),
            "PANTHEON_TELEMETRY_API_URL": telemetry_server.url,
            "PANTHEON_TELEMETRY_SERVICE_TOKEN": telemetry_token,
            "PANTHEON_TELEMETRY_SERVICE_TOKEN_FILE": None,
            "TRAINING_SESSION_AUTH_MODE": "strict",
            "TRAINING_SESSION_JWT_SECRET": jwt_secret,
            "TRAINING_SESSION_JWT_ISSUER": jwt_issuer,
            "TRAINING_SESSION_JWT_AUDIENCE": jwt_audience,
            "TRAINING_SESSION_MFA_REQUIRED": "false",
            "TRAINING_SESSION_ALLOWED_CALLER_SERVICES": "control-plane-bff,training-session-preview-worker,research-orchestrator",
            "TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN": tokens[
                "TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN"
            ],
            "RESEARCH_ORCHESTRATOR_DATA_DIR": str(temp_root / "research"),
            "RESEARCH_RETRAIN_EVAL_POLL_SECONDS": "0.15",
            "EVOLUTION_DATA_DIR": str(temp_root / "evolution"),
            "INCIDENT_DATA_DIR": str(temp_root / "incidents"),
            "EVOLUTION_STORE_BACKEND": "json",
            "EVOLUTION_AUTH_MODE": "disabled",
            "PANTHEON_TENANT_ID": tenant_id,
        }

        with _patched_environ(env_overrides):
            # Start Registry server
            from services.registry import service as registry_service

            reset_registry_store()
            registry_server = _UvicornThreadServer(registry_service.app).start()
            servers.append(registry_server)
            os.environ["REGISTRY_URL"] = registry_server.url
            os.environ["PANTHEON_REGISTRY_API_URL"] = registry_server.url

            # Seed synthetic Persona with runtime_binding pointing to Registry
            artifact_bytes = json.dumps(strategy_artifact, sort_keys=True).encode(
                "utf-8"
            )
            artifact_payload_checksum = (
                f"sha256:{hashlib.sha256(artifact_bytes).hexdigest()}"
            )
            object_store_projection = {
                f"openclaw/registry/{strategy_id}/1.0.0/metadata.json": {
                    "registry_id": artifact_id,
                    "strategy_id": strategy_id,
                    "version": "1.0.0",
                    "artifact_type": "execution_bundle",
                    "artifact_state": "approved",
                    "deployment_stage": "paper",
                    "promotion_state": "paper",
                    "checksum": artifact_payload_checksum,
                    "created_at": now.isoformat().replace("+00:00", "Z"),
                    "lineage": dict(strategy_artifact.get("lineage") or {}),
                },
                f"openclaw/registry/{strategy_id}/1.0.0/artifact.bin": (
                    artifact_bytes.decode("utf-8")
                ),
            }
            persona_owner.create(
                CreatePersonaRequest(
                    actor_id="sim-seed",
                    persona_id=persona_id,
                    name="Isolated Synthetic Stimulus Persona",
                    mandate="Synthetic paper drawdown qualification simulation only",
                    strategy_family="momentum",
                    tenant_id=tenant_id,
                    metadata={
                        "data_scope": cfg.data_scope,
                        "simulation_case_id": SIMULATION_CASE_ID,
                        "synthetic": True,
                        "tenant_id": tenant_id,
                        "provisioning_authoritative_readback": {
                            "runtime_binding": {
                                "binding_id": binding_id,
                                "runtime_id": runtime_id,
                                "artifact_id": artifact_id,
                                "artifact_version": "1.0.0",
                                "execution_mode": "paper",
                                "metadata": {
                                    "persona_id": persona_id,
                                    "tenant_id": tenant_id,
                                    "strategy_id": strategy_id,
                                    "artifact_checksum": artifact_payload_checksum,
                                    "strategy_artifact_checksum": (
                                        strategy_artifact_digest
                                    ),
                                    "object_store": object_store_projection,
                                },
                            }
                        },
                    },
                )
            )
            persona_app = create_persona_app(
                persona_owner, capability_owner=cap_owner
            )
            persona_server = _UvicornThreadServer(persona_app).start()
            servers.append(persona_server)
            os.environ["PERSONA_URL"] = persona_server.url
            os.environ[
                "TRAINING_SESSION_PERSONA_READBACK_URL_TEMPLATE"
            ] = f"{persona_server.url}/api/personas/{{persona_id}}/training-target"

            # Seed strategy artifact in Registry
            reg_status, reg_body = _http_json(
                "POST",
                f"{registry_server.url}/api/registry/strategy-artifacts",
                token=tokens["registry_operator"],
                tenant_id=tenant_id,
                body={
                    "registry_id": artifact_id,
                    "artifact_state": "candidate",
                    "strategy_artifact": strategy_artifact,
                    "metadata": {
                        "persona_id": persona_id,
                        "data_scope": cfg.data_scope,
                        "simulation_case_id": SIMULATION_CASE_ID,
                        "execution_context": "paper",
                    },
                },
            )
            if reg_status not in (200, 201):
                raise RuntimeError(f"Failed seeding Registry strategy artifact: {reg_status} {reg_body}")

            # Start Governance server with isolated JSON store & audit store
            from services.governance import main as gov_main
            from services.governance.pg_store import JsonlGovernanceAuditStore

            gov_dir = temp_root / "governance"
            gov_dir.mkdir(parents=True, exist_ok=True)
            gov_store = gov_main.ApprovalDecisionStore(
                storage_path=str(gov_dir / "approval_decisions.json")
            )
            gov_audit_store = JsonlGovernanceAuditStore(
                str(gov_dir / "audit.jsonl")
            )
            orig_gov_store, orig_gov_audit = gov_main.store, gov_main.audit_store
            gov_main.store = gov_store
            gov_main.audit_store = gov_audit_store
            try:
                governance_server = _UvicornThreadServer(gov_main.app).start()
                servers.append(governance_server)
                os.environ["EVOLUTION_GOVERNANCE_BASE_URL"] = governance_server.url

                # Start Reconciliation-Drift server
                drift_mod = _load_isolated_module(
                    "reconciliation_drift_sim_main",
                    REPO_ROOT / "services" / "reconciliation-drift" / "main.py",
                    REPO_ROOT / "services" / "reconciliation-drift",
                )
                drift_server = _UvicornThreadServer(drift_mod.app).start()
                servers.append(drift_server)
                os.environ["RECONCILIATION_DRIFT_URL"] = drift_server.url

                # Start Training Session server
                training_mod = _load_isolated_module(
                    "training_session_sim_main",
                    training_dir / "main.py",
                    training_dir,
                )
                training_mod.store = training_mod.TrainingSessionStore(
                    training_fixture.data_dir
                )
                training_server = _UvicornThreadServer(training_mod.app).start()
                servers.append(training_server)
                os.environ["TRAINING_SESSION_URL"] = training_server.url

                # Start Research Orchestrator server
                research_dir = REPO_ROOT / "services" / "research"
                research_mod = _load_isolated_module(
                    "research_orchestrator_sim_main",
                    research_dir / "main.py",
                    research_dir,
                )
                research_server = _UvicornThreadServer(research_mod.app).start()
                servers.append(research_server)
                os.environ["EVOLUTION_RESEARCH_API_URL"] = research_server.url
                os.environ["RESEARCH_ORCHESTRATOR_URL"] = research_server.url

                # Configure isolated Evolution stores & server
                from services.evolution import dispatch_worker
                from services.evolution import main as evo_main
                from services.evolution.dispatch_outbox import (
                    CompensationLedger,
                    EvolutionDispatchOutbox,
                    build_dispatch_outbox_store,
                    install_program_approval_resolver,
                )
                from services.evolution.dispatch_receipts import (
                    configured_adapter_registry,
                )
                from services.evolution.program_qualification import (
                    ProgramQualifier,
                    QualificationOwnerReader,
                    QualificationReportStore,
                    load_policy,
                )
                from services.evolution.program_service import (
                    ProgramApprovalVerifier,
                    ProgramService,
                )
                from services.evolution.program_store import JsonProgramStore
                from services.incident.incident import IncidentStore
                from services.incidents.consumer import DriftReportIncidentConsumer

                evo_dir = temp_root / "evolution"
                evo_dir.mkdir(parents=True, exist_ok=True)
                inc_dir = temp_root / "incidents"
                inc_dir.mkdir(parents=True, exist_ok=True)

                sim_incident_store = IncidentStore(
                    path=inc_dir / "incidents.json"
                )
                sim_decision_store = evo_main.EvolutionDecisionStore(
                    storage_path=str(evo_dir / "decisions.json")
                )
                sim_program_store = JsonProgramStore(evo_dir / "programs.json")
                sim_outbox = EvolutionDispatchOutbox(
                    build_dispatch_outbox_store(data_dir=str(evo_dir))
                )
                sim_compensation = CompensationLedger(data_dir=str(evo_dir))
                sim_receipt_registry = configured_adapter_registry(
                    research_api_url=research_server.url,
                    timeout=20.0,
                )
                sim_report_store = QualificationReportStore(
                    evo_dir / "program_qualification_report.json"
                )
                sim_program_service = ProgramService(
                    sim_program_store,
                    approval_verifier=ProgramApprovalVerifier(
                        evo_main._program_approval_reader
                    ),
                    decisions=evo_main._EvolutionDecisionGateway(),
                )
                sim_owner_reader = QualificationOwnerReader.from_env()

                # Project 3 distinct synthetic mark dates through Telemetry -> Reconciliation-Drift -> IncidentConsumer
                mark_days = [
                    (now.date() - timedelta(days=2), now - timedelta(days=2, hours=2)),
                    (now.date() - timedelta(days=1), now - timedelta(days=1, hours=2)),
                    (now.date(), now - timedelta(minutes=10)),
                ]
                drift_incident_consumer = DriftReportIncidentConsumer(
                    incident_store=sim_incident_store,
                    reference_validator=None,
                )
                created_eval_ids: list[str] = []
                mark_event_ids: list[str] = []
                incident_id: str | None = None

                for idx, (mark_date, eval_dt) in enumerate(mark_days, start=1):
                    mark_iso = f"{mark_date.isoformat()}T13:30:00Z"
                    eval_iso = eval_dt.isoformat().replace("+00:00", "Z")
                    dd_event_id = f"evt-sim-dd-{mark_date.strftime('%Y%m%d')}-{idx:02d}"
                    hb_event_id = f"evt-sim-hb-{mark_date.strftime('%Y%m%d')}-{idx:02d}"
                    mark_event_ids.append(dd_event_id)

                    dd_event = {
                        "tenant_id": tenant_id,
                        "event_id": dd_event_id,
                        "event_type": "drawdown_snapshot",
                        "created_at": eval_iso,
                        "execution_mode": "paper",
                        "environment": "paper",
                        "deployment_stage": "paper",
                        "binding_id": binding_id,
                        "runtime_id": runtime_id,
                        "capital_pool_id": pool_id,
                        "artifact_id": artifact_id,
                        "artifact_version": "1.0.0",
                        "plan_id": plan_id,
                        "persona_capital_binding_id": pcb_id,
                        "trace_id": f"trace-{SIMULATION_CASE_ID}",
                        "target": {"strategy_id": strategy_id, "persona_id": persona_id},
                        "metrics": {"drawdown_pct": 0.07, "fill_count": 2},
                        "metadata": {
                            "data_scope": cfg.data_scope,
                            "simulation_case_id": SIMULATION_CASE_ID,
                            "runtime_package": "paper_execution_runtime",
                            "mark_refs": [
                                {
                                    "symbol": "2330.TW",
                                    "price": 100.0 - idx,
                                    "as_of": mark_iso,
                                    "source_ref": f"simulation-mark:{SIMULATION_CASE_ID}:{mark_date.isoformat()}",
                                }
                            ],
                        },
                    }
                    hb_time = (
                        (now - timedelta(seconds=15)).isoformat().replace("+00:00", "Z")
                        if idx == len(mark_days)
                        else eval_iso
                    )
                    hb_event = {
                        "tenant_id": tenant_id,
                        "event_id": hb_event_id,
                        "event_type": "heartbeat",
                        "created_at": hb_time,
                        "execution_mode": "paper",
                        "environment": "paper",
                        "deployment_stage": "paper",
                        "binding_id": binding_id,
                        "runtime_id": runtime_id,
                        "capital_pool_id": pool_id,
                        "artifact_id": artifact_id,
                        "artifact_version": "1.0.0",
                        "plan_id": plan_id,
                        "persona_capital_binding_id": pcb_id,
                        "trace_id": f"trace-{SIMULATION_CASE_ID}",
                        "target": {"strategy_id": strategy_id, "persona_id": persona_id},
                        "metrics": {"queue_lag_ms": 12.0, "delivery_lag_ms": 18.0},
                        "metadata": {
                            "connectivity_status": "connected",
                            "broker_status": "ok",
                            "runtime_package": "paper_execution_runtime",
                            "queue_lag_ms": 12.0,
                            "event_delivery_lag_ms": 18.0,
                        },
                    }
                    telemetry_events_by_id[dd_event_id] = dd_event
                    telemetry_events_by_id[hb_event_id] = hb_event
                    summary_store.project_event(dd_event)
                    projected_summary = summary_store.project_event(hb_event)

                    # Run real reconciliation-drift scheduled pass for this mark day
                    reconcile_res = drift_mod._execute_scheduled_reconcile(
                        drift_mod.ScheduledReconcileBody(
                            binding_id=binding_id,
                            tenant_id=tenant_id,
                            dispatch_incidents=False,
                        ),
                        tenant_id=tenant_id,
                        timestamp=eval_iso,
                        tick_id=f"tick-sim-{mark_date.isoformat()}",
                    )
                    if reconcile_res["evaluated_binding_count"] != 1:
                        raise RuntimeError(
                            f"Expected 1 drift evaluation on {mark_date}, got {reconcile_res}"
                        )
                    eval_id = reconcile_res["evaluation_ids"][0]
                    created_eval_ids.append(eval_id)
                    stored_eval = drift_mod.store.get_evaluation(
                        eval_id, tenant_id=tenant_id
                    )
                    drift_report = drift_mod._scheduled_drift_report(
                        summary=projected_summary,
                        evaluation=stored_eval,
                        telemetry_event_ids=drift_mod._summary_telemetry_event_ids(
                            projected_summary
                        ),
                        timestamp=eval_iso,
                    )
                    if drift_report is None:
                        raise RuntimeError(f"Expected scheduled drift report on {mark_date}")
                    inc_result = drift_incident_consumer.consume(
                        {"drift_report": drift_report}
                    )
                    incident_id = inc_result.incident.incident_id

                # Swap Evolution module globals for the duration of the isolated run
                orig_evo_attrs = {
                    "incident_store": evo_main.incident_store,
                    "store": evo_main.store,
                    "program_store": evo_main.program_store,
                    "program_service": evo_main.program_service,
                    "program_service_store": evo_main.program_service._store,
                    "dispatch_outbox": evo_main.dispatch_outbox,
                    "compensation_ledger": evo_main.compensation_ledger,
                    "receipt_registry": evo_main.receipt_registry,
                    "program_qualifier": evo_main.program_qualifier,
                    "RESEARCH_API_URL": evo_main.RESEARCH_API_URL,
                }
                evo_main.incident_store = sim_incident_store
                evo_main.store = sim_decision_store
                evo_main.program_store = sim_program_store
                evo_main.program_service._store = sim_program_store
                evo_main.program_service = sim_program_service
                evo_main.dispatch_outbox = sim_outbox
                evo_main.compensation_ledger = sim_compensation
                evo_main.receipt_registry = sim_receipt_registry
                evo_main.RESEARCH_API_URL = research_server.url
                install_program_approval_resolver(
                    evo_main._resolve_program_for_decision
                )

                try:
                    # First: qualifier with shared policy (allow_test_scope=False)
                    shared_qualifier = ProgramQualifier(
                        decision_store=sim_decision_store,
                        program_service=sim_program_service,
                        owner_reader=sim_owner_reader,
                        report_store=sim_report_store,
                        action_paths=evo_main._ACTION_PATHS,
                        summary_field_for_metric=evo_main.summary_field_for_metric,
                        summary_is_stale_or_degraded=evo_main.summary_is_stale_or_degraded,
                        metric_is_stale=evo_main.metric_is_stale,
                        policy_loader=lambda: load_policy(str(SHARED_POLICY_PATH)),
                    )
                    evo_main.program_qualifier = shared_qualifier
                    evo_server = _UvicornThreadServer(evo_main.app).start()
                    servers.append(evo_server)

                    # Step 1: Run daily-sweep with shared policy -> creates EvolutionDecision, refuses real_target
                    sweep1_status, sweep1_body = _http_json(
                        "POST",
                        f"{evo_server.url}/api/evolution/daily-sweep",
                        tenant_id=tenant_id,
                        body={"sweep_id": "sim-sweep-shared-policy"},
                    )
                    if sweep1_status != 200:
                        raise RuntimeError(f"Sweep 1 failed: {sweep1_status} {sweep1_body}")
                    _, shared_qual_report = _http_json(
                        "GET",
                        f"{evo_server.url}/api/evolution/program-qualification",
                        tenant_id=tenant_id,
                    )
                    shared_decision_row = shared_qual_report["items"][0]
                    decision_id = shared_decision_row["decision_id"]

                    # Step 2: Switch only isolated ProgramQualifier policy loader to isolated_policy_path
                    isolated_qualifier = ProgramQualifier(
                        decision_store=sim_decision_store,
                        program_service=sim_program_service,
                        owner_reader=sim_owner_reader,
                        report_store=sim_report_store,
                        action_paths=evo_main._ACTION_PATHS,
                        summary_field_for_metric=evo_main.summary_field_for_metric,
                        summary_is_stale_or_degraded=evo_main.summary_is_stale_or_degraded,
                        metric_is_stale=evo_main.metric_is_stale,
                        policy_loader=lambda: load_policy(str(isolated_policy_path)),
                    )
                    evo_main.program_qualifier = isolated_qualifier

                    sweep2_status, sweep2_body = _http_json(
                        "POST",
                        f"{evo_server.url}/api/evolution/daily-sweep",
                        tenant_id=tenant_id,
                        body={"sweep_id": "sim-sweep-isolated-policy"},
                    )
                    if sweep2_status != 200:
                        raise RuntimeError(f"Sweep 2 failed: {sweep2_status} {sweep2_body}")
                    _, isolated_qual_report = _http_json(
                        "GET",
                        f"{evo_server.url}/api/evolution/program-qualification",
                        tenant_id=tenant_id,
                    )
                    qualified_row = isolated_qual_report["items"][0]
                    if not qualified_row.get("qualified") or not qualified_row.get("program_id"):
                        raise RuntimeError(
                            f"Expected synthetic stimulus to qualify under isolated policy, got: {json.dumps(qualified_row, indent=2)}"
                        )
                    program_id = qualified_row["program_id"]

                    # Read pre-approval state (must stop at under_review with 0 outbox and 0 runs)
                    _, pre_program = _http_json(
                        "GET",
                        f"{evo_server.url}/api/evolution/programs/{program_id}",
                        tenant_id=tenant_id,
                    )
                    _, pre_decision = _http_json(
                        "GET",
                        f"{evo_server.url}/api/evolution/proposals/{decision_id}",
                        tenant_id=tenant_id,
                    )
                    _, pre_outbox = _http_json(
                        "GET",
                        f"{evo_server.url}/api/evolution/dispatch-outbox",
                        tenant_id=tenant_id,
                    )
                    _, pre_runs = _http_json(
                        "GET",
                        f"{research_server.url}/api/research-orchestrator/runs",
                        tenant_id=tenant_id,
                    )
                    pre_outbox_records = pre_outbox.get("records") or []
                    pre_run_list = pre_runs if isinstance(pre_runs, list) else (pre_runs.get("runs") or [])
                    pre_approval_stop = {
                        "program_id": program_id,
                        "program_origin": pre_program.get("origin"),
                        "program_status": pre_program.get("status"),
                        "program_stage": pre_program.get("stage"),
                        "review_status": (pre_program.get("review") or {}).get("status"),
                        "review_approval_id": (pre_program.get("review") or {}).get(
                            "approval_id"
                        ),
                        "decision_state": pre_decision.get("decision_state"),
                        "outbox_record_count": len(pre_outbox_records),
                        "research_run_count": len(pre_run_list),
                        "stopped_before_dispatch": (
                            pre_program.get("status") == "under_review"
                            and pre_decision.get("decision_state") == "proposed"
                            and len(pre_outbox_records) == 0
                            and len(pre_run_list) == 0
                        ),
                    }

                    # Step 3: Negative Governance checks (missing, mismatched, rejected, pending)
                    # 3a. Missing approval on approve_program
                    missing_status, missing_body = _http_json(
                        "POST",
                        f"{evo_server.url}/api/evolution/programs/{program_id}/actions/approve_program",
                        tenant_id=tenant_id,
                        body={
                            "actor_id": SIMULATION_REVIEWER_ID,
                            "actor_role": "approver",
                            "expected_revision": pre_program["revision"],
                            "idempotency_key": "sim-approve-missing",
                        },
                    )

                    # Read exact approval binding required for the program
                    _, binding_spec = _http_json(
                        "GET",
                        f"{evo_server.url}/api/evolution/programs/{program_id}/approval-binding",
                        tenant_id=tenant_id,
                    )

                    # 3b. Mismatched approval binding (wrong target_id)
                    mismatch_prop_status, mismatch_prop = _http_json(
                        "POST",
                        f"{governance_server.url}/api/governance/approvals",
                        token=tokens["proposer_operator"],
                        tenant_id=tenant_id,
                        headers={"Idempotency-Key": "sim-gov-propose-mismatch"},
                        body={
                            "target_type": "evolution_proposal",
                            "target_id": "evp-wrong-program",
                            "target_version": str(binding_spec["target_version"]),
                            "tenant_id": tenant_id,
                            "owner_user_id": SIMULATION_PROPOSER_ID,
                            "risk_level": binding_spec["risk_level"],
                            "expected_version": 0,
                            "proposal_id": "evp-wrong-program",
                            "proposal_revision": int(binding_spec["proposal_revision"]),
                            "proposal_content_digest": binding_spec[
                                "proposal_content_digest"
                            ],
                            "expires_at": "2099-01-01T00:00:00Z",
                        },
                    )
                    if mismatch_prop_status != 201:
                        raise RuntimeError(f"Mismatch proposal failed: {mismatch_prop}")
                    mismatch_attach_status, mismatch_attach_body = _http_json(
                        "POST",
                        f"{evo_server.url}/api/evolution/programs/{program_id}/actions/submit_evolution_review",
                        tenant_id=tenant_id,
                        body={
                            "actor_id": SIMULATION_PROPOSER_ID,
                            "actor_role": "operator",
                            "expected_revision": pre_program["revision"],
                            "idempotency_key": "sim-attach-mismatch",
                            "payload": {"approval_id": mismatch_prop["decision_id"]},
                        },
                    )

                    # 3c. Rejected approval (propose -> review -> decide rejected -> attempt approve_program)
                    rej_prop_status, rej_prop = _http_json(
                        "POST",
                        f"{governance_server.url}/api/governance/approvals",
                        token=tokens["proposer_operator"],
                        tenant_id=tenant_id,
                        headers={"Idempotency-Key": "sim-gov-propose-rejected"},
                        body={
                            "target_type": binding_spec["target_type"],
                            "target_id": binding_spec["target_id"],
                            "target_version": str(binding_spec["target_version"]),
                            "tenant_id": tenant_id,
                            "owner_user_id": SIMULATION_PROPOSER_ID,
                            "risk_level": binding_spec["risk_level"],
                            "expected_version": 0,
                            "proposal_id": binding_spec["proposal_id"],
                            "proposal_revision": int(binding_spec["proposal_revision"]),
                            "proposal_content_digest": binding_spec[
                                "proposal_content_digest"
                            ],
                            "expires_at": "2099-01-01T00:00:00Z",
                        },
                    )
                    if rej_prop_status != 201:
                        raise RuntimeError(f"Rejected-case proposal failed: {rej_prop}")
                    _http_json(
                        "POST",
                        f"{governance_server.url}/api/governance/approvals/{rej_prop['decision_id']}/review",
                        token=tokens["governance_reviewer"],
                        tenant_id=tenant_id,
                        headers={"Idempotency-Key": "sim-gov-review-rejected"},
                        body={
                            "expected_version": 1,
                            "actor_id": SIMULATION_REVIEWER_ID,
                            "actor_role": "governance_reviewer",
                        },
                    )
                    _http_json(
                        "POST",
                        f"{governance_server.url}/api/governance/approvals/{rej_prop['decision_id']}/decide",
                        token=tokens["governance_reviewer"],
                        tenant_id=tenant_id,
                        headers={"Idempotency-Key": "sim-gov-decide-rejected"},
                        body={
                            "expected_version": 2,
                            "actor_id": SIMULATION_REVIEWER_ID,
                            "actor_role": "governance_reviewer",
                            "outcome": "rejected",
                            "rationale": "Synthetic negative test rejection",
                        },
                    )
                    rejected_attach_status, rejected_attach_body = _http_json(
                        "POST",
                        f"{evo_server.url}/api/evolution/programs/{program_id}/actions/submit_evolution_review",
                        tenant_id=tenant_id,
                        body={
                            "actor_id": SIMULATION_PROPOSER_ID,
                            "actor_role": "operator",
                            "expected_revision": pre_program["revision"],
                            "idempotency_key": "sim-attach-already-decided",
                            "payload": {"approval_id": rej_prop["decision_id"]},
                        },
                    )

                    # 3d. Propose real pending Governance request & attach to program
                    gov_prop_status, gov_prop = _http_json(
                        "POST",
                        f"{governance_server.url}/api/governance/approvals",
                        token=tokens["proposer_operator"],
                        tenant_id=tenant_id,
                        headers={"Idempotency-Key": "sim-gov-propose-main"},
                        body={
                            "target_type": binding_spec["target_type"],
                            "target_id": binding_spec["target_id"],
                            "target_version": str(binding_spec["target_version"]),
                            "tenant_id": tenant_id,
                            "owner_user_id": SIMULATION_PROPOSER_ID,
                            "risk_level": binding_spec["risk_level"],
                            "expected_version": 0,
                            "proposal_id": binding_spec["proposal_id"],
                            "proposal_revision": int(binding_spec["proposal_revision"]),
                            "proposal_content_digest": binding_spec[
                                "proposal_content_digest"
                            ],
                            "expires_at": "2099-01-01T00:00:00Z",
                        },
                    )
                    if gov_prop_status != 201:
                        raise RuntimeError(f"Governance proposal failed: {gov_prop_status} {gov_prop}")
                    approval_id = gov_prop["decision_id"]

                    attach_status, attached_program = _http_json(
                        "POST",
                        f"{evo_server.url}/api/evolution/programs/{program_id}/actions/submit_evolution_review",
                        tenant_id=tenant_id,
                        body={
                            "actor_id": SIMULATION_PROPOSER_ID,
                            "actor_role": "operator",
                            "expected_revision": pre_program["revision"],
                            "idempotency_key": "sim-attach-pending-main",
                            "payload": {"approval_id": approval_id},
                        },
                    )
                    if attach_status != 200:
                        raise RuntimeError(f"Attaching pending approval failed: {attach_status} {attached_program}")

                    # Attempt approve_program while Governance decision is still pending (proposed)
                    pending_status, pending_body = _http_json(
                        "POST",
                        f"{evo_server.url}/api/evolution/programs/{program_id}/actions/approve_program",
                        tenant_id=tenant_id,
                        body={
                            "actor_id": SIMULATION_REVIEWER_ID,
                            "actor_role": "approver",
                            "expected_revision": attached_program["revision"],
                            "idempotency_key": "sim-approve-while-pending",
                            "payload": {"approval_id": approval_id},
                        },
                    )

                    # Self-decision by proposer must also be refused by Governance separation of duties
                    self_decide_status, self_decide_body = _http_json(
                        "POST",
                        f"{governance_server.url}/api/governance/approvals/{approval_id}/decide",
                        token=tokens["proposer_operator"],
                        tenant_id=tenant_id,
                        headers={"Idempotency-Key": "sim-gov-self-decide"},
                        body={
                            "expected_version": 1,
                            "actor_id": SIMULATION_PROPOSER_ID,
                            "actor_role": "governance_reviewer",
                            "outcome": "approved",
                            "rationale": "Self approval attempt",
                        },
                    )

                    _, mid_outbox = _http_json(
                        "GET",
                        f"{evo_server.url}/api/evolution/dispatch-outbox",
                        tenant_id=tenant_id,
                    )
                    _, mid_runs = _http_json(
                        "GET",
                        f"{research_server.url}/api/research-orchestrator/runs",
                        tenant_id=tenant_id,
                    )
                    mid_outbox_records = mid_outbox.get("records") or []
                    mid_run_list = mid_runs if isinstance(mid_runs, list) else (mid_runs.get("runs") or [])

                    def _err_info(status: int, body: Any) -> dict[str, Any]:
                        detail = body.get("detail") if isinstance(body, dict) else body
                        if isinstance(detail, dict):
                            return {
                                "http_status": status,
                                "code": detail.get("code"),
                                "message": detail.get("message") or str(detail),
                            }
                        return {"http_status": status, "detail": str(detail)}

                    negative_governance_proofs = {
                        "missing_approval_refusal": _err_info(missing_status, missing_body),
                        "mismatched_binding_refusal": _err_info(
                            mismatch_attach_status, mismatch_attach_body
                        ),
                        "already_decided_rejected_refusal": _err_info(
                            rejected_attach_status, rejected_attach_body
                        ),
                        "pending_approval_refusal": _err_info(pending_status, pending_body),
                        "proposer_self_decide_refusal": _err_info(
                            self_decide_status, self_decide_body
                        ),
                        "work_started_during_invalid_or_pending_attempts": (
                            len(mid_outbox_records) > 0 or len(mid_run_list) > 0
                        ),
                    }

                    # Step 4: Real Governance review + decide(approved) by distinct simulated reviewer
                    rev_status, rev_body = _http_json(
                        "POST",
                        f"{governance_server.url}/api/governance/approvals/{approval_id}/review",
                        token=tokens["governance_reviewer"],
                        tenant_id=tenant_id,
                        headers={"Idempotency-Key": "sim-gov-review-main"},
                        body={
                            "expected_version": 1,
                            "actor_id": SIMULATION_REVIEWER_ID,
                            "actor_role": "governance_reviewer",
                        },
                    )
                    if rev_status != 200:
                        raise RuntimeError(f"Governance review failed: {rev_status} {rev_body}")

                    dec_status, dec_body = _http_json(
                        "POST",
                        f"{governance_server.url}/api/governance/approvals/{approval_id}/decide",
                        token=tokens["governance_reviewer"],
                        tenant_id=tenant_id,
                        headers={"Idempotency-Key": "sim-gov-decide-main"},
                        body={
                            "expected_version": 2,
                            "actor_id": SIMULATION_REVIEWER_ID,
                            "actor_role": "governance_reviewer",
                            "outcome": "approved",
                            "rationale": "Approved isolated synthetic Evolution acceptance stimulus (EVOLUTION-ISOLATED-STIMULUS-20261107)",
                        },
                    )
                    if dec_status != 200:
                        raise RuntimeError(f"Governance decide failed: {dec_status} {dec_body}")

                    _, gov_readback = _http_json(
                        "GET",
                        f"{governance_server.url}/api/governance/approvals/{approval_id}",
                        token=tokens["EVOLUTION_GOVERNANCE_SERVICE_TOKEN"],
                        tenant_id=tenant_id,
                    )
                    _, gov_audit = _http_json(
                        "GET",
                        f"{governance_server.url}/api/governance/audit?decision_id={approval_id}",
                        token=tokens["governance_reviewer"],
                        tenant_id=tenant_id,
                    )

                    # Approve program in Evolution
                    approve_status, approved_program = _http_json(
                        "POST",
                        f"{evo_server.url}/api/evolution/programs/{program_id}/actions/approve_program",
                        tenant_id=tenant_id,
                        body={
                            "actor_id": SIMULATION_REVIEWER_ID,
                            "actor_role": "approver",
                            "idempotency_key": "sim-approve-program-main",
                            "payload": {"approval_id": approval_id},
                        },
                    )
                    if approve_status != 200:
                        raise RuntimeError(f"Program approve_program failed: {approve_status} {approved_program}")

                    _, approved_decision = _http_json(
                        "GET",
                        f"{evo_server.url}/api/evolution/proposals/{decision_id}",
                        tenant_id=tenant_id,
                    )
                    _, post_approve_outbox = _http_json(
                        "GET",
                        f"{evo_server.url}/api/evolution/dispatch-outbox",
                        tenant_id=tenant_id,
                    )

                    # Step 5: Outbox dispatch -> Research Orchestrator retrain executor -> receipt convergence
                    poll_1 = dispatch_worker.run_poll(
                        api_url=evo_server.url,
                        outbox=sim_outbox,
                        registry=sim_receipt_registry,
                        compensations=sim_compensation,
                        worker_id="sim-dispatch-worker-1",
                        claim_limit=10,
                        actor_id="evolution-dispatch-worker",
                        auth_token=None,
                        timeout_seconds=20.0,
                    )

                    # Wait for Research Orchestrator background retrain execution to reach terminal status
                    deadline = time.monotonic() + 45.0
                    terminal_run: dict[str, Any] | None = None
                    while time.monotonic() < deadline:
                        _, prog_runs_resp = _http_json(
                            "GET",
                            f"{evo_server.url}/api/evolution/programs/{program_id}/runs",
                            tenant_id=tenant_id,
                        )
                        items = prog_runs_resp.get("items") or []
                        if items and items[0].get("status") in {"success", "failed"}:
                            terminal_run = items[0]
                            break
                        time.sleep(0.2)
                    if terminal_run is None:
                        raise RuntimeError("Timed out waiting for Research Orchestrator retrain run to complete")

                    poll_2 = dispatch_worker.run_poll(
                        api_url=evo_server.url,
                        outbox=sim_outbox,
                        registry=sim_receipt_registry,
                        compensations=sim_compensation,
                        worker_id="sim-dispatch-worker-1",
                        claim_limit=10,
                        actor_id="evolution-dispatch-worker",
                        auth_token=None,
                        timeout_seconds=20.0,
                        now=datetime.now(timezone.utc) + timedelta(minutes=10),
                    )

                    # Read back final state across Evolution and Research Orchestrator
                    _, final_program = _http_json(
                        "GET",
                        f"{evo_server.url}/api/evolution/programs/{program_id}",
                        tenant_id=tenant_id,
                    )
                    _, final_program_runs = _http_json(
                        "GET",
                        f"{evo_server.url}/api/evolution/programs/{program_id}/runs",
                        tenant_id=tenant_id,
                    )
                    _, final_decision = _http_json(
                        "GET",
                        f"{evo_server.url}/api/evolution/proposals/{decision_id}",
                        tenant_id=tenant_id,
                    )
                    _, final_outbox = _http_json(
                        "GET",
                        f"{evo_server.url}/api/evolution/dispatch-outbox",
                        tenant_id=tenant_id,
                    )
                    run_id = final_program_runs["items"][0]["run_id"]
                    _, research_run = _http_json(
                        "GET",
                        f"{research_server.url}/api/research-orchestrator/runs/{run_id}",
                        tenant_id=tenant_id,
                    )
                    task_id = research_run["task_id"]
                    _, research_task = _http_json(
                        "GET",
                        f"{research_server.url}/api/research-orchestrator/tasks/{task_id}",
                        tenant_id=tenant_id,
                    )
                    candidate_entry_version: str | None = None
                    candidate_entry_state: str | None = None
                    cand_id = final_program_runs["items"][0].get("candidate_artifact_id")
                    if cand_id:
                        _, cand_view = _http_json(
                            "GET",
                            f"{registry_server.url}/api/registry/entries/{cand_id}",
                            token=tokens["RESEARCH_REGISTRY_SERVICE_TOKEN"],
                            tenant_id=tenant_id,
                        )
                        cand_entry = (
                            cand_view.get("entry")
                            if isinstance(cand_view, dict)
                            else None
                        ) or {}
                        candidate_entry_version = cand_entry.get("version")
                        candidate_entry_state = cand_entry.get("artifact_state")

                    # Step 6: Idempotency & replay proofs
                    # 6a. Re-running daily-sweep + qualifier creates no second decision or program
                    _, replay_sweep = _http_json(
                        "POST",
                        f"{evo_server.url}/api/evolution/daily-sweep",
                        tenant_id=tenant_id,
                        body={"sweep_id": "sim-sweep-idempotent-replay"},
                    )
                    _, all_programs = _http_json(
                        "GET",
                        f"{evo_server.url}/api/evolution/programs",
                        tenant_id=tenant_id,
                    )
                    _, all_decisions = _http_json(
                        "GET",
                        f"{evo_server.url}/api/evolution/proposals",
                        tenant_id=tenant_id,
                    )

                    # 6b. Re-sending approve_program with same idempotency_key returns original receipt
                    replay_approve_status, replay_approve_body = _http_json(
                        "POST",
                        f"{evo_server.url}/api/evolution/programs/{program_id}/actions/approve_program",
                        tenant_id=tenant_id,
                        body={
                            "actor_id": SIMULATION_REVIEWER_ID,
                            "actor_role": "approver",
                            "idempotency_key": "sim-approve-program-main",
                            "payload": {"approval_id": approval_id},
                        },
                    )

                    # 6c. Re-running dispatch_worker.run_poll creates no second task or run
                    poll_3 = dispatch_worker.run_poll(
                        api_url=evo_server.url,
                        outbox=sim_outbox,
                        registry=sim_receipt_registry,
                        compensations=sim_compensation,
                        worker_id="sim-dispatch-worker-1",
                        claim_limit=10,
                        actor_id="evolution-dispatch-worker",
                        auth_token=None,
                        timeout_seconds=20.0,
                        now=datetime.now(timezone.utc) + timedelta(minutes=20),
                    )
                    _, all_tasks = _http_json(
                        "GET",
                        f"{research_server.url}/api/research-orchestrator/tasks",
                        tenant_id=tenant_id,
                    )
                    _, all_runs = _http_json(
                        "GET",
                        f"{research_server.url}/api/research-orchestrator/runs",
                        tenant_id=tenant_id,
                    )
                finally:
                    evo_main.incident_store = orig_evo_attrs["incident_store"]
                    evo_main.store = orig_evo_attrs["store"]
                    evo_main.program_store = orig_evo_attrs["program_store"]
                    orig_evo_attrs["program_service"]._store = orig_evo_attrs[
                        "program_service_store"
                    ]
                    evo_main.program_service = orig_evo_attrs["program_service"]
                    evo_main.dispatch_outbox = orig_evo_attrs["dispatch_outbox"]
                    evo_main.compensation_ledger = orig_evo_attrs["compensation_ledger"]
                    evo_main.receipt_registry = orig_evo_attrs["receipt_registry"]
                    evo_main.program_qualifier = orig_evo_attrs["program_qualifier"]
                    evo_main.RESEARCH_API_URL = orig_evo_attrs["RESEARCH_API_URL"]
                    install_program_approval_resolver(
                        evo_main._resolve_program_for_decision
                    )
            finally:
                gov_main.store = orig_gov_store
                gov_main.audit_store = orig_gov_audit
    finally:
        for srv in reversed(servers):
            srv.stop()
        reset_registry_store()
        if temp_root.exists():
            shutil.rmtree(temp_root, ignore_errors=True)

    shared_policy_after = _verify_shared_policy_untouched(
        expected_sha256=shared_policy_before["sha256"]
    )

    run_item = final_program_runs["items"][0]
    post_approve_outbox_records = post_approve_outbox.get("records") or []
    final_outbox_records = final_outbox.get("records") or []
    try:
        source_sha = (
            subprocess.check_output(
                ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            or "unknown"
        )
    except Exception:
        source_sha = "unknown"
    report = {
        "task_id": "EVOLUTION-ISOLATED-STIMULUS-20261107",
        "source_sha": source_sha,
        "evidence_level": "local_integration_simulation",
        "natural_market_qualification_proven": False,
        "operator_authorization": {
            "plan_reference": "/tmp/pantheon-evolution-simulation-20261107/PLAN.md",
            "scope": "One isolated synthetic trigger and downstream test flow through qualification, governance approval, outbox dispatch, and real research execution",
            "natural_ac5_status": "unproven_in_natural_paper_trading",
        },
        "synthetic_provenance": {
            "simulation_case_id": SIMULATION_CASE_ID,
            "synthetic": True,
            "data_scope": cfg.data_scope,
            "tenant_id": tenant_id,
            "persona_id": persona_id,
            "artifact_id": artifact_id,
            "strategy_id": strategy_id,
            "runtime_binding_id": binding_id,
            "runtime_id": runtime_id,
            "capital_pool_id": pool_id,
            "deployment_plan_id": plan_id,
            "persona_capital_binding_id": pcb_id,
            "incident_id": incident_id,
            "mark_event_ids": mark_event_ids,
            "drift_evaluation_ids": created_eval_ids,
            "proposer_principal": {
                "actor_id": SIMULATION_PROPOSER_ID,
                "role": "operator",
                "synthetic_test_identity": True,
            },
            "reviewer_principal": {
                "actor_id": SIMULATION_REVIEWER_ID,
                "role": "governance_reviewer",
                "synthetic_test_identity": True,
            },
        },
        "policy_diff": {
            "shared_policy_before": shared_policy_before,
            "shared_policy_after": shared_policy_after,
            "shared_policy_unchanged": (
                shared_policy_before["sha256"] == shared_policy_after["sha256"]
                and shared_policy_after["allow_test_scope"] is False
            ),
            "isolated_simulation_policy_overrides": {
                "policy_version": {
                    "from": shared_policy_before["policy_version"],
                    "to": SIMULATION_POLICY_VERSION,
                },
                "allow_test_scope": {
                    "from": False,
                    "to": True,
                },
            },
            "shared_policy_refusal_on_synthetic_target": {
                "qualified": shared_decision_row["qualified"],
                "first_failing_criterion": shared_decision_row[
                    "first_failing_criterion"
                ],
                "real_target_criterion": next(
                    c
                    for c in shared_decision_row["criteria"]
                    if c["criterion"] == "real_target"
                ),
            },
        },
        "qualification": {
            "policy_version": isolated_qual_report["policy_version"],
            "evaluated": isolated_qual_report["evaluated"],
            "qualified": isolated_qual_report["qualified"],
            "drafted": isolated_qual_report["drafted"],
            "decision_id": decision_id,
            "program_id": program_id,
            "metric_name": qualified_row["metric_name"],
            "action_type": qualified_row["action_type"],
            "all_six_criteria_passed": all(
                c["result"] == "passed" for c in qualified_row["criteria"]
            ),
            "criteria": qualified_row["criteria"],
        },
        "pre_approval_stop": pre_approval_stop,
        "negative_governance_proofs": negative_governance_proofs,
        "governance_approval": {
            "approval_id": approval_id,
            "target_type": gov_readback["target_type"],
            "target_id": gov_readback["target_id"],
            "target_version": gov_readback["target_version"],
            "proposal_id": gov_readback["proposal_id"],
            "proposal_revision": gov_readback["proposal_revision"],
            "proposal_content_digest": gov_readback["proposal_content_digest"],
            "decision_state": gov_readback["decision_state"],
            "decision": gov_readback["decision"],
            "owner_user_id": gov_readback["owner_user_id"],
            "actor_id": gov_readback["actor_id"],
            "actor_role": gov_readback["actor_role"],
            "version": gov_readback["version"],
            "audit_event_count": len(gov_audit),
            "program_status_after_approval": approved_program["status"],
            "decision_state_after_approval": approved_decision["decision_state"],
            "outbox_status_after_approval": post_approve_outbox_records[0]["status"],
            "outbox_id": post_approve_outbox_records[0]["outbox_id"],
        },
        "research_execution": {
            "dispatch_poll_1": {
                "claimed": poll_1["claimed"],
                "pending": poll_1["pending"],
                "executed": poll_1["executed"],
            },
            "dispatch_poll_2": {
                "claimed": poll_2["claimed"],
                "executed": poll_2["executed"],
            },
            "task_id": task_id,
            "task_status": research_task["status"],
            "run_id": run_id,
            "run_status": research_run["status"],
            "executor": "services.research.main._execute_retrain",
            "adapter": research_run.get("adapter"),
            "requested_mode": research_run.get("requested_mode"),
            "outcome": run_item["outcome"],
            "outcome_reason": run_item["outcome_reason"],
            "session_id": run_item["session_id"],
            "baseline_job_id": research_run.get("baseline_job_id"),
            "candidate_job_id": research_run.get("candidate_job_id"),
            "baseline_metrics": run_item["baseline_metrics"],
            "candidate_metrics": run_item["candidate_metrics"],
            "candidate_artifact_id": run_item["candidate_artifact_id"],
            "candidate_artifact_version": candidate_entry_version,
            "candidate_artifact_state": candidate_entry_state,
            "runtime_binding_changed": False,
            "capital_records_created": 0,
            "broker_orders_created": 0,
            "program_readback": {
                "program_id": final_program["program_id"],
                "status": final_program["status"],
                "stage": final_program.get("stage"),
                "research_run_ids": final_program.get("run_ids")
                or [
                    link["run_id"]
                    for link in (final_program.get("run_links") or [])
                    if isinstance(link, dict) and link.get("run_id")
                ],
                "runs_endpoint_total": len(final_program_runs["items"]),
            },
            "decision_readback": {
                "decision_id": final_decision["decision_id"],
                "decision_state": final_decision["decision_state"],
                "approval_decision_id": final_decision["approval_decision_id"],
                "execution_result_status": (
                    final_decision.get("execution_result") or {}
                ).get("status"),
                "execution_result_run_id": (
                    final_decision.get("execution_result") or {}
                ).get("execution_ref_id"),
            },
            "outbox_readback": {
                "outbox_id": final_outbox_records[0]["outbox_id"],
                "status": final_outbox_records[0]["status"],
                "published_at": final_outbox_records[0]["published_at"],
            },
        },
        "idempotency": {
            "repeat_daily_sweep_created_decisions": replay_sweep["created_decisions"],
            "total_decisions": len(all_decisions),
            "total_programs": len(all_programs.get("items") or []),
            "repeat_approve_program_http_status": replay_approve_status,
            "repeat_approve_program_same_revision": (
                replay_approve_body.get("revision") == approved_program.get("revision")
            ),
            "repeat_dispatch_poll_claimed": poll_3["claimed"],
            "total_research_tasks": len(
                all_tasks if isinstance(all_tasks, list) else (all_tasks.get("tasks") or [])
            ),
            "total_research_runs": len(
                all_runs if isinstance(all_runs, list) else (all_runs.get("runs") or [])
            ),
        },
        "cleanup": {
            "temp_root": str(temp_root),
            "cleaned": not temp_root.exists(),
            "exists_after_cleanup": temp_root.exists(),
        },
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write the structured JSON simulation report.",
    )
    parser.add_argument(
        "--tenant-id",
        default=DEFAULT_SYNTHETIC_TENANT,
        help="Synthetic tenant identifier (must contain sim/synthetic/isolated).",
    )
    parser.add_argument(
        "--data-scope",
        default="synthetic",
        help="Synthetic data_scope marker on the simulated target persona.",
    )
    parser.add_argument(
        "--target-url",
        default=None,
        help="Optional loopback target URL validation parameter.",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Optional isolated parent directory for temporary simulation files.",
    )
    parser.add_argument(
        "--variant",
        choices=("better", "worse"),
        default="better",
        help="Deterministic local retrain outcome variant ('better' -> candidate_created, 'worse' -> no_candidate).",
    )
    args = parser.parse_args(argv)

    config = SimulationConfig(
        tenant_id=args.tenant_id,
        data_scope=args.data_scope,
        target_url=args.target_url,
        work_dir=args.work_dir,
        retrain_variant=args.variant,
    )
    report = run_simulation(config)
    rendered = json.dumps(report, indent=2, sort_keys=False) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
