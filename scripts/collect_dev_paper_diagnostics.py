#!/usr/bin/env python3
"""Read bounded Docker diagnostics on the dev host; emit only structural fields.

The trusted deployment controller sends this file over its existing pinned SSH
channel on stdin, before rollback replaces the failed containers. Raw log lines,
environment, request bodies and exception values never leave the host.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import selectors
import subprocess
import time

SERVICES = ("operator-bff", "persona", "capital", "registry", "governance", "deployment", "postgres")
MAX_BYTES = 256 * 1024
COMMAND_SECONDS = 5
SHA = re.compile(r"[0-9a-f]{40}")
IDENTIFIER = r"[A-Za-z_][A-Za-z_0-9.]{0,120}"
FRAME = re.compile(
    r'File "(?:/workspace/|/usr/local/lib/python[0-9.]+/site-packages/)'
    r'([A-Za-z_0-9./-]{1,240}\.py)", line ([0-9]{1,7}), in (' + IDENTIFIER + r'|<module>)$'
)
# Explicit project exception allowlist. Only names listed here or ending in
# Error/Exception (plus the fixed psycopg names below) are surfaced; an
# arbitrary "Something: <request text>" line is never treated as an event.
PROJECT_EXCEPTION_NAMES = ("PersonaWriteOwnerUnavailable", "ProvisioningLeaseLost")
EXCEPTION = re.compile(
    r"(?:^|\s)((?:[A-Za-z_][A-Za-z_0-9]*\.)*"
    r"(?:[A-Za-z_][A-Za-z_0-9]*(?:Error|Exception)|"
    + "|".join(re.escape(name) for name in PROJECT_EXCEPTION_NAMES) + r"|"
    r"UndefinedTable|UndefinedColumn|InsufficientPrivilege|UniqueViolation|"
    r"ForeignKeyViolation|NotNullViolation|SerializationFailure|DeadlockDetected)):\s*(.*)$"
)


def command(args: list[str]) -> tuple[str, str]:
    """Bound elapsed time and bytes even for a single extremely long log line."""
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = bytearray()
    status = "ok"
    deadline = time.monotonic() + COMMAND_SECONDS
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    status = "timeout"
                    break
                if not selector.select(remaining):
                    status = "timeout"
                    break
                chunk = os.read(process.stdout.fileno(), min(8192, MAX_BYTES + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > MAX_BYTES:
                    status = "truncated"
                    break
        if status == "ok":
            try:
                if process.wait(timeout=max(0.01, deadline - time.monotonic())):
                    status = "command_failed"
            except subprocess.TimeoutExpired:
                status = "timeout"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
    return bytes(output[:MAX_BYTES]).decode("utf-8", errors="replace"), status


def log_events(raw: str) -> list[dict]:
    """Allowlist traceback structure; no raw line or free-form message output."""
    events = []
    for line in raw.splitlines():
        timestamp = re.match(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)\s+", line)
        text = line[timestamp.end():] if timestamp else line
        text = text.strip()
        event = None
        frame = FRAME.search(text)
        error = EXCEPTION.search(text)
        if frame:
            event = {"kind": "frame", "file": frame[1], "line": int(frame[2]), "function": frame[3]}
        elif error:
            event = {"kind": "exception", "type": error[1]}
            # These patterns preserve program identifiers, never argument values.
            patterns = (
                (r"name '(" + IDENTIFIER + r")' is not defined$", "undefined_name"),
                (r"'" + IDENTIFIER + r"' object has no attribute '(" + IDENTIFIER + r")'$", "missing_attribute"),
                (r".* got an unexpected keyword argument '(" + IDENTIFIER + r")'$", "unexpected_keyword"),
                (r".* missing \d+ required .* argument[s]?: '(" + IDENTIFIER + r")'$", "missing_argument"),
                (r'relation "(' + IDENTIFIER + r')" does not exist$', "missing_relation"),
                (r'column "(' + IDENTIFIER + r')" does not exist$', "missing_column"),
            )
            for pattern, category in patterns:
                match = re.fullmatch(pattern, error[2])
                if match:
                    event.update(category=category, identifier=match[1])
                    break
            http = re.match(r"HTTP Error ([45][0-9]{2})(?::|$)", error[2])
            if http:
                event["http_status"] = int(http[1])
        if event:
            if timestamp:
                event["timestamp"] = timestamp[1]
            events.append(event)
    return events[-240:]


def container_state(container_id: str) -> dict:
    # Never inspect .Config.Env or .State.Error (both may contain credentials).
    template = ('{"status":{{json .State.Status}},"health":'
                '{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}},'
                '"exit_code":{{.State.ExitCode}},"oom_killed":{{.State.OOMKilled}},'
                '"restart_count":{{.RestartCount}},"image_id":{{json .Image}},'
                '"source_sha":{{json (index .Config.Labels "org.opencontainers.image.revision")}}}')
    raw, status = command(["docker", "inspect", "--format", template, container_id])
    if status != "ok":
        return {"collection_status": status}
    value = json.loads(raw)
    result = {"collection_status": "ok"}
    for key, choices in (("status", {"created", "running", "paused", "restarting", "removing", "exited", "dead"}),
                         ("health", {"healthy", "unhealthy", "starting"})):
        result[key] = value.get(key) if value.get(key) in choices else None
    for key in ("exit_code", "restart_count"):
        result[key] = value.get(key) if type(value.get(key)) is int else None
    result["oom_killed"] = value.get("oom_killed") if type(value.get("oom_killed")) is bool else None
    result["image_id"] = value.get("image_id") if re.fullmatch(r"sha256:[0-9a-f]{64}", str(value.get("image_id"))) else None
    result["source_sha"] = value.get("source_sha") if SHA.fullmatch(str(value.get("source_sha"))) else None
    return result


def collect(expected_sha: str, *, run_id: str | None = None, attempt: str | None = None,
            phase: str | None = None, expected_fe_sha: str | None = None,
            bootstrap_exit: str | None = None) -> dict:
    if not SHA.fullmatch(expected_sha):
        raise ValueError("expected BFF SHA must be a full commit")
    result = {"schema_version": "pantheon.dev-paper-diagnostics.v1", "environment": "dev",
              "run_id": run_id, "attempt": attempt, "phase": phase,
              "expected_fe_sha": expected_fe_sha, "expected_bff_sha": expected_sha,
              "bootstrap_exit": bootstrap_exit,
              "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "read_only": True, "raw_logs_included": False, "services": {}}
    statuses = set()
    for service in SERVICES:
        row = {}
        result["services"][service] = row
        try:
            raw, status = command(["docker", "ps", "--all", "--no-trunc", "--quiet",
                                   "--filter", "label=com.docker.compose.project=pantheon",
                                   "--filter", f"label=com.docker.compose.service={service}"])
            ids = raw.split()
            if status != "ok" or len(ids) != 1 or not re.fullmatch(r"[0-9a-f]{64}", ids[0]):
                row["collection_status"] = status if status != "ok" else "container_missing_or_ambiguous"
                statuses.add(row["collection_status"])
                continue
            state = container_state(ids[0])
            row.update(container_id=ids[0], state=state)
            # The inspect call's own collection_status must feed the aggregate
            # status too; a docker-inspect timeout/error previously vanished
            # once docker-ps and docker-logs both happened to return "ok".
            statuses.add(state.get("collection_status", "collector_error"))
            if service == "operator-bff":
                result["identity_matches"] = state.get("source_sha") == expected_sha
                result["container_id"] = ids[0]
                result["image_id"] = state.get("image_id")
                result["observed_source_sha"] = state.get("source_sha")
            raw, status = command(["docker", "logs", "--timestamps", "--since=15m", "--tail=240", ids[0]])
            row.update(collection_status=status, events=log_events(raw))
            statuses.add(status)
        except Exception:
            # Even local Docker/JSON exceptions can embed raw output. Fail closed.
            row["collection_status"] = "collector_error"
            statuses.add("collector_error")
    result.setdefault("identity_matches", False)
    result.setdefault("container_id", None)
    result.setdefault("image_id", None)
    result.setdefault("observed_source_sha", None)
    # A clean set of per-command "ok" statuses is not itself sufficient: the
    # candidate operator-bff container can be fully inspectable yet running
    # the wrong source SHA. That must never be folded into "ok".
    if statuses - {"ok"}:
        result["collection_status"] = "partial"
    elif not result["identity_matches"]:
        result["collection_status"] = "identity_mismatch"
    else:
        result["collection_status"] = "ok"
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-bff-sha", required=True)
    parser.add_argument("--expected-fe-sha", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--attempt", default=None)
    parser.add_argument("--phase", default=None)
    parser.add_argument("--bootstrap-exit", default=None)
    args = parser.parse_args()
    print(json.dumps(collect(
        args.expected_bff_sha,
        run_id=args.run_id,
        attempt=args.attempt,
        phase=args.phase,
        expected_fe_sha=args.expected_fe_sha,
        bootstrap_exit=args.bootstrap_exit,
    ), indent=2, sort_keys=True))
