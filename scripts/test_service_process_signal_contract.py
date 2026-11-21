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
SERVER_MODULES = {"uvicorn", "gunicorn", "hypercorn", "daphne", "waitress", "http.server"}
LEADING_KEYWORDS = {"if", "then", "else", "elif", "while", "until", "do", "{", "}", "!"}
REDIRECT_OPS = {">", ">>", "<", "<<", "<>", ">&", "<&", ">|", "&>", "&>>"}


def _is_shell_token(token: str) -> bool:
    return bool(SHELL_RE.match(token) or SHELL_RE.match(Path(token).name))


def _shlex_split(text: str) -> list[str]:
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return text.split()


def _unwrap_command_tokens(seg: str) -> list[str]:
    tokens = _shlex_split(seg)
    while tokens:
        head = tokens[0]
        if head in LEADING_KEYWORDS or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", head):
            tokens.pop(0)
            continue
        cmd_name = Path(head).name
        if cmd_name == "env":
            tokens.pop(0)
            while tokens and (
                re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0])
                or tokens[0].startswith("-")
            ):
                flag = tokens.pop(0)
                if flag in ("-u", "-C", "-S") and tokens:
                    tokens.pop(0)
            continue
        if cmd_name in ("command", "builtin", "nohup", "setsid", "busybox"):
            tokens.pop(0)
            while tokens and tokens[0].startswith("-"):
                tokens.pop(0)
            continue
        if cmd_name in ("poetry", "uv", "pipenv") and len(tokens) > 1 and tokens[1] == "run":
            tokens = tokens[2:]
            while tokens and tokens[0].startswith("-"):
                tokens.pop(0)
            continue
        break
    return tokens


def _has_subshell_or_cmd_subst(seg: str) -> bool:
    quote: str | None = None
    i = 0
    n = len(seg)
    while i < n:
        ch = seg[i]
        if quote == "'":
            if ch == "'":
                quote = None
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if quote == '"':
            if ch == "`" or seg[i : i + 2] == "$(":
                return True
            if ch == '"':
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if ch in ("(", ")", "`"):
            return True
        i += 1
    return False


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


def _unwrap_shell_argv(argv: list[str]) -> list[str]:
    tokens = list(argv)
    while tokens and Path(tokens[0]).name in ("env", "command", "builtin", "nohup", "setsid", "busybox"):
        cmd_name = Path(tokens.pop(0)).name
        if cmd_name == "env":
            while tokens and (
                re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0])
                or tokens[0].startswith("-")
            ):
                flag = tokens.pop(0)
                if flag in ("-u", "-C", "-S") and tokens:
                    tokens.pop(0)
        else:
            while tokens and tokens[0].startswith("-"):
                tokens.pop(0)
    return tokens


def _scan_shell_c(argv: list[str]) -> tuple[bool, str | None]:
    tokens = _unwrap_shell_argv(argv)
    if not tokens or not _is_shell_token(tokens[0]):
        return False, None
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-o", "+o") and i + 1 < len(tokens):
            i += 2
            continue
        if tok == "--":
            i += 1
            break
        if tok.startswith("--"):
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:
            if "c" in tok[1:]:
                if i + 1 < len(tokens):
                    return True, tokens[i + 1].strip()
                return True, None
            i += 1
            continue
        break
    return False, None


def _is_shell_c_prefix(argv: list[str]) -> bool:
    has_c, script = _scan_shell_c(argv)
    return has_c and script is None


def _extract_shell_c_script(argv: list[str]) -> str | None:
    has_c, script = _scan_shell_c(argv)
    if not has_c:
        return None
    return "" if script is None else script


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
    if not seg or _has_subshell_or_cmd_subst(seg):
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
        while rest and Path(rest[0]).name in ("env", "command", "builtin", "nohup", "setsid", "busybox"):
            cmd_name = Path(rest.pop(0)).name
            if cmd_name == "env":
                while rest and (
                    re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", rest[0])
                    or rest[0].startswith("-")
                ):
                    flag = rest.pop(0)
                    if flag in ("-u", "-C", "-S") and rest:
                        rest.pop(0)
            else:
                while rest and rest[0].startswith("-"):
                    rest.pop(0)
        if not rest:
            return False
        if _is_shell_token(rest[0]):
            return _extract_shell_c_script(rest) is not None and shell_c_violation(rest) is None
        return not rest[0].startswith("-") and not rest[0].startswith("#")
    return False


def _is_unexeced_server_command(seg: str) -> bool:
    tokens = _unwrap_command_tokens(seg)
    if not tokens or tokens[0] == "exec":
        return False
    cmd = Path(tokens[0]).name
    if cmd in SERVER_BINS:
        return True
    if cmd.startswith("python"):
        for idx, tok in enumerate(tokens[1:], start=1):
            if tok == "-m" and idx + 1 < len(tokens):
                mod_full = tokens[idx + 1]
                mod_root = mod_full.split(".")[0]
                if mod_full in SERVER_MODULES or mod_root in SERVER_MODULES:
                    return True
                break
    return False


def _is_unsafe_prefix_segment(seg: str) -> bool:
    if _has_subshell_or_cmd_subst(seg) or _is_exec_command(seg) or _is_unexeced_server_command(seg):
        return True
    tokens = _unwrap_command_tokens(seg)
    if not tokens:
        return False
    if tokens[0] in ("exec", "eval", "source", ".") or _is_shell_token(tokens[0]):
        return True
    return False


def shell_c_violation(argv: list[str]) -> str | None:
    """Return the offending script if argv is `sh -c <script>` without safe terminal exec."""
    script = _extract_shell_c_script(argv)
    if script is None:
        return None
    segments = _split_shell_segments(script)
    if not segments:
        return script or " ".join(argv)
    final_op, final_seg = segments[-1]
    if final_op not in (None, ";", "\n", "&&") or not _is_exec_command(final_seg):
        return script
    for op, seg in segments[:-1]:
        if op == "&" or _is_unsafe_prefix_segment(seg):
            return script
    return None


def _normalize_argv_list(items: list[str]) -> list[str]:
    if len(items) == 1 and " " in items[0].strip():
        raw = items[0].strip()
        tokens = _shlex_split(raw)
        if _scan_shell_c(tokens)[0]:
            if len(_split_shell_segments(raw)) > 1:
                return ["sh", "-c", raw]
            return tokens
    return items


def _parse_dockerfile_instruction(raw: str) -> tuple[list[str] | None, str | None]:
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                if not parsed:
                    return None, None
                items = [str(v) for v in parsed]
                single_raw = items[0].strip() if len(items) == 1 else None
                return _normalize_argv_list(items), single_raw
        except json.JSONDecodeError:
            raw = raw.strip("[]").strip()
    tokens = _shlex_split(raw)
    if len(_split_shell_segments(raw)) == 1 and _scan_shell_c(tokens)[0]:
        return tokens, None
    return ["sh", "-c", raw], raw


def _combine_entrypoint_and_cmd(
    ep_argv: list[str],
    cmd_argv: list[str],
    cmd_raw: str | None = None,
) -> list[str]:
    if _is_shell_c_prefix(ep_argv) and cmd_raw is not None:
        return [*ep_argv, cmd_raw]
    return [*ep_argv, *cmd_argv]


def _emit_stage_forms(
    ep: tuple[list[str], str | None] | None,
    cmd: tuple[list[str], str | None] | None,
    ep_label: str,
    cmd_label: str,
):
    if ep is not None and cmd is None:
        yield ep_label, ep[0]
        return
    if ep is None and cmd is not None:
        yield cmd_label, cmd[0]
        return
    if ep is None or cmd is None:
        return
    ep_argv, _ = ep
    cmd_argv, cmd_raw = cmd
    ep_has_c, ep_script = _scan_shell_c(ep_argv)
    if ep_has_c and ep_script is not None:
        yield ep_label, ep_argv
        yield cmd_label, cmd_argv
        return
    combined = _combine_entrypoint_and_cmd(ep_argv, cmd_argv, cmd_raw)
    if _is_shell_c_prefix(ep_argv) or (_scan_shell_c(combined)[0] and not _scan_shell_c(cmd_argv)[0]):
        yield f"{ep_label}+{cmd_label}", combined
        return
    yield ep_label, ep_argv
    yield cmd_label, cmd_argv


def _dockerfile_stages(path: Path):
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not re.match(r"^\s*#", line)
    ]
    text = re.sub(r"\\\s*\n", " ", "\n".join(lines))
    stages: list[dict[str, object]] = []
    aliases: dict[str, tuple[tuple[list[str], str | None] | None, tuple[list[str], str | None] | None]] = {}
    current: dict[str, object] | None = None
    for m in re.finditer(r"^\s*(FROM|CMD|ENTRYPOINT)\s+(.+?)\s*$", text, re.M | re.I):
        kind = m.group(1).upper()
        raw = m.group(2).strip()
        if not raw:
            continue
        if kind == "FROM":
            if current is not None:
                stages.append(current)
                alias = current.get("alias")
                if isinstance(alias, str):
                    aliases[alias] = (current.get("ep"), current.get("cmd"))  # type: ignore[assignment]
            from_tokens = [t for t in _shlex_split(raw) if not t.startswith("--")]
            base_ref = from_tokens[0].lower() if from_tokens else ""
            stage_alias = (
                from_tokens[2].lower()
                if len(from_tokens) >= 3 and from_tokens[1].lower() == "as"
                else None
            )
            inh_ep, inh_cmd = aliases.get(base_ref, (None, None))
            current = {
                "alias": stage_alias,
                "base": base_ref,
                "ep": inh_ep,
                "cmd": inh_cmd,
                "overridden": [],
            }
            continue
        if current is None:
            current = {"alias": None, "base": "", "ep": None, "cmd": None, "overridden": []}
        argv, single_raw = _parse_dockerfile_instruction(raw)
        slot = "ep" if kind == "ENTRYPOINT" else "cmd"
        prev = current.get(slot)
        if prev is not None and not (slot == "ep" and _is_shell_c_prefix(prev[0])):  # type: ignore[index]
            current["overridden"].append((kind, prev[0]))  # type: ignore[attr-defined,index]
        current[slot] = (argv, single_raw) if argv is not None else None
    if current is not None:
        stages.append(current)
    return stages


def _dockerfile_exec_forms(path: Path):
    stages = _dockerfile_stages(path)
    inherited_bases = {str(s.get("base")) for s in stages if s.get("base")}
    for idx, stage in enumerate(stages):
        for kind, argv in stage["overridden"]:  # type: ignore[union-attr]
            yield kind, argv
        alias = stage.get("alias")
        ep = stage.get("ep")
        cmd = stage.get("cmd")
        if (
            idx < len(stages) - 1
            and isinstance(alias, str)
            and alias in inherited_bases
            and ep is not None
            and cmd is None
            and _is_shell_c_prefix(ep[0])  # type: ignore[index]
        ):
            continue
        yield from _emit_stage_forms(ep, cmd, "ENTRYPOINT", "CMD")  # type: ignore[arg-type]


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that tolerates compose merge tags such as !override / !reset."""


def _any_tag(loader, suffix, node):
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return loader.construct_scalar(node)


_ComposeLoader.add_multi_constructor("!", _any_tag)


def _parse_compose_value(val: object) -> tuple[list[str], str | None] | None:
    if isinstance(val, list):
        if not val:
            return None
        items = [str(v) for v in val]
        single_raw = items[0].strip() if len(items) == 1 else None
        return _normalize_argv_list(items), single_raw
    if isinstance(val, str):
        raw = val.strip()
        if not raw:
            return None
        tokens = _shlex_split(raw)
        if len(_split_shell_segments(raw)) > 1 and _scan_shell_c(tokens)[0]:
            return ["sh", "-c", raw], raw
        return tokens, raw
    return None


def _resolve_compose_dockerfile_ep(
    compose_path: Path,
    svc: Mapping[str, object],
) -> tuple[list[str], str | None] | None:
    build_cfg = svc.get("build")
    df_path: Path | None = None
    if isinstance(build_cfg, str) and build_cfg.strip():
        df_path = compose_path.parent / build_cfg.strip() / "Dockerfile"
    elif isinstance(build_cfg, Mapping):
        ctx = compose_path.parent / str(build_cfg.get("context") or ".")
        df_name = str(build_cfg.get("dockerfile") or "Dockerfile")
        df_path = ctx / df_name
    if df_path is not None and df_path.is_file():
        stages = _dockerfile_stages(df_path)
        if stages:
            ep = stages[-1].get("ep")
            if isinstance(ep, tuple):
                return ep  # type: ignore[return-value]
    return None


def _compose_argvs(path: Path):
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader) or {}
    for name, svc in (data.get("services") or {}).items():
        if not isinstance(svc, Mapping):
            continue
        ep = _parse_compose_value(svc.get("entrypoint"))
        cmd = _parse_compose_value(svc.get("command"))
        if ep is None and cmd is not None:
            df_ep = _resolve_compose_dockerfile_ep(path, svc)
            if df_ep is not None and not (_scan_shell_c(df_ep[0])[0] and _scan_shell_c(df_ep[0])[1] is not None):
                ep = df_ep
        yield from (
            (name, key, argv)
            for key, argv in _emit_stage_forms(ep, cmd, "entrypoint", "command")
        )


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
    assert shell_c_violation(["sh", "-c", "sh -c 'uvicorn app:app'; exec true"])
    assert shell_c_violation(["sh", "-c", "sh -c 'exec uvicorn app:app'; exec true"])
    assert shell_c_violation(["sh", "-c", "(uvicorn app:app); exec true"])
    assert shell_c_violation(["sh", "-c", "(exec uvicorn app:app); exec true"])
    assert shell_c_violation(["sh", "-c", "if true; then sh -c 'uvicorn app:app'; fi; exec true"])
    assert shell_c_violation(["sh", "-c", "if true; then (uvicorn app:app); fi; exec true"])
    assert shell_c_violation(["sh", "-c", "exec sh -c \"(uvicorn app:app); exec true\""])
    assert shell_c_violation(["sh", "-c", "exec uvicorn app:app | cat"])
    assert shell_c_violation(["sh", "-c", "exec uvicorn app:app &"])
    assert shell_c_violation(["sh", "-c", "(exec uvicorn app:app)"])
    assert shell_c_violation(["sh", "-c", "exec >/dev/null; uvicorn app:app"])
    assert shell_c_violation(["sh", "-c", "exec sh -c 'uvicorn app:app'"])
    assert shell_c_violation(["sh", "-c", "my-daemon & true; exec wait"])
    assert shell_c_violation(["sh", "-c"])
    assert not shell_c_violation(["sh", "-c", "exec uvicorn a:b --port ${PORT:-1}"])
    assert not shell_c_violation(["sh", "-ec", "mkdir -p /tmp/x && exec uvicorn a:b"])
    assert not shell_c_violation(["sh", "-c", "exec sh -c 'mkdir -p /tmp/x && exec uvicorn a:b'"])
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
    dockerfile_nested = tmp_path / "services" / "nested" / "Dockerfile"
    dockerfile_nested.parent.mkdir(parents=True)
    dockerfile_nested.write_text(
        'FROM python:3.12-slim\n'
        'ENTRYPOINT ["sh", "-c", "(uvicorn app:app); exec true"]\n'
        'CMD ["sh", "-c", "sh -c \'uvicorn app:app\'; exec true"]\n',
        encoding="utf-8",
    )
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n"
        '  api:\n    command: sh -c "uvicorn app:app"\n'
        '  worker:\n    entrypoint: bash -c "uvicorn app:app"\n'
        '  decoy:\n    command: ["sh", "-c", "true || exec echo skipped; uvicorn app:app"]\n'
        '  nested-cmd:\n    command: ["sh", "-c", "sh -c \'uvicorn app:app\'; exec true"]\n'
        '  subshell-ep:\n    entrypoint: ["bash", "-c", "(uvicorn app:app); exec true"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    violations = collect_violations()
    assert len(violations) == 9
    assert any("Dockerfile: CMD sh -c without exec: uvicorn app:app" in v for v in violations)
    assert any("services/decoy/Dockerfile: ENTRYPOINT sh -c without exec:" in v for v in violations)
    assert any("services/nested/Dockerfile: ENTRYPOINT sh -c without exec: (uvicorn app:app); exec true" in v for v in violations)
    assert any("services/nested/Dockerfile: CMD sh -c without exec: sh -c 'uvicorn app:app'; exec true" in v for v in violations)
    assert any("docker-compose.yml:api: command sh -c without exec: uvicorn app:app" in v for v in violations)
    assert any("docker-compose.yml:worker: entrypoint sh -c without exec: uvicorn app:app" in v for v in violations)
    assert any("docker-compose.yml:decoy: command sh -c without exec:" in v for v in violations)
    assert any("docker-compose.yml:nested-cmd: command sh -c without exec: sh -c 'uvicorn app:app'; exec true" in v for v in violations)
    assert any("docker-compose.yml:subshell-ep: entrypoint sh -c without exec: (uvicorn app:app); exec true" in v for v in violations)


def test_collector_accepts_valid_exec_and_justified_one_shot_fixture(tmp_path):
    dockerfile_json = tmp_path / "services" / "a" / "Dockerfile"
    dockerfile_json.parent.mkdir(parents=True)
    dockerfile_json.write_text(
        'FROM python:3.12-slim\n'
        'ENTRYPOINT ["sh", "-ec", "mkdir -p /tmp/x && exec uvicorn app:app --port ${PORT:-8000}"]\n'
        'CMD ["sh", "-c", "exec uvicorn app:app --port ${PORT:-8000}"]\n',
        encoding="utf-8",
    )
    dockerfile_shell = tmp_path / "services" / "b" / "Dockerfile"
    dockerfile_shell.parent.mkdir(parents=True)
    dockerfile_shell.write_text(
        'FROM python:3.12-slim\nCMD sh -c "mkdir -p /tmp/x && exec uvicorn app:app --port ${PORT:-8000}"\n',
        encoding="utf-8",
    )
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n"
        '  api:\n    command: sh -c "mkdir -p /tmp/x && exec uvicorn app:app"\n'
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


def test_collector_rejects_and_accepts_split_shell_launches(tmp_path):
    bad_root = tmp_path / "bad"
    (bad_root / "services" / "split_c").mkdir(parents=True)
    (bad_root / "services" / "split_c" / "Dockerfile").write_text(
        'FROM python:3.12-slim\nENTRYPOINT ["sh", "-c"]\nCMD ["uvicorn app:app"]\n',
        encoding="utf-8",
    )
    (bad_root / "services" / "split_ec").mkdir(parents=True)
    (bad_root / "services" / "split_ec" / "Dockerfile").write_text(
        'FROM python:3.12-slim\nENTRYPOINT ["bash", "-ec"]\nCMD ["mkdir -p /tmp/x && uvicorn app:app"]\n',
        encoding="utf-8",
    )
    (bad_root / "services" / "split_flag").mkdir(parents=True)
    (bad_root / "services" / "split_flag" / "Dockerfile").write_text(
        'FROM python:3.12-slim\nENTRYPOINT ["sh"]\nCMD ["-c", "uvicorn app:app"]\n',
        encoding="utf-8",
    )
    (bad_root / "docker-compose.yml").write_text(
        "services:\n"
        '  split-list:\n    entrypoint: ["sh", "-c"]\n    command: ["uvicorn app:app"]\n'
        '  split-str:\n    entrypoint: sh -c\n    command: "mkdir -p /tmp/x && uvicorn app:app"\n'
        '  split-flag:\n    entrypoint: ["bash"]\n    command: ["-c", "uvicorn app:app"]\n',
        encoding="utf-8",
    )
    bad_violations = collect_violations(root=bad_root)
    assert len(bad_violations) == 6
    assert any("services/split_c/Dockerfile: ENTRYPOINT+CMD sh -c without exec: uvicorn app:app" in v for v in bad_violations)
    assert any("services/split_ec/Dockerfile: ENTRYPOINT+CMD sh -c without exec: mkdir -p /tmp/x && uvicorn app:app" in v for v in bad_violations)
    assert any("services/split_flag/Dockerfile: ENTRYPOINT+CMD sh -c without exec: uvicorn app:app" in v for v in bad_violations)
    assert any("docker-compose.yml:split-list: entrypoint+command sh -c without exec: uvicorn app:app" in v for v in bad_violations)
    assert any("docker-compose.yml:split-str: entrypoint+command sh -c without exec: mkdir -p /tmp/x && uvicorn app:app" in v for v in bad_violations)
    assert any("docker-compose.yml:split-flag: entrypoint+command sh -c without exec: uvicorn app:app" in v for v in bad_violations)

    good_root = tmp_path / "good"
    (good_root / "services" / "split_c").mkdir(parents=True)
    (good_root / "services" / "split_c" / "Dockerfile").write_text(
        'FROM python:3.12-slim\nENTRYPOINT ["sh", "-c"]\nCMD ["exec uvicorn app:app --port ${PORT:-8000}"]\n',
        encoding="utf-8",
    )
    (good_root / "services" / "split_ec").mkdir(parents=True)
    (good_root / "services" / "split_ec" / "Dockerfile").write_text(
        'FROM python:3.12-slim\nENTRYPOINT ["bash", "-ec"]\nCMD ["mkdir -p /tmp/x && exec uvicorn app:app"]\n',
        encoding="utf-8",
    )
    (good_root / "services" / "split_flag").mkdir(parents=True)
    (good_root / "services" / "split_flag" / "Dockerfile").write_text(
        'FROM python:3.12-slim\nENTRYPOINT ["sh"]\nCMD ["-c", "exec uvicorn app:app"]\n',
        encoding="utf-8",
    )
    (good_root / "docker-compose.yml").write_text(
        "services:\n"
        '  split-list:\n    entrypoint: ["sh", "-c"]\n    command: ["exec uvicorn app:app"]\n'
        '  split-str:\n    entrypoint: sh -c\n    command: "mkdir -p /tmp/x && exec uvicorn app:app"\n'
        '  split-flag:\n    entrypoint: ["bash"]\n    command: ["-c", "exec uvicorn app:app"]\n',
        encoding="utf-8",
    )
    assert collect_violations(root=good_root) == []


