"""Contract: long-running service processes must be PID 1 (exec) so SIGTERM reaches them.

`sh -c "uvicorn ..."` leaves sh as PID 1, which does not forward SIGTERM, so
`docker stop` waits out the grace period and ends with SIGKILL (137).
"""
from __future__ import annotations

import fnmatch
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Mapping

import yaml

_DEFAULT_ROOT = Path(__file__).resolve().parents[1]
ROOT = _DEFAULT_ROOT

# One-shot init containers that exit on their own; a shell wrapper is fine.
ONE_SHOT_ALLOWLIST = {
    ("docker-compose.yml", "openclaw-data-init"): "one-shot chown/mkdir init, exits immediately",
}

SHELL_RE = re.compile(r"^(?:(?:/usr)?(?:/local)?/bin/)?(?:ba|da|a|z)?sh$")
SERVER_BINS = {"uvicorn", "gunicorn", "hypercorn", "daphne", "waitress-serve"}
SERVER_MODULES = {"uvicorn", "gunicorn", "hypercorn", "daphne", "waitress"}
LEADING_KEYWORDS = {"if", "then", "else", "elif", "while", "until", "do", "{", "!"}
REDIRECT_OPS = {">", ">>", "<", "<<", "<>", ">&", "<&", ">|", "&>", "&>>"}


def _shlex_split(text: str) -> list[str]:
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return text.split()


def _matches_any(rel_path: Path, patterns: list[str]) -> bool:
    rel_posix = rel_path.as_posix()
    name = rel_path.name
    return any(
        fnmatch.fnmatchcase(rel_posix, pat) or fnmatch.fnmatchcase(name, pat)
        for pat in patterns
    )


def _tracked(patterns: list[str], root: Path | None = None) -> list[Path]:
    base = ROOT if root is None else Path(root)
    if base == _DEFAULT_ROOT or (base / ".git").exists():
        proc = subprocess.run(
            ["git", "ls-files", *patterns],
            cwd=base,
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return [base / p for p in proc.stdout.split()]
    return sorted(
        p
        for p in base.rglob("*")
        if p.is_file() and _matches_any(p.relative_to(base), patterns)
    )


def _extract_shell_c_script(argv: list[str]) -> str | None:
    tokens = list(argv)
    if tokens and Path(tokens[0]).name == "env":
        tokens.pop(0)
        while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
            tokens.pop(0)
    if len(tokens) < 3 or not SHELL_RE.match(tokens[0]):
        return None
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-o" and i + 1 < len(tokens):
            i += 2
            continue
        if tok.startswith("--"):
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:
            if "c" in tok[1:] and i + 1 < len(tokens):
                return tokens[i + 1].strip()
            i += 1
            continue
        break
    return None


def _split_shell_segments(script: str) -> list[tuple[str | None, str]]:
    s = script.strip()
    if not s:
        return []
    segments: list[tuple[str | None, str]] = []
    op: str | None = None
    buf: list[str] = []
    quote: str | None = None
    paren_depth = 0
    param_depth = 0
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if quote == "'":
            buf.append(ch)
            if ch == "'":
                quote = None
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            buf.append(s[i : i + 2])
            i += 2
            continue
        if quote == '"':
            buf.append(ch)
            if ch == '"':
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if s[i : i + 2] == "${":
            param_depth += 1
            buf.append("${")
            i += 2
            continue
        if ch == "}" and param_depth > 0:
            param_depth -= 1
            buf.append(ch)
            i += 1
            continue
        if ch == "(":
            paren_depth += 1
            buf.append(ch)
            i += 1
            continue
        if ch == ")" and paren_depth > 0:
            paren_depth -= 1
            buf.append(ch)
            i += 1
            continue
        if paren_depth == 0 and param_depth == 0:
            two = s[i : i + 2]
            if two in ("&&", "||"):
                segments.append((op, "".join(buf).strip()))
                buf = []
                op = two
                i += 2
                continue
            if two == "|&":
                segments.append((op, "".join(buf).strip()))
                buf = []
                op = "|"
                i += 2
                continue
            if two == ";;":
                segments.append((op, "".join(buf).strip()))
                buf = []
                op = ";"
                i += 2
                continue
            if ch in (";", "\n", "|"):
                segments.append((op, "".join(buf).strip()))
                buf = []
                op = ch
                i += 1
                continue
            if ch == "&":
                prev_ch = s[i - 1] if i > 0 else ""
                next_ch = s[i + 1] if i + 1 < n else ""
                if prev_ch not in (">", "<") and next_ch != ">":
                    segments.append((op, "".join(buf).strip()))
                    buf = []
                    op = "&"
                    i += 1
                    continue
        buf.append(ch)
        i += 1
    segments.append((op, "".join(buf).strip()))
    while len(segments) > 1 and not segments[-1][1] and segments[-1][0] in (";", "\n"):
        segments.pop()
    return segments


def _is_redirect_token(tok: str) -> bool:
    return tok in REDIRECT_OPS or bool(re.match(r"^(?:\d+|&)?(?:>>?|<<?|[<>]&)", tok))


def _is_exec_command(seg: str) -> bool:
    if not seg or seg.startswith("("):
        return False
    tokens = _shlex_split(seg)
    if not tokens or tokens[0] != "exec":
        return False
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-a" and i + 1 < len(tokens):
            i += 2
            continue
        if tok in ("-c", "-l", "--"):
            i += 1
            continue
        if tok in REDIRECT_OPS or re.match(r"^\d*(?:>>?|<<?|[<>]&)$", tok):
            i += 2
            continue
        if _is_redirect_token(tok):
            i += 1
            continue
        rest = tokens[i:]
        if Path(rest[0]).name == "env":
            rest = rest[1:]
            while rest and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", rest[0]):
                rest = rest[1:]
            if not rest:
                return False
        if SHELL_RE.match(rest[0]):
            return _extract_shell_c_script(rest) is not None and shell_c_violation(rest) is None
        return not rest[0].startswith("-") and not rest[0].startswith("#")
    return False


def _is_unexeced_server_command(seg: str) -> bool:
    tokens = _shlex_split(seg)
    while tokens and (
        tokens[0] in LEADING_KEYWORDS
        or Path(tokens[0]).name == "env"
        or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0])
    ):
        tokens.pop(0)
    if not tokens or tokens[0] == "exec":
        return False
    cmd = Path(tokens[0]).name
    if cmd in SERVER_BINS:
        return True
    if cmd.startswith("python"):
        for idx, tok in enumerate(tokens[1:], start=1):
            if tok == "-m" and idx + 1 < len(tokens):
                mod = tokens[idx + 1].split(".")[0]
                if mod in SERVER_MODULES:
                    return True
                break
    return False


def shell_c_violation(argv: list[str]) -> str | None:
    """Return the offending script if argv is `sh -c <script>` without safe terminal exec."""
    script = _extract_shell_c_script(argv)
    if script is None:
        return None
    segments = _split_shell_segments(script)
    if not segments:
        return script
    final_op, final_seg = segments[-1]
    if final_op not in (None, ";", "\n", "&&") or not _is_exec_command(final_seg):
        return script
    for op, seg in segments[:-1]:
        if op == "&" or _is_exec_command(seg) or _is_unexeced_server_command(seg):
            return script
    return None


def _normalize_argv_list(items: list[str]) -> list[str]:
    if len(items) == 1 and " " in items[0].strip():
        return _shlex_split(items[0].strip())
    return items


def _dockerfile_exec_forms(path: Path):
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not re.match(r"^\s*#", line)
    ]
    text = re.sub(r"\\\s*\n", " ", "\n".join(lines))
    for m in re.finditer(r"^\s*(CMD|ENTRYPOINT)\s+(.+?)\s*$", text, re.M | re.I):
        kind = m.group(1).upper()
        raw = m.group(2).strip()
        if not raw:
            continue
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    yield kind, _normalize_argv_list([str(v) for v in parsed])
                    continue
            except json.JSONDecodeError:
                raw = raw.strip("[]").strip()
        tokens = _shlex_split(raw)
        if _extract_shell_c_script(tokens) is not None:
            yield kind, tokens
        else:
            yield kind, ["sh", "-c", raw]


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that tolerates compose merge tags such as !override / !reset."""


def _any_tag(loader, suffix, node):
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return loader.construct_scalar(node)


_ComposeLoader.add_multi_constructor("!", _any_tag)


def _compose_argvs(path: Path):
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader) or {}
    for name, svc in (data.get("services") or {}).items():
        for key in ("command", "entrypoint"):
            val = (svc or {}).get(key)
            if isinstance(val, list) and val:
                yield name, key, _normalize_argv_list([str(v) for v in val])
            elif isinstance(val, str) and val.strip():
                yield name, key, _shlex_split(val.strip())


def collect_violations(
    root: Path | None = None,
    allowlist: Mapping[tuple[str, str], str] | None = None,
) -> list[str]:
    base = ROOT if root is None else Path(root)
    active_allowlist = ONE_SHOT_ALLOWLIST if allowlist is None else allowlist
    dockerfiles = (
        _tracked(["*Dockerfile", "*Dockerfile.*"])
        if root is None
        else _tracked(["*Dockerfile", "*Dockerfile.*"], base)
    )
    compose_files = (
        _tracked(["docker-compose*.yml", "docker-compose*.yaml"])
        if root is None
        else _tracked(["docker-compose*.yml", "docker-compose*.yaml"], base)
    )
    bad = []
    for path in dockerfiles:
        for kind, argv in _dockerfile_exec_forms(path):
            script = shell_c_violation(argv)
            if script:
                bad.append(f"{path.relative_to(base)}: {kind} sh -c without exec: {script}")
    for path in compose_files:
        rel = str(path.relative_to(base))
        for svc, key, argv in _compose_argvs(path):
            if str(active_allowlist.get((rel, svc), "")).strip():
                continue
            script = shell_c_violation(argv)
            if script:
                bad.append(f"{rel}:{svc}: {key} sh -c without exec: {script}")
    return bad


def test_no_long_running_sh_c_without_exec():
    assert collect_violations() == []


def test_allowlist_has_reasons_and_exists():
    for (rel, svc), reason in ONE_SHOT_ALLOWLIST.items():
        assert reason.strip()
        assert svc in {s for s, _, _ in _compose_argvs(ROOT / rel)}


def test_detector_flags_and_accepts():
    assert shell_c_violation(["sh", "-c", "uvicorn a:b --port ${PORT:-1}"])
    assert shell_c_violation(["bash", "-c", "uvicorn a:b"])
    assert shell_c_violation(["sh", "-c", "true || exec echo skipped; uvicorn app:app"])
    assert shell_c_violation(["sh", "-c", "true || exec uvicorn app:app"])
    assert shell_c_violation(["sh", "-c", "uvicorn app:app && exec true"])
    assert shell_c_violation(["sh", "-c", "exec uvicorn app:app | cat"])
    assert shell_c_violation(["sh", "-c", "exec uvicorn app:app &"])
    assert shell_c_violation(["sh", "-c", "(exec uvicorn app:app)"])
    assert shell_c_violation(["sh", "-c", "exec >/dev/null; uvicorn app:app"])
    assert shell_c_violation(["sh", "-c", "exec sh -c 'uvicorn app:app'"])
    assert shell_c_violation(["sh", "-c", "my-daemon & true; exec wait"])
    assert not shell_c_violation(["sh", "-c", "exec uvicorn a:b --port ${PORT:-1}"])
    assert not shell_c_violation(["sh", "-ec", "mkdir -p /tmp/x && exec uvicorn a:b"])
    assert not shell_c_violation(["uvicorn", "a:b"])


def test_collector_flags_shell_and_string_forms_in_isolated_fixture(tmp_path, monkeypatch):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        'FROM python:3.12-slim\nCMD sh -c "uvicorn app:app"\n',
        encoding="utf-8",
    )
    dockerfile_decoy = tmp_path / "services" / "decoy" / "Dockerfile"
    dockerfile_decoy.parent.mkdir(parents=True)
    dockerfile_decoy.write_text(
        'FROM python:3.12-slim\nENTRYPOINT ["sh", "-c", "true || exec echo skipped; uvicorn app:app"]\n',
        encoding="utf-8",
    )
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n"
        '  api:\n    command: sh -c "uvicorn app:app"\n'
        '  worker:\n    entrypoint: bash -c "uvicorn app:app"\n'
        '  decoy:\n    command: ["sh", "-c", "true || exec echo skipped; uvicorn app:app"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    violations = collect_violations()
    assert len(violations) == 5
    assert any("Dockerfile: CMD sh -c without exec: uvicorn app:app" in v for v in violations)
    assert any("services/decoy/Dockerfile: ENTRYPOINT sh -c without exec:" in v for v in violations)
    assert any("docker-compose.yml:api: command sh -c without exec: uvicorn app:app" in v for v in violations)
    assert any("docker-compose.yml:worker: entrypoint sh -c without exec: uvicorn app:app" in v for v in violations)
    assert any("docker-compose.yml:decoy: command sh -c without exec:" in v for v in violations)


def test_collector_accepts_valid_exec_and_justified_one_shot_fixture(tmp_path):
    dockerfile_json = tmp_path / "services" / "a" / "Dockerfile"
    dockerfile_json.parent.mkdir(parents=True)
    dockerfile_json.write_text(
        'FROM python:3.12-slim\nCMD ["sh", "-c", "exec uvicorn app:app --port ${PORT:-8000}"]\n',
        encoding="utf-8",
    )
    dockerfile_shell = tmp_path / "services" / "b" / "Dockerfile"
    dockerfile_shell.parent.mkdir(parents=True)
    dockerfile_shell.write_text(
        'FROM python:3.12-slim\nCMD sh -c "exec uvicorn app:app --port ${PORT:-8000}"\n',
        encoding="utf-8",
    )
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n"
        '  api:\n    command: sh -c "exec uvicorn app:app"\n'
        '  gateway:\n    entrypoint: ["bash", "-ec", "mkdir -p /tmp/x && exec uvicorn app:app"]\n'
        '  openclaw-data-init:\n    command: sh -c "mkdir -p /data && chown -R 1000:1000 /data"\n',
        encoding="utf-8",
    )
    assert collect_violations(root=tmp_path) == []
    unjustified = collect_violations(
        root=tmp_path,
        allowlist={("docker-compose.yml", "openclaw-data-init"): ""},
    )
    assert len(unjustified) == 1
    assert "docker-compose.yml:openclaw-data-init: command sh -c without exec:" in unjustified[0]

