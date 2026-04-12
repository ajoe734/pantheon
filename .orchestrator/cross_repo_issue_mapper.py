#!/usr/bin/env python3
from __future__ import annotations

import json
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - best effort fallback
    yaml = None


def coordination_commands_help() -> list[str]:
    return [
        "/dispatch pantheon-bff F-xxx",
        "/dispatch front-ui F-xxx",
        "/needs-runtime F-xxx",
        "/contract-ready F-xxx",
        "/approve-engine F-xxx",
    ]


def _payload_excerpt(payload: dict[str, Any] | None) -> str:
    if not payload:
        return "{}"
    if yaml is not None:
        return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).strip()
    return json.dumps(payload, indent=2, ensure_ascii=False)


def coordination_issue_title(feature: dict[str, Any]) -> str:
    feature_id = str(feature.get("feature_id") or "-")
    status = str(feature.get("status") or "unknown")
    return f"[CoordBus] {feature_id} {status}"


def coordination_issue_labels(config: dict[str, Any], feature: dict[str, Any]) -> list[str]:
    labels = list((((config.get("github_bus") or {}).get("labels") or {}).get("coordination") or []))
    for label in feature.get("state_labels", []) or []:
        if label not in labels:
            labels.append(label)
    return labels


def coordination_issue_body(feature: dict[str, Any], *, repo_slug: str, counterpart_links: list[str]) -> str:
    feature_id = str(feature.get("feature_id") or "-")
    summary = str(feature.get("summary") or feature.get("current_payload_type") or "No summary provided.")
    payload = dict(feature.get("latest_payload") or {})
    payload_type = str(feature.get("current_payload_type") or payload.get("type") or "unknown")
    worker_kind = str(feature.get("worker_kind") or "-")
    target_agent = str(feature.get("target_agent") or "-")
    source_repo = str(feature.get("source_repo") or feature.get("source_repo_id") or "-")
    source_branch = str(feature.get("source_branch") or "-")
    latest_path = str(feature.get("latest_path") or "-")
    latest_updated_at = str(feature.get("last_updated_at") or "-")
    labels = ", ".join(feature.get("state_labels") or []) or "-"
    next_step = str(feature.get("next_step") or summary)
    counterparts = "\n".join(f"- {item}" for item in counterpart_links) or "- (none yet)"
    commands = "\n".join(f"- `{item}`" for item in coordination_commands_help())

    body = [
        "<!-- pantheon-bus -->",
        "# Pantheon Coordination Bus",
        "",
        "## Feature",
        f"- ID: `{feature_id}`",
        f"- Status: `{feature.get('status') or 'unknown'}`",
        f"- Payload Type: `{payload_type}`",
        f"- Current Repo: `{repo_slug}`",
        f"- Source Repo: `{source_repo}`",
        f"- Source Branch: `{source_branch}`",
        f"- Latest Coordination File: `{latest_path}`",
        f"- Updated At: `{latest_updated_at}`",
        "",
        "## Delivery State",
        f"- Labels: {labels}",
        f"- Routed Worker: `{worker_kind}`",
        f"- Target Agent: `{target_agent}`",
        f"- Next Step: {next_step}",
        "",
        "## Counterpart Issues",
        counterparts,
        "",
        "## Command Shortcuts",
        commands,
        "",
        "## Payload Excerpt",
        "```yaml",
        _payload_excerpt(payload),
        "```",
    ]
    return "\n".join(body).rstrip() + "\n"
