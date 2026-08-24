"""Constrained structured-alpha AST validator, engine, and snapshot store."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import uuid4

from services.knowledge.evidence.models import EvidenceValidationError
from services.search.filters import SearchAccessContext, SearchPolicyError


LOGICAL_OPS = {"and", "or", "not"}
COMPARISON_OPS = {"eq", "neq", "gt", "gte", "lt", "lte", "in", "not_in", "between"}
ALLOWED_OPS = LOGICAL_OPS | COMPARISON_OPS
ALLOWED_FIELD_TYPES = {"float", "int", "str", "bool", "datetime"}
MAX_AST_DEPTH = 5
MAX_ARGS_PER_NODE = 20
MAX_AST_NODES = 50
MAX_LIMIT = 1000


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _format_iso(dt: datetime | str | None) -> str:
    if dt is None:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if isinstance(dt, str):
        parsed = _parse_time(dt)
        return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z") if parsed else dt
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class AlphaFieldDef:
    name: str
    field_type: str  # float, int, str, bool, datetime
    unit: str | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if self.field_type not in ALLOWED_FIELD_TYPES:
            raise SearchPolicyError(f"Unsupported alpha field type '{self.field_type}' for field '{self.name}'")


@dataclass(frozen=True)
class AlphaDatasetSchema:
    dataset_ref: str
    schema_version: str = "alpha_dataset.v1"
    fields: Mapping[str, AlphaFieldDef] = field(default_factory=dict)
    license_scope: str = "internal"
    access_scope: Sequence[str] = field(default_factory=lambda: ("research", "operator"))
    entitlement_tags: Sequence[str] = field(default_factory=tuple)
    allowed_universes: Sequence[str] = field(default_factory=lambda: ("US_EQUITY", "GLOBAL_MACRO"))
    default_citations: Sequence[str] = field(default_factory=tuple)

    def fingerprint(self) -> str:
        data = {
            "dataset_ref": self.dataset_ref,
            "schema_version": self.schema_version,
            "fields": {
                k: {"type": v.field_type, "unit": v.unit}
                for k, v in sorted(self.fields.items())
            },
            "license_scope": self.license_scope,
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class AlphaRecord:
    entity_id: str
    dataset_ref: str
    universe: str
    values: Mapping[str, Any]
    event_time: datetime | str
    available_time: datetime | str
    citations: Sequence[str] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "dataset_ref": self.dataset_ref,
            "universe": self.universe,
            "values": dict(self.values),
            "event_time": _format_iso(self.event_time),
            "available_time": _format_iso(self.available_time),
            "citations": list(self.citations),
        }


@dataclass(frozen=True)
class AlphaSortSpec:
    field: str
    direction: str = "desc"  # asc or desc

    def __post_init__(self) -> None:
        dir_norm = str(self.direction or "desc").strip().lower()
        if dir_norm not in ("asc", "desc"):
            raise SearchPolicyError("Sort direction must be 'asc' or 'desc'")
        object.__setattr__(self, "direction", dir_norm)
        object.__setattr__(self, "field", str(self.field).strip())


@dataclass(frozen=True)
class StructuredAlphaQuery:
    dataset_ref: str
    universe: Sequence[str]
    rule: Mapping[str, Any]
    schema_version: str = "alpha_rule_query.v1"
    as_of: str | None = None
    sort: Sequence[AlphaSortSpec] = field(default_factory=tuple)
    limit: int = 50

    def __post_init__(self) -> None:
        if self.schema_version != "alpha_rule_query.v1":
            raise SearchPolicyError(f"Unsupported schema_version '{self.schema_version}'; must be 'alpha_rule_query.v1'")
        if not str(self.dataset_ref or "").strip():
            raise SearchPolicyError("dataset_ref is required")
        if not self.universe:
            raise SearchPolicyError("universe must contain at least one universe identifier")
        if not isinstance(self.rule, Mapping) or not self.rule:
            raise SearchPolicyError("rule AST is required")
        if int(self.limit) <= 0 or int(self.limit) > MAX_LIMIT:
            raise SearchPolicyError(f"limit must be between 1 and {MAX_LIMIT}")
        object.__setattr__(self, "limit", int(self.limit))
        object.__setattr__(self, "dataset_ref", str(self.dataset_ref).strip())
        object.__setattr__(self, "universe", tuple(str(u).strip() for u in self.universe if str(u).strip()))
        normalized_sort = []
        for s in (self.sort or ()):
            if isinstance(s, Mapping):
                normalized_sort.append(AlphaSortSpec(field=s["field"], direction=s.get("direction", "desc")))
            elif isinstance(s, AlphaSortSpec):
                normalized_sort.append(s)
        object.__setattr__(self, "sort", tuple(normalized_sort))
        if self.as_of is not None:
            object.__setattr__(self, "as_of", _format_iso(self.as_of))

    def fingerprint(self) -> str:
        data = {
            "schema_version": self.schema_version,
            "dataset_ref": self.dataset_ref,
            "universe": sorted(self.universe),
            "rule": self.rule,
            "sort": [{"field": s.field, "direction": s.direction} for s in self.sort],
            "limit": self.limit,
            "as_of": self.as_of,
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "dataset_ref": self.dataset_ref,
            "universe": list(self.universe),
            "as_of": self.as_of,
            "rule": dict(self.rule),
            "sort": [{"field": s.field, "direction": s.direction} for s in self.sort],
            "limit": self.limit,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StructuredAlphaQuery":
        sort_entries = []
        for s in data.get("sort") or ():
            if isinstance(s, Mapping):
                sort_entries.append(AlphaSortSpec(field=s["field"], direction=s.get("direction", "desc")))
            elif isinstance(s, AlphaSortSpec):
                sort_entries.append(s)
        return cls(
            schema_version=str(data.get("schema_version") or "alpha_rule_query.v1"),
            dataset_ref=str(data.get("dataset_ref") or ""),
            universe=list(data.get("universe") or ()),
            rule=dict(data.get("rule") or {}),
            as_of=data.get("as_of"),
            sort=sort_entries,
            limit=int(data.get("limit") or 50),
        )


@dataclass(frozen=True)
class AlphaQueryResultSnapshot:
    snapshot_id: str
    query_fingerprint: str
    dataset_fingerprint: str
    ranker_fingerprint: str
    dataset_ref: str
    cutoff: str
    matched_entity_ids: list[str]
    matched_records: list[dict[str, Any]]
    citations: list[str]
    license_scope: str
    quota_receipt: dict[str, Any]
    cost_receipt: dict[str, Any]
    created_at: str
    schema_version: str = "alpha_query_snapshot.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "query_fingerprint": self.query_fingerprint,
            "dataset_fingerprint": self.dataset_fingerprint,
            "ranker_fingerprint": self.ranker_fingerprint,
            "dataset_ref": self.dataset_ref,
            "cutoff": self.cutoff,
            "matched_entity_ids": list(self.matched_entity_ids),
            "matched_records": [dict(r) for r in self.matched_records],
            "citations": list(self.citations),
            "license_scope": self.license_scope,
            "quota_receipt": dict(self.quota_receipt),
            "cost_receipt": dict(self.cost_receipt),
            "created_at": self.created_at,
        }


class AlphaRuleValidator:
    """Validates AST structure, types, depth, complexity, and field existence."""

    def __init__(self, schema: AlphaDatasetSchema) -> None:
        self.schema = schema
        self._node_count = 0

    def validate(self, query: StructuredAlphaQuery, now: datetime | None = None) -> None:
        # 1. Universe check
        allowed_universes = set(self.schema.allowed_universes)
        for u in query.universe:
            if u not in allowed_universes:
                raise SearchPolicyError(f"Universe '{u}' is not supported by dataset '{self.schema.dataset_ref}'")

        # 2. As-of time check (must not be in future)
        effective_now = now or datetime.now(timezone.utc)
        if query.as_of:
            parsed_as_of = _parse_time(query.as_of)
            if parsed_as_of is None:
                raise SearchPolicyError(f"Invalid as_of timestamp format: '{query.as_of}'")
            if parsed_as_of > effective_now:
                raise SearchPolicyError(f"as_of '{query.as_of}' is in the future; lookahead not allowed")

        # 3. Sort spec check
        for sort_spec in query.sort:
            if sort_spec.field not in self.schema.fields:
                raise SearchPolicyError(f"Unknown sort field '{sort_spec.field}' in dataset '{self.schema.dataset_ref}'")

        # 4. AST recursive validation
        self._node_count = 0
        self._validate_node(query.rule, depth=1)

    def _validate_node(self, node: Mapping[str, Any], depth: int) -> None:
        self._node_count += 1
        if self._node_count > MAX_AST_NODES:
            raise SearchPolicyError(f"AST node count exceeds maximum allowed ({MAX_AST_NODES})")
        if depth > MAX_AST_DEPTH:
            raise SearchPolicyError(f"AST nesting depth exceeds maximum allowed ({MAX_AST_DEPTH})")

        if not isinstance(node, Mapping):
            raise SearchPolicyError("AST node must be an object with an 'op' field")

        op = str(node.get("op") or "").strip().lower()
        if op not in ALLOWED_OPS:
            raise SearchPolicyError(f"Unknown or forbidden operator '{op}' in AST rule")

        if op in ("and", "or"):
            args = node.get("args")
            if not isinstance(args, Sequence) or isinstance(args, (str, bytes)) or not args:
                raise SearchPolicyError(f"Operator '{op}' requires non-empty 'args' array")
            if len(args) > MAX_ARGS_PER_NODE:
                raise SearchPolicyError(f"Operator '{op}' exceeds maximum {MAX_ARGS_PER_NODE} args")
            for child in args:
                self._validate_node(child, depth + 1)
        elif op == "not":
            arg = node.get("arg") or (node.get("args")[0] if isinstance(node.get("args"), Sequence) and node.get("args") else None)
            if not isinstance(arg, Mapping):
                raise SearchPolicyError("Operator 'not' requires a single 'arg' object")
            self._validate_node(arg, depth + 1)
        else:
            # Comparison op
            field_name = node.get("field")
            if not field_name or str(field_name).strip() not in self.schema.fields:
                raise SearchPolicyError(f"Unknown or missing field '{field_name}' in dataset '{self.schema.dataset_ref}'")
            field_name_str = str(field_name).strip()
            field_def = self.schema.fields[field_name_str]

            if "value" not in node:
                raise SearchPolicyError(f"Comparison operator '{op}' requires 'value'")
            val = node["value"]
            self._validate_value_type(op, field_def, val)

    def _validate_value_type(self, op: str, field_def: AlphaFieldDef, val: Any) -> None:
        ftype = field_def.field_type
        if op in ("in", "not_in"):
            if not isinstance(val, (list, tuple)) or not val:
                raise SearchPolicyError(f"Operator '{op}' requires non-empty list value for field '{field_def.name}'")
            for item in val:
                self._check_scalar_type(field_def, item)
        elif op == "between":
            if not isinstance(val, (list, tuple)) or len(val) != 2:
                raise SearchPolicyError(f"Operator 'between' requires 2-element [min, max] list for field '{field_def.name}'")
            self._check_scalar_type(field_def, val[0])
            self._check_scalar_type(field_def, val[1])
        else:
            self._check_scalar_type(field_def, val)

    def _check_scalar_type(self, field_def: AlphaFieldDef, val: Any) -> None:
        ftype = field_def.field_type
        if ftype in ("float", "int"):
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise SearchPolicyError(
                    f"Type mismatch: field '{field_def.name}' expects numeric value ({ftype}), got {type(val).__name__}"
                )
        elif ftype == "str":
            if not isinstance(val, str):
                raise SearchPolicyError(
                    f"Type mismatch: field '{field_def.name}' expects string, got {type(val).__name__}"
                )
        elif ftype == "bool":
            if not isinstance(val, bool):
                raise SearchPolicyError(
                    f"Type mismatch: field '{field_def.name}' expects boolean, got {type(val).__name__}"
                )
        elif ftype == "datetime":
            if not isinstance(val, (str, datetime)) or _parse_time(val) is None:
                raise SearchPolicyError(
                    f"Type mismatch: field '{field_def.name}' expects RFC3339 datetime, got {val!r}"
                )


class AlphaRuleEvaluator:
    """Evaluates validated AST rule against record values."""

    def evaluate(self, node: Mapping[str, Any], values: Mapping[str, Any]) -> bool:
        op = str(node.get("op") or "").strip().lower()
        if op == "and":
            args = node.get("args") or ()
            return all(self.evaluate(child, values) for child in args)
        elif op == "or":
            args = node.get("args") or ()
            return any(self.evaluate(child, values) for child in args)
        elif op == "not":
            arg = node.get("arg") or (node.get("args")[0] if node.get("args") else {})
            return not self.evaluate(arg, values)

        field_name = str(node.get("field") or "").strip()
        record_val = values.get(field_name)
        target_val = node.get("value")

        if record_val is None:
            return False

        if op == "eq":
            return record_val == target_val
        elif op == "neq":
            return record_val != target_val
        elif op == "gt":
            return float(record_val) > float(target_val)
        elif op == "gte":
            return float(record_val) >= float(target_val)
        elif op == "lt":
            return float(record_val) < float(target_val)
        elif op == "lte":
            return float(record_val) <= float(target_val)
        elif op == "in":
            return record_val in target_val
        elif op == "not_in":
            return record_val not in target_val
        elif op == "between":
            low, high = target_val[0], target_val[1]
            return float(low) <= float(record_val) <= float(high)
        return False


class StructuredAlphaEngine:
    """Executes structured alpha queries against governed alpha datasets."""

    def __init__(self, schemas: Mapping[str, AlphaDatasetSchema] | None = None) -> None:
        self.schemas: dict[str, AlphaDatasetSchema] = dict(schemas or {})
        self.records: dict[str, list[AlphaRecord]] = {}
        self._ensure_default_schemas()

    def _ensure_default_schemas(self) -> None:
        if "alpha-equity-factors-v1" not in self.schemas:
            self.schemas["alpha-equity-factors-v1"] = AlphaDatasetSchema(
                dataset_ref="alpha-equity-factors-v1",
                fields={
                    "quality_score": AlphaFieldDef(name="quality_score", field_type="float", unit="score"),
                    "momentum_20d": AlphaFieldDef(name="momentum_20d", field_type="float", unit="ratio"),
                    "volatility_60d": AlphaFieldDef(name="volatility_60d", field_type="float", unit="ratio"),
                    "market_cap_usd": AlphaFieldDef(name="market_cap_usd", field_type="float", unit="usd"),
                    "sector": AlphaFieldDef(name="sector", field_type="str"),
                    "is_sp500": AlphaFieldDef(name="is_sp500", field_type="bool"),
                },
                license_scope="internal",
                access_scope=("research", "operator"),
                default_citations=("alpha-db:equity-factors-v1",),
            )

    def register_schema(self, schema: AlphaDatasetSchema) -> None:
        self.schemas[schema.dataset_ref] = schema

    def add_records(self, dataset_ref: str, records: Sequence[AlphaRecord]) -> None:
        if dataset_ref not in self.records:
            self.records[dataset_ref] = []
        self.records[dataset_ref].extend(records)

    def execute(self, query: StructuredAlphaQuery, context: SearchAccessContext) -> AlphaQueryResultSnapshot:
        schema = self.schemas.get(query.dataset_ref)
        if schema is None:
            raise SearchPolicyError(f"Alpha dataset '{query.dataset_ref}' not found in registry")

        # Access / Entitlement Check
        if schema.license_scope not in set(context.license_scopes):
            raise SearchPolicyError(
                f"License scope '{schema.license_scope}' for dataset '{query.dataset_ref}' is not permitted in access context"
            )
        if schema.access_scope and "public" not in schema.access_scope and set(schema.access_scope).isdisjoint(set(context.access_scopes)):
            raise SearchPolicyError(
                f"Access scope for dataset '{query.dataset_ref}' is not permitted for caller"
            )
        if schema.entitlement_tags and set(schema.entitlement_tags).isdisjoint(set(context.entitlements)):
            raise SearchPolicyError(
                f"Required entitlement for dataset '{query.dataset_ref}' is missing"
            )

        now = datetime.now(timezone.utc)
        validator = AlphaRuleValidator(schema)
        validator.validate(query, now=now)

        evaluator = AlphaRuleEvaluator()
        dataset_records = self.records.get(query.dataset_ref, [])

        cutoff_dt = _parse_time(query.as_of) or now
        allowed_universes = set(query.universe)

        matched: list[AlphaRecord] = []
        citations_set: set[str] = set(schema.default_citations)

        for record in dataset_records:
            if record.universe not in allowed_universes:
                continue

            # Point-in-time check: available_time must be <= cutoff_dt
            avail_dt = _parse_time(record.available_time)
            if avail_dt is not None and avail_dt > cutoff_dt:
                continue

            if evaluator.evaluate(query.rule, record.values):
                matched.append(record)
                citations_set.update(record.citations)

        # Apply sorting
        if query.sort:
            for s in reversed(query.sort):
                matched.sort(
                    key=lambda r: r.values.get(s.field, 0),
                    reverse=(s.direction == "desc"),
                )

        limited = matched[:query.limit]

        snapshot = AlphaQueryResultSnapshot(
            snapshot_id=f"alpha-snap-{uuid4().hex[:12]}",
            query_fingerprint=query.fingerprint(),
            dataset_fingerprint=schema.fingerprint(),
            ranker_fingerprint="structured_alpha_ast.v1",
            dataset_ref=query.dataset_ref,
            cutoff=_format_iso(cutoff_dt),
            matched_entity_ids=[r.entity_id for r in limited],
            matched_records=[r.to_dict() for r in limited],
            citations=sorted(citations_set),
            license_scope=schema.license_scope,
            quota_receipt={
                "units_consumed": 1,
                "rate_limit_remaining": 999,
                "quota_category": "external_alpha",
            },
            cost_receipt={
                "currency": "USD",
                "estimated_cost": round(0.001 * len(limited), 4),
                "cost_model": "per_entity_v1",
            },
            created_at=_format_iso(now),
        )
        return snapshot
