"""HTTP service for the execution grant issuer.

OPS-EXECUTION-MFA-ISSUER-001.
Implements the authenticated challenge-response issuance flow:
1. Verifies Google Cloud Identity Platform MFA ID tokens.
2. Manages atomic, single-use, task-bound challenges.
3. Signs execution authorization grants with Ed25519.
4. Enforces strict S5/step-5 pauses, task allowlists, and policy consistency.
5. Implements redacted logging with zero token or secret leakage.
"""
from __future__ import annotations

import json
import logging
import re
import ssl
import sys
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence

# Safe import for execution_authorization
_orchestrator_dir = Path(__file__).resolve().parents[1]
if str(_orchestrator_dir) not in sys.path:
    sys.path.insert(0, str(_orchestrator_dir))

try:
    import execution_authorization as ea
except ImportError:
    from .. import execution_authorization as ea

from .challenge_store import ChallengeStore
from .models import AuthenticationError, ChallengeError, IssuerError, PolicyValidationError, VerifiedOperator
from .signer import Ed25519GrantSigner
from .token_verifier import IdentityPlatformTokenVerifier

logger = logging.getLogger("execution_grant_issuer.service")

# Regex to detect S5 / Step 5 tasks to enforce mandatory pause
S5_PATTERN = re.compile(r"(^|\b|[-_])(s5|step-?5)(\b|[-_]|$)", re.IGNORECASE)


def _is_s5_restricted(task_id: str, policy: Mapping[str, Any] | None = None) -> bool:
    """Return True if task is associated with Step 5 / S5 (paused)."""
    if S5_PATTERN.search(task_id):
        return True
    if policy:
        phase = str(policy.get("phase") or policy.get("action_scope") or "").lower()
        if S5_PATTERN.search(phase):
            return True
        artifacts = policy.get("artifacts") or []
        for art in artifacts:
            if S5_PATTERN.search(str(art)):
                return True
    return False


class ExecutionGrantIssuerService:
    """Core domain service for execution grant issuance."""

    def __init__(
        self,
        *,
        verifier: IdentityPlatformTokenVerifier,
        signer: Ed25519GrantSigner,
        challenge_store: ChallengeStore | None = None,
        allowed_tasks: Sequence[str] | None = ("DEV502-TRACE-001",),
        allowed_environments: Sequence[str] | None = ("pantheon-dev",),
        grant_freshness_seconds: int = 120,
        run_ttl_seconds: int = 1800,
        audit_log_path: Path | str | None = None,
    ) -> None:
        self.verifier = verifier
        self.signer = signer
        self.challenge_store = challenge_store or ChallengeStore()
        self.allowed_tasks = (
            frozenset(t.strip() for t in allowed_tasks if t.strip())
            if allowed_tasks is not None
            else None
        )
        self.allowed_environments = (
            frozenset(e.strip() for e in allowed_environments if e.strip())
            if allowed_environments is not None
            else None
        )
        self.grant_freshness_seconds = grant_freshness_seconds
        self.run_ttl_seconds = run_ttl_seconds
        self.audit_log_path = Path(audit_log_path) if audit_log_path else None
        self._audit_receipts: list[dict[str, Any]] = []

    def _record_audit_receipt(self, receipt: dict[str, Any]) -> None:
        """Record a redacted audit receipt (never storing tokens or private keys)."""
        self._audit_receipts.append(receipt)
        if self.audit_log_path:
            try:
                self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.audit_log_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(receipt) + "\n")
            except Exception as exc:
                logger.error("Failed to write audit receipt to %s: %s", self.audit_log_path, exc)

    def validate_task_policy(self, task_id: str, policy: Mapping[str, Any]) -> None:
        """Validate that task policy is privileged, well-formed, and permissible."""
        if _is_s5_restricted(task_id, policy):
            raise PolicyValidationError(
                f"Task {task_id} is associated with Step 5 / S5, which remains strictly paused"
            )

        if self.allowed_tasks is not None and task_id not in self.allowed_tasks:
            raise PolicyValidationError(
                f"Task {task_id} is not in the allowed task scope for this issuer: {sorted(self.allowed_tasks)}"
            )

        env = str(policy.get("environment") or "").strip()
        if self.allowed_environments is not None and env not in self.allowed_environments:
            raise PolicyValidationError(
                f"Environment {env!r} is not permitted for task {task_id}: allowed {sorted(self.allowed_environments)}"
            )

        if policy.get("requires_execution_authorization") is not True:
            raise PolicyValidationError(
                f"Task {task_id} execution policy does not require execution authorization"
            )

        # Recompute policy digest using canonical orchestrator algorithm
        recomputed = ea.execution_policy_digest(
            task_id=task_id,
            repository=policy.get("repository"),
            environment=env,
            resources=policy.get("resources"),
            action_scope=policy.get("action_scope"),
            artifacts=policy.get("artifacts"),
            work_class=policy.get("work_class"),
            task_spec_hash=policy.get("task_spec_hash"),
        )
        if str(policy.get("policy_digest") or "").strip() != recomputed:
            raise PolicyValidationError(
                f"Task {task_id} policy digest mismatch: claimed {policy.get('policy_digest')!r} != recomputed {recomputed!r}"
            )

    def handle_create_challenge(
        self,
        token_str: str,
        payload: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Verify operator token and create an issuance challenge."""
        operator = self.verifier.verify_token(token_str, now=now)

        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            raise ChallengeError("task_id is required")

        generation = payload.get("generation")
        if type(generation) is not int or generation < 0:
            raise ChallengeError("generation must be a non-negative integer")

        policy = payload.get("policy_snapshot")
        if not isinstance(policy, Mapping):
            raise ChallengeError("policy_snapshot must be an object")

        self.validate_task_policy(task_id, policy)

        env = str(policy.get("environment") or "").strip()
        resources = policy.get("resources") or []
        policy_digest = str(policy.get("policy_digest") or "").strip()

        challenge = self.challenge_store.create_challenge(
            actor_uid=operator.uid,
            actor_email=operator.email,
            task_id=task_id,
            generation=generation,
            policy_snapshot=dict(policy),
            policy_digest=policy_digest,
            environment=env,
            resources=list(resources),
            now=now,
        )

        return {
            "status": "ok",
            "challenge_id": challenge.challenge_id,
            "task_id": challenge.task_id,
            "generation": challenge.generation,
            "policy_digest": challenge.policy_digest,
            "expires_at": challenge.expires_at.isoformat().replace("+00:00", "Z"),
        }

    def handle_issue_grant(
        self,
        token_str: str,
        payload: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Verify operator token, consume challenge, and sign execution grant."""
        operator = self.verifier.verify_token(token_str, now=now)

        challenge_id = str(payload.get("challenge_id") or "").strip()
        if not challenge_id:
            raise ChallengeError("challenge_id is required")

        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            raise ChallengeError("task_id is required")

        generation = payload.get("generation")
        if type(generation) is not int or generation < 0:
            raise ChallengeError("generation must be a non-negative integer")

        policy = payload.get("policy_snapshot")
        if not isinstance(policy, Mapping):
            raise ChallengeError("policy_snapshot must be an object")

        self.validate_task_policy(task_id, policy)

        # Atomically validate and consume challenge (enforcing single use)
        challenge = self.challenge_store.consume_challenge(
            challenge_id=challenge_id,
            actor_uid=operator.uid,
            task_id=task_id,
            generation=generation,
            policy_snapshot=dict(policy),
            now=now,
        )

        current_time = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)

        # Cryptographically sign the grant using the dedicated Ed25519 signer
        signed_grant = self.signer.sign_grant(
            task_id=task_id,
            generation=generation,
            policy=challenge.policy_snapshot,
            actor_uid=operator.uid,
            now=current_time,
            freshness_seconds=self.grant_freshness_seconds,
            run_ttl_seconds=self.run_ttl_seconds,
        )

        # Record redacted audit receipt (no bearer token, no private key, no raw auth token)
        self._record_audit_receipt(
            {
                "event": "grant_issued",
                "ts": current_time.isoformat().replace("+00:00", "Z"),
                "task_id": task_id,
                "generation": generation,
                "actor_uid": operator.uid,
                "actor_email": operator.email,
                "signer_key_id": self.signer.key_id,
                "nonce": signed_grant["nonce"],
                "policy_digest": signed_grant["policy_digest"],
                "expires_at": signed_grant["expires_at"],
            }
        )

        return {
            "status": "ok",
            "grant": signed_grant,
        }

    def get_liveness(self) -> dict[str, Any]:
        """Return trivial liveness status: the process is up and serving requests.

        Liveness intentionally does not exercise any dependency (certificate
        fetch, signer, allowlist); that is the job of get_readiness().
        """
        return {"status": "ok", "service": "execution-grant-issuer"}

    def get_readiness(self) -> dict[str, Any]:
        """Return readiness status by actually exercising each required
        dependency: Identity Platform certificate availability, a non-empty
        operator allowlist, a loadable signer, and a configured task policy.

        Returns status "ok" only when every check succeeds; otherwise
        "unavailable" with per-check detail so the caller can return a
        non-200 status rather than reporting false health.
        """
        checks: dict[str, str] = {}
        healthy = True

        try:
            self.verifier._get_google_public_keys()
            checks["identity_platform_certs"] = "ok"
        except Exception as exc:
            checks["identity_platform_certs"] = f"unavailable: {exc}"
            healthy = False

        if not self.verifier.allowed_operator_uids:
            checks["operator_allowlist"] = "unavailable: allowlist is empty"
            healthy = False
        else:
            checks["operator_allowlist"] = "ok"

        try:
            fingerprint = self.signer.public_key_fingerprint
            if not fingerprint:
                raise ValueError("signer fingerprint is empty")
            checks["signer"] = "ok"
        except Exception as exc:
            checks["signer"] = f"unavailable: {exc}"
            healthy = False

        if self.allowed_tasks is not None and not self.allowed_tasks:
            checks["task_policy"] = "unavailable: allowed_tasks is empty"
            healthy = False
        elif self.allowed_environments is not None and not self.allowed_environments:
            checks["task_policy"] = "unavailable: allowed_environments is empty"
            healthy = False
        else:
            checks["task_policy"] = "ok"

        return {
            "status": "ok" if healthy else "unavailable",
            "service": "execution-grant-issuer",
            "version": "1.0.0",
            "identity_project": self.verifier.project_id,
            "signer_key_id": self.signer.key_id,
            "allowed_tasks": sorted(self.allowed_tasks) if self.allowed_tasks is not None else None,
            "allowed_environments": (
                sorted(self.allowed_environments) if self.allowed_environments is not None else None
            ),
            "checks": checks,
        }


class RedactedIssuerHTTPRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler with strict header/body redaction in access logs."""

    service: ExecutionGrantIssuerService

    def log_message(self, format: str, *args: Any) -> None:
        """Custom access log that strips query parameters and never logs bodies or auth."""
        # Clean request line of query strings or sensitive parameters
        clean_path = self.path.split("?")[0]
        sys.stderr.write(
            f"[{self.log_date_time_string()}] {self.address_string()} {self.command} {clean_path} "
            f"- {args[1] if len(args) > 1 else ''}\n"
        )

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _get_auth_token(self) -> str:
        auth_header = self.headers.get("Authorization", "")
        if not auth_header:
            raise AuthenticationError("Authorization header is missing")
        return auth_header

    def _read_json_body(self) -> dict[str, Any]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise IssuerError("Invalid Content-Length header", status_code=400)

        if content_length <= 0:
            raise IssuerError("Request body is empty", status_code=400)
        if content_length > 1024 * 1024:
            raise IssuerError("Request body exceeds 1MB limit", status_code=413)

        raw_bytes = self.rfile.read(content_length)
        try:
            parsed = json.loads(raw_bytes.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise IssuerError("JSON body must be an object", status_code=400)
            return parsed
        except json.JSONDecodeError as exc:
            raise IssuerError(f"Malformed JSON body: {exc}", status_code=400)

    def do_GET(self) -> None:
        clean_path = self.path.split("?")[0].rstrip("/")
        if clean_path in ("", "/tooling"):
            html_file = Path(__file__).parent / "web" / "index.html"
            if html_file.is_file():
                content = html_file.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Tooling UI file not found"})
            return

        if clean_path == "/livez":
            self._send_json(HTTPStatus.OK, self.service.get_liveness())
            return

        if clean_path in ("/healthz", "/health", "/readyz"):
            result = self.service.get_readiness()
            status = HTTPStatus.OK if result.get("status") == "ok" else HTTPStatus.SERVICE_UNAVAILABLE
            self._send_json(status, result)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not Found"})

    def do_POST(self) -> None:
        clean_path = self.path.split("?")[0].rstrip("/")
        try:
            if clean_path == "/v1/challenge":
                token = self._get_auth_token()
                body = self._read_json_body()
                resp = self.service.handle_create_challenge(token, body)
                self._send_json(HTTPStatus.OK, resp)
                return

            if clean_path == "/v1/issue":
                token = self._get_auth_token()
                body = self._read_json_body()
                resp = self.service.handle_issue_grant(token, body)
                self._send_json(HTTPStatus.OK, resp)
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not Found"})
        except IssuerError as exc:
            self._send_json(exc.status_code, {"status": "error", "error": exc.message})
        except Exception as exc:
            logger.exception("Unhandled server error: %s", exc)
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"status": "error", "error": "Internal server error"})


def create_issuer_server(
    service: ExecutionGrantIssuerService,
    *,
    host: str = "127.0.0.1",
    port: int = 8090,
    ssl_cert_file: str | Path | None = None,
    ssl_key_file: str | Path | None = None,
) -> ThreadingHTTPServer:
    """Create and return a configured ThreadingHTTPServer instance."""

    class BoundHandler(RedactedIssuerHTTPRequestHandler):
        pass

    BoundHandler.service = service
    server = ThreadingHTTPServer((host, port), BoundHandler)
    if ssl_cert_file and ssl_key_file:
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(certfile=str(ssl_cert_file), keyfile=str(ssl_key_file))
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server
