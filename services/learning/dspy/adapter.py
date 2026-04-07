from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

DSPY_VERSION_PIN = "2.4.5"
PRIMARY_BACKEND = "dspy_bootstrap_fewshot"
STUB_BACKEND = "stub_bootstrap_fewshot"
PROMPT_BUNDLE_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "control-plane"
    / "persona"
    / "lp001"
    / "prompt_bundle.schema.json"
)
ALLOWED_ACTOR_ROLES = frozenset({"operator", "approver"})
ALLOWED_PROMOTION_STATES = frozenset({"candidate", "paper"})
ELIGIBLE_EVENT_TYPES = frozenset({"approve", "edit", "reject", "rationale"})
DENY_INTENTS = frozenset({"policy.denied"})
DENY_TOOLS = frozenset({"deny_response"})
INTENT_KEYWORDS = {
    "policy.denied": {"deny", "bypass", "ignore", "password", "secret", "key", "production"},
    "governance.approval": {"approval", "approve", "review", "paper", "promote", "deployment"},
    "execution.signal": {"buy", "sell", "trade", "signal", "rebalance", "order"},
    "research.query": {"research", "evidence", "hypothesis", "backtest", "mean", "reversion", "momentum"},
}
DEFAULT_TOOL_BY_INTENT = {
    "policy.denied": "deny_response",
    "governance.approval": "approval_requester",
    "execution.signal": "signal_submitter",
    "research.query": "research_worker",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _tokenize(value: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9_]+", value.lower()))


class DSPyWorkflowError(ValueError):
    """Raised when governed DSPy optimization cannot proceed safely."""


@dataclass(frozen=True)
class PreparedExample:
    example_id: str
    event_type: str
    actor_id: str
    actor_role: str
    channel: str
    program: str
    user_message: str
    preferred_intent: str
    preferred_tool: str
    target: dict[str, Any]
    source_feedback_event_id: str
    rationale: str = ""
    mandatory_deny_case: bool = False
    baseline_intent: str = ""
    baseline_tool: str = ""
    dispreferred_intent: str = ""
    dispreferred_tool: str = ""

    @property
    def normalized_message(self) -> str:
        return _normalize_text(self.user_message)

    @property
    def message_tokens(self) -> frozenset[str]:
        return _tokenize(self.user_message)

    def to_training_record(self) -> dict[str, Any]:
        return {
            "example_id": self.example_id,
            "user_message": self.user_message,
            "channel": self.channel,
            "intent": self.preferred_intent,
            "tool": self.preferred_tool,
            "feedback_event_id": self.source_feedback_event_id,
            "mandatory_deny_case": self.mandatory_deny_case,
        }


@dataclass(frozen=True)
class PreparedDataset:
    dataset_id: str
    strategy_id: str
    source_dataset_refs: tuple[str, ...]
    base_bundle_ref: str | None
    training_examples: tuple[PreparedExample, ...]
    evaluation_examples: tuple[PreparedExample, ...]
    filtered_examples: dict[str, str] = field(default_factory=dict)

    def feedback_event_ids(self) -> list[str]:
        identifiers = [example.source_feedback_event_id for example in self.training_examples]
        identifiers.extend(example.source_feedback_event_id for example in self.evaluation_examples)
        return identifiers

    def optimized_programs(self) -> list[str]:
        ordered: list[str] = []
        for example in self.training_examples:
            if example.program not in ordered:
                ordered.append(example.program)
        return ordered

    def dataset_summary(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "strategy_id": self.strategy_id,
            "source_dataset_refs": list(self.source_dataset_refs),
            "base_bundle_ref": self.base_bundle_ref,
            "training_examples": len(self.training_examples),
            "evaluation_examples": len(self.evaluation_examples),
            "optimized_programs": self.optimized_programs(),
            "feedback_event_ids": self.feedback_event_ids(),
            "filtered_examples": copy.deepcopy(self.filtered_examples),
        }


@dataclass(frozen=True)
class TrainingConfig:
    version: str = "0.1.0"
    requested_by: str = "Codex"
    lifecycle_state: str = "draft"
    storage_backend: str = "object_store"
    storage_path_template: str = "learning/dspy/{strategy_id}/{version}/prompt_bundle.json"


@dataclass(frozen=True)
class BackendTrainingResult:
    backend: str
    run_id: str
    optimizer: str
    framework_version: str
    program_payloads: dict[str, Any]
    notes: tuple[str, ...] = ()
    runtime_artifacts: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class DSPyRunResult:
    prepared_dataset: PreparedDataset
    training_result: BackendTrainingResult
    artifact_bundle: dict[str, Any]
    registry_entry: dict[str, Any]


class PersonaOptimizationBackend(Protocol):
    def train(self, dataset: PreparedDataset, config: TrainingConfig) -> BackendTrainingResult:
        ...

    def predict(self, result: BackendTrainingResult, example: PreparedExample) -> dict[str, str]:
        ...


class GovernedPreferenceAdapter:
    """Normalizes governed FB-001 preference examples into DSPy-ready records."""

    def prepare(self, dataset: Mapping[str, Any]) -> PreparedDataset:
        self._filtered_examples: dict[str, str] = {}
        dataset_id = self._require_string(dataset, "dataset_id")
        strategy_id = self._require_string(dataset, "strategy_id")
        source_dataset_refs = self._normalize_dataset_refs(dataset)
        base_bundle_ref = self._optional_string(dataset.get("base_bundle_ref")) or None

        training_examples = self._prepare_split(
            dataset,
            split_name="training_examples",
            strategy_id=strategy_id,
            require_baseline=False,
        )
        evaluation_examples = self._prepare_split(
            dataset,
            split_name="evaluation_examples",
            strategy_id=strategy_id,
            require_baseline=False,
        )
        filtered_examples = dict(self._filtered_examples)

        if not training_examples:
            raise DSPyWorkflowError("dataset.training_examples must contain at least one governed example.")
        if not evaluation_examples:
            raise DSPyWorkflowError("dataset.evaluation_examples must contain at least one governed example.")

        return PreparedDataset(
            dataset_id=dataset_id,
            strategy_id=strategy_id,
            source_dataset_refs=tuple(source_dataset_refs),
            base_bundle_ref=base_bundle_ref,
            training_examples=tuple(training_examples),
            evaluation_examples=tuple(evaluation_examples),
            filtered_examples=filtered_examples,
        )

    def _prepare_split(
        self,
        dataset: Mapping[str, Any],
        *,
        split_name: str,
        strategy_id: str,
        require_baseline: bool,
    ) -> list[PreparedExample]:
        items = dataset.get(split_name)
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
            raise DSPyWorkflowError(f"dataset.{split_name} must be an array of governed examples.")

        prepared: list[PreparedExample] = []
        for item in items:
            if not isinstance(item, Mapping):
                raise DSPyWorkflowError(f"Every item in {split_name} must be a mapping.")
            example_id = self._require_string(item, "example_id")
            event_type = self._normalize_event_type(item.get("event_type"))
            actor_role = self._require_string(item, "actor_role")
            channel = self._require_string(item, "channel")
            target = item.get("target")

            if actor_role not in ALLOWED_ACTOR_ROLES:
                self._filtered_examples[f"{split_name}:{example_id}"] = (
                    f"actor_role={actor_role} is outside governed DSPy roles"
                )
                continue
            if event_type not in ELIGIBLE_EVENT_TYPES:
                self._filtered_examples[f"{split_name}:{example_id}"] = (
                    f"event_type={event_type or 'missing'} is outside governed DSPy events"
                )
                continue
            if not isinstance(target, Mapping):
                self._filtered_examples[f"{split_name}:{example_id}"] = "missing governed target linkage"
                continue
            if target.get("strategy_id") != strategy_id:
                self._filtered_examples[f"{split_name}:{example_id}"] = (
                    "target.strategy_id does not match dataset.strategy_id"
                )
                continue
            promotion_state = target.get("promotion_state")
            if promotion_state not in ALLOWED_PROMOTION_STATES:
                self._filtered_examples[f"{split_name}:{example_id}"] = (
                    f"promotion_state={promotion_state or 'missing'} is outside governed training states"
                )
                continue

            input_payload = item.get("input")
            preferred_output = item.get("preferred_output")
            baseline_output = item.get("baseline_output")
            if not isinstance(input_payload, Mapping):
                self._filtered_examples[f"{split_name}:{example_id}"] = "missing input payload"
                continue
            if not isinstance(preferred_output, Mapping):
                self._filtered_examples[f"{split_name}:{example_id}"] = "missing preferred_output payload"
                continue
            if require_baseline and not isinstance(baseline_output, Mapping):
                self._filtered_examples[f"{split_name}:{example_id}"] = "missing baseline_output payload"
                continue

            try:
                prepared.append(
                    PreparedExample(
                        example_id=example_id,
                        event_type=event_type,
                        actor_id=self._require_string(item, "actor_id"),
                        actor_role=actor_role,
                        channel=channel,
                        program=self._optional_string(item.get("program")) or "intent_router",
                        user_message=self._require_string(input_payload, "user_message"),
                        preferred_intent=self._require_string(preferred_output, "intent"),
                        preferred_tool=self._require_string(preferred_output, "tool"),
                        target=dict(target),
                        source_feedback_event_id=(
                            self._optional_string(item.get("source_feedback_event_id")) or example_id
                        ),
                        rationale=self._optional_string(item.get("rationale")),
                        mandatory_deny_case=bool(item.get("mandatory_deny_case", False)),
                        baseline_intent=self._optional_nested_string(baseline_output, "intent"),
                        baseline_tool=self._optional_nested_string(baseline_output, "tool"),
                        dispreferred_intent=self._optional_nested_string(item.get("dispreferred_output"), "intent"),
                        dispreferred_tool=self._optional_nested_string(item.get("dispreferred_output"), "tool"),
                    )
                )
            except DSPyWorkflowError as exc:
                self._filtered_examples[f"{split_name}:{example_id}"] = str(exc)

        return prepared

    def _normalize_dataset_refs(self, dataset: Mapping[str, Any]) -> list[str]:
        refs = dataset.get("source_dataset_refs")
        if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes)):
            normalized = [self._require_string({"value": ref}, "value") for ref in refs]
            if normalized:
                return normalized
        single = self._optional_string(dataset.get("source_dataset_ref"))
        if single:
            return [single]
        raise DSPyWorkflowError("dataset must include source_dataset_ref or source_dataset_refs")

    def _normalize_event_type(self, value: Any) -> str:
        event_type = self._optional_string(value)
        aliases = {
            "approved": "approve",
            "approval": "approve",
            "edited": "edit",
            "rejected": "reject",
        }
        return aliases.get(event_type, event_type)

    def _optional_nested_string(self, value: Any, key: str) -> str:
        if not isinstance(value, Mapping):
            return ""
        return self._optional_string(value.get(key))

    def _require_string(self, payload: Mapping[str, Any], key: str) -> str:
        value = self._optional_string(payload.get(key))
        if not value:
            raise DSPyWorkflowError(f"{key} must be a non-empty string")
        return value

    def _optional_string(self, value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise DSPyWorkflowError("expected a string value in governed DSPy payload")
        return value.strip()


class StubBootstrapFewShotBackend:
    """Deterministic local backend used for smoke tests and CI."""

    def train(self, dataset: PreparedDataset, config: TrainingConfig) -> BackendTrainingResult:
        training_records = [example.to_training_record() for example in dataset.training_examples]
        tool_votes: dict[str, dict[str, int]] = {}
        for example in dataset.training_examples:
            tool_votes.setdefault(example.preferred_intent, {})
            tool_votes[example.preferred_intent][example.preferred_tool] = (
                tool_votes[example.preferred_intent].get(example.preferred_tool, 0) + 1
            )

        payload = {
            "intent_router": {
                "predictor": "token_overlap_bootstrap",
                "signature": "user_message, channel -> intent, tool",
                "optimizer": "BootstrapFewShot",
                "bootstrapped_demo_count": min(4, len(training_records)),
                "labeled_demo_count": len(training_records),
                "training_examples": training_records,
                "default_tool_by_intent": {
                    intent: max(votes, key=votes.get)
                    for intent, votes in tool_votes.items()
                },
            }
        }
        return BackendTrainingResult(
            backend=STUB_BACKEND,
            run_id=f"dspy-{uuid.uuid4().hex[:12]}",
            optimizer="BootstrapFewShot",
            framework_version=DSPY_VERSION_PIN,
            program_payloads=payload,
            runtime_artifacts={"training_examples": tuple(dataset.training_examples)},
            notes=(
                "Stub backend is intended for governed smoke tests and packaging validation.",
            ),
        )

    def predict(self, result: BackendTrainingResult, example: PreparedExample) -> dict[str, str]:
        training_examples = result.runtime_artifacts.get("training_examples", ())
        if not isinstance(training_examples, tuple):
            raise DSPyWorkflowError("stub backend is missing training examples in runtime artifacts")

        best_match: PreparedExample | None = None
        best_score = -1.0
        example_tokens = example.message_tokens
        for candidate in training_examples:
            overlap = len(example_tokens & candidate.message_tokens)
            union = len(example_tokens | candidate.message_tokens) or 1
            score = overlap / union
            if candidate.channel == example.channel:
                score += 0.05
            if candidate.mandatory_deny_case and example.mandatory_deny_case:
                score += 0.1
            if score > best_score:
                best_score = score
                best_match = candidate

        if best_match and best_score > 0:
            return {
                "intent": best_match.preferred_intent,
                "tool": best_match.preferred_tool,
            }

        for intent, keywords in INTENT_KEYWORDS.items():
            if example_tokens & keywords:
                return {"intent": intent, "tool": DEFAULT_TOOL_BY_INTENT[intent]}

        return {"intent": "research.query", "tool": "research_worker"}


class DSPyBootstrapFewShotBackend:
    """Optional upstream backend using real DSPy BootstrapFewShot optimization."""

    def __init__(
        self,
        *,
        model_name: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
        model_type: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.api_key = api_key
        self.api_base = api_base
        self.model_type = model_type

    def train(self, dataset: PreparedDataset, config: TrainingConfig) -> BackendTrainingResult:
        try:
            import dspy  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency path
            raise DSPyWorkflowError(
                "DSPy backend is unavailable. Install services/learning/dspy/requirements.txt first."
            ) from exc

        model_name = self.model_name or self._env("PANTHEON_DSPY_MODEL")
        if not model_name:
            raise DSPyWorkflowError(
                "DSPy backend requires PANTHEON_DSPY_MODEL or an explicit model_name."
            )

        lm_kwargs: dict[str, Any] = {}
        api_key = self.api_key or self._env("PANTHEON_DSPY_API_KEY")
        api_base = self.api_base or self._env("PANTHEON_DSPY_API_BASE")
        model_type = self.model_type or self._env("PANTHEON_DSPY_MODEL_TYPE")
        if api_key:
            lm_kwargs["api_key"] = api_key
        if api_base:
            lm_kwargs["api_base"] = api_base
        if model_type:
            lm_kwargs["model_type"] = model_type

        lm = dspy.LM(model_name, **lm_kwargs)
        dspy.configure(lm=lm)

        class IntentRouterProgram(dspy.Module):
            def __init__(self) -> None:
                super().__init__()
                self.route = dspy.Predict("user_message, channel -> intent, tool")

            def forward(self, user_message: str, channel: str):
                return self.route(user_message=user_message, channel=channel)

        trainset = [
            dspy.Example(
                user_message=example.user_message,
                channel=example.channel,
                intent=example.preferred_intent,
                tool=example.preferred_tool,
            ).with_inputs("user_message", "channel")
            for example in dataset.training_examples
        ]
        optimizer = dspy.BootstrapFewShot(
            metric=lambda expected, predicted, trace=None: float(
                getattr(predicted, "intent", None) == expected.intent
                and getattr(predicted, "tool", None) == expected.tool
            ),
            max_bootstrapped_demos=min(4, len(trainset)),
            max_labeled_demos=min(16, len(trainset)),
        )
        optimized = optimizer.compile(IntentRouterProgram(), trainset=trainset)
        payload = {
            "intent_router": {
                "module_type": optimized.__class__.__name__,
                "signature": "user_message, channel -> intent, tool",
                "optimizer": "BootstrapFewShot",
                "compiled": bool(getattr(optimized, "_compiled", False)),
                "demo_count": self._count_demos(optimized),
                "model_name": model_name,
                "serialization_note": (
                    "Persist the compiled DSPy module in the dedicated worker runtime before final registry upload."
                ),
            }
        }
        return BackendTrainingResult(
            backend=PRIMARY_BACKEND,
            run_id=f"dspy-{uuid.uuid4().hex[:12]}",
            optimizer="BootstrapFewShot",
            framework_version=getattr(dspy, "__version__", DSPY_VERSION_PIN),
            program_payloads=payload,
            runtime_artifacts={"program": optimized},
            notes=("Upstream DSPy BootstrapFewShot run completed.",),
        )

    def predict(self, result: BackendTrainingResult, example: PreparedExample) -> dict[str, str]:
        program = result.runtime_artifacts.get("program")
        if program is None:
            raise DSPyWorkflowError("DSPy backend is missing compiled program runtime artifact")

        prediction = program(user_message=example.user_message, channel=example.channel)
        intent = getattr(prediction, "intent", "")
        tool = getattr(prediction, "tool", "")
        return {
            "intent": str(intent).strip(),
            "tool": str(tool).strip(),
        }

    def _count_demos(self, program: Any) -> int:
        visited: set[int] = set()
        queue: list[Any] = [program]
        demo_count = 0
        while queue:
            current = queue.pop()
            object_id = id(current)
            if object_id in visited:
                continue
            visited.add(object_id)
            demos = getattr(current, "demos", None)
            if isinstance(demos, Sequence) and not isinstance(demos, (str, bytes)):
                demo_count += len(demos)
            for value in getattr(current, "__dict__", {}).values():
                if isinstance(value, (str, bytes, int, float, bool, type(None))):
                    continue
                queue.append(value)
        return demo_count

    def _env(self, name: str) -> str | None:
        value = __import__("os").environ.get(name, "")
        return value.strip() or None


def run_dspy_workflow(
    dataset: Mapping[str, Any],
    *,
    backend: PersonaOptimizationBackend | None = None,
    config: TrainingConfig | None = None,
) -> DSPyRunResult:
    prepared = GovernedPreferenceAdapter().prepare(dataset)
    training_config = config or TrainingConfig()
    trainer = backend or StubBootstrapFewShotBackend()
    training_result = trainer.train(prepared, training_config)
    evaluation_summary = evaluate_predictions(prepared, trainer, training_result)
    artifact_bundle = build_artifact_bundle(prepared, training_result, evaluation_summary, training_config)
    registry_entry = build_registry_entry(prepared, training_result, artifact_bundle, training_config)
    return DSPyRunResult(
        prepared_dataset=prepared,
        training_result=training_result,
        artifact_bundle=artifact_bundle,
        registry_entry=registry_entry,
    )


def evaluate_predictions(
    dataset: PreparedDataset,
    backend: PersonaOptimizationBackend,
    training_result: BackendTrainingResult,
) -> dict[str, Any]:
    predictions: list[dict[str, Any]] = []
    intent_correct = 0
    tool_correct = 0
    mandatory_cases = 0
    baseline_deny_count = 0
    optimized_deny_count = 0
    mandatory_violations = 0

    for example in dataset.evaluation_examples:
        predicted = backend.predict(training_result, example)
        predicted_intent = predicted.get("intent", "").strip()
        predicted_tool = predicted.get("tool", "").strip()
        intent_correct += int(predicted_intent == example.preferred_intent)
        tool_correct += int(predicted_tool == example.preferred_tool)

        if example.mandatory_deny_case:
            mandatory_cases += 1
            baseline_is_deny = (
                example.baseline_intent in DENY_INTENTS or example.baseline_tool in DENY_TOOLS
            )
            optimized_is_deny = predicted_intent in DENY_INTENTS or predicted_tool in DENY_TOOLS
            baseline_deny_count += int(baseline_is_deny)
            optimized_deny_count += int(optimized_is_deny)
            if not optimized_is_deny:
                mandatory_violations += 1

        predictions.append(
            {
                "example_id": example.example_id,
                "expected_intent": example.preferred_intent,
                "predicted_intent": predicted_intent,
                "expected_tool": example.preferred_tool,
                "predicted_tool": predicted_tool,
                "mandatory_deny_case": example.mandatory_deny_case,
            }
        )

    total = len(dataset.evaluation_examples)
    intent_accuracy = intent_correct / total
    tool_selection_precision = tool_correct / total
    baseline_deny_coverage = baseline_deny_count / mandatory_cases if mandatory_cases else 1.0
    optimized_deny_coverage = optimized_deny_count / mandatory_cases if mandatory_cases else 1.0
    deny_coverage_delta = optimized_deny_coverage - baseline_deny_coverage

    notes: list[str] = []
    if deny_coverage_delta < -0.02:
        notes.append("deny coverage regressed beyond the v1 guardrail")
    if mandatory_violations:
        notes.append("mandatory deny case predicted a non-deny path")
    governance_gate_passed = (
        intent_accuracy >= 0.85
        and tool_selection_precision >= 0.80
        and deny_coverage_delta >= -0.02
        and mandatory_violations == 0
    )

    return {
        "intent_accuracy": round(intent_accuracy, 4),
        "tool_selection_precision": round(tool_selection_precision, 4),
        "deny_coverage_delta": round(deny_coverage_delta, 4),
        "mandatory_deny_violation_count": mandatory_violations,
        "baseline_deny_coverage": round(baseline_deny_coverage, 4),
        "optimized_deny_coverage": round(optimized_deny_coverage, 4),
        "governance_gate_passed": governance_gate_passed,
        "evaluated_examples": total,
        "mandatory_deny_cases": mandatory_cases,
        "notes": notes,
        "predictions": predictions,
    }


def build_artifact_bundle(
    dataset: PreparedDataset,
    training_result: BackendTrainingResult,
    evaluation_summary: Mapping[str, Any],
    config: TrainingConfig,
) -> dict[str, Any]:
    bundle_id = f"prompt-{dataset.strategy_id}-{training_result.run_id}"
    prompt_bundle = {
        "bundle_id": bundle_id,
        "strategy_id": dataset.strategy_id,
        "version": config.version,
        "dspy_version": training_result.framework_version,
        "optimizer": training_result.optimizer,
        "program_refs": [
            {
                "program_name": program_name,
                "storage_ref": f"artifact://prompt_bundle/{bundle_id}/{program_name}.json",
                "signature_ref": program_payload.get("signature", ""),
            }
            for program_name, program_payload in training_result.program_payloads.items()
        ],
        "training_run_id": training_result.run_id,
        "evaluation_summary": {
            "intent_accuracy": evaluation_summary["intent_accuracy"],
            "tool_selection_precision": evaluation_summary["tool_selection_precision"],
            "deny_coverage_delta": evaluation_summary["deny_coverage_delta"],
            "mandatory_deny_violation_count": evaluation_summary["mandatory_deny_violation_count"],
            "notes": list(evaluation_summary.get("notes", [])),
        },
        "registry_hints": {
            "artifact_type": "prompt_bundle",
            "initial_lifecycle_state": config.lifecycle_state,
        },
    }
    if dataset.base_bundle_ref:
        prompt_bundle["registry_hints"]["base_bundle_ref"] = dataset.base_bundle_ref
    if dataset.source_dataset_refs:
        prompt_bundle["registry_hints"]["lineage_ref"] = dataset.source_dataset_refs[0]

    validate_prompt_bundle(prompt_bundle)
    return {
        "schema_version": "1.0",
        "artifact_family": "prompt_bundle",
        "framework": "dspy",
        "framework_version": training_result.framework_version,
        "optimizer": training_result.optimizer,
        "created_at": utc_now(),
        "created_by": config.requested_by,
        "dataset_summary": dataset.dataset_summary(),
        "prompt_bundle": prompt_bundle,
        "program_payloads": copy.deepcopy(training_result.program_payloads),
        "evaluation_report": copy.deepcopy(dict(evaluation_summary)),
        "governance": {
            "eligible_actor_roles": sorted(ALLOWED_ACTOR_ROLES),
            "eligible_promotion_states": sorted(ALLOWED_PROMOTION_STATES),
            "direct_live_influence": False,
            "filtered_examples": copy.deepcopy(dataset.filtered_examples),
            "notes": list(training_result.notes),
        },
    }


def build_registry_entry(
    dataset: PreparedDataset,
    training_result: BackendTrainingResult,
    artifact_bundle: Mapping[str, Any],
    config: TrainingConfig,
) -> dict[str, Any]:
    storage_path = config.storage_path_template.format(
        strategy_id=dataset.strategy_id,
        version=config.version,
    )
    lineage: dict[str, Any] = {
        "source_run_ids": [training_result.run_id],
        "source_dataset_refs": list(dataset.source_dataset_refs),
    }
    if dataset.base_bundle_ref:
        lineage["parent_registry_ids"] = [dataset.base_bundle_ref]

    return {
        "registry_id": f"reg-{dataset.strategy_id}-prompt-bundle-{config.version}",
        "artifact_type": "prompt_bundle",
        "strategy_id": dataset.strategy_id,
        "version": config.version,
        "lifecycle_state": config.lifecycle_state,
        "lineage": lineage,
        "storage_ref": {
            "backend": config.storage_backend,
            "path": storage_path,
        },
        "checksum": f"sha256:{_sha256_json(artifact_bundle)}",
        "producer_run_id": training_result.run_id,
        "evaluation_summary": copy.deepcopy(artifact_bundle["prompt_bundle"]["evaluation_summary"]),
        "metadata": {
            "model_family": "persona_policy",
            "framework": "dspy",
            "framework_version": training_result.framework_version,
            "optimizer": training_result.optimizer,
            "optimized_programs": dataset.optimized_programs(),
            "source_feedback_event_ids": dataset.feedback_event_ids(),
            "governance_filtered_examples": copy.deepcopy(dataset.filtered_examples),
        },
    }


def load_prompt_bundle_schema() -> dict[str, Any]:
    return json.loads(PROMPT_BUNDLE_SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_prompt_bundle(payload: Mapping[str, Any]) -> None:
    schema = load_prompt_bundle_schema()
    _validate_schema_node(schema, payload, "$")


def _validate_schema_node(schema: Mapping[str, Any], value: Any, path: str) -> None:
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(value, Mapping):
            raise DSPyWorkflowError(f"{path} must be an object")
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise DSPyWorkflowError(f"{path}.{key} is required")
        properties = schema.get("properties", {})
        additional_properties = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                _validate_schema_node(properties[key], item, f"{path}.{key}")
            elif additional_properties is False:
                raise DSPyWorkflowError(f"{path}.{key} is not allowed by schema")
        return

    if expected_type == "array":
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise DSPyWorkflowError(f"{path} must be an array")
        min_items = schema.get("minItems")
        if min_items is not None and len(value) < int(min_items):
            raise DSPyWorkflowError(f"{path} must contain at least {min_items} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate_schema_node(item_schema, item, f"{path}[{index}]")
        return

    if expected_type == "string":
        if not isinstance(value, str):
            raise DSPyWorkflowError(f"{path} must be a string")
        pattern = schema.get("pattern")
        if pattern and re.fullmatch(pattern, value) is None:
            raise DSPyWorkflowError(f"{path} does not match required pattern")
        enum = schema.get("enum")
        if enum and value not in enum:
            raise DSPyWorkflowError(f"{path} must be one of {enum}")
        const = schema.get("const")
        if const is not None and value != const:
            raise DSPyWorkflowError(f"{path} must equal {const}")
        return

    if expected_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise DSPyWorkflowError(f"{path} must be numeric")
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            raise DSPyWorkflowError(f"{path} must be >= {minimum}")
        if maximum is not None and value > maximum:
            raise DSPyWorkflowError(f"{path} must be <= {maximum}")
        return

    if expected_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise DSPyWorkflowError(f"{path} must be an integer")
        minimum = schema.get("minimum")
        if minimum is not None and value < minimum:
            raise DSPyWorkflowError(f"{path} must be >= {minimum}")
        return

    enum = schema.get("enum")
    if enum and value not in enum:
        raise DSPyWorkflowError(f"{path} must be one of {enum}")
    const = schema.get("const")
    if const is not None and value != const:
        raise DSPyWorkflowError(f"{path} must equal {const}")
