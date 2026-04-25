"""Governed TRL DPO preference-learning adapter for Pantheon.

Governance boundary:
- Input: governed FB-002 preference events (approve/edit/reject) with metadata
- Output: registry-ready model_artifact (artifact_state=draft) + registry_entry
- TRL or its dependencies never write directly to registry, runtime, or LEAN.
- CI / smoke tests use StubDPOBackend (no TRL install required).

Production activation prerequisites (ACTIVATION_CRITERIA.md §1):
- ≥200 governed FB-002 events spanning ≥2 strategy families
- ≥100 valid preference pairs constructed from those events
- LP-002 imitation baseline active with approved artifacts
- At least one downstream consumer (EV-001, LP-005, or LP-001) ready
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

TRL_VERSION_PIN = "0.8.0"
PRIMARY_BACKEND = "trl_dpo"
STUB_BACKEND = "stub_dpo"

ALLOWED_ACTOR_ROLES = frozenset(["operator", "approver"])
ALLOWED_PROMOTION_STATES = frozenset(["candidate", "paper"])
ALLOWED_ACTIONS = frozenset(["approve", "edit", "reject"])

# Smoke-test floor; production requires ≥100 pairs (ACTIVATION_CRITERIA §1.3)
MIN_PREFERENCE_PAIRS = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


class TRLWorkflowError(ValueError):
    """Raised when a governed TRL workflow cannot be built safely."""


@dataclass(frozen=True)
class PreferencePair:
    """A single governed preference pair derived from FB-002 events.

    chosen: serialized metadata of the preferred artifact (may be None for reject events)
    rejected: serialized metadata of the non-preferred artifact (may be None for approve events)
    actor_role: operator or approver — must be in ALLOWED_ACTOR_ROLES
    promotion_state: candidate or paper — must be in ALLOWED_PROMOTION_STATES
    feedback_event_id: source FB-002 event ID for lineage
    action: approve / edit / reject
    strategy_family: strategy family label for diversity gate
    """

    chosen: dict[str, Any]
    rejected: dict[str, Any]
    actor_role: str
    promotion_state: str
    feedback_event_id: str
    action: str
    strategy_family: str

    def __post_init__(self) -> None:
        if self.actor_role not in ALLOWED_ACTOR_ROLES:
            raise TRLWorkflowError(
                f"actor_role '{self.actor_role}' not in {sorted(ALLOWED_ACTOR_ROLES)}"
            )
        if self.promotion_state not in ALLOWED_PROMOTION_STATES:
            raise TRLWorkflowError(
                f"promotion_state '{self.promotion_state}' not in "
                f"{sorted(ALLOWED_PROMOTION_STATES)}"
            )
        if self.action not in ALLOWED_ACTIONS:
            raise TRLWorkflowError(
                f"action '{self.action}' not in {sorted(ALLOWED_ACTIONS)}"
            )


@dataclass(frozen=True)
class PreferencePairDataset:
    dataset_id: str
    strategy_id: str
    source_feedback_event_ids: tuple[str, ...]
    source_dataset_refs: tuple[str, ...]
    pairs: tuple[PreferencePair, ...]
    action_distribution: dict[str, int]
    strategy_families: tuple[str, ...]
    num_operators: int

    @property
    def num_pairs(self) -> int:
        return len(self.pairs)

    def dataset_summary(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "strategy_id": self.strategy_id,
            "num_pairs": self.num_pairs,
            "num_operators": self.num_operators,
            "action_distribution": self.action_distribution,
            "strategy_families": list(self.strategy_families),
            "source_feedback_event_count": len(self.source_feedback_event_ids),
            "source_dataset_refs": list(self.source_dataset_refs),
        }


@dataclass(frozen=True)
class TrainingConfig:
    version: str = "1.0.0"
    requested_by: str = "Claude"
    method: str = "dpo"
    beta: float = 0.1
    learning_rate: float = 5e-6
    batch_size: int = 16
    num_epochs: int = 3
    seed: int = 42
    storage_backend: str = "object_store"
    storage_path_template: str = "learning/trl/{strategy_id}/{version}/artifact.bin"


@dataclass(frozen=True)
class DPOTrainingResult:
    backend: str
    run_id: str
    model_payload: dict[str, Any]
    metrics: dict[str, Any]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class TRLRunResult:
    preference_dataset: PreferencePairDataset
    training_result: DPOTrainingResult
    artifact_bundle: dict[str, Any]
    registry_entry: dict[str, Any]


class DPOBackend(Protocol):
    def train(
        self, dataset: PreferencePairDataset, config: TrainingConfig
    ) -> DPOTrainingResult:
        ...


class GovernedPreferencePairAdapter:
    """Constructs governed preference pairs from FB-002 feedback events.

    Governance rules (PREFERENCE_LEARNING_CONTRACT.md §4):
    - Only events with actor_role in ALLOWED_ACTOR_ROLES are accepted.
    - Only events with promotion_state in ALLOWED_PROMOTION_STATES are accepted.
    - Missing artifact linkage causes rejection of the event.
    - Each event must carry a unique feedback_event_id for lineage.
    - Deduplication within 24-hour windows is the caller's responsibility at production scale.

    Pair construction:
    - approve → chosen=artifact, rejected=null_artifact_stub
    - reject  → chosen=null_artifact_stub, rejected=artifact
    - edit    → chosen=artifact_edited, rejected=artifact_original
    """

    _NULL_ARTIFACT_STUB: dict[str, Any] = {"artifact_id": "__null__", "is_null": True}

    def build_dataset(
        self,
        events: Sequence[Mapping[str, Any]],
        *,
        dataset_id: str,
        strategy_id: str,
        source_dataset_refs: Sequence[str],
    ) -> PreferencePairDataset:
        if not events:
            raise TRLWorkflowError("events must be a non-empty sequence")
        if not dataset_id.strip():
            raise TRLWorkflowError("dataset_id must be a non-empty string")
        if not strategy_id.strip():
            raise TRLWorkflowError("strategy_id must be a non-empty string")
        if not source_dataset_refs:
            raise TRLWorkflowError("source_dataset_refs must be non-empty")

        pairs: list[PreferencePair] = []
        feedback_event_ids: list[str] = []
        action_counts: dict[str, int] = {"approve": 0, "edit": 0, "reject": 0}
        families: set[str] = set()
        operators: set[str] = set()

        for i, event in enumerate(events):
            evt_id = self._req_str(event, "feedback_event_id", f"events[{i}]")
            actor_role = self._req_str(event, "actor_role", f"events[{i}]")
            promotion_state = self._req_str(event, "promotion_state", f"events[{i}]")
            action = self._req_str(event, "action", f"events[{i}]")
            strategy_family = self._req_str(event, "strategy_family", f"events[{i}]")
            operator_id = event.get("operator_id", "")

            if actor_role not in ALLOWED_ACTOR_ROLES:
                raise TRLWorkflowError(
                    f"events[{i}].actor_role '{actor_role}' not in "
                    f"{sorted(ALLOWED_ACTOR_ROLES)}"
                )
            if promotion_state not in ALLOWED_PROMOTION_STATES:
                raise TRLWorkflowError(
                    f"events[{i}].promotion_state '{promotion_state}' not in "
                    f"{sorted(ALLOWED_PROMOTION_STATES)}"
                )
            if action not in ALLOWED_ACTIONS:
                raise TRLWorkflowError(
                    f"events[{i}].action '{action}' not in {sorted(ALLOWED_ACTIONS)}"
                )

            artifact = event.get("artifact")
            if not isinstance(artifact, Mapping):
                raise TRLWorkflowError(
                    f"events[{i}] missing artifact mapping (required for pair construction)"
                )
            artifact_id = artifact.get("artifact_id", "")
            if not artifact_id:
                raise TRLWorkflowError(
                    f"events[{i}].artifact must include a non-empty artifact_id"
                )

            if action == "approve":
                chosen = dict(artifact)
                rejected = copy.deepcopy(self._NULL_ARTIFACT_STUB)
            elif action == "reject":
                chosen = copy.deepcopy(self._NULL_ARTIFACT_STUB)
                rejected = dict(artifact)
            else:
                # edit: artifact_edited is "chosen", artifact is the original "rejected"
                edited = event.get("artifact_edited")
                if not isinstance(edited, Mapping) or not edited.get("artifact_id"):
                    raise TRLWorkflowError(
                        f"events[{i}] with action='edit' must include a non-empty artifact_edited"
                    )
                chosen = dict(edited)
                rejected = dict(artifact)

            pairs.append(
                PreferencePair(
                    chosen=chosen,
                    rejected=rejected,
                    actor_role=actor_role,
                    promotion_state=promotion_state,
                    feedback_event_id=evt_id,
                    action=action,
                    strategy_family=strategy_family,
                )
            )
            feedback_event_ids.append(evt_id)
            action_counts[action] = action_counts.get(action, 0) + 1
            families.add(strategy_family)
            if operator_id:
                operators.add(str(operator_id))

        if len(pairs) < MIN_PREFERENCE_PAIRS:
            raise TRLWorkflowError(
                f"dataset must have at least {MIN_PREFERENCE_PAIRS} preference pairs; "
                f"got {len(pairs)}"
            )

        return PreferencePairDataset(
            dataset_id=dataset_id,
            strategy_id=strategy_id,
            source_feedback_event_ids=tuple(feedback_event_ids),
            source_dataset_refs=tuple(source_dataset_refs),
            pairs=tuple(pairs),
            action_distribution=action_counts,
            strategy_families=tuple(sorted(families)),
            num_operators=max(len(operators), 1),
        )

    def _req_str(self, event: Mapping[str, Any], key: str, ctx: str) -> str:
        val = event.get(key)
        if not isinstance(val, str) or not val.strip():
            raise TRLWorkflowError(f"{ctx}.{key} must be a non-empty string")
        return val.strip()


class StubDPOBackend:
    """Deterministic DPO stub for CI and smoke tests.

    Computes a simple majority-label accuracy metric without any ML dependencies.
    Produces a plausible but clearly synthetic model payload.
    """

    def train(
        self, dataset: PreferencePairDataset, config: TrainingConfig
    ) -> DPOTrainingResult:
        run_id = f"trl-stub-{uuid.uuid4().hex[:12]}"
        n = dataset.num_pairs
        # Stub: "accuracy" = fraction of non-null chosen (approve + edit events)
        non_null_chosen = sum(
            1 for p in dataset.pairs if not p.chosen.get("is_null", False)
        )
        accuracy = round(non_null_chosen / n if n > 0 else 0.0, 4)
        auc_roc = round(min(accuracy + 0.05, 0.99), 4)  # synthetic stub value

        metrics = {
            "num_pairs": n,
            "num_operators": dataset.num_operators,
            "accuracy": accuracy,
            "auc_roc": auc_roc,
            "coverage": 1.0,
            "backend": STUB_BACKEND,
        }
        model_payload = {
            "predictor": "stub_dpo",
            "method": config.method,
            "beta": config.beta,
            "strategy_id": dataset.strategy_id,
            "num_pairs_trained": n,
            "action_distribution": dataset.action_distribution,
        }
        return DPOTrainingResult(
            backend=STUB_BACKEND,
            run_id=run_id,
            model_payload=model_payload,
            metrics=metrics,
            notes=(
                "Stub DPO backend is intended for governed smoke tests and packaging validation.",
                "Production training requires trl>=0.8.0 and real FB-002 event volume.",
            ),
        )


class TRLDPOBackend:
    """Optional upstream TRL DPO backend.

    Requires: pip install -r services/learning/trl/requirements.txt
    Production activation prerequisites: ≥100 preference pairs, ≥200 FB-002 events.
    """

    def train(
        self, dataset: PreferencePairDataset, config: TrainingConfig
    ) -> DPOTrainingResult:
        try:
            from trl import DPOConfig, DPOTrainer  # type: ignore
            from transformers import AutoModelForSequenceClassification, AutoTokenizer  # type: ignore
            import torch  # type: ignore
        except ImportError as exc:
            raise TRLWorkflowError(
                "TRL backend unavailable. "
                "Install services/learning/trl/requirements.txt first."
            ) from exc

        # Serialize each pair into prompt strings for the DPO trainer
        chosen_texts = [
            json.dumps(p.chosen, sort_keys=True, ensure_ascii=False)
            for p in dataset.pairs
        ]
        rejected_texts = [
            json.dumps(p.rejected, sort_keys=True, ensure_ascii=False)
            for p in dataset.pairs
        ]

        # Lightweight model for smoke validation — distilbert or equivalent
        model_name = "distilbert-base-uncased"
        try:
            tokenizer = AutoTokenizer.from_pretrained(model_name)
            model = AutoModelForSequenceClassification.from_pretrained(model_name, num_labels=1)
            ref_model = AutoModelForSequenceClassification.from_pretrained(
                model_name, num_labels=1
            )
        except Exception as exc:
            raise TRLWorkflowError(
                f"Failed to load base model '{model_name}' for TRL DPO smoke: {exc}"
            ) from exc

        try:
            from datasets import Dataset  # type: ignore

            hf_dataset = Dataset.from_dict(
                {
                    "prompt": ["preference_score"] * len(chosen_texts),
                    "chosen": chosen_texts,
                    "rejected": rejected_texts,
                }
            )

            dpo_config = DPOConfig(
                output_dir="/tmp/trl-dpo-smoke",
                num_train_epochs=1,
                per_device_train_batch_size=min(config.batch_size, len(dataset.pairs)),
                learning_rate=config.learning_rate,
                beta=config.beta,
                seed=config.seed,
                logging_steps=1,
                save_strategy="no",
                report_to="none",
            )
            trainer = DPOTrainer(
                model=model,
                ref_model=ref_model,
                args=dpo_config,
                train_dataset=hf_dataset,
                processing_class=tokenizer,
            )
            trainer.train()
        except Exception as exc:
            raise TRLWorkflowError(f"TRL DPO training failed: {exc}") from exc

        run_id = f"trl-dpo-{uuid.uuid4().hex[:12]}"
        metrics = {
            "num_pairs": dataset.num_pairs,
            "num_operators": dataset.num_operators,
            "backend": PRIMARY_BACKEND,
            "trl_version": TRL_VERSION_PIN,
            "base_model": model_name,
            "num_epochs": dpo_config.num_train_epochs,
            "note": "Smoke path — production training requires full preference pair volume.",
        }
        model_payload = {
            "framework": "trl",
            "method": "dpo",
            "framework_version": TRL_VERSION_PIN,
            "base_model": model_name,
            "beta": config.beta,
            "learning_rate": config.learning_rate,
            "num_epochs": config.num_epochs,
            "strategy_id": dataset.strategy_id,
            "serialization_note": (
                "Serialize model weights separately before final registry submission."
            ),
        }
        return DPOTrainingResult(
            backend=PRIMARY_BACKEND,
            run_id=run_id,
            model_payload=model_payload,
            metrics=metrics,
            notes=(
                "TRL DPO smoke run complete. Persist model weights before production use.",
                f"trl version: {TRL_VERSION_PIN}",
            ),
        )


def run_trl_dpo_workflow(
    events: Sequence[Mapping[str, Any]],
    *,
    dataset_id: str,
    strategy_id: str,
    source_dataset_refs: Sequence[str],
    backend: DPOBackend | None = None,
    config: TrainingConfig | None = None,
) -> TRLRunResult:
    """Main governed TRL workflow entrypoint.

    Builds a preference-pair dataset from governed FB-002 events, trains a DPO model,
    and emits a registry-ready artifact with artifact_state=draft.
    """
    adapter = GovernedPreferencePairAdapter()
    preference_dataset = adapter.build_dataset(
        events,
        dataset_id=dataset_id,
        strategy_id=strategy_id,
        source_dataset_refs=source_dataset_refs,
    )
    training_config = config or TrainingConfig()
    trainer = backend or StubDPOBackend()
    training_result = trainer.train(preference_dataset, training_config)
    artifact_bundle = _build_artifact_bundle(preference_dataset, training_result, training_config)
    registry_entry = _build_registry_entry(
        preference_dataset, training_result, artifact_bundle, training_config
    )
    return TRLRunResult(
        preference_dataset=preference_dataset,
        training_result=training_result,
        artifact_bundle=artifact_bundle,
        registry_entry=registry_entry,
    )


def _build_artifact_bundle(
    dataset: PreferencePairDataset,
    result: DPOTrainingResult,
    config: TrainingConfig,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "artifact_family": "trl_preference_model",
        "model_family": "preference_model",
        "framework": "trl",
        "framework_version": TRL_VERSION_PIN,
        "created_at": utc_now(),
        "created_by": config.requested_by,
        "dataset_summary": dataset.dataset_summary(),
        "training_config": {
            "version": config.version,
            "method": config.method,
            "beta": config.beta,
            "learning_rate": config.learning_rate,
            "batch_size": config.batch_size,
            "num_epochs": config.num_epochs,
            "seed": config.seed,
            "requested_by": config.requested_by,
        },
        "model": copy.deepcopy(result.model_payload),
        "evaluation_summary": copy.deepcopy(result.metrics),
        "governance": {
            "allowed_actor_roles": sorted(ALLOWED_ACTOR_ROLES),
            "allowed_promotion_states": sorted(ALLOWED_PROMOTION_STATES),
            "direct_live_influence": False,
            "output_type": "preference_model",
            "execution_stage": "none",
            "consumption_pattern": "evaluator_augmentation_reward_shaping_persona_policy",
            "notes": list(result.notes),
        },
        "registry_hints": {
            "artifact_type": "model_artifact",
            "model_family": "preference_model",
            "artifact_state": "draft",
            "deployment_stage": "none",
            "source_feedback_event_ids": list(dataset.source_feedback_event_ids),
        },
    }


def _build_registry_entry(
    dataset: PreferencePairDataset,
    result: DPOTrainingResult,
    artifact_bundle: Mapping[str, Any],
    config: TrainingConfig,
) -> dict[str, Any]:
    storage_path = config.storage_path_template.format(
        strategy_id=dataset.strategy_id,
        version=config.version,
    )
    return {
        "registry_id": f"trl-preference-model-{dataset.strategy_id}-{config.version}",
        "artifact_type": "model_artifact",
        "strategy_id": dataset.strategy_id,
        "version": config.version,
        "artifact_state": "draft",
        "deployment_summary": {"current_stage": "none"},
        "created_at": artifact_bundle["created_at"],
        "lineage": {
            "source_run_ids": [result.run_id],
            "source_dataset_refs": list(dataset.source_dataset_refs),
            "source_feedback_event_ids": list(dataset.source_feedback_event_ids),
        },
        "storage_ref": {
            "backend": config.storage_backend,
            "path": storage_path,
        },
        "checksum": f"sha256:{_sha256_json(artifact_bundle)}",
        "producer_run_id": result.run_id,
        "evaluation_summary": copy.deepcopy(result.metrics),
        "metadata": {
            "framework": "trl",
            "model_family": "preference_model",
            "algorithm": config.method,
            "framework_version": TRL_VERSION_PIN,
            "training_backend": result.backend,
            "num_preference_pairs": dataset.num_pairs,
            "num_operators": dataset.num_operators,
            "action_distribution": dataset.action_distribution,
            "strategy_families": list(dataset.strategy_families),
            "entry_criteria_satisfied": {
                "version_pinned": True,
                "pair_construction_pipeline_present": True,
                "dpo_smoke_path_present": True,
                "production_data_volume_not_yet_met": True,
            },
        },
        "approved_at": None,
        "approver": None,
        "rollback_target": None,
    }
