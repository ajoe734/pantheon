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

import contextlib
import fcntl
import json
import os
import subprocess
import threading
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
    fallback_models: List[str] = field(default_factory=list)
    routing_mode: str = "pool_default"

    def model_policy(self) -> Dict[str, Any]:
        return {
            "primary": self.model,
            "fallbacks": list(self.fallback_models),
        }


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
) -> tuple[str, List[str], str, str, int]:
    policy = (
        {"model_routing": dict(route_policy)}
        if isinstance(route_policy, Mapping)
        and "model_routing" not in route_policy
        and "modelRouting" not in route_policy
        and "runtime_profile" not in route_policy
        and "runtimeProfile" not in route_policy
        and ("mode" in route_policy or "routing_mode" in route_policy)
        else route_policy
    )
    profile = build_persona_runtime_profile(persona, route_policy=policy)
    routing = profile.model_routing
    if routing.status != "ready" or not routing.primary_model:
        reason = routing.blocked_reason or routing.reason or "model_routing_degraded"
        invalid = ",".join(routing.invalid_refs) if routing.invalid_refs else "none"
        raise ValueError(
            "persona_runtime_profile_model_routing_degraded "
            f"reason={reason} invalid_refs={invalid} "
            "repair_action=fix_persona_route_policy_or_provider_pool"
        )
    return (
        routing.primary_model,
        list(routing.fallback_models),
        routing.mode,
        profile.workspace_ref,
        profile.sync_generation,
    )


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


# ----------------------------------------------------------------------------
# Active harness (read from the Persona owner record; never synthesized)
# ----------------------------------------------------------------------------

# Tool profile of a trading persona agent: read-only plus proposal submission.
# Order / broker / capital / lifecycle / runtime tools are never granted; the
# deterministic gates of the owning services keep authority over every action.
PERSONA_PROPOSAL_TOOL = "persona_proposal_submit"
PERSONA_AGENT_TOOL_PROFILE: Dict[str, Any] = {
    "allow": ["read", PERSONA_PROPOSAL_TOOL],
    "deny": ["exec", "process", "write", "edit", "apply_patch", "browser", "web_fetch", "web_search"],
    "exec": {"security": "deny", "ask": "always"},
}
_FORBIDDEN_TOOL_MARKERS = (
    "order", "broker", "capital", "lifecycle", "runtime", "bind", "promote",
    "trade", "exec", "write", "edit", "patch", "process", "cron", "deploy",
)


def assert_persona_agent_tool_profile(profile: Mapping[str, Any]) -> None:
    """Fail closed unless the profile is read-only plus proposal submission."""
    allow = [str(item) for item in profile.get("allow") or []]
    if PERSONA_PROPOSAL_TOOL not in allow:
        raise ValueError("persona_agent_tool_profile_missing_proposal_tool")
    for tool in allow:
        if tool in ("read", PERSONA_PROPOSAL_TOOL):
            continue
        raise ValueError(f"persona_agent_tool_profile_forbidden_tool:{tool}")
    exec_policy = profile.get("exec") if isinstance(profile.get("exec"), Mapping) else {}
    if exec_policy.get("security") != "deny":
        raise ValueError("persona_agent_tool_profile_exec_not_denied")


def persona_agent_runtime_policy() -> Dict[str, Any]:
    """`agents.list` entry policy for a trading persona agent (validated copy)."""
    assert_persona_agent_tool_profile(PERSONA_AGENT_TOOL_PROFILE)
    return {"tools": json.loads(json.dumps(PERSONA_AGENT_TOOL_PROFILE))}


def active_harness_version(persona: Mapping[str, Any]) -> tuple[Optional[Dict[str, Any]], str]:
    """Return (active harness version dict, reason). Validation matches the owner's
    runtime profile: schema, persona binding and content digest. Absent or invalid
    data yields (None, reason) - there is no baseline fabricated here."""
    metadata = persona.get("metadata") if isinstance(persona.get("metadata"), Mapping) else {}
    state = metadata.get("persona_harness") if isinstance(metadata.get("persona_harness"), Mapping) else {}
    if not state:
        return None, "harness_not_initialized"
    active = state.get("active_version")
    versions = state.get("versions")
    if type(active) is int and isinstance(versions, list) and 1 <= active <= len(versions):
        from pydantic import ValidationError
        from services.persona.harness import HarnessVersion, digest

        try:
            row = HarnessVersion.model_validate(versions[active - 1])
        except ValidationError:
            return None, "invalid_harness_record"
        if (
            row.persona_id == _persona_id(persona)
            and row.version == active
            and row.content_digest == digest(row.surface.model_dump(mode="json"))
        ):
            return row.model_dump(mode="json"), "ready"
    return None, "invalid_harness_record"


def _render_harness_block(persona: Mapping[str, Any]) -> str:
    version, reason = active_harness_version(persona)
    if version is None:
        return (
            "\n## Active harness\n"
            f"_(No active harness version: `{reason}`. Operate on the registry fields above; "
            "do not assume extra skills, thresholds or workflow steps.)_\n"
        )
    surface = version["surface"]
    lines = [
        "",
        "## Active harness",
        f"- Version: **{version['version']}** (digest `{version['content_digest'][:12]}`)",
    ]
    workflow = surface["workflow"]
    lines.append(f"- Workflow cadence: every {workflow['cadence_seconds']}s")
    lines.extend(f"  {i}. {step}" for i, step in enumerate(workflow["steps"], 1))
    thresholds = surface["cycle_thresholds"]
    lines.append(
        f"- Evidence floor: at least {thresholds['minimum_evidence_count']} evidence items, "
        f"confidence >= {thresholds['minimum_confidence']}"
    )
    forbidden = surface["guardrails"]["forbidden_solo_actions"]
    if forbidden:
        lines.append("- Never do alone: " + ", ".join(forbidden))
    for trigger in surface["consult_policy"]:
        lines.append(f"- Consult before `{trigger['action']}`: {', '.join(trigger['required_roles'])}")
    for tool, guidance in sorted(surface["tool_guidance"].items()):
        lines.append(f"- Tool `{tool}`: {guidance}")
    for skill in surface["skills"]:
        lines.append(f"- Skill `{skill['name']}` (when: {skill['trigger']}): {skill['guidance']}")
    return "\n".join(lines) + "\n"


_MEMORY_SECTION = """## Memory
MEMORY.md and memory/ in this workspace are a **read-only cache** materialized from the
canonical Pantheon Memory Plane (each entry carries its source memory id). They contain
only your own private entries plus institutional entries. Do **not** edit MEMORY.md or
anything under memory/: edits are discarded at the next materialization.
When something is worth keeping, **stage a memory candidate** (summary, evidence, source
event id) in your cycle output / through `{proposal_tool}`; the memory service reviews it.
"""


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

    harness_block = _render_harness_block(persona)
    memory_section = _MEMORY_SECTION.format(proposal_tool=PERSONA_PROPOSAL_TOOL)
    return f"""# SOUL.md — {name} (`{pid}`)

You are **{name}** — a Pantheon trading persona with your own mandate. You are
NOT the Management AI and NOT a generic assistant. You are one persona among many
(each has its own domain); answer only within yours, and defer out-of-scope asks
to the right persona.

## Who you are
{mandate_line}
{strategy_line}{archetype_line}{state_line}
{traits_block}{harness_block}
## How you work every turn (OODA)
When asked to assess or decide, answer **as this persona**, concretely:
- **Observe** — what the signals / market / regime you were given actually show (cite the numbers).
- **Orient** — what it means for *your* mandate and strategy family.
- **Decide** — a concrete stance with a reason and a risk note (instrument, direction, size/stop). If you can't decide from the data, say exactly which signal/market input you'd need — never stall with filler.

## Hard guardrails
- **Paper unless a live capital binding is explicitly active** (`is_real_capital=false` by default). You never place real orders or move capital on your own.
- Stay inside your mandate; don't answer other personas' domains.
- Be direct and quantitative — no filler, no bare 「在，老闆」 / `NO_REPLY`. Reply in 繁體中文 by default.
- Your tools are read-only plus `{PERSONA_PROPOSAL_TOOL}`. You have no order, broker, capital, lifecycle or runtime tool; you only propose, deterministic gates decide.

{memory_section}"""


def desired_agent_spec(
    persona: Mapping[str, Any],
    *,
    route_policy: Optional[Mapping[str, Any]] = None,
) -> PersonaAgentSpec:
    pid = _persona_id(persona)
    if not pid:
        raise ValueError("persona record has no persona_id/id")
    model, fallback_models, routing_mode, workspace, sync_generation = _runtime_profile_model(
        persona, route_policy=route_policy
    )
    return PersonaAgentSpec(
        persona_id=pid,
        name=str(persona.get("name") or pid),
        model=model,
        workspace=workspace,
        soul=build_persona_soul(persona),
        sync_generation=sync_generation,
        fallback_models=fallback_models,
        routing_mode=routing_mode,
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
        raw = agent.get(key)
        if isinstance(raw, Mapping):
            primary = str(raw.get("primary") or raw.get("model") or "").strip()
            if primary:
                return primary
            continue
        value = str(raw or "").strip()
        if value:
            return value
    runtime = agent.get("runtime") if isinstance(agent.get("runtime"), Mapping) else {}
    for key in ("model", "model_id", "modelRef"):
        raw = runtime.get(key)
        if isinstance(raw, Mapping):
            primary = str(raw.get("primary") or raw.get("model") or "").strip()
            if primary:
                return primary
            continue
        value = str(raw or "").strip()
        if value:
            return value
    return ""


def _agent_fallbacks(agent: Mapping[str, Any]) -> Optional[List[str]]:
    raw = agent.get("model")
    if isinstance(raw, Mapping) and isinstance(raw.get("fallbacks"), list):
        return [str(item).strip() for item in raw["fallbacks"] if str(item).strip()]
    if isinstance(agent.get("fallbacks"), list):
        return [str(item).strip() for item in agent["fallbacks"] if str(item).strip()]
    return None


def _reconcile_agent_model_policy(
    run: CliRunner,
    spec: PersonaAgentSpec,
    runtime_policy: Optional[Mapping[str, Any]] = None,
) -> Optional[Dict[str, str]]:
    """Persist `{primary, fallbacks}` into `agents.list` via the existing config owner."""

    get_proc = run(["openclaw", "config", "get", "agents.list", "--json"])
    if get_proc.returncode != 0:
        return {
            "persona_id": spec.persona_id,
            "error": (get_proc.stderr or get_proc.stdout or "agents.list config get failed")[:300],
        }
    raw_stdout = (get_proc.stdout or "").strip()
    if not raw_stdout:
        return {
            "persona_id": spec.persona_id,
            "error": "agents.list config is empty or not valid JSON",
        }
    try:
        agents = json.loads(raw_stdout)
    except (TypeError, ValueError):
        return {
            "persona_id": spec.persona_id,
            "error": "agents.list config is not valid JSON",
        }
    if not isinstance(agents, list):
        return {
            "persona_id": spec.persona_id,
            "error": "agents.list config is not a JSON list",
        }
    target = next(
        (
            item
            for item in agents
            if isinstance(item, dict) and str(item.get("id") or "") == spec.persona_id
        ),
        None,
    )
    if target is None:
        return {
            "persona_id": spec.persona_id,
            "error": "agent missing from agents.list config",
        }
    target_model = _agent_model(target)
    if target_model and target_model != spec.model:
        return {
            "persona_id": spec.persona_id,
            "error": "model_drift_update_unavailable",
            "current_model": target_model[:120],
            "desired_model": spec.model[:120],
            "repair_action": "recreate_openclaw_agent_or_add_set_model_support",
        }
    desired_policy = spec.model_policy()
    policy_extra = json.loads(json.dumps(dict(runtime_policy))) if runtime_policy else {}
    if target.get("model") == desired_policy and all(
        target.get(key) == value for key, value in policy_extra.items()
    ):
        return None
    target["model"] = desired_policy
    target.update(policy_extra)
    set_proc = run([
        "openclaw", "config", "set", "agents.list",
        json.dumps(agents, sort_keys=True, separators=(",", ":")),
        "--strict-json", "--replace",
    ])
    if set_proc.returncode != 0:
        return {
            "persona_id": spec.persona_id,
            "error": (set_proc.stderr or set_proc.stdout or "agents.list model policy write failed")[:300],
        }
    return None


def _existing_agents(runner: CliRunner) -> Dict[str, Dict[str, Any]]:
    proc = runner(["openclaw", "agents", "list", "--json"])
    out = (proc.stdout or "").strip()
    existing: Dict[str, Dict[str, Any]] = {}
    try:
        data = json.loads(out)
        agents = data.get("agents") if isinstance(data, dict) else data
        for agent in agents or []:
            if isinstance(agent, Mapping):
                aid = str(agent.get("id") or agent.get("agent_id") or agent.get("agentId") or "").strip()
                if not aid:
                    continue
                row: Dict[str, Any] = {"id": aid}
                model = _agent_model(agent)
                if model:
                    row["model"] = model
                fallbacks = _agent_fallbacks(agent)
                if fallbacks is not None:
                    row["fallbacks"] = fallbacks
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


_REGISTRY_LOCK_ENV = "PANTHEON_OPENCLAW_AGENT_REGISTRY_LOCK"
_registry_thread_lock = threading.RLock()
_registry_depth = threading.local()


def agent_registry_lock_path() -> str:
    """Lock identity shared by every agents.list writer (CLI, adapter HTTP).

    Lives in the shared OpenClaw state dir (parent of the workspace root), not
    beside any process-local DB, so the standalone reconciler and the adapter
    contend on the same inode.
    """

    override = os.getenv(_REGISTRY_LOCK_ENV, "").strip()
    if override:
        return override
    return os.path.join(os.path.dirname(PERSONA_WORKSPACE_ROOT), ".pantheon-agent-registry.lock")


def _open_shared_registry_lock(path: str) -> int:
    """Open the shared lock for every deployed identity (root adapter, uid1000 gateway).

    Created world read/write so both identities can open it. flock(2) needs no
    write permission, so if write access is still denied on a pre-existing
    root-owned file we lock via a read-only descriptor of the *same inode*.
    Any other failure propagates; callers must not fall back to a local lock.
    """

    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o666)
    except PermissionError:
        return os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        if (os.fstat(fd).st_mode & 0o666) != 0o666:
            os.fchmod(fd, 0o666)
    except OSError:
        pass  # not the owner; read-only/flock access still works on the shared inode
    return fd


@contextlib.contextmanager
def agent_registry_lock() -> Any:
    """Reentrant cross-process exclusive lock around agents.list read-modify-write."""

    with _registry_thread_lock:
        depth = getattr(_registry_depth, "n", 0)
        if depth:
            _registry_depth.n = depth + 1
            try:
                yield
            finally:
                _registry_depth.n -= 1
            return
        path = agent_registry_lock_path()
        # Fail closed: one shared inode only. No private /tmp fallback.
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fd = _open_shared_registry_lock(path)
        handle = os.fdopen(fd, "rb", closefd=True)
        with handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            _registry_depth.n = 1
            try:
                yield
            finally:
                _registry_depth.n = 0
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def sync_persona_agents(
    personas: Sequence[Mapping[str, Any]],
    *,
    runner: Optional[CliRunner] = None,
    soul_writer: Optional[SoulWriter] = None,
    memory_materializer: Optional[MemoryMaterializer] = None,
    skip_persona: Optional[Callable[[Mapping[str, Any]], bool]] = None,
    route_policy_resolver: Optional[Callable[[Mapping[str, Any]], Optional[Mapping[str, Any]]]] = None,
    runtime_policy: Optional[Mapping[str, Any]] = None,
) -> SyncReport:
    """Reconcile personas; the whole read-modify-write runs under the shared registry lock."""
    with agent_registry_lock():
        return _sync_persona_agents_locked(
            personas,
            runner=runner,
            soul_writer=soul_writer,
            memory_materializer=memory_materializer,
            skip_persona=skip_persona,
            route_policy_resolver=route_policy_resolver,
            runtime_policy=runtime_policy,
        )


def _sync_persona_agents_locked(
    personas: Sequence[Mapping[str, Any]],
    *,
    runner: Optional[CliRunner] = None,
    soul_writer: Optional[SoulWriter] = None,
    memory_materializer: Optional[MemoryMaterializer] = None,
    skip_persona: Optional[Callable[[Mapping[str, Any]], bool]] = None,
    route_policy_resolver: Optional[Callable[[Mapping[str, Any]], Optional[Mapping[str, Any]]]] = None,
    runtime_policy: Optional[Mapping[str, Any]] = None,
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
                    "openclaw", "agents", "set-identity", "--agent", spec.persona_id,
                    "--name", spec.name,
                ])
                if proc.returncode != 0:
                    report.failed.append({
                        "persona_id": spec.persona_id,
                        "error": (proc.stderr or proc.stdout or "agents set-identity failed")[:300],
                    })
                    continue
                policy_failure = _reconcile_agent_model_policy(run, spec, runtime_policy)
                if policy_failure is not None:
                    report.failed.append(policy_failure)
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
                policy_failure = _reconcile_agent_model_policy(run, spec, runtime_policy)
                if policy_failure is not None:
                    report.failed.append(policy_failure)
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
