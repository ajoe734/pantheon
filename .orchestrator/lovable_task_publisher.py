#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from common import ensure_parent
from multi_repo_registry import coordination_responses_dir, repository_local_path, repository_slug

try:
    import yaml
except ImportError:  # pragma: no cover - best effort fallback
    yaml = None


DEFAULT_REQUIRED_FEEDBACK_FILES = (
    "LOVABLE_CHANGE_FEEDBACK.md",
    "API_GAP_REQUESTS.json",
    "UI_DECISIONS.md",
    "QA_STATUS.md",
)


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


def _repo_relative(path: Path | None, root: Path | None) -> str | None:
    if path is None or root is None:
        return None
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _resolve_contract_path(pantheon_root: Path | None, value: str | None) -> Path | None:
    candidate = str(value or "").strip()
    if not candidate or pantheon_root is None:
        return None
    path = pantheon_root / candidate
    return path if path.exists() else None


def _slugify(value: str | None) -> str | None:
    candidate = str(value or "").strip().lower()
    if not candidate:
        return None
    return re.sub(r"[^a-z0-9]+", "-", candidate).strip("-") or None


def _first_matching(pantheon_root: Path | None, pattern: str) -> Path | None:
    if pantheon_root is None:
        return None
    matches = sorted(pantheon_root.glob(pattern))
    return matches[0] if matches else None


def _packet_metadata(*paths: Path | None) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for path in paths:
        if path is None or not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith("- ") or ":" not in stripped:
                continue
            key, value = stripped[2:].split(":", 1)
            normalized_key = key.strip().casefold()
            normalized_value = value.strip().strip("`")
            if normalized_key == "workbench" and normalized_value:
                metadata.setdefault("workbench", _slugify(normalized_value) or normalized_value)
            elif normalized_key == "screen id" and normalized_value:
                metadata.setdefault("screen_id", normalized_value)
    return metadata


def default_required_feedback(feature_id: str) -> list[str]:
    feedback_dir = f"docs/pantheon-feedback/{feature_id}"
    return [f"{feedback_dir}/{name}" for name in DEFAULT_REQUIRED_FEEDBACK_FILES]


def resolve_contract_packet_refs(config: dict[str, Any], contract_payload: dict[str, Any]) -> dict[str, Any]:
    feature_id = str(contract_payload.get("feature_id") or "").strip()
    pantheon_root = repository_local_path(config, "pantheon")
    artifacts = dict(contract_payload.get("artifacts") or {})

    bff_spec = _resolve_contract_path(
        pantheon_root,
        str(contract_payload.get("bff_spec_path") or artifacts.get("bff_contract") or "").strip() or None,
    ) or _first_matching(pantheon_root, f"docs/bff/{feature_id}*.md")

    ui_spec = _resolve_contract_path(
        pantheon_root,
        str(contract_payload.get("ui_spec_path") or artifacts.get("screen_spec") or "").strip() or None,
    ) or _first_matching(pantheon_root, f"docs/screens/{feature_id}*.md")

    frontend_change_spec = _resolve_contract_path(
        pantheon_root,
        str(contract_payload.get("frontend_change_spec_path") or "").strip() or None,
    )
    if frontend_change_spec is None and pantheon_root is not None and feature_id:
        candidate = pantheon_root / "docs" / "pantheon-handoffs" / feature_id / "FRONTEND_CHANGE_SPEC.md"
        if candidate.exists():
            frontend_change_spec = candidate

    explicit_examples: list[Path] = []
    for raw in list(contract_payload.get("examples") or []):
        resolved = _resolve_contract_path(pantheon_root, str(raw).strip() or None)
        if resolved is not None:
            explicit_examples.append(resolved)
    for raw in (contract_payload.get("example_path"), artifacts.get("example_payload")):
        resolved = _resolve_contract_path(pantheon_root, str(raw).strip() or None)
        if resolved is not None and resolved not in explicit_examples:
            explicit_examples.append(resolved)
    if not explicit_examples and pantheon_root is not None and feature_id:
        explicit_examples = sorted(pantheon_root.glob(f"docs/examples/{feature_id}*.json"))

    metadata = _packet_metadata(frontend_change_spec, ui_spec)

    return {
        "pantheon_root": pantheon_root,
        "bff_spec": bff_spec,
        "ui_spec": ui_spec,
        "frontend_change_spec": frontend_change_spec,
        "examples": explicit_examples,
        "workbench": str(contract_payload.get("workbench") or metadata.get("workbench") or "").strip() or None,
        "screen_id": str(contract_payload.get("screen_id") or metadata.get("screen_id") or "").strip() or None,
        "bff_spec_path": _repo_relative(bff_spec, pantheon_root),
        "ui_spec_path": _repo_relative(ui_spec, pantheon_root),
        "frontend_change_spec_path": _repo_relative(frontend_change_spec, pantheon_root),
        "example_payload_paths": [
            rel for rel in (_repo_relative(example, pantheon_root) for example in explicit_examples) if rel
        ],
    }


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
    if machine_packet.get("workbench"):
        prompt_lines.append(f"Workbench: `{machine_packet['workbench']}`.")
    if machine_packet.get("screen_id"):
        prompt_lines.append(f"Screen ID: `{machine_packet['screen_id']}`.")
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
    ref_lines: list[str] = []
    for value in [
        links.get("lovable_project_url"),
        machine_packet.get("ui_spec_path"),
        machine_packet.get("frontend_change_spec_path"),
        links.get("bff_spec_path"),
        links.get("handoff_bundle_dir"),
        links.get("ui_spec_path"),
        links.get("frontend_change_spec_path"),
        *(links.get("example_payload_paths") or []),
    ]:
        if value and value not in ref_lines:
            ref_lines.append(str(value))
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
    refs = resolve_contract_packet_refs(config, contract_payload)

    machine_packet = {
        "feature_id": feature_id,
        "type": "lovable-ui-task",
        "project": str(contract_payload.get("target_repo") or repository_slug(config, "front_ai_trading_system") or "front-ai-trading-system"),
        "status": "ready",
        "pantheon_pr": contract_payload.get("pantheon_pr"),
        "base_url": contract_payload.get("base_url") or contract_payload.get("env"),
        "workbench": contract_payload.get("workbench") or refs.get("workbench"),
        "screen": contract_payload.get("screen"),
        "screen_id": contract_payload.get("screen_id") or refs.get("screen_id"),
        "ui_spec_path": contract_payload.get("ui_spec_path") or refs.get("ui_spec_path"),
        "frontend_change_spec_path": contract_payload.get("frontend_change_spec_path") or refs.get("frontend_change_spec_path"),
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
        "required_feedback": list(contract_payload.get("required_feedback") or [])
        or default_required_feedback(feature_id),
        "delivery_dependencies": list(contract_payload.get("delivery_dependencies") or [])
        or [f".coordination/responses/{feature_id}-contract-ready.yaml"],
        "links": {
            "lovable_project_url": _env_or_value(
                str(lovable_cfg.get("project_url_env") or "").strip() or None,
                str(contract_payload.get("lovable_project_url") or "").strip()
                or str(lovable_cfg.get("project_url") or "").strip()
                or None,
            ),
            "bff_spec_path": contract_payload.get("bff_spec_path") or refs.get("bff_spec_path"),
            "ui_spec_path": contract_payload.get("ui_spec_path") or refs.get("ui_spec_path"),
            "frontend_change_spec_path": contract_payload.get("frontend_change_spec_path") or refs.get("frontend_change_spec_path"),
            "example_payload_paths": refs.get("example_payload_paths") or [],
            "handoff_bundle_dir": f"docs/pantheon-handoffs/{feature_id}",
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
