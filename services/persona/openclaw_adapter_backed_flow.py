"""Adapter-backed persona -> OpenClaw -> persona validation flow.

This module proves the persona side of the OpenClaw OSS interaction is usable:
the persona request is sent through ``OpenClawOpsClient`` to the gateway
adapter, the provider response is consumed by persona reasoning, and the final
candidate/scorer/decision trace cites that provider response. It intentionally
rejects artifacts that merely look like OpenClaw output but have no adapter
invocation evidence.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from services.persona.ooda_cycle_runtime import ALPHA_SEED_SOURCES, AlphaSeedSource


REPO_ROOT = Path(__file__).resolve().parents[2]
BFF_DIR = REPO_ROOT / "services" / "control-plane" / "bff"

OPENCLAW_ADAPTER_PROVIDER_PATH = "/api/openclaw-adapter/assistant/providers/openclaw/invoke"
OPENCLAW_ADAPTER_PROVIDER = "openclaw"
OPENCLAW_OPS_CLIENT_CLASS = "OpenClawOpsClient"
FLOW_SCHEMA_VERSION = "persona-openclaw-adapter-backed-flow.v1"
OODA_ITERATION_SCHEMA_VERSION = "persona-openclaw-adapter-backed-ooda-iteration.v1"

OSS_COMPONENTS = (
    "openclaw",
    "dspy",
    "imitation",
    "trl",
    "qlib",
    "vectorbt",
    "statsmodels",
    "quantlib",
    "finrl",
    "rllib",
    "ray_tune",
    "mlflow",
    "wandb",
    "lean_handoff",
)


@dataclass(frozen=True)
class DepthProfile:
    depth_id: str
    ooda_phase: str
    validation_focus: str
    expected_persona_work: str


@dataclass(frozen=True)
class BreadthScenario:
    scenario_id: str
    primary_component: str
    related_components: tuple[str, ...]
    decision_action: str
    realistic_trigger: str


@dataclass(frozen=True)
class AdapterBackedPersonaFlowSpec:
    case_no: int
    case_id: str
    assertion_label: str
    depth: DepthProfile
    scenario: BreadthScenario
    seed: AlphaSeedSource


class AssistantProviderClient(Protocol):
    @property
    def base_url(self) -> str: ...

    @property
    def configured(self) -> bool: ...

    def invoke_assistant_provider(self, **kwargs: Any) -> dict[str, Any]: ...


ClientFactory = Callable[[AdapterBackedPersonaFlowSpec], AssistantProviderClient]


DEPTH_PROFILES: tuple[DepthProfile, ...] = (
    DepthProfile(
        "shallow_provider_ping",
        "observe",
        "adapter route, operator header, and provider envelope",
        "confirm OpenClaw can answer a seed-backed persona request",
    ),
    DepthProfile(
        "context_pack_memory",
        "observe",
        "context pack, alpha seed refs, and previous response refs",
        "bind OpenClaw response into session memory inputs",
    ),
    DepthProfile(
        "oss_feedback_trigger",
        "orient",
        "OSS response triggers a persona follow-up step",
        "interpret an upstream OSS result before candidate generation",
    ),
    DepthProfile(
        "candidate_generation",
        "decide",
        "provider response drives candidate generation",
        "create candidate actions only after OpenClaw provider response",
    ),
    DepthProfile(
        "scoring_lineage",
        "decide",
        "scorer inputs cite provider response and alpha seed lineage",
        "score candidates from the OpenClaw response and seed evidence",
    ),
    DepthProfile(
        "decision_trace",
        "decide",
        "selected candidate carries provider evidence refs",
        "emit a decision trace with provider-backed evidence",
    ),
    DepthProfile(
        "cross_component_handoff",
        "act",
        "decision references downstream component handoff context",
        "prepare a next-action handoff without bypassing OpenClaw evidence",
    ),
    DepthProfile(
        "runtime_feedback_loop",
        "learn",
        "provider response loops back into persona state",
        "turn adapter feedback into a next validation or learning step",
    ),
    DepthProfile(
        "multi_oss_arbitration",
        "orient",
        "OpenClaw adjudicates realistic multi-OSS disagreement",
        "resolve a component disagreement from provider-backed reasoning",
    ),
    DepthProfile(
        "lean_ready_decision_packet",
        "act",
        "decision packet is ready for LEAN handoff review",
        "carry provider-backed candidate selection into runtime handoff refs",
    ),
)

BREADTH_SCENARIOS: tuple[BreadthScenario, ...] = (
    BreadthScenario(
        "alpha_seed_to_qlib_vectorbt",
        "vectorbt",
        ("qlib", "vectorbt", "mlflow"),
        "promote_backtest_candidate",
        "Qlib proposes an alpha seed and vectorbt returns a backtest needing persona judgment.",
    ),
    BreadthScenario(
        "tracker_to_reflection",
        "mlflow",
        ("mlflow", "wandb", "openclaw"),
        "record_experiment_reflection",
        "Experiment tracking reports a run delta that should alter persona memory.",
    ),
    BreadthScenario(
        "dspy_prompt_candidate",
        "dspy",
        ("dspy", "vectorbt", "mlflow"),
        "revise_prompt_candidate",
        "A DSPy prompt bundle changes how the persona describes the alpha thesis.",
    ),
    BreadthScenario(
        "imitation_policy_candidate",
        "imitation",
        ("imitation", "finrl", "rllib"),
        "compare_policy_candidate",
        "Imitation learning emits a behavior candidate that must be compared with RL output.",
    ),
    BreadthScenario(
        "trl_alignment_feedback",
        "trl",
        ("trl", "openclaw", "wandb"),
        "apply_alignment_feedback",
        "TRL feedback changes candidate preference and requires persona re-scoring.",
    ),
    BreadthScenario(
        "statsmodels_regime_read",
        "statsmodels",
        ("statsmodels", "quantlib", "vectorbt"),
        "attach_regime_interpretation",
        "Statsmodels flags a regime shift that should change backtest interpretation.",
    ),
    BreadthScenario(
        "quantlib_risk_review",
        "quantlib",
        ("quantlib", "statsmodels", "lean_handoff"),
        "tighten_risk_handoff",
        "QuantLib reprices risk and should alter the handoff review payload.",
    ),
    BreadthScenario(
        "rl_tuning_convergence",
        "ray_tune",
        ("finrl", "rllib", "ray_tune", "mlflow"),
        "select_rl_training_candidate",
        "Ray Tune ranks RL trials and the persona must select a follow-up candidate.",
    ),
    BreadthScenario(
        "lean_packet_readiness",
        "lean_handoff",
        ("lean_handoff", "vectorbt", "mlflow"),
        "prepare_lean_handoff_review",
        "A LEAN handoff packet is available but needs provider-backed persona review.",
    ),
    BreadthScenario(
        "oss_disagreement_arbitration",
        "openclaw",
        ("openclaw", "qlib", "vectorbt", "statsmodels", "quantlib"),
        "arbitrate_oss_disagreement",
        "Alpha, backtest, and risk evidence disagree in a realistic operator workflow.",
    ),
)


def load_openclaw_ops_client_class() -> type:
    """Load the BFF OpenClawOpsClient despite the hyphenated service path."""

    bff_dir = str(BFF_DIR)
    if bff_dir not in sys.path:
        sys.path.insert(0, bff_dir)
    from openclaw_ops_client import OpenClawOpsClient  # noqa: PLC0415

    return OpenClawOpsClient


def stable_json_hash(payload: Mapping[str, Any] | Sequence[Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_openclaw_adapter_backed_specs(
    *, case_count: int = 100
) -> tuple[AdapterBackedPersonaFlowSpec, ...]:
    """Build non-repeated cases ordered shallow-to-deep and broad within each depth."""

    if case_count < 1:
        raise ValueError("case_count must be positive")

    specs: list[AdapterBackedPersonaFlowSpec] = []
    for index in range(case_count):
        case_no = index + 1
        depth = DEPTH_PROFILES[min(index // len(BREADTH_SCENARIOS), len(DEPTH_PROFILES) - 1)]
        scenario = BREADTH_SCENARIOS[index % len(BREADTH_SCENARIOS)]
        seed = ALPHA_SEED_SOURCES[index % len(ALPHA_SEED_SOURCES)]
        case_id = f"persona-openclaw-adapter-{case_no:03d}"
        specs.append(
            AdapterBackedPersonaFlowSpec(
                case_no=case_no,
                case_id=case_id,
                assertion_label=(
                    f"{depth.depth_id}:{scenario.scenario_id}:{seed.key}:{case_no:03d}"
                ),
                depth=depth,
                scenario=scenario,
                seed=seed,
            )
        )
    return tuple(specs)


def run_openclaw_adapter_backed_persona_flow_validations(
    *,
    case_count: int = 100,
    client_factory: ClientFactory | None = None,
) -> dict[str, Any]:
    """Run the full persona request -> OpenClaw adapter -> persona decision chain."""

    specs = build_openclaw_adapter_backed_specs(case_count=case_count)
    cases = [
        run_openclaw_adapter_backed_persona_case(
            spec,
            client=_client_for_spec(spec, client_factory=client_factory),
        )
        for spec in specs
    ]
    validations = [validate_openclaw_adapter_backed_persona_case(case) for case in cases]
    passed = [item for item in validations if item["passed"]]
    covered_related_components = sorted(
        {
            component
            for case in cases
            for component in case["triggering_oss_response"]["related_components"]
        }
    )
    summary = {
        "schema_version": FLOW_SCHEMA_VERSION,
        "case_count": len(cases),
        "passed_count": len(passed),
        "adapter_invocation_count": sum(
            1 for case in cases if case.get("adapter_exchange", {}).get("invoked") is True
        ),
        "provider_response_count": sum(1 for case in cases if case.get("provider_response", {}).get("ref")),
        "unique_assertion_label_count": len({case["assertion_label"] for case in cases}),
        "unique_request_hash_count": len(
            {case["adapter_exchange"]["request_hash"] for case in cases}
        ),
        "depth_ids": [depth.depth_id for depth in DEPTH_PROFILES],
        "scenario_ids": [scenario.scenario_id for scenario in BREADTH_SCENARIOS],
        "covered_related_components": covered_related_components,
        "all_oss_components_covered": set(OSS_COMPONENTS).issubset(set(covered_related_components)),
        "alpha_seed_keys": sorted({case["alpha_seed"]["key"] for case in cases}),
    }
    return {
        "schema_version": FLOW_SCHEMA_VERSION,
        "summary": summary,
        "cases": cases,
        "validations": validations,
    }


def run_openclaw_adapter_backed_ooda_iteration_validations(
    *,
    cycle_count: int = 100,
    episode_id: str = "persona-openclaw-ooda-episode-001",
    client_factory: ClientFactory | None = None,
) -> dict[str, Any]:
    """Run one sequential adapter-backed OODA episode across many feedback cycles."""

    specs = build_openclaw_adapter_backed_specs(case_count=cycle_count)
    cycles: list[dict[str, Any]] = []
    previous_cycle: dict[str, Any] | None = None
    for cycle_no, spec in enumerate(specs, start=1):
        cycle = run_openclaw_adapter_backed_persona_case(
            spec,
            client=_client_for_spec(spec, client_factory=client_factory),
            episode_id=episode_id,
            cycle_no=cycle_no,
            previous_cycle=previous_cycle,
        )
        cycles.append(cycle)
        previous_cycle = cycle

    case_validations = [validate_openclaw_adapter_backed_persona_case(cycle) for cycle in cycles]
    episode_validation = validate_openclaw_adapter_backed_ooda_episode(
        {
            "schema_version": OODA_ITERATION_SCHEMA_VERSION,
            "episode_id": episode_id,
            "cycles": cycles,
        }
    )
    covered_related_components = {
        component
        for cycle in cycles
        for component in cycle["triggering_oss_response"]["related_components"]
    }
    summary = {
        "schema_version": OODA_ITERATION_SCHEMA_VERSION,
        "episode_id": episode_id,
        "cycle_count": len(cycles),
        "passed_case_count": sum(1 for item in case_validations if item["passed"]),
        "episode_passed": episode_validation["passed"],
        "adapter_invocation_count": sum(
            1 for cycle in cycles if cycle.get("adapter_exchange", {}).get("invoked") is True
        ),
        "provider_response_count": sum(1 for cycle in cycles if cycle.get("provider_response", {}).get("ref")),
        "iteration_link_count": sum(
            1
            for cycle in cycles
            if cycle.get("ooda_iteration", {}).get("request_consumes_previous_cycle") is True
        ),
        "unique_request_hash_count": len(
            {cycle["adapter_exchange"]["request_hash"] for cycle in cycles}
        ),
        "covered_related_components": sorted(covered_related_components),
        "all_oss_components_covered": set(OSS_COMPONENTS).issubset(covered_related_components),
    }
    return {
        "schema_version": OODA_ITERATION_SCHEMA_VERSION,
        "summary": summary,
        "episode_id": episode_id,
        "cycles": cycles,
        "case_validations": case_validations,
        "episode_validation": episode_validation,
    }


def run_openclaw_adapter_backed_persona_case(
    spec: AdapterBackedPersonaFlowSpec,
    *,
    client: AssistantProviderClient | None = None,
    episode_id: str | None = None,
    cycle_no: int | None = None,
    previous_cycle: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one adapter-backed case and return the full persona decision record."""

    provider_client = client or _client_for_spec(spec, client_factory=None)
    iteration_context = _iteration_context_for_spec(
        spec,
        episode_id=episode_id,
        cycle_no=cycle_no,
        previous_cycle=previous_cycle,
    )
    persona_request = _persona_request_for_spec(spec, iteration_context=iteration_context)
    adapter_body = _adapter_body_for_persona_request(persona_request)
    request_hash = stable_json_hash(adapter_body)
    persona_request["adapter_request_hash"] = request_hash
    metadata = dict(adapter_body["metadata"])
    metadata["adapter_request_hash"] = request_hash
    adapter_body["metadata"] = metadata

    provider_payload = provider_client.invoke_assistant_provider(
        provider=OPENCLAW_ADAPTER_PROVIDER,
        mode=adapter_body["mode"],
        prompt=adapter_body["prompt"],
        context_pack=copy.deepcopy(adapter_body["context_pack"]),
        operator_id=persona_request["operator_id"],
        metadata=copy.deepcopy(metadata),
        trace_id=persona_request["trace_id"],
        messages=copy.deepcopy(adapter_body["messages"]),
        attachments=copy.deepcopy(adapter_body["attachments"]),
    )
    adapter_body["metadata"] = metadata
    request_hash = stable_json_hash(adapter_body)
    persona_request["adapter_request_hash"] = request_hash

    provider_response = _provider_response_from_payload(spec, provider_payload)
    persona_request["openclaw_provider_response_ref"] = provider_response["ref"]
    adapter_exchange = _adapter_exchange(
        spec,
        client=provider_client,
        adapter_body=adapter_body,
        request_hash=request_hash,
        provider_payload=provider_payload,
        provider_response=provider_response,
        persona_request=persona_request,
    )
    triggering_oss_response = _triggering_oss_response(spec)
    persona_reasoning = _persona_reasoning(
        spec,
        persona_request=persona_request,
        provider_response=provider_response,
        triggering_oss_response=triggering_oss_response,
    )
    candidate_generation = _candidate_generation(
        spec,
        persona_request=persona_request,
        provider_response=provider_response,
        triggering_oss_response=triggering_oss_response,
        persona_reasoning=persona_reasoning,
    )
    scorer = _score_candidates(
        spec,
        provider_response=provider_response,
        triggering_oss_response=triggering_oss_response,
        candidates=candidate_generation["candidates"],
    )
    decision_trace = _decision_trace(
        spec,
        persona_request=persona_request,
        provider_response=provider_response,
        triggering_oss_response=triggering_oss_response,
        candidate_generation=candidate_generation,
        scorer=scorer,
    )
    case = {
        "schema_version": FLOW_SCHEMA_VERSION,
        "case_id": spec.case_id,
        "case_no": spec.case_no,
        "assertion_label": spec.assertion_label,
        "depth": {
            "depth_id": spec.depth.depth_id,
            "ooda_phase": spec.depth.ooda_phase,
            "validation_focus": spec.depth.validation_focus,
            "expected_persona_work": spec.depth.expected_persona_work,
        },
        "scenario": {
            "scenario_id": spec.scenario.scenario_id,
            "primary_component": spec.scenario.primary_component,
            "related_components": list(spec.scenario.related_components),
            "decision_action": spec.scenario.decision_action,
            "realistic_trigger": spec.scenario.realistic_trigger,
        },
        "alpha_seed": _seed_to_dict(spec.seed),
        "ooda_iteration": iteration_context,
        "validation_plan": _validation_plan(spec),
        "persona_request": persona_request,
        "adapter_exchange": adapter_exchange,
        "provider_response": provider_response,
        "triggering_oss_response": triggering_oss_response,
        "persona_reasoning": persona_reasoning,
        "candidate_generation": candidate_generation,
        "scorer": scorer,
        "decision_trace": decision_trace,
    }
    case["validation"] = validate_openclaw_adapter_backed_persona_case(case)
    return case


def validate_openclaw_adapter_backed_ooda_episode(episode: Mapping[str, Any]) -> dict[str, Any]:
    """Validate that sequential OODA cycles consume the previous cycle's response."""

    errors: list[str] = []
    cycles = episode.get("cycles")
    if not isinstance(cycles, list) or not cycles:
        return {
            "episode_id": str(episode.get("episode_id") or ""),
            "passed": False,
            "errors": ["episode.cycles are required"],
        }

    episode_id = str(episode.get("episode_id") or "")
    request_hashes: set[str] = set()
    for index, cycle in enumerate(cycles):
        if not isinstance(cycle, Mapping):
            errors.append(f"cycle {index + 1} must be a mapping")
            continue
        case_validation = validate_openclaw_adapter_backed_persona_case(cycle)
        if not case_validation["passed"]:
            errors.extend(f"{cycle.get('case_id')}: {error}" for error in case_validation["errors"])

        iteration = _mapping(cycle.get("ooda_iteration"))
        persona_request = _mapping(cycle.get("persona_request"))
        context_pack = _mapping(persona_request.get("context_pack"))
        adapter_body = _mapping(_mapping(cycle.get("adapter_exchange")).get("body"))
        adapter_context = _mapping(adapter_body.get("context_pack"))
        metadata = _mapping(adapter_body.get("metadata"))
        provider = _mapping(cycle.get("provider_response"))
        decision_trace = _mapping(cycle.get("decision_trace"))
        ooda = _mapping(decision_trace.get("ooda"))
        observe = _mapping(ooda.get("observe"))
        orient = _mapping(ooda.get("orient"))
        decide = _mapping(ooda.get("decide"))
        act = _mapping(ooda.get("act"))
        reasoning = _mapping(cycle.get("persona_reasoning"))
        generation = _mapping(cycle.get("candidate_generation"))
        selected = _mapping(decision_trace.get("selected_candidate"))
        request_hash = str(_mapping(cycle.get("adapter_exchange")).get("request_hash") or "")
        if request_hash:
            request_hashes.add(request_hash)

        _require(iteration.get("episode_id") == episode_id, errors, f"{cycle.get('case_id')}: episode_id mismatch")
        _require(
            iteration.get("cycle_no") == index + 1,
            errors,
            f"{cycle.get('case_id')}: ooda_iteration.cycle_no must be sequential",
        )
        _require(
            provider.get("ref") in _strings(act.get("evidence_refs")),
            errors,
            f"{cycle.get('case_id')}: OODA act evidence_refs must include current provider response",
        )

        context_iteration = _mapping(context_pack.get("ooda_iteration"))
        adapter_context_iteration = _mapping(adapter_context.get("ooda_iteration"))
        _require(
            context_iteration == iteration,
            errors,
            f"{cycle.get('case_id')}: persona context_pack must carry the OODA iteration context",
        )
        _require(
            adapter_context_iteration == iteration,
            errors,
            f"{cycle.get('case_id')}: adapter context_pack must carry the OODA iteration context",
        )
        _require(
            metadata.get("ooda_episode_id") == episode_id,
            errors,
            f"{cycle.get('case_id')}: adapter metadata must include ooda_episode_id",
        )
        _require(
            metadata.get("ooda_cycle_no") == index + 1,
            errors,
            f"{cycle.get('case_id')}: adapter metadata must include ooda_cycle_no",
        )

        if index == 0:
            _require(
                iteration.get("request_consumes_previous_cycle") is False,
                errors,
                f"{cycle.get('case_id')}: first cycle must not claim previous-cycle consumption",
            )
            _require(
                not iteration.get("previous_provider_response_ref"),
                errors,
                f"{cycle.get('case_id')}: first cycle must not have previous provider response",
            )
            continue

        previous = cycles[index - 1]
        if not isinstance(previous, Mapping):
            continue
        previous_decision = _mapping(previous.get("decision_trace"))
        previous_ooda = _mapping(previous_decision.get("ooda"))
        previous_act = _mapping(previous_ooda.get("act"))
        previous_provider_ref = str(_mapping(previous.get("provider_response")).get("ref") or "")
        previous_decision_ref = str(previous_decision.get("trace_ref") or "")
        previous_action_ref = str(previous_act.get("handoff_ref") or "")
        previous_action = str(previous_act.get("next_action") or "")
        carry_refs = {
            previous_provider_ref,
            previous_decision_ref,
            previous_action_ref,
        }
        _require(
            iteration.get("request_consumes_previous_cycle") is True,
            errors,
            f"{cycle.get('case_id')}: cycle must consume previous cycle",
        )
        _require(
            iteration.get("previous_provider_response_ref") == previous_provider_ref,
            errors,
            f"{cycle.get('case_id')}: previous provider response ref was not carried forward",
        )
        _require(
            iteration.get("previous_decision_trace_ref") == previous_decision_ref,
            errors,
            f"{cycle.get('case_id')}: previous decision trace ref was not carried forward",
        )
        _require(
            iteration.get("previous_action_ref") == previous_action_ref,
            errors,
            f"{cycle.get('case_id')}: previous action ref was not carried forward",
        )
        _require(
            iteration.get("previous_selected_action") == previous_action,
            errors,
            f"{cycle.get('case_id')}: previous selected action was not carried forward",
        )

        consumed_refs = set(_strings(context_pack.get("source_refs")))
        consumed_refs.update(_strings(_mapping(observe.get("feedback_from_previous_cycle")).get("input_refs")))
        consumed_refs.update(_strings(orient.get("input_refs")))
        consumed_refs.update(_strings(decide.get("evidence_refs")))
        consumed_refs.update(_strings(reasoning.get("input_refs")))
        consumed_refs.update(_strings(generation.get("input_refs")))
        consumed_refs.update(_strings(selected.get("evidence_refs")))
        _require(
            carry_refs.issubset(consumed_refs),
            errors,
            f"{cycle.get('case_id')}: next OODA cycle must consume previous response, decision, and action refs",
        )
        _require(
            metadata.get("previous_provider_response_ref") == previous_provider_ref,
            errors,
            f"{cycle.get('case_id')}: adapter metadata must carry previous provider response",
        )
        _require(
            metadata.get("previous_action_ref") == previous_action_ref,
            errors,
            f"{cycle.get('case_id')}: adapter metadata must carry previous action ref",
        )

    _require(
        len(request_hashes) == len(cycles),
        errors,
        "every OODA cycle must have a distinct adapter request hash",
    )
    return {
        "episode_id": episode_id,
        "passed": not errors,
        "errors": errors,
    }


def validate_openclaw_adapter_backed_persona_case(case: Mapping[str, Any]) -> dict[str, Any]:
    """Return pass/fail details for one adapter-backed persona case."""

    errors: list[str] = []
    adapter = _mapping(case.get("adapter_exchange"))
    provider = _mapping(case.get("provider_response"))
    persona_request = _mapping(case.get("persona_request"))
    candidate_generation = _mapping(case.get("candidate_generation"))
    scorer = _mapping(case.get("scorer"))
    decision_trace = _mapping(case.get("decision_trace"))
    triggering_oss = _mapping(case.get("triggering_oss_response"))
    provider_ref = str(provider.get("ref") or "")
    adapter_body = _mapping(adapter.get("body"))
    expected_hash = stable_json_hash(adapter_body) if adapter_body else ""

    _require(adapter.get("invoked") is True, errors, "adapter_exchange.invoked must be true")
    _require(
        adapter.get("client_class") == OPENCLAW_OPS_CLIENT_CLASS,
        errors,
        "adapter_exchange.client_class must be OpenClawOpsClient",
    )
    _require(
        adapter.get("method") == "POST" and adapter.get("path") == OPENCLAW_ADAPTER_PROVIDER_PATH,
        errors,
        "adapter_exchange must POST the OpenClaw provider adapter path",
    )
    _require(
        adapter.get("request_hash") == expected_hash,
        errors,
        "adapter_exchange.request_hash must match the adapter body",
    )
    _require(
        persona_request.get("adapter_request_hash") == adapter.get("request_hash"),
        errors,
        "persona_request.adapter_request_hash must match adapter_exchange.request_hash",
    )
    _require(
        adapter.get("client_method") == "invoke_assistant_provider",
        errors,
        "adapter_exchange.client_method must be invoke_assistant_provider",
    )
    _require(adapter.get("provider") == OPENCLAW_ADAPTER_PROVIDER, errors, "adapter provider must be openclaw")
    _require(str(adapter.get("url") or "").endswith(OPENCLAW_ADAPTER_PROVIDER_PATH), errors, "adapter URL must end with OpenClaw invoke path")

    _require(provider_ref.startswith("openclaw-provider-response://"), errors, "provider_response.ref is required")
    _require(
        provider.get("provider") == OPENCLAW_ADAPTER_PROVIDER,
        errors,
        "provider_response.provider must be openclaw",
    )
    _require(provider.get("status") == "completed", errors, "provider_response.status must be completed")
    _require(provider.get("response_text_hash"), errors, "provider_response.response_text_hash is required")
    _require(provider.get("output_request_id"), errors, "provider_response.output_request_id is required")
    _require(
        adapter.get("provider_response_ref") == provider_ref,
        errors,
        "adapter_exchange.provider_response_ref must match provider_response.ref",
    )
    _require(
        persona_request.get("openclaw_provider_response_ref") == provider_ref,
        errors,
        "persona_request.openclaw_provider_response_ref must match provider_response.ref",
    )

    proof = _mapping(adapter.get("provider_invocation_proof"))
    _require(
        proof.get("source") == "OpenClawOpsClient.invoke_assistant_provider",
        errors,
        "provider_invocation_proof.source must cite OpenClawOpsClient.invoke_assistant_provider",
    )
    _require(
        proof.get("adapter_request_hash") == adapter.get("request_hash"),
        errors,
        "provider_invocation_proof.adapter_request_hash must match adapter_exchange.request_hash",
    )
    _require(
        proof.get("provider_response_ref") == provider_ref,
        errors,
        "provider_invocation_proof.provider_response_ref must match provider_response.ref",
    )

    input_refs = set(_strings(candidate_generation.get("input_refs")))
    _require(provider_ref in input_refs, errors, "candidate_generation.input_refs must include provider_response.ref")
    _require(
        case.get("alpha_seed", {}).get("seed_ref") in input_refs,
        errors,
        "candidate_generation.input_refs must include alpha seed ref",
    )
    candidates = candidate_generation.get("candidates")
    _require(isinstance(candidates, list) and bool(candidates), errors, "candidate_generation.candidates are required")

    scorer_inputs = _mapping(scorer.get("scoring_inputs"))
    _require(
        scorer_inputs.get("openclaw_provider_response_ref") == provider_ref,
        errors,
        "scorer.scoring_inputs must cite provider_response.ref",
    )
    _require(
        scorer_inputs.get("triggering_oss_response_ref") == triggering_oss.get("response_ref"),
        errors,
        "scorer.scoring_inputs must cite triggering OSS response",
    )

    selected = _mapping(decision_trace.get("selected_candidate"))
    selected_refs = set(_strings(selected.get("evidence_refs")))
    decision_refs = set(_strings(decision_trace.get("evidence_refs")))
    _require(provider_ref in selected_refs, errors, "selected candidate must cite provider_response.ref")
    _require(provider_ref in decision_refs, errors, "decision_trace.evidence_refs must cite provider_response.ref")
    _require(
        decision_trace.get("adapter_backed") is True,
        errors,
        "decision_trace.adapter_backed must be true",
    )
    _require(
        decision_trace.get("selected_candidate_id") == selected.get("candidate_id"),
        errors,
        "decision_trace.selected_candidate_id must match selected candidate",
    )

    ooda = _mapping(decision_trace.get("ooda"))
    _require(
        _mapping(ooda.get("observe")).get("request_ref") == persona_request.get("request_ref"),
        errors,
        "OODA observe request_ref must match persona request",
    )
    _require(
        _mapping(ooda.get("observe")).get("response_ref") == provider_ref,
        errors,
        "OODA observe response_ref must be provider response",
    )
    _require(
        provider_ref in _strings(_mapping(ooda.get("decide")).get("evidence_refs")),
        errors,
        "OODA decide evidence_refs must include provider response",
    )

    return {
        "case_id": str(case.get("case_id") or ""),
        "assertion_label": str(case.get("assertion_label") or ""),
        "passed": not errors,
        "errors": errors,
    }


def assert_openclaw_adapter_backed_persona_case(case: Mapping[str, Any]) -> None:
    validation = validate_openclaw_adapter_backed_persona_case(case)
    if not validation["passed"]:
        raise AssertionError("; ".join(validation["errors"]))


def _client_for_spec(
    spec: AdapterBackedPersonaFlowSpec,
    *,
    client_factory: ClientFactory | None,
) -> AssistantProviderClient:
    if client_factory is not None:
        return client_factory(spec)
    client_class = load_openclaw_ops_client_class()
    return client_class()


def _persona_request_for_spec(
    spec: AdapterBackedPersonaFlowSpec,
    *,
    iteration_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    seed = spec.seed
    iteration = _mapping(iteration_context)
    operator_id = f"operator-{seed.key}-{spec.case_no:03d}"
    trace_id = f"trace-{spec.case_id}"
    triggering_oss_ref = f"oss://{spec.scenario.primary_component}/{spec.case_id}"
    alpha_seed_ref = f"alpha-seed://{seed.key}"
    carryover_refs = _strings(iteration.get("carryover_refs"))
    context_pack = {
        "context_pack_id": f"ctx-{spec.case_id}",
        "persona_id": "persona-alpha",
        "case_id": spec.case_id,
        "ooda_iteration": iteration,
        "ooda_phase": spec.depth.ooda_phase,
        "depth_id": spec.depth.depth_id,
        "validation_focus": spec.depth.validation_focus,
        "alpha_seed": _seed_to_dict(seed),
        "alpha_seed_ref": alpha_seed_ref,
        "triggering_oss_response_ref": triggering_oss_ref,
        "triggering_component": spec.scenario.primary_component,
        "related_components": list(spec.scenario.related_components),
        "realistic_trigger": spec.scenario.realistic_trigger,
        "source_refs": [
            alpha_seed_ref,
            seed.evidence_path,
            *seed.source_dataset_refs,
            triggering_oss_ref,
            *carryover_refs,
        ],
        "requested_persona_work": spec.depth.expected_persona_work,
        "validation_plan": _validation_plan(spec),
    }
    if iteration.get("request_consumes_previous_cycle"):
        context_pack["previous_cycle_feedback"] = {
            "previous_provider_response_ref": iteration.get("previous_provider_response_ref"),
            "previous_decision_trace_ref": iteration.get("previous_decision_trace_ref"),
            "previous_action_ref": iteration.get("previous_action_ref"),
            "previous_selected_action": iteration.get("previous_selected_action"),
            "required_next_ooda_step": "observe_previous_feedback_then_orient",
        }
    prompt = (
        f"Persona persona-alpha case {spec.case_no:03d}: route this request through OpenClaw. "
        f"Use alpha seed {seed.key} ({seed.strategy_id}) and OSS trigger "
        f"{spec.scenario.primary_component}. Depth={spec.depth.depth_id}. "
        f"Return a decision-ready next action for {spec.scenario.decision_action}."
    )
    if iteration.get("request_consumes_previous_cycle"):
        prompt = (
            f"{prompt} Continue OODA episode {iteration.get('episode_id')} cycle "
            f"{iteration.get('cycle_no')} by consuming previous provider response "
            f"{iteration.get('previous_provider_response_ref')} and previous action "
            f"{iteration.get('previous_selected_action')}."
        )
    metadata = {
        "validation_family": FLOW_SCHEMA_VERSION,
        "case_id": spec.case_id,
        "assertion_label": spec.assertion_label,
        "alpha_seed_key": seed.key,
        "alpha_seed_source_ref": seed.evidence_path,
        "source_strategy_spec_id": seed.source_strategy_spec_id,
        "triggering_component": spec.scenario.primary_component,
        "related_components": list(spec.scenario.related_components),
        "depth_id": spec.depth.depth_id,
        "scenario_id": spec.scenario.scenario_id,
        "ooda_episode_id": iteration.get("episode_id"),
        "ooda_cycle_no": iteration.get("cycle_no"),
        "request_consumes_previous_cycle": iteration.get("request_consumes_previous_cycle") is True,
        "previous_provider_response_ref": iteration.get("previous_provider_response_ref"),
        "previous_action_ref": iteration.get("previous_action_ref"),
    }
    return {
        "request_ref": f"persona-request://{spec.case_id}",
        "persona_id": "persona-alpha",
        "operator_id": operator_id,
        "trace_id": trace_id,
        "mode": "user",
        "prompt": prompt,
        "context_pack": context_pack,
        "metadata": metadata,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "refs": [alpha_seed_ref, triggering_oss_ref, *carryover_refs],
            }
        ],
        "attachments": [
            {
                "type": "alpha_seed_source",
                "ref": seed.evidence_path,
                "anchors": list(seed.anchors),
            }
        ],
    }


def _iteration_context_for_spec(
    spec: AdapterBackedPersonaFlowSpec,
    *,
    episode_id: str | None,
    cycle_no: int | None,
    previous_cycle: Mapping[str, Any] | None,
) -> dict[str, Any]:
    resolved_episode_id = episode_id or f"single-cycle-{spec.case_id}"
    resolved_cycle_no = int(cycle_no or 1)
    context: dict[str, Any] = {
        "episode_id": resolved_episode_id,
        "cycle_no": resolved_cycle_no,
        "case_id": spec.case_id,
        "request_consumes_previous_cycle": False,
        "previous_case_id": None,
        "previous_provider_response_ref": None,
        "previous_decision_trace_ref": None,
        "previous_action_ref": None,
        "previous_selected_action": None,
        "carryover_refs": [],
    }
    if not previous_cycle:
        return context

    previous_decision = _mapping(previous_cycle.get("decision_trace"))
    previous_ooda = _mapping(previous_decision.get("ooda"))
    previous_act = _mapping(previous_ooda.get("act"))
    previous_provider_ref = str(_mapping(previous_cycle.get("provider_response")).get("ref") or "")
    previous_decision_ref = str(previous_decision.get("trace_ref") or "")
    previous_action_ref = str(previous_act.get("handoff_ref") or "")
    context.update(
        {
            "request_consumes_previous_cycle": True,
            "previous_case_id": previous_cycle.get("case_id"),
            "previous_provider_response_ref": previous_provider_ref,
            "previous_decision_trace_ref": previous_decision_ref,
            "previous_action_ref": previous_action_ref,
            "previous_selected_action": previous_act.get("next_action"),
            "carryover_refs": [
                previous_provider_ref,
                previous_decision_ref,
                previous_action_ref,
            ],
        }
    )
    return context

def _adapter_body_for_persona_request(persona_request: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "mode": str(persona_request["mode"]),
        "prompt": str(persona_request["prompt"]),
        "context_pack": copy.deepcopy(dict(persona_request["context_pack"])),
        "metadata": copy.deepcopy(dict(persona_request["metadata"])),
        "messages": copy.deepcopy(list(persona_request.get("messages") or [])),
        "attachments": copy.deepcopy(list(persona_request.get("attachments") or [])),
    }


def _provider_response_from_payload(
    spec: AdapterBackedPersonaFlowSpec,
    provider_payload: Mapping[str, Any],
) -> dict[str, Any]:
    data = _mapping(provider_payload.get("data"))
    output = _mapping(data.get("output"))
    output_request_id = str(
        output.get("request_id")
        or _mapping(output.get("upstream")).get("runId")
        or stable_json_hash(data)[:16]
    )
    response_text = _extract_provider_text(output)
    provider_ref = f"openclaw-provider-response://{output_request_id}"
    return {
        "ref": provider_ref,
        "case_id": spec.case_id,
        "provider": data.get("provider"),
        "mode": data.get("mode"),
        "status": data.get("status"),
        "output_request_id": output_request_id,
        "transport": output.get("transport"),
        "response_text": response_text,
        "response_text_hash": stable_json_hash({"text": response_text}),
        "raw": copy.deepcopy(dict(provider_payload)),
    }


def _adapter_exchange(
    spec: AdapterBackedPersonaFlowSpec,
    *,
    client: AssistantProviderClient,
    adapter_body: Mapping[str, Any],
    request_hash: str,
    provider_payload: Mapping[str, Any],
    provider_response: Mapping[str, Any],
    persona_request: Mapping[str, Any],
) -> dict[str, Any]:
    base_url = str(getattr(client, "base_url", "") or "")
    return {
        "exchange_ref": f"openclaw-adapter-exchange://{spec.case_id}",
        "invoked": True,
        "client_class": client.__class__.__name__,
        "client_method": "invoke_assistant_provider",
        "provider": OPENCLAW_ADAPTER_PROVIDER,
        "method": "POST",
        "path": OPENCLAW_ADAPTER_PROVIDER_PATH,
        "url": f"{base_url.rstrip('/')}{OPENCLAW_ADAPTER_PROVIDER_PATH}" if base_url else OPENCLAW_ADAPTER_PROVIDER_PATH,
        "operator_id": persona_request["operator_id"],
        "trace_id": persona_request["trace_id"],
        "request_hash": request_hash,
        "body": copy.deepcopy(dict(adapter_body)),
        "response_status": provider_payload.get("status"),
        "provider_response_ref": provider_response["ref"],
        "provider_invocation_proof": {
            "source": "OpenClawOpsClient.invoke_assistant_provider",
            "adapter_request_hash": request_hash,
            "provider_response_ref": provider_response["ref"],
            "provider_output_request_id": provider_response["output_request_id"],
            "client_class": client.__class__.__name__,
            "adapter_path": OPENCLAW_ADAPTER_PROVIDER_PATH,
        },
    }


def _triggering_oss_response(spec: AdapterBackedPersonaFlowSpec) -> dict[str, Any]:
    seed = spec.seed
    primary = spec.scenario.primary_component
    response_ref = f"oss://{primary}/{spec.case_id}"
    return {
        "response_ref": response_ref,
        "component": primary,
        "related_components": list(spec.scenario.related_components),
        "status": "completed",
        "alpha_seed_ref": f"alpha-seed://{seed.key}",
        "alpha_seed_key": seed.key,
        "source_strategy_spec_id": seed.source_strategy_spec_id,
        "source_dataset_refs": list(seed.source_dataset_refs),
        "metrics": _oss_metrics_for_spec(spec),
        "persona_followup_trigger": {
            "ooda_phase": spec.depth.ooda_phase,
            "next_action": spec.scenario.decision_action,
            "reason": spec.scenario.realistic_trigger,
        },
    }


def _persona_reasoning(
    spec: AdapterBackedPersonaFlowSpec,
    *,
    persona_request: Mapping[str, Any],
    provider_response: Mapping[str, Any],
    triggering_oss_response: Mapping[str, Any],
) -> dict[str, Any]:
    input_refs = [
        persona_request["request_ref"],
        provider_response["ref"],
        triggering_oss_response["response_ref"],
        f"alpha-seed://{spec.seed.key}",
    ]
    input_refs.extend(_strings(_mapping(persona_request.get("context_pack")).get("source_refs")))
    input_refs = list(dict.fromkeys(input_refs))
    return {
        "reasoning_ref": f"persona-reasoning://{spec.case_id}",
        "model": "persona-alpha-openclaw-response-consumer",
        "input_refs": input_refs,
        "provider_response_ref": provider_response["ref"],
        "provider_response_text_hash": provider_response["response_text_hash"],
        "reasoning_summary": (
            f"OpenClaw response is available for {spec.scenario.primary_component}; "
            f"persona can proceed to {spec.scenario.decision_action}."
        ),
        "next_required_step": "generate_candidates",
    }


def _candidate_generation(
    spec: AdapterBackedPersonaFlowSpec,
    *,
    persona_request: Mapping[str, Any],
    provider_response: Mapping[str, Any],
    triggering_oss_response: Mapping[str, Any],
    persona_reasoning: Mapping[str, Any],
) -> dict[str, Any]:
    provider_ref = provider_response["ref"]
    alpha_seed_ref = f"alpha-seed://{spec.seed.key}"
    oss_ref = triggering_oss_response["response_ref"]
    input_refs = [
        persona_request["request_ref"],
        persona_reasoning["reasoning_ref"],
        provider_ref,
        alpha_seed_ref,
        oss_ref,
    ]
    input_refs.extend(_strings(_mapping(persona_request.get("context_pack")).get("source_refs")))
    input_refs = list(dict.fromkeys(input_refs))
    actions = (
        spec.scenario.decision_action,
        f"deepen_{spec.scenario.primary_component}_evidence",
        f"hold_{spec.scenario.primary_component}_until_next_openclaw_turn",
    )
    candidates = []
    for index, action in enumerate(actions, start=1):
        candidates.append(
            {
                "candidate_id": f"candidate://{spec.case_id}/{index}",
                "action": action,
                "source": "persona_candidate_generator",
                "generated_after_provider_response": True,
                "evidence_refs": [provider_ref, alpha_seed_ref, oss_ref],
                "input_hash": stable_json_hash(
                    {
                        "case_id": spec.case_id,
                        "action": action,
                        "provider_ref": provider_ref,
                        "oss_ref": oss_ref,
                    }
                ),
            }
        )
    return {
        "generation_ref": f"persona-candidate-generation://{spec.case_id}",
        "input_refs": input_refs,
        "provider_response_ref": provider_ref,
        "candidates": candidates,
    }


def _score_candidates(
    spec: AdapterBackedPersonaFlowSpec,
    *,
    provider_response: Mapping[str, Any],
    triggering_oss_response: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    provider_ref = provider_response["ref"]
    scores = []
    for index, candidate in enumerate(candidates):
        provider_bonus = 0.2 if provider_response.get("status") == "completed" else -0.4
        depth_bonus = min(spec.case_no // len(BREADTH_SCENARIOS), len(DEPTH_PROFILES) - 1) * 0.015
        action_bonus = 0.1 if candidate.get("action") == spec.scenario.decision_action else -0.03 * index
        score = round(0.62 + provider_bonus + depth_bonus + action_bonus, 4)
        scores.append(
            {
                "candidate_id": candidate["candidate_id"],
                "score": score,
                "score_inputs": {
                    "provider_response_ref": provider_ref,
                    "triggering_oss_response_ref": triggering_oss_response["response_ref"],
                    "alpha_seed_ref": f"alpha-seed://{spec.seed.key}",
                },
            }
        )
    selected_id = max(scores, key=lambda item: item["score"])["candidate_id"]
    return {
        "scorer_ref": f"persona-candidate-scorer://{spec.case_id}",
        "scoring_inputs": {
            "openclaw_provider_response_ref": provider_ref,
            "triggering_oss_response_ref": triggering_oss_response["response_ref"],
            "alpha_seed_ref": f"alpha-seed://{spec.seed.key}",
            "scenario_id": spec.scenario.scenario_id,
        },
        "scores": scores,
        "selected_candidate_id": selected_id,
    }


def _decision_trace(
    spec: AdapterBackedPersonaFlowSpec,
    *,
    persona_request: Mapping[str, Any],
    provider_response: Mapping[str, Any],
    triggering_oss_response: Mapping[str, Any],
    candidate_generation: Mapping[str, Any],
    scorer: Mapping[str, Any],
) -> dict[str, Any]:
    selected_id = scorer["selected_candidate_id"]
    selected = next(
        dict(candidate)
        for candidate in candidate_generation["candidates"]
        if candidate["candidate_id"] == selected_id
    )
    provider_ref = provider_response["ref"]
    iteration = _mapping(_mapping(persona_request.get("context_pack")).get("ooda_iteration"))
    carryover_refs = _strings(iteration.get("carryover_refs"))
    evidence_refs = [
        provider_ref,
        triggering_oss_response["response_ref"],
        f"alpha-seed://{spec.seed.key}",
        candidate_generation["generation_ref"],
        scorer["scorer_ref"],
        *carryover_refs,
    ]
    selected["evidence_refs"] = list(dict.fromkeys([*selected.get("evidence_refs", []), *carryover_refs]))
    return {
        "trace_ref": f"persona-decision-trace://{spec.case_id}",
        "adapter_backed": True,
        "selected_candidate_id": selected_id,
        "selected_candidate": selected,
        "evidence_refs": evidence_refs,
        "decision_inputs": {
            "persona_request_ref": persona_request["request_ref"],
            "openclaw_provider_response_ref": provider_ref,
            "triggering_oss_response_ref": triggering_oss_response["response_ref"],
            "candidate_generation_ref": candidate_generation["generation_ref"],
            "scorer_ref": scorer["scorer_ref"],
            "previous_cycle_refs": carryover_refs,
        },
        "ooda": {
            "observe": {
                "request_ref": persona_request["request_ref"],
                "response_ref": provider_ref,
                "triggering_oss_response_ref": triggering_oss_response["response_ref"],
                "feedback_from_previous_cycle": {
                    "consumed": bool(carryover_refs),
                    "input_refs": carryover_refs,
                },
            },
            "orient": {
                "input_refs": [provider_ref, triggering_oss_response["response_ref"], *carryover_refs],
                "output_ref": f"persona-orient://{spec.case_id}",
            },
            "decide": {
                "candidate_generation_ref": candidate_generation["generation_ref"],
                "scorer_ref": scorer["scorer_ref"],
                "selected_candidate_id": selected_id,
                "evidence_refs": evidence_refs,
            },
            "act": {
                "next_action": selected["action"],
                "handoff_ref": f"persona-next-action://{spec.case_id}/{selected['action']}",
                "evidence_refs": evidence_refs,
                "produces_next_observe_refs": [provider_ref, f"persona-decision-trace://{spec.case_id}"],
            },
        },
    }


def _validation_plan(spec: AdapterBackedPersonaFlowSpec) -> dict[str, Any]:
    previous_depths = [depth.depth_id for depth in DEPTH_PROFILES[: spec.case_no // len(BREADTH_SCENARIOS)]]
    return {
        "preflight_questions": [
            {
                "question": "what_has_not_been_validated_yet",
                "answer": (
                    f"{spec.depth.depth_id} with {spec.scenario.scenario_id} "
                    f"and alpha seed {spec.seed.key}"
                ),
            },
            {
                "question": "what_can_be_deepened",
                "answer": spec.depth.validation_focus,
            },
            {
                "question": "which_realistic_combination_is_uncovered",
                "answer": spec.scenario.realistic_trigger,
            },
        ],
        "already_covered_depths": previous_depths,
        "planned_assertion_label": spec.assertion_label,
        "requires_real_adapter_invocation": True,
    }


def _seed_to_dict(seed: AlphaSeedSource) -> dict[str, Any]:
    return {
        "key": seed.key,
        "seed_ref": f"alpha-seed://{seed.key}",
        "strategy_id": seed.strategy_id,
        "source_strategy_spec_id": seed.source_strategy_spec_id,
        "source_dataset_refs": list(seed.source_dataset_refs),
        "evidence_path": seed.evidence_path,
        "anchors": list(seed.anchors),
    }


def _oss_metrics_for_spec(spec: AdapterBackedPersonaFlowSpec) -> dict[str, Any]:
    basis = spec.case_no + len(spec.scenario.related_components)
    return {
        "usability_delta": round(0.01 * (basis % 17), 4),
        "confidence": round(0.64 + 0.01 * (basis % 23), 4),
        "evidence_count": len(spec.scenario.related_components) + len(spec.seed.source_dataset_refs),
        "historical_data_bound": True,
    }


def _extract_provider_text(output: Mapping[str, Any]) -> str:
    events = output.get("json_events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, Mapping):
                continue
            item = event.get("item")
            if isinstance(item, Mapping) and item.get("text"):
                return str(item["text"])
    if output.get("text"):
        return str(output["text"])
    upstream = output.get("upstream")
    if isinstance(upstream, Mapping):
        result = upstream.get("result")
        if isinstance(result, Mapping):
            meta = result.get("meta")
            if isinstance(meta, Mapping) and meta.get("finalAssistantVisibleText"):
                return str(meta["finalAssistantVisibleText"])
    return json.dumps(output, sort_keys=True, default=str)[:500]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _strings(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None]
    if value is None:
        return []
    return [str(value)]


def _require(condition: bool, errors: list[str], message: str) -> None:
    if not condition:
        errors.append(message)
