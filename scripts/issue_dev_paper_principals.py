#!/usr/bin/env python3
"""Issue operator-authorized, bounded dev product credentials without printing them.

Run only inside the governed dev deploy lane. Existing dev verifier material is
read from environment, not command arguments. No owner signing key is rotated.
Output is a newly created mode-0600 shell env file; it must never be an artifact.
Governance's owner policy, not possession of a labelled JWT alone, enforces paper.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shlex
import tempfile
import time
from typing import Mapping


PAPER_SUBJECT = "pantheon-dev-paper-provisioner"
PAPER_SCOPE = "pantheon:dev-paper-approval"
# Persona paper enrolment proposes lifecycle approvals under its own propose-only
# subject; Governance forbids decider == proposer, so it must differ from PAPER_SUBJECT.
ENROLLMENT_PROPOSER_SUBJECT = "pantheon-dev-persona-enrollment-proposer"
# Research card admission (design section 6A): the research owner proposes the admission of a
# hypothesis-card StrategySpec under its own propose-only subject; the paper automated gate
# (PAPER_SUBJECT) decides it.  Distinct from PAPER_SUBJECT and from the enrolment proposer.
CARD_PROPOSER_SUBJECT = "pantheon-dev-research-card-proposer"
# Grant content Governance reads: explicit paper-ladder persona lifecycle widening naming the
# enrolment proposer, and explicit research card admission widening naming the card proposer
# (services/governance/paper_approval_scope.py).
GOVERNANCE_GRANT_CONTENT = (
    "enabled;card_admission=paper_research;card_proposer=" + CARD_PROPOSER_SUBJECT
    + ";persona_lifecycle=paper_ladder;proposer=" + ENROLLMENT_PROPOSER_SUBJECT
)
GOVERNANCE_CAPITAL_READER_SUBJECT = "pantheon-dev-governance-capital-binding-reader"
GOVERNANCE_RUNTIME_READER_SUBJECT = "pantheon-dev-governance-runtime-binding-reader"
TTL_SECONDS = 24 * 60 * 60
READERS = {
    "DEPLOYMENT_REGISTRY_SERVICE_TOKEN": ("pantheon-dev-deployment-registry-reader", "registry-reader"),
    "DEPLOYMENT_GOVERNANCE_SERVICE_TOKEN": ("pantheon-dev-deployment-approval-reader", "approval_reader"),
    "REGISTRY_GOVERNANCE_SERVICE_TOKEN": ("pantheon-dev-registry-approval-reader", "approval_reader"),
    "GOVERNANCE_REGISTRY_SERVICE_TOKEN": ("pantheon-dev-governance-registry-reader", "registry-reader"),
    # Governance paper-ladder lifecycle review -> Capital /api/bindings (capital-reader, GET only) and
    # Runtime Manager /api/runtime-bindings (viewer, GET only).  Distinct subjects, no write role.
    "GOVERNANCE_CAPITAL_SERVICE_TOKEN": (GOVERNANCE_CAPITAL_READER_SUBJECT, "capital-reader"),
    "GOVERNANCE_RUNTIME_MANAGER_SERVICE_TOKEN": (GOVERNANCE_RUNTIME_READER_SUBJECT, "viewer"),
    "RUNTIME_MANAGER_GOVERNANCE_SERVICE_TOKEN": ("pantheon-dev-runtime-approval-reader", "approval_reader"),
    "RUNTIME_MANAGER_REGISTRY_SERVICE_TOKEN": ("pantheon-dev-runtime-registry-reader", "registry-reader"),
    "RUNTIME_MANAGER_CAPITAL_SERVICE_TOKEN": ("runtime-manager", "capital-reader"),
    "DEPLOYMENT_CAPITAL_SERVICE_TOKEN": ("runtime-manager", "capital-reader"),
    "PERSONA_GOVERNANCE_SERVICE_TOKEN": ("pantheon-dev-persona-approval-reader", "approval_reader"),
    "CAPITAL_GOVERNANCE_SERVICE_TOKEN": ("pantheon-dev-capital-approval-reader", "approval_reader"),
    "EVOLUTION_GOVERNANCE_SERVICE_TOKEN": ("pantheon-dev-evolution-approval-reader", "approval_reader"),
    "EVOLUTION_REGISTRY_SERVICE_TOKEN": ("pantheon-dev-evolution-registry-reader", "registry-reader"),
    "EVOLUTION_PERSONA_SERVICE_TOKEN": ("pantheon-dev-evolution-persona-reader", "viewer"),
    # Discovery allocator read-only owner reads (persona list, capital bindings).
    "RESEARCH_PERSONA_SERVICE_TOKEN": ("research-orchestrator", "viewer"),
    # Persona daily pool cycle -> research owner (persona demands, candidate supply).
    "PERSONA_RESEARCH_SERVICE_TOKEN": ("persona-pool-cycle", "viewer"),
    # Persona daily cycle & strategy pool -> registry owner (candidate specs, champions).
    "PERSONA_REGISTRY_SERVICE_TOKEN": ("pantheon-dev-persona-registry-reader", ("registry-reader", "regime-evaluator")),
    # Persona daily cycle case-file assembly -> search owner (hybrid retrieval query).
    "PERSONA_SEARCH_SERVICE_TOKEN": ("persona_cycle_controller", ("researcher", "persona_session")),
    # Evolution automatic consumer -> owner read ports.
    "AUTO_CONSUMER_PERSONA_TOKEN": ("pantheon-dev-auto-consumer-persona-reader", "viewer"),
    "AUTO_CONSUMER_REGISTRY_TOKEN": ("pantheon-dev-auto-consumer-registry-reader", "registry-reader"),
    "AUTO_CONSUMER_CAPITAL_TOKEN": ("runtime-manager", "capital-reader"),
    "AUTO_CONSUMER_RESEARCH_TOKEN": ("pantheon-dev-auto-consumer-research-reader", "viewer"),
    "AUTO_CONSUMER_BFF_TOKEN": ("pantheon-dev-auto-consumer-bff-reader", ("viewer", "registry-reader")),
    "DISTILLATION_RESEARCH_SERVICE_TOKEN": ("strategy-distillation-worker", "viewer"),
    "RESEARCH_CAPITAL_SERVICE_TOKEN": ("research-orchestrator", "capital-reader"),
    "ALPHA_REPLICATION_REGISTRY_SERVICE_TOKEN": ("pantheon-dev-alpha-replication-registry-reader", "registry-reader"),
    # Card admission path of the alpha replication worker: open persona demands (research owner)
    # and the demand's persona (persona owner), both read-only.
    "ALPHA_REPLICATION_RESEARCH_SERVICE_TOKEN": ("pantheon-dev-alpha-replication-research-reader", "viewer"),
    "ALPHA_REPLICATION_PERSONA_SERVICE_TOKEN": ("pantheon-dev-alpha-replication-persona-reader", "viewer"),
    "PERSONA_EVALUATOR_BFF_TOKEN": (
        "pantheon-dev-persona-evaluator-bff-reader",
        ("viewer", "registry-reader"),
    ),
    "PERSONA_RANKING_TOKEN": (
        "pantheon-dev-persona-evaluator-bff-reader",
        ("viewer", "registry-reader"),
    ),
    # Consultation executor case-file composition -> persona owner strategy-pool GET routes ONLY
    # (current / versions / one version). persona_pool_reader is accepted by no other persona route
    # and is not a write role; the dev tenant is the only admitted tenant. Delivered as a file only:
    # PANTHEON_PERSONA_SERVICE_TOKEN is the persona owner's own service-token name, so it is never
    # exported into the shared deploy environment.
    "CONSULTATION_PERSONA_POOL_READ_TOKEN": ("pantheon-dev-consultation-persona-pool-reader", "persona_pool_reader"),
    # Meeting roster resolution -> persona owner directory (GET /api/personas) ONLY. viewer is the existing
    # read-only persona role (no write/admin/decide role); a file, never an env value.
    "CONSULTATION_PERSONA_DIRECTORY_READ_TOKEN": ("pantheon-dev-consultation-persona-directory-reader", "viewer"),
    # Consultation executor case-file composition & meeting lesson-candidate episode verification ->
    # telemetry owner read-only GET routes (runtime-summaries and trade-episodes; viewer + reviewer roles,
    # no service/operator/admin write authority). File-delivered only so PANTHEON_TELEMETRY_SERVICE_TOKEN
    # is never exported into the shared deploy env.
    "CONSULTATION_TELEMETRY_READ_TOKEN": (
        "pantheon-dev-consultation-telemetry-reader",
        ("viewer", "reviewer"),
    ),
    # Consultation executor -> memory learn-feedback writeback ONLY as write_authority consultation-svc.
    # Role consultation-svc is in the memory write set and satisfies the authority check; no
    # operator/admin/system/memory-writer role, so it cannot represent any other memory writer.
    "CONSULTATION_MEMORY_WRITE_TOKEN": ("pantheon-dev-consultation-memory-writer", "consultation-svc"),
    # Consultation executor + meeting pre-read -> memory retrieve ONLY. consultation_session is a memory read
    # role (institutional and committee-scoped persona reads) and not a write role. The actor id the service
    # sends must equal this subject (memory-svc matches actor_id to the verified token identity).
    "CONSULTATION_MEMORY_READ_TOKEN": ("pantheon-dev-consultation-memory-reader", "consultation_session"),
    # OpenClaw adapter persona-memory materialize -> memory owner: read-only (persona_memory_reader is not a
    # memory write role). The adapter's actor id must equal this subject in strict auth.
    "OPENCLAW_ADAPTER_MEMORY_TOKEN": ("openclaw-persona-sync", "persona_memory_reader"),
    # Persona daily pool cycle -> BFF strategy-pool publish route ONLY. The role is accepted by no other BFF route.
    "PERSONA_POOL_PUBLISH_BFF_TOKEN": ("persona-pool-cycle", "persona_pool_publisher"),
    "TRAINING_SESSION_SOURCE_READ_TOKEN": ("pantheon-dev-training-session-source-reader", "source_ingest_reader"),
    "PERSONA_POOL_SOURCE_READ_TOKEN": ("pantheon-dev-persona-pool-source-reader", "source_ingest_reader"),
    "AGORA_PROJECTOR_SERVICE_JWT": ("pantheon-dev-agora-projector-reader", "source_ingest_reader"),
    # Preview worker -> Training API only: training-service role, no owner/decide roles.
    "TRAINING_SESSION_WORKER_TOKEN": ("training-session-preview-worker", "training-service"),
    # Research retrain executor -> Training API only: training-service role. The
    # subject is the X-Pantheon-Service the executor sends; no decide/persona.admin/approval role.
    "RESEARCH_TRAINING_SERVICE_TOKEN": ("research-orchestrator", "training-service"),
    # Persona precondition/CAS owner roles + Governance decision read; no decide/revoke/promotion roles.
    "TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN": (
        "pantheon-dev-training-session-persona-authority", ("persona.admin", "approval_reader")),
}
WRITERS = {
    "DISTILLATION_REGISTRY_SERVICE_TOKEN": (
        "pantheon-dev-distillation-registry-writer",
        "registry-writer",
        "pantheon:dev-owner-write",
    ),
    # Research retrain executor -> Registry mutate (candidate child only); owner-write
    # scope like the distillation writer. registry-writer cannot advance or approve.
    "RESEARCH_REGISTRY_SERVICE_TOKEN": (
        "pantheon-dev-research-registry-writer",
        "registry-writer",
        "pantheon:dev-owner-write",
    ),
    # Discovery allocator -> operator BFF paper provisioning (operator-approved
    # option A, 2026-11-11). Operator role with owner-write scope; paper-only is
    # enforced by the allocator and the BFF, not by this credential.
    "RESEARCH_BFF_SERVICE_TOKEN": (
        "research-orchestrator",
        "operator",
        "pantheon:dev-owner-write",
    ),
    # Subject is the evaluator's owner/actor id; propose-only, no decide/revoke role.
    "PERSONA_EVALUATOR_GOVERNANCE_TOKEN": (
        "persona-evaluator-agent",
        "approval_proposer",
        "pantheon:dev-owner-write",
    ),
    # Evolution owner files the Governance evolution_execute request under this
    # propose-only identity (owner_user_id must equal the subject). It has no
    # decide/revoke role, so a human decides once and Governance's
    # "decider must not be the proposer" rule stays intact.
    "EVOLUTION_EXECUTE_PROPOSER_TOKEN": (
        "evolution-execute-proposer",
        "approval_proposer",
        "pantheon:dev-owner-write",
    ),
    # Persona daily cycle & harness adoption -> memory owner (bundle recall + outcome writeback).
    "PERSONA_MEMORY_SERVICE_TOKEN": (
        "persona_cycle_controller",
        "persona_session",
        "pantheon:dev-owner-write",
    ),
    # Persona service memory writes (harness adoption, learning feedback, pool outcomes) -> memory owner.
    # Least privilege: persona-svc write role only; persona_session (reader above) stays read-only.
    "PERSONA_MEMORY_WRITE_TOKEN": (
        "persona-svc",
        "persona-svc",
        "pantheon:dev-owner-write",
    ),
    # Persona lesson-rule proposal publisher -> governance owner (propose-only, no decide).
    "LESSON_RULE_PROPOSER_TOKEN": (
        "persona-svc",
        "approval_proposer",
        "pantheon:dev-owner-write",
    ),
    # Evolution automatic consumer -> governance owner (propose-only, no decide).
    "AUTO_CONSUMER_PROPOSER_TOKEN": (
        "evolution-auto-consumer-proposer",
        "approval_proposer",
        "pantheon:dev-owner-write",
    ),
    # Consultation meeting owner -> research owner meeting-action tickets (create + chair review). The
    # research owner admits actor consultation-svc with role "service" for both; no operator/admin role,
    # no signing secret is distributed and no other service subject is substituted.
    "CONSULTATION_RESEARCH_SERVICE_TOKEN": (
        "consultation-svc",
        "service",
        "pantheon:dev-owner-write",
    ),
    # Persona paper enrolment (operator-bff) -> governance owner: propose-only, no decide.
    "PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN": (
        ENROLLMENT_PROPOSER_SUBJECT,
        "approval_proposer",
        "pantheon:dev-owner-write",
    ),
    # Alpha replication worker card admission -> governance owner: propose-only, no decide/revoke,
    # so the paper gate's "decider must not be the proposer" rule stays intact.
    "RESEARCH_GOVERNANCE_PROPOSER_TOKEN": (
        CARD_PROPOSER_SUBJECT,
        "approval_proposer",
        "pantheon:dev-owner-write",
    ),
    # Persona evaluator paper-only quarterly allocation -> operator BFF (paper pool only).
    "PERSONA_ALLOCATION_TOKEN": (
        "pantheon-dev-persona-evaluator-allocation",
        "operator",
        "pantheon:dev-owner-write",
    ),
}
# Downstream health monitor -> Telemetry infrastructure-health only. The telemetry
# verifier needs the service role plus a producer scope inside its own allowlist.
TELEMETRY_SCOPE = "pantheon:dev-telemetry-health"
TELEMETRY_PRODUCERS = {
    "PANTHEON_BFF_HEALTH_TELEMETRY_JWT": (
        "control-plane-bff-health-monitor", "service", ["control-plane-bff"]),
}
SEARCH_ACCESS_SCOPES = ["operator", "research", "public"]
CONSUMER_FILES = {
    "deployment": (
        "DEPLOYMENT_REGISTRY_SERVICE_TOKEN",
        "DEPLOYMENT_GOVERNANCE_SERVICE_TOKEN",
        "DEPLOYMENT_CAPITAL_SERVICE_TOKEN",
    ),
    "registry": ("REGISTRY_GOVERNANCE_SERVICE_TOKEN",),
    "governance": (
        "GOVERNANCE_REGISTRY_SERVICE_TOKEN",
        "GOVERNANCE_CAPITAL_SERVICE_TOKEN",
        "GOVERNANCE_RUNTIME_MANAGER_SERVICE_TOKEN",
    ),
    "runtime-manager": ("RUNTIME_MANAGER_REGISTRY_SERVICE_TOKEN", "RUNTIME_MANAGER_GOVERNANCE_SERVICE_TOKEN", "RUNTIME_MANAGER_CAPITAL_SERVICE_TOKEN"),
    "persona": (
        "PERSONA_GOVERNANCE_SERVICE_TOKEN",
        "PERSONA_RESEARCH_SERVICE_TOKEN",
        "PERSONA_POOL_SOURCE_READ_TOKEN",
        "PERSONA_REGISTRY_SERVICE_TOKEN",
        "PERSONA_MEMORY_SERVICE_TOKEN",
        "PERSONA_MEMORY_WRITE_TOKEN",
        "LESSON_RULE_PROPOSER_TOKEN",
        "PERSONA_SEARCH_SERVICE_TOKEN",
        "PERSONA_POOL_PUBLISH_BFF_TOKEN",
    ),
    "capital": ("CAPITAL_GOVERNANCE_SERVICE_TOKEN",),
    "evolution": (
        "EVOLUTION_GOVERNANCE_SERVICE_TOKEN",
        "EVOLUTION_REGISTRY_SERVICE_TOKEN",
        "EVOLUTION_PERSONA_SERVICE_TOKEN",
        "EVOLUTION_EXECUTE_PROPOSER_TOKEN",
    ),
    "evolution-daily-sweep-scheduler": (
        "AUTO_CONSUMER_PERSONA_TOKEN",
        "AUTO_CONSUMER_REGISTRY_TOKEN",
        "AUTO_CONSUMER_CAPITAL_TOKEN",
        "AUTO_CONSUMER_RESEARCH_TOKEN",
        "AUTO_CONSUMER_BFF_TOKEN",
        "AUTO_CONSUMER_PROPOSER_TOKEN",
        "AUTO_CONSUMER_GATE_TOKEN",
    ),
    "operator-bff": (
        "PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN",
        "PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN",
        "PANTHEON_BFF_HEALTH_TELEMETRY_JWT",
    ),
    "strategy-distillation-worker": ("DISTILLATION_REGISTRY_SERVICE_TOKEN", "DISTILLATION_RESEARCH_SERVICE_TOKEN"),
    "alpha-replication-worker": (
        "ALPHA_REPLICATION_REGISTRY_SERVICE_TOKEN",
        "TRAINING_SESSION_WORKER_TOKEN",
        "ALPHA_REPLICATION_RESEARCH_SERVICE_TOKEN",
        "ALPHA_REPLICATION_PERSONA_SERVICE_TOKEN",
        "RESEARCH_REGISTRY_SERVICE_TOKEN",
        "RESEARCH_GOVERNANCE_PROPOSER_TOKEN",
        "RESEARCH_GOVERNANCE_GATE_TOKEN",
    ),
    "research-orchestrator-svc": (
        "RESEARCH_REGISTRY_SERVICE_TOKEN",
        "RESEARCH_TRAINING_SERVICE_TOKEN",
        "RESEARCH_BFF_SERVICE_TOKEN",
        "RESEARCH_PERSONA_SERVICE_TOKEN",
        "RESEARCH_CAPITAL_SERVICE_TOKEN",
    ),
    "persona-evaluator-agent": (
        "PERSONA_EVALUATOR_BFF_TOKEN",
        "PERSONA_RANKING_TOKEN",
        "PERSONA_ALLOCATION_TOKEN",
        "PERSONA_EVALUATOR_GOVERNANCE_TOKEN",
        "AUTO_CONSUMER_CAPITAL_TOKEN",
        "AUTO_CONSUMER_REGISTRY_TOKEN",
    ),
    "training-session-preview-worker": ("TRAINING_SESSION_WORKER_TOKEN",),
    "consultation-svc": ("CONSULTATION_PERSONA_POOL_READ_TOKEN", "CONSULTATION_MEMORY_WRITE_TOKEN",
                         "CONSULTATION_MEMORY_READ_TOKEN", "CONSULTATION_PERSONA_DIRECTORY_READ_TOKEN",
                         "CONSULTATION_TELEMETRY_READ_TOKEN",
                         "CONSULTATION_RESEARCH_SERVICE_TOKEN"),
    "training-session-svc": ("TRAINING_SESSION_SOURCE_READ_TOKEN", "TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN"),
    "source-ingest-agora-projector": ("AGORA_PROJECTOR_SERVICE_JWT",),
    "openclaw-gateway-adapter": ("OPENCLAW_ADAPTER_MEMORY_TOKEN",),
    # Shared by paper-signal-producer and paper-fleet-reconciler: only the capital
    # reader principal, never the deployment volume's governance/registry files.
    "paper-capital-reader": ("DEPLOYMENT_CAPITAL_SERVICE_TOKEN",),
}
REFRESH_SECONDS = 60 * 60


def _roles(role: str | tuple[str, ...]) -> list[str]:
    return [role] if isinstance(role, str) else list(role)


def issue_environment(env: Mapping[str, str], *, now: int | None = None) -> dict[str, str]:
    if env.get("PANTHEON_ENV") != "dev" or env.get("PANTHEON_DEV_BFF_TENANT_ID") != "tenant-dev":
        raise ValueError("Product principal issuance requires dev and exact tenant-dev")
    if env.get("PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED") != "true":
        raise ValueError("Explicit dev paper principal issuance authorization required")
    secret = env.get("PANTHEON_DEV_BFF_JWT_SECRET", "").strip()
    issuer = env.get("PANTHEON_DEV_BFF_JWT_ISSUER", "").strip()
    audience = env.get("PANTHEON_DEV_BFF_JWT_AUDIENCE", "").strip()
    if len(secret) < 32 or not issuer or not audience:
        raise ValueError("Configured dev verifier secret, issuer and audience required")
    issued = int(time.time()) if now is None else now

    def token(
        subject: str,
        role: str | tuple[str, ...],
        scope: str,
        producers: list[str] | None = None,
        access_scopes: list[str] | None = None,
        azp: str | None = None,
    ) -> str:
        claims = {
            "sub": subject, "service": subject, "tenant_id": "tenant-dev",
            "allowed_tenants": ["tenant-dev"], "roles": _roles(role), "scope": scope,
            "iss": issuer, "aud": audience, "iat": issued, "nbf": issued,
            "exp": issued + TTL_SECONDS, "jti": secrets.token_hex(16),
        }
        if producers:
            claims["allowed_producers"] = list(producers)
        if access_scopes:
            claims["access_scopes"] = list(access_scopes)
        if azp:
            claims["azp"] = azp
        encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=")
        encoded = encode(b'{"alg":"HS256","typ":"JWT"}') + b"." + encode(
            json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
        )
        signature = hmac.new(secret.encode(), encoded, hashlib.sha256).digest()
        return (encoded + b"." + encode(signature)).decode("ascii")

    evolution_service_token = (
        env.get("EVOLUTION_AUTH_TOKEN", "pantheon-local-evolution-service").strip()
        or "pantheon-local-evolution-service"
    )
    consultation_service_token = (
        env.get("CONSULTATION_SERVICE_TOKEN", "pantheon-local-consultation-service").strip()
        or "pantheon-local-consultation-service"
    )
    consultation_api_token = (
        (env.get("CONSULTATION_API_TOKEN") or consultation_service_token).strip()
        or consultation_service_token
    )
    values = {
        variable: token(
            subject,
            role,
            "pantheon:dev-owner-read",
            access_scopes=SEARCH_ACCESS_SCOPES if variable == "PERSONA_SEARCH_SERVICE_TOKEN" else None,
        )
        for variable, (subject, role) in READERS.items()
    }
    values.update({
        variable: token(
            subject,
            role,
            scope,
            azp="control-plane-bff" if variable == "PERSONA_ALLOCATION_TOKEN" else None,
        )
        for variable, (subject, role, scope) in WRITERS.items()
    })
    values.update({
        variable: token(subject, role, TELEMETRY_SCOPE, producers)
        for variable, (subject, role, producers) in TELEMETRY_PRODUCERS.items()
    })
    values.update({
        "PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN": token(PAPER_SUBJECT, "automated_gate", PAPER_SCOPE),
        "AUTO_CONSUMER_GATE_TOKEN": token(PAPER_SUBJECT, "automated_gate", PAPER_SCOPE),
        "RESEARCH_GOVERNANCE_GATE_TOKEN": token(PAPER_SUBJECT, "automated_gate", PAPER_SCOPE),
        "RESEARCH_GOVERNANCE_PROPOSER_ID": CARD_PROPOSER_SUBJECT,
        "RESEARCH_GOVERNANCE_GATE_ID": PAPER_SUBJECT,
        "PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID": PAPER_SUBJECT,
        "PANTHEON_PERSONA_ENROLLMENT_PROPOSER_ID": ENROLLMENT_PROPOSER_SUBJECT,
        "GOVERNANCE_DEV_PAPER_APPROVAL_ENABLED": "true",
        "GOVERNANCE_REGISTRY_BASE_URL": "http://registry:8087",
        "GOVERNANCE_CAPITAL_BASE_URL": "http://capital:8092",
        "GOVERNANCE_RUNTIME_MANAGER_BASE_URL": "http://runtime-manager:8081",
        "PANTHEON_REGISTRY_JWT_ISSUER": issuer,
        "PANTHEON_REGISTRY_JWT_AUDIENCE": audience,
        "GOVERNANCE_DEV_PAPER_GRANT_FILE": "/run/pantheon-principals/grant",
        "PANTHEON_MEMORY_AUTH_TOKEN": values["PERSONA_MEMORY_WRITE_TOKEN"],
        "PANTHEON_MEMORY_SERVICE_TOKEN": values["PERSONA_MEMORY_SERVICE_TOKEN"],
        "PANTHEON_RESEARCH_SERVICE_TOKEN": values["PERSONA_RESEARCH_SERVICE_TOKEN"],
        "PANTHEON_REGISTRY_SERVICE_TOKEN": values["PERSONA_REGISTRY_SERVICE_TOKEN"],
        "PANTHEON_SEARCH_SERVICE_TOKEN": values["PERSONA_SEARCH_SERVICE_TOKEN"],
        "PERSONA_SOURCE_INGEST_SERVICE_TOKEN": values["PERSONA_POOL_SOURCE_READ_TOKEN"],
        "EVOLUTION_AUTH_TOKEN": evolution_service_token,
        "CONSULTATION_SERVICE_TOKEN": consultation_service_token,
        "CONSULTATION_API_TOKEN": consultation_api_token,
    })
    for variables in CONSUMER_FILES.values():
        for variable in variables:
            values[variable + "_FILE"] = "/run/pantheon-principals/" + variable
    return values


def revoked_environment(env: Mapping[str, str]) -> dict[str, str]:
    if (env.get("PANTHEON_ENV") != "dev" or env.get("PANTHEON_DEV_BFF_TENANT_ID") != "tenant-dev"
            or env.get("PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED") != "false"):
        raise ValueError("Explicit tenant-dev withdrawal required")
    values = {
        "GOVERNANCE_DEV_PAPER_APPROVAL_ENABLED": "false",
        "PANTHEON_PERSONA_ENROLLMENT_PROPOSER_ID": "",
        "RESEARCH_GOVERNANCE_PROPOSER_ID": "",
        "RESEARCH_GOVERNANCE_GATE_ID": "",
        "GOVERNANCE_DEV_PAPER_GRANT_FILE": "/run/pantheon-principals/grant",
        "PANTHEON_MEMORY_AUTH_TOKEN": "",
        "PANTHEON_MEMORY_SERVICE_TOKEN": "",
        "PANTHEON_RESEARCH_SERVICE_TOKEN": "",
        "PANTHEON_REGISTRY_SERVICE_TOKEN": "",
        "PANTHEON_SEARCH_SERVICE_TOKEN": "",
        "PERSONA_SOURCE_INGEST_SERVICE_TOKEN": "",
        "EVOLUTION_AUTH_TOKEN": "",
        "CONSULTATION_SERVICE_TOKEN": "",
        "CONSULTATION_API_TOKEN": "",
    }
    for variables in CONSUMER_FILES.values():
        for variable in variables:
            values[variable] = ""
            values[variable + "_FILE"] = "/run/pantheon-principals/" + variable
    return values


def _atomic_private(directory: Path, name: str, value: str) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError("Unsafe principal directory")
    os.chmod(directory, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".rotating-", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, directory / name)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def revoke_files(root: Path, env: Mapping[str, str]) -> None:
    """After issuer stop, deny even captured JWTs at Governance before deleting files."""
    revoked_environment(env)
    _atomic_private(root / "governance", "grant", "revoked")
    for consumer, variables in CONSUMER_FILES.items():
        directory = root / consumer
        if directory.is_symlink():
            raise ValueError("Unsafe principal directory")
        for variable in variables:
            (directory / variable).unlink(missing_ok=True)


def refresh_files(root: Path, env: Mapping[str, str], *, now: int | None = None) -> None:
    """Trusted dev issuer writes each consumer's distinct, read-only mounted directory.

    Atomic replacement plus request-time reads rotate without restarting product
    owners. The issuer has no listening socket, shell API or untrusted payload.
    """
    values = issue_environment(env, now=now)
    for consumer, variables in CONSUMER_FILES.items():
        directory = root / consumer
        for variable in variables:
            _atomic_private(directory, variable, values[variable])
    _atomic_private(root / "governance", "grant", GOVERNANCE_GRANT_CONTENT)


def healthy_files(root: Path, env: Mapping[str, str], *, now: int | None = None) -> bool:
    """Check signatures, fixed grants and renewal headroom without logging values."""
    issued = int(time.time()) if now is None else now
    # Validate current environment authorization, not only old files.
    issue_environment(env, now=issued)
    if (root / "governance" / "grant").read_text() != GOVERNANCE_GRANT_CONTENT:
        return False
    secret = env["PANTHEON_DEV_BFF_JWT_SECRET"].strip().encode()
    decode = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    for consumer, variables in CONSUMER_FILES.items():
        for variable in variables:
            path = root / consumer / variable
            if path.is_symlink() or path.stat().st_mode & 0o077:
                return False
            encoded = path.read_text(encoding="utf-8")
            header, payload, signature = encoded.split(".")
            signed = (header + "." + payload).encode()
            if not hmac.compare_digest(decode(signature), hmac.new(secret, signed, hashlib.sha256).digest()):
                return False
            claims = json.loads(decode(payload))
            if variable in READERS:
                subject, role = READERS[variable]
                scope = "pantheon:dev-owner-read"
            elif variable in WRITERS:
                subject, role, scope = WRITERS[variable]
            elif variable in TELEMETRY_PRODUCERS:
                subject, role, producers = TELEMETRY_PRODUCERS[variable]
                scope = TELEMETRY_SCOPE
            else:
                subject, role = PAPER_SUBJECT, "automated_gate"
                scope = PAPER_SCOPE
            expected = {"sub": subject, "service": subject, "roles": _roles(role), "scope": scope,
                        "tenant_id": "tenant-dev", "allowed_tenants": ["tenant-dev"],
                        "iss": env["PANTHEON_DEV_BFF_JWT_ISSUER"].strip(),
                        "aud": env["PANTHEON_DEV_BFF_JWT_AUDIENCE"].strip()}
            if variable in TELEMETRY_PRODUCERS:
                expected["allowed_producers"] = producers
            if variable == "PERSONA_SEARCH_SERVICE_TOKEN":
                expected["access_scopes"] = SEARCH_ACCESS_SCOPES
            if variable == "PERSONA_ALLOCATION_TOKEN":
                expected["azp"] = "control-plane-bff"
            if any(claims.get(key) != value for key, value in expected.items()):
                return False
            if not (claims["nbf"] <= issued < claims["exp"] - REFRESH_SECONDS * 2
                    and 0 < claims["exp"] - claims["iat"] <= TTL_SECONDS):
                return False
    return True


BROKER_TOKEN_FILE_ENV = "PANTHEON_PERSONA_TOOL_BROKER_TOKEN_FILE"
BROKER_TOKEN_FILE_DEFAULT = "~/.pantheon-secrets/persona-tool-broker.token"


def broker_environment(env: Mapping[str, str]) -> dict[str, str]:
    """Dev persona tool broker enablement with one host-persistent secret file.

    The token is generated once (0600, never overwritten) and re-read on every
    deploy, so it survives container recreation. Value is never logged.
    """
    if env.get("PANTHEON_ENV") != "dev":
        raise ValueError("Broker secret is dev-only")
    path = Path(env.get(BROKER_TOKEN_FILE_ENV) or BROKER_TOKEN_FILE_DEFAULT).expanduser()
    if path.is_symlink():
        raise ValueError("Unsafe broker token file")
    token = ""
    if path.exists():
        if path.stat().st_mode & 0o077:
            raise ValueError("Broker token file must be private")
        token = path.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        token = secrets.token_urlsafe(48)
        descriptor, temporary = tempfile.mkstemp(prefix=".broker-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(token)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {"PERSONA_TOOL_BROKER_ENABLED": "true", "PERSONA_TOOL_BROKER_TOKEN": token}


def write_environment(path: Path, values: Mapping[str, str]) -> None:
    # Refuse existing files and symlinks. Never truncate a credential or follow a
    # caller-selected symlink; the deploy lane supplies its own fresh private dir.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        for name, value in sorted(values.items()):
            output.write(f"export {name}={shlex.quote(value)}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output-env", type=Path)
    mode.add_argument("--serve", action="store_true")
    mode.add_argument("--healthcheck", action="store_true")
    mode.add_argument("--revoke", action="store_true")
    parser.add_argument("--credentials-root", type=Path, default=Path("/issued"))
    args = parser.parse_args()
    try:
        if args.revoke:
            revoke_files(args.credentials_root, os.environ)
            print("Dev paper grant withdrawn; scoped credential files removed; values withheld")
            return 0
        if args.healthcheck:
            return 0 if healthy_files(args.credentials_root, os.environ) else 1
        if args.serve:
            while True:
                refresh_files(args.credentials_root, os.environ)
                print("Dev paper principals refreshed; values withheld", flush=True)
                time.sleep(REFRESH_SECONDS)
        values = (revoked_environment(os.environ)
                  if os.environ.get("PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED") == "false"
                  else issue_environment(os.environ))
        write_environment(args.output_env, {**values, **broker_environment(os.environ)})
    except (ValueError, OSError, KeyError, TypeError):
        # Do not echo paths, environment, key material or a token on failures.
        parser.exit(1, "Dev product principal issuance failed closed\n")
    print("Prepared bounded dev principal configuration; values withheld")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
