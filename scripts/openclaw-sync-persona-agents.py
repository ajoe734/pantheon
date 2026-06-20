#!/usr/bin/env python3
"""Reconcile Pantheon personas → OpenClaw agents (run INSIDE the gateway container).

Reads persona records as JSON (stdin or --personas-file) and ensures each persona
exists as an isolated OpenClaw agent (`openclaw agents add` + a per-agent SOUL.md),
so personas run as themselves (model=openclaw/<persona_id>), not on shared `main`.

Self-contained / stdlib-only so it runs unchanged inside the openclaw gateway
image. The reconcile logic mirrors integrations/openclaw/persona_agent_sync.py
(which is unit-tested); this is the deployable driver.

Usage (in the gateway container):
    cat personas.json | docker exec -i pantheon-openclaw-gateway-1 python3 - < this_script
  or, after copying in:
    python3 openclaw-sync-persona-agents.py --personas-file /tmp/personas.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Mapping

PERSONA_WORKSPACE_ROOT = "/home/node/.openclaw/workspaces"
DEFAULT_PERSONA_MODEL = "anthropic/claude-opus-4-8"
KNOWN_MODELS = {"anthropic/claude-opus-4-8", "anthropic/claude-sonnet-4-6", "openai/gpt-5.5"}


def persona_id(p: Mapping[str, Any]) -> str:
    return str(p.get("persona_id") or p.get("id") or "").strip()


def resolve_model(p: Mapping[str, Any]) -> str:
    pref = str(p.get("preferred_model") or p.get("model") or "").strip()
    return pref if pref in KNOWN_MODELS else DEFAULT_PERSONA_MODEL


TRAIT_FIELDS = ("instruments", "risk_appetite", "decision_style", "time_horizon", "hard_rules", "persona_voice")
TRAIT_LABELS = {
    "instruments": "Instruments / universe", "risk_appetite": "Risk appetite",
    "decision_style": "Decision style", "time_horizon": "Time horizon",
    "hard_rules": "Hard rules", "persona_voice": "Voice / temperament",
}


def trait_value(p: Mapping[str, Any], key: str) -> str:
    raw = None
    for container in (p.get("traits"), (p.get("metadata") or {}).get("traits")):
        if isinstance(container, dict) and container.get(key) not in (None, ""):
            raw = container.get(key)
            break
    if raw in (None, ""):
        raw = p.get(key)
    if raw in (None, ""):
        return ""
    if isinstance(raw, (list, tuple)):
        return ", ".join(str(x).strip() for x in raw if str(x).strip())
    return str(raw).strip()


def build_soul(p: Mapping[str, Any]) -> str:
    pid = persona_id(p)
    name = str(p.get("name") or pid or "Persona").strip()
    mandate = str(p.get("mandate") or "").strip()
    strategy = str(p.get("strategy_family") or p.get("strategyFamily") or "").strip()
    state = str(p.get("lifecycle_state") or p.get("state") or "").strip()
    mandate_line = f"- Mandate: **{mandate}**" if mandate else "- Mandate: (not yet set — ask the operator to define it)"
    strategy_line = f"- Strategy family: **{strategy}**" if strategy else "- Strategy family: (unset)"
    state_line = f"\n- Current lifecycle state: `{state}`" if state else ""
    trait_lines = [f"- {TRAIT_LABELS[k]}: {trait_value(p, k)}" for k in TRAIT_FIELDS if trait_value(p, k)]
    traits_block = ("\n## Your trading character\n" + "\n".join(trait_lines) + "\n") if trait_lines else (
        "\n## Your trading character\n_(No detailed traits set yet — instruments / risk / style / rules / voice "
        "are unset. Operate on mandate + strategy only and tell the operator what to define before sized decisions.)_\n")
    return f"""# SOUL.md — {name} (`{pid}`)

You are **{name}** — a Pantheon trading persona with your own mandate. You are NOT
the Management AI and NOT a generic assistant. Answer only within your mandate;
defer out-of-scope asks to the right persona.

## Who you are
{mandate_line}
{strategy_line}{state_line}
{traits_block}
## Every turn (OODA)
Answer AS this persona, concretely: **Observe** (cite the numbers you were given),
**Orient** (what it means for your strategy family), **Decide** (a concrete stance —
instrument, direction, size/stop, reason). If you can't decide from the data, say
exactly which signal/market input you'd need. Never stall with filler.

## Hard guardrails
- Paper unless a live capital binding is explicitly active (is_real_capital=false). No real orders on your own.
- Stay inside your mandate. Be direct and quantitative — no 「在，老闆」 / NO_REPLY. Reply in 繁體中文 by default.
"""


def existing_agent_ids() -> set[str]:
    proc = subprocess.run(["openclaw", "agents", "list", "--json"], capture_output=True, text=True, timeout=60)
    ids: set[str] = set()
    try:
        data = json.loads((proc.stdout or "").strip())
        agents = data.get("agents") if isinstance(data, dict) else data
        for a in agents or []:
            aid = a.get("id") if isinstance(a, dict) else a
            if aid:
                ids.add(str(aid))
    except (ValueError, TypeError):
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("- "):
                ids.add(line[2:].split(" ")[0].strip())
    return ids


def write_soul(workspace: str, soul: str) -> None:
    os.makedirs(workspace, exist_ok=True)
    with open(os.path.join(workspace, "SOUL.md"), "w", encoding="utf-8") as fh:
        fh.write(soul)


def reconcile(personas: List[Mapping[str, Any]]) -> Dict[str, Any]:
    report: Dict[str, Any] = {"created": [], "updated": [], "failed": []}
    existing = existing_agent_ids()
    for p in personas:
        pid = persona_id(p)
        if not pid:
            report["failed"].append({"persona_id": "?", "error": "no id"})
            continue
        ws = f"{PERSONA_WORKSPACE_ROOT}/{pid}"
        try:
            if pid in existing:
                subprocess.run(["openclaw", "agents", "set-identity", pid, "--name", str(p.get("name") or pid)],
                               capture_output=True, text=True, timeout=60)
                write_soul(ws, build_soul(p))
                report["updated"].append(pid)
            else:
                proc = subprocess.run(
                    ["openclaw", "agents", "add", pid, "--workspace", ws, "--model", resolve_model(p),
                     "--non-interactive", "--json"],
                    capture_output=True, text=True, timeout=90,
                )
                if proc.returncode != 0:
                    report["failed"].append({"persona_id": pid, "error": (proc.stderr or proc.stdout)[:300]})
                    continue
                write_soul(ws, build_soul(p))
                report["created"].append(pid)
        except Exception as exc:  # noqa: BLE001
            report["failed"].append({"persona_id": pid, "error": str(exc)[:300]})
    report["counts"] = {k: len(v) for k, v in report.items() if isinstance(v, list)}
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--personas-file", default=None)
    args = ap.parse_args()
    raw = open(args.personas_file, encoding="utf-8").read() if args.personas_file else sys.stdin.read()
    data = json.loads(raw)
    personas = data.get("personas") if isinstance(data, dict) else data
    report = reconcile(list(personas or []))
    print(json.dumps(report, ensure_ascii=False))
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
