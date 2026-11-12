#!/usr/bin/env python3
"""Protected caller for the ORIGINAL paired Persona/strategy read-only QA.

Runs AFTER the existing dev Root has accepted the exact FE/BFF pair.  It is a
supplementary measurement, never a release gate: it records the raw CLI
outcome and exits non-zero for anything that is not a proven PASS, and the
workflow step that calls it must not rewrite that outcome.

Contract with the frontend CLI (``accept-persona-strategy-hosted-readonly.mjs``):
one JSON document on stdin, ``--out-dir`` as the only argument.  No other
schema is parsed and there is no compatibility fallback; a missing CLI or a
schema mismatch is reported as ``unproven``.

Secrets (the four dev-login client secrets) are read from the process
environment supplied by the protected workflow step, kept in RAM, and written
only to the CLI's stdin.  They never enter argv, evidence, or logs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

SCHEMA = "pantheon.dev-original-paired-readonly-qa-caller.v1"
CLI_RELATIVE = "scripts/accept-persona-strategy-hosted-readonly.mjs"
PROFILES = ("TEST_ADMIN", "OPERATOR_A", "OPERATOR_B", "VIEWER")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
HOST_SUFFIX = ".mvl-cap.tw"
BASELINE_PATH = re.compile(
    r"^/home/[A-Za-z0-9_.-]+/pantheon-acceptance-captures/[A-Za-z0-9_./-]+\.json$"
)
INVENTORY_PATH = "/bff/management/data-sources"
CLI_TIMEOUT_SECONDS = 3600

Http = Callable[[str, str, Mapping[str, str], "bytes | None", float], "tuple[int, Any]"]
CliRunner = Callable[[Sequence[str], bytes, float], "tuple[int, str, str]"]
BaselineFetcher = Callable[[str, Path], None]


class Unproven(Exception):
    """The measurement could not be performed; never convertible to PASS."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_origin(label: str, url: str, canonical: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(HOST_SUFFIX)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.port not in (None, 443)
    ):
        raise Unproven("wrong_origin", f"{label} is not a canonical https {HOST_SUFFIX} origin")
    origin = f"https://{parsed.hostname}"
    if canonical and origin != canonical.rstrip("/"):
        raise Unproven("wrong_origin", f"{label} differs from the canonical origin")
    return origin


def default_http(
    url: str, method: str, headers: Mapping[str, str], body: "bytes | None", timeout: float
) -> "tuple[int, Any]":
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        response = urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 (https checked)
        status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    try:
        return status, json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        return status, {}


def default_cli_runner(argv: Sequence[str], stdin: bytes, timeout: float) -> "tuple[int, str, str]":
    # Minimal environment: nothing from the workflow (secrets included) leaks.
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp")}
    try:
        proc = subprocess.run(  # noqa: S603
            list(argv), input=stdin, capture_output=True, timeout=timeout, env=env, check=False
        )
    except subprocess.TimeoutExpired:
        return 124, "", "cli timed out"
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


def default_baseline_fetcher(remote_path: str, local_path: Path) -> None:
    transport = Path(__file__).resolve().parent / "dev_vm_ssh.sh"
    proc = subprocess.run(  # noqa: S603
        [str(transport), "copy-from", remote_path, str(local_path)],
        capture_output=True,
        timeout=300,
        check=False,
    )
    if proc.returncode != 0:
        raise Unproven("missing_private_baseline", "private baseline could not be fetched")


class Caller:
    def __init__(
        self,
        args: argparse.Namespace,
        environ: Mapping[str, str],
        *,
        http: Http = default_http,
        cli_runner: CliRunner = default_cli_runner,
        baseline_fetcher: BaselineFetcher = default_baseline_fetcher,
    ) -> None:
        self.args = args
        self.environ = environ
        self.http = http
        self.cli_runner = cli_runner
        self.baseline_fetcher = baseline_fetcher
        self.secrets: list[str] = []

    # -- helpers ---------------------------------------------------------
    def _redact(self, text: str) -> str:
        for secret in self.secrets:
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text

    def _identity(self, fe: str, bff: str) -> dict:
        timeout = 30
        status, deployment = self.http(f"{fe}/deployment.json", "GET", {"Accept": "application/json"}, None, timeout)
        if status != 200 or not isinstance(deployment, dict):
            raise Unproven("identity_unreachable", "FE deployment identity unavailable")
        status, version = self.http(f"{bff}/bff/version", "GET", {"Accept": "application/json"}, None, timeout)
        if status != 200 or not isinstance(version, dict):
            raise Unproven("identity_unreachable", "BFF version identity unavailable")
        live_fe = str(deployment.get("commit") or deployment.get("frontendSha") or "")
        live_bff = str(version.get("source_commit_sha") or version.get("commit") or "")
        posture = version.get("config_posture") if isinstance(version.get("config_posture"), dict) else {}
        if live_fe != self.args.fe_sha or live_bff != self.args.bff_sha:
            raise Unproven("pair_mismatch", "served FE/BFF differs from the accepted pair")
        if posture.get("auth_mode") != "strict" or posture.get("auth_stub") is True:
            raise Unproven("auth_posture", "BFF auth posture is not strict")
        return {"fe": live_fe, "bff": live_bff, "auth_mode": "strict"}

    def _login(self, bff: str, profile: str) -> str:
        cred = self.profiles[profile]
        body = json.dumps(
            {"grant_type": "client_credentials", **cred}, separators=(",", ":")
        ).encode()
        status, resp = self.http(
            f"{bff}/bff/auth/dev-login",
            "POST",
            {"Accept": "application/json", "Content-Type": "application/json"},
            body,
            30,
        )
        token = str(resp.get("access_token") or "") if isinstance(resp, dict) else ""
        if status != 200 or not token:
            raise Unproven("login_failed", f"dev-login failed for {profile}")
        self.secrets.append(token)
        return token

    def _inventory(self, bff: str) -> dict:
        """Authenticated read-only owner inventory (no backing-store reads)."""
        token = self._login(bff, "TEST_ADMIN")
        headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
        ids: list[str] = []
        mode = meta_source = None
        page = ""
        try:
            for _ in range(100):
                url = f"{bff}{INVENTORY_PATH}?page_size=100" + (f"&page_token={page}" if page else "")
                status, resp = self.http(url, "GET", headers, None, 60)
                if status != 200 or not isinstance(resp, dict):
                    raise Unproven("inventory_failed", "owner inventory request failed")
                meta = resp.get("meta") if isinstance(resp.get("meta"), dict) else {}
                data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
                src = str(meta.get("source") or "")
                this_mode = "governed_owner" if src == "service_client" else "legacy_projection"
                if mode not in (None, this_mode):
                    raise Unproven("inventory_failed", "owner inventory mode changed between pages")
                mode, meta_source = this_mode, src
                for item in data.get("items") or []:
                    ident = item.get("source_instance_id") if mode == "governed_owner" else None
                    ident = ident or item.get("connector_id") or item.get("id")
                    if not ident:
                        raise Unproven("inventory_failed", "owner inventory row has no identifier")
                    ids.append(str(ident))
                info = resp.get("page_info") if isinstance(resp.get("page_info"), dict) else {}
                page = str(info.get("next_page_token") or "")
                if not page:
                    break
            else:
                raise Unproven("inventory_failed", "owner inventory pagination did not terminate")
        finally:
            self.http(f"{bff}/bff/logout", "POST", {**headers, "Content-Type": "application/json"}, b"{}", 30)
        return {"connector_ids": sorted(set(ids)), "source_mode": mode, "meta_source": meta_source}

    # -- main ------------------------------------------------------------
    def run(self) -> dict:
        a = self.args
        evidence: dict[str, Any] = {
            "schema_version": SCHEMA,
            "started_at": _now(),
            "supplementary_not_a_release_gate": True,
            "run": {"root": a.root_run_id, "pair": a.pair_id, "fe": a.fe_sha, "bff": a.bff_sha},
            "outcome": "unproven",
        }
        tmp = Path(tempfile.mkdtemp(prefix="pantheon-readonly-qa-"))
        os.chmod(tmp, 0o700)
        try:
            self._run(evidence, tmp)
        except Unproven as exc:
            evidence["outcome"] = "unproven"
            evidence["reason_code"] = exc.code
            evidence["reason"] = self._redact(str(exc))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        evidence["finished_at"] = _now()
        return evidence

    def _run(self, evidence: dict, tmp: Path) -> None:
        a, env = self.args, self.environ
        for label, sha in (("fe_sha", a.fe_sha), ("bff_sha", a.bff_sha)):
            if not SHA40.match(a.__dict__[label] or ""):
                raise Unproven("bad_pair", f"{label} is not an exact 40-hex SHA")
        if not (a.pair_id and a.root_run_id and a.tenant_id and a.persona_id):
            raise Unproven("missing_input", "pair/root/tenant/persona identity is required")
        fe = _check_origin("DEV_FE_URL", a.fe_url, "https://app.dev.mvl-cap.tw")
        bff = _check_origin("DEV_BFF_URL", a.bff_url, "https://api.dev.mvl-cap.tw")
        try:
            roles = json.loads(a.expected_roles_json or "")
            if set(roles) != set(PROFILES) or not all(isinstance(v, list) and v for v in roles.values()):
                raise ValueError
        except ValueError:
            raise Unproven("missing_profile", "expected roles for all four profiles are required") from None

        profiles: dict[str, dict[str, str]] = {}
        for name in PROFILES:
            cid = env.get(f"DEV_BFF_DEV_LOGIN_{name}_CLIENT_ID", "")
            secret = env.get(f"DEV_BFF_DEV_LOGIN_{name}_CLIENT_SECRET", "")
            if not cid or not secret:
                raise Unproven("missing_profile", f"profile {name} credentials are not configured")
            self.secrets.append(secret)
            profiles[name] = {"client_id": cid, "client_secret": secret}
        self.profiles = profiles

        cli = Path(a.frontend_checkout) / CLI_RELATIVE
        if not cli.is_file() or cli.is_symlink():
            raise Unproven("missing_cli", "qualified frontend CLI is absent from the exact FE revision")
        evidence["cli"] = {"path": CLI_RELATIVE, "sha256": hashlib.sha256(cli.read_bytes()).hexdigest()}

        if not SHA64.match(a.baseline_sha256 or ""):
            raise Unproven("missing_private_baseline", "expected baseline sha256 is required")
        if not BASELINE_PATH.match(a.baseline_remote_path or "") or ".." in a.baseline_remote_path:
            raise Unproven("missing_private_baseline", "baseline path is not an allowed private acceptance path")
        local = tmp / "baseline.json"
        self.baseline_fetcher(a.baseline_remote_path, local)
        if not local.is_file():
            raise Unproven("missing_private_baseline", "private baseline was not retrieved")
        raw = local.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        evidence["baseline"] = {"sha256": digest, "bytes": len(raw)}  # metadata only
        if digest != a.baseline_sha256:
            raise Unproven("baseline_hash_mismatch", "private baseline does not match the expected hash")
        try:
            baseline_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise Unproven("missing_private_baseline", "private baseline is not UTF-8 text") from None

        evidence["identity_before"] = self._identity(fe, bff)
        inventory = self._inventory(bff)
        evidence["source_owner_registry"] = inventory

        payload = {
            "profiles": profiles,
            "identity": {"DEV_FE_URL": fe, "DEV_BFF_URL": bff},
            "expected": {
                "fe": a.fe_sha,
                "bff": a.bff_sha,
                "root": a.root_run_id,
                "pair": a.pair_id,
                "tenant_id": a.tenant_id,
                "roles": roles,
                "persona_id": a.persona_id,
                "source_owner_registry": inventory,
            },
            "baseline_raw": baseline_text,
            "baseline_identity": {"sha256": digest},
        }
        out_dir = Path(a.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(out_dir, 0o700)
        code, out, err = self.cli_runner(
            ["node", str(cli), "--out-dir", str(out_dir)],
            json.dumps(payload, separators=(",", ":")).encode(),
            CLI_TIMEOUT_SECONDS,
        )
        evidence["cli_result"] = {
            "exit_code": code,
            "stdout_tail": self._redact(out)[-4000:],
            "stderr_tail": self._redact(err)[-2000:],
        }
        evidence["identity_after"] = self._identity(fe, bff)  # drift raises Unproven
        evidence["outcome"] = "pass" if code == 0 else "failed"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    for name in (
        "fe-url", "bff-url", "fe-sha", "bff-sha", "root-run-id", "pair-id", "tenant-id",
        "persona-id", "expected-roles-json", "frontend-checkout", "baseline-remote-path",
        "baseline-sha256", "out-dir", "evidence-out",
    ):
        p.add_argument(f"--{name}", default="")
    return p


def main(argv: "Sequence[str] | None" = None, environ: "Mapping[str, str] | None" = None, **kw: Any) -> int:
    args = build_parser().parse_args(argv)
    if not args.evidence_out or not args.out_dir:
        print("--evidence-out and --out-dir are required", file=sys.stderr)
        return 2
    caller = Caller(args, os.environ if environ is None else environ, **kw)
    evidence = caller.run()
    dest = Path(args.evidence_out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    os.chmod(dest, 0o600)
    print(json.dumps({"outcome": evidence["outcome"], "reason_code": evidence.get("reason_code")}))
    return 0 if evidence["outcome"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
