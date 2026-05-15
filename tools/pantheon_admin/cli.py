#!/usr/bin/env python3
"""Pantheon Admin CLI

Executes operator control actions through the protected internal API.
"""
from __future__ import annotations

import argparse
import configparser
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_AUTH = 2
EXIT_USAGE = 3
EXIT_UNAVAILABLE = 4
EXIT_PARTIAL = 5

_DEFAULT_BASE_URL = os.getenv("PANTHEON_INTERNAL_API_URL", "http://localhost:5001")
_DEFAULT_TIMEOUT = int(os.getenv("PANTHEON_COMMAND_TIMEOUT_SECONDS", "30"))
_DEFAULT_CONFIG_PATH = os.path.expanduser("~/.pantheon/cli.conf")

log = logging.getLogger("pantheon_admin")


@dataclass
class CliContext:
    base_url: str
    auth_token: Optional[str]
    output: str
    timeout: int
    dry_run: bool
    verbose: bool


class NetworkError(RuntimeError):
    pass


class ApiError(RuntimeError):
    def __init__(self, status: int, payload: Dict[str, Any]):
        super().__init__(payload.get("error", {}).get("message", "API error"))
        self.status = status
        self.payload = payload


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_config(path: Optional[str]) -> Dict[str, str]:
    if not path:
        return {}
    if not os.path.exists(path):
        return {}
    parser = configparser.ConfigParser()
    parser.read(path)
    for section in ("pantheon", "cli", "DEFAULT"):
        if section in parser:
            return {k: v for k, v in parser[section].items()}
    return {}


def _resolve_context(args: argparse.Namespace) -> CliContext:
    config_path = args.config or os.getenv("PANTHEON_CLI_CONFIG") or _DEFAULT_CONFIG_PATH
    config = _load_config(config_path)
    base_url = (
        args.base_url
        or os.getenv("PANTHEON_INTERNAL_API_URL")
        or config.get("base_url")
        or _DEFAULT_BASE_URL
    )
    auth_token = (
        args.auth_token
        or os.getenv("PANTHEON_BEARER_TOKEN")
        or config.get("auth_token")
    )
    output = (
        args.output
        or os.getenv("PANTHEON_CLI_OUTPUT")
        or config.get("output")
        or "text"
    )
    timeout_value = args.timeout or config.get("timeout")
    try:
        timeout = int(timeout_value) if timeout_value is not None else _DEFAULT_TIMEOUT
    except ValueError:
        timeout = _DEFAULT_TIMEOUT
    verbose = bool(args.verbose)
    dry_run = bool(args.dry_run)
    return CliContext(
        base_url=base_url.rstrip("/"),
        auth_token=auth_token,
        output=output,
        timeout=timeout,
        dry_run=dry_run,
        verbose=verbose,
    )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _request_json(
    ctx: CliContext,
    method: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    mfa_token: Optional[str] = None,
    params: Optional[Dict[str, str]] = None,
) -> Tuple[int, Dict[str, Any]]:
    url = f"{ctx.base_url}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if ctx.auth_token:
        headers["Authorization"] = f"Bearer {ctx.auth_token}"
    if mfa_token:
        headers["X-MFA-Token"] = mfa_token

    if ctx.verbose:
        log.debug("HTTP %s %s", method, url)

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=ctx.timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"error": {"code": str(exc.code), "message": raw or exc.reason}}
        return exc.code, payload
    except urllib.error.URLError as exc:
        raise NetworkError(str(exc)) from exc


def _call_api(
    ctx: CliContext,
    method: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    mfa_token: Optional[str] = None,
    params: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
    try:
        status, body = _request_json(
            ctx,
            method,
            path,
            payload,
            mfa_token=mfa_token,
            params=params,
        )
        return status, body, None
    except NetworkError as exc:
        payload = {"error": {"code": "SERVICE_UNAVAILABLE", "message": str(exc)}}
        _emit_error(ctx, 503, payload)
        return None, None, EXIT_UNAVAILABLE


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _emit(ctx: CliContext, payload: Dict[str, Any], *, title: Optional[str] = None) -> None:
    if ctx.output == "json":
        print(json.dumps(payload, indent=2, sort_keys=False))
        return
    if title:
        print(title)
    if payload:
        for key, value in payload.items():
            print(f"{key}: {value}")


def _emit_error(ctx: CliContext, status: int, payload: Dict[str, Any]) -> None:
    if ctx.output == "json":
        print(json.dumps({"status": status, **payload}, indent=2, sort_keys=False))
        return
    error = payload.get("error") or {}
    code = error.get("code", status)
    message = error.get("message", "request failed")
    print(f"ERROR {code}: {message}", file=sys.stderr)


def _exit_code_from_status(status: int, payload: Dict[str, Any]) -> int:
    if status < 300:
        return EXIT_SUCCESS
    if status in (401, 403):
        return EXIT_AUTH
    if status in (502, 503, 504):
        return EXIT_UNAVAILABLE
    if status == 400 and payload.get("error", {}).get("code") == "MFA_VALIDATION_FAILED":
        return EXIT_AUTH
    if status == 400:
        return EXIT_USAGE
    return EXIT_FAILURE


def _require_auth(ctx: CliContext) -> Optional[int]:
    if not ctx.auth_token:
        print("ERROR: missing bearer token. Set --auth-token or PANTHEON_BEARER_TOKEN.", file=sys.stderr)
        return EXIT_AUTH
    return None


def _dry_run(ctx: CliContext, action: str, payload: Dict[str, Any]) -> int:
    if ctx.output == "json":
        print(json.dumps({"dry_run": True, "action": action, "payload": payload}, indent=2))
    else:
        print(f"DRY RUN: {action}")
        print(json.dumps(payload, indent=2))
    return EXIT_SUCCESS


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def cmd_deployment(args: argparse.Namespace, ctx: CliContext) -> int:
    missing = _require_auth(ctx)
    if missing is not None:
        return missing
    action = args.action
    payload: Dict[str, Any] = {
        "approval_decision": "approve" if action == "approve" else "reject",
        "reason": args.reason,
    }
    # approval only carries verification_timestamp
    if action == "approve":
        payload["verification_timestamp"] = getattr(args, "verification_timestamp", None)
    payload = {k: v for k, v in payload.items() if v}
    if ctx.dry_run or args.dry_run:
        return _dry_run(ctx, f"deployment.{action}", payload)
    status, body, exit_code = _call_api(
        ctx,
        "POST",
        f"/api/internal/v1/deployments/{args.plan_id}/approve",
        payload,
        mfa_token=args.mfa_token,
    )
    if exit_code is not None:
        return exit_code
    if status < 300:
        _emit(ctx, body, title=f"Deployment {args.plan_id} {body.get('state_after', action)}")
        return EXIT_SUCCESS
    _emit_error(ctx, status, body)
    return _exit_code_from_status(status, body)


def cmd_runtime(args: argparse.Namespace, ctx: CliContext) -> int:
    missing = _require_auth(ctx)
    if missing is not None:
        return missing
    if args.action == "force-halt":
        if not args.confirm:
            print("WARNING: force-halt called without --confirm", file=sys.stderr)
        if not args.mfa_token:
            print("ERROR: force-halt requires --mfa-token", file=sys.stderr)
            return EXIT_AUTH
        payload = {
            "action": "activate",
            "action_override": "terminate",
            "scope": "persona",
            "scope_id": args.binding_id,
            "severity": "critical",
            "reason": args.reason or "operator_emergency_stop",
        }
        if ctx.dry_run or args.dry_run:
            return _dry_run(ctx, "runtime.force-halt", payload)
        status, body, exit_code = _call_api(
            ctx,
            "POST",
            "/api/internal/v1/kill-switch",
            payload,
            mfa_token=args.mfa_token,
        )
        if exit_code is not None:
            return exit_code
        if status < 300:
            _emit(ctx, body, title=f"Runtime {args.binding_id} force-halt dispatched")
            return EXIT_SUCCESS
        _emit_error(ctx, status, body)
        return _exit_code_from_status(status, body)

    payload = {
        "pause_action": "pause" if args.action == "pause" else "resume",
        "duration_seconds": getattr(args, "duration", None),
        "reason": getattr(args, "reason", None),
    }
    payload = {k: v for k, v in payload.items() if v is not None and v != ""}
    if ctx.dry_run or args.dry_run:
        return _dry_run(ctx, f"runtime.{args.action}", payload)
    status, body, exit_code = _call_api(
        ctx,
        "POST",
        f"/api/internal/v1/runtimes/{args.binding_id}/pause",
        payload,
        mfa_token=args.mfa_token,
    )
    if exit_code is not None:
        return exit_code
    if status < 300:
        _emit(ctx, body, title=f"Runtime {args.binding_id} {args.action}")
        return EXIT_SUCCESS
    _emit_error(ctx, status, body)
    return _exit_code_from_status(status, body)


def cmd_rollback(args: argparse.Namespace, ctx: CliContext) -> int:
    missing = _require_auth(ctx)
    if missing is not None:
        return missing

    if args.action == "list":
        if ctx.dry_run or args.dry_run:
            return _dry_run(ctx, "rollback.list", {"target_id": args.target_id})
        status, body, exit_code = _call_api(
            ctx,
            "GET",
            "/api/internal/v1/rollbacks",
            params={"target_id": args.target_id},
        )
        if exit_code is not None:
            return exit_code
        if status < 300:
            _emit(ctx, body, title=f"Rollbacks for {args.target_id}")
            return EXIT_SUCCESS
        _emit_error(ctx, status, body)
        return _exit_code_from_status(status, body)

    if args.action == "abort":
        if not args.mfa_token:
            print("ERROR: rollback abort requires --mfa-token", file=sys.stderr)
            return EXIT_AUTH
        payload = {"reason": args.reason}
        payload = {k: v for k, v in payload.items() if v}
        if ctx.dry_run or args.dry_run:
            return _dry_run(ctx, "rollback.abort", payload)
        status, body, exit_code = _call_api(
            ctx,
            "POST",
            f"/api/internal/v1/rollbacks/{args.rollback_id}/abort",
            payload,
            mfa_token=args.mfa_token,
        )
        if exit_code is not None:
            return exit_code
        if status < 300:
            _emit(ctx, body, title=f"Rollback {args.rollback_id} aborted")
            return EXIT_SUCCESS
        _emit_error(ctx, status, body)
        return _exit_code_from_status(status, body)

    # execute
    if not args.mfa_token:
        print("ERROR: rollback execute requires --mfa-token", file=sys.stderr)
        return EXIT_AUTH
    payload = {
        "rollback_target_type": args.target_type,
        "target_id": args.target_id,
        "rollback_to_version": args.rollback_to_version,
        "rollback_action_type": args.action_type,
        "reason": args.reason,
        "verify_before_executing": args.verify_before_executing,
    }
    payload = {k: v for k, v in payload.items() if v not in (None, "")}
    if ctx.dry_run or args.dry_run:
        return _dry_run(ctx, "rollback.execute", payload)
    status, body, exit_code = _call_api(
        ctx,
        "POST",
        "/api/internal/v1/rollbacks/execute",
        payload,
        mfa_token=args.mfa_token,
    )
    if exit_code is not None:
        return exit_code
    if status < 300:
        _emit(ctx, body, title=f"Rollback executed for {args.target_id}")
        return EXIT_SUCCESS
    _emit_error(ctx, status, body)
    return _exit_code_from_status(status, body)


def cmd_kill_switch(args: argparse.Namespace, ctx: CliContext) -> int:
    missing = _require_auth(ctx)
    if missing is not None:
        return missing

    if args.action == "status":
        if ctx.dry_run or args.dry_run:
            return _dry_run(ctx, "kill-switch.status", {"scope": args.scope, "scope_id": args.scope_id})
        status, body, exit_code = _call_api(
            ctx,
            "GET",
            "/api/internal/v1/kill-switch",
            params={
                "scope": args.scope,
                "scope_id": args.scope_id or "",
            },
        )
        if exit_code is not None:
            return exit_code
        if status < 300:
            _emit(ctx, body, title="Kill-switch status")
            return EXIT_SUCCESS
        _emit_error(ctx, status, body)
        return _exit_code_from_status(status, body)

    if args.action == "deactivate":
        if not args.mfa_token:
            print("ERROR: kill-switch deactivate requires --mfa-token", file=sys.stderr)
            return EXIT_AUTH
        payload = {
            "action": "deactivate",
            "scope": args.scope,
            "scope_id": args.scope_id,
            "reason": args.rationale,
        }
        payload = {k: v for k, v in payload.items() if v}
        if ctx.dry_run or args.dry_run:
            return _dry_run(ctx, "kill-switch.deactivate", payload)
        status, body, exit_code = _call_api(
            ctx,
            "POST",
            "/api/internal/v1/kill-switch",
            payload,
            mfa_token=args.mfa_token,
        )
        if exit_code is not None:
            return exit_code
        if status < 300:
            _emit(ctx, body, title="Kill-switch deactivated")
            return EXIT_SUCCESS
        _emit_error(ctx, status, body)
        return _exit_code_from_status(status, body)

    # activate
    if not args.force:
        print("WARNING: kill-switch activate called without --force", file=sys.stderr)
    if not args.mfa_token:
        print("ERROR: kill-switch activate requires --mfa-token", file=sys.stderr)
        return EXIT_AUTH
    if args.action_override == "replace" and (
        not args.fallback_artifact_id or not args.fallback_artifact_version
    ):
        print(
            "ERROR: kill-switch replace requires --fallback-artifact-id "
            "and --fallback-artifact-version",
            file=sys.stderr,
        )
        return EXIT_USAGE
    payload = {
        "action": "activate",
        "scope": args.scope,
        "scope_id": args.scope_id,
        "severity": args.severity,
        "reason": args.rationale,
        "action_override": args.action_override,
        "fallback_artifact_id": args.fallback_artifact_id,
        "fallback_artifact_version": args.fallback_artifact_version,
    }
    payload = {k: v for k, v in payload.items() if v}
    if ctx.dry_run or args.dry_run:
        return _dry_run(ctx, "kill-switch.activate", payload)
    status, body, exit_code = _call_api(
        ctx,
        "POST",
        "/api/internal/v1/kill-switch",
        payload,
        mfa_token=args.mfa_token,
    )
    if exit_code is not None:
        return exit_code
    if status < 300:
        _emit(ctx, body, title="Kill-switch activated")
        return EXIT_SUCCESS
    _emit_error(ctx, status, body)
    return _exit_code_from_status(status, body)


def cmd_evolution(args: argparse.Namespace, ctx: CliContext) -> int:
    print(
        "ERROR: evolution control path is not yet wired to the internal API. "
        "Use the BFF command surfaces until the evolution controller API is exposed.",
        file=sys.stderr,
    )
    return EXIT_UNAVAILABLE


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    # Shared arguments available to all subcommands
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument("--config", default=None, help="Path to CLI config file")
    parent_parser.add_argument("--base-url", default=None, help="Internal API base URL")
    parent_parser.add_argument("--auth-token", default=None, help="Bearer token for internal API")
    parent_parser.add_argument("--output", choices=["text", "json"], default=None, help="Output format")
    parent_parser.add_argument("--timeout", type=int, default=None, help="HTTP timeout in seconds")
    parent_parser.add_argument("--dry-run", action="store_true", help="Print actions without executing")
    parent_parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parent_parser.add_argument("--log-level", choices=["debug", "info", "warn", "error"], default=None)

    parser = argparse.ArgumentParser(
        prog="pantheon-admin",
        description="Pantheon Admin CLI",
        parents=[parent_parser],
    )
    parser.add_argument("--version", action="version", version="Pantheon Admin CLI v1.0.0")

    subparsers = parser.add_subparsers(dest="cmd", required=True)

    # deployment
    p_dep = subparsers.add_parser("deployment")
    dep_sub = p_dep.add_subparsers(dest="action", required=True)
    p_approve = dep_sub.add_parser("approve", parents=[parent_parser])
    p_approve.add_argument("plan_id")
    p_approve.add_argument("--reason", default="")
    p_approve.add_argument("--verification-timestamp", default="")
    p_approve.add_argument("--mfa-token", default=None)

    p_reject = dep_sub.add_parser("reject", parents=[parent_parser])
    p_reject.add_argument("plan_id")
    p_reject.add_argument("--reason", default="")
    p_reject.add_argument("--mfa-token", default=None)

    # runtime
    p_run = subparsers.add_parser("runtime")
    run_sub = p_run.add_subparsers(dest="action", required=True)
    p_pause = run_sub.add_parser("pause", parents=[parent_parser])
    p_pause.add_argument("binding_id")
    p_pause.add_argument("--reason", default="")
    p_pause.add_argument("--duration", type=int, default=3600)
    p_pause.add_argument("--mfa-token", default=None)

    p_resume = run_sub.add_parser("resume", parents=[parent_parser])
    p_resume.add_argument("binding_id")
    p_resume.add_argument("--mfa-token", default=None)

    p_force = run_sub.add_parser("force-halt", parents=[parent_parser])
    p_force.add_argument("binding_id")
    p_force.add_argument("--reason", default="")
    p_force.add_argument("--confirm", action="store_true")
    p_force.add_argument("--mfa-token", default=None)

    # rollback
    p_rb = subparsers.add_parser("rollback")
    rb_sub = p_rb.add_subparsers(dest="action", required=True)
    p_rb_exec = rb_sub.add_parser("execute", parents=[parent_parser])
    p_rb_exec.add_argument("target_id")
    p_rb_exec.add_argument("--target-type", choices=["deployment", "runtime"], required=True)
    p_rb_exec.add_argument("--rollback-to-version", required=True)
    p_rb_exec.add_argument(
        "--action-type",
        choices=["replace", "pause_then_replace", "liquidate_then_replace"],
        default="replace",
    )
    p_rb_exec.add_argument("--reason", default="")
    p_rb_exec.add_argument("--verify-before-executing", action="store_true")
    p_rb_exec.add_argument("--mfa-token", default=None)

    p_rb_list = rb_sub.add_parser("list", parents=[parent_parser])
    p_rb_list.add_argument("target_id")

    p_rb_abort = rb_sub.add_parser("abort", parents=[parent_parser])
    p_rb_abort.add_argument("rollback_id")
    p_rb_abort.add_argument("--reason", default="")
    p_rb_abort.add_argument("--mfa-token", default=None)

    # kill-switch
    p_ks = subparsers.add_parser("kill-switch")
    ks_sub = p_ks.add_subparsers(dest="action", required=True)
    p_ks_act = ks_sub.add_parser("activate", parents=[parent_parser])
    p_ks_act.add_argument("--scope", choices=["all", "persona", "pool"], default="all")
    p_ks_act.add_argument("--scope-id", default=None)
    p_ks_act.add_argument("--severity", choices=["critical", "high", "medium"], default="critical")
    p_ks_act.add_argument("--rationale", default="")
    p_ks_act.add_argument("--action-override", choices=["pause", "risk_off", "liquidate", "replace", "terminate"], default=None)
    p_ks_act.add_argument("--fallback-artifact-id", default=None)
    p_ks_act.add_argument("--fallback-artifact-version", default=None)
    p_ks_act.add_argument("--force", action="store_true")
    p_ks_act.add_argument("--mfa-token", default=None)

    p_ks_status = ks_sub.add_parser("status", parents=[parent_parser])
    p_ks_status.add_argument("--scope", choices=["all", "persona", "pool"], default="all")
    p_ks_status.add_argument("--scope-id", default=None)

    p_ks_deact = ks_sub.add_parser("deactivate", parents=[parent_parser])
    p_ks_deact.add_argument("--scope", choices=["all", "persona", "pool"], required=True)
    p_ks_deact.add_argument("--scope-id", default=None)
    p_ks_deact.add_argument("--rationale", default="")
    p_ks_deact.add_argument("--mfa-token", default=None)

    # evolution
    p_ev = subparsers.add_parser("evolution")
    ev_sub = p_ev.add_subparsers(dest="action", required=True)
    p_ev_approve = ev_sub.add_parser("approve", parents=[parent_parser])
    p_ev_approve.add_argument("decision_id")
    p_ev_approve.add_argument("--mfa-token", default=None)

    p_ev_reject = ev_sub.add_parser("reject", parents=[parent_parser])
    p_ev_reject.add_argument("decision_id")
    p_ev_reject.add_argument("--mfa-token", default=None)

    p_ev_exec = ev_sub.add_parser("execute", parents=[parent_parser])
    p_ev_exec.add_argument("decision_id")
    p_ev_exec.add_argument("--action-type", choices=["freeze", "retrain", "mutate", "retire"], required=True)
    p_ev_exec.add_argument("--action-params", default=None)
    p_ev_exec.add_argument("--mfa-token", default=None)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.log_level:
        level = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warn": logging.WARNING,
            "error": logging.ERROR,
        }[args.log_level]
        logging.basicConfig(level=level)
    ctx = _resolve_context(args)

    if args.cmd == "deployment":
        return cmd_deployment(args, ctx)
    if args.cmd == "runtime":
        return cmd_runtime(args, ctx)
    if args.cmd == "rollback":
        return cmd_rollback(args, ctx)
    if args.cmd == "kill-switch":
        return cmd_kill_switch(args, ctx)
    if args.cmd == "evolution":
        return cmd_evolution(args, ctx)

    parser.print_help()
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
