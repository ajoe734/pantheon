"""Institutional knowledge reader and Trading Room system-suggestions selector (Design §12.7)."""
from __future__ import annotations

import copy, hashlib, json, os, urllib.error, urllib.parse, urllib.request
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

_REJECTED_OR_LUCK_TOKENS = frozenset({"luck_or_unsupported", "unsupported", "rejected", "failed", "invalidated", "quarantined", "expired", "confounded", "insufficient_sample", "no_testable_hypothesis"})
_UNVALIDATED_PENDING_TOKENS = frozenset({"unvalidated", "not_validated", "pending", "pending_review", "proposed", "draft", "queued", "running", "dispatching", "in_progress", "untested", "mechanism_untested"})
_POSITIVE_VALIDATION_TOKENS = frozenset({"validated", "passed", "approved", "admitted", "endorsed", "merged", "confirmed", "supported", "completed", "satisfied"})
_ENDORSED_LESSON_STATES = frozenset({"endorsed", "merged"})
_COMPONENT_TO_ACTIONS: Mapping[str, Tuple[str, ...]] = {"selection": ("entry", "enter", "add", "review"), "entry_timing": ("entry", "enter", "add"), "sizing": ("entry", "enter", "add", "reduce"), "exit": ("exit", "reduce", "review"), "costs": ("entry", "enter", "add", "reduce", "exit")}


class InstitutionalKnowledgeUnavailableError(RuntimeError):
    """Raised when an institutional-knowledge owner is unconfigured, unreachable, or invalid."""

    def __init__(self, owner: str, reason: str, status_code: int = 503) -> None:
        super().__init__(f"{owner} unavailable: {reason}")
        self.owner, self.reason, self.status_code = owner, reason, status_code

    def to_detail(self) -> Dict[str, Any]:
        return {"status": "unavailable", "owner": self.owner, "reason": self.reason, "fallback_used": False, "environment_ceiling": "paper", "execution_authority": "none"}


def _clean_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _string_list(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        raw = [part.strip() for part in value.replace(",", " ").split() if part.strip()]
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, Mapping)):
        raw = [_clean_str(item) for item in value if _clean_str(item)]
    else:
        raw = [_clean_str(value)] if _clean_str(value) else []
    return list(dict.fromkeys(item for item in raw if item))


def _normalize_symbol(symbol: Any) -> str:
    return _clean_str(symbol).upper()


def _extract_symbols_from_mapping(record: Mapping[str, Any]) -> List[str]:
    symbols = [_normalize_symbol(i.removeprefix("symbol:").removeprefix("instrument:")) for k in ("symbol", "symbols", "instrument", "instruments", "underlying", "ticker") for i in _string_list(record.get(k)) if _normalize_symbol(i.removeprefix("symbol:").removeprefix("instrument:"))]
    subj = record.get("subject")
    if isinstance(subj, Mapping):
        for k in ("symbol", "symbols", "instrument", "id"):
            symbols.extend(_normalize_symbol(i.removeprefix("symbol:")) for i in _string_list(subj.get(k)) if ("." in i or i.isupper() or k in ("symbol", "symbols", "instrument")) and _normalize_symbol(i.removeprefix("symbol:")))
    for k in ("subject_anchors", "tags"):
        symbols.extend(_normalize_symbol(a.split(":", 1)[1]) for a in _string_list(record.get(k)) if a.lower().startswith(("symbol:", "instrument:", "ticker:", "underlying:")) and _normalize_symbol(a.split(":", 1)[1]))
    for nk in ("metrics", "parameters", "payload", "metadata", "structured_payload", "artifact"):
        if isinstance(record.get(nk), Mapping):
            symbols.extend(_extract_symbols_from_mapping(record[nk]))
    return list(dict.fromkeys(symbols))


def _extract_strategy_ids(record: Mapping[str, Any]) -> List[str]:
    ids = [i.removeprefix("strategy:") for k in ("strategy_id", "strategy_ids", "strategy_spec_id", "strategy_spec_registry_id", "strategy_ref") for i in _string_list(record.get(k)) if i.removeprefix("strategy:")]
    for k in ("subject_anchors", "tags"):
        ids.extend(a.split(":", 1)[1].strip() for a in _string_list(record.get(k)) if a.lower().startswith("strategy:") and a.split(":", 1)[1].strip())
    for nk in ("metrics", "payload", "metadata", "structured_payload", "artifact"):
        if isinstance(record.get(nk), Mapping):
            ids.extend(_extract_strategy_ids(record[nk]))
    return list(dict.fromkeys(ids))


def _extract_strategy_families(record: Mapping[str, Any]) -> List[str]:
    families = [i.removeprefix("family:").removeprefix("strategy_family:") for k in ("strategy_family", "strategy_families", "tried_families", "family", "family_id") for i in _string_list(record.get(k)) if i.removeprefix("family:").removeprefix("strategy_family:")]
    if _clean_str(record.get("scope")) == "strategy_family" and _clean_str(record.get("scope_filter")):
        families.append(_clean_str(record.get("scope_filter")))
    for k in ("subject_anchors", "tags"):
        families.extend(a.split(":", 1)[1].strip() for a in _string_list(record.get(k)) if a.lower().startswith(("family:", "strategy_family:")) and a.split(":", 1)[1].strip())
    for nk in ("metrics", "payload", "metadata", "structured_payload", "artifact", "template"):
        if isinstance(record.get(nk), Mapping):
            families.extend(_extract_strategy_families(record[nk]))
    return list(dict.fromkeys(families))


def _extract_actions(record: Mapping[str, Any], component: Optional[str] = None) -> List[str]:
    actions = [item.lower() for key in ("action", "actions", "event_kind", "suggested_action", "decision_kind") for item in _string_list(record.get(key)) if item]
    for nested_key in ("payload", "metadata", "structured_payload"):
        nested = record.get(nested_key)
        if isinstance(nested, Mapping):
            actions.extend(_extract_actions(nested))
    comp = _clean_str(component or record.get("component")).lower()
    actions.extend(_COMPONENT_TO_ACTIONS.get(comp, ()))
    return list(dict.fromkeys(actions))


def _has_luck_or_rejected_marker(record: Mapping[str, Any]) -> bool:
    if record.get("luck_or_unsupported") is True or record.get("is_luck") is True or record.get("validated") is False:
        return True
    for key in ("validation_status", "status", "outcome", "verdict", "review_state", "queue_status", "admission_status", "hypothesis_status", "result_status", "mechanism_status"):
        if _clean_str(record.get(key)).lower() in _REJECTED_OR_LUCK_TOKENS:
            return True
    if any(tag.lower() in _REJECTED_OR_LUCK_TOKENS for tag in _string_list(record.get("tags"))):
        return True
    luck = record.get("luck_interval") or record.get("luck_check")
    if isinstance(luck, Mapping) and (luck.get("luck_or_unsupported") is True or luck.get("is_luck") is True or _clean_str(luck.get("status") or luck.get("verdict") or luck.get("outcome")).lower() in _REJECTED_OR_LUCK_TOKENS):
        return True
    gate = record.get("gate")
    if isinstance(gate, Mapping) and (gate.get("passed") is False or _clean_str(gate.get("status") or gate.get("admission_status")).lower() in _REJECTED_OR_LUCK_TOKENS):
        return True
    return any(isinstance(record.get(k), Mapping) and _has_luck_or_rejected_marker(record[k]) for k in ("validation_result", "validation", "payload", "metadata", "structured_payload", "receipt"))


def _is_positively_validated_research_record(record: Mapping[str, Any]) -> bool:
    if _has_luck_or_rejected_marker(record):
        return False
    val_res = record.get("validation_result") or record.get("validation")
    if isinstance(val_res, Mapping):
        if _has_luck_or_rejected_marker(val_res):
            return False
        if val_res.get("passed") is True or val_res.get("validated") is True or _clean_str(val_res.get("validation_status") or val_res.get("status") or val_res.get("verdict") or val_res.get("outcome")).lower() in _POSITIVE_VALIDATION_TOKENS:
            return True
    for key in ("validation_status", "verdict", "outcome", "hypothesis_status"):
        val = _clean_str(record.get(key)).lower()
        if val in _UNVALIDATED_PENDING_TOKENS:
            return False
        if val in _POSITIVE_VALIDATION_TOKENS:
            return True
    if record.get("validated") is True or record.get("replication_success") is True:
        return True
    adm_status = _clean_str(record.get("admission_status") or record.get("status")).lower()
    queue_status = _clean_str(record.get("queue_status")).lower()
    if queue_status:
        return queue_status not in (_UNVALIDATED_PENDING_TOKENS | _REJECTED_OR_LUCK_TOKENS) and adm_status in {"admitted", "approved", "validated", "passed"} and queue_status in _POSITIVE_VALIDATION_TOKENS
    if adm_status in {"validated", "passed", "approved", "endorsed", "confirmed"} or (adm_status == "satisfied" and (_clean_str(record.get("satisfied_by")) or _clean_str(record.get("validation_result_id")) or isinstance(val_res, Mapping))):
        return True
    discovery = record.get("discovery")
    if isinstance(discovery, Mapping):
        attempts = (discovery.get("template_search") or {}).get("attempts") if isinstance(discovery.get("template_search"), Mapping) else None
        if isinstance(attempts, Mapping):
            for receipt in attempts.values():
                if isinstance(receipt, Mapping) and not _has_luck_or_rejected_marker(receipt):
                    gate = receipt.get("gate") if isinstance(receipt.get("gate"), Mapping) else {}
                    reg = receipt.get("registration") if isinstance(receipt.get("registration"), Mapping) else {}
                    if _clean_str(receipt.get("status")).lower() in {"admitted", "approved", "validated", "passed"} and gate.get("passed") is not False and (not reg or _clean_str(reg.get("status")).lower() in {"approved", "admitted", "validated"}):
                        return True
        if isinstance(discovery.get("actions"), list):
            if any(isinstance(a, Mapping) and not _has_luck_or_rejected_marker(a) and _clean_str(a.get("status")).lower() in {"validated", "passed", "approved", "admitted", "completed"} for a in discovery["actions"]):
                return True
    return False


def _extract_validation_id_and_link(record: Mapping[str, Any], *, tenant_id: str) -> Tuple[str, str, Dict[str, Any]]:
    val_res = record.get("validation_result") or record.get("validation")
    val_map = dict(val_res) if isinstance(val_res, Mapping) else {}
    discovery = record.get("discovery")
    passed_attempt: Dict[str, Any] = {}
    if isinstance(discovery, Mapping) and isinstance((discovery.get("template_search") or {}).get("attempts") if isinstance(discovery.get("template_search"), Mapping) else None, Mapping):
        for aid, receipt in discovery["template_search"]["attempts"].items():
            if isinstance(receipt, Mapping) and not _has_luck_or_rejected_marker(receipt) and _clean_str(receipt.get("status")).lower() in {"admitted", "approved", "validated", "passed"}:
                passed_attempt = {"attempt_id": str(aid), **dict(receipt)}
                break
    val_id = (
        _clean_str(val_map.get("validation_result_id") or val_map.get("validation_id") or val_map.get("run_id"))
        or _clean_str(record.get("validation_result_id") or record.get("validation_id") or record.get("admission_id"))
        or _clean_str(passed_attempt.get("registry_id") or passed_attempt.get("attempt_id"))
        or _clean_str(record.get("satisfied_by") or record.get("demand_id") or record.get("strategy_spec_id") or record.get("run_id") or record.get("id"))
    )
    spec_id = _clean_str(record.get("strategy_spec_id") or val_map.get("strategy_spec_id"))
    demand_id = _clean_str(record.get("demand_id") or record.get("demand_ref") or val_map.get("demand_id"))
    exp_runs = record.get("experiment_run_ids") or record.get("authority_run_ids")
    run_id = _clean_str(val_map.get("run_id") or record.get("run_id") or (exp_runs[0] if isinstance(exp_runs, list) and exp_runs else ""))
    explicit_link = _clean_str(val_map.get("link") or val_map.get("href") or ((record.get("links") or {}).get("validation_result") if isinstance(record.get("links"), Mapping) else None))
    if explicit_link:
        link = explicit_link
    elif spec_id:
        link = f"/api/research-orchestrator/alpha-replication/admissions/{urllib.parse.quote(tenant_id, safe='')}/{urllib.parse.quote(spec_id, safe='')}"
    elif demand_id:
        link = f"/api/research/persona-demands?demand_id={urllib.parse.quote(demand_id, safe='')}"
    elif run_id:
        link = f"/api/research-orchestrator/runs/{urllib.parse.quote(run_id, safe='')}"
    else:
        link = f"/api/research/persona-demands?validation_id={urllib.parse.quote(val_id or 'unknown', safe='')}"
    metrics = {k: (val_map[k] if val_map.get(k) is not None else record[k]) for k in ("sharpe_ratio", "oos_sharpe", "p_value", "t_stat", "sample_size", "effect_bps", "mean_return_bps", "metrics") if val_map.get(k) is not None or record.get(k) is not None}
    if passed_attempt.get("gate"):
        metrics["gate"] = passed_attempt["gate"]
    return val_id or "val-unknown", link, metrics


class InstitutionalKnowledgeReader:
    """Read-only adapter that fetches and validates institutional facts from their owners."""

    def __init__(
        self, *, evaluation_base_url: Optional[str] = None, evaluation_service_token: Optional[str] = None,
        research_base_url: Optional[str] = None, research_service_token: Optional[str] = None,
        memory_base_url: Optional[str] = None, memory_service_token: Optional[str] = None,
        timeout_seconds: Optional[float] = None, urlopen: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._evaluation_base_url, self._evaluation_service_token = evaluation_base_url, evaluation_service_token
        self._research_base_url, self._research_service_token = research_base_url, research_service_token
        self._memory_base_url, self._memory_service_token = memory_base_url, memory_service_token
        self._timeout_seconds, self._urlopen = timeout_seconds, urlopen

    def _timeout(self) -> float:
        return float(self._timeout_seconds) if self._timeout_seconds is not None else float(os.getenv("PANTHEON_INSTITUTIONAL_KNOWLEDGE_TIMEOUT_SECONDS", "3.0"))

    def _resolve_owner_config(self, owner: str, *, caller_authorization: Optional[str] = None) -> Tuple[str, str]:
        if owner == "attribution":
            base = (self._evaluation_base_url or os.getenv("PANTHEON_EVALUATION_API_URL") or os.getenv("PANTHEON_EVALUATION_SERVICE_URL") or os.getenv("PANTHEON_ATTRIBUTION_API_URL") or os.getenv("EVALUATION_URL") or "").strip().rstrip("/")
            token = (self._evaluation_service_token or os.getenv("PANTHEON_EVALUATION_SERVICE_TOKEN") or os.getenv("EVALUATION_SERVICE_TOKEN") or os.getenv("PANTHEON_ATTRIBUTION_SERVICE_TOKEN") or "").strip()
            if not token and caller_authorization:
                token = caller_authorization.removeprefix("Bearer ").removeprefix("bearer ").strip()
            if not base:
                raise InstitutionalKnowledgeUnavailableError("attribution", "attribution_owner_unconfigured", 503)
            if not token:
                raise InstitutionalKnowledgeUnavailableError("attribution", "attribution_service_credential_unconfigured", 503)
            return base, f"Bearer {token}" if not token.lower().startswith("bearer ") else token
        if owner == "research":
            base = (self._research_base_url or os.getenv("PANTHEON_RESEARCH_API_URL") or os.getenv("PANTHEON_RESEARCH_SERVICE_URL") or os.getenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL") or os.getenv("RESEARCH_URL") or "").strip().rstrip("/")
            token = (self._research_service_token or os.getenv("PANTHEON_RESEARCH_SERVICE_TOKEN") or os.getenv("PERSONA_RESEARCH_SERVICE_TOKEN") or os.getenv("RESEARCH_SERVICE_TOKEN") or "").strip()
            auth = (caller_authorization or "").strip() or token
            if not base:
                raise InstitutionalKnowledgeUnavailableError("research", "research_owner_unconfigured", 503)
            if not auth:
                raise InstitutionalKnowledgeUnavailableError("research", "research_service_credential_unconfigured", 503)
            return base, f"Bearer {auth}" if not auth.lower().startswith("bearer ") else auth
        if owner == "memory":
            base = (self._memory_base_url or os.getenv("PANTHEON_MEMORY_API_URL") or os.getenv("PANTHEON_MEMORY_SERVICE_URL") or os.getenv("MEMORY_URL") or "").strip().rstrip("/")
            token = (self._memory_service_token or os.getenv("PANTHEON_MEMORY_SERVICE_TOKEN") or os.getenv("MEMORY_SERVICE_TOKEN") or "").strip()
            auth = (caller_authorization or "").strip() or token
            if not base:
                raise InstitutionalKnowledgeUnavailableError("memory", "memory_owner_unconfigured", 503)
            if not auth:
                raise InstitutionalKnowledgeUnavailableError("memory", "memory_caller_credential_missing", 503)
            return base, f"Bearer {auth}" if not auth.lower().startswith("bearer ") else auth
        raise InstitutionalKnowledgeUnavailableError(owner, f"{owner}_owner_unknown", 503)

    def _http_get_json(self, owner: str, path: str, *, tenant_id: str, caller_authorization: Optional[str] = None, allow_404: bool = False) -> Any:
        base, auth_header = self._resolve_owner_config(owner, caller_authorization=caller_authorization)
        headers = {"Authorization": auth_header, "X-Tenant-Id": tenant_id, "X-Pantheon-Tenant-Id": tenant_id, "Accept": "application/json"}
        req = urllib.request.Request(f"{base}{path}", method="GET", headers=headers)
        opener = self._urlopen or urllib.request.urlopen
        try:
            resp = opener(req, timeout=self._timeout())
            raw_bytes = resp.__enter__().read() if hasattr(resp, "__enter__") else resp.read()
        except urllib.error.HTTPError as exc:
            if allow_404 and exc.code == 404:
                return None
            raise InstitutionalKnowledgeUnavailableError(owner, f"{owner}_owner_http_{exc.code}", exc.code if exc.code in {401, 403} else 503) from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise InstitutionalKnowledgeUnavailableError(owner, f"{owner}_owner_unavailable", 503) from exc
        try:
            text = raw_bytes.decode("utf-8") if isinstance(raw_bytes, (bytes, bytearray)) else str(raw_bytes)
            return json.loads(text) if text.strip() else {}
        except (ValueError, UnicodeDecodeError) as exc:
            raise InstitutionalKnowledgeUnavailableError(owner, f"{owner}_owner_invalid_json", 503) from exc

    def _fetch_attribution_records(self, *, tenant_id: str, caller_authorization: Optional[str] = None) -> List[Dict[str, Any]]:
        payload = self._http_get_json("attribution", "/api/attribution/records", tenant_id=tenant_id, caller_authorization=caller_authorization)
        raw_items = (payload.get("items") if "items" in payload else payload.get("records")) if isinstance(payload, Mapping) else (payload if isinstance(payload, list) else None)
        if not isinstance(raw_items, list):
            raise InstitutionalKnowledgeUnavailableError("attribution", "attribution_owner_invalid_response", 503)
        records: List[Dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, Mapping):
                raise InstitutionalKnowledgeUnavailableError("attribution", "attribution_owner_invalid_record", 503)
            rec_id = _clean_str(item.get("record_id") or item.get("attribution_record_id"))
            if not rec_id:
                raise InstitutionalKnowledgeUnavailableError("attribution", "attribution_record_missing_id", 503)
            if _clean_str(item.get("tenant_id")) and _clean_str(item.get("tenant_id")) != tenant_id:
                raise InstitutionalKnowledgeUnavailableError("attribution", "attribution_record_tenant_mismatch", 503)
            records.append({**copy.deepcopy(dict(item)), "record_id": rec_id})
        return records

    def _fetch_research_records(self, *, tenant_id: str, caller_authorization: Optional[str] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        demands_payload = self._http_get_json("research", "/api/research/persona-demands", tenant_id=tenant_id, caller_authorization=caller_authorization, allow_404=True)
        repl_payload = self._http_get_json("research", "/api/research-orchestrator/alpha-replication/entries", tenant_id=tenant_id, caller_authorization=caller_authorization, allow_404=True)
        if demands_payload is None and repl_payload is None:
            raise InstitutionalKnowledgeUnavailableError("research", "research_owner_http_404", 503)
        demands: List[Dict[str, Any]] = []
        if demands_payload is not None:
            raw_demands = (demands_payload.get("demands") if "demands" in demands_payload else demands_payload.get("items")) if isinstance(demands_payload, Mapping) else (demands_payload if isinstance(demands_payload, list) else None)
            if not isinstance(raw_demands, list):
                raise InstitutionalKnowledgeUnavailableError("research", "research_owner_invalid_demands_response", 503)
            for row in raw_demands:
                if not isinstance(row, Mapping):
                    raise InstitutionalKnowledgeUnavailableError("research", "research_owner_invalid_demand_item", 503)
                if _clean_str(row.get("tenant_id")) and _clean_str(row.get("tenant_id")) != tenant_id:
                    raise InstitutionalKnowledgeUnavailableError("research", "research_demand_tenant_mismatch", 503)
                demands.append(copy.deepcopy(dict(row)))
        repl_entries: List[Dict[str, Any]] = []
        if repl_payload is not None:
            raw_entries = repl_payload if isinstance(repl_payload, list) else ((repl_payload.get("entries") if "entries" in repl_payload else repl_payload.get("items")) if isinstance(repl_payload, Mapping) else None)
            if not isinstance(raw_entries, list):
                raise InstitutionalKnowledgeUnavailableError("research", "research_owner_invalid_replication_response", 503)
            for row in raw_entries:
                if not isinstance(row, Mapping):
                    raise InstitutionalKnowledgeUnavailableError("research", "research_owner_invalid_replication_item", 503)
                if _clean_str(row.get("tenant_id")) and _clean_str(row.get("tenant_id")) != tenant_id:
                    raise InstitutionalKnowledgeUnavailableError("research", "research_replication_tenant_mismatch", 503)
                repl_entries.append(copy.deepcopy(dict(row)))
        return demands, repl_entries

    def _fetch_memory_records(self, *, tenant_id: str, caller_authorization: Optional[str] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        lessons_payload = self._http_get_json("memory", "/api/memory/trade-lessons", tenant_id=tenant_id, caller_authorization=caller_authorization, allow_404=True)
        entries_payload = self._http_get_json("memory", "/api/memory/entries", tenant_id=tenant_id, caller_authorization=caller_authorization, allow_404=True)
        if lessons_payload is None and entries_payload is None:
            raise InstitutionalKnowledgeUnavailableError("memory", "memory_owner_http_404", 503)
        lessons: List[Dict[str, Any]] = []
        if lessons_payload is not None:
            raw_lessons = (lessons_payload.get("candidates") if "candidates" in lessons_payload else lessons_payload.get("items")) if isinstance(lessons_payload, Mapping) else (lessons_payload if isinstance(lessons_payload, list) else None)
            if not isinstance(raw_lessons, list):
                raise InstitutionalKnowledgeUnavailableError("memory", "memory_owner_invalid_lessons_response", 503)
            for row in raw_lessons:
                if not isinstance(row, Mapping) or not _clean_str(row.get("lesson_candidate_id") or row.get("id")):
                    raise InstitutionalKnowledgeUnavailableError("memory", "memory_owner_invalid_lesson_item", 503)
                if _clean_str(row.get("tenant_id")) and _clean_str(row.get("tenant_id")) != tenant_id:
                    raise InstitutionalKnowledgeUnavailableError("memory", "memory_lesson_tenant_mismatch", 503)
                lessons.append(copy.deepcopy(dict(row)))
        inst_entries: List[Dict[str, Any]] = []
        if entries_payload is not None:
            raw_entries = (entries_payload.get("entries") if "entries" in entries_payload else entries_payload.get("items")) if isinstance(entries_payload, Mapping) else (entries_payload if isinstance(entries_payload, list) else None)
            if not isinstance(raw_entries, list):
                raise InstitutionalKnowledgeUnavailableError("memory", "memory_owner_invalid_entries_response", 503)
            for row in raw_entries:
                if not isinstance(row, Mapping) or not _clean_str(row.get("entry_id") or row.get("id")):
                    raise InstitutionalKnowledgeUnavailableError("memory", "memory_owner_invalid_entry_item", 503)
                if _clean_str(row.get("tenant_id")) and _clean_str(row.get("tenant_id")) != tenant_id:
                    continue
                inst_entries.append(copy.deepcopy(dict(row)))
        return lessons, inst_entries

    @staticmethod
    def _match_research_to_attribution(attr: Mapping[str, Any], demands: Sequence[Mapping[str, Any]], repl_entries: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        rec_id, meeting_id = _clean_str(attr.get("record_id") or attr.get("attribution_record_id")), _clean_str(attr.get("meeting_id"))
        demands_by_id: Dict[str, Dict[str, Any]] = {}
        linked_demand_ids: set[str] = set()
        candidates: List[Dict[str, Any]] = []
        for d in demands:
            did = _clean_str(d.get("demand_id") or d.get("id"))
            if did:
                demands_by_id[did] = dict(d)
            meta = d.get("metadata") if isinstance(d.get("metadata"), Mapping) else {}
            d_attr, d_meeting, ev_ref = _clean_str(d.get("attribution_record_id") or d.get("record_id") or meta.get("attribution_record_id")), _clean_str(d.get("meeting_id") or meta.get("meeting_id")), _clean_str(d.get("evidence_ref"))
            if (rec_id and (d_attr == rec_id or rec_id in ev_ref)) or (meeting_id and (d_meeting == meeting_id or f"meeting:{meeting_id}" in ev_ref or meeting_id == ev_ref)):
                if did:
                    linked_demand_ids.add(did)
                candidates.append(dict(d))
        for e in repl_entries:
            meta = e.get("metadata") if isinstance(e.get("metadata"), Mapping) else {}
            e_attr, e_demand, e_meeting, rev_ref = _clean_str(e.get("attribution_record_id") or e.get("record_id") or meta.get("attribution_record_id")), _clean_str(e.get("demand_ref") or e.get("demand_id")), _clean_str(e.get("meeting_id")), _clean_str(e.get("review_ref"))
            if (rec_id and (e_attr == rec_id or rec_id in rev_ref)) or (e_demand and e_demand in linked_demand_ids) or (meeting_id and (e_meeting == meeting_id or meeting_id in rev_ref)):
                candidates.append({**demands_by_id.get(e_demand, {}), **dict(e)})
        if isinstance(attr.get("hypotheses"), list):
            for hyp in attr["hypotheses"]:
                if not isinstance(hyp, Mapping):
                    continue
                hyp_dict = dict(hyp)
                h_demand_id = _clean_str(hyp_dict.get("demand_id") or hyp_dict.get("downstream_id"))
                h_val_id = _clean_str(hyp_dict.get("validation_result_id") or hyp_dict.get("validation_id"))
                matched = dict(demands_by_id[h_demand_id]) if h_demand_id and h_demand_id in demands_by_id else None
                if matched is None and h_val_id:
                    matched = next((dict(e) for e in repl_entries if h_val_id in {_clean_str(e.get("admission_id")), _clean_str(e.get("strategy_spec_id")), _clean_str(e.get("demand_ref"))}), None)
                if matched is not None:
                    combined = {**matched, **hyp_dict}
                    for k in ("validation_status", "status", "queue_status", "admission_status", "discovery", "validation_result", "satisfied_by"):
                        if matched.get(k) is not None:
                            combined[k] = matched[k]
                    candidates.append(combined)
                else:
                    candidates.append(hyp_dict)
        return candidates

    @staticmethod
    def _dominant_component(decomposition: Any) -> str:
        if not isinstance(decomposition, Mapping):
            return "selection"
        best_comp, best_mag = "selection", -1.0
        for comp in ("selection", "entry_timing", "sizing", "exit", "costs"):
            val = decomposition.get(comp)
            mag = abs(float(val)) if isinstance(val, (int, float)) else 0.0
            if isinstance(val, Mapping):
                for num_key in ("contribution_bps", "contribution", "value", "bps", "impact"):
                    if isinstance(val.get(num_key), (int, float)):
                        mag = abs(float(val[num_key]))
                        break
            if mag > best_mag:
                best_mag, best_comp = mag, comp
        return best_comp

    def read_institutional_facts(
        self, *, tenant_id: str, user_id: str, operator_id: Optional[str] = None,
        authorization: Optional[str] = None, fact_kind: Optional[str] = None,
    ) -> Dict[str, Any]:
        clean_tenant = _clean_str(tenant_id)
        if not clean_tenant:
            raise InstitutionalKnowledgeUnavailableError("identity", "tenant_id_required", 403)
        attr_records = self._fetch_attribution_records(tenant_id=clean_tenant, caller_authorization=authorization)
        demands, repl_entries = self._fetch_research_records(tenant_id=clean_tenant, caller_authorization=authorization)
        lessons, inst_entries = self._fetch_memory_records(tenant_id=clean_tenant, caller_authorization=authorization)

        attr_by_id = {r["record_id"]: r for r in attr_records}
        demands_by_id = {_clean_str(d.get("demand_id") or d.get("id")): d for d in demands if _clean_str(d.get("demand_id") or d.get("id"))}
        facts: List[Dict[str, Any]] = []
        seen_fact_ids: set[str] = set()
        used_validation_ids_for_attr: set[str] = set()
        excluded_unvalidated = excluded_luck_or_unsupported = 0

        for attr in attr_records:
            rec_id = attr["record_id"]
            if _has_luck_or_rejected_marker(attr):
                excluded_luck_or_unsupported += 1
                continue
            linked_candidates = self._match_research_to_attribution(attr, demands, repl_entries)
            if not linked_candidates:
                excluded_unvalidated += 1
                continue
            for cand in linked_candidates:
                if _has_luck_or_rejected_marker(cand):
                    excluded_luck_or_unsupported += 1
                    continue
                if not _is_positively_validated_research_record(cand):
                    excluded_unvalidated += 1
                    continue
                val_id, val_link, val_metrics = _extract_validation_id_and_link(cand, tenant_id=clean_tenant)
                fact_id = f"fact:attribution:{rec_id}:{val_id}"
                if fact_id in seen_fact_ids:
                    continue
                seen_fact_ids.add(fact_id)
                used_validation_ids_for_attr.add(val_id)
                payload_map = cand.get("payload") if isinstance(cand.get("payload"), Mapping) else {}
                component = _clean_str(cand.get("component") or payload_map.get("component")).lower() or self._dominant_component(attr.get("decomposition"))
                val_res_map = cand.get("validation_result") if isinstance(cand.get("validation_result"), Mapping) else {}
                hypothesis_text = _clean_str(cand.get("hypothesis") or cand.get("claim") or cand.get("summary") or val_res_map.get("summary") or cand.get("mandate")) or f"Validated {component} attribution finding for {attr.get('subject_id')}"
                title = _clean_str(cand.get("title")) or f"Validated attribution finding ({component}): {hypothesis_text[:80]}"
                symbols = list(dict.fromkeys(_extract_symbols_from_mapping(attr) + _extract_symbols_from_mapping(cand)))
                strategy_ids = list(dict.fromkeys(_extract_strategy_ids(attr) + _extract_strategy_ids(cand)))
                strategy_families = list(dict.fromkeys(_extract_strategy_families(attr) + _extract_strategy_families(cand)))
                actions = list(dict.fromkeys(_extract_actions(cand, component=component) + _extract_actions(attr, component=component)))
                attr_link = f"/api/attribution/records/{urllib.parse.quote(rec_id, safe='')}"
                evidence_refs = [
                    {"ref_type": "source_record", "ref_id": rec_id, "summary": f"Attribution record {rec_id} ({attr.get('direction', 'outlier')}, component={component})"},
                    {"ref_type": "research_run", "ref_id": val_id, "summary": f"Validated research finding {val_id}: {hypothesis_text}"},
                ]
                facts.append({
                    "fact_id": fact_id, "fact_kind": "validated_attribution_finding", "tenant_id": clean_tenant, "title": title, "summary": hypothesis_text,
                    "hypothesis": hypothesis_text, "component": component, "validation_status": "validated", "source_owner": "evaluation+research",
                    "attribution_record_id": rec_id, "validation_result_id": val_id,
                    "links": {"attribution_record": attr_link, "validation_result": val_link, "bff_attribution_record": f"/bff/attribution/records/{urllib.parse.quote(rec_id, safe='')}"},
                    "evidence": {"attribution_record_id": rec_id, "validation_result_id": val_id, "subject_kind": attr.get("subject_kind"), "subject_id": attr.get("subject_id"), "direction": attr.get("direction"), "window": attr.get("window"), "component": component, "metrics": attr.get("metrics") or {}, "decomposition": attr.get("decomposition") or {}, "sample_size": attr.get("sample_size"), "luck_interval": attr.get("luck_interval") or attr.get("luck_check"), "regime_ref": attr.get("regime_ref") or attr.get("regime_context"), "meeting_id": attr.get("meeting_id"), "validation_summary": hypothesis_text, "validation_metrics": val_metrics, "evidence_refs": evidence_refs},
                    "scope": {"symbols": symbols, "strategy_ids": strategy_ids, "strategy_families": strategy_families, "actions": actions, "subject_kind": attr.get("subject_kind"), "subject_id": _clean_str(attr.get("subject_id")), "regime_ref": _clean_str(attr.get("regime_ref") or attr.get("regime_context")) or None, "breadth": "strategy_family" if strategy_families else "system_wide"},
                    "read_only": True, "advice_only": True, "execution_authority": "none", "environment_ceiling": "paper", "written_at": _clean_str(cand.get("updated_at") or cand.get("created_at") or attr.get("created_at")),
                })

        for lesson in lessons:
            lesson_id, review_state = _clean_str(lesson.get("lesson_candidate_id") or lesson.get("id")), _clean_str(lesson.get("review_state")).lower()
            if _has_luck_or_rejected_marker(lesson):
                excluded_luck_or_unsupported += 1
                continue
            if review_state not in _ENDORSED_LESSON_STATES:
                excluded_unvalidated += 1
                continue
            fact_id = f"fact:lesson:{lesson_id}"
            if fact_id in seen_fact_ids:
                continue
            seen_fact_ids.add(fact_id)
            receipt = lesson.get("receipt") if isinstance(lesson.get("receipt"), Mapping) else {}
            meta = lesson.get("metadata") if isinstance(lesson.get("metadata"), Mapping) else {}
            persona_id = _clean_str(lesson.get("persona_id"))
            linked_attr_id = _clean_str(lesson.get("attribution_record_id") or meta.get("attribution_record_id") or receipt.get("attribution_record_id")) or next((a["record_id"] for a in attr_records if persona_id and _clean_str(a.get("subject_id")) == persona_id), attr_records[0]["record_id"] if attr_records else "")
            val_id = _clean_str(lesson.get("validation_result_id") or meta.get("validation_result_id") or receipt.get("audit_receipt_id") or lesson.get("reflection_id")) or lesson_id
            links_map = lesson.get("links") if isinstance(lesson.get("links"), Mapping) else {}
            val_link = _clean_str(links_map.get("validation_result")) or (f"/api/research/persona-demands?demand_id={urllib.parse.quote(val_id, safe='')}" if val_id in demands_by_id else f"/api/memory/trade-lessons/{urllib.parse.quote(lesson_id, safe='')}")
            attr_link = f"/api/attribution/records/{urllib.parse.quote(linked_attr_id, safe='')}" if linked_attr_id else f"/api/memory/trade-lessons/{urllib.parse.quote(lesson_id, safe='')}"
            proposed_change = _clean_str(lesson.get("proposed_change") or lesson.get("summary") or lesson.get("lesson")) or f"Endorsed trade lesson {lesson_id}"
            component = _clean_str(lesson.get("component") or meta.get("component") or "lesson").lower()
            linked_attr = attr_by_id.get(linked_attr_id, {})
            symbols = list(dict.fromkeys(_extract_symbols_from_mapping(lesson) + _extract_symbols_from_mapping(linked_attr)))
            strategy_ids = list(dict.fromkeys(_extract_strategy_ids(lesson) + _extract_strategy_ids(linked_attr)))
            strategy_families = list(dict.fromkeys(_extract_strategy_families(lesson) + _extract_strategy_families(linked_attr)))
            evidence_refs = [{"ref_type": "evidence_item", "ref_id": lesson_id, "summary": f"Endorsed lesson ({review_state}): {proposed_change}"}, {"ref_type": "research_run", "ref_id": val_id, "summary": f"Endorsement validation receipt {val_id}"}]
            if linked_attr_id:
                evidence_refs.insert(0, {"ref_type": "source_record", "ref_id": linked_attr_id, "summary": f"Linked attribution record {linked_attr_id}"})
            facts.append({
                "fact_id": fact_id, "fact_kind": "endorsed_lesson", "tenant_id": clean_tenant, "title": _clean_str(lesson.get("title")) or f"Endorsed lesson: {proposed_change[:80]}",
                "summary": proposed_change, "hypothesis": proposed_change, "component": component, "validation_status": review_state, "source_owner": "memory",
                "attribution_record_id": linked_attr_id or None, "validation_result_id": val_id,
                "links": {"attribution_record": attr_link, "validation_result": val_link, "lesson_record": f"/api/memory/trade-lessons/{urllib.parse.quote(lesson_id, safe='')}"},
                "evidence": {"lesson_candidate_id": lesson_id, "persona_id": lesson.get("persona_id"), "review_state": review_state, "confidence": lesson.get("confidence"), "trade_episode_ids": list(lesson.get("trade_episode_ids") or []), "attribution_record_id": linked_attr_id or None, "validation_result_id": val_id, "receipt": dict(receipt), "evidence_refs": evidence_refs},
                "scope": {"symbols": symbols, "strategy_ids": strategy_ids, "strategy_families": strategy_families, "actions": _extract_actions(lesson, component=component), "subject_kind": "persona", "subject_id": persona_id, "regime_ref": _clean_str(lesson.get("regime_ref") or meta.get("regime_ref")) or None, "breadth": _clean_str(lesson.get("scope") or "system_wide")},
                "read_only": True, "advice_only": True, "execution_authority": "none", "environment_ceiling": "paper", "written_at": _clean_str(lesson.get("updated_at") or lesson.get("created_at")),
            })

        strategy_candidates = list(repl_entries) + [
            d for d in demands
            if not any(_clean_str(e.get("demand_ref") or e.get("demand_id")) == _clean_str(d.get("demand_id") or d.get("id")) for e in repl_entries)
            and (d.get("strategy_id") or d.get("strategy_spec_id") or isinstance(d.get("discovery"), Mapping))
        ]
        for entry in strategy_candidates:
            if _has_luck_or_rejected_marker(entry):
                excluded_luck_or_unsupported += 1
                continue
            if not _is_positively_validated_research_record(entry):
                excluded_unvalidated += 1
                continue
            val_id, val_link, val_metrics = _extract_validation_id_and_link(entry, tenant_id=clean_tenant)
            spec_id = _clean_str(entry.get("strategy_spec_id") or entry.get("strategy_id") or val_id)
            has_strategy_identity = bool(entry.get("strategy_id") or entry.get("strategy_spec_id") or entry.get("admission_id") or entry.get("template_id") or isinstance((entry.get("discovery") or {}).get("template_search"), Mapping))
            if not spec_id or (not has_strategy_identity and val_id in used_validation_ids_for_attr):
                continue
            fact_id = f"fact:strategy:{spec_id}:{val_id}"
            if fact_id in seen_fact_ids:
                continue
            seen_fact_ids.add(fact_id)
            demand_ref = _clean_str(entry.get("demand_ref") or entry.get("demand_id"))
            combined = {**demands_by_id.get(demand_ref, {}), **dict(entry)}
            meta = combined.get("metadata") if isinstance(combined.get("metadata"), Mapping) else {}
            ev_ref, persona_id = _clean_str(combined.get("evidence_ref")), _clean_str(combined.get("persona_id"))
            linked_attr_id = _clean_str(combined.get("attribution_record_id") or combined.get("record_id") or meta.get("attribution_record_id")) or next((aid for aid in attr_by_id if aid in ev_ref), "") or next((a["record_id"] for a in attr_records if persona_id and _clean_str(a.get("subject_id")) == persona_id), attr_records[0]["record_id"] if attr_records else "")
            attr_link = f"/api/attribution/records/{urllib.parse.quote(linked_attr_id, safe='')}" if linked_attr_id else val_link
            summary_text = _clean_str(combined.get("summary") or combined.get("hypothesis") or combined.get("claim") or combined.get("mandate")) or f"Validated strategy fact for {combined.get('strategy_id') or spec_id}"
            title = _clean_str(combined.get("title")) or f"Validated strategy fact ({combined.get('strategy_id') or spec_id}): {summary_text[:80]}"
            component = _clean_str(combined.get("component") or "selection").lower()
            evidence_refs = [{"ref_type": "registry_entry", "ref_id": spec_id, "summary": f"Validated strategy spec {spec_id} ({summary_text})"}, {"ref_type": "research_run", "ref_id": val_id, "summary": f"Validation result {val_id}"}]
            if linked_attr_id:
                evidence_refs.insert(0, {"ref_type": "source_record", "ref_id": linked_attr_id, "summary": f"Linked attribution record {linked_attr_id}"})
            strategy_families = _extract_strategy_families(combined)
            facts.append({
                "fact_id": fact_id, "fact_kind": "validated_strategy_fact", "tenant_id": clean_tenant, "title": title, "summary": summary_text, "hypothesis": summary_text,
                "component": component, "validation_status": "validated", "source_owner": "research", "attribution_record_id": linked_attr_id or None, "validation_result_id": val_id,
                "links": {"attribution_record": attr_link, "validation_result": val_link},
                "evidence": {"strategy_id": combined.get("strategy_id"), "strategy_spec_id": combined.get("strategy_spec_id") or spec_id, "spec_version": combined.get("spec_version"), "admission_id": combined.get("admission_id"), "approval_decision_id": combined.get("approval_decision_id"), "demand_ref": demand_ref or None, "attribution_record_id": linked_attr_id or None, "validation_result_id": val_id, "validation_metrics": val_metrics, "evidence_refs": evidence_refs},
                "scope": {"symbols": _extract_symbols_from_mapping(combined), "strategy_ids": _extract_strategy_ids(combined), "strategy_families": strategy_families, "actions": _extract_actions(combined, component=component), "subject_kind": "persona", "subject_id": persona_id, "regime_ref": _clean_str(combined.get("regime_ref")) or None, "breadth": "strategy_family" if strategy_families else "system_wide"},
                "read_only": True, "advice_only": True, "execution_authority": "none", "environment_ceiling": "paper", "written_at": _clean_str(combined.get("created_at") or combined.get("enqueued_at")),
            })

        for entry in inst_entries:
            if entry.get("superseded_by") or entry.get("archived_at"):
                continue
            content = entry.get("content") if isinstance(entry.get("content"), Mapping) else {}
            structured = content.get("structured_payload") if isinstance(content.get("structured_payload"), Mapping) else {}
            if _has_luck_or_rejected_marker(entry) or _has_luck_or_rejected_marker(structured):
                excluded_luck_or_unsupported += 1
                continue
            k_type, src_type = _clean_str(entry.get("knowledge_type")).lower(), _clean_str(entry.get("source_event_type")).lower()
            if k_type not in {"research_finding", "regime_pattern", "evolution_rationale"}:
                continue
            if src_type not in {"research_finding_published", "research_task_completed", "evolution_decision_approved"} and not _is_positively_validated_research_record(structured):
                excluded_unvalidated += 1
                continue
            entry_id = _clean_str(entry.get("entry_id") or entry.get("id"))
            fact_id = f"fact:strategy:mem:{entry_id}"
            if fact_id in seen_fact_ids:
                continue
            seen_fact_ids.add(fact_id)
            linked_attr_id = _clean_str(structured.get("attribution_record_id")) or (attr_records[0]["record_id"] if attr_records else "")
            val_id = _clean_str(structured.get("validation_result_id") or structured.get("run_id") or entry.get("source_event_id")) or entry_id
            val_link = _clean_str(structured.get("validation_link")) or f"/api/research-orchestrator/runs/{urllib.parse.quote(val_id, safe='')}"
            attr_link = f"/api/attribution/records/{urllib.parse.quote(linked_attr_id, safe='')}" if linked_attr_id else f"/api/memory/entries/{urllib.parse.quote(entry_id, safe='')}"
            headline = _clean_str(content.get("headline")) or f"Institutional finding {entry_id}"
            body_text, component = _clean_str(content.get("body")) or headline, _clean_str(structured.get("component") or "selection").lower()
            facts.append({
                "fact_id": fact_id, "fact_kind": "validated_strategy_fact", "tenant_id": clean_tenant, "title": headline, "summary": body_text, "hypothesis": body_text,
                "component": component, "validation_status": "validated", "source_owner": "memory+research", "attribution_record_id": linked_attr_id or None, "validation_result_id": val_id,
                "links": {"attribution_record": attr_link, "validation_result": val_link, "memory_entry": f"/api/memory/entries/{urllib.parse.quote(entry_id, safe='')}"},
                "evidence": {"entry_id": entry_id, "knowledge_type": k_type, "source_event_type": src_type, "source_event_id": entry.get("source_event_id"), "attribution_record_id": linked_attr_id or None, "validation_result_id": val_id, "structured_payload": dict(structured), "evidence_refs": [{"ref_type": "evidence_item", "ref_id": entry_id, "summary": headline}, {"ref_type": "research_run", "ref_id": val_id, "summary": body_text}]},
                "scope": {"symbols": _extract_symbols_from_mapping(entry), "strategy_ids": _extract_strategy_ids(entry), "strategy_families": _extract_strategy_families(entry), "actions": _extract_actions(entry, component=component), "subject_kind": "institution", "subject_id": ",".join(_string_list(entry.get("contributing_persona_ids"))), "regime_ref": _clean_str(structured.get("regime_ref")) or None, "breadth": _clean_str(entry.get("scope") or "system_wide")},
                "read_only": True, "advice_only": True, "execution_authority": "none", "environment_ceiling": "paper", "written_at": _clean_str(entry.get("written_at")),
            })

        if fact_kind:
            wanted = _clean_str(fact_kind).lower()
            facts = [f for f in facts if _clean_str(f.get("fact_kind")).lower() == wanted]
        counts = {
            "validated_attribution_findings": sum(1 for f in facts if f.get("fact_kind") == "validated_attribution_finding"),
            "endorsed_lessons": sum(1 for f in facts if f.get("fact_kind") == "endorsed_lesson"),
            "validated_strategy_facts": sum(1 for f in facts if f.get("fact_kind") == "validated_strategy_fact"),
            "total": len(facts), "excluded_unvalidated": excluded_unvalidated, "excluded_luck_or_unsupported": excluded_luck_or_unsupported,
        }
        return {
            "items": facts, "facts": facts, "counts": counts, "read_only": True, "advice_only": True,
            "execution_authority": "none", "environment_ceiling": "paper", "no_order_route_proof": "agora_institutional_knowledge_read_only",
        }

    def read_servant_institutional_knowledge(
        self, *, tenant_id: str, user_id: str, servant_persona_id: str, operator_id: Optional[str] = None,
        authorization: Optional[str] = None, fact_kind: Optional[str] = None,
        symbol: Optional[str] = None, strategy_id: Optional[str] = None, strategy_family: Optional[str] = None,
    ) -> Dict[str, Any]:
        result = self.read_institutional_facts(tenant_id=tenant_id, user_id=user_id, operator_id=operator_id, authorization=authorization, fact_kind=fact_kind)
        items = list(result["items"])
        if symbol or strategy_id or strategy_family:
            items = [i for i in items if _score_fact_relevance(i, user_id=user_id, symbol=symbol, strategy_id=strategy_id, strategy_spec_registry_id=None, strategy_family=strategy_family, event_kind=None, suggested_action=None, regime_ref=None)[0]]
        return {"servant_persona_id": servant_persona_id, "recall_scope": "institutional_facts_read_only", "read_only": True, "advice_only": True, "execution_authority": "none", "environment_ceiling": "paper", "no_order_route_proof": "agora_institutional_knowledge_read_only", "items": items, "facts": items, "counts": {**result["counts"], "returned": len(items)}}

    def get_system_suggestions_for_decision(
        self, *, tenant_id: str, user_id: str, servant_persona_id: Optional[str] = None,
        decision_event: Optional[Mapping[str, Any]] = None, decision_event_id: Optional[str] = None,
        symbol: Optional[str] = None, strategy_id: Optional[str] = None, strategy_spec_registry_id: Optional[str] = None,
        strategy_family: Optional[str] = None, event_kind: Optional[str] = None, suggested_action: Optional[str] = None,
        regime_ref: Optional[str] = None, operator_id: Optional[str] = None, authorization: Optional[str] = None,
    ) -> Dict[str, Any]:
        if isinstance(decision_event, Mapping):
            decision_event_id = decision_event_id or _clean_str(decision_event.get("decision_event_id"))
            subj = decision_event.get("subject") if isinstance(decision_event.get("subject"), Mapping) else {}
            symbol = symbol or _clean_str(subj.get("symbol") or decision_event.get("symbol"))
            strategy_id, strategy_spec_registry_id = strategy_id or _clean_str(decision_event.get("strategy_id")), strategy_spec_registry_id or _clean_str(decision_event.get("strategy_spec_registry_id"))
            strategy_family, event_kind = strategy_family or _clean_str(decision_event.get("strategy_family")), event_kind or _clean_str(decision_event.get("event_kind"))
            suggested_action, regime_ref = suggested_action or _clean_str(decision_event.get("suggested_action")), regime_ref or _clean_str(decision_event.get("regime_ref"))
        resolved_servant_id = servant_persona_id or _default_servant_persona_id(tenant_id=tenant_id, user_id=user_id)
        recall = self.read_servant_institutional_knowledge(tenant_id=tenant_id, user_id=user_id, servant_persona_id=resolved_servant_id, operator_id=operator_id, authorization=authorization)
        suggestions: List[Dict[str, Any]] = []
        for fact in recall["items"]:
            if _clean_str(fact.get("validation_status")).lower() in (_REJECTED_OR_LUCK_TOKENS | _UNVALIDATED_PENDING_TOKENS):
                continue
            relevant, score, reasons = _score_fact_relevance(fact, user_id=user_id, symbol=symbol, strategy_id=strategy_id, strategy_spec_registry_id=strategy_spec_registry_id, strategy_family=strategy_family, event_kind=event_kind, suggested_action=suggested_action, regime_ref=regime_ref)
            if not relevant:
                continue
            ev = dict(fact.get("evidence") or {})
            suggestions.append({
                "suggestion_id": f"sug:{decision_event_id or 'tr'}:{fact['fact_id']}", "fact_id": fact["fact_id"], "fact_kind": fact["fact_kind"], "decision_event_id": decision_event_id,
                "servant_persona_id": resolved_servant_id, "title": fact["title"], "suggestion": fact["summary"], "summary": fact["summary"], "hypothesis": fact.get("hypothesis"),
                "component": fact.get("component"), "validation_status": fact["validation_status"], "source_owner": fact.get("source_owner"),
                "relevance_score": score, "relevance_reasons": reasons, "attribution_record_id": fact.get("attribution_record_id"), "validation_result_id": fact.get("validation_result_id"),
                "links": dict(fact.get("links") or {}), "evidence": ev, "evidence_refs": list(ev.get("evidence_refs") or []),
                "advice_only": True, "read_only": True, "execution_authority": "none", "environment_ceiling": "paper", "no_order_route_proof": "agora_decision_support_only",
            })
        suggestions.sort(key=lambda s: (-float(s.get("relevance_score") or 0.0), str(s.get("fact_id") or "")))
        return {
            "decision_event_id": decision_event_id, "servant_persona_id": resolved_servant_id, "recall_scope": "institutional_facts_read_only",
            "read_only": True, "advice_only": True, "execution_authority": "none", "environment_ceiling": "paper", "no_order_route_proof": "agora_decision_support_only",
            "items": suggestions, "suggestions": suggestions, "count": len(suggestions), "counts": recall["counts"],
            "decision_context": {"decision_event_id": decision_event_id, "symbol": _normalize_symbol(symbol) or None, "strategy_id": _clean_str(strategy_id) or None, "strategy_spec_registry_id": _clean_str(strategy_spec_registry_id) or None, "strategy_family": _clean_str(strategy_family) or None, "event_kind": _clean_str(event_kind) or None, "suggested_action": _clean_str(suggested_action) or None, "regime_ref": _clean_str(regime_ref) or None},
        }


def _default_servant_persona_id(*, tenant_id: str, user_id: str) -> str:
    return f"agora-servant-{hashlib.sha256(f'{tenant_id}\0{user_id}\0agora_servant'.encode('utf-8')).hexdigest()[:20]}"


def _score_fact_relevance(
    fact: Mapping[str, Any], *, user_id: str, symbol: Optional[str], strategy_id: Optional[str],
    strategy_spec_registry_id: Optional[str], strategy_family: Optional[str], event_kind: Optional[str],
    suggested_action: Optional[str], regime_ref: Optional[str],
) -> Tuple[bool, float, List[str]]:
    scope = fact.get("scope") if isinstance(fact.get("scope"), Mapping) else {}
    fact_symbols = [_normalize_symbol(s) for s in (scope.get("symbols") or []) if _normalize_symbol(s)]
    fact_strategies = [_clean_str(s) for s in (scope.get("strategy_ids") or []) if _clean_str(s)]
    fact_families = [_clean_str(f).lower() for f in (scope.get("strategy_families") or []) if _clean_str(f)]
    fact_actions = [_clean_str(a).lower() for a in (scope.get("actions") or []) if _clean_str(a)]
    fact_subject_kind, fact_subject_id = _clean_str(scope.get("subject_kind")).lower(), _clean_str(scope.get("subject_id"))
    fact_regime, breadth = _clean_str(scope.get("regime_ref")), _clean_str(scope.get("breadth") or "system_wide").lower()
    ctx_symbol, ctx_family, ctx_regime, clean_user = _normalize_symbol(symbol), _clean_str(strategy_family).lower(), _clean_str(regime_ref), _clean_str(user_id)
    ctx_strategies = {s for s in (_clean_str(strategy_id), _clean_str(strategy_spec_registry_id)) if s}
    ctx_actions = {a.lower() for a in (_clean_str(event_kind), _clean_str(suggested_action)) if a}
    if "enter" in ctx_actions or "entry" in ctx_actions:
        ctx_actions |= {"enter", "entry"}
    if fact_symbols and ctx_symbol and ctx_symbol not in fact_symbols:
        return False, 0.0, []
    if fact_strategies and ctx_strategies and not (ctx_strategies & set(fact_strategies)) and not ((fact_symbols and ctx_symbol and ctx_symbol in fact_symbols) or (fact_families and ctx_family and ctx_family in fact_families)):
        return False, 0.0, []
    if fact_families and ctx_family and ctx_family not in fact_families and not ((fact_symbols and ctx_symbol and ctx_symbol in fact_symbols) or (fact_strategies and ctx_strategies and (ctx_strategies & set(fact_strategies)))):
        return False, 0.0, []
    reasons: List[str] = []
    score = 0.0
    for matched, label, weight in (
        (bool(fact_symbols and ctx_symbol and ctx_symbol in fact_symbols), "symbol_match", 0.45),
        (bool(fact_strategies and ctx_strategies and (ctx_strategies & set(fact_strategies))), "strategy_match", 0.40),
        (bool(fact_families and ctx_family and ctx_family in fact_families), "strategy_family_match", 0.30),
        (bool(fact_subject_kind == "trader" and fact_subject_id and clean_user and fact_subject_id == clean_user), "trader_subject_match", 0.35),
        (bool(fact_actions and ctx_actions and (ctx_actions & set(fact_actions))), "component_action_match", 0.20),
        (bool(fact_regime and ctx_regime and fact_regime == ctx_regime), "regime_match", 0.15),
    ):
        if matched:
            reasons.append(label)
            score += weight
    if not reasons:
        if not (ctx_symbol or ctx_strategies or ctx_family or ctx_actions or ctx_regime) or (breadth == "system_wide" and not fact_symbols and not fact_strategies):
            reasons.append("system_wide_applicable")
            score += 0.10
        else:
            return False, 0.0, []
    return True, round(min(score, 1.0), 4), reasons
