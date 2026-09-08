#!/usr/bin/env python3
"""Scoped CLI client for preparing, requesting, and submitting execution grants.

OPS-EXECUTION-MFA-ISSUER-001.
Source of record: ISSUER-SA-SD-20260908.md.

Enforces:
1. Reads canonical task through the qualified existing status CLI (not raw JSON).
2. Supports requesting exact task IDs without becoming an authorization authority;
   issuer-side configured exact scope and canonical task policy remain authoritative.
3. Accepts operator token from protected file or stdin (NEVER argv).
4. Verifies downloaded grant locally using existing verifier and configured public trust.
5. Submits only through existing governed Human/Ops CLI (no new task writer).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

# Resolve .orchestrator for execution_authorization imports
ROOT_DIR = Path(__file__).resolve().parents[1]
ORCHESTRATOR_DIR = ROOT_DIR / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

try:
    import execution_authorization as ea
except ImportError:
    ea = None  # Handled gracefully if executed in foreign environment

from execution_grant_issuer.secure_io import UnsafeCredentialFileError, read_private_file_strict

DEFAULT_ALLOWED_TASK = "DEV502-TRACE-001"
DEFAULT_ALLOWED_ENV = "pantheon-dev"
DEFAULT_ISSUER_URL = "http://127.0.0.1:8090"


def _check_no_secrets_in_argv() -> None:
    """Refuse execution if token or raw grant is passed directly on command-line argument."""
    for arg in sys.argv[1:]:
        if arg == "--token" or arg.startswith("--token="):
            sys.stderr.write(
                "ERROR: Passing ID tokens directly via command-line arguments leaks credentials "
                "in process listings (ps). Use --token-file <path> or --token-stdin instead.\n"
            )
            sys.exit(2)
        if arg in ("--grant", "--grant-json") or arg.startswith(("--grant=", "--grant-json=")):
            sys.stderr.write(
                "ERROR: Passing execution grants directly via command-line arguments leaks bearer secrets "
                "in process listings (ps) and shell history. Use --grant-file <path> or --grant-stdin instead.\n"
            )
            sys.exit(2)


def _canonical_json(val: Any) -> bytes:
    return json.dumps(val, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


def get_command_root() -> Path:
    """Resolve the governed command root strictly from environment."""
    env_root = os.environ.get("PANTHEON_COMMAND_ROOT")
    if not env_root or not Path(env_root).is_dir():
        raise RuntimeError(
            "PANTHEON_COMMAND_ROOT environment variable must be set and point to a valid directory; "
            "unqualified checkout fallbacks are strictly prohibited"
        )
    return Path(env_root).resolve()


def fetch_canonical_task(task_id: str) -> dict[str, Any]:
    """Read authoritative task row via qualified ai-status.sh show command."""
    command_root = get_command_root()
    script = command_root / "scripts" / "ai-status.sh"
    if not script.is_file():
        raise RuntimeError(
            f"Qualified status script not found at {script}; "
            "checkout and python fallbacks are not permitted"
        )
    cmd = [str(script), "show", task_id]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to fetch canonical task {task_id} (exit {proc.returncode}): {proc.stderr.strip()}"
            )
        data = json.loads(proc.stdout)
        task = data.get("task")
        if not task:
            raise RuntimeError(f"show command returned no 'task' object for {task_id}")
        return task
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON returned by status CLI for {task_id}: {exc}") from exc


def validate_task_eligibility(
    task: Mapping[str, Any],
    **kwargs: Any,
) -> tuple[dict[str, Any], int]:
    """Validate task against target scope and execution policy.

    The CLI supports requesting exact task IDs without becoming an authorization
    authority. Issuer-side configured exact scope and canonical task binding
    remain authoritative.
    """
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        raise ValueError("Task has no 'id' field")

    if any(c in task_id for c in ("*", "?", "[", "]", "{", "}", " ", "\t", "\n")):
        raise ValueError(f"Task ID contains invalid characters or wildcards: {task_id!r}")

    ea_record = task.get("execution_authorization")
    if not isinstance(ea_record, Mapping):
        raise ValueError(f"Task {task_id} has no execution_authorization record")

    policy = ea_record.get("policy")
    if not isinstance(policy, Mapping):
        raise ValueError(f"Task {task_id} execution_authorization has no policy")

    if policy.get("requires_execution_authorization") is not True:
        raise ValueError(f"Task {task_id} policy does not require execution authorization")

    env = str(policy.get("environment") or "").strip()
    if not env:
        raise ValueError(f"Task {task_id} policy has no environment")
    if env != DEFAULT_ALLOWED_ENV:
        raise ValueError(f"Task {task_id} environment {env!r} does not match required {DEFAULT_ALLOWED_ENV!r}")

    generation = task.get("generation", 0)
    if type(generation) is not int or generation < 0:
        raise ValueError(f"Task {task_id} has invalid generation: {generation}")

    return dict(policy), generation


def load_token(token_file: str | None, token_stdin: bool) -> str:
    """Read operator ID token from file or stdin."""
    if token_stdin:
        token = sys.stdin.read().strip()
    elif token_file:
        token_path = Path(token_file).expanduser()
        try:
            token_bytes = read_private_file_strict(token_path, description="Operator ID token file")
        except UnsafeCredentialFileError as exc:
            raise ValueError(str(exc)) from exc
        token = token_bytes.decode("utf-8").strip()
    else:
        env_token = os.environ.get("OPERATOR_ID_TOKEN", "").strip()
        if env_token:
            token = env_token
        else:
            raise ValueError("Operator ID token required: provide --token-file, --token-stdin, or OPERATOR_ID_TOKEN env")

    if not token:
        raise ValueError("Operator ID token is empty")
    return token


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse redirects on authenticated requests to prevent credential leaks."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            f"Redirects are not permitted for authenticated requests: {newurl}",
            headers,
            fp,
        )


def validate_issuer_url(url: str) -> None:
    """Validate issuer URL against scheme, userinfo, fragment, and remote HTTP rules."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")
    if parsed.username or parsed.password:
        raise ValueError("URLs with embedded userinfo (credentials) are strictly prohibited")
    if parsed.fragment:
        raise ValueError("URLs with fragments are not permitted")
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme == "http" and hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(f"Insecure HTTP is only permitted for loopback testing; remote URL {url!r} must use HTTPS")


def post_json(url: str, payload: dict[str, Any], auth_token: str) -> dict[str, Any]:
    """Execute HTTP POST with JSON body and Bearer token, refusing redirects and insecure remote URLs."""
    validate_issuer_url(url)
    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=req_data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {auth_token}",
            "User-Agent": "pantheon-request-execution-grant/1.0",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(NoRedirectHandler)
    try:
        with opener.open(req, timeout=15) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content)
    except urllib.error.HTTPError as exc:
        # Never echo the remote response body verbatim: it is attacker/issuer
        # controlled content (which may reflect the submitted bearer token or
        # arbitrary text) and must not reach the operator's terminal or logs.
        # Only a small allowlisted set of known, non-sensitive issuer error
        # fields is surfaced; anything else collapses to a fixed generic
        # message that still carries the HTTP status code for diagnosis.
        err_msg = f"issuer returned HTTP {exc.code}"
        try:
            exc.read()  # drain the body without ever inspecting/echoing it
        except Exception:
            pass
        raise RuntimeError(f"HTTP {exc.code} from issuer ({url}): {err_msg}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Connection failed to issuer ({url}): {exc.reason}") from exc


def write_private_exclusive_json(path_str: str | Path, data: Any) -> Path:
    """Atomically create and write JSON to a private 0600 file without following symlinks or clobbering."""
    out_path = Path(path_str)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    try:
        fd = os.open(str(out_path), flags, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"Output file already exists or is a symlink: {out_path}") from exc
    except OSError as exc:
        raise RuntimeError(f"Failed to open exclusive output file {out_path}: {exc}") from exc

    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
    except Exception:
        raise
    return out_path


def load_trusted_keys(config_path: Path | None = None) -> dict[str, str]:
    """Load trusted MFA issuer public keys from config."""
    paths_to_try: list[Path] = []
    if config_path:
        paths_to_try.append(config_path)

    try:
        cmd_root = get_command_root()
        paths_to_try.append(cmd_root / ".orchestrator" / "config.json")
    except Exception:
        pass

    for p in paths_to_try:
        if p.is_file():
            try:
                cfg = json.loads(p.read_text(encoding="utf-8"))
                section = cfg.get("execution_authorization", {})
                keys = section.get("mfa_issuer_public_keys", {})
                if isinstance(keys, Mapping):
                    return {str(k): str(v) for k, v in keys.items() if str(k).strip() and str(v).strip()}
            except Exception:
                continue
    return {}


def verify_grant_locally(
    grant: Mapping[str, Any],
    task: Mapping[str, Any],
    policy: Mapping[str, Any],
    trusted_keys: Mapping[str, str],
) -> str:
    """Verify grant locally against execution_authorization rules and trusted keys."""
    if ea is None:
        raise RuntimeError("execution_authorization module is unavailable; cannot verify grant locally")

    if not trusted_keys:
        raise RuntimeError(
            "No trusted MFA issuer public keys found in configuration; "
            "cannot verify grant locally"
        )

    task_id = str(task.get("id") or "")
    generation = int(task.get("generation", 0))
    now = datetime.now(timezone.utc)

    fingerprint = ea.verify_execution_grant(
        grant,
        policy=policy,
        task_id=task_id,
        generation=generation,
        trusted_issuers=trusted_keys,
        now=now,
        task=task,
    )
    return fingerprint


def submit_grant_via_cli(task_id: str, grant: Mapping[str, Any]) -> None:
    """Submit verified grant via governed scripts/ai-status.sh execution-grant-submit."""
    command_root = get_command_root()
    script = command_root / "scripts" / "ai-status.sh"
    if not script.is_file():
        raise RuntimeError(
            f"Qualified status script not found at {script}; "
            "checkout and python fallbacks are not permitted"
        )

    grant_json = json.dumps(grant, separators=(",", ":"))
    env = dict(os.environ)
    env["AI_NAME"] = "Human/Ops"
    env["EXECUTION_GRANT_JSON"] = grant_json

    cmd = [str(script), "execution-grant-submit", task_id]
    proc = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"CLI grant submission failed with exit code {proc.returncode}: {proc.stderr.strip()}")


def cmd_prepare(args: argparse.Namespace) -> None:
    task = fetch_canonical_task(args.task)
    policy, generation = validate_task_eligibility(task)
    payload = {
        "task_id": task["id"],
        "generation": generation,
        "policy_snapshot": policy,
        "environment": policy.get("environment"),
        "resources": policy.get("resources"),
    }
    if args.out:
        out_path = write_private_exclusive_json(args.out, payload)
        print(f"Wrote prepared challenge request to {out_path}")
    else:
        print(json.dumps(payload, indent=2))


def cmd_request(args: argparse.Namespace) -> None:
    task = fetch_canonical_task(args.task)
    policy, generation = validate_task_eligibility(task)

    # Missing trust MUST reject before any request
    trusted_keys = load_trusted_keys(Path(args.config_file) if args.config_file else None)
    if not trusted_keys:
        raise RuntimeError(
            "Missing trusted MFA issuer public keys in configuration; "
            "cannot proceed with grant request without configured trust"
        )

    issuer_url = args.issuer_url.rstrip("/")
    validate_issuer_url(issuer_url)
    token = load_token(args.token_file, args.token_stdin)

    # Step 1: Challenge
    challenge_payload = {
        "task_id": task["id"],
        "generation": generation,
        "policy_snapshot": policy,
    }
    challenge_resp = post_json(f"{issuer_url}/v1/challenge", challenge_payload, token)
    cid = challenge_resp.get("challenge_id")
    if not cid or not isinstance(cid, str):
        # Never echo the response body or even its key names: an HTTP-200
        # response is still attacker/issuer-controlled content and may
        # reflect the submitted bearer token or other sensitive input back
        # at the operator's terminal or logs (including as a dict key, not
        # only a value). Only a fixed, non-reflective diagnostic is raised.
        raise RuntimeError(
            f"Issuer response to {issuer_url}/v1/challenge did not contain a valid "
            f"'challenge_id' string (response had {len(challenge_resp)} field(s))"
        )

    # Step 2: Issue
    issue_payload = {
        "challenge_id": cid,
        "task_id": task["id"],
        "generation": generation,
        "policy_snapshot": policy,
    }
    issue_resp = post_json(f"{issuer_url}/v1/issue", issue_payload, token)
    grant = issue_resp.get("grant")
    if not grant or not isinstance(grant, Mapping):
        # Same non-reflective diagnostic rule as the challenge response above.
        raise RuntimeError(
            f"Issuer response to {issuer_url}/v1/issue did not contain a valid 'grant' "
            f"object (response had {len(issue_resp)} field(s))"
        )

    # Step 3: Local verification (NEVER skipped)
    try:
        fp = verify_grant_locally(grant, task, policy, trusted_keys)
        key_id = grant.get("signature", {}).get("key_id", "unknown")
        print(f"✓ Grant locally verified against trusted issuer {key_id!r} (fp: {fp[:16]}...)")
    except Exception as exc:
        raise RuntimeError(f"Local grant verification FAILED: {exc}") from exc

    # Save to file if requested (atomic exclusive 0600 without symlink clobber)
    if args.grant_out:
        out_path = write_private_exclusive_json(args.grant_out, grant)
        print(f"Wrote signed execution grant to {out_path}")

    # Step 4: Submission if requested
    if args.submit:
        # Refetch and compare full canonical task/generation/policy/owner immediately before submission
        task_refetched = fetch_canonical_task(task["id"])
        policy_refetched, gen_refetched = validate_task_eligibility(task_refetched)

        if task_refetched.get("id") != task.get("id"):
            raise RuntimeError("Canonical task ID mismatch on refetch before submission")
        if gen_refetched != generation:
            raise RuntimeError(
                f"Canonical task generation changed from {generation} to {gen_refetched} before submission"
            )
        if task_refetched.get("owner") != task.get("owner"):
            raise RuntimeError(
                f"Canonical task owner changed from {task.get('owner')!r} to {task_refetched.get('owner')!r} before submission"
            )
        if _canonical_json(policy_refetched) != _canonical_json(policy):
            raise RuntimeError("Canonical task policy changed concurrently before submission")

        print(f"Submitting execution grant for {task['id']} via governed CLI...")
        submit_grant_via_cli(task["id"], grant)
        print(f"✓ Execution grant successfully verified and submitted for {task['id']}.")
    else:
        # Never print bearer or shell command containing grant JSON
        if not args.grant_out:
            print("✓ Grant issued and locally verified successfully. (Specify --grant-out to save or --submit to submit)")


def cmd_submit(args: argparse.Namespace) -> None:
    task = fetch_canonical_task(args.task)
    policy, generation = validate_task_eligibility(task)

    trusted_keys = load_trusted_keys(Path(args.config_file) if args.config_file else None)
    if not trusted_keys:
        raise RuntimeError(
            "Missing trusted MFA issuer public keys in configuration; "
            "cannot proceed with grant submission without configured trust"
        )

    if args.grant_stdin:
        raw_text = sys.stdin.read().strip()
        if not raw_text:
            raise ValueError("Grant JSON from stdin is empty")
        try:
            grant = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed grant JSON from stdin: {exc}") from exc
    elif args.grant_file:
        grant_path = Path(args.grant_file).expanduser()
        try:
            grant_bytes = read_private_file_strict(grant_path, description="Execution grant file")
        except UnsafeCredentialFileError as exc:
            raise ValueError(str(exc)) from exc
        try:
            grant = json.loads(grant_bytes.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed grant JSON from {grant_path}: {exc}") from exc
    else:
        raise ValueError("Must provide either --grant-file <path> or --grant-stdin")

    if not isinstance(grant, Mapping):
        raise ValueError("Execution grant must be a JSON object")

    # Local verification
    try:
        fp = verify_grant_locally(grant, task, policy, trusted_keys)
        key_id = grant.get("signature", {}).get("key_id", "unknown")
        print(f"✓ Grant locally verified against trusted issuer {key_id!r} (fp: {fp[:16]}...)")
    except Exception as exc:
        raise RuntimeError(f"Local grant verification FAILED: {exc}") from exc

    # CAS refetch checks
    task_refetched = fetch_canonical_task(task["id"])
    policy_refetched, gen_refetched = validate_task_eligibility(task_refetched)

    if task_refetched.get("id") != task.get("id"):
        raise RuntimeError("Canonical task ID mismatch on refetch before submission")
    if gen_refetched != generation:
        raise RuntimeError(
            f"Canonical task generation changed from {generation} to {gen_refetched} before submission"
        )
    if task_refetched.get("owner") != task.get("owner"):
        raise RuntimeError(
            f"Canonical task owner changed from {task.get('owner')!r} to {task_refetched.get('owner')!r} before submission"
        )
    if _canonical_json(policy_refetched) != _canonical_json(policy):
        raise RuntimeError("Canonical task policy changed concurrently before submission")

    print(f"Submitting execution grant for {task['id']} via governed CLI...")
    submit_grant_via_cli(task["id"], grant)
    print(f"✓ Execution grant successfully verified and submitted for {task['id']}.")


def main() -> None:
    _check_no_secrets_in_argv()

    parser = argparse.ArgumentParser(
        description="Scoped CLI client for Pantheon execution grant requests."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # prepare
    prep_p = subparsers.add_parser("prepare", help="Prepare task challenge request payload")
    prep_p.add_argument("--task", default=DEFAULT_ALLOWED_TASK, help="Task ID (default: %(default)s)")
    prep_p.add_argument("--out", help="Write output JSON to file")

    # request
    req_p = subparsers.add_parser("request", help="Request, verify, and optionally submit grant")
    req_p.add_argument("--task", default=DEFAULT_ALLOWED_TASK, help="Task ID (default: %(default)s)")
    req_p.add_argument("--issuer-url", default=os.environ.get("EXECUTION_GRANT_ISSUER_URL", DEFAULT_ISSUER_URL))
    req_p.add_argument("--token-file", help="Path to file containing Identity Platform ID token")
    req_p.add_argument("--token-stdin", action="store_true", help="Read ID token from standard input")
    req_p.add_argument("--config-file", help="Path to config.json containing trusted issuer keys")
    req_p.add_argument("--grant-out", help="Save downloaded grant to file")
    req_p.add_argument("--submit", action="store_true", help="Submit grant to Human/Ops CLI immediately")

    # submit
    sub_p = subparsers.add_parser("submit", help="Locally verify and submit grant from private file or stdin")
    sub_p.add_argument("--task", default=DEFAULT_ALLOWED_TASK, help="Task ID (default: %(default)s)")
    sub_p.add_argument("--grant-file", help="Path to private file containing execution grant JSON")
    sub_p.add_argument("--grant-stdin", action="store_true", help="Read execution grant JSON from standard input")
    sub_p.add_argument("--config-file", help="Path to config.json containing trusted issuer keys")

    args = parser.parse_args()

    try:
        if args.command == "prepare":
            cmd_prepare(args)
        elif args.command == "request":
            cmd_request(args)
        elif args.command == "submit":
            cmd_submit(args)
    except Exception as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
