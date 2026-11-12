"""Hypothesis card -> factor_rule_v1 compiler (design §9.4, §8.4).

The model only *proposes* JSON. Code decides: the proposal must be a closed
object (no free code), every input must be a governed catalogue
(dataset, field), and the logic must pass ``validate_factor_rule_logic`` (which
rejects unknown operators). Anything else is recorded as ``not_expressible``
(with reason) or ``needs_data:<dataset>`` (counted per dataset). Cards with the
same rule fingerprint raise ``support_count`` instead of creating a new draft.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional

from services.registry.factor_rule import (
    BUILT_IN_PRICE_VOLUME_FIELDS,
    TIME_SERIES_OPERATORS,
    FactorRuleValidationError,
    get_feature_definition,
    list_feature_definitions,
    validate_factor_rule_logic,
)

COMPILED = "compiled"
SUPPORT_ADDED = "support_added"
NOT_EXPRESSIBLE = "not_expressible"
NEEDS_DATA = "needs_data"
SKIPPED = "skipped"

_DATASET_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")
_PROPOSAL_KEYS = {"outcome", "reason", "dataset", "strategy_logic", "parameters", "mutation_surface"}
_OUTCOMES = ("factor_rule", NOT_EXPRESSIBLE, NEEDS_DATA)

Proposer = Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]


def catalogue_summary() -> dict[str, Any]:
    """The only datasets/fields/operators the model may use."""
    datasets: dict[str, list[str]] = {}
    for d in list_feature_definitions():
        datasets.setdefault(d.dataset, []).append(d.field)
    return {
        "datasets": {k: sorted(v) for k, v in sorted(datasets.items())},
        "builtin_fields": sorted(BUILT_IN_PRICE_VOLUME_FIELDS),
        "operators": sorted(TIME_SERIES_OPERATORS),
    }


def build_compile_prompt(card: Mapping[str, Any], catalogue: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "Propose a factor_rule_v1 for the hypothesis card, or refuse.",
            "Answer with JSON: {outcome: factor_rule|not_expressible|needs_data, reason, dataset,",
            "strategy_logic, parameters, mutation_surface}. Use ONLY the datasets, fields and operators",
            "in the catalogue; every numeric constant is {const_param: key} declared in parameters and",
            "mutation_surface.controls. No code. Use needs_data with the dataset name when the data is missing.",
            "CATALOGUE: " + json.dumps(catalogue, sort_keys=True),
            "CARD: " + json.dumps({k: card.get(k) for k in ("claim", "mechanism", "signal_inputs", "rule_sketch", "horizon", "direction", "conditions")}, sort_keys=True),
        ]
    )


def rule_fingerprint(strategy_logic: Mapping[str, Any]) -> str:
    """Hash of the normalized rule structure (parameter values excluded)."""
    canon = json.dumps(strategy_logic, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CompileOutcome:
    status: str
    reason: str = ""
    dataset: str = ""
    fingerprint: str = ""
    support_count: int = 0
    artifact: Optional[dict[str, Any]] = None
    extra: dict[str, Any] = field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CompilerStore:
    """SQLite store: drafts (fingerprint -> support_count), outcomes, needs_data counts."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False)
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS rule_drafts(
              fingerprint TEXT PRIMARY KEY, artifact TEXT NOT NULL,
              support_count INTEGER NOT NULL, first_seen TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS draft_support(
              fingerprint TEXT NOT NULL, card_key TEXT NOT NULL, PRIMARY KEY(fingerprint, card_key));
            CREATE TABLE IF NOT EXISTS compile_outcomes(
              card_key TEXT PRIMARY KEY, status TEXT NOT NULL, reason TEXT NOT NULL,
              dataset TEXT NOT NULL, fingerprint TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS needs_data_counts(
              dataset TEXT PRIMARY KEY, count INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS needs_data_cards(
              dataset TEXT NOT NULL, card_key TEXT NOT NULL, PRIMARY KEY(dataset, card_key));
            """
        )

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._conn:
            yield self._conn

    def get_outcome(self, card_key: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT status,reason,dataset,fingerprint FROM compile_outcomes WHERE card_key=?", (card_key,)
            ).fetchone()
        return dict(zip(("status", "reason", "dataset", "fingerprint"), row)) if row else None

    def record_outcome(self, card_key: str, status: str, reason: str = "", dataset: str = "", fingerprint: str = "") -> None:
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO compile_outcomes VALUES(?,?,?,?,?,?)",
                (card_key, status, reason[:500], dataset, fingerprint, _now()),
            )

    def add_rule(self, fingerprint: str, artifact: Mapping[str, Any], card_key: str) -> tuple[bool, int]:
        """Returns (created, support_count). Same card_key never double counts."""
        with self._tx() as c:
            row = c.execute("SELECT support_count FROM rule_drafts WHERE fingerprint=?", (fingerprint,)).fetchone()
            if row is None:
                c.execute(
                    "INSERT INTO rule_drafts VALUES(?,?,?,?,?)",
                    (fingerprint, json.dumps(artifact, sort_keys=True), 1, _now(), _now()),
                )
                c.execute("INSERT INTO draft_support VALUES(?,?)", (fingerprint, card_key))
                return True, 1
            cur = c.execute("INSERT OR IGNORE INTO draft_support VALUES(?,?)", (fingerprint, card_key))
            if cur.rowcount:
                c.execute(
                    "UPDATE rule_drafts SET support_count=support_count+1, updated=? WHERE fingerprint=?",
                    (_now(), fingerprint),
                )
            n = c.execute("SELECT support_count FROM rule_drafts WHERE fingerprint=?", (fingerprint,)).fetchone()[0]
            return False, int(n)

    def count_needs_data(self, dataset: str, card_key: str) -> None:
        with self._tx() as c:
            cur = c.execute("INSERT OR IGNORE INTO needs_data_cards VALUES(?,?)", (dataset, card_key))
            if cur.rowcount:
                c.execute(
                    "INSERT INTO needs_data_counts VALUES(?,1) ON CONFLICT(dataset) DO UPDATE SET count=count+1",
                    (dataset,),
                )

    def needs_data_report(self) -> list[dict[str, Any]]:
        """Operator view: datasets ordered by how many hypotheses they would unlock."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT dataset,count FROM needs_data_counts ORDER BY count DESC, dataset ASC"
            ).fetchall()
        return [{"dataset": d, "count": n} for d, n in rows]

    def get_draft(self, fingerprint: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT artifact,support_count FROM rule_drafts WHERE fingerprint=?", (fingerprint,)
            ).fetchone()
        return {"artifact": json.loads(row[0]), "support_count": int(row[1])} if row else None

    def drafts(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT fingerprint,artifact,support_count FROM rule_drafts ORDER BY support_count DESC, fingerprint"
            ).fetchall()
        return [{"fingerprint": f, "artifact": json.loads(a), "support_count": n} for f, a, n in rows]


def card_key(card: Mapping[str, Any]) -> str:
    spans = card.get("cited_spans") or []
    sid = spans[0].get("source_record_id", "") if spans and isinstance(spans[0], Mapping) else ""
    basis = json.dumps({"sid": sid, "claim": card.get("claim"), "spans": spans}, sort_keys=True)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def validate_proposal(proposal: Any) -> tuple[str, str, str, Optional[dict[str, Any]]]:
    """Return (kind, reason, dataset, artifact). kind in compiled|not_expressible|needs_data."""
    if not isinstance(proposal, Mapping):
        return NOT_EXPRESSIBLE, "proposal_not_object", "", None
    extra = set(proposal) - _PROPOSAL_KEYS
    if extra:
        return NOT_EXPRESSIBLE, f"unknown_proposal_keys:{sorted(extra)}", "", None
    outcome = proposal.get("outcome")
    reason = str(proposal.get("reason") or "")[:300]
    if outcome == NOT_EXPRESSIBLE:
        return NOT_EXPRESSIBLE, reason or "model_declared_not_expressible", "", None
    if outcome == NEEDS_DATA:
        ds = str(proposal.get("dataset") or "")
        if not _DATASET_RE.match(ds):
            return NOT_EXPRESSIBLE, "needs_data_invalid_dataset_name", "", None
        return NEEDS_DATA, reason, ds, None
    if outcome != "factor_rule":
        return NOT_EXPRESSIBLE, "unknown_outcome", "", None

    logic = proposal.get("strategy_logic")
    params = proposal.get("parameters")
    surface = proposal.get("mutation_surface")
    if not isinstance(logic, Mapping) or not isinstance(params, Mapping) or not isinstance(surface, Mapping):
        return NOT_EXPRESSIBLE, "missing_logic_parameters_or_mutation_surface", "", None
    # Governed catalogue only: an unknown dataset/field is never accepted.
    inputs = logic.get("inputs")
    if inputs is None:
        inputs = ()
    if isinstance(inputs, (str, bytes)) or not isinstance(inputs, (list, tuple)):
        return NOT_EXPRESSIBLE, "invalid_inputs_not_sequence", "", None
    for item in inputs:
        if not isinstance(item, Mapping):
            return NOT_EXPRESSIBLE, "invalid_input_entry", "", None
        ds, fld = item.get("dataset"), item.get("field")
        if not isinstance(ds, str) or not isinstance(fld, str):
            return NOT_EXPRESSIBLE, "invalid_input_entry", "", None
        if get_feature_definition(ds, fld) is None:
            if _DATASET_RE.match(ds) and not any(d.dataset == ds for d in list_feature_definitions()):
                return NEEDS_DATA, f"unknown_dataset:{ds}", ds, None
            return NOT_EXPRESSIBLE, f"unknown_field:{ds}.{fld}", "", None
    try:
        normalized = validate_factor_rule_logic(logic, params, surface)
    except FactorRuleValidationError as exc:
        return NOT_EXPRESSIBLE, f"invalid_factor_rule:{exc}"[:300], "", None
    except Exception as exc:  # defensive: malformed model output must never crash the worker
        return NOT_EXPRESSIBLE, f"invalid_factor_rule:{type(exc).__name__}", "", None
    artifact = {
        "strategy_logic": normalized,
        "parameters": dict(params),
        "mutation_surface": dict(surface),
    }
    return COMPILED, "", "", artifact


class HypothesisCompiler:
    def __init__(self, proposer: Proposer, store: CompilerStore) -> None:
        self._proposer = proposer
        self._store = store

    @property
    def store(self) -> CompilerStore:
        return self._store

    def compile(self, card: Mapping[str, Any]) -> CompileOutcome:
        testability = str(card.get("testability") or "")
        key = card_key(card)
        if testability == "not_testable":
            return CompileOutcome(SKIPPED, "not_testable")
        if testability.startswith("needs_data:"):
            ds = testability.split(":", 1)[1]
            if not _DATASET_RE.match(ds):
                self._store.record_outcome(key, NOT_EXPRESSIBLE, "needs_data_invalid_dataset_name")
                return CompileOutcome(NOT_EXPRESSIBLE, "needs_data_invalid_dataset_name")
            self._store.count_needs_data(ds, key)
            self._store.record_outcome(key, NEEDS_DATA, "card_declared", ds)
            return CompileOutcome(NEEDS_DATA, "card_declared", dataset=ds)
        if testability != "testable":
            return CompileOutcome(SKIPPED, "unknown_testability")
        prior = self._store.get_outcome(key)
        if prior is not None:
            draft = self._store.get_draft(prior["fingerprint"]) if prior["fingerprint"] else None
            return CompileOutcome(
                prior["status"], prior["reason"], prior["dataset"], prior["fingerprint"],
                support_count=draft["support_count"] if draft else 0,
                artifact=draft["artifact"] if draft else None,
                extra={"cached": True},
            )
        try:
            proposal = self._proposer(card, catalogue_summary())
        except Exception as exc:
            return CompileOutcome(SKIPPED, f"proposer_failed:{type(exc).__name__}")
        kind, reason, dataset, artifact = validate_proposal(proposal)
        if kind == NEEDS_DATA:
            self._store.count_needs_data(dataset, key)
            self._store.record_outcome(key, NEEDS_DATA, reason, dataset)
            return CompileOutcome(NEEDS_DATA, reason, dataset=dataset)
        if kind == NOT_EXPRESSIBLE or artifact is None:
            self._store.record_outcome(key, NOT_EXPRESSIBLE, reason)
            return CompileOutcome(NOT_EXPRESSIBLE, reason)
        fp = rule_fingerprint(artifact["strategy_logic"])
        created, support = self._store.add_rule(fp, artifact, key)
        status = COMPILED if created else SUPPORT_ADDED
        self._store.record_outcome(key, status, "", "", fp)
        return CompileOutcome(status, fingerprint=fp, support_count=support, artifact=artifact)


_PROPOSAL_SCHEMA: dict[str, Any] = {"type": "object", "additionalProperties": True}
_PROPOSE_TIMEOUT_SECONDS = 15.0


def client_proposer(client: Any) -> Proposer:
    """Proposer that asks the governed extraction client's transport for one JSON proposal."""

    def propose(card: Mapping[str, Any], catalogue: Mapping[str, Any]) -> Mapping[str, Any]:
        from services.source_ingestion.semantic_extraction import (
            ExtractionTaskType,
            SemanticExtractionRequest,
        )

        spans = card.get("cited_spans") or [{}]
        request = SemanticExtractionRequest(
            source_id=str(spans[0].get("source_record_id") or "hypothesis-compiler"),
            text=str(card.get("claim") or ""),
            task_type=ExtractionTaskType.HYPOTHESIS_CARD,
        )
        raw = client._dispatch_turn(
            prompt=build_compile_prompt(card, catalogue),
            schema=_PROPOSAL_SCHEMA,
            request=request,
            timeout_seconds=_PROPOSE_TIMEOUT_SECONDS,
        )
        out = raw.get("output") or raw.get("data") or raw
        structured = out.get("structured_data") if isinstance(out, Mapping) else None
        proposal = structured if isinstance(structured, Mapping) else out
        if not isinstance(proposal, Mapping):
            raise ValueError("compiler proposal is not an object")
        return proposal

    return propose


def compiler_from_env(
    client: Any | None = None, store_path: str | Path | None = None
) -> Optional[HypothesisCompiler]:
    """Build the compiler only when L2 extraction is enabled (same switch as the extractor)."""
    from services.source_ingestion.hypothesis_card import ENABLED_ENV

    import os

    if str(os.environ.get(ENABLED_ENV, "")).strip().lower() not in ("1", "true", "yes", "on"):
        return None
    if client is None:
        from services.source_ingestion.semantic_extraction_client import SemanticExtractionClient

        client = SemanticExtractionClient()
    return HypothesisCompiler(client_proposer(client), CompilerStore(store_path))


__all__ = [
    "CompileOutcome",
    "CompilerStore",
    "HypothesisCompiler",
    "build_compile_prompt",
    "client_proposer",
    "compiler_from_env",
    "card_key",
    "catalogue_summary",
    "rule_fingerprint",
    "validate_proposal",
]
