"""Persona → OpenClaw agent sync (real provisioning).

Each Pantheon persona is a governance object in the Persona Registry. To actually
RUN as itself (own identity / model / workspace), a persona must exist as an
**isolated OpenClaw agent** — not share the `main` agent. This module reconciles
the registry (desired state) into OpenClaw agents (`openclaw agents add` + a
per-agent SOUL.md), idempotently.

It is NOT a stub: when wired with the real runner it shells out to the actual
`openclaw` CLI inside the gateway container (where the agent state + workspaces
live). The runner / soul-writer are injectable so the spec-building and
reconcile logic are unit-testable without a live gateway, while the live path is
exercised by an explicit live test (no skip-as-green).

Routing: a synced persona is reachable at model id `openclaw/<persona_id>` via
the gateway `/v1/responses` endpoint (same path Management AI uses for `main`).
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from services.persona.runtime_profile import DEFAULT_PERSONA_MODEL, build_persona_runtime_profile

# Workspaces for synced persona agents live under the gateway state dir.
PERSONA_WORKSPACE_ROOT = "/home/node/.openclaw/workspaces"
# Model pool refs (see integrations/openclaw/model-pool-and-persona-routing.md).
# The runtime profile resolver owns default/preferred/hard-pin/fallback routing.


@dataclass(frozen=True)
class PersonaAgentSpec:
    persona_id: str
    name: str
    model: str
    workspace: str
    soul: str
    sync_generation: int


@dataclass
class SyncReport:
    created: List[str] = field(default_factory=list)
    updated: List[str] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)
    memory_materialized: List[str] = field(default_factory=list)
    failed: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "memory_materialized": self.memory_materialized,
            "failed": self.failed,
            "counts": {
                "created": len(self.created),
                "updated": len(self.updated),
                "unchanged": len(self.unchanged),
                "memory_materialized": len(self.memory_materialized),
                "failed": len(self.failed),
            },
        }


def _persona_id(persona: Mapping[str, Any]) -> str:
    return str(persona.get("persona_id") or persona.get("id") or "").strip()


def _runtime_profile_model(
    persona: Mapping[str, Any],
    route_policy: Optional[Mapping[str, Any]] = None,
) -> tuple[str, str, int]:
    profile = build_persona_runtime_profile(persona, route_policy=route_policy)
    routing = profile.model_routing
    if routing.status != "ready" or not routing.primary_model:
        reason = routing.blocked_reason or routing.reason or "model_routing_degraded"
        invalid = ",".join(routing.invalid_refs) if routing.invalid_refs else "none"
        raise ValueError(
            "persona_runtime_profile_model_routing_degraded "
            f"reason={reason} invalid_refs={invalid} "
            "repair_action=fix_persona_route_policy_or_provider_pool"
        )
    return routing.primary_model, profile.workspace_ref, profile.sync_generation


# Trait fields (beyond mandate/strategy_family) that shape a persona's identity.
# Read from a `traits` dict on the persona record (preferred) or top-level keys.
TRAIT_FIELDS = ("instruments", "risk_appetite", "decision_style", "time_horizon", "hard_rules", "persona_voice")
_TRAIT_LABELS = {
    "instruments": "Instruments / universe",
    "risk_appetite": "Risk appetite",
    "decision_style": "Decision style",
    "time_horizon": "Time horizon",
    "hard_rules": "Hard rules",
    "persona_voice": "Voice / temperament",
}


def _trait_value(persona: Mapping[str, Any], key: str) -> str:
    raw = None
    # Preferred: a `traits` dict (top-level or nested under metadata, where the
    # BFF persists it). Fall back to a top-level key of the same name.
    for container in (persona.get("traits"), (persona.get("metadata") or {}).get("traits")):
        if isinstance(container, Mapping) and container.get(key) not in (None, ""):
            raw = container.get(key)
            break
    if raw in (None, "") and key in persona:
        raw = persona.get(key)
    if raw in (None, ""):
        return ""
    if isinstance(raw, (list, tuple)):
        return ", ".join(str(x).strip() for x in raw if str(x).strip())
    return str(raw).strip()


def build_persona_soul(persona: Mapping[str, Any]) -> str:
    """Render a persona-specific SOUL.md from its registry record + traits.

    The SOUL makes the agent answer AS this trading persona — its mandate,
    strategy, instruments, risk appetite, decision style, hard rules and voice —
    explicitly NOT as the Management AI or a generic assistant, with the
    paper/live capital guardrails. Sparse fields are honestly marked so the
    persona asks for them rather than faking depth it doesn't have.
    """
    pid = _persona_id(persona)
    name = str(persona.get("name") or pid or "Persona").strip()
    mandate = str(persona.get("mandate") or "").strip()
    strategy = str(persona.get("strategy_family") or persona.get("strategyFamily") or "").strip()
    state = str(persona.get("lifecycle_state") or persona.get("state") or "").strip()
    archetype = str(persona.get("archetype") or "").strip()

    mandate_line = f"- Mandate: **{mandate}**" if mandate else "- Mandate: (not yet set in the registry — ask the operator to define it)"
    strategy_line = f"- Strategy family: **{strategy}**" if strategy else "- Strategy family: (unset)"
    archetype_line = f"\n- Archetype: {archetype}" if archetype else ""
    state_line = f"\n- Current lifecycle state: `{state}`" if state else ""

    trait_lines = []
    for key in TRAIT_FIELDS:
        val = _trait_value(persona, key)
        if val:
            trait_lines.append(f"- {_TRAIT_LABELS[key]}: {val}")
    if trait_lines:
        traits_block = "\n## Your trading character\n" + "\n".join(trait_lines) + "\n"
    else:
        traits_block = (
            "\n## Your trading character\n"
            "_(No detailed traits set yet — instruments / risk appetite / decision style / "
            "rules / voice are unset in the registry. Operate on mandate + strategy only, "
            "and tell the operator which of these to define before making sized decisions.)_\n"
        )

    return f"""# SOUL.md — {name} (`{pid}`)

You are **{name}** — a Pantheon trading persona with your own mandate. You are
NOT the Management AI and NOT a generic assistant. You are one persona among many
(each has its own domain); answer only within yours, and defer out-of-scope asks
to the right persona.

## Who you are
{mandate_line}
{strategy_line}{archetype_line}{state_line}
{traits_block}
## How you work every turn (OODA)
When asked to assess or decide, answer **as this persona**, concretely:
- **Observe** — what the signals / market / regime you were given actually show (cite the numbers).
- **Orient** — what it means for *your* mandate and strategy family.
- **Decide** — a concrete stance with a reason and a risk note (instrument, direction, size/stop). If you can't decide from the data, say exactly which signal/market input you'd need — never stall with filler.

## Hard guardrails
- **Paper unless a live capital binding is explicitly active** (`is_real_capital=false` by default). You never place real orders or move capital on your own.
- Stay inside your mandate; don't answer other personas' domains.
- Be direct and quantitative — no filler, no bare 「在，老闆」 / `NO_REPLY`. Reply in 繁體中文 by default.

## Memory
MEMORY.md + memory/ + USER.md in this workspace are your durable memory. Read them; update MEMORY.md when something is worth keeping.
"""


def desired_agent_spec(
    persona: Mapping[str, Any],
    *,
    route_policy: Optional[Mapping[str, Any]] = None,
) -> PersonaAgentSpec:
    pid = _persona_id(persona)
    if not pid:
        raise ValueError("persona record has no persona_id/id")
    model, workspace, sync_generation = _runtime_profile_model(persona, route_policy=route_policy)
    return PersonaAgentSpec(
        persona_id=pid,
        name=str(persona.get("name") or pid),
        model=model,
        workspace=workspace,
        soul=build_persona_soul(persona),
        sync_generation=sync_generation,
    )


# ----------------------------------------------------------------------------
# Real CLI runner (gateway container) — injectable for tests.
# ----------------------------------------------------------------------------

CliRunner = Callable[[List[str]], "subprocess.CompletedProcess[str]"]
SoulWriter = Callable[[str, str], None]  # (workspace_dir, soul_text)
MemoryMaterializer = Callable[[str, Mapping[str, Any], PersonaAgentSpec], Any]


def _default_runner(args: List[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(args, capture_output=True, text=True, timeout=60)


def _default_soul_writer(workspace: str, soul: str) -> None:
    import os

    os.makedirs(workspace, exist_ok=True)
    with open(os.path.join(workspace, "SOUL.md"), "w", encoding="utf-8") as fh:
        fh.write(soul)


def _agent_model(agent: Mapping[str, Any]) -> str:
    for key in ("model", "model_id", "modelId", "model_ref", "modelRef"):
        value = str(agent.get(key) or "").strip()
        if value:
            return value
    runtime = agent.get("runtime") if isinstance(agent.get("runtime"), Mapping) else {}
    for key in ("model", "model_id", "modelRef"):
        value = str(runtime.get(key) or "").strip()
        if value:
            return value
    return ""


def _existing_agents(runner: CliRunner) -> Dict[str, Dict[str, str]]:
    proc = runner(["openclaw", "agents", "list", "--json"])
    out = (proc.stdout or "").strip()
    existing: Dict[str, Dict[str, str]] = {}
    try:
        data = json.loads(out)
        agents = data.get("agents") if isinstance(data, dict) else data
        for agent in agents or []:
            if isinstance(agent, Mapping):
                aid = str(agent.get("id") or agent.get("agent_id") or agent.get("agentId") or "").strip()
                if not aid:
                    continue
                row = {"id": aid}
                model = _agent_model(agent)
                if model:
                    row["model"] = model
                existing[aid] = row
            elif agent:
                aid = str(agent).strip()
                existing[aid] = {"id": aid}
    except (ValueError, TypeError):
        # Fallback: parse the human "- <id>" lines.
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("- "):
                aid = line[2:].split(" ")[0].strip()
                if aid:
                    existing[aid] = {"id": aid}
    return existing


def _existing_agent_ids(runner: CliRunner) -> set[str]:
    return set(_existing_agents(runner))


def sync_persona_agents(
    personas: Sequence[Mapping[str, Any]],
    *,
    runner: Optional[CliRunner] = None,
    soul_writer: Optional[SoulWriter] = None,
    memory_materializer: Optional[MemoryMaterializer] = None,
    skip_persona: Optional[Callable[[Mapping[str, Any]], bool]] = None,
    route_policy_resolver: Optional[Callable[[Mapping[str, Any]], Optional[Mapping[str, Any]]]] = None,
) -> SyncReport:
    """Reconcile each persona into an OpenClaw agent (create missing) + write its
    SOUL.md. Idempotent: existing agents keep their id; the SOUL is rewritten so
    registry edits propagate. Model routing comes from PersonaRuntimeProfile and
    fails closed when the requested model is outside the provider pool.
    """
    run = runner or _default_runner
    write_soul = soul_writer or _default_soul_writer
    report = SyncReport()
    try:
        existing = _existing_agents(run)
    except Exception as exc:  # noqa: BLE001
        report.failed.append({"persona_id": "*", "error": f"agents list failed: {exc}"})
        return report

    for persona in personas:
        try:
            if skip_persona and skip_persona(persona):
                continue
            route_policy = route_policy_resolver(persona) if route_policy_resolver else None
            spec = desired_agent_spec(persona, route_policy=route_policy)
            existing_agent = existing.get(spec.persona_id)
            if existing_agent is not None:
                current_model = str(existing_agent.get("model") or "").strip()
                if current_model and current_model != spec.model:
                    report.failed.append({
                        "persona_id": spec.persona_id,
                        "error": "model_drift_update_unavailable",
                        "current_model": current_model[:120],
                        "desired_model": spec.model[:120],
                        "repair_action": "recreate_openclaw_agent_or_add_set_model_support",
                    })
                    continue
                proc = run([
                    "openclaw", "agents", "set-identity", spec.persona_id,
                    "--name", spec.name,
                ])
                if proc.returncode != 0:
                    report.failed.append({
                        "persona_id": spec.persona_id,
                        "error": (proc.stderr or proc.stdout or "agents set-identity failed")[:300],
                    })
                    continue
                write_soul(spec.workspace, spec.soul)
                report.updated.append(spec.persona_id)
                _try_materialize_memory(report, memory_materializer, spec, persona)
            else:
                proc = run([
                    "openclaw", "agents", "add", spec.persona_id,
                    "--workspace", spec.workspace,
                    "--model", spec.model,
                    "--non-interactive", "--json",
                ])
                if proc.returncode != 0:
                    report.failed.append({
                        "persona_id": spec.persona_id,
                        "error": (proc.stderr or proc.stdout or "agents add failed")[:300],
                    })
                    continue
                write_soul(spec.workspace, spec.soul)
                report.created.append(spec.persona_id)
                _try_materialize_memory(report, memory_materializer, spec, persona)
        except Exception as exc:  # noqa: BLE001
            report.failed.append({"persona_id": _persona_id(persona) or "?", "error": str(exc)[:300]})
    return report


def _try_materialize_memory(
    report: SyncReport,
    memory_materializer: Optional[MemoryMaterializer],
    spec: PersonaAgentSpec,
    persona: Mapping[str, Any],
) -> None:
    if memory_materializer is None:
        return
    try:
        memory_materializer(spec.workspace, persona, spec)
    except Exception as exc:  # noqa: BLE001
        report.failed.append({
            "persona_id": spec.persona_id,
            "error": f"memory_materialization_failed: {exc}"[:300],
        })
        return
    report.memory_materialized.append(spec.persona_id)
