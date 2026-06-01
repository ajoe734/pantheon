"""Assistant command broker policy for OpenClaw kernel modes.

This module is intentionally a policy/audit layer, not a shell executor.  The
OpenClaw gateway may ask whether a diagnostic command is admissible for an
assistant session, and this policy returns a deny-by-default decision that can
be audited before any future runner exists.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.parse import urlparse


ASSISTANT_COMMAND_TOOL_NAME = "assistant.command"

USER_MODE = "user"
KERNEL_OBSERVE_MODE = "kernel_observe"
KERNEL_DEBUG_MODE = "kernel_debug"
KERNEL_REPAIR_MODE = "kernel_repair"

HEALTH_PROBE = "health_probe"
REPO_STATUS = "repo_status"
CODE_SEARCH = "code_search"
FILE_SLICE = "file_slice"
TEST_RUN = "test_run"
LOG_READ = "log_read"

_MODE_COMMAND_CLASSES: Mapping[str, frozenset[str]] = {
    USER_MODE: frozenset(),
    KERNEL_OBSERVE_MODE: frozenset({HEALTH_PROBE, REPO_STATUS}),
    KERNEL_DEBUG_MODE: frozenset({HEALTH_PROBE, REPO_STATUS, CODE_SEARCH, FILE_SLICE, TEST_RUN, LOG_READ}),
    # Repair-specific write/restart classes are owned by ASST-KERNEL-007.  This
    # observe/debug policy only permits the diagnostic subset in repair mode.
    KERNEL_REPAIR_MODE: frozenset({HEALTH_PROBE, REPO_STATUS, CODE_SEARCH, FILE_SLICE, TEST_RUN, LOG_READ}),
}

_COMMAND_CLASS_ALIASES = {
    "health": HEALTH_PROBE,
    "service_health": HEALTH_PROBE,
    "git_status": REPO_STATUS,
    "repo_status": REPO_STATUS,
    "rg": CODE_SEARCH,
    "code_search": CODE_SEARCH,
    "read_file_slice": FILE_SLICE,
    "file_read": FILE_SLICE,
    "file_slice": FILE_SLICE,
    "targeted_tests": TEST_RUN,
    "test": TEST_RUN,
    "test_run": TEST_RUN,
    "sanitized_logs": LOG_READ,
    "log_read": LOG_READ,
}

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
_ALLOWED_PROBE_PREFIXES = (
    "/healthz",
    "/readyz",
    "/livez",
    "/bff/",
    "/api/openclaw-adapter/health",
    "/api/openclaw-adapter/ready",
)
_SAFE_CURL_FLAGS = {
    "-s",
    "-S",
    "-f",
    "-I",
    "-sS",
    "-fsS",
    "--silent",
    "--show-error",
    "--fail",
    "--head",
}
_CURL_VALUE_FLAGS = {
    "--max-time",
    "--connect-timeout",
    "--retry",
    "--retry-delay",
    "-X",
    "--request",
}
_CURL_DENIED_FLAGS = {
    "-d",
    "--data",
    "--data-raw",
    "--data-binary",
    "--form",
    "-F",
    "-T",
    "--upload-file",
    "-H",
    "--header",
    "-u",
    "--user",
    "--cookie",
    "--cookie-jar",
}
_RG_VALUE_FLAGS = {
    "--max-count",
    "-m",
    "--max-filesize",
    "--glob",
    "-g",
    "--context",
    "-C",
    "--before-context",
    "-B",
    "--after-context",
    "-A",
    "-e",
}
_RG_DENIED_FLAGS = {
    "--hidden",
    "--no-ignore",
    "--follow",
    "-u",
    "-uu",
    "-uuu",
}
_PYTEST_VALUE_FLAGS = {
    "-k",
    "-m",
    "--maxfail",
    "--tb",
}
_PYTEST_SAFE_FLAGS = {
    "-q",
    "-x",
    "-s",
    "-vv",
    "-v",
    "--quiet",
    "--disable-warnings",
    "--strict-markers",
}
_SHELL_HEADS = {"bash", "sh", "zsh", "fish", "dash", "powershell", "pwsh"}
_DB_HEADS = {"psql", "mysql", "mariadb", "mongo", "mongosh", "redis-cli", "clickhouse-client", "sqlite3"}
_NETWORK_EXFIL_HEADS = {"wget", "nc", "netcat", "ssh", "scp", "sftp", "rsync"}
_SECRET_DUMP_HEADS = {"env", "printenv", "set"}
_DIRECT_BROKER_HEADS = {
    "broker",
    "broker_order",
    "openclaw-broker",
    "shioaji",
    "live_order",
    "capital_bind",
    "capital_release",
}
_DESTRUCTIVE_HEADS = {"rm", "rmdir", "chmod", "chown", "kill", "pkill", "docker", "kubectl", "terraform"}
_DESTRUCTIVE_GIT_SUBCOMMANDS = {
    "reset",
    "checkout",
    "clean",
    "push",
    "commit",
    "rebase",
    "merge",
    "switch",
    "restore",
    "branch",
    "tag",
}
_SENSITIVE_SEGMENTS = {
    ".aws",
    ".azure",
    ".claude",
    ".codex",
    ".config",
    ".gnupg",
    ".kube",
    ".netrc",
    ".ssh",
    "credential-store",
    "credentials",
    "keyring",
    "private_keys",
    "secrets",
    "tokens",
}
_SENSITIVE_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "auth.json",
    "authorized_keys",
    "config.json",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "known_hosts",
}
_SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".kubeconfig")
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)(authorization|bearer|cookie|token|secret|password|passwd|api[_-]?key|private[_-]?key|database[_-]?url)="
)
_SED_RANGE_RE = re.compile(r"^(\d+),(\d+)p$")


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def command_argv_hash(argv: Sequence[str]) -> str:
    blob = json.dumps(list(argv), sort_keys=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def command_classes_for_mode(mode: str) -> list[str]:
    return sorted(_MODE_COMMAND_CLASSES.get(_mode_value(mode), frozenset()))


@dataclasses.dataclass(frozen=True)
class CommandPolicyDecision:
    allowed: bool
    reason: str
    policy_class: str
    mode: str
    command_class: Optional[str]
    argv: tuple[str, ...]
    cwd: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "policy_class": self.policy_class,
            "mode": self.mode,
            "command_class": self.command_class,
            "argv_hash": command_argv_hash(self.argv),
            "argv_head": self.argv[0] if self.argv else None,
            "cwd": self.cwd,
        }


class AssistantCommandDenied(RuntimeError):
    """Raised by AssistantCommandBroker when a command request is denied."""

    def __init__(self, decision: CommandPolicyDecision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


class AssistantCommandAuditLog:
    """Append-only JSONL audit log for assistant command allow/deny decisions."""

    def __init__(
        self,
        path: Optional[str | os.PathLike[str]] = None,
        *,
        clock: Callable[[], str] = _utc_now_iso,
    ) -> None:
        if path is None:
            raw = os.getenv("PANTHEON_ASSISTANT_COMMAND_AUDIT_PATH", "")
            path = raw if raw else "/tmp/openclaw-gateway-adapter/assistant_command_audit.jsonl"
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.Lock()

    def record(self, entry: Mapping[str, Any]) -> None:
        payload = dict(entry)
        payload.setdefault("at", self._clock())
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line)

    def read(
        self,
        *,
        session_id: Optional[str] = None,
        operator_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        results: list[dict[str, Any]] = []
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                for raw_line in fh:
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        entry = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    if session_id is not None and entry.get("session_id") != session_id:
                        continue
                    if operator_id is not None and entry.get("operator_id") != operator_id:
                        continue
                    results.append(entry)
        except OSError:
            return []
        return results[-limit:]


class AssistantCommandPolicy:
    """Deny-by-default command policy for assistant kernel observe/debug modes."""

    def __init__(
        self,
        *,
        max_rg_count: int = 200,
        max_file_slice_lines: int = 240,
        max_log_lines: int = 200,
    ) -> None:
        self.max_rg_count = max_rg_count
        self.max_file_slice_lines = max_file_slice_lines
        self.max_log_lines = max_log_lines

    def evaluate(
        self,
        *,
        mode: str,
        argv: Sequence[Any],
        command_class: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> CommandPolicyDecision:
        normalized_mode = _mode_value(mode)
        normalized_argv = tuple(str(arg) for arg in argv if str(arg) != "")
        normalized_class = _normalize_command_class(command_class) if command_class else _infer_command_class(normalized_argv)

        def deny(reason: str, policy_class: str = "denylist") -> CommandPolicyDecision:
            return CommandPolicyDecision(
                allowed=False,
                reason=reason,
                policy_class=policy_class,
                mode=normalized_mode,
                command_class=normalized_class,
                argv=normalized_argv,
                cwd=cwd,
            )

        if normalized_mode == USER_MODE:
            return deny("User mode does not permit command broker requests.", "mode_denied")
        if normalized_mode not in _MODE_COMMAND_CLASSES:
            return deny(f"Unknown assistant mode {normalized_mode!r}; command denied.", "mode_denied")
        if not normalized_argv:
            return deny("Command argv is empty.", "malformed")
        if cwd and _looks_like_sensitive_path(cwd):
            return deny(f"Command cwd {cwd!r} is sensitive or outside the command policy boundary.")

        deny_reason = _denylist_reason(normalized_argv)
        if deny_reason:
            return deny(deny_reason, "denylist")

        allowed_classes = _MODE_COMMAND_CLASSES[normalized_mode]
        if normalized_class is None or normalized_class not in allowed_classes:
            return deny(
                f"Command class {normalized_class or 'unknown'!r} is not allowed in mode {normalized_mode!r}.",
                "not_in_allowlist",
            )

        validator = {
            HEALTH_PROBE: self._validate_health_probe,
            REPO_STATUS: self._validate_repo_status,
            CODE_SEARCH: self._validate_code_search,
            FILE_SLICE: self._validate_file_slice,
            TEST_RUN: self._validate_test_run,
            LOG_READ: self._validate_log_read,
        }[normalized_class]
        valid, reason = validator(normalized_argv)
        if not valid:
            return deny(reason, "not_in_allowlist")

        return CommandPolicyDecision(
            allowed=True,
            reason=f"Command class {normalized_class!r} is allowed in mode {normalized_mode!r}.",
            policy_class="allowlist",
            mode=normalized_mode,
            command_class=normalized_class,
            argv=normalized_argv,
            cwd=cwd,
        )

    def _validate_health_probe(self, argv: tuple[str, ...]) -> tuple[bool, str]:
        if argv[0] != "curl":
            return False, "Health probes must use curl."

        urls: list[str] = []
        i = 1
        while i < len(argv):
            token = argv[i]
            if token in _CURL_DENIED_FLAGS:
                return False, f"curl flag {token!r} is not allowed for health/read probes."
            if token in _CURL_VALUE_FLAGS:
                if i + 1 >= len(argv):
                    return False, f"curl flag {token!r} requires a value."
                value = argv[i + 1]
                if token in {"-X", "--request"} and value.upper() not in {"GET", "HEAD"}:
                    return False, "Only GET/HEAD curl probes are allowed."
                if token in {"--max-time", "--connect-timeout"} and not _bounded_number(value, maximum=15):
                    return False, f"curl timeout {value!r} exceeds the allowed probe bound."
                if token in {"--retry", "--retry-delay"} and not _bounded_number(value, maximum=3):
                    return False, f"curl retry value {value!r} exceeds the allowed probe bound."
                i += 2
                continue
            if token.startswith("-") and token not in _SAFE_CURL_FLAGS:
                return False, f"curl flag {token!r} is not allowlisted."
            if _looks_like_url_or_route(token):
                urls.append(token)
            i += 1

        if not urls:
            return False, "Health/read probe must include a local URL or route."
        for url in urls:
            if not _is_allowed_probe_url(url):
                return False, f"Probe URL {url!r} is not an allowlisted local health/read route."
        return True, "Allowed local health/read probe."

    def _validate_repo_status(self, argv: tuple[str, ...]) -> tuple[bool, str]:
        allowed = {
            ("git", "status", "-sb"),
            ("git", "status", "--short", "--branch"),
        }
        if argv not in allowed:
            return False, "Only 'git status -sb' is allowed for repo status."
        return True, "Allowed git status probe."

    def _validate_code_search(self, argv: tuple[str, ...]) -> tuple[bool, str]:
        if argv[0] != "rg":
            return False, "Code search must use rg."

        max_count: Optional[int] = None
        positionals: list[str] = []
        i = 1
        while i < len(argv):
            token = argv[i]
            if token in _RG_DENIED_FLAGS or (token.startswith("-") and set(token[1:]) == {"u"}):
                return False, f"rg flag {token!r} can expose ignored or hidden files."
            if token.startswith("--max-count="):
                max_count = _parse_int(token.split("=", 1)[1])
                i += 1
                continue
            if token in _RG_VALUE_FLAGS:
                if i + 1 >= len(argv):
                    return False, f"rg flag {token!r} requires a value."
                value = argv[i + 1]
                if token in {"--max-count", "-m"}:
                    max_count = _parse_int(value)
                if token in {"--context", "-C", "--before-context", "-B", "--after-context", "-A"} and not _bounded_number(value, maximum=20):
                    return False, f"rg context bound {value!r} is too large."
                if _looks_like_sensitive_path(value):
                    return False, f"rg value {value!r} targets a sensitive path."
                i += 2
                continue
            if token.startswith("-") and token not in {"-n", "--line-number", "-S", "--smart-case", "-i", "--ignore-case", "-F", "--fixed-strings", "--json", "--no-heading"}:
                return False, f"rg flag {token!r} is not allowlisted."
            else:
                positionals.append(token)
            i += 1

        if max_count is None or max_count < 1 or max_count > self.max_rg_count:
            return False, f"rg searches must include --max-count/-m between 1 and {self.max_rg_count}."
        if not positionals:
            return False, "rg searches must include a pattern and at least one bounded path."
        paths = positionals[1:]
        if not paths:
            return False, "rg searches must include at least one explicit bounded path."
        for path in paths:
            if _unsafe_repo_path(path):
                return False, f"rg path {path!r} is not allowed."
        return True, "Allowed bounded rg search."

    def _validate_file_slice(self, argv: tuple[str, ...]) -> tuple[bool, str]:
        if len(argv) != 4 or argv[0] != "sed" or argv[1] != "-n":
            return False, "File slices must use 'sed -n <start>,<end>p <path>'."
        match = _SED_RANGE_RE.match(argv[2])
        if not match:
            return False, "sed file slices must use a numeric '<start>,<end>p' range."
        start, end = int(match.group(1)), int(match.group(2))
        if start < 1 or end < start or (end - start + 1) > self.max_file_slice_lines:
            return False, f"File slice must be 1..{self.max_file_slice_lines} lines."
        if _unsafe_repo_path(argv[3]):
            return False, f"File slice path {argv[3]!r} is not allowed."
        return True, "Allowed bounded file slice."

    def _validate_test_run(self, argv: tuple[str, ...]) -> tuple[bool, str]:
        if argv[0] == "pytest":
            args = argv[1:]
        elif len(argv) >= 3 and argv[0] in {"python", "python3"} and argv[1:3] == ("-m", "pytest"):
            args = argv[3:]
        else:
            return False, "Targeted tests must use pytest or python -m pytest."

        paths: list[str] = []
        i = 0
        while i < len(args):
            token = args[i]
            if token in _PYTEST_VALUE_FLAGS:
                if i + 1 >= len(args):
                    return False, f"pytest flag {token!r} requires a value."
                if token == "--maxfail" and not _bounded_number(args[i + 1], maximum=5):
                    return False, "--maxfail must be between 1 and 5."
                i += 2
                continue
            if token.startswith("--maxfail="):
                if not _bounded_number(token.split("=", 1)[1], maximum=5):
                    return False, "--maxfail must be between 1 and 5."
                i += 1
                continue
            if token.startswith("-"):
                if token not in _PYTEST_SAFE_FLAGS:
                    return False, f"pytest flag {token!r} is not allowlisted."
                i += 1
                continue
            paths.append(token)
            i += 1

        if not paths:
            return False, "Targeted tests must include at least one explicit test path."
        for path in paths:
            if _unsafe_repo_path(path):
                return False, f"pytest path {path!r} is not allowed."
            lowered = path.lower()
            if "test" not in lowered and not lowered.endswith(".py"):
                return False, f"pytest target {path!r} is not a bounded test target."
        return True, "Allowed targeted pytest run."

    def _validate_log_read(self, argv: tuple[str, ...]) -> tuple[bool, str]:
        if len(argv) != 4 or argv[0] != "tail" or argv[1] != "-n":
            return False, "Log reads must use 'tail -n <lines> <sanitized-log-path>'."
        if not _bounded_number(argv[2], maximum=self.max_log_lines):
            return False, f"Log reads must request 1..{self.max_log_lines} lines."
        path = argv[3]
        if _looks_like_sensitive_path(path):
            return False, f"Log path {path!r} is sensitive."
        lowered = path.lower()
        if not (("sanitized" in lowered or "redacted" in lowered) and (lowered.endswith(".log") or lowered.endswith(".jsonl"))):
            return False, "Log reads must target sanitized/redacted .log or .jsonl files."
        return True, "Allowed bounded sanitized log read."

    def to_dict(self) -> dict[str, Any]:
        return {
            "assistant_command_tool": ASSISTANT_COMMAND_TOOL_NAME,
            "mode_command_classes": {
                mode: sorted(classes) for mode, classes in sorted(_MODE_COMMAND_CLASSES.items())
            },
            "default_posture": "deny_all",
            "max_rg_count": self.max_rg_count,
            "max_file_slice_lines": self.max_file_slice_lines,
            "max_log_lines": self.max_log_lines,
        }


class AssistantCommandBroker:
    """Policy/audit broker for assistant command requests.

    The broker records allow and deny decisions.  It does not execute commands.
    """

    def __init__(
        self,
        *,
        policy: Optional[AssistantCommandPolicy] = None,
        audit_log: Optional[AssistantCommandAuditLog] = None,
        command_id_factory: Callable[[], str] = lambda: f"asst_cmd_{uuid.uuid4().hex}",
    ) -> None:
        self._policy = policy or AssistantCommandPolicy()
        self._audit = audit_log or AssistantCommandAuditLog()
        self._command_id_factory = command_id_factory

    def request_command(
        self,
        *,
        session_id: str,
        operator_id: str,
        mode: str,
        argv: Sequence[Any],
        command_class: Optional[str] = None,
        cwd: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> dict[str, Any]:
        command_id = self._command_id_factory()
        decision = self._policy.evaluate(mode=mode, argv=argv, command_class=command_class, cwd=cwd)
        entry = _audit_entry(
            command_id=command_id,
            session_id=session_id,
            operator_id=operator_id,
            trace_id=trace_id,
            decision=decision,
        )
        self._audit.record(entry)
        if not decision.allowed:
            raise AssistantCommandDenied(decision)
        return {
            "status": "allowed",
            "command_id": command_id,
            "session_id": session_id,
            "operator_id": operator_id,
            "trace_id": trace_id,
            "mode": decision.mode,
            "command_class": decision.command_class,
            "argv_hash": command_argv_hash(decision.argv),
            "policy_class": decision.policy_class,
            "reason": decision.reason,
        }


def build_command_audit_entry(
    *,
    command_id: str,
    session_id: str,
    operator_id: str,
    trace_id: Optional[str],
    decision: CommandPolicyDecision,
    extra: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    entry = _audit_entry(
        command_id=command_id,
        session_id=session_id,
        operator_id=operator_id,
        trace_id=trace_id,
        decision=decision,
    )
    if extra:
        entry.update(dict(extra))
    return entry


def _audit_entry(
    *,
    command_id: str,
    session_id: str,
    operator_id: str,
    trace_id: Optional[str],
    decision: CommandPolicyDecision,
) -> dict[str, Any]:
    return {
        "event_type": "assistant.command.requested" if decision.allowed else "assistant.command.denied",
        "request_type": "assistant_command",
        "outcome": "allowed" if decision.allowed else "denied",
        "command_id": command_id,
        "session_id": session_id,
        "operator_id": operator_id,
        "trace_id": trace_id,
        "mode": decision.mode,
        "command_class": decision.command_class,
        "argv_hash": command_argv_hash(decision.argv),
        "argv_head": decision.argv[0] if decision.argv else None,
        "cwd": decision.cwd,
        "policy_decision": "allowed" if decision.allowed else "denied",
        "policy_class": decision.policy_class,
        "policy_reason": decision.reason,
    }


def _mode_value(mode: Any) -> str:
    return str(getattr(mode, "value", mode) or "").strip()


def _normalize_command_class(command_class: Optional[str]) -> Optional[str]:
    if not command_class:
        return None
    return _COMMAND_CLASS_ALIASES.get(str(command_class).strip(), str(command_class).strip())


def _infer_command_class(argv: Sequence[str]) -> Optional[str]:
    if not argv:
        return None
    head = argv[0]
    if head == "curl":
        return HEALTH_PROBE
    if head == "git" and len(argv) >= 2 and argv[1] == "status":
        return REPO_STATUS
    if head == "rg":
        return CODE_SEARCH
    if head == "sed":
        return FILE_SLICE
    if head == "tail":
        return LOG_READ
    if head == "pytest" or (len(argv) >= 3 and head in {"python", "python3"} and argv[1:3] == ("-m", "pytest")):
        return TEST_RUN
    return None


def _denylist_reason(argv: tuple[str, ...]) -> Optional[str]:
    head = argv[0].lower()
    joined = " ".join(argv).lower()

    if head in {"sudo", "su", "doas"}:
        return "sudo/su/doas commands are always denied."
    if head in _SHELL_HEADS:
        return "Direct shell execution is always denied."
    if head in _SECRET_DUMP_HEADS:
        return "Environment/secret dumping commands are always denied."
    if head in _DB_HEADS:
        return "Direct database client commands are always denied."
    if head in _NETWORK_EXFIL_HEADS:
        return "Network exfiltration commands are always denied."
    if head in _DIRECT_BROKER_HEADS:
        return "Direct broker/live/capital commands are always denied."
    if head in _DESTRUCTIVE_HEADS:
        return f"Command {head!r} is destructive or outside diagnostic scope."
    if head == "git" and len(argv) > 1 and argv[1].lower() in _DESTRUCTIVE_GIT_SUBCOMMANDS:
        return f"git {argv[1]} is always denied by assistant command policy."
    if head == "curl":
        urls = [arg for arg in argv[1:] if _looks_like_url_or_route(arg)]
        if any(not _is_allowed_probe_url(url) for url in urls):
            return "curl is limited to allowlisted local health/read probes."
    if "rm -rf" in joined or "--force" in argv or "-rf" in argv:
        return "Destructive force/remove command pattern is always denied."
    for arg in argv:
        if _SENSITIVE_ASSIGNMENT_RE.search(arg):
            return "Command argv contains secret-like inline data."
        if _looks_like_sensitive_path(arg):
            return f"Command references sensitive path {arg!r}."
    return None


def _looks_like_url_or_route(value: str) -> bool:
    return value.startswith(("http://", "https://", "/"))


def _is_allowed_probe_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme:
        if parsed.scheme not in {"http", "https"}:
            return False
        if parsed.hostname not in _LOCAL_HOSTS:
            return False
        path = parsed.path or "/"
        query = parsed.query or ""
    else:
        path = value
        query = ""
    if _SENSITIVE_ASSIGNMENT_RE.search(query):
        return False
    return any(path == prefix or path.startswith(prefix) for prefix in _ALLOWED_PROBE_PREFIXES)


def _bounded_number(value: str, *, maximum: int) -> bool:
    parsed = _parse_int(value)
    return parsed is not None and 1 <= parsed <= maximum


def _parse_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _unsafe_repo_path(path: str) -> bool:
    if not path or path in {".", ".."}:
        return True
    if path.startswith(("/", "~")):
        return True
    if ".." in Path(path).parts:
        return True
    return _looks_like_sensitive_path(path)


def _looks_like_sensitive_path(value: str) -> bool:
    if not value:
        return False
    lowered = value.strip().lower()
    if _SENSITIVE_ASSIGNMENT_RE.search(lowered):
        return True
    parts = [part for part in re.split(r"[\\/]+", lowered) if part]
    if not parts:
        return False
    for part in parts:
        if part in _SENSITIVE_SEGMENTS or part in _SENSITIVE_FILENAMES:
            return True
        if part.startswith(".env"):
            return True
    filename = parts[-1]
    return filename.endswith(_SENSITIVE_SUFFIXES)
