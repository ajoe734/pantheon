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
from typing import Any, Dict, List, Mapping, Optional

try:
    from integrations.openclaw.persona_agent_sync import build_persona_soul as _shared_build_soul
    from integrations.openclaw.persona_agent_sync import persona_agent_runtime_policy as _shared_runtime_policy
    from integrations.openclaw.persona_agent_sync import sync_persona_agents as _shared_sync
    from integrations.openclaw.persona_memory_bridge import materialize_persona_memory_from_api as _shared_materialize_memory
    from services.persona.runtime_profile import build_persona_runtime_profile as _shared_runtime_profile
except Exception:  # noqa: BLE001 - script must stay self-contained in the gateway container
    _shared_build_soul = None
    _shared_runtime_policy = None
    _shared_sync = None
    _shared_materialize_memory = None
    _shared_runtime_profile = None

PERSONA_WORKSPACE_ROOT = "/home/node/.openclaw/workspaces"
DEFAULT_PERSONA_MODEL = "anthropic/claude-opus-4-8"
KNOWN_MODELS = {"anthropic/claude-opus-4-8", "anthropic/claude-sonnet-4-6", "openai/gpt-5.5"}


def persona_id(p: Mapping[str, Any]) -> str:
    return str(p.get("persona_id") or p.get("id") or "").strip()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_model_ref(source: Mapping[str, Any]) -> str:
    for key in ("model", "model_ref", "modelRef", "primary_model", "primaryModel", "preferred_model", "preferredModel"):
        value = str(source.get(key) or "").strip()
        if value:
            return value
    return ""


def _fallback_runtime_profile(p: Mapping[str, Any]) -> Dict[str, Any]:
    metadata = _mapping(p.get("metadata"))
    runtime = _mapping(metadata.get("runtime_profile") or metadata.get("runtimeProfile"))
    routing = _mapping(
        metadata.get("model_routing")
        or metadata.get("modelRouting")
        or runtime.get("model_routing")
        or runtime.get("modelRouting")
    )
    model = _first_model_ref(routing) or _first_model_ref(metadata) or _first_model_ref(p)
    if model and model not in KNOWN_MODELS:
        raise ValueError(
            "persona_runtime_profile_model_routing_degraded "
            f"reason=unknown_model_ref invalid_refs={model} "
            "repair_action=fix_persona_route_policy_or_provider_pool"
        )
    workspace = (
        p.get("workspace_ref")
        or metadata.get("workspace_ref")
        or _mapping(metadata.get("openclaw_agent")).get("workspace_ref")
        or f"{PERSONA_WORKSPACE_ROOT}/{persona_id(p)}"
    )
    return {"model": model or DEFAULT_PERSONA_MODEL, "workspace": str(workspace), "sync_generation": 1}


def runtime_profile(p: Mapping[str, Any]) -> Dict[str, Any]:
    if _shared_runtime_profile is not None:
        profile = _shared_runtime_profile(p).to_dict()
        routing = profile.get("model_routing") or {}
        if routing.get("status") != "ready" or not routing.get("primary_model"):
            reason = routing.get("blocked_reason") or routing.get("reason") or "model_routing_degraded"
            invalid = ",".join(routing.get("invalid_refs") or []) or "none"
            raise ValueError(
                "persona_runtime_profile_model_routing_degraded "
                f"reason={reason} invalid_refs={invalid} "
                "repair_action=fix_persona_route_policy_or_provider_pool"
            )
        return {
            "model": str(routing["primary_model"]),
            "workspace": str(profile["workspace_ref"]),
            "sync_generation": int(profile.get("sync_generation") or 1),
        }
    return _fallback_runtime_profile(p)


def resolve_model(p: Mapping[str, Any]) -> str:
    return runtime_profile(p)["model"]


def resolve_workspace(p: Mapping[str, Any]) -> str:
    return runtime_profile(p)["workspace"]


def build_soul(p: Mapping[str, Any]) -> str:
    """Single shared renderer (integrations/openclaw/persona_agent_sync.py).

    There is deliberately no local fallback: a second renderer would drift from
    the registry/harness contract, so an unavailable library fails closed.
    """
    if _shared_build_soul is None:
        raise RuntimeError("shared_soul_renderer_unavailable")
    return _shared_build_soul(p)


def agent_model(agent: Mapping[str, Any]) -> str:
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


def existing_agents() -> Dict[str, Dict[str, str]]:
    proc = subprocess.run(["openclaw", "agents", "list", "--json"], capture_output=True, text=True, timeout=60)
    existing: Dict[str, Dict[str, str]] = {}
    try:
        data = json.loads((proc.stdout or "").strip())
        agents = data.get("agents") if isinstance(data, dict) else data
        for agent in agents or []:
            if isinstance(agent, Mapping):
                aid = str(agent.get("id") or agent.get("agent_id") or agent.get("agentId") or "").strip()
                if not aid:
                    continue
                row = {"id": aid}
                model = agent_model(agent)
                if model:
                    row["model"] = model
                existing[aid] = row
            elif agent:
                aid = str(agent).strip()
                existing[aid] = {"id": aid}
    except (ValueError, TypeError):
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("- "):
                aid = line[2:].split(" ")[0].strip()
                if aid:
                    existing[aid] = {"id": aid}
    return existing


def existing_agent_ids() -> set[str]:
    return set(existing_agents())


def write_soul(workspace: str, soul: str) -> None:
    os.makedirs(workspace, exist_ok=True)
    with open(os.path.join(workspace, "SOUL.md"), "w", encoding="utf-8") as fh:
        fh.write(soul)


def memory_api_url() -> str:
    return (os.getenv("PANTHEON_MEMORY_API_URL") or os.getenv("PANTHEON_MEMORY_SERVICE_URL") or "").strip()


def memory_actor_roles() -> List[str]:
    raw = os.getenv("PANTHEON_MEMORY_ACTOR_ROLES", "operator")
    roles = [part.strip() for part in raw.split(",") if part.strip()]
    return roles or ["operator"]


def materialize_memory_if_configured(pid: str, workspace: str) -> Optional[Dict[str, Any]]:
    url = memory_api_url()
    if not url:
        return None
    if _shared_materialize_memory is None:
        raise RuntimeError("memory_bridge_module_unavailable")
    result = _shared_materialize_memory(
        memory_api_url=url,
        persona_id=pid,
        workspace=workspace,
        actor_id=os.getenv("PANTHEON_MEMORY_ACTOR_ID", "openclaw-persona-sync"),
        actor_roles=memory_actor_roles(),
        session_id=os.getenv("PANTHEON_MEMORY_SESSION_ID") or f"openclaw-memory-sync-{pid}",
        query=os.getenv(
            "PANTHEON_OPENCLAW_MEMORY_QUERY",
            "recent lessons, preferences, risks, and institutional context for this persona",
        ),
        limit=int(os.getenv("PANTHEON_OPENCLAW_MEMORY_LIMIT", "8")),
        auth_token=os.getenv("PANTHEON_MEMORY_AUTH_TOKEN") or None,
    )
    return result.to_dict()


def record_memory_materialization(report: Dict[str, Any], pid: str, workspace: str) -> None:
    try:
        result = materialize_memory_if_configured(pid, workspace)
    except Exception as exc:  # noqa: BLE001
        report["failed"].append({"persona_id": pid, "error": f"memory_materialization_failed: {exc}"[:300]})
        return
    if result is not None:
        report["memory_materialized"].append(pid)


def reconcile(personas: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """Delegate to the tested library reconciler (create/update, SOUL, memory)."""
    if _shared_sync is None or _shared_runtime_policy is None:
        return {
            "created": [], "updated": [], "unchanged": [], "memory_materialized": [],
            "failed": [{"persona_id": "*", "error": "shared_reconciler_unavailable"}],
            "counts": {"failed": 1},
        }

    def materializer(workspace: str, persona: Mapping[str, Any], spec: Any) -> Any:
        if not memory_api_url():
            raise RuntimeError("memory_api_unconfigured")
        return materialize_memory_if_configured(spec.persona_id, workspace)

    return _shared_sync(
        list(personas),
        memory_materializer=materializer,
        runtime_policy=_shared_runtime_policy(),
    ).to_dict()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--personas-file", default=None)
    ap.add_argument(
        "--persona-id", action="append", default=[],
        help="backfill only these persona ids (repeatable); default: every supplied persona",
    )
    args = ap.parse_args()
    raw = open(args.personas_file, encoding="utf-8").read() if args.personas_file else sys.stdin.read()
    data = json.loads(raw)
    personas = data.get("personas") if isinstance(data, dict) else data
    selected = list(personas or [])
    if args.persona_id:
        wanted = set(args.persona_id)
        selected = [p for p in selected if persona_id(p) in wanted]
        missing = sorted(wanted - {persona_id(p) for p in selected})
        if missing:
            print(json.dumps({"failed": [{"persona_id": m, "error": "persona_not_in_input"} for m in missing]}))
            return 1
    report = reconcile(selected)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
