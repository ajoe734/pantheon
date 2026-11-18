"""Persona runtime profile contract.

The runtime profile is a read-only contract between the persona registry,
OpenClaw agent reconciliation, and the memory plane. It deliberately exposes
provider/model pool references, never provider auth material.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional, Sequence

from services.persona.model_chain import cli_runtime_refs, configured_chain


PERSONA_WORKSPACE_ROOT = "/home/node/.openclaw/workspaces"
# The model chain (primary first) is not named here: it comes from
# services.persona.model_chain (OPENCLAW_MODEL_CHAIN, else the one default
# file) or, at sync time, from the gateway's own agents.defaults.model. Models
# on a CLI runtime backend lack host identity for persona tools, so they are
# never an automatic persona fallback (`cli_blocked`, read from the gateway).
ROUTING_MODES = frozenset(
    {
        "pool_default",
        "preferred_pool_model",
        "hard_pin",
        "fallback",
    }
)


@dataclass(frozen=True)
class ModelRouting:
    mode: str
    status: str
    primary_model: Optional[str]
    fallback_models: list[str]
    provider_pool_refs: list[str]
    selected_provider_pool_ref: Optional[str]
    requested_models: list[str]
    invalid_refs: list[str]
    reason: str
    blocked_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MemoryPolicy:
    source: str
    read_endpoint: str
    writeback_endpoint: str
    canonical_store: str
    materialization: str
    workspace_files: list[str]
    cache_mutation_policy: str
    direct_session_writes: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PersonaRuntimeProfile:
    persona_id: str
    workspace_ref: str
    model_routing: ModelRouting
    memory_policy: MemoryPolicy
    sync_generation: int
    source_refs: list[dict[str, str]]
    harness: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "persona_id": self.persona_id,
            "workspace_ref": self.workspace_ref,
            "model_routing": self.model_routing.to_dict(),
            "memory_policy": self.memory_policy.to_dict(),
            "sync_generation": self.sync_generation,
            "source_refs": json.loads(json.dumps(self.source_refs)),
            "harness": dict(self.harness),
        }


def build_persona_runtime_profile(
    persona: Mapping[str, Any],
    *,
    route_policy: Optional[Mapping[str, Any]] = None,
    provider_pool: Optional[Mapping[str, Any]] = None,
    model_chain: Optional[Sequence[str]] = None,
) -> PersonaRuntimeProfile:
    """Build the fail-closed persona runtime profile exposed by BFF."""
    persona_id = _persona_id(persona)
    if not persona_id:
        raise ValueError("persona runtime profile requires persona_id or id")

    chain = list(model_chain) if model_chain else configured_chain()
    pool_refs = _pool_refs(provider_pool, chain)
    cli_blocked = cli_runtime_refs(provider_pool)
    workspace_ref = _workspace_ref(persona, persona_id)
    routing_source = _routing_source(persona, route_policy)
    model_routing = _resolve_model_routing(routing_source, pool_refs, chain, cli_blocked)

    return PersonaRuntimeProfile(
        persona_id=persona_id,
        workspace_ref=workspace_ref,
        model_routing=model_routing,
        memory_policy=_memory_policy(),
        sync_generation=_sync_generation(persona, route_policy),
        harness=_harness_summary(persona),
        source_refs=[
            {"surface": "persona_registry", "ref": f"persona:{persona_id}"},
            {
                "surface": "persona_route_policy",
                "ref": f"persona_route_policy:{persona_id}",
                "status": "present" if route_policy else "missing",
            },
            {
                "surface": "memory_store",
                "ref": "services.memory.persona_memory_store.PersonaMemoryStore",
            },
            {"surface": "openclaw_workspace", "ref": workspace_ref},
        ],
    )


def _persona_id(persona: Mapping[str, Any]) -> str:
    return str(persona.get("persona_id") or persona.get("id") or "").strip()


def _pool_refs(provider_pool: Optional[Mapping[str, Any]], chain: Sequence[str]) -> set[str]:
    if not provider_pool:
        return set(chain)
    refs: set[str] = set()
    for key, value in provider_pool.items():
        if isinstance(value, Mapping):
            ref = (
                value.get("provider_pool_ref")
                or value.get("model_ref")
                or value.get("model")
                or value.get("id")
                or key
            )
        else:
            ref = key
        text = str(ref or "").strip()
        if text:
            refs.add(text)
    return refs


def _workspace_ref(persona: Mapping[str, Any], persona_id: str) -> str:
    metadata = _mapping(persona.get("metadata"))
    openclaw_agent = _mapping(metadata.get("openclaw_agent") or persona.get("openclaw_agent"))
    explicit = (
        persona.get("workspace_ref")
        or metadata.get("workspace_ref")
        or openclaw_agent.get("workspace_ref")
        or openclaw_agent.get("workspace")
    )
    return str(explicit or f"{PERSONA_WORKSPACE_ROOT}/{persona_id}").strip()


def _sync_generation(
    persona: Mapping[str, Any],
    route_policy: Optional[Mapping[str, Any]],
) -> int:
    candidates = [
        _mapping(route_policy).get("sync_generation"),
        _mapping(_mapping(route_policy).get("runtime_profile")).get("sync_generation"),
        _mapping(persona.get("metadata")).get("sync_generation"),
        _mapping(_mapping(persona.get("metadata")).get("runtime_profile")).get("sync_generation"),
        persona.get("sync_generation"),
    ]
    for candidate in candidates:
        try:
            parsed = int(candidate)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return 1


def _routing_source(
    persona: Mapping[str, Any],
    route_policy: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    policy = _mapping(route_policy)
    policy_runtime = _mapping(policy.get("runtime_profile") or policy.get("runtimeProfile"))
    policy_routing = _mapping(
        policy.get("model_routing")
        or policy.get("modelRouting")
        or policy_runtime.get("model_routing")
        or policy_runtime.get("modelRouting")
    )
    if policy_routing:
        return {**policy_routing, "_source": "route_policy"}
    policy_loose = _loose_routing(policy)
    if policy_loose:
        policy_loose["_source"] = "route_policy"
        return policy_loose

    metadata = _mapping(persona.get("metadata"))
    metadata_runtime = _mapping(metadata.get("runtime_profile") or metadata.get("runtimeProfile"))
    metadata_routing = _mapping(
        metadata.get("model_routing")
        or metadata.get("modelRouting")
        or metadata_runtime.get("model_routing")
        or metadata_runtime.get("modelRouting")
    )
    if metadata_routing:
        return {**metadata_routing, "_source": "persona_metadata"}
    metadata_loose = _loose_routing(metadata)
    if metadata_loose:
        metadata_loose["_source"] = "persona_metadata"
        return metadata_loose

    persona_loose = _loose_routing(persona)
    if persona_loose:
        persona_loose["_source"] = "persona_registry"
        return persona_loose
    return {"mode": "pool_default", "_source": "default"}


def _loose_routing(source: Mapping[str, Any]) -> dict[str, Any]:
    model = (
        source.get("preferred_model")
        or source.get("preferredModel")
        or source.get("model_ref")
        or source.get("modelRef")
        or source.get("model")
        or source.get("primary_model")
        or source.get("primaryModel")
    )
    if not str(model or "").strip():
        return {}
    return {"mode": "preferred_pool_model", "model": model}


def _resolve_model_routing(
    source: Mapping[str, Any],
    pool_refs: set[str],
    chain: Sequence[str],
    cli_blocked: set[str],
) -> ModelRouting:
    mode = str(source.get("mode") or source.get("routing_mode") or "pool_default").strip()
    if mode not in ROUTING_MODES:
        return _blocked_routing(
            mode=mode or "unknown",
            pool_refs=pool_refs,
            chain=chain,
            requested=[],
            invalid=[mode or "unknown"],
            reason="invalid_routing_mode",
            blocked_reason="invalid_routing_mode",
        )

    if mode == "pool_default":
        default_primary = next((m for m in chain if m in pool_refs), "")
        if not default_primary:
            return _blocked_routing(
                mode=mode,
                pool_refs=pool_refs,
                chain=chain,
                requested=[],
                invalid=[],
                reason="model_chain_empty",
                blocked_reason="model_chain_empty",
            )
        return _ready_routing(
            mode="pool_default",
            pool_refs=pool_refs,
            chain=chain,
            primary=default_primary,
            fallback=_fallback_after(default_primary, pool_refs, chain, cli_blocked),
            requested=[],
            reason="pool_default",
        )

    if mode in {"preferred_pool_model", "hard_pin"}:
        requested_model = _first_model_ref(source)
        requested = [requested_model] if requested_model else []
        invalid = [model for model in requested if model not in pool_refs]
        if not requested_model:
            return _blocked_routing(
                mode=mode,
                pool_refs=pool_refs,
                chain=chain,
                requested=[],
                invalid=[],
                reason="missing_model_ref",
                blocked_reason="missing_model_ref",
            )
        if invalid:
            return _blocked_routing(
                mode=mode,
                pool_refs=pool_refs,
                chain=chain,
                requested=requested,
                invalid=invalid,
                reason="invalid_model_ref",
                blocked_reason="unknown_model_ref",
            )
        return _ready_routing(
            mode=mode,
            pool_refs=pool_refs,
            chain=chain,
            primary=requested_model,
            fallback=[] if mode == "hard_pin" else _fallback_after(requested_model, pool_refs, chain, cli_blocked),
            requested=requested,
            reason=mode,
        )

    requested = _fallback_model_refs(source)
    invalid = [model for model in requested if model not in pool_refs]
    if not requested:
        return _blocked_routing(
            mode="fallback",
            pool_refs=pool_refs,
            chain=chain,
            requested=[],
            invalid=[],
            reason="missing_fallback_order",
            blocked_reason="missing_fallback_order",
        )
    if invalid:
        return _blocked_routing(
            mode="fallback",
            pool_refs=pool_refs,
            chain=chain,
            requested=requested,
            invalid=invalid,
            reason="invalid_model_ref",
            blocked_reason="unknown_model_ref",
        )
    return _ready_routing(
        mode="fallback",
        pool_refs=pool_refs,
        chain=chain,
        primary=requested[0],
        fallback=requested[1:],
        requested=requested,
        reason="fallback_order",
    )


def _first_model_ref(source: Mapping[str, Any]) -> str:
    value = (
        source.get("model")
        or source.get("model_ref")
        or source.get("modelRef")
        or source.get("primary_model")
        or source.get("primaryModel")
        or source.get("preferred_model")
        or source.get("preferredModel")
    )
    return str(value or "").strip()


def _fallback_model_refs(source: Mapping[str, Any]) -> list[str]:
    values = (
        source.get("fallback_models")
        or source.get("fallbackModels")
        or source.get("ordered_fallback")
        or source.get("orderedFallback")
        or source.get("fallback_order")
        or source.get("fallbackOrder")
        or source.get("models")
        or source.get("model_refs")
        or source.get("modelRefs")
    )
    return _string_list(values)


def _fallback_after(
    primary: str,
    pool_refs: set[str],
    chain: Sequence[str],
    cli_blocked: set[str],
) -> list[str]:
    """Fallbacks are the chain entries after the primary; nothing outside it."""
    return [
        model
        for model in chain
        if model != primary and model in pool_refs and model not in cli_blocked
    ]


def _ready_routing(
    *,
    mode: str,
    pool_refs: set[str],
    primary: str,
    fallback: list[str],
    requested: list[str],
    reason: str,
    chain: Sequence[str] = (),
) -> ModelRouting:
    return ModelRouting(
        mode=mode,
        status="ready",
        primary_model=primary,
        fallback_models=fallback,
        provider_pool_refs=_ordered_pool_refs(pool_refs, chain),
        selected_provider_pool_ref=_provider_pool_ref(primary),
        requested_models=requested,
        invalid_refs=[],
        reason=reason,
    )


def _blocked_routing(
    *,
    mode: str,
    pool_refs: set[str],
    requested: list[str],
    invalid: list[str],
    reason: str,
    blocked_reason: str,
    chain: Sequence[str] = (),
) -> ModelRouting:
    return ModelRouting(
        mode=mode,
        status="degraded",
        primary_model=None,
        fallback_models=[],
        provider_pool_refs=_ordered_pool_refs(pool_refs, chain),
        selected_provider_pool_ref=None,
        requested_models=requested,
        invalid_refs=invalid,
        reason=reason,
        blocked_reason=blocked_reason,
    )


def _ordered_pool_refs(pool_refs: set[str], chain: Sequence[str]) -> list[str]:
    ordered = [model for model in chain if model in pool_refs]
    ordered.extend(sorted(model for model in pool_refs if model not in ordered))
    return [_provider_pool_ref(model) for model in ordered]


def _provider_pool_ref(model_ref: str) -> str:
    return f"openclaw_provider_pool:{model_ref}"


def _memory_policy() -> MemoryPolicy:
    return MemoryPolicy(
        source="canonical_persona_memory_plane",
        read_endpoint="GET /api/memory/retrieve?scope=persona",
        writeback_endpoint="POST /api/memory/writebacks/persona",
        canonical_store="services.memory.persona_memory_store.PersonaMemoryStore",
        materialization="openclaw_workspace_cache",
        workspace_files=["SOUL.md", "MEMORY.md", "USER.md", "memory/"],
        cache_mutation_policy="memory_bridge_only",
        direct_session_writes=False,
    )


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _harness_summary(persona: Mapping[str, Any]) -> dict[str, Any]:
    state = _mapping(_mapping(persona.get("metadata")).get("persona_harness"))
    active = state.get("active_version")
    versions = state.get("versions")
    if type(active) is int and isinstance(versions, list) and 1 <= active <= len(versions):
        from services.persona.harness import HarnessVersion, digest
        from pydantic import ValidationError
        try:
            row = HarnessVersion.model_validate(versions[active - 1])
            if (row.persona_id == _persona_id(persona) and row.version == active
                    and row.content_digest == digest(row.surface.model_dump(mode="json"))):
                return {"status": "ready", "active_version": active,
                        "content_digest": row.content_digest, "reason": None}
        except ValidationError:
            pass
    return {"status": "unavailable", "active_version": None, "content_digest": None,
            "reason": "invalid_harness_record" if state else "harness_not_initialized"}
