#!/usr/bin/env python3
"""Project real research/memory service output into BFF read-surface stores.

This script keeps the console population boundary narrow:

* research-orchestrator remains the producer for tasks, runs, artifacts, and
  strategy parameters.
* memory-svc remains the producer for institutional knowledge entries when a
  MEMORY_URL is provided.
* the BFF only receives read-model projections under OUT_DIR.

No sample rows are invented here. Every projected row is derived from records
returned by the live service APIs.

Usage:
    RO_URL=http://research-orchestrator-svc:8101 \
    MEMORY_URL=http://memory:8086 \
    OUT_DIR=/data/bff \
        python3 scripts/project_research_to_bff_surfaces.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


StoreMap = dict[str, dict[str, dict[str, Any]]]


def _get(url: str) -> Any:
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return json.loads(response.read())
    except Exception as exc:  # noqa: BLE001
        print(f"  warn: GET {url} failed: {exc}", file=sys.stderr)
        return None


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("items", "data", "runs", "artifacts", "tasks", "entries"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [item for item in payload.values() if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value in (None, ""):
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _record_id(record: dict[str, Any], *keys: str) -> str:
    return _first_text(*(record.get(key) for key in keys)) or ""


def _slug_ref(prefix: str, raw: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in raw.strip())
    return f"{prefix}-{clean}" if clean else ""


def _run_timestamp(run: dict[str, Any]) -> str | None:
    return _first_text(
        run.get("completed_at"),
        run.get("finished_at"),
        run.get("updated_at"),
        run.get("requested_at"),
        run.get("created_at"),
    )


def _task_timestamp(task: dict[str, Any]) -> str | None:
    return _first_text(task.get("updated_at"), task.get("created_at"))


def _event_summary(run: dict[str, Any], event_type: str) -> str | None:
    for event in reversed(list(run.get("events") or [])):
        if not isinstance(event, dict):
            continue
        if str(event.get("event_type") or "") == event_type:
            return _first_text(event.get("summary"), event.get("message"))
    return None


def _ticket_status(task: dict[str, Any]) -> str:
    status = str(task.get("status") or "").strip().lower()
    return {
        "ready": "open",
        "queued": "open",
        "running": "in_progress",
        "completed": "closed",
        "failed": "closed",
        "rejected": "archived",
        "canceled": "archived",
        "cancelled": "archived",
    }.get(status, status or "open")


def _project_ticket(
    task: dict[str, Any],
    *,
    runs: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    task_id = _record_id(task, "task_id", "id")
    if not task_id:
        return None

    status = _ticket_status(task)
    created_at = _first_text(task.get("created_at"), _task_timestamp(task))
    updated_at = _task_timestamp(task) or created_at
    linked_runs = [
        _record_id(run, "run_id", "id")
        for run in runs
        if _record_id(run, "task_id") == task_id and _record_id(run, "run_id", "id")
    ]
    linked_artifacts = [
        _record_id(artifact, "artifact_id", "id")
        for artifact in artifacts
        if _record_id(artifact, "task_id") == task_id and _record_id(artifact, "artifact_id", "id")
    ]
    lifecycle = [
        {
            "from_status": None,
            "to_status": status,
            "transitioned_at": updated_at or created_at,
            "transitioned_by": _first_text(task.get("created_by"), "research-orchestrator"),
        }
    ]
    closed_at = updated_at if status in {"closed", "archived"} else None
    return {
        "id": task_id,
        "ticket_id": task_id,
        "title": _first_text(task.get("title"), task_id),
        "description": _first_text(task.get("objective"), task.get("description"), task_id),
        "status": status,
        "priority": _first_text(_mapping(task.get("constraints")).get("priority"), "normal"),
        "owner": _first_text(task.get("owner"), task.get("created_by"), "research-orchestrator"),
        "created_at": created_at,
        "updated_at": updated_at,
        "closed_at": closed_at,
        "archived_at": updated_at if status == "archived" else None,
        "lifecycle_history": lifecycle,
        "linked_experiments": linked_runs,
        "linked_artifacts": linked_artifacts,
    }


def _metric_records(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for key, value in sorted(metrics.items()):
        if isinstance(value, dict):
            metric_value = value.get("value")
            display_value = value.get("display_value")
            unit = value.get("unit")
            direction = value.get("direction")
        else:
            metric_value = value
            display_value = None
            unit = None
            direction = None
        if isinstance(metric_value, (list, tuple, dict)) or metric_value in (None, ""):
            continue
        records.append(
            {
                "metric_key": str(key),
                "label": str(key).replace("_", " ").title(),
                "value": metric_value,
                "unit": unit,
                "display_value": _first_text(display_value, metric_value),
                "direction": _first_text(direction, "contextual"),
                "baseline_value": None,
                "delta_value": None,
                "delta_display": None,
            }
        )
    return records


def _artifact_metrics(artifact: dict[str, Any]) -> dict[str, Any]:
    direct = _mapping(artifact.get("metrics"))
    if direct:
        return direct
    metadata = _mapping(artifact.get("metadata"))
    for key in ("metrics", "evaluation_metrics"):
        nested = _mapping(metadata.get(key))
        if nested:
            return nested
    evaluation = _mapping(metadata.get("evaluation_summary"))
    nested = _mapping(evaluation.get("metrics"))
    return nested


def _metric_groups(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for artifact in artifacts:
        artifact_id = _record_id(artifact, "artifact_id", "id")
        metrics = _metric_records(_artifact_metrics(artifact))
        if not artifact_id or not metrics:
            continue
        groups.append(
            {
                "group_key": f"artifact_{artifact_id}",
                "label": _first_text(artifact.get("title"), artifact_id),
                "description": f"Metrics emitted by research artifact {artifact_id}.",
                "metrics": metrics,
            }
        )
    return groups


def _project_analysis(
    run: dict[str, Any],
    *,
    task: dict[str, Any] | None,
    artifacts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    run_id = _record_id(run, "run_id", "id")
    task_id = _record_id(run, "task_id")
    if not run_id or not task_id:
        return None
    if str(run.get("status") or "").strip().lower() != "completed":
        return None

    first_artifact = artifacts[0] if artifacts else {}
    completed_at = _run_timestamp(run)
    headline = _first_text(
        first_artifact.get("title"),
        _mapping(first_artifact.get("metadata")).get("headline"),
        task.get("title") if task else None,
        run_id,
    )
    narrative = _first_text(
        _event_summary(run, "run_completed"),
        _mapping(first_artifact.get("metadata")).get("summary"),
        task.get("objective") if task else None,
        f"Research run {run_id} completed.",
    )
    metric_groups = _metric_groups(artifacts)
    focus_metrics = [
        str(metric.get("metric_key"))
        for group in metric_groups
        for metric in group.get("metrics", [])
        if metric.get("metric_key")
    ]
    analysis_id = _slug_ref("analysis", run_id)
    return {
        "id": analysis_id,
        "analysis_id": analysis_id,
        "ticket_id": task_id,
        "experiment_id": run_id,
        "status": "completed",
        "run_at": completed_at,
        "completed_at": completed_at,
        "summary": {
            "headline": headline,
            "narrative": narrative,
            "verdict": _first_text(
                _mapping(first_artifact.get("metadata")).get("verdict"),
                _mapping(run.get("parameters")).get("verdict"),
                "completed",
            ),
            "next_question": _first_text(_mapping(run.get("parameters")).get("next_question")),
        },
        "metric_groups": metric_groups,
        "comparative_summary": {
            "basis": "Projected from research-orchestrator run and artifact records.",
            "baseline_analysis_id": None,
            "focus_metrics": focus_metrics,
            "comparisons": [],
        },
    }


def _project_note(run: dict[str, Any], task: dict[str, Any] | None) -> dict[str, Any] | None:
    run_id = _record_id(run, "run_id", "id")
    task_id = _record_id(run, "task_id")
    if not run_id or not task_id:
        return None
    timestamp = _run_timestamp(run)
    note_id = _slug_ref("note", run_id)
    return {
        "id": note_id,
        "note_id": note_id,
        "title": _first_text(task.get("title") if task else None, run_id),
        "body": _first_text(
            _event_summary(run, "run_completed"),
            task.get("objective") if task else None,
            f"Research run {run_id} status: {run.get('status')}.",
        ),
        "tags": ["research-orchestrator", str(run.get("adapter") or "adapter")],
        "linked_ticket_id": task_id,
        "linked_experiment_id": run_id,
        "created_at": timestamp,
        "updated_at": timestamp,
        "route_href": f"/knowledge/notes/{note_id}",
    }


def _project_evidence_ref(
    artifact: dict[str, Any],
    *,
    run: dict[str, Any] | None,
) -> dict[str, Any] | None:
    artifact_id = _record_id(artifact, "artifact_id", "id")
    if not artifact_id:
        return None
    run_id = _record_id(run or {}, "run_id", "id")
    timestamp = _first_text(artifact.get("created_at"), _run_timestamp(run or {}))
    ref_id = _slug_ref("evref", artifact_id)
    return {
        "id": ref_id,
        "ref_id": ref_id,
        "source_document": {
            "title": _first_text(artifact.get("title"), artifact_id),
            "uri": artifact.get("storage_ref"),
            "captured_at": timestamp,
            "document_type": _first_text(artifact.get("artifact_type"), "research_artifact"),
        },
        "linked_object_summary": {
            "entity_type": "artifact",
            "entity_ref": artifact_id,
            "display_label": _first_text(artifact.get("title"), artifact_id),
            "route_href": f"/research/artifacts/{artifact_id}",
        },
        "credibility": {
            "tier": "producer_record",
            "verified": True,
            "last_verified_at": timestamp,
            "verification_method": "research_orchestrator_projection",
        },
        "source_run_id": run_id,
        "created_at": timestamp,
        "updated_at": timestamp,
        "route_href": f"/knowledge/evidence/{ref_id}",
    }


def _project_insight(
    run: dict[str, Any],
    *,
    task: dict[str, Any] | None,
    analysis: dict[str, Any] | None,
    evidence_refs: list[str],
) -> dict[str, Any] | None:
    run_id = _record_id(run, "run_id", "id")
    if not run_id or analysis is None:
        return None
    timestamp = _run_timestamp(run)
    insight_id = _slug_ref("insight", run_id)
    summary = _first_text(
        _mapping(analysis.get("summary")).get("headline"),
        task.get("title") if task else None,
        run_id,
    )
    return {
        "id": insight_id,
        "insight_id": insight_id,
        "summary": summary,
        "scope": "strategy",
        "status": "active",
        "tags": ["research-orchestrator"],
        "source_ref": f"research-run:{run_id}",
        "supporting_evidence_refs": evidence_refs,
        "linked_sources": [
            {
                "type": "research_analysis",
                "id": analysis.get("analysis_id"),
                "route_href": f"/research/analyze/{analysis.get('analysis_id')}",
            }
        ],
        "aggregation_provenance": {"aggregated_at": timestamp},
        "created_at": timestamp,
        "updated_at": timestamp,
        "route_href": f"/knowledge/insights/{insight_id}",
    }


def _project_strategy(run: dict[str, Any]) -> dict[str, Any] | None:
    params = _mapping(run.get("parameters"))
    strategy_id = _first_text(
        params.get("strategy_id"),
        params.get("source_strategy_spec_id"),
        run.get("strategy_id"),
    )
    if not strategy_id:
        return None
    version = _first_text(params.get("strategy_spec_version"), params.get("version"), "v1")
    version_id = _first_text(params.get("strategy_spec_version_id"), f"specver-{strategy_id}-0001")
    title = _first_text(run.get("title"), params.get("title"), strategy_id)
    updated_at = _run_timestamp(run)
    current_version = {
        "strategy_id": strategy_id,
        "spec_version_id": version_id,
        "spec_version": version,
        "lifecycle_state": "candidate",
        "source_kind": "research",
        "title": title,
        "persona_ids": list(params.get("persona_ids") or []),
        "last_modified_at": updated_at,
        "hypothesis": params.get("hypothesis"),
        "object_ref": {
            "type": "research_run",
            "id": _record_id(run, "run_id", "id"),
        },
    }
    return {
        "id": strategy_id,
        "strategy_id": strategy_id,
        "current_spec_version_id": version_id,
        "title": title,
        "source_kind": "research",
        "persona_ids": current_version["persona_ids"],
        "updated_at": updated_at,
        "versions": [current_version],
    }


def _project_artifact(artifact: dict[str, Any], run: dict[str, Any] | None) -> dict[str, Any] | None:
    artifact_id = _record_id(artifact, "artifact_id", "id")
    if not artifact_id:
        return None
    run_id = _record_id(run or {}, "run_id", "id")
    return {
        "id": artifact_id,
        "artifact_id": artifact_id,
        "lineage_id": _first_text(artifact.get("lineage_id"), f"lin-{artifact_id}"),
        "version": 1,
        "status": _first_text(
            _mapping(artifact.get("registry_hints")).get("artifact_state"),
            artifact.get("artifact_state"),
            "candidate",
        ),
        "name": _first_text(artifact.get("title"), artifact_id),
        "artifact_type": _first_text(artifact.get("artifact_type"), "model_artifact"),
        "description": f"Produced by research run {run_id}.",
        "produced_by_experiment_id": _record_id(run or {}, "task_id"),
        "created_at": artifact.get("created_at"),
        "metrics": _artifact_metrics(artifact),
    }


def _memory_entries(memory_url: str | None) -> dict[str, dict[str, Any]]:
    if not memory_url:
        return {}
    payload = _get(f"{memory_url.rstrip('/')}/api/memory/entries")
    entries: dict[str, dict[str, Any]] = {}
    for entry in _items(payload):
        entry_id = _record_id(entry, "entry_id", "id")
        if entry_id:
            entries[entry_id] = entry
    return entries


def project(ro_url: str, memory_url: str | None = None) -> StoreMap:
    ro_url = ro_url.rstrip("/")
    tasks = _items(_get(f"{ro_url}/api/research-orchestrator/tasks"))
    runs = _items(_get(f"{ro_url}/api/research-orchestrator/runs"))
    tasks_by_id = {_record_id(task, "task_id", "id"): task for task in tasks if _record_id(task, "task_id", "id")}
    runs_by_id = {_record_id(run, "run_id", "id"): run for run in runs if _record_id(run, "run_id", "id")}

    artifacts_by_run: dict[str, list[dict[str, Any]]] = {}
    for run_id in runs_by_id:
        encoded_run_id = urllib.parse.quote(run_id, safe="")
        artifacts_by_run[run_id] = _items(
            _get(f"{ro_url}/api/research-orchestrator/runs/{encoded_run_id}/artifacts")
        )

    all_artifacts = [artifact for artifacts in artifacts_by_run.values() for artifact in artifacts]
    stores: StoreMap = {
        "research_artifacts": {},
        "strategy_specs": {},
        "research_tickets": {},
        "research_analyses": {},
        "research_notes": {},
        "evidence_refs": {},
        "insight_cards": {},
        "institutional_memory_entries": _memory_entries(memory_url),
    }

    for task in tasks:
        task_id = _record_id(task, "task_id", "id")
        task_runs = [run for run in runs if _record_id(run, "task_id") == task_id]
        task_artifacts = [
            artifact
            for artifact in all_artifacts
            if _record_id(artifact, "task_id") == task_id
        ]
        ticket = _project_ticket(task, runs=task_runs, artifacts=task_artifacts)
        if ticket:
            stores["research_tickets"][str(ticket["ticket_id"])] = ticket

    for run in runs:
        run_id = _record_id(run, "run_id", "id")
        task_id = _record_id(run, "task_id")
        task = tasks_by_id.get(task_id)
        run_artifacts = artifacts_by_run.get(run_id, [])

        strategy = _project_strategy(run)
        if strategy:
            stores["strategy_specs"][str(strategy["strategy_id"])] = strategy

        projected_evidence_refs: list[str] = []
        for artifact in run_artifacts:
            projected_artifact = _project_artifact(artifact, run)
            if projected_artifact:
                stores["research_artifacts"][str(projected_artifact["artifact_id"])] = projected_artifact
            evidence_ref = _project_evidence_ref(artifact, run=run)
            if evidence_ref:
                ref_id = str(evidence_ref["ref_id"])
                stores["evidence_refs"][ref_id] = evidence_ref
                projected_evidence_refs.append(ref_id)

        analysis = _project_analysis(run, task=task, artifacts=run_artifacts)
        if analysis:
            stores["research_analyses"][str(analysis["analysis_id"])] = analysis
            insight = _project_insight(
                run,
                task=task,
                analysis=analysis,
                evidence_refs=projected_evidence_refs,
            )
            if insight:
                stores["insight_cards"][str(insight["insight_id"])] = insight

        note = _project_note(run, task)
        if note:
            stores["research_notes"][str(note["note_id"])] = note

    return stores


def write_projection(stores: StoreMap, out_dir: str | os.PathLike[str]) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    filenames = {
        "research_artifacts": "research_artifacts.json",
        "strategy_specs": "strategy_specs.json",
        "research_tickets": "research_tickets.json",
        "research_analyses": "research_analyses.json",
        "research_notes": "research_notes.json",
        "evidence_refs": "evidence_refs.json",
        "insight_cards": "insight_cards.json",
        "institutional_memory_entries": "institutional_memory_entries.json",
    }
    for dataset, filename in filenames.items():
        payload = stores.get(dataset, {})
        (out / filename).write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def main() -> int:
    ro_url = os.environ.get("RO_URL", "http://research-orchestrator-svc:8101")
    memory_url = os.environ.get("MEMORY_URL", "").strip() or None
    out_dir = os.environ.get("OUT_DIR", "/data/bff")
    stores = project(ro_url, memory_url=memory_url)
    write_projection(stores, out_dir)
    print(
        "projected "
        f"{len(stores['research_artifacts'])} artifacts, "
        f"{len(stores['strategy_specs'])} strategies, "
        f"{len(stores['research_tickets'])} research tasks, "
        f"{len(stores['research_analyses'])} analyses, "
        f"{len(stores['research_notes'])} notes, "
        f"{len(stores['evidence_refs'])} evidence refs, "
        f"{len(stores['insight_cards'])} insights, "
        f"{len(stores['institutional_memory_entries'])} memory entries -> {out_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
