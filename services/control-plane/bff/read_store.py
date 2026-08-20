from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.consultation.models import (
    ActorRef as ConsultationActorRef,
    ConsultAuditEvent,
    ConsultGateHandoff,
    ConsultPriority,
    ConsultRequest,
    ConsultRequestStatus,
    ConsultRequestType,
    GateHandoffStatus,
    MemoStatus,
)
from services.consultation.client import ConsultationClientError, ConsultationServiceClient
from services.consultation.store import ConsultationStore
from openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError
from trade_journey_projection_store import configured_projection_reader

_NOT_SUPPLIED = object()


def _first_existing(paths: List[Path]) -> Optional[Path]:
    for path in paths:
        if path.exists():
            return path
    return None


def _record_key(record: Dict[str, Any], candidates: List[str]) -> Optional[str]:
    for key in candidates:
        if "." in key:
            value: Any = record
            for part in key.split("."):
                if not isinstance(value, dict):
                    value = None
                    break
                value = value.get(part)
        else:
            value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _normalize_records(payload: Any, key_candidates: List[str]) -> Dict[str, Dict[str, Any]]:
    if isinstance(payload, dict):
        return {
            str(key): value
            for key, value in payload.items()
            if isinstance(value, dict)
        }
    if isinstance(payload, list):
        normalized: Dict[str, Dict[str, Any]] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            key = _record_key(item, key_candidates)
            if key:
                normalized[key] = item
        return normalized
    return {}


def _parse_rfc3339(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _utc_now_rfc3339() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _model_to_data(model: Any) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return json.loads(model.json())


_FIXTURE_PACK_A_PATH = Path(__file__).resolve().parent / "data" / "fixtures_pack_a.json"
_FIXTURE_PACK_B_PATH = Path(__file__).resolve().parent / "data" / "fixtures_pack_b.json"
_FIXTURE_PACK_C_PATH = Path(__file__).resolve().parent / "data" / "fixtures_pack_c.json"
_FIXTURE_PACK_PATHS = (_FIXTURE_PACK_A_PATH, _FIXTURE_PACK_B_PATH, _FIXTURE_PACK_C_PATH)
_FIXTURE_DATASET_ALIASES = {
    "deployments": "deployment_plans",
    "runtimes": "runtime_bindings",
}
_FIXTURE_RECORD_KEYS = [
    "id",
    "analysis_id",
    "entry_id",
    "decision_id",
    "intervention_id",
    "job_id",
    "plan_id",
    "program_id",
    "pool_id",
    "persona_id",
    "server_id",
    "signal_id",
    "skill_id",
    "session_id",
    "sessionId",
    "packet_id",
    "strategy_id",
    "experiment_id",
    "artifact_id",
    "rebalance_id",
    "binding_id",
    "runtime_id",
    "tool_id",
    "channel_id",
]


def _load_fixture_pack_datasets(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    datasets = payload.get("datasets") if isinstance(payload, dict) else None
    if not isinstance(datasets, dict):
        return {}
    return json.loads(json.dumps(datasets))


def _load_fixture_pack_a_datasets() -> Dict[str, Any]:
    return _load_fixture_pack_datasets(_FIXTURE_PACK_A_PATH)


def _fixture_list_record_key(record: Any) -> str:
    if isinstance(record, dict):
        key = _record_key(record, _FIXTURE_RECORD_KEYS)
        if key:
            return key
    return json.dumps(record, sort_keys=True, ensure_ascii=True)


def _compact_string_list(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        values = [part.strip() for part in re.split(r"[\s,]+", value) if part.strip()]
    elif isinstance(value, (list, tuple, set)):
        values = [str(item).strip() for item in value if str(item).strip()]
    else:
        values = [str(value).strip()]
    deduped: List[str] = []
    seen = set()
    for item in values:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def _merge_default_fixture_pack(target: Dict[str, Any], fixture: Dict[str, Any]) -> bool:
    changed = False
    for raw_key, incoming in fixture.items():
        key = _FIXTURE_DATASET_ALIASES.get(raw_key, raw_key)
        if isinstance(incoming, dict):
            existing = target.get(key)
            if not isinstance(existing, dict):
                target[key] = json.loads(json.dumps(incoming))
                changed = True
                continue
            for record_key, record in incoming.items():
                if record_key not in existing:
                    existing[record_key] = json.loads(json.dumps(record))
                    changed = True
            continue
        if isinstance(incoming, list):
            existing = target.get(key)
            if not isinstance(existing, list):
                target[key] = json.loads(json.dumps(incoming))
                changed = True
                continue
            seen = {_fixture_list_record_key(record) for record in existing}
            for record in incoming:
                record_key = _fixture_list_record_key(record)
                if record_key in seen:
                    continue
                existing.append(json.loads(json.dumps(record)))
                seen.add(record_key)
                changed = True
    return changed


def _is_fixture_pack_record(record: Dict[str, Any]) -> bool:
    if not isinstance(record, dict):
        return False
    for key in (
        "id",
        "plan_id",
        "decision_id",
        "approval_decision_id",
        "strategy_id",
        "persona_id",
        "pool_id",
        "binding_id",
        "runtime_id",
        "incident_id",
        "edge_id",
        "entry_id",
        "ticket_id",
        "result_id",
        "artifact_id",
        "from_artifact_id",
        "to_artifact_id",
    ):
        value = str(record.get(key) or "")
        if "-pack-" in value:
            return True
    return False


def _load_default_fixture_pack_datasets() -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for path in _FIXTURE_PACK_PATHS:
        _merge_default_fixture_pack(merged, _load_fixture_pack_datasets(path))
    return merged


def _put_default_record(
    target: Dict[str, Any],
    dataset: str,
    key: str,
    record: Dict[str, Any],
    *,
    skip_datasets: Optional[set[str]] = None,
) -> bool:
    if skip_datasets and dataset in skip_datasets:
        return False
    records = target.setdefault(dataset, {})
    if not isinstance(records, dict):
        return False
    if key not in records:
        records[key] = json.loads(json.dumps(record))
        return True
    existing = records.get(key)
    if not isinstance(existing, dict):
        return False
    return _merge_missing_default_values(existing, record)


def _merge_missing_default_values(existing: Dict[str, Any], defaults: Dict[str, Any]) -> bool:
    changed = False
    for field, value in defaults.items():
        if value is None:
            continue
        if field not in existing or existing.get(field) is None:
            existing[field] = json.loads(json.dumps(value))
            changed = True
            continue
        existing_value = existing.get(field)
        if isinstance(existing_value, dict) and isinstance(value, dict):
            changed |= _merge_missing_default_values(existing_value, value)
    return changed


_MARKETDATA_EVIDENCE_BASE = (
    "support/evidence/P2-MARKETDATA-CREDENTIAL-SMOKE-001"
)
_UNAVAILABLE_MARKETDATA_EVIDENCE_BASE = (
    f"{_MARKETDATA_EVIDENCE_BASE}/repo-local-uncredentialed"
)
_QUOTE_READBACK_EVIDENCE_BASE = (
    f"{_MARKETDATA_EVIDENCE_BASE}/repo-local-quote-readback"
)
_BROKER_006_DATASOURCE_SMOKE_REF = (
    "support/evidence/MGMT-BROKER-006/datasource-smoke/datasource-smoke.json"
)
_TW_QLIB_DATASET_MANIFEST_REF = "support/evidence/MGMT-QLIB-001/dataset_manifest.json"
_TW_QLIB_LINKAGE_PACKET_REF = (
    "support/evidence/MGMT-QLIB-006/management_linkage_packet.json"
)
_TW_QLIB_EXPERIMENT_ID = "exp-mgmt-qlib-006"
_TW_QLIB_STRATEGY_ID = "tw-cross-sectional-equity-alpha"
_TW_QLIB_STRATEGY_SPEC_ID = "qlib-tw-cross-sectional-alpha-spec-v1"
_TW_QLIB_ARTIFACT_ID = "qlib-tw-cross-sectional-alpha-model-draft-v1"
_TW_QLIB_DATASET_REF = "dataset:tw-equity-ohlcv-top50-2024-daily"
_TW_QLIB_DATASET_MANIFEST_ID = (
    "qlib-dataset-manifest:dataset-tw-equity-ohlcv-top50-2024-daily"
)


def _read_only_side_effect_guard() -> Dict[str, Any]:
    return {
        "read_only": True,
        "order_side_effects_allowed": False,
        "capital_side_effects_allowed": False,
        "live_ingestion_enabled": False,
    }


def _tw_qlib_evidence_refs() -> List[Dict[str, Any]]:
    return [
        {
            "ref_type": "management_linkage_packet",
            "ref_id": "mgmt-qlib-006-management-linkage-v1",
            "ref": _TW_QLIB_LINKAGE_PACKET_REF,
        },
        {
            "ref_type": "dataset_manifest",
            "ref_id": _TW_QLIB_DATASET_MANIFEST_ID,
            "ref": _TW_QLIB_DATASET_MANIFEST_REF,
        },
        {
            "ref_type": "research_experiment",
            "ref_id": _TW_QLIB_EXPERIMENT_ID,
            "route": f"/bff/research-experiments/{_TW_QLIB_EXPERIMENT_ID}",
        },
        {
            "ref_type": "strategy_artifacts",
            "ref_id": _TW_QLIB_STRATEGY_ID,
            "route": f"/bff/strategies/{_TW_QLIB_STRATEGY_ID}/artifacts",
        },
    ]


def _tw_qlib_safety_assertions() -> Dict[str, Any]:
    return {
        "registry_write_performed": False,
        "registry_write_authority": "registry_service_only",
        "broker_session_opened": False,
        "order_route": "none",
        "deployment_stage": "none",
        "live_capital_side_effects": False,
    }


def _tw_qlib_research_linkage() -> Dict[str, Any]:
    return {
        "kind": "qlib_admission_research_linkage",
        "framework": "qlib",
        "admission_stage": "management_review_linked",
        "strategy_id": _TW_QLIB_STRATEGY_ID,
        "strategy_spec_id": _TW_QLIB_STRATEGY_SPEC_ID,
        "dataset_manifest_id": _TW_QLIB_DATASET_MANIFEST_ID,
        "source_task_ids": [
            "MGMT-QLIB-001",
            "MGMT-QLIB-002",
            "MGMT-QLIB-004",
            "MGMT-QLIB-006",
        ],
        "pending_task_ids": ["MGMT-QLIB-003", "MGMT-QLIB-005"],
        "evidence_refs": [
            {
                "ref_type": "dataset_manifest",
                "task_id": "MGMT-QLIB-001",
                "ref": _TW_QLIB_DATASET_MANIFEST_REF,
            },
            {
                "ref_type": "strategy_spec_packet",
                "task_id": "MGMT-QLIB-002",
                "ref": "support/evidence/MGMT-QLIB-002/strategy_spec_packet.json",
            },
            {
                "ref_type": "model_eval_artifact_review",
                "task_id": "MGMT-QLIB-004",
                "ref": "support/reviews/MGMT-QLIB-004-review-codex2.md",
            },
        ],
        "expected_evidence_refs": [
            {
                "ref_type": "registry_admission_packet",
                "task_id": "MGMT-QLIB-005",
                "ref": "support/evidence/MGMT-QLIB-005/registry_admission_packet.json",
                "status": "pending_upstream_task",
            },
        ],
        "artifact_refs": [
            {
                "artifact_name": "model_artifact",
                "artifact_type": "model_artifact",
                "artifact_ref": f"{_TW_QLIB_ARTIFACT_ID}@1.0.0",
                "artifact_state": "draft",
                "deployment_stage": "none",
                "registry_id": _TW_QLIB_ARTIFACT_ID,
            },
            {
                "artifact_name": "evaluation_report",
                "artifact_type": "evaluation_result",
                "artifact_ref": f"eval-{_TW_QLIB_ARTIFACT_ID}@1.0.0",
                "target_artifact_ref": f"{_TW_QLIB_ARTIFACT_ID}@1.0.0",
                "artifact_state": "draft",
                "deployment_stage": "none",
            },
            {
                "artifact_name": "registry_entry_projection",
                "artifact_type": "registry_entry_projection",
                "artifact_ref": f"artifact://qlib/{_TW_QLIB_ARTIFACT_ID}/1.0.0/registry_entry",
                "artifact_state": "draft",
                "deployment_stage": "none",
            },
            {
                "artifact_name": "candidate_packet",
                "artifact_type": "registry_candidate_handoff",
                "artifact_ref": f"artifact://qlib/{_TW_QLIB_ARTIFACT_ID}/1.0.0/candidate_packet",
                "artifact_state": "draft",
                "deployment_stage": "none",
            },
        ],
        "ooda_refs": [
            {
                "stage": "observe",
                "ref_type": "dataset_manifest",
                "ref": _TW_QLIB_DATASET_MANIFEST_REF,
            },
            {
                "stage": "orient",
                "ref_type": "strategy_spec_packet",
                "ref": "support/evidence/MGMT-QLIB-002/strategy_spec_packet.json",
            },
            {
                "stage": "decide",
                "ref_type": "registry_admission_packet",
                "ref": "support/evidence/MGMT-QLIB-005/registry_admission_packet.json",
                "status": "pending_upstream_task",
            },
        ],
        "management_routes": {
            "artifact_detail": f"/bff/artifacts/{_TW_QLIB_ARTIFACT_ID}",
            "api_artifact_detail": f"/api/v1/artifacts/{_TW_QLIB_ARTIFACT_ID}",
            "research_experiment_detail": f"/bff/research-experiments/{_TW_QLIB_EXPERIMENT_ID}",
            "strategy_artifacts": f"/bff/strategies/{_TW_QLIB_STRATEGY_ID}/artifacts",
        },
        "safety_assertions": _tw_qlib_safety_assertions(),
    }


def _tw_qlib_research_experiment_default() -> Dict[str, Any]:
    linkage = _tw_qlib_research_linkage()
    return {
        "experiment_id": _TW_QLIB_EXPERIMENT_ID,
        "ticket_id": "rt-mgmt-qlib-006",
        "experiment_name": "MGMT-QLIB-006 Qlib TW admission linkage",
        "status": "completed",
        "stage": "management_review_linked",
        "queued_at": "2026-05-15T17:25:00Z",
        "started_at": "2026-05-15T17:26:00Z",
        "completed_at": "2026-05-15T17:30:00Z",
        "progress": {
            "percent": 100,
            "phase": "management_review_linked",
            "message": "Management linkage packet is ready; registry admission remains gated.",
        },
        "strategy_selector": {"strategy_id": _TW_QLIB_STRATEGY_ID, "variant_id": None},
        "linked_strategy_id": _TW_QLIB_STRATEGY_ID,
        "parameter_set": {
            "framework": "qlib",
            "model_family": "lightgbm",
            "market": "TW",
            "universe": "tw-equity-top50",
        },
        "run_config": {
            "backend": "qlib",
            "dataset_ref": _TW_QLIB_DATASET_REF,
            "dataset_manifest_id": _TW_QLIB_DATASET_MANIFEST_ID,
            "time_range": {
                "start_at": "2024-01-02T00:00:00Z",
                "end_at": "2026-01-05T00:00:00Z",
            },
            "execution_mode": "offline_admission_review",
            "priority": "normal",
            "requested_by": "pathreon-management",
        },
        "launch_context": {
            "task_id": "MGMT-QLIB-006",
            "analysis_refs": None,
            "source_task_ids": linkage["source_task_ids"],
            "pending_task_ids": linkage["pending_task_ids"],
        },
        "validation_warnings": [
            {
                "code": "REGISTRY_ADMISSION_PENDING",
                "message": "MGMT-QLIB-005 registry admission evidence is pending; deployment is blocked.",
            }
        ],
        "artifact_ids": [_TW_QLIB_ARTIFACT_ID],
        "artifact_refs": linkage["artifact_refs"],
        "framework": "qlib",
        "dataset_ref": _TW_QLIB_DATASET_REF,
        "dataset_manifest_id": _TW_QLIB_DATASET_MANIFEST_ID,
        "research_linkage": linkage,
        "evidence_refs": _tw_qlib_evidence_refs(),
        "safety_assertions": _tw_qlib_safety_assertions(),
        "registry_admission_status": "pending_upstream_task",
        "can_deploy": False,
        "deployment_stage": "none",
        "failure": {"reason_code": None, "message": None},
        "governed_default_source": "composed_market_persona_defaults",
    }


def _governed_research_experiment_defaults() -> Dict[str, Dict[str, Any]]:
    return {
        _TW_QLIB_EXPERIMENT_ID: _tw_qlib_research_experiment_default(),
    }


def _provider_truth(
    *,
    provider_key: str,
    provider: str,
    market: str,
    source_class: str,
    status: str,
    evidence_ref: str,
    order_capable_provider: bool,
    order_path: str,
    read_intent: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None,
    secret_ref: Optional[str] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "provider_key": provider_key,
        "provider": provider,
        "market": market,
        "source_class": source_class,
        "status": status,
        "evidence_ref": evidence_ref,
        "order_capable_provider": order_capable_provider,
        "order_path": order_path,
        "read_intent": json.loads(json.dumps(read_intent)) if read_intent else None,
        "reason": reason,
        **_read_only_side_effect_guard(),
    }
    if secret_ref is not None:
        result["secret_ref"] = secret_ref
    return result


def _market_persona_data_truth(item: Dict[str, Any]) -> Dict[str, Any]:
    market = str(item.get("market") or "").upper()
    watch_symbol = str(item.get("watch_symbol") or "")
    if market == "TW":
        sources = [
            _provider_truth(
                provider_key="shioaji",
                provider="Shioaji quote",
                market="TW",
                source_class="broker_execution",
                status="read_ok",
                evidence_ref=f"{_QUOTE_READBACK_EVIDENCE_BASE}/shioaji.json",
                order_capable_provider=True,
                order_path="disabled_for_marketdata_smoke",
                read_intent={
                    "symbol": "2330",
                    "exchange": "TSE",
                    "quote_type": "tick",
                    "version": "v1",
                    "bind": True,
                },
            ),
            _provider_truth(
                provider_key="twse",
                provider="TWSE OpenAPI",
                market="TW",
                source_class="official_reference",
                status="read_unavailable",
                evidence_ref=f"{_UNAVAILABLE_MARKETDATA_EVIDENCE_BASE}/twse.json",
                order_capable_provider=False,
                order_path="not_applicable",
                reason="repo-local smoke did not open a TWSE network session",
            ),
            _provider_truth(
                provider_key="tpex",
                provider="TPEx E-Data",
                market="TW",
                source_class="official_reference",
                status="read_unavailable",
                evidence_ref=f"{_UNAVAILABLE_MARKETDATA_EVIDENCE_BASE}/tpex.json",
                order_capable_provider=False,
                order_path="not_applicable",
                reason="repo-local smoke did not open a TPEx network session",
            ),
            _provider_truth(
                provider_key="mops",
                provider="MOPS",
                market="TW",
                source_class="official_reference",
                status="public_reference_unavailable",
                evidence_ref=f"{_UNAVAILABLE_MARKETDATA_EVIDENCE_BASE}/mops.json",
                order_capable_provider=False,
                order_path="not_applicable",
                reason="repo-local smoke has no public-reference readback",
            ),
            _provider_truth(
                provider_key="finmind",
                provider="FinMind",
                market="TW",
                source_class="research_grade",
                status="read_unavailable",
                evidence_ref=f"{_UNAVAILABLE_MARKETDATA_EVIDENCE_BASE}/finmind.json",
                order_capable_provider=False,
                order_path="not_applicable",
                reason="live FinMind readback overlaid from source-ingest health when available",
            ),
        ]
        provider_statuses = {source["provider_key"]: source["status"] for source in sources}
        readback_refs = [
            source["evidence_ref"]
            for source in sources
            if source.get("status") == "read_ok"
        ]
        unavailable_refs = [
            source["evidence_ref"]
            for source in sources
            if source.get("status") != "read_ok"
        ]
        data_source_status = {
            "state": "partial_readback",
            "summary": (
                "Shioaji quote readback is present; TWSE, TPEx, MOPS, and FinMind "
                "default to unavailable repo-local smoke evidence; FinMind flips to "
                "read_ok when source-ingest reports live health."
            ),
            "provider_statuses": provider_statuses,
            "readback_refs": readback_refs,
            "unavailable_refs": unavailable_refs,
            "research_dataset_ref": "dataset:tw-equity-ohlcv-top50-2024-daily",
            "research_dataset_manifest_ref": _TW_QLIB_DATASET_MANIFEST_REF,
            "research_dataset_as_of": "2026-01-05T00:00:00Z",
            "readback_captured_at": "2026-05-01T17:20:00Z",
            **_read_only_side_effect_guard(),
        }
        return {
            "data_source_status": data_source_status,
            "data_sources": sources,
            "data_source_refs": [*readback_refs, *unavailable_refs],
        }

    if market == "US":
        sources = [
            _provider_truth(
                provider_key="ibkr",
                provider="IBKR market data",
                market="US",
                source_class="broker_execution",
                status="read_ok",
                evidence_ref=f"{_QUOTE_READBACK_EVIDENCE_BASE}/ibkr.json",
                order_capable_provider=True,
                order_path="disabled_for_marketdata_smoke",
                read_intent={
                    "contract": {
                        "symbol": watch_symbol,
                        "exchange": "SMART",
                        "currency": "USD",
                        "secType": "STK",
                    },
                    "snapshot": True,
                    "readonly": True,
                },
            ),
            _provider_truth(
                provider_key="yahoo",
                provider="Yahoo Finance chart daily OHLCV",
                market="US",
                source_class="research_grade",
                status="read_unavailable",
                evidence_ref=f"{_UNAVAILABLE_MARKETDATA_EVIDENCE_BASE}/us-yahoo.json",
                order_capable_provider=False,
                order_path="not_applicable",
                reason="Yahoo chart API replaces blocked Stooq CSV; flips to read_ok when source-ingest connector reports live health",
            ),
            _provider_truth(
                provider_key="sec_edgar",
                provider="SEC EDGAR filings",
                market="US",
                source_class="official_reference",
                status="read_unavailable",
                evidence_ref=f"{_UNAVAILABLE_MARKETDATA_EVIDENCE_BASE}/us-sec-edgar.json",
                order_capable_provider=False,
                order_path="not_applicable",
                reason="SEC EDGAR batch connector requires configured user-agent; flips to read_ok when source-ingest reports ok",
            ),
            _provider_truth(
                provider_key="finra",
                provider="FINRA short-sale volume",
                market="US",
                source_class="official_reference",
                status="read_unavailable",
                evidence_ref=f"{_UNAVAILABLE_MARKETDATA_EVIDENCE_BASE}/us-finra.json",
                order_capable_provider=False,
                order_path="not_applicable",
                reason="FINRA public short-volume files; flips to read_ok when source-ingest connector reports live health",
            ),
            _provider_truth(
                provider_key="fred",
                provider="FRED macro series",
                market="GLOBAL",
                source_class="official_reference",
                status="credential_unavailable",
                evidence_ref=f"{_UNAVAILABLE_MARKETDATA_EVIDENCE_BASE}/us-fred.json",
                order_capable_provider=False,
                order_path="not_applicable",
                reason="FRED keyed API requires FRED_API_KEY; flips to read_ok when source-ingest connector reports live health",
                secret_ref="env://FRED_API_KEY",
            ),
            _provider_truth(
                provider_key="polygon",
                provider="Polygon.io daily OHLCV",
                market="US",
                source_class="research_grade",
                status="credential_unavailable",
                evidence_ref=f"{_UNAVAILABLE_MARKETDATA_EVIDENCE_BASE}/us-polygon.json",
                order_capable_provider=False,
                order_path="not_applicable",
                reason="Polygon API key required; set POLYGON_API_KEY (or MASSIVE_API_KEY / US_MARKET_DATA_API_KEY)",
                secret_ref="env://POLYGON_API_KEY",
            ),
            _provider_truth(
                provider_key="alphavantage",
                provider="Alpha Vantage daily OHLCV",
                market="US",
                source_class="research_grade",
                status="credential_unavailable",
                evidence_ref=f"{_UNAVAILABLE_MARKETDATA_EVIDENCE_BASE}/us-alphavantage.json",
                order_capable_provider=False,
                order_path="not_applicable",
                reason="Alpha Vantage API key required; set ALPHA_VANTAGE_API_KEY",
                secret_ref="env://ALPHA_VANTAGE_API_KEY",
            ),
        ]
        provider_statuses = {src["provider_key"]: src["status"] for src in sources}
        readback_refs = [
            src["evidence_ref"] for src in sources if src.get("status") == "read_ok"
        ]
        unavailable_refs = [
            src["evidence_ref"] for src in sources if src.get("status") != "read_ok"
        ]
        data_source_status = {
            "state": "partial_readback",
            "summary": (
                "IBKR broker readback is present; Yahoo, SEC EDGAR, and FINRA "
                "default to read_unavailable and flip to read_ok when source-ingest "
                "reports live health; FRED, Polygon, and Alpha Vantage require API "
                "credentials before they can become read_ok."
            ),
            "provider_statuses": provider_statuses,
            "readback_refs": readback_refs,
            "unavailable_refs": unavailable_refs,
            "readback_captured_at": "2026-05-01T17:20:00Z",
            **_read_only_side_effect_guard(),
        }
        return {
            "data_source_status": data_source_status,
            "data_sources": sources,
            "data_source_refs": [*readback_refs, *unavailable_refs],
        }

    if market == "CRYPTO":
        sources = [
            _provider_truth(
                provider_key="kraken",
                provider="Kraken market data",
                market="CRYPTO",
                source_class="broker_execution",
                status="datasource_smoke_ok",
                evidence_ref=_BROKER_006_DATASOURCE_SMOKE_REF,
                order_capable_provider=True,
                order_path="validate_only",
                read_intent={
                    "pair": watch_symbol,
                    "interval": 1,
                    "venue": "KRAKEN",
                },
            ),
            _provider_truth(
                provider_key="coingecko",
                provider="CoinGecko",
                market="CRYPTO",
                source_class="research_grade",
                status="read_unavailable",
                evidence_ref=f"{_UNAVAILABLE_MARKETDATA_EVIDENCE_BASE}/coingecko.json",
                order_capable_provider=False,
                order_path="not_applicable",
                reason="repo-local smoke did not open a CoinGecko network session",
            ),
        ]
        data_source_status = {
            "state": "datasource_smoke_ok",
            "summary": (
                "Kraken datasource smoke has a normalized quote projection; "
                "repo-local network readback remains disabled."
            ),
            "provider_statuses": {
                "kraken": "datasource_smoke_ok",
                "coingecko": "read_unavailable",
            },
            "readback_refs": [_BROKER_006_DATASOURCE_SMOKE_REF],
            "unavailable_refs": [sources[1]["evidence_ref"]],
            "readback_captured_at": "2026-05-15T17:34:44Z",
            **_read_only_side_effect_guard(),
        }
        return {
            "data_source_status": data_source_status,
            "data_sources": sources,
            "data_source_refs": [source["evidence_ref"] for source in sources],
        }

    data_source_status = {
        "state": "not_declared",
        "summary": "No governed data source readback is declared for this persona.",
        "provider_statuses": {},
        "readback_refs": [],
        "unavailable_refs": [],
        **_read_only_side_effect_guard(),
    }
    return {
        "data_source_status": data_source_status,
        "data_sources": [],
        "data_source_refs": [],
    }


def _market_persona_required_data_sources(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    market = str(item.get("market") or "").upper()
    if market == "TW":
        return [
            {
                "dataset": "tw_price_daily",
                "market": "TW",
                "cadence": "daily",
                "source_class": "live_pull",
                "connector_candidates": [
                    "tw-finmind-datasets",
                    "tw-twse-tpex-official-market",
                ],
                "policy_gates": [
                    "require_connector_approved",
                    "require_schedule_active",
                    "require_source_health_ok",
                ],
            },
            {
                "dataset": "tw_broker_top",
                "market": "TW",
                "cadence": "daily",
                "source_class": "live_push",
                "connector_candidates": [
                    "tw-finmind-broker-daily-report",
                    "tw-finmind-broker-bulk-parquet",
                ],
                "policy_gates": [
                    "require_connector_approved",
                    "require_schedule_active",
                    "require_payload_push_health",
                ],
            },
        ]
    return []


def _market_persona_research_truth(item: Dict[str, Any]) -> Dict[str, Any]:
    market = str(item.get("market") or "").upper()
    if market == "TW":
        research_refs = _tw_qlib_evidence_refs()
        research_status = {
            "stage": "management_review_linked",
            "framework": "qlib",
            "frameworks": ["qlib", "vectorbt", "statsmodels"],
            "experiment_id": _TW_QLIB_EXPERIMENT_ID,
            "strategy_id": _TW_QLIB_STRATEGY_ID,
            "strategy_spec_id": _TW_QLIB_STRATEGY_SPEC_ID,
            "artifact_id": _TW_QLIB_ARTIFACT_ID,
            "artifact_state": "draft",
            "deployment_stage": "none",
            "dataset_ref": _TW_QLIB_DATASET_REF,
            "dataset_manifest_id": _TW_QLIB_DATASET_MANIFEST_ID,
            "registry_admission_status": "pending_upstream_task",
            "pending_task_ids": ["MGMT-QLIB-003", "MGMT-QLIB-005"],
            "can_deploy": False,
            "summary": (
                "Qlib TW cross-sectional alpha draft is linked for Management review; "
                "registry admission and deployment are still pending upstream evidence."
            ),
            "safety_assertions": _tw_qlib_safety_assertions(),
        }
        return {
            "research_status": research_status,
            "research_refs": research_refs,
            "current_research_projects": [
                {
                    "project_id": "MGMT-QLIB-006",
                    "title": "Qlib TW cross-sectional equity alpha admission linkage",
                    "stage": research_status["stage"],
                    "status": "needs_human_approval",
                    "frameworks": research_status["frameworks"],
                    "dataset_ref": research_status["dataset_ref"],
                    "artifact_id": research_status["artifact_id"],
                    "experiment_id": research_status["experiment_id"],
                    "blocked_by_task_ids": research_status["pending_task_ids"],
                    "evidence_refs": research_refs,
                    "can_deploy": False,
                }
            ],
        }

    frameworks = ["vectorbt", "statsmodels", "quantlib"]
    if market == "CRYPTO":
        frameworks = ["vectorbt", "statsmodels", "finrl-rllib"]
    research_status = {
        "stage": str(item.get("ooda_stage") or "observe"),
        "frameworks": frameworks,
        "strategy_id": item.get("strategy_id"),
        "artifact_id": item.get("artifact_id"),
        "deployment_stage": item.get("deployment_stage"),
        "registry_admission_status": "not_requested",
        "can_deploy": False,
        "summary": str(item.get("current_work") or ""),
        "safety_assertions": {
            "registry_write_performed": False,
            "broker_session_opened": False,
            "order_route": "none",
            "live_capital_side_effects": False,
        },
    }
    return {
        "research_status": research_status,
        "research_refs": [],
        "current_research_projects": [
            {
                "project_id": f"research-{market.lower()}-paper-001",
                "title": str(item.get("current_work") or f"{market} paper research loop"),
                "stage": research_status["stage"],
                "status": item.get("persona_status"),
                "frameworks": frameworks,
                "artifact_id": item.get("artifact_id"),
                "strategy_id": item.get("strategy_id"),
                "can_deploy": False,
            }
        ],
    }


def _ref_values(refs: List[Any]) -> List[str]:
    values: List[str] = []
    for ref in refs:
        if isinstance(ref, str) and ref:
            values.append(ref)
            continue
        if not isinstance(ref, dict):
            continue
        value = ref.get("ref") or ref.get("route") or ref.get("ref_id")
        if value:
            values.append(str(value))
    return values


def _market_persona_seed_enabled() -> bool:
    """Return whether the retired demo persona fleet was explicitly requested."""

    return str(os.getenv("PANTHEON_BFF_MARKET_PERSONA_SEED", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _merge_market_persona_fleet(
    target: Dict[str, Any],
    *,
    preserve_explicit_agora: bool = False,
) -> bool:
    """Seed the legacy US/TW/CRYPTO persona fleet read model when opted in.

    The records are deliberately read-model only: they prove the Agora,
    Management, and Execution Plane wiring without granting live capital
    authority. Production read truth must come from persisted paper personas,
    so the synthetic fleet is disabled by default and retained only for
    explicit fixture/diagnostic use.
    """
    if not _market_persona_seed_enabled():
        return False
    skip_datasets: set[str] = set()
    if preserve_explicit_agora:
        for dataset in ("agora_signals", "agora_sessions", "agora_watchlist"):
            if dataset in target:
                skip_datasets.add(dataset)

    changed = False
    fleet = [
        {
            "market": "US",
            "persona_id": "persona-us-equity",
            "name": "US Equity Persona",
            "mandate": "us_equity_alpha_research_and_paper_execution",
            "strategy_family": "cross_sectional_momentum",
            "asset_classes": ["equity", "etf", "option"],
            "timezone": "America/New_York",
            "broker_adapter": "ibkr-paper",
            "pool_id": "pool-us-equity-paper",
            "binding_id": "binding-us-equity-paper",
            "runtime_id": "runtime-us-equity-paper",
            "plan_id": "plan-us-equity-paper",
            "approval_id": "approval-us-equity-paper",
            "artifact_id": "artifact-us-equity-momentum-v1",
            "strategy_id": "strategy-us-equity-momentum",
            "session_id": "agora-us-equity-session",
            "teaching_id": "trn-us-equity-20260607",
            "capability_id": "cap-us-equity",
            "signal_id": "sig-us-equity-001",
            "watch_symbol": "AAPL",
            "ooda_id": "ooda-us-equity-paper-001",
            "ooda_status": "oriented",
            "ooda_stage": "orient",
            "persona_status": "researching",
            "deployment_stage": "paper",
            "nav": 2_500_000.0,
            "cash": 2_180_000.0,
            "gross_exposure": 0.31,
            "net_exposure": 0.18,
            "leverage": 0.34,
            "realized_pnl": 18_420.0,
            "unrealized_pnl": 6_140.0,
            "var_95": 0.018,
            "drawdown": 0.021,
            "slippage_bps": 2.7,
            "fill_ratio": 0.982,
            "order_reject_rate": 0.001,
            "pnl": 24_560.0,
            "sharpe": 1.41,
            "sortino": 2.03,
            "max_drawdown": 0.057,
            "win_rate": 0.56,
            "trading_cost_bps": 4.2,
            "stability_score": 0.87,
            "human_interventions": 1,
            "training_improvement_pct": 14.0,
            "violation_count": 0,
            "league_rank": 2,
            "league_score": 87.4,
            "recommendation": None,
            "current_work": None,
            "risk_flags": [],
        },
        {
            "market": "TW",
            "persona_id": "persona-tw-equity",
            "name": "Taiwan Equity Persona",
            "mandate": "taiwan_equity_session_research_and_paper_execution",
            "strategy_family": "tw_session_momentum",
            "asset_classes": ["equity", "etf", "future", "option"],
            "timezone": "Asia/Taipei",
            "broker_adapter": "shioaji-sandbox",
            "pool_id": "pool-tw-equity-paper",
            "binding_id": "binding-tw-equity-paper",
            "runtime_id": "runtime-tw-equity-paper",
            "plan_id": "plan-tw-equity-paper",
            "approval_id": "approval-tw-equity-paper",
            "artifact_id": "artifact-tw-equity-session-v1",
            "strategy_id": "strategy-tw-equity-session",
            "session_id": "agora-tw-equity-session",
            "teaching_id": "trn-tw-equity-20260607",
            "capability_id": "cap-tw-equity",
            "signal_id": "sig-tw-equity-001",
            "watch_symbol": "2330.TW",
            "ooda_id": "ooda-tw-equity-paper-001",
            "ooda_status": "decided",
            "ooda_stage": "decide",
            "persona_status": "needs_human_approval",
            "deployment_stage": "paper",
            "nav": 90_000_000.0,
            "cash": 82_400_000.0,
            "gross_exposure": 0.24,
            "net_exposure": 0.11,
            "leverage": 0.28,
            "realized_pnl": 512_000.0,
            "unrealized_pnl": -86_000.0,
            "var_95": 0.021,
            "drawdown": 0.033,
            "slippage_bps": 5.9,
            "fill_ratio": 0.951,
            "order_reject_rate": 0.004,
            "pnl": 426_000.0,
            "sharpe": 1.08,
            "sortino": 1.62,
            "max_drawdown": 0.071,
            "win_rate": 0.53,
            "trading_cost_bps": 7.6,
            "stability_score": 0.78,
            "human_interventions": 3,
            "training_improvement_pct": 9.5,
            "violation_count": 0,
            "league_rank": 3,
            "league_score": 79.1,
            "recommendation": None,
            "current_work": None,
            "risk_flags": ["slippage_watch"],
        },
        {
            "market": "CRYPTO",
            "persona_id": "persona-crypto",
            "name": "Crypto Persona",
            "mandate": "crypto_24x7_alpha_research_and_paper_execution",
            "strategy_family": "crypto_trend_carry",
            "asset_classes": ["crypto", "perpetual_future", "dated_future"],
            "timezone": "UTC",
            "broker_adapter": "kraken-sandbox",
            "pool_id": "pool-crypto-paper",
            "binding_id": "binding-crypto-paper",
            "runtime_id": "runtime-crypto-paper",
            "plan_id": "plan-crypto-paper",
            "approval_id": "approval-crypto-paper",
            "artifact_id": "artifact-crypto-trend-carry-v1",
            "strategy_id": "strategy-crypto-trend-carry",
            "session_id": "agora-crypto-session",
            "teaching_id": "trn-crypto-20260607",
            "capability_id": "cap-crypto",
            "signal_id": "sig-crypto-001",
            "watch_symbol": "BTC/USD",
            "ooda_id": "ooda-crypto-paper-001",
            "ooda_status": "acted",
            "ooda_stage": "act",
            "persona_status": "paper_running",
            "deployment_stage": "paper",
            "nav": 1_200_000.0,
            "cash": 1_015_000.0,
            "gross_exposure": 0.37,
            "net_exposure": 0.22,
            "leverage": 0.42,
            "realized_pnl": 36_200.0,
            "unrealized_pnl": 11_800.0,
            "var_95": 0.029,
            "drawdown": 0.044,
            "slippage_bps": 4.8,
            "fill_ratio": 0.974,
            "order_reject_rate": 0.002,
            "pnl": 48_000.0,
            "sharpe": 1.76,
            "sortino": 2.31,
            "max_drawdown": 0.064,
            "win_rate": 0.59,
            "trading_cost_bps": 6.1,
            "stability_score": 0.91,
            "human_interventions": 1,
            "training_improvement_pct": 18.2,
            "violation_count": 0,
            "league_rank": 1,
            "league_score": 91.8,
            "recommendation": None,
            "current_work": None,
            "risk_flags": [],
        },
    ]

    for item in fleet:
        persona_id = item["persona_id"]
        market = item["market"]
        pool_id = item["pool_id"]
        runtime_id = item["runtime_id"]
        plan_id = item["plan_id"]
        approval_id = item["approval_id"]
        strategy_id = item["strategy_id"]
        artifact_id = item["artifact_id"]
        binding_id = item["binding_id"]
        capability_id = item["capability_id"]
        session_id = item["session_id"]
        teaching_id = item["teaching_id"]
        signal_id = item["signal_id"]
        ooda_id = item["ooda_id"]

        common_metrics = {
            "pnl": item["pnl"],
            "sharpe": item["sharpe"],
            "sortino": item["sortino"],
            "max_drawdown": item["max_drawdown"],
            "win_rate": item["win_rate"],
            "trading_cost_bps": item["trading_cost_bps"],
            "stability_score": item["stability_score"],
            "human_interventions": item["human_interventions"],
            "training_improvement_pct": item["training_improvement_pct"],
            "violation_count": item["violation_count"],
        }
        data_truth = _market_persona_data_truth(item)
        research_truth = _market_persona_research_truth(item)
        data_source_refs = list(data_truth.get("data_source_refs") or [])
        research_ref_values = _ref_values(list(research_truth.get("research_refs") or []))
        metadata = {
            "market_scope": [market],
            "asset_classes": list(item["asset_classes"]),
            "timezone": item["timezone"],
            "broker_adapter": item["broker_adapter"],
            "persona_status": item["persona_status"],
            "current_work": item["current_work"],
            "ooda_stage": item["ooda_stage"],
            "capital_pool_id": pool_id,
            "runtime_binding_id": runtime_id,
            "deployment_stage": item["deployment_stage"],
            "is_market_persona_default": True,
            "seed_row": True,
            "has_trading_telemetry": False,
            "league_score": item["league_score"],
            "league_rank": item["league_rank"],
            "recommended_governance_action": item["recommendation"],
            "governance_required": True,
            "risk_flags": list(item["risk_flags"]),
            "success_rate": item["win_rate"],
            "risk_level": "medium" if item["risk_flags"] else "low",
            "performance": common_metrics,
            "data_source_status": data_truth["data_source_status"],
            "data_sources": data_truth["data_sources"],
            "data_source_refs": data_source_refs,
            "research_status": research_truth["research_status"],
            "research_refs": research_truth["research_refs"],
            "current_research_projects": research_truth["current_research_projects"],
        }
        changed |= _put_default_record(
            target,
            "personas",
            persona_id,
            {
                "id": persona_id,
                "persona_id": persona_id,
                "name": item["name"],
                "lifecycle_state": "paper_owner",
                "status": item["persona_status"],
                "mandate": item["mandate"],
                "strategy_family": item["strategy_family"],
                "created_at": "2026-02-01T00:00:00Z",
                "updated_at": "2026-06-07T13:00:00Z",
                "last_active_at": "2026-06-07T13:00:00Z",
                "required_data_sources": _market_persona_required_data_sources(item),
                "metadata": metadata,
            },
        )
        changed |= _put_default_record(
            target,
            "capital_pools",
            pool_id,
            {
                "id": pool_id,
                "pool_id": pool_id,
                "name": f"{market} Paper Capital Pool",
                "status": "ready",
                "owner_id": "pathreon-management",
                "owner_type": "control-plane",
                "capital_mode": "paper",
                "risk_policy_ref": f"risk-policy-{market.lower()}-paper",
                "single_runtime_enforced": True,
                "currency": "TWD" if market == "TW" else "USD",
                "nav": item["nav"],
                "cash": item["cash"],
                "gross_exposure": item["gross_exposure"],
                "net_exposure": item["net_exposure"],
                "leverage": item["leverage"],
                "realized_pnl": item["realized_pnl"],
                "unrealized_pnl": item["unrealized_pnl"],
                "var_95": item["var_95"],
                "drawdown": item["drawdown"],
                "slippage_bps": item["slippage_bps"],
                "fill_ratio": item["fill_ratio"],
                "order_reject_rate": item["order_reject_rate"],
                "market_scope": [market],
                "live_capital_enabled": False,
            },
        )
        changed |= _put_default_record(
            target,
            "bindings",
            binding_id,
            {
                "id": binding_id,
                "binding_id": binding_id,
                "persona_id": persona_id,
                "capital_pool_id": pool_id,
                "role": "paper_owner",
                "validity": "active",
                "status": "active",
                "allowed_deployment_scope": "paper",
                "approval_decision_id": approval_id,
            },
        )
        changed |= _put_default_record(
            target,
            "runtime_bindings",
            runtime_id,
            {
                "id": runtime_id,
                "binding_id": runtime_id,
                "runtime_binding_id": runtime_id,
                "runtime_id": runtime_id,
                "deployment_mode": item["deployment_stage"],
                "deployment_stage": item["deployment_stage"],
                "status": "active",
                "plan_id": plan_id,
                "artifact_id": artifact_id,
                "artifact_version": "v1.0.0",
                "capital_pool_id": pool_id,
                "persona_capital_binding_id": binding_id,
                "effective_at": "2026-06-07T12:00:00Z",
                "metadata": {
                    "market_scope": [market],
                    "broker_adapter": item["broker_adapter"],
                    "runtime_kind": "lean",
                    "artifact_loader": "approved_artifact_loader",
                    "live_write_enabled": False,
                    "fail_closed": True,
                },
            },
        )
        changed |= _put_default_record(
            target,
            "sessions",
            session_id,
            {
                "id": session_id,
                "session_id": session_id,
                "persona_id": persona_id,
                "session_type": "interactive",
                "status": "active",
                "started_at": "2026-06-07T12:05:00Z",
                "capability_snapshot_id": capability_id,
                "trace_id": f"trace-{session_id}",
                "request_id": f"req-{session_id}",
                "runtime_binding_id": runtime_id,
                "deployment_stage": item["deployment_stage"],
                "capital_pool_id": pool_id,
                "last_heartbeat_at": "2026-06-07T13:00:00Z",
                "tools_enabled": ["market_data_read", "strategy_research", "telemetry_query"],
                "pool_scope": pool_id,
            },
        )
        changed |= _put_default_record(
            target,
            "capability_snapshots",
            capability_id,
            {
                "snapshot_id": capability_id,
                "persona_id": persona_id,
                "effective_tools": [
                    "market_data_read",
                    "research_backend_run",
                    "strategy_spec_write",
                    "telemetry_query",
                    "governance_handoff",
                ],
                "effective_skills": [
                    "qlib_research",
                    "vectorbt_backtest",
                    "statsmodels_analysis",
                    "quantlib_risk",
                    "finrl_rllib_simulation",
                ],
                "effective_workflows": [
                    "observe_market",
                    "orient_research",
                    "submit_governance_candidate",
                    "paper_runtime_monitor",
                    "learn_from_postmortem",
                ],
                "restrictions": [
                    "no_live_trade_without_approval",
                    "approved_artifact_only_for_execution",
                    "human_gate_required_for_capital_changes",
                ],
                "generated_at": "2026-06-07T12:00:00Z",
                "source_refs": [f"persona:{persona_id}", f"policy:risk-policy-{market.lower()}-paper"],
            },
        )
        changed |= _put_default_record(
            target,
            "teaching_sessions",
            teaching_id,
            {
                "id": teaching_id,
                "session_id": teaching_id,
                "persona_id": persona_id,
                "session_type": "trainer",
                "opened_by": "operator-desk",
                "status": "completed",
                "started_at": "2026-06-07T10:00:00Z",
                "ended_at": "2026-06-07T10:35:00Z",
                "topic": f"{market} trader preference alignment",
                "objective": f"Align {item['name']} to trader questions while preserving governance boundaries.",
                "outcomes": ["training-example-created", "risk-boundary-preserved"],
                "context_refs": [{"type": "persona", "id": persona_id}],
                "actor_context": {
                    "persona_display_name": item["name"],
                    "persona_role_context": item["mandate"],
                },
            },
        )
        changed |= _put_default_record(
            target,
            "strategy_specs",
            strategy_id,
            {
                "id": strategy_id,
                "strategy_id": strategy_id,
                "current_spec_version_id": f"specver-{strategy_id}-v1",
                "current_spec_version": "v1",
                "title": f"{market} governed alpha strategy",
                "source_kind": "workflow",
                "status": "active",
                "lifecycle_state": "approved",
                "persona_ids": [persona_id],
                "capital_pool_id": pool_id,
                "artifact_id": artifact_id,
                "created_at": "2026-04-01T11:30:00Z",
                "updated_at": "2026-04-01T12:00:00Z",
                "hypothesis": f"{market} persona can operate a paper-only governed {item['strategy_family']} loop.",
                "objective": "Validate OOS evidence, trading costs, and runtime isolation before any canary request.",
                "market_scope": {
                    "symbols": [item["watch_symbol"]],
                    "frequency": "daily" if market != "CRYPTO" else "hourly",
                    "asset_classes": list(item["asset_classes"]),
                    "venues": [item["broker_adapter"]],
                },
                "execution_profile": {
                    "signal_schema_version": "1.0",
                    "quantity_type": "PERCENT_PORTFOLIO",
                    "rebalance_cadence": "daily" if market != "CRYPTO" else "4h",
                    "execution_mode_hint": item["deployment_stage"],
                    "runtime_id": runtime_id,
                    "approved_artifact_only": True,
                },
                "evaluation_plan": {
                    "metrics": ["sharpe_ratio", "max_drawdown", "slippage_bps", "fill_ratio"],
                    "candidate_gate": "Evidence packet must include OOS, cost model, and risk owner review.",
                    "paper_gate": "Paper runtime must stay fail-closed with approved artifacts only.",
                    "live_gate": "Live capital remains disabled until a separate human-gated promotion.",
                },
                "governance": {
                    "approval_required": True,
                    "human_gate_required": True,
                    "risk_level": "medium" if item["risk_flags"] else "low",
                    "approval_decision_id": approval_id,
                    "live_capital_side_effects": False,
                },
                "citation_bundle": {
                    "evidence_refs": [
                        {"ref_id": f"evidence-{market.lower()}-oos", "type": "oos_backtest"},
                        {"ref_id": f"evidence-{market.lower()}-cost", "type": "cost_model"},
                        *[
                            {"ref_id": ref, "type": "data_source_readback"}
                            for ref in data_source_refs
                        ],
                        *[
                            {"ref_id": ref, "type": "research_linkage"}
                            for ref in research_ref_values
                        ],
                    ],
                    "memory_anchors": [],
                    "insight_citations": [],
                },
                "summary": {"last_modified_at": "2026-04-01T12:00:00Z"},
            },
        )
        changed |= _put_default_record(
            target,
            "telemetry_summaries",
            runtime_id,
            {
                "runtime_id": runtime_id,
                "window": "1d",
                "pnl": item["pnl"],
                "drawdown": item["drawdown"],
                "sharpe_ratio": item["sharpe"],
                "sortino_ratio": item["sortino"],
                "total_trades": 38,
                "fill_rate": item["fill_ratio"],
                "fill_ratio": item["fill_ratio"],
                "avg_slippage_bps": item["slippage_bps"],
                "order_reject_rate": item["order_reject_rate"],
                "collected_at": "2026-06-07T13:00:00Z",
            },
        )
        changed |= _put_default_record(
            target,
            "agora_watchlist",
            item["watch_symbol"],
            {
                "id": f"watch-{item['watch_symbol']}",
                "symbol": item["watch_symbol"],
                "market_scope": market,
                "personaId": persona_id,
                "return1dPct": 1.2 if market != "TW" else 0.6,
                "updatedAt": "2026-06-07T13:00:00Z",
            },
            skip_datasets=skip_datasets,
        )
        changed |= _put_default_record(
            target,
            "agora_signals",
            signal_id,
            {
                "id": signal_id,
                "signal_id": signal_id,
                "title": f"{market} market briefing signal",
                "description": f"{item['name']} generated an Observe/Orient briefing for {item['watch_symbol']}.",
                "reviewStatus": "pending_trader_review",
                "conviction": 0.74 if market != "TW" else 0.66,
                "alpha": item["strategy_family"],
                "personaId": persona_id,
                "persona_id": persona_id,
                "market_scope": market,
                "symbol": item["watch_symbol"],
                "ooda_packet_id": ooda_id,
                "updatedAt": "2026-06-07T13:00:00Z",
                "createdAt": "2026-06-07T12:30:00Z",
                "governance": {
                    "can_promote_directly": False,
                    "handoff_required": True,
                    "data_source_status": data_truth["data_source_status"]["state"],
                    "research_stage": research_truth["research_status"].get("stage"),
                },
            },
            skip_datasets=skip_datasets,
        )
        changed |= _put_default_record(
            target,
            "agora_sessions",
            session_id,
            {
                "id": session_id,
                "sessionId": session_id,
                "personaId": persona_id,
                "persona_id": persona_id,
                "title": f"{item['name']} daily Agora briefing",
                "mode": "quick_ask",
                "status": "active",
                "targetEntity": {"type": "market", "id": market},
                "participants": [
                    {"type": "operator", "id": "operator-desk"},
                    {"type": "persona", "id": persona_id},
                ],
                "messages": [
                    {
                        "id": f"msg-{session_id}-001",
                        "sessionId": session_id,
                        "sender": {"type": "operator", "id": "operator-desk"},
                        "role": "user",
                        "content": "今天我該注意什麼？",
                        "language": "zh-TW",
                        "attachments": [],
                        "citations": [],
                        "annotations": [],
                        "createdAt": "2026-06-07T12:31:00Z",
                    },
                    {
                        "id": f"msg-{session_id}-002",
                        "sessionId": session_id,
                        "sender": {"type": "persona", "id": persona_id},
                        "role": "assistant",
                        "content": f"{market} briefing is ready; promotion still requires governance evidence and human gate.",
                        "language": "en-US",
                        "attachments": [],
                        "citations": [{"ref_id": ooda_id, "type": "ooda_packet"}],
                        "annotations": ["no_live_capital_side_effects"],
                        "createdAt": "2026-06-07T12:32:00Z",
                    },
                ],
                "createdAt": "2026-06-07T12:30:00Z",
                "updatedAt": "2026-06-07T13:00:00Z",
            },
            skip_datasets=skip_datasets,
        )
        changed |= _put_default_record(
            target,
            "ooda_packets",
            ooda_id,
            {
                "packet_id": ooda_id,
                "loop_type": "market_persona_governed_paper",
                "status": item["ooda_status"],
                "stage": item["ooda_stage"],
                "environment": "paper",
                "market_scope": [market],
                "strategy_id": strategy_id,
                "persona_ids": [persona_id],
                "runtime_id": runtime_id,
                "evolution_program_id": f"evo-program-{market.lower()}",
                "capital_pool_id": pool_id,
                "created_at": "2026-06-07T12:00:00Z",
                "updated_at": "2026-06-07T13:00:00Z",
                "observe": {
                    "market_data_refs": [
                        f"market://{market}/{item['watch_symbol']}",
                        *data_source_refs,
                    ],
                    "telemetry_refs": [f"telemetry://{runtime_id}/1d"],
                    "trader_training_refs": [teaching_id],
                    "data_source_status": data_truth["data_source_status"],
                },
                "orient": {
                    "backend_refs": ["qlib", "vectorbt", "statsmodels", "quantlib", "finrl-rllib"],
                    "evidence_bundle_refs": [
                        f"evidence://{market.lower()}-oos",
                        *research_ref_values,
                    ],
                    "consultation_refs": [],
                    "research_status": research_truth["research_status"],
                },
                "decide": {
                    "approval_decision_id": approval_id,
                    "human_gate_required": True,
                    "recommendation": item["recommendation"],
                },
                "act": {
                    "deployment_plan_id": plan_id,
                    "runtime_binding_id": runtime_id,
                    "artifact_id": artifact_id,
                    "live_capital_side_effects": False,
                },
                "learn": {
                    "telemetry_refs": [f"telemetry://{runtime_id}/learn"],
                    "evolution_followthrough_refs": [f"evo://{persona_id}/next"],
                    "training_improvement_pct": item["training_improvement_pct"],
                },
                "fail_closed_checks": [
                    {"name": "approved_artifact_only", "passed": True},
                    {"name": "live_capital_disabled", "passed": True},
                    {"name": "human_gate_required_for_capital", "passed": True},
                ],
            },
        )
        changed |= _put_default_record(
            target,
            "persona_league",
            persona_id,
            {
                "id": persona_id,
                "persona_id": persona_id,
                "name": item["name"],
                "market_scope": [market],
                "deployment_stage": item["deployment_stage"],
                "status": item["persona_status"],
                "rank": item["league_rank"],
                "league_score": item["league_score"],
                "quarter": "2026Q2",
                "capital_pool_id": pool_id,
                "runtime_id": runtime_id,
                "ooda_stage": item["ooda_stage"],
                "recommendation": item["recommendation"],
                "governance_required": True,
                "metrics": common_metrics,
                "risk_flags": list(item["risk_flags"]),
                "updated_at": "2026-06-07T13:00:00Z",
            },
        )

    return changed


def _market_persona_read_model_data() -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    _merge_market_persona_fleet(data)
    return data


# Evidence redaction support
from models import (
    EvidenceKind,
    RedactedEvidenceRef,
    EVIDENCE_CAPABILITY_MAP,
    SOURCE_TYPE_TO_EVIDENCE_KIND,
    OperatorIdentity,
)


def redact_evidence_refs(
    identity: OperatorIdentity,
    evidence_refs: List[Dict[str, Any]],
    capabilities: Optional[List[str]] = None,
) -> tuple[List[Dict[str, Any]], int]:
    """Return a processed list of evidence refs where entries the operator
    lacks capability for are replaced by RedactedEvidenceRef dicts.

    If `capabilities` is None, this function is a no-op and returns the
    original refs (backwards-compatible behavior).
    """
    processed: List[Dict[str, Any]] = []
    redacted_count = 0

    if capabilities is None:
        # Backwards-compatible: do not redact when capabilities are not supplied
        return list(evidence_refs), 0

    caps = set(capabilities)

    for ref in evidence_refs:
        if not isinstance(ref, dict):
            processed.append(ref)
            continue
        # Determine kind from common fields first.
        kind_key = (
            str(ref.get("evidence_type") or "").strip()
            or str(ref.get("type") or "").strip()
            or str(ref.get("ref_type") or "").strip()
            or str(ref.get("link_type") or "").strip()
        )
        # Fall back to source_document.source_type so refs that carry no
        # explicit evidence_type still get capability-gated.
        if not kind_key:
            src_doc = ref.get("source_document")
            if isinstance(src_doc, dict):
                source_type = str(src_doc.get("source_type") or "").strip()
                kind_key = SOURCE_TYPE_TO_EVIDENCE_KIND.get(source_type, "")
        required = None
        if kind_key:
            required = EVIDENCE_CAPABILITY_MAP.get(kind_key)
        if required and required not in caps:
            redacted_count += 1
            ref_id = str(ref.get("ref_id") or ref.get("id") or "")
            try:
                ek = EvidenceKind(kind_key)
            except Exception:
                ek = None
            redacted = RedactedEvidenceRef(
                ref_id=ref_id,
                kind=ek,
                required_capability=required,
                reason="insufficient_capability",
            )
            processed.append(redacted.model_dump())
            continue
        processed.append(ref)

    return processed, redacted_count


_CONSULT_MEMO_REVIEW_REDACTED_KEYS = {
    # Persona-internal state.
    "policyinternals",
    "memorytrace",
    "internalscore",
    "personainternalstate",
    "internalstate",
    "reasoningtrace",
    "scratchpad",
    "privatecontext",
    # Secret or credential material.
    "secretcredentials",
    "credential",
    "credentials",
    "secret",
    "secretref",
    "secretrefs",
    "apikey",
    "apitoken",
    "accesstoken",
    "refreshtoken",
    "clientsecret",
    "privatekey",
    "password",
    # Capability-map internals.
    "capabilitymapinternals",
    "capabilitymap",
    "capabilitysnapshot",
    "effectivecapabilities",
    "effectivetools",
    "effectiveskills",
    "toolgrants",
    "envgrants",
    "permissionmap",
}

_CONSULT_MEMO_REVIEW_REDACTED_TEXT_TOKENS = (
    "policy_internals",
    "memory_trace",
    "internal_score",
    "persona_internal_state",
    "secret_credentials",
    "secretRef",
    "secret_ref",
    "capability_map_internals",
    "capabilityMap",
    "capability_map",
    "effective_tools",
    "effective_skills",
)


def _consult_memo_review_redaction_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key or "").lower())


def _redact_consult_memo_review_text(value: str) -> str:
    redacted = value
    for token in _CONSULT_MEMO_REVIEW_REDACTED_TEXT_TOKENS:
        redacted = re.sub(
            rf"(?i)(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])",
            "[redacted]",
            redacted,
        )
    return redacted


def _redact_consult_memo_review_payload(value: Any) -> Any:
    """Remove persona-internal material from review-facing consult memo payloads."""
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            if _consult_memo_review_redaction_key(key) in _CONSULT_MEMO_REVIEW_REDACTED_KEYS:
                continue
            redacted[key] = _redact_consult_memo_review_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_consult_memo_review_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_consult_memo_review_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_consult_memo_review_text(value)
    return value


_CONSULTATION_DATA_DIR_ENVS = (
    "PANTHEON_BFF_CONSULTATION_DATA_DIR",
    "PANTHEON_CONSULTATION_DATA_DIR",
    "CONSULTATION_DATA_DIR",
)
_CONSULTATION_SERVICE_DATASETS = {
    "consult_requests",
    "consultation_sessions",
    "consult_transcripts",
    "consult_memos",
}
_BFF_TO_SERVICE_REQUEST_TYPE = {
    "pre_deployment": ConsultRequestType.STRATEGY_REVIEW,
    "risk_review": ConsultRequestType.EXECUTION_RISK,
    "macro_regime_shift": ConsultRequestType.STRATEGY_REVIEW,
    "incident_response": ConsultRequestType.INCIDENT,
    "policy_change": ConsultRequestType.PERSONA_POLICY,
    "general": ConsultRequestType.STRATEGY_REVIEW,
}
_BFF_TO_SERVICE_PRIORITY = {
    "low": ConsultPriority.LOW,
    "normal": ConsultPriority.NORMAL,
    "high": ConsultPriority.HIGH,
    "critical": ConsultPriority.URGENT,
}
_SERVICE_TO_BFF_REQUEST_STATUS = {
    ConsultRequestStatus.DRAFT.value: "created",
    ConsultRequestStatus.SUBMITTED.value: "created",
    ConsultRequestStatus.ASSIGNED.value: "created",
    ConsultRequestStatus.IN_PROGRESS.value: "running",
    ConsultRequestStatus.MEMO_PENDING.value: "running",
    ConsultRequestStatus.PUBLISHED.value: "completed",
    ConsultRequestStatus.CANCELLED.value: "canceled",
    ConsultRequestStatus.FAILED.value: "failed",
}
_SERVICE_TO_SESSION_STATUS = {
    ConsultRequestStatus.DRAFT.value: "pending",
    ConsultRequestStatus.SUBMITTED.value: "queued",
    ConsultRequestStatus.ASSIGNED.value: "active",
    ConsultRequestStatus.IN_PROGRESS.value: "active",
    ConsultRequestStatus.MEMO_PENDING.value: "active",
    ConsultRequestStatus.PUBLISHED.value: "terminated",
    ConsultRequestStatus.CANCELLED.value: "terminated",
    ConsultRequestStatus.FAILED.value: "failed",
}

_DORMANT_OSS_BACKENDS = (
    "openclaw",
    "qlib",
    "trl",
    "finrl",
    "rllib",
    "ray_tune",
    "wandb",
)
_DORMANT_SAFE_DISPATCHERS = {"stub", "handoff_only", "manual"}
_DORMANT_OFFLINE_SCOPE = "offline_worker_dispatch_enabled"
_DORMANT_FAIL_CLOSED_REASONS = {
    "dispatch_mode_disabled",
    "execution_path_disabled",
    "governance_write_disabled",
    "learning_activation_disabled",
    "offline_dispatch_not_available",
    "offline_mode_required",
    "production_adapter_disabled",
    "registry_write_disabled",
    "unknown_adapter",
    "unknown_worker",
}
_DORMANT_SERVICE_SPECS = {
    "research_orchestrator": {
        "base_env": (
            "PANTHEON_RESEARCH_ORCHESTRATOR_API_URL",
            "PANTHEON_RESEARCH_ORCHESTRATOR_URL",
            "RESEARCH_ORCHESTRATOR_URL",
        ),
        "capabilities_path": "/api/research-orchestrator/capabilities",
        "activity_path": "/api/research-orchestrator/runs",
        "actor_field": "adapter",
        "activity_kind": "run",
        "id_fields": ("run_id", "id"),
    },
    "policy_learning": {
        "base_env": (
            "PANTHEON_POLICY_LEARNING_API_URL",
            "PANTHEON_POLICY_LEARNING_URL",
            "POLICY_LEARNING_URL",
        ),
        "capabilities_path": "/api/policy-learning/capabilities",
        "activity_path": "/api/policy-learning/jobs",
        "actor_field": "adapter",
        "activity_kind": "job",
        "id_fields": ("job_id", "id"),
    },
    "research_worker_gateway": {
        "base_env": (
            "PANTHEON_RESEARCH_WORKER_GATEWAY_API_URL",
            "PANTHEON_RESEARCH_WORKER_GATEWAY_URL",
            "RESEARCH_WORKER_GATEWAY_URL",
        ),
        "capabilities_path": "/api/research-worker-gateway/capabilities",
        "activity_path": "/api/research-worker-gateway/jobs",
        "actor_field": "worker",
        "activity_kind": "job",
        "id_fields": ("job_id", "id"),
    },
    "openclaw_gateway_adapter": {
        "base_env": (
            "PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL",
            "PANTHEON_OPENCLAW_ADAPTER_URL",
            "OPENCLAW_GATEWAY_ADAPTER_URL",
        ),
        "capabilities_path": "/api/openclaw-adapter/capabilities",
        "upstream_status_path": "/api/openclaw-adapter/upstream/status",
        "actor_field": "adapter",
        "activity_kind": "openclaw_status",
        "id_fields": ("session_id", "id"),
    },
}

_OPENCLAW_GATE_FIELDS = {
    "broker_execution": {
        "activation_gate": "OPENCLAW_PRODUCTION_BROKER_ENABLED",
        "allowed_scope": "not_enabled",
    },
    "paper_adapter": {
        "activation_gate": "OPENCLAW_PAPER_ADAPTER_ENABLED",
        "allowed_scope": "paper_gate_not_enabled",
    },
    "live_adapter": {
        "activation_gate": "OPENCLAW_LIVE_ADAPTER_ENABLED",
        "allowed_scope": "live_gate_not_enabled",
    },
    "canary_adapter": {
        "activation_gate": "OPENCLAW_CANARY_ADAPTER_ENABLED",
        "allowed_scope": "canary_gate_not_enabled",
    },
    "capital_binding": {
        "activation_gate": "OPENCLAW_CAPITAL_BINDING_ENABLED",
        "allowed_scope": "capital_binding_not_enabled",
    },
}


_TW04_DRAWDOWN_EVIDENCE_REF_ID = "tel-drawdown-2026-04-18"
_TW04_DRAWDOWN_EVIDENCE_ROUTE = "/operator/paper-live-drift/runtime-042"


def _default_bff_snapshot_path() -> Path:
    return Path(os.getenv("BFF_DATA_DIR", "/tmp/pantheon/bff")) / "read_surfaces.json"


def _load_snapshot_dataset(
    snapshot_path: Path,
    dataset_key: str,
    key_candidates: List[str],
) -> tuple[bool, Dict[str, Dict[str, Any]], tuple[str, int]]:
    if not snapshot_path.exists():
        return False, {}, ("", 0)

    stat = snapshot_path.stat().st_mtime_ns
    text = snapshot_path.read_text(encoding="utf-8").strip()
    payload = json.loads(text) if text else {}
    if not isinstance(payload, dict):
        return False, {}, ("", 0)

    dataset_payload = payload.get(dataset_key, {})
    normalized = _normalize_records(dataset_payload, key_candidates)
    return True, normalized, (str(snapshot_path), stat)


def _load_record_store_payload(path: Path) -> Any:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if path.suffix.lower() == ".jsonl":
        records: List[Any] = []
        for line in text.splitlines():
            raw = line.strip()
            if raw:
                records.append(json.loads(raw))
        return records
    return json.loads(text)


def _packet_from_ooda_store_record(record: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(record, dict):
        return None
    if str(record.get("schema_version") or "") != "ooda_loop_packet_record.v1":
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    record_type = str(record.get("record_type") or "")
    if record_type == "packet_snapshot":
        packet = payload
    elif record_type == "stage_transition":
        packet = payload.get("packet")
    else:
        packet = None
    if not isinstance(packet, dict):
        return None
    projected = json.loads(json.dumps(packet))
    projected.setdefault("packet_id", record.get("packet_id"))
    return projected


def _project_ooda_packet_store_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        for key in ("items", "packets", "data", "records"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return _project_ooda_packet_store_payload(nested)
        packet = _packet_from_ooda_store_record(payload)
        if packet is not None:
            return {str(packet.get("packet_id") or packet.get("id")): packet}
        return payload

    if not isinstance(payload, list):
        return payload

    projected: Dict[str, Dict[str, Any]] = {}
    passthrough: List[Dict[str, Any]] = []
    for item in payload:
        packet = _packet_from_ooda_store_record(item)
        if packet is None:
            if isinstance(item, dict):
                packet_id = _record_key(item, ["packet_id", "id"])
                if packet_id:
                    projected[str(packet_id)] = item
                else:
                    passthrough.append(item)
            continue
        packet_id = _record_key(packet, ["packet_id", "id"])
        if packet_id:
            projected[str(packet_id)] = packet

    if passthrough:
        return [*projected.values(), *passthrough]
    return projected


def _project_synthesis_conflict_log_record(record: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(record, dict):
        return None

    payload = record.get("payload") if isinstance(record.get("payload"), dict) else record
    log_payload = (
        payload.get("conflict_resolution_log")
        or payload.get("conflictResolutionLog")
        or payload.get("log")
        if isinstance(payload, dict)
        else None
    )
    if log_payload is None and isinstance(payload, dict):
        log_payload = payload
    if not isinstance(log_payload, dict):
        return None

    log_id = _record_key(log_payload, ["log_id", "id", "conflict_resolution_log_id"])
    if not log_id:
        return None

    projected = json.loads(json.dumps(log_payload))
    projected.setdefault("log_id", str(log_id))
    projected.setdefault("id", str(log_id))

    artifact = None
    if isinstance(payload, dict):
        artifact = (
            payload.get("allocation_policy_artifact")
            or payload.get("allocationPolicyArtifact")
            or payload.get("artifact")
        )
    if isinstance(artifact, dict):
        artifact_id = artifact.get("artifact_id") or artifact.get("id")
        if artifact_id:
            projected.setdefault("allocation_policy_artifact_id", str(artifact_id))
        for field in (
            "target_weights",
            "constraints_bundle",
            "risk_budget",
            "provenance_refs",
            "sponsor_persona_id",
            "synthesis_method",
        ):
            if field in artifact and field not in projected:
                projected[field] = json.loads(json.dumps(artifact[field]))

    approval = None
    if isinstance(payload, dict):
        approval = (
            payload.get("governance_approval_packet")
            or payload.get("governanceApprovalPacket")
            or payload.get("approval_decision")
        )
    if isinstance(approval, dict):
        approval_id = approval.get("approval_decision_id") or approval.get("decision_id") or approval.get("id")
        if approval_id:
            projected.setdefault("governance_approval_id", str(approval_id))
        for source, target in (
            ("decision", "governance_decision"),
            ("decision_state", "governance_decision_state"),
            ("can_proceed", "governance_can_proceed"),
            ("rationale", "governance_rationale"),
            ("risk_level", "governance_risk_level"),
        ):
            if source in approval and target not in projected:
                projected[target] = json.loads(json.dumps(approval[source]))
        if "evidence_refs" in approval and "evidence_refs" not in projected:
            projected["evidence_refs"] = json.loads(json.dumps(approval["evidence_refs"]))

    return projected


def _project_synthesis_conflict_log_store_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        for key in ("items", "logs", "conflict_resolution_logs", "data", "records"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return _project_synthesis_conflict_log_store_payload(nested)

        single = _project_synthesis_conflict_log_record(payload)
        if single is not None:
            return {str(single["log_id"]): single}

        projected: Dict[str, Dict[str, Any]] = {}
        for item in payload.values():
            single = _project_synthesis_conflict_log_record(item)
            if single is not None:
                projected[str(single["log_id"])] = single
        if projected:
            return projected
        return payload

    if not isinstance(payload, list):
        return payload

    projected: Dict[str, Dict[str, Any]] = {}
    passthrough: List[Dict[str, Any]] = []
    for item in payload:
        single = _project_synthesis_conflict_log_record(item)
        if single is None:
            if isinstance(item, dict):
                log_id = _record_key(item, ["log_id", "id", "conflict_resolution_log_id"])
                if log_id:
                    projected[str(log_id)] = item
                else:
                    passthrough.append(item)
            continue
        projected[str(single["log_id"])] = single

    if passthrough:
        return [*projected.values(), *passthrough]
    return projected


def _base_url_from_env(env_names: tuple[str, ...]) -> Optional[str]:
    for env_name in env_names:
        raw = os.getenv(env_name, "").strip()
        if raw:
            return raw.rstrip("/")
    return None


def _service_timeout_seconds() -> float:
    raw = os.getenv("PANTHEON_BFF_SERVICE_TIMEOUT_SECONDS", "2.0").strip()
    try:
        return max(float(raw), 0.1)
    except ValueError:
        return 2.0


def _http_json_get(
    base_url: str,
    path: str,
    *,
    headers: Optional[Dict[str, str]] = None,
) -> tuple[bool, Any]:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", **(headers or {})},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=_service_timeout_seconds()) as response:
            text = response.read().decode("utf-8").strip()
            if not text:
                return True, None
            return True, json.loads(text)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return True, None
        return False, None
    except (OSError, ValueError, json.JSONDecodeError):
        return False, None


def _http_json_post(
    base_url: str,
    path: str,
    *,
    body: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
) -> tuple[bool, Any]:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            **(headers or {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_service_timeout_seconds()) as response:
            text = response.read().decode("utf-8").strip()
            if not text:
                return True, None
            return True, json.loads(text)
    except (urllib.error.HTTPError, OSError, ValueError, json.JSONDecodeError):
        return False, None


def _auth_headers_from_spec(spec: Dict[str, Any]) -> Dict[str, str]:
    token_env = str(spec.get("auth_token_env") or "").strip()
    if not token_env:
        return {}
    token = os.getenv(token_env, "").strip() or str(spec.get("default_token") or "").strip()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _records_from_http_payload(payload: Any, *, list_key: Optional[str]) -> Any:
    if list_key and isinstance(payload, dict):
        return payload.get(list_key, [])
    return payload


def _records_from_envelope(payload: Any, spec: Dict[str, Any]) -> Any:
    envelope_key = str(spec.get("envelope_key") or "").strip()
    if envelope_key and isinstance(payload, dict):
        nested = payload.get(envelope_key)
        if nested is not None:
            return nested
    return payload


class CanonicalSnapshotAdapter:
    """Best-effort adapter over canonical governance/runtime JSON snapshots.

    The BFF remains read-oriented. When canonical snapshot files are available,
    the read surfaces prefer them. When they are absent, the normal integration
    path must surface backend unavailability explicitly instead of silently
    inventing local defaults.
    """

    _DATASETS = {
        "deployment_plans": {
            "env": "PANTHEON_BFF_DEPLOYMENT_PLAN_STORE",
            "dirs": ("PANTHEON_GOVERNANCE_DATA_DIR",),
            "filenames": ("deployment_plans.json",),
            "keys": ["plan_id", "id"],
            "snapshot_key": "deployment_plans",
        },
        "deployment_sagas": {
            "env": "PANTHEON_BFF_DEPLOYMENT_SAGA_STORE",
            "dirs": ("PANTHEON_GOVERNANCE_DATA_DIR",),
            "filenames": ("deployment_sagas.json",),
            "keys": ["saga_id", "id"],
            "snapshot_key": "deployment_sagas",
            "envelope_key": "sagas",
        },
        "deployment_saga_outbox": {
            "env": "PANTHEON_BFF_DEPLOYMENT_SAGA_STORE",
            "dirs": ("PANTHEON_GOVERNANCE_DATA_DIR",),
            "filenames": ("deployment_sagas.json",),
            "keys": ["event.event_id", "event_id", "id"],
            "snapshot_key": "deployment_saga_outbox",
            "envelope_key": "outbox",
        },
        "approval_decisions": {
            "env": "PANTHEON_BFF_APPROVAL_DECISION_STORE",
            "dirs": ("PANTHEON_GOVERNANCE_DATA_DIR",),
            "filenames": ("approval_decisions.json",),
            "keys": ["decision_id", "id"],
            "snapshot_key": "approval_decisions",
        },
        "capital_pools": {
            "env": "PANTHEON_BFF_CAPITAL_POOL_STORE",
            "dirs": ("PANTHEON_GOVERNANCE_DATA_DIR",),
            "filenames": ("capital_pools.json",),
            "keys": ["pool_id", "id"],
            "snapshot_key": "capital_pools",
        },
        "persona_bindings": {
            "env": "PANTHEON_BFF_PERSONA_BINDING_STORE",
            "dirs": ("PANTHEON_GOVERNANCE_DATA_DIR",),
            "filenames": ("persona_capital_bindings.json", "bindings.json"),
            "keys": ["binding_id", "id"],
            "snapshot_key": "bindings",
        },
        "rebalances": {
            "env": "PANTHEON_BFF_REBALANCE_STORE",
            "dirs": ("CAPITAL_DATA_DIR",),
            "filenames": ("capital_allocation_authority.json",),
            "keys": ["rebalance_id", "id"],
            "snapshot_key": "rebalances",
            "envelope_key": "rebalances",
        },
        "capital_allocations": {
            "env": "PANTHEON_BFF_CAPITAL_ALLOCATION_STORE",
            "dirs": ("CAPITAL_DATA_DIR",),
            "filenames": ("capital_allocation_authority.json",),
            "keys": ["allocation_id", "id"],
            "snapshot_key": "capital_allocations",
            "envelope_key": "allocations",
        },
        "containments": {
            "env": "PANTHEON_BFF_CONTAINMENT_STORE",
            "dirs": ("CAPITAL_DATA_DIR",),
            "filenames": ("capital_allocation_authority.json",),
            "keys": ["containment_id", "id"],
            "snapshot_key": "containments",
            "envelope_key": "containments",
        },
        "runtime_bindings": {
            "env": "PANTHEON_BFF_RUNTIME_BINDING_STORE",
            "dirs": ("PANTHEON_RUNTIME_DATA_DIR",),
            "filenames": ("runtime_bindings.json",),
            "keys": ["binding_id", "id"],
            "snapshot_key": "runtime_bindings",
        },
        "paper_runtime_monitoring_sessions": {
            "env": "PANTHEON_BFF_PAPER_RUNTIME_MONITORING_SESSION_STORE",
            "dirs": ("PANTHEON_RUNTIME_DATA_DIR",),
            "filenames": ("paper_runtime_monitoring_sessions.json",),
            "keys": ["session_id", "id"],
            "snapshot_key": "paper_runtime_monitoring_sessions",
        },
        "registry_entries": {
            "env": "PANTHEON_BFF_REGISTRY_ENTRY_STORE",
            "dirs": ("PANTHEON_REGISTRY_DATA_DIR",),
            "filenames": ("registry_entries.json",),
            "keys": ["artifact_id", "registry_id", "id"],
            "snapshot_key": "registry_entries",
        },
    }

    _HTTP_DATASETS = {
        "deployment_plans": {
            "base_env": ("PANTHEON_DEPLOYMENT_API_URL", "PANTHEON_DEPLOYMENT_SERVICE_URL"),
            "list_path": "/api/deployment/plans",
        },
        "approval_decisions": {
            "base_env": (
                "PANTHEON_GOVERNANCE_APPROVAL_API_URL",
                "PANTHEON_GOVERNANCE_SERVICE_URL",
            ),
            "list_path": "/api/governance/approvals",
            # PANTHEON_PROMOTION_API_URL wires directly to the promotion service
            # (the real approval producer) using its native path /api/v1/approvals.
            # Tried before the governance service URL when both are set.
            # Each override may be a plain path string or a dict with path+list_key.
            "path_env_overrides": {
                "PANTHEON_PROMOTION_API_URL": {
                    "path": "/api/v1/approvals",
                    "list_key": "items",
                },
            },
        },
        "capital_pools": {
            "base_env": ("PANTHEON_CAPITAL_API_URL", "PANTHEON_CAPITAL_SERVICE_URL"),
            "list_path": "/api/capital-pools",
        },
        "persona_bindings": {
            "base_env": ("PANTHEON_CAPITAL_API_URL", "PANTHEON_CAPITAL_SERVICE_URL"),
            "list_path": "/api/bindings",
        },
        "rebalances": {
            "base_env": ("PANTHEON_CAPITAL_API_URL", "PANTHEON_CAPITAL_SERVICE_URL"),
            "list_path": "/api/rebalances",
        },
        "capital_allocations": {
            "base_env": ("PANTHEON_CAPITAL_API_URL", "PANTHEON_CAPITAL_SERVICE_URL"),
            "list_path": "/api/allocations",
            "list_key": "items",
        },
        "containments": {
            "base_env": ("PANTHEON_CAPITAL_API_URL", "PANTHEON_CAPITAL_SERVICE_URL"),
            "list_path": "/api/containments",
        },
        "runtime_bindings": {
            "base_env": ("PANTHEON_RUNTIME_MANAGER_URL", "PANTHEON_INTERNAL_API_URL"),
            "list_path": "/api/runtime-bindings",
            "list_key": "bindings",
            "auth_token_env": "PANTHEON_RUNTIME_MANAGER_TOKEN",
            "default_token": "runtime-control-internal",
        },
        "paper_runtime_monitoring_sessions": {
            "base_env": (
                "PANTHEON_PAPER_FLEET_RECONCILER_URL",
                "PANTHEON_PAPER_RUNTIME_MONITORING_URL",
            ),
            "list_path": "/api/fleet/state",
            "list_key": "monitoring_sessions",
        },
    }

    def __init__(
        self,
        *,
        snapshot_path: Optional[Path] = None,
        allow_snapshot_fallback: bool = True,
    ) -> None:
        self._cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._cache_meta: Dict[str, tuple[str, int]] = {}
        self._cache_source: Dict[str, str] = {}
        self._snapshot_path = snapshot_path or _default_bff_snapshot_path()
        self._allow_snapshot_fallback = allow_snapshot_fallback

    def _load_http_dataset(self, dataset: str) -> tuple[bool, Dict[str, Dict[str, Any]]]:
        spec = self._HTTP_DATASETS.get(dataset)
        if not spec:
            return False, {}

        # Try path_env_overrides first: env-var-specific URL + path pairs that differ
        # from the default list_path. E.g. promotion service at /api/v1/approvals.
        # Override spec may be a plain path string or a dict with "path" and "list_key".
        for env_var, override_spec in (spec.get("path_env_overrides") or {}).items():
            if isinstance(override_spec, str):
                specific_path = override_spec
                override_list_key = spec.get("list_key")
            else:
                specific_path = override_spec["path"]
                override_list_key = override_spec.get("list_key", spec.get("list_key"))
            override_url = os.getenv(env_var, "").strip().rstrip("/")
            if not override_url:
                continue
            available, payload = _http_json_get(
                override_url,
                specific_path,
                headers=_auth_headers_from_spec(spec),
            )
            if not available:
                continue
            records_payload = _records_from_http_payload(payload, list_key=override_list_key)
            normalized = _normalize_records(records_payload, self._DATASETS[dataset]["keys"])
            self._cache[dataset] = normalized
            self._cache_meta[dataset] = (f"{override_url}{specific_path}", 0)
            self._cache_source[dataset] = "service_client"
            return True, normalized

        base_url = _base_url_from_env(tuple(spec.get("base_env") or ()))
        if not base_url:
            return False, {}
        available, payload = _http_json_get(
            base_url,
            str(spec.get("list_path") or ""),
            headers=_auth_headers_from_spec(spec),
        )
        if not available:
            return False, {}
        records_payload = _records_from_http_payload(
            payload,
            list_key=spec.get("list_key"),
        )
        normalized = _normalize_records(records_payload, self._DATASETS[dataset]["keys"])
        self._cache[dataset] = normalized
        self._cache_meta[dataset] = (f"{base_url}{spec.get('list_path')}", 0)
        self._cache_source[dataset] = "service_client"
        return True, normalized

    def _resolve_path(self, dataset: str) -> Optional[Path]:
        spec = self._DATASETS[dataset]
        explicit = os.getenv(spec["env"], "").strip()
        if explicit:
            return Path(explicit)
        candidates: List[Path] = []
        for dir_env in spec["dirs"]:
            base = os.getenv(dir_env, "").strip()
            if not base:
                continue
            for filename in spec["filenames"]:
                candidates.append(Path(base) / filename)
        return _first_existing(candidates)

    def _load_dataset(
        self,
        dataset: str,
        *,
        include_snapshot_fallback: bool = True,
    ) -> tuple[bool, Dict[str, Dict[str, Any]]]:
        # When an explicit store-path env var is set and the file exists, prefer it
        # over the HTTP service client. This prevents a configured service URL
        # (e.g. PANTHEON_GOVERNANCE_APPROVAL_API_URL in docker-compose) from
        # shadowing a projection-script-populated file such as the one written by
        # project_approvals_to_bff_surfaces.py into PANTHEON_BFF_APPROVAL_DECISION_STORE.
        spec = self._DATASETS.get(dataset)
        if spec:
            explicit = os.getenv(spec["env"], "").strip()
            if explicit:
                explicit_path = Path(explicit)
                if explicit_path.exists():
                    stat = explicit_path.stat().st_mtime_ns
                    cache_key = str(explicit_path)
                    if self._cache_meta.get(dataset) == (cache_key, stat):
                        return True, self._cache.get(dataset, {})
                    text = explicit_path.read_text(encoding="utf-8").strip()
                    payload = json.loads(text) if text else {}
                    payload = _records_from_envelope(payload, spec)
                    normalized = _normalize_records(payload, spec["keys"])
                    self._cache[dataset] = normalized
                    self._cache_meta[dataset] = (cache_key, stat)
                    self._cache_source[dataset] = "canonical"
                    return True, normalized

        http_available, http_records = self._load_http_dataset(dataset)
        if http_available:
            return True, http_records

        path = self._resolve_path(dataset)
        if path is not None and path.exists():
            stat = path.stat().st_mtime_ns
            cache_key = str(path)
            if self._cache_meta.get(dataset) == (cache_key, stat):
                return True, self._cache.get(dataset, {})

            text = path.read_text(encoding="utf-8").strip()
            payload = json.loads(text) if text else {}
            payload = _records_from_envelope(payload, self._DATASETS[dataset])
            normalized = _normalize_records(payload, self._DATASETS[dataset]["keys"])
            self._cache[dataset] = normalized
            self._cache_meta[dataset] = (cache_key, stat)
            self._cache_source[dataset] = "canonical"
            return True, normalized

        snapshot_key = self._DATASETS[dataset].get("snapshot_key")
        if include_snapshot_fallback and self._allow_snapshot_fallback and snapshot_key:
            available, normalized, cache_meta = _load_snapshot_dataset(
                self._snapshot_path,
                str(snapshot_key),
                self._DATASETS[dataset]["keys"],
            )
            if available:
                if self._cache_meta.get(dataset) != cache_meta:
                    self._cache[dataset] = normalized
                    self._cache_meta[dataset] = cache_meta
                    self._cache_source[dataset] = "local_snapshot"
                return True, self._cache.get(dataset, normalized)

        return False, {}

    def source(self, dataset: str) -> str:
        return self._cache_source.get(dataset, "canonical")

    def cached_source(self, dataset: str) -> Optional[str]:
        """Return provenance from an earlier read without touching the backend."""
        return self._cache_source.get(dataset)

    def list_records(
        self,
        dataset: str,
        *,
        include_snapshot_fallback: bool = True,
    ) -> tuple[bool, List[Dict[str, Any]]]:
        available, records = self._load_dataset(
            dataset,
            include_snapshot_fallback=include_snapshot_fallback,
        )
        return available, list(records.values())

    def deployment_plan(self, plan_id: str) -> tuple[bool, Optional[Dict[str, Any]]]:
        available, records = self._load_dataset("deployment_plans")
        return available, records.get(plan_id)

    def approval_decision(self, decision_id: Optional[str]) -> tuple[bool, Optional[Dict[str, Any]]]:
        if not decision_id:
            available, _ = self._load_dataset("approval_decisions")
            return available, None
        available, records = self._load_dataset("approval_decisions")
        return available, records.get(str(decision_id))

    def capital_pool(self, pool_id: Optional[str]) -> tuple[bool, Optional[Dict[str, Any]]]:
        if not pool_id:
            available, _ = self._load_dataset("capital_pools")
            return available, None
        available, records = self._load_dataset("capital_pools")
        if not available:
            return available, None
        record = records.get(str(pool_id))
        if record is not None:
            return True, record
        # The dataset dict may be keyed by a value other than the pool's public id: when the
        # canonical payload is a JSON object, `_normalize_records` keys by the object's own keys
        # (not `pool_id`/`id`), while `list_capital_pools` exposes each pool as `pool_id or id`.
        # Resolve by that same identity so every id the list surface returns has a working detail
        # endpoint (previously such pools 404'd even though they appeared in the list).
        target = str(pool_id)
        for candidate in records.values():
            if str(candidate.get("pool_id") or candidate.get("id") or "") == target:
                return True, candidate
        return True, None

    def binding(self, binding_id: Optional[str]) -> tuple[bool, Optional[Dict[str, Any]]]:
        if not binding_id:
            available, _ = self._load_dataset("persona_bindings")
            return available, None
        available, records = self._load_dataset("persona_bindings")
        return available, records.get(str(binding_id))

    def bindings_for_pool(self, pool_id: Optional[str]) -> tuple[bool, List[Dict[str, Any]]]:
        available, records = self._load_dataset("persona_bindings")
        if not available or not pool_id:
            return available, []
        return True, [
            record
            for record in records.values()
            if str(record.get("capital_pool_id") or "") == str(pool_id)
        ]

    def runtime_binding(self, binding_id: Optional[str]) -> tuple[bool, Optional[Dict[str, Any]]]:
        if not binding_id:
            available, _ = self._load_dataset("runtime_bindings")
            return available, None
        available, records = self._load_dataset("runtime_bindings")
        return available, records.get(str(binding_id))

    def runtime_binding_for_plan(self, plan_id: Optional[str]) -> tuple[bool, Optional[Dict[str, Any]]]:
        available, records = self._load_dataset("runtime_bindings")
        if not available or not plan_id:
            return available, None
        for record in records.values():
            if str(record.get("plan_id") or "") == str(plan_id):
                return True, record
        return True, None


class ServiceBackedReadAdapter:
    """Best-effort adapter over backend-owned JSON stores produced by services."""

    _DATASETS = {
        "personas": {
            "env": "PANTHEON_BFF_PERSONA_REGISTRY_STORE",
            "dirs": ("PANTHEON_PERSONA_DATA_DIR",),
            "filenames": ("personas.json",),
            "keys": ["persona_id", "id"],
            "snapshot_key": "personas",
        },
        "sessions": {
            "env": "PANTHEON_BFF_PERSONA_SESSION_STORE",
            "dirs": ("PANTHEON_PERSONA_DATA_DIR",),
            "filenames": ("sessions.json",),
            "keys": ["session_id", "id"],
            "snapshot_key": "sessions",
        },
        "capability_snapshots": {
            "env": "PANTHEON_BFF_CAPABILITY_SNAPSHOT_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["snapshot_id", "id"],
            "snapshot_key": "capability_snapshots",
        },
        "teaching_sessions": {
            "env": "PANTHEON_BFF_TEACHING_SESSION_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["session_id", "id"],
            "snapshot_key": "teaching_sessions",
        },
        "trainer_previews": {
            "env": "PANTHEON_BFF_TRAINER_PREVIEW_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["session_id", "id"],
            "snapshot_key": "trainer_previews",
        },
        "consultation_sessions": {
            "env": "PANTHEON_BFF_CONSULTATION_SESSION_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["session_id", "id"],
            "snapshot_key": "consultation_sessions",
        },
        "consult_transcripts": {
            "env": "PANTHEON_BFF_CONSULT_TRANSCRIPT_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["session_id", "transcript_id"],
            "snapshot_key": "consult_transcripts",
        },
        "consult_policies": {
            "env": "PANTHEON_BFF_CONSULT_POLICY_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["persona_id", "id"],
            "snapshot_key": "consult_policies",
        },
        "route_policies": {
            "env": "PANTHEON_BFF_ROUTE_POLICY_STORE",
            "dirs": (
                "PANTHEON_PERSONA_DATA_DIR",
                "PANTHEON_CONTROL_PLANE_DATA_DIR",
            ),
            "filenames": (
                "route_policies.json",
                "persona_route_policies.json",
            ),
            "keys": ["policy_id", "route_policy_id", "id"],
            "snapshot_key": "route_policies",
        },
        "incidents": {
            "env": "PANTHEON_BFF_INCIDENT_STORE",
            "dirs": ("INCIDENTS_DATA_DIR", "POSTMORTEMS_DATA_DIR"),
            "filenames": ("incidents.json",),
            "keys": ["incident_id", "id"],
            "nested_key": "incidents",
            "snapshot_key": "incidents",
        },
        "postmortems": {
            "env": "PANTHEON_BFF_POSTMORTEM_STORE",
            "dirs": ("POSTMORTEMS_DATA_DIR", "INCIDENTS_DATA_DIR"),
            "filenames": ("postmortems.json", "incidents.json"),
            "keys": ["postmortem_id", "id"],
            "nested_key": "postmortems",
            "snapshot_key": "postmortems",
        },
        "formula_jobs": {
            "env": "PANTHEON_BFF_FORMULA_JOBS_STORE",
            "dirs": ("FORMULA_JOBS_DATA_DIR",),
            "filenames": ("formula_jobs.json", "jobs.json"),
            "keys": ["job_id", "id"],
            "snapshot_key": "formula_jobs",
        },
        "activity_audit": {
            "env": "PANTHEON_BFF_ACTIVITY_AUDIT_STORE",
            "dirs": ("ACTIVITY_AUDIT_DATA_DIR",),
            "filenames": ("activity_audit.json", "activities.json", "activity.json"),
            "keys": ["event_id", "id"],
            "snapshot_key": "activity_audit",
        },
        "paper_telemetry": {
            "env": "PANTHEON_BFF_PAPER_TELEMETRY_STORE",
            "dirs": ("PAPER_TELEMETRY_DATA_DIR",),
            "filenames": ("paper_telemetry.json", "telemetry.json"),
            "keys": ["strategy_id", "paper_ledger_id", "id"],
            "snapshot_key": "paper_telemetry",
        },
        "kill_switch": {
            "env": "PANTHEON_BFF_KILL_SWITCH_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["id", "status"],
            "snapshot_key": "kill_switch",
        },
        "evolution_decisions": {
            "env": "PANTHEON_BFF_EVOLUTION_DECISION_STORE",
            "dirs": ("EVOLUTION_DATA_DIR",),
            "filenames": ("decisions.json",),
            "keys": ["decision_id", "id"],
            "snapshot_key": "evolution_decisions",
        },
        "freeze_orders": {
            "env": "PANTHEON_BFF_FREEZE_ORDER_STORE",
            "dirs": ("PANTHEON_GOVERNANCE_DATA_DIR", "GOVERNANCE_DATA_DIR"),
            "filenames": ("freeze_orders.json",),
            "keys": ["freeze_order_id", "id"],
            "snapshot_key": "freeze_orders",
        },
        "all_rollbacks": {
            "env": "PANTHEON_BFF_ROLLBACK_STORE",
            "dirs": ("PANTHEON_GOVERNANCE_DATA_DIR", "GOVERNANCE_DATA_DIR"),
            "filenames": ("rollbacks.json",),
            "keys": ["rollback_id", "id"],
            "snapshot_key": "all_rollbacks",
        },
        "evolution_programs": {
            "env": "PANTHEON_BFF_EVOLUTION_PROGRAM_STORE",
            "dirs": ("EVOLUTION_DATA_DIR",),
            "filenames": ("evolution_programs.json",),
            "keys": ["program_id", "id"],
            "snapshot_key": "evolution_programs",
        },
        "telemetry_summaries": {
            "env": "PANTHEON_BFF_TELEMETRY_SUMMARY_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["runtime_id", "id"],
            "snapshot_key": "telemetry_summaries",
        },
        "telemetry_events": {
            "env": "PANTHEON_BFF_TELEMETRY_EVENT_STORE",
            "dirs": ("TELEMETRY_STORAGE_DIR", "PANTHEON_TELEMETRY_DATA_DIR"),
            "filenames": (
                "telemetry_events.jsonl",
                "telemetry_events.json",
                "events.jsonl",
                "events.json",
            ),
            "keys": ["event_id", "telemetry_event_id", "id"],
            "nested_key": "events",
            "snapshot_key": "telemetry_events",
        },
        "telemetry_performance": {
            "env": "PANTHEON_BFF_TELEMETRY_PERFORMANCE_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["artifact_id", "id"],
            "snapshot_key": "telemetry_performance",
        },
        "paper_live_drift_reports": {
            "env": "PANTHEON_BFF_PAPER_LIVE_DRIFT_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["runtime_id", "id"],
            "snapshot_key": "paper_live_drift_reports",
        },
        "lineage_edges": {
            "env": "PANTHEON_BFF_LINEAGE_EDGE_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["id"],
            "snapshot_key": "lineage_edges",
        },
        "inspiration_graphs": {
            "env": "PANTHEON_BFF_INSPIRATION_GRAPH_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["artifact_id", "id"],
            "snapshot_key": "inspiration_graphs",
        },
        "research_tickets": {
            "env": "PANTHEON_BFF_RESEARCH_TICKET_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["ticket_id", "id"],
            "snapshot_key": "research_tickets",
        },
        "research_experiments": {
            "env": "PANTHEON_BFF_RESEARCH_EXPERIMENT_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["experiment_id", "id"],
            "snapshot_key": "research_experiments",
        },
        "research_artifacts": {
            "env": "PANTHEON_BFF_RESEARCH_ARTIFACT_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["artifact_id", "id"],
            "snapshot_key": "research_artifacts",
        },
        "research_notes": {
            "env": "PANTHEON_BFF_RESEARCH_NOTES_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["note_id", "id"],
            "snapshot_key": "research_notes",
        },
        "research_analyses": {
            "env": "PANTHEON_BFF_RESEARCH_ANALYSIS_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["analysis_id", "id"],
            "snapshot_key": "research_analyses",
        },
        "evidence_refs": {
            "env": "PANTHEON_BFF_EVIDENCE_REF_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["ref_id", "id"],
            "snapshot_key": "evidence_refs",
        },
        "insight_cards": {
            "env": "PANTHEON_BFF_INSIGHT_CARD_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["insight_id", "id"],
            "snapshot_key": "insight_cards",
        },
        "agora_signals": {
            "env": "PANTHEON_BFF_AGORA_SIGNAL_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["signal_id", "id"],
            "snapshot_key": "agora_signals",
        },
        "agora_sessions": {
            "env": "PANTHEON_BFF_AGORA_SESSION_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["sessionId", "session_id", "id"],
            "snapshot_key": "agora_sessions",
        },
        "agora_skill_coaching_sessions": {
            "env": "PANTHEON_BFF_AGORA_SKILL_COACHING_SESSION_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["sessionId", "session_id", "id"],
            "snapshot_key": "agora_skill_coaching_sessions",
        },
        "agora_persona_lab_runs": {
            "env": "PANTHEON_BFF_AGORA_PERSONA_LAB_RUN_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["runId", "run_id", "id"],
            "snapshot_key": "agora_persona_lab_runs",
        },
        "agora_evaluation_suites": {
            "env": "PANTHEON_BFF_AGORA_EVALUATION_SUITE_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["suiteId", "suite_id", "id"],
            "snapshot_key": "agora_evaluation_suites",
        },
        "agora_evaluation_runs": {
            "env": "PANTHEON_BFF_AGORA_EVALUATION_RUN_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["runId", "run_id", "id"],
            "snapshot_key": "agora_evaluation_runs",
        },
        "agora_watchlist": {
            "env": "PANTHEON_BFF_AGORA_WATCHLIST_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["watchlist_id", "symbol", "id"],
            "snapshot_key": "agora_watchlist",
        },
        "agora_committee_evidence_packs": {
            "env": "PANTHEON_BFF_AGORA_COMMITTEE_EVIDENCE_PACK_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["packId", "pack_id", "id", "sessionId", "session_id"],
            "snapshot_key": "agora_committee_evidence_packs",
        },
        "agora_handoffs": {
            "env": "PANTHEON_BFF_AGORA_HANDOFF_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["handoffId", "handoff_id", "id"],
            "snapshot_key": "agora_handoffs",
        },
        "agora_training_examples": {
            "env": "PANTHEON_BFF_AGORA_TRAINING_EXAMPLE_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["trainingExampleId", "training_example_id", "example_id", "id"],
            "snapshot_key": "agora_training_examples",
        },
        "agora_audit_events": {
            "env": "PANTHEON_BFF_AGORA_AUDIT_EVENT_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["auditId", "audit_id", "eventId", "event_id", "id"],
            "snapshot_key": "agora_audit_events",
        },
        "institutional_memory_entries": {
            "env": "PANTHEON_BFF_INSTITUTIONAL_MEMORY_STORE",
            "dirs": ("PANTHEON_MEMORY_DATA_DIR",),
            "filenames": ("institutional_memory_entries.json", "institutional_memory.json"),
            "keys": ["entry_id", "id"],
            "snapshot_key": "institutional_memory_entries",
        },
        "strategy_specs": {
            "env": "PANTHEON_BFF_STRATEGY_SPEC_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["strategy_id", "id"],
            "snapshot_key": "strategy_specs",
        },
        "research_search_documents": {
            "env": "PANTHEON_BFF_RESEARCH_SEARCH_DOCUMENT_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["result_id", "id"],
            "snapshot_key": "research_search_documents",
        },
        "research_search_index": {
            "env": "PANTHEON_BFF_RESEARCH_SEARCH_INDEX_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["adapter_id", "id"],
            "snapshot_key": "research_search_index",
        },
        "consult_requests": {
            "env": "PANTHEON_BFF_CONSULT_REQUEST_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["request_id", "id"],
            "snapshot_key": "consult_requests",
        },
        "consult_memos": {
            "env": "PANTHEON_BFF_CONSULT_MEMO_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["memo_id", "id"],
            "snapshot_key": "consult_memos",
        },
        "trainer_replays": {
            "env": "PANTHEON_BFF_TRAINER_REPLAY_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["session_id", "id"],
            "snapshot_key": "trainer_replays",
        },
        "trainer_controls": {
            "env": "PANTHEON_BFF_TRAINER_CONTROL_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["session_id", "id"],
            "snapshot_key": "trainer_controls",
        },
        "workflow_templates": {
            "env": "PANTHEON_BFF_WORKFLOW_TEMPLATE_STORE",
            "dirs": (
                "PANTHEON_AUTOMATION_DATA_DIR",
                "PANTHEON_CRON_DATA_DIR",
                "PANTHEON_CONTROL_PLANE_DATA_DIR",
            ),
            "filenames": (
                "workflow_templates.json",
                "workflow_registry.json",
                "workflows.json",
            ),
            "keys": ["workflow_id", "template_id", "id", "name"],
            "snapshot_key": "workflow_templates",
        },
        "hook_registry": {
            "env": "PANTHEON_BFF_HOOK_REGISTRY_STORE",
            "dirs": (
                "PANTHEON_AUTOMATION_DATA_DIR",
                "PANTHEON_CRON_DATA_DIR",
                "PANTHEON_CONTROL_PLANE_DATA_DIR",
            ),
            "filenames": (
                "hook_registry.json",
                "cron_hooks.json",
                "hooks.json",
            ),
            "keys": ["hook_id", "cron_id", "id", "name"],
            "snapshot_key": "hook_registry",
        },
        "jobs": {
            "env": "PANTHEON_BFF_JOB_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["job_id", "run_id", "id"],
            "snapshot_key": "jobs",
        },
        "decision_journal_entries": {
            "env": "PANTHEON_BFF_DECISION_JOURNAL_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["entry_id", "id"],
            "snapshot_key": "decision_journal_entries",
        },
        "loop_runs": {
            "env": "PANTHEON_BFF_LOOP_RUN_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["id"],
            "snapshot_key": "loop_runs",
            "nested_key": "records",
            "snapshot_requires_key": True,
        },
        "loop_health": {
            "env": "PANTHEON_BFF_LOOP_HEALTH_STORE",
            "dirs": ("PANTHEON_CONTROL_PLANE_DATA_DIR",),
            "filenames": ("loop_health.json", "loop-health.json"),
            "keys": ["loop_id", "id"],
            "snapshot_key": "loop_health",
        },
        "sentinel_findings": {
            "env": "PANTHEON_BFF_SENTINEL_FINDING_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["id"],
            "snapshot_key": "sentinel_findings",
        },
        "v5_interventions": {
            "env": "PANTHEON_BFF_V5_INTERVENTION_STORE",
            "dirs": (),
            "filenames": (),
            "keys": ["intervention_id", "id"],
            "snapshot_key": "v5_interventions",
        },
        "ooda_packets": {
            "env": "PANTHEON_BFF_OODA_PACKET_STORE",
            "dirs": ("PANTHEON_OODA_DATA_DIR", "PANTHEON_CONTROL_PLANE_DATA_DIR"),
            "filenames": (
                "ooda_packets.jsonl",
                "ooda_loop_packets.jsonl",
                "loop_packets.jsonl",
                "ooda_packets.json",
            ),
            "keys": ["packet_id", "id"],
            "snapshot_key": "ooda_packets",
        },
        "synthesis_conflict_logs": {
            "env": "PANTHEON_BFF_SYNTHESIS_CONFLICT_LOG_STORE",
            "dirs": ("PANTHEON_SYNTHESIS_DATA_DIR", "PANTHEON_OPTIMIZER_DATA_DIR"),
            "filenames": (
                "synthesis_conflict_logs.jsonl",
                "conflict_resolution_logs.jsonl",
                "conflict_logs.jsonl",
                "synthesis_conflict_logs.json",
                "conflict_resolution_logs.json",
            ),
            "keys": ["log_id", "id", "conflict_resolution_log_id"],
            "snapshot_key": "synthesis_conflict_logs",
        },
        "ranking_formulas": {
            "env": "PANTHEON_BFF_RANKING_FORMULA_STORE",
            "dirs": (
                "PANTHEON_CAPITAL_DATA_DIR",
                "PANTHEON_CONTROL_PLANE_DATA_DIR",
            ),
            "filenames": ("ranking_formulas.json",),
            "keys": ["formula_id", "id"],
            "snapshot_key": "ranking_formulas",
        },
        "rankings": {
            "env": "PANTHEON_BFF_RANKING_STORE",
            "dirs": (
                "PANTHEON_CAPITAL_DATA_DIR",
                "PANTHEON_CONTROL_PLANE_DATA_DIR",
            ),
            "filenames": ("rankings.json",),
            "keys": ["ranking_id", "id"],
            "snapshot_key": "rankings",
        },
        "skills": {
            "env": "PANTHEON_BFF_SKILLS_STORE",
            "dirs": ("PANTHEON_CONTROL_PLANE_DATA_DIR",),
            "filenames": ("skills.json",),
            "keys": ["skill_id", "id"],
            "snapshot_key": "skills",
        },
        "tools": {
            "env": "PANTHEON_BFF_TOOLS_STORE",
            "dirs": ("PANTHEON_CONTROL_PLANE_DATA_DIR",),
            "filenames": ("tools.json",),
            "keys": ["tool_id", "id"],
            "snapshot_key": "tools",
        },
        "mcp_servers": {
            "env": "PANTHEON_BFF_MCP_SERVERS_STORE",
            "dirs": ("PANTHEON_CONTROL_PLANE_DATA_DIR",),
            "filenames": ("mcp_servers.json",),
            "keys": ["server_id", "id"],
            "snapshot_key": "mcp_servers",
        },
        "mcp_tools": {
            "env": "PANTHEON_BFF_MCP_TOOLS_STORE",
            "dirs": ("PANTHEON_CONTROL_PLANE_DATA_DIR",),
            "filenames": ("mcp_tools.json",),
            "keys": ["tool_id", "id"],
            "snapshot_key": "mcp_tools",
        },
        "formula_jobs": {
            "env": "PANTHEON_BFF_FORMULA_JOBS_STORE",
            "dirs": ("PANTHEON_FORMULA_DATA_DIR", "PANTHEON_CONTROL_PLANE_DATA_DIR"),
            "filenames": ("formula_jobs.json", "formula_jobs.jsonl"),
            "keys": ["job_id", "id"],
            "snapshot_key": "formula_jobs",
        },
        "activity_audit": {
            "env": "PANTHEON_BFF_ACTIVITY_AUDIT_STORE",
            "dirs": ("PANTHEON_AUDIT_DATA_DIR", "PANTHEON_CONTROL_PLANE_DATA_DIR"),
            "filenames": ("activity_audit.json", "activity_audit.jsonl", "audit_events.jsonl"),
            "keys": ["event_id", "id"],
            "snapshot_key": "activity_audit",
        },
        "paper_telemetry": {
            "env": "PANTHEON_BFF_PAPER_TELEMETRY_STORE",
            "dirs": ("PANTHEON_TELEMETRY_DATA_DIR", "PANTHEON_CONTROL_PLANE_DATA_DIR"),
            "filenames": ("paper_telemetry.json", "paper_telemetry.jsonl"),
            "keys": ["strategy_id", "id"],
            "snapshot_key": "paper_telemetry",
        },
    }

    _HTTP_DATASETS = {
        "incidents": {
            "base_env": ("PANTHEON_INCIDENTS_API_URL", "PANTHEON_INCIDENTS_URL"),
            "list_path": "/api/incidents",
        },
        "postmortems": {
            "base_env": ("PANTHEON_POSTMORTEMS_API_URL", "PANTHEON_POSTMORTEMS_URL"),
            "list_path": "/api/postmortems",
        },
        "evolution_decisions": {
            "base_env": ("PANTHEON_EVOLUTION_API_URL", "PANTHEON_GOVERNANCE_API_URL"),
            "list_path": "/api/evolution/proposals",
        },
        "freeze_orders": {
            "base_env": (
                "PANTHEON_GOVERNANCE_APPROVAL_API_URL",
                "PANTHEON_GOVERNANCE_SERVICE_URL",
            ),
            "list_path": "/api/governance/freeze-orders",
            "require_list_payload": True,
        },
        "all_rollbacks": {
            "base_env": (
                "PANTHEON_GOVERNANCE_APPROVAL_API_URL",
                "PANTHEON_GOVERNANCE_SERVICE_URL",
            ),
            "list_path": "/api/governance/rollbacks",
            "require_list_payload": True,
        },
        "telemetry_summaries": {
            "base_env": ("PANTHEON_TELEMETRY_API_URL", "PANTHEON_TELEMETRY_URL"),
            "list_path": "/api/telemetry/runtime-summaries",
            "list_key": "summaries",
        },
        "telemetry_events": {
            "base_env": ("PANTHEON_TELEMETRY_API_URL", "PANTHEON_TELEMETRY_URL"),
            "list_path": "/api/telemetry/events",
            "list_key": "events",
        },
        "lineage_edges": {
            "base_env": ("PANTHEON_LINEAGE_READ_URL", "PANTHEON_LINEAGE_API_URL"),
            "list_path": "/api/v1/lineage",
        },
        "teaching_sessions": {
            "base_env": ("PANTHEON_TRAINING_SESSION_API_URL", "PANTHEON_TRAINING_SESSION_URL"),
            "list_path": "/api/training/sessions",
        },
        "trainer_controls": {
            "base_env": ("PANTHEON_TRAINING_SESSION_API_URL", "PANTHEON_TRAINING_SESSION_URL"),
            "list_path": "/api/training/controls",
        },
        "trainer_previews": {
            "base_env": ("PANTHEON_TRAINING_SESSION_API_URL", "PANTHEON_TRAINING_SESSION_URL"),
            "list_path": "/api/training/previews",
        },
        "trainer_replays": {
            "base_env": ("PANTHEON_TRAINING_SESSION_API_URL", "PANTHEON_TRAINING_SESSION_URL"),
            "list_path": "/api/training/replays",
        },
        "institutional_memory_entries": {
            "base_env": ("PANTHEON_MEMORY_API_URL", "PANTHEON_MEMORY_SERVICE_URL"),
            "list_path": "/api/memory/entries",
            "list_key": "entries",
        },
        "ooda_packets": {
            "base_env": ("PANTHEON_OODA_API_URL", "PANTHEON_CONTROL_PLANE_OODA_URL"),
            "list_path": "/api/ooda/packets",
            "list_key": "items",
        },
    }

    def __init__(
        self,
        *,
        snapshot_path: Optional[Path] = None,
        allow_snapshot_fallback: bool = True,
    ) -> None:
        self._cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._cache_meta: Dict[str, tuple[str, int]] = {}
        self._cache_source: Dict[str, str] = {}
        self._cache_envelope_metadata: Dict[str, Dict[str, Any]] = {}
        self._snapshot_path = snapshot_path or _default_bff_snapshot_path()
        self._allow_snapshot_fallback = allow_snapshot_fallback

    def _load_http_dataset(self, dataset: str) -> tuple[bool, Dict[str, Dict[str, Any]]]:
        spec = self._HTTP_DATASETS.get(dataset)
        if not spec:
            return False, {}
        base_url = _base_url_from_env(tuple(spec.get("base_env") or ()))
        if not base_url:
            return False, {}
        available, payload = _http_json_get(
            base_url,
            str(spec.get("list_path") or ""),
            headers=_auth_headers_from_spec(spec),
        )
        if not available:
            return False, {}
        records_payload = _records_from_http_payload(
            payload,
            list_key=spec.get("list_key"),
        )
        if spec.get("require_list_payload") and not isinstance(records_payload, list):
            return False, {}
        if dataset == "lineage_edges" and isinstance(records_payload, list):
            records_payload = [
                {
                    **record,
                    "from_artifact_id": record.get("from_artifact_id") or record.get("source_id"),
                    "to_artifact_id": record.get("to_artifact_id") or record.get("target_id"),
                }
                if isinstance(record, dict)
                else record
                for record in records_payload
            ]
        normalized = _normalize_records(records_payload, self._DATASETS[dataset]["keys"])
        self._cache[dataset] = normalized
        self._cache_meta[dataset] = (f"{base_url}{spec.get('list_path')}", 0)
        self._cache_source[dataset] = "service_client"
        return True, normalized

    def _resolve_path(self, dataset: str) -> Optional[Path]:
        spec = self._DATASETS[dataset]
        explicit = os.getenv(spec["env"], "").strip()
        if explicit:
            return Path(explicit)
        candidates: List[Path] = []
        for dir_env in spec["dirs"]:
            base = os.getenv(dir_env, "").strip()
            if not base:
                continue
            for filename in spec["filenames"]:
                candidates.append(Path(base) / filename)
        return _first_existing(candidates)

    def _load_dataset(
        self,
        dataset: str,
        *,
        include_snapshot_fallback: bool = True,
    ) -> tuple[bool, Dict[str, Dict[str, Any]]]:
        http_available, http_records = self._load_http_dataset(dataset)
        if http_available:
            return True, http_records

        path = self._resolve_path(dataset)
        if path is not None and path.exists():
            stat = path.stat().st_mtime_ns
            cache_key = str(path)
            if self._cache_meta.get(dataset) == (cache_key, stat):
                return True, self._cache.get(dataset, {})

            payload = _load_record_store_payload(path)
            if dataset == "loop_runs" and isinstance(payload, dict):
                controller = payload.get("controller")
                self._cache_envelope_metadata[dataset] = {
                    "schema_version": payload.get("schema_version"),
                    "generation": payload.get("generation"),
                    "controller": dict(controller) if isinstance(controller, dict) else {},
                }
            else:
                self._cache_envelope_metadata.pop(dataset, None)
            if dataset == "ooda_packets":
                payload = _project_ooda_packet_store_payload(payload)
            if dataset == "synthesis_conflict_logs":
                payload = _project_synthesis_conflict_log_store_payload(payload)
            nested_key = self._DATASETS[dataset].get("nested_key")
            if nested_key and isinstance(payload, dict) and str(nested_key) in payload:
                payload = payload.get(str(nested_key), {})
            normalized = _normalize_records(payload, self._DATASETS[dataset]["keys"])
            self._cache[dataset] = normalized
            self._cache_meta[dataset] = (cache_key, stat)
            self._cache_source[dataset] = "service_store"
            return True, normalized

        snapshot_key = self._DATASETS[dataset].get("snapshot_key")
        if include_snapshot_fallback and self._allow_snapshot_fallback and snapshot_key:
            if self._DATASETS[dataset].get("snapshot_requires_key") and self._snapshot_path.exists():
                try:
                    snapshot_payload = _load_record_store_payload(self._snapshot_path)
                except (OSError, ValueError):
                    snapshot_payload = None
                if not isinstance(snapshot_payload, dict) or snapshot_key not in snapshot_payload:
                    self._cache_envelope_metadata.pop(dataset, None)
                    return False, {}
            available, normalized, cache_meta = _load_snapshot_dataset(
                self._snapshot_path,
                str(snapshot_key),
                self._DATASETS[dataset]["keys"],
            )
            if available:
                if self._cache_meta.get(dataset) != cache_meta:
                    self._cache[dataset] = normalized
                    self._cache_meta[dataset] = cache_meta
                    self._cache_source[dataset] = "local_snapshot"
                    self._cache_envelope_metadata.pop(dataset, None)
                return True, self._cache.get(dataset, normalized)

        self._cache_envelope_metadata.pop(dataset, None)
        return False, {}

    def source(self, dataset: str) -> str:
        return self._cache_source.get(dataset, "service_store")

    def cached_source(self, dataset: str) -> Optional[str]:
        """Return provenance from an earlier read without touching the backend."""
        return self._cache_source.get(dataset)

    def envelope_metadata(self, dataset: str) -> Dict[str, Any]:
        """Return wrapper metadata retained during the most recent dataset read."""
        return json.loads(json.dumps(self._cache_envelope_metadata.get(dataset, {})))

    def list_records(
        self,
        dataset: str,
        *,
        include_snapshot_fallback: bool = True,
    ) -> tuple[bool, List[Dict[str, Any]]]:
        available, records = self._load_dataset(
            dataset,
            include_snapshot_fallback=include_snapshot_fallback,
        )
        return available, list(records.values())

    def record(
        self,
        dataset: str,
        record_id: Optional[str],
        *,
        include_snapshot_fallback: bool = True,
    ) -> tuple[bool, Optional[Dict[str, Any]]]:
        if not record_id:
            available, _ = self._load_dataset(
                dataset,
                include_snapshot_fallback=include_snapshot_fallback,
            )
            return available, None
        available, records = self._load_dataset(
            dataset,
            include_snapshot_fallback=include_snapshot_fallback,
        )
        return available, records.get(str(record_id))

    _LOOP_RUN_ID_RE = re.compile(r"loop-run-(\d+)$")
    _SENTINEL_FINDING_ID_RE = re.compile(r"sentinel-finding-(\d+)$")

    @staticmethod
    def _derive_loop_run(inc: Dict[str, Any], *, override_id: Optional[str] = None) -> Dict[str, Any]:
        inc_id = str(inc.get("incident_id") or inc.get("id") or "")
        return {
            "id": override_id or inc_id,
            "status": inc.get("status", "unknown"),
            "activePeriod": {"start": inc.get("created_at"), "end": inc.get("resolved_at")},
            "derived_from_incident_id": inc_id,
            "runtime_id": inc.get("runtime_id"),
            "binding_id": inc.get("binding_id"),
            # Incident reconstruction is retained only as an explicitly
            # degraded compatibility view.  It is neither the canonical loop
            # ledger nor evidence that the lifecycle projector is live.
            "source": "legacy_incident_backfill",
            "projection_mode": "backfill",
            "truth_level": "legacy_backfill",
            "accepted_live": False,
            "read_state": "degraded",
        }

    _SENTINEL_KIND_KEYWORDS: Dict[str, List[str]] = {
        "hiq_sentinel": ["hiq", "sentinel"],
        "risk_breach": ["risk", "breach", "capital"],
        "strategy_drift": ["drift", "strategy"],
        "loop_anomaly": ["loop", "anomaly"],
    }

    @classmethod
    def _infer_sentinel_kind(cls, inc: Dict[str, Any]) -> Optional[str]:
        """Infer sentinel finding kind from kind field or title keywords."""
        raw_kind = str(inc.get("kind") or "").strip().lower()
        if raw_kind in cls._SENTINEL_KIND_KEYWORDS:
            return raw_kind
        title_lower = str(inc.get("title") or "").lower()
        for kind, keywords in cls._SENTINEL_KIND_KEYWORDS.items():
            if any(kw in title_lower for kw in keywords):
                return kind
        return None

    @classmethod
    def _derive_sentinel_finding(cls, inc: Dict[str, Any], *, override_id: Optional[str] = None) -> Dict[str, Any]:
        inc_id = str(inc.get("incident_id") or inc.get("id") or "")
        return {
            "id": override_id or inc_id,
            "status": inc.get("status", "unknown"),
            "kind": inc.get("kind") or cls._infer_sentinel_kind(inc),
            "derived_from_incident_id": inc_id,
            "runtime_id": inc.get("runtime_id"),
            "binding_id": inc.get("binding_id"),
            "severity": inc.get("severity"),
            "title": inc.get("title"),
        }

    def list_loop_runs(self) -> tuple[bool, List[Dict[str, Any]]]:
        # A configured projector ledger is authoritative, including when it is
        # valid but empty.  Incident reconstruction is an incident-only legacy
        # fallback and must never be merged into canonical lifecycle truth.
        avail_lr, lr_data = self._load_dataset("loop_runs")
        if avail_lr:
            return True, [run for run in lr_data.values() if isinstance(run, dict)]

        avail_inc, incidents = self._load_dataset("incidents")
        if not avail_inc:
            return False, []
        return True, [
            self._derive_loop_run(inc)
            for inc in incidents.values()
            if isinstance(inc, dict)
            and "sentinel" not in str(inc.get("title") or "").lower()
        ]

    def get_loop_run(self, loop_run_id: str) -> tuple[bool, Optional[Dict[str, Any]]]:
        # Canonical lookup is first and conclusive.  A missing ID in an
        # available canonical ledger must not resolve to an unrelated incident.
        avail_lr, lr_data = self._load_dataset("loop_runs")
        if avail_lr:
            return True, lr_data.get(loop_run_id)

        avail_inc, incidents = self._load_dataset("incidents")
        if avail_inc:
            inc = incidents.get(loop_run_id)
            if inc and isinstance(inc, dict) and "sentinel" not in str(inc.get("title") or "").lower():
                return True, self._derive_loop_run(inc)
            m = self._LOOP_RUN_ID_RE.match(loop_run_id)
            if m:
                n = int(m.group(1))
                non_sentinel = [
                    v for v in incidents.values()
                    if isinstance(v, dict) and "sentinel" not in str(v.get("title") or "").lower()
                ]
                if 1 <= n <= len(non_sentinel):
                    return True, self._derive_loop_run(non_sentinel[n - 1], override_id=loop_run_id)
        if avail_inc:
            return True, None
        return False, None

    def list_loop_health_records(self) -> tuple[bool, List[Dict[str, Any]]]:
        return self.list_records("loop_health")

    def get_loop_health_record(self, loop_id: str) -> tuple[bool, Optional[Dict[str, Any]]]:
        return self.record("loop_health", loop_id)

    def list_sentinel_findings(
        self,
        *,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
    ) -> tuple[bool, List[Dict[str, Any]]]:
        avail_inc, incidents = self._load_dataset("incidents")
        if avail_inc:
            results = [
                self._derive_sentinel_finding(inc)
                for inc in incidents.values()
                if isinstance(inc, dict) and "loop" not in str(inc.get("title") or "").lower()
            ]
            return True, self._apply_sentinel_filters(results, kind=kind, status=status, severity=severity)
        avail_sf, sf_data = self._load_dataset("sentinel_findings")
        if avail_sf:
            results = list(sf_data.values())
            return True, self._apply_sentinel_filters(results, kind=kind, status=status, severity=severity)
        return False, []

    @staticmethod
    def _apply_sentinel_filters(
        records: List[Dict[str, Any]],
        *,
        kind: Optional[str],
        status: Optional[str],
        severity: Optional[str],
    ) -> List[Dict[str, Any]]:
        out = records
        if kind is not None:
            kind_lower = kind.lower()
            out = [r for r in out if str(r.get("kind") or "").lower() == kind_lower]
        if status is not None:
            status_lower = status.lower()
            out = [r for r in out if str(r.get("status") or "").lower() == status_lower]
        if severity is not None:
            severity_lower = severity.lower()
            out = [r for r in out if str(r.get("severity") or "").lower() == severity_lower]
        return out

    def get_sentinel_finding(self, finding_id: str) -> tuple[bool, Optional[Dict[str, Any]]]:
        avail_inc, incidents = self._load_dataset("incidents")
        if avail_inc:
            inc = incidents.get(finding_id)
            if inc and isinstance(inc, dict) and "loop" not in str(inc.get("title") or "").lower():
                return True, self._derive_sentinel_finding(inc)
            m = self._SENTINEL_FINDING_ID_RE.match(finding_id)
            if m:
                n = int(m.group(1))
                non_loop = [
                    v for v in incidents.values()
                    if isinstance(v, dict) and "loop" not in str(v.get("title") or "").lower()
                ]
                if 1 <= n <= len(non_loop):
                    return True, self._derive_sentinel_finding(non_loop[n - 1], override_id=finding_id)
            return True, None
        avail_sf, sf_data = self._load_dataset("sentinel_findings")
        if avail_sf:
            return True, sf_data.get(finding_id)
        return False, None

    def write_records(self, dataset: str, records: Dict[str, Dict[str, Any]]) -> bool:
        path = self._resolve_path(dataset)
        if path is None:
            return False

        normalized = {
            str(key): json.loads(json.dumps(value))
            for key, value in records.items()
            if isinstance(value, dict)
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(normalized, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
        self._cache[dataset] = normalized
        self._cache_meta[dataset] = (str(path), path.stat().st_mtime_ns)
        return True


def _default_read_data() -> Dict[str, Any]:
    data = {
        "deployment_plans": {
            "plan-F-042": {
                "id": "plan-F-042",
                "plan_id": "plan-F-042",
                "stage": "paper",
                "current_stage": "none",
                "target_stage": "paper",
                "status": "approved",
                "artifact_id": "artifact-042",
                "artifact_version": "v1.4.2",
                "submitted_at": "2026-04-11T07:45:00Z",
                "transition_type": "activate",
                "approval_decision_id": "approval-042",
                "capital_pool_id": "pool-main",
                "binding_ids": ["binding-042"],
                "runtime_binding_id": "runtime-042",
            }
        },
        "approval_decisions": {
            "approval-042": {
                "id": "approval-042",
                "outcome": "approved",
                "state": "decided",
                "reviewer": "governance",
                "decided_at": "2026-04-11T07:55:00Z",
                "risk_level": "low",
            },
            "appr-dec-c5a9f11e": {
                "id": "appr-dec-c5a9f11e",
                "outcome": None,
                "state": "under_review",
                "reviewer": "reviewer-001",
                "actor_role": "reviewer",
                "decided_at": None,
                "risk_level": "medium",
                "rationale": "Medium-risk freeze proposal is awaiting final approval.",
            }
        },
        "capital_pools": {
            "pool-main": {
                "id": "pool-main",
                "name": "Primary Capital Pool",
                "status": "ready",
                "owner_id": "ops-team",
                "owner_type": "control-plane",
                "single_runtime_enforced": True,
                "risk_policy_ref": "risk-policy-main",
            }
        },
        "bindings": {
            "binding-042": {
                "id": "binding-042",
                "persona_id": "persona-alpha",
                "capital_pool_id": "pool-main",
                "role": "primary",
                "validity": "active",
                "status": "active",
                "allowed_deployment_scope": "paper",
            }
        },
        "personas": {
            "persona-alpha": {
                "id": "persona-alpha",
                "name": "Alpha Persona",
                "lifecycle_state": "active",
                "mandate": "systematic_crypto_trading",
                "strategy_family": "momentum",
                "created_at": "2026-03-01T00:00:00Z",
                "last_active_at": "2026-04-11T10:00:00Z",
            },
            "p-risk-analyst": {  # consultation responder persona
                "id": "p-risk-analyst",
                "name": "Risk Analyst Persona",
                "lifecycle_state": "active",
                "mandate": "risk_review",
                "strategy_family": "risk_management",
                "created_at": "2026-02-15T00:00:00Z",
                "last_active_at": "2026-04-10T10:14:00Z",
            },
            "p-macro-observer": {
                "id": "p-macro-observer",
                "name": "Macro Observer",
                "lifecycle_state": "active",
                "mandate": "macro_regime_review",
                "strategy_family": "macro",
                "created_at": "2026-02-20T00:00:00Z",
                "last_active_at": "2026-04-19T17:08:00Z",
            },
            "p-execution-lead": {
                "id": "p-execution-lead",
                "name": "Execution Lead",
                "lifecycle_state": "active",
                "mandate": "execution_quality",
                "strategy_family": "execution",
                "created_at": "2026-02-24T00:00:00Z",
                "last_active_at": "2026-04-19T17:09:20Z",
            },
            "p-compliance-sponsor": {
                "id": "p-compliance-sponsor",
                "name": "Compliance Sponsor",
                "lifecycle_state": "active",
                "mandate": "governance_sponsorship",
                "strategy_family": "governance",
                "created_at": "2026-02-28T00:00:00Z",
                "last_active_at": "2026-04-19T17:11:00Z",
            },
        },
        "sessions": {
            "sess-001": {
                "id": "sess-001",
                "session_id": "sess-001",
                "persona_id": "persona-alpha",
                "session_type": "interactive",
                "status": "active",
                "started_at": "2026-04-11T08:00:00Z",
                "capability_snapshot_id": "cap-001",
                "trace_id": "trace-sess-001",
                "request_id": "req-sess-001",
                "runtime_binding_id": "runtime-042",
                "deployment_stage": "paper",
                "capital_pool_id": "pool-main",
                "last_heartbeat_at": "2026-04-11T11:55:00Z",
                "tools_enabled": ["signal_read", "artifact_load", "telemetry_query"],
                "pool_scope": "pool-main",
            },
            "sess-002": {
                "id": "sess-002",
                "session_id": "sess-002",
                "persona_id": "persona-alpha",
                "session_type": "interactive",
                "status": "idle",
                "started_at": "2026-04-10T14:00:00Z",
                "capability_snapshot_id": "cap-001",
                "trace_id": "trace-sess-002",
                "request_id": "req-sess-002",
                "runtime_binding_id": "runtime-042",
                "deployment_stage": "paper",
                "capital_pool_id": "pool-main",
                "last_heartbeat_at": "2026-04-10T18:00:00Z",
                "tools_enabled": ["signal_read", "artifact_load"],
                "pool_scope": "pool-main",
            },
        },
        "capability_snapshots": {
            "cap-001": {
                "snapshot_id": "cap-001",
                "persona_id": "persona-alpha",
                "effective_tools": ["signal_read", "artifact_load", "telemetry_query"],
                "effective_skills": ["risk_review", "incident_triage"],
                "effective_workflows": ["promotion_review", "incident_response"],
                "restrictions": ["no_live_trade_without_approval"],
                "generated_at": "2026-04-11T07:55:00Z",
                "source_refs": ["persona:persona-alpha", "policy:risk-policy-main"],
            },
        },
        "teaching_sessions": {
            "trn-20260419-001": {
                "id": "trn-20260419-001",
                "session_id": "trn-20260419-001",
                "persona_id": "persona-alpha",
                "session_type": "trainer",
                "opened_by": "operator-hedging-desk",
                "status": "active",
                "started_at": "2026-04-19T19:30:00Z",
                "ended_at": None,
                "objective": "Tighten event-window response and reduce premature signal reversals during macro surprise sessions.",
                "context_refs": [
                    {
                        "type": "research_ticket",
                        "id": "rt-20260419-007",
                    },
                    {
                        "type": "memory_entry",
                        "id": "mem-8f3c6d45-7d61-4c61-a0c6-3c5e8d1740f1",
                    },
                ],
                "actor_context": {
                    "persona_display_name": "Alpha Persona",
                    "persona_role_context": "systematic momentum coach",
                },
                "events": [
                    {
                        "event_id": "tevt-20260419-001",
                        "session_id": "trn-20260419-001",
                        "actor": "operator",
                        "message_body": "Focus on macro surprise windows where the current policy reverses within the first three bars after a data release.",
                        "emitted_at": "2026-04-19T19:30:11Z",
                        "sequence_number": 1,
                        "outcome_signal": None,
                    },
                    {
                        "event_id": "tevt-20260419-002",
                        "session_id": "trn-20260419-001",
                        "actor": "persona",
                        "message_body": "I am overweighting immediate reversal probability after event spikes. I can hold the initial directional bias longer when volatility expands with breadth confirmation.",
                        "emitted_at": "2026-04-19T19:31:04Z",
                        "sequence_number": 2,
                        "outcome_signal": "acknowledged-adjustment",
                    },
                    {
                        "event_id": "tevt-20260419-003",
                        "session_id": "trn-20260419-001",
                        "actor": "operator",
                        "message_body": "Constrain reversal sensitivity during the first five minutes unless the spread regime also deteriorates.",
                        "emitted_at": "2026-04-19T19:37:40Z",
                        "sequence_number": 3,
                        "outcome_signal": "candidate-adjustment-ready",
                    },
                ],
                "topic": "macro surprise window coaching",
                "outcomes": ["candidate-adjustment-ready"],
            },
            "trn-20260418-003": {
                "id": "trn-20260418-003",
                "session_id": "trn-20260418-003",
                "persona_id": "persona-alpha",
                "session_type": "trainer",
                "opened_by": "operator-risk-desk",
                "status": "completed",
                "started_at": "2026-04-18T08:00:00Z",
                "ended_at": "2026-04-18T08:42:00Z",
                "objective": "Reduce regime-switch whipsaw sensitivity in drawdown containment mode.",
                "context_refs": [],
                "actor_context": {
                    "persona_display_name": "Alpha Persona",
                    "persona_role_context": "systematic momentum coach",
                },
                "events": [
                    {
                        "event_id": "tevt-20260418-001",
                        "session_id": "trn-20260418-003",
                        "actor": "operator",
                        "message_body": "Focus on drawdown containment and reduce sensitivity to short-lived regime flips.",
                        "emitted_at": "2026-04-18T08:00:12Z",
                        "sequence_number": 1,
                        "outcome_signal": None,
                    },
                    {
                        "event_id": "tevt-20260418-002",
                        "session_id": "trn-20260418-003",
                        "actor": "persona",
                        "message_body": "I can widen confirmation requirements before reversing the posture in containment mode.",
                        "emitted_at": "2026-04-18T08:42:00Z",
                        "sequence_number": 2,
                        "outcome_signal": "teaching-complete",
                    },
                ],
                "topic": "drawdown containment coaching",
                "outcomes": ["teaching-complete"],
            },
        },
        "trainer_previews": {
            "trn-20260419-001": {
                "session_id": "trn-20260419-001",
                "latest_eval_id": "teval-20260419-014",
                "evaluations": {
                    "teval-20260419-014": {
                        "session_id": "trn-20260419-001",
                        "status": "complete",
                        "eval_id": "teval-20260419-014",
                        "baseline_snapshot_at": "2026-04-19T19:43:30Z",
                        "candidate_snapshot_at": "2026-04-19T19:48:00Z",
                        "control_diff": [
                            {
                                "control_id": "ctrl-reversal-threshold",
                                "parameter_key": "reversal_threshold",
                                "display_label": "Reversal Threshold",
                                "previous_value": 0.65,
                                "new_value": 0.9,
                                "unit": "score",
                                "last_modified_at": "2026-04-19T19:45:12Z",
                            },
                            {
                                "control_id": "ctrl-hold-bars",
                                "parameter_key": "minimum_hold_bars",
                                "display_label": "Minimum Hold Bars",
                                "previous_value": 3,
                                "new_value": 4,
                                "unit": "bars",
                                "last_modified_at": "2026-04-19T19:43:30Z",
                            },
                        ],
                        "metric_delta": [
                            {
                                "metric_key": "event_window_reversal_rate",
                                "display_label": "Event Window Reversal Rate",
                                "baseline_value": 0.31,
                                "candidate_value": 0.22,
                                "delta": -0.09,
                                "delta_pct": -29.03,
                                "unit": "ratio",
                                "direction": "improved",
                            },
                            {
                                "metric_key": "sponsor_alignment_score",
                                "display_label": "Sponsor Alignment Score",
                                "baseline_value": 0.74,
                                "candidate_value": 0.81,
                                "delta": 0.07,
                                "delta_pct": 9.46,
                                "unit": "score",
                                "direction": "improved",
                            },
                            {
                                "metric_key": "latency_to_recover_after_false_break",
                                "display_label": "False-Break Recovery Latency",
                                "baseline_value": 3.2,
                                "candidate_value": 3.6,
                                "delta": 0.4,
                                "delta_pct": 12.5,
                                "unit": "bars",
                                "direction": "regressed",
                            },
                        ],
                        "warnings": [
                            {
                                "warning_id": "warn-preview-20260419-001",
                                "warning_code": "upper_bound_pressure",
                                "level": "high",
                                "parameter_key": "reversal_threshold",
                                "metric_key": "latency_to_recover_after_false_break",
                                "message": "The candidate pushes reversal sensitivity near the upper bound and increases false-break recovery latency.",
                                "impact_summary": "Recovery remains slower in thin-liquidity regimes even though immediate reversal rate improves.",
                            },
                            {
                                "warning_id": "warn-preview-20260419-002",
                                "warning_code": "limited_regime_coverage",
                                "level": "medium",
                                "parameter_key": None,
                                "metric_key": "sponsor_alignment_score",
                                "message": "Rapid-eval coverage is directional only for the last two FOMC surprise windows.",
                                "impact_summary": "Preview is useful for operator review, but the sample is not broad enough for unattended promotion.",
                            },
                            {
                                "warning_id": "warn-preview-20260419-003",
                                "warning_code": "spread_gate_note",
                                "level": "informational",
                                "parameter_key": "spread_regime_gate",
                                "metric_key": None,
                                "message": "Spread regime gate remains unchanged from the current baseline.",
                                "impact_summary": "No additional gating was introduced for stable spread sessions.",
                            },
                        ],
                        "warning_count_by_level": {
                            "critical": 0,
                            "high": 1,
                            "medium": 1,
                            "informational": 1,
                        },
                        "preview_quality": "directional_only",
                        "allowedActions": {
                            "canRefreshPreview": True,
                        },
                        "polling": {
                            "enabled": False,
                            "poll_interval_ms": 3000,
                            "max_wait_ms": 45000,
                            "deadline_at": None,
                        },
                        "degraded_copy": None,
                        "meta": {
                            "snapshot_at": "2026-04-19T19:48:04Z",
                            "surfaces": {
                                "trainer_preview": "ok",
                            },
                        },
                    },
                    "teval-20260419-015": {
                        "session_id": "trn-20260419-001",
                        "status": "pending",
                        "eval_id": "teval-20260419-015",
                        "baseline_snapshot_at": "2026-04-19T19:43:30Z",
                        "candidate_snapshot_at": "2026-04-19T19:50:00Z",
                        "control_diff": [
                            {
                                "control_id": "ctrl-reversal-threshold",
                                "parameter_key": "reversal_threshold",
                                "display_label": "Reversal Threshold",
                                "previous_value": 0.65,
                                "new_value": 0.85,
                                "unit": "score",
                                "last_modified_at": "2026-04-19T19:50:00Z",
                            },
                        ],
                        "metric_delta": [],
                        "warnings": [],
                        "warning_count_by_level": {
                            "critical": 0,
                            "high": 0,
                            "medium": 0,
                            "informational": 0,
                        },
                        "preview_quality": "directional_only",
                        "allowedActions": {
                            "canRefreshPreview": False,
                        },
                        "polling": {
                            "enabled": True,
                            "poll_interval_ms": 3000,
                            "max_wait_ms": 45000,
                            "deadline_at": "2026-04-20T19:50:45Z",
                        },
                        "degraded_copy": {
                            "title": "Trainer preview is still running",
                            "body": "Pantheon is evaluating the current trainer candidate. Keep the compare surface open and poll again after the published interval.",
                        },
                        "meta": {
                            "snapshot_at": "2026-04-19T19:50:03Z",
                            "surfaces": {
                                "trainer_preview": "ok",
                            },
                        },
                    },
                    "teval-20260419-016": {
                        "session_id": "trn-20260419-001",
                        "status": "failed",
                        "eval_id": "teval-20260419-016",
                        "baseline_snapshot_at": "2026-04-19T19:43:30Z",
                        "candidate_snapshot_at": "2026-04-19T19:51:10Z",
                        "control_diff": [
                            {
                                "control_id": "ctrl-hold-bars",
                                "parameter_key": "minimum_hold_bars",
                                "display_label": "Minimum Hold Bars",
                                "previous_value": 3,
                                "new_value": 5,
                                "unit": "bars",
                                "last_modified_at": "2026-04-19T19:51:10Z",
                            },
                        ],
                        "metric_delta": [],
                        "warnings": [],
                        "warning_count_by_level": {
                            "critical": 0,
                            "high": 0,
                            "medium": 0,
                            "informational": 0,
                        },
                        "preview_quality": "insufficient_data",
                        "allowedActions": {
                            "canRefreshPreview": False,
                        },
                        "polling": {
                            "enabled": False,
                            "poll_interval_ms": 3000,
                            "max_wait_ms": 45000,
                            "deadline_at": None,
                        },
                        "degraded_copy": {
                            "title": "Trainer preview could not complete",
                            "body": "Pantheon could not finish the rapid-eval for this candidate. Review the current control diff, then retry the preview when the compare surface is healthy.",
                        },
                        "meta": {
                            "snapshot_at": "2026-04-19T19:51:22Z",
                            "surfaces": {
                                "trainer_preview": "degraded",
                            },
                        },
                    },
                },
            },
            "trn-20260418-003": {
                "session_id": "trn-20260418-003",
                "latest_eval_id": None,
                "evaluations": {},
                "preview": {
                    "session_id": "trn-20260418-003",
                    "status": "preview_unavailable",
                    "eval_id": None,
                    "baseline_snapshot_at": "2026-04-18T08:35:00Z",
                    "candidate_snapshot_at": "2026-04-18T08:40:00Z",
                    "control_diff": [
                        {
                            "control_id": "ctrl-reversal-threshold",
                            "parameter_key": "reversal_threshold",
                            "display_label": "Reversal Threshold",
                            "previous_value": 0.65,
                            "new_value": 0.8,
                            "unit": "score",
                            "last_modified_at": "2026-04-18T08:40:00Z",
                        },
                    ],
                    "metric_delta": [],
                    "warnings": [],
                    "warning_count_by_level": {
                        "critical": 0,
                        "high": 0,
                        "medium": 0,
                        "informational": 0,
                    },
                    "preview_quality": "not_available",
                    "allowedActions": {
                        "canRefreshPreview": False,
                    },
                    "polling": {
                        "enabled": False,
                        "poll_interval_ms": 3000,
                        "max_wait_ms": 45000,
                        "deadline_at": None,
                    },
                    "degraded_copy": {
                        "title": "Trainer preview is temporarily unavailable",
                        "body": "Pantheon cannot serve rapid-eval results for the trainer compare surface right now. Control changes remain visible, but before/after metrics are temporarily unavailable.",
                    },
                    "meta": {
                        "snapshot_at": "2026-04-18T08:40:07Z",
                        "surfaces": {
                            "trainer_preview": "degraded",
                        },
                    },
                },
            },
        },
        "runtime_bindings": {
            "runtime-042": {
                "id": "runtime-042",
                "runtime_id": "runtime-042",
                "persona_id": "persona-alpha",
                "binding_id": "binding-042",
                "runtime_binding_id": "binding-042",
                "persona_capital_binding_id": "binding-042",
                "deployment_mode": "paper",
                "deployment_stage": "none",
                "status": "idle",
                "plan_id": "plan-F-042",
                "artifact_id": "artifact-042",
                "artifact_version": "v2.1.0",
            }
        },
        "registry_entries": {
            "artifact-041": {
                "registry_id": "artifact-041",
                "artifact_id": "artifact-041",
                "artifact_type": "strategy",
                "version": "v2.0.0",
                "artifact_version": "v2.0.0",
            },
            "artifact-042": {
                "registry_id": "artifact-042",
                "artifact_id": "artifact-042",
                "artifact_type": "strategy",
                "version": "v2.1.0",
                "artifact_version": "v2.1.0",
            },
            "artifact-043": {
                "registry_id": "artifact-043",
                "artifact_id": "artifact-043",
                "artifact_type": "strategy",
                "version": "v2.2.0",
                "artifact_version": "v2.2.0",
            },
        },
        "rollbacks": {
            "runtime-042": []
        },
        "allowed_actions": {
            "plan-F-042": {
                "canApprove": False,
                "canReject": False,
                "canPromoteToPaper": True
            }
        },
        "latest_runs": {
            "plan-F-042": {
                "progress": 0.82
            }
        },
        "review_summaries": {
            "plan-F-042": {
                "riskSummary": "No unresolved severity-1 or severity-2 incidents."
            }
        },
        # ------------------------------------------------------------------ #
        # Incident surfaces (IN-01 – IN-05)
        # ------------------------------------------------------------------ #
        "incidents": {
            "inc-20260410-001": {
                "incident_id": "inc-20260410-001",
                "title": "Unexpected drawdown in persona-alpha",
                "severity": "high",
                "status": "open",
                "created_at": "2026-04-10T14:30:00Z",
                "binding_id": "runtime-042",
                "deployment_stage": "live",
                "deployment_plan_id": "plan-F-042",
                "capital_pool_id": "pool-main",
                "persona_capital_binding_id": "binding-042",
                "artifact_id": "artifact-042",
                "artifact_version": "v2.1.0",
                "runtime_id": "runtime-042",
                "trace_id": "trace-inc-20260410-001",
                "telemetry_event_ids": ["tl-001"],
                "evidence_summary": "12% drawdown exceeded 10% threshold; runtime paused pending review.",
                "lineage_ref": "artifact-042@v2.1.0",
            },
            "inc-20260409-002": {
                "incident_id": "inc-20260409-002",
                "title": "Deployment plan plan-F-042 stalled at paper stage",
                "severity": "medium",
                "status": "resolved",
                "created_at": "2026-04-09T08:00:00Z",
                "resolved_at": "2026-04-09T10:30:00Z",
                "binding_id": "runtime-042",
                "deployment_stage": "paper",
                "deployment_plan_id": "plan-F-042",
                "capital_pool_id": "pool-main",
                "persona_capital_binding_id": "binding-042",
                "artifact_id": "artifact-042",
                "artifact_version": "v2.1.0",
                "runtime_id": "runtime-042",
                "trace_id": "trace-inc-20260409-002",
                "telemetry_event_ids": [],
                "evidence_summary": "Promotion gate timeout during artifact validation.",
                "lineage_ref": "artifact-042@v2.1.0",
            },
        },
        "postmortems": {
            "pm-20260409-002": {
                "postmortem_id": "pm-20260409-002",
                "incident_id": "inc-20260409-002",
                "title": "Postmortem: Deployment plan F-042 promotion timeout",
                "status": "published",
                "created_at": "2026-04-09T11:00:00Z",
                "published_at": "2026-04-09T12:00:00Z",
                "binding_id": "runtime-042",
                "deployment_stage": "paper",
                "deployment_plan_id": "plan-F-042",
                "capital_pool_id": "pool-main",
                "persona_capital_binding_id": "binding-042",
                "artifact_id": "artifact-042",
                "artifact_version": "v2.1.0",
                "runtime_id": "runtime-042",
                "trace_id": "trace-inc-20260409-002",
                "root_cause": "Promotion gate timeout was set too low (30s) for artifact validation under load.",
                "contributing_factors": [
                    "Artifact validation queue became saturated during peak load",
                    "Timeout threshold was insufficient for large artifact bundles",
                ],
                "timeline": [
                    {"at": "2026-04-09T08:00:00Z", "event": "Incident opened"},
                    {"at": "2026-04-09T10:30:00Z", "event": "Incident resolved"},
                    {"at": "2026-04-09T11:00:00Z", "event": "Postmortem drafted"},
                ],
                "action_items": [
                    "Increase promotion gate timeout to 120s",
                    "Add queue-depth alerting for promotion gate",
                ],
                "author_ids": ["platform"],
            },
        },
        "kill_switch": {
            "active": False,
            "active_freeze_orders": [],
            "last_checked_at": "2026-04-11T12:00:00Z",
            "last_confirmed_at": "2026-04-11T12:00:00Z",
            "last_triggered_at": None,
            "active_commands": [],
            "secondary_path_available": True,
            "safe_mode_status": "off",
            "status": "armed",
        },
        # Cross-references for composed views
        "evolution_decisions": {
            "evo-dec-001": {
                "id": "evo-dec-001",
                "action_type": "retrain",
                "risk_level": "medium",
                "status": "approved",
                "incident_ref": "inc-20260410-001",
                "artifact_id": "artifact-042",
                "created_at": "2026-04-10T16:00:00Z",
                "updated_at": "2026-04-11T09:00:00Z",
                "notes": "Approved for retrain after promotion gate timeout root cause confirmed.",
            },
            "evo-dec-88f3a2c1": {
                "id": "evo-dec-88f3a2c1",
                "decision_id": "evo-dec-88f3a2c1",
                "target_type": "candidate_artifact",
                "target_id": "artifact-44d7e9b0",
                "target_version": "v3.1.2",
                "target_stage": "canary",
                "action_type": "freeze_canary",
                "risk_level": "medium",
                "status": "reviewed",
                "decision_state": "reviewed",
                "approval_decision_id": "appr-dec-c5a9f11e",
                "created_at": "2026-04-18T09:32:00Z",
                "updated_at": "2026-04-18T11:05:00Z",
                "created_by_role": "evolution_controller",
                "created_by_id": "evo-controller-01",
                "rationale": "Freeze candidate artifact at canary stage due to sustained slippage drift exceeding the 25% execution drift threshold over three consecutive trading days.",
                "notes": "Slippage drift confirmed. Forwarded to Risk Owner for final approval.",
                "linked_incident_id": None,
                "linked_postmortem_id": None,
                "evidence_refs": [
                    {
                        "ref_type": "drift_report",
                        "ref_id": "drift-rpt-b7c2d3e4",
                        "summary": "Execution drift report: 3-day slippage anomaly on artifact-44d7e9b0 canary stage (2026-04-15 – 2026-04-17).",
                    },
                    {
                        "ref_type": "telemetry_summary",
                        "ref_id": "telem-sum-9a1f0e22",
                        "summary": "Telemetry summary: realized slippage drift at 31% above 20-day baseline.",
                    },
                ],
                "threshold_snapshots": [
                    {
                        "signal_type": "execution_drift",
                        "metric_name": "realized_slippage_drift_pct",
                        "observed_value": "0.31",
                        "threshold_value": "0.25",
                        "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.2",
                    },
                    {
                        "signal_type": "execution_drift",
                        "metric_name": "consecutive_anomaly_days",
                        "observed_value": "3",
                        "threshold_value": "3",
                        "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.2",
                    },
                ],
                "review_chain": [
                    {
                        "step_type": "reviewed",
                        "actor_role": "reviewer",
                        "actor_id": "reviewer-001",
                        "timestamp": "2026-04-18T11:05:00Z",
                        "note": "Slippage drift confirmed. Forwarded to Risk Owner for final approval.",
                    }
                ],
                "proposed_changes": {
                    "summary": "Freeze candidate artifact 'artifact-44d7e9b0' at canary stage due to sustained slippage drift exceeding the 25% execution drift threshold over three consecutive trading days.",
                    "target_stage": "canary",
                    "downstream_plane": "governance",
                    "change_details": [
                        {
                            "field": "artifact_stage",
                            "current_value": "canary",
                            "proposed_value": "frozen",
                            "note": "Governance quarantine only; existing canary runtime not automatically stopped unless a companion operational follow-through is initiated.",
                        },
                        {
                            "field": "admissibility",
                            "current_value": "eligible",
                            "proposed_value": "quarantined",
                            "note": None,
                        },
                    ],
                },
                "risk_assessment": {
                    "risk_summary": "Sustained execution drift on canary stage triggered medium-risk freeze proposal. Slippage drift observed at 31% above the 20-day baseline, exceeding the 25% threshold defined in DriftPolicy.",
                    "severity": None,
                    "threshold_triggers": [
                        {
                            "trigger_type": "execution_drift",
                            "metric": "realized_slippage_drift_pct",
                            "observed_value": "0.31",
                            "threshold_value": "0.25",
                            "threshold_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.2",
                        },
                        {
                            "trigger_type": "execution_drift",
                            "metric": "consecutive_anomaly_days",
                            "observed_value": "3",
                            "threshold_value": "3",
                            "threshold_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.2",
                        },
                    ],
                },
                "required_approvals": [
                    {
                        "role": "reviewer",
                        "approved_by": "reviewer-001",
                        "approved_at": "2026-04-18T11:05:00Z",
                        "status": "approved",
                    },
                    {
                        "role": "risk_owner",
                        "approved_by": None,
                        "approved_at": None,
                        "status": "pending",
                    },
                ],
                "rollback_followthrough": None,
            },
        },
        "rollbacks_by_incident": {
            "inc-20260410-001": [
                {
                    "id": "rb-001",
                    "runtime_id": "runtime-042",
                    "action_type": "rollback",
                    "from_version": "v2.1.0",
                    "to_version": "v2.0.0",
                    "status": "completed",
                    "initiated_at": "2026-04-10T14:45:00Z",
                    "completed_at": "2026-04-10T14:50:00Z",
                    "initiated_by": "operator-oncall",
                    "reason": "Excessive drawdown triggered automatic rollback",
                }
            ],
            "inc-20260409-002": [],
        },
        "telemetry_summaries": {
            "runtime-042": {
                "runtime_id": "runtime-042",
                "window": "1h",
                "pnl": -0.12,
                "drawdown": 0.125,
                "sharpe_ratio": -0.8,
                "total_trades": 47,
                "fill_rate": 0.94,
                "avg_slippage_bps": 3.2,
                "collected_at": "2026-04-10T15:00:00Z",
            },
        },
        # EV-03: Freeze orders
        "freeze_orders": {
            "fo-001": {
                "id": "fo-001",
                "scope": "persona",
                "target_id": "persona-alpha",
                "status": "active",
                "created_at": "2026-04-10T14:35:00Z",
                "created_by": "system",
                "reason": "Excessive drawdown triggered automatic freeze",
                "incident_ref": "inc-20260410-001",
            },
        },
        # EV-04: Global rollback list (flat, not grouped by incident)
        "all_rollbacks": [
            {
                "id": "rb-001",
                "runtime_id": "runtime-042",
                "action_type": "rollback",
                "from_version": "v2.1.0",
                "to_version": "v2.0.0",
                "status": "completed",
                "initiated_at": "2026-04-10T14:45:00Z",
                "completed_at": "2026-04-10T14:50:00Z",
                "initiated_by": "operator-oncall",
                "reason": "Excessive drawdown triggered automatic rollback",
                "incident_ref": "inc-20260410-001",
            },
        ],
        "rollback_reviews": {
            "rollback-rb-001": {
                "rollback_id": "rollback-rb-001",
                "target_plan_id": "plan-dp-000",
                "trigger_reason": "Automated risk trigger: max_drawdown threshold breached during paper trading window.",
                "requested_at": "2026-04-16T09:30:00Z",
                "requested_by": "risk-monitor",
                "rollback_scope": "partial",
                "affected_persona_count": 2,
                "affected_binding_count": 3,
                "target_stage": "paper",
                "position_impact": [
                    {
                        "binding_id": "binding-001",
                        "persona_id": "persona-alpha",
                        "current_stage": "paper",
                        "target_stage": "paper",
                        "position_impact_summary": "Open long position of 4% portfolio weight will be closed before rollback. No live positions affected.",
                        "position_data_stale": False,
                    },
                    {
                        "binding_id": "binding-002",
                        "persona_id": "persona-beta",
                        "current_stage": "paper",
                        "target_stage": "paper",
                        "position_impact_summary": "No open positions; rollback is position-neutral for this binding.",
                        "position_data_stale": False,
                    },
                    {
                        "binding_id": "binding-003",
                        "persona_id": "persona-alpha",
                        "current_stage": "paper",
                        "target_stage": "paper",
                        "position_impact_summary": None,
                        "position_data_stale": True,
                    },
                ],
                "affected_bindings": [
                    {
                        "binding_id": "binding-001",
                        "persona_id": "persona-alpha",
                        "capital_pool_id": "pool-002",
                        "current_stage": "paper",
                    },
                    {
                        "binding_id": "binding-002",
                        "persona_id": "persona-beta",
                        "capital_pool_id": "pool-001",
                        "current_stage": "paper",
                    },
                    {
                        "binding_id": "binding-003",
                        "persona_id": "persona-alpha",
                        "capital_pool_id": "pool-002",
                        "current_stage": "paper",
                    },
                ],
                "trigger_evidence": {
                    "trigger_reason": "Automated risk trigger: max_drawdown threshold breached during paper trading window.",
                    "evidence_refs": [
                        {"ref_id": "ev-rb-001", "type": "TelemetryAlert", "url": None},
                        {"ref_id": "ev-rb-002", "type": "RiskControlEvent", "url": None},
                    ],
                    "linked_incident_id": None,
                },
                "allowedActions": {
                    "canApproveRollback": False,
                    "canRejectRollback": True,
                },
                "meta": {
                    "snapshot_at": "2026-04-16T10:00:00Z",
                    "surfaces": {
                        "position_data": {"status": "degraded"},
                        "rollback_review": {"status": "ok"},
                        "allowedActions": {"status": "ok"},
                    },
                },
            },
        },
        "governance_audit_events": [
            {
                "entry_id": "audit-001",
                "actor": "operator-jane",
                "action_type": "ApproveDecision",
                "target_type": "ApprovalDecision",
                "target_id": "appr-001",
                "timestamp": "2026-04-16T10:05:00Z",
                "outcome": "success",
                "audit_context": {
                    "reason": "Risk review completed; all evidence within acceptable bounds.",
                },
                "evidence_refs": [
                    {"ref_id": "ev-101", "type": "BacktestResult", "url": None},
                ],
            },
            {
                "entry_id": "audit-002",
                "actor": "operator-jane",
                "action_type": "ForwardToApprovalQueue",
                "target_type": "GovernanceReviewItem",
                "target_id": "gov-review-001",
                "timestamp": "2026-04-16T09:58:00Z",
                "outcome": "success",
                "audit_context": {
                    "reason": "Review complete; forwarding to approval.",
                },
                "evidence_refs": [],
            },
            {
                "entry_id": "audit-003",
                "actor": "risk-monitor",
                "action_type": "EscalateGovernanceItem",
                "target_type": "GovernanceReviewItem",
                "target_id": "gov-review-002",
                "timestamp": "2026-04-16T09:45:00Z",
                "outcome": "escalated",
                "audit_context": {
                    "reason": None,
                },
                "evidence_refs": [
                    {"ref_id": "ev-103", "type": "EvolutionDecision", "url": None},
                ],
            },
            {
                "entry_id": "audit-004",
                "actor": "operator-bob",
                "action_type": "RejectRollback",
                "target_type": "Rollback",
                "target_id": "rollback-rb-001",
                "timestamp": "2026-04-16T09:40:00Z",
                "outcome": "success",
                "audit_context": {
                    "reason": "Position data is stale; cannot safely approve rollback at this time.",
                },
                "evidence_refs": [],
            },
            {
                "entry_id": "audit-005",
                "actor": "operator-jane",
                "action_type": "RequestGovernanceChanges",
                "target_type": "GovernanceReviewItem",
                "target_id": "gov-review-003",
                "timestamp": "2026-04-16T09:20:00Z",
                "outcome": "success",
                "audit_context": {
                    "reason": "Capital pool reference needs correction before approval.",
                },
                "evidence_refs": [],
            },
        ],
        "governance_review_queue_items": {
            "gov-review-001": {
                "item_id": "gov-review-001",
                "item_type": "DeploymentPlan",
                "risk_level": "medium",
                "status": "pending",
                "submitted_at": "2026-04-14T06:15:00Z",
                "submitted_by": "orchestrator",
                "governance_outcome": "pending",
                "allowedActions": {
                    "canReview": True,
                    "canForwardToApproval": True,
                    "canRequestChanges": True,
                    "canEscalate": False,
                },
                "review_summary": {
                    "risk_assessment": "Medium risk - parameter drift within acceptable bounds; no open severity-1 or severity-2 incidents.",
                    "evidence_refs": [
                        {"ref_id": "ev-001", "type": "IncidentReport", "url": None},
                        {"ref_id": "ev-002", "type": "BacktestResult", "url": None},
                    ],
                    "linked_approval_decision_id": None,
                },
            },
            "gov-review-002": {
                "item_id": "gov-review-002",
                "item_type": "EvolutionProposal",
                "risk_level": "high",
                "status": "escalated",
                "submitted_at": "2026-04-14T05:45:00Z",
                "submitted_by": "orchestrator",
                "governance_outcome": "escalated",
                "allowedActions": {
                    "canReview": False,
                    "canForwardToApproval": False,
                    "canRequestChanges": False,
                    "canEscalate": False,
                },
                "review_summary": {
                    "risk_assessment": "High risk - escalated due to open severity-2 incident; no further routing CTAs available until resolved.",
                    "evidence_refs": [
                        {"ref_id": "ev-003", "type": "IncidentReport", "url": None},
                    ],
                    "linked_approval_decision_id": None,
                },
            },
            "gov-review-003": {
                "item_id": "gov-review-003",
                "item_type": "PersonaBinding",
                "risk_level": "low",
                "status": "in_review",
                "submitted_at": "2026-04-14T07:00:00Z",
                "submitted_by": "orchestrator",
                "governance_outcome": "pending",
                "allowedActions": {
                    "canReview": True,
                    "canForwardToApproval": True,
                    "canRequestChanges": True,
                    "canEscalate": True,
                },
                "review_summary": {
                    "risk_assessment": "Low risk - new persona binding within established capital pool; no open incidents.",
                    "evidence_refs": [],
                    "linked_approval_decision_id": None,
                },
            },
        },
        "approval_queue_items": {
            "appr-001": {
                "decision_id": "appr-001",
                "decision_type": "DeploymentPlan",
                "risk_level": "medium",
                "submitted_at": "2026-04-16T08:15:00Z",
                "submitted_by": "governance-review-queue",
                "decision_state": "pending",
                "allowedActions": {
                    "canApprove": True,
                    "canReject": True,
                    "canRequestRevision": True,
                },
                "decision_context": {
                    "risk_summary": "Medium risk — parameter drift within acceptable bounds; no open severity-1 or severity-2 incidents. Review queue forwarded after passing risk threshold check.",
                    "evidence_refs": [
                        {"ref_id": "ev-101", "type": "BacktestResult", "url": None},
                        {"ref_id": "ev-102", "type": "IncidentReport", "url": None},
                    ],
                    "governance_chain": {
                        "linked_review_item_id": "gov-review-001",
                    },
                    "required_approvals": 1,
                },
            },
            "appr-002": {
                "decision_id": "appr-002",
                "decision_type": "PersonaBinding",
                "risk_level": "low",
                "submitted_at": "2026-04-16T09:00:00Z",
                "submitted_by": "governance-review-queue",
                "decision_state": "pending",
                "allowedActions": {
                    "canApprove": True,
                    "canReject": True,
                    "canRequestRevision": False,
                },
                "decision_context": {
                    "risk_summary": "Low risk — new persona binding within established capital pool; no open incidents.",
                    "evidence_refs": [],
                    "governance_chain": {
                        "linked_review_item_id": "gov-review-003",
                    },
                    "required_approvals": 1,
                },
            },
            "appr-003": {
                "decision_id": "appr-003",
                "decision_type": "EvolutionProposal",
                "risk_level": "high",
                "submitted_at": "2026-04-16T07:30:00Z",
                "submitted_by": "governance-review-queue",
                "decision_state": "in_review",
                "allowedActions": {
                    "canApprove": False,
                    "canReject": False,
                    "canRequestRevision": False,
                },
                "decision_context": {
                    "risk_summary": "High risk — evolution proposal involves parameter changes beyond threshold bounds; additional review in progress.",
                    "evidence_refs": [
                        {"ref_id": "ev-103", "type": "EvolutionDecision", "url": None},
                    ],
                    "governance_chain": {
                        "linked_review_item_id": "gov-review-002",
                    },
                    "required_approvals": 2,
                },
            },
        },
        "deployment_diffs": {
            "plan-dp-001": {
                "plan_id": "plan-dp-001",
                "artifact_id": "artifact-abc123",
                "stage": "paper",
                "submitted_at": "2026-04-16T08:00:00Z",
                "submitted_by": "orchestrator",
                "previous_plan_id": "plan-dp-000",
                "first_deployment": False,
                "changes": [
                    {
                        "field_path": "parameters.max_drawdown",
                        "previous_value": "0.08",
                        "current_value": "0.10",
                        "change_reason": "Risk policy updated to allow 10% max drawdown for this persona class after governance review.",
                        "change_category": "parameters",
                        "risk_tier": "medium",
                    },
                    {
                        "field_path": "parameters.position_size_limit",
                        "previous_value": "0.05",
                        "current_value": "0.04",
                        "change_reason": "Position size limit reduced to align with updated capital pool allocation.",
                        "change_category": "capital_allocation",
                        "risk_tier": "low",
                    },
                    {
                        "field_path": "bindings[0].capital_pool_id",
                        "previous_value": "pool-001",
                        "current_value": "pool-002",
                        "change_reason": "Binding reassigned to new capital pool after pool-001 was retired.",
                        "change_category": "bindings",
                        "risk_tier": "high",
                    },
                    {
                        "field_path": "risk_controls.stop_loss_threshold",
                        "previous_value": "0.03",
                        "current_value": "0.025",
                        "change_reason": "Stop-loss threshold tightened following post-incident review recommendations.",
                        "change_category": "risk_controls",
                        "risk_tier": "medium",
                    },
                ],
                "change_summary": {
                    "total_changes": 4,
                    "by_category": {
                        "parameters": {"count": 1, "highest_risk_tier": "medium"},
                        "capital_allocation": {"count": 1, "highest_risk_tier": "low"},
                        "bindings": {"count": 1, "highest_risk_tier": "high"},
                        "risk_controls": {"count": 1, "highest_risk_tier": "medium"},
                        "stage_transition": {"count": 0, "highest_risk_tier": None},
                    },
                },
                "allowedActions": {
                    "canProceedToApproval": True,
                    "canEscalateDiff": True,
                },
            },
            "plan-dp-002": {
                "plan_id": "plan-dp-002",
                "artifact_id": "artifact-def456",
                "stage": "paper",
                "submitted_at": "2026-04-16T11:20:00Z",
                "submitted_by": "orchestrator",
                "previous_plan_id": None,
                "first_deployment": True,
                "changes": [],
                "change_summary": {
                    "total_changes": 0,
                    "by_category": {
                        "parameters": {"count": 0, "highest_risk_tier": None},
                        "capital_allocation": {"count": 0, "highest_risk_tier": None},
                        "bindings": {"count": 0, "highest_risk_tier": None},
                        "risk_controls": {"count": 0, "highest_risk_tier": None},
                        "stage_transition": {"count": 0, "highest_risk_tier": None},
                    },
                },
                "allowedActions": {
                    "canProceedToApproval": False,
                    "canEscalateDiff": True,
                },
            },
        },
        # LN-01: Lineage edges
        "lineage_edges": {
            "ln-edge-001": {
                "id": "ln-edge-001",
                "from_artifact_id": "artifact-041",
                "to_artifact_id": "artifact-042",
                "relationship": "derived_from",
                "created_at": "2026-04-09T00:00:00Z",
            },
            "ln-edge-002": {
                "id": "ln-edge-002",
                "from_artifact_id": "artifact-042",
                "to_artifact_id": "artifact-043",
                "relationship": "promoted_to",
                "created_at": "2026-04-10T00:00:00Z",
            },
        },
        "inspiration_graphs": {
            "artifact-042": {
                "artifact_id": "artifact-042",
                "inspiration_edges": [
                    {
                        "source_artifact_id": "artifact-041",
                        "relationship_type": "derived_from",
                        "influence_weight": 0.85,
                    },
                    {
                        "source_artifact_id": "artifact-039",
                        "relationship_type": "strategy_applied",
                        "influence_weight": 0.60,
                    },
                    {
                        "source_artifact_id": "artifact-038",
                        "relationship_type": "inspired_by",
                        "influence_weight": 0.40,
                    },
                ],
                "strategy_tags": [
                    "momentum-alpha",
                    "low-volatility",
                    "sector-rotation",
                ],
                "snapshot_at": "2026-04-19T03:00:00Z",
                "surface_state": "fresh",
            },
        },
        # ------------------------------------------------------------------ #
        # Consultation surfaces (CS-01 – CS-06)
        # ------------------------------------------------------------------ #

        # CS-01/CS-02/CS-03/CS-04/CS-05: SessionPersona records with
        # session_type = "consult" or "committee".
        # All fields are the canonical SessionPersona fields from
        # PERSONA_RUNTIME_MODEL.md §14, plus metadata.consultation.*
        # materialized by the Persona Plane.
        "consultation_sessions": {
            "cs-20260410-001": {
                "session_id": "cs-20260410-001",
                "persona_id": "persona-alpha",
                "session_type": "consult",
                "status": "terminated",
                "started_at": "2026-04-10T10:00:00Z",
                "ended_at": "2026-04-10T10:15:00Z",
                "capability_snapshot_id": "cap-001",
                "trace_id": "trace-cs-20260410-001",
                "request_id": "req-cs-20260410-001",
                "context_bundle_ref": "workspace://consultation-context/cs-20260410-001",
                "task_ref": None,
                "runtime_binding_id": None,
                "deployment_stage": None,
                "capital_pool_id": None,
                "metadata": {
                    "consultation": {
                        "consultation_type": "pre_deployment",
                        "requester_session_id": "cs-20260410-001",
                        "responder_session_ids": ["cs-resp-20260410-001"],
                        "committee_session_ids": [],
                        "consult_policy_ref": "cp-risk-analyst",
                        "trigger_rule": "pre_deployment_live",
                        "required_reviewers": 1,
                        "required_committees": [],
                        "forbidden_solo_actions": ["approve_live_deployment"],
                        "actual_reviewers": 1,
                        "outcome": "conditional",
                        "rationale_ref": "workspace://consultation-rationales/cs-20260410-001",
                        "evidence_refs": [
                            {
                                "id": "ev-001",
                                "type": "evidence_link",
                                "evidence_type": "telemetry",
                                "artifact_ref": "artifact-042",
                                "description": "30-day performance metrics",
                                "link": "/api/v1/telemetry/artifact-042/performance?time_range=30d",
                            },
                            {
                                "id": "ev-002",
                                "type": "evidence_link",
                                "evidence_type": "lineage",
                                "artifact_ref": "artifact-042",
                                "description": "Full lineage chain for artifact-042",
                                "link": "/api/v1/lineage?artifact_id=artifact-042",
                            },
                        ],
                        "escalation_path": None,
                    }
                },
            },
            "cs-resp-20260410-001": {
                "session_id": "cs-resp-20260410-001",
                "persona_id": "p-risk-analyst",
                "session_type": "consult",
                "status": "terminated",
                "started_at": "2026-04-10T10:00:30Z",
                "ended_at": "2026-04-10T10:14:00Z",
                "capability_snapshot_id": "cap-001",
                "trace_id": "trace-cs-resp-20260410-001",
                "request_id": "req-cs-resp-20260410-001",
                "context_bundle_ref": "workspace://consultation-context/cs-20260410-001",
                "task_ref": None,
                "runtime_binding_id": None,
                "deployment_stage": None,
                "capital_pool_id": None,
                "metadata": {
                    "consultation": {
                        "consultation_type": "pre_deployment",
                        "consult_policy_ref": "cp-risk-analyst",
                        "root_session_id": "cs-20260410-001",
                    }
                },
            },
            "cs-20260419-081": {
                "session_id": "cs-20260419-081",
                "persona_id": "persona-alpha",
                "session_type": "consult",
                "status": "active",
                "started_at": "2026-04-19T17:06:00Z",
                "ended_at": None,
                "capability_snapshot_id": "cap-001",
                "trace_id": "trace-cs-20260419-081",
                "request_id": "cr-20260419-014",
                "context_bundle_ref": "workspace://consultation-context/cs-20260419-081",
                "task_ref": None,
                "runtime_binding_id": "runtime-042",
                "deployment_stage": "paper",
                "capital_pool_id": "pool-main",
                "metadata": {
                    "consultation": {
                        "consultation_type": "risk_review",
                        "requester_session_id": "cs-20260419-081",
                        "responder_session_ids": [],
                        "committee_session_ids": [
                            "cm-20260419-081-001",
                            "cm-20260419-081-002",
                            "cm-20260419-081-003",
                        ],
                        "consult_policy_ref": "cp-alpha",
                        "trigger_rule": "macro_regime_shift",
                        "required_reviewers": 0,
                        "required_committees": ["committee-regime-risk"],
                        "forbidden_solo_actions": ["approve_live_deployment"],
                        "actual_reviewers": 0,
                        "outcome": "pending",
                        "rationale_ref": "workspace://consultation-rationales/cs-20260419-081",
                        "evidence_refs": [
                            {
                                "id": "telemetry-vol-spike-20260419",
                                "type": "evidence_link",
                                "evidence_type": "telemetry",
                                "artifact_ref": "artifact-042",
                                "description": "Volatility spike - 2026-04-19",
                                "link": "/telemetry/events/telemetry-vol-spike-20260419",
                            },
                            {
                                "id": "dp-20260419-014",
                                "type": "evidence_link",
                                "evidence_type": "deployment_plan",
                                "artifact_ref": "dp-20260419-014",
                                "description": "Deployment plan dp-20260419-014",
                                "link": "/deployments/plans/dp-20260419-014",
                            },
                        ],
                        "dissent_refs": [
                            "workspace://consultation-dissent/cs-20260419-081/execution-lead"
                        ],
                        "escalation_path": "committee_override",
                        "committee_ref": "committee-regime-risk-20260419-081",
                        "quorum_state": "quorum_met",
                        "consensus_state": "sponsor_required",
                        "committee_started_at": "2026-04-19T17:07:00Z",
                        "committee_surface_state": "ok",
                        "sponsor_session_id": "cm-20260419-081-003",
                        "sponsor_decision": None,
                        "sponsor_decided_at": None,
                        "sponsor_decided_by": None,
                        "escalation_reason": {
                            "trigger_rule": "macro_regime_shift",
                            "forbidden_solo_action": "approve_live_deployment",
                            "escalation_path": "committee_override",
                        },
                        "synthesis_summary": {
                            "outcome": "pending",
                            "rationale_ref": "workspace://consultation-rationales/cs-20260419-081",
                            "evidence_refs": [
                                "telemetry-vol-spike-20260419",
                                "dp-20260419-014",
                            ],
                            "dissent_refs": [
                                "workspace://consultation-dissent/cs-20260419-081/execution-lead"
                            ],
                        },
                    }
                },
            },
            "cm-20260419-081-001": {
                "session_id": "cm-20260419-081-001",
                "persona_id": "p-macro-observer",
                "session_type": "committee",
                "status": "active",
                "started_at": "2026-04-19T17:07:00Z",
                "ended_at": None,
                "capability_snapshot_id": "cap-001",
                "trace_id": "trace-cm-20260419-081-001",
                "request_id": "cr-20260419-014",
                "context_bundle_ref": "workspace://consultation-context/cs-20260419-081",
                "task_ref": None,
                "runtime_binding_id": "runtime-042",
                "deployment_stage": "paper",
                "capital_pool_id": "pool-main",
                "metadata": {
                    "consultation": {
                        "consultation_type": "risk_review",
                        "root_session_id": "cs-20260419-081",
                        "committee_ref": "committee-regime-risk-20260419-081",
                        "participant_status": "voted",
                        "outcome_signal": "approved",
                        "role": "committee_participant",
                        "rationale_ref": "workspace://consultation-rationales/cs-20260419-081/macro-observer",
                    }
                },
            },
            "cm-20260419-081-002": {
                "session_id": "cm-20260419-081-002",
                "persona_id": "p-execution-lead",
                "session_type": "committee",
                "status": "active",
                "started_at": "2026-04-19T17:07:30Z",
                "ended_at": None,
                "capability_snapshot_id": "cap-001",
                "trace_id": "trace-cm-20260419-081-002",
                "request_id": "cr-20260419-014",
                "context_bundle_ref": "workspace://consultation-context/cs-20260419-081",
                "task_ref": None,
                "runtime_binding_id": "runtime-042",
                "deployment_stage": "paper",
                "capital_pool_id": "pool-main",
                "metadata": {
                    "consultation": {
                        "consultation_type": "risk_review",
                        "root_session_id": "cs-20260419-081",
                        "committee_ref": "committee-regime-risk-20260419-081",
                        "participant_status": "voted",
                        "outcome_signal": "conditional",
                        "role": "committee_participant",
                        "rationale_ref": "workspace://consultation-dissent/cs-20260419-081/execution-lead",
                    }
                },
            },
            "cm-20260419-081-003": {
                "session_id": "cm-20260419-081-003",
                "persona_id": "p-compliance-sponsor",
                "session_type": "committee",
                "status": "active",
                "started_at": "2026-04-19T17:08:00Z",
                "ended_at": None,
                "capability_snapshot_id": "cap-001",
                "trace_id": "trace-cm-20260419-081-003",
                "request_id": "cr-20260419-014",
                "context_bundle_ref": "workspace://consultation-context/cs-20260419-081",
                "task_ref": None,
                "runtime_binding_id": "runtime-042",
                "deployment_stage": "paper",
                "capital_pool_id": "pool-main",
                "metadata": {
                    "consultation": {
                        "consultation_type": "risk_review",
                        "root_session_id": "cs-20260419-081",
                        "committee_ref": "committee-regime-risk-20260419-081",
                        "participant_status": "active",
                        "outcome_signal": None,
                        "role": "sponsor",
                        "rationale_ref": "workspace://consultation-rationales/cs-20260419-081/sponsor",
                    }
                },
            },
        },
        # CS-06: ConsultPolicy records keyed by persona_id
        "consult_policies": {
            "p-risk-analyst": {
                "id": "cp-risk-analyst",
                "persona_id": "p-risk-analyst",
                "required_reviewers": 1,
                "required_committees": [],
                "trigger_rules": [
                    {
                        "condition": "pre_deployment_live",
                        "description": "Risk analyst must review before any live deployment",
                    },
                ],
                "forbidden_solo_actions": ["approve_live_deployment"],
                "escalation_rules": [
                    {
                        "trigger": "responder_rejects",
                        "escalate_to": "governance_committee",
                    }
                ],
            },
            "persona-alpha": {
                "id": "cp-alpha",
                "persona_id": "persona-alpha",
                "required_reviewers": 1,
                "required_committees": [],
                "trigger_rules": [
                    {
                        "condition": "pre_deployment_live",
                        "description": "Must consult before any live deployment",
                    },
                    {
                        "condition": "macro_regime_shift",
                        "description": "Must consult when macro regime shift detected",
                    },
                ],
                "forbidden_solo_actions": [
                    "approve_live_deployment",
                    "increase_capital_allocation_above_20pct",
                ],
                "escalation_rules": [
                    {
                        "trigger": "responder_rejects",
                        "escalate_to": "governance_committee",
                    }
                ],
            },
        },
        # CW-02: Consult transcript records keyed by session_id
        "consult_transcripts": {
            "cs-20260419-081": {
                "transcript_id": "tr-cs-20260419-081",
                "session_id": "cs-20260419-081",
                "linked_request_id": "cr-20260419-014",
                "events": [
                    {
                        "transcript_id": "tr-cs-20260419-081",
                        "session_id": "cs-20260419-081",
                        "event_id": "evt-tr-cs-20260419-081-001",
                        "sequence_no": 1,
                        "parent_event_id": None,
                        "event_type": "message",
                        "event_time": "2026-04-19T17:06:30Z",
                        "ingest_time": "2026-04-19T17:06:31Z",
                        "actor": {
                            "actor_type": "persona",
                            "actor_id": "persona-alpha",
                            "display_name": "Alpha Trader",
                            "role": "requester",
                        },
                        "content": {
                            "format": "markdown",
                            "text": "Requesting risk review for macro regime shift scenario before approving live deployment.",
                        },
                        "evidence_refs": [],
                        "visibility": "committee",
                        "redaction": {
                            "is_redacted": False,
                            "reason": None,
                        },
                        "meta": {
                            "source": "consultation-service",
                            "hash": None,
                        },
                    },
                    {
                        "transcript_id": "tr-cs-20260419-081",
                        "session_id": "cs-20260419-081",
                        "event_id": "evt-tr-cs-20260419-081-002",
                        "sequence_no": 2,
                        "parent_event_id": "evt-tr-cs-20260419-081-001",
                        "event_type": "evidence_attachment",
                        "event_time": "2026-04-19T17:07:00Z",
                        "ingest_time": "2026-04-19T17:07:01Z",
                        "actor": {
                            "actor_type": "persona",
                            "actor_id": "p-macro-observer",
                            "display_name": "Macro Observer",
                            "role": "committee_participant",
                        },
                        "content": {
                            "format": "plaintext",
                            "text": None,
                        },
                        "evidence_refs": ["telemetry-vol-spike-20260419"],
                        "visibility": "committee",
                        "redaction": {
                            "is_redacted": False,
                            "reason": None,
                        },
                        "meta": {
                            "source": "consultation-service",
                            "hash": None,
                        },
                    },
                    {
                        "transcript_id": "tr-cs-20260419-081",
                        "session_id": "cs-20260419-081",
                        "event_id": "evt-tr-cs-20260419-081-003",
                        "sequence_no": 3,
                        "parent_event_id": "evt-tr-cs-20260419-081-001",
                        "event_type": "outcome_signal",
                        "event_time": "2026-04-19T17:08:30Z",
                        "ingest_time": "2026-04-19T17:08:31Z",
                        "actor": {
                            "actor_type": "persona",
                            "actor_id": "p-execution-lead",
                            "display_name": "Execution Lead",
                            "role": "committee_participant",
                        },
                        "content": {
                            "format": "markdown",
                            "text": "Conditional approval — deployment must reduce capital allocation by 20% pending regime confirmation.",
                        },
                        "evidence_refs": ["dp-20260419-014"],
                        "visibility": "committee",
                        "redaction": {
                            "is_redacted": False,
                            "reason": None,
                        },
                        "meta": {
                            "source": "consultation-service",
                            "hash": None,
                        },
                    },
                ],
            },
        },
        # CW-04: Red-team memo records keyed by memo_id
        "consult_memos": {
            "memo-rt-20260419-081": {
                "memo_id": "memo-rt-20260419-081",
                "memo_type": "red_team",
                "status": "published",
                "lifecycle_state": "published",
                "author_ref": "p-risk-analyst",
                "linked_request_id": "cr-20260419-014",
                "linked_session_id": "cs-20260419-081",
                "session_to_memo_mapping": {
                    "mapping_id": "map-20260419-081",
                    "source_session_id": "cs-20260419-081",
                    "transcript_id": "tr-cs-20260419-081",
                    "transcript_version": "v1",
                    "memo_id": "memo-rt-20260419-081",
                    "memo_type": "red_team",
                    "created_by": {
                        "actor_type": "persona",
                        "actor_id": "p-risk-analyst",
                    },
                    "evidence_refs": [
                        "telemetry-vol-spike-20260419",
                        "dp-20260419-014",
                    ],
                    "mapping_status": "active",
                    "created_at": "2026-04-19T17:18:00Z",
                },
                "summary": (
                    "Red-team memo flags volatility-guard gaps, rollback sequencing risk, "
                    "and missing governance handoff proof before paper-to-live promotion."
                ),
                "recommendations": [
                    "Raise the ATR-based circuit-breaker threshold before approving macro-regime deployment promotion.",
                    "Add an explicit volatility guard in the rebalance path instead of relying on degraded-mode persona behavior.",
                    "Run a rollback drill for position-limit breach scenarios before any live promotion decision.",
                ],
                "evidence_refs": [
                    {
                        "id": "telemetry-vol-spike-20260419",
                        "evidence_type": "telemetry",
                        "artifact_ref": "artifact-042",
                        "description": "Volatility spike - 2026-04-19",
                        "link": "/telemetry/events/telemetry-vol-spike-20260419",
                    },
                    {
                        "id": "dp-20260419-014",
                        "evidence_type": "deployment_plan",
                        "artifact_ref": "plan-F-042",
                        "description": "Deployment plan plan-F-042",
                        "link": "/deployments/plans/plan-F-042",
                    },
                ],
                "published_at": "2026-04-19T17:22:00Z",
                "created_at": "2026-04-19T17:15:00Z",
                "supersedes_memo_id": None,
                "superseded_by_memo_id": None,
                "surface_state": "ok",
                "governance_target": {
                    "target_type": "deployment_plan",
                    "target_id": "plan-F-042",
                    "deployment_plan_id": "plan-F-042",
                    "artifact_id": None,
                    "strategy_id": None,
                },
                "suppressed": False,
                "withdrawn": False,
                "active_governance_review_id": None,
            },
            "memo-rt-20260420-002": {
                "memo_id": "memo-rt-20260420-002",
                "memo_type": "red_team",
                "status": "draft",
                "lifecycle_state": "draft",
                "author_ref": "p-execution-lead",
                "linked_request_id": "cr-20260419-014",
                "linked_session_id": "cs-20260419-081",
                "session_to_memo_mapping": {
                    "mapping_id": "map-20260420-002",
                    "source_session_id": "cs-20260419-081",
                    "transcript_id": "tr-cs-20260419-081",
                    "transcript_version": "v2",
                    "memo_id": "memo-rt-20260420-002",
                    "memo_type": "red_team",
                    "created_by": {
                        "actor_type": "persona",
                        "actor_id": "p-execution-lead",
                    },
                    "evidence_refs": [
                        "telemetry-vol-spike-20260419",
                    ],
                    "mapping_status": "active",
                    "created_at": "2026-04-20T08:45:00Z",
                },
                "summary": "Draft follow-up memo awaiting rollback drill evidence before publication.",
                "recommendations": [
                    "Attach rollback-drill evidence before promoting this follow-up memo to published.",
                ],
                "evidence_refs": [
                    {
                        "id": "telemetry-vol-spike-20260419",
                        "evidence_type": "telemetry",
                        "artifact_ref": "artifact-042",
                        "description": "Volatility spike - 2026-04-19",
                        "link": "/telemetry/events/telemetry-vol-spike-20260419",
                    },
                ],
                "published_at": None,
                "created_at": "2026-04-20T08:40:00Z",
                "supersedes_memo_id": "memo-rt-20260419-081",
                "superseded_by_memo_id": None,
                "surface_state": "ok",
                "governance_target": {
                    "target_type": "artifact",
                    "target_id": "artifact-042",
                    "deployment_plan_id": None,
                    "artifact_id": "artifact-042",
                    "strategy_id": None,
                },
                "suppressed": False,
                "withdrawn": False,
                "active_governance_review_id": None,
            },
        },
        # TW-02: Trainer control state records keyed by session_id
        "trainer_controls": {
            "trn-20260419-001": {
                "session_id": "trn-20260419-001",
                "controls": [
                    {
                        "control_id": "ctrl-reversal-threshold",
                        "parameter_key": "reversal_threshold",
                        "display_label": "Reversal Threshold",
                        "control_type": "number",
                        "current_value": 0.55,
                        "allowed_range": {
                            "kind": "number",
                            "min": 0.1,
                            "max": 1.0,
                            "step": 0.05,
                        },
                        "unit": "score",
                        "last_modified_at": "2026-04-19T19:31:04Z",
                    },
                    {
                        "control_id": "ctrl-hold-bars",
                        "parameter_key": "minimum_hold_bars",
                        "display_label": "Minimum Hold Bars",
                        "control_type": "integer",
                        "current_value": 3,
                        "allowed_range": {
                            "kind": "integer",
                            "min": 1,
                            "max": 8,
                            "step": 1,
                        },
                        "unit": "bars",
                        "last_modified_at": "2026-04-19T19:31:04Z",
                    },
                ],
            },
        },
        # TL-03: Telemetry performance by artifact
        "telemetry_performance": {
            "artifact-042": {
                "artifact_id": "artifact-042",
                "artifact_version": "v2.1.0",
                "window": "24h",
                "data_points": [
                    {"timestamp": "2026-04-10T14:00:00Z", "pnl": -0.05, "drawdown": 0.06},
                    {"timestamp": "2026-04-10T15:00:00Z", "pnl": -0.12, "drawdown": 0.125},
                ],
                "summary": {
                    "total_pnl": -0.12,
                    "max_drawdown": 0.125,
                    "sharpe_ratio": -0.8,
                    "total_trades": 47,
                    "fill_rate": 0.94,
                    "avg_slippage_bps": 3.2,
                },
                "collected_at": "2026-04-10T15:00:00Z",
            },
        },
        "paper_live_drift_reports": {
            "runtime-042": {
                "runtime_id": "runtime-042",
                "artifact_id": "artifact-042",
                "artifact_version": "v2.1.0",
                "plan_id": "plan-F-042",
                "paper_baseline": {
                    "captured_at": "2026-04-09T16:00:00Z",
                    "deployment_stage": "paper",
                    "window": "24h",
                    "metrics": {
                        "pnl": -0.04,
                        "max_drawdown": 0.06,
                        "fill_rate": 0.97,
                        "avg_slippage_bps": 2.4,
                        "turnover": 0.32,
                    },
                },
                "observed_state": {
                    "deployment_stage": "live",
                    "runtime_status": "running",
                    "observed_at": "2026-04-10T15:00:00Z",
                    "metrics": {
                        "pnl": -0.12,
                        "drawdown": 0.125,
                        "fill_rate": 0.94,
                        "avg_slippage_bps": 3.2,
                        "turnover": 0.41,
                    },
                },
                "drift_groups": [
                    {
                        "group_id": "execution",
                        "label": "Execution",
                        "status": "watch",
                        "metrics": [
                            {
                                "metric_id": "fill_rate",
                                "label": "Fill rate",
                                "baseline_value": 0.97,
                                "observed_value": 0.94,
                                "delta": -0.03,
                                "threshold": ">= 0.95",
                                "status": "watch",
                                "unit": "ratio",
                            },
                            {
                                "metric_id": "turnover",
                                "label": "Turnover",
                                "baseline_value": 0.32,
                                "observed_value": 0.41,
                                "delta": 0.09,
                                "threshold": "<= 0.40",
                                "status": "watch",
                                "unit": "ratio",
                            },
                        ],
                    },
                    {
                        "group_id": "exposure",
                        "label": "Exposure",
                        "status": "breached",
                        "metrics": [
                            {
                                "metric_id": "max_drawdown",
                                "label": "Max drawdown",
                                "baseline_value": 0.06,
                                "observed_value": 0.125,
                                "delta": 0.065,
                                "threshold": "<= 0.08",
                                "status": "breached",
                                "unit": "ratio",
                            },
                        ],
                    },
                    {
                        "group_id": "slippage",
                        "label": "Slippage",
                        "status": "breached",
                        "metrics": [
                            {
                                "metric_id": "avg_slippage_bps",
                                "label": "Average slippage",
                                "baseline_value": 2.4,
                                "observed_value": 3.2,
                                "delta": 0.8,
                                "threshold": "<= 3.0",
                                "status": "breached",
                                "unit": "bps",
                            },
                        ],
                    },
                    {
                        "group_id": "risk_signals",
                        "label": "Risk Signals",
                        "status": "breached",
                        "metrics": [
                            {
                                "metric_id": "active_incident_count",
                                "label": "Active incident count",
                                "baseline_value": 0,
                                "observed_value": 1,
                                "delta": 1,
                                "threshold": "== 0",
                                "status": "breached",
                                "unit": "count",
                            },
                            {
                                "metric_id": "safe_mode_active",
                                "label": "Safe mode active",
                                "baseline_value": 0,
                                "observed_value": 1,
                                "delta": 1,
                                "threshold": "== 0",
                                "status": "breached",
                                "unit": "flag",
                            },
                        ],
                    },
                ],
                "threshold_evaluation": {
                    "overall_status": "breached",
                    "summary": "Observed live metrics exceed the published paper baseline envelope; operator review is required.",
                    "breached_metric_ids": [
                        "max_drawdown",
                        "avg_slippage_bps",
                        "active_incident_count",
                        "safe_mode_active",
                    ],
                },
                "evidence_refs": [
                    {
                        "ref_id": "approval-042",
                        "type": "ApprovalDecision",
                        "href": "/api/v1/approval-decisions/approval-042",
                    },
                    {
                        "ref_id": "inc-20260410-001",
                        "type": "IncidentCase",
                        "href": "/api/v1/operator/incident-response/inc-20260410-001",
                    },
                    {
                        "ref_id": "evo-dec-001",
                        "type": "EvolutionDecision",
                        "href": "/api/v1/evolution-decisions/evo-dec-001",
                    },
                    {
                        "ref_id": "drift-report-runtime-042",
                        "type": "drift_report",
                        "href": None,
                    },
                ],
                "recommended_actions": [
                    {
                        "action_id": "open-deployment-review",
                        "label": "Open deployment review",
                        "reason": "Re-verify promotion assumptions against observed live drift.",
                        "target_ref": {
                            "surface_id": "PKT-001",
                            "label": "Open deployment review",
                            "href": "/api/v1/operator/deployment-review/plan-F-042",
                            "target_id": "plan-F-042",
                        },
                    },
                    {
                        "action_id": "open-incident-response",
                        "label": "Open incident response",
                        "reason": "The active incident already tracks the drawdown breach.",
                        "target_ref": {
                            "surface_id": "PKT-002",
                            "label": "Open incident response",
                            "href": "/api/v1/operator/incident-response/inc-20260410-001",
                            "target_id": "inc-20260410-001",
                        },
                    },
                    {
                        "action_id": "open-post-incident-review",
                        "label": "Open post-incident review",
                        "reason": "Review evolution follow-up and drift evidence before another promotion step.",
                        "target_ref": {
                            "surface_id": "PKT-003",
                            "label": "Open post-incident review",
                            "href": "/api/v1/operator/post-incident-review/inc-20260410-001",
                            "target_id": "inc-20260410-001",
                        },
                    },
                ],
            }
        },
        "research_tickets": {
            "tkt-7a8b9c0d-1234-5678-abcd-ef0123456789": {
                "ticket_id": "tkt-7a8b9c0d-1234-5678-abcd-ef0123456789",
                "title": "RW-Ticket: MOM-v3 slippage investigation (Apr 14)",
                "description": "Ticket aligned with the KW-02 contract example payload.",
                "status": "closed",
                "priority": "high",
                "owner": "op-001",
                "created_at": "2026-04-14T10:30:00Z",
                "updated_at": "2026-04-16T14:22:00Z",
                "closed_at": "2026-04-16T13:55:00Z",
                "archived_at": None,
                "lifecycle_history": [
                    {
                        "from_status": None,
                        "to_status": "open",
                        "transitioned_at": "2026-04-14T10:30:00Z",
                        "transitioned_by": "op-001",
                    },
                    {
                        "from_status": "open",
                        "to_status": "closed",
                        "transitioned_at": "2026-04-16T13:55:00Z",
                        "transitioned_by": "op-001",
                    },
                ],
                "linked_experiments": [],
                "linked_artifacts": [],
            },
            "rt-20260419-007": {
                "ticket_id": "rt-20260419-007",
                "title": "Evaluate momentum factor decay in high-volatility regime",
                "description": (
                    "Assess whether momentum factors lose predictive power during sustained "
                    "volatility spikes and whether the current strategy's rebalancing windows "
                    "account for this regime shift."
                ),
                "status": "in_progress",
                "priority": "high",
                "owner": "persona-risk-chief",
                "created_at": "2026-04-19T17:10:00Z",
                "updated_at": "2026-04-19T18:30:00Z",
                "closed_at": None,
                "archived_at": None,
                "lifecycle_history": [
                    {
                        "from_status": None,
                        "to_status": "open",
                        "transitioned_at": "2026-04-19T17:10:00Z",
                        "transitioned_by": "persona-risk-chief",
                    },
                    {
                        "from_status": "open",
                        "to_status": "in_progress",
                        "transitioned_at": "2026-04-19T18:30:00Z",
                        "transitioned_by": "persona-risk-chief",
                    },
                ],
                "linked_experiments": ["exp-20260419-012"],
                "linked_artifacts": [],
            },
            "rt-20260418-003": {
                "ticket_id": "rt-20260418-003",
                "title": "Benchmark drawdown recovery across three strategy variants",
                "description": (
                    "Compare recovery time and equity curve shape for the baseline, conservative, "
                    "and accelerated variants after drawdown shocks."
                ),
                "status": "open",
                "priority": "normal",
                "owner": "persona-execution-sponsor",
                "created_at": "2026-04-18T09:00:00Z",
                "updated_at": "2026-04-18T09:00:00Z",
                "closed_at": None,
                "archived_at": None,
                "lifecycle_history": [
                    {
                        "from_status": None,
                        "to_status": "open",
                        "transitioned_at": "2026-04-18T09:00:00Z",
                        "transitioned_by": "persona-execution-sponsor",
                    }
                ],
                "linked_experiments": [],
                "linked_artifacts": [],
            },
            "rt-20260415-001": {
                "ticket_id": "rt-20260415-001",
                "title": "Validate signal quality on macro event windows",
                "description": (
                    "Check whether signal quality deteriorates around scheduled macro events and "
                    "whether the current exclusion windows are sufficient."
                ),
                "status": "closed",
                "priority": "low",
                "owner": "persona-risk-chief",
                "created_at": "2026-04-15T14:20:00Z",
                "updated_at": "2026-04-19T11:00:00Z",
                "closed_at": "2026-04-19T11:00:00Z",
                "archived_at": None,
                "lifecycle_history": [
                    {
                        "from_status": None,
                        "to_status": "open",
                        "transitioned_at": "2026-04-15T14:20:00Z",
                        "transitioned_by": "persona-risk-chief",
                    },
                    {
                        "from_status": "open",
                        "to_status": "in_progress",
                        "transitioned_at": "2026-04-17T09:15:00Z",
                        "transitioned_by": "persona-risk-chief",
                    },
                    {
                        "from_status": "in_progress",
                        "to_status": "closed",
                        "transitioned_at": "2026-04-19T11:00:00Z",
                        "transitioned_by": "persona-risk-chief",
                    },
                ],
                "linked_experiments": ["exp-20260417-004"],
                "linked_artifacts": ["artifact-20260417-004-a"],
            },
        },
        "institutional_memory_entries": {
            "mem-7f2a1b9c-4d5e-4f6a-8b0c-9d1e2f3a4b5c": {
                "entry_id": "mem-7f2a1b9c-4d5e-4f6a-8b0c-9d1e2f3a4b5c",
                "knowledge_type": "incident_lesson",
                "content": {
                    "headline": "Risk exposure exceeded thresholds during BTC-regime shift due to stale parameter sync",
                    "body": (
                        "Detailed investigation into the incident on 2026-04-05 revealed that the latency in "
                        "parameter propagation from the Research Plane to the Execution Plane allowed the persona "
                        "'Alpha-Momentum' to maintain high exposure even as market volatility spiked. The lesson "
                        "learned is that parameter staleness signals must trigger an immediate safety halt or "
                        "aggressive de-risking if the drift exceeds 300ms during high-volatility regimes."
                    ),
                    "structured_payload": {
                        "affected_personas": ["Alpha-Momentum", "Beta-Arbitrage"],
                        "drift_observed_ms": 450,
                        "max_exposure_violation_pct": 12.5,
                    },
                    "tags": ["risk", "latency", "regime-shift", "btc"],
                },
                "source_event": {
                    "type": "postmortem_published",
                    "id": "pm-2026-04-05-btc-drift",
                    "href": "/operator/post-incident-review?incident=inc-2026-04-05-001",
                },
                "contributing_persona_ids": ["Alpha-Momentum"],
                "written_at": "2026-04-07T14:30:00Z",
                "write_authority": "incident-svc",
                "scope": {"type": "system_wide", "filter": None},
                "lifecycle": {"status": "active", "superseded_by": None},
                "usage": {"reuse_count": 28, "last_cited_at": "2026-04-19T08:45:00Z"},
            },
            "mem-2c3d4e5f-6a7b-8c9d-0e1f-2a3b4c5d6e7f": {
                "entry_id": "mem-2c3d4e5f-6a7b-8c9d-0e1f-2a3b4c5d6e7f",
                "knowledge_type": "regime_pattern",
                "content": {
                    "headline": "Momentum strategy underperforms in low-volatility sideways regimes",
                    "body": (
                        "Cross-regime replay analysis shows the momentum stack loses edge when volatility compresses "
                        "and breadth confirmation decays. Future deployments should widen confirmation thresholds "
                        "or reduce exposure scaling in sideways regimes."
                    ),
                    "structured_payload": {
                        "affected_strategy_family": "momentum",
                        "observed_regime": "low_vol_sideways",
                        "underperformance_window_days": 17,
                    },
                    "tags": ["regime", "momentum", "volatility"],
                },
                "source_event": {
                    "type": "evolution_decision_published",
                    "id": "evo-2026-04-10-regime-001",
                    "href": "/evolution/mutation-review/evo-2026-04-10-regime-001",
                },
                "contributing_persona_ids": ["persona-alpha", "p-macro-observer"],
                "written_at": "2026-04-10T09:15:00Z",
                "write_authority": "evolution-svc",
                "scope": {"type": "strategy_family", "filter": "momentum"},
                "lifecycle": {"status": "active", "superseded_by": None},
                "usage": {"reuse_count": 14, "last_cited_at": "2026-04-18T14:20:00Z"},
            },
            "mem-8e9f0a1b-2c3d-4e5f-6a7b-8c9d0e1f2a3b": {
                "entry_id": "mem-8e9f0a1b-2c3d-4e5f-6a7b-8c9d0e1f2a3b",
                "knowledge_type": "policy_precedent",
                "content": {
                    "headline": "Simultaneous drawdown across correlated instruments triggers portfolio-level halt",
                    "body": (
                        "This precedent was archived after newer capital-pool controls replaced the original halt "
                        "policy, but the older rationale remains relevant for historical incident interpretation."
                    ),
                    "structured_payload": {
                        "halt_threshold_pct": 9.0,
                        "correlation_bucket": "high_beta_cluster",
                    },
                    "tags": ["policy", "drawdown", "halt", "correlation"],
                },
                "source_event": {
                    "type": "incident_policy_archived",
                    "id": "policy-2026-03-28-portfolio-halt",
                    "href": "/operator/post-incident-review?incident=inc-2026-03-28-002",
                },
                "contributing_persona_ids": ["p-risk-analyst", "p-compliance-sponsor"],
                "written_at": "2026-03-28T16:00:00Z",
                "write_authority": "incident-svc",
                "scope": {"type": "system_wide", "filter": None},
                "lifecycle": {
                    "status": "superseded",
                    "superseded_by": "mem-7f2a1b9c-4d5e-4f6a-8b0c-9d1e2f3a4b5c",
                },
                "usage": {"reuse_count": 41, "last_cited_at": "2026-04-11T10:00:00Z"},
            },
            "mem-e5f6a7b8-c9d0-1234-efab-234567890123": {
                "entry_id": "mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                "knowledge_type": "regime_pattern",
                "content": {
                    "headline": "High-volatility momentum slippage - pattern observed Q1 2026",
                    "body": "Reference pattern used by KW-02 contract fallback data.",
                    "structured_payload": {"confidence": "medium"},
                    "tags": ["momentum", "slippage", "regime"],
                },
                "source_event": {
                    "type": "research_ticket_closed",
                    "id": "tkt-7a8b9c0d-1234-5678-abcd-ef0123456789",
                    "href": "/research/tickets/tkt-7a8b9c0d-1234-5678-abcd-ef0123456789",
                },
                "contributing_persona_ids": ["persona-HAWK-001"],
                "written_at": "2026-04-15T12:00:00Z",
                "write_authority": "research-svc",
                "scope": {"type": "strategy_family", "filter": "momentum"},
                "lifecycle": {"status": "active", "superseded_by": None},
                "usage": {"reuse_count": 3, "last_cited_at": "2026-04-17T09:05:00Z"},
            },
        },
        "strategy_specs": {
            "strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a": {
                "strategy_id": "strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a",
                "current_spec_version_id": "specver-0a1b2c3d-0003-0003-0003-000000000003",
                "title": "Momentum Regime Response",
                "source_kind": "paper",
                "persona_ids": ["persona-HAWK-001"],
                "updated_at": "2026-04-18T09:00:00Z",
                "versions": [
                    {
                        "spec_version_id": "specver-0a1b2c3d-0001-0001-0001-000000000001",
                        "spec_version": "v1",
                        "lifecycle_state": "retired",
                        "title": "Momentum Regime Response v1",
                        "hypothesis": "Baseline momentum response loses edge when the volatility regime breaks.",
                        "objective": "Establish the baseline regime-aware momentum behavior.",
                        "market_scope": {
                            "symbols": ["ES", "NQ"],
                            "frequency": "daily",
                            "asset_classes": ["futures"],
                            "venues": ["CME"],
                        },
                        "execution_profile": {
                            "signal_schema_version": "1.0",
                            "quantity_type": "PERCENT_PORTFOLIO",
                            "rebalance_cadence": "daily",
                            "execution_mode_hint": "research",
                        },
                        "evaluation_plan": {
                            "metrics": ["sharpe_ratio", "max_drawdown"],
                            "candidate_gate": "Sharpe >= 0.7 over 90d replay",
                            "paper_gate": "Sharpe >= 0.9 over 30d paper run",
                            "live_gate": "Sharpe >= 1.1 over 60d paper run",
                        },
                        "governance": {
                            "approval_required": True,
                            "policy_id": "gov-policy-momentum-001",
                            "risk_profile": "medium",
                        },
                        "citation_bundle": {
                            "evidence_refs": [
                                {
                                    "ref_id": "evref-b2c3d4e5-f6a7-8901-bcde-f12345678901",
                                    "source_document_title": "Volatility Regime Analysis Q1 2026",
                                    "link_type": "citation",
                                    "credibility_tier": "secondary",
                                    "association": "background",
                                    "resolved_link": {
                                        "availability": "external",
                                        "route_href": "https://arxiv.org/abs/2026.12345",
                                        "display_label": "Open external paper",
                                        "open_in_new_tab": True,
                                    },
                                }
                            ],
                            "memory_anchors": [],
                            "insight_citations": [],
                        },
                        "parent_spec_version_id": None,
                        "derived_from_source_refs": ["paper-jegadeesh-titman-1993"],
                        "created_at": "2026-03-01T10:00:00Z",
                        "created_by": "Operator: Alice Chen",
                    },
                    {
                        "spec_version_id": "specver-0a1b2c3d-0002-0002-0002-000000000002",
                        "spec_version": "v2",
                        "lifecycle_state": "candidate",
                        "title": "Momentum Regime Response v2",
                        "hypothesis": "A shorter decay half-life should recover faster after volatility breaks.",
                        "objective": "Reduce post-break decay lag while preserving regime awareness.",
                        "market_scope": {
                            "symbols": ["ES", "NQ"],
                            "frequency": "daily",
                            "asset_classes": ["futures"],
                            "venues": ["CME"],
                        },
                        "execution_profile": {
                            "signal_schema_version": "1.1",
                            "quantity_type": "PERCENT_PORTFOLIO",
                            "rebalance_cadence": "daily",
                            "execution_mode_hint": "paper",
                        },
                        "evaluation_plan": {
                            "metrics": ["sharpe_ratio", "max_drawdown"],
                            "candidate_gate": "Sharpe >= 0.8 over 90d replay",
                            "paper_gate": "Sharpe >= 1.0 over 30d paper run",
                            "live_gate": "Sharpe >= 1.15 over 60d paper run",
                        },
                        "governance": {
                            "approval_required": True,
                            "policy_id": "gov-policy-momentum-001",
                            "risk_profile": "medium",
                        },
                        "citation_bundle": {
                            "evidence_refs": [
                                {
                                    "ref_id": "evref-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                                    "source_document_title": "Post-Incident Review: Flash Spike 2026-03-14",
                                    "link_type": "supporting_evidence",
                                    "credibility_tier": "primary",
                                    "association": "evaluation",
                                    "resolved_link": {
                                        "availability": "available",
                                        "route_href": "/knowledge/evidence/evref-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                                        "display_label": "View evidence reference",
                                        "open_in_new_tab": False,
                                    },
                                }
                            ],
                            "memory_anchors": [
                                {
                                    "entry_id": "mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                                    "knowledge_type": "regime_pattern",
                                    "content_headline": "High-volatility momentum slippage - pattern observed Q1 2026",
                                    "route_href": "/knowledge/memory/mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                                }
                            ],
                            "insight_citations": [],
                        },
                        "parent_spec_version_id": "specver-0a1b2c3d-0001-0001-0001-000000000001",
                        "derived_from_source_refs": [
                            "paper-jegadeesh-titman-1993",
                            "note-momentum-research-2026-q1",
                        ],
                        "created_at": "2026-04-02T09:30:00Z",
                        "created_by": "Persona: Momentum-alpha",
                    },
                    {
                        "spec_version_id": "specver-0a1b2c3d-0003-0003-0003-000000000003",
                        "spec_version": "v3",
                        "lifecycle_state": "approved",
                        "title": "Momentum Regime Response v3",
                        "hypothesis": "Shorter decay and stricter paper gates improve post-break recovery without destabilizing signal selection.",
                        "objective": "Promote the volatility-aware momentum response into governed paper-ready shape.",
                        "market_scope": {
                            "symbols": ["ES", "NQ"],
                            "frequency": "daily",
                            "asset_classes": ["futures"],
                            "venues": ["CME"],
                        },
                        "execution_profile": {
                            "signal_schema_version": "1.2",
                            "quantity_type": "PERCENT_PORTFOLIO",
                            "rebalance_cadence": "daily",
                            "execution_mode_hint": "paper",
                        },
                        "evaluation_plan": {
                            "metrics": ["sharpe_ratio", "max_drawdown"],
                            "candidate_gate": "Sharpe >= 0.85 over 90d replay",
                            "paper_gate": "Sharpe >= 1.05 over 30d paper run",
                            "live_gate": "Sharpe >= 1.2 over 60d paper run",
                        },
                        "governance": {
                            "approval_required": True,
                            "policy_id": "gov-policy-momentum-001",
                            "risk_profile": "medium",
                        },
                        "citation_bundle": {
                            "evidence_refs": [
                                {
                                    "ref_id": "evref-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                                    "source_document_title": "Post-Incident Review: Flash Spike 2026-03-14",
                                    "link_type": "supporting_evidence",
                                    "credibility_tier": "primary",
                                    "association": "evaluation",
                                    "resolved_link": {
                                        "availability": "available",
                                        "route_href": "/knowledge/evidence/evref-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                                        "display_label": "View evidence reference",
                                        "open_in_new_tab": False,
                                    },
                                },
                                {
                                    "ref_id": "evref-b2c3d4e5-f6a7-8901-bcde-f12345678901",
                                    "source_document_title": "Volatility Regime Analysis Q1 2026",
                                    "link_type": "citation",
                                    "credibility_tier": "secondary",
                                    "association": "background",
                                    "resolved_link": {
                                        "availability": "external",
                                        "route_href": "https://arxiv.org/abs/2026.12345",
                                        "display_label": "Open external paper",
                                        "open_in_new_tab": True,
                                    },
                                },
                            ],
                            "memory_anchors": [
                                {
                                    "entry_id": "mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                                    "knowledge_type": "regime_pattern",
                                    "content_headline": "High-volatility momentum slippage - pattern observed Q1 2026",
                                    "route_href": "/knowledge/memory/mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                                }
                            ],
                            "insight_citations": [
                                {
                                    "insight_id": "ins-7a3f2c91-e4b8-4d12-9f65-0c8e1a234567",
                                    "summary": "Momentum decay intensifies after the regime break and remains sensitive to rebalance cadence.",
                                    "route_href": "/knowledge/insights/ins-7a3f2c91-e4b8-4d12-9f65-0c8e1a234567",
                                }
                            ],
                        },
                        "parent_spec_version_id": "specver-0a1b2c3d-0002-0002-0002-000000000002",
                        "derived_from_source_refs": [
                            "paper-jegadeesh-titman-1993",
                            "note-momentum-research-2026-q1",
                            "analysis-20260419-007-a",
                        ],
                        "created_at": "2026-04-18T09:00:00Z",
                        "created_by": "Operator: Alice Chen",
                    },
                ],
            },
        },
        "research_analyses": {
            "analysis-20260419-007-a": {
                "analysis_id": "analysis-20260419-007-a",
                "ticket_id": "rt-20260419-007",
                "experiment_id": "exp-20260419-012",
                "status": "completed",
                "run_at": "2026-04-19T20:40:00Z",
                "completed_at": "2026-04-19T20:43:00Z",
                "summary": {
                    "headline": "Volatility-cluster replay confirms faster momentum decay after the regime break.",
                    "narrative": (
                        "The shorter half-life configuration recovered more quickly after the March volatility "
                        "cluster while preserving enough signal selectivity to remain actionable."
                    ),
                    "verdict": "candidate_update",
                    "next_question": (
                        "Validate whether the shorter decay setting remains stable outside stress weeks before "
                        "promoting it into launch defaults."
                    ),
                },
                "metric_groups": [
                    {
                        "group_key": "performance",
                        "label": "Performance",
                        "description": "Return and efficiency metrics for the replay window.",
                        "metrics": [
                            {
                                "metric_key": "annualized_return",
                                "label": "Annualized return",
                                "value": 0.182,
                                "unit": "ratio",
                                "display_value": "18.2%",
                                "direction": "higher_is_better",
                                "baseline_value": 0.161,
                                "delta_value": 0.021,
                                "delta_display": "+2.1 pts",
                            },
                            {
                                "metric_key": "information_ratio",
                                "label": "Information ratio",
                                "value": 1.34,
                                "unit": "score",
                                "display_value": "1.34",
                                "direction": "higher_is_better",
                                "baseline_value": 1.19,
                                "delta_value": 0.15,
                                "delta_display": "+0.15",
                            },
                        ],
                    },
                    {
                        "group_key": "drawdown",
                        "label": "Drawdown",
                        "description": "Depth and recovery behavior during stressed windows.",
                        "metrics": [
                            {
                                "metric_key": "max_drawdown",
                                "label": "Max drawdown",
                                "value": 0.072,
                                "unit": "ratio",
                                "display_value": "7.2%",
                                "direction": "lower_is_better",
                                "baseline_value": 0.086,
                                "delta_value": -0.014,
                                "delta_display": "-1.4 pts",
                            },
                            {
                                "metric_key": "recovery_days",
                                "label": "Recovery days",
                                "value": 9,
                                "unit": "days",
                                "display_value": "9 days",
                                "direction": "lower_is_better",
                                "baseline_value": 12,
                                "delta_value": -3,
                                "delta_display": "-3 days",
                            },
                        ],
                    },
                    {
                        "group_key": "signal_quality",
                        "label": "Signal quality",
                        "description": "Stability and usefulness of the signal under the analyzed regime.",
                        "metrics": [
                            {
                                "metric_key": "signal_half_life_days",
                                "label": "Signal half-life",
                                "value": 3.8,
                                "unit": "days",
                                "display_value": "3.8 days",
                                "direction": "contextual",
                                "baseline_value": 5.2,
                                "delta_value": -1.4,
                                "delta_display": "-1.4 days",
                            },
                            {
                                "metric_key": "hit_rate",
                                "label": "Hit rate",
                                "value": 0.57,
                                "unit": "ratio",
                                "display_value": "57.0%",
                                "direction": "higher_is_better",
                                "baseline_value": 0.54,
                                "delta_value": 0.03,
                                "delta_display": "+3.0 pts",
                            },
                        ],
                    },
                ],
                "comparative_summary": {
                    "basis": "Most recent completed runs for the same ticket over the last 30 days.",
                    "baseline_analysis_id": "analysis-20260418-004-b",
                    "focus_metrics": [
                        "annualized_return",
                        "max_drawdown",
                        "signal_half_life_days",
                    ],
                    "comparisons": [
                        {
                            "analysis_id": "analysis-20260418-004-b",
                            "label": "Previous completed replay",
                            "status": "completed",
                            "run_at": "2026-04-18T16:15:00Z",
                            "delta_highlights": [
                                {
                                    "metric_key": "annualized_return",
                                    "change_label": "Higher return",
                                    "direction": "improved",
                                    "delta_display": "+2.1 pts",
                                    "interpretation": "Return improved while holding risk lower than the previous replay.",
                                },
                                {
                                    "metric_key": "max_drawdown",
                                    "change_label": "Lower drawdown",
                                    "direction": "improved",
                                    "delta_display": "-1.4 pts",
                                    "interpretation": "The updated decay setting reduced the worst loss depth in the stress window.",
                                },
                            ],
                        }
                    ],
                },
            },
            "analysis-20260418-004-b": {
                "analysis_id": "analysis-20260418-004-b",
                "ticket_id": "rt-20260419-007",
                "experiment_id": "exp-20260418-009",
                "status": "completed",
                "run_at": "2026-04-18T16:15:00Z",
                "completed_at": "2026-04-18T16:18:00Z",
                "summary": {
                    "headline": "Earlier replay showed decay pressure but with weaker drawdown improvement.",
                    "narrative": "The first replay suggested the regime-break hypothesis was real, but the improvement was not yet strong enough to justify a parameter change.",
                    "verdict": "observe",
                    "next_question": "Retest with a shorter decay half-life and compare recovery speed during the same volatility cluster.",
                },
                "metric_groups": [
                    {
                        "group_key": "performance",
                        "label": "Performance",
                        "description": "Return and efficiency metrics for the replay window.",
                        "metrics": [
                            {
                                "metric_key": "annualized_return",
                                "label": "Annualized return",
                                "value": 0.161,
                                "unit": "ratio",
                                "display_value": "16.1%",
                                "direction": "higher_is_better",
                                "baseline_value": None,
                                "delta_value": None,
                                "delta_display": None,
                            }
                        ],
                    },
                    {
                        "group_key": "drawdown",
                        "label": "Drawdown",
                        "description": "Depth and recovery behavior during stressed windows.",
                        "metrics": [
                            {
                                "metric_key": "max_drawdown",
                                "label": "Max drawdown",
                                "value": 0.086,
                                "unit": "ratio",
                                "display_value": "8.6%",
                                "direction": "lower_is_better",
                                "baseline_value": None,
                                "delta_value": None,
                                "delta_display": None,
                            }
                        ],
                    },
                    {
                        "group_key": "signal_quality",
                        "label": "Signal quality",
                        "description": "Stability and usefulness of the signal under the analyzed regime.",
                        "metrics": [
                            {
                                "metric_key": "signal_half_life_days",
                                "label": "Signal half-life",
                                "value": 5.2,
                                "unit": "days",
                                "display_value": "5.2 days",
                                "direction": "contextual",
                                "baseline_value": None,
                                "delta_value": None,
                                "delta_display": None,
                            }
                        ],
                    },
                ],
                "comparative_summary": {
                    "basis": "No earlier completed runs available for this ticket at the time of analysis.",
                    "baseline_analysis_id": "analysis-20260418-004-b",
                    "focus_metrics": [
                        "annualized_return",
                        "max_drawdown",
                        "signal_half_life_days",
                    ],
                    "comparisons": [],
                },
            },
            "analysis-20260417-001-c": {
                "analysis_id": "analysis-20260417-001-c",
                "ticket_id": "rt-20260415-001",
                "experiment_id": "exp-20260417-004",
                "status": "failed",
                "run_at": "2026-04-17T12:05:00Z",
                "completed_at": "2026-04-17T12:07:00Z",
                "summary": {
                    "headline": "Macro-event replay failed before final aggregation completed.",
                    "narrative": "The replay produced incomplete signal windows because one macro calendar partition was missing from the source snapshot.",
                    "verdict": "retry_required",
                    "next_question": "Repair the macro calendar partition and rerun aggregation before drawing conclusions.",
                },
                "metric_groups": [
                    {
                        "group_key": "pipeline_health",
                        "label": "Pipeline health",
                        "description": "Execution and aggregation completeness for the failed run.",
                        "metrics": [
                            {
                                "metric_key": "completed_windows_pct",
                                "label": "Completed windows",
                                "value": 0.62,
                                "unit": "ratio",
                                "display_value": "62.0%",
                                "direction": "higher_is_better",
                                "baseline_value": None,
                                "delta_value": None,
                                "delta_display": None,
                            }
                        ],
                    }
                ],
                "comparative_summary": {
                    "basis": "Comparison unavailable because the run did not complete.",
                    "baseline_analysis_id": "analysis-20260417-001-c",
                    "focus_metrics": [],
                    "comparisons": [],
                },
            },
        },
        "research_experiments": {
            "exp-20260419-012": {
                "experiment_id": "exp-20260419-012",
                "ticket_id": "rt-20260419-007",
                "experiment_name": "Momentum decay replay on March volatility cluster",
                "status": "completed",
                "queued_at": "2026-04-19T19:00:00Z",
                "started_at": "2026-04-19T19:03:00Z",
                "completed_at": "2026-04-19T20:15:00Z",
                "progress": {"percent": 100, "phase": "aggregation", "message": "Aggregation complete."},
                "strategy_selector": {"strategy_id": "strat-momentum-v4", "variant_id": "var-short-halflife"},
                "parameter_set": {"half_life_days": 5, "rebalance_window": "2d", "signal_threshold": 0.62},
                "run_config": {
                    "dataset_ref": "equities-us-2026Q1",
                    "time_range": {"start_at": "2026-03-01T00:00:00Z", "end_at": "2026-03-31T23:59:59Z"},
                    "execution_mode": "backtest",
                    "priority": "high",
                    "requested_by": "persona-risk-chief",
                },
                "launch_context": {"analysis_refs": ["analysis-20260418-004-b"]},
                "validation_warnings": [],
                "artifact_ids": ["artifact-20260418-005", "artifact-20260419-014"],
                "failure": {"reason_code": None, "message": None},
                "allowedActions": {"canCancel": False},
            },
            "exp-20260418-009": {
                "experiment_id": "exp-20260418-009",
                "ticket_id": "rt-20260419-007",
                "experiment_name": "Momentum decay baseline — short lookback variant",
                "status": "running",
                "queued_at": "2026-04-18T14:00:00Z",
                "started_at": "2026-04-18T14:05:00Z",
                "completed_at": None,
                "progress": {"percent": 62, "phase": "signal_aggregation", "message": "Aggregating signal windows…"},
                "strategy_selector": {"strategy_id": "strat-momentum-v4", "variant_id": "var-baseline"},
                "parameter_set": {"half_life_days": 10, "rebalance_window": "3d", "signal_threshold": 0.58},
                "run_config": {
                    "dataset_ref": "equities-us-2026Q1",
                    "time_range": {"start_at": "2026-02-01T00:00:00Z", "end_at": "2026-04-17T23:59:59Z"},
                    "execution_mode": "backtest",
                    "priority": "normal",
                    "requested_by": "persona-risk-chief",
                },
                "launch_context": {"analysis_refs": None},
                "validation_warnings": [
                    {"code": "WIDE_DATE_RANGE", "message": "Date range exceeds 60 days; runtime may be elevated."}
                ],
                "artifact_ids": [],
                "failure": {"reason_code": None, "message": None},
                "allowedActions": {"canCancel": True},
            },
            "exp-20260417-004": {
                "experiment_id": "exp-20260417-004",
                "ticket_id": "rt-20260415-001",
                "experiment_name": "Macro event signal quality — exclusion window test",
                "status": "failed",
                "queued_at": "2026-04-17T12:00:00Z",
                "started_at": "2026-04-17T12:02:00Z",
                "completed_at": "2026-04-17T12:07:00Z",
                "progress": {"percent": None, "phase": None, "message": None},
                "strategy_selector": {"strategy_id": "strat-macro-event-v2", "variant_id": None},
                "parameter_set": {"exclusion_window_hrs": 4, "signal_min_quality": 0.70},
                "run_config": {
                    "dataset_ref": "equities-us-2026Q1",
                    "time_range": {"start_at": "2026-01-01T00:00:00Z", "end_at": "2026-04-16T23:59:59Z"},
                    "execution_mode": "simulation",
                    "priority": "normal",
                    "requested_by": "persona-risk-chief",
                },
                "launch_context": {"analysis_refs": None},
                "validation_warnings": [],
                "artifact_ids": [],
                "failure": {
                    "reason_code": "MISSING_DATA_PARTITION",
                    "message": "Macro calendar partition for Q1 was missing from source snapshot.",
                },
                "allowedActions": {"canCancel": False},
            },
        },
        "research_artifacts": {
            "art_2024_abc121": {
                "artifact_id": "art_2024_abc121",
                "lineage_id": "lin_xyz987",
                "version": 1,
                "parent_artifact_id": None,
                "status": "superseded",
                "name": "MACD-momentum-v1",
                "artifact_type": "strategy_model",
                "description": "Baseline MACD-momentum strategy artifact.",
                "produced_by_experiment_id": "exp_9800",
                "linked_ticket_id": "tkt_5432",
                "created_at": "2026-04-10T09:00:00Z",
                "sealed_at": "2026-04-10T09:04:00Z",
                "metrics": {
                    "sharpe_ratio": 0.98,
                    "sortino_ratio": 1.31,
                    "max_drawdown": -0.14,
                    "annualized_return": 0.11,
                    "win_rate": 0.49,
                    "avg_trade_duration_days": 4.1,
                    "total_trades": 376,
                },
                "parameters": {
                    "fast_period": 10,
                    "slow_period": 26,
                    "signal_period": 9,
                    "position_sizing": "fixed_fractional",
                    "risk_per_trade": 0.01,
                },
                "provenance": {
                    "linked_experiment": {
                        "experiment_id": "exp_9800",
                        "display_label": "MACD baseline run - 2026-04-10",
                    },
                    "linked_ticket": {
                        "ticket_id": "tkt_5432",
                        "title": "Momentum strategy parameter optimization",
                    },
                    "lineage_refs": [],
                },
            },
            "art_2024_abc122": {
                "artifact_id": "art_2024_abc122",
                "lineage_id": "lin_xyz987",
                "version": 2,
                "parent_artifact_id": "art_2024_abc121",
                "status": "superseded",
                "name": "MACD-momentum-v2",
                "artifact_type": "strategy_model",
                "description": "Second MACD-momentum iteration with tuned entry timing.",
                "produced_by_experiment_id": "exp_9840",
                "linked_ticket_id": "tkt_5432",
                "created_at": "2026-04-14T11:30:00Z",
                "sealed_at": "2026-04-14T11:35:10Z",
                "metrics": {
                    "sharpe_ratio": 1.21,
                    "sortino_ratio": 1.58,
                    "max_drawdown": -0.10,
                    "annualized_return": 0.15,
                    "win_rate": 0.52,
                    "avg_trade_duration_days": 3.5,
                    "total_trades": 401,
                },
                "parameters": {
                    "fast_period": 11,
                    "slow_period": 26,
                    "signal_period": 9,
                    "position_sizing": "fixed_fractional",
                    "risk_per_trade": 0.01,
                },
                "provenance": {
                    "linked_experiment": {
                        "experiment_id": "exp_9840",
                        "display_label": "MACD tuning run 2 - 2026-04-14",
                    },
                    "linked_ticket": {
                        "ticket_id": "tkt_5432",
                        "title": "Momentum strategy parameter optimization",
                    },
                    "lineage_refs": [],
                },
            },
            "art_2024_abc123": {
                "artifact_id": "art_2024_abc123",
                "lineage_id": "lin_xyz987",
                "version": 3,
                "parent_artifact_id": "art_2024_abc122",
                "status": "sealed",
                "name": "MACD-momentum-v3",
                "artifact_type": "strategy_model",
                "description": "Third iteration of MACD-momentum strategy after parameter tuning",
                "produced_by_experiment_id": "exp_9876",
                "linked_ticket_id": "tkt_5432",
                "created_at": "2026-04-18T14:22:00Z",
                "sealed_at": "2026-04-18T14:25:10Z",
                "metrics": {
                    "sharpe_ratio": 1.42,
                    "sortino_ratio": 1.87,
                    "max_drawdown": -0.08,
                    "annualized_return": 0.18,
                    "win_rate": 0.54,
                    "avg_trade_duration_days": 3.2,
                    "total_trades": 412,
                },
                "parameters": {
                    "fast_period": 12,
                    "slow_period": 26,
                    "signal_period": 9,
                    "position_sizing": "fixed_fractional",
                    "risk_per_trade": 0.01,
                },
                "provenance": {
                    "linked_experiment": {
                        "experiment_id": "exp_9876",
                        "display_label": "MACD tuning run 3 - 2026-04-18",
                    },
                    "linked_ticket": {
                        "ticket_id": "tkt_5432",
                        "title": "Momentum strategy parameter optimization",
                    },
                    "lineage_refs": [
                        {
                            "ref_type": "inspired_by",
                            "target_artifact_id": "art_2020_base01",
                            "resolved_link": "/research/compare?artifact_ids=art_2024_abc123,art_2020_base01",
                        }
                    ],
                },
            },
            "art_2024_pending01": {
                "artifact_id": "art_2024_pending01",
                "lineage_id": "lin_pending555",
                "version": 1,
                "parent_artifact_id": None,
                "status": "pending",
                "name": "Volatility-gated candidate",
                "artifact_type": "strategy_model",
                "description": "Artifact is still being assembled by the experiment pipeline.",
                "produced_by_experiment_id": "exp_9900",
                "linked_ticket_id": "tkt_6000",
                "created_at": "2026-04-19T16:00:00Z",
                "sealed_at": None,
                "metrics": {
                    "sharpe_ratio": 1.05,
                    "sortino_ratio": 1.39,
                    "max_drawdown": -0.12,
                    "annualized_return": 0.13,
                    "win_rate": 0.5,
                    "avg_trade_duration_days": 3.8,
                    "total_trades": 210,
                },
                "parameters": {
                    "fast_period": 14,
                    "slow_period": 28,
                    "signal_period": 10,
                    "position_sizing": "fixed_fractional",
                    "risk_per_trade": 0.012,
                },
                "provenance": {
                    "linked_experiment": {
                        "experiment_id": "exp_9900",
                        "display_label": "Volatility-gated candidate run - 2026-04-19",
                    },
                    "linked_ticket": {
                        "ticket_id": "tkt_6000",
                        "title": "Volatility-gated signal candidate",
                    },
                    "lineage_refs": [],
                },
            },
            "art_2024_failed01": {
                "artifact_id": "art_2024_failed01",
                "lineage_id": "lin_failed777",
                "version": 1,
                "parent_artifact_id": None,
                "status": "failed",
                "name": "Macro-event candidate",
                "artifact_type": "strategy_model",
                "description": "Artifact creation failed before the payload sealed.",
                "produced_by_experiment_id": "exp_9910",
                "linked_ticket_id": "tkt_6001",
                "created_at": "2026-04-19T18:00:00Z",
                "sealed_at": None,
                "metrics": {
                    "sharpe_ratio": None,
                    "sortino_ratio": None,
                    "max_drawdown": None,
                    "annualized_return": None,
                    "win_rate": None,
                    "avg_trade_duration_days": None,
                    "total_trades": None,
                },
                "parameters": {
                    "fast_period": 8,
                    "slow_period": 21,
                    "signal_period": 7,
                    "position_sizing": "fixed_fractional",
                    "risk_per_trade": 0.015,
                },
                "provenance": {
                    "linked_experiment": {
                        "experiment_id": "exp_9910",
                        "display_label": "Macro-event candidate run - 2026-04-19",
                    },
                    "linked_ticket": {
                        "ticket_id": "tkt_6001",
                        "title": "Macro-event strategy candidate",
                    },
                    "lineage_refs": [],
                },
            },
        },
        "research_notes": {
            "note-a1b2c3d4-e5f6-7890-abcd-ef1234567890": {
                "note_id": "note-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "title": "Momentum regime - observed slippage above 2sigma",
                "body": (
                    "## Observation\n\nDuring the April 14-16 high-volatility window, strategy "
                    "**MOM-v3** showed consistent bid-ask slippage above the 2sigma threshold "
                    "on ES futures.\n\nThe effect was most pronounced in the 09:30-10:00 window."
                ),
                "attachment_type": "research_ticket",
                "attachment_ref": "tkt-7a8b9c0d-1234-5678-abcd-ef0123456789",
                "owner_ref": {
                    "owner_type": "operator",
                    "owner_id": "op-001",
                    "display_name": "Alice Chen",
                },
                "tags": ["slippage", "momentum", "high-volatility"],
                "linked_evidence_refs": [
                    "evref-c3d4e5f6-a7b8-9012-cdef-012345678901",
                    "evref-d4e5f6a7-b8c9-0123-defa-123456789012",
                ],
                "linked_memory_anchors": [
                    "mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                ],
                "created_at": "2026-04-16T14:22:00Z",
                "updated_at": "2026-04-17T09:05:00Z",
            },
            "note-b2c3d4e5-f6a7-8901-bcde-f01234567891": {
                "note_id": "note-b2c3d4e5-f6a7-8901-bcde-f01234567891",
                "title": None,
                "body": (
                    "Free-standing note: tracking cross-persona alignment on risk tolerance "
                    "thresholds following the 2026-04-10 governance review."
                ),
                "attachment_type": "free_standing",
                "attachment_ref": None,
                "owner_ref": {
                    "owner_type": "persona",
                    "owner_id": "persona-HAWK-001",
                    "display_name": "HAWK (Persona)",
                },
                "tags": ["governance", "risk-tolerance", "cross-persona"],
                "linked_evidence_refs": [],
                "linked_memory_anchors": [],
                "created_at": "2026-04-11T08:00:00Z",
                "updated_at": "2026-04-11T08:00:00Z",
            },
        },
        "evidence_refs": {
            "evref-c3d4e5f6-a7b8-9012-cdef-012345678901": {
                "ref_id": "evref-c3d4e5f6-a7b8-9012-cdef-012345678901",
                "source_document": {
                    "title": "ES Futures Slippage Distribution - Apr 14-16 Backtrace",
                    "source_type": "experiment_artifact",
                    "source_ref": "artifact://research/artifact-abc123",
                    "excerpt": (
                        "Backtrace confirms that April 14-16 slippage concentrated in the 09:30-10:00 UTC "
                        "execution window and coincided with widened spread conditions."
                    ),
                    "storage_preview": {
                        "available": True,
                        "preview_type": "image",
                        "preview_token": "prev-local-artifact-abc123",
                    },
                    "captured_at": "2026-04-16T13:10:00Z",
                    "captured_by": "Operator: Alice Chen",
                },
                "link_type": "supporting_evidence",
                "credibility": {
                    "tier": "primary",
                    "verified": True,
                    "last_verified_at": "2026-04-17T11:30:00Z",
                    "verification_method": "operator_review",
                },
                "linked_object_summary": {
                    "entity_type": "memory_entry",
                    "entity_ref": "mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                    "display_label": "High-volatility momentum slippage - pattern observed Q1 2026",
                },
                "resolved_link": {
                    "availability": "available",
                    "route_href": "/research/artifacts/artifact-abc123",
                    "display_label": "Open experiment artifact",
                    "open_in_new_tab": False,
                },
                "linked_decisions": [
                    {
                        "entity_type": "memory_entry",
                        "entity_ref": "mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                        "display_label": "High-volatility momentum slippage - pattern observed Q1 2026",
                        "route_href": "/knowledge/memory/mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                        "link_type": "supporting_evidence",
                        "relationship_note": "Histogram reinforces the standing slippage pattern captured in institutional memory.",
                    }
                ],
                "source_note_context": {
                    "note_id": "note-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                    "title": "Momentum regime - observed slippage above 2sigma",
                    "excerpt": "Observed persistent slippage above the 2sigma threshold during the April high-volatility window.",
                    "route_href": "/knowledge/notes/note-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                },
                "source_memory_context": {
                    "entry_id": "mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                    "headline": "High-volatility momentum slippage - pattern observed Q1 2026",
                    "knowledge_type": "regime_pattern",
                    "lifecycle_status": "active",
                    "route_href": "/knowledge/memory/mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                },
                "created_at": "2026-04-16T13:15:00Z",
                "route_href": "/knowledge/evidence/evref-c3d4e5f6-a7b8-9012-cdef-012345678901",
            },
            "evref-a1b2c3d4-e5f6-7890-abcd-ef1234567890": {
                "ref_id": "evref-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "source_document": {
                    "title": "Post-Incident Review: Flash Spike 2026-03-14",
                    "source_type": "postmortem",
                    "source_ref": "s3://pantheon-docs/postmortems/2026-03-14-flash-spike.pdf",
                    "excerpt": (
                        "During the flash spike event on 2026-03-14, the momentum persona exhibited early "
                        "position unwinding behavior at -2.1σ drawdown across three execution windows."
                    ),
                    "storage_preview": {
                        "available": True,
                        "preview_type": "pdf",
                        "preview_token": "prev-local-flash-spike",
                    },
                    "captured_at": "2026-03-15T08:30:00Z",
                    "captured_by": "Operator: Alice Chen",
                },
                "link_type": "supporting_evidence",
                "credibility": {
                    "tier": "primary",
                    "verified": True,
                    "last_verified_at": "2026-04-01T12:00:00Z",
                    "verification_method": "operator_review",
                },
                "linked_object_summary": {
                    "entity_type": "memory_entry",
                    "entity_ref": "mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                    "display_label": "High-volatility momentum slippage - pattern observed Q1 2026",
                },
                "resolved_link": {
                    "availability": "available",
                    "route_href": "/knowledge/memory/mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                    "display_label": "View institutional memory entry",
                    "open_in_new_tab": False,
                },
                "linked_decisions": [
                    {
                        "entity_type": "memory_entry",
                        "entity_ref": "mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                        "display_label": "High-volatility momentum slippage - pattern observed Q1 2026",
                        "route_href": "/knowledge/memory/mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                        "link_type": "supporting_evidence",
                        "relationship_note": "Post-incident review directly supports the institutional memory entry.",
                    },
                    {
                        "entity_type": "insight_card",
                        "entity_ref": "ins-22222222-3333-4444-5555-666666666666",
                        "display_label": "Momentum personas self-protect under flash spike conditions",
                        "route_href": "/knowledge/insights/ins-22222222-3333-4444-5555-666666666666",
                        "link_type": "corroboration",
                        "relationship_note": None,
                    },
                ],
                "source_note_context": None,
                "source_memory_context": None,
                "created_at": "2026-03-15T08:30:00Z",
                "route_href": "/knowledge/evidence/evref-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            },
            "evref-b2c3d4e5-f6a7-8901-bcde-f12345678901": {
                "ref_id": "evref-b2c3d4e5-f6a7-8901-bcde-f12345678901",
                "source_document": {
                    "title": "Volatility Regime Analysis Q1 2026",
                    "source_type": "external_paper",
                    "source_ref": "external://arxiv.org/abs/2026.12345",
                    "excerpt": "Independent analysis of volatility-regime transitions and their impact on momentum model decay.",
                    "storage_preview": {
                        "available": False,
                        "preview_type": "unavailable",
                        "preview_token": None,
                    },
                    "captured_at": "2026-04-01T14:00:00Z",
                    "captured_by": "Operator: Alice Chen",
                },
                "link_type": "citation",
                "credibility": {
                    "tier": "secondary",
                    "verified": False,
                    "last_verified_at": None,
                    "verification_method": None,
                },
                "linked_object_summary": {
                    "entity_type": "strategy_spec",
                    "entity_ref": "strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a",
                    "display_label": "Momentum Regime Response v4",
                },
                "resolved_link": {
                    "availability": "external",
                    "route_href": "https://arxiv.org/abs/2026.12345",
                    "display_label": "Open external paper",
                    "open_in_new_tab": True,
                },
                "linked_decisions": [
                    {
                        "entity_type": "strategy_spec",
                        "entity_ref": "strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a",
                        "display_label": "Momentum Regime Response v4",
                        "route_href": "/knowledge/strategy-specs/strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a",
                        "link_type": "citation",
                        "relationship_note": "Background citation attached to the current strategy spec.",
                    }
                ],
                "source_note_context": None,
                "source_memory_context": None,
                "created_at": "2026-04-01T14:00:00Z",
                "route_href": "/knowledge/evidence/evref-b2c3d4e5-f6a7-8901-bcde-f12345678901",
            },
        },
        "insight_cards": {
            "ins-7a3f2c91-e4b8-4d12-9f65-0c8e1a234567": {
                "insight_id": "ins-7a3f2c91-e4b8-4d12-9f65-0c8e1a234567",
                "summary": (
                    "Momentum strategies consistently underperform during high-volatility regimes when "
                    "rebalancing frequency exceeds weekly cadence. Evidence from four primary sources "
                    "across three independent experiments."
                ),
                "scope": "strategy",
                "scope_ref": "strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a",
                "status": "active",
                "superseded_by_id": None,
                "confidence": {
                    "score": 0.82,
                    "label": "high",
                    "basis": (
                        "Supported by 4 primary evidence refs from 3 independent experiments "
                        "(EXP-441, EXP-456, EXP-472), corroborated by 2 institutional memory entries "
                        "from Q1–Q2 2026 postmortem reviews."
                    ),
                },
                "tags": ["momentum", "volatility-regime", "rebalancing-frequency"],
                "source_ref": "agg-ref:ins-7a3f2c91-v2026041914",
                "supporting_evidence_refs": [
                    {
                        "ref_id": "evref-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "source_document_title": "Post-Incident Review: Flash Spike 2026-03-14",
                        "link_type": "supporting_evidence",
                        "credibility_tier": "primary",
                        "resolved_link": {
                            "availability": "available",
                            "route_href": "/knowledge/evidence/evref-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                            "display_label": "View evidence reference",
                            "open_in_new_tab": False,
                        },
                    },
                    {
                        "ref_id": "evref-c3d4e5f6-a7b8-9012-cdef-012345678901",
                        "source_document_title": "ES Futures Slippage Distribution - Apr 14-16 Backtrace",
                        "link_type": "supporting_evidence",
                        "credibility_tier": "primary",
                        "resolved_link": {
                            "availability": "available",
                            "route_href": "/knowledge/evidence/evref-c3d4e5f6-a7b8-9012-cdef-012345678901",
                            "display_label": "View evidence reference",
                            "open_in_new_tab": False,
                        },
                    },
                    {
                        "ref_id": "evref-b2c3d4e5-f6a7-8901-bcde-f12345678901",
                        "source_document_title": "Volatility Regime Analysis Q1 2026",
                        "link_type": "citation",
                        "credibility_tier": "secondary",
                        "resolved_link": {
                            "availability": "external",
                            "route_href": "https://arxiv.org/abs/2026.12345",
                            "display_label": "Open external paper",
                            "open_in_new_tab": True,
                        },
                    },
                ],
                "linked_sources": [
                    {
                        "entity_type": "experiment",
                        "entity_ref": "exp-20260419-012",
                        "display_label": "Momentum decay replay on March volatility cluster",
                        "route_href": "/research/experiments/exp-20260419-012",
                        "relationship_note": "Primary aggregation input; full result set included in evidence refs",
                    },
                    {
                        "entity_type": "memory_entry",
                        "entity_ref": "mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                        "display_label": "High-volatility momentum slippage - pattern observed Q1 2026",
                        "route_href": "/knowledge/memory/mem-e5f6a7b8-c9d0-1234-efab-234567890123",
                        "relationship_note": "Institutional memory anchor; corroborating finding",
                    },
                    {
                        "entity_type": "strategy_spec",
                        "entity_ref": "strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a",
                        "display_label": "Momentum Regime Response v4",
                        "route_href": "/knowledge/strategy-specs/strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a",
                        "relationship_note": "Insight scoped to this strategy spec",
                    },
                ],
                "aggregation_provenance": {
                    "memory_entry_count": 2,
                    "note_count": 1,
                    "evidence_ref_count": 3,
                    "primary_evidence_count": 2,
                    "aggregated_at": "2026-04-19T14:32:10Z",
                    "aggregation_version": "v2.3.1",
                },
                "created_at": "2026-04-15T10:00:00Z",
                "updated_at": "2026-04-19T14:32:10Z",
            },
            "ins-b5d8e3f2-1a7c-4e09-8d56-f2c3a4b5d6e7": {
                "insight_id": "ins-b5d8e3f2-1a7c-4e09-8d56-f2c3a4b5d6e7",
                "summary": (
                    "Mean-reversion signals derived from order-book imbalance show reduced predictive "
                    "power during the first 30 minutes of each trading session."
                ),
                "scope": "global",
                "scope_ref": None,
                "status": "active",
                "superseded_by_id": None,
                "confidence": {
                    "score": 0.67,
                    "label": "medium",
                    "basis": "Corroborated by institutional memory entries from Q1 and Q2 2026.",
                },
                "tags": ["mean-reversion", "order-book", "session-open"],
                "source_ref": "agg-ref:ins-b5d8e3f2-v2026041822",
                "supporting_evidence_refs": [],
                "linked_sources": [
                    {
                        "entity_type": "memory_entry",
                        "entity_ref": "mem-2c3d4e5f-6a7b-8c9d-0e1f-2a3b4c5d6e7f",
                        "display_label": "Momentum strategy underperforms in low-volatility sideways regimes",
                        "route_href": "/knowledge/memory/mem-2c3d4e5f-6a7b-8c9d-0e1f-2a3b4c5d6e7f",
                        "relationship_note": "Cross-check against regime-memory findings",
                    }
                ],
                "aggregation_provenance": {
                    "memory_entry_count": 1,
                    "note_count": 0,
                    "evidence_ref_count": 0,
                    "primary_evidence_count": 0,
                    "aggregated_at": "2026-04-18T22:11:45Z",
                    "aggregation_version": "v2.3.0",
                },
                "created_at": "2026-04-18T22:11:45Z",
                "updated_at": "2026-04-18T22:11:45Z",
            },
            "ins-c9e0f1a2-3b4c-5d6e-7f80-91a2b3c4d5e6": {
                "insight_id": "ins-c9e0f1a2-3b4c-5d6e-7f80-91a2b3c4d5e6",
                "summary": "Legacy momentum parameter set v1 is superseded. See replacement card for updated findings.",
                "scope": "strategy",
                "scope_ref": "strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a",
                "status": "superseded",
                "superseded_by_id": "ins-7a3f2c91-e4b8-4d12-9f65-0c8e1a234567",
                "confidence": {
                    "score": 0.55,
                    "label": "medium",
                    "basis": "Older synthesis retained for traceability only.",
                },
                "tags": ["momentum", "volatility-regime"],
                "source_ref": "agg-ref:ins-c9e0f1a2-v2026031509",
                "supporting_evidence_refs": [
                    {
                        "ref_id": "evref-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "source_document_title": "Post-Incident Review: Flash Spike 2026-03-14",
                        "link_type": "corroboration",
                        "credibility_tier": "primary",
                        "resolved_link": {
                            "availability": "available",
                            "route_href": "/knowledge/evidence/evref-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                            "display_label": "View evidence reference",
                            "open_in_new_tab": False,
                        },
                    }
                ],
                "linked_sources": [
                    {
                        "entity_type": "strategy_spec",
                        "entity_ref": "strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a",
                        "display_label": "Momentum Regime Response v4",
                        "route_href": "/knowledge/strategy-specs/strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a",
                        "relationship_note": "Legacy insight attached to the same strategy family",
                    }
                ],
                "aggregation_provenance": {
                    "memory_entry_count": 1,
                    "note_count": 0,
                    "evidence_ref_count": 1,
                    "primary_evidence_count": 1,
                    "aggregated_at": "2026-03-15T09:00:00Z",
                    "aggregation_version": "v1.8.4",
                },
                "created_at": "2026-03-15T09:00:00Z",
                "updated_at": "2026-03-15T09:00:00Z",
            },
        },
        "research_search_documents": {
            "rt-20260419-007": {
                "result_id": "rt-20260419-007",
                "match_type": "ticket",
                "title": "Evaluate momentum factor decay in high-volatility regime",
                "excerpt": (
                    "Research ticket asks whether momentum factors lose predictive power during sustained "
                    "volatility spikes and whether current rebalancing windows are too slow."
                ),
                "linked_ticket_id": "rt-20260419-007",
                "linked_ticket_status": "in_progress",
                "relevance_score": 0.98,
                "updated_at": "2026-04-19T20:15:00Z",
                "search_text": (
                    "momentum decay volatility regime ticket predictive power rebalancing windows "
                    "sustained volatility spikes"
                ),
                "links": {
                    "result_detail": "/research/tickets/rt-20260419-007",
                    "linked_ticket_detail": "/research/tickets/rt-20260419-007",
                },
            },
            "exp-20260419-012": {
                "result_id": "exp-20260419-012",
                "match_type": "experiment",
                "title": "Momentum decay replay on March volatility cluster",
                "excerpt": (
                    "Experiment replay compares signal half-life before and after the March volatility "
                    "cluster and shows reduced persistence after regime break."
                ),
                "linked_ticket_id": "rt-20260419-007",
                "linked_ticket_status": "in_progress",
                "relevance_score": 0.91,
                "updated_at": "2026-04-19T20:14:30Z",
                "search_text": (
                    "momentum decay experiment replay march volatility cluster signal half life regime break"
                ),
                "links": {
                    "result_detail": "/research/experiments/exp-20260419-012",
                    "linked_ticket_detail": "/research/tickets/rt-20260419-007",
                },
            },
            "artifact-20260418-005": {
                "result_id": "artifact-20260418-005",
                "match_type": "artifact",
                "title": "Momentum regime-break feature set v5",
                "excerpt": (
                    "Artifact notes include volatility-regime bucketing and a shorter half-life decay "
                    "coefficient intended for stressed market windows."
                ),
                "linked_ticket_id": "rt-20260419-007",
                "linked_ticket_status": "in_progress",
                "relevance_score": 0.87,
                "updated_at": "2026-04-18T20:12:58Z",
                "search_text": (
                    "momentum artifact volatility regime bucketing shorter half life decay stressed market windows"
                ),
                "links": {
                    "result_detail": "/research/artifacts/artifact-20260418-005",
                    "linked_ticket_detail": "/research/tickets/rt-20260419-007",
                },
            },
            "rt-20260415-001": {
                "result_id": "rt-20260415-001",
                "match_type": "ticket",
                "title": "Validate signal quality on macro event windows",
                "excerpt": (
                    "Closed research ticket recorded weaker signal quality around scheduled macro events "
                    "and tested whether exclusion windows were sufficient."
                ),
                "linked_ticket_id": "rt-20260415-001",
                "linked_ticket_status": "closed",
                "relevance_score": 0.64,
                "updated_at": "2026-04-19T11:00:00Z",
                "search_text": (
                    "signal quality macro event windows exclusion windows scheduled macro events closed ticket"
                ),
                "links": {
                    "result_detail": "/research/tickets/rt-20260415-001",
                    "linked_ticket_detail": "/research/tickets/rt-20260415-001",
                },
            },
        },
        "research_search_index": {
            "rw02-search-index": {
                "adapter_id": "rw02-search-index",
                "snapshot_at": "2026-04-19T20:14:30Z",
                "adapter_state": "fresh",
                "indexed_match_types": ["ticket", "experiment", "artifact"],
                "source_watermarks": {
                    "tickets": "2026-04-19T20:14:10Z",
                    "experiments": "2026-04-19T20:13:42Z",
                    "artifacts": "2026-04-19T20:12:58Z",
                },
            }
        },
        "trainer_replays": {
            "trn-20260418-003": {
                "session_id": "trn-20260418-003",
                "persona_id": "persona-alpha",
                "objective": "Reduce regime-switch whipsaw sensitivity in drawdown containment mode.",
                "status": "completed",
                "started_at": "2026-04-18T08:00:00Z",
                "ended_at": "2026-04-18T08:42:00Z",
                "actor_context": {
                    "persona_display_name": "Alpha Persona",
                    "persona_role_context": "systematic momentum coach",
                },
                "replay_resolution": {
                    "state": "pending_decision",
                    "decision_at": None,
                    "decision_by": None,
                    "note": None,
                },
                "artifacts": {
                    "before_artifact_ref": "artifact-041",
                    "candidate_artifact_ref": "artifact-042-candidate",
                    "after_artifact_ref": None,
                },
                "events": [
                    {
                        "event_id": "tevt-20260418-001",
                        "session_id": "trn-20260418-003",
                        "actor": "operator",
                        "actor_label": "Operator (Hedging Desk)",
                        "event_type": "message",
                        "message_body": "Focus on drawdown containment and reduce sensitivity to short-lived regime flips.",
                        "summary": "Operator instructs persona to focus on drawdown containment.",
                        "emitted_at": "2026-04-18T08:00:12Z",
                        "sequence_number": 1,
                        "outcome_signal": None,
                        "evidence_ref": None,
                        "patch_delta": None,
                        "eval_ref": None,
                        "artifact_refs": None,
                    },
                    {
                        "event_id": "tevt-20260418-002",
                        "session_id": "trn-20260418-003",
                        "actor": "system",
                        "actor_label": "System",
                        "event_type": "control_patch",
                        "message_body": None,
                        "summary": "Drawdown sensitivity threshold adjusted.",
                        "emitted_at": "2026-04-18T08:15:00Z",
                        "sequence_number": 2,
                        "outcome_signal": None,
                        "evidence_ref": {
                            "type": "telemetry",
                            "id": _TW04_DRAWDOWN_EVIDENCE_REF_ID,
                            "display_label": "Drawdown telemetry — April 18",
                            "url_pattern": _TW04_DRAWDOWN_EVIDENCE_ROUTE,
                        },
                        "patch_delta": [
                            {
                                "parameter_key": "drawdown_sensitivity_threshold",
                                "previous_value": 0.12,
                                "new_value": 0.08,
                            }
                        ],
                        "eval_ref": None,
                        "artifact_refs": None,
                    },
                    {
                        "event_id": "tevt-20260418-003",
                        "session_id": "trn-20260418-003",
                        "actor": "system",
                        "actor_label": "System",
                        "event_type": "preview_trigger",
                        "message_body": None,
                        "summary": "Rapid-eval triggered for candidate patch.",
                        "emitted_at": "2026-04-18T08:20:00Z",
                        "sequence_number": 3,
                        "outcome_signal": None,
                        "evidence_ref": None,
                        "patch_delta": None,
                        "eval_ref": {
                            "eval_id": "teval-20260418-003",
                            "baseline_snapshot_at": "2026-04-18T08:00:00Z",
                            "candidate_snapshot_at": "2026-04-18T08:18:00Z",
                        },
                        "artifact_refs": None,
                    },
                    {
                        "event_id": "tevt-20260418-004",
                        "session_id": "trn-20260418-003",
                        "actor": "persona",
                        "actor_label": "Alpha Persona",
                        "event_type": "outcome_signal",
                        "message_body": "I can widen confirmation requirements before reversing the posture in containment mode.",
                        "summary": "Teaching complete.",
                        "emitted_at": "2026-04-18T08:42:00Z",
                        "sequence_number": 4,
                        "outcome_signal": "teaching-complete",
                        "evidence_ref": None,
                        "patch_delta": None,
                        "eval_ref": None,
                        "artifact_refs": None,
                    },
                ],
            },
            "trn-20260417-001": {
                "session_id": "trn-20260417-001",
                "persona_id": "persona-alpha",
                "objective": "Adjust momentum signal weights for low-volume overnight sessions.",
                "status": "completed",
                "started_at": "2026-04-17T10:00:00Z",
                "ended_at": "2026-04-17T10:30:00Z",
                "actor_context": {
                    "persona_display_name": "Alpha Persona",
                    "persona_role_context": "systematic momentum coach",
                },
                "replay_resolution": {
                    "state": "committed",
                    "decision_at": "2026-04-17T11:00:00Z",
                    "decision_by": "operator-risk-desk",
                    "note": "Momentum weight adjustment approved after stable overnight eval.",
                },
                "artifacts": {
                    "before_artifact_ref": "artifact-040",
                    "candidate_artifact_ref": "artifact-041-candidate",
                    "after_artifact_ref": "artifact-041",
                },
                "events": [
                    {
                        "event_id": "tevt-20260417-001",
                        "session_id": "trn-20260417-001",
                        "actor": "operator",
                        "actor_label": "Operator (Risk Desk)",
                        "event_type": "message",
                        "message_body": "Reduce overnight signal weight during thin volume periods.",
                        "summary": "Operator instructs persona to reduce overnight signal weight.",
                        "emitted_at": "2026-04-17T10:00:10Z",
                        "sequence_number": 1,
                        "outcome_signal": None,
                        "evidence_ref": None,
                        "patch_delta": None,
                        "eval_ref": None,
                        "artifact_refs": None,
                    },
                    {
                        "event_id": "tevt-20260417-002",
                        "session_id": "trn-20260417-001",
                        "actor": "system",
                        "actor_label": "System",
                        "event_type": "commit",
                        "message_body": None,
                        "summary": "Candidate committed.",
                        "emitted_at": "2026-04-17T11:00:00Z",
                        "sequence_number": 2,
                        "outcome_signal": None,
                        "evidence_ref": None,
                        "patch_delta": None,
                        "eval_ref": None,
                        "artifact_refs": {
                            "before_artifact_ref": "artifact-040",
                            "candidate_artifact_ref": "artifact-041-candidate",
                            "after_artifact_ref": "artifact-041",
                        },
                    },
                ],
            },
        },
    }
    _merge_market_persona_fleet(data)
    _merge_default_fixture_pack(data, _load_default_fixture_pack_datasets())
    return data


class ReadSurfaceStore:
    _LOCAL_DATA_KEYS = {
        "deployment_plans": "deployment_plans",
        "approval_decisions": "approval_decisions",
        "capital_pools": "capital_pools",
        "persona_bindings": "bindings",
        "runtime_bindings": "runtime_bindings",
        "registry_entries": "registry_entries",
        "personas": "personas",
        "persona_route_policies": "persona_route_policies",
        "sessions": "sessions",
        "capability_snapshots": "capability_snapshots",
        "teaching_sessions": "teaching_sessions",
        "trainer_previews": "trainer_previews",
        "consultation_sessions": "consultation_sessions",
        "consult_transcripts": "consult_transcripts",
        "consult_policies": "consult_policies",
        "route_policies": "route_policies",
        "incidents": "incidents",
        "postmortems": "postmortems",
        "evolution_decisions": "evolution_decisions",
        "evolution_programs": "evolution_programs",
        "evolution_program_runs": "evolution_program_runs",
        "evolution_program_candidates": "evolution_program_candidates",
        "telemetry_summaries": "telemetry_summaries",
        "telemetry_performance": "telemetry_performance",
        "paper_live_drift_reports": "paper_live_drift_reports",
        "paper_runtime_monitoring_sessions": "paper_runtime_monitoring_sessions",
        "lineage_edges": "lineage_edges",
        "inspiration_graphs": "inspiration_graphs",
        "kill_switch": "kill_switch",
        "rollbacks": "rollbacks",
        "rollbacks_by_incident": "rollbacks_by_incident",
        "all_rollbacks": "all_rollbacks",
        "latest_runs": "latest_runs",
        "review_summaries": "review_summaries",
        "rollback_reviews": "rollback_reviews",
        "governance_audit_events": "governance_audit_events",
        "governance_review_queue_items": "governance_review_queue_items",
        "approval_queue_items": "approval_queue_items",
        "deployment_diffs": "deployment_diffs",
        "research_tickets": "research_tickets",
        "research_experiments": "research_experiments",
        "research_artifacts": "research_artifacts",
        "research_notes": "research_notes",
        "institutional_memory_entries": "institutional_memory_entries",
        "research_analyses": "research_analyses",
        "evidence_refs": "evidence_refs",
        "insight_cards": "insight_cards",
        "strategy_specs": "strategy_specs",
        "research_search_documents": "research_search_documents",
        "research_search_index": "research_search_index",
        "consult_requests": "consult_requests",
        "consult_memos": "consult_memos",
        "trainer_replays": "trainer_replays",
        "trainer_controls": "trainer_controls",
        "workflow_templates": "workflow_templates",
        "hook_registry": "hook_registry",
        "jobs": "jobs",
        "bff_jobs": "bff_jobs",
        "decision_journal_entries": "decision_journal_entries",
        "decision_journal_idempotency": "decision_journal_idempotency",
        "agora_journal_audit_events": "agora_journal_audit_events",
        "agora_signals": "agora_signals",
        "agora_feedback": "agora_feedback",
        "agora_signal_feedback": "agora_signal_feedback",
        "agora_watchlist": "agora_watchlist",
        "agora_sessions": "agora_sessions",
        "agora_skill_coaching_sessions": "agora_skill_coaching_sessions",
        "agora_persona_lab_runs": "agora_persona_lab_runs",
        "agora_evaluation_suites": "agora_evaluation_suites",
        "agora_evaluation_runs": "agora_evaluation_runs",
        "agora_committee_evidence_packs": "agora_committee_evidence_packs",
        "agora_handoffs": "agora_handoffs",
        "agora_training_examples": "agora_training_examples",
        "agora_audit_events": "agora_audit_events",
        "v5_interventions": "v5_interventions",
        "ooda_packets": "ooda_packets",
        "synthesis_conflict_logs": "synthesis_conflict_logs",
        "ranking_formulas": "ranking_formulas",
        "rebalances": "rebalances",
        "capital_allocations": "capital_allocations",
        "containments": "containments",
        "rankings": "rankings",
        "persona_league": "persona_league",
        "ranking_snapshots": "ranking_snapshots",
        "allocation_evaluations": "allocation_evaluations",
    }
    _MARKET_PERSONA_RECORD_KEYS = {
        "personas": ["persona_id", "id"],
        "capital_pools": ["pool_id", "id"],
        "persona_bindings": ["binding_id", "id"],
        "bindings": ["binding_id", "id"],
        "runtime_bindings": ["runtime_id", "runtime_binding_id", "binding_id", "id"],
        "sessions": ["session_id", "id"],
        "capability_snapshots": ["snapshot_id", "id"],
        "teaching_sessions": ["session_id", "id"],
        "strategy_specs": ["strategy_id", "id"],
        "telemetry_summaries": ["runtime_id", "id"],
        "ooda_packets": ["packet_id", "id"],
        "persona_league": ["persona_id", "id"],
    }

    def __init__(
        self,
        storage_path: str,
        *,
        allow_local_snapshot_fallback: Optional[bool] = None,
    ) -> None:
        self._path = Path(storage_path)
        self._data: Dict[str, Any] = {}
        # These admission records are owned and persisted by the BFF.  They
        # remain authoritative after restart even when read-model fallback is
        # disabled.
        self._local_overlay_write_datasets: set[str] = {
            "ranking_snapshots",
            "allocation_evaluations",
        }
        if allow_local_snapshot_fallback is None:
            allow_local_snapshot_fallback = False
        self._allow_local_snapshot_fallback = allow_local_snapshot_fallback
        self._canonical = CanonicalSnapshotAdapter(
            snapshot_path=self._path,
            allow_snapshot_fallback=self._allow_local_snapshot_fallback,
        )
        self._service = ServiceBackedReadAdapter(
            snapshot_path=self._path,
            allow_snapshot_fallback=self._allow_local_snapshot_fallback,
        )
        self._consultation_client_instance: Optional[ConsultationServiceClient] = None
        self._last_governed_search_refs: Dict[str, Dict[str, Any]] = {}
        self._load_or_seed()

    def _training_session_base_url(self) -> Optional[str]:
        return _base_url_from_env(("PANTHEON_TRAINING_SESSION_API_URL", "PANTHEON_TRAINING_SESSION_URL"))

    @staticmethod
    def _dormant_service_base_url(service: str) -> Optional[str]:
        spec = _DORMANT_SERVICE_SPECS.get(service, {})
        return _base_url_from_env(tuple(spec.get("base_env") or ()))

    @staticmethod
    def _dormant_empty_surface(reason: str, *, configured: bool = False) -> Dict[str, Any]:
        return {
            "status": "unavailable",
            "source": "service_client" if configured else "missing",
            "reason": reason,
        }

    @staticmethod
    def _dormant_capability_backend(record: Dict[str, Any]) -> str:
        return str(record.get("adapter") or record.get("worker") or record.get("backend") or "").strip().lower()

    @staticmethod
    def _dormant_activity_id(record: Dict[str, Any], id_fields: tuple[str, ...]) -> Optional[str]:
        for field in id_fields:
            value = record.get(field)
            if value not in (None, ""):
                return str(value)
        return None

    @staticmethod
    def _dormant_activity_time(record: Dict[str, Any]) -> str:
        for field in ("updated_at", "created_at", "requested_at", "proposed_at", "emitted_at"):
            value = record.get(field)
            if value not in (None, ""):
                return str(value)
        return ""

    @staticmethod
    def _dormant_dict_list(value: Any) -> List[Dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    @staticmethod
    def _dormant_truncate(value: Any, *, limit: int = 1200) -> Optional[str]:
        if value in (None, ""):
            return None
        text = str(value)
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    @staticmethod
    def _dormant_json_payload(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text or text[0] not in "[{":
            return None
        try:
            return json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _automation_record_sort_key(record: Dict[str, Any]) -> str:
        return str(
            record.get("workflow_id")
            or record.get("hook_id")
            or record.get("cron_id")
            or record.get("template_id")
            or record.get("id")
            or record.get("name")
            or ""
        )

    def list_workflow_templates(self) -> List[Dict[str, Any]]:
        available, records = self._service.list_records("workflow_templates")
        if not available:
            return []
        return sorted(
            [dict(record) for record in records if isinstance(record, dict)],
            key=self._automation_record_sort_key,
        )

    def list_hook_registry(self) -> List[Dict[str, Any]]:
        available, records = self._service.list_records("hook_registry")
        if not available:
            return []
        return sorted(
            [dict(record) for record in records if isinstance(record, dict)],
            key=self._automation_record_sort_key,
        )

    def _dormant_artifact_refs_from_worker_stdout(self, record: Dict[str, Any]) -> List[Dict[str, Any]]:
        payload = self._dormant_json_payload(record.get("stdout"))
        if not isinstance(payload, dict):
            return []

        refs: List[Dict[str, Any]] = []
        manifest = payload.get("artifact_manifest")
        if isinstance(manifest, dict):
            files = manifest.get("files")
            if isinstance(files, dict):
                for name, path in sorted(files.items()):
                    if path in (None, ""):
                        continue
                    ref: Dict[str, Any] = {
                        "artifact_name": str(name),
                        "storage_ref": str(path),
                        "source_field": "stdout.artifact_manifest.files",
                    }
                    if payload.get("checksum"):
                        ref["checksum"] = payload.get("checksum")
                    refs.append(ref)

        artifact_paths = payload.get("artifact_paths")
        if isinstance(artifact_paths, dict):
            for name, path in sorted(artifact_paths.items()):
                if path in (None, ""):
                    continue
                refs.append(
                    {
                        "artifact_name": str(name),
                        "storage_ref": str(path),
                        "source_field": "stdout.artifact_paths",
                    }
                )

        return refs

    def _dormant_activity_artifact_refs(self, record: Dict[str, Any]) -> List[Dict[str, Any]]:
        refs: List[Dict[str, Any]] = []
        for field in ("artifact_refs", "output_refs"):
            for ref in self._dormant_dict_list(record.get(field)):
                projected = dict(ref)
                projected.setdefault("source_field", field)
                refs.append(projected)
        refs.extend(self._dormant_artifact_refs_from_worker_stdout(record))
        return refs

    def _dormant_activity_logs(self, record: Dict[str, Any]) -> List[Dict[str, Any]]:
        logs: List[Dict[str, Any]] = []
        for event in self._dormant_dict_list(record.get("events")):
            logs.append(
                {
                    "source": "event",
                    "event_type": event.get("event_type"),
                    "summary": event.get("summary"),
                    "emitted_at": event.get("emitted_at"),
                    "sequence_number": event.get("sequence_number"),
                }
            )
        stdout = self._dormant_truncate(record.get("stdout"))
        if stdout:
            logs.append({"source": "stdout", "message": stdout})
        stderr = self._dormant_truncate(record.get("stderr"))
        if stderr:
            logs.append({"source": "stderr", "severity": "error", "message": stderr})
        return logs

    def _dormant_activity_error_summary(
        self,
        record: Dict[str, Any],
        *,
        status: str,
        rejection: Dict[str, Any],
    ) -> Dict[str, Any]:
        errors: List[Dict[str, Any]] = []
        if rejection:
            errors.append(
                {
                    "kind": "rejection",
                    "reason": rejection.get("reason"),
                    "detail": rejection.get("detail"),
                    "rejected_at": rejection.get("rejected_at"),
                }
            )

        gateway_ref = record.get("gateway_ref")
        if isinstance(gateway_ref, dict) and gateway_ref.get("error"):
            errors.append(
                {
                    "kind": "gateway",
                    "reason": gateway_ref.get("error"),
                    "detail": "Downstream research-worker-gateway dispatch was unavailable.",
                }
            )

        exit_code = record.get("exit_code")
        stderr = self._dormant_truncate(record.get("stderr"))
        if status in {"failed", "error"} or (exit_code not in (None, 0, "0")):
            errors.append(
                {
                    "kind": "worker_exit",
                    "reason": f"exit_code={exit_code}" if exit_code is not None else status,
                    "detail": stderr,
                }
            )
        elif stderr:
            errors.append(
                {
                    "kind": "stderr",
                    "reason": "stderr_observed",
                    "detail": stderr,
                }
            )

        cancel_reason = record.get("cancel_reason")
        if cancel_reason:
            errors.append(
                {
                    "kind": "cancel",
                    "reason": cancel_reason,
                    "detail": "Research operation was canceled before completion.",
                }
            )

        return {
            "has_error": bool(errors),
            "error_count": len(errors),
            "errors": errors,
        }

    def _fetch_dormant_json(self, service: str, path_key: str) -> tuple[Dict[str, Any], Any]:
        spec = _DORMANT_SERVICE_SPECS[service]
        base_url = self._dormant_service_base_url(service)
        path = str(spec.get(path_key) or "")
        if not base_url or not path:
            return self._dormant_empty_surface("service_url_not_configured"), None
        available, payload = _http_json_get(base_url, path)
        if not available:
            return self._dormant_empty_surface("service_unavailable", configured=True), None
        return {
            "status": "ok",
            "source": "service_client",
            "path": path,
        }, payload

    def _project_dormant_openclaw_capabilities(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        fail_closed = bool(payload.get("fail_closed"))
        return [
            {
                "service": "openclaw_gateway_adapter",
                "backend": "openclaw",
                "status": "deferred",
                "gate_state": "fail_closed" if fail_closed else "unknown",
                "allowed_scope": "capability_metadata_read_only",
                "activation_state": payload.get("activation_state"),
                "broker_execution": payload.get("broker_execution"),
                "paper_adapter": payload.get("paper_adapter"),
                "live_adapter": payload.get("live_adapter"),
                "capital_binding": payload.get("capital_binding"),
                "fail_closed": fail_closed,
            }
        ]

    def _project_dormant_capabilities(
        self,
        service: str,
        payload: Any,
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        if not isinstance(payload, dict):
            return [], []
        if service == "openclaw_gateway_adapter":
            return self._project_dormant_openclaw_capabilities(payload), []

        capabilities = payload.get("capabilities")
        if not isinstance(capabilities, list):
            capabilities = []

        dormant: List[Dict[str, Any]] = []
        safe_dispatchers: List[str] = []
        for item in capabilities:
            if not isinstance(item, dict):
                continue
            backend = self._dormant_capability_backend(item)
            if not backend:
                continue
            if backend in _DORMANT_SAFE_DISPATCHERS and str(item.get("status") or "").lower() == "available":
                safe_dispatchers.append(backend)
            if backend not in _DORMANT_OSS_BACKENDS:
                continue
            dormant.append(
                {
                    "service": service,
                    "backend": backend,
                    "status": item.get("status"),
                    "gate_state": item.get("gate_state") or "unknown",
                    "allowed_scope": item.get("allowed_scope") or "unknown",
                    "offline_gate": payload.get("offline_gate"),
                    "offline_dispatch": item.get("offline_dispatch"),
                    "gateway_routing": item.get("gateway_routing"),
                    "activation_gate": item.get("activation_gate"),
                    "entrypoint": item.get("entrypoint"),
                    "purpose": item.get("purpose"),
                    "note": item.get("note"),
                }
            )
        return dormant, sorted(set(safe_dispatchers))

    def _project_dormant_activity(
        self,
        service: str,
        payload: Any,
    ) -> List[Dict[str, Any]]:
        if isinstance(payload, dict):
            records = payload.get("items") or payload.get("data") or payload.get("runs") or payload.get("jobs") or []
        else:
            records = payload
        if not isinstance(records, list):
            return []

        spec = _DORMANT_SERVICE_SPECS[service]
        actor_field = str(spec.get("actor_field") or "adapter")
        id_fields = tuple(str(value) for value in spec.get("id_fields") or ("id",))
        activity_kind = str(spec.get("activity_kind") or "activity")
        projected: List[Dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            actor = str(record.get(actor_field) or "").strip().lower()
            rejection = record.get("rejection") if isinstance(record.get("rejection"), dict) else {}
            reason = str(rejection.get("reason") or "").strip()
            status = str(record.get("status") or "").strip().lower()
            artifact_refs = self._dormant_activity_artifact_refs(record)
            logs = self._dormant_activity_logs(record)
            error_summary = self._dormant_activity_error_summary(
                record,
                status=status,
                rejection=rejection,
            )
            projected.append(
                {
                    "service": service,
                    "object_type": activity_kind,
                    "object_id": self._dormant_activity_id(record, id_fields),
                    "backend": actor,
                    "status": status,
                    "requested_mode": record.get("requested_mode"),
                    "dispatch_mode": record.get("dispatch_mode"),
                    "production_activation": record.get("production_activation") or "disabled",
                    "rejection_reason": reason or None,
                    "fail_closed_rejection": (
                        status == "rejected"
                        and reason in _DORMANT_FAIL_CLOSED_REASONS
                    ),
                    "gateway_ref": record.get("gateway_ref") if isinstance(record.get("gateway_ref"), dict) else None,
                    "artifact_refs": artifact_refs,
                    "proposal_refs": self._dormant_dict_list(record.get("proposal_refs")),
                    "logs": logs,
                    "error_summary": error_summary,
                    "exit_code": record.get("exit_code"),
                    "updated_at": self._dormant_activity_time(record),
                }
            )
        return projected

    def get_research_oss_preactivation_snapshot(
        self,
        *,
        activity_limit: int = 20,
    ) -> Dict[str, Any]:
        service_status: Dict[str, Dict[str, Any]] = {}
        capability_entries: List[Dict[str, Any]] = []
        activity: List[Dict[str, Any]] = []
        safe_dispatch: Dict[str, List[str]] = {}

        for service, spec in _DORMANT_SERVICE_SPECS.items():
            cap_surface, cap_payload = self._fetch_dormant_json(service, "capabilities_path")
            service_status[service] = dict(cap_surface)
            entries, dispatchers = self._project_dormant_capabilities(service, cap_payload)
            capability_entries.extend(entries)
            if dispatchers:
                safe_dispatch[service] = dispatchers

            activity_path = spec.get("activity_path")
            if activity_path:
                activity_surface, activity_payload = self._fetch_dormant_json(service, "activity_path")
                service_status[service]["activity_status"] = activity_surface.get("status")
                activity.extend(self._project_dormant_activity(service, activity_payload))

            upstream_status_path = spec.get("upstream_status_path")
            if upstream_status_path:
                upstream_surface, upstream_payload = self._fetch_dormant_json(service, "upstream_status_path")
                service_status[service]["upstream_status"] = upstream_surface.get("status")
                if isinstance(upstream_payload, dict):
                    service_status[service]["upstream_reachable"] = bool(upstream_payload.get("reachable"))

        backend_inventory: List[Dict[str, Any]] = []
        entries_by_backend: Dict[str, List[Dict[str, Any]]] = {
            backend: [] for backend in _DORMANT_OSS_BACKENDS
        }
        for entry in capability_entries:
            backend = str(entry.get("backend") or "").strip().lower()
            if backend in entries_by_backend:
                entries_by_backend[backend].append(entry)

        offline_gate_observed = False
        for backend in _DORMANT_OSS_BACKENDS:
            entries = entries_by_backend[backend]
            observed_gate_states = {
                str(entry.get("gate_state") or "unknown").strip().lower()
                for entry in entries
            }
            observed_scopes = {
                str(entry.get("allowed_scope") or "unknown").strip().lower()
                for entry in entries
            }
            backend_offline_ready = (
                "activation_ready" in observed_gate_states
                or _DORMANT_OFFLINE_SCOPE in observed_scopes
                or any(str(entry.get("offline_dispatch") or "").lower() == "enabled" for entry in entries)
                or any(str(entry.get("gateway_routing") or "").lower() == "enabled" for entry in entries)
            )
            offline_gate_observed = offline_gate_observed or backend_offline_ready
            if backend_offline_ready:
                gate_state = "activation_ready"
                allowed_scope = _DORMANT_OFFLINE_SCOPE
                offline_dispatch = "enabled"
            elif entries and observed_gate_states == {"fail_closed"}:
                gate_state = "fail_closed"
                allowed_scope = (
                    "capability_metadata_read_only"
                    if observed_scopes == {"capability_metadata_read_only"}
                    else "mixed"
                )
                offline_dispatch = "disabled"
            elif entries:
                gate_state = "mixed" if len(observed_gate_states) > 1 else next(iter(observed_gate_states))
                allowed_scope = "mixed" if len(observed_scopes) > 1 else next(iter(observed_scopes))
                offline_dispatch = "disabled"
            else:
                gate_state = "unknown"
                allowed_scope = "unknown"
                offline_dispatch = "disabled"
            backend_inventory.append(
                {
                    "backend": backend,
                    "activated": False,
                    "activation_state": (
                        "offline_activation_ready"
                        if backend_offline_ready
                        else "preactivation_only"
                    ),
                    "production_activation": "disabled",
                    "gate_state": gate_state,
                    "allowed_scope": allowed_scope,
                    "offline_dispatch": offline_dispatch,
                    "service_count": len(entries),
                    "services": {str(entry["service"]): entry for entry in entries},
                }
            )

        activity.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        activity = activity[: max(min(activity_limit, 100), 1)]

        artifact_refs: List[Dict[str, Any]] = []
        event_log_count = 0
        stream_log_count = 0
        for row in activity:
            object_ref = {
                "service": row.get("service"),
                "object_type": row.get("object_type"),
                "object_id": row.get("object_id"),
                "backend": row.get("backend"),
            }
            for ref in row.get("artifact_refs") or []:
                if isinstance(ref, dict):
                    artifact_refs.append({**object_ref, **ref})
            for log_entry in row.get("logs") or []:
                if not isinstance(log_entry, dict):
                    continue
                if log_entry.get("source") == "event":
                    event_log_count += 1
                else:
                    stream_log_count += 1

        rejected_activity = [
            row for row in activity if str(row.get("status") or "").lower() == "rejected"
        ]
        failed_activity = [
            row for row in activity if str(row.get("status") or "").lower() in {"failed", "error"}
        ]
        error_rows = [
            row
            for row in activity
            if isinstance(row.get("error_summary"), dict)
            and row["error_summary"].get("has_error")
        ]
        observed_reasons = sorted(
            {
                str(row.get("rejection_reason") or "")
                for row in rejected_activity
                if str(row.get("rejection_reason") or "")
            }
        )
        all_observed_fail_closed = all(
            bool(row.get("fail_closed_rejection")) for row in rejected_activity
        )
        activation_state = (
            "offline_activation_ready"
            if offline_gate_observed
            else "preactivation_only"
        )

        return {
            "surface": "research_oss_activation_ready",
            "surface_aliases": ["research_oss_preactivation"],
            "activation_state": activation_state,
            "production_activation": "disabled",
            "activated": False,
            "offline_gate": "enabled" if offline_gate_observed else "disabled",
            "allowed_scope": (
                _DORMANT_OFFLINE_SCOPE
                if offline_gate_observed
                else "capability_metadata_read_only"
            ),
            "write_paths": {
                "training_dispatch": "disabled",
                "paper_canary_live": "disabled",
                "registry_writes": "disabled",
                "governance_writes": "disabled",
                "broker_execution": "disabled",
                "capital_binding": "disabled",
            },
            "operator_controls": {
                "read_operations": [
                    "capability_inventory",
                    "gate_state",
                    "run_history",
                    "artifact_refs",
                    "logs",
                    "error_summary",
                ],
                "activation_commands": "not_exposed",
                "blocked_commands": {
                    "enable_production_activation": "governance_required_not_available_in_bff",
                    "promote_to_registry": "registry_governance_path_required_not_available_in_bff",
                    "paper_canary_live": "deployment_governance_path_required_not_available_in_bff",
                    "broker_execution": "execution_plane_gate_required_not_available_in_bff",
                },
            },
            "backend_inventory": backend_inventory,
            "safe_dispatch": safe_dispatch,
            "run_history": activity,
            "activity": activity,
            "artifact_refs": artifact_refs,
            "log_summary": {
                "event_log_count": event_log_count,
                "stream_log_count": stream_log_count,
                "activity_with_logs": len([row for row in activity if row.get("logs")]),
            },
            "error_summary": {
                "activity_with_errors": len(error_rows),
                "rejection_count": len(rejected_activity),
                "failed_count": len(failed_activity),
                "gateway_error_count": len(
                    [
                        error
                        for row in error_rows
                        for error in row.get("error_summary", {}).get("errors", [])
                        if isinstance(error, dict) and error.get("kind") == "gateway"
                    ]
                ),
            },
            "rejection_verification": {
                "verification_state": (
                    "evidence_observed"
                    if rejected_activity
                    else "no_rejection_activity_observed"
                ),
                "observed_rejection_count": len(rejected_activity),
                "observed_reasons": observed_reasons,
                "all_observed_rejections_fail_closed": all_observed_fail_closed,
                "expected_fail_closed_reasons": sorted(_DORMANT_FAIL_CLOSED_REASONS),
            },
            "service_status": service_status,
        }

    @staticmethod
    def _openclaw_client() -> OpenClawOpsClient:
        return OpenClawOpsClient()

    @staticmethod
    def _openclaw_error_surface(exc: OpenClawOpsClientError) -> Dict[str, Any]:
        surface = exc.to_surface()
        return {key: value for key, value in surface.items() if value is not None}

    def _fetch_openclaw_surface(
        self,
        surface_key: str,
        call: Any,
    ) -> tuple[Dict[str, Any], Any]:
        try:
            payload = call()
        except OpenClawOpsClientError as exc:
            return self._openclaw_error_surface(exc), None
        return {
            "status": "ok",
            "source": "service_client",
            "surface": surface_key,
        }, payload

    @staticmethod
    def _openclaw_gate_enabled(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        normalized = str(value or "").strip().lower()
        return normalized in {"1", "true", "yes", "on", "enabled", "active", "available"}

    @classmethod
    def _project_openclaw_gate_state(cls, capabilities: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        activation_gates = capabilities.get("activation_gates")
        if not isinstance(activation_gates, dict):
            activation_gates = {}
        gates: Dict[str, Dict[str, Any]] = {}
        for gate_name, defaults in _OPENCLAW_GATE_FIELDS.items():
            raw_state = capabilities.get(gate_name)
            state = str(raw_state or "deferred").strip().lower()
            enabled = cls._openclaw_gate_enabled(raw_state)
            gate_reason = (
                "enabled_by_adapter"
                if enabled
                else f"{activation_gates.get(gate_name) or defaults['activation_gate']} is not enabled"
            )
            gates[gate_name] = {
                "state": state,
                "enabled": enabled,
                "activation_gate": activation_gates.get(gate_name) or defaults["activation_gate"],
                "allowed_scope": "enabled_by_adapter" if enabled else defaults["allowed_scope"],
                "gate_reason": gate_reason,
                "bff_activation_command": "not_exposed",
            }
        return gates

    @staticmethod
    def _project_openclaw_capabilities(capabilities: Dict[str, Any]) -> Dict[str, Any]:
        upstream = capabilities.get("upstream") if isinstance(capabilities.get("upstream"), dict) else {}
        return {
            "adapter_version": capabilities.get("adapter_version"),
            "activation_state": capabilities.get("activation_state") or "unknown",
            "session_lifecycle_state": capabilities.get("session_lifecycle_state") or "unknown",
            "fail_closed": bool(capabilities.get("fail_closed", True)),
            "supported_session_types": list(capabilities.get("supported_session_types") or []),
            "minimum_runtime_contract": capabilities.get("minimum_runtime_contract") or {},
            "upstream_status": upstream.get("status"),
            "upstream_error_code": upstream.get("error_code"),
        }

    @staticmethod
    def _project_openclaw_upstream(
        payload: Any,
        surface: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {
                "status": "unavailable",
                "reachable": False,
                "reason": surface.get("reason") or "upstream_status_unavailable",
                "details": surface,
            }
        details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
        reachable = bool(payload.get("reachable"))
        reason = (
            details.get("reason")
            or payload.get("reason")
            or surface.get("reason")
            or (None if reachable else "upstream_unreachable")
        )
        return {
            "status": "ok" if reachable else "degraded",
            "upstream_url": payload.get("upstream_url"),
            "reachable": reachable,
            "reason": reason,
            "http_status": details.get("http_status"),
            "probe": details.get("probe"),
            "details": details,
        }

    @staticmethod
    def _project_openclaw_session(record: Dict[str, Any]) -> Dict[str, Any]:
        state = str(record.get("state") or record.get("status") or "unknown").strip().lower()
        audit_log = record.get("audit_log") if isinstance(record.get("audit_log"), list) else []
        context_bundle = record.get("context_bundle") if isinstance(record.get("context_bundle"), dict) else {}
        last_error = record.get("last_error") if isinstance(record.get("last_error"), dict) else None
        cancelable = state in {"pending", "active", "lost", "cancel_requested"}
        return {
            "session_id": record.get("session_id") or record.get("id"),
            "agent_id": record.get("agent_id"),
            "session_type": record.get("session_type"),
            "state": state,
            "operator_id": record.get("operator_id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "upstream_session_id": record.get("upstream_session_id"),
            "degraded": state in {"lost", "failed"} or bool(last_error),
            "last_error": last_error,
            "audit_count": len(audit_log),
            "latest_audit_event": audit_log[-1] if audit_log else None,
            "context_keys": sorted(str(key) for key in context_bundle.keys()),
            "allowedActions": {
                "canCancel": cancelable,
                "canInvokeTool": False,
                "canTriggerWorkflow": False,
            },
        }

    @staticmethod
    def _project_openclaw_invocation(entry: Dict[str, Any]) -> Dict[str, Any]:
        error = entry.get("error") if isinstance(entry.get("error"), dict) else None
        return {
            "at": entry.get("at"),
            "request_type": entry.get("request_type"),
            "trace_id": entry.get("trace_id"),
            "operator_id": entry.get("operator_id"),
            "session_id": entry.get("session_id"),
            "tool_name": entry.get("tool_name"),
            "workflow_ref": entry.get("workflow_ref"),
            "policy_decision": entry.get("policy_decision"),
            "policy_class": entry.get("policy_class"),
            "policy_reason": entry.get("policy_reason"),
            "outcome": entry.get("outcome"),
            "retryable": bool(error.get("retryable")) if error else False,
            "error": (
                {
                    "error_code": error.get("error_code"),
                    "message": error.get("message"),
                    "status_code": error.get("status_code"),
                    "retryable": bool(error.get("retryable")),
                }
                if error
                else None
            ),
            "args_hash": entry.get("args_hash"),
            "context_hash": entry.get("context_hash"),
        }

    @staticmethod
    def _openclaw_counts_by_key(rows: List[Dict[str, Any]], key: str) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for row in rows:
            value = str(row.get(key) or "unknown")
            counts[value] = counts.get(value, 0) + 1
        return counts

    def get_openclaw_ops_snapshot(
        self,
        *,
        session_limit: int = 25,
        audit_limit: int = 20,
        operator_id: Optional[str] = None,
        state: Optional[str] = None,
        agent_id: Optional[str] = None,
        effective_tools_session_id: Optional[str] = None,
        requesting_operator_id: Optional[str] = None,
        effective_tools_mode: Optional[str] = None,
        requesting_operator_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        bounded_session_limit = max(min(session_limit, 100), 1)
        bounded_audit_limit = max(min(audit_limit, 100), 1)
        client = self._openclaw_client()

        service_status: Dict[str, Dict[str, Any]] = {}

        cap_surface, cap_payload = self._fetch_openclaw_surface(
            "openclaw_capabilities",
            client.get_capabilities,
        )
        service_status["openclaw_capabilities"] = cap_surface
        capabilities = cap_payload if isinstance(cap_payload, dict) else {}

        upstream_surface, upstream_payload = self._fetch_openclaw_surface(
            "openclaw_upstream_status",
            client.get_upstream_status,
        )
        service_status["openclaw_upstream_status"] = upstream_surface

        session_surface, session_payload = self._fetch_openclaw_surface(
            "openclaw_session_lifecycle",
            lambda: client.list_lifecycle_sessions(operator_id=operator_id, state=state),
        )
        service_status["openclaw_session_lifecycle"] = session_surface

        policy_surface, policy_payload = self._fetch_openclaw_surface(
            "openclaw_tool_policy",
            client.get_tool_policy,
        )
        service_status["openclaw_tool_policy"] = policy_surface

        audit_surface, audit_payload = self._fetch_openclaw_surface(
            "openclaw_invocation_audit",
            lambda: client.list_invocation_audit(
                operator_id=operator_id,
                limit=bounded_audit_limit,
            ),
        )
        service_status["openclaw_invocation_audit"] = audit_surface

        effective_tools: Optional[Dict[str, Any]] = None
        if agent_id and requesting_operator_id:
            tools_surface, tools_payload = self._fetch_openclaw_surface(
                "openclaw_effective_tools",
                lambda: client.list_effective_tools(
                    agent_id=agent_id,
                    operator_id=requesting_operator_id,
                    session_id=effective_tools_session_id,
                    mode=effective_tools_mode,
                    operator_role=requesting_operator_role,
                ),
            )
            service_status["openclaw_effective_tools"] = tools_surface
            effective_tools = tools_payload if isinstance(tools_payload, dict) else None

        raw_sessions = []
        if isinstance(session_payload, dict) and isinstance(session_payload.get("sessions"), list):
            raw_sessions = [
                dict(item)
                for item in session_payload["sessions"]
                if isinstance(item, dict)
            ]
        sessions = [
            self._project_openclaw_session(item)
            for item in raw_sessions[:bounded_session_limit]
        ]

        raw_invocations = []
        if isinstance(audit_payload, dict) and isinstance(audit_payload.get("entries"), list):
            raw_invocations = [
                dict(item)
                for item in audit_payload["entries"]
                if isinstance(item, dict)
            ]
        invocations = [self._project_openclaw_invocation(item) for item in raw_invocations]

        session_counts = self._openclaw_counts_by_key(sessions, "state")
        invocation_outcomes = self._openclaw_counts_by_key(invocations, "outcome")
        policy_decisions = self._openclaw_counts_by_key(invocations, "policy_decision")

        upstream = self._project_openclaw_upstream(upstream_payload, upstream_surface)
        gate_state = self._project_openclaw_gate_state(capabilities)

        degraded_reasons: List[str] = []
        for surface_key, surface in service_status.items():
            if surface.get("status") == "ok":
                continue
            reason = str(surface.get("reason") or surface.get("message") or surface_key)
            degraded_reasons.append(f"{surface_key}:{reason}")
        if not upstream.get("reachable"):
            degraded_reasons.append(f"openclaw_upstream:{upstream.get('reason') or 'unreachable'}")
        cap_upstream = capabilities.get("upstream") if isinstance(capabilities.get("upstream"), dict) else {}
        if cap_upstream.get("status") == "degraded":
            degraded_reasons.append(
                f"openclaw_capabilities_upstream:{cap_upstream.get('error_code') or 'degraded'}"
            )
        for session in sessions:
            if session.get("degraded"):
                last_error = session.get("last_error") if isinstance(session.get("last_error"), dict) else {}
                degraded_reasons.append(
                    "openclaw_session:"
                    f"{session.get('session_id')}:{last_error.get('error_code') or session.get('state')}"
                )

        unique_reasons = sorted({reason for reason in degraded_reasons if reason})
        surface_states = [surface.get("status") for surface in service_status.values()]
        if surface_states and all(state == "unavailable" for state in surface_states):
            overall_status = "unavailable"
        elif any(state != "ok" for state in surface_states) or unique_reasons:
            overall_status = "degraded"
        else:
            overall_status = "ok"

        return {
            "surface": "openclaw_ops",
            "surface_aliases": ["openclaw_tool_workflow_bridge"],
            "overall_status": overall_status,
            "activation": self._project_openclaw_capabilities(capabilities),
            "gate_state": gate_state,
            "production_activation": "disabled",
            "upstream": upstream,
            "session_lifecycle": {
                "status": session_surface.get("status"),
                "count": len(sessions),
                "state_counts": session_counts,
                "sessions": sessions,
                "degraded_session_count": len([s for s in sessions if s.get("degraded")]),
                "filters": {"operator_id": operator_id, "state": state},
            },
            "tool_workflow": {
                "policy": policy_payload if isinstance(policy_payload, dict) else {},
                "effective_tools": effective_tools,
                "audit": {
                    "status": audit_surface.get("status"),
                    "count": len(invocations),
                    "outcome_counts": invocation_outcomes,
                    "policy_decision_counts": policy_decisions,
                    "entries": invocations,
                },
                "bridge_posture": {
                    "policy_state": (
                        "adapter_enforcing"
                        if policy_surface.get("status") == "ok"
                        else "degraded"
                    ),
                    "unknown_tools": "fail_closed",
                    "disallowed_tools": "fail_closed",
                    "workflow_triggers": "adapter_policy_checked",
                    "bff_tool_invocation_commands": "not_exposed",
                    "bff_workflow_trigger_commands": "not_exposed",
                },
            },
            "operator_controls": {
                "read_operations": [
                    "upstream_status",
                    "capability_inventory",
                    "session_lifecycle",
                    "tool_policy",
                    "tool_workflow_audit",
                    "degraded_reason",
                ],
                "commands": {
                    "create_session": {
                        "endpoint": "POST /api/v1/operator/openclaw/sessions",
                        "requires_auth": True,
                        "requires_idempotency_key": True,
                        "adapter_route": "POST /api/openclaw-adapter/lifecycle/sessions",
                    },
                    "cancel_session": {
                        "endpoint": "POST /api/v1/operator/openclaw/sessions/{session_id}/cancel",
                        "requires_auth": True,
                        "requires_idempotency_key": True,
                        "adapter_route": "POST /api/openclaw-adapter/lifecycle/sessions/{session_id}/cancel",
                    },
                    "invoke_tool": "not_exposed_by_bff",
                    "trigger_workflow": "not_exposed_by_bff",
                },
                "blocked_commands": {
                    "enable_paper_adapter": "activation_gate_required_not_available_in_bff",
                    "enable_canary_adapter": "activation_gate_required_not_available_in_bff",
                    "enable_live_adapter": "activation_gate_required_not_available_in_bff",
                    "enable_broker_execution": "execution_gate_required_not_available_in_bff",
                    "enable_capital_binding": "capital_binding_gate_required_not_available_in_bff",
                },
            },
            "allowedActions": {
                "canCreateSession": client.configured and session_surface.get("status") != "unavailable",
                "canInvokeTool": False,
                "canTriggerWorkflow": False,
                "canEnablePaper": False,
                "canEnableCanary": False,
                "canEnableLive": False,
            },
            "degradation": {
                "status": overall_status,
                "reasons": unique_reasons,
            },
            "service_status": service_status,
        }

    def get_openclaw_broker_adapter_readiness(self) -> Dict[str, Any]:
        """Project broker adapter capability states and gate reasons.

        Returns sandbox/paper/canary/live capability states with gate reason text,
        without claiming live activation.  Always fail-closed on live/canary paths.
        """
        client = self._openclaw_client()
        surface, payload = self._fetch_openclaw_surface(
            "openclaw_broker_capabilities",
            client.get_broker_capabilities,
        )
        if not isinstance(payload, dict):
            return {
                "surface": "openclaw_broker_adapter_readiness",
                "overall_status": "unavailable",
                "sandbox_adapter_state": "unknown",
                "paper_adapter_state": "unknown",
                "canary_adapter_state": "fail_closed",
                "live_adapter_state": "fail_closed",
                "live_execution_enabled": False,
                "canary_execution_enabled": False,
                "is_real_capital": False,
                "is_real_order": False,
                "gate_reason": surface.get("reason") or "broker_capabilities_unavailable",
                "service_status": surface,
            }

        def _state(key: str, default: str = "deferred") -> str:
            return str(payload.get(key) or default).strip().lower()

        def _gate_reason(state: str, gate_env: str, fail_closed: bool = False) -> str:
            if fail_closed:
                return "fail_closed_explicit_gate_required"
            if state in {"enabled", "active"}:
                return "enabled_by_adapter"
            return f"{gate_env} is not enabled"

        sandbox_state = _state("sandbox_adapter_state", "activation_ready")
        paper_state = _state("paper_adapter_state", "gated")
        canary_state = "fail_closed"
        live_state = "fail_closed"

        return {
            "surface": "openclaw_broker_adapter_readiness",
            "overall_status": "ok" if surface.get("status") == "ok" else surface.get("status", "degraded"),
            "sandbox_adapter_state": sandbox_state,
            "sandbox_gate": payload.get("sandbox_gate") or "OPENCLAW_PAPER_ADAPTER_ENABLED",
            "sandbox_gate_reason": _gate_reason(sandbox_state, str(payload.get("sandbox_gate") or "")),
            "paper_adapter_state": paper_state,
            "paper_adapter_gate": payload.get("paper_adapter_gate") or "OPENCLAW_PAPER_ADAPTER_ENABLED",
            "paper_gate_reason": _gate_reason(paper_state, str(payload.get("paper_adapter_gate") or "")),
            "canary_adapter_state": canary_state,
            "canary_adapter_gate": payload.get("canary_adapter_gate") or "OPENCLAW_CANARY_ADAPTER_ENABLED",
            "canary_gate_reason": _gate_reason(canary_state, "", fail_closed=True),
            "live_adapter_state": live_state,
            "live_adapter_gate": payload.get("live_adapter_gate") or "OPENCLAW_LIVE_ADAPTER_ENABLED",
            "live_gate_reason": _gate_reason(live_state, "", fail_closed=True),
            "live_execution_enabled": False,
            "canary_execution_enabled": False,
            "is_real_capital": False,
            "is_real_order": False,
            "broker_sidecar_configured": bool(payload.get("broker_sidecar_configured")),
            "runtime_manager_configured": bool(payload.get("runtime_manager_configured")),
            "bff_activation_command": "not_exposed",
            "note": (
                "Live and canary broker execution remain fail-closed. "
                "Sandbox/paper state reflects adapter configuration only. "
                "Activation requires explicit gate enablement outside the BFF."
            ),
            "service_status": surface,
        }

    def _consultation_data_dir(self) -> Optional[Path]:
        for env_name in _CONSULTATION_DATA_DIR_ENVS:
            raw = os.getenv(env_name, "").strip()
            if raw:
                return Path(raw)
        return None

    def _consultation_store(self) -> Optional[ConsultationStore]:
        data_dir = self._consultation_data_dir()
        if data_dir is None:
            return None
        return ConsultationStore(str(data_dir))

    def _consultation_client(self) -> Optional[ConsultationServiceClient]:
        if not ConsultationServiceClient.configured():
            return None
        if self._consultation_client_instance is None:
            self._consultation_client_instance = ConsultationServiceClient(
                timeout_seconds=_service_timeout_seconds(),
            )
        return self._consultation_client_instance

    def _consultation_service_dataset_available(self, dataset: str) -> bool:
        return dataset in _CONSULTATION_SERVICE_DATASETS and (
            self._consultation_client() is not None or self._consultation_data_dir() is not None
        )

    @staticmethod
    def _service_context_refs_to_bff(req: Dict[str, Any]) -> List[Dict[str, str]]:
        metadata = req.get("metadata") if isinstance(req.get("metadata"), dict) else {}
        bff_refs = metadata.get("bff_context_refs")
        if isinstance(bff_refs, list):
            return [
                {"type": str(item.get("type") or ""), "id": str(item.get("id") or "")}
                for item in bff_refs
                if isinstance(item, dict) and item.get("type") and item.get("id")
            ]
        refs: List[Dict[str, str]] = []
        for raw_ref in req.get("context_refs") or []:
            text = str(raw_ref or "")
            if ":" not in text:
                continue
            ref_type, ref_id = text.split(":", 1)
            if ref_type and ref_id:
                refs.append({"type": ref_type, "id": ref_id})
        return refs

    @staticmethod
    def _service_request_status(req: Dict[str, Any]) -> str:
        status = str(req.get("status") or "").strip().lower()
        return _SERVICE_TO_BFF_REQUEST_STATUS.get(status, status or "created")

    @classmethod
    def _project_service_request_record(cls, req: Dict[str, Any]) -> Dict[str, Any]:
        metadata = req.get("metadata") if isinstance(req.get("metadata"), dict) else {}
        bff_priority = metadata.get("bff_priority") or req.get("priority")
        consultation_type = req.get("consultation_type") or metadata.get("consultation_type") or req.get("request_type")
        status = cls._service_request_status(req)
        request_to_session_status = (
            req.get("request_to_session_status")
            or metadata.get("request_to_session_status")
            or ("session_completed" if status == "completed" else "pending_session")
        )
        return {
            "request_id": req.get("request_id"),
            "status": status,
            "from_persona_id": req.get("from_persona_id"),
            "target_type": req.get("target_type"),
            "target_ref": req.get("target_ref") or req.get("target_id"),
            "task": req.get("task") or metadata.get("task") or "",
            "context_refs": cls._service_context_refs_to_bff(req),
            "priority": bff_priority,
            "consultation_type": consultation_type,
            "created_at": req.get("created_at"),
            "completed_at": req.get("completed_at"),
            "canceled_at": req.get("canceled_at"),
            "linked_session_id": req.get("linked_session_id") or metadata.get("linked_session_id"),
            "request_to_session_status": request_to_session_status,
            "session_handoff_note": (
                req.get("session_handoff_note")
                or metadata.get("session_handoff_note")
                or "Request is served from the consultation service lifecycle store."
            ),
            "created_by": (req.get("requested_by") or {}).get("actor_id"),
            "service_request_type": req.get("request_type"),
            "service_trace_id": req.get("trace_id"),
            "service_evidence_refs": list(req.get("evidence_refs") or []),
        }

    @staticmethod
    def _service_evidence_ref(ref_id: str) -> Dict[str, Any]:
        return {
            "id": ref_id,
            "type": "evidence_link",
            "evidence_type": "consultation_evidence",
            "artifact_ref": ref_id,
            "description": ref_id,
            "link": f"/evidence/{ref_id}",
        }

    def _project_service_session_records_from_data(
        self,
        request_records: List[Dict[str, Any]],
        handoff_records: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        sessions: Dict[str, Dict[str, Any]] = {}
        handoffs_by_request: Dict[str, List[Dict[str, Any]]] = {}
        for handoff in handoff_records:
            handoffs_by_request.setdefault(str(handoff.get("request_id") or ""), []).append(handoff)

        for req in request_records:
            metadata = req.get("metadata") if isinstance(req.get("metadata"), dict) else {}
            consult = dict(metadata.get("consultation") or {})
            request_id = str(req.get("request_id") or "")
            linked_session_id = (
                req.get("linked_session_id")
                or consult.get("requester_session_id")
                or consult.get("root_session_id")
                or request_id
            )
            service_handoffs = sorted(
                handoffs_by_request.get(request_id, []),
                key=lambda item: str(item.get("created_at") or ""),
                reverse=True,
            )
            latest_handoff = service_handoffs[0] if service_handoffs else None

            evidence_refs = consult.get("evidence_refs")
            if not evidence_refs:
                evidence_refs = [
                    self._service_evidence_ref(str(ref_id))
                    for ref_id in req.get("evidence_refs") or []
                    if str(ref_id or "").strip()
                ]
            consult.setdefault("consultation_type", req.get("consultation_type") or req.get("request_type"))
            consult.setdefault("requester_session_id", linked_session_id)
            consult.setdefault("responder_session_ids", [])
            consult.setdefault("committee_session_ids", [])
            consult.setdefault("outcome", self._service_request_status(req))
            consult.setdefault("evidence_refs", evidence_refs)
            consult.setdefault(
                "synthesis_summary",
                {
                    "outcome": consult.get("outcome"),
                    "rationale_ref": consult.get("rationale_ref"),
                    "evidence_refs": [
                        item.get("id") if isinstance(item, dict) else item
                        for item in evidence_refs
                    ],
                },
            )
            if latest_handoff:
                consult["service_handoff"] = {
                    "handoff_id": latest_handoff.get("handoff_id"),
                    "target_gate": latest_handoff.get("target_gate"),
                    "evidence_refs": list(latest_handoff.get("evidence_refs") or []),
                    "audit_refs": list(latest_handoff.get("audit_refs") or []),
                    "status": latest_handoff.get("status"),
                }

            sessions[linked_session_id] = {
                "id": linked_session_id,
                "session_id": linked_session_id,
                "persona_id": req.get("from_persona_id") or (req.get("requested_by") or {}).get("actor_id"),
                "session_type": "consult",
                "status": _SERVICE_TO_SESSION_STATUS.get(str(req.get("status") or ""), "active"),
                "started_at": req.get("created_at"),
                "ended_at": req.get("completed_at") or req.get("canceled_at"),
                "capability_snapshot_id": consult.get("capability_snapshot_id"),
                "trace_id": req.get("trace_id"),
                "request_id": request_id,
                "context_bundle_ref": consult.get("context_bundle_ref"),
                "task_ref": req.get("task"),
                "runtime_binding_id": consult.get("runtime_binding_id"),
                "deployment_stage": consult.get("deployment_stage"),
                "capital_pool_id": consult.get("capital_pool_id"),
                "metadata": {"consultation": consult},
            }

            for participant in consult.get("committee_participants") or []:
                if not isinstance(participant, dict):
                    continue
                session_id = str(participant.get("session_id") or participant.get("participant_id") or "").strip()
                if not session_id:
                    continue
                sessions[session_id] = {
                    "id": session_id,
                    "session_id": session_id,
                    "persona_id": participant.get("persona_id") or participant.get("participant_ref"),
                    "session_type": "committee",
                    "status": participant.get("status") or "active",
                    "started_at": participant.get("started_at") or req.get("created_at"),
                    "ended_at": participant.get("ended_at"),
                    "capability_snapshot_id": participant.get("capability_snapshot_id"),
                    "trace_id": participant.get("trace_id") or req.get("trace_id"),
                    "request_id": request_id,
                    "context_bundle_ref": participant.get("context_bundle_ref") or consult.get("context_bundle_ref"),
                    "task_ref": req.get("task"),
                    "runtime_binding_id": participant.get("runtime_binding_id") or consult.get("runtime_binding_id"),
                    "deployment_stage": participant.get("deployment_stage") or consult.get("deployment_stage"),
                    "capital_pool_id": participant.get("capital_pool_id") or consult.get("capital_pool_id"),
                    "metadata": {
                        "consultation": {
                            "consultation_type": consult.get("consultation_type"),
                            "root_session_id": linked_session_id,
                            "committee_ref": consult.get("committee_ref"),
                            "participant_status": participant.get("participant_status") or participant.get("status"),
                            "outcome_signal": participant.get("outcome_signal"),
                            "role": participant.get("role") or "committee_participant",
                            "rationale_ref": participant.get("rationale_ref"),
                        }
                    },
                }

        return list(sessions.values())

    def _project_service_session_records(self, store: ConsultationStore) -> List[Dict[str, Any]]:
        return self._project_service_session_records_from_data(
            [_model_to_data(request) for request in store.list_requests()],
            [_model_to_data(handoff) for handoff in store.list_handoffs()],
        )

    @staticmethod
    def _project_service_transcript_record(transcript: Dict[str, Any]) -> Dict[str, Any]:
        transcript_id = str(transcript.get("transcript_id") or f"tr-{transcript.get('session_id')}")
        events: List[Dict[str, Any]] = []
        for event in transcript.get("events") or []:
            if not isinstance(event, dict):
                continue
            actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
            content = event.get("content") if isinstance(event.get("content"), dict) else {}
            events.append(
                {
                    "transcript_id": transcript_id,
                    "session_id": event.get("session_id") or transcript.get("session_id"),
                    "event_id": event.get("event_id"),
                    "sequence_no": event.get("sequence_no"),
                    "parent_event_id": event.get("parent_event_id"),
                    "event_type": event.get("event_type"),
                    "event_time": event.get("event_time"),
                    "ingest_time": event.get("ingest_time") or event.get("event_time"),
                    "actor": {
                        "actor_type": actor.get("actor_type"),
                        "actor_id": actor.get("actor_id"),
                        "display_name": actor.get("display_name"),
                        "role": actor.get("role") or content.get("actor_role"),
                    },
                    "content": {
                        "format": content.get("format") or "json",
                        "text": content.get("text"),
                        **{key: value for key, value in content.items() if key not in {"format", "text"}},
                    },
                    "evidence_refs": list(event.get("evidence_refs") or []),
                    "visibility": event.get("visibility") or "committee",
                    "redaction": event.get("redaction") or {"is_redacted": False, "reason": None},
                    "meta": event.get("meta") or {"source": "consultation-service", "hash": None},
                }
            )
        return {
            "transcript_id": transcript_id,
            "session_id": transcript.get("session_id") or transcript.get("request_id"),
            "linked_request_id": transcript.get("request_id") or transcript.get("linked_request_id"),
            "events": events,
        }

    def _project_service_memo_record(
        self,
        memo: Dict[str, Any],
        request_lookup: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        memo_id = str(memo.get("memo_id") or "")
        request_id = str(memo.get("request_id") or "")
        request = (request_lookup or {}).get(request_id)
        if request is None:
            client = self._consultation_client()
            if client is not None and request_id:
                try:
                    request = client.get_request(request_id)
                except ConsultationClientError:
                    request = None
        if request is None:
            store = self._consultation_store()
        else:
            store = None
        if request is None and store is not None and request_id:
            found = store.get_request(request_id)
            request = _model_to_data(found) if found else None
        request_metadata = request.get("metadata") if isinstance((request or {}).get("metadata"), dict) else {}
        consult = request_metadata.get("consultation") if isinstance(request_metadata.get("consultation"), dict) else {}
        linked_session_id = (
            (request or {}).get("linked_session_id")
            or consult.get("requester_session_id")
            or request_id
        )
        findings = [item for item in memo.get("findings") or [] if isinstance(item, dict)]
        evidence_ref_ids = []
        for finding in findings:
            evidence_ref_ids.extend(str(ref_id) for ref_id in finding.get("evidence_refs") or [] if str(ref_id or "").strip())
        evidence_ref_ids = list(dict.fromkeys(evidence_ref_ids))
        recommendation = memo.get("recommendation")
        recommendations = [
            finding.get("recommendation")
            for finding in findings
            if finding.get("recommendation")
        ] or ([recommendation] if recommendation else [])
        return {
            "memo_id": memo_id,
            "memo_type": "red_team" if memo.get("memo_type") == "redteam_report" else memo.get("memo_type"),
            "status": str(memo.get("status") or "").lower(),
            "lifecycle_state": str(memo.get("status") or "").lower(),
            "author_ref": memo.get("author_ref"),
            "linked_request_id": request_id,
            "linked_session_id": linked_session_id,
            "session_to_memo_mapping": {
                "mapping_id": f"map-{memo_id}" if memo_id else None,
                "source_session_id": linked_session_id,
                "transcript_id": f"tr-{linked_session_id}" if linked_session_id else None,
                "transcript_version": None,
                "memo_id": memo_id,
                "memo_type": "red_team" if memo.get("memo_type") == "redteam_report" else memo.get("memo_type"),
                "created_by": {
                    "actor_type": memo.get("author_type"),
                    "actor_id": memo.get("author_ref"),
                },
                "evidence_refs": evidence_ref_ids,
                "mapping_status": "active",
                "created_at": memo.get("created_at"),
            },
            "summary": memo.get("summary"),
            "recommendations": recommendations,
            "evidence_refs": [self._service_evidence_ref(ref_id) for ref_id in evidence_ref_ids],
            "published_at": memo.get("published_at"),
            "created_at": memo.get("created_at"),
            "supersedes_memo_id": None,
            "superseded_by_memo_id": None,
            "surface_state": "ok",
            "governance_target": {
                "target_type": memo.get("target_type"),
                "target_id": memo.get("target_id"),
                "deployment_plan_id": memo.get("target_id") if memo.get("target_type") == "deployment_plan" else None,
                "artifact_id": memo.get("target_id") if memo.get("target_type") == "artifact" else None,
                "strategy_id": memo.get("target_id") if memo.get("target_type") == "strategy" else None,
            },
            "suppressed": False,
            "withdrawn": False,
            "active_governance_review_id": None,
        }

    def _consultation_service_records(self, dataset: str) -> Optional[List[Dict[str, Any]]]:
        client = self._consultation_client()
        if client is not None:
            try:
                if dataset == "consult_requests":
                    return [
                        self._project_service_request_record(request)
                        for request in client.list_requests()
                    ]
                if dataset == "consultation_sessions":
                    return self._project_service_session_records_from_data(
                        client.list_requests(),
                        client.list_handoffs(),
                    )
                if dataset == "consult_transcripts":
                    return [
                        self._project_service_transcript_record(transcript)
                        for transcript in client.list_transcripts()
                    ]
                if dataset == "consult_memos":
                    requests = {
                        str(request.get("request_id") or ""): request
                        for request in client.list_requests()
                        if isinstance(request, dict)
                    }
                    return [
                        self._project_service_memo_record(memo, requests)
                        for memo in client.list_memos()
                    ]
            except ConsultationClientError:
                return None

        store = self._consultation_store()
        if store is None:
            return None
        if dataset == "consult_requests":
            return [
                self._project_service_request_record(_model_to_data(request))
                for request in store.list_requests()
            ]
        if dataset == "consultation_sessions":
            return self._project_service_session_records(store)
        if dataset == "consult_transcripts":
            return [
                self._project_service_transcript_record(_model_to_data(transcript))
                for transcript in store.list_transcripts()
            ]
        if dataset == "consult_memos":
            return [
                self._project_service_memo_record(_model_to_data(memo))
                for memo in store.list_memos()
            ]
        return None

    def _load_or_seed(self) -> None:
        if self._path.exists():
            raw = self._path.read_text().strip()
            if raw:
                self._data = json.loads(raw)
                for dataset, key in self._LOCAL_DATA_KEYS.items():
                    if key in self._data:
                        self._local_overlay_write_datasets.add(dataset)
                if self._allow_local_snapshot_fallback and self._backfill_local_contract_defaults():
                    self._save()
                return
        if self._allow_local_snapshot_fallback:
            self._data = _default_read_data()
            self._save()
            return
        self._data = {}

    def _backfill_local_contract_defaults(self) -> bool:
        changed = False
        default_data = _default_read_data()
        fixture_datasets = _load_default_fixture_pack_datasets()
        # When the snapshot explicitly provides an "incidents" key (even if empty),
        # preserve it as-is and do not inject fixture-pack incident records.
        if "incidents" in self._data:
            fixture_datasets.pop("incidents", None)
        for explicit_agora_dataset in ("agora_signals", "agora_sessions", "agora_watchlist"):
            if explicit_agora_dataset in self._data:
                fixture_datasets.pop(explicit_agora_dataset, None)
        if _merge_default_fixture_pack(self._data, fixture_datasets):
            changed = True
        deployment_plans = self._data.get("deployment_plans")
        default_plans = default_data.get("deployment_plans", {})
        approval_decisions = self._data.get("approval_decisions")
        default_approval_decisions = default_data.get("approval_decisions", {})
        evolution_decisions = self._data.get("evolution_decisions")
        default_decisions = default_data.get("evolution_decisions", {})
        rollback_reviews = self._data.get("rollback_reviews")
        default_rollback_reviews = default_data.get("rollback_reviews", {})
        governance_review_queue_items = self._data.get("governance_review_queue_items")
        default_governance_review_queue_items = default_data.get(
            "governance_review_queue_items",
            {},
        )
        approval_queue_items = self._data.get("approval_queue_items")
        default_approval_queue_items = default_data.get("approval_queue_items", {})
        deployment_diffs = self._data.get("deployment_diffs")
        default_deployment_diffs = default_data.get("deployment_diffs", {})
        paper_live_drift_reports = self._data.get("paper_live_drift_reports")
        default_paper_live_drift_reports = default_data.get("paper_live_drift_reports", {})
        registry_entries = self._data.get("registry_entries")
        default_registry_entries = default_data.get("registry_entries", {})
        research_analyses = self._data.get("research_analyses")
        default_research_analyses = default_data.get("research_analyses", {})
        research_artifacts = self._data.get("research_artifacts")
        default_research_artifacts = default_data.get("research_artifacts", {})
        research_notes = self._data.get("research_notes")
        default_research_notes = default_data.get("research_notes", {})
        evidence_refs = self._data.get("evidence_refs")
        default_evidence_refs = default_data.get("evidence_refs", {})
        insight_cards = self._data.get("insight_cards")
        default_insight_cards = default_data.get("insight_cards", {})
        institutional_memory_entries = self._data.get("institutional_memory_entries")
        default_institutional_memory_entries = default_data.get("institutional_memory_entries", {})
        strategy_specs = self._data.get("strategy_specs")
        default_strategy_specs = default_data.get("strategy_specs", {})
        research_search_documents = self._data.get("research_search_documents")
        default_research_search_documents = default_data.get("research_search_documents", {})
        research_search_index = self._data.get("research_search_index")
        default_research_search_index = default_data.get("research_search_index", {})
        inspiration_graphs = self._data.get("inspiration_graphs")
        default_inspiration_graphs = default_data.get("inspiration_graphs", {})
        trainer_previews = self._data.get("trainer_previews")
        default_trainer_previews = default_data.get("trainer_previews", {})
        consult_memos = self._data.get("consult_memos")
        default_consult_memos = default_data.get("consult_memos", {})

        if isinstance(deployment_plans, dict):
            for plan_id, default_plan in default_plans.items():
                existing_plan = deployment_plans.get(plan_id)
                if not isinstance(existing_plan, dict):
                    continue
                if "plan_id" not in existing_plan and default_plan.get("plan_id") is not None:
                    existing_plan["plan_id"] = default_plan["plan_id"]
                    changed = True

        if approval_decisions is None:
            self._data["approval_decisions"] = json.loads(json.dumps(default_approval_decisions))
            changed = True
        elif isinstance(approval_decisions, dict):
            for decision_id, default_decision in default_approval_decisions.items():
                if decision_id not in approval_decisions:
                    approval_decisions[decision_id] = json.loads(json.dumps(default_decision))
                    changed = True
                    continue
                existing_decision = approval_decisions.get(decision_id)
                if not isinstance(existing_decision, dict):
                    continue
                for key, value in default_decision.items():
                    if key not in existing_decision and value is not None:
                        existing_decision[key] = json.loads(json.dumps(value))
                        changed = True

        if evolution_decisions is None:
            self._data["evolution_decisions"] = json.loads(json.dumps(default_decisions))
            changed = True
        elif isinstance(evolution_decisions, dict):
            for decision_id, default_decision in default_decisions.items():
                if decision_id not in evolution_decisions:
                    evolution_decisions[decision_id] = json.loads(json.dumps(default_decision))
                    changed = True
                    continue
                existing_decision = evolution_decisions.get(decision_id)
                if not isinstance(existing_decision, dict):
                    continue
                for key, value in default_decision.items():
                    if key not in existing_decision and value is not None:
                        existing_decision[key] = json.loads(json.dumps(value))
                        changed = True

        if rollback_reviews is None:
            self._data["rollback_reviews"] = json.loads(json.dumps(default_rollback_reviews))
            changed = True
        elif isinstance(rollback_reviews, dict):
            for rollback_id, default_review in default_rollback_reviews.items():
                if rollback_id not in rollback_reviews:
                    rollback_reviews[rollback_id] = json.loads(json.dumps(default_review))
                    changed = True

        if governance_review_queue_items is None:
            self._data["governance_review_queue_items"] = json.loads(
                json.dumps(default_governance_review_queue_items)
            )
            changed = True
        elif isinstance(governance_review_queue_items, dict):
            for item_id, default_item in default_governance_review_queue_items.items():
                if item_id not in governance_review_queue_items:
                    governance_review_queue_items[item_id] = json.loads(
                        json.dumps(default_item)
                    )
                    changed = True

        if approval_queue_items is None:
            self._data["approval_queue_items"] = json.loads(
                json.dumps(default_approval_queue_items)
            )
            changed = True
        elif isinstance(approval_queue_items, dict):
            for item_id, default_item in default_approval_queue_items.items():
                if item_id not in approval_queue_items:
                    approval_queue_items[item_id] = json.loads(json.dumps(default_item))
                    changed = True

        if deployment_diffs is None:
            self._data["deployment_diffs"] = json.loads(json.dumps(default_deployment_diffs))
            changed = True
        elif isinstance(deployment_diffs, dict):
            for plan_id, default_diff in default_deployment_diffs.items():
                if plan_id not in deployment_diffs:
                    deployment_diffs[plan_id] = json.loads(json.dumps(default_diff))
                    changed = True

        if paper_live_drift_reports is None:
            self._data["paper_live_drift_reports"] = json.loads(
                json.dumps(default_paper_live_drift_reports)
            )
            changed = True
        elif isinstance(paper_live_drift_reports, dict):
            for runtime_id, default_report in default_paper_live_drift_reports.items():
                if runtime_id not in paper_live_drift_reports:
                    paper_live_drift_reports[runtime_id] = json.loads(
                        json.dumps(default_report)
                    )
                    changed = True

        if registry_entries is None:
            self._data["registry_entries"] = json.loads(json.dumps(default_registry_entries))
            changed = True
        elif isinstance(registry_entries, dict):
            for artifact_id, default_entry in default_registry_entries.items():
                if artifact_id not in registry_entries:
                    registry_entries[artifact_id] = json.loads(json.dumps(default_entry))
                    changed = True

        if research_analyses is None:
            self._data["research_analyses"] = json.loads(json.dumps(default_research_analyses))
            changed = True
        elif isinstance(research_analyses, dict):
            for analysis_id, default_analysis in default_research_analyses.items():
                if analysis_id not in research_analyses:
                    research_analyses[analysis_id] = json.loads(json.dumps(default_analysis))
                    changed = True

        if research_artifacts is None:
            self._data["research_artifacts"] = json.loads(json.dumps(default_research_artifacts))
            changed = True
        elif isinstance(research_artifacts, dict):
            for artifact_id, default_artifact in default_research_artifacts.items():
                if artifact_id not in research_artifacts:
                    research_artifacts[artifact_id] = json.loads(json.dumps(default_artifact))
                    changed = True

        if research_notes is None:
            self._data["research_notes"] = json.loads(json.dumps(default_research_notes))
            changed = True
        elif isinstance(research_notes, dict):
            for note_id, default_note in default_research_notes.items():
                if note_id not in research_notes:
                    research_notes[note_id] = json.loads(json.dumps(default_note))
                    changed = True

        if evidence_refs is None:
            self._data["evidence_refs"] = json.loads(json.dumps(default_evidence_refs))
            changed = True
        elif isinstance(evidence_refs, dict):
            for ref_id, default_ref in default_evidence_refs.items():
                if ref_id not in evidence_refs:
                    evidence_refs[ref_id] = json.loads(json.dumps(default_ref))
                    changed = True

        if insight_cards is None:
            self._data["insight_cards"] = json.loads(json.dumps(default_insight_cards))
            changed = True
        elif isinstance(insight_cards, dict):
            for insight_id, default_card in default_insight_cards.items():
                if insight_id not in insight_cards:
                    insight_cards[insight_id] = json.loads(json.dumps(default_card))
                    changed = True

        if institutional_memory_entries is None:
            self._data["institutional_memory_entries"] = json.loads(
                json.dumps(default_institutional_memory_entries)
            )
            changed = True
        elif isinstance(institutional_memory_entries, dict):
            for entry_id, default_entry in default_institutional_memory_entries.items():
                if entry_id not in institutional_memory_entries:
                    institutional_memory_entries[entry_id] = json.loads(json.dumps(default_entry))
                    changed = True

        if strategy_specs is None:
            self._data["strategy_specs"] = json.loads(json.dumps(default_strategy_specs))
            changed = True
        elif isinstance(strategy_specs, dict):
            for strategy_id, default_strategy in default_strategy_specs.items():
                if strategy_id not in strategy_specs:
                    strategy_specs[strategy_id] = json.loads(json.dumps(default_strategy))
                    changed = True

        if research_search_documents is None:
            self._data["research_search_documents"] = json.loads(
                json.dumps(default_research_search_documents)
            )
            changed = True
        elif isinstance(research_search_documents, dict):
            for result_id, default_document in default_research_search_documents.items():
                if result_id not in research_search_documents:
                    research_search_documents[result_id] = json.loads(json.dumps(default_document))
                    changed = True

        if research_search_index is None:
            self._data["research_search_index"] = json.loads(json.dumps(default_research_search_index))
            changed = True
        elif isinstance(research_search_index, dict):
            for adapter_id, default_index in default_research_search_index.items():
                if adapter_id not in research_search_index:
                    research_search_index[adapter_id] = json.loads(json.dumps(default_index))
                    changed = True

        if inspiration_graphs is None:
            self._data["inspiration_graphs"] = json.loads(json.dumps(default_inspiration_graphs))
            changed = True
        elif isinstance(inspiration_graphs, dict):
            for artifact_id, default_graph in default_inspiration_graphs.items():
                if artifact_id not in inspiration_graphs:
                    inspiration_graphs[artifact_id] = json.loads(json.dumps(default_graph))
                    changed = True

        if trainer_previews is None:
            self._data["trainer_previews"] = json.loads(json.dumps(default_trainer_previews))
            changed = True
        elif isinstance(trainer_previews, dict):
            for session_id, default_preview in default_trainer_previews.items():
                if session_id not in trainer_previews:
                    trainer_previews[session_id] = json.loads(json.dumps(default_preview))
                    changed = True

        consult_transcripts = self._data.get("consult_transcripts")
        default_consult_transcripts = default_data.get("consult_transcripts", {})
        if consult_transcripts is None:
            self._data["consult_transcripts"] = json.loads(json.dumps(default_consult_transcripts))
            changed = True
        elif isinstance(consult_transcripts, dict):
            for session_id, default_transcript in default_consult_transcripts.items():
                if session_id not in consult_transcripts:
                    consult_transcripts[session_id] = json.loads(json.dumps(default_transcript))
                    changed = True

        if consult_memos is None:
            self._data["consult_memos"] = json.loads(json.dumps(default_consult_memos))
            changed = True
        elif isinstance(consult_memos, dict):
            for memo_id, default_memo in default_consult_memos.items():
                if memo_id not in consult_memos:
                    consult_memos[memo_id] = json.loads(json.dumps(default_memo))
                    changed = True

        trainer_controls = self._data.get("trainer_controls")
        default_trainer_controls = default_data.get("trainer_controls", {})
        if trainer_controls is None:
            self._data["trainer_controls"] = json.loads(json.dumps(default_trainer_controls))
            changed = True
        elif isinstance(trainer_controls, dict):
            for session_id, default_ctrl in default_trainer_controls.items():
                if session_id not in trainer_controls:
                    trainer_controls[session_id] = json.loads(json.dumps(default_ctrl))
                    changed = True

        if self._backfill_tw04_replay_route_defaults():
            changed = True

        if _merge_market_persona_fleet(self._data, preserve_explicit_agora=True):
            changed = True

        return changed

    def _backfill_tw04_replay_route_defaults(self) -> bool:
        trainer_replays = self._data.get("trainer_replays")
        if not isinstance(trainer_replays, dict):
            return False

        changed = False
        for session in trainer_replays.values():
            if not isinstance(session, dict):
                continue
            events = session.get("events")
            if not isinstance(events, list):
                continue
            for event in events:
                if not isinstance(event, dict):
                    continue
                evidence_ref = event.get("evidence_ref")
                if not isinstance(evidence_ref, dict):
                    continue
                if str(evidence_ref.get("id") or "") != _TW04_DRAWDOWN_EVIDENCE_REF_ID:
                    continue
                if str(evidence_ref.get("type") or "") != "telemetry":
                    continue
                if evidence_ref.get("url_pattern") == _TW04_DRAWDOWN_EVIDENCE_ROUTE:
                    continue
                evidence_ref["url_pattern"] = _TW04_DRAWDOWN_EVIDENCE_ROUTE
                changed = True

        return changed

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2, ensure_ascii=True))

    def _local_dataset(self, dataset: str) -> Any:
        key = self._LOCAL_DATA_KEYS.get(dataset, dataset)
        return self._data.get(key)

    def _local_fallback(self, dataset: str) -> Any:
        if not self._allow_local_snapshot_fallback:
            return None
        return self._local_dataset(dataset)

    def _local_overlay_records(self, dataset: str) -> Dict[str, Dict[str, Any]]:
        if dataset not in self._local_overlay_write_datasets:
            return {}
        records = self._local_dataset(dataset)
        if isinstance(records, dict):
            return records
        return {}

    def _local_bff_write_records(
        self,
        dataset: str,
        key_candidates: Optional[List[str]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        records = self._local_dataset(dataset)
        if not isinstance(records, dict):
            return {}
        keys = key_candidates or self._MARKET_PERSONA_RECORD_KEYS.get(dataset, ["id"])
        out: Dict[str, Dict[str, Any]] = {}
        for fallback_key, record in records.items():
            if not isinstance(record, dict):
                continue
            metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
            persistence_mode = str(record.get("persistenceMode") or metadata.get("persistenceMode") or "").strip()
            write_authority = str(record.get("canonicalWriteAuthority") or "").strip()
            if persistence_mode != "bff_local_dev_store" and not write_authority:
                continue
            key = _record_key(record, keys) or str(fallback_key or "").strip()
            if key:
                out[key] = json.loads(json.dumps(record))
        return out

    def _local_bff_persona_records(self) -> Dict[str, Dict[str, Any]]:
        records = self._local_dataset("personas")
        if not isinstance(records, dict):
            return {}
        local_personas: Dict[str, Dict[str, Any]] = {}
        for key, record in records.items():
            if not isinstance(record, dict) or not self._is_bff_local_persona(record):
                continue
            persona_id = str(record.get("persona_id") or record.get("id") or key or "").strip()
            if persona_id:
                # Local Persona Registry writes use the same canonical projection
                # as service-backed records.  In particular, tenant ownership is
                # copied from canonical metadata to the top-level fields consumed
                # by fail-closed BFF audience checks.
                local_personas[persona_id] = self._project_service_persona(record)
        return local_personas

    @staticmethod
    def _is_bff_local_capability_snapshot(snapshot: Dict[str, Any]) -> bool:
        metadata = snapshot.get("metadata") if isinstance(snapshot.get("metadata"), dict) else {}
        return (
            snapshot.get("persistenceMode") == "bff_local_dev_store"
            or metadata.get("persistenceMode") == "bff_local_dev_store"
            or snapshot.get("canonicalWriteAuthority") == "persona_capability_service"
        )

    def _local_bff_capability_snapshot_records(self) -> Dict[str, Dict[str, Any]]:
        records = self._local_dataset("capability_snapshots")
        if not isinstance(records, dict):
            return {}
        local_snapshots: Dict[str, Dict[str, Any]] = {}
        for key, record in records.items():
            if not isinstance(record, dict) or not self._is_bff_local_capability_snapshot(record):
                continue
            snapshot_id = str(record.get("snapshot_id") or record.get("id") or key or "").strip()
            if snapshot_id:
                local_snapshots[snapshot_id] = json.loads(json.dumps(record))
        return local_snapshots

    def _market_persona_records(
        self,
        dataset: str,
        key_candidates: Optional[List[str]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        data_key = self._LOCAL_DATA_KEYS.get(dataset, dataset)
        records = _market_persona_read_model_data().get(data_key)
        keys = key_candidates or self._MARKET_PERSONA_RECORD_KEYS.get(dataset, ["id"])
        return json.loads(json.dumps(_normalize_records(records, keys)))

    def _merge_market_persona_records(
        self,
        dataset: str,
        records_by_id: Dict[str, Dict[str, Any]],
        key_candidates: Optional[List[str]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        merged = {
            str(key): json.loads(json.dumps(record))
            for key, record in records_by_id.items()
            if str(key).strip() and isinstance(record, dict)
        }
        for key, default_record in self._market_persona_records(dataset, key_candidates).items():
            existing = merged.get(key)
            if isinstance(existing, dict):
                _merge_missing_default_values(existing, default_record)
                continue
            merged[key] = default_record
        return merged

    def _merge_market_persona_record_list(
        self,
        dataset: str,
        records: List[Dict[str, Any]],
        key_candidates: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        keys = key_candidates or self._MARKET_PERSONA_RECORD_KEYS.get(dataset)
        if not keys:
            return records
        records_by_id = {
            str(key): record
            for record in records
            if isinstance(record, dict)
            for key in [_record_key(record, keys)]
            if key
        }
        return list(
            self._merge_market_persona_records(
                dataset,
                records_by_id,
                keys,
            ).values()
        )

    def _ensure_local_overlay_records(self, dataset: str) -> Dict[str, Dict[str, Any]]:
        self._local_overlay_write_datasets.add(dataset)
        key = self._LOCAL_DATA_KEYS.get(dataset, dataset)
        records = self._data.get(key)
        if not isinstance(records, dict):
            records = {}
            self._data[key] = records
        return records

    def dataset_source(
        self,
        dataset: str,
        *,
        include_snapshot_fallback: bool = True,
        include_local_fallback: bool = True,
    ) -> str:
        if self._consultation_service_dataset_available(dataset):
            service_records = self._consultation_service_records(dataset)
            if service_records is not None:
                return "consultation_service_client" if self._consultation_client() is not None else "consultation_service_store"
            if not include_local_fallback:
                return "missing"
        if dataset == "approval_queue_items":
            approval_source = self.dataset_source(
                "approval_decisions",
                include_snapshot_fallback=include_snapshot_fallback,
                include_local_fallback=include_local_fallback,
            )
            if approval_source != "missing":
                return approval_source
        if dataset == "governance_review_queue_items":
            for upstream_dataset in ("deployment_plans", "approval_decisions", "evolution_decisions"):
                upstream_source = self.dataset_source(
                    upstream_dataset,
                    include_snapshot_fallback=include_snapshot_fallback,
                    include_local_fallback=include_local_fallback,
                )
                if upstream_source != "missing":
                    return upstream_source
        if dataset in CanonicalSnapshotAdapter._DATASETS:
            available, _ = self._canonical.list_records(
                dataset,
                include_snapshot_fallback=include_snapshot_fallback,
            )
            if available:
                return self._canonical.source(dataset)
        if dataset in ServiceBackedReadAdapter._DATASETS:
            available, _ = self._service.list_records(
                dataset,
                include_snapshot_fallback=include_snapshot_fallback,
            )
            if available:
                return self._service.source(dataset)
        if include_local_fallback and dataset == "personas" and self._local_bff_persona_records():
            return "bff_local_dev_store"
        local_payload = self._local_fallback(dataset) if include_local_fallback else None
        if include_local_fallback and local_payload in (None, "", [], {}):
            local_payload = self._local_overlay_records(dataset)
        if local_payload not in (None, "", [], {}):
            return "local_snapshot"
        return "missing"

    def dataset_source_cached(
        self,
        dataset: str,
        *,
        include_local_fallback: bool = True,
    ) -> str:
        """Resolve provenance after a read without issuing another backend read.

        Human Inbox contributors already loaded their records. Calling
        ``dataset_source`` immediately afterward repeats adapter list calls and
        can double HTTP latency, so hot aggregation paths use this cache-only
        variant for the provenance envelope.
        """
        if dataset == "approval_queue_items":
            approval_source = self.dataset_source_cached(
                "approval_decisions",
                include_local_fallback=include_local_fallback,
            )
            if approval_source != "missing":
                return approval_source
        if dataset == "governance_review_queue_items":
            for upstream_dataset in (
                "deployment_plans",
                "approval_decisions",
                "evolution_decisions",
            ):
                upstream_source = self.dataset_source_cached(
                    upstream_dataset,
                    include_local_fallback=include_local_fallback,
                )
                if upstream_source != "missing":
                    return upstream_source
        canonical_source = self._canonical.cached_source(dataset)
        if canonical_source:
            return canonical_source
        service_source = self._service.cached_source(dataset)
        if service_source:
            return service_source
        if include_local_fallback and dataset == "personas" and self._local_bff_persona_records():
            return "bff_local_dev_store"
        local_payload = self._local_fallback(dataset) if include_local_fallback else None
        if include_local_fallback and local_payload in (None, "", [], {}):
            local_payload = self._local_overlay_records(dataset)
        if local_payload not in (None, "", [], {}):
            return "local_snapshot"
        return "missing"

    def _decision_journal_records(self) -> Dict[str, Dict[str, Any]]:
        local_key = self._LOCAL_DATA_KEYS.get("decision_journal_entries", "decision_journal_entries")
        records = self._data.get(local_key)
        if not isinstance(records, dict):
            records = {}
            self._data[local_key] = records
        return records

    def _decision_journal_read_records(self) -> Dict[str, Dict[str, Any]]:
        records: Dict[str, Dict[str, Any]] = {}
        if "decision_journal_entries" in ServiceBackedReadAdapter._DATASETS:
            available, service_records = self._service.list_records(
                "decision_journal_entries",
                include_snapshot_fallback=self._allow_local_snapshot_fallback,
            )
            if available:
                for index, record in enumerate(service_records):
                    if not isinstance(record, dict):
                        continue
                    key = _record_key(record, ["entry_id", "id"]) or str(index)
                    records[key] = json.loads(json.dumps(record))
        records.update(self._decision_journal_records())
        return records

    def _decision_journal_idempotency_records(self) -> Dict[str, Dict[str, Any]]:
        local_key = self._LOCAL_DATA_KEYS.get(
            "decision_journal_idempotency",
            "decision_journal_idempotency",
        )
        records = self._data.get(local_key)
        if not isinstance(records, dict):
            records = {}
            self._data[local_key] = records
        return records

    def _agora_journal_audit_records(self) -> Dict[str, Dict[str, Any]]:
        local_key = self._LOCAL_DATA_KEYS.get(
            "agora_journal_audit_events",
            "agora_journal_audit_events",
        )
        records = self._data.get(local_key)
        if not isinstance(records, dict):
            records = {}
            self._data[local_key] = records
        return records

    @staticmethod
    def _project_decision_journal_entry(record: Dict[str, Any]) -> Dict[str, Any]:
        entry_id = str(record.get("id") or record.get("entry_id") or "").strip()
        timestamp = _utc_now_rfc3339()
        return {
            "id": entry_id,
            "title": str(record.get("title") or "").strip(),
            "body": str(record.get("body") or ""),
            "tags": list(record.get("tags") or []),
            "linkedStrategyIds": list(
                record.get("linkedStrategyIds")
                or record.get("linked_strategy_ids")
                or []
            ),
            "linkedPersonaIds": list(
                record.get("linkedPersonaIds")
                or record.get("linked_persona_ids")
                or []
            ),
            "visibility": str(record.get("visibility") or "private").strip() or "private",
            "createdAt": str(record.get("createdAt") or record.get("created_at") or timestamp),
            "updatedAt": str(record.get("updatedAt") or record.get("updated_at") or timestamp),
            "version": int(record.get("version") or 1),
            "createdBy": str(record.get("createdBy") or record.get("created_by") or ""),
            "canonicalWriteAuthority": "agora_journal_service",
            "persistenceMode": str(record.get("persistenceMode") or "bff_local_dev_store"),
        }

    @staticmethod
    def _decision_journal_diff(
        before: Dict[str, Any],
        after: Dict[str, Any],
        fields: List[str],
    ) -> Dict[str, Any]:
        changes = []
        for field in fields:
            if before.get(field) == after.get(field):
                continue
            changes.append(
                {
                    "field": field,
                    "before": json.loads(json.dumps(before.get(field))),
                    "after": json.loads(json.dumps(after.get(field))),
                }
            )
        return {
            "changedFields": [change["field"] for change in changes],
            "changes": changes,
            "before": json.loads(json.dumps(before)),
            "after": json.loads(json.dumps(after)),
        }

    def patch_decision_journal_entry(
        self,
        entry_id: str,
        *,
        patch: Dict[str, Any],
        actor_id: str,
        correlation_id: Optional[str],
        idempotency_key: str,
        request_hash: str,
        patched_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Apply a journal merge patch through the explicit BFF-local dev path.

        Canonical journal write ownership remains outside the BFF. Until an
        Agora journal service adapter is wired, this method persists only a
        local degraded/dev projection plus replayable audit evidence.
        """
        clean_entry_id = str(entry_id or "").strip()
        if not clean_entry_id:
            return None

        idem_records = self._decision_journal_idempotency_records()
        existing_idem = idem_records.get(idempotency_key)
        if isinstance(existing_idem, dict):
            if existing_idem.get("request_hash") != request_hash:
                return {
                    "status": "conflict",
                    "existing_patch_id": existing_idem.get("patch_id"),
                    "entry": existing_idem.get("entry"),
                    "audit": existing_idem.get("audit"),
                }
            if isinstance(existing_idem.get("entry"), dict) and isinstance(existing_idem.get("audit"), dict):
                return {
                    "status": "replayed",
                    "entry": json.loads(json.dumps(existing_idem["entry"])),
                    "audit": json.loads(json.dumps(existing_idem["audit"])),
                }

        records = self._decision_journal_records()
        stored = records.get(clean_entry_id)
        if not isinstance(stored, dict):
            return None

        timestamp = patched_at or _utc_now_rfc3339()
        before = self._project_decision_journal_entry(stored)
        after = json.loads(json.dumps(before))
        changed_candidate_fields = [
            "title",
            "body",
            "tags",
            "linkedStrategyIds",
            "linkedPersonaIds",
            "visibility",
        ]
        for field in changed_candidate_fields:
            if field not in patch:
                continue
            value = patch[field]
            if value is None and field in {"tags", "linkedStrategyIds", "linkedPersonaIds"}:
                after[field] = []
            elif value is not None:
                after[field] = json.loads(json.dumps(value))

        after["updatedAt"] = timestamp
        after["version"] = int(before.get("version") or 0) + 1
        after["canonicalWriteAuthority"] = "agora_journal_service"
        after["persistenceMode"] = "bff_local_dev_store"

        diff = self._decision_journal_diff(before, after, changed_candidate_fields)
        audit_id = f"aud-agora-journal-{uuid.uuid4().hex[:12]}"
        audit = {
            "auditId": audit_id,
            "action": "agora.journal.merge_patch",
            "target": {"type": "DecisionJournalEntry", "id": clean_entry_id},
            "actorId": actor_id,
            "correlationId": correlation_id,
            "idempotencyKey": idempotency_key,
            "recordedAt": timestamp,
            "canonicalWriteAuthority": "agora_journal_service",
            "persistenceMode": "bff_local_dev_store",
            "degraded": True,
            "diff": diff,
        }

        records[clean_entry_id] = json.loads(json.dumps(after))
        audit_records = self._agora_journal_audit_records()
        audit_records[audit_id] = json.loads(json.dumps(audit))
        idem_records[idempotency_key] = {
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "patch_id": audit_id,
            "status": "succeeded",
            "entry": json.loads(json.dumps(after)),
            "audit": json.loads(json.dumps(audit)),
        }
        self._save()
        return {"status": "updated", "entry": after, "audit": audit}

    def list_decision_journal_entries(self) -> List[Dict[str, Any]]:
        entries = [
            self._project_decision_journal_entry(record)
            for record in self._decision_journal_read_records().values()
            if isinstance(record, dict)
        ]
        entries.sort(
            key=lambda entry: (
                (_parse_rfc3339(entry.get("updatedAt"))
                or _parse_rfc3339(entry.get("createdAt"))
                or datetime.min).replace(tzinfo=None)
            ),
            reverse=True,
        )
        return json.loads(json.dumps(entries))

    def create_decision_journal_entry(
        self,
        *,
        entry_id: str,
        title: str,
        body: str,
        tags: List[str],
        linked_strategy_ids: List[str],
        linked_persona_ids: List[str],
        visibility: str,
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        timestamp = created_at or _utc_now_rfc3339()
        record = {
            "id": entry_id,
            "title": title,
            "body": body,
            "tags": list(tags),
            "linkedStrategyIds": list(linked_strategy_ids),
            "linkedPersonaIds": list(linked_persona_ids),
            "visibility": visibility,
            "createdAt": timestamp,
            "updatedAt": timestamp,
            "version": 1,
            "createdBy": actor_id,
            "canonicalWriteAuthority": "agora_journal_service",
            "persistenceMode": "bff_local_dev_store",
        }
        self._decision_journal_records()[entry_id] = json.loads(json.dumps(record))
        self._save()
        return self._project_decision_journal_entry(record)

    def _agora_record_map(
        self,
        dataset: str,
        id_fields: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        records: Dict[str, Dict[str, Any]] = {}
        for record in self._read_dataset_records(dataset):
            if not isinstance(record, dict):
                continue
            key = _record_key(record, id_fields)
            if key:
                records[key] = json.loads(json.dumps(record))
        for key, record in self._local_overlay_records(dataset).items():
            if isinstance(record, dict):
                record_key = _record_key(record, id_fields) or str(key)
                records[record_key] = json.loads(json.dumps(record))
        return records

    @staticmethod
    def _recent_sort_value(record: Dict[str, Any]) -> datetime:
        return (
            (_parse_rfc3339(record.get("updated_at"))
            or _parse_rfc3339(record.get("updatedAt"))
            or _parse_rfc3339(record.get("created_at"))
            or _parse_rfc3339(record.get("createdAt"))
            or datetime.min).replace(tzinfo=None)
        )

    def list_agora_signals(self, *, review_status: Optional[str] = None) -> List[Dict[str, Any]]:
        signals = list(self._agora_record_map("agora_signals", ["signal_id", "id"]).values())
        if review_status:
            requested = str(review_status).strip().lower()
            signals = [
                signal
                for signal in signals
                if str(signal.get("reviewStatus") or signal.get("review_status") or "").strip().lower() == requested
            ]
        signals.sort(key=self._recent_sort_value, reverse=True)
        return json.loads(json.dumps(signals))

    def get_agora_signal(self, signal_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not signal_id:
            return None
        return self._agora_record_map("agora_signals", ["signal_id", "id"]).get(str(signal_id))

    def create_agora_signal(
        self,
        *,
        signal_id: str,
        title: str,
        body: str,
        actor_id: str,
        payload: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        timestamp = created_at or _utc_now_rfc3339()
        signal = {
            "id": signal_id,
            "signal_id": signal_id,
            "title": title,
            "body": body,
            "market": str(payload.get("market") or "").strip() or None,
            "tags": _compact_string_list(payload.get("tags")),
            "linkedPersonaIds": _compact_string_list(payload.get("linkedPersonaIds") or payload.get("linked_persona_ids")),
            "linkedStrategyIds": _compact_string_list(payload.get("linkedStrategyIds") or payload.get("linked_strategy_ids")),
            "severity": str(payload.get("severity") or "info").strip().lower(),
            "status": "open",
            "reviewStatus": "pending_trader_review",
            "createdAt": timestamp,
            "updatedAt": timestamp,
            "createdBy": actor_id,
            "authorId": actor_id,
        }
        self._ensure_local_overlay_records("agora_signals")[signal_id] = json.loads(json.dumps(signal))
        self._save()
        return json.loads(json.dumps(signal))

    def record_agora_signal_feedback(
        self,
        signal_id: str,
        *,
        decision: str,
        confidence: int,
        reason: Optional[str],
        actor_id: str,
        edit_window_seconds: int,
        recorded_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        signal = self.get_agora_signal(signal_id)
        if signal is None:
            return None

        timestamp = recorded_at or _utc_now_rfc3339()
        feedback_records = self._ensure_local_overlay_records("agora_signal_feedback")
        latest_key: Optional[str] = None
        latest_ts: Optional[datetime] = None
        timestamp_dt = _parse_rfc3339(timestamp)
        for key, record in feedback_records.items():
            if not isinstance(record, dict):
                continue
            if record.get("signalId") != signal_id or record.get("actorId") != actor_id:
                continue
            recorded_dt = _parse_rfc3339(record.get("updatedAt") or record.get("createdAt"))
            if recorded_dt is None or timestamp_dt is None:
                continue
            if (timestamp_dt - recorded_dt).total_seconds() > edit_window_seconds:
                continue
            if latest_ts is None or recorded_dt > latest_ts:
                latest_key = str(key)
                latest_ts = recorded_dt

        feedback_id = latest_key or f"sigfb-{uuid.uuid4().hex[:12]}"
        existing = feedback_records.get(feedback_id) if latest_key else None
        feedback = {
            "id": feedback_id,
            "feedbackId": feedback_id,
            "signalId": signal_id,
            "decision": decision,
            "confidence": confidence,
            "reason": reason,
            "actorId": actor_id,
            "createdAt": (existing or {}).get("createdAt") or timestamp,
            "updatedAt": timestamp,
            "editWindowSeconds": edit_window_seconds,
        }
        feedback_records[feedback_id] = json.loads(json.dumps(feedback))

        signal_records = self._ensure_local_overlay_records("agora_signals")
        signal_copy = json.loads(json.dumps(signal))
        signal_copy["reviewStatus"] = decision
        signal_copy["latestFeedbackId"] = feedback_id
        signal_copy["updatedAt"] = timestamp
        signal_records[signal_id] = signal_copy
        self._save()
        return json.loads(json.dumps(feedback))

    def create_agora_feedback(
        self,
        signal_id: str,
        *,
        verdict: str,
        memo: Optional[str],
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        signal = self.get_agora_signal(signal_id)
        if signal is None:
            return None

        timestamp = created_at or _utc_now_rfc3339()
        feedback_id = f"agfb-{uuid.uuid4().hex[:12]}"
        feedback = {
            "id": feedback_id,
            "feedbackId": feedback_id,
            "signal_id": signal_id,
            "signalId": signal_id,
            "verdict": verdict,
            "memo": memo,
            "author_id": actor_id,
            "authorId": actor_id,
            "created_at": timestamp,
            "createdAt": timestamp,
        }
        self._ensure_local_overlay_records("agora_feedback")[feedback_id] = json.loads(json.dumps(feedback))

        signal_records = self._ensure_local_overlay_records("agora_signals")
        signal_copy = json.loads(json.dumps(signal))
        signal_copy["latestFeedbackId"] = feedback_id
        signal_copy["updatedAt"] = timestamp
        signal_records[signal_id] = signal_copy
        self._save()
        return json.loads(json.dumps(feedback))

    def list_agora_watchlist(self) -> List[Dict[str, Any]]:
        items = list(self._agora_record_map("agora_watchlist", ["watchlist_id", "id", "symbol"]).values())
        items.sort(key=lambda item: str(item.get("symbol") or item.get("id") or ""))
        return json.loads(json.dumps(items))

    def list_agora_sessions(self, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
        sessions = list(self._agora_record_map("agora_sessions", ["sessionId", "session_id", "id"]).values())
        if status:
            requested = str(status).strip().lower()
            sessions = [s for s in sessions if str(s.get("status") or "").strip().lower() == requested]
        sessions.sort(key=self._recent_sort_value, reverse=True)
        return json.loads(json.dumps(sessions))

    def get_agora_session(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        return self._agora_record_map("agora_sessions", ["sessionId", "session_id", "id"]).get(str(session_id))

    def create_agora_session(
        self,
        *,
        session_id: str,
        title: str,
        actor_id: str,
        payload: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        timestamp = created_at or _utc_now_rfc3339()
        session = {
            "id": session_id,
            "sessionId": session_id,
            "title": title,
            "mode": payload.get("mode") or payload.get("sessionType") or "quick_ask",
            "status": payload.get("status") or "active",
            "participants": json.loads(json.dumps(payload.get("participants") or [])),
            "contextRefs": json.loads(json.dumps(payload.get("contextRefs") or payload.get("context_refs") or [])),
            "messages": json.loads(json.dumps(payload.get("messages") or [])),
            "createdBy": actor_id,
            "createdAt": timestamp,
            "updatedAt": timestamp,
        }
        for _committee_field in ("quorumState", "consensusState", "participantRoster", "linkedRequestId"):
            if payload.get(_committee_field) is not None:
                session[_committee_field] = json.loads(json.dumps(payload[_committee_field]))
        self._ensure_local_overlay_records("agora_sessions")[session_id] = json.loads(json.dumps(session))
        self._save()
        return json.loads(json.dumps(session))

    def list_agora_session_messages(self, session_id: str) -> Optional[List[Dict[str, Any]]]:
        session = self.get_agora_session(session_id)
        if session is None:
            return None
        messages = [item for item in (session.get("messages") or []) if isinstance(item, dict)]
        messages.sort(key=lambda item: str(item.get("createdAt") or item.get("created_at") or ""))
        return json.loads(json.dumps(messages))

    def append_agora_session_message(
        self,
        session_id: str,
        *,
        message_id: str,
        content: str,
        actor_id: str,
        payload: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        session = self.get_agora_session(session_id)
        if session is None:
            return None
        timestamp = created_at or _utc_now_rfc3339()
        message = {
            "id": message_id,
            "sessionId": session_id,
            "sender": payload.get("sender") or {"type": "operator", "id": actor_id},
            "role": payload.get("role") or "user",
            "content": content,
            "language": payload.get("language") or "zh-TW",
            "attachments": json.loads(json.dumps(payload.get("attachments") or [])),
            "citations": json.loads(json.dumps(payload.get("citations") or [])),
            "annotations": json.loads(json.dumps(payload.get("annotations") or [])),
            "createdAt": timestamp,
        }
        session = json.loads(json.dumps(session))
        session.setdefault("messages", []).append(message)
        session["updatedAt"] = timestamp
        self._ensure_local_overlay_records("agora_sessions")[session_id] = session
        self._save()
        return json.loads(json.dumps(message))

    def close_agora_session(
        self,
        session_id: str,
        *,
        closed_at: Optional[str] = None,
        outcome: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        session = self.get_agora_session(session_id)
        if session is None:
            return None
        timestamp = closed_at or _utc_now_rfc3339()
        session = json.loads(json.dumps(session))
        session["status"] = "closed"
        session["closedAt"] = timestamp
        session["updatedAt"] = timestamp
        if outcome is not None:
            session["outcome"] = outcome
        self._ensure_local_overlay_records("agora_sessions")[session_id] = session
        self._save()
        return json.loads(json.dumps(session))

    def open_committee_session(
        self,
        session_id: str,
        *,
        opened_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        session = self.get_agora_session(session_id)
        if session is None:
            return None
        if str(session.get("mode") or "").strip() != "committee":
            return None
        timestamp = opened_at or _utc_now_rfc3339()
        session = json.loads(json.dumps(session))
        session["status"] = "open"
        session["openedAt"] = timestamp
        session["updatedAt"] = timestamp
        self._ensure_local_overlay_records("agora_sessions")[session_id] = session
        self._save()
        return json.loads(json.dumps(session))

    def close_committee_session(
        self,
        session_id: str,
        *,
        closed_at: Optional[str] = None,
        outcome: Optional[str] = None,
        memo_ids: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        session = self.get_agora_session(session_id)
        if session is None:
            return None
        if str(session.get("mode") or "").strip() != "committee":
            return None
        timestamp = closed_at or _utc_now_rfc3339()
        session = json.loads(json.dumps(session))
        session["status"] = "closed"
        session["closedAt"] = timestamp
        session["updatedAt"] = timestamp
        if outcome is not None:
            session["outcome"] = outcome
        if memo_ids is not None:
            session["memoIds"] = memo_ids
        self._ensure_local_overlay_records("agora_sessions")[session_id] = session
        self._save()
        return json.loads(json.dumps(session))

    # ---- ASK-004: committee session memo publish to registry / review ---- #

    def submit_committee_session_memo(
        self,
        session_id: str,
        *,
        memo_id: str,
        actor_id: str,
        payload: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """ASK-004: draft a memo linked to a committee session."""
        session = self.get_agora_session(session_id)
        if session is None or str(session.get("mode") or "").strip() != "committee":
            return None
        payload = _redact_consult_memo_review_payload(payload)
        timestamp = created_at or _utc_now_rfc3339()
        memo_type = str(payload.get("memoType") or payload.get("memo_type") or "committee_summary").strip() or "committee_summary"
        author_ref = json.loads(
            json.dumps(
                payload.get("authorRef")
                or payload.get("author_ref")
                or {"type": "operator", "id": actor_id}
            )
        )
        evidence_refs = json.loads(
            json.dumps(list(payload.get("evidenceRefs") or payload.get("evidence_refs") or []))
        )
        evidence_ref_ids: List[str] = []
        for item in evidence_refs:
            if isinstance(item, dict):
                ref_id = str(item.get("id") or item.get("ref_id") or item.get("artifact_ref") or "").strip()
            else:
                ref_id = str(item or "").strip()
            if ref_id and ref_id not in evidence_ref_ids:
                evidence_ref_ids.append(ref_id)
        if isinstance(author_ref, dict):
            created_by = json.loads(json.dumps(author_ref))
        else:
            created_by = {"actor_type": "operator", "actor_id": str(author_ref or actor_id)}
        target = self._agora_session_target(session)
        memo: Dict[str, Any] = {
            "id": memo_id,
            "memo_id": memo_id,
            "memo_type": memo_type,
            "status": "draft",
            "lifecycle_state": "draft",
            "linked_session_id": session_id,
            "linked_request_id": (
                payload.get("linkedRequestId")
                or payload.get("linked_request_id")
                or session.get("linkedRequestId")
            ),
            "author_ref": author_ref,
            "session_to_memo_mapping": {
                "mapping_id": f"map-{memo_id}",
                "source_session_id": session_id,
                "transcript_id": payload.get("transcriptId") or payload.get("transcript_id") or f"tr-{session_id}",
                "transcript_version": payload.get("transcriptVersion") or payload.get("transcript_version"),
                "memo_id": memo_id,
                "memo_type": memo_type,
                "created_by": created_by,
                "evidence_refs": evidence_ref_ids,
                "mapping_status": "draft",
                "created_at": timestamp,
            },
            "summary": str(payload.get("summary") or "").strip() or None,
            "recommendations": json.loads(json.dumps(list(payload.get("recommendations") or []))),
            "evidence_refs": evidence_refs,
            "created_at": timestamp,
            "published_at": None,
            "governance_target": {
                "target_type": target.get("type"),
                "target_id": target.get("id"),
                "deployment_plan_id": target.get("id") if target.get("type") == "deployment_plan" else None,
                "artifact_id": target.get("id") if target.get("type") == "artifact" else None,
                "strategy_id": target.get("id") if target.get("type") == "strategy" else None,
            },
        }
        self._ensure_local_overlay_records("consult_memos")[memo_id] = memo
        self._save()
        return self._project_consult_memo_detail(json.loads(json.dumps(memo)))

    def list_committee_session_memos(self, session_id: str) -> List[Dict[str, Any]]:
        """ASK-004: list memos linked to a committee session."""
        all_memos = self._read_dataset_records("consult_memos")
        memos = [
            memo
            for memo in all_memos
            if isinstance(memo, dict) and str(memo.get("linked_session_id") or "") == str(session_id)
        ]
        memos.sort(key=self._recent_sort_value, reverse=True)
        return [self._project_consult_memo_summary(memo) for memo in memos]

    def get_committee_session_memo(self, session_id: str, memo_id: str) -> Optional[Dict[str, Any]]:
        """ASK-004: get a memo linked to a committee session."""
        record = self._agora_record_map("consult_memos", ["memo_id", "id"]).get(str(memo_id))
        if record is None:
            return None
        if str(record.get("linked_session_id") or "") != str(session_id):
            return None
        return self._project_consult_memo_detail(record)

    def publish_committee_session_memo(
        self,
        session_id: str,
        memo_id: str,
        *,
        actor_id: str,
        published_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """ASK-004: publish a committee session memo to the consult memo registry."""
        record = self._agora_record_map("consult_memos", ["memo_id", "id"]).get(str(memo_id))
        if record is None:
            return None
        if str(record.get("linked_session_id") or "") != str(session_id):
            return None
        timestamp = published_at or _utc_now_rfc3339()
        memo = json.loads(json.dumps(record))
        if str(memo.get("status") or memo.get("lifecycle_state") or "").strip().lower() == "published":
            return self._project_consult_memo_detail(memo)
        memo["status"] = "published"
        memo["lifecycle_state"] = "published"
        memo["published_at"] = timestamp
        memo["published_by"] = actor_id
        mapping = memo.get("session_to_memo_mapping")
        if isinstance(mapping, dict):
            mapping["mapping_status"] = "active"
        self._ensure_local_overlay_records("consult_memos")[memo_id] = memo
        self._save()
        return self._project_consult_memo_detail(memo)

    def get_agora_message(self, message_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not message_id:
            return None
        for session in self.list_agora_sessions():
            for message in session.get("messages") or []:
                if isinstance(message, dict) and str(message.get("id") or "") == str(message_id):
                    return json.loads(json.dumps(message))
        return None

    @staticmethod
    def _agora_session_target(session: Optional[Dict[str, Any]]) -> Dict[str, str]:
        session = session or {}
        target = session.get("targetEntity") if isinstance(session.get("targetEntity"), dict) else None
        if target is None:
            target = session.get("targetObject") if isinstance(session.get("targetObject"), dict) else None
        target = target or {}
        target_type = str(
            target.get("type")
            or session.get("targetEntityType")
            or session.get("target_type")
            or "artifact"
        ).strip() or "artifact"
        target_id = str(
            target.get("id")
            or session.get("targetEntityId")
            or session.get("target_id")
            or session.get("sessionId")
            or session.get("id")
            or ""
        ).strip()
        return {"type": target_type, "id": target_id}

    def get_agora_committee_evidence_pack(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        records = self._agora_record_map(
            "agora_committee_evidence_packs",
            ["id", "packId", "sessionId", "session_id"],
        )
        for record in records.values():
            if str(record.get("sessionId") or record.get("session_id") or "") == str(session_id):
                return json.loads(json.dumps(record))
        return None

    def create_agora_committee_evidence_pack(
        self,
        session_id: str,
        *,
        payload: Dict[str, Any],
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        timestamp = created_at or _utc_now_rfc3339()
        existing = self.get_agora_committee_evidence_pack(session_id)
        target = payload.get("targetEntity") if isinstance(payload.get("targetEntity"), dict) else {}
        session_target = self._agora_session_target(self.get_agora_session(session_id))
        target_type = str(
            payload.get("targetEntityType")
            or payload.get("target_entity_type")
            or target.get("type")
            or session_target.get("type")
            or "artifact"
        ).strip() or "artifact"
        target_id = str(
            payload.get("targetEntityId")
            or payload.get("target_entity_id")
            or target.get("id")
            or session_target.get("id")
            or session_id
        ).strip() or session_id
        pack_id = str(
            payload.get("id")
            or payload.get("packId")
            or (existing or {}).get("id")
            or f"evpack-{uuid.uuid4().hex[:12]}"
        )
        pack = {
            "id": pack_id,
            "packId": pack_id,
            "sessionId": session_id,
            "targetEntityType": target_type,
            "targetEntityId": target_id,
            "uploadedFiles": json.loads(json.dumps((existing or {}).get("uploadedFiles") or [])),
            "linkedEntities": json.loads(json.dumps(payload.get("linkedEntities") or payload.get("linked_entities") or [])),
            "notes": str(payload.get("notes") or ""),
            "createdBy": (existing or {}).get("createdBy") or actor_id,
            "createdAt": (existing or {}).get("createdAt") or timestamp,
            "updatedAt": timestamp,
            "canonicalWriteAuthority": "agora_committee_evidence_service",
            "persistenceMode": "bff_local_dev_store",
        }
        records = self._ensure_local_overlay_records("agora_committee_evidence_packs")
        records[pack_id] = json.loads(json.dumps(pack))
        self._save()
        return json.loads(json.dumps(pack))

    def append_agora_committee_evidence_files(
        self,
        session_id: str,
        *,
        files: List[Dict[str, Any]],
        actor_id: str,
        uploaded_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        session = self.get_agora_session(session_id)
        if session is None:
            return None
        timestamp = uploaded_at or _utc_now_rfc3339()
        pack = self.get_agora_committee_evidence_pack(session_id)
        if pack is None:
            session_target = self._agora_session_target(session)
            pack = self.create_agora_committee_evidence_pack(
                session_id,
                payload={
                    "targetEntityType": session_target["type"],
                    "targetEntityId": session_target["id"] or session_id,
                    "linkedEntities": [],
                    "notes": "",
                },
                actor_id=actor_id,
                created_at=timestamp,
            )
        uploaded_files = list(pack.get("uploadedFiles") or [])
        new_files: List[Dict[str, Any]] = []
        for item in files:
            file_id = str(item.get("id") or item.get("fileId") or f"evfile-{uuid.uuid4().hex[:12]}")
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            record = {
                "id": file_id,
                "fileName": str(item.get("fileName") or item.get("filename") or item.get("name") or ""),
                "mimeType": str(item.get("mimeType") or item.get("mime_type") or ""),
                "sizeBytes": int(item.get("sizeBytes") or item.get("size_bytes") or 0),
                "storageUrl": str(item.get("storageUrl") or item.get("storage_url") or f"bff://agora/committee/{session_id}/evidence/{file_id}"),
                "extractedTextStatus": str(item.get("extractedTextStatus") or item.get("extracted_text_status") or "not_started"),
                "metadata": json.loads(json.dumps(metadata)),
                "uploadedBy": metadata.get("uploadedBy") or actor_id,
                "createdAt": metadata.get("createdAt") or timestamp,
            }
            uploaded_files.append(record)
            new_files.append(record)
        pack = json.loads(json.dumps(pack))
        pack["uploadedFiles"] = uploaded_files
        pack["updatedAt"] = timestamp
        records = self._ensure_local_overlay_records("agora_committee_evidence_packs")
        records[str(pack["id"])] = pack
        self._save()
        result = json.loads(json.dumps(pack))
        result["newFiles"] = json.loads(json.dumps(new_files))
        return result

    def list_agora_notes(self) -> List[Dict[str, Any]]:
        notes = self._read_dataset_records("research_notes")
        notes.sort(key=self._recent_sort_value, reverse=True)
        return json.loads(json.dumps(notes))

    def create_agora_note(
        self,
        *,
        note_id: str,
        title: Optional[str],
        body: str,
        actor_id: str,
        payload: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        timestamp = created_at or _utc_now_rfc3339()
        note = {
            "id": note_id,
            "note_id": note_id,
            "title": title,
            "body": body,
            "attachment_type": payload.get("attachment_type") or "free_standing",
            "attachment_ref": json.loads(json.dumps(payload.get("attachment_ref"))),
            "owner_ref": payload.get("owner_ref") or {"owner_type": "operator", "owner_id": actor_id},
            "tags": list(payload.get("tags") or []),
            "linked_evidence_refs": list(payload.get("linked_evidence_refs") or payload.get("linkedEvidenceRefs") or []),
            "linked_memory_anchors": list(payload.get("linked_memory_anchors") or payload.get("linkedMemoryAnchors") or []),
            "created_at": timestamp,
            "updated_at": timestamp,
            "created_by": actor_id,
        }
        self._ensure_local_overlay_records("research_notes")[note_id] = json.loads(json.dumps(note))
        self._save()
        return json.loads(json.dumps(note))

    def list_agora_insights(self) -> List[Dict[str, Any]]:
        insights = self._read_dataset_records("insight_cards")
        insights.sort(key=self._recent_sort_value, reverse=True)
        return json.loads(json.dumps(insights))

    def create_agora_insight(
        self,
        *,
        insight_id: str,
        summary: str,
        actor_id: str,
        payload: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        timestamp = created_at or _utc_now_rfc3339()
        insight = {
            "id": insight_id,
            "insight_id": insight_id,
            "summary": summary,
            "scope": payload.get("scope") or "global",
            "scope_ref": payload.get("scope_ref") or payload.get("scopeRef"),
            "status": payload.get("status") or "classified",
            "confidence": json.loads(json.dumps(payload.get("confidence") or {})),
            "tags": list(payload.get("tags") or []),
            "source_ref": payload.get("source_ref") or payload.get("sourceRef") or f"agora:{insight_id}",
            "supporting_evidence_refs": json.loads(
                json.dumps(payload.get("supporting_evidence_refs") or payload.get("supportingEvidenceRefs") or [])
            ),
            "linked_sources": json.loads(json.dumps(payload.get("linked_sources") or payload.get("linkedSources") or [])),
            "aggregation_provenance": {
                "created_by": actor_id,
                "aggregated_at": timestamp,
                **(payload.get("aggregation_provenance") or {}),
            },
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        self._ensure_local_overlay_records("insight_cards")[insight_id] = json.loads(json.dumps(insight))
        self._save()
        return json.loads(json.dumps(insight))

    def list_agora_memory(self) -> List[Dict[str, Any]]:
        entries = self._read_dataset_records("institutional_memory_entries")
        entries.sort(key=self._recent_sort_value, reverse=True)
        return json.loads(json.dumps(entries))

    def get_agora_memory_entry(self, memory_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not memory_id:
            return None
        return self._agora_record_map("institutional_memory_entries", ["entry_id", "id"]).get(str(memory_id))

    def list_agora_training_examples(self) -> List[Dict[str, Any]]:
        examples = list(self._agora_record_map("agora_training_examples", ["trainingExampleId", "example_id", "id"]).values())
        examples.sort(key=self._recent_sort_value, reverse=True)
        return json.loads(json.dumps(examples))

    def create_agora_training_example(
        self,
        *,
        example_id: str,
        payload: Dict[str, Any],
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        timestamp = created_at or _utc_now_rfc3339()
        example = {
            "id": example_id,
            "trainingExampleId": example_id,
            "source": payload.get("source") or "agora",
            "personaId": payload.get("personaId") or payload.get("persona_id"),
            "input": json.loads(json.dumps(payload.get("input") or {})),
            "expected": json.loads(json.dumps(payload.get("expected") or {})),
            "labels": list(payload.get("labels") or []),
            "status": payload.get("status") or "draft",
            "createdBy": actor_id,
            "createdAt": timestamp,
            "updatedAt": timestamp,
        }
        self._ensure_local_overlay_records("agora_training_examples")[example_id] = json.loads(json.dumps(example))
        self._save()
        return json.loads(json.dumps(example))

    def record_agora_audit_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        timestamp = str(event.get("recordedAt") or event.get("timestamp") or _utc_now_rfc3339())
        event_id = str(event.get("auditId") or event.get("eventId") or f"aud-agora-{uuid.uuid4().hex[:12]}")
        record = {
            "auditId": event_id,
            "recordedAt": timestamp,
            **json.loads(json.dumps(event)),
        }
        records = self._ensure_local_overlay_records("agora_audit_events")
        records[event_id] = record
        self._save()
        return json.loads(json.dumps(record))

    def list_agora_handoffs(
        self,
        *,
        status: Optional[str] = None,
        handoff_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        items = list(self._agora_record_map("agora_handoffs", ["id", "handoffId"]).values())
        if status:
            requested = str(status).strip().lower()
            items = [item for item in items if str(item.get("status") or "").strip().lower() == requested]
        if handoff_type:
            requested_type = str(handoff_type).strip()
            items = [item for item in items if str(item.get("handoffType") or "") == requested_type]
        items.sort(key=self._recent_sort_value, reverse=True)
        return json.loads(json.dumps(items))

    def create_agora_handoff(
        self,
        *,
        handoff_id: str,
        handoff_type: str,
        source_route: str,
        source_entity: Dict[str, Any],
        destination_route: str,
        destination_queue: str,
        priority: str,
        payload: Dict[str, Any],
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        timestamp = created_at or _utc_now_rfc3339()
        due_dt = (_parse_rfc3339(timestamp) or datetime.now(timezone.utc)) + timedelta(
            hours={"low": 168, "normal": 48, "high": 24, "urgent": 4}.get(priority, 48)
        )
        due_at = due_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        record = {
            "id": handoff_id,
            "handoffId": handoff_id,
            "handoffType": handoff_type,
            "status": "submitted",
            "source": {
                "app": "agora",
                "route": source_route,
                "entity": json.loads(json.dumps(source_entity)),
            },
            "destination": {
                "app": "management",
                "route": destination_route,
                "queue": destination_queue,
            },
            "priority": priority,
            "slaDueAt": due_at,
            "rerouteCount": 0,
            "payload": json.loads(json.dumps(payload)),
            "createdBy": {"type": "operator", "id": actor_id},
            "createdAt": timestamp,
            "updatedAt": timestamp,
            "canonicalWriteAuthority": "agora_handoff_service",
            "persistenceMode": "bff_local_dev_store",
        }
        records = self._ensure_local_overlay_records("agora_handoffs")
        records[handoff_id] = json.loads(json.dumps(record))
        self._save()
        return json.loads(json.dumps(record))

    @staticmethod
    def _project_canonical_deployment_plan(
        raw: Dict[str, Any],
        runtime_binding_id: Optional[str],
        saga_progress: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        plan_id = str(raw.get("plan_id") or raw.get("id") or "")
        binding_id = raw.get("binding_id")
        binding_ids = [str(binding_id)] if binding_id else []
        projected = {
            "id": plan_id,
            "plan_id": plan_id,
            "stage": raw.get("target_stage") or raw.get("stage") or raw.get("current_stage"),
            "current_stage": raw.get("current_stage"),
            "target_stage": raw.get("target_stage") or raw.get("stage"),
            "strategy_id": raw.get("strategy_id"),
            "artifact_id": raw.get("artifact_id"),
            "artifact_version": raw.get("artifact_version"),
            "submitted_at": raw.get("submitted_at") or raw.get("created_at"),
            "approval_decision_id": raw.get("approval_decision_id"),
            "capital_pool_id": raw.get("capital_pool_id"),
            "binding_ids": binding_ids,
            "runtime_binding_id": runtime_binding_id or raw.get("runtime_binding_id"),
            "status": raw.get("status"),
            "transition_type": raw.get("transition_type"),
            "stages": json.loads(json.dumps(raw.get("stages") or [])),
            "approval_ref": json.loads(
                json.dumps(
                    raw.get("approval_ref")
                    or (
                        {
                            "approval_decision_id": raw.get("approval_decision_id"),
                            "href": f"/bff/approvals/{raw.get('approval_decision_id')}",
                        }
                        if raw.get("approval_decision_id")
                        else {}
                    )
                )
            ),
        }
        if saga_progress is not None:
            projected["deployment_saga_id"] = saga_progress.get("saga_id")
            projected["deployment_saga_status"] = saga_progress.get("saga_status")
            projected["saga_progress"] = json.loads(json.dumps(saga_progress))
            projected["saga_progress_status"] = saga_progress.get("progress_status")
            projected["blocked_reason"] = saga_progress.get("blocked_reason")
            projected["retry_state"] = json.loads(json.dumps(saga_progress.get("retry_state") or []))
            projected["dlq_count"] = saga_progress.get("dlq_count", 0)
        return projected

    @staticmethod
    def _project_canonical_approval_decision(raw: Dict[str, Any]) -> Dict[str, Any]:
        decision_id = raw.get("decision_id") or raw.get("id")
        target_type = raw.get("target_type") or raw.get("decision_type")
        target_id = raw.get("target_id")
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        tenant_id = (
            raw.get("tenant_id")
            or raw.get("tenantId")
            or metadata.get("tenant_id")
            or metadata.get("tenantId")
        )
        owner_user_id = (
            raw.get("owner_user_id")
            or raw.get("user_id")
            or raw.get("userId")
            or metadata.get("owner_user_id")
            or metadata.get("user_id")
            or metadata.get("userId")
        )
        deployment_ref = {}
        if str(target_type or "") == "DeploymentPlan" and target_id:
            deployment_ref = {
                "plan_id": target_id,
                "href": f"/bff/deployments/{target_id}",
            }
        return {
            "id": decision_id,
            "decision_id": decision_id,
            "decision_type": raw.get("decision_type"),
            "target_type": target_type,
            "target_id": target_id,
            "target_version": raw.get("target_version"),
            "tenant_id": tenant_id,
            "owner_user_id": owner_user_id,
            "deployment_ref": deployment_ref,
            "outcome": raw.get("decision") or raw.get("outcome"),
            "reviewer": raw.get("actor_id") or raw.get("reviewer"),
            "actor_role": raw.get("actor_role"),
            "created_by": raw.get("created_by") or raw.get("actor_id"),
            "created_at": raw.get("created_at") or raw.get("submitted_at"),
            "submitted_at": raw.get("submitted_at") or raw.get("created_at"),
            "decided_at": raw.get("decided_at"),
            "expires_at": raw.get("expires_at"),
            "revoked_at": raw.get("revoked_at"),
            "proposal_id": raw.get("proposal_id"),
            "proposal_revision": raw.get("proposal_revision"),
            "proposal_content_digest": raw.get("proposal_content_digest"),
            "validation_result_digest": raw.get("validation_result_digest"),
            "risk_level": raw.get("risk_level"),
            "state": raw.get("decision_state") or raw.get("state"),
            "rationale": raw.get("rationale"),
            "evidence_refs": json.loads(json.dumps(raw.get("evidence_refs") or [])),
        }

    @staticmethod
    def _project_canonical_capital_pool(raw: Dict[str, Any]) -> Dict[str, Any]:
        pool_id = raw.get("pool_id") or raw.get("id")
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        tenant_id = raw.get("tenant_id") or raw.get("tenantId") or metadata.get("tenant_id") or metadata.get("tenantId")
        projected = json.loads(json.dumps(raw))
        projected["id"] = pool_id
        projected["pool_id"] = pool_id
        projected.setdefault("name", raw.get("name"))
        projected.setdefault("status", raw.get("status"))
        projected.setdefault("owner_id", raw.get("owner_id"))
        projected.setdefault("owner_type", raw.get("owner_type"))
        projected.setdefault("single_runtime_enforced", raw.get("single_runtime_enforced", True))
        projected.setdefault("risk_policy_ref", raw.get("risk_policy_ref"))
        projected["canonicalWriteAuthority"] = "capital_service"
        projected["canonical_write_authority"] = "capital_service"
        projected["persistenceMode"] = "owner_store"
        projected["persistence_mode"] = "owner_store"
        projected["tenant_id"] = tenant_id
        projected["tenantId"] = tenant_id
        return projected

    @staticmethod
    def _project_canonical_binding(raw: Dict[str, Any]) -> Dict[str, Any]:
        binding_id = raw.get("binding_id") or raw.get("id")
        projected = json.loads(json.dumps(raw))
        projected["id"] = binding_id
        projected["binding_id"] = binding_id
        projected.setdefault("persona_id", raw.get("persona_id"))
        projected.setdefault("capital_pool_id", raw.get("capital_pool_id"))
        projected.setdefault("capital_sleeve_id", raw.get("capital_sleeve_id"))
        projected.setdefault("role", raw.get("role"))
        projected.setdefault("validity", raw.get("validity"))
        projected.setdefault("status", raw.get("status"))
        projected.setdefault("approval_decision_id", raw.get("approval_decision_id"))
        projected.setdefault("allowed_deployment_scope", raw.get("allowed_deployment_scope"))
        projected["canonicalWriteAuthority"] = "capital_service"
        projected["canonical_write_authority"] = "capital_service"
        projected["persistenceMode"] = "owner_store"
        projected["persistence_mode"] = "owner_store"
        return projected

    @staticmethod
    def _project_canonical_runtime_binding(raw: Dict[str, Any]) -> Dict[str, Any]:
        binding_id = raw.get("binding_id") or raw.get("id")
        deployment_stage = raw.get("deployment_stage")
        deployment_mode = raw.get("deployment_mode")
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        tenant_id = raw.get("tenant_id") or raw.get("tenantId") or metadata.get("tenant_id") or metadata.get("tenantId")
        projected = json.loads(json.dumps(raw))
        projected["id"] = binding_id
        projected["binding_id"] = binding_id
        projected["runtime_binding_id"] = raw.get("runtime_binding_id") or binding_id
        projected["runtime_id"] = raw.get("runtime_id") or binding_id
        projected.setdefault("name", raw.get("name"))
        projected.setdefault("state", raw.get("state") or raw.get("status"))
        projected.setdefault("persona_id", raw.get("persona_id"))
        projected.setdefault("deployment_plan_id", raw.get("deployment_plan_id") or raw.get("plan_id"))
        projected.setdefault("runtime_kind", raw.get("runtime_kind"))
        projected["deployment_stage"] = deployment_stage
        projected["deployment_mode"] = deployment_mode
        projected.setdefault("status", raw.get("status"))
        projected.setdefault("plan_id", raw.get("plan_id"))
        projected.setdefault("capital_pool_id", raw.get("capital_pool_id"))
        projected.setdefault("artifact_id", raw.get("artifact_id"))
        projected.setdefault("artifact_version", raw.get("artifact_version"))
        projected.setdefault("persona_capital_binding_id", raw.get("persona_capital_binding_id"))
        projected["tenant_id"] = tenant_id
        projected["tenantId"] = tenant_id
        projected.setdefault("effective_at", raw.get("effective_at"))
        projected.setdefault("retired_at", raw.get("retired_at"))
        projected.setdefault("rollback_parent", raw.get("rollback_parent"))
        projected.setdefault("rollback_action_type", raw.get("rollback_action_type"))
        projected.setdefault("created_at", raw.get("created_at"))
        projected.setdefault("updated_at", raw.get("updated_at"))
        projected.setdefault("created_by", raw.get("created_by"))
        projected.setdefault("params", json.loads(json.dumps(raw.get("params") or {})))
        projected["metadata"] = json.loads(json.dumps(metadata))
        return projected

    @staticmethod
    def _project_service_persona(raw: Dict[str, Any]) -> Dict[str, Any]:
        persona_id = raw.get("persona_id") or raw.get("id")
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        tenant_id = raw.get("tenant_id") or raw.get("tenantId") or metadata.get("tenant_id") or metadata.get("tenantId")
        projected = json.loads(json.dumps(raw))
        projected["id"] = persona_id
        projected["persona_id"] = persona_id
        projected.setdefault("name", raw.get("name"))
        projected.setdefault("mandate", raw.get("mandate"))
        projected.setdefault("lifecycle_state", raw.get("lifecycle_state"))
        projected.setdefault("created_at", raw.get("created_at"))
        projected.setdefault("strategy_family", raw.get("strategy_family"))
        projected.setdefault("status", raw.get("status"))
        projected.setdefault("updated_at", raw.get("updated_at"))
        projected.setdefault("metadata", raw.get("metadata", {}))
        projected["tenant_id"] = tenant_id
        projected["tenantId"] = tenant_id
        return projected

    @staticmethod
    def _project_service_session(raw: Dict[str, Any]) -> Dict[str, Any]:
        session_id = raw.get("session_id") or raw.get("id")
        projected = {
            "id": session_id,
            "session_id": session_id,
            "persona_id": raw.get("persona_id"),
            "session_type": raw.get("session_type"),
            "status": raw.get("status"),
            "started_at": raw.get("started_at"),
            "ended_at": raw.get("ended_at"),
            "capability_snapshot_id": raw.get("capability_snapshot_id"),
            "trace_id": raw.get("trace_id"),
            "request_id": raw.get("request_id"),
            "runtime_binding_id": raw.get("runtime_binding_id"),
            "runtime_id": raw.get("runtime_id"),
            "persona_capital_binding_id": raw.get("persona_capital_binding_id"),
            "deployment_stage": raw.get("deployment_stage"),
            "capital_pool_id": raw.get("capital_pool_id"),
            "context_bundle_ref": raw.get("context_bundle_ref"),
            "metadata": json.loads(json.dumps(raw.get("metadata", {}))),
        }
        authoritative_session_fields = (
            "binding_id",
            "runtime_identity",
            "runtime_kind",
            "deployment_mode",
            "state",
            "lifecycle_state",
            "active",
            "created_at",
            "updated_at",
            "last_heartbeat_at",
            "last_seen_at",
            "heartbeat_status",
            "freshness",
            "staleness",
            "stale",
            "stale_at",
            "stale_after_seconds",
            "degraded",
            "degraded_at",
            "last_error",
        )
        for field in authoritative_session_fields:
            if field in raw:
                projected[field] = json.loads(json.dumps(raw[field]))
        for field, value in raw.items():
            if field == "reason" or field.endswith("_reason") or field.endswith("_reasons"):
                projected[field] = json.loads(json.dumps(value))
        return projected

    @staticmethod
    def _project_service_evolution_decision(raw: Dict[str, Any]) -> Dict[str, Any]:
        decision_id = raw.get("decision_id") or raw.get("id")
        decision_state = raw.get("decision_state") or raw.get("status")
        linked_incident_id = raw.get("linked_incident_id") or raw.get("incident_ref")
        target_id = raw.get("target_id") or raw.get("artifact_id")
        return {
            "id": decision_id,
            "decision_id": decision_id,
            "program_id": raw.get("program_id"),
            "persona_id": raw.get("persona_id"),
            "capital_pool_id": raw.get("capital_pool_id"),
            "action_type": raw.get("action_type"),
            "risk_level": raw.get("risk_level"),
            "status": decision_state,
            "decision_state": decision_state,
            "incident_ref": linked_incident_id,
            "linked_incident_id": linked_incident_id,
            "linked_postmortem_id": raw.get("linked_postmortem_id"),
            "artifact_id": target_id,
            "target_type": raw.get("target_type"),
            "target_id": target_id,
            "target_version": raw.get("target_version"),
            "target_stage": raw.get("target_stage"),
            "approval_decision_id": raw.get("approval_decision_id"),
            "artifact_ref": raw.get("artifact_ref"),
            "score": raw.get("score"),
            "created_at": raw.get("created_at"),
            "updated_at": raw.get("updated_at"),
            "notes": raw.get("notes"),
            "rationale": raw.get("rationale"),
            "created_by_role": raw.get("created_by_role"),
            "created_by_id": raw.get("created_by_id"),
            "evidence_refs": raw.get("evidence_refs") or [],
            "threshold_snapshots": raw.get("threshold_snapshots") or [],
            "review_chain": raw.get("review_chain") or [],
            "proposed_changes": raw.get("proposed_changes"),
            "risk_assessment": raw.get("risk_assessment"),
            "required_approvals": raw.get("required_approvals"),
            "rollback_followthrough": raw.get("rollback_followthrough"),
            "metadata": raw.get("metadata"),
            "origin": raw.get("origin"),
            "provenance": raw.get("provenance"),
            "execution_result": raw.get("execution_result"),
        }

    def _derive_can_promote_to_paper(
        self,
        plan: Optional[Dict[str, Any]],
        decision: Optional[Dict[str, Any]],
    ) -> bool:
        if not plan:
            return False
        target_stage = str(plan.get("target_stage") or plan.get("stage") or "").lower()
        current_stage = str(plan.get("current_stage") or "").lower()
        plan_status = str(plan.get("status") or "").lower()
        decision_outcome = str((decision or {}).get("outcome") or "").lower()
        return (
            target_stage == "paper"
            and current_stage != "paper"
            and decision_outcome in {"approved", "approved_with_conditions"}
            and plan_status not in {"rejected", "aborted", "failed", "executed"}
        )

    @staticmethod
    def _derive_can_review_deployment_plan(
        plan: Optional[Dict[str, Any]],
        decision: Optional[Dict[str, Any]],
    ) -> bool:
        if not plan:
            return False
        plan_status = str(plan.get("status") or "").lower()
        decision_outcome = str((decision or {}).get("outcome") or "").lower()
        decision_state = str((decision or {}).get("state") or "").lower()
        if plan_status in {"approved", "rejected", "aborted", "failed", "executed"}:
            return False
        if decision_outcome in {"approved", "approved_with_conditions", "rejected"}:
            return False
        if decision_state in {"decided", "completed", "rejected"}:
            return False
        return True

    # ------------------------------------------------------------------ #
    # Catalog list surfaces (PS/CP/DP/RT)
    # ------------------------------------------------------------------ #

    def list_personas(
        self,
        lifecycle_state: Optional[str] = None,
        mandate: Optional[str] = None,
        strategy_family: Optional[str] = None,
        include_market_persona_defaults: bool = False,
    ) -> List[Dict[str, Any]]:
        local_personas = self._local_bff_persona_records()
        available, raw_personas = self._service.list_records("personas")
        if available:
            personas_by_id = {
                str((projected.get("persona_id") or projected.get("id") or "")): projected
                for projected in (self._project_service_persona(persona) for persona in raw_personas)
                if str(projected.get("persona_id") or projected.get("id") or "").strip()
            }
            personas_by_id.update(local_personas)
        else:
            personas_by_id = {}
            local_fallback = self._local_fallback("personas")
            if isinstance(local_fallback, dict):
                personas_by_id.update({
                    str(persona.get("persona_id") or persona.get("id") or key): persona
                    for key, persona in local_fallback.items()
                    if isinstance(persona, dict)
                })
            personas_by_id.update(local_personas)
        if include_market_persona_defaults:
            personas_by_id = self._merge_market_persona_records(
                "personas",
                personas_by_id,
                ["persona_id", "id"],
            )
        personas = [persona for key, persona in personas_by_id.items() if key]
        if lifecycle_state:
            personas = [p for p in personas if p.get("lifecycle_state") == lifecycle_state]
        if mandate:
            personas = [p for p in personas if p.get("mandate") == mandate]
        if strategy_family:
            personas = [p for p in personas if p.get("strategy_family") == strategy_family]
        anchor = [
            persona
            for persona in personas
            if str(persona.get("id") or persona.get("persona_id") or "") == "persona-alpha"
        ]
        rest = [
            persona
            for persona in personas
            if str(persona.get("id") or persona.get("persona_id") or "") != "persona-alpha"
        ]
        return anchor + sorted(
            rest,
            key=lambda x: str(x.get("created_at") or ""),
            reverse=True,
        )

    @staticmethod
    def _is_bff_local_persona(persona: Dict[str, Any]) -> bool:
        return (
            persona.get("persistenceMode") == "bff_local_dev_store"
            or persona.get("canonicalWriteAuthority") == "persona_registry_service"
        )

    def create_persona(
        self,
        *,
        persona_id: str,
        name: str,
        actor_id: str,
        created_at: Optional[str] = None,
        archetype: str = "generalist",
        lifecycle_state: str = "draft",
        risk_level: str = "low",
        mandate: Optional[str] = None,
        strategy_family: Optional[str] = None,
        traits: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        required_data_sources: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        timestamp = created_at or _utc_now_rfc3339()
        clean_metadata = json.loads(json.dumps(metadata or {}))
        clean_metadata.update({
            "owner": actor_id,
            "archetype": archetype,
            "risk_level": risk_level,
        })
        # Real persona identity (previously aliased to archetype). Traits carry the
        # persona's trading character so its OpenClaw SOUL is meaningful, not thin.
        clean_mandate = (str(mandate).strip() if mandate else "") or archetype
        clean_strategy = (str(strategy_family).strip() if strategy_family else "") or archetype
        if traits:
            clean_metadata["traits"] = json.loads(json.dumps(traits))
        record = {
            "id": persona_id,
            "persona_id": persona_id,
            "name": name,
            "mandate": clean_mandate,
            "strategy_family": clean_strategy,
            "lifecycle_state": lifecycle_state,
            "status": lifecycle_state,
            "created_at": timestamp,
            "updated_at": timestamp,
            "created_by": actor_id,
            "required_data_sources": json.loads(json.dumps(required_data_sources or [])),
            "metadata": clean_metadata,
            "canonicalWriteAuthority": "persona_registry_service",
            "persistenceMode": "bff_local_dev_store",
        }

        service_store_path = self._service._resolve_path("personas")
        if service_store_path is not None:
            available, service_personas = self._service.list_records(
                "personas",
                include_snapshot_fallback=False,
            )
            records = {
                str(existing.get("persona_id") or existing.get("id") or ""): json.loads(json.dumps(existing))
                for existing in service_personas
                if isinstance(existing, dict) and str(existing.get("persona_id") or existing.get("id") or "").strip()
            } if available else {}
            records[persona_id] = record
            if self._service.write_records("personas", records):
                return self._project_service_persona(record)

        personas = self._ensure_local_overlay_records("personas")
        personas[persona_id] = record
        self._save()
        return self._project_service_persona(record)

    def update_persona(
        self,
        persona_id: str,
        *,
        name: Optional[str] = None,
        actor_id: Optional[str] = None,
        updated_at: Optional[str] = None,
        archetype: Optional[str] = None,
        lifecycle_state: Optional[str] = None,
        risk_level: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not persona_id:
            return None
        existing = self.get_persona(persona_id)
        if existing is None:
            return None
        timestamp = updated_at or _utc_now_rfc3339()
        record = json.loads(json.dumps(existing))
        record["id"] = persona_id
        record["persona_id"] = persona_id
        if name is not None:
            record["name"] = name
        if lifecycle_state is not None:
            record["lifecycle_state"] = lifecycle_state
            record["status"] = lifecycle_state
        if archetype is not None:
            record["mandate"] = archetype
            record["strategy_family"] = archetype
        record["updated_at"] = timestamp

        clean_metadata = dict(record.get("metadata") if isinstance(record.get("metadata"), dict) else {})
        if metadata:
            clean_metadata.update(json.loads(json.dumps(metadata)))
        if actor_id is not None:
            clean_metadata["owner"] = actor_id
        if archetype is not None:
            clean_metadata["archetype"] = archetype
        if risk_level is not None:
            clean_metadata["risk_level"] = risk_level
        record["metadata"] = clean_metadata
        record["canonicalWriteAuthority"] = "persona_registry_service"
        record["persistenceMode"] = "bff_local_dev_store"

        personas = self._ensure_local_overlay_records("personas")
        personas[persona_id] = record
        self._save()
        return self._project_service_persona(record)

    def upsert_persona_capability_snapshot(
        self,
        *,
        snapshot_id: str,
        persona_id: str,
        capabilities: List[str],
        generated_at: Optional[str] = None,
        source_refs: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist an explicit Persona capability grant in the owner store.

        Lifecycle and deployment metadata never imply a capability.  Callers
        that provision an interaction-capable Persona must write this separate
        snapshot so eligibility can remain fail-closed.
        """
        clean_snapshot_id = str(snapshot_id or "").strip()
        clean_persona_id = str(persona_id or "").strip()
        if not clean_snapshot_id or not clean_persona_id:
            raise ValueError("snapshot_id and persona_id are required")
        clean_capabilities = list(dict.fromkeys(
            str(capability or "").strip()
            for capability in capabilities
            if str(capability or "").strip()
        ))
        timestamp = generated_at or _utc_now_rfc3339()
        record = {
            "id": clean_snapshot_id,
            "snapshot_id": clean_snapshot_id,
            "persona_id": clean_persona_id,
            "capabilities": clean_capabilities,
            "allowed_capabilities": list(clean_capabilities),
            "generated_at": timestamp,
            "updated_at": timestamp,
            "source_refs": json.loads(json.dumps(source_refs or [])),
            "metadata": json.loads(json.dumps(metadata or {})),
            "canonicalWriteAuthority": "persona_capability_service",
            "persistenceMode": "bff_local_dev_store",
        }

        service_store_path = self._service._resolve_path("capability_snapshots")
        if service_store_path is not None:
            available, service_snapshots = self._service.list_records(
                "capability_snapshots",
                include_snapshot_fallback=False,
            )
            records = {
                str(existing.get("snapshot_id") or existing.get("id") or ""): json.loads(json.dumps(existing))
                for existing in service_snapshots
                if isinstance(existing, dict)
                and str(existing.get("snapshot_id") or existing.get("id") or "").strip()
            } if available else {}
            records[clean_snapshot_id] = record
            if self._service.write_records("capability_snapshots", records):
                return json.loads(json.dumps(record))

        snapshots = self._ensure_local_overlay_records("capability_snapshots")
        snapshots[clean_snapshot_id] = record
        self._save()
        return json.loads(json.dumps(record))

    def list_capital_pools(
        self,
        status: Optional[str] = None,
        risk_policy_ref: Optional[str] = None,
        include_market_persona_defaults: bool = False,
    ) -> List[Dict[str, Any]]:
        local_pools = {
            **self._local_bff_write_records("capital_pools", ["pool_id", "id"]),
            **self._local_overlay_records("capital_pools"),
        }
        available, raw_pools = self._canonical.list_records("capital_pools")
        if available:
            pools_by_id = {
                str(pool.get("pool_id") or pool.get("id") or ""): pool
                for pool in (self._project_canonical_capital_pool(pool) for pool in raw_pools)
            }
            for pool_id, pool in local_pools.items():
                pools_by_id[str(pool.get("pool_id") or pool.get("id") or pool_id)] = pool
        else:
            pools_by_id = {
                str(pool.get("pool_id") or pool.get("id") or pool_id): pool
                for pool_id, pool in local_pools.items()
                if isinstance(pool, dict)
            }
        if include_market_persona_defaults:
            pools_by_id = self._merge_market_persona_records(
                "capital_pools",
                pools_by_id,
                ["pool_id", "id"],
            )
        pools = [pool for key, pool in pools_by_id.items() if key]
        if status:
            pools = [p for p in pools if p.get("status") == status]
        if risk_policy_ref:
            pools = [p for p in pools if p.get("risk_policy_ref") == risk_policy_ref]
        return sorted(
            pools,
            key=lambda x: (
                0 if str(x.get("id") or x.get("pool_id") or "") == "pool-main" else 1,
                str(x.get("id") or x.get("pool_id") or ""),
            ),
        )

    def list_bindings(
        self,
        persona_id: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        role: Optional[str] = None,
        validity: Optional[str] = None,
        include_market_persona_defaults: bool = False,
    ) -> List[Dict[str, Any]]:
        local_bindings = {
            **self._local_bff_write_records("persona_bindings", ["binding_id", "id"]),
            **self._local_overlay_records("persona_bindings"),
        }
        available, raw_bindings = self._canonical.list_records("persona_bindings")
        if available:
            bindings_by_id = {
                str(binding.get("binding_id") or binding.get("id") or ""): binding
                for binding in (self._project_canonical_binding(binding) for binding in raw_bindings)
            }
        else:
            bindings_by_id = {
                str(binding.get("binding_id") or binding.get("id") or binding_id): binding
                for binding_id, binding in (self._local_fallback("persona_bindings") or {}).items()
                if isinstance(binding, dict)
            }
        for binding_id, binding in local_bindings.items():
            key = str(binding.get("binding_id") or binding.get("id") or binding_id)
            if key:
                bindings_by_id[key] = json.loads(json.dumps(binding))
        if include_market_persona_defaults:
            bindings_by_id = self._merge_market_persona_records(
                "persona_bindings",
                bindings_by_id,
                ["binding_id", "id"],
            )
        bindings = [binding for key, binding in bindings_by_id.items() if key]
        if persona_id:
            bindings = [b for b in bindings if b.get("persona_id") == persona_id]
        if capital_pool_id:
            bindings = [b for b in bindings if b.get("capital_pool_id") == capital_pool_id]
        if role:
            bindings = [b for b in bindings if b.get("role") == role]
        if validity:
            bindings = [b for b in bindings if b.get("validity") == validity]
        return sorted(bindings, key=lambda x: x.get("id", ""))

    def create_persona_binding(
        self,
        *,
        binding_id: str,
        persona_id: str,
        capital_pool_id: str,
        actor_id: str,
        created_at: Optional[str] = None,
        role: str = "paper_owner",
        validity: str = "active",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        bindings = self._ensure_local_overlay_records("persona_bindings")
        timestamp = created_at or _utc_now_rfc3339()
        record = {
            "id": binding_id,
            "binding_id": binding_id,
            "persona_capital_binding_id": binding_id,
            "persona_id": persona_id,
            "capital_pool_id": capital_pool_id,
            "role": role,
            "validity": validity,
            "status": validity,
            "created_at": timestamp,
            "updated_at": timestamp,
            "created_by": actor_id,
            "metadata": json.loads(json.dumps(metadata or {})),
            "canonicalWriteAuthority": "capital_service",
            "persistenceMode": "bff_local_dev_store",
        }
        bindings[binding_id] = record
        self._save()
        return record

    def _deployment_saga_progress_for_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        available, sagas = self._canonical.list_records("deployment_sagas")
        if not available:
            return None
        matches = [
            saga
            for saga in sagas
            if str(saga.get("plan_id") or "") == plan_id
        ]
        if not matches:
            return None
        saga = sorted(
            matches,
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
            reverse=True,
        )[0]
        saga_id = str(saga.get("saga_id") or saga.get("id") or "")
        outbox_available, outbox = self._canonical.list_records("deployment_saga_outbox")
        saga_outbox = [
            record
            for record in (outbox if outbox_available else [])
            if str((record.get("event") or {}).get("aggregate_id") or "") == saga_id
        ]
        return self._project_deployment_saga_progress(saga, saga_outbox)

    @staticmethod
    def _project_deployment_saga_progress(
        saga: Dict[str, Any],
        outbox: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        saga_status = str(saga.get("status") or "")
        dlq_records = [
            record
            for record in outbox
            if str(record.get("status") or "").lower() == "dead_lettered"
        ]
        if saga_status == "completed":
            progress_status = "completed"
        elif saga_status in {"failed", "aborted"}:
            progress_status = "failed"
        elif dlq_records:
            progress_status = "blocked"
        elif saga_status == "awaiting_binding" and int(saga.get("last_sequence_no") or 0) <= 1:
            progress_status = "pending"
        else:
            progress_status = "running"

        blocked_reason = None
        if dlq_records:
            blocked = sorted(
                dlq_records,
                key=lambda item: int((item.get("event") or {}).get("sequence_no") or 0),
            )[-1]
            blocked_reason = blocked.get("blocked_reason") or blocked.get("last_error")
        elif saga.get("failure_reason"):
            blocked_reason = saga.get("failure_reason")

        retry_state = []
        for record in sorted(
            outbox,
            key=lambda item: int((item.get("event") or {}).get("sequence_no") or 0),
        ):
            event = record.get("event") if isinstance(record.get("event"), dict) else {}
            retry_state.append(
                {
                    "event_id": event.get("event_id"),
                    "event_type": event.get("event_type"),
                    "sequence_no": event.get("sequence_no"),
                    "status": record.get("status"),
                    "delivery_attempts": record.get("delivery_attempts", 0),
                    "replay_count": record.get("replay_count", 0),
                    "last_error": record.get("last_error"),
                    "last_attempt_at": record.get("last_attempt_at"),
                    "next_retry_at": record.get("next_retry_at"),
                    "blocked_reason": record.get("blocked_reason"),
                    "dlq_at": record.get("dlq_at"),
                    "last_replayed_at": record.get("last_replayed_at"),
                    "retry_policy": json.loads(json.dumps(record.get("retry_policy") or {})),
                }
            )

        latest_policy = next(
            (
                item.get("retry_policy")
                for item in reversed(retry_state)
                if isinstance(item.get("retry_policy"), dict) and item.get("retry_policy")
            ),
            {},
        )
        return {
            "saga_id": saga.get("saga_id") or saga.get("id"),
            "plan_id": saga.get("plan_id"),
            "progress_status": progress_status,
            "saga_status": saga_status,
            "current_step": saga.get("current_step"),
            "blocked_reason": blocked_reason,
            "retry_policy": {
                "max_attempts": int(latest_policy.get("max_attempts") or 3),
                "retry_delay_seconds": int(latest_policy.get("retry_delay_seconds") or 0),
                "retryable": bool(latest_policy.get("retryable", True)),
            },
            "retry_state": retry_state,
            "completed_steps": [
                str(item.get("step"))
                for item in saga.get("history", [])
                if isinstance(item, dict) and item.get("step")
            ],
            "pending_event_count": sum(
                1
                for record in outbox
                if str(record.get("status") or "").lower() == "pending"
            ),
            "dlq_count": len(dlq_records),
        }

    def list_deployment_plans(
        self,
        status: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        include_fixture_pack: bool = True,
    ) -> List[Dict[str, Any]]:
        local_plans = {
            **self._local_bff_write_records("deployment_plans", ["plan_id", "id"]),
            **self._local_overlay_records("deployment_plans"),
        }
        available, raw_plans = self._canonical.list_records("deployment_plans")
        if available:
            runtime_by_plan: Dict[str, Dict[str, Any]] = {}
            runtime_available, raw_runtime = self._canonical.list_records("runtime_bindings")
            if runtime_available:
                for runtime in raw_runtime:
                    plan_id = str(runtime.get("plan_id") or runtime.get("deployment_plan_id") or "")
                    if plan_id:
                        runtime_by_plan[plan_id] = runtime
            plans = []
            for raw in raw_plans:
                plan_id = str(raw.get("plan_id") or raw.get("id") or "")
                runtime_binding = runtime_by_plan.get(plan_id)
                runtime_binding_id = None
                if runtime_binding:
                    runtime_binding_id = str(runtime_binding.get("binding_id") or runtime_binding.get("id") or "")
                plans.append(
                    self._project_canonical_deployment_plan(
                        raw,
                        runtime_binding_id,
                        saga_progress=self._deployment_saga_progress_for_plan(plan_id),
                    )
                )
        else:
            plans = list((self._local_fallback("deployment_plans") or {}).values())
        if local_plans:
            plans_by_id = {str(p.get("id") or p.get("plan_id") or ""): p for p in plans}
            for overlay_key, plan in local_plans.items():
                key = str(plan.get("id") or plan.get("plan_id") or overlay_key)
                plans_by_id[key] = plan
            plans = [plan for key, plan in plans_by_id.items() if key]
        if status:
            plans = [
                p for p in plans
                if str(p.get("status") or "").lower() == status.lower()
            ]
        if capital_pool_id:
            plans = [
                p for p in plans
                if str(p.get("capital_pool_id") or p.get("target_pool_id") or "") == capital_pool_id
            ]
        if not include_fixture_pack:
            plans = [p for p in plans if not _is_fixture_pack_record(p)]
        return sorted(plans, key=lambda x: x.get("id", ""))

    def list_approval_decisions(
        self,
        outcome: Optional[str] = None,
        state: Optional[str] = None,
        include_fixture_pack: bool = True,
    ) -> List[Dict[str, Any]]:
        available, raw_decisions = self._canonical.list_records("approval_decisions")
        if available:
            decisions = [self._project_canonical_approval_decision(decision) for decision in raw_decisions]
        else:
            decisions = list((self._local_fallback("approval_decisions") or {}).values())
        if outcome:
            decisions = [d for d in decisions if str(d.get("outcome") or "").lower() == outcome.lower()]
        if state:
            decisions = [
                d for d in decisions
                if str(d.get("state") or "").lower() == state.lower()
            ]
        if not include_fixture_pack:
            decisions = [d for d in decisions if not _is_fixture_pack_record(d)]
        anchor = [
            decision
            for decision in decisions
            if str(decision.get("id") or decision.get("decision_id") or "") == "approval-042"
        ]
        rest = [
            decision
            for decision in decisions
            if str(decision.get("id") or decision.get("decision_id") or "") != "approval-042"
        ]
        return anchor + sorted(rest, key=lambda x: str(x.get("decided_at") or ""), reverse=True)

    def list_runtime_bindings(
        self,
        deployment_mode: Optional[str] = None,
        version: Optional[str] = None,
        include_market_persona_defaults: bool = False,
    ) -> List[Dict[str, Any]]:
        local_bindings = {
            **self._local_bff_write_records("runtime_bindings", ["runtime_id", "runtime_binding_id", "binding_id", "id"]),
            **self._local_overlay_records("runtime_bindings"),
        }
        available, raw_bindings = self._canonical.list_records("runtime_bindings")
        if available:
            bindings_by_id = {
                str(
                    binding.get("runtime_id")
                    or binding.get("runtime_binding_id")
                    or binding.get("binding_id")
                    or binding.get("id")
                    or ""
                ): binding
                for binding in (self._project_canonical_runtime_binding(binding) for binding in raw_bindings)
            }
        else:
            bindings_by_id = {
                str(
                    binding.get("runtime_id")
                    or binding.get("runtime_binding_id")
                    or binding.get("binding_id")
                    or binding.get("id")
                    or binding_id
                ): binding
                for binding_id, binding in (self._local_fallback("runtime_bindings") or {}).items()
                if isinstance(binding, dict)
            }
        for binding_id, binding in local_bindings.items():
            key = str(
                binding.get("runtime_id")
                or binding.get("runtime_binding_id")
                or binding.get("binding_id")
                or binding.get("id")
                or binding_id
            )
            if key:
                bindings_by_id[key] = json.loads(json.dumps(binding))
        if include_market_persona_defaults:
            bindings_by_id = self._merge_market_persona_records(
                "runtime_bindings",
                bindings_by_id,
                ["runtime_id", "runtime_binding_id", "binding_id", "id"],
            )
        persona_capital_bindings = self.list_bindings(
            include_market_persona_defaults=include_market_persona_defaults,
        )
        capital_binding_by_id = {
            str(binding.get("binding_id") or binding.get("id") or "").strip(): binding
            for binding in persona_capital_bindings
            if str(binding.get("binding_id") or binding.get("id") or "").strip()
        }
        # Legacy paper runtimes predate the explicit persona_id column.  Reconcile
        # only through typed, exact identity references.  A canonical
        # persona-capital binding owner is authoritative even when the runtime
        # still carries a stale seed persona_id.  Registry declarations are a
        # fail-closed fallback: a reference must identify exactly one persona.
        declaration_indexes: Dict[str, Dict[str, set[str]]] = {
            "runtime_id": {},
            "runtime_binding_id": {},
            "persona_capital_binding_id": {},
        }
        persona_declarations: Dict[str, Dict[str, Any]] = {}
        for persona in self.list_personas(
            include_market_persona_defaults=include_market_persona_defaults,
        ):
            persona_id = str(persona.get("persona_id") or persona.get("id") or "").strip()
            if not persona_id:
                continue
            metadata = persona.get("metadata") if isinstance(persona.get("metadata"), dict) else {}
            declaration = {**persona, **metadata}
            persona_declarations[persona_id] = declaration
            declaration_values = {
                "runtime_id": declaration.get("runtime_id") or declaration.get("runtimeId"),
                "runtime_binding_id": (
                    declaration.get("runtime_binding_id")
                    or declaration.get("runtimeBindingId")
                ),
                "persona_capital_binding_id": (
                    declaration.get("persona_capital_binding_id")
                    or declaration.get("personaCapitalBindingId")
                ),
            }
            for field, index in declaration_indexes.items():
                value = str(declaration_values.get(field) or "").strip()
                if value:
                    index.setdefault(value, set()).add(persona_id)

        bindings = []
        for key, binding in bindings_by_id.items():
            if not key:
                continue
            projected = json.loads(json.dumps(binding))
            persona_binding_id = str(projected.get("persona_capital_binding_id") or "").strip()

            capital_binding = capital_binding_by_id.get(persona_binding_id, {})
            canonical_persona_id = str(capital_binding.get("persona_id") or "").strip()
            if (
                not canonical_persona_id
                and str(projected.get("canonicalWriteAuthority") or "").strip()
                == "runtime_manager_service"
            ):
                canonical_persona_id = str(projected.get("persona_id") or "").strip()
            if canonical_persona_id:
                projected["persona_id"] = canonical_persona_id
                if not str(projected.get("capital_pool_id") or "").strip():
                    capital_pool_id = str(capital_binding.get("capital_pool_id") or "").strip()
                    if capital_pool_id:
                        projected["capital_pool_id"] = capital_pool_id
            else:
                candidates: set[str] = set()
                typed_references = {
                    "runtime_id": str(projected.get("runtime_id") or "").strip(),
                    "runtime_binding_id": str(
                        projected.get("runtime_binding_id")
                        or projected.get("binding_id")
                        or projected.get("id")
                        or ""
                    ).strip(),
                    "persona_capital_binding_id": persona_binding_id,
                }
                for field, reference in typed_references.items():
                    if reference:
                        candidates.update(declaration_indexes[field].get(reference, set()))
                if len(candidates) == 1:
                    resolved_persona_id = next(iter(candidates))
                    projected["persona_id"] = resolved_persona_id
                    declaration = persona_declarations.get(resolved_persona_id, {})
                    if not persona_binding_id:
                        declared_binding_id = str(
                            declaration.get("persona_capital_binding_id")
                            or declaration.get("personaCapitalBindingId")
                            or ""
                        ).strip()
                        if declared_binding_id:
                            projected["persona_capital_binding_id"] = declared_binding_id
                    if not str(projected.get("capital_pool_id") or "").strip():
                        declared_pool_id = str(
                            declaration.get("capital_pool_id")
                            or declaration.get("capitalPoolId")
                            or declaration.get("legacy_paper_capital_pool_id")
                            or declaration.get("legacyPaperCapitalPoolId")
                            or ""
                        ).strip()
                        if declared_pool_id:
                            projected["capital_pool_id"] = declared_pool_id
                else:
                    # No declaration or conflicting declarations: do not let
                    # a stale raw persona_id assign ownership.
                    projected["persona_id"] = None
            bindings.append(projected)
        if deployment_mode:
            bindings = [
                b for b in bindings
                if str(b.get("deployment_stage") or b.get("deployment_mode") or "").lower() == deployment_mode.lower()
            ]
        if version:
            bindings = [
                b for b in bindings
                if str(b.get("artifact_version") or b.get("version") or "") == version
            ]
        return sorted(bindings, key=lambda x: x.get("id", ""))

    @staticmethod
    def _paper_runtime_monitoring_staleness_marker(session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        staleness = session.get("staleness")
        if not isinstance(staleness, dict):
            return None
        status = str(staleness.get("status") or "").strip().lower()
        reason = str(staleness.get("reason") or "").strip()
        if status == "stale" or reason:
            return dict(staleness)
        return None

    @staticmethod
    def _paper_runtime_monitoring_session_active(session: Dict[str, Any]) -> bool:
        if session.get("ended_at") not in (None, ""):
            return False
        status = str(session.get("status") or "").strip().lower()
        if status in {"ended", "stale", "failed"}:
            return False
        if ReadSurfaceStore._paper_runtime_monitoring_staleness_marker(session) is not None:
            return False
        explicit = session.get("active")
        if explicit is not None:
            return bool(explicit)
        return True

    @staticmethod
    def _paper_runtime_monitoring_sort_key(session: Dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(
                session.get("last_heartbeat_at")
                or session.get("ended_at")
                or session.get("started_at")
                or ""
            ),
            str(session.get("started_at") or ""),
            str(session.get("session_id") or session.get("id") or ""),
        )

    def list_paper_runtime_monitoring_sessions(self) -> List[Dict[str, Any]]:
        local_sessions = self._local_overlay_records("paper_runtime_monitoring_sessions")
        available, raw_sessions = self._canonical.list_records("paper_runtime_monitoring_sessions")
        if available:
            sessions_by_id = {
                str(session.get("session_id") or session.get("id") or ""): session
                for session in raw_sessions
                if str(session.get("session_id") or session.get("id") or "").strip()
            }
            for session_id, session in local_sessions.items():
                key = str(session.get("session_id") or session.get("id") or session_id)
                if key:
                    sessions_by_id[key] = session
            sessions = [session for key, session in sessions_by_id.items() if key]
        else:
            sessions = list(local_sessions.values()) or list(
                (self._local_fallback("paper_runtime_monitoring_sessions") or {}).values()
            )
        return sorted(
            [json.loads(json.dumps(session)) for session in sessions],
            key=self._paper_runtime_monitoring_sort_key,
            reverse=True,
        )

    def list_authoritative_paper_runtime_monitoring_sessions(self) -> List[Dict[str, Any]]:
        """Return only paper-fleet owner records, never local/snapshot substitutes.

        Persona provisioning is a safety-relevant lifecycle gate.  Local BFF
        overlays and bundled snapshots remain useful for operator read views,
        but they cannot prove that the canonical paper worker has joined its
        RuntimeBinding.  An unavailable owner therefore reads back as no
        authoritative evidence and leaves provisioning pending.
        """

        available, raw_sessions = self._canonical.list_records(
            "paper_runtime_monitoring_sessions",
            include_snapshot_fallback=False,
        )
        if not available:
            return []
        return sorted(
            [
                json.loads(json.dumps(session))
                for session in raw_sessions
                if isinstance(session, dict)
            ],
            key=self._paper_runtime_monitoring_sort_key,
            reverse=True,
        )

    def get_paper_runtime_monitoring_session(
        self,
        *,
        runtime_id: Optional[str] = None,
        binding_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        runtime_id = str(runtime_id or "").strip()
        binding_id = str(binding_id or "").strip()
        if not runtime_id and not binding_id:
            return None
        matches = []
        for session in self.list_paper_runtime_monitoring_sessions():
            session_runtime_id = str(session.get("runtime_id") or "").strip()
            session_binding_id = str(
                session.get("binding_id") or session.get("runtime_binding_id") or ""
            ).strip()
            if binding_id and session_binding_id == binding_id:
                matches.append(session)
                continue
            if runtime_id and session_runtime_id == runtime_id:
                matches.append(session)
        if not matches:
            return None
        active = [
            session for session in matches
            if self._paper_runtime_monitoring_session_active(session)
        ]
        selected = (active or matches)[0]
        selected["active"] = self._paper_runtime_monitoring_session_active(selected)
        return selected

    def list_registry_entries(self) -> List[Dict[str, Any]]:
        available, raw_entries = self._canonical.list_records("registry_entries")
        if available and raw_entries:
            return list(raw_entries)
        return list((self._local_fallback("registry_entries") or {}).values())

    def get_deployment_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        overlay = self._local_overlay_records("deployment_plans").get(plan_id)
        if overlay is not None:
            return overlay
        available, raw = self._canonical.deployment_plan(plan_id)
        if available:
            if raw is None:
                return None
            _, runtime_binding = self._canonical.runtime_binding_for_plan(plan_id)
            runtime_binding_id = None
            if runtime_binding:
                runtime_binding_id = str(runtime_binding.get("binding_id") or runtime_binding.get("id") or "")
            return self._project_canonical_deployment_plan(
                raw,
                runtime_binding_id or None,
                saga_progress=self._deployment_saga_progress_for_plan(plan_id),
            )
        return (self._local_fallback("deployment_plans") or {}).get(plan_id)

    def get_approval_decision(self, decision_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not decision_id:
            return None
        available, raw = self._canonical.approval_decision(decision_id)
        if available:
            return self._project_canonical_approval_decision(raw) if raw else None
        return (self._local_fallback("approval_decisions") or {}).get(decision_id)

    def get_canonical_approval_decision_readback(
        self, decision_id: Optional[str]
    ) -> Dict[str, Any]:
        """Preserve canonical availability; never substitute local fixtures."""
        if not decision_id:
            return {"available": False, "record": None, "source": "canonical.approval_decision"}
        available, raw = self._canonical.approval_decision(decision_id)
        return {
            "available": bool(available),
            "record": self._project_canonical_candidate_approval(raw) if available and raw else None,
            "source": "canonical.approval_decision",
        }

    @staticmethod
    def _project_canonical_candidate_approval(raw: Dict[str, Any]) -> Dict[str, Any]:
        """Preserve the checksum-covered candidate FormalApprovalReceipt."""
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        receipt = {
            "approval_decision_id": raw.get("approval_decision_id") or raw.get("decision_id") or raw.get("id"),
            "authority": raw.get("authority"),
            "tenant_id": raw.get("tenant_id") or metadata.get("tenant_id"),
            "proposal_id": raw.get("proposal_id"),
            "revision": raw.get("revision") or raw.get("proposal_revision"),
            "proposal_digest": raw.get("proposal_digest") or raw.get("proposal_content_digest"),
            "validation_receipt_id": raw.get("validation_receipt_id"),
            "validation_receipt_sha256": raw.get("validation_receipt_sha256"),
            "proposer_id": raw.get("proposer_id"),
            "reviewer_id": raw.get("reviewer_id") or raw.get("actor_id"),
            "outcome": raw.get("outcome") or raw.get("decision"),
            "self_approval": raw.get("self_approval"),
            "decided_at": raw.get("decided_at"),
            "expires_at": raw.get("expires_at"),
            "receipt_sha256": raw.get("receipt_sha256"),
            "execution_authority": raw.get("execution_authority"),
        }
        return {
            **receipt,
            "revoked_at": raw.get("revoked_at"),
            "superseded_by": raw.get("superseded_by"),
            "decision_state": raw.get("decision_state") or raw.get("state"),
        }

    def get_capital_pool(self, pool_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not pool_id:
            return None
        # Cheap fast paths for the common local/canonical cases.
        local_pool = self._local_overlay_records("capital_pools").get(pool_id)
        if isinstance(local_pool, dict):
            return local_pool
        available, raw = self._canonical.capital_pool(pool_id)
        if available and raw:
            return self._project_canonical_capital_pool(raw)
        # Definitive fallback: resolve against the EXACT set list_capital_pools() surfaces, so any
        # pool the list shows has a working detail endpoint. This covers every source the list
        # merges — the BFF local dev write store (persona-created paper pools), market-persona
        # default pools (pool-*-paper), and canonical — without this method having to re-enumerate
        # (and inevitably miss) each one. Previously these 404'd even though they appear in the list.
        target = str(pool_id)
        for pool in self.list_capital_pools(include_market_persona_defaults=True):
            if str(pool.get("id") or pool.get("pool_id") or "") == target:
                return pool
        return None

    def create_capital_pool(
        self,
        *,
        pool_id: str,
        name: str,
        actor_id: str,
        created_at: Optional[str] = None,
        risk_policy_ref: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        status: str = "draft",
    ) -> Dict[str, Any]:
        pools = self._ensure_local_overlay_records("capital_pools")
        timestamp = created_at or _utc_now_rfc3339()
        record = {
            "id": pool_id,
            "pool_id": pool_id,
            "name": name,
            "status": status,
            "risk_policy_ref": risk_policy_ref,
            "params": params or {},
            "created_at": timestamp,
            "updated_at": timestamp,
            "created_by": actor_id,
            "metadata": {
                "created_via": "POST /bff/personas",
                "persistenceMode": "bff_local_dev_store",
            },
            "canonicalWriteAuthority": "capital_service",
            "persistenceMode": "bff_local_dev_store",
        }
        pools[pool_id] = record
        self._save()
        return record

    def create_runtime_binding(
        self,
        *,
        runtime_id: str,
        name: str,
        persona_id: str,
        binding_id: str,
        deployment_plan_id: str,
        runtime_kind: str,
        actor_id: str,
        created_at: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        state: str = "stopped",
    ) -> Dict[str, Any]:
        runtimes = self._ensure_local_overlay_records("runtime_bindings")
        timestamp = created_at or _utc_now_rfc3339()
        clean_params = json.loads(json.dumps(params or {}))
        clean_state = str(state or "stopped").strip() or "stopped"
        capital_pool_id = clean_params.get("capital_pool_id")
        record = {
            "id": runtime_id,
            "runtime_id": runtime_id,
            "name": name,
            "state": clean_state,
            "status": clean_state,
            "persona_id": persona_id,
            "binding_id": binding_id,
            "runtime_binding_id": binding_id,
            "persona_capital_binding_id": binding_id,
            "deployment_plan_id": deployment_plan_id,
            "plan_id": deployment_plan_id,
            "runtime_kind": runtime_kind,
            "deployment_stage": runtime_kind,
            "deployment_mode": runtime_kind,
            "capital_pool_id": capital_pool_id,
            "params": clean_params,
            "created_at": timestamp,
            "updated_at": timestamp,
            "created_by": actor_id,
            "metadata": {
                "created_via": "POST /bff/runtimes",
                "persistenceMode": "bff_local_dev_store",
            },
            "canonicalWriteAuthority": "runtime_manager_service",
            "persistenceMode": "bff_local_dev_store",
        }
        runtimes[runtime_id] = record
        self._save()
        return record

    def create_deployment_plan(
        self,
        *,
        plan_id: str,
        binding_id: str,
        artifact_id: str,
        deployment_mode: str,
        capital_pool_id: str,
        actor_id: str,
        created_at: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        locked: bool = False,
        status: str = "pending_approval",
    ) -> Dict[str, Any]:
        plans = self._ensure_local_overlay_records("deployment_plans")
        timestamp = created_at or _utc_now_rfc3339()
        clean_params = json.loads(json.dumps(params or {}))
        record = {
            "id": plan_id,
            "plan_id": plan_id,
            "binding_id": binding_id,
            "persona_capital_binding_id": binding_id,
            "artifact_id": artifact_id,
            "deployment_mode": deployment_mode,
            "deployment_stage": deployment_mode,
            "target_stage": deployment_mode,
            "capital_pool_id": capital_pool_id,
            "target_pool_id": capital_pool_id,
            "status": status,
            "locked": bool(locked),
            "params": clean_params,
            "created_at": timestamp,
            "updated_at": timestamp,
            "created_by": actor_id,
            "metadata": {
                "created_via": "POST /api/v1/deployment-plans",
                "persistenceMode": "bff_local_dev_store",
            },
            "canonicalWriteAuthority": "deployment_service",
            "persistenceMode": "bff_local_dev_store",
        }
        plans[plan_id] = record
        self._save()
        return record

    def patch_capital_pool(
        self,
        pool_id: str,
        *,
        patch: Dict[str, Any],
        actor_id: str,
        updated_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        pools = self._ensure_local_overlay_records("capital_pools")
        record = pools.get(pool_id)
        if record is None:
            existing = self.get_capital_pool(pool_id)
            if existing is None:
                return None
            record = dict(existing)
            pools[pool_id] = record
        timestamp = updated_at or _utc_now_rfc3339()
        for field in ("name", "status", "risk_policy_ref", "params"):
            if field in patch:
                record[field] = patch[field]
        record["updated_at"] = timestamp
        record["updated_by"] = actor_id
        self._save()
        return record

    def get_binding(self, binding_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not binding_id:
            return None
        for binding in self.list_bindings():
            if str(binding.get("binding_id") or binding.get("id") or "") == str(binding_id):
                return json.loads(json.dumps(binding))
        return None

    def get_bindings_for_pool(self, pool_id: Optional[str]) -> List[Dict[str, Any]]:
        if not pool_id:
            return []
        return self.list_bindings(capital_pool_id=pool_id)

    def get_bindings_for_persona(self, persona_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        """Return all bindings where the given persona_id is the owner.

        Returns None when the persona itself cannot be verified (degraded mode).
        """
        if not persona_id:
            return None
        if self.get_persona(persona_id) is None:
            return None
        return self.list_bindings(persona_id=persona_id)

    # ------------------------------------------------------------------ #
    # Ranking formulas
    # ------------------------------------------------------------------ #

    def list_ranking_formulas(
        self,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        # Prefer service store (projected file); merge overlay writes on top.
        all_records = self._read_dataset_records("ranking_formulas")
        if not all_records:
            all_records = list(self._local_overlay_records("ranking_formulas").values())
        items = [json.loads(json.dumps(r)) for r in all_records if isinstance(r, dict)]
        if status:
            items = [i for i in items if i.get("status") == status]
        return sorted(items, key=lambda x: x.get("id", ""))

    def get_ranking_formula(self, formula_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not formula_id:
            return None
        overlay = self._local_overlay_records("ranking_formulas").get(formula_id)
        if overlay is not None:
            return overlay
        available, service_records = self._service.list_records("ranking_formulas")
        if available and service_records:
            for record in service_records:
                if not isinstance(record, dict):
                    continue
                rid = str(record.get("formula_id") or record.get("id") or "")
                if rid == formula_id:
                    return json.loads(json.dumps(record))
        return None

    def create_ranking_formula(
        self,
        *,
        name: str,
        description: str,
        actor_id: str,
        created_at: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        formulas = self._ensure_local_overlay_records("ranking_formulas")
        timestamp = created_at or _utc_now_rfc3339()
        formula_id = f"rf-{timestamp[:10].replace('-', '')}-{len(formulas) + 1:03d}"
        while formula_id in formulas:
            formula_id = f"rf-{timestamp[:10].replace('-', '')}-{len(formulas) + 2:03d}"
        record = {
            "id": formula_id,
            "formula_id": formula_id,
            "name": name,
            "description": description,
            "status": "active",
            "params": params or {},
            "created_at": timestamp,
            "updated_at": timestamp,
            "created_by": actor_id,
        }
        formulas[formula_id] = record
        self._save()
        return record

    def patch_ranking_formula(
        self,
        formula_id: str,
        *,
        patch: Dict[str, Any],
        actor_id: str,
        updated_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        formulas = self._local_overlay_records("ranking_formulas")
        record = formulas.get(formula_id)
        if record is None:
            return None
        timestamp = updated_at or _utc_now_rfc3339()
        for field in ("name", "description", "status", "params"):
            if field in patch:
                record[field] = patch[field]
        record["updated_at"] = timestamp
        record["updated_by"] = actor_id
        self._save()
        return record

    # ------------------------------------------------------------------ #
    # Rebalances
    # ------------------------------------------------------------------ #

    def list_rebalances(
        self,
        status: Optional[str] = None,
        pool_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        records = dict(self._local_overlay_records("rebalances"))
        available, authoritative = self._canonical.list_records("rebalances")
        if available:
            merged = {
                str(item.get("rebalance_id") or item.get("id") or ""): item
                for item in authoritative
                if isinstance(item, dict)
                and str(item.get("rebalance_id") or item.get("id") or "").strip()
            }
            # Compatibility-only local records may coexist; they cannot replace
            # an owner record with the same stable identity.
            for key, item in records.items():
                merged.setdefault(str(key), item)
            records = merged
        else:
            local_fallback = self._local_fallback("rebalances")
            if isinstance(local_fallback, dict):
                merged = dict(local_fallback)
                merged.update(records)
                records = merged
        items = list(records.values())
        if status:
            items = [i for i in items if i.get("status") == status]
        if pool_id:
            items = [i for i in items if i.get("capital_pool_id") == pool_id]
        return sorted(items, key=lambda x: str(x.get("created_at") or ""), reverse=True)

    def get_rebalance(self, rebalance_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not rebalance_id:
            return None
        available, authoritative = self._canonical.list_records("rebalances")
        if available:
            for item in authoritative:
                if str(item.get("rebalance_id") or item.get("id") or "") == str(rebalance_id):
                    return json.loads(json.dumps(item))
        overlay = self._local_overlay_records("rebalances").get(rebalance_id)
        if overlay is not None:
            return overlay
        local_fallback = self._local_fallback("rebalances")
        if isinstance(local_fallback, dict):
            return local_fallback.get(rebalance_id)
        return None

    def list_capital_allocations(
        self,
        *,
        capital_pool_id: Optional[str] = None,
        persona_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        available, records = self._canonical.list_records("capital_allocations")
        if not available:
            return []
        items = [json.loads(json.dumps(item)) for item in records if isinstance(item, dict)]
        if capital_pool_id:
            items = [
                item
                for item in items
                if str(item.get("capital_pool_id") or "") == str(capital_pool_id)
            ]
        if persona_id:
            items = [
                item
                for item in items
                if str(item.get("persona_id") or "") == str(persona_id)
            ]
        return sorted(
            items,
            key=lambda item: (
                str(item.get("capital_pool_id") or ""),
                str(item.get("persona_id") or ""),
                str(item.get("capital_sleeve_id") or item.get("sleeve_id") or ""),
            ),
        )

    def list_containments(
        self,
        *,
        persona_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        available, records = self._canonical.list_records("containments")
        if not available:
            return []
        items = [json.loads(json.dumps(item)) for item in records if isinstance(item, dict)]
        if persona_id:
            items = [
                item
                for item in items
                if str(item.get("persona_id") or "") == str(persona_id)
            ]
        return sorted(
            items,
            key=lambda item: str(
                item.get("executed_at")
                or item.get("updated_at")
                or item.get("applied_at")
                or item.get("created_at")
                or ""
            ),
            reverse=True,
        )

    def get_persona_containment(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not persona_id:
            return None
        items = self.list_containments(persona_id=str(persona_id))
        return items[0] if items else None

    def create_rebalance(
        self,
        *,
        capital_pool_id: str,
        actor_id: str,
        created_at: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
        proposal: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        rebalances = self._ensure_local_overlay_records("rebalances")
        timestamp = created_at or _utc_now_rfc3339()
        rebalance_id = f"rb-{timestamp[:10].replace('-', '')}-{len(rebalances) + 1:03d}"
        while rebalance_id in rebalances:
            rebalance_id = f"rb-{timestamp[:10].replace('-', '')}-{len(rebalances) + 2:03d}"
        record = {
            "id": rebalance_id,
            "rebalance_id": rebalance_id,
            "capital_pool_id": capital_pool_id,
            "status": "pending",
            "reason": reason or "",
            "params": params or {},
            "created_at": timestamp,
            "updated_at": timestamp,
            "created_by": actor_id,
            "command_audit": {
                "submitted_by": actor_id,
                "submitted_at": timestamp,
            },
        }
        if proposal:
            record.update(json.loads(json.dumps(proposal)))
        rebalances[rebalance_id] = record
        self._save()
        return record

    # ------------------------------------------------------------------ #
    # Ranking snapshot / allocation-evaluation admission records
    # ------------------------------------------------------------------ #

    def put_ranking_snapshot(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Persist an immutable BFF-owned ranking snapshot admission record."""
        snapshot_id = str(
            record.get("ranking_snapshot_id") or record.get("snapshot_id") or ""
        ).strip()
        content_digest = str(record.get("content_digest") or "").strip()
        if not snapshot_id or not content_digest:
            raise ValueError("ranking snapshot id and content_digest are required")
        snapshots = self._ensure_local_overlay_records("ranking_snapshots")
        existing = snapshots.get(snapshot_id)
        if isinstance(existing, dict):
            if str(existing.get("content_digest") or "") != content_digest:
                raise ValueError("ranking snapshot id already has different content")
            changed = False
            incoming_variants = record.get("evidence_assertion_digests")
            if isinstance(incoming_variants, dict):
                existing_variants = existing.setdefault(
                    "evidence_assertion_digests", {}
                )
                if not isinstance(existing_variants, dict):
                    existing_variants = {}
                    existing["evidence_assertion_digests"] = existing_variants
                for persona_id, raw_digests in incoming_variants.items():
                    if not isinstance(raw_digests, list):
                        continue
                    merged = sorted({
                        str(value).strip()
                        for value in (
                            list(existing_variants.get(str(persona_id)) or [])
                            + raw_digests
                        )
                        if str(value).strip()
                    })
                    if merged != existing_variants.get(str(persona_id)):
                        existing_variants[str(persona_id)] = merged
                        changed = True
            if changed:
                self._save()
            return json.loads(json.dumps(existing))
        stored = json.loads(json.dumps({**record, "ranking_snapshot_id": snapshot_id}))
        snapshots[snapshot_id] = stored
        self._save()
        return json.loads(json.dumps(stored))

    def get_ranking_snapshot(self, snapshot_id: Optional[str]) -> Optional[Dict[str, Any]]:
        clean_id = str(snapshot_id or "").strip()
        if not clean_id:
            return None
        record = self._local_overlay_records("ranking_snapshots").get(clean_id)
        return json.loads(json.dumps(record)) if isinstance(record, dict) else None

    def put_allocation_evaluation(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Persist one immutable server-materialized allocation evaluation."""
        evaluation_id = str(record.get("allocation_evaluation_id") or "").strip()
        content_digest = str(record.get("content_digest") or "").strip()
        if not evaluation_id or not content_digest:
            raise ValueError("allocation evaluation id and content_digest are required")
        evaluations = self._ensure_local_overlay_records("allocation_evaluations")
        existing = evaluations.get(evaluation_id)
        if isinstance(existing, dict):
            if str(existing.get("content_digest") or "") != content_digest:
                raise ValueError("allocation evaluation id already has different content")
            return json.loads(json.dumps(existing))
        stored = json.loads(
            json.dumps({**record, "allocation_evaluation_id": evaluation_id})
        )
        evaluations[evaluation_id] = stored
        self._save()
        return json.loads(json.dumps(stored))

    def get_allocation_evaluation(
        self,
        evaluation_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        clean_id = str(evaluation_id or "").strip()
        if not clean_id:
            return None
        record = self._local_overlay_records("allocation_evaluations").get(clean_id)
        return json.loads(json.dumps(record)) if isinstance(record, dict) else None

    # ------------------------------------------------------------------ #
    # Rankings (full-spec long tail)
    # ------------------------------------------------------------------ #

    def list_rankings(
        self,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        # Prefer service store (projected file); merge overlay writes on top.
        all_records = self._read_dataset_records("rankings")
        if not all_records:
            all_records = list(self._local_overlay_records("rankings").values())
        items = [json.loads(json.dumps(r)) for r in all_records if isinstance(r, dict)]
        if status:
            items = [i for i in items if i.get("status") == status]
        return sorted(items, key=lambda x: x.get("id", ""))

    def get_ranking(self, ranking_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not ranking_id:
            return None
        overlay = self._local_overlay_records("rankings").get(ranking_id)
        if overlay is not None:
            return overlay
        available, service_records = self._service.list_records("rankings")
        if available and service_records:
            for record in service_records:
                if not isinstance(record, dict):
                    continue
                rid = str(record.get("ranking_id") or record.get("id") or "")
                if rid == ranking_id:
                    return json.loads(json.dumps(record))
        return None

    # ------------------------------------------------------------------ #
    # Persona League / Management fleet projection
    # ------------------------------------------------------------------ #

    def list_persona_league(
        self,
        *,
        market_scope: Optional[str] = None,
        status: Optional[str] = None,
        include_market_persona_defaults: bool = False,
    ) -> List[Dict[str, Any]]:
        items = [json.loads(json.dumps(item)) for item in self._read_dataset_records("persona_league")]
        if include_market_persona_defaults:
            items = self._merge_market_persona_record_list(
                "persona_league",
                items,
                ["persona_id", "id"],
            )
        if market_scope:
            requested = {item.strip().upper() for item in market_scope.split(",") if item.strip()}
            items = [
                item
                for item in items
                if requested.intersection(
                    {str(scope).upper() for scope in (item.get("market_scope") or [])}
                )
            ]
        if status:
            requested_statuses = {item.strip().lower() for item in status.split(",") if item.strip()}
            items = [
                item
                for item in items
                if str(item.get("status") or "").lower() in requested_statuses
            ]
        return sorted(
            items,
            key=lambda item: (
                int(item.get("rank") or 9999),
                -float(item.get("league_score") or 0.0),
                str(item.get("persona_id") or item.get("id") or ""),
            ),
        )

    def get_persona_league_entry(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not persona_id:
            return None
        for item in self.list_persona_league():
            if str(item.get("persona_id") or item.get("id") or "") == str(persona_id):
                return json.loads(json.dumps(item))
        return None

    # ------------------------------------------------------------------ #
    # Evolution programs (BFF-LUV-GAP-004)
    # ------------------------------------------------------------------ #

    def list_evolution_programs(
        self,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        available, raw_records = self._service.list_records("evolution_programs")
        if available:
            items = raw_records
        else:
            items = list((self._local_fallback("evolution_programs") or {}).values())
        if status:
            items = [i for i in items if i.get("status") == status]
        return sorted(items, key=lambda x: str(x.get("created_at") or ""), reverse=True)

    def get_evolution_program(self, program_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not program_id:
            return None
        available, record = self._service.record("evolution_programs", program_id)
        if available:
            return record
        return (self._local_fallback("evolution_programs") or {}).get(program_id)

    def create_evolution_program(
        self,
        *,
        program_id: str,
        name: str,
        actor_id: str,
        created_at: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        programs = self._local_fallback("evolution_programs")
        if programs is None:
            programs = self._data.setdefault("evolution_programs", {})
        timestamp = created_at or _utc_now_rfc3339()
        record: Dict[str, Any] = {
            "id": program_id,
            "program_id": program_id,
            "name": name,
            "status": "active",
            "params": params or {},
            "created_at": timestamp,
            "updated_at": timestamp,
            "created_by": actor_id,
        }
        programs[program_id] = record
        self._save()
        return record

    def patch_evolution_program(
        self,
        program_id: str,
        *,
        patch: Dict[str, Any],
        actor_id: str,
        updated_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        programs = self._local_fallback("evolution_programs")
        if programs is None:
            programs = self._data.get("evolution_programs")
        if not programs:
            return None
        record = programs.get(program_id)
        if record is None:
            return None
        timestamp = updated_at or _utc_now_rfc3339()
        for field in ("name", "status", "params"):
            if field in patch:
                record[field] = patch[field]
        record["updated_at"] = timestamp
        record["updated_by"] = actor_id
        self._save()
        return record

    def list_evolution_program_runs(self, program_id: str) -> List[Dict[str, Any]]:
        """Return synthetic run projections for an evolution program."""
        if not self.get_evolution_program(program_id):
            return []
        all_decisions = self.list_evolution_decisions()
        related = [d for d in all_decisions if d.get("program_id") == program_id]
        return [
            {
                "run_id": d.get("decision_id", d.get("id", "")),
                "program_id": program_id,
                "status": d.get("status", "unknown"),
                "started_at": d.get("created_at"),
                "completed_at": d.get("resolved_at"),
                "score": d.get("score"),
                "artifact_ref": d.get("artifact_ref"),
            }
            for d in related
        ]

    def list_evolution_program_candidates(self, program_id: str) -> List[Dict[str, Any]]:
        """Return candidate projections for an evolution program."""
        if not self.get_evolution_program(program_id):
            return []
        all_decisions = self.list_evolution_decisions(status="pending")
        related = [d for d in all_decisions if d.get("program_id") == program_id]
        return [
            {
                "candidate_id": d.get("decision_id", d.get("id", "")),
                "program_id": program_id,
                "status": "pending",
                "score": d.get("score"),
                "proposed_at": d.get("created_at"),
            }
            for d in related
        ]

    # ------------------------------------------------------------------ #
    # OODA packet read surface (MGMT-OODA-004)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _ooda_packet_id(packet: Dict[str, Any]) -> str:
        return str(packet.get("packet_id") or packet.get("id") or "").strip()

    @staticmethod
    def _add_ooda_ref_value(target: set[str], value: Any) -> None:
        if value in (None, ""):
            return
        if isinstance(value, (str, int, float)):
            text = str(value).strip()
            if text:
                target.add(text)
            return
        if isinstance(value, list):
            for item in value:
                ReadSurfaceStore._add_ooda_ref_value(target, item)
            return
        if isinstance(value, dict):
            for field in ("id", "ref_id", "object_id", "entity_id", "strategy_id", "runtime_id", "program_id"):
                raw = value.get(field)
                if raw not in (None, ""):
                    target.add(str(raw).strip())

    @classmethod
    def _collect_ooda_ref_values(
        cls,
        value: Any,
        *,
        field_aliases: set[str],
        type_tokens: set[str],
    ) -> set[str]:
        refs: set[str] = set()

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                raw_type = str(
                    node.get("type")
                    or node.get("object_type")
                    or node.get("entity_type")
                    or node.get("ref_type")
                    or ""
                ).lower()
                if raw_type and any(token in raw_type for token in type_tokens):
                    cls._add_ooda_ref_value(refs, node)
                for key, child in node.items():
                    normalized_key = str(key).replace("_", "").lower()
                    if normalized_key in field_aliases:
                        cls._add_ooda_ref_value(refs, child)
                    visit(child)
                return
            if isinstance(node, list):
                for child in node:
                    visit(child)

        visit(value)
        return refs

    @classmethod
    def _ooda_packet_matches_ref(cls, packet: Dict[str, Any], ref_id: str, ref_type: str) -> bool:
        clean_ref = str(ref_id or "").strip()
        if not clean_ref:
            return False
        aliases_by_type = {
            "strategy": {
                "strategyid",
                "strategyids",
                "linkedstrategyid",
                "linkedstrategyids",
                "strategyspecid",
                "strategyspecids",
            },
            "runtime": {
                "runtimeid",
                "runtimeids",
                "runtimebindingid",
                "runtimebindingids",
                "bindingid",
                "bindingids",
            },
            "evolution_program": {
                "evolutionprogramid",
                "evolutionprogramids",
                "programid",
                "programids",
            },
        }
        type_tokens_by_type = {
            "strategy": {"strategy"},
            "runtime": {"runtime", "runtimebinding"},
            "evolution_program": {"evolutionprogram"},
        }
        refs = cls._collect_ooda_ref_values(
            packet,
            field_aliases=aliases_by_type[ref_type],
            type_tokens=type_tokens_by_type[ref_type],
        )
        return clean_ref in refs

    def list_ooda_packets(
        self,
        *,
        status: Optional[str] = None,
        stage: Optional[str] = None,
        strategy_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
        evolution_program_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        items = [
            json.loads(json.dumps(packet))
            for packet in self._read_dataset_records("ooda_packets")
            if self._ooda_packet_id(packet)
        ]
        if status:
            requested = {item.strip().lower() for item in status.split(",") if item.strip()}
            items = [
                packet
                for packet in items
                if str(packet.get("status") or packet.get("state") or "").lower() in requested
            ]
        if stage:
            requested_stages = {item.strip().lower() for item in stage.split(",") if item.strip()}
            items = [
                packet
                for packet in items
                if str(packet.get("stage") or packet.get("current_stage") or "").lower() in requested_stages
            ]
        if strategy_id:
            items = [
                packet
                for packet in items
                if self._ooda_packet_matches_ref(packet, strategy_id, "strategy")
            ]
        if runtime_id:
            items = [
                packet
                for packet in items
                if self._ooda_packet_matches_ref(packet, runtime_id, "runtime")
            ]
        if evolution_program_id:
            items = [
                packet
                for packet in items
                if self._ooda_packet_matches_ref(packet, evolution_program_id, "evolution_program")
            ]
        items.sort(
            key=lambda packet: (
                (_parse_rfc3339(
                    packet.get("updated_at")
                    or packet.get("closed_at")
                    or packet.get("created_at")
                    or packet.get("started_at")
                )
                or datetime.min).replace(tzinfo=None)
            ),
            reverse=True,
        )
        return items

    def get_ooda_packet(self, packet_id: Optional[str]) -> Optional[Dict[str, Any]]:
        clean_id = str(packet_id or "").strip()
        if not clean_id:
            return None
        for packet in self.list_ooda_packets():
            if self._ooda_packet_id(packet) == clean_id:
                return json.loads(json.dumps(packet))
        return None

    def list_ooda_packets_for_strategy(self, strategy_id: str) -> List[Dict[str, Any]]:
        return self.list_ooda_packets(strategy_id=strategy_id)

    def list_ooda_packets_for_runtime(self, runtime_id: str) -> List[Dict[str, Any]]:
        return self.list_ooda_packets(runtime_id=runtime_id)

    def list_ooda_packets_for_evolution_program(self, program_id: str) -> List[Dict[str, Any]]:
        return self.list_ooda_packets(evolution_program_id=program_id)

    # ------------------------------------------------------------------ #
    # Synthesis conflict log read surface (MGMT-SYN-006)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _synthesis_conflict_log_id(log: Dict[str, Any]) -> str:
        return str(log.get("log_id") or log.get("id") or log.get("conflict_resolution_log_id") or "").strip()

    @staticmethod
    def _synthesis_log_text_matches(value: Any, requested: set[str]) -> bool:
        if value in (None, ""):
            return False
        if isinstance(value, list):
            return any(ReadSurfaceStore._synthesis_log_text_matches(item, requested) for item in value)
        return str(value).strip() in requested

    @classmethod
    def _synthesis_conflict_log_matches_proposal(cls, log: Dict[str, Any], proposal_id: str) -> bool:
        clean_id = str(proposal_id or "").strip()
        if not clean_id:
            return True
        requested = {clean_id}
        if cls._synthesis_log_text_matches(log.get("proposal_ids"), requested):
            return True
        if clean_id in {str(key) for key in (log.get("weighting_inputs") or {}).keys()}:
            return True
        if clean_id in {str(key) for key in (log.get("weighting_outputs") or {}).keys()}:
            return True
        for veto in log.get("vetoed_proposals") or []:
            if isinstance(veto, dict) and str(veto.get("proposal_id") or "").strip() == clean_id:
                return True
        return False

    def list_synthesis_conflict_logs(
        self,
        *,
        capital_pool_id: Optional[str] = None,
        scope_ref: Optional[str] = None,
        proposal_id: Optional[str] = None,
        sponsor_persona_id: Optional[str] = None,
        synthesis_method: Optional[str] = None,
        committee_ref: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        items = [
            json.loads(json.dumps(log))
            for log in self._read_dataset_records("synthesis_conflict_logs")
            if self._synthesis_conflict_log_id(log)
        ]
        if capital_pool_id:
            requested = {item.strip() for item in capital_pool_id.split(",") if item.strip()}
            items = [log for log in items if str(log.get("capital_pool_id") or "").strip() in requested]
        if scope_ref:
            requested = {item.strip() for item in scope_ref.split(",") if item.strip()}
            items = [log for log in items if str(log.get("scope_ref") or "").strip() in requested]
        if sponsor_persona_id:
            requested = {item.strip() for item in sponsor_persona_id.split(",") if item.strip()}
            items = [log for log in items if str(log.get("sponsor_persona_id") or "").strip() in requested]
        if synthesis_method:
            requested = {item.strip() for item in synthesis_method.split(",") if item.strip()}
            items = [log for log in items if str(log.get("synthesis_method") or "").strip() in requested]
        if committee_ref:
            requested = {item.strip() for item in committee_ref.split(",") if item.strip()}
            items = [log for log in items if str(log.get("committee_ref") or "").strip() in requested]
        if proposal_id:
            items = [log for log in items if self._synthesis_conflict_log_matches_proposal(log, proposal_id)]
        items.sort(
            key=lambda log: (
                (_parse_rfc3339(
                    log.get("timestamp")
                    or log.get("created_at")
                    or log.get("recorded_at")
                    or log.get("updated_at")
                )
                or datetime.min).replace(tzinfo=None)
            ),
            reverse=True,
        )
        return items

    def get_synthesis_conflict_log(self, log_id: Optional[str]) -> Optional[Dict[str, Any]]:
        clean_id = str(log_id or "").strip()
        if not clean_id:
            return None
        for log in self.list_synthesis_conflict_logs():
            if self._synthesis_conflict_log_id(log) == clean_id:
                return json.loads(json.dumps(log))
        return None

    # ------------------------------------------------------------------ #
    # v5 intervention fixture read surface (BFF-CONSOL-009)
    # ------------------------------------------------------------------ #

    def list_v5_interventions(
        self,
        *,
        status: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        items = self._read_dataset_records("v5_interventions")
        if status:
            items = [
                item
                for item in items
                if str(item.get("status") or "").strip().lower() == str(status).strip().lower()
            ]
        if kind:
            items = [
                item
                for item in items
                if str(item.get("kind") or "").strip().lower() == str(kind).strip().lower()
            ]
        items.sort(
            key=lambda item: (_parse_rfc3339(item.get("triggered_at")) or datetime.min).replace(tzinfo=None),
            reverse=True,
        )
        return json.loads(json.dumps(items))

    def get_v5_intervention(self, intervention_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not intervention_id:
            return None
        for item in self.list_v5_interventions():
            if str(item.get("intervention_id") or item.get("id") or "") == str(intervention_id):
                return json.loads(json.dumps(item))
        return None

    # ------------------------------------------------------------------ #
    # Experiments BFF compat (BFF-LUV-GAP-004)
    # ------------------------------------------------------------------ #

    def list_experiments_bff(
        self,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        items = self.list_research_experiments(status=status)
        return [self._project_experiment_bff(e) for e in items]

    def get_experiment_bff(self, experiment_id: Optional[str]) -> Optional[Dict[str, Any]]:
        item = self.get_research_experiment(experiment_id)
        if item is None:
            return None
        return self._project_experiment_bff(item)

    def create_experiment_bff(
        self,
        *,
        name: str,
        actor_id: str,
        created_at: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        timestamp = created_at or _utc_now_rfc3339()
        import uuid as _uuid
        exp_id = f"exp-bff-{timestamp[:10].replace('-','')}-{_uuid.uuid4().hex[:8]}"
        return self.create_research_experiment(
            ticket_id=f"ticket-{exp_id}",
            experiment_name=name,
            strategy_selector=params.get("strategy_selector") if params else {},
            parameter_set=params.get("parameter_set") if params else {},
            run_config=params.get("run_config", {"mode": "paper"}) if params else {"mode": "paper"},
            launch_context={},
        )

    @staticmethod
    def _project_experiment_bff(experiment: Dict[str, Any]) -> Dict[str, Any]:
        exp_id = str(experiment.get("experiment_id") or experiment.get("id") or "")
        return {
            "id": exp_id,
            "experiment_id": exp_id,
            "name": experiment.get("experiment_name", experiment.get("name", "")),
            "status": experiment.get("status", "unknown"),
            "created_at": experiment.get("queued_at", experiment.get("created_at")),
            "updated_at": experiment.get("updated_at"),
            "artifact_ref": experiment.get("artifact_ref"),
            "links": {
                "self": f"/bff/experiments/{exp_id}",
                "logs": f"/bff/experiments/{exp_id}/logs",
                "metrics": f"/bff/experiments/{exp_id}/metrics",
                "artifacts": f"/bff/experiments/{exp_id}/artifacts",
            },
        }

    def get_experiment_logs(self, experiment_id: str) -> List[Dict[str, Any]]:
        experiment = self.get_research_experiment(experiment_id)
        if not experiment:
            return []
        return list(experiment.get("logs") or [])

    def get_experiment_metrics(self, experiment_id: str) -> Dict[str, Any]:
        experiment = self.get_research_experiment(experiment_id)
        if not experiment:
            return {}
        return dict(experiment.get("metrics") or {})

    def get_experiment_artifacts(self, experiment_id: str) -> List[Dict[str, Any]]:
        experiment = self.get_research_experiment(experiment_id)
        if not experiment:
            return []
        artifact_ref = experiment.get("artifact_ref")
        if not artifact_ref:
            return []
        return [{"artifact_ref": artifact_ref, "experiment_id": experiment_id}]

    # ------------------------------------------------------------------ #
    # Jobs BFF compat (BFF-LUV-GAP-004)
    # ------------------------------------------------------------------ #

    def list_jobs_bff(
        self,
        status: Optional[str] = None,
        job_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        available, service_records = self._service.list_records("jobs")
        if available and service_records:
            items = [dict(record) for record in service_records if isinstance(record, dict)]
        else:
            raw = self._local_fallback("jobs") or self._local_fallback("bff_jobs") or {}
            if isinstance(raw, dict):
                items = [dict(record) for record in raw.values() if isinstance(record, dict)]
            elif isinstance(raw, list):
                items = [dict(record) for record in raw if isinstance(record, dict)]
            else:
                items = []
        if status:
            items = [i for i in items if i.get("status") == status]
        if job_type:
            items = [i for i in items if i.get("job_type") == job_type]
        return sorted(items, key=lambda x: str(x.get("created_at") or ""), reverse=True)

    def get_job_bff(self, job_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not job_id:
            return None
        available, service_records = self._service.list_records("jobs")
        if available and service_records:
            for job in service_records:
                if not isinstance(job, dict):
                    continue
                found = str(job.get("job_id") or job.get("run_id") or job.get("id") or "")
                if found == str(job_id):
                    return dict(job)
        jobs = self._local_fallback("jobs") or self._local_fallback("bff_jobs") or {}
        if isinstance(jobs, dict):
            job = jobs.get(job_id)
            return dict(job) if isinstance(job, dict) else None
        if isinstance(jobs, list):
            for job in jobs:
                if not isinstance(job, dict):
                    continue
                found = str(job.get("job_id") or job.get("run_id") or job.get("id") or "")
                if found == str(job_id):
                    return dict(job)
        return None

    def get_job_logs_bff(self, job_id: str) -> List[Dict[str, Any]]:
        job = self.get_job_bff(job_id)
        if not job:
            return []
        return list(job.get("logs") or [])

    # ------------------------------------------------------------------ #
    # Events list BFF compat (BFF-LUV-GAP-004)
    # ------------------------------------------------------------------ #

    def list_events_bff(
        self,
        event_type: Optional[str] = None,
        page_size: int = 50,
    ) -> List[Dict[str, Any]]:
        """Return recent events from telemetry/audit as a paginated list."""
        telemetry_raw = self.list_telemetry_events()
        telemetry = [
            {
                "event_id": e.get("id", ""),
                "event_type": event_type or e.get("type", "telemetry"),
                "occurred_at": e.get("timestamp"),
                "entity_type": "runtime",
                "entity_id": e.get("runtime_id", ""),
                "summary": f"Telemetry snapshot for runtime {e.get('runtime_id', '')}",
            }
            for e in telemetry_raw
            if not event_type or e.get("type") == event_type
        ]
        audit: List[Dict[str, Any]] = []
        try:
            raw_audit = self.list_governance_audit_events(
                actor=None,
                action_types=None,
                target_type=None,
                from_ts=None,
                to_ts=None,
            )
            audit = [
                {
                    "event_id": e.get("event_id", e.get("id", "")),
                    "event_type": e.get("action_type", "audit"),
                    "occurred_at": e.get("timestamp"),
                    "entity_type": e.get("target_type"),
                    "entity_id": e.get("target_id", ""),
                    "summary": e.get("summary", ""),
                }
                for e in raw_audit
                if not event_type or e.get("action_type") == event_type
            ]
        except Exception:
            pass
        combined = telemetry + audit
        combined.sort(key=lambda x: str(x.get("occurred_at") or ""), reverse=True)
        return combined[:page_size]

    def get_persona(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not persona_id:
            return None
        local_persona = self._local_bff_persona_records().get(str(persona_id))
        available, raw = self._service.record("personas", persona_id)
        if available:
            if raw:
                projected = self._project_service_persona(raw)
                if local_persona:
                    projected.update(json.loads(json.dumps(local_persona)))
                return projected
            if local_persona:
                return local_persona
            return None
        if local_persona:
            return local_persona
        local = self._local_fallback("personas")
        if isinstance(local, dict):
            return local.get(persona_id)
        return None

    def get_runtime_binding(self, binding_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not binding_id:
            return None
        for binding in self.list_runtime_bindings(include_market_persona_defaults=True):
            if str(binding.get("binding_id") or binding.get("runtime_binding_id") or binding.get("id") or "") == str(binding_id):
                return json.loads(json.dumps(binding))
        return None

    def get_runtime_binding_by_runtime_id(self, runtime_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not runtime_id:
            return None
        for binding in self.list_runtime_bindings(include_market_persona_defaults=True):
            if str(binding.get("runtime_id") or binding.get("id") or "") == runtime_id:
                return json.loads(json.dumps(binding))
        return None

    def get_rollbacks(self, runtime_id: Optional[str]) -> List[Dict[str, Any]]:
        if not runtime_id:
            return []
        return list((self._local_fallback("rollbacks") or {}).get(runtime_id, []))

    def get_allowed_actions(
        self,
        plan_id: str,
        *,
        plan: Optional[Dict[str, Any]] = None,
        decision: Any = _NOT_SUPPLIED,
    ) -> Dict[str, Any]:
        if plan is None:
            plan = self.get_deployment_plan(plan_id)
        if decision is _NOT_SUPPLIED:
            if plan:
                decision = self.get_approval_decision(plan.get("approval_decision_id"))
            else:
                decision = None
        fallback_actions = dict((self._local_fallback("allowed_actions") or {}).get(plan_id, {}))
        if plan and (plan.get("status") is not None or plan.get("target_stage") is not None):
            can_review = self._derive_can_review_deployment_plan(plan, decision)
            return {
                "canApprove": bool(fallback_actions.get("canApprove", can_review)),
                "canReject": bool(fallback_actions.get("canReject", can_review)),
                "canPromoteToPaper": bool(
                    fallback_actions.get(
                        "canPromoteToPaper",
                        self._derive_can_promote_to_paper(plan, decision),
                    )
                ),
            }
        if self._allow_local_snapshot_fallback:
            return {
                "canApprove": bool(fallback_actions.get("canApprove", False)),
                "canReject": bool(fallback_actions.get("canReject", False)),
                "canPromoteToPaper": bool(fallback_actions.get("canPromoteToPaper", False)),
            }
        return {
            "canApprove": False,
            "canReject": False,
            "canPromoteToPaper": False,
        }

    def get_latest_run(self, plan_id: str) -> Dict[str, Any]:
        if self._allow_local_snapshot_fallback:
            return (self._local_fallback("latest_runs") or {}).get(plan_id, {"progress": 0.0})
        return None

    def get_review_summary(
        self,
        plan_id: str,
        *,
        plan: Optional[Dict[str, Any]] = None,
        decision: Any = _NOT_SUPPLIED,
    ) -> Dict[str, Any]:
        summary = dict((self._local_fallback("review_summaries") or {}).get(plan_id, {}))
        if plan is None:
            plan = self.get_deployment_plan(plan_id)
        if decision is _NOT_SUPPLIED:
            if plan:
                decision = self.get_approval_decision(plan.get("approval_decision_id"))
            else:
                decision = None
        if decision:
            summary.setdefault("governanceOutcome", decision.get("outcome"))
            summary.setdefault("decisionState", decision.get("state"))
            summary.setdefault("decidedAt", decision.get("decided_at"))
            summary.setdefault("reviewer", decision.get("reviewer"))
            if "riskSummary" not in summary or not summary["riskSummary"]:
                risk_level = decision.get("risk_level")
                if risk_level:
                    summary["riskSummary"] = f"Approval decision risk level: {risk_level}."
        if not summary:
            return None
        if "riskSummary" not in summary or not summary["riskSummary"]:
            summary["riskSummary"] = "Risk summary unavailable."
        return summary

    def list_governance_review_queue_items(
        self,
        *,
        item_types: Optional[List[str]] = None,
        risk_levels: Optional[List[str]] = None,
        statuses: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        items = list((self._local_fallback("governance_review_queue_items") or {}).values())
        if not items:
            reviewable_statuses = {"draft", "pending_review", "proposed", "under_review", "reviewed"}
            linked_approval_decision_ids: set[str] = set()
            decisions = self.list_approval_decisions()
            decisions_by_id = {}
            for d in decisions:
                d_id = str(d.get("decision_id") or d.get("id") or "").strip()
                if d_id:
                    decisions_by_id[d_id] = d

            for plan in self.list_deployment_plans():
                status = str(plan.get("status") or "").strip().lower()
                if status and status not in reviewable_statuses:
                    continue
                plan_id = str(plan.get("plan_id") or plan.get("id") or "").strip()
                if not plan_id:
                    continue
                dec_id = str(plan.get("approval_decision_id") or "").strip()
                decision = decisions_by_id.get(dec_id)
                decision_id = str((decision or {}).get("decision_id") or (decision or {}).get("id") or "").strip()
                if decision_id:
                    linked_approval_decision_ids.add(decision_id)
                items.append(
                    {
                        "item_id": f"review-{plan_id}",
                        "item_type": "DeploymentPlan",
                        "risk_level": (decision or {}).get("risk_level"),
                        "submitted_at": plan.get("submitted_at") or plan.get("created_at"),
                        "submitted_by": plan.get("created_by") or "deployment-service",
                        "governance_outcome": (decision or {}).get("outcome"),
                        "allowedActions": self.get_allowed_actions(plan_id, plan=plan, decision=decision),
                        "review_summary": self.get_review_summary(plan_id, plan=plan, decision=decision) or {},
                    }
                )
            for decision in self.list_evolution_decisions():
                status = str(decision.get("decision_state") or decision.get("status") or "").strip().lower()
                if status and status not in reviewable_statuses:
                    continue
                decision_id = str(decision.get("decision_id") or decision.get("id") or "").strip()
                if not decision_id:
                    continue
                items.append(
                    {
                        "item_id": f"review-{decision_id}",
                        "item_type": "EvolutionDecision",
                        "risk_level": decision.get("risk_level"),
                        "submitted_at": decision.get("created_at"),
                        "submitted_by": decision.get("created_by_id") or "evolution-service",
                        "governance_outcome": decision.get("status"),
                        "allowedActions": {
                            "canApprove": status in {"reviewed", "under_review"},
                            "canReject": status in {"reviewed", "under_review"},
                            "canRequestRevision": status in {"proposed", "under_review", "reviewed"},
                        },
                        "review_summary": {
                            "riskSummary": decision.get("rationale") or "Evolution decision awaiting governance review.",
                        },
                    }
                )
            for decision in self.list_approval_decisions():
                decision_id = str(decision.get("decision_id") or decision.get("id") or "").strip()
                if not decision_id or decision_id in linked_approval_decision_ids:
                    continue
                status = str(decision.get("state") or decision.get("decision_state") or "").strip().lower()
                outcome = str(decision.get("outcome") or decision.get("decision") or "").strip().lower()
                if outcome in {"approved", "approved_with_conditions", "rejected"}:
                    continue
                if status and status not in reviewable_statuses:
                    continue
                target_type = str(decision.get("target_type") or decision.get("decision_type") or "ApprovalDecision")
                submitted_by = decision.get("created_by") or decision.get("reviewer") or "governance-service"
                can_decide = status in {"under_review", "reviewed", "in_review"}
                items.append(
                    {
                        "item_id": f"review-{decision_id}",
                        "item_type": "ApprovalDecision",
                        "risk_level": decision.get("risk_level"),
                        "status": status or "proposed",
                        "submitted_at": decision.get("submitted_at") or decision.get("created_at"),
                        "submitted_by": submitted_by,
                        "governance_outcome": outcome or status or "proposed",
                        "allowedActions": {
                            "canApprove": can_decide,
                            "canReject": can_decide,
                            "canRequestRevision": status in {"proposed", "under_review", "reviewed"},
                        },
                        "review_summary": {
                            "riskSummary": (
                                decision.get("rationale")
                                or f"{target_type} approval decision awaiting governance review."
                            ),
                            "evidence_refs": json.loads(json.dumps(decision.get("evidence_refs") or [])),
                            "linked_approval_decision_id": decision_id,
                            "target_type": target_type,
                            "target_id": decision.get("target_id"),
                            "target_version": decision.get("target_version"),
                        },
                    }
                )

        if item_types:
            requested_item_types = {value for value in item_types if value}
            items = [
                item
                for item in items
                if str(item.get("item_type") or "") in requested_item_types
            ]
        if risk_levels:
            requested_risk_levels = {value for value in risk_levels if value}
            items = [
                item
                for item in items
                if str(item.get("risk_level") or "") in requested_risk_levels
            ]
        if statuses:
            requested_statuses = {value for value in statuses if value}
            items = [
                item
                for item in items
                if str(item.get("status") or "") in requested_statuses
            ]

        projected_items: List[Dict[str, Any]] = []
        for item in items:
            projected_items.append(
                {
                    "item_id": item.get("item_id"),
                    "item_type": item.get("item_type"),
                    "risk_level": item.get("risk_level"),
                    "submitted_at": item.get("submitted_at"),
                    "submitted_by": item.get("submitted_by"),
                    "governance_outcome": item.get("governance_outcome"),
                    "allowedActions": json.loads(json.dumps(item.get("allowedActions") or {})),
                    "review_summary": json.loads(json.dumps(item.get("review_summary") or {})),
                }
            )

        return projected_items

    def list_approval_queue_items(
        self,
        *,
        decision_types: Optional[List[str]] = None,
        risk_levels: Optional[List[str]] = None,
        decision_states: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        items = list((self._local_fallback("approval_queue_items") or {}).values())
        if not items:
            pending_states = {"proposed", "under_review", "reviewed", "pending", "in_review"}
            available, raw_decisions = self._canonical.list_records("approval_decisions")
            if available:
                for raw in raw_decisions:
                    decision_id = str(raw.get("decision_id") or raw.get("id") or "").strip()
                    if not decision_id:
                        continue
                    state = str(raw.get("decision_state") or raw.get("state") or "").strip().lower()
                    outcome = str(raw.get("outcome") or raw.get("decision") or "").strip().lower()
                    if outcome in {"approved", "approved_with_conditions", "rejected"}:
                        continue
                    if state and state not in pending_states:
                        continue
                    target_type = raw.get("target_type") or raw.get("decision_type") or "ApprovalDecision"
                    can_decide = state in {"under_review", "reviewed", "in_review"}
                    items.append(
                        {
                            "decision_id": decision_id,
                            "decision_type": target_type,
                            "risk_level": raw.get("risk_level"),
                            "submitted_at": raw.get("created_at") or raw.get("submitted_at"),
                            "submitted_by": raw.get("actor_id") or raw.get("created_by") or "governance-service",
                            "decision_state": state or "pending",
                            "allowedActions": {
                                "canApprove": can_decide,
                                "canReject": can_decide,
                                "canRequestRevision": state in pending_states,
                            },
                            "decision_context": {
                                "risk_summary": raw.get("rationale") or "Approval decision awaiting governance action.",
                                "evidence_refs": list(raw.get("evidence_refs") or []),
                                "governance_chain": {
                                    "target_type": target_type,
                                    "target_id": raw.get("target_id"),
                                    "target_version": raw.get("target_version"),
                                },
                                "required_approvals": 1,
                            },
                        }
                    )

        if decision_types:
            requested_types = {value for value in decision_types if value}
            items = [
                item
                for item in items
                if str(item.get("decision_type") or "") in requested_types
            ]
        if risk_levels:
            requested_risk_levels = {value for value in risk_levels if value}
            items = [
                item
                for item in items
                if str(item.get("risk_level") or "") in requested_risk_levels
            ]
        if decision_states:
            requested_states = {value for value in decision_states if value}
            items = [
                item
                for item in items
                if str(item.get("decision_state") or "") in requested_states
            ]

        projected_items: List[Dict[str, Any]] = []
        for item in items:
            projected_items.append(
                {
                    "decision_id": item.get("decision_id"),
                    "decision_type": item.get("decision_type"),
                    "risk_level": item.get("risk_level"),
                    "submitted_at": item.get("submitted_at"),
                    "submitted_by": item.get("submitted_by"),
                    "decision_state": item.get("decision_state"),
                    "allowedActions": json.loads(json.dumps(item.get("allowedActions") or {})),
                    "decision_context": json.loads(json.dumps(item.get("decision_context") or {})),
                }
            )

        return projected_items

    def get_deployment_diff(self, plan_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not plan_id:
            return None
        diff = (self._local_fallback("deployment_diffs") or {}).get(plan_id)
        return json.loads(json.dumps(diff)) if diff else None

    # ------------------------------------------------------------------ #
    # Research Ticket surfaces (RW-01)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _research_ticket_allowed_actions(status: Optional[str]) -> Dict[str, bool]:
        normalized = str(status or "").strip().lower()
        if normalized == "archived":
            return {
                "canEdit": False,
                "canClose": False,
                "canArchive": False,
            }
        if normalized == "closed":
            return {
                "canEdit": False,
                "canClose": False,
                "canArchive": True,
            }
        if normalized in {"open", "in_progress"}:
            return {
                "canEdit": True,
                "canClose": True,
                "canArchive": False,
            }
        return {
            "canEdit": False,
            "canClose": False,
            "canArchive": False,
        }

    @classmethod
    def _project_research_ticket_summary(cls, ticket: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ticket_id": ticket.get("ticket_id"),
            "title": ticket.get("title"),
            "status": ticket.get("status"),
            "priority": ticket.get("priority"),
            "owner": ticket.get("owner"),
            "created_at": ticket.get("created_at"),
            "updated_at": ticket.get("updated_at"),
            "allowedActions": cls._research_ticket_allowed_actions(ticket.get("status")),
        }

    @classmethod
    def _project_research_ticket_detail(cls, ticket: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ticket_id": ticket.get("ticket_id"),
            "title": ticket.get("title"),
            "description": ticket.get("description"),
            "status": ticket.get("status"),
            "priority": ticket.get("priority"),
            "owner": ticket.get("owner"),
            "created_at": ticket.get("created_at"),
            "updated_at": ticket.get("updated_at"),
            "closed_at": ticket.get("closed_at"),
            "archived_at": ticket.get("archived_at"),
            "lifecycle_history": json.loads(json.dumps(ticket.get("lifecycle_history") or [])),
            "linked_experiments": list(ticket.get("linked_experiments") or []),
            "linked_artifacts": list(ticket.get("linked_artifacts") or []),
            "allowedActions": cls._research_ticket_allowed_actions(ticket.get("status")),
        }

    def list_research_tickets(
        self,
        *,
        statuses: Optional[List[str]] = None,
        owner: Optional[str] = None,
        include_fixture_pack: bool = True,
    ) -> List[Dict[str, Any]]:
        tickets = self._read_dataset_records("research_tickets")
        if not include_fixture_pack:
            tickets = [ticket for ticket in tickets if not _is_fixture_pack_record(ticket)]
        if statuses:
            requested_statuses = {str(value).strip().lower() for value in statuses if str(value).strip()}
            tickets = [
                ticket
                for ticket in tickets
                if str(ticket.get("status") or "").strip().lower() in requested_statuses
            ]
        if owner:
            requested_owner = str(owner).strip()
            tickets = [
                ticket
                for ticket in tickets
                if str(ticket.get("owner") or "").strip() == requested_owner
            ]

        tickets.sort(
            key=lambda ticket: (
                (_parse_rfc3339(ticket.get("updated_at")) or _parse_rfc3339(ticket.get("created_at")) or datetime.min).replace(tzinfo=None)
            ),
            reverse=True,
        )
        return [self._project_research_ticket_summary(ticket) for ticket in tickets]

    def get_research_ticket(
        self,
        ticket_id: Optional[str],
        *,
        include_snapshot_fallback: bool = True,
        include_local_fallback: bool = True,
    ) -> Optional[Dict[str, Any]]:
        if not ticket_id:
            return None
        available, ticket = self._service.record(
            "research_tickets",
            ticket_id,
            include_snapshot_fallback=include_snapshot_fallback,
        )
        if not available and include_local_fallback:
            ticket = (self._local_fallback("research_tickets") or {}).get(ticket_id)
        if not ticket:
            return None
        return self._project_research_ticket_detail(ticket)

    def list_research_notes(self) -> List[Dict[str, Any]]:
        notes = self._read_dataset_records("research_notes")
        notes.sort(
            key=lambda note: (
                (_parse_rfc3339(note.get("updated_at")) or _parse_rfc3339(note.get("created_at")) or datetime.min).replace(tzinfo=None)
            ),
            reverse=True,
        )
        return [json.loads(json.dumps(note)) for note in notes]

    def get_research_note(self, note_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not note_id:
            return None
        available, note = self._service.record("research_notes", note_id)
        if not available:
            note = (self._local_fallback("research_notes") or {}).get(note_id)
        if not note:
            return None
        return json.loads(json.dumps(note))

    @staticmethod
    def _kw03_route_href(ref_id: Optional[str]) -> Optional[str]:
        ref = str(ref_id or "").strip()
        if not ref:
            return None
        return f"/knowledge/evidence/{ref}"

    @staticmethod
    def _kw03_entity_route_href(entity_type: Optional[str], entity_ref: Optional[str]) -> Optional[str]:
        entity = str(entity_type or "").strip()
        ref = str(entity_ref or "").strip()
        if not entity or not ref:
            return None
        route_map = {
            "memory_entry": "/knowledge/memory",
            "research_note": "/knowledge/notes",
            "insight_card": "/knowledge/insights",
            "strategy_spec": "/knowledge/strategy-specs",
            "experiment": "/research/experiments",
            "artifact": "/research/artifacts",
        }
        base = route_map.get(entity)
        if not base:
            return None
        return f"{base}/{ref}"

    def _kw03_entity_display_label(self, entity_type: Optional[str], entity_ref: Optional[str]) -> Optional[str]:
        entity = str(entity_type or "").strip()
        ref = str(entity_ref or "").strip()
        if not entity or not ref:
            return None
        if entity == "memory_entry":
            entry = self.get_institutional_memory_entry(ref) or {}
            content = entry.get("content") if isinstance(entry.get("content"), dict) else {}
            return content.get("headline")
        if entity == "research_note":
            note = self.get_research_note(ref) or {}
            return note.get("title")
        if entity == "evidence_ref":
            evidence_ref = self.get_evidence_ref(ref) or {}
            source_document = (
                evidence_ref.get("source_document")
                if isinstance(evidence_ref.get("source_document"), dict)
                else {}
            )
            return evidence_ref.get("display_label") or source_document.get("title")
        if entity == "insight_card":
            insight = self.get_insight_card(ref) or {}
            return insight.get("summary")
        if entity == "strategy_spec":
            spec = self.get_strategy_spec(ref) or {}
            return spec.get("title") or spec.get("name")
        if entity == "experiment":
            experiment = self.get_research_experiment(ref) or {}
            return experiment.get("experiment_name")
        if entity == "artifact":
            artifact = self.get_research_artifact(ref) or {}
            return artifact.get("name")
        return None

    @staticmethod
    def _kw03_normalize_credibility(raw: Any, *, include_detail: bool) -> Dict[str, Any]:
        credibility = raw if isinstance(raw, dict) else {}
        payload: Dict[str, Any] = {
            "tier": credibility.get("tier") or "unverified",
            "verified": bool(credibility.get("verified")),
        }
        if include_detail:
            payload["last_verified_at"] = credibility.get("last_verified_at")
            payload["verification_method"] = credibility.get("verification_method")
        return payload

    @staticmethod
    def _kw03_normalize_resolved_link(raw: Any) -> Dict[str, Any]:
        link = raw if isinstance(raw, dict) else {}
        availability = str(link.get("availability") or "").strip().lower()
        if availability not in {"available", "unavailable", "external"}:
            availability = "unavailable"
        route_href = link.get("route_href")
        if availability == "unavailable":
            route_href = None
        open_in_new_tab = bool(link.get("open_in_new_tab")) if availability != "unavailable" else False
        if availability == "external" and route_href:
            open_in_new_tab = True if link.get("open_in_new_tab") is None else bool(link.get("open_in_new_tab"))
        return {
            "availability": availability,
            "route_href": route_href,
            "display_label": link.get("display_label") or "Source unavailable",
            "open_in_new_tab": open_in_new_tab,
        }

    @staticmethod
    def _tenant_scope_values(value: Any) -> List[str]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            return [part.strip() for part in re.split(r"[\s,]+", value) if part.strip()]
        if isinstance(value, dict):
            values: List[str] = []
            for key in ("id", "tenant_id", "tenantId", "value", "name"):
                if value.get(key) not in (None, ""):
                    values.extend(ReadSurfaceStore._tenant_scope_values(value.get(key)))
            return values
        if isinstance(value, (list, tuple, set)):
            values: List[str] = []
            for item in value:
                values.extend(ReadSurfaceStore._tenant_scope_values(item))
            return values
        return [str(value).strip()]

    @classmethod
    def _record_tenant_ids(cls, record: Dict[str, Any]) -> List[str]:
        values: List[str] = []
        direct_keys = (
            "tenant_id",
            "tenantId",
            "tenant",
            "tenant_ref",
            "tenantRef",
            "org_id",
            "orgId",
            "organization_id",
            "organizationId",
            "workspace_id",
            "workspaceId",
        )
        for key in direct_keys:
            if key in record:
                values.extend(cls._tenant_scope_values(record.get(key)))
        for key in ("metadata", "scope", "source_document", "linked_object_summary"):
            nested = record.get(key)
            if isinstance(nested, dict):
                values.extend(cls._record_tenant_ids(nested))
        seen = set()
        result: List[str] = []
        for value in values:
            clean = str(value or "").strip()
            if clean and clean not in seen:
                seen.add(clean)
                result.append(clean)
        return result

    @classmethod
    def _record_matches_tenant(
        cls,
        record: Dict[str, Any],
        tenant_id: Optional[str],
        *,
        include_tenant_agnostic: bool,
    ) -> bool:
        clean_tenant = str(tenant_id or "").strip()
        if not clean_tenant:
            return True
        record_tenants = cls._record_tenant_ids(record)
        if not record_tenants:
            return include_tenant_agnostic
        return "*" in record_tenants or clean_tenant in record_tenants

    @staticmethod
    def _evidence_linked_entity_pairs(evidence_ref: Dict[str, Any]) -> set[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()

        def add_pair(entity_type: Any, entity_ref: Any) -> None:
            clean_type = str(entity_type or "").strip().lower()
            clean_ref = str(entity_ref or "").strip()
            if clean_type and clean_ref:
                pairs.add((clean_type, clean_ref))

        linked_summary = evidence_ref.get("linked_object_summary")
        if isinstance(linked_summary, dict):
            add_pair(linked_summary.get("entity_type"), linked_summary.get("entity_ref"))
        add_pair(evidence_ref.get("linked_entity_type"), evidence_ref.get("linked_entity_ref"))
        add_pair(evidence_ref.get("target_type"), evidence_ref.get("target_id"))
        for key in ("linked_decisions", "linked_entities", "related_entities"):
            for item in evidence_ref.get(key) or []:
                if isinstance(item, dict):
                    add_pair(
                        item.get("entity_type") or item.get("type"),
                        item.get("entity_ref") or item.get("ref") or item.get("id"),
                    )
        return pairs

    @staticmethod
    def _evidence_source_type(evidence_ref: Dict[str, Any]) -> str:
        source_document = (
            evidence_ref.get("source_document")
            if isinstance(evidence_ref.get("source_document"), dict)
            else {}
        )
        return str(
            evidence_ref.get("source_type")
            or source_document.get("source_type")
            or evidence_ref.get("evidence_type")
            or evidence_ref.get("type")
            or ""
        ).strip().lower()

    @classmethod
    def _evidence_matches_scope(
        cls,
        evidence_ref: Dict[str, Any],
        *,
        linked_entities: Optional[set[tuple[str, str]]],
        source_types: Optional[set[str]],
    ) -> bool:
        normalized_entities = {
            (str(entity_type or "").strip().lower(), str(entity_ref or "").strip())
            for entity_type, entity_ref in (linked_entities or set())
            if str(entity_type or "").strip() and str(entity_ref or "").strip()
        }
        normalized_source_types = {
            str(source_type or "").strip().lower()
            for source_type in (source_types or set())
            if str(source_type or "").strip()
        }
        if not normalized_entities and not normalized_source_types:
            return True
        ref_entities = cls._evidence_linked_entity_pairs(evidence_ref)
        if ref_entities:
            return bool(normalized_entities and ref_entities.intersection(normalized_entities))
        ref_source_type = cls._evidence_source_type(evidence_ref)
        if ref_source_type and ref_source_type in normalized_source_types:
            return True
        return False

    def _project_evidence_ref_list_item(
        self,
        evidence_ref: Dict[str, Any],
        *,
        include_scope_metadata: bool = False,
    ) -> Dict[str, Any]:
        ref_id = evidence_ref.get("ref_id")
        source_document = evidence_ref.get("source_document") if isinstance(evidence_ref.get("source_document"), dict) else {}
        linked_summary = (
            evidence_ref.get("linked_object_summary")
            if isinstance(evidence_ref.get("linked_object_summary"), dict)
            else {}
        )
        if not linked_summary and isinstance(evidence_ref.get("linked_decisions"), list) and evidence_ref.get("linked_decisions"):
            first = evidence_ref["linked_decisions"][0]
            if isinstance(first, dict):
                linked_summary = {
                    "entity_type": first.get("entity_type"),
                    "entity_ref": first.get("entity_ref"),
                    "display_label": first.get("display_label"),
                }

        linked_entity_type = linked_summary.get("entity_type")
        linked_entity_ref = linked_summary.get("entity_ref")
        linked_display_label = (
            linked_summary.get("display_label")
            or self._kw03_entity_display_label(linked_entity_type, linked_entity_ref)
        )
        route_href = evidence_ref.get("route_href") or self._kw03_route_href(ref_id)
        payload = {
            "ref_id": ref_id,
            # evidence_type carries the EvidenceKind for capability redaction in main.py.
            # It is stripped from the public API response by the endpoint's re-projection.
            "evidence_type": evidence_ref.get("evidence_type") or evidence_ref.get("type") or None,
            "display_label": evidence_ref.get("display_label") or source_document.get("title") or linked_display_label or ref_id,
            "route_href": route_href,
            "source_document": {
                "title": source_document.get("title") or evidence_ref.get("display_label") or ref_id,
                "source_type": source_document.get("source_type"),
                "source_ref": source_document.get("source_ref"),
                "captured_at": source_document.get("captured_at") or evidence_ref.get("created_at"),
            },
            "link_type": evidence_ref.get("link_type"),
            "credibility": self._kw03_normalize_credibility(
                evidence_ref.get("credibility"),
                include_detail=False,
            ),
            "linked_object_summary": {
                "entity_type": linked_entity_type,
                "entity_ref": linked_entity_ref,
                "display_label": linked_display_label,
            },
            "resolved_link": self._kw03_normalize_resolved_link(evidence_ref.get("resolved_link")),
        }
        artifact_manifest = evidence_ref.get("artifact_manifest")
        if isinstance(artifact_manifest, dict):
            payload["artifact_manifest"] = json.loads(json.dumps(artifact_manifest))
        criteria = evidence_ref.get("criteria")
        if isinstance(criteria, dict):
            payload["criteria"] = json.loads(json.dumps(criteria))
        if "overall" in evidence_ref:
            payload["overall"] = evidence_ref.get("overall")
        if include_scope_metadata:
            tenant_ids = self._record_tenant_ids(evidence_ref)
            if tenant_ids:
                payload["tenant_id"] = tenant_ids[0]
                payload["tenantId"] = tenant_ids[0]
            linked_decisions = [
                {
                    "entity_type": item.get("entity_type") or item.get("type"),
                    "entity_ref": item.get("entity_ref") or item.get("ref") or item.get("id"),
                }
                for item in evidence_ref.get("linked_decisions") or []
                if isinstance(item, dict)
            ]
            if linked_decisions:
                payload["linked_decisions"] = linked_decisions
        return payload

    def _project_evidence_ref_detail(self, evidence_ref: Dict[str, Any]) -> Dict[str, Any]:
        projected = self._project_evidence_ref_list_item(evidence_ref)
        source_document = evidence_ref.get("source_document") if isinstance(evidence_ref.get("source_document"), dict) else {}
        storage_preview = (
            source_document.get("storage_preview")
            if isinstance(source_document.get("storage_preview"), dict)
            else {}
        )
        linked_decisions: List[Dict[str, Any]] = []
        for item in evidence_ref.get("linked_decisions") or []:
            if not isinstance(item, dict):
                continue
            entity_type = item.get("entity_type")
            entity_ref = item.get("entity_ref")
            linked_decisions.append(
                {
                    "entity_type": entity_type,
                    "entity_ref": entity_ref,
                    "display_label": item.get("display_label")
                    or self._kw03_entity_display_label(entity_type, entity_ref),
                    "route_href": item.get("route_href")
                    or self._kw03_entity_route_href(entity_type, entity_ref),
                    "link_type": item.get("link_type") or evidence_ref.get("link_type"),
                    "relationship_note": item.get("relationship_note"),
                }
            )

        source_note_context = (
            evidence_ref.get("source_note_context")
            if isinstance(evidence_ref.get("source_note_context"), dict)
            else None
        )
        source_memory_context = (
            evidence_ref.get("source_memory_context")
            if isinstance(evidence_ref.get("source_memory_context"), dict)
            else None
        )

        return {
            "ref_id": projected.get("ref_id"),
            # evidence_type is used by main.py for top-level capability redaction; not exposed in the API response.
            "evidence_type": projected.get("evidence_type"),
            "display_label": projected.get("display_label"),
            "route_href": projected.get("route_href"),
            "source_document": {
                "title": projected["source_document"].get("title"),
                "source_type": projected["source_document"].get("source_type"),
                "excerpt": source_document.get("excerpt"),
                "source_ref": projected["source_document"].get("source_ref"),
                "storage_preview": {
                    "available": bool(storage_preview.get("available")),
                    "preview_type": storage_preview.get("preview_type") or "unavailable",
                    "preview_token": storage_preview.get("preview_token"),
                },
                "captured_at": projected["source_document"].get("captured_at"),
                "captured_by": source_document.get("captured_by"),
            },
            "link_type": projected.get("link_type"),
            "credibility": self._kw03_normalize_credibility(
                evidence_ref.get("credibility"),
                include_detail=True,
            ),
            "resolved_link": projected.get("resolved_link"),
            "linked_object_summary": projected.get("linked_object_summary"),
            "linked_decisions": linked_decisions,
            "source_note_context": json.loads(json.dumps(source_note_context)),
            "source_memory_context": json.loads(json.dumps(source_memory_context)),
            "created_at": evidence_ref.get("created_at") or projected["source_document"].get("captured_at"),
        }

    def list_evidence_refs(
        self,
        *,
        tenant_id: Optional[str] = None,
        include_tenant_agnostic: bool = True,
        linked_entities: Optional[set[tuple[str, str]]] = None,
        source_types: Optional[set[str]] = None,
        include_scope_metadata: bool = False,
    ) -> List[Dict[str, Any]]:
        evidence_refs = self._read_dataset_records("evidence_refs")
        evidence_refs = [
            evidence_ref
            for evidence_ref in evidence_refs
            if self._record_matches_tenant(
                evidence_ref,
                tenant_id,
                include_tenant_agnostic=include_tenant_agnostic,
            )
            and self._evidence_matches_scope(
                evidence_ref,
                linked_entities=linked_entities,
                source_types=source_types,
            )
        ]
        evidence_refs.sort(
            key=lambda evidence_ref: (
                (_parse_rfc3339(
                    ((evidence_ref.get("source_document") or {}).get("captured_at"))
                    or evidence_ref.get("created_at")
                )
                or datetime.min).replace(tzinfo=None),
                str(evidence_ref.get("ref_id") or ""),
            ),
            reverse=True,
        )
        return [
            self._project_evidence_ref_list_item(
                evidence_ref,
                include_scope_metadata=include_scope_metadata,
            )
            for evidence_ref in evidence_refs
        ]

    def get_evidence_ref(self, ref_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not ref_id:
            return None
        available, evidence_ref = self._service.record("evidence_refs", ref_id)
        if not available:
            evidence_ref = (self._local_fallback("evidence_refs") or {}).get(ref_id)
        if not evidence_ref:
            return None
        return self._project_evidence_ref_list_item(evidence_ref)

    def get_evidence_ref_detail(self, ref_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not ref_id:
            return None
        available, evidence_ref = self._service.record("evidence_refs", ref_id)
        if not available:
            evidence_ref = (self._local_fallback("evidence_refs") or {}).get(ref_id)
        if not evidence_ref:
            return None
        return self._project_evidence_ref_detail(evidence_ref)

    @staticmethod
    def _kw04_route_href(insight_id: Optional[str]) -> Optional[str]:
        ref = str(insight_id or "").strip()
        if not ref:
            return None
        return f"/knowledge/insights/{ref}"

    def _kw04_scope_context(self, scope: Optional[str], scope_ref: Optional[str]) -> Dict[str, Any]:
        normalized_scope = str(scope or "").strip().lower()
        ref = str(scope_ref or "").strip()
        if normalized_scope == "global" or not ref:
            return {
                "scope_ref": None,
                "display_label": None,
                "route_href": None,
            }
        if normalized_scope == "persona":
            persona = self.get_persona(ref) or {}
            return {
                "scope_ref": ref,
                "display_label": persona.get("name"),
                "route_href": f"/personas/{ref}",
            }
        if normalized_scope == "strategy":
            strategy_spec = self.get_strategy_spec(ref) or {}
            title = strategy_spec.get("title") or strategy_spec.get("name")
            display = f"{title} — Strategy Spec" if title else None
            return {
                "scope_ref": ref,
                "display_label": display,
                "route_href": f"/knowledge/strategy-specs/{ref}",
            }
        if normalized_scope == "experiment":
            experiment = self.get_research_experiment(ref) or {}
            return {
                "scope_ref": ref,
                "display_label": experiment.get("experiment_name"),
                "route_href": f"/research/experiments/{ref}",
            }
        if normalized_scope == "incident":
            return {
                "scope_ref": ref,
                "display_label": f"Incident {ref}",
                "route_href": f"/operator/post-incident-review?incident={ref}",
            }
        return {
            "scope_ref": ref,
            "display_label": None,
            "route_href": None,
        }

    def _project_kw04_supporting_evidence_ref(self, item: Dict[str, Any]) -> Dict[str, Any]:
        ref_id = str(item.get("ref_id") or "").strip()
        evidence_ref = self.get_evidence_ref(ref_id) if ref_id else None
        source_document = (
            evidence_ref.get("source_document")
            if isinstance((evidence_ref or {}).get("source_document"), dict)
            else {}
        )
        credibility = (
            evidence_ref.get("credibility")
            if isinstance((evidence_ref or {}).get("credibility"), dict)
            else {}
        )
        return {
            "ref_id": ref_id,
            "source_document_title": (
                item.get("source_document_title")
                or source_document.get("title")
                or (evidence_ref or {}).get("display_label")
                or ref_id
            ),
            "link_type": item.get("link_type") or (evidence_ref or {}).get("link_type"),
            "credibility_tier": item.get("credibility_tier") or credibility.get("tier") or "unverified",
            "resolved_link": self._kw03_normalize_resolved_link(
                item.get("resolved_link") or (evidence_ref or {}).get("resolved_link")
            ),
        }

    def _project_kw04_linked_source(self, item: Dict[str, Any]) -> Dict[str, Any]:
        entity_type = item.get("entity_type")
        entity_ref = item.get("entity_ref")
        return {
            "entity_type": entity_type,
            "entity_ref": entity_ref,
            "display_label": item.get("display_label")
            or self._kw03_entity_display_label(entity_type, entity_ref),
            "route_href": item.get("route_href")
            or self._kw03_entity_route_href(entity_type, entity_ref),
            "relationship_note": item.get("relationship_note"),
        }

    def _project_insight_card_list_item(self, insight_card: Dict[str, Any]) -> Dict[str, Any]:
        confidence = insight_card.get("confidence") if isinstance(insight_card.get("confidence"), dict) else {}
        provenance = (
            insight_card.get("aggregation_provenance")
            if isinstance(insight_card.get("aggregation_provenance"), dict)
            else {}
        )
        supporting_evidence_refs = [
            self._project_kw04_supporting_evidence_ref(item)
            for item in insight_card.get("supporting_evidence_refs") or []
            if isinstance(item, dict)
        ]
        linked_sources = [
            self._project_kw04_linked_source(item)
            for item in insight_card.get("linked_sources") or []
            if isinstance(item, dict)
        ]
        return {
            "insight_id": insight_card.get("insight_id"),
            "summary": insight_card.get("summary"),
            "scope": insight_card.get("scope"),
            "scope_ref": insight_card.get("scope_ref"),
            "status": insight_card.get("status") or "active",
            "superseded_by_id": insight_card.get("superseded_by_id"),
            "confidence": {
                "score": confidence.get("score"),
                "label": confidence.get("label"),
            },
            "tags": list(insight_card.get("tags") or []),
            "evidence_count": len(supporting_evidence_refs),
            "primary_evidence_count": provenance.get("primary_evidence_count")
            if provenance.get("primary_evidence_count") is not None
            else len(
                [
                    item
                    for item in supporting_evidence_refs
                    if str(item.get("credibility_tier") or "") == "primary"
                ]
            ),
            "aggregated_at": provenance.get("aggregated_at"),
            "route_href": insight_card.get("route_href") or self._kw04_route_href(insight_card.get("insight_id")),
            "linked_sources": linked_sources,
        }

    def _project_insight_card_detail(self, insight_card: Dict[str, Any]) -> Dict[str, Any]:
        projected = self._project_insight_card_list_item(insight_card)
        superseded_by_id = projected.get("superseded_by_id")
        superseded_card = self.get_insight_card(superseded_by_id) if superseded_by_id else None
        confidence = insight_card.get("confidence") if isinstance(insight_card.get("confidence"), dict) else {}
        return {
            "insight_id": projected.get("insight_id"),
            "summary": projected.get("summary"),
            "scope": projected.get("scope"),
            "scope_context": self._kw04_scope_context(
                projected.get("scope"),
                projected.get("scope_ref"),
            ),
            "status": projected.get("status"),
            "superseded_by": {
                "insight_id": superseded_by_id,
                "summary": (superseded_card or {}).get("summary") if superseded_by_id else None,
                "route_href": (superseded_card or {}).get("route_href") if superseded_by_id else None,
            },
            "confidence": {
                "score": confidence.get("score"),
                "label": confidence.get("label"),
                "basis": confidence.get("basis"),
            },
            "tags": projected.get("tags"),
            "source_ref": insight_card.get("source_ref"),
            "supporting_evidence_refs": [
                self._project_kw04_supporting_evidence_ref(item)
                for item in insight_card.get("supporting_evidence_refs") or []
                if isinstance(item, dict)
            ],
            "linked_sources": [
                self._project_kw04_linked_source(item)
                for item in insight_card.get("linked_sources") or []
                if isinstance(item, dict)
            ],
            "aggregation_provenance": json.loads(
                json.dumps(insight_card.get("aggregation_provenance") or {})
            ),
            "created_at": insight_card.get("created_at"),
            "updated_at": insight_card.get("updated_at"),
        }

    def list_insight_cards(self) -> List[Dict[str, Any]]:
        insight_cards = self._read_dataset_records("insight_cards")
        insight_cards.sort(
            key=lambda insight_card: (
                (_parse_rfc3339(
                    ((insight_card.get("aggregation_provenance") or {}).get("aggregated_at"))
                )
                or _parse_rfc3339(insight_card.get("updated_at"))
                or _parse_rfc3339(insight_card.get("created_at"))
                or datetime.min).replace(tzinfo=None),
                str(insight_card.get("insight_id") or ""),
            ),
            reverse=True,
        )
        return [self._project_insight_card_list_item(insight_card) for insight_card in insight_cards]

    def get_insight_card(self, insight_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not insight_id:
            return None
        available, insight_card = self._service.record("insight_cards", insight_id)
        if not available:
            insight_card = (self._local_fallback("insight_cards") or {}).get(insight_id)
        if not insight_card:
            return None
        return self._project_insight_card_list_item(insight_card)

    def get_insight_card_detail(self, insight_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not insight_id:
            return None
        available, insight_card = self._service.record("insight_cards", insight_id)
        if not available:
            insight_card = (self._local_fallback("insight_cards") or {}).get(insight_id)
        if not insight_card:
            return None
        return self._project_insight_card_detail(insight_card)

    @staticmethod
    def _kw05_lifecycle_state(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        mapping = {
            "draft": "draft",
            "candidate": "candidate",
            "approved": "approved",
            "retired": "retired",
            "active": "approved",
        }
        return mapping.get(normalized, "draft")

    @staticmethod
    def _kw05_hypothesis_excerpt(value: Any, limit: int = 180) -> Optional[str]:
        text = " ".join(str(value or "").split())
        if not text:
            return None
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip() + "…"

    @staticmethod
    def _kw05_strategy_route_href(strategy_id: str, version_id: Optional[str] = None) -> str:
        base = f"/knowledge/strategy-specs/{strategy_id}"
        if version_id:
            return f"{base}?version={version_id}"
        return base

    @classmethod
    def _kw05_normalize_citation_bundle(cls, raw: Any) -> Dict[str, Any]:
        bundle = raw if isinstance(raw, dict) else {}
        return {
            "evidence_refs": [
                json.loads(json.dumps(item))
                for item in bundle.get("evidence_refs") or []
                if isinstance(item, dict)
            ],
            "memory_anchors": [
                json.loads(json.dumps(item))
                for item in bundle.get("memory_anchors") or []
                if isinstance(item, dict)
            ],
            "insight_citations": [
                json.loads(json.dumps(item))
                for item in bundle.get("insight_citations") or []
                if isinstance(item, dict)
            ],
        }

    @classmethod
    def _kw05_allowed_actions(cls, version: Dict[str, Any]) -> Dict[str, bool]:
        lifecycle_state = cls._kw05_lifecycle_state(version.get("lifecycle_state"))
        return {
            "canSubmitForApproval": lifecycle_state == "draft",
            "canRetire": lifecycle_state in {"candidate", "approved"},
            "canCompare": lifecycle_state in {"candidate", "approved", "retired"},
        }

    @classmethod
    def _kw05_sort_versions(cls, versions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(
            versions,
            key=lambda version: (
                (_parse_rfc3339(version.get("created_at")) or datetime.min).replace(tzinfo=None),
                str(version.get("spec_version_id") or ""),
            ),
            reverse=True,
        )

    @classmethod
    def _kw05_versions(cls, strategy_spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        strategy_id = str(strategy_spec.get("strategy_id") or strategy_spec.get("id") or "").strip()
        if not strategy_id:
            return []

        raw_versions = strategy_spec.get("versions")
        candidates = raw_versions if isinstance(raw_versions, list) and raw_versions else [strategy_spec]
        versions: List[Dict[str, Any]] = []
        for index, raw_version in enumerate(candidates, start=1):
            if not isinstance(raw_version, dict):
                continue
            provenance = (
                raw_version.get("provenance")
                if isinstance(raw_version.get("provenance"), dict)
                else {}
            )
            spec_version_id = str(
                raw_version.get("spec_version_id")
                or (
                    strategy_spec.get("current_spec_version_id")
                    if index == 1 and len(candidates) == 1
                    else ""
                )
                or raw_version.get("id")
                or strategy_id
            ).strip()
            spec_version = str(
                raw_version.get("spec_version")
                or (
                    strategy_spec.get("current_spec_version")
                    if index == 1 and len(candidates) == 1
                    else ""
                )
                or f"v{index}"
            ).strip()
            title = (
                raw_version.get("title")
                or strategy_spec.get("title")
                or strategy_spec.get("name")
            )
            version = {
                "object_ref": {
                    "type": "StrategySpec",
                    "id": spec_version_id,
                },
                "strategy_id": strategy_id,
                "spec_version_id": spec_version_id,
                "spec_version": spec_version,
                "parent_spec_version_id": raw_version.get("parent_spec_version_id"),
                "derived_from_source_refs": list(
                    raw_version.get("derived_from_source_refs")
                    or provenance.get("source_refs")
                    or []
                ),
                "lifecycle_state": cls._kw05_lifecycle_state(
                    raw_version.get("lifecycle_state")
                    or raw_version.get("status")
                    or strategy_spec.get("lifecycle_state")
                    or strategy_spec.get("status")
                ),
                "title": title,
                "hypothesis": raw_version.get("hypothesis") or strategy_spec.get("hypothesis"),
                "objective": raw_version.get("objective") or strategy_spec.get("objective"),
                "market_scope": json.loads(
                    json.dumps(raw_version.get("market_scope") or strategy_spec.get("market_scope") or {})
                ),
                "execution_profile": json.loads(
                    json.dumps(
                        raw_version.get("execution_profile")
                        or strategy_spec.get("execution_profile")
                        or {}
                    )
                ),
                "evaluation_plan": json.loads(
                    json.dumps(
                        raw_version.get("evaluation_plan")
                        or strategy_spec.get("evaluation_plan")
                        or {}
                    )
                ),
                "governance": json.loads(
                    json.dumps(raw_version.get("governance") or strategy_spec.get("governance") or {})
                ),
                "citation_bundle": cls._kw05_normalize_citation_bundle(
                    raw_version.get("citation_bundle") or strategy_spec.get("citation_bundle")
                ),
                "source_kind": (
                    raw_version.get("source_kind")
                    or provenance.get("source_kind")
                    or strategy_spec.get("source_kind")
                ),
                "persona_ids": list(raw_version.get("persona_ids") or strategy_spec.get("persona_ids") or []),
                "created_at": (
                    raw_version.get("created_at")
                    or provenance.get("created_at")
                    or strategy_spec.get("created_at")
                    or strategy_spec.get("updated_at")
                ),
                "created_by": raw_version.get("created_by") or provenance.get("created_by"),
                "last_modified_at": (
                    raw_version.get("updated_at")
                    or raw_version.get("last_modified_at")
                    or strategy_spec.get("updated_at")
                    or raw_version.get("created_at")
                    or provenance.get("created_at")
                ),
            }
            version["allowedActions"] = cls._kw05_allowed_actions(version)
            versions.append(version)

        return cls._kw05_sort_versions(versions)

    @classmethod
    def _kw05_current_version(
        cls,
        strategy_spec: Dict[str, Any],
        versions: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        current_spec_version_id = str(strategy_spec.get("current_spec_version_id") or "").strip()
        if current_spec_version_id:
            for version in versions:
                if str(version.get("spec_version_id") or "") == current_spec_version_id:
                    return version
        return versions[0] if versions else None

    @classmethod
    def _kw05_find_version(
        cls,
        strategy_spec: Dict[str, Any],
        selector: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        versions = cls._kw05_versions(strategy_spec)
        if not versions:
            return None
        normalized = str(selector or "current").strip()
        if normalized in {"", "current"}:
            return cls._kw05_current_version(strategy_spec, versions)
        for version in versions:
            if normalized in {
                str(version.get("spec_version_id") or ""),
                str(version.get("spec_version") or ""),
            }:
                return version
        return None

    @classmethod
    def _kw05_compare_section(
        cls,
        left: Dict[str, Any],
        right: Dict[str, Any],
        field: str,
        label: str,
        *,
        breaking: bool = False,
    ) -> Optional[Dict[str, Any]]:
        if json.dumps(left.get(field), sort_keys=True) == json.dumps(right.get(field), sort_keys=True):
            return None
        summary = f"{label} changed from {left.get('spec_version')} to {right.get('spec_version')}."
        if field == "execution_profile":
            left_mode = ((left.get(field) or {}).get("execution_mode_hint"))
            right_mode = ((right.get(field) or {}).get("execution_mode_hint"))
            if left_mode != right_mode and left_mode and right_mode:
                summary = f"Execution mode hint changed from {left_mode} to {right_mode}."
        if field == "evaluation_plan":
            summary = "Evaluation gates or metrics changed."
        if field == "market_scope":
            summary = "Market scope changed."
        if field == "governance":
            summary = "Governance policy or approval requirements changed."
        if field == "hypothesis":
            summary = "Hypothesis changed."
        if field == "objective":
            summary = "Objective changed."
        payload = {
            "section": field,
            "summary": summary,
        }
        if breaking:
            payload["severity"] = "breaking"
        return payload

    def list_strategy_specs(
        self,
        *,
        lifecycle_state: Optional[str] = None,
        source_kind: Optional[str] = None,
        persona_id: Optional[str] = None,
        include_retired: bool = False,
        include_fixture_pack: bool = True,
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for strategy_spec in self._read_dataset_records("strategy_specs"):
            versions = self._kw05_versions(strategy_spec)
            current_version = self._kw05_current_version(strategy_spec, versions)
            if current_version is None:
                continue

            current_lifecycle_state = str(current_version.get("lifecycle_state") or "")
            current_source_kind = str(current_version.get("source_kind") or "")
            persona_ids = {
                str(value)
                for value in (current_version.get("persona_ids") or [])
                if str(value).strip()
            }

            if lifecycle_state and lifecycle_state != "all" and current_lifecycle_state != lifecycle_state:
                continue
            if not include_retired and lifecycle_state in {None, "", "all"} and current_lifecycle_state == "retired":
                continue
            if source_kind and current_source_kind != source_kind:
                continue
            if persona_id and str(persona_id) not in persona_ids:
                continue

            strategy_id = str(current_version.get("strategy_id") or "")
            if not include_fixture_pack and _is_fixture_pack_record({"strategy_id": strategy_id}):
                continue
            items.append(
                {
                    "object_ref": json.loads(json.dumps(current_version.get("object_ref") or {})),
                    "strategy_id": strategy_id,
                    "current_spec_version_id": current_version.get("spec_version_id"),
                    "current_spec_version": current_version.get("spec_version"),
                    "title": current_version.get("title"),
                    "lifecycle_state": current_lifecycle_state,
                    "source_kind": current_source_kind,
                    "hypothesis_excerpt": self._kw05_hypothesis_excerpt(current_version.get("hypothesis")),
                    "version_count": len(versions),
                    "last_modified_at": current_version.get("last_modified_at"),
                    "route_href": self._kw05_strategy_route_href(strategy_id),
                }
            )

        items.sort(
            key=lambda item: (
                (_parse_rfc3339(item.get("last_modified_at")) or datetime.min).replace(tzinfo=None),
                str(item.get("strategy_id") or ""),
            ),
            reverse=True,
        )
        return items

    def get_strategy_spec(self, strategy_id: Optional[str]) -> Optional[Dict[str, Any]]:
        detail = self.get_strategy_spec_detail(strategy_id, version_selector="current")
        if not detail:
            return None
        return {
            "strategy_id": detail.get("strategy_id"),
            "title": detail.get("title"),
            "name": detail.get("title"),
            "spec_version_id": detail.get("spec_version_id"),
            "spec_version": detail.get("spec_version"),
            "lifecycle_state": detail.get("lifecycle_state"),
        }

    def get_strategy_spec_detail(
        self,
        strategy_id: Optional[str],
        *,
        version_selector: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not strategy_id:
            return None
        available, strategy_spec = self._service.record("strategy_specs", strategy_id)
        if not available:
            strategy_spec = (self._local_fallback("strategy_specs") or {}).get(strategy_id)
        if not strategy_spec:
            return None
        version = self._kw05_find_version(strategy_spec, version_selector)
        if not version:
            return None
        return json.loads(json.dumps(version))

    def list_strategy_spec_versions(self, strategy_id: Optional[str]) -> List[Dict[str, Any]]:
        if not strategy_id:
            return []
        available, strategy_spec = self._service.record("strategy_specs", strategy_id)
        if not available:
            strategy_spec = (self._local_fallback("strategy_specs") or {}).get(strategy_id)
        if not strategy_spec:
            return []
        return [
            {
                "spec_version_id": version.get("spec_version_id"),
                "spec_version": version.get("spec_version"),
                "lifecycle_state": version.get("lifecycle_state"),
                "created_at": version.get("created_at"),
                "created_by": version.get("created_by"),
                "parent_spec_version_id": version.get("parent_spec_version_id"),
                "route_href": self._kw05_strategy_route_href(
                    str(version.get("strategy_id") or ""),
                    str(version.get("spec_version_id") or ""),
                ),
            }
            for version in self._kw05_versions(strategy_spec)
        ]

    def compare_strategy_spec_versions(
        self,
        strategy_id: Optional[str],
        *,
        left_selector: str,
        right_selector: str,
    ) -> Optional[Dict[str, Any]]:
        if not strategy_id:
            return None
        available, strategy_spec = self._service.record("strategy_specs", strategy_id)
        if not available:
            strategy_spec = (self._local_fallback("strategy_specs") or {}).get(strategy_id)
        if not strategy_spec:
            return None
        left = self._kw05_find_version(strategy_spec, left_selector)
        right = self._kw05_find_version(strategy_spec, right_selector)
        if not left or not right:
            return None

        changed_sections = [
            item
            for item in [
                self._kw05_compare_section(left, right, "hypothesis", "Hypothesis"),
                self._kw05_compare_section(left, right, "objective", "Objective"),
                self._kw05_compare_section(left, right, "market_scope", "Market scope"),
                self._kw05_compare_section(left, right, "evaluation_plan", "Evaluation plan"),
                self._kw05_compare_section(left, right, "governance", "Governance"),
            ]
            if item is not None
        ]
        breaking_changes = [
            item
            for item in [
                self._kw05_compare_section(
                    left,
                    right,
                    "execution_profile",
                    "Execution profile",
                    breaking=True,
                )
            ]
            if item is not None
        ]

        evidence_refs = sorted(
            {
                str(item.get("ref_id") or "")
                for item in (
                    (left.get("citation_bundle") or {}).get("evidence_refs") or []
                ) + (
                    (right.get("citation_bundle") or {}).get("evidence_refs") or []
                )
                if isinstance(item, dict) and str(item.get("ref_id") or "").strip()
            }
        )

        return {
            "strategy_id": str(strategy_id),
            "left_spec_version_id": left.get("spec_version_id"),
            "right_spec_version_id": right.get("spec_version_id"),
            "changed_sections": changed_sections,
            "breaking_changes": breaking_changes,
            "evidence_refs": evidence_refs,
        }

    def create_research_note(self, note: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        note_id = str(note.get("note_id") or "").strip()
        if not note_id:
            return None

        service_store_path = self._service._resolve_path("research_notes")
        persist_service_store = service_store_path is not None
        notes: Optional[Dict[str, Any]]
        if persist_service_store:
            available, service_notes = self._service.list_records("research_notes")
            if not available and service_store_path.exists():
                return None
            notes = {
                str(existing.get("note_id") or existing.get("id") or ""): json.loads(json.dumps(existing))
                for existing in service_notes
                if isinstance(existing, dict) and str(existing.get("note_id") or existing.get("id") or "").strip()
            }
        else:
            notes = self._local_fallback("research_notes")
        if notes is None:
            return None

        notes[note_id] = json.loads(json.dumps(note))
        if persist_service_store:
            self._service.write_records("research_notes", notes)
        else:
            self._save()
        return json.loads(json.dumps(note))

    def create_research_ticket(
        self,
        *,
        title: str,
        description: str,
        priority: str,
        owner: str,
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        service_store_path = self._service._resolve_path("research_tickets")
        persist_service_store = service_store_path is not None
        tickets: Optional[Dict[str, Any]]
        if persist_service_store:
            available, service_tickets = self._service.list_records("research_tickets")
            if not available and service_store_path.exists():
                raise RuntimeError("Research ticket store is unavailable.")
            tickets = {
                str(ticket.get("ticket_id") or ticket.get("id") or ""): json.loads(json.dumps(ticket))
                for ticket in service_tickets
                if isinstance(ticket, dict) and str(ticket.get("ticket_id") or ticket.get("id") or "").strip()
            }
        else:
            tickets = self._local_fallback("research_tickets")
        if tickets is None:
            raise RuntimeError("Research ticket store is unavailable.")

        timestamp = created_at or _utc_now_rfc3339()
        ticket_id = f"rt-{timestamp[:10].replace('-', '')}-{len(tickets) + 1:03d}"
        while ticket_id in tickets:
            ticket_id = f"rt-{timestamp[:10].replace('-', '')}-{len(tickets) + 2:03d}"

        ticket = {
            "ticket_id": ticket_id,
            "title": title,
            "description": description,
            "status": "open",
            "priority": priority,
            "owner": owner,
            "created_at": timestamp,
            "updated_at": timestamp,
            "closed_at": None,
            "archived_at": None,
            "lifecycle_history": [
                {
                    "from_status": None,
                    "to_status": "open",
                    "transitioned_at": timestamp,
                    "transitioned_by": actor_id,
                }
            ],
            "linked_experiments": [],
            "linked_artifacts": [],
        }
        tickets[ticket_id] = ticket
        if persist_service_store:
            self._service.write_records("research_tickets", tickets)
        else:
            self._save()
        return self._project_research_ticket_detail(ticket)

    def patch_research_ticket(
        self,
        ticket_id: str,
        *,
        patch: Dict[str, Any],
        actor_id: str,
        updated_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        service_store_path = self._service._resolve_path("research_tickets")
        persist_service_store = service_store_path is not None
        tickets: Optional[Dict[str, Any]]
        if persist_service_store:
            available, service_tickets = self._service.list_records("research_tickets")
            if not available and service_store_path.exists():
                return None
            tickets = {
                str(ticket.get("ticket_id") or ticket.get("id") or ""): json.loads(json.dumps(ticket))
                for ticket in service_tickets
                if isinstance(ticket, dict) and str(ticket.get("ticket_id") or ticket.get("id") or "").strip()
            }
        else:
            tickets = self._local_fallback("research_tickets")
        if tickets is None:
            return None
        ticket = tickets.get(ticket_id)
        if ticket is None:
            return None

        timestamp = updated_at or _utc_now_rfc3339()
        editable_fields = {"title", "description", "priority", "owner"}
        for field in editable_fields:
            if field in patch:
                ticket[field] = patch[field]

        next_status = patch.get("status")
        if next_status is not None and next_status != ticket.get("status"):
            previous_status = ticket.get("status")
            ticket["status"] = next_status
            if next_status == "closed":
                ticket["closed_at"] = timestamp
                ticket["archived_at"] = None
            elif next_status == "archived":
                ticket["archived_at"] = timestamp
                if ticket.get("closed_at") is None:
                    ticket["closed_at"] = timestamp
            else:
                if next_status in {"open", "in_progress"}:
                    ticket["closed_at"] = None
                if next_status != "archived":
                    ticket["archived_at"] = None
            ticket.setdefault("lifecycle_history", []).append(
                {
                    "from_status": previous_status,
                    "to_status": next_status,
                    "transitioned_at": timestamp,
                    "transitioned_by": actor_id,
                }
            )

        ticket["updated_at"] = timestamp
        if persist_service_store:
            self._service.write_records("research_tickets", tickets)
        else:
            self._save()
        return self._project_research_ticket_detail(ticket)

    @staticmethod
    def _institutional_memory_scope(entry: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        scope = entry.get("scope")
        if isinstance(scope, dict):
            return (
                scope.get("type") or scope.get("scope_type") or scope.get("value"),
                scope.get("filter") or scope.get("scope_filter") or scope.get("scope_ref"),
            )
        return scope, entry.get("scope_filter")

    @staticmethod
    def _institutional_memory_lifecycle(entry: Dict[str, Any]) -> Dict[str, Any]:
        lifecycle = entry.get("lifecycle") if isinstance(entry.get("lifecycle"), dict) else {}
        superseded_by = lifecycle.get("superseded_by") or entry.get("superseded_by")
        status = lifecycle.get("status") or lifecycle.get("state")
        if not status:
            if superseded_by:
                status = "superseded"
            elif entry.get("archived_at"):
                status = "archived"
            else:
                status = "active"
        return {"status": status, "superseded_by": superseded_by}

    @staticmethod
    def _institutional_memory_usage(entry: Dict[str, Any]) -> Dict[str, Any]:
        usage = entry.get("usage") if isinstance(entry.get("usage"), dict) else {}
        return {
            **usage,
            "reuse_count": usage.get("reuse_count") if "reuse_count" in usage else entry.get("reuse_count", 0),
        }

    @staticmethod
    def _institutional_memory_source_event(entry: Dict[str, Any]) -> Dict[str, Any]:
        source_event = entry.get("source_event") if isinstance(entry.get("source_event"), dict) else {}
        event_type = source_event.get("type") or entry.get("source_event_type")
        event_id = source_event.get("id") or entry.get("source_event_id")
        if not event_type and not event_id:
            return json.loads(json.dumps(source_event))
        projected = {**source_event, "type": event_type, "id": event_id}
        return json.loads(json.dumps({key: value for key, value in projected.items() if value is not None}))

    @staticmethod
    def _project_institutional_memory_summary(entry: Dict[str, Any]) -> Dict[str, Any]:
        entry_id = str(entry.get("entry_id") or entry.get("id") or "")
        content = entry.get("content") if isinstance(entry.get("content"), dict) else {}
        scope, scope_filter = ReadSurfaceStore._institutional_memory_scope(entry)
        lifecycle = ReadSurfaceStore._institutional_memory_lifecycle(entry)
        usage = ReadSurfaceStore._institutional_memory_usage(entry)
        return {
            "entry_id": entry_id,
            "knowledge_type": entry.get("knowledge_type"),
            "headline": content.get("headline"),
            "scope": scope,
            "scope_filter": scope_filter,
            "written_at": entry.get("written_at"),
            "write_authority": entry.get("write_authority"),
            "tags": list(content.get("tags") or []),
            "reuse_count": usage.get("reuse_count") or 0,
            "is_superseded": str(lifecycle.get("status") or "").strip().lower() == "superseded",
            "route_href": f"/knowledge/memory/{entry_id}",
        }

    @staticmethod
    def _project_institutional_memory_detail(entry: Dict[str, Any]) -> Dict[str, Any]:
        scope, scope_filter = ReadSurfaceStore._institutional_memory_scope(entry)
        lifecycle = ReadSurfaceStore._institutional_memory_lifecycle(entry)
        usage = ReadSurfaceStore._institutional_memory_usage(entry)
        return {
            "entry_id": entry.get("entry_id") or entry.get("id"),
            "knowledge_type": entry.get("knowledge_type"),
            "content": json.loads(json.dumps(entry.get("content") or {})),
            "source_event": ReadSurfaceStore._institutional_memory_source_event(entry),
            "contributing_persona_ids": list(entry.get("contributing_persona_ids") or []),
            "written_at": entry.get("written_at"),
            "write_authority": entry.get("write_authority"),
            "scope": {"type": scope, "filter": scope_filter},
            "lifecycle": lifecycle,
            "usage": usage,
        }

    def list_institutional_memory_entries(self) -> List[Dict[str, Any]]:
        available, entries = self._service.list_records(
            "institutional_memory_entries",
            include_snapshot_fallback=False,
        )
        if not available:
            return []
        entries.sort(
            key=lambda entry: (
                (_parse_rfc3339(entry.get("written_at")) or datetime.min).replace(tzinfo=None),
                int(self._institutional_memory_usage(entry).get("reuse_count") or 0),
            ),
            reverse=True,
        )
        return [self._project_institutional_memory_summary(entry) for entry in entries]

    def get_institutional_memory_entry(
        self,
        entry_id: Optional[str],
        *,
        include_snapshot_fallback: bool = True,
    ) -> Optional[Dict[str, Any]]:
        if not entry_id:
            return None
        available, entry = self._service.record(
            "institutional_memory_entries",
            entry_id,
            include_snapshot_fallback=include_snapshot_fallback,
        )
        if not available:
            return None
        if not entry:
            return None
        return self._project_institutional_memory_detail(entry)

    # ------------------------------------------------------------------ #
    # Research Analyze surfaces (RW-03)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _project_research_analysis_summary(analysis: Dict[str, Any]) -> Dict[str, Any]:
        metric_groups = list(analysis.get("metric_groups") or [])
        return {
            "analysis_id": analysis.get("analysis_id"),
            "ticket_id": analysis.get("ticket_id"),
            "experiment_id": analysis.get("experiment_id"),
            "status": analysis.get("status"),
            "run_at": analysis.get("run_at"),
            "summary": {
                "headline": ((analysis.get("summary") or {}).get("headline")),
                "verdict": ((analysis.get("summary") or {}).get("verdict")),
            },
            "metric_group_refs": [
                str(group.get("group_key") or "")
                for group in metric_groups
                if str(group.get("group_key") or "").strip()
            ],
        }

    @staticmethod
    def _project_research_analysis_detail(analysis: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "analysis_id": analysis.get("analysis_id"),
            "ticket_id": analysis.get("ticket_id"),
            "experiment_id": analysis.get("experiment_id"),
            "status": analysis.get("status"),
            "run_at": analysis.get("run_at"),
            "completed_at": analysis.get("completed_at"),
            "summary": json.loads(json.dumps(analysis.get("summary") or {})),
            "metric_groups": json.loads(json.dumps(analysis.get("metric_groups") or [])),
            "comparative_summary": json.loads(json.dumps(analysis.get("comparative_summary") or {})),
        }

    @staticmethod
    def _date_range_cutoff_token(date_range: Optional[str]) -> Optional[int]:
        if date_range == "24h":
            return 1
        if date_range == "7d":
            return 7
        if date_range == "30d":
            return 30
        if date_range == "90d":
            return 90
        return None

    def list_research_analyses(
        self,
        *,
        ticket_id: Optional[str] = None,
        experiment_id: Optional[str] = None,
        statuses: Optional[List[str]] = None,
        date_range: Optional[str] = None,
        include_snapshot_fallback: bool = True,
        include_local_fallback: bool = True,
    ) -> List[Dict[str, Any]]:
        analyses = self._read_dataset_records(
            "research_analyses",
            include_snapshot_fallback=include_snapshot_fallback,
            include_local_fallback=include_local_fallback,
        )
        if ticket_id:
            analyses = [
                analysis
                for analysis in analyses
                if str(analysis.get("ticket_id") or "") == str(ticket_id)
            ]
        if experiment_id:
            analyses = [
                analysis
                for analysis in analyses
                if str(analysis.get("experiment_id") or "") == str(experiment_id)
            ]
        if statuses:
            requested_statuses = {str(value).strip().lower() for value in statuses if str(value).strip()}
            analyses = [
                analysis
                for analysis in analyses
                if str(analysis.get("status") or "").strip().lower() in requested_statuses
            ]
        cutoff_days = self._date_range_cutoff_token(date_range)
        if cutoff_days is not None:
            reference_now = datetime.utcnow()
            analyses = [
                analysis
                for analysis in analyses
                if (
                    (parsed := _parse_rfc3339(analysis.get("run_at"))) is not None
                    and (reference_now - parsed.replace(tzinfo=None)).days < cutoff_days
                )
            ]

        # Normalize the sort key to a naive datetime: _parse_rfc3339 returns an
        # aware value for tz-bearing run_at (e.g. "...Z") but the datetime.min
        # fallback (and tz-less run_at) is naive, and sorting a mix of aware and
        # naive datetimes raises TypeError. Strip tzinfo so the key is uniform
        # (consistent with the cutoff comparison above).
        analyses.sort(
            key=lambda analysis: (
                _parse_rfc3339(analysis.get("run_at")) or datetime.min
            ).replace(tzinfo=None),
            reverse=True,
        )
        return [self._project_research_analysis_summary(analysis) for analysis in analyses]

    def get_research_analysis(
        self,
        analysis_id: Optional[str],
        *,
        include_snapshot_fallback: bool = True,
        include_local_fallback: bool = True,
    ) -> Optional[Dict[str, Any]]:
        if not analysis_id:
            return None
        available, analysis = self._service.record(
            "research_analyses",
            analysis_id,
            include_snapshot_fallback=include_snapshot_fallback,
        )
        if not available and include_local_fallback:
            analysis = (self._local_fallback("research_analyses") or {}).get(analysis_id)
        if not analysis:
            return None
        return self._project_research_analysis_detail(analysis)

    # ------------------------------------------------------------------ #
    # Research Experiments (RW-04)
    # ------------------------------------------------------------------ #

    _RW04_TERMINAL_STATUSES = frozenset({"completed", "failed", "canceled"})
    _RW04_CANCELABLE_STATUSES = frozenset({"queued", "running"})

    @classmethod
    def _rw04_can_cancel(cls, status: Optional[str]) -> bool:
        return str(status or "").strip().lower() in cls._RW04_CANCELABLE_STATUSES

    @classmethod
    def _project_research_experiment_summary(cls, exp: Dict[str, Any]) -> Dict[str, Any]:
        status = str(exp.get("status") or "")
        strategy_selector = exp.get("strategy_selector") or {}
        strategy_id = (
            exp.get("linked_strategy_id")
            or exp.get("strategy_id")
            or strategy_selector.get("strategy_id")
        )
        run_config = exp.get("run_config") or {}
        return {
            "experiment_id": exp.get("experiment_id"),
            "ticket_id": exp.get("ticket_id"),
            "experiment_name": exp.get("experiment_name"),
            "status": status,
            "stage": exp.get("stage"),
            "framework": exp.get("framework") or run_config.get("backend"),
            "queued_at": exp.get("queued_at"),
            "started_at": exp.get("started_at"),
            "completed_at": exp.get("completed_at"),
            "strategy_id": strategy_id,
            "linked_strategy_id": strategy_id,
            "dataset_ref": exp.get("dataset_ref") or run_config.get("dataset_ref"),
            "dataset_manifest_id": (
                exp.get("dataset_manifest_id") or run_config.get("dataset_manifest_id")
            ),
            "artifact_ids": list(exp.get("artifact_ids") or []),
            "registry_admission_status": exp.get("registry_admission_status"),
            "can_deploy": bool(exp.get("can_deploy", True)),
            "allowedActions": {"canCancel": cls._rw04_can_cancel(status)},
        }

    @classmethod
    def _project_research_experiment_detail(cls, exp: Dict[str, Any]) -> Dict[str, Any]:
        status = str(exp.get("status") or "")
        failure = exp.get("failure") or {}
        progress = exp.get("progress") or {}
        strategy_selector = exp.get("strategy_selector") or {}
        run_config = exp.get("run_config") or {}
        time_range = run_config.get("time_range") or {}
        launch_context = exp.get("launch_context") or {}
        return {
            "experiment_id": exp.get("experiment_id"),
            "ticket_id": exp.get("ticket_id"),
            "experiment_name": exp.get("experiment_name"),
            "status": status,
            "stage": exp.get("stage"),
            "queued_at": exp.get("queued_at"),
            "started_at": exp.get("started_at"),
            "completed_at": exp.get("completed_at"),
            "progress": {
                "percent": progress.get("percent"),
                "phase": progress.get("phase"),
                "message": progress.get("message"),
            },
            "strategy_selector": {
                "strategy_id": strategy_selector.get("strategy_id"),
                "variant_id": strategy_selector.get("variant_id"),
            },
            "parameter_set": json.loads(json.dumps(exp.get("parameter_set") or {})),
            "run_config": {
                "backend": run_config.get("backend"),
                "dataset_ref": run_config.get("dataset_ref"),
                "dataset_manifest_id": run_config.get("dataset_manifest_id"),
                "time_range": {
                    "start_at": time_range.get("start_at"),
                    "end_at": time_range.get("end_at"),
                },
                "execution_mode": run_config.get("execution_mode"),
                "priority": run_config.get("priority"),
                "requested_by": run_config.get("requested_by"),
            },
            "launch_context": {
                "analysis_refs": (
                    list(launch_context["analysis_refs"])
                    if isinstance(launch_context.get("analysis_refs"), list)
                    else None
                ),
            },
            "validation_warnings": json.loads(json.dumps(exp.get("validation_warnings") or [])),
            "artifact_ids": list(exp.get("artifact_ids") or []),
            "artifact_refs": json.loads(json.dumps(exp.get("artifact_refs") or [])),
            "framework": exp.get("framework") or run_config.get("backend"),
            "dataset_ref": exp.get("dataset_ref") or run_config.get("dataset_ref"),
            "dataset_manifest_id": (
                exp.get("dataset_manifest_id") or run_config.get("dataset_manifest_id")
            ),
            "research_linkage": json.loads(json.dumps(exp.get("research_linkage") or {})),
            "evidence_refs": json.loads(json.dumps(exp.get("evidence_refs") or [])),
            "safety_assertions": json.loads(json.dumps(exp.get("safety_assertions") or {})),
            "registry_admission_status": exp.get("registry_admission_status"),
            "can_deploy": bool(exp.get("can_deploy", True)),
            "deployment_stage": exp.get("deployment_stage"),
            "failure": {
                "reason_code": failure.get("reason_code"),
                "message": failure.get("message"),
            },
            "allowedActions": {"canCancel": cls._rw04_can_cancel(status)},
        }

    def _research_experiments_store(self) -> Dict[str, Any]:
        """Return the live experiment store regardless of fallback setting.

        Uses the service-backed snapshot (same on-disk file create/cancel write
        to) so launch→list/detail/cancel round-trip works with fallback=False.
        Falls through to the in-memory dict so experiments created in the same
        process are always visible before the file-cache is flushed.
        """
        defaults = _governed_research_experiment_defaults()
        available, records = self._service.list_records("research_experiments")
        if available:
            records_by_id = {
                str(r.get("experiment_id") or r.get("id") or ""): r
                for r in records
                if isinstance(r, dict)
            }
        else:
            records_by_id = dict(self._data.get("research_experiments") or {})
        for experiment_id, default in defaults.items():
            existing = records_by_id.get(experiment_id)
            if isinstance(existing, dict):
                _merge_missing_default_values(existing, default)
                continue
            records_by_id[experiment_id] = default
        return records_by_id

    def list_research_experiments(
        self,
        *,
        ticket_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        experiments = list(self._research_experiments_store().values())
        if ticket_id:
            experiments = [
                exp for exp in experiments
                if str(exp.get("ticket_id") or "") == str(ticket_id)
            ]
        if status:
            requested_status = str(status).strip().lower()
            experiments = [
                exp for exp in experiments
                if str(exp.get("status") or "").strip().lower() == requested_status
            ]
        experiments.sort(
            key=lambda exp: (_parse_rfc3339(exp.get("queued_at")) or datetime.min).replace(tzinfo=None),
            reverse=True,
        )
        return [self._project_research_experiment_summary(exp) for exp in experiments]

    def get_research_experiment(self, experiment_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not experiment_id:
            return None
        available, record = self._service.record("research_experiments", experiment_id)
        if available and record:
            return self._project_research_experiment_detail(record) if record else None
        experiment = self._research_experiments_store().get(experiment_id)
        if not experiment:
            return None
        return self._project_research_experiment_detail(experiment)

    def create_research_experiment(
        self,
        *,
        ticket_id: str,
        experiment_name: str,
        strategy_selector: Dict[str, Any],
        parameter_set: Dict[str, Any],
        run_config: Dict[str, Any],
        launch_context: Dict[str, Any],
        queued_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        experiments = self._data.get("research_experiments") or {}

        timestamp = queued_at or _utc_now_rfc3339()
        date_part = timestamp[:10].replace("-", "")
        experiment_id = f"exp-{date_part}-{len(experiments) + 1:03d}"
        while experiment_id in experiments:
            experiment_id = f"exp-{date_part}-{len(experiments) + 2:03d}"

        record: Dict[str, Any] = {
            "experiment_id": experiment_id,
            "ticket_id": ticket_id,
            "experiment_name": experiment_name,
            "status": "queued",
            "queued_at": timestamp,
            "started_at": None,
            "completed_at": None,
            "progress": {"percent": None, "phase": None, "message": None},
            "strategy_selector": json.loads(json.dumps(strategy_selector)),
            "parameter_set": json.loads(json.dumps(parameter_set)),
            "run_config": json.loads(json.dumps(run_config)),
            "launch_context": json.loads(json.dumps(launch_context)),
            "validation_warnings": [],
            "artifact_ids": [],
            "failure": {"reason_code": None, "message": None},
            "allowedActions": {"canCancel": True},
        }

        if isinstance(self._data.get("research_experiments"), dict):
            self._data["research_experiments"][experiment_id] = record
        else:
            self._data.setdefault("research_experiments", {})[experiment_id] = record
        self._save()
        return self._project_research_experiment_detail(record)

    def cancel_research_experiment(
        self,
        experiment_id: str,
        *,
        completed_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        experiments = self._data.get("research_experiments") or {}
        if experiment_id not in experiments:
            return None

        record = dict(json.loads(json.dumps(experiments[experiment_id])))
        status = str(record.get("status") or "").strip().lower()
        if status not in self._RW04_CANCELABLE_STATUSES:
            return None

        record["status"] = "canceled"
        record["completed_at"] = completed_at or _utc_now_rfc3339()
        record["allowedActions"] = {"canCancel": False}

        if isinstance(self._data.get("research_experiments"), dict):
            self._data["research_experiments"][experiment_id] = record
        else:
            self._data.setdefault("research_experiments", {})[experiment_id] = record
        self._save()
        return self._project_research_experiment_detail(record)

    # ------------------------------------------------------------------ #
    # Research Artifacts (RW-05)
    # ------------------------------------------------------------------ #

    _RW05_COMPARABLE_STATUSES = frozenset({"sealed", "superseded"})
    _RW05_FIELD_SPECS = (
        ("metrics.sharpe_ratio", "Sharpe Ratio", "performance", "higher_is_better"),
        ("metrics.sortino_ratio", "Sortino Ratio", "performance", "higher_is_better"),
        ("metrics.max_drawdown", "Max Drawdown", "risk", "higher_is_better"),
        ("metrics.annualized_return", "Annualized Return", "performance", "higher_is_better"),
        ("metrics.win_rate", "Win Rate", "performance", "higher_is_better"),
        ("metrics.avg_trade_duration_days", "Avg Trade Duration", "performance", "lower_is_better"),
        ("metrics.total_trades", "Total Trades", "metadata", "neutral"),
        ("parameters.fast_period", "Fast Period", "parameters", "neutral"),
        ("parameters.slow_period", "Slow Period", "parameters", "neutral"),
        ("parameters.signal_period", "Signal Period", "parameters", "neutral"),
        ("parameters.position_sizing", "Position Sizing", "parameters", "neutral"),
        ("parameters.risk_per_trade", "Risk Per Trade", "parameters", "lower_is_better"),
        ("name", "Artifact Name", "metadata", "neutral"),
        ("produced_by_experiment_id", "Experiment Run", "metadata", "neutral"),
    )

    @classmethod
    def _rw05_can_compare(cls, status: Optional[str]) -> bool:
        return str(status or "").strip().lower() in cls._RW05_COMPARABLE_STATUSES

    def _research_artifacts_store(self) -> Dict[str, Any]:
        available, records = self._service.list_records("research_artifacts")
        if available:
            return {str(r.get("artifact_id") or r.get("id") or ""): r for r in records}
        return self._data.get("research_artifacts") or {}

    def _rw05_lineage_versions(self, lineage_id: Optional[str]) -> List[Dict[str, Any]]:
        artifacts = list(self._research_artifacts_store().values())
        chain = [
            artifact
            for artifact in artifacts
            if str(artifact.get("lineage_id") or "") == str(lineage_id or "")
        ]
        chain.sort(
            key=lambda artifact: (
                int(artifact.get("version") or 0),
                str(artifact.get("created_at") or ""),
            )
        )
        return chain

    @classmethod
    def _rw05_metric_summary(cls, artifact: Dict[str, Any]) -> Dict[str, Any]:
        metrics = artifact.get("metrics") or {}
        return {
            "sharpe_ratio": metrics.get("sharpe_ratio"),
            "max_drawdown": metrics.get("max_drawdown"),
            "annualized_return": metrics.get("annualized_return"),
        }

    def _rw05_is_current_version(self, artifact: Dict[str, Any]) -> bool:
        lineage_chain = self._rw05_lineage_versions(artifact.get("lineage_id"))
        if not lineage_chain:
            return False
        latest = max(lineage_chain, key=lambda item: int(item.get("version") or 0))
        return str(latest.get("artifact_id") or "") == str(artifact.get("artifact_id") or "")

    def _project_research_artifact_summary(self, artifact: Dict[str, Any]) -> Dict[str, Any]:
        metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
        provenance = artifact.get("provenance") if isinstance(artifact.get("provenance"), dict) else {}
        strategy_id = (
            artifact.get("linked_strategy_id")
            or artifact.get("strategy_id")
            or metadata.get("linked_strategy_id")
            or metadata.get("strategy_id")
            or provenance.get("linked_strategy_id")
            or provenance.get("strategy_id")
        )
        return {
            "artifact_id": artifact.get("artifact_id"),
            "lineage_id": artifact.get("lineage_id"),
            "version": artifact.get("version"),
            "status": artifact.get("status"),
            "name": artifact.get("name"),
            "artifact_type": artifact.get("artifact_type"),
            "produced_by_experiment_id": artifact.get("produced_by_experiment_id"),
            "linked_ticket_id": artifact.get("linked_ticket_id"),
            "strategy_id": strategy_id,
            "linked_strategy_id": strategy_id,
            "created_at": artifact.get("created_at"),
            "metric_summary": self._rw05_metric_summary(artifact),
            "experiment_refs": self._rw05_experiment_refs(artifact),
            "research_linkage": self._rw05_research_linkage(artifact),
            "is_current_version": self._rw05_is_current_version(artifact),
            "allowedActions": {
                "canCompare": self._rw05_can_compare(artifact.get("status")),
            },
        }

    @staticmethod
    def _rw05_experiment_refs(artifact: Dict[str, Any]) -> List[Dict[str, Any]]:
        refs = artifact.get("experiment_refs")
        metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
        provenance = artifact.get("provenance") if isinstance(artifact.get("provenance"), dict) else {}
        if refs is None:
            refs = metadata.get("experiment_refs")
        if refs is None:
            refs = provenance.get("experiment_refs")
        if refs is None and artifact.get("experiment_ref") is not None:
            refs = [artifact["experiment_ref"]]
        if not isinstance(refs, list):
            return []
        return [json.loads(json.dumps(ref)) for ref in refs if isinstance(ref, dict)]

    @staticmethod
    def _rw05_research_linkage(artifact: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
        provenance = artifact.get("provenance") if isinstance(artifact.get("provenance"), dict) else {}
        for source in (artifact, metadata, provenance):
            linkage = source.get("research_linkage")
            if isinstance(linkage, dict):
                return json.loads(json.dumps(linkage))
        return None

    def _project_research_artifact_detail(self, artifact: Dict[str, Any]) -> Dict[str, Any]:
        lineage_chain = self._rw05_lineage_versions(artifact.get("lineage_id"))
        metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
        provenance = artifact.get("provenance") if isinstance(artifact.get("provenance"), dict) else {}
        strategy_id = (
            artifact.get("linked_strategy_id")
            or artifact.get("strategy_id")
            or metadata.get("linked_strategy_id")
            or metadata.get("strategy_id")
            or provenance.get("linked_strategy_id")
            or provenance.get("strategy_id")
        )
        return {
            "artifact_id": artifact.get("artifact_id"),
            "lineage_id": artifact.get("lineage_id"),
            "version": artifact.get("version"),
            "parent_artifact_id": artifact.get("parent_artifact_id"),
            "status": artifact.get("status"),
            "name": artifact.get("name"),
            "artifact_type": artifact.get("artifact_type"),
            "description": artifact.get("description"),
            "produced_by_experiment_id": artifact.get("produced_by_experiment_id"),
            "linked_ticket_id": artifact.get("linked_ticket_id"),
            "strategy_id": strategy_id,
            "linked_strategy_id": strategy_id,
            "created_at": artifact.get("created_at"),
            "sealed_at": artifact.get("sealed_at"),
            "is_current_version": self._rw05_is_current_version(artifact),
            "version_chain": [
                {
                    "artifact_id": item.get("artifact_id"),
                    "version": item.get("version"),
                    "status": item.get("status"),
                    "produced_by_experiment_id": item.get("produced_by_experiment_id"),
                    "created_at": item.get("created_at"),
                }
                for item in lineage_chain
            ],
            "metrics": json.loads(json.dumps(artifact.get("metrics") or {})),
            "parameters": json.loads(json.dumps(artifact.get("parameters") or {})),
            "provenance": json.loads(json.dumps(artifact.get("provenance") or {})),
            "experiment_refs": self._rw05_experiment_refs(artifact),
            "research_linkage": self._rw05_research_linkage(artifact),
            "allowedActions": {
                "canCompare": self._rw05_can_compare(artifact.get("status")),
                "canViewDetail": True,
            },
        }

    def list_research_artifacts(
        self,
        *,
        experiment_id: Optional[str] = None,
        ticket_id: Optional[str] = None,
        lineage_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        artifacts = list(self._research_artifacts_store().values())
        if experiment_id:
            artifacts = [
                artifact
                for artifact in artifacts
                if str(artifact.get("produced_by_experiment_id") or "") == str(experiment_id)
            ]
        if ticket_id:
            artifacts = [
                artifact
                for artifact in artifacts
                if str(artifact.get("linked_ticket_id") or "") == str(ticket_id)
            ]
        if lineage_id:
            artifacts = [
                artifact
                for artifact in artifacts
                if str(artifact.get("lineage_id") or "") == str(lineage_id)
            ]
        if status:
            artifacts = [
                artifact
                for artifact in artifacts
                if str(artifact.get("status") or "").strip().lower() == str(status).strip().lower()
            ]
        artifacts.sort(
            key=lambda artifact: (
                (_parse_rfc3339(artifact.get("created_at")) or datetime.min).replace(tzinfo=None),
                int(artifact.get("version") or 0),
            ),
            reverse=True,
        )
        return [self._project_research_artifact_summary(artifact) for artifact in artifacts]

    def get_research_artifact(self, artifact_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not artifact_id:
            return None
        available, record = self._service.record("research_artifacts", artifact_id)
        if available:
            return self._project_research_artifact_detail(record) if record else None
        artifact = (self._data.get("research_artifacts") or {}).get(artifact_id)
        if not artifact:
            return None
        return self._project_research_artifact_detail(artifact)

    @staticmethod
    def _rw05_field_value(artifact: Dict[str, Any], field_key: str) -> Any:
        current: Any = artifact
        for part in field_key.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    @staticmethod
    def _rw05_round(value: float) -> float:
        return round(value, 4)

    @classmethod
    def _rw05_delta_display(
        cls,
        field_key: str,
        baseline: Any,
        target: Any,
        delta: float,
    ) -> str:
        if field_key == "metrics.max_drawdown" and baseline not in (None, 0):
            reduction_pct = (abs(float(delta)) / abs(float(baseline))) * 100
            if delta >= 0:
                return f"{delta:+.2f} ({reduction_pct:.1f}% reduction)"
            return f"{delta:+.2f} ({reduction_pct:.1f}% deeper)"

        if isinstance(baseline, (int, float)) and isinstance(target, (int, float)):
            if isinstance(baseline, int) and isinstance(target, int):
                return f"{int(delta):+d}"
            if baseline not in (None, 0):
                relative_pct = (delta / float(baseline)) * 100
                return f"{delta:+.2f} ({relative_pct:+.1f}%)"
            return f"{delta:+.2f}"

        return "changed"

    @classmethod
    def _rw05_compare_field_pair(
        cls,
        artifacts: List[Dict[str, Any]],
        field_key: str,
        display_label: str,
        group: str,
        orientation: str,
    ) -> Dict[str, Any]:
        values = [
            {
                "artifact_id": artifact.get("artifact_id"),
                "value": cls._rw05_field_value(artifact, field_key),
            }
            for artifact in artifacts
        ]
        baseline = values[0]["value"]
        target = values[-1]["value"]

        pair: Dict[str, Any] = {
            "field_key": field_key,
            "display_label": display_label,
            "group": group,
            "values": values,
            "change_label": "unchanged",
            "delta_magnitude": 0,
            "delta_direction": "none",
            "delta_display": "No change",
        }

        if baseline == target:
            return pair

        if isinstance(baseline, (int, float)) and isinstance(target, (int, float)):
            delta = float(target) - float(baseline)
            pair["delta_magnitude"] = cls._rw05_round(abs(delta))
            pair["delta_direction"] = "up" if delta > 0 else "down"
            pair["delta_display"] = cls._rw05_delta_display(field_key, baseline, target, delta)

            if orientation == "neutral":
                pair["change_label"] = "changed"
            elif orientation == "higher_is_better":
                pair["change_label"] = "improved" if delta > 0 else "degraded"
            elif orientation == "lower_is_better":
                pair["change_label"] = "improved" if delta < 0 else "degraded"
            else:
                pair["change_label"] = "changed"
            return pair

        pair["delta_magnitude"] = None
        pair["delta_direction"] = "none"
        pair["delta_display"] = "changed"
        pair["change_label"] = "changed"
        return pair

    def compare_research_artifacts(self, artifact_ids: List[str]) -> Dict[str, Any]:
        artifacts: List[Dict[str, Any]] = []
        for artifact_id in artifact_ids:
            artifact = self.get_research_artifact(artifact_id)
            if artifact:
                artifacts.append(artifact)

        field_pairs = [
            self._rw05_compare_field_pair(artifacts, field_key, display_label, group, orientation)
            for field_key, display_label, group, orientation in self._RW05_FIELD_SPECS
        ]
        changed_count = sum(1 for pair in field_pairs if pair["change_label"] != "unchanged")
        change_labels = [
            pair["change_label"]
            for pair in field_pairs
            if pair["change_label"] in {"improved", "degraded", "changed"}
        ]
        dominant_change_label = "unchanged"
        if change_labels:
            dominant_change_label = max(set(change_labels), key=change_labels.count)

        return {
            "comparison_id": f"cmp_{uuid.uuid4().hex[:12]}",
            "artifacts": [
                {
                    "artifact_id": artifact.get("artifact_id"),
                    "version": artifact.get("version"),
                    "name": artifact.get("name"),
                    "status": artifact.get("status"),
                }
                for artifact in artifacts
            ],
            "field_pairs": field_pairs,
            "change_summary": {
                "total_fields_compared": len(field_pairs),
                "fields_changed": changed_count,
                "fields_unchanged": len(field_pairs) - changed_count,
                "dominant_change_label": dominant_change_label,
            },
            "provenance_pairs": [
                {
                    "artifact_id": artifact.get("artifact_id"),
                    "linked_experiment": json.loads(
                        json.dumps((artifact.get("provenance") or {}).get("linked_experiment") or {})
                    ),
                    "linked_ticket": json.loads(
                        json.dumps((artifact.get("provenance") or {}).get("linked_ticket") or {})
                    ),
                    "experiment_refs": self._rw05_experiment_refs(artifact),
                }
                for artifact in artifacts
            ],
        }

    def _read_dataset_records(
        self,
        dataset: str,
        *,
        include_snapshot_fallback: bool = True,
        include_local_fallback: bool = True,
    ) -> List[Dict[str, Any]]:
        overlay = self._local_overlay_records(dataset)

        def _with_overlay(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            if not overlay:
                return records
            merged: Dict[str, Dict[str, Any]] = {}
            for index, record in enumerate(records):
                if not isinstance(record, dict):
                    continue
                key = _record_key(
                    record,
                    [
                        "id",
                        "memo_id",
                        "entry_id",
                        "note_id",
                        "insight_id",
                        "ticket_id",
                        "session_id",
                        "sessionId",
                        "packId",
                        "handoffId",
                        "packet_id",
                        "trainingExampleId",
                        "example_id",
                        "analysis_id",
                        "artifact_id",
                        "log_id",
                        "conflict_resolution_log_id",
                        "intervention_id",
                        "program_id",
                        "runtime_id",
                        "signal_id",
                    ],
                ) or str(index)
                merged[key] = record
            for key, record in overlay.items():
                if isinstance(record, dict):
                    merged[str(key)] = record
            return list(merged.values())

        if self._consultation_service_dataset_available(dataset):
            service_records = self._consultation_service_records(dataset)
            if service_records is not None:
                return _with_overlay(service_records)
        if dataset in ServiceBackedReadAdapter._DATASETS:
            available, records = self._service.list_records(
                dataset,
                include_snapshot_fallback=include_snapshot_fallback,
            )
            if available:
                return _with_overlay(list(records))
        local_payload = self._local_fallback(dataset) if include_local_fallback else None
        if isinstance(local_payload, dict):
            return _with_overlay([record for record in local_payload.values() if isinstance(record, dict)])
        if isinstance(local_payload, list):
            return _with_overlay([record for record in local_payload if isinstance(record, dict)])
        return _with_overlay([])

    def get_research_search_index(self) -> Optional[Dict[str, Any]]:
        adapter: Optional[Dict[str, Any]] = None
        if "research_search_index" in ServiceBackedReadAdapter._DATASETS:
            available, adapter = self._service.record("research_search_index", "rw02-search-index")
            if available and adapter:
                return json.loads(json.dumps(adapter))

        local_index = self._local_fallback("research_search_index") or {}
        if isinstance(local_index, dict):
            adapter = local_index.get("rw02-search-index")
            if isinstance(adapter, dict):
                return json.loads(json.dumps(adapter))

        documents = self._read_dataset_records("research_search_documents")
        if not documents:
            return None

        indexed_match_types = sorted(
            {
                str(document.get("match_type") or "").strip()
                for document in documents
                if str(document.get("match_type") or "").strip()
            }
        )
        latest_update = max(
            (
                _parse_rfc3339(
                    document.get("updated_at")
                    or document.get("indexed_at")
                    or document.get("created_at")
                )
                for document in documents
            ),
            default=None,
        )
        snapshot_at = (
            latest_update.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            if latest_update is not None
            else _utc_now_rfc3339()
        )
        return {
            "adapter_id": "rw02-search-index",
            "snapshot_at": snapshot_at,
            "adapter_state": "fresh",
            "indexed_match_types": indexed_match_types,
            "source_watermarks": {
                "tickets": None,
                "experiments": None,
                "artifacts": None,
            },
        }

    def get_last_governed_search_refs(self) -> Dict[str, Dict[str, Any]]:
        return json.loads(json.dumps(self._last_governed_search_refs))

    def _rw02_evidence_store_path(self) -> Path:
        return self._path.parent / "source_evidence" / "rw02-evidence.jsonl"

    def _rw02_search_index_store_path(self) -> Path:
        return self._path.parent / "source_evidence" / "rw02-search-index.jsonl"

    def _search_service_url(self) -> Optional[str]:
        return _base_url_from_env(("PANTHEON_SEARCH_API_URL", "PANTHEON_SEARCH_SERVICE_URL"))

    def _source_ingest_service_url(self) -> Optional[str]:
        return _base_url_from_env(
            (
                "PANTHEON_SOURCE_INGEST_API_URL",
                "PANTHEON_SOURCE_INGEST_URL",
                "SOURCE_INGEST_URL",
            )
        )

    def get_source_connector_registry(self) -> Dict[str, Any]:
        base_url = self._source_ingest_service_url()
        if not base_url:
            return {
                "source": "missing",
                "connectors": [],
                "provider_examples": [],
                "financial_data_source_catalog": None,
                "active_universe_policy": None,
            }
        available, payload = _http_json_get(base_url, "/api/source-ingest/registry")
        if not available or not isinstance(payload, dict):
            return {
                "source": "unavailable",
                "connectors": [],
                "provider_examples": [],
                "financial_data_source_catalog": None,
                "active_universe_policy": None,
            }
        connectors = payload.get("connectors") if isinstance(payload.get("connectors"), list) else []
        provider_examples = (
            payload.get("provider_examples") if isinstance(payload.get("provider_examples"), list) else []
        )
        policy_registry = payload.get("policy_registry") if isinstance(payload.get("policy_registry"), dict) else None
        financial_catalog = (
            payload.get("financial_data_source_catalog")
            if isinstance(payload.get("financial_data_source_catalog"), dict)
            else None
        )
        active_universe_policy = (
            payload.get("active_universe_policy")
            if isinstance(payload.get("active_universe_policy"), dict)
            else None
        )
        if active_universe_policy is None and isinstance(financial_catalog, dict):
            nested_policy = financial_catalog.get("active_universe_policy")
            if isinstance(nested_policy, dict):
                active_universe_policy = nested_policy
        return {
            "source": "service_client",
            "schema_version": payload.get("schema_version"),
            "connectors": json.loads(json.dumps(connectors)),
            "provider_examples": json.loads(json.dumps(provider_examples)),
            "policy_registry": json.loads(json.dumps(policy_registry)) if policy_registry else None,
            "financial_data_source_catalog": json.loads(json.dumps(financial_catalog)) if financial_catalog else None,
            "active_universe_policy": json.loads(json.dumps(active_universe_policy)) if active_universe_policy else None,
        }

    def get_source_change_proposals(
        self,
        *,
        status: Optional[str] = None,
        proposal_type: Optional[str] = None,
        source_kind: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read source-change proposals from the source-ingest service.

        BFF is read-only: this method never mutates proposal state.  All
        lifecycle transitions go through operator-gated action endpoints on
        the source-ingest service directly.
        """
        base_url = self._source_ingest_service_url()
        if not base_url:
            return {"source": "missing", "proposals": []}
        path = "/api/source-change-proposals"
        params: list[str] = []
        if status:
            params.append(f"status={status}")
        if proposal_type:
            params.append(f"proposal_type={proposal_type}")
        if source_kind:
            params.append(f"source_kind={source_kind}")
        if params:
            path = path + "?" + "&".join(params)
        available, payload = _http_json_get(base_url, path)
        if not available or not isinstance(payload, dict):
            return {"source": "unavailable", "proposals": []}
        proposals = payload.get("proposals") if isinstance(payload.get("proposals"), list) else []
        return {
            "source": "service_client",
            "proposals": json.loads(json.dumps(proposals)),
        }

    # ---------------------------------------------------------------------- #
    # Source / Search Ops BFF surfaces (SVC-SOURCE-SEARCH-OPS-BFF)
    # ---------------------------------------------------------------------- #

    def get_source_ops_snapshot(
        self,
        *,
        crawl_run_limit: int = 50,
        dlq_status: Optional[str] = None,
        frontier_status: Optional[str] = None,
        audit_limit: int = 20,
    ) -> Dict[str, Any]:
        """Composite source-ingestion operator surface.

        Calls the source-ingest service for connector health, crawl runs,
        DLQ state, frontier, and audit.  The BFF never reads volumes directly.
        """
        base_url = self._source_ingest_service_url()
        if not base_url:
            return {
                "source": "missing",
                "connector_health": [],
                "policy_registry": None,
                "financial_data_source_catalog": None,
                "active_universe_policy": None,
                "crawl_runs": [],
                "dlq": [],
                "frontier": [],
                "audit": [],
                "summary": {
                    "connector_count": 0,
                    "recent_run_count": 0,
                    "dlq_count": 0,
                    "frontier_count": 0,
                    "audit_count": 0,
                    "scheduled_connector_count": 0,
                    "due_connector_count": 0,
                    "degraded_connector_count": 0,
                    "connector_policy_count": 0,
                    "external_allowlist_policy_count": 0,
                    "pit_policy_count": 0,
                    "scheduled_policy_count": 0,
                    "financial_data_source_count": 0,
                    "financial_data_source_template_count": 0,
                    "active_universe_rule_count": 0,
                    "search_refresh_notification_configured": False,
                },
            }

        # connectors / health
        avail_reg, payload_reg = _http_json_get(base_url, "/api/source-ingest/registry")
        connectors: List[Dict[str, Any]] = []
        policy_registry: Optional[Dict[str, Any]] = None
        financial_catalog: Optional[Dict[str, Any]] = None
        active_universe_policy: Optional[Dict[str, Any]] = None
        if avail_reg and isinstance(payload_reg, dict):
            raw = payload_reg.get("connectors")
            if isinstance(raw, list):
                connectors = json.loads(json.dumps(raw))
            raw_policy = payload_reg.get("policy_registry")
            if isinstance(raw_policy, dict):
                policy_registry = json.loads(json.dumps(raw_policy))
            raw_catalog = payload_reg.get("financial_data_source_catalog")
            if isinstance(raw_catalog, dict):
                financial_catalog = json.loads(json.dumps(raw_catalog))
            raw_active_policy = payload_reg.get("active_universe_policy")
            if isinstance(raw_active_policy, dict):
                active_universe_policy = json.loads(json.dumps(raw_active_policy))
            if active_universe_policy is None and isinstance(financial_catalog, dict):
                raw_nested_policy = financial_catalog.get("active_universe_policy")
                if isinstance(raw_nested_policy, dict):
                    active_universe_policy = json.loads(json.dumps(raw_nested_policy))

        # crawl runs
        runs_path = "/api/source-ingest/jobs"
        avail_runs, payload_runs = _http_json_get(base_url, runs_path)
        crawl_runs: List[Dict[str, Any]] = []
        if avail_runs and isinstance(payload_runs, dict):
            raw = payload_runs.get("runs")
            if isinstance(raw, list):
                crawl_runs = json.loads(json.dumps(raw[-crawl_run_limit:]))

        # DLQ
        dlq_path = "/api/source-ingest/dlq"
        if dlq_status:
            dlq_path += f"?status={dlq_status}"
        avail_dlq, payload_dlq = _http_json_get(base_url, dlq_path)
        dlq_entries: List[Dict[str, Any]] = []
        if avail_dlq and isinstance(payload_dlq, dict):
            raw = payload_dlq.get("entries")
            if isinstance(raw, list):
                dlq_entries = json.loads(json.dumps(raw))

        # frontier
        frontier_path = "/api/source-ingest/frontier"
        if frontier_status:
            frontier_path += f"?status={frontier_status}"
        avail_fr, payload_fr = _http_json_get(base_url, frontier_path)
        frontier: List[Dict[str, Any]] = []
        if avail_fr and isinstance(payload_fr, dict):
            raw = payload_fr.get("frontier")
            if isinstance(raw, list):
                frontier = json.loads(json.dumps(raw))

        # audit
        avail_audit, payload_audit = _http_json_get(base_url, "/api/source-ingest/audit")
        audit: List[Dict[str, Any]] = []
        if avail_audit and isinstance(payload_audit, dict):
            raw = payload_audit.get("actions")
            if isinstance(raw, list):
                audit = json.loads(json.dumps(raw[-audit_limit:]))

        service_available = any([avail_reg, avail_runs, avail_dlq, avail_fr, avail_audit])
        scheduled_connector_count = 0
        due_connector_count = 0
        degraded_connector_count = 0
        for connector in connectors:
            schedule = connector.get("schedule") if isinstance(connector.get("schedule"), dict) else {}
            freshness = connector.get("freshness") if isinstance(connector.get("freshness"), dict) else {}
            if schedule.get("enabled") is True:
                scheduled_connector_count += 1
            freshness_status = str(freshness.get("status") or "")
            if freshness.get("is_due") is True or freshness_status in {"due", "never_ingested"}:
                due_connector_count += 1
            connector_status = str(connector.get("status") or "").strip().lower()
            if freshness_status == "degraded" or connector_status in {"disabled", "degraded"}:
                degraded_connector_count += 1
        registry_summary = policy_registry.get("summary", {}) if isinstance(policy_registry, dict) else {}
        default_guards = policy_registry.get("default_guards", {}) if isinstance(policy_registry, dict) else {}
        financial_catalog_summary = (
            financial_catalog.get("summary", {}) if isinstance(financial_catalog, dict) else {}
        )
        active_universe_summary = (
            active_universe_policy.get("summary", {}) if isinstance(active_universe_policy, dict) else {}
        )
        return {
            "source": "service_client" if service_available else "unavailable",
            "connector_health": connectors,
            "policy_registry": policy_registry,
            "financial_data_source_catalog": financial_catalog,
            "active_universe_policy": active_universe_policy,
            "crawl_runs": crawl_runs,
            "dlq": dlq_entries,
            "frontier": frontier,
            "audit": audit,
            "summary": {
                "connector_count": len(connectors),
                "recent_run_count": len(crawl_runs),
                "dlq_count": len(dlq_entries),
                "frontier_count": len(frontier),
                "audit_count": len(audit),
                "scheduled_connector_count": scheduled_connector_count,
                "due_connector_count": due_connector_count,
                "degraded_connector_count": degraded_connector_count,
                "connector_policy_count": int(registry_summary.get("connector_policy_count") or 0),
                "external_allowlist_policy_count": int(registry_summary.get("external_allowlist_policy_count") or 0),
                "pit_policy_count": int(registry_summary.get("pit_policy_count") or 0),
                "scheduled_policy_count": int(registry_summary.get("scheduled_policy_count") or 0),
                "financial_data_source_count": int(financial_catalog_summary.get("data_source_count") or 0),
                "financial_data_source_template_count": int(
                    financial_catalog_summary.get("config_template_count") or 0
                ),
                "active_universe_rule_count": int(active_universe_summary.get("rule_count") or 0),
                "search_refresh_notification_configured": bool(
                    default_guards.get("search_refresh_notification_configured")
                ),
            },
        }

    def get_source_health_usage_snapshot(self) -> Dict[str, Any]:
        """Composite source health, usage, and retirement recommendation surface.

        Calls the source-ingest service for enriched health/usage data.
        The BFF is read-only: it never writes health or usage records.
        """
        base_url = self._source_ingest_service_url()
        if not base_url:
            return {
                "source": "missing",
                "source_count": 0,
                "sources": [],
                "recommendation_summary": {},
            }
        available, payload = _http_json_get(base_url, "/api/source-ingest/health-usage-snapshot")
        if not available or not isinstance(payload, dict):
            return {
                "source": "unavailable",
                "source_count": 0,
                "sources": [],
                "recommendation_summary": {},
            }
        sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
        rec_summary = payload.get("recommendation_summary") if isinstance(payload.get("recommendation_summary"), dict) else {}
        return {
            "source": "service_client",
            "source_count": int(payload.get("source_count") or len(sources)),
            "sources": json.loads(json.dumps(sources)),
            "recommendation_summary": json.loads(json.dumps(rec_summary)),
        }

    def get_search_ops_snapshot(
        self,
        *,
        pipeline_run_limit: int = 50,
    ) -> Dict[str, Any]:
        """Composite search-index operator surface.

        Calls the search service for index freshness and pipeline runs.
        The BFF never reads search volumes directly.
        """
        base_url = self._search_service_url()
        if not base_url:
            return {
                "source": "missing",
                "index_freshness": None,
                "pipeline_runs": [],
                "pipeline_retention_runs": None,
                "materialized_index": None,
                "summary": {
                    "pipeline_run_count": 0,
                    "pipeline_retention_runs": None,
                    "freshness_ok": False,
                    "freshness_status": "unknown",
                },
            }

        # index freshness
        avail_fresh, payload_fresh = _http_json_get(base_url, "/api/search/index/freshness")
        freshness: Optional[Dict[str, Any]] = None
        if avail_fresh and isinstance(payload_fresh, dict):
            freshness = json.loads(json.dumps(payload_fresh))

        # pipeline runs
        runs_path = f"/api/search/index/pipeline-runs?limit={pipeline_run_limit}"
        avail_pipe, payload_pipe = _http_json_get(base_url, runs_path)
        pipeline_runs: List[Dict[str, Any]] = []
        pipeline_total: int = 0
        pipeline_retention_runs: Optional[int] = None
        if avail_pipe and isinstance(payload_pipe, dict):
            raw = payload_pipe.get("runs")
            if isinstance(raw, list):
                pipeline_runs = json.loads(json.dumps(raw))
            pipeline_total = int(payload_pipe.get("total") or len(pipeline_runs))
            if payload_pipe.get("retention_runs") is not None:
                pipeline_retention_runs = int(payload_pipe.get("retention_runs") or 0)

        # materialized index (best-effort; 404 → None)
        avail_mat, payload_mat = _http_json_get(base_url, "/api/search/index/materialize")
        materialized: Optional[Dict[str, Any]] = None
        if avail_mat and isinstance(payload_mat, dict):
            materialized = json.loads(json.dumps(payload_mat))

        service_available = any([avail_fresh, avail_pipe, avail_mat])
        freshness_ok = bool(
            freshness
            and (
                freshness.get("within_sla") is True
                or freshness.get("is_fresh") is True
                or str(freshness.get("status") or "").lower() == "fresh"
            )
        )
        freshness_status = "unknown"
        if freshness:
            freshness_status = "ok" if freshness_ok else "stale"

        return {
            "source": "service_client" if service_available else "unavailable",
            "index_freshness": freshness,
            "pipeline_runs": pipeline_runs,
            "pipeline_run_total": pipeline_total,
            "pipeline_retention_runs": pipeline_retention_runs,
            "materialized_index": materialized,
            "summary": {
                "pipeline_run_count": pipeline_total,
                "pipeline_retention_runs": pipeline_retention_runs,
                "freshness_ok": freshness_ok,
                "freshness_status": freshness_status,
            },
        }

    def _build_research_search_repository(self, documents: List[Dict[str, Any]]):
        from services.knowledge.evidence import (
            EvidenceBundleBuilder,
            EvidenceItem,
            InMemoryEvidenceRepository,
            JsonlEvidenceRepository,
        )
        from services.source_ingestion.connectors import SourceRecord

        repository = JsonlEvidenceRepository(self._rw02_evidence_store_path())
        builder = EvidenceBundleBuilder(repository)
        eligible_result_ids: set[str] = set()
        for document in documents:
            result_id = str(document.get("result_id") or "").strip()
            if not result_id:
                continue
            eligible_result_ids.add(result_id)
            match_type = str(document.get("match_type") or "document").strip().lower()
            links = document.get("links") if isinstance(document.get("links"), dict) else {}
            result_detail = str(
                links.get("result_detail")
                or (
                    f"/research/tickets/{result_id}"
                    if match_type == "ticket"
                    else f"/research/{match_type}s/{result_id}"
                )
            )
            source = SourceRecord(
                source_id=f"src-rw02-{result_id}",
                connector_id="bff-rw02-search-index",
                source_type="internal_note",
                title=str(document.get("title") or result_id),
                content_ref=result_detail,
                metadata={
                    "provider": "pantheon-bff-read-model",
                    "raw_uri": result_detail,
                    "license_scope": "internal",
                    "access_scope": ["operator", "research"],
                    "match_type": match_type,
                },
                trace_id=str(document.get("trace_id") or f"trace-rw02-{result_id}"),
            )
            item = EvidenceItem(
                evidence_item_id=f"evi-rw02-{result_id}",
                source_id=source.source_id,
                item_type="text_chunk",
                content_ref=f"{result_detail}#search-index",
                citation_label=f"{match_type}:{result_id}",
                body=str(document.get("excerpt") or document.get("title") or result_id),
                confidence=float(document.get("confidence") or 1.0),
                access_scope=["operator", "research"],
                trace_refs=[source.trace_id],
                metadata={"linked_ticket_id": document.get("linked_ticket_id")},
            )
            bundle = builder.build_bundle(
                source_records=[source],
                evidence_items=[item],
                summary=str(document.get("excerpt") or document.get("title") or result_id),
                created_by="bff-rw02-search-index",
                evidence_bundle_id=f"evbundle-rw02-{result_id}",
                confidence=float(document.get("confidence") or item.confidence),
                license_scope="internal",
                metadata={
                    "result_id": result_id,
                    "match_type": match_type,
                    "linked_ticket_id": document.get("linked_ticket_id"),
                    "result_detail": result_detail,
                },
            )
            builder.build_knowledge_object(
                knowledge_object_id=result_id,
                source_record=source,
                evidence_item=item,
                evidence_bundle=bundle,
                title=str(document.get("title") or result_id),
                text=str(document.get("excerpt") or document.get("search_text") or document.get("title") or result_id),
                source_type="internal_note",
                keywords=[match_type, str(document.get("linked_ticket_status") or "")],
                metadata={
                    **json.loads(json.dumps(document)),
                    "result_detail": result_detail,
                },
            )

        scoped_repository = InMemoryEvidenceRepository()
        for knowledge_object in repository.list_knowledge_objects():
            if knowledge_object.knowledge_object_id not in eligible_result_ids:
                continue
            source = repository.get_source_record(knowledge_object.source_id)
            evidence_item = repository.get_evidence_item(knowledge_object.evidence_item_id)
            bundle = repository.get_bundle(knowledge_object.evidence_bundle_id)
            if source is None or evidence_item is None or bundle is None:
                continue
            scoped_repository.add_source_record(source)
            scoped_repository.add_evidence_item(evidence_item)
            scoped_repository.add_bundle(bundle)
            scoped_repository.add_knowledge_object(knowledge_object)
        return scoped_repository

    def _rw02_search_service_payload(
        self,
        *,
        query: str,
        match_type: str,
        status: Optional[str],
        date_range: Optional[str],
        eligible_documents: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "request_id": "rw02-bff-search",
            "trace_id": "trace-rw02-bff-search",
            "query": query,
            "persona_id": "operator-workbench",
            "workspace_id": "research-workbench",
            "source_types": ["internal_note"],
            "environment": "paper",
            "top_k": max(len(eligible_documents), 1),
            "require_citations": True,
            "filters_applied": {
                "match_type": match_type,
                "status": status,
                "date_range": date_range,
            },
            "access_context": {
                "persona_id": "operator-workbench",
                "workspace_id": "research-workbench",
                "environment": "paper",
                "access_scopes": ["operator", "research"],
                "license_scopes": ["internal"],
            },
        }

    def _list_research_search_results_from_service(
        self,
        *,
        query: str,
        match_type: str,
        status: Optional[str],
        date_range: Optional[str],
        eligible_documents: List[Dict[str, Any]],
    ) -> Optional[List[Dict[str, Any]]]:
        base_url = self._search_service_url()
        if not base_url:
            return None
        available, payload = _http_json_post(
            base_url,
            "/api/search/query",
            body=self._rw02_search_service_payload(
                query=query,
                match_type=match_type,
                status=status,
                date_range=date_range,
                eligible_documents=eligible_documents,
            ),
        )
        if not available or not isinstance(payload, dict):
            self._last_governed_search_refs = {}
            return []

        documents_by_id = {str(document.get("result_id") or ""): document for document in eligible_documents}
        results = [item for item in payload.get("results") or [] if isinstance(item, dict)]
        self._last_governed_search_refs = {
            str(result.get("result_id") or ""): {
                "evidence_bundle_id": result.get("evidence_bundle_id"),
                "citations": list(result.get("citations") or []),
                "matched_items": list(result.get("matched_items") or []),
            }
            for result in results
            if str(result.get("result_id") or "").strip()
        }

        projected: List[Dict[str, Any]] = []
        for result in results:
            result_id = str(result.get("result_id") or "")
            document = documents_by_id.get(result_id)
            if not document:
                continue
            document_match_type = str(document.get("match_type") or "").strip().lower()
            linked_ticket_id = str(document.get("linked_ticket_id") or "").strip()
            links = document.get("links") if isinstance(document.get("links"), dict) else {}
            projected.append(
                {
                    "result_id": result_id,
                    "match_type": document_match_type,
                    "title": str(document.get("title") or ""),
                    "excerpt": str(document.get("excerpt") or ""),
                    "linked_ticket_id": linked_ticket_id,
                    "relevance_score": float(result.get("relevance_score") or 0.0),
                    "links": {
                        "result_detail": str(
                            links.get("result_detail")
                            or (
                                f"/research/tickets/{result_id}"
                                if document_match_type == "ticket"
                                else f"/research/{document_match_type}s/{result_id}"
                            )
                        ),
                        "linked_ticket_detail": str(
                            links.get("linked_ticket_detail")
                            or f"/research/tickets/{linked_ticket_id}"
                        ),
                    },
                }
            )
        return projected

    def list_research_search_results(
        self,
        *,
        query: str,
        match_type: str = "all",
        status: Optional[str] = None,
        date_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        documents = self._read_dataset_records("research_search_documents")
        if not documents:
            self._last_governed_search_refs = {}
            return []

        ticket_status_by_id = {
            str(ticket.get("ticket_id") or ""): str(ticket.get("status") or "")
            for ticket in self._read_dataset_records("research_tickets")
            if str(ticket.get("ticket_id") or "").strip()
        }
        cutoff_days = self._date_range_cutoff_token(date_range)
        index_adapter = self.get_research_search_index()
        reference_at = (
            _parse_rfc3339((index_adapter or {}).get("snapshot_at"))
            if isinstance(index_adapter, dict)
            else None
        )
        reference_now = (
            reference_at.replace(tzinfo=None)
            if reference_at is not None
            else datetime.now(timezone.utc).replace(tzinfo=None)
        )

        eligible_documents: List[Dict[str, Any]] = []
        for document in documents:
            document_match_type = str(document.get("match_type") or "").strip().lower()
            if document_match_type not in {"ticket", "experiment", "artifact"}:
                continue
            if match_type != "all" and document_match_type != match_type:
                continue

            linked_ticket_id = str(document.get("linked_ticket_id") or "").strip()
            linked_ticket_status = ticket_status_by_id.get(linked_ticket_id) or str(
                document.get("linked_ticket_status") or ""
            ).strip()
            if status and linked_ticket_status.lower() != status.lower():
                continue

            updated_at = _parse_rfc3339(
                document.get("updated_at")
                or document.get("indexed_at")
                or document.get("created_at")
            )
            if cutoff_days is not None:
                if updated_at is None:
                    continue
                if (reference_now - updated_at.replace(tzinfo=None)).days >= cutoff_days:
                    continue
            eligible_documents.append(document)

        service_results = self._list_research_search_results_from_service(
            query=query,
            match_type=match_type,
            status=status,
            date_range=date_range,
            eligible_documents=eligible_documents,
        )
        if service_results is not None:
            return service_results

        from services.search import JsonlSearchIndexStore, SearchAccessContext, SearchGateway, SearchRequest

        repository = self._build_research_search_repository(eligible_documents)
        gateway = SearchGateway(repository, index_store=JsonlSearchIndexStore(self._rw02_search_index_store_path()))
        response = gateway.search(
            SearchRequest(
                request_id="rw02-bff-search",
                query=query,
                persona_id="operator-workbench",
                workspace_id="research-workbench",
                source_types=["internal_note"],
                top_k=max(len(eligible_documents), 1),
                require_citations=True,
                trace_id="trace-rw02-bff-search",
                filters_applied={
                    "match_type": match_type,
                    "status": status,
                    "date_range": date_range,
                },
            ),
            SearchAccessContext(
                persona_id="operator-workbench",
                workspace_id="research-workbench",
                environment="paper",
                access_scopes=["operator", "research"],
                license_scopes=["internal"],
            ),
        )

        documents_by_id = {str(document.get("result_id") or ""): document for document in eligible_documents}
        self._last_governed_search_refs = {
            result.result_id: {
                "evidence_bundle_id": result.evidence_bundle_id,
                "citations": result.citations,
                "matched_items": result.matched_items,
            }
            for result in response.results
        }

        projected: List[Dict[str, Any]] = []
        for result in response.results:
            document = documents_by_id.get(result.result_id)
            if not document:
                continue
            document_match_type = str(document.get("match_type") or "").strip().lower()
            linked_ticket_id = str(document.get("linked_ticket_id") or "").strip()
            links = document.get("links") if isinstance(document.get("links"), dict) else {}
            projected.append(
                {
                    "result_id": str(document.get("result_id") or ""),
                    "match_type": document_match_type,
                    "title": str(document.get("title") or ""),
                    "excerpt": str(document.get("excerpt") or ""),
                    "linked_ticket_id": linked_ticket_id,
                    "relevance_score": result.relevance_score,
                    "links": {
                        "result_detail": str(
                            links.get("result_detail")
                            or (
                                f"/research/tickets/{document.get('result_id')}"
                                if document_match_type == "ticket"
                                else f"/research/{document_match_type}s/{document.get('result_id')}"
                            )
                        ),
                        "linked_ticket_detail": str(
                            links.get("linked_ticket_detail")
                            or f"/research/tickets/{linked_ticket_id}"
                        ),
                    },
                }
            )
        return projected

    # ------------------------------------------------------------------ #
    # Incident surfaces (IN-01 – IN-05)
    # ------------------------------------------------------------------ #

    def list_incidents(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        affected_pool_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        available, incidents = self._service.list_records("incidents")
        if not available:
            incidents = list((self._local_fallback("incidents") or {}).values())
        if status:
            requested_statuses = {
                token.strip().lower()
                for token in status.split(",")
                if token.strip()
            }
            incidents = [
                i for i in incidents
                if str(i.get("status") or "").lower() in requested_statuses
            ]
        if severity:
            incidents = [i for i in incidents if i.get("severity") == severity]
        if affected_pool_id:
            incidents = [i for i in incidents if i.get("capital_pool_id") == affected_pool_id]
        anchor = [
            incident
            for incident in incidents
            if str(incident.get("incident_id") or incident.get("id") or "") == "inc-20260410-001"
        ]
        rest = [
            incident
            for incident in incidents
            if str(incident.get("incident_id") or incident.get("id") or "") != "inc-20260410-001"
        ]
        return anchor + sorted(rest, key=lambda x: x.get("created_at", ""), reverse=True)

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        available, raw = self._service.record("incidents", incident_id)
        if available:
            return raw
        return (self._local_fallback("incidents") or {}).get(incident_id)

    def list_postmortems(self, time_range: Optional[str] = None) -> List[Dict[str, Any]]:
        # time_range deferred — v1 returns all postmortems
        available, postmortems = self._service.list_records("postmortems")
        if available:
            return postmortems
        return list((self._local_fallback("postmortems") or {}).values())

    def get_postmortem(self, report_id: str) -> Optional[Dict[str, Any]]:
        available, raw = self._service.record("postmortems", report_id)
        if available:
            return raw
        return (self._local_fallback("postmortems") or {}).get(report_id)

    def get_postmortem_by_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        available, postmortems = self._service.list_records("postmortems")
        if not available:
            postmortems = list((self._local_fallback("postmortems") or {}).values())
        for pm in postmortems:
            if pm.get("incident_id") == incident_id:
                return pm
        return None

    def list_loop_runs(self) -> tuple[bool, List[Dict[str, Any]]]:
        return self._service.list_loop_runs()

    def trade_journey_projection_reader(self):
        """Return the explicitly selected scoped Postgres reader, if any.

        The disabled default remains the existing JSON reader.  A selected but
        invalid Postgres configuration returns a fail-closed sentinel so route
        handlers cannot quietly reinterpret JSON fallback as Postgres truth.
        """
        return configured_projection_reader()

    def loop_run_projection_metadata(self) -> Dict[str, Any]:
        return self._service.envelope_metadata("loop_runs")

    def get_loop_run(self, loop_run_id: str) -> tuple[bool, Optional[Dict[str, Any]]]:
        return self._service.get_loop_run(loop_run_id)

    def list_loop_health_records(self) -> tuple[bool, List[Dict[str, Any]]]:
        return self._service.list_loop_health_records()

    def get_loop_health_record(self, loop_id: str) -> tuple[bool, Optional[Dict[str, Any]]]:
        return self._service.get_loop_health_record(loop_id)

    def list_sentinel_findings(
        self,
        *,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
    ) -> tuple[bool, List[Dict[str, Any]]]:
        return self._service.list_sentinel_findings(kind=kind, status=status, severity=severity)

    def get_sentinel_finding(self, finding_id: str) -> tuple[bool, Optional[Dict[str, Any]]]:
        return self._service.get_sentinel_finding(finding_id)

    def get_kill_switch_status(self) -> Dict[str, Any]:
        available, raw = self._service.record("kill_switch", "current")
        if available and isinstance(raw, dict):
            ks = json.loads(json.dumps(raw))
        elif available:
            _, records = self._service.list_records("kill_switch")
            ks = json.loads(json.dumps(records[0])) if records else {}
        else:
            ks = self._local_fallback("kill_switch") or {}
        status = str(ks.get("status") or "").lower()
        if status not in {"armed", "triggered", "cooling_down"}:
            safe_mode_status = str(ks.get("safe_mode_status") or "").lower()
            if safe_mode_status in {"cooling_down", "cooldown"}:
                status = "cooling_down"
            elif ks.get("active"):
                status = "triggered"
            else:
                status = "armed"

        active_commands_raw = ks.get("active_commands")
        if active_commands_raw is None:
            active_commands_raw = ks.get("active_freeze_orders", [])
        active_commands: List[str] = []
        for command in active_commands_raw:
            if isinstance(command, str):
                active_commands.append(command)
                continue
            if not isinstance(command, dict):
                continue
            command_id = (
                command.get("command_id")
                or command.get("id")
                or command.get("type")
                or command.get("scope")
            )
            if command_id:
                active_commands.append(str(command_id))

        last_confirmed_at = ks.get("last_confirmed_at") or ks.get("last_checked_at", "")
        return {
            "active": ks.get("active", False),
            "active_freeze_orders": ks.get("active_freeze_orders", []),
            "last_checked_at": ks.get("last_checked_at", ""),
            "safe_mode_status": ks.get("safe_mode_status", "off"),
            "status": status,
            "last_triggered_at": ks.get("last_triggered_at"),
            "last_confirmed_at": last_confirmed_at,
            "active_commands": active_commands,
            "secondary_path_available": ks.get("secondary_path_available", True),
        }

    # ------------------------------------------------------------------ #
    # Composed view helpers
    # ------------------------------------------------------------------ #

    def get_evolution_decision(self, decision_id: str) -> Optional[Dict[str, Any]]:
        available, raw = self._service.record("evolution_decisions", decision_id)
        if available:
            return self._project_service_evolution_decision(raw) if raw else None
        raw = (self._local_fallback("evolution_decisions") or {}).get(decision_id)
        return self._project_service_evolution_decision(raw) if raw else None

    def get_evolution_decisions_by_incident(self, incident_id: str) -> List[Dict[str, Any]]:
        available, raw_decisions = self._service.list_records("evolution_decisions")
        if available:
            decisions = [
                self._project_service_evolution_decision(raw)
                for raw in raw_decisions
            ]
        else:
            decisions = [
                self._project_service_evolution_decision(raw)
                for raw in (self._local_fallback("evolution_decisions") or {}).values()
            ]
        return [
            d for d in decisions
            if d.get("incident_ref") == incident_id
        ]

    def get_rollbacks_by_incident(self, incident_id: str) -> List[Dict[str, Any]]:
        return list((self._local_fallback("rollbacks_by_incident") or {}).get(incident_id, []))

    def get_telemetry_summary(self, runtime_id: str) -> Optional[Dict[str, Any]]:
        available, raw = self._service.record("telemetry_summaries", runtime_id)
        if available:
            return raw
        return (self._local_fallback("telemetry_summaries") or {}).get(runtime_id)

    def list_telemetry_summaries(self) -> List[Dict[str, Any]]:
        available, raw = self._service.list_records("telemetry_summaries")
        if available:
            return [json.loads(json.dumps(summary)) for summary in raw]
        fallback = self._local_fallback("telemetry_summaries") or {}
        if isinstance(fallback, dict):
            return [json.loads(json.dumps(summary)) for summary in fallback.values()]
        if isinstance(fallback, list):
            return [json.loads(json.dumps(summary)) for summary in fallback]
        return []

    # ------------------------------------------------------------------ #
    # Evolution surfaces (EV-01 – EV-04)
    # ------------------------------------------------------------------ #

    def list_evolution_decisions(
        self,
        action_type: Optional[str] = None,
        risk_level: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        available, raw_decisions = self._service.list_records("evolution_decisions")
        if available:
            decisions = [
                self._project_service_evolution_decision(raw)
                for raw in raw_decisions
            ]
        else:
            decisions = [
                self._project_service_evolution_decision(raw)
                for raw in (self._local_fallback("evolution_decisions") or {}).values()
            ]
        if action_type:
            decisions = [d for d in decisions if d.get("action_type") == action_type]
        if risk_level:
            decisions = [d for d in decisions if d.get("risk_level") == risk_level]
        if status:
            decisions = [d for d in decisions if d.get("status") == status]
        return sorted(decisions, key=lambda x: x.get("created_at", ""), reverse=True)

    def get_evolution_decision_by_id(self, decision_id: str) -> Optional[Dict[str, Any]]:
        available, raw = self._service.record("evolution_decisions", decision_id)
        if available:
            return self._project_service_evolution_decision(raw) if raw else None
        raw = (self._local_fallback("evolution_decisions") or {}).get(decision_id)
        return self._project_service_evolution_decision(raw) if raw else None

    def list_freeze_orders(
        self,
        status: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        available, service_orders = self._service.list_records("freeze_orders")
        if available:
            orders = list(service_orders)
        else:
            local_orders = self._local_fallback("freeze_orders") or {}
            orders = list(local_orders.values()) if isinstance(local_orders, dict) else list(local_orders)
        if status:
            orders = [o for o in orders if o.get("status") == status]
        if scope:
            orders = [o for o in orders if o.get("scope") == scope]
        return sorted(
            orders,
            key=lambda x: str(x.get("created_at") or x.get("issued_at") or x.get("updated_at") or ""),
            reverse=True,
        )

    def list_all_rollbacks(
        self,
        runtime_id: Optional[str] = None,
        action_type: Optional[str] = None,
        time_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        available, service_rollbacks = self._service.list_records("all_rollbacks")
        if available:
            rollbacks = list(service_rollbacks)
        else:
            local_rollbacks = self._local_fallback("all_rollbacks") or []
            rollbacks = list(local_rollbacks.values()) if isinstance(local_rollbacks, dict) else list(local_rollbacks)
        if runtime_id:
            rollbacks = [r for r in rollbacks if r.get("runtime_id") == runtime_id]
        if action_type:
            rollbacks = [r for r in rollbacks if r.get("action_type") == action_type]
        # time_range filtering deferred in v1
        return sorted(
            rollbacks,
            key=lambda x: str(
                x.get("initiated_at") or x.get("requested_at") or x.get("created_at") or x.get("updated_at") or ""
            ),
            reverse=True,
        )

    def get_rollback_review(self, rollback_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not rollback_id:
            return None
        review = (self._local_fallback("rollback_reviews") or {}).get(rollback_id)
        if review:
            return json.loads(json.dumps(review))
        return None

    def list_governance_audit_events(
        self,
        *,
        actor: Optional[str] = None,
        action_types: Optional[List[str]] = None,
        target_type: Optional[str] = None,
        from_ts: Optional[datetime] = None,
        to_ts: Optional[datetime] = None,
        include_fixture_pack: bool = True,
    ) -> List[Dict[str, Any]]:
        events = list(self._local_fallback("governance_audit_events") or [])
        if not include_fixture_pack:
            events = [event for event in events if not _is_fixture_pack_record(event)]

        if actor:
            events = [event for event in events if event.get("actor") == actor]
        if action_types:
            allowed = {value for value in action_types if value}
            events = [event for event in events if event.get("action_type") in allowed]
        if target_type:
            events = [event for event in events if event.get("target_type") == target_type]

        if from_ts is not None:
            events = [
                event
                for event in events
                if (
                    _parse_rfc3339(event.get("timestamp")) is not None
                    and _parse_rfc3339(event.get("timestamp")) >= from_ts
                )
            ]
        if to_ts is not None:
            events = [
                event
                for event in events
                if (
                    _parse_rfc3339(event.get("timestamp")) is not None
                    and _parse_rfc3339(event.get("timestamp")) <= to_ts
                )
            ]

        events.sort(key=lambda event: event.get("timestamp", ""), reverse=True)
        return json.loads(json.dumps(events))

    # ------------------------------------------------------------------ #
    # Lineage surfaces (LN-01 – LN-03)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _lineage_edge_sort_key(edge: Dict[str, Any]) -> tuple[str, str]:
        return (
            str(edge.get("created_at") or ""),
            str(edge.get("id") or ""),
        )

    def _artifact_metadata_index(self) -> Dict[str, Dict[str, str]]:
        index: Dict[str, Dict[str, str]] = {}

        def merge(
            artifact_id: Any,
            *,
            artifact_version: Any = None,
            artifact_type: Any = None,
        ) -> None:
            key = str(artifact_id or "").strip()
            if not key:
                return
            entry = index.setdefault(
                key,
                {
                    "artifact_id": key,
                    "artifact_version": "",
                    "artifact_type": "",
                },
            )
            if artifact_version not in (None, "") and not entry["artifact_version"]:
                entry["artifact_version"] = str(artifact_version)
            if artifact_type not in (None, "") and not entry["artifact_type"]:
                entry["artifact_type"] = str(artifact_type)

        for entry in self.list_registry_entries():
            merge(
                entry.get("artifact_id") or entry.get("registry_id") or entry.get("id"),
                artifact_version=entry.get("artifact_version") or entry.get("version"),
                artifact_type=entry.get("artifact_type"),
            )

        for plan in self.list_deployment_plans():
            merge(
                plan.get("artifact_id"),
                artifact_version=plan.get("artifact_version"),
                artifact_type=plan.get("artifact_type"),
            )

        for binding in self.list_runtime_bindings():
            merge(
                binding.get("artifact_id"),
                artifact_version=binding.get("artifact_version"),
                artifact_type=binding.get("artifact_type"),
            )

        for incident in self.list_incidents():
            merge(
                incident.get("artifact_id"),
                artifact_version=incident.get("artifact_version"),
                artifact_type=incident.get("artifact_type"),
            )

        available, raw_postmortems = self._service.list_records("postmortems")
        postmortems = raw_postmortems if available else list((self._local_fallback("postmortems") or {}).values())
        for postmortem in postmortems:
            merge(
                postmortem.get("artifact_id"),
                artifact_version=postmortem.get("artifact_version"),
                artifact_type=postmortem.get("artifact_type"),
            )

        available, edges = self._service.list_records("lineage_edges")
        if not available:
            edges = list((self._local_fallback("lineage_edges") or {}).values())
        for edge in edges:
            merge(
                edge.get("from_artifact_id"),
                artifact_version=edge.get("from_artifact_version"),
                artifact_type=edge.get("from_artifact_type"),
            )
            merge(
                edge.get("to_artifact_id"),
                artifact_version=edge.get("to_artifact_version"),
                artifact_type=edge.get("to_artifact_type"),
            )

        return index

    def list_lineage_edges(
        self,
        artifact_id: Optional[str] = None,
        include_fixture_pack: bool = True,
    ) -> List[Dict[str, Any]]:
        available, edges = self._service.list_records("lineage_edges")
        if not available:
            edges = list((self._local_fallback("lineage_edges") or {}).values())
        if not include_fixture_pack:
            edges = [edge for edge in edges if not _is_fixture_pack_record(edge)]
        if artifact_id:
            edges = [
                e for e in edges
                if e.get("from_artifact_id") == artifact_id or e.get("to_artifact_id") == artifact_id
            ]
        return sorted(edges, key=self._lineage_edge_sort_key, reverse=True)

    def list_lineage_records(
        self,
        artifact_id: Optional[str] = None,
        include_fixture_pack: bool = True,
    ) -> List[Dict[str, Any]]:
        edges = self.list_lineage_edges(include_fixture_pack=include_fixture_pack)
        if artifact_id:
            artifact_edges = [
                edge
                for edge in edges
                if edge.get("from_artifact_id") == artifact_id or edge.get("to_artifact_id") == artifact_id
            ]
            if not artifact_edges:
                return []
            last_edge_at = max(
                (str(edge.get("created_at") or "") for edge in artifact_edges),
                default="",
            )
            return [{
                "artifact_id": artifact_id,
                "edge_count": len(artifact_edges),
                "last_edge_at": last_edge_at,
            }]

        aggregates: Dict[str, Dict[str, Any]] = {}
        for edge in edges:
            for key in {edge.get("from_artifact_id"), edge.get("to_artifact_id")}:
                artifact_key = str(key or "").strip()
                if not artifact_key:
                    continue
                aggregate = aggregates.setdefault(
                    artifact_key,
                    {
                        "artifact_id": artifact_key,
                        "edge_count": 0,
                        "last_edge_at": "",
                    },
                )
                aggregate["edge_count"] += 1
                created_at = str(edge.get("created_at") or "")
                if created_at > str(aggregate.get("last_edge_at") or ""):
                    aggregate["last_edge_at"] = created_at

        items = sorted(aggregates.values(), key=lambda item: str(item.get("artifact_id") or ""))
        items.sort(key=lambda item: str(item.get("last_edge_at") or ""), reverse=True)
        return items

    def get_lineage_edge(self, edge_id: str) -> Optional[Dict[str, Any]]:
        available, raw = self._service.record("lineage_edges", edge_id)
        if available:
            return raw
        return (self._local_fallback("lineage_edges") or {}).get(edge_id)

    @staticmethod
    def _project_inspiration_graph(raw: Dict[str, Any]) -> Dict[str, Any]:
        meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        surfaces = meta.get("surfaces") if isinstance(meta.get("surfaces"), dict) else {}

        edges: List[Dict[str, Any]] = []
        for edge in raw.get("inspiration_edges") or []:
            if not isinstance(edge, dict):
                continue
            source_artifact_id = str(edge.get("source_artifact_id") or "").strip()
            relationship_type = str(edge.get("relationship_type") or "").strip()
            if not source_artifact_id or not relationship_type:
                continue
            try:
                influence_weight = float(edge.get("influence_weight") or 0.0)
            except (TypeError, ValueError):
                influence_weight = 0.0
            edges.append(
                {
                    "source_artifact_id": source_artifact_id,
                    "relationship_type": relationship_type,
                    "influence_weight": round(min(max(influence_weight, 0.0), 1.0), 3),
                }
            )

        strategy_tags = [
            str(tag).strip()
            for tag in raw.get("strategy_tags") or []
            if str(tag).strip()
        ]

        projection: Dict[str, Any] = {
            "artifact_id": str(raw.get("artifact_id") or raw.get("id") or "").strip(),
            "inspiration_edges": edges,
            "strategy_tags": strategy_tags,
            "meta": {
                "snapshot_at": str(
                    raw.get("snapshot_at")
                    or meta.get("snapshot_at")
                    or _utc_now_rfc3339()
                ),
                "surfaces": {
                    "inspiration": str(
                        surfaces.get("inspiration")
                        or raw.get("surface_state")
                        or "fresh"
                    ),
                },
            },
        }

        page_info = raw.get("page_info") if isinstance(raw.get("page_info"), dict) else {}
        next_page_token = raw.get("next_page_token")
        if next_page_token is None:
            next_page_token = page_info.get("next_page_token")
        if next_page_token not in (None, ""):
            projection["page_info"] = {"next_page_token": str(next_page_token)}

        return projection

    def get_lineage_graph(
        self,
        root_type: Optional[str] = None,
        root_id: Optional[str] = None,
        depth: int = 3,
    ) -> List[Dict[str, Any]]:
        """Return lineage edges reachable from a root artifact within depth hops.

        v1: returns all edges that touch the root_id directly (depth=1 semantics).
        Production implementation would traverse the graph iteratively up to depth.
        """
        available, edges = self._service.list_records("lineage_edges")
        if not available:
            edges = list((self._local_fallback("lineage_edges") or {}).values())
        if root_id:
            edges = [
                e for e in edges
                if e.get("from_artifact_id") == root_id or e.get("to_artifact_id") == root_id
            ]
        if root_type:
            # v1: root_type filtering is a no-op since edges don't carry type metadata
            # Production would filter by artifact type via registry lookup
            pass
        return sorted(edges, key=self._lineage_edge_sort_key, reverse=True)

    def get_lineage_graph_nodes(
        self,
        edges: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        metadata_index = self._artifact_metadata_index()
        artifact_ids = sorted({
            str(edge.get("from_artifact_id") or "")
            for edge in edges
        } | {
            str(edge.get("to_artifact_id") or "")
            for edge in edges
        })
        artifact_ids = [artifact_id for artifact_id in artifact_ids if artifact_id]

        nodes: List[Dict[str, str]] = []
        for artifact_id in artifact_ids:
            metadata = metadata_index.get(
                artifact_id,
                {
                    "artifact_id": artifact_id,
                    "artifact_version": "",
                    "artifact_type": "",
                },
            )
            nodes.append(
                {
                    "artifact_id": artifact_id,
                    "artifact_version": str(metadata.get("artifact_version") or ""),
                    "artifact_type": str(metadata.get("artifact_type") or ""),
                }
            )
        return nodes

    def artifact_exists(self, artifact_id: str) -> bool:
        artifact_key = str(artifact_id or "").strip()
        if not artifact_key:
            return False
        return artifact_key in self._artifact_metadata_index()

    def get_inspiration_graph(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        available, raw = self._service.record("inspiration_graphs", artifact_id)
        if available:
            return self._project_inspiration_graph(raw) if raw else None
        raw = (self._local_fallback("inspiration_graphs") or {}).get(artifact_id)
        return self._project_inspiration_graph(raw) if raw else None

    # ------------------------------------------------------------------ #
    # Telemetry surfaces (TL-01 – TL-03)
    # ------------------------------------------------------------------ #

    def list_telemetry_events(
        self,
        pool_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        time_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """TL-01: Return telemetry events with optional filters."""
        _, events = self.list_telemetry_events_with_source(
            pool_id=pool_id,
            artifact_id=artifact_id,
            time_range=time_range,
        )
        return events

    def list_telemetry_events_with_source(
        self,
        pool_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        time_range: Optional[str] = None,
    ) -> tuple[str, List[Dict[str, Any]]]:
        """Return telemetry events and the read source used for TL-01.

        The event store is authoritative when it has records. The legacy summary
        projection is retained only as an explicitly degraded empty-store fallback.
        """
        available, raw_events = self._service.list_records("telemetry_events")
        source = self._service.source("telemetry_events") if available else "missing"
        event_records = [
            self._project_telemetry_event(event)
            for event in raw_events
            if isinstance(event, dict)
        ]
        if event_records:
            return source, self._filter_telemetry_events(
                event_records,
                pool_id=pool_id,
                artifact_id=artifact_id,
                time_range=time_range,
            )

        fallback_events = self._telemetry_summary_projection_events()
        if fallback_events:
            return "telemetry_summary_fallback", self._filter_telemetry_events(
                fallback_events,
                pool_id=pool_id,
                artifact_id=artifact_id,
                time_range=time_range,
            )
        return "missing", []

    @staticmethod
    def _project_telemetry_event(event: Dict[str, Any]) -> Dict[str, Any]:
        projected = json.loads(json.dumps(event))
        event_id = (
            projected.get("id")
            or projected.get("event_id")
            or projected.get("telemetry_event_id")
        )
        if event_id not in (None, ""):
            projected.setdefault("id", str(event_id))
        runtime_id = (
            projected.get("runtime_id")
            or projected.get("runtimeBindingId")
            or projected.get("runtime_binding_id")
        )
        if runtime_id not in (None, ""):
            projected.setdefault("runtime_id", str(runtime_id))
        event_type = (
            projected.get("type")
            or projected.get("event_type")
            or projected.get("kind")
            or "telemetry"
        )
        projected.setdefault("type", str(event_type))
        timestamp = ReadSurfaceStore._telemetry_event_timestamp(projected)
        if timestamp:
            projected.setdefault("timestamp", timestamp)
        return projected

    @staticmethod
    def _telemetry_event_timestamp(event: Dict[str, Any]) -> str:
        for key in (
            "timestamp",
            "occurred_at",
            "emitted_at",
            "created_at",
            "collected_at",
        ):
            value = event.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    @staticmethod
    def _filter_telemetry_events(
        events: List[Dict[str, Any]],
        *,
        pool_id: Optional[str],
        artifact_id: Optional[str],
        time_range: Optional[str],
    ) -> List[Dict[str, Any]]:
        filtered = list(events)
        if artifact_id:
            filtered = [
                event
                for event in filtered
                if event.get("artifact_id") == artifact_id
                or event.get("runtime_id") == artifact_id
            ]
        if pool_id:
            filtered = [event for event in filtered if event.get("pool_id") == pool_id]
        # time_range filtering remains deferred to the telemetry service.
        return sorted(
            filtered,
            key=ReadSurfaceStore._telemetry_event_timestamp,
            reverse=True,
        )

    def _telemetry_summary_projection_events(self) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        available, summaries = self._service.list_records("telemetry_summaries")
        if available:
            summary_records = [
                (summary.get("runtime_id") or summary.get("id"), summary)
                for summary in summaries
            ]
        else:
            summary_records = list((self._local_fallback("telemetry_summaries") or {}).items())

        for runtime_id, summary in summary_records:
            if not runtime_id:
                continue
            event = {
                "id": f"tl-evt-{runtime_id}",
                "runtime_id": runtime_id,
                "type": "telemetry_snapshot",
                "timestamp": summary.get("collected_at", ""),
                "metrics": {
                    "pnl": summary.get("pnl"),
                    "drawdown": summary.get("drawdown"),
                    "sharpe_ratio": summary.get("sharpe_ratio"),
                    "total_trades": summary.get("total_trades"),
                    "fill_rate": summary.get("fill_rate"),
                    "avg_slippage_bps": summary.get("avg_slippage_bps"),
                },
            }
            events.append(event)
        return events

    # ------------------------------------------------------------------ #
    # Telemetry performance (TL-03)
    # ------------------------------------------------------------------ #

    def get_telemetry_performance(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        available, raw = self._service.record("telemetry_performance", artifact_id)
        if available:
            return raw
        return (self._local_fallback("telemetry_performance") or {}).get(artifact_id)

    def get_paper_live_drift_report(self, runtime_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not runtime_id:
            return None
        available, raw = self._service.record("paper_live_drift_reports", runtime_id)
        if available:
            return json.loads(json.dumps(raw)) if raw else None
        raw = (self._local_fallback("paper_live_drift_reports") or {}).get(runtime_id)
        return json.loads(json.dumps(raw)) if raw else None

    def list_paper_live_drift_reports(self) -> List[Dict[str, Any]]:
        available, raw = self._service.list_records("paper_live_drift_reports")
        if available:
            return [json.loads(json.dumps(report)) for report in raw]
        fallback = self._local_fallback("paper_live_drift_reports") or {}
        if isinstance(fallback, dict):
            return [json.loads(json.dumps(report)) for report in fallback.values()]
        if isinstance(fallback, list):
            return [json.loads(json.dumps(report)) for report in fallback]
        return []

    # ------------------------------------------------------------------ #
    # Persona session surfaces (PS-03, PS-05)
    # ------------------------------------------------------------------ #

    def get_sessions_for_persona(self, persona_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        """PS-03: Return active sessions for a persona.

        Returns None when the persona cannot be verified (degraded mode).
        """
        if not persona_id:
            return None
        if self.get_persona(persona_id) is None:
            return None
        available, raw_sessions = self._service.list_records("sessions")
        if available:
            return [
                self._project_service_session(session)
                for session in raw_sessions
                if session.get("persona_id") == persona_id
            ]
        return [
            s for s in (self._local_fallback("sessions") or {}).values()
            if s.get("persona_id") == persona_id
        ]

    def list_sessions_for_persona(
        self,
        persona_id: Optional[str],
        status: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        sessions = self.get_sessions_for_persona(persona_id)
        if sessions is None:
            return None
        if status:
            sessions = [s for s in sessions if s.get("status") == status]
        return sessions

    def get_session(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        available, raw = self._service.record("sessions", session_id)
        if available:
            return self._project_service_session(raw) if raw else None
        return (self._local_fallback("sessions") or {}).get(session_id)

    def get_teaching_sessions_for_persona(self, persona_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        """PS-05: Return teaching sessions for a persona.

        Returns None when the persona cannot be verified (degraded mode).
        """
        if not persona_id:
            return None
        if self.get_persona(persona_id) is None:
            return None
        available, raw_sessions = self._service.list_records("teaching_sessions")
        if available:
            return [
                session
                for session in raw_sessions
                if session.get("persona_id") == persona_id
            ]
        return [
            s for s in (self._local_fallback("teaching_sessions") or {}).values()
            if s.get("persona_id") == persona_id
        ]

    def list_teaching_sessions_for_persona(
        self,
        persona_id: Optional[str],
        status: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        sessions = self.get_teaching_sessions_for_persona(persona_id)
        if sessions is None:
            return None
        if status:
            sessions = [s for s in sessions if s.get("status") == status]
        return sessions

    @staticmethod
    def _trainer_session_allowed_actions(status: Optional[str]) -> Dict[str, bool]:
        return {
            "canSendMessage": str(status or "").strip().lower() == "active",
        }

    def _trainer_actor_context(self, session: Dict[str, Any]) -> Dict[str, Optional[str]]:
        explicit = session.get("actor_context")
        if isinstance(explicit, dict):
            return {
                "persona_display_name": explicit.get("persona_display_name"),
                "persona_role_context": explicit.get("persona_role_context"),
            }

        persona = self.get_persona(session.get("persona_id"))
        if persona:
            return {
                "persona_display_name": persona.get("name"),
                "persona_role_context": persona.get("mandate") or persona.get("strategy_family"),
            }
        return {
            "persona_display_name": None,
            "persona_role_context": None,
        }

    @staticmethod
    def _project_teaching_event(session_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "event_id": event.get("event_id"),
            "session_id": session_id,
            "actor": event.get("actor"),
            "message_body": event.get("message_body"),
            "emitted_at": event.get("emitted_at"),
            "sequence_number": event.get("sequence_number"),
            "outcome_signal": event.get("outcome_signal"),
        }

    def _project_trainer_session_summary(self, session: Dict[str, Any]) -> Dict[str, Any]:
        session_id = str(session.get("session_id") or session.get("id") or "")
        events = sorted(
            [
                self._project_teaching_event(session_id, event)
                for event in (session.get("events") or [])
                if isinstance(event, dict)
            ],
            key=lambda item: int(item.get("sequence_number") or 0),
        )
        latest_outcome_signal = None
        for event in events:
            if event.get("outcome_signal") not in (None, ""):
                latest_outcome_signal = event.get("outcome_signal")
        last_event_at = events[-1].get("emitted_at") if events else None
        return {
            "message_count": len(events),
            "last_event_at": last_event_at,
            "latest_outcome_signal": latest_outcome_signal,
        }

    def _project_trainer_session_list_item(self, session: Dict[str, Any]) -> Dict[str, Any]:
        session_id = str(session.get("session_id") or session.get("id") or "")
        summary = self._project_trainer_session_summary(session)
        status = session.get("status")
        return {
            "session_id": session_id,
            "persona_id": session.get("persona_id"),
            "session_type": session.get("session_type") or "trainer",
            "objective": session.get("objective") or session.get("topic"),
            "status": status,
            "started_at": session.get("started_at"),
            "ended_at": session.get("ended_at") or session.get("completed_at"),
            "message_count": summary["message_count"],
            "last_event_at": summary["last_event_at"],
            "latest_outcome_signal": summary["latest_outcome_signal"],
            "actor_context": self._trainer_actor_context(session),
            "allowedActions": self._trainer_session_allowed_actions(status),
            "links": {
                "workbench_detail": f"/trainer/sessions/{session_id}",
            },
        }

    def _project_trainer_session_detail(self, session: Dict[str, Any]) -> Dict[str, Any]:
        session_id = str(session.get("session_id") or session.get("id") or "")
        events = sorted(
            [
                self._project_teaching_event(session_id, event)
                for event in (session.get("events") or [])
                if isinstance(event, dict)
            ],
            key=lambda item: int(item.get("sequence_number") or 0),
        )
        status = session.get("status")
        return {
            "session_id": session_id,
            "persona_id": session.get("persona_id"),
            "session_type": session.get("session_type") or "trainer",
            "objective": session.get("objective") or session.get("topic"),
            "status": status,
            "started_at": session.get("started_at"),
            "ended_at": session.get("ended_at") or session.get("completed_at"),
            "opened_by": session.get("opened_by") or session.get("operator_id"),
            "context_refs": json.loads(json.dumps(session.get("context_refs") or [])),
            "actor_context": self._trainer_actor_context(session),
            "session_summary": self._project_trainer_session_summary(session),
            "events": events,
            "allowedActions": self._trainer_session_allowed_actions(status),
            "links": {
                "self": f"/api/v1/trainer/sessions/{session_id}",
                "workbench_detail": f"/trainer/sessions/{session_id}",
            },
        }

    def list_trainer_sessions(
        self,
        *,
        persona_id: Optional[str],
        status: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        sessions = self.get_teaching_sessions_for_persona(persona_id)
        if sessions is None:
            return None
        items = [
            self._project_trainer_session_list_item(session)
            for session in sessions
            if str(session.get("session_type") or session.get("mode") or "").strip().lower() == "trainer"
        ]
        if status:
            normalized = str(status).strip().lower()
            items = [item for item in items if str(item.get("status") or "").strip().lower() == normalized]
        items.sort(
            key=lambda item: (
                (_parse_rfc3339(item.get("last_event_at"))
                or _parse_rfc3339(item.get("started_at"))
                or datetime.min).replace(tzinfo=None)
            ),
            reverse=True,
        )
        return items

    def get_trainer_session(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        available, raw = self._service.record("teaching_sessions", session_id)
        if available:
            if raw is None:
                return None
            normalized = dict(raw)
        else:
            teaching_sessions = self._local_fallback("teaching_sessions") or {}
            normalized = teaching_sessions.get(session_id)
            if normalized is None:
                return None
        session_type = str(normalized.get("session_type") or normalized.get("mode") or "").strip().lower()
        if session_type != "trainer":
            return None
        return self._project_trainer_session_detail(normalized)

    def create_trainer_session(
        self,
        *,
        persona_id: str,
        objective: str,
        context_refs: List[Dict[str, Any]],
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        base_url = self._training_session_base_url()
        if base_url:
            available, payload = _http_json_post(
                base_url,
                "/api/training/sessions",
                body={
                    "persona_id": persona_id,
                    "objective": objective,
                    "context_refs": context_refs,
                    "actor_id": actor_id,
                    "created_at": created_at,
                },
            )
            if not available or not isinstance(payload, dict):
                return None
            return self._project_trainer_session_detail(payload)

        service_store_path = self._service._resolve_path("teaching_sessions")
        persist_service_store = service_store_path is not None
        teaching_sessions: Optional[Dict[str, Dict[str, Any]]]
        if persist_service_store:
            available, service_sessions = self._service.list_records("teaching_sessions")
            if not available and service_store_path.exists():
                return None
            teaching_sessions = {
                str(session.get("session_id") or session.get("id") or ""): json.loads(
                    json.dumps(session)
                )
                for session in service_sessions
                if isinstance(session, dict)
                and str(session.get("session_id") or session.get("id") or "").strip()
            }
        else:
            teaching_sessions = self._local_fallback("teaching_sessions")
        if teaching_sessions is None:
            return None

        timestamp = created_at or _utc_now_rfc3339()
        prefix = timestamp[:10].replace("-", "")
        next_index = len(teaching_sessions) + 1
        session_id = f"trn-{prefix}-{next_index:03d}"
        while session_id in teaching_sessions:
            next_index += 1
            session_id = f"trn-{prefix}-{next_index:03d}"

        persona = self.get_persona(persona_id)
        session = {
            "id": session_id,
            "session_id": session_id,
            "persona_id": persona_id,
            "session_type": "trainer",
            "objective": objective,
            "status": "active",
            "started_at": timestamp,
            "ended_at": None,
            "opened_by": actor_id,
            "context_refs": json.loads(json.dumps(context_refs)),
            "actor_context": {
                "persona_display_name": (persona or {}).get("name"),
                "persona_role_context": (persona or {}).get("mandate") or (persona or {}).get("strategy_family"),
            },
            "events": [],
            "topic": objective,
            "outcomes": [],
        }
        teaching_sessions[session_id] = session
        if persist_service_store:
            self._service.write_records("teaching_sessions", teaching_sessions)
        else:
            self._save()
        return self._project_trainer_session_detail(session)

    def append_trainer_message(
        self,
        session_id: str,
        *,
        message_body: str,
        actor_id: str,
        accepted_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        base_url = self._training_session_base_url()
        if base_url:
            available, payload = _http_json_post(
                base_url,
                f"/api/training/sessions/{session_id}/events",
                body={
                    "actor": "operator",
                    "event_type": "message",
                    "message_body": message_body,
                    "emitted_at": accepted_at,
                },
            )
            if not available or not isinstance(payload, dict) or not isinstance(payload.get("session"), dict):
                return None
            projected = self._project_trainer_session_detail(payload["session"])
            event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
            return {
                "accepted_at": payload.get("accepted_at") or accepted_at or _utc_now_rfc3339(),
                "event": self._project_teaching_event(session_id, event),
                "session": projected,
            }

        service_store_path = self._service._resolve_path("teaching_sessions")
        persist_service_store = service_store_path is not None
        teaching_sessions: Optional[Dict[str, Dict[str, Any]]]
        if persist_service_store:
            available, service_sessions = self._service.list_records("teaching_sessions")
            if not available and service_store_path.exists():
                return None
            teaching_sessions = {
                str(session.get("session_id") or session.get("id") or ""): json.loads(
                    json.dumps(session)
                )
                for session in service_sessions
                if isinstance(session, dict)
                and str(session.get("session_id") or session.get("id") or "").strip()
            }
        else:
            teaching_sessions = self._local_fallback("teaching_sessions")
        if teaching_sessions is None:
            return None

        session = teaching_sessions.get(session_id)
        if session is None:
            return None

        timestamp = accepted_at or _utc_now_rfc3339()
        events = session.setdefault("events", [])
        next_sequence = max((int(event.get("sequence_number") or 0) for event in events), default=0) + 1
        prefix = timestamp[:10].replace("-", "")
        event_id = f"tevt-{prefix}-{next_sequence:03d}"
        existing_ids = {str(event.get("event_id") or "") for event in events if isinstance(event, dict)}
        dedupe_index = next_sequence
        while event_id in existing_ids:
            dedupe_index += 1
            event_id = f"tevt-{prefix}-{dedupe_index:03d}"

        event = {
            "event_id": event_id,
            "session_id": session_id,
            "actor": "operator",
            "message_body": message_body,
            "emitted_at": timestamp,
            "sequence_number": next_sequence,
            "outcome_signal": None,
        }
        events.append(event)
        if persist_service_store:
            self._service.write_records("teaching_sessions", teaching_sessions)
        else:
            self._save()

        projected = self._project_trainer_session_detail(session)
        return {
            "accepted_at": timestamp,
            "event": self._project_teaching_event(session_id, event),
            "session": projected,
        }

    _TW03_WARNING_LEVELS = ("critical", "high", "medium", "informational")
    _TW03_SURFACE_STATES = {"ok", "stale", "degraded", "unavailable"}
    _TW03_REFRESHABLE_SESSION_STATUSES = {"active", "paused"}
    _TW03_POLL_INTERVAL_MS = 3000
    _TW03_MAX_WAIT_MS = 45000

    def _trainer_preview_records(self) -> Dict[str, Dict[str, Any]]:
        available, records = self._service.list_records("trainer_previews")
        if available:
            return {
                str(record.get("session_id") or record.get("id") or ""): json.loads(json.dumps(record))
                for record in records
                if isinstance(record, dict) and str(record.get("session_id") or record.get("id") or "").strip()
            }
        return json.loads(json.dumps(self._local_fallback("trainer_previews") or {}))

    def _mutable_trainer_preview_records(self) -> Optional[tuple[bool, Dict[str, Dict[str, Any]]]]:
        service_store_path = self._service._resolve_path("trainer_previews")
        persist_service_store = service_store_path is not None
        if persist_service_store:
            available, service_records = self._service.list_records("trainer_previews")
            if not available and service_store_path.exists():
                return None
            records = {
                str(record.get("session_id") or record.get("id") or ""): json.loads(json.dumps(record))
                for record in service_records
                if isinstance(record, dict) and str(record.get("session_id") or record.get("id") or "").strip()
            }
            return True, records

        trainer_previews = self._local_fallback("trainer_previews")
        if trainer_previews is None:
            return None
        return False, json.loads(json.dumps(trainer_previews))

    def _persist_trainer_preview_records(
        self,
        *,
        persist_service_store: bool,
        records: Dict[str, Dict[str, Any]],
    ) -> bool:
        if persist_service_store:
            return self._service.write_records("trainer_previews", records)
        self._data["trainer_previews"] = json.loads(json.dumps(records))
        self._save()
        return True

    @classmethod
    def _tw03_zero_warning_counts(cls) -> Dict[str, int]:
        return {level: 0 for level in cls._TW03_WARNING_LEVELS}

    @classmethod
    def _tw03_warning_sort_key(cls, warning: Dict[str, Any]) -> tuple[int, str]:
        level = str(warning.get("level") or "").strip().lower()
        try:
            priority = cls._TW03_WARNING_LEVELS.index(level)
        except ValueError:
            priority = len(cls._TW03_WARNING_LEVELS)
        return priority, str(warning.get("warning_id") or "")

    @classmethod
    def _tw03_preview_degraded_copy(
        cls,
        *,
        status: str,
        surface_state: str,
    ) -> Dict[str, str]:
        if status == "pending":
            return {
                "title": "Trainer preview is still running",
                "body": "Pantheon is evaluating the current trainer candidate. Keep the compare surface open and poll again after the published interval.",
            }
        if status == "failed":
            return {
                "title": "Trainer preview could not complete",
                "body": "Pantheon could not finish the rapid-eval for this candidate. Review the current control diff, then retry the preview when the compare surface is healthy.",
            }
        if surface_state == "stale":
            return {
                "title": "Trainer preview may be stale",
                "body": "Pantheon is serving the last known compare result. Review the current control diff and refresh only when the compare surface allows it.",
            }
        if surface_state == "unavailable":
            return {
                "title": "Trainer preview is temporarily unavailable",
                "body": "Pantheon cannot serve rapid-eval results for the trainer compare surface right now. Before/after metrics are temporarily unavailable.",
            }
        return {
            "title": "Trainer preview is temporarily unavailable",
            "body": "Pantheon cannot serve rapid-eval results for the trainer compare surface right now. Control changes remain visible, but before/after metrics are temporarily unavailable.",
        }

    @classmethod
    def _tw03_preview_surface_state(
        cls,
        *,
        preview: Dict[str, Any],
        dataset_source: str,
    ) -> str:
        meta = preview.get("meta") if isinstance(preview.get("meta"), dict) else {}
        surfaces = meta.get("surfaces") if isinstance(meta.get("surfaces"), dict) else {}
        requested = str(surfaces.get("trainer_preview") or "ok").strip().lower()
        if requested not in cls._TW03_SURFACE_STATES:
            requested = "ok"

        status = str(preview.get("status") or "").strip().lower()
        has_control_diff = bool(preview.get("control_diff"))
        if status == "preview_unavailable":
            return "degraded" if has_control_diff else "unavailable"
        if status == "failed" and requested == "ok" and not preview.get("metric_delta"):
            requested = "degraded"
        if dataset_source == "local_snapshot" and requested == "ok":
            return "stale"
        if dataset_source == "missing":
            return "degraded" if has_control_diff else "unavailable"
        return requested

    @classmethod
    def _project_trainer_preview_payload(
        cls,
        preview: Dict[str, Any],
        *,
        session_status: Optional[str],
        dataset_source: str,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = json.loads(json.dumps(preview))
        snapshot_timestamp = snapshot_at or _utc_now_rfc3339()
        status = str(payload.get("status") or "preview_unavailable").strip().lower()
        polling = payload.get("polling") if isinstance(payload.get("polling"), dict) else {}
        deadline_at = polling.get("deadline_at")
        deadline = _parse_rfc3339(deadline_at)

        if status == "pending" and deadline is not None and deadline <= _parse_rfc3339(snapshot_timestamp):
            payload["status"] = "preview_unavailable"
            payload["eval_id"] = None
            payload["metric_delta"] = []
            payload["warnings"] = []
            payload["warning_count_by_level"] = cls._tw03_zero_warning_counts()
            payload["preview_quality"] = "not_available"
            payload["polling"] = {
                "enabled": False,
                "poll_interval_ms": cls._TW03_POLL_INTERVAL_MS,
                "max_wait_ms": cls._TW03_MAX_WAIT_MS,
                "deadline_at": None,
            }
            status = "preview_unavailable"

        warnings = [
            {
                "warning_id": item.get("warning_id"),
                "warning_code": item.get("warning_code"),
                "level": item.get("level"),
                "parameter_key": item.get("parameter_key"),
                "metric_key": item.get("metric_key"),
                "message": item.get("message"),
                "impact_summary": item.get("impact_summary"),
            }
            for item in (payload.get("warnings") or [])
            if isinstance(item, dict)
        ]
        warnings.sort(key=cls._tw03_warning_sort_key)
        warning_counts = cls._tw03_zero_warning_counts()
        for warning in warnings:
            level = str(warning.get("level") or "").strip().lower()
            if level in warning_counts:
                warning_counts[level] += 1

        surface_state = cls._tw03_preview_surface_state(
            preview=payload,
            dataset_source=dataset_source,
        )
        session_status_normalized = str(session_status or "").strip().lower()
        allowed_refresh = (
            bool((payload.get("allowedActions") or {}).get("canRefreshPreview"))
            and session_status_normalized in cls._TW03_REFRESHABLE_SESSION_STATUSES
            and status not in {"pending", "preview_unavailable"}
            and surface_state in {"ok", "stale"}
        )

        if status == "preview_unavailable":
            payload["eval_id"] = None
            payload["metric_delta"] = []
            warnings = []
            warning_counts = cls._tw03_zero_warning_counts()
            payload["preview_quality"] = "not_available"
            allowed_refresh = False
            payload["polling"] = {
                "enabled": False,
                "poll_interval_ms": cls._TW03_POLL_INTERVAL_MS,
                "max_wait_ms": cls._TW03_MAX_WAIT_MS,
                "deadline_at": None,
            }
        else:
            payload["polling"] = {
                "enabled": status == "pending" and surface_state not in {"degraded", "unavailable"},
                "poll_interval_ms": cls._TW03_POLL_INTERVAL_MS,
                "max_wait_ms": cls._TW03_MAX_WAIT_MS,
                "deadline_at": deadline_at if status == "pending" and deadline is not None else None,
            }

        payload["status"] = status
        payload["warnings"] = warnings
        payload["warning_count_by_level"] = warning_counts
        payload["allowedActions"] = {"canRefreshPreview": allowed_refresh}
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        payload["meta"] = meta
        meta["snapshot_at"] = str(meta.get("snapshot_at") or snapshot_timestamp)
        surfaces = meta.get("surfaces") if isinstance(meta.get("surfaces"), dict) else {}
        meta["surfaces"] = surfaces
        surfaces["trainer_preview"] = surface_state

        if status == "preview_unavailable" or surface_state != "ok":
            payload["degraded_copy"] = cls._tw03_preview_degraded_copy(
                status=status,
                surface_state=surface_state,
            )
        else:
            payload["degraded_copy"] = None

        payload.setdefault("control_diff", [])
        payload.setdefault("metric_delta", [])
        payload.setdefault("baseline_snapshot_at", None)
        payload.setdefault("candidate_snapshot_at", None)
        return payload

    def build_trainer_preview_unavailable(
        self,
        session_id: str,
        *,
        session_status: Optional[str],
        snapshot_at: Optional[str] = None,
        control_diff: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        preview = {
            "session_id": session_id,
            "status": "preview_unavailable",
            "eval_id": None,
            "baseline_snapshot_at": None,
            "candidate_snapshot_at": None,
            "control_diff": json.loads(json.dumps(control_diff or [])),
            "metric_delta": [],
            "warnings": [],
            "warning_count_by_level": self._tw03_zero_warning_counts(),
            "preview_quality": "not_available",
            "allowedActions": {
                "canRefreshPreview": False,
            },
            "polling": {
                "enabled": False,
                "poll_interval_ms": self._TW03_POLL_INTERVAL_MS,
                "max_wait_ms": self._TW03_MAX_WAIT_MS,
                "deadline_at": None,
            },
            "degraded_copy": self._tw03_preview_degraded_copy(
                status="preview_unavailable",
                surface_state="degraded" if control_diff else "unavailable",
            ),
            "meta": {
                "snapshot_at": snapshot_at or _utc_now_rfc3339(),
                "surfaces": {
                    "trainer_preview": "degraded" if control_diff else "unavailable",
                },
            },
        }
        return self._project_trainer_preview_payload(
            preview,
            session_status=session_status,
            dataset_source=self.dataset_source("trainer_previews"),
            snapshot_at=snapshot_at,
        )

    def get_trainer_preview(
        self,
        session_id: str,
        *,
        session_status: Optional[str],
        eval_id: Optional[str] = None,
        snapshot_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        bundle = self._trainer_preview_records().get(session_id)
        if not bundle:
            return None
        evaluations = bundle.get("evaluations") if isinstance(bundle.get("evaluations"), dict) else {}

        preview: Optional[Dict[str, Any]] = None
        if eval_id:
            candidate = evaluations.get(eval_id)
            if isinstance(candidate, dict):
                preview = candidate
        else:
            latest_eval_id = str(bundle.get("latest_eval_id") or "").strip()
            if latest_eval_id and isinstance(evaluations.get(latest_eval_id), dict):
                preview = evaluations[latest_eval_id]
            elif isinstance(bundle.get("preview"), dict):
                preview = bundle.get("preview")
            elif evaluations:
                preview = next(iter(evaluations.values()))

        if not isinstance(preview, dict):
            return None
        return self._project_trainer_preview_payload(
            preview,
            session_status=session_status,
            dataset_source=self.dataset_source("trainer_previews"),
            snapshot_at=snapshot_at,
        )

    def refresh_trainer_preview(
        self,
        session_id: str,
        *,
        session_status: Optional[str],
        refreshed_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        base_url = self._training_session_base_url()
        if base_url:
            timestamp = refreshed_at or _utc_now_rfc3339()
            available, payload = _http_json_post(
                base_url,
                f"/api/training/sessions/{session_id}/preview",
                body={"mode": "refresh", "refreshed_at": refreshed_at},
            )
            if not available or not isinstance(payload, dict):
                return None
            return self._project_trainer_preview_payload(
                payload,
                session_status=session_status,
                dataset_source=self.dataset_source("trainer_previews"),
                snapshot_at=timestamp,
            )

        mutable = self._mutable_trainer_preview_records()
        if mutable is None:
            return None
        persist_service_store, records = mutable

        timestamp = refreshed_at or _utc_now_rfc3339()
        bundle = records.get(session_id) or {
            "session_id": session_id,
            "latest_eval_id": None,
            "evaluations": {},
        }
        evaluations = bundle.setdefault("evaluations", {})
        latest_preview = self.get_trainer_preview(
            session_id,
            session_status=session_status,
            snapshot_at=timestamp,
        )
        if latest_preview and latest_preview.get("status") == "pending":
            return latest_preview

        baseline_snapshot_at = (
            (latest_preview or {}).get("baseline_snapshot_at")
            or (self.get_trainer_session(session_id) or {}).get("started_at")
        )
        seed_control_diff = list((latest_preview or {}).get("control_diff") or [])
        latest_preview_count = len(evaluations) + 1
        prefix = timestamp[:10].replace("-", "")
        eval_id = f"teval-{prefix}-{latest_preview_count:03d}"
        while eval_id in evaluations:
            latest_preview_count += 1
            eval_id = f"teval-{prefix}-{latest_preview_count:03d}"

        preview = {
            "session_id": session_id,
            "status": "pending",
            "eval_id": eval_id,
            "baseline_snapshot_at": baseline_snapshot_at,
            "candidate_snapshot_at": timestamp,
            "control_diff": json.loads(json.dumps(seed_control_diff)),
            "metric_delta": [],
            "warnings": [],
            "warning_count_by_level": self._tw03_zero_warning_counts(),
            "preview_quality": "directional_only",
            "allowedActions": {
                "canRefreshPreview": False,
            },
            "polling": {
                "enabled": True,
                "poll_interval_ms": self._TW03_POLL_INTERVAL_MS,
                "max_wait_ms": self._TW03_MAX_WAIT_MS,
                "deadline_at": (
                    (
                        _parse_rfc3339(timestamp)
                        or datetime.now(timezone.utc)
                    ) + timedelta(milliseconds=self._TW03_MAX_WAIT_MS)
                ).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            },
            "degraded_copy": self._tw03_preview_degraded_copy(
                status="pending",
                surface_state="ok",
            ),
            "meta": {
                "snapshot_at": timestamp,
                "surfaces": {
                    "trainer_preview": "ok",
                },
            },
        }

        evaluations[eval_id] = preview
        bundle["latest_eval_id"] = eval_id
        records[session_id] = bundle
        if not self._persist_trainer_preview_records(
            persist_service_store=persist_service_store,
            records=records,
        ):
            return None

        return self._project_trainer_preview_payload(
            preview,
            session_status=session_status,
            dataset_source=self.dataset_source("trainer_previews"),
            snapshot_at=timestamp,
        )

    def get_capability_snapshot(self, snapshot_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not snapshot_id:
            return None
        available, raw = self._service.record("capability_snapshots", snapshot_id)
        if available and raw:
            return raw
        local_owned = self._local_bff_capability_snapshot_records().get(str(snapshot_id))
        if local_owned:
            return local_owned
        if available:
            return None
        return (self._local_fallback("capability_snapshots") or {}).get(snapshot_id)

    def get_capability_snapshot_for_persona(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not persona_id:
            return None
        available, snapshots = self._service.list_records("capability_snapshots")
        if available:
            for snapshot in snapshots:
                if snapshot.get("persona_id") == persona_id:
                    return snapshot
        for snapshot in self._local_bff_capability_snapshot_records().values():
            if snapshot.get("persona_id") == persona_id:
                return snapshot
        for snapshot in (self._local_fallback("capability_snapshots") or {}).values():
            if snapshot.get("persona_id") == persona_id:
                return snapshot
        return None

    # ------------------------------------------------------------------ #
    # Consultation surfaces (CS-01 – CS-06)
    # ------------------------------------------------------------------ #

    def _consultation_session_records(self) -> Dict[str, Dict[str, Any]]:
        if self._consultation_service_dataset_available("consultation_sessions"):
            service_records = self._consultation_service_records("consultation_sessions") or []
            return {
                str(session_id): session
                for session in service_records
                if isinstance(session, dict)
                for session_id in [session.get("session_id") or session.get("id")]
                if session_id
            }
        available, sessions = self._service.list_records("consultation_sessions")
        if available:
            return {
                str(session_id): session
                for session in sessions
                if isinstance(session, dict)
                for session_id in [session.get("session_id") or session.get("id")]
                if session_id
            }
        return self._local_fallback("consultation_sessions") or {}

    def list_consultations_for_persona(
        self,
        persona_id: Optional[str],
        consultation_type: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Optional[List[Dict[str, Any]]]:
        """CS-01: List consultation sessions for a persona.

        Returns None when the persona cannot be verified (degraded mode).
        Only returns sessions where persona_id is the requester (the
        session whose session_id matches metadata.consultation.requester_session_id).
        """
        if not persona_id:
            return None
        if self.get_persona(persona_id) is None:
            return None
        all_sessions = self._consultation_session_records()
        sessions = [
            s for s in all_sessions.values()
            if s.get("persona_id") == persona_id
            and s.get("session_type") in {"consult", "committee"}
            and s.get("session_id") == (
                (s.get("metadata") or {}).get("consultation", {}).get("requester_session_id")
            )
        ]
        if consultation_type:
            sessions = [
                s for s in sessions
                if (s.get("metadata") or {}).get("consultation", {}).get("consultation_type") == consultation_type
            ]
        if status:
            sessions = [s for s in sessions if s.get("status") == status]
        sessions = sorted(sessions, key=lambda x: x.get("started_at", ""), reverse=True)
        return sessions

    def get_consultation(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """CS-02: Return a consultation session by session_id."""
        if not session_id:
            return None
        session = self._consultation_session_records().get(session_id)
        if session is None:
            return None
        if session.get("session_type") not in {"consult", "committee"}:
            return None
        return session

    def _resolve_root_consultation_id(self, session_id: str) -> str:
        """Return the root (requester) session id for a given consultation session_id.

        For requester sessions the id is returned unchanged.
        For responder/committee sessions that carry root_session_id in their
        metadata.consultation, that pointer is followed one level.
        """
        session = self._consultation_session_records().get(session_id)
        if session is None:
            return session_id
        meta_consult = (session.get("metadata") or {}).get("consultation", {})
        if meta_consult.get("requester_session_id"):
            # Already the root
            return session_id
        root_ref = meta_consult.get("root_session_id")
        if root_ref:
            return root_ref
        return session_id

    def get_consultation_participants(self, session_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        """CS-03: Return all participant sessions linked to a consultation.

        The requester session is identified by session_id = metadata.consultation.requester_session_id.
        Responder sessions are identified by metadata.consultation.responder_session_ids.
        Committee sessions are identified by metadata.consultation.committee_session_ids.

        When called with a responder or committee session id, the root session is
        resolved via metadata.consultation.root_session_id so that participants,
        outcome, and evidence are always served from the authoritative root record.
        """
        if not session_id:
            return None
        all_sessions = self._consultation_session_records()
        if session_id not in all_sessions:
            return None
        root_id = self._resolve_root_consultation_id(session_id)
        root = all_sessions.get(root_id)
        if root is None:
            return None
        meta_consult = (root.get("metadata") or {}).get("consultation", {})
        requester_id = meta_consult.get("requester_session_id")
        responder_ids: List[str] = meta_consult.get("responder_session_ids") or []
        committee_ids: List[str] = meta_consult.get("committee_session_ids") or []

        participants = []

        def _role_for(sid: str) -> str:
            if sid == requester_id:
                return "requester"
            if sid in committee_ids:
                return "committee_participant"
            return "responder"

        for sid in [requester_id] + responder_ids + committee_ids:
            if not sid:
                continue
            session = all_sessions.get(sid)
            if session:
                enriched = dict(session)
                enriched["consultation_role"] = _role_for(sid)
                participants.append(enriched)

        return participants

    def get_consultation_outcome(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """CS-04: Return the consultation outcome projection for a session.

        When called with a responder or committee session id, outcome is resolved
        from the root (requester) session via root_session_id.
        """
        if not session_id:
            return None
        all_sessions = self._consultation_session_records()
        if session_id not in all_sessions:
            return None
        root_id = self._resolve_root_consultation_id(session_id)
        session = self.get_consultation(root_id)
        if session is None:
            return None
        meta_consult = (session.get("metadata") or {}).get("consultation", {})
        return {
            "session_id": session_id,
            "root_session_id": root_id,
            "source_session": f"/api/v1/consultations/{root_id}",
            "metadata": {
                "consultation": {
                    "outcome": meta_consult.get("outcome"),
                    "actual_reviewers": meta_consult.get("actual_reviewers"),
                    "responder_session_ids": meta_consult.get("responder_session_ids", []),
                    "rationale_ref": meta_consult.get("rationale_ref"),
                    "evidence_refs": meta_consult.get("evidence_refs", []),
                    "escalation_path": meta_consult.get("escalation_path"),
                }
            },
        }

    def get_consultation_evidence(self, session_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        """CS-05: Return evidence refs attached to a consultation session.

        When called with a responder or committee session id, evidence is resolved
        from the root (requester) session via root_session_id.
        """
        if not session_id:
            return None
        all_sessions = self._consultation_session_records()
        if session_id not in all_sessions:
            return None
        root_id = self._resolve_root_consultation_id(session_id)
        session = self.get_consultation(root_id)
        if session is None:
            return None
        meta_consult = (session.get("metadata") or {}).get("consultation", {})
        return list(meta_consult.get("evidence_refs") or [])

    def _consult_transcript_records(self) -> Dict[str, Dict[str, Any]]:
        if self._consultation_service_dataset_available("consult_transcripts"):
            service_records = self._consultation_service_records("consult_transcripts") or []
            return {
                str(session_id): transcript
                for transcript in service_records
                if isinstance(transcript, dict)
                for session_id in [transcript.get("session_id") or transcript.get("transcript_id")]
                if session_id
            }
        available, records = self._service.list_records("consult_transcripts")
        if available:
            return {
                str(session_id): transcript
                for transcript in records
                if isinstance(transcript, dict)
                for session_id in [transcript.get("session_id") or transcript.get("transcript_id")]
                if session_id
            }
        return self._local_fallback("consult_transcripts") or {}

    def get_consult_transcript(
        self,
        session_id: Optional[str],
        *,
        from_sequence_no: Optional[int] = None,
        page_size: int = 50,
        page_token: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """CW-02: Return the ordered transcript for a consultation session.

        Resolves root session_id so responder and committee sessions route to the
        same transcript as the requester session.  Events are ordered by
        sequence_no ascending and filtered by from_sequence_no when given.
        page_token is treated as an opaque integer offset into the filtered set.
        Returns None when the session does not exist.
        """
        if not session_id:
            return None
        all_sessions = self._consultation_session_records()
        if session_id not in all_sessions:
            return None
        root_id = self._resolve_root_consultation_id(session_id)
        root_session = all_sessions.get(root_id)
        if root_session is None:
            return None

        transcripts = self._consult_transcript_records()
        record = transcripts.get(root_id)

        surface_state: str
        events: List[Dict[str, Any]]
        transcript_id: str
        linked_request_id: Optional[str]

        if record is None:
            surface_state = "unavailable"
            events = []
            transcript_id = f"tr-{root_id}"
            linked_request_id = root_session.get("request_id")
        else:
            transcript_id = str(record.get("transcript_id") or f"tr-{root_id}")
            linked_request_id = record.get("linked_request_id") or root_session.get("request_id")
            raw_events: List[Dict[str, Any]] = list(record.get("events") or [])
            raw_events.sort(key=lambda e: int(e.get("sequence_no") or 0))

            full_seqs = [int(e.get("sequence_no") or 0) for e in raw_events]
            has_gap = any(
                full_seqs[i + 1] != full_seqs[i] + 1
                for i in range(len(full_seqs) - 1)
            )
            surface_state = "degraded" if has_gap else "ok"

            if from_sequence_no is not None:
                raw_events = [e for e in raw_events if int(e.get("sequence_no") or 0) >= from_sequence_no]

            events = raw_events

        offset = 0
        if page_token:
            try:
                offset = int(page_token)
            except (ValueError, TypeError):
                offset = 0

        page_events = events[offset: offset + page_size]
        next_offset = offset + page_size
        next_page_token: Optional[str] = str(next_offset) if next_offset < len(events) else None

        now = _utc_now_rfc3339()
        return {
            "object_ref": {
                "type": "ConsultTranscript",
                "id": transcript_id,
            },
            "transcript_id": transcript_id,
            "session_id": root_id,
            "linked_request_id": linked_request_id,
            "events": page_events,
            "page_info": {
                "next_page_token": next_page_token,
                "page_size": page_size,
                "total": len(events),
            },
            "meta": {
                "snapshot_at": now,
                "staleness": {
                    "served_from": self.dataset_source("consult_transcripts") if record is not None else "unavailable",
                    "last_known_at": now,
                },
                "surfaces": {
                    "transcript": {
                        "state": surface_state,
                    },
                },
            },
        }

    @staticmethod
    def _committee_surface_state(root_session: Dict[str, Any]) -> str:
        consult = (root_session.get("metadata") or {}).get("consultation", {})
        return str(consult.get("committee_surface_state") or "ok")

    @staticmethod
    def _committee_linked_request_id(root_session: Dict[str, Any]) -> Optional[str]:
        return root_session.get("request_id")

    @classmethod
    def _committee_board_row(
        cls,
        root_session: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        consult = (root_session.get("metadata") or {}).get("consultation", {})
        committee_id = str(consult.get("committee_ref") or "").strip()
        committee_session_ids = list(consult.get("committee_session_ids") or [])
        if not committee_id or not committee_session_ids:
            return None
        return {
            "committee_id": committee_id,
            "committee_ref": committee_id,
            "escalation_reason": json.loads(json.dumps(consult.get("escalation_reason") or {})),
            "quorum_state": consult.get("quorum_state"),
            "consensus_state": consult.get("consensus_state"),
            "linked_request_id": cls._committee_linked_request_id(root_session),
            "started_at": consult.get("committee_started_at") or root_session.get("started_at"),
            "surface_state": cls._committee_surface_state(root_session),
            "route_href": f"/consultation/committees/{committee_id}",
        }

    def list_committees(
        self,
        *,
        quorum_states: Optional[List[str]] = None,
        consensus_states: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for session in self._consultation_session_records().values():
            if session.get("session_type") != "consult":
                continue
            row = self._committee_board_row(session)
            if row is None:
                continue
            rows.append(row)

        if quorum_states:
            requested = {str(value).strip().lower() for value in quorum_states if str(value).strip()}
            rows = [
                row
                for row in rows
                if str(row.get("quorum_state") or "").strip().lower() in requested
            ]
        if consensus_states:
            requested = {str(value).strip().lower() for value in consensus_states if str(value).strip()}
            rows = [
                row
                for row in rows
                if str(row.get("consensus_state") or "").strip().lower() in requested
            ]

        rows.sort(
            key=lambda row: (_parse_rfc3339(row.get("started_at")) or datetime.min).replace(tzinfo=None),
            reverse=True,
        )
        return rows

    def get_committee(self, committee_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not committee_id:
            return None

        root_session: Optional[Dict[str, Any]] = None
        for session in self._consultation_session_records().values():
            if session.get("session_type") != "consult":
                continue
            consult = (session.get("metadata") or {}).get("consultation", {})
            if str(consult.get("committee_ref") or "") == str(committee_id):
                root_session = session
                break
        if root_session is None:
            return None

        consult = (root_session.get("metadata") or {}).get("consultation", {})
        committee_session_ids = list(consult.get("committee_session_ids") or [])
        all_sessions = self._consultation_session_records()
        participant_roster: List[Dict[str, Any]] = []
        sponsor_session_id = str(consult.get("sponsor_session_id") or "").strip()

        for session_id in committee_session_ids:
            participant = all_sessions.get(session_id)
            if not participant:
                continue
            participant_consult = (participant.get("metadata") or {}).get("consultation", {})
            persona = self.get_persona(participant.get("persona_id"))
            participant_roster.append(
                {
                    "participant_id": participant.get("session_id"),
                    "persona_id": participant.get("persona_id"),
                    "persona_label": (persona or {}).get("name"),
                    "role": "sponsor" if participant.get("session_id") == sponsor_session_id else (
                        participant_consult.get("role") or "committee_participant"
                    ),
                    "status": participant_consult.get("participant_status") or participant.get("status"),
                    "outcome_signal": participant_consult.get("outcome_signal"),
                    "rationale_ref": participant_consult.get("rationale_ref"),
                }
            )

        sponsor_assignment = next(
            (row for row in participant_roster if str(row.get("participant_id") or "") == sponsor_session_id),
            None,
        )
        board_row = self._committee_board_row(root_session)
        if board_row is None:
            return None

        return {
            **board_row,
            "linked_session_id": root_session.get("session_id"),
            "participant_roster": participant_roster,
            "sponsor_assignment": sponsor_assignment,
            "sponsor_decision": consult.get("sponsor_decision"),
            "sponsor_decided_at": consult.get("sponsor_decided_at"),
            "sponsor_decided_by": consult.get("sponsor_decided_by"),
            "synthesis_summary": json.loads(json.dumps(consult.get("synthesis_summary") or {})),
            "linked_evidence": json.loads(json.dumps(consult.get("evidence_refs") or [])),
            "service_handoff": json.loads(json.dumps(consult.get("service_handoff") or {})),
        }

    def record_sponsor_decision(
        self,
        committee_id: str,
        *,
        sponsor_decision: str,
        rationale_ref: str,
        actor_id: str,
        recorded_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        client = self._consultation_client()
        if client is not None:
            try:
                client.record_sponsor_decision(
                    committee_id,
                    sponsor_decision=sponsor_decision,
                    rationale_ref=rationale_ref,
                    actor_id=actor_id,
                    recorded_at=recorded_at or _utc_now_rfc3339(),
                )
            except ConsultationClientError as exc:
                if exc.status_code in {404, 409}:
                    return None
                raise
            return self.get_committee(committee_id)

        store = self._consultation_store()
        if store is not None:
            matched_request: Optional[ConsultRequest] = None
            matched_consult: Dict[str, Any] = {}
            for request in store.list_requests():
                data = _model_to_data(request)
                metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
                consult = metadata.get("consultation") if isinstance(metadata.get("consultation"), dict) else {}
                if str(consult.get("committee_ref") or "") == str(committee_id):
                    matched_request = request
                    matched_consult = dict(consult)
                    break
            if matched_request is None:
                return None

            timestamp = recorded_at or _utc_now_rfc3339()
            matched_consult["sponsor_decision"] = sponsor_decision
            matched_consult["sponsor_decided_at"] = timestamp
            matched_consult["sponsor_decided_by"] = actor_id
            matched_consult["consensus_state"] = "reached"
            matched_consult["outcome"] = sponsor_decision
            synthesis_summary = dict(matched_consult.get("synthesis_summary") or {})
            synthesis_summary["outcome"] = sponsor_decision
            synthesis_summary["rationale_ref"] = rationale_ref
            matched_consult["synthesis_summary"] = synthesis_summary
            matched_consult["rationale_ref"] = rationale_ref

            memos = [
                memo
                for memo in store.list_memos_for_request(matched_request.request_id)
                if str(memo.status.value if hasattr(memo.status, "value") else memo.status) == MemoStatus.PUBLISHED.value
            ]
            if not memos:
                return None

            evidence_ref_ids: List[str] = []
            for ref_id in matched_request.evidence_refs:
                if str(ref_id or "").strip() and str(ref_id) not in evidence_ref_ids:
                    evidence_ref_ids.append(str(ref_id))
            for attachment in store.list_evidence_for_request(matched_request.request_id):
                ref_id = str(attachment.evidence_ref.id or "").strip()
                if ref_id and ref_id not in evidence_ref_ids:
                    evidence_ref_ids.append(ref_id)
            for item in matched_consult.get("evidence_refs") or []:
                ref_id = str(item.get("id") if isinstance(item, dict) else item or "").strip()
                if ref_id and ref_id not in evidence_ref_ids:
                    evidence_ref_ids.append(ref_id)

            audit_refs = [
                event.audit_id
                for event in store.list_audit_for_request(matched_request.request_id)
            ]
            handoff = ConsultGateHandoff(
                handoff_id=f"gh-{uuid.uuid4().hex[:12]}",
                request_id=matched_request.request_id,
                target_gate=f"committee_sponsor_decision:{committee_id}",
                memo_ids=[memo.memo_id for memo in memos],
                evidence_refs=evidence_ref_ids,
                audit_refs=audit_refs,
                trace_id=matched_request.trace_id,
                status=GateHandoffStatus.SENT,
                sent_at=timestamp,
            )
            store.put_handoff(handoff)
            audit = ConsultAuditEvent(
                audit_id=f"aud-{uuid.uuid4().hex[:12]}",
                request_id=matched_request.request_id,
                actor_ref=ConsultationActorRef(actor_type="operator", actor_id=actor_id),
                service_actor_ref=ConsultationActorRef(actor_type="service", actor_id="consultation-svc"),
                action="gate_handoff_created",
                after_state=handoff.handoff_id,
                timestamp=timestamp,
                trace_id=matched_request.trace_id,
            )
            store.append_audit(audit)
            handoff.audit_refs.append(audit.audit_id)
            store.put_handoff(handoff)

            metadata = matched_request.metadata if isinstance(matched_request.metadata, dict) else {}
            metadata["consultation"] = matched_consult
            metadata["service_handoff"] = {
                "handoff_id": handoff.handoff_id,
                "target_gate": handoff.target_gate,
                "evidence_refs": list(handoff.evidence_refs),
                "audit_refs": list(handoff.audit_refs),
                "status": handoff.status.value if hasattr(handoff.status, "value") else handoff.status,
            }
            matched_request.metadata = metadata
            store.put_request(matched_request)
            return self.get_committee(committee_id)

        service_store_path = self._service._resolve_path("consultation_sessions")
        persist_service_store = service_store_path is not None
        consultation_sessions: Optional[Dict[str, Any]]
        if persist_service_store:
            available, service_sessions = self._service.list_records("consultation_sessions")
            if not available and service_store_path.exists():
                return None
            consultation_sessions = {
                str(session.get("session_id") or session.get("id") or ""): json.loads(json.dumps(session))
                for session in service_sessions
                if isinstance(session, dict)
                and str(session.get("session_id") or session.get("id") or "").strip()
            }
        else:
            consultation_sessions = self._local_fallback("consultation_sessions")
            if consultation_sessions is None:
                return None

        root_session_id: Optional[str] = None
        for session_id, session in consultation_sessions.items():
            consult = (session.get("metadata") or {}).get("consultation", {})
            if (
                session.get("session_type") == "consult"
                and str(consult.get("committee_ref") or "") == str(committee_id)
            ):
                root_session_id = session_id
                break
        if root_session_id is None:
            return None

        timestamp = recorded_at or _utc_now_rfc3339()
        consult = consultation_sessions[root_session_id].setdefault("metadata", {}).setdefault("consultation", {})
        consult["sponsor_decision"] = sponsor_decision
        consult["sponsor_decided_at"] = timestamp
        consult["sponsor_decided_by"] = actor_id
        consult["consensus_state"] = "reached"
        consult["outcome"] = sponsor_decision
        synthesis_summary = dict(consult.get("synthesis_summary") or {})
        synthesis_summary["outcome"] = sponsor_decision
        synthesis_summary["rationale_ref"] = rationale_ref
        consult["synthesis_summary"] = synthesis_summary
        consult["rationale_ref"] = rationale_ref

        if persist_service_store:
            self._service.write_records("consultation_sessions", consultation_sessions)
        else:
            self._save()
        return self.get_committee(committee_id)

    def get_consult_policy(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """CS-06: Return the ConsultPolicy for a persona."""
        if not persona_id:
            return None
        available, raw = self._service.record("consult_policies", persona_id)
        if available:
            return raw
        return (self._local_fallback("consult_policies") or {}).get(persona_id)

    def get_persona_consult_policy(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.get_consult_policy(persona_id)

    def get_route_policy_for_persona(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not persona_id:
            return None
        route_policies = self._local_fallback("persona_route_policies") or {}
        if isinstance(route_policies, dict):
            policy = route_policies.get(persona_id)
            if isinstance(policy, dict):
                return json.loads(json.dumps(policy))
        consult_policy = self.get_consult_policy(persona_id)
        if consult_policy:
            return {
                "personaId": persona_id,
                "version": "v1",
                "rules": [
                    {
                        "route": rule.get("condition"),
                        "mode": "consult_required",
                        "description": rule.get("description"),
                    }
                    for rule in consult_policy.get("trigger_rules") or []
                    if isinstance(rule, dict)
                ],
                "consult_policy": json.loads(json.dumps(consult_policy)),
            }
        return None

    def get_persona_allowed_actions(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Derive allowed actions for a persona based on lifecycle state and session status.

        Returns None when the persona cannot be verified (degraded mode).
        """
        if not persona_id:
            return None
        persona = self.get_persona(persona_id)
        if not persona:
            return None

        lifecycle_state = persona.get("lifecycle_state", "unknown")
        sessions = self.get_sessions_for_persona(persona_id) or []
        active_sessions = [s for s in sessions if s.get("status") == "active"]

        actions: Dict[str, Any] = {}

        # Persona lifecycle-based actions
        if lifecycle_state == "draft":
            actions["canActivate"] = True
            actions["canEdit"] = True
            actions["canDelete"] = True
        elif lifecycle_state == "active":
            actions["canActivate"] = False
            actions["canEdit"] = True
            actions["canDelete"] = False
            actions["canRetire"] = True
            actions["canPause"] = len(active_sessions) == 0
        elif lifecycle_state == "retired":
            actions["canActivate"] = False
            actions["canEdit"] = False
            actions["canDelete"] = False
            actions["canRetire"] = False
            actions["canPause"] = False

        # Session-based actions
        if active_sessions:
            actions["canTerminateSession"] = True
            actions["canPauseSession"] = True

        # Teaching session inference
        teaching_sessions = self.get_teaching_sessions_for_persona(persona_id) or []
        if teaching_sessions:
            actions["canViewTeachingHistory"] = True

        return actions

    # ---------------------------------------------------------------------- #
    # CW-01: Consult Request
    # ---------------------------------------------------------------------- #

    _CW01_TERMINAL_STATUSES = {"completed", "canceled"}

    @classmethod
    def _consult_request_can_cancel(cls, req: Dict[str, Any]) -> bool:
        status = str(req.get("status") or "created")
        if status in cls._CW01_TERMINAL_STATUSES:
            return False
        return not bool(req.get("linked_session_id"))

    @classmethod
    def _project_consult_request_summary(cls, req: Dict[str, Any]) -> Dict[str, Any]:
        status = str(req.get("status") or "created")
        can_cancel = cls._consult_request_can_cancel(req)
        task_full = str(req.get("task") or "")
        task_summary = task_full[:120] + ("…" if len(task_full) > 120 else "")
        return {
            "request_id": req.get("request_id"),
            "status": status,
            "from_persona_id": req.get("from_persona_id"),
            "target_type": req.get("target_type"),
            "target_ref": req.get("target_ref"),
            "task_summary": task_summary,
            "priority": req.get("priority"),
            "consultation_type": req.get("consultation_type"),
            "created_at": req.get("created_at"),
            "linked_session_id": req.get("linked_session_id"),
            "request_to_session_status": req.get(
                "request_to_session_status", "pending_session"
            ),
            "allowedActions": {"canCancel": can_cancel},
        }

    @classmethod
    def _project_consult_request_detail(cls, req: Dict[str, Any]) -> Dict[str, Any]:
        status = str(req.get("status") or "created")
        can_cancel = cls._consult_request_can_cancel(req)
        linked_session_id = req.get("linked_session_id")
        r2s_status = str(
            req.get("request_to_session_status") or "pending_session"
        )
        session_route_href = (
            f"/api/v1/consultations/{linked_session_id}" if linked_session_id else None
        )
        return {
            "request_id": req.get("request_id"),
            "status": status,
            "from_persona_id": req.get("from_persona_id"),
            "target_type": req.get("target_type"),
            "target_ref": req.get("target_ref"),
            "task": req.get("task"),
            "context_refs": req.get("context_refs", []),
            "priority": req.get("priority"),
            "consultation_type": req.get("consultation_type"),
            "created_at": req.get("created_at"),
            "completed_at": req.get("completed_at"),
            "canceled_at": req.get("canceled_at"),
            "linked_session_id": linked_session_id,
            "request_to_session_status": r2s_status,
            "session_handoff": {
                "status": r2s_status,
                "linked_session_id": linked_session_id,
                "session_route_href": session_route_href,
                "note": req.get("session_handoff_note", ""),
            },
            "allowedActions": {"canCancel": can_cancel},
        }

    def list_consult_requests(
        self,
        *,
        statuses: Optional[List[str]] = None,
        target_type: Optional[str] = None,
        consultation_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        requests = self._read_dataset_records("consult_requests")
        if statuses:
            requested = {s.strip().lower() for s in statuses if s.strip()}
            requests = [
                r for r in requests
                if str(r.get("status") or "").strip().lower() in requested
            ]
        if target_type:
            requested_tt = target_type.strip().lower()
            requests = [
                r for r in requests
                if str(r.get("target_type") or "").strip().lower() == requested_tt
            ]
        if consultation_type:
            requested_ct = consultation_type.strip().lower()
            requests = [
                r for r in requests
                if str(r.get("consultation_type") or "").strip().lower() == requested_ct
            ]
        requests.sort(
            key=lambda r: (_parse_rfc3339(r.get("created_at")) or datetime.min).replace(tzinfo=None),
            reverse=True,
        )
        return [self._project_consult_request_summary(r) for r in requests]

    def get_consult_request(self, request_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not request_id:
            return None
        if self._consultation_service_dataset_available("consult_requests"):
            requests = {
                str(record.get("request_id") or ""): record
                for record in (self._consultation_service_records("consult_requests") or [])
                if isinstance(record, dict)
            }
            req = requests.get(request_id)
            return self._project_consult_request_detail(req) if req else None
        available, req = self._service.record("consult_requests", request_id)
        if not available:
            req = (self._local_fallback("consult_requests") or {}).get(request_id)
        if not req:
            return None
        return self._project_consult_request_detail(req)

    def create_consult_request(
        self,
        *,
        from_persona_id: str,
        target_type: str,
        target_ref: str,
        task: str,
        context_refs: List[Dict[str, str]],
        priority: str,
        consultation_type: str,
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        client = self._consultation_client()
        if client is not None:
            timestamp = created_at or _utc_now_rfc3339()
            serialized_context_refs = [
                f"{item['type']}:{item['id']}"
                for item in context_refs
                if isinstance(item, dict) and item.get("type") and item.get("id")
            ]
            service_request = client.create_request(
                {
                    "request_type": _BFF_TO_SERVICE_REQUEST_TYPE.get(
                        consultation_type,
                        ConsultRequestType.STRATEGY_REVIEW,
                    ).value,
                    "requested_by": {"actor_type": "operator", "actor_id": actor_id},
                    "from_persona_id": from_persona_id,
                    "target_type": target_type,
                    "target_id": target_ref,
                    "task": task,
                    "consultation_type": consultation_type,
                    "context_refs": serialized_context_refs,
                    "priority": _BFF_TO_SERVICE_PRIORITY.get(priority, ConsultPriority.NORMAL).value,
                    "status": ConsultRequestStatus.DRAFT.value,
                    "linked_session_id": None,
                    "request_to_session_status": "pending_session",
                    "completed_at": None,
                    "canceled_at": None,
                    "session_handoff_note": "Request accepted; session creation is pending Persona Plane assignment.",
                    "metadata": {
                        "bff_context_refs": context_refs,
                        "bff_priority": priority,
                        "task": task,
                        "consultation_type": consultation_type,
                    },
                    "trace_id": f"trace-cr-{uuid.uuid4().hex[:12]}",
                    "created_at": timestamp,
                }
            )
            return self._project_consult_request_detail(
                self._project_service_request_record(service_request)
            )

        store = self._consultation_store()
        if store is not None:
            timestamp = created_at or _utc_now_rfc3339()
            request_id = f"cr-{timestamp[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
            existing_ids = {request.request_id for request in store.list_requests()}
            while request_id in existing_ids:
                request_id = f"cr-{timestamp[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
            serialized_context_refs = [
                f"{item['type']}:{item['id']}"
                for item in context_refs
                if isinstance(item, dict) and item.get("type") and item.get("id")
            ]
            trace_id = f"trace-{request_id}"
            service_request = ConsultRequest(
                request_id=request_id,
                request_type=_BFF_TO_SERVICE_REQUEST_TYPE.get(
                    consultation_type,
                    ConsultRequestType.STRATEGY_REVIEW,
                ),
                requested_by=ConsultationActorRef(actor_type="operator", actor_id=actor_id),
                from_persona_id=from_persona_id,
                target_type=target_type,
                target_id=target_ref,
                task=task,
                consultation_type=consultation_type,
                context_refs=serialized_context_refs,
                priority=_BFF_TO_SERVICE_PRIORITY.get(priority, ConsultPriority.NORMAL),
                status=ConsultRequestStatus.DRAFT,
                linked_session_id=None,
                request_to_session_status="pending_session",
                completed_at=None,
                canceled_at=None,
                session_handoff_note="Request accepted; session creation is pending Persona Plane assignment.",
                metadata={
                    "bff_context_refs": context_refs,
                    "bff_priority": priority,
                    "task": task,
                    "consultation_type": consultation_type,
                },
                trace_id=trace_id,
                created_at=timestamp,
            )
            store.put_request(service_request)
            store.append_audit(
                ConsultAuditEvent(
                    audit_id=f"aud-{uuid.uuid4().hex[:12]}",
                    request_id=request_id,
                    actor_ref=ConsultationActorRef(actor_type="operator", actor_id=actor_id),
                    action="request_created",
                    after_state=ConsultRequestStatus.DRAFT.value,
                    trace_id=trace_id,
                )
            )
            return self._project_consult_request_detail(
                self._project_service_request_record(_model_to_data(service_request))
            )

        service_store_path = self._service._resolve_path("consult_requests")
        persist_service_store = service_store_path is not None
        requests: Optional[Dict[str, Any]]
        if persist_service_store:
            available, service_requests = self._service.list_records("consult_requests")
            if not available and service_store_path.exists():
                raise RuntimeError("Consult request store is unavailable.")
            requests = {
                str(r.get("request_id") or r.get("id") or ""): json.loads(json.dumps(r))
                for r in service_requests
                if isinstance(r, dict)
                and str(r.get("request_id") or r.get("id") or "").strip()
            }
        else:
            requests = self._local_fallback("consult_requests") or {}

        timestamp = created_at or _utc_now_rfc3339()
        request_id = f"cr-{timestamp[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
        while request_id in (requests or {}):
            request_id = f"cr-{timestamp[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"

        req: Dict[str, Any] = {
            "request_id": request_id,
            "status": "created",
            "from_persona_id": from_persona_id,
            "target_type": target_type,
            "target_ref": target_ref,
            "task": task,
            "context_refs": context_refs,
            "priority": priority,
            "consultation_type": consultation_type,
            "created_at": timestamp,
            "completed_at": None,
            "canceled_at": None,
            "linked_session_id": None,
            "request_to_session_status": "pending_session",
            "session_handoff_note": "Request accepted; session creation is pending Persona Plane assignment.",
            "created_by": actor_id,
        }
        if requests is None:
            requests = {}
        requests[request_id] = req
        if persist_service_store:
            self._service.write_records("consult_requests", requests)
        else:
            local_key = self._LOCAL_DATA_KEYS.get("consult_requests", "consult_requests")
            self._data.setdefault(local_key, {})[request_id] = req
            self._save()
        return self._project_consult_request_detail(req)

    # ---------------------------------------------------------------------- #
    # CW-04: Red-team Memo
    # ---------------------------------------------------------------------- #

    @staticmethod
    def _cw04_route_href(memo_id: Optional[str]) -> Optional[str]:
        memo_ref = str(memo_id or "").strip()
        if not memo_ref:
            return None
        return f"/consultation/memos/{memo_ref}"

    @staticmethod
    def _cw04_normalize_evidence_ref(raw: Any) -> Dict[str, Any]:
        evidence_ref = raw if isinstance(raw, dict) else {}
        ref_id = str(evidence_ref.get("id") or evidence_ref.get("ref_id") or "").strip()
        link = evidence_ref.get("link") or evidence_ref.get("route_href")
        if not link and ref_id:
            link = f"/evidence/{ref_id}"
        return {
            "id": ref_id,
            "evidence_type": evidence_ref.get("evidence_type") or evidence_ref.get("type"),
            "artifact_ref": evidence_ref.get("artifact_ref"),
            "description": evidence_ref.get("description") or evidence_ref.get("display_label"),
            "link": link,
        }

    @classmethod
    def _project_consult_memo_summary(cls, memo: Dict[str, Any]) -> Dict[str, Any]:
        memo_id = str(memo.get("memo_id") or memo.get("id") or "").strip()
        recommendations = list(memo.get("recommendations") or [])
        return {
            "object_ref": {
                "type": "ConsultMemo",
                "id": memo_id,
            },
            "memo_id": memo_id,
            "memo_type": memo.get("memo_type") or "red_team",
            "status": memo.get("status") or memo.get("lifecycle_state") or "draft",
            "linked_request_id": memo.get("linked_request_id"),
            "recommendation_count": len(recommendations),
            "published_at": memo.get("published_at"),
            "created_at": memo.get("created_at"),
            "route_href": cls._cw04_route_href(memo_id),
        }

    @classmethod
    def _project_consult_memo_detail(cls, memo: Dict[str, Any]) -> Dict[str, Any]:
        memo = _redact_consult_memo_review_payload(memo)
        memo_id = str(memo.get("memo_id") or memo.get("id") or "").strip()
        mapping = memo.get("session_to_memo_mapping") if isinstance(memo.get("session_to_memo_mapping"), dict) else {}
        governance_target = memo.get("governance_target") if isinstance(memo.get("governance_target"), dict) else {}
        return {
            "object_ref": {
                "type": "ConsultMemo",
                "id": memo_id,
            },
            "memo_id": memo_id,
            "memo_type": memo.get("memo_type") or "red_team",
            "status": memo.get("status") or memo.get("lifecycle_state") or "draft",
            "lifecycle_state": memo.get("lifecycle_state") or memo.get("status") or "draft",
            "author_ref": memo.get("author_ref"),
            "linked_request_id": memo.get("linked_request_id"),
            "linked_session_id": memo.get("linked_session_id"),
            "session_to_memo_mapping": {
                "mapping_id": mapping.get("mapping_id"),
                "source_session_id": mapping.get("source_session_id"),
                "transcript_id": mapping.get("transcript_id"),
                "transcript_version": mapping.get("transcript_version"),
                "memo_id": mapping.get("memo_id") or memo_id,
                "memo_type": mapping.get("memo_type") or memo.get("memo_type") or "red_team",
                "created_by": json.loads(json.dumps(mapping.get("created_by") or {})),
                "evidence_refs": list(mapping.get("evidence_refs") or []),
                "mapping_status": mapping.get("mapping_status"),
                "created_at": mapping.get("created_at"),
            },
            "summary": memo.get("summary"),
            "recommendations": list(memo.get("recommendations") or []),
            "evidence_refs": [
                cls._cw04_normalize_evidence_ref(item)
                for item in (memo.get("evidence_refs") or [])
            ],
            "published_at": memo.get("published_at"),
            "created_at": memo.get("created_at"),
            "supersedes_memo_id": memo.get("supersedes_memo_id"),
            "superseded_by_memo_id": memo.get("superseded_by_memo_id"),
            "surface_state": memo.get("surface_state") or "ok",
            "governance_target": json.loads(json.dumps(governance_target)),
            "suppressed": bool(memo.get("suppressed")),
            "withdrawn": bool(memo.get("withdrawn")),
            "active_governance_review_id": memo.get("active_governance_review_id"),
        }

    def list_consult_memos(
        self,
        *,
        statuses: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        memos = self._read_dataset_records("consult_memos")
        if statuses:
            requested = {str(value).strip().lower() for value in statuses if str(value).strip()}
            memos = [
                memo
                for memo in memos
                if str(memo.get("status") or memo.get("lifecycle_state") or "").strip().lower() in requested
            ]
        memos.sort(
            key=lambda memo: (
                (_parse_rfc3339(memo.get("published_at") or memo.get("created_at")) or datetime.min).replace(tzinfo=None),
                (_parse_rfc3339(memo.get("created_at")) or datetime.min).replace(tzinfo=None),
                str(memo.get("memo_id") or ""),
            ),
            reverse=True,
        )
        return [self._project_consult_memo_summary(memo) for memo in memos]

    def get_consult_memo(self, memo_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not memo_id:
            return None
        if self._consultation_service_dataset_available("consult_memos"):
            memos = {
                str(record.get("memo_id") or ""): record
                for record in (self._consultation_service_records("consult_memos") or [])
                if isinstance(record, dict)
            }
            memo = memos.get(memo_id)
            return self._project_consult_memo_detail(memo) if memo else None
        available, memo = self._service.record("consult_memos", memo_id)
        if not available:
            memo = (self._local_fallback("consult_memos") or {}).get(memo_id)
        if not memo:
            return None
        return self._project_consult_memo_detail(memo)

    def cancel_consult_request(
        self,
        request_id: str,
        *,
        actor_id: str,
        canceled_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        client = self._consultation_client()
        if client is not None:
            try:
                request = client.cancel_request(
                    request_id,
                    actor_id=actor_id,
                    canceled_at=canceled_at or _utc_now_rfc3339(),
                )
            except ConsultationClientError as exc:
                if exc.status_code in {404, 409}:
                    return None
                raise
            if request is None:
                return None
            return self._project_consult_request_detail(
                self._project_service_request_record(request)
            )

        store = self._consultation_store()
        if store is not None:
            request = store.get_request(request_id)
            if request is None:
                return None
            projected = self._project_service_request_record(_model_to_data(request))
            if not self._consult_request_can_cancel(projected):
                return None
            before_state = request.status.value if hasattr(request.status, "value") else str(request.status)
            timestamp = canceled_at or _utc_now_rfc3339()
            request.status = ConsultRequestStatus.CANCELLED
            request.canceled_at = timestamp
            request.request_to_session_status = "canceled_before_session"
            request.session_handoff_note = "Request canceled by operator."
            store.put_request(request)
            store.append_audit(
                ConsultAuditEvent(
                    audit_id=f"aud-{uuid.uuid4().hex[:12]}",
                    request_id=request_id,
                    actor_ref=ConsultationActorRef(actor_type="operator", actor_id=actor_id),
                    action="request_cancelled",
                    before_state=before_state,
                    after_state=ConsultRequestStatus.CANCELLED.value,
                    trace_id=request.trace_id,
                )
            )
            return self._project_consult_request_detail(
                self._project_service_request_record(_model_to_data(request))
            )

        service_store_path = self._service._resolve_path("consult_requests")
        persist_service_store = service_store_path is not None
        requests: Optional[Dict[str, Any]]
        if persist_service_store:
            available, service_requests = self._service.list_records("consult_requests")
            if not available and service_store_path.exists():
                return None
            requests = {
                str(r.get("request_id") or r.get("id") or ""): json.loads(json.dumps(r))
                for r in service_requests
                if isinstance(r, dict)
                and str(r.get("request_id") or r.get("id") or "").strip()
            }
        else:
            local_key = self._LOCAL_DATA_KEYS.get("consult_requests", "consult_requests")
            local_payload = self._data.get(local_key)
            if isinstance(local_payload, dict):
                requests = json.loads(json.dumps(local_payload))
            else:
                requests = {}

        req = (requests or {}).get(request_id)
        if not req:
            return None
        if not self._consult_request_can_cancel(req):
            return None

        timestamp = canceled_at or _utc_now_rfc3339()
        req["status"] = "canceled"
        req["canceled_at"] = timestamp
        req["request_to_session_status"] = "canceled_before_session"
        req["session_handoff_note"] = "Request canceled by operator."

        if persist_service_store:
            self._service.write_records("consult_requests", requests)
        else:
            local_key = self._LOCAL_DATA_KEYS.get("consult_requests", "consult_requests")
            self._data.setdefault(local_key, {})[request_id] = req
            self._save()
        return self._project_consult_request_detail(req)

    # -------------------------------------------------------------------------
    # TW-02 Parameter Controls
    # -------------------------------------------------------------------------

    _TW02_CONTROL_TYPES = {"number", "integer", "enum", "boolean"}

    def _tw02_control_surface_state(self, *, has_record: bool) -> str:
        if not has_record:
            return "unavailable"
        source = self.dataset_source("trainer_controls")
        if source == "local_snapshot":
            return "degraded"
        if source == "missing":
            return "unavailable"
        return "ok"

    @staticmethod
    def _tw02_control_staleness(surface_state: str, as_of: str) -> Dict[str, Any]:
        return {
            "status": "stale" if surface_state == "degraded" else "fresh",
            "as_of": as_of,
        }

    def _tw02_validate_control_patch(
        self,
        ctrl: Dict[str, Any],
        value: Any,
    ) -> Optional[Dict[str, Any]]:
        allowed = ctrl.get("allowed_range") or {}
        ctrl_type = str(ctrl.get("control_type") or "number").strip().lower()
        allowed_range = json.loads(json.dumps(allowed)) if isinstance(allowed, dict) else None

        if ctrl_type not in self._TW02_CONTROL_TYPES:
            return {
                "field": ctrl.get("parameter_key"),
                "reason": "unsupported_control_type",
                "current_value": ctrl.get("current_value"),
                "requested_value": value,
                "allowed_range": allowed_range,
            }

        if value is None:
            return {
                "field": ctrl.get("parameter_key"),
                "reason": "invalid_control_type",
                "current_value": ctrl.get("current_value"),
                "requested_value": value,
                "allowed_range": allowed_range,
            }

        if ctrl_type == "boolean":
            if not isinstance(value, bool):
                return {
                    "field": ctrl.get("parameter_key"),
                    "reason": "invalid_control_type",
                    "current_value": ctrl.get("current_value"),
                    "requested_value": value,
                    "allowed_range": allowed_range,
                }
            return None

        if ctrl_type == "enum":
            allowed_values = allowed.get("allowed_values") or allowed.get("options") or allowed.get("values") or []
            if allowed_values and value not in allowed_values:
                return {
                    "field": ctrl.get("parameter_key"),
                    "reason": "invalid_enum_value",
                    "current_value": ctrl.get("current_value"),
                    "requested_value": value,
                    "allowed_range": {"allowed_values": list(allowed_values)},
                }
            return None

        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return {
                "field": ctrl.get("parameter_key"),
                "reason": "invalid_control_type",
                "current_value": ctrl.get("current_value"),
                "requested_value": value,
                "allowed_range": allowed_range,
            }

        if ctrl_type == "integer" and not numeric_value.is_integer():
            return {
                "field": ctrl.get("parameter_key"),
                "reason": "invalid_control_type",
                "current_value": ctrl.get("current_value"),
                "requested_value": value,
                "allowed_range": allowed_range,
            }

        range_min = allowed.get("min")
        range_max = allowed.get("max")
        out_of_range = False
        if range_min is not None and numeric_value < float(range_min):
            out_of_range = True
        if range_max is not None and numeric_value > float(range_max):
            out_of_range = True
        if out_of_range:
            return {
                "field": ctrl.get("parameter_key"),
                "reason": "exceeds_allowed_range",
                "current_value": ctrl.get("current_value"),
                "requested_value": value,
                "allowed_range": {
                    "min": range_min,
                    "max": range_max,
                },
            }

        return None

    def _trainer_control_records(self) -> Dict[str, Dict[str, Any]]:
        available, records = self._service.list_records("trainer_controls")
        if available:
            return {
                str(session_id): record
                for record in records
                if isinstance(record, dict)
                for session_id in [record.get("session_id") or record.get("id")]
                if session_id
            }
        return self._local_fallback("trainer_controls") or {}

    def get_trainer_controls(
        self,
        session_id: Optional[str],
        *,
        snapshot_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        session = self.get_trainer_session(session_id)
        if session is None:
            return None

        records = self._trainer_control_records()
        record = records.get(session_id)

        now = snapshot_at or _utc_now_rfc3339()
        status = str(session.get("status") or "").strip().lower()
        surface_state = self._tw02_control_surface_state(has_record=record is not None)
        controls = list(record.get("controls") or []) if record is not None else []
        can_patch = status == "active" and surface_state == "ok"

        return {
            "object_ref": {
                "type": "TrainerControlState",
                "id": session_id,
            },
            "session_id": session_id,
            "status": session.get("status"),
            "controls": controls,
            "allowedActions": {
                "canPatchControls": can_patch,
            },
            "meta": {
                "snapshot_at": now,
                "staleness": self._tw02_control_staleness(surface_state, now),
                "surfaces": {
                    "trainer_controls": {
                        "state": surface_state,
                    },
                },
            },
        }

    def patch_trainer_controls(
        self,
        session_id: str,
        patches: List[Dict[str, Any]],
        *,
        patched_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = patched_at or _utc_now_rfc3339()
        base_url = self._training_session_base_url()
        if base_url:
            available, payload = _http_json_post(
                base_url,
                f"/api/training/sessions/{session_id}/controls",
                body={"patches": patches, "patched_at": patched_at},
            )
            if not available or not isinstance(payload, dict):
                return None
            controls = list(payload.get("controls") or payload.get("current_controls") or [])
            if payload.get("status") == "rejected":
                return {
                    "session_id": session_id,
                    "status": "rejected",
                    "error_code": "CONTROL_PATCH_VALIDATION_FAILED",
                    "message": "Patch contains invalid control updates.",
                    "field_errors": list(payload.get("field_errors") or []),
                    "rejected_changes": [],
                    "current_controls": controls,
                    "allowedActions": {"canPatchControls": True},
                    "meta": {
                        "snapshot_at": now,
                        "staleness": self._tw02_control_staleness("ok", now),
                        "surfaces": {"trainer_controls": {"state": "ok"}},
                    },
                }
            updated_controls_diff = [
                {
                    "field": row.get("parameter_key"),
                    "before": row.get("previous_value"),
                    "after": row.get("new_value"),
                    "validation_status": "accepted",
                }
                for row in list(payload.get("patch_delta") or [])
                if isinstance(row, dict)
            ]
            return {
                "session_id": session_id,
                "status": "accepted",
                "message": "Patch applied successfully.",
                "warnings": [],
                "diff": {"updated_controls": updated_controls_diff},
                "current_controls": controls,
                "allowedActions": {"canPatchControls": True},
                "meta": {
                    "snapshot_at": now,
                    "staleness": self._tw02_control_staleness("ok", now),
                    "surfaces": {"trainer_controls": {"state": "ok"}},
                },
            }

        service_store_path = self._service._resolve_path("trainer_controls")
        persist_service_store = service_store_path is not None

        if persist_service_store:
            available, service_records = self._service.list_records("trainer_controls")
            if not available and service_store_path.exists():
                all_records: Dict[str, Any] = {}
            else:
                all_records = {
                    str(r.get("session_id") or r.get("id") or ""): json.loads(json.dumps(r))
                    for r in service_records
                    if isinstance(r, dict)
                    and str(r.get("session_id") or r.get("id") or "").strip()
                }
        else:
            local_key = self._LOCAL_DATA_KEYS.get("trainer_controls", "trainer_controls")
            local_payload = self._data.get(local_key)
            all_records = json.loads(json.dumps(local_payload)) if isinstance(local_payload, dict) else {}

        record = all_records.get(session_id) or {}
        controls = list(record.get("controls") or [])

        controls_by_key: Dict[str, Dict[str, Any]] = {
            str(c.get("parameter_key") or ""): c
            for c in controls
            if isinstance(c, dict) and c.get("parameter_key")
        }

        field_errors: List[Dict[str, Any]] = []
        valid_patches: List[Dict[str, Any]] = []

        for patch in patches:
            key = str(patch.get("parameter_key") or "").strip()
            value = patch.get("proposed_value")
            if not key:
                continue
            ctrl = controls_by_key.get(key)
            if ctrl is None:
                field_errors.append({
                    "field": key,
                    "reason": "unknown_parameter_key",
                    "current_value": None,
                    "requested_value": value,
                    "allowed_range": None,
                })
                continue

            validation_error = self._tw02_validate_control_patch(ctrl, value)
            if validation_error is not None:
                field_errors.append(validation_error)
                continue
            valid_patches.append({"key": key, "value": value})

        session = self.get_trainer_session(session_id)
        status = str((session or {}).get("status") or "").strip().lower()
        surface_state = self._tw02_control_surface_state(has_record=record is not None)
        can_patch = status == "active" and surface_state == "ok"

        if field_errors:
            return {
                "session_id": session_id,
                "status": "rejected",
                "error_code": "CONTROL_PATCH_VALIDATION_FAILED",
                "message": "Patch contains invalid control updates.",
                "field_errors": field_errors,
                "rejected_changes": [],
                "current_controls": controls,
                "allowedActions": {"canPatchControls": can_patch},
                "meta": {
                    "snapshot_at": now,
                    "staleness": self._tw02_control_staleness(surface_state, now),
                    "surfaces": {"trainer_controls": {"state": surface_state}},
                },
            }

        updated_controls_diff: List[Dict[str, Any]] = []
        for p in valid_patches:
            key = p["key"]
            value = p["value"]
            ctrl = controls_by_key[key]
            before = ctrl.get("current_value")
            ctrl["current_value"] = value
            ctrl["last_modified_at"] = now
            updated_controls_diff.append({
                "field": key,
                "before": before,
                "after": value,
                "validation_status": "accepted",
            })

        if record:
            record["controls"] = controls
        else:
            record = {"session_id": session_id, "controls": controls}
        all_records[session_id] = record

        if persist_service_store:
            self._service.write_records("trainer_controls", all_records)
        else:
            local_key = self._LOCAL_DATA_KEYS.get("trainer_controls", "trainer_controls")
            self._data[local_key] = all_records
            self._save()

        return {
            "session_id": session_id,
            "status": "accepted",
            "message": "Patch applied successfully.",
            "warnings": [],
            "diff": {"updated_controls": updated_controls_diff},
            "current_controls": controls,
            "allowedActions": {"canPatchControls": can_patch},
            "meta": {
                "snapshot_at": now,
                "staleness": self._tw02_control_staleness("ok", now),
                "surfaces": {"trainer_controls": {"state": "ok"}},
            },
        }

    # -------------------------------------------------------------------------
    # TW-04 Teaching Replay
    # -------------------------------------------------------------------------

    _TW04_REPLAY_SURFACE_STATES = {"ok", "stale", "degraded", "unavailable"}
    _TW04_REPLAY_RESOLUTION_STATES = {"pending_decision", "committed", "discarded", "not_applicable"}

    def _tw04_replay_records(self) -> Dict[str, Dict[str, Any]]:
        available, records = self._service.list_records("trainer_replays")
        if available:
            return {
                str(r.get("session_id") or r.get("id") or ""): json.loads(json.dumps(r))
                for r in records
                if isinstance(r, dict) and str(r.get("session_id") or r.get("id") or "").strip()
            }
        return json.loads(json.dumps(self._local_fallback("trainer_replays") or {}))

    def _mutable_tw04_replay_records(self) -> Optional[tuple[bool, Dict[str, Dict[str, Any]]]]:
        service_store_path = self._service._resolve_path("trainer_replays")
        if service_store_path is not None:
            available, service_records = self._service.list_records("trainer_replays")
            if not available and service_store_path.exists():
                return None
            records = {
                str(r.get("session_id") or r.get("id") or ""): json.loads(json.dumps(r))
                for r in service_records
                if isinstance(r, dict) and str(r.get("session_id") or r.get("id") or "").strip()
            }
            return True, records
        replays = self._local_fallback("trainer_replays")
        if replays is None:
            return None
        return False, json.loads(json.dumps(replays))

    @classmethod
    def _tw04_replay_surface_state(
        cls,
        *,
        has_data: bool,
        dataset_source: str = "service_store",
        stored_state_override: Optional[str] = None,
    ) -> str:
        if not has_data or dataset_source == "missing":
            return "unavailable"
        requested = str(stored_state_override or "ok").strip().lower()
        if requested not in cls._TW04_REPLAY_SURFACE_STATES:
            requested = "ok"
        if dataset_source == "local_snapshot" and requested == "ok":
            return "stale"
        return requested

    @staticmethod
    def _project_replay_teaching_event(raw: Dict[str, Any]) -> Dict[str, Any]:
        patch_delta = raw.get("patch_delta")
        if isinstance(patch_delta, list):
            patch_delta = [
                {
                    "parameter_key": row.get("parameter_key"),
                    "previous_value": row.get("previous_value"),
                    "new_value": row.get("new_value"),
                }
                for row in patch_delta
                if isinstance(row, dict)
            ]
        eval_ref = raw.get("eval_ref")
        if isinstance(eval_ref, dict):
            eval_ref = {
                "eval_id": eval_ref.get("eval_id"),
                "baseline_snapshot_at": eval_ref.get("baseline_snapshot_at"),
                "candidate_snapshot_at": eval_ref.get("candidate_snapshot_at"),
            }
        evidence_ref = raw.get("evidence_ref")
        if isinstance(evidence_ref, dict):
            evidence_ref = {
                "type": evidence_ref.get("type"),
                "id": evidence_ref.get("id"),
                "display_label": evidence_ref.get("display_label"),
                "url_pattern": evidence_ref.get("url_pattern"),
            }
        artifact_refs = raw.get("artifact_refs")
        if isinstance(artifact_refs, dict):
            artifact_refs = {
                "before_artifact_ref": artifact_refs.get("before_artifact_ref"),
                "candidate_artifact_ref": artifact_refs.get("candidate_artifact_ref"),
                "after_artifact_ref": artifact_refs.get("after_artifact_ref"),
            }
        return {
            "event_id": raw.get("event_id"),
            "session_id": raw.get("session_id"),
            "actor": raw.get("actor"),
            "actor_label": raw.get("actor_label"),
            "event_type": raw.get("event_type"),
            "message_body": raw.get("message_body"),
            "summary": raw.get("summary"),
            "emitted_at": raw.get("emitted_at"),
            "sequence_number": raw.get("sequence_number"),
            "outcome_signal": raw.get("outcome_signal"),
            "evidence_ref": evidence_ref,
            "patch_delta": patch_delta,
            "eval_ref": eval_ref,
            "artifact_refs": artifact_refs,
        }

    @staticmethod
    def _project_replay_resolution(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            return {
                "state": "not_applicable",
                "decision_at": None,
                "decision_by": None,
                "note": None,
            }
        return {
            "state": str(raw.get("state") or "not_applicable"),
            "decision_at": raw.get("decision_at"),
            "decision_by": raw.get("decision_by"),
            "note": raw.get("note"),
        }

    @staticmethod
    def _project_replay_artifacts(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            return {
                "before_artifact_ref": None,
                "candidate_artifact_ref": None,
                "after_artifact_ref": None,
            }
        return {
            "before_artifact_ref": raw.get("before_artifact_ref"),
            "candidate_artifact_ref": raw.get("candidate_artifact_ref"),
            "after_artifact_ref": raw.get("after_artifact_ref"),
        }

    @classmethod
    def _tw04_replay_allowed_actions(
        cls,
        *,
        session_status: Optional[str],
        resolution_state: str,
        surface_state: str,
        candidate_artifact_ref: Optional[str],
    ) -> Dict[str, bool]:
        status_ok = str(session_status or "").strip().lower() == "completed"
        resolution_ok = resolution_state == "pending_decision"
        surface_ok = surface_state not in {"degraded", "unavailable"}
        candidate_ok = bool(candidate_artifact_ref)
        can_act = status_ok and resolution_ok and surface_ok and candidate_ok
        return {
            "canReplay": surface_state != "unavailable",
            "canCommit": can_act,
            "canDiscard": can_act,
        }

    @classmethod
    def _project_trainer_replay_list_item(
        cls,
        session: Dict[str, Any],
        *,
        surface_state: str,
    ) -> Dict[str, Any]:
        session_id = str(session.get("session_id") or session.get("id") or "")
        events = sorted(
            [e for e in (session.get("events") or []) if isinstance(e, dict)],
            key=lambda e: int(e.get("sequence_number") or 0),
        )
        event_count = len(events)
        latest_event_type = events[-1].get("event_type") if events else None
        latest_outcome_signal = None
        for ev in events:
            if ev.get("outcome_signal"):
                latest_outcome_signal = ev.get("outcome_signal")

        resolution = cls._project_replay_resolution(session.get("replay_resolution"))
        artifacts = cls._project_replay_artifacts(session.get("artifacts"))
        allowed = cls._tw04_replay_allowed_actions(
            session_status=session.get("status"),
            resolution_state=resolution["state"],
            surface_state=surface_state,
            candidate_artifact_ref=artifacts.get("candidate_artifact_ref"),
        )
        return {
            "session_id": session_id,
            "persona_id": session.get("persona_id"),
            "objective": session.get("objective"),
            "status": session.get("status"),
            "started_at": session.get("started_at"),
            "ended_at": session.get("ended_at"),
            "event_count": event_count,
            "latest_event_type": latest_event_type,
            "latest_outcome_signal": latest_outcome_signal,
            "replay_resolution": {"state": resolution["state"]},
            "allowedActions": allowed,
            "links": {
                "replay_detail": f"/trainer/replay/{session_id}",
            },
        }

    @classmethod
    def _project_trainer_replay_detail(
        cls,
        session: Dict[str, Any],
        *,
        surface_state: str,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        session_id = str(session.get("session_id") or session.get("id") or "")
        snapshot_timestamp = snapshot_at or _utc_now_rfc3339()

        raw_events = sorted(
            [e for e in (session.get("events") or []) if isinstance(e, dict)],
            key=lambda e: int(e.get("sequence_number") or 0),
        )
        events = [cls._project_replay_teaching_event(e) for e in raw_events]
        event_count = len(events)
        first_seq = events[0]["sequence_number"] if events else None
        last_seq = events[-1]["sequence_number"] if events else None
        latest_outcome_signal = None
        for ev in events:
            if ev.get("outcome_signal"):
                latest_outcome_signal = ev["outcome_signal"]

        resolution = cls._project_replay_resolution(session.get("replay_resolution"))
        artifacts = cls._project_replay_artifacts(session.get("artifacts"))
        allowed = cls._tw04_replay_allowed_actions(
            session_status=session.get("status"),
            resolution_state=resolution["state"],
            surface_state=surface_state,
            candidate_artifact_ref=artifacts.get("candidate_artifact_ref"),
        )
        return {
            "session_id": session_id,
            "persona_id": session.get("persona_id"),
            "objective": session.get("objective"),
            "status": session.get("status"),
            "started_at": session.get("started_at"),
            "ended_at": session.get("ended_at"),
            "replay_resolution": resolution,
            "artifacts": artifacts,
            "event_summary": {
                "event_count": event_count,
                "first_sequence_number": first_seq,
                "last_sequence_number": last_seq,
                "latest_outcome_signal": latest_outcome_signal,
            },
            "events": events,
            "allowedActions": allowed,
            "links": {
                "self": f"/trainer/replay/{session_id}",
                "session_detail": f"/trainer/sessions/{session_id}",
            },
            "meta": {
                "snapshot_at": snapshot_timestamp,
                "surfaces": {
                    "trainer_replay": surface_state,
                },
            },
        }

    def list_trainer_replays(
        self,
        *,
        persona_id: Optional[str],
        status: Optional[str] = None,
        snapshot_at: Optional[str] = None,
    ) -> tuple[List[Dict[str, Any]], str]:
        records = self._tw04_replay_records()
        _surface_severity = {"unavailable": 4, "degraded": 3, "stale": 2, "ok": 1}
        worst_stored_override: Optional[str] = None
        for r in records.values():
            if not isinstance(r, dict):
                continue
            stored = str(
                ((r.get("meta") or {}).get("surfaces") or {}).get("trainer_replay") or ""
            ).strip().lower()
            if stored in _surface_severity:
                if (
                    worst_stored_override is None
                    or _surface_severity[stored] > _surface_severity.get(worst_stored_override, 0)
                ):
                    worst_stored_override = stored
        surface_state = self._tw04_replay_surface_state(
            has_data=bool(records),
            dataset_source=self.dataset_source("trainer_replays"),
            stored_state_override=worst_stored_override,
        )
        items = [
            self._project_trainer_replay_list_item(session, surface_state=surface_state)
            for session in records.values()
            if isinstance(session, dict)
            and str(session.get("persona_id") or "") == str(persona_id or "")
        ]
        if status is not None:
            normalized = str(status).strip().lower()
            items = [item for item in items if str(item.get("status") or "").strip().lower() == normalized]
        items.sort(
            key=lambda item: (
                (_parse_rfc3339(item.get("ended_at")) or datetime.min).replace(tzinfo=None)
            ),
            reverse=True,
        )
        return items, surface_state

    def get_trainer_replay(
        self,
        session_id: Optional[str],
        *,
        snapshot_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        snapshot_timestamp = snapshot_at or _utc_now_rfc3339()
        available, raw = self._service.record("trainer_replays", session_id)
        if available:
            if raw is None:
                return None
            session = dict(raw)
        else:
            replays = self._local_fallback("trainer_replays") or {}
            session = replays.get(session_id)
            if session is None:
                return None
        stored_meta_surfaces = ((session.get("meta") or {}).get("surfaces") or {})
        surface_state = self._tw04_replay_surface_state(
            has_data=True,
            dataset_source=self.dataset_source("trainer_replays"),
            stored_state_override=stored_meta_surfaces.get("trainer_replay"),
        )
        return self._project_trainer_replay_detail(
            session,
            surface_state=surface_state,
            snapshot_at=snapshot_timestamp,
        )

    def commit_trainer_replay(
        self,
        session_id: str,
        *,
        expected_candidate_snapshot_at: str,
        note: Optional[str],
        actor_id: str,
        committed_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        base_url = self._training_session_base_url()
        if base_url:
            available, payload = _http_json_post(
                base_url,
                f"/api/training/replays/{session_id}/commit",
                body={
                    "expected_candidate_snapshot_at": expected_candidate_snapshot_at,
                    "note": note,
                    "actor_id": actor_id,
                    "decided_at": committed_at,
                },
            )
            if not available or not isinstance(payload, dict):
                return None
            surface_state = self._tw04_replay_surface_state(
                has_data=True,
                dataset_source=self.dataset_source("trainer_replays"),
            )
            projected = self._project_trainer_replay_detail(payload, surface_state=surface_state)
            resolution = projected["replay_resolution"]
            events = projected.get("events") or []
            return {
                "session_id": session_id,
                "status": projected.get("status"),
                "replay_resolution": resolution,
                "artifacts": projected.get("artifacts"),
                "committed_at": resolution.get("decision_at"),
                "committed_by": resolution.get("decision_by"),
                "event": events[-1] if events else None,
                "allowedActions": projected.get("allowedActions"),
                "meta": projected.get("meta"),
            }

        mutable = self._mutable_tw04_replay_records()
        if mutable is None:
            return None
        persist_service, records = mutable

        session = records.get(session_id)
        if session is None:
            return None

        timestamp = committed_at or _utc_now_rfc3339()
        resolution = session.setdefault("replay_resolution", {})
        resolution["state"] = "committed"
        resolution["decision_at"] = timestamp
        resolution["decision_by"] = actor_id
        resolution["note"] = note

        artifacts = session.setdefault("artifacts", {})
        after_ref = f"{session_id}-committed-artifact"
        artifacts["after_artifact_ref"] = after_ref

        events = session.setdefault("events", [])
        next_seq = max((int(e.get("sequence_number") or 0) for e in events), default=0) + 1
        prefix = timestamp[:10].replace("-", "")
        event_id = f"tevt-{prefix}-{next_seq:03d}"
        existing_ids = {str(e.get("event_id") or "") for e in events}
        dedupe = next_seq
        while event_id in existing_ids:
            dedupe += 1
            event_id = f"tevt-{prefix}-{dedupe:03d}"

        commit_event = {
            "event_id": event_id,
            "session_id": session_id,
            "actor": "system",
            "actor_label": "System",
            "event_type": "commit",
            "message_body": None,
            "summary": f"Candidate committed by {actor_id}.",
            "emitted_at": timestamp,
            "sequence_number": next_seq,
            "outcome_signal": None,
            "evidence_ref": None,
            "patch_delta": None,
            "eval_ref": None,
            "artifact_refs": {
                "before_artifact_ref": artifacts.get("before_artifact_ref"),
                "candidate_artifact_ref": artifacts.get("candidate_artifact_ref"),
                "after_artifact_ref": after_ref,
            },
        }
        events.append(commit_event)

        if persist_service:
            self._service.write_records("trainer_replays", records)
        else:
            local_key = self._LOCAL_DATA_KEYS.get("trainer_replays", "trainer_replays")
            self._data.setdefault(local_key, {})[session_id] = session
            self._save()

        surface_state = self._tw04_replay_surface_state(
            has_data=True,
            dataset_source=self.dataset_source("trainer_replays"),
        )
        projected_resolution = self._project_replay_resolution(resolution)
        projected_artifacts = self._project_replay_artifacts(artifacts)
        allowed = self._tw04_replay_allowed_actions(
            session_status=session.get("status"),
            resolution_state=projected_resolution["state"],
            surface_state=surface_state,
            candidate_artifact_ref=projected_artifacts.get("candidate_artifact_ref"),
        )
        return {
            "session_id": session_id,
            "status": session.get("status"),
            "replay_resolution": projected_resolution,
            "artifacts": projected_artifacts,
            "committed_at": timestamp,
            "committed_by": actor_id,
            "event": self._project_replay_teaching_event(commit_event),
            "allowedActions": allowed,
            "meta": {
                "snapshot_at": timestamp,
                "surfaces": {"trainer_replay": surface_state},
            },
        }

    def discard_trainer_replay(
        self,
        session_id: str,
        *,
        expected_candidate_snapshot_at: str,
        note: Optional[str],
        actor_id: str,
        discarded_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        base_url = self._training_session_base_url()
        if base_url:
            available, payload = _http_json_post(
                base_url,
                f"/api/training/replays/{session_id}/discard",
                body={
                    "expected_candidate_snapshot_at": expected_candidate_snapshot_at,
                    "note": note,
                    "actor_id": actor_id,
                    "decided_at": discarded_at,
                },
            )
            if not available or not isinstance(payload, dict):
                return None
            surface_state = self._tw04_replay_surface_state(
                has_data=True,
                dataset_source=self.dataset_source("trainer_replays"),
            )
            projected = self._project_trainer_replay_detail(payload, surface_state=surface_state)
            resolution = projected["replay_resolution"]
            events = projected.get("events") or []
            return {
                "session_id": session_id,
                "status": projected.get("status"),
                "replay_resolution": resolution,
                "artifacts": projected.get("artifacts"),
                "discarded_at": resolution.get("decision_at"),
                "discarded_by": resolution.get("decision_by"),
                "event": events[-1] if events else None,
                "allowedActions": projected.get("allowedActions"),
                "meta": projected.get("meta"),
            }

        mutable = self._mutable_tw04_replay_records()
        if mutable is None:
            return None
        persist_service, records = mutable

        session = records.get(session_id)
        if session is None:
            return None

        timestamp = discarded_at or _utc_now_rfc3339()
        resolution = session.setdefault("replay_resolution", {})
        resolution["state"] = "discarded"
        resolution["decision_at"] = timestamp
        resolution["decision_by"] = actor_id
        resolution["note"] = note

        artifacts = session.setdefault("artifacts", {})

        events = session.setdefault("events", [])
        next_seq = max((int(e.get("sequence_number") or 0) for e in events), default=0) + 1
        prefix = timestamp[:10].replace("-", "")
        event_id = f"tevt-{prefix}-{next_seq:03d}"
        existing_ids = {str(e.get("event_id") or "") for e in events}
        dedupe = next_seq
        while event_id in existing_ids:
            dedupe += 1
            event_id = f"tevt-{prefix}-{dedupe:03d}"

        discard_event = {
            "event_id": event_id,
            "session_id": session_id,
            "actor": "system",
            "actor_label": "System",
            "event_type": "discard",
            "message_body": None,
            "summary": f"Candidate discarded by {actor_id}.",
            "emitted_at": timestamp,
            "sequence_number": next_seq,
            "outcome_signal": None,
            "evidence_ref": None,
            "patch_delta": None,
            "eval_ref": None,
            "artifact_refs": {
                "before_artifact_ref": artifacts.get("before_artifact_ref"),
                "candidate_artifact_ref": artifacts.get("candidate_artifact_ref"),
                "after_artifact_ref": None,
            },
        }
        events.append(discard_event)

        if persist_service:
            self._service.write_records("trainer_replays", records)
        else:
            local_key = self._LOCAL_DATA_KEYS.get("trainer_replays", "trainer_replays")
            self._data.setdefault(local_key, {})[session_id] = session
            self._save()

        surface_state = self._tw04_replay_surface_state(
            has_data=True,
            dataset_source=self.dataset_source("trainer_replays"),
        )
        projected_resolution = self._project_replay_resolution(resolution)
        projected_artifacts = self._project_replay_artifacts(artifacts)
        allowed = self._tw04_replay_allowed_actions(
            session_status=session.get("status"),
            resolution_state=projected_resolution["state"],
            surface_state=surface_state,
            candidate_artifact_ref=projected_artifacts.get("candidate_artifact_ref"),
        )
        return {
            "session_id": session_id,
            "status": session.get("status"),
            "replay_resolution": projected_resolution,
            "artifacts": projected_artifacts,
            "discarded_at": timestamp,
            "discarded_by": actor_id,
            "event": self._project_replay_teaching_event(discard_event),
            "allowedActions": allowed,
            "meta": {
                "snapshot_at": timestamp,
                "surfaces": {"trainer_replay": surface_state},
            },
        }

    # ------------------------------------------------------------------ #
    # TRN-003: rapid-eval store
    # ------------------------------------------------------------------ #

    def _rapid_eval_store_path(self) -> Optional[Path]:
        raw = os.getenv("PANTHEON_BFF_RAPID_EVAL_STORE", "").strip()
        return Path(raw) if raw else None

    def _load_rapid_evals(self) -> Dict[str, Any]:
        path = self._rapid_eval_store_path()
        if path is None or not path.exists():
            return {}
        text = path.read_text(encoding="utf-8").strip()
        return json.loads(text) if text else {}

    def _save_rapid_evals(self, records: Dict[str, Any]) -> None:
        path = self._rapid_eval_store_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, indent=2, ensure_ascii=True))

    def create_rapid_eval(
        self,
        session_id: str,
        *,
        persona_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
        eval_scope: str,
        patch_ref: Optional[str] = None,
        dataset_version_id: str,
        max_runtime_seconds: int,
        requested_by: str,
        requested_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if self._rapid_eval_store_path() is None:
            return None
        records = self._load_rapid_evals()
        timestamp = requested_at or _utc_now_rfc3339()
        existing_ids = set(records.keys())
        date_prefix = timestamp[:10].replace("-", "")
        index = len(existing_ids) + 1
        eval_id = f"reval-{date_prefix}-{index:03d}"
        while eval_id in existing_ids:
            index += 1
            eval_id = f"reval-{date_prefix}-{index:03d}"
        record: Dict[str, Any] = {
            "rapid_eval_id": eval_id,
            "session_id": session_id,
            "status": "queued",
            "eval_scope": eval_scope,
            "dataset_version_id": dataset_version_id,
            "max_runtime_seconds": max_runtime_seconds,
            "patch_ref": patch_ref,
            "persona_id": persona_id,
            "strategy_id": strategy_id,
            "requested_by": requested_by,
            "requested_at": timestamp,
            "completed_at": None,
            "advisory_note": (
                "Rapid eval queued for bounded execution. "
                "Results will be available once the eval completes."
            ),
            "meta": {
                "snapshot_at": timestamp,
                "surfaces": {"rapid_eval": "ok"},
            },
        }
        records[eval_id] = record
        self._save_rapid_evals(records)
        return json.loads(json.dumps(record))

    def get_rapid_eval(
        self,
        eval_id: Optional[str],
        *,
        snapshot_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not eval_id:
            return None
        records = self._load_rapid_evals()
        record = records.get(str(eval_id))
        if record is None:
            return None
        return json.loads(json.dumps(record))

    # --- governance sub-rules read surfaces (BFFGAP-GOVRULES) ---

    def list_governance_permissions(self) -> List[Dict[str, Any]]:
        raw = self._local_fallback("governance_permissions")
        if isinstance(raw, dict):
            return [json.loads(json.dumps(v)) for v in raw.values() if isinstance(v, dict)]
        if isinstance(raw, list):
            return [json.loads(json.dumps(v)) for v in raw if isinstance(v, dict)]
        return []

    def list_memory_governance_rules(self) -> List[Dict[str, Any]]:
        raw = self._local_fallback("memory_governance_rules")
        if isinstance(raw, dict):
            return [json.loads(json.dumps(v)) for v in raw.values() if isinstance(v, dict)]
        if isinstance(raw, list):
            return [json.loads(json.dumps(v)) for v in raw if isinstance(v, dict)]
        return []

    def list_consult_rules(self) -> List[Dict[str, Any]]:
        raw = self._local_fallback("consult_rules")
        if isinstance(raw, dict):
            return [json.loads(json.dumps(v)) for v in raw.values() if isinstance(v, dict)]
        if isinstance(raw, list):
            return [json.loads(json.dumps(v)) for v in raw if isinstance(v, dict)]
        return []

    def list_route_policies(self) -> List[Dict[str, Any]]:
        available, service_records = self._service.list_records("route_policies")
        if available and service_records:
            return [
                json.loads(json.dumps(record))
                for record in service_records
                if isinstance(record, dict)
            ]
        raw = self._local_fallback("route_policies")
        if isinstance(raw, dict):
            return [json.loads(json.dumps(v)) for v in raw.values() if isinstance(v, dict)]
        if isinstance(raw, list):
            return [json.loads(json.dumps(v)) for v in raw if isinstance(v, dict)]
        return []

    def list_alpha_factory_cards(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        lane: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return []

    def list_skills(self) -> List[Dict[str, Any]]:
        return list(self._read_dataset_records("skills"))

    def list_tools(self) -> List[Dict[str, Any]]:
        return list(self._read_dataset_records("tools"))

    def list_mcp_servers(self) -> List[Dict[str, Any]]:
        return list(self._read_dataset_records("mcp_servers"))

    def list_mcp_tools(self) -> List[Dict[str, Any]]:
        return list(self._read_dataset_records("mcp_tools"))

    # --- Management Read Models (PFG-MGMT-READ-MODELS-20260820) ---

    def get_formula_jobs_read_model(
        self,
        *,
        status: Optional[str] = None,
        formula_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        available, service_records = self._service.list_records("formula_jobs")
        source = "service" if available else "event_store"
        raw_items = service_records if available else list(self._read_dataset_records("formula_jobs"))

        filtered = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            if status and item.get("status") != status:
                continue
            if formula_id and item.get("formula_id") != formula_id:
                continue
            item_copy = json.loads(json.dumps(item))
            if "source_identity" not in item_copy:
                item_copy["source_identity"] = "formula_job_executor"
            if "freshness" not in item_copy:
                item_copy["freshness"] = item_copy.get("submitted_at") or _utc_now_rfc3339()
            filtered.append(item_copy)

        return {
            "source": source if (available or filtered) else "missing",
            "items": filtered,
        }

    def get_activity_read_model(
        self,
        *,
        event_type: Optional[str] = None,
        actor_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        available, service_records = self._service.list_records("activity_audit")
        source = "audit" if available else "event_store"
        raw_items = service_records if available else list(self._read_dataset_records("activity_audit"))

        filtered = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            if event_type and item.get("event_type") != event_type:
                continue
            if actor_id and item.get("actor_id") != actor_id:
                continue
            item_copy = json.loads(json.dumps(item))
            if "source_identity" not in item_copy:
                item_copy["source_identity"] = "activity_audit_store"
            if "freshness" not in item_copy:
                item_copy["freshness"] = item_copy.get("timestamp") or _utc_now_rfc3339()
            filtered.append(item_copy)

        return {
            "source": source if (available or filtered) else "missing",
            "items": filtered,
        }

    def get_paper_telemetry_read_model(
        self,
        *,
        strategy_id: Optional[str] = None,
        persona_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        available, service_records = self._service.list_records("paper_telemetry")
        source = "service" if available else "store"
        raw_items = service_records if available else list(self._read_dataset_records("paper_telemetry"))

        filtered = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            if strategy_id and item.get("strategy_id") != strategy_id:
                continue
            if persona_id and item.get("persona_id") != persona_id:
                continue
            item_copy = json.loads(json.dumps(item))
            if "source_identity" not in item_copy:
                item_copy["source_identity"] = "paper_telemetry_store"
            if "freshness" not in item_copy:
                item_copy["freshness"] = item_copy.get("last_signal_at") or _utc_now_rfc3339()
            filtered.append(item_copy)

        return {
            "source": source if (available or filtered) else "missing",
            "items": filtered,
        }

    def get_postmortems_read_model(
        self,
        *,
        severity: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        available, service_records = self._service.list_records("postmortems")
        source = "store" if available else "event_store"
        raw_items = service_records if available else list(self._read_dataset_records("postmortems"))

        filtered = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            item_copy = json.loads(json.dumps(item))
            # Normalize schema differences from canonical postmortem records if present
            if "impact_summary" not in item_copy and "incident_evidence_summary" in item_copy:
                item_copy["impact_summary"] = item_copy.get("incident_evidence_summary")
            if "severity" not in item_copy:
                # Do not relabel deployment_stage as severity; default to real severity field or "medium"
                item_copy["severity"] = item_copy.get("severity") or "medium"
            if "action_items" in item_copy and isinstance(item_copy["action_items"], list):
                # Ensure action_items elements are dicts if they were strings
                norm_actions = []
                for idx, act in enumerate(item_copy["action_items"]):
                    if isinstance(act, str):
                        norm_actions.append({"id": f"act-{idx+1}", "desc": act})
                    elif isinstance(act, dict):
                        norm_actions.append(act)
                item_copy["action_items"] = norm_actions

            if severity and item_copy.get("severity") != severity:
                continue
            if status and item_copy.get("status") != status:
                continue

            if "source_identity" not in item_copy:
                item_copy["source_identity"] = "postmortem_store"
            if "freshness" not in item_copy:
                item_copy["freshness"] = item_copy.get("created_at") or _utc_now_rfc3339()
            filtered.append(item_copy)

        return {
            "source": source if (available or filtered) else "missing",
            "items": filtered,
        }

    def get_postmortem_detail_read_model(
        self,
        postmortem_id: str,
    ) -> Dict[str, Any]:
        res = self.get_postmortems_read_model()
        items = res.get("items") or []
        for item in items:
            if item.get("postmortem_id") == postmortem_id:
                return {
                    "source": res.get("source") or "store",
                    "item": item,
                }
        return {
            "source": res.get("source") or "missing",
            "item": None,
        }
