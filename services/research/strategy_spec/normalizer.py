"""
RS-002 StrategySpec normalizer.

Consumes RS-001 governed research handoffs and emits:
1. Canonical StrategySpec payloads aligned to OC-003.
2. Canonical WorkflowHandoff envelopes for downstream registry/execution work.
3. A legacy compatibility handoff for the current RS-003 replication gate.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from jsonschema import Draft7Validator, RefResolver

from .models import StrategySpecValidationError, validate_strategy_spec_payload


class StrategySpecNormalizationError(ValueError):
    """Raised when RS-001 material cannot be normalized safely."""


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slugify(value: str, fallback: str = "strategy") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or fallback


def _ensure_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _first_sentence(text: str) -> str:
    compact = " ".join((text or "").split())
    if not compact:
        return ""
    match = re.split(r"(?<=[.!?])\s+", compact, maxsplit=1)
    return match[0].strip()


@dataclass(frozen=True)
class NormalizationResult:
    """Canonical RS-002 outputs plus RS-003 compatibility data."""

    strategy_spec: Dict[str, Any]
    workflow_handoff: Dict[str, Any]
    replication_handoff: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_spec": self.strategy_spec,
            "workflow_handoff": self.workflow_handoff,
            "replication_handoff": self.replication_handoff,
        }

    def to_replication_payload(
        self,
        candidate_id: str,
        metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Build an RS-003-compatible payload without downgrading the StrategySpec."""
        return {
            "candidate_id": candidate_id,
            "source_task_id": "RS-002",
            "research_handoff": self.replication_handoff,
            "proposed_strategy_spec": self.strategy_spec,
            "metadata": {
                "workflow_handoff_id": self.workflow_handoff["handoff_id"],
                "strategy_id": self.strategy_spec["strategy_id"],
                "compatibility_view": "legacy_research_handoff",
                **(metadata or {}),
            },
        }


class StrategySpecNormalizer:
    """Normalize RS-001 research handoffs into the governed OC-003 objects."""

    SOURCE_KIND_MAP = {
        "academic_paper": "paper",
        "code_repository": "repo",
        "research_note": "note",
    }

    ALLOWED_SOURCE_TYPES = set(SOURCE_KIND_MAP)
    DEFAULT_METRICS = ["sharpe_ratio", "max_drawdown", "win_rate"]
    DEFAULT_SIGNAL_SCHEMA_VERSION = "1.0"
    DEFAULT_QUANTITY_TYPE = "PERCENT_PORTFOLIO"
    DEFAULT_POLICY_ID = "oc-001-research-normalization"

    def __init__(self, schema_dir: Path | None = None, created_by: str = "Codex") -> None:
        repo_root = Path(__file__).resolve().parents[3]
        self.schema_dir = schema_dir or (repo_root / "services" / "control-plane" / "specs")
        self.created_by = created_by
        self._workflow_handoff_validator = self._build_validator(
            self.schema_dir / "workflow_handoff.schema.json"
        )

    def normalize(self, research_handoff: Dict[str, Any]) -> NormalizationResult:
        """Normalize one RS-001 handoff into canonical and compatibility outputs."""
        self._validate_input_handoff(research_handoff)

        handoff = copy.deepcopy(research_handoff)
        source_type = handoff["source_type"]
        source_metadata = handoff["source_metadata"]
        findings = handoff["normalized_findings"]
        legacy_spec = findings["strategy_spec"]
        processing_notes = handoff["grok_processing_notes"]
        created_at = _iso_now()
        title = findings["strategy_spec"].get("name") or findings["strategy_spec"].get("title") or "Unnamed Strategy"
        primary_ref = self._primary_source_ref(handoff)
        strategy_id = self._build_strategy_id(title, primary_ref)
        signals = [str(item) for item in _ensure_list(legacy_spec.get("signals")) if str(item).strip()]
        parameters = legacy_spec.get("parameters") if isinstance(legacy_spec.get("parameters"), dict) else {}
        initial_state = self._determine_initial_lifecycle_state(processing_notes)

        strategy_spec = {
            "spec_version": "1.0",
            "strategy_id": strategy_id,
            "title": title,
            "hypothesis": self._build_hypothesis(legacy_spec, findings, signals, parameters),
            "objective": self._build_objective(title, findings),
            "market_scope": self._build_market_scope(legacy_spec, source_type, findings),
            "data_dependencies": self._build_data_dependencies(handoff, legacy_spec),
            "execution_profile": self._build_execution_profile(legacy_spec, parameters, findings),
            "evaluation_plan": self._build_evaluation_plan(findings),
            "governance": self._build_governance(processing_notes),
            "provenance": self._build_provenance(
                source_type=source_type,
                created_at=created_at,
                primary_ref=primary_ref,
                source_metadata=source_metadata,
            ),
        }
        self.validate_strategy_spec(strategy_spec)

        workflow_handoff = {
            "handoff_version": "1.0",
            "handoff_id": f"handoff-{strategy_id}",
            "handoff_type": "strategy_spec",
            "from_stage": "research_normalization",
            "to_stage": "replication_gate",
            "created_at": created_at,
            "strategy_spec": strategy_spec,
            "registry_hints": {
                "artifact_type": "strategy_spec",
                "initial_lifecycle_state": initial_state,
                "lineage_ref": primary_ref,
                "producer_run_id": f"rs002-{strategy_id}",
                "source_dataset_refs": [
                    dependency["ref"]
                    for dependency in strategy_spec["data_dependencies"]
                    if dependency["kind"] in {"dataset", "feature_set"}
                ],
            },
            "governance_context": {
                "approval_required": strategy_spec["governance"]["approval_required"],
                "execution_context": "research",
                "policy_id": strategy_spec["governance"].get("policy_id"),
                "risk_profile": strategy_spec["governance"].get("risk_profile"),
            },
            "provenance": {
                "created_by": self.created_by,
                "created_at": created_at,
                "source_task_id": handoff["task_id"],
                "source_channel": source_type,
                "source_persona": "Grok",
            },
        }
        self.validate_workflow_handoff(workflow_handoff)

        replication_handoff = self._build_replication_handoff(
            handoff=handoff,
            strategy_spec=strategy_spec,
            workflow_handoff=workflow_handoff,
            signals=signals,
            parameters=parameters,
        )

        return NormalizationResult(
            strategy_spec=strategy_spec,
            workflow_handoff=workflow_handoff,
            replication_handoff=replication_handoff,
        )

    def validate_strategy_spec(self, strategy_spec: Dict[str, Any]) -> None:
        try:
            validate_strategy_spec_payload(strategy_spec, self.schema_dir / "strategy_spec.schema.json")
        except StrategySpecValidationError as exc:
            raise StrategySpecNormalizationError(str(exc)) from exc

    def validate_workflow_handoff(self, workflow_handoff: Dict[str, Any]) -> None:
        errors = sorted(
            self._workflow_handoff_validator.iter_errors(workflow_handoff),
            key=lambda item: list(item.path),
        )
        if errors:
            first = errors[0]
            location = ".".join(str(part) for part in first.path) or "<root>"
            raise StrategySpecNormalizationError(
                f"WorkflowHandoff validation failed at {location}: {first.message}"
            )

    def _build_validator(self, schema_path: Path) -> Draft7Validator:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        strategy_schema_path = self.schema_dir / "strategy_spec.schema.json"
        strategy_schema = json.loads(strategy_schema_path.read_text(encoding="utf-8"))
        store = {
            schema.get("$id", schema_path.resolve().as_uri()): schema,
            schema_path.resolve().as_uri(): schema,
            strategy_schema.get("$id", strategy_schema_path.resolve().as_uri()): strategy_schema,
            strategy_schema_path.resolve().as_uri(): strategy_schema,
            f"{schema_path.parent.resolve().as_uri()}/strategy_spec.schema.json": strategy_schema,
            "https://pantheon/workflow-handoff/strategy_spec.schema.json": strategy_schema,
        }
        resolver = RefResolver(
            base_uri=f"{schema_path.parent.resolve().as_uri()}/",
            referrer=schema,
            store=store,
        )
        return Draft7Validator(schema, resolver=resolver)

    def _validate_input_handoff(self, handoff: Dict[str, Any]) -> None:
        if not isinstance(handoff, dict):
            raise StrategySpecNormalizationError("RS-001 handoff must be a JSON object")
        if handoff.get("task_id") != "RS-001":
            raise StrategySpecNormalizationError("RS-002 only accepts RS-001 handoffs")
        source_type = handoff.get("source_type")
        if source_type not in self.ALLOWED_SOURCE_TYPES:
            raise StrategySpecNormalizationError(f"Unsupported source_type: {source_type}")

        source_metadata = handoff.get("source_metadata")
        normalized_findings = handoff.get("normalized_findings")
        processing_notes = handoff.get("grok_processing_notes")
        if not isinstance(source_metadata, dict):
            raise StrategySpecNormalizationError("source_metadata is required")
        if not isinstance(normalized_findings, dict):
            raise StrategySpecNormalizationError("normalized_findings is required")
        if not isinstance(processing_notes, dict):
            raise StrategySpecNormalizationError("grok_processing_notes is required")
        if not isinstance(normalized_findings.get("strategy_spec"), dict):
            raise StrategySpecNormalizationError("normalized_findings.strategy_spec is required")
        if not source_metadata.get("retrieved_at"):
            raise StrategySpecNormalizationError("source_metadata.retrieved_at is required")
        if not source_metadata.get("governance_context"):
            raise StrategySpecNormalizationError("source_metadata.governance_context is required")

    def _primary_source_ref(self, handoff: Dict[str, Any]) -> str:
        source_metadata = handoff["source_metadata"]
        legacy_spec = handoff["normalized_findings"]["strategy_spec"]

        candidates = [
            legacy_spec.get("source_paper"),
            legacy_spec.get("doi"),
            legacy_spec.get("source_repository"),
            source_metadata.get("repository_url"),
            source_metadata.get("api_endpoint"),
            source_metadata.get("source_url"),
        ]
        for candidate in candidates:
            if candidate:
                return str(candidate)
        return f"{handoff['source_type']}:{handoff['task_id']}"

    def _build_strategy_id(self, title: str, primary_ref: str) -> str:
        digest = hashlib.sha1(primary_ref.encode("utf-8")).hexdigest()[:8]
        return f"strat-{_slugify(title)[:40]}-{digest}"

    def _build_hypothesis(
        self,
        legacy_spec: Dict[str, Any],
        findings: Dict[str, Any],
        signals: List[str],
        parameters: Dict[str, Any],
    ) -> str:
        base = _first_sentence(legacy_spec.get("description") or findings.get("replication_notes") or "")
        if not base:
            base = "Source material proposes an investable signal with governed research provenance."

        signal_clause = (
            f" Signal hints: {', '.join(signals[:5])}."
            if signals
            else " Signal hints remain source-defined and need deeper extraction during replication."
        )
        if parameters:
            preview = ", ".join(f"{key}={parameters[key]}" for key in sorted(parameters)[:4])
            parameter_clause = f" Parameter anchors: {preview}."
        else:
            parameter_clause = ""
        return f"{base}{signal_clause}{parameter_clause}".strip()

    def _build_objective(self, title: str, findings: Dict[str, Any]) -> str:
        hypotheses = " ".join((findings.get("evaluation_hypotheses") or "").split())
        if hypotheses:
            return hypotheses
        return (
            f"Replicate and evaluate {title} under governed research, registry, and promotion gates "
            "before any paper or live promotion."
        )

    def _build_market_scope(
        self,
        legacy_spec: Dict[str, Any],
        source_type: str,
        findings: Dict[str, Any],
    ) -> Dict[str, Any]:
        metadata = legacy_spec.get("metadata") if isinstance(legacy_spec.get("metadata"), dict) else {}
        symbols = [str(item) for item in _ensure_list(legacy_spec.get("symbols") or metadata.get("symbols")) if str(item).strip()]
        asset_classes = [str(item) for item in _ensure_list(legacy_spec.get("asset_classes") or metadata.get("asset_classes")) if str(item).strip()]
        venues = [str(item) for item in _ensure_list(legacy_spec.get("venues") or metadata.get("venues")) if str(item).strip()]
        frequency = (
            legacy_spec.get("frequency")
            or metadata.get("frequency")
            or self._infer_frequency(findings.get("replication_notes") or findings.get("evaluation_hypotheses") or "")
        )

        if not asset_classes:
            asset_classes = self._infer_asset_classes(
                " ".join(
                    filter(
                        None,
                        [
                            legacy_spec.get("name"),
                            legacy_spec.get("description"),
                            findings.get("replication_notes"),
                        ],
                    )
                )
            )

        if not symbols:
            symbols = [self._default_symbol(asset_classes, source_type)]

        scope = {
            "symbols": symbols,
            "frequency": frequency or "unspecified",
        }
        if asset_classes:
            scope["asset_classes"] = asset_classes
        if venues:
            scope["venues"] = venues
        return scope

    def _build_data_dependencies(
        self,
        handoff: Dict[str, Any],
        legacy_spec: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        dependencies: List[Dict[str, str]] = []
        source_type = handoff["source_type"]
        source_metadata = handoff["source_metadata"]
        kind = {
            "academic_paper": "paper",
            "code_repository": "repo",
            "research_note": "note",
        }[source_type]

        self._append_dependency(dependencies, kind, self._primary_source_ref(handoff))
        self._append_dependency(dependencies, kind, source_metadata.get("api_endpoint"))
        self._append_dependency(dependencies, kind, source_metadata.get("repository_url"))

        dataset_refs = _ensure_list(legacy_spec.get("dataset_refs"))
        for ref in dataset_refs:
            self._append_dependency(dependencies, "dataset", ref)

        feature_refs = _ensure_list(legacy_spec.get("feature_set_refs"))
        for ref in feature_refs:
            self._append_dependency(dependencies, "feature_set", ref)

        for file_entry in _ensure_list(legacy_spec.get("key_files")):
            if isinstance(file_entry, dict):
                self._append_dependency(
                    dependencies,
                    "note",
                    file_entry.get("path") or file_entry.get("name"),
                )

        if not dependencies:
            dependencies.append({"ref": self._primary_source_ref(handoff), "kind": kind})
        return dependencies

    def _append_dependency(
        self,
        dependencies: List[Dict[str, str]],
        kind: str,
        ref: Any,
    ) -> None:
        if not ref:
            return
        ref_str = str(ref)
        if any(item["ref"] == ref_str and item["kind"] == kind for item in dependencies):
            return
        dependencies.append({"ref": ref_str, "kind": kind})

    def _build_execution_profile(
        self,
        legacy_spec: Dict[str, Any],
        parameters: Dict[str, Any],
        findings: Dict[str, Any],
    ) -> Dict[str, Any]:
        signal_schema_version = (
            legacy_spec.get("signal_schema_version")
            or parameters.get("signal_schema_version")
            or self.DEFAULT_SIGNAL_SCHEMA_VERSION
        )
        quantity_type = (
            legacy_spec.get("quantity_type")
            or parameters.get("quantity_type")
            or self.DEFAULT_QUANTITY_TYPE
        )
        rebalance_cadence = (
            legacy_spec.get("rebalance_cadence")
            or parameters.get("rebalance_cadence")
            or self._infer_frequency(findings.get("replication_notes") or "")
        )

        profile = {
            "signal_schema_version": signal_schema_version,
            "quantity_type": quantity_type,
            "execution_mode_hint": "research",
        }
        if rebalance_cadence:
            profile["rebalance_cadence"] = rebalance_cadence
        return profile

    def _build_evaluation_plan(self, findings: Dict[str, Any]) -> Dict[str, Any]:
        metrics = [str(item) for item in _ensure_list(findings.get("metrics")) if str(item).strip()]
        if not metrics:
            metrics = list(self.DEFAULT_METRICS)

        return {
            "metrics": metrics,
            "candidate_gate": "Pass RS-003 replication checks with verified governance and lineage.",
            "paper_gate": "Promote through REG-002 with explicit paper approval before paper execution.",
            "live_gate": "Promote through REG-002 with explicit live approval after paper validation succeeds.",
        }

    def _build_governance(self, processing_notes: Dict[str, Any]) -> Dict[str, Any]:
        confidence = processing_notes.get("normalization_confidence", "unknown")
        readiness = processing_notes.get("downstream_readiness", "needs_clarification")
        return {
            "approval_required": True,
            "policy_id": self.DEFAULT_POLICY_ID,
            "risk_profile": f"research_only:{confidence}:{readiness}",
        }

    def _build_provenance(
        self,
        source_type: str,
        created_at: str,
        primary_ref: str,
        source_metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        source_refs = list(
            dict.fromkeys(
                ref
                for ref in [
                    primary_ref,
                    source_metadata.get("api_endpoint"),
                    source_metadata.get("repository_url"),
                    source_metadata.get("source_url"),
                ]
                if ref
            )
        )
        return {
            "source_kind": self.SOURCE_KIND_MAP[source_type],
            "created_at": created_at,
            "source_refs": source_refs,
            "created_by": self.created_by,
        }

    def _build_replication_handoff(
        self,
        handoff: Dict[str, Any],
        strategy_spec: Dict[str, Any],
        workflow_handoff: Dict[str, Any],
        signals: List[str],
        parameters: Dict[str, Any],
    ) -> Dict[str, Any]:
        findings = handoff["normalized_findings"]
        processing_notes = copy.deepcopy(handoff["grok_processing_notes"])
        processing_notes["canonical_strategy_spec_id"] = strategy_spec["strategy_id"]
        processing_notes["workflow_handoff_id"] = workflow_handoff["handoff_id"]

        source_metadata = copy.deepcopy(handoff["source_metadata"])
        source_metadata["canonical_strategy_spec_id"] = strategy_spec["strategy_id"]
        source_metadata["workflow_handoff_id"] = workflow_handoff["handoff_id"]

        return {
            "task_id": "RS-002",
            "source_type": handoff["source_type"],
            "source_metadata": source_metadata,
            "normalized_findings": {
                "strategy_spec": {
                    "name": strategy_spec["title"],
                    "description": strategy_spec["hypothesis"],
                    "signals": signals,
                    "parameters": parameters,
                    "canonical_strategy_spec_id": strategy_spec["strategy_id"],
                    "canonical_spec_version": strategy_spec["spec_version"],
                },
                "replication_notes": findings.get("replication_notes")
                or strategy_spec["hypothesis"],
                "evaluation_hypotheses": findings.get("evaluation_hypotheses")
                or strategy_spec["objective"],
            },
            "grok_processing_notes": processing_notes,
        }

    def _determine_initial_lifecycle_state(self, processing_notes: Dict[str, Any]) -> str:
        confidence = processing_notes.get("normalization_confidence", "low")
        readiness = processing_notes.get("downstream_readiness", "needs_clarification")
        governance = processing_notes.get("governance_compliance", "")
        if governance == "verified" and readiness == "ready_for_replication" and confidence in {"high", "medium"}:
            return "candidate"
        return "draft"

    def _default_symbol(self, asset_classes: Iterable[str], source_type: str) -> str:
        lowered = {item.lower() for item in asset_classes}
        if "crypto" in lowered:
            return "CRYPTO_UNIVERSE"
        if "futures" in lowered:
            return "FUTURES_UNIVERSE"
        if "forex" in lowered:
            return "FX_UNIVERSE"
        if source_type == "code_repository":
            return "REPO_DEFINED_UNIVERSE"
        return "RESEARCH_UNIVERSE"

    def _infer_asset_classes(self, text: str) -> List[str]:
        lowered = (text or "").lower()
        inferred: List[str] = []
        if any(token in lowered for token in ("crypto", "bitcoin", "ethereum")):
            inferred.append("crypto")
        if any(token in lowered for token in ("future", "futures")):
            inferred.append("futures")
        if any(token in lowered for token in ("forex", "fx", "currency")):
            inferred.append("forex")
        if any(token in lowered for token in ("equity", "equities", "stock", "stocks")):
            inferred.append("equities")
        return inferred

    def _infer_frequency(self, text: str) -> str:
        lowered = (text or "").lower()
        if "minute" in lowered:
            return "1m"
        if "hour" in lowered or "intraday" in lowered:
            return "1h"
        if "weekly" in lowered:
            return "1w"
        if "daily" in lowered:
            return "1d"
        return "unspecified"
