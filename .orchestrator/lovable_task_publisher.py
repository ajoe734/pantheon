#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from common import ensure_parent
from multi_repo_registry import coordination_responses_dir, repository_slug

try:
    import yaml
except ImportError:  # pragma: no cover - best effort fallback
    yaml = None


def _write_if_changed(path: Path, content: str) -> bool:
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    if existing == content:
        return False
    ensure_parent(path)
    path.write_text(content, encoding="utf-8")
    return True


def _yaml_dump(payload: dict[str, Any]) -> str:
    if yaml is not None:
        return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _env_or_value(env_name: str | None, explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    if not env_name:
        return None
    value = os.environ.get(env_name, "").strip()
    return value or None


def render_lovable_prompt(machine_packet: dict[str, Any]) -> str:
    endpoints = list(machine_packet.get("allowed_endpoints") or [])
    gap_handoff_path = str(machine_packet.get("gap_handoff_path") or "").strip()
    gap_handoff_template = str(machine_packet.get("gap_handoff_template") or "").strip()
    completion_handoff_path = str(machine_packet.get("completion_handoff_path") or "").strip()
    completion_handoff_template = str(machine_packet.get("completion_handoff_template") or "").strip()
    prompt_lines = [
        f"Build the `{machine_packet.get('feature_id')}` UI flow in `front-ai-trading-system` using only Pantheon APIs.",
    ]
    if gap_handoff_path:
        gap_line = f"If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `{gap_handoff_path}`"
        if gap_handoff_template:
            gap_line += f" using `{gap_handoff_template}` as the template."
        else:
            gap_line += "."
        gap_line += " Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop."
        prompt_lines.append(gap_line)
    if machine_packet.get("screen"):
        prompt_lines.append(f"Screen: `{machine_packet['screen']}`.")
    if endpoints:
        prompt_lines.append("Allowed endpoints:")
        prompt_lines.extend(f"- {endpoint}" for endpoint in endpoints)
    prompt_lines.append("Constraints:")
    prompt_lines.extend(f"- {item}" for item in list(machine_packet.get("constraints") or []))
    acceptance = list(machine_packet.get("acceptance") or [])
    if acceptance:
        prompt_lines.append("Acceptance:")
        prompt_lines.extend(f"- {item}" for item in acceptance)
    if completion_handoff_path:
        completion_line = f"When the UI implementation is ready, write `{completion_handoff_path}`"
        if completion_handoff_template:
            completion_line += f" using `{completion_handoff_template}` as the template."
        else:
            completion_line += "."
        completion_line += " Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically."
        prompt_lines.append("Completion handoff:")
        prompt_lines.append(f"- {completion_line}")
    links = dict(machine_packet.get("links") or {})
    ref_lines = [
        value
        for value in [
            links.get("lovable_project_url"),
            links.get("bff_spec_path"),
            *(links.get("example_payload_paths") or []),
        ]
        if value
    ]
    if ref_lines:
        prompt_lines.append("References:")
        prompt_lines.extend(f"- {item}" for item in ref_lines)
    return "\n".join(prompt_lines).rstrip() + "\n"


def publish_lovable_task_packet(config: dict[str, Any], contract_payload: dict[str, Any]) -> dict[str, Any] | None:
    feature_id = str(contract_payload.get("feature_id") or "").strip()
    if not feature_id:
        return None

    responses_dir = coordination_responses_dir(config, "pantheon")
    if responses_dir is None:
        return None

    lovable_cfg = ((config.get("coordination") or {}).get("lovable") or {})
    endpoints = list(contract_payload.get("endpoint") or contract_payload.get("endpoints") or [])
    example_paths = list(contract_payload.get("examples") or [])
    if contract_payload.get("example_path"):
        example_paths.append(str(contract_payload.get("example_path")))

    machine_packet = {
        "feature_id": feature_id,
        "type": "lovable-ui-task",
        "project": str(contract_payload.get("target_repo") or repository_slug(config, "front_ai_trading_system") or "front-ai-trading-system"),
        "status": "ready",
        "pantheon_pr": contract_payload.get("pantheon_pr"),
        "base_url": contract_payload.get("base_url") or contract_payload.get("env"),
        "screen": contract_payload.get("screen"),
        "allowed_endpoints": endpoints,
        "constraints": list(contract_payload.get("constraints") or [])
        or [
            "use existing bff client only",
            "do not add raw fetch in components",
            "do not import demo providers",
            "if any required field is missing, emit a bff-gap handoff instead of mocking",
        ],
        "acceptance": list(contract_payload.get("acceptance") or [])
        or list(contract_payload.get("front_actions_required") or []),
        "links": {
            "lovable_project_url": _env_or_value(
                str(lovable_cfg.get("project_url_env") or "").strip() or None,
                str(contract_payload.get("lovable_project_url") or "").strip()
                or str(lovable_cfg.get("project_url") or "").strip()
                or None,
            ),
            "bff_spec_path": contract_payload.get("bff_spec_path"),
            "example_payload_paths": example_paths,
        },
        "gap_handoff_path": f".coordination/requests/{feature_id}-bff-gap.yaml",
        "gap_handoff_template": f".coordination/requests/{feature_id}-bff-gap.example.yaml",
        "completion_handoff_path": f".coordination/requests/{feature_id}-ui-done.yaml",
        "completion_handoff_template": f".coordination/requests/{feature_id}-ui-done.example.yaml",
    }

    packet_path = responses_dir / f"{feature_id}-lovable-ui-task.yaml"
    packet_changed = _write_if_changed(packet_path, _yaml_dump(machine_packet))

    prompt_path = responses_dir / f"{feature_id}-lovable-prompt.md"
    prompt_changed = _write_if_changed(prompt_path, render_lovable_prompt(machine_packet))

    return {
        "packet_path": str(packet_path),
        "prompt_path": str(prompt_path),
        "packet_changed": packet_changed,
        "prompt_changed": prompt_changed,
        "payload": machine_packet,
    }
