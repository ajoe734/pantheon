#!/usr/bin/env python3
"""Audit Persona Fleet clickable targets against canonical BFF content.

The fleet UI can render a row even when a linked detail/list target is stale.
This verifier builds a per-row link matrix and validates the target content,
not just the HTTP status:

* detail targets must return the expected id and row key;
* list targets must contain a row matching the Fleet persona;
* human review may use readiness_blocker:persona:<persona_id>, but stale
  promotion/human_gate ids are still reported as broken available targets;
* canonical linkTargets/link_targets with available=false are warnings only;
* summary-only fields without an available href/bffHref are warnings, not
  synthetic detail-link failures.

Exit codes:
  0 = no broken available links
  1 = at least one broken available link
  2 = configuration or Fleet fetch error

Usage:
    python3 scripts/audit_persona_fleet_links.py \
        --env-file /home/lupin/code/execute-plans/.env \
        --json-output /tmp/persona-fleet-link-audit.json
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_BFF_BASE = "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io"
MATRIX_CATEGORIES = (
    "persona",
    "data_source",
    "research",
    "performance",
    "mutation_evolution",
    "human_gate",
    "runtime_action",
    "artifact",
)


@dataclass(frozen=True)
class HttpResult:
    path: str
    status: int | None
    body: Any
    error: str | None = None


class BffClient:
    def __init__(self, *, base_url: str, token: str, timeout: float, insecure: bool) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._cache: dict[str, HttpResult] = {}
        self._ctx = ssl.create_default_context()
        if insecure:
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE

    def get(self, path: str) -> HttpResult:
        if path in self._cache:
            return self._cache[path]
        url = path if path.startswith(("http://", "https://")) else self.base_url + path
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                raw = resp.read()
                body = json.loads(raw) if raw else {}
                result = HttpResult(path=path, status=resp.status, body=body)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = raw.decode("utf-8", "replace")[:500]
            result = HttpResult(path=path, status=exc.code, body=body)
        except Exception as exc:  # noqa: BLE001 - audit should report transport failures
            result = HttpResult(path=path, status=None, body=None, error=f"{type(exc).__name__}: {exc}")
        self._cache[path] = result
        return result


def load_env_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(path)
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key.startswith("#"):
            continue
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("items", "persona_fleet", "data", "rows", "runtimes", "artifacts"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        for key in ("items", "data", "rows", "runtimes", "artifacts"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _detail(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload
    return {}


def _row_id(row: dict[str, Any]) -> str:
    return _text(row.get("persona_id") or row.get("personaId") or row.get("id"))


def _record_ids(record: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in (
        "id",
        "persona_id",
        "personaId",
        "source_id",
        "sourceId",
        "runtime_id",
        "runtimeId",
        "binding_id",
        "bindingId",
        "runtime_binding_id",
        "runtimeBindingId",
        "artifact_id",
        "artifactId",
        "experiment_id",
        "experimentId",
        "inbox_id",
        "inboxId",
    ):
        value = _text(record.get(key))
        if value:
            ids.add(value)
    return ids


def _record_matches_id(record: dict[str, Any], expected_id: str) -> bool:
    return expected_id in _record_ids(record)


def _record_matches_persona(record: dict[str, Any], persona_id: str) -> bool:
    if _record_matches_id(record, persona_id):
        return True
    for key in ("persona", "source", "metadata", "row", "subject"):
        nested = record.get(key)
        if isinstance(nested, dict) and _record_matches_persona(nested, persona_id):
            return True
    return False


def _find_matching_persona(items: list[dict[str, Any]], persona_id: str) -> dict[str, Any] | None:
    for item in items:
        if _record_matches_persona(item, persona_id):
            return item
    return None


def _first_mapping(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = row.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _links(row: dict[str, Any]) -> dict[str, Any]:
    return _first_mapping(row, "links")


TARGET_ALIASES = {
    "persona": ("persona", "detail", "persona_detail", "personaDetail"),
    "data_source": ("data_source", "dataSource", "source_health", "sourceHealth", "source"),
    "research": ("research", "experiment", "research_experiment", "researchExperiment"),
    "performance": ("performance", "performance_attribution", "performanceAttribution"),
    "mutation_evolution": ("mutation_evolution", "mutationEvolution", "mutation", "evolution"),
    "human_gate": ("human_gate", "humanGate", "human_review", "humanReview", "review"),
    "runtime_action": ("runtime_action", "runtimeAction", "runtime", "action"),
    "artifact": ("artifact", "research_artifact", "researchArtifact"),
}


def _link_targets(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = row.get("linkTargets") if isinstance(row.get("linkTargets"), (dict, list)) else row.get("link_targets")
    targets: dict[str, dict[str, Any]] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict):
                targets[str(key)] = value
    elif isinstance(raw, list):
        for value in raw:
            if not isinstance(value, dict):
                continue
            key = _text(
                value.get("category")
                or value.get("kind")
                or value.get("key")
                or value.get("name")
                or value.get("id")
            )
            if key:
                targets[key] = value
    return targets


def _target_key(value: str) -> str:
    return re_sub_non_word(value).lower()


def re_sub_non_word(value: str) -> str:
    return "".join("_" if not ch.isalnum() else ch.lower() for ch in value).strip("_")


def _target_for(row: dict[str, Any], category: str) -> dict[str, Any] | None:
    targets = _link_targets(row)
    normalized = {_target_key(key): value for key, value in targets.items()}
    for key in TARGET_ALIASES.get(category, (category,)):
        value = targets.get(key)
        if isinstance(value, dict):
            return value
        value = normalized.get(_target_key(key))
        if isinstance(value, dict):
            return value
    return None


def _target_available(target: dict[str, Any] | None) -> bool | None:
    if not isinstance(target, dict):
        return None
    if "available" in target:
        value = target.get("available")
    elif "isAvailable" in target:
        value = target.get("isAvailable")
    else:
        value = None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "available"}:
            return True
        if lowered in {"false", "0", "no", "unavailable"}:
            return False
    status = _lower(target.get("status") or target.get("state"))
    if status in {"unavailable", "disabled", "missing", "none"}:
        return False
    if status in {"available", "ok", "ready"}:
        return True
    return None


def _target_href(target: dict[str, Any] | None) -> str:
    if not isinstance(target, dict):
        return ""
    for key in ("bffHref", "bff_href", "bffPath", "bff_path"):
        value = _text(target.get(key))
        if value:
            return value
    for key in ("href", "route", "url", "path"):
        value = _text(target.get(key))
        if value:
            return value
    return ""


def _target_reason(target: dict[str, Any] | None) -> str:
    if not isinstance(target, dict):
        return ""
    for key in ("reason", "unavailableReason", "unavailable_reason", "message", "summary"):
        value = _text(target.get(key))
        if value:
            return value
    return ""


def _target_unavailable(category: str, target: dict[str, Any] | None, *, expected_id: str | None = None) -> list[dict[str, Any]] | None:
    if _target_available(target) is not False:
        return None
    reason = _target_reason(target)
    message = "link target is marked available=false"
    if reason:
        message += f": {reason}"
    return [_warn(category, message, target=_target_href(target) or None, expected_id=expected_id)]


def _target_missing_href_failure(category: str, target: dict[str, Any] | None, *, expected_id: str | None = None) -> list[dict[str, Any]] | None:
    if _target_available(target) is True and not _target_href(target):
        return [_fail(category, None, None, "link target is available=true but has no href or bffHref", expected_id=expected_id)]
    return None


def _check(
    *,
    category: str,
    status: str,
    severity: str,
    target: str | None = None,
    canonical_target: str | None = None,
    message: str,
    expected_id: str | None = None,
    matched_id: str | None = None,
    suggested_target: str | None = None,
) -> dict[str, Any]:
    return {
        "category": category,
        "status": status,
        "severity": severity,
        "target": target,
        "canonical_target": canonical_target,
        "expected_id": expected_id,
        "matched_id": matched_id,
        "suggested_target": suggested_target,
        "message": message,
    }


def _ok(category: str, target: str | None, canonical_target: str | None, message: str, *, expected_id: str | None = None, matched_id: str | None = None) -> dict[str, Any]:
    return _check(
        category=category,
        status="ok",
        severity="ok",
        target=target,
        canonical_target=canonical_target,
        message=message,
        expected_id=expected_id,
        matched_id=matched_id,
    )


def _fail(category: str, target: str | None, canonical_target: str | None, message: str, *, expected_id: str | None = None, suggested_target: str | None = None) -> dict[str, Any]:
    return _check(
        category=category,
        status="broken_available",
        severity="fail",
        target=target,
        canonical_target=canonical_target,
        message=message,
        expected_id=expected_id,
        suggested_target=suggested_target,
    )


def _warn(category: str, message: str, *, target: str | None = None, canonical_target: str | None = None, expected_id: str | None = None, suggested_target: str | None = None) -> dict[str, Any]:
    return _check(
        category=category,
        status="unavailable_with_summary",
        severity="warn",
        target=target,
        canonical_target=canonical_target,
        message=message,
        expected_id=expected_id,
        suggested_target=suggested_target,
    )


def _unavailable(category: str, message: str) -> dict[str, Any]:
    return _check(
        category=category,
        status="unavailable_ok",
        severity="ok",
        message=message,
    )


def _canonical_href(href: str) -> str:
    parsed = urllib.parse.urlsplit(href)
    path = urllib.parse.unquote(parsed.path)
    query_values = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    query = f"?{parsed.query}" if parsed.query else ""
    if path.startswith("/personas/"):
        persona_id = path.rstrip("/").split("/")[-1]
        return f"/bff/personas/{_quote(persona_id)}{query}"
    if path.startswith("/management/personas/"):
        persona_id = path.rstrip("/").split("/")[-1]
        return f"/bff/personas/{_quote(persona_id)}{query}"
    if path.startswith("/management/runtimes/"):
        runtime_id = path.rstrip("/").split("/")[-1]
        return f"/bff/runtimes/{_quote(runtime_id)}{query}"
    if path.startswith("/management/human-inbox/"):
        inbox_id = path.split("/management/human-inbox/", 1)[1].strip("/")
        return f"/bff/management/human-inbox/{_quote(urllib.parse.unquote(inbox_id))}{query}"
    if path == "/management/human-inbox":
        return f"/bff/management/human-inbox{query}"
    if path.startswith("/management/experiments/"):
        experiment_id = path.rstrip("/").split("/")[-1]
        return f"/bff/research-experiments/{_quote(experiment_id)}{query}"
    if path.startswith("/management/research-experiments/"):
        experiment_id = path.rstrip("/").split("/")[-1]
        return f"/bff/research-experiments/{_quote(experiment_id)}{query}"
    if path == "/management/performance-attribution":
        persona = _text((query_values.get("persona_id") or query_values.get("persona") or [""])[0])
        if persona:
            return f"/bff/management/performance-attribution/by-persona?persona_id={_quote(persona)}"
        return f"/bff/management/performance-attribution{query}"
    if path == "/management/data-sources":
        persona = _text((query_values.get("persona_id") or query_values.get("persona") or [""])[0])
        source = _text((query_values.get("source") or [""])[0])
        query_parts = []
        if persona:
            query_parts.append(("persona_id", persona))
        if source:
            query_parts.append(("source", source))
        encoded = urllib.parse.urlencode(query_parts)
        return "/bff/v5/execution/persona-health" + (f"?{encoded}" if encoded else "")
    if path.startswith("/research/artifacts/"):
        artifact_id = path.rstrip("/").split("/")[-1]
        return f"/bff/artifacts/{_quote(artifact_id)}{query}"
    if path.startswith("/management/artifacts/"):
        artifact_id = path.rstrip("/").split("/")[-1]
        return f"/bff/artifacts/{_quote(artifact_id)}{query}"
    if path.startswith("/research/experiments/"):
        experiment_id = path.rstrip("/").split("/")[-1]
        return f"/bff/research-experiments/{_quote(experiment_id)}{query}"
    if path.startswith("/bff/") or path.startswith("/api/"):
        return f"{path}{query}"
    return f"{path}{query}"


def _fetch_target(canonical: str) -> str:
    if canonical.startswith("/bff/v5/execution/persona-health?"):
        return "/bff/v5/execution/persona-health"
    if canonical.startswith("/bff/management/performance-attribution/by-persona?"):
        return "/bff/management/performance-attribution/by-persona"
    return canonical


def _http_problem(result: HttpResult) -> str:
    if result.status is None:
        return result.error or "transport error"
    return f"HTTP {result.status}"


def validate_persona(row: dict[str, Any], client: BffClient) -> list[dict[str, Any]]:
    category = "persona"
    persona_id = _row_id(row)
    target = _target_for(row, category)
    unavailable = _target_unavailable(category, target, expected_id=persona_id)
    if unavailable:
        return unavailable
    missing_href = _target_missing_href_failure(category, target, expected_id=persona_id)
    if missing_href:
        return missing_href
    href = _text(_target_href(target) or _links(row).get("detail") or _first_mapping(row, "drillDown", "drill_down").get("href"))
    if not persona_id:
        return [_unavailable(category, "Fleet row has no persona id")]
    if not href:
        return [_warn(category, "persona id is present but no persona link target is available", expected_id=persona_id)]

    canonical = _canonical_href(href)
    result = client.get(_fetch_target(canonical))
    record = _detail(result.body)
    if result.status == 200 and record and _record_matches_persona(record, persona_id):
        return [_ok(category, href, canonical, "persona detail returned the Fleet persona", expected_id=persona_id, matched_id=persona_id)]

    fallback = f"/bff/personas?persona_id={_quote(persona_id)}"
    fallback_result = client.get(fallback)
    fallback_match = _find_matching_persona(_items(fallback_result.body), persona_id)
    suffix = ""
    if fallback_match:
        suffix = "; fallback persona list contains the row, but the advertised detail target is missing"
    return [
        _fail(
            category,
            href,
            canonical,
            f"persona detail target does not return the Fleet persona ({_http_problem(result)}){suffix}",
            expected_id=persona_id,
            suggested_target=fallback if fallback_match else None,
        )
    ]


def validate_data_source(row: dict[str, Any], client: BffClient) -> list[dict[str, Any]]:
    category = "data_source"
    persona_id = _row_id(row)
    target = _target_for(row, category)
    unavailable = _target_unavailable(category, target, expected_id=persona_id)
    if unavailable:
        return unavailable
    missing_href = _target_missing_href_failure(category, target, expected_id=persona_id)
    if missing_href:
        return missing_href
    summary = _first_mapping(row, "data_source_summary", "dataSourceSummary", "data_source_status", "dataSourceStatus")
    href = _text(_target_href(target) or _links(row).get("source_health") or summary.get("href") or summary.get("route"))
    if href:
        canonical = _canonical_href(href)
        result = client.get(_fetch_target(canonical))
        items = _items(result.body)
        match = _find_matching_persona(items, persona_id)
        if result.status == 200 and match:
            checks = [_ok(category, href, canonical, "source-health list contains the Fleet persona", expected_id=persona_id, matched_id=persona_id)]
            parsed = urllib.parse.urlsplit(canonical)
            if "persona_id=" in parsed.query and len(items) > 1:
                checks.append(
                    _warn(
                        category,
                        f"source-health target returned {len(items)} rows for a persona_id query; verify the frontend narrows to the matching row",
                        target=href,
                        canonical_target=canonical,
                        expected_id=persona_id,
                    )
                )
            return checks
        return [
            _fail(
                category,
                href,
                canonical,
                f"source-health target did not contain the Fleet persona ({_http_problem(result)})",
                expected_id=persona_id,
            )
        ]
    if summary:
        state = summary.get("state") or summary.get("status")
        provider_count = int(summary.get("provider_count") or summary.get("configured_source_count") or 0)
        if state or provider_count:
            return [_warn(category, "data-source summary exists but no source-health target is available", expected_id=persona_id)]
    return [_unavailable(category, "no data-source summary or target")]


def _experiment_matches_row(record: dict[str, Any], row: dict[str, Any]) -> tuple[bool, str]:
    summary = _first_mapping(row, "research_summary", "researchSummary", "research_status", "researchStatus")
    expected_stage = _lower(summary.get("stage") or summary.get("status"))
    expected_framework = _lower(summary.get("framework"))
    record_stage = _lower(record.get("stage") or record.get("status"))
    record_framework = _lower(record.get("framework"))
    if not record_framework:
        params = record.get("parameter_set") if isinstance(record.get("parameter_set"), dict) else {}
        record_framework = _lower(params.get("framework"))
    if expected_stage and record_stage and expected_stage != record_stage:
        return False, f"stage mismatch {expected_stage!r} != {record_stage!r}"
    if expected_framework and record_framework and expected_framework != record_framework:
        return False, f"framework mismatch {expected_framework!r} != {record_framework!r}"

    row_markets = {_lower(item) for item in row.get("market_scope", []) if _text(item)}
    params = record.get("parameter_set") if isinstance(record.get("parameter_set"), dict) else {}
    record_market = _lower(params.get("market") or record.get("market_scope") or record.get("market"))
    if row_markets and record_market and record_market not in row_markets:
        return False, f"market mismatch {record_market!r} not in {sorted(row_markets)!r}"
    return True, "experiment id plus non-conflicting stage/framework/market matched"


def validate_research(row: dict[str, Any], client: BffClient) -> list[dict[str, Any]]:
    category = "research"
    target = _target_for(row, category)
    unavailable = _target_unavailable(category, target, expected_id=_row_id(row))
    if unavailable:
        return unavailable
    missing_href = _target_missing_href_failure(category, target, expected_id=_row_id(row))
    if missing_href:
        return missing_href
    summary = _first_mapping(row, "research_summary", "researchSummary", "research_status", "researchStatus")
    href = _text(_target_href(target) or _links(row).get("research") or summary.get("href") or summary.get("route"))
    experiment_id = _text(summary.get("experiment_id") or summary.get("experimentId"))
    current_project_count = int(summary.get("current_project_count") or summary.get("currentProjectCount") or 0)
    stage = _text(summary.get("stage") or summary.get("status"))
    if href:
        canonical = _canonical_href(href)
        result = client.get(_fetch_target(canonical))
        if result.status == 200:
            record = _detail(result.body)
            if not experiment_id or _record_matches_id(record, experiment_id):
                ok, reason = _experiment_matches_row(record, row)
                if ok:
                    return [_ok(category, href, canonical, reason, expected_id=experiment_id or None, matched_id=experiment_id or None)]
        return [_fail(category, href, canonical, f"research target does not match the Fleet row ({_http_problem(result)})", expected_id=experiment_id or None)]
    if experiment_id or current_project_count or stage:
        return [
            _warn(
                category,
                "research summary exists but no available research href/bffHref is advertised",
                expected_id=experiment_id or _row_id(row),
                suggested_target="/bff/research-experiments?framework=<row.framework>&market_scope=<row.market_scope>",
            )
        ]
    return [_unavailable(category, "no research target or research summary")]


def _has_nonzero_performance(row: dict[str, Any]) -> bool:
    if _text(row.get("perf_delta") or row.get("perfDelta")):
        try:
            if float(row.get("perf_delta") or row.get("perfDelta") or 0) != 0:
                return True
        except (TypeError, ValueError):
            return True
    summary = _first_mapping(row, "performance_summary", "performanceSummary", "metrics")
    for value in summary.values():
        if isinstance(value, (int, float)) and value != 0:
            return True
    return False


def validate_performance(row: dict[str, Any], client: BffClient) -> list[dict[str, Any]]:
    category = "performance"
    persona_id = _row_id(row)
    target = _target_for(row, category)
    unavailable = _target_unavailable(category, target, expected_id=persona_id)
    if unavailable:
        return unavailable
    missing_href = _target_missing_href_failure(category, target, expected_id=persona_id)
    if missing_href:
        return missing_href
    href = _text(_target_href(target) or _links(row).get("performance") or _first_mapping(row, "performance_summary", "performanceSummary").get("href"))
    if href:
        canonical = _canonical_href(href)
        result = client.get(_fetch_target(canonical))
        match = _find_matching_persona(_items(result.body), persona_id)
        if result.status == 200 and match:
            return [_ok(category, href, canonical, "performance list contains the Fleet persona", expected_id=persona_id, matched_id=persona_id)]
        record = _detail(result.body)
        if result.status == 200 and record and _record_matches_persona(record, persona_id):
            return [_ok(category, href, canonical, "performance detail returned the Fleet persona", expected_id=persona_id, matched_id=persona_id)]
        return [_fail(category, href, canonical, f"performance target does not match the Fleet persona ({_http_problem(result)})", expected_id=persona_id)]
    if _has_nonzero_performance(row):
        return [_warn(category, "performance summary exists but no available performance href/bffHref is advertised", expected_id=persona_id)]
    return [_unavailable(category, "no nonzero performance summary or target")]


def validate_mutation_evolution(row: dict[str, Any], client: BffClient) -> list[dict[str, Any]]:
    category = "mutation_evolution"
    persona_id = _row_id(row)
    target = _target_for(row, category)
    unavailable = _target_unavailable(category, target, expected_id=persona_id)
    if unavailable:
        return unavailable
    missing_href = _target_missing_href_failure(category, target, expected_id=persona_id)
    if missing_href:
        return missing_href
    href = _text(_target_href(target) or _links(row).get("mutation") or _links(row).get("evolution") or row.get("mutation_href") or row.get("evolution_href"))
    if href:
        canonical = _canonical_href(href)
        result = client.get(_fetch_target(canonical))
        match = _find_matching_persona(_items(result.body), persona_id)
        record = _detail(result.body)
        if result.status == 200 and (match or _record_matches_persona(record, persona_id)):
            return [_ok(category, href, canonical, "mutation/evolution target matches the Fleet persona", expected_id=persona_id, matched_id=persona_id)]
        return [_fail(category, href, canonical, f"mutation/evolution target does not match the Fleet persona ({_http_problem(result)})", expected_id=persona_id)]
    last_mutation = _text(row.get("last_mutation") or row.get("lastMutation") or row.get("updated_at"))
    if last_mutation:
        return [_warn(category, "last mutation/update summary exists but no available mutation/evolution href/bffHref is advertised", expected_id=persona_id)]
    return [_unavailable(category, "no mutation/evolution summary or target")]


def _readiness_target(persona_id: str) -> str:
    return f"/bff/management/human-inbox/{_quote(f'readiness_blocker:persona:{persona_id}')}"


def _human_inbox_list(client: BffClient) -> list[dict[str, Any]]:
    return _items(client.get("/bff/management/human-inbox?page_size=200").body)


def _human_target_id(route: str) -> str:
    parsed = urllib.parse.urlsplit(route)
    path = urllib.parse.unquote(parsed.path).rstrip("/")
    prefix = "/bff/management/human-inbox/"
    if path.startswith(prefix):
        return path[len(prefix):]
    prefix = "/management/human-inbox/"
    if path.startswith(prefix):
        return path[len(prefix):]
    return ""


def _find_human_inbox_item(items: list[dict[str, Any]], target_id: str, persona_id: str) -> dict[str, Any] | None:
    for item in items:
        if target_id and target_id in _record_ids(item):
            return item
    readiness_id = f"readiness_blocker:persona:{persona_id}"
    for item in items:
        if readiness_id in _record_ids(item):
            return item
    return None


def validate_human_gate(row: dict[str, Any], client: BffClient) -> list[dict[str, Any]]:
    category = "human_gate"
    persona_id = _row_id(row)
    target = _target_for(row, category)
    unavailable = _target_unavailable(category, target, expected_id=persona_id)
    if unavailable:
        return unavailable
    missing_href = _target_missing_href_failure(category, target, expected_id=persona_id)
    if missing_href:
        return missing_href
    review = _first_mapping(row, "review")
    route = _text(_target_href(target) or review.get("route") or row.get("human_gate_href") or row.get("review_href"))
    inbox_id = _text(row.get("inbox_id") or review.get("inbox_id") or review.get("inboxId"))
    requires = bool(review.get("requires_human_gate") or review.get("requiresHumanGate") or row.get("human_needed") or row.get("humanNeeded"))
    if route and route.rstrip("/") != "/bff/management/human-inbox":
        canonical = _canonical_href(route)
        target_id = inbox_id or _human_target_id(canonical)
        inbox_items = _human_inbox_list(client)
        exact = _find_human_inbox_item(inbox_items, target_id, "")
        if exact and _record_matches_persona(exact, persona_id):
            return [_ok(category, route, canonical, "human gate list contains the advertised Fleet review id", expected_id=target_id or persona_id, matched_id=target_id or persona_id)]
        fallback = _readiness_target(persona_id)
        fallback_item = _find_human_inbox_item(inbox_items, f"readiness_blocker:persona:{persona_id}", persona_id)
        suggested = fallback if fallback_item and _record_matches_persona(fallback_item, persona_id) else None
        message = "human gate route is advertised but the id is absent from the human-inbox list"
        if suggested:
            message += "; readiness_blocker fallback exists and should be the target"
        return [_fail(category, route, canonical, message, expected_id=inbox_id or persona_id, suggested_target=suggested)]
    if requires:
        fallback = _readiness_target(persona_id)
        fallback_item = _find_human_inbox_item(_human_inbox_list(client), f"readiness_blocker:persona:{persona_id}", persona_id)
        if fallback_item and _record_matches_persona(fallback_item, persona_id):
            return [_warn(category, "human review is required but no explicit route is advertised; readiness_blocker target exists", canonical_target=fallback, expected_id=persona_id)]
        return [_warn(category, "human review is required but no human gate target is available", canonical_target=fallback, expected_id=persona_id)]
    return [_unavailable(category, "no human gate required or advertised")]


def validate_runtime_action(row: dict[str, Any], client: BffClient) -> list[dict[str, Any]]:
    category = "runtime_action"
    links = _links(row)
    target = _target_for(row, category)
    persona_id = _row_id(row)
    unavailable = _target_unavailable(category, target, expected_id=persona_id)
    if unavailable:
        return unavailable
    missing_href = _target_missing_href_failure(category, target, expected_id=persona_id)
    if missing_href:
        return missing_href
    runtime = _first_mapping(row, "runtime_binding", "runtimeBinding")
    runtime_id = _text(row.get("runtime_id") or row.get("runtimeId") or runtime.get("runtime_id") or runtime.get("runtimeId"))
    href = _text(_target_href(target) or links.get("runtime") or _first_mapping(row, "drillDown", "drill_down").get("href") or runtime.get("href"))
    if href:
        canonical = _canonical_href(href)
        result = client.get(_fetch_target(canonical))
        record = _detail(result.body)
        expected = runtime_id or href.rstrip("/").split("/")[-1]
        pool_id = _text(row.get("capital_pool_id") or row.get("capitalPoolId") or _first_mapping(row, "capital_pool", "capitalPool").get("id"))
        target_pool = _text(record.get("capital_pool_id") or record.get("capitalPoolId"))
        id_match = bool(record and _record_matches_id(record, expected))
        pool_match = not pool_id or not target_pool or pool_id == target_pool
        if result.status == 200 and id_match and pool_match:
            return [_ok(category, href, canonical, "runtime detail returned the Fleet runtime/pool", expected_id=expected, matched_id=expected)]
        reason = f"runtime target does not match the Fleet runtime ({_http_problem(result)})"
        if id_match and not pool_match:
            reason = f"runtime target pool mismatch {pool_id!r} != {target_pool!r}"
        return [_fail(category, href, canonical, reason, expected_id=expected)]
    if runtime_id:
        return [_warn(category, "runtime id is present but no runtime/action href is available", expected_id=runtime_id, suggested_target=f"/management/runtimes/{runtime_id}")]
    return [_unavailable(category, "no runtime/action target")]


def validate_artifact(row: dict[str, Any], client: BffClient) -> list[dict[str, Any]]:
    category = "artifact"
    target = _target_for(row, category)
    unavailable = _target_unavailable(category, target, expected_id=_row_id(row))
    if unavailable:
        return unavailable
    missing_href = _target_missing_href_failure(category, target, expected_id=_row_id(row))
    if missing_href:
        return missing_href
    summary = _first_mapping(row, "research_summary", "researchSummary")
    runtime = _first_mapping(row, "runtime_binding", "runtimeBinding")
    artifact_id = _text(
        summary.get("artifact_id")
        or summary.get("artifactId")
        or runtime.get("artifact_id")
        or row.get("artifact_id")
        or row.get("artifactId")
    )
    href = _text(_target_href(target) or _links(row).get("artifact") or summary.get("artifact_href") or summary.get("artifactHref"))
    if href:
        canonical = _canonical_href(href)
    else:
        if artifact_id:
            return [_warn(category, "artifact id is summarized but no available artifact href/bffHref is advertised", expected_id=artifact_id)]
        return [_unavailable(category, "no artifact id or artifact target")]

    result = client.get(_fetch_target(canonical))
    record = _detail(result.body)
    expected_id = artifact_id or urllib.parse.unquote(urllib.parse.urlsplit(canonical).path.rstrip("/").split("/")[-1])
    if result.status == 200 and record and expected_id and _record_matches_id(record, expected_id):
        return [_ok(category, href, canonical, "artifact detail returned the Fleet artifact id", expected_id=expected_id, matched_id=expected_id)]
    return [_fail(category, href, canonical, f"artifact target is advertised but detail is missing or mismatched ({_http_problem(result)})", expected_id=expected_id)]


VALIDATORS = {
    "persona": validate_persona,
    "data_source": validate_data_source,
    "research": validate_research,
    "performance": validate_performance,
    "mutation_evolution": validate_mutation_evolution,
    "human_gate": validate_human_gate,
    "runtime_action": validate_runtime_action,
    "artifact": validate_artifact,
}


def _category_status(checks: list[dict[str, Any]]) -> str:
    if any(check["severity"] == "fail" for check in checks):
        return "broken_available"
    if any(check["severity"] == "warn" for check in checks):
        return "warning"
    if any(check["status"] == "ok" for check in checks):
        return "ok"
    return "unavailable_ok"


def audit_rows(rows: list[dict[str, Any]], client: BffClient) -> list[dict[str, Any]]:
    matrix = []
    for row in rows:
        persona_id = _row_id(row)
        row_result = {
            "persona_id": persona_id,
            "name": row.get("name") or row.get("personaName") or row.get("persona_name"),
            "state": row.get("state"),
            "mode": row.get("mode") or row.get("deployment_stage") or row.get("deploymentStage"),
            "categories": {},
        }
        for category in MATRIX_CATEGORIES:
            checks = VALIDATORS[category](row, client)
            row_result["categories"][category] = {
                "status": _category_status(checks),
                "checks": checks,
            }
        matrix.append(row_result)
    return matrix


def _flatten_checks(matrix: list[dict[str, Any]], severity: str | None = None) -> list[tuple[str, str, dict[str, Any]]]:
    out = []
    for row in matrix:
        for category, payload in row["categories"].items():
            for check in payload["checks"]:
                if severity is None or check["severity"] == severity:
                    out.append((row["persona_id"], category, check))
    return out


def _status_counts(matrix: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {category: {} for category in MATRIX_CATEGORIES}
    for row in matrix:
        for category, payload in row["categories"].items():
            status = payload["status"]
            counts[category][status] = counts[category].get(status, 0) + 1
    return counts


def _print_report(base_url: str, fleet_count: int, matrix: list[dict[str, Any]]) -> None:
    failures = _flatten_checks(matrix, "fail")
    warnings = _flatten_checks(matrix, "warn")
    print("== Persona Fleet link matrix audit ==")
    print(f"base_url={base_url}")
    print(f"fleet_rows={fleet_count}")
    print(f"broken_available_links={len(failures)}")
    print(f"unavailable_with_summary_warnings={len(warnings)}")
    print("")
    print("Category status counts:")
    for category, counts in _status_counts(matrix).items():
        rendered = ", ".join(f"{key}={counts[key]}" for key in sorted(counts))
        print(f"  {category}: {rendered}")
    print("")
    print("Row matrix:")
    header = ["persona_id", *MATRIX_CATEGORIES]
    print(" | ".join(header))
    print(" | ".join("-" * len(item) for item in header))
    for row in matrix:
        statuses = [row["categories"][category]["status"] for category in MATRIX_CATEGORIES]
        print(" | ".join([row["persona_id"], *statuses]))

    if failures:
        print("")
        print("Broken available links:")
        for persona_id, category, check in failures[:80]:
            target = check.get("target") or check.get("canonical_target")
            suggestion = f" suggested={check['suggested_target']}" if check.get("suggested_target") else ""
            print(f"  {persona_id} {category}: {target} -> {check['message']}{suggestion}")
        if len(failures) > 80:
            print(f"  ... {len(failures) - 80} more")

    if warnings:
        print("")
        print("Warnings:")
        for persona_id, category, check in warnings[:80]:
            target = check.get("target") or check.get("canonical_target") or ""
            suggestion = f" suggested={check['suggested_target']}" if check.get("suggested_target") else ""
            print(f"  {persona_id} {category}: {target} {check['message']}{suggestion}")
        if len(warnings) > 80:
            print(f"  ... {len(warnings) - 80} more")


def _summary_payload(base_url: str, fleet_payload: dict[str, Any], matrix: list[dict[str, Any]]) -> dict[str, Any]:
    failures = _flatten_checks(matrix, "fail")
    warnings = _flatten_checks(matrix, "warn")
    return {
        "base_url": base_url,
        "fleet_summary": fleet_payload.get("summary") or _detail(fleet_payload).get("summary"),
        "fleet_rows": len(matrix),
        "broken_available_links": len(failures),
        "unavailable_with_summary_warnings": len(warnings),
        "category_status_counts": _status_counts(matrix),
        "matrix": matrix,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, action="append", default=[], help="Optional dotenv file to load before resolving BFF env vars.")
    parser.add_argument("--base-url", default="", help="BFF base URL. Defaults to BFF_BASE/PANTHEON_BFF_BASE_URL/VITE_BFF_BASE_URL or dev BFF.")
    parser.add_argument("--token", default="", help="Bearer token. Prefer env/--env-file; this value is never printed.")
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--insecure", action=argparse.BooleanOptionalAction, default=True, help="Disable TLS hostname/cert verification for dev sslip endpoints.")
    parser.add_argument("--json-output", type=Path, help="Write full matrix JSON to this path.")
    parser.add_argument("--json", action="store_true", help="Print full matrix JSON instead of the table report.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        for env_file in args.env_file:
            load_env_file(env_file)
    except OSError as exc:
        print(f"ERROR: could not load env file: {exc}", file=sys.stderr)
        return 2

    base_url = (
        args.base_url
        or os.environ.get("BFF_BASE")
        or os.environ.get("PANTHEON_BFF_BASE_URL")
        or os.environ.get("VITE_BFF_BASE_URL")
        or DEFAULT_BFF_BASE
    )
    token = (
        args.token
        or os.environ.get("BFF_TOKEN")
        or os.environ.get("PANTHEON_BFF_TOKEN")
        or os.environ.get("PANTHEON_BFF_SMOKE_BEARER_TOKEN")
        or os.environ.get("VITE_BFF_DEV_BEARER_TOKEN")
        or ""
    )
    if not token:
        print("ERROR: set BFF_TOKEN/PANTHEON_BFF_TOKEN/VITE_BFF_DEV_BEARER_TOKEN or pass --token", file=sys.stderr)
        return 2

    client = BffClient(base_url=base_url, token=token, timeout=args.timeout, insecure=args.insecure)
    fleet_path = f"/bff/management/persona-fleet?page_size={args.page_size}"
    fleet_result = client.get(fleet_path)
    if fleet_result.status != 200:
        print(f"ERROR: could not fetch {fleet_path}: {_http_problem(fleet_result)}", file=sys.stderr)
        return 2
    if not isinstance(fleet_result.body, dict):
        print(f"ERROR: Fleet payload was not a JSON object: {type(fleet_result.body).__name__}", file=sys.stderr)
        return 2
    rows = _items(fleet_result.body)
    matrix = audit_rows(rows, client)
    payload = _summary_payload(base_url, fleet_result.body, matrix)

    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_report(base_url, len(rows), matrix)

    return 1 if payload["broken_available_links"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
