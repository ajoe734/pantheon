"""Contract: long-running service processes must be PID 1 (exec) so SIGTERM reaches them.

`sh -c "uvicorn ..."` leaves sh as PID 1, which does not forward SIGTERM, so
`docker stop` waits out the grace period and ends with SIGKILL (137).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

# One-shot init containers that exit on their own; a shell wrapper is fine.
ONE_SHOT_ALLOWLIST = {
    ("docker-compose.yml", "openclaw-data-init"): "one-shot chown/mkdir init, exits immediately",
}

SHELL_RE = re.compile(r"^(?:/bin/|/usr/bin/)?(?:ba)?sh$")


def _tracked(patterns: list[str]) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", *patterns], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.split()
    return [ROOT / p for p in out]


def shell_c_violation(argv: list[str]) -> str | None:
    """Return the offending script if argv is `sh -c <script>` without exec."""
    if len(argv) >= 3 and SHELL_RE.match(argv[0]) and argv[1] in ("-c", "-lc"):
        script = argv[2].strip()
        if not re.search(r"(^|[;&|]\s*)exec\s", script):
            return script
    return None


def _dockerfile_exec_forms(path: Path):
    text = path.read_text().replace("\\\n", " ")
    for m in re.finditer(r"^\s*(CMD|ENTRYPOINT)\s+(\[.*\])\s*$", text, re.M):
        try:
            yield m.group(1), json.loads(m.group(2))
        except json.JSONDecodeError:
            continue


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
    data = yaml.load(path.read_text(), Loader=_ComposeLoader) or {}
    for name, svc in (data.get("services") or {}).items():
        for key in ("command", "entrypoint"):
            val = (svc or {}).get(key)
            if isinstance(val, list):
                yield name, key, [str(v) for v in val]


def collect_violations() -> list[str]:
    bad = []
    for path in _tracked(["*Dockerfile", "*Dockerfile.*"]):
        for kind, argv in _dockerfile_exec_forms(path):
            script = shell_c_violation(argv)
            if script:
                bad.append(f"{path.relative_to(ROOT)}: {kind} sh -c without exec: {script}")
    for path in _tracked(["docker-compose*.yml", "docker-compose*.yaml"]):
        rel = str(path.relative_to(ROOT))
        for svc, key, argv in _compose_argvs(path):
            if (rel, svc) in ONE_SHOT_ALLOWLIST:
                continue
            script = shell_c_violation(argv)
            if script:
                bad.append(f"{rel}:{svc}: {key} sh -c without exec: {script}")
    return bad


def test_no_long_running_sh_c_without_exec():
    assert collect_violations() == []


def test_allowlist_has_reasons_and_exists():
    for (rel, svc), reason in ONE_SHOT_ALLOWLIST.items():
        assert reason
        assert svc in {s for s, _, _ in _compose_argvs(ROOT / rel)}


def test_detector_flags_and_accepts():
    assert shell_c_violation(["sh", "-c", "uvicorn a:b --port ${PORT:-1}"])
    assert shell_c_violation(["bash", "-c", "uvicorn a:b"])
    assert not shell_c_violation(["sh", "-c", "exec uvicorn a:b --port ${PORT:-1}"])
    assert not shell_c_violation(["uvicorn", "a:b"])
