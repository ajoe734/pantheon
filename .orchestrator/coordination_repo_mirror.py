#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from common import ensure_parent, resolve_path
from lovable_task_publisher import render_lovable_prompt
from multi_repo_registry import coordination_responses_dir, repository_local_path

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


def _copy_if_changed(source: Path, target: Path) -> bool:
    existing = target.read_text(encoding="utf-8") if target.exists() else None
    content = source.read_text(encoding="utf-8")
    if existing == content:
        return False
    ensure_parent(target)
    target.write_text(content, encoding="utf-8")
    return True


def _yaml_dump(payload: dict[str, Any]) -> str:
    if yaml is not None:
        return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _resolve_source_path(config: dict[str, Any], value: str | None) -> Path | None:
    if not value:
        return None
    path = resolve_path(value)
    if path is not None and path.exists():
        return path
    pantheon_root = repository_local_path(config, "pantheon")
    if pantheon_root is None:
        return None
    candidate = pantheon_root / value
    return candidate if candidate.exists() else None


def _default_reference_paths(config: dict[str, Any], feature_id: str) -> tuple[Path | None, list[Path]]:
    pantheon_root = repository_local_path(config, "pantheon")
    if pantheon_root is None:
        return None, []
    bff_dir = pantheon_root / "docs" / "bff"
    examples_dir = pantheon_root / "docs" / "examples"
    bff_doc = next(iter(sorted(bff_dir.glob(f"{feature_id}-*.md"))), None) if bff_dir.exists() else None
    examples = sorted(examples_dir.glob(f"{feature_id}-*.json")) if examples_dir.exists() else []
    return bff_doc, examples


def _default_request_templates(config: dict[str, Any], feature_id: str) -> list[Path]:
    pantheon_root = repository_local_path(config, "pantheon")
    if pantheon_root is None:
        return []
    requests_dir = pantheon_root / ".coordination" / "requests"
    if not requests_dir.exists():
        return []
    templates = []
    for suffix in ("bff-gap.example.yaml", "ui-done.example.yaml"):
        candidate = requests_dir / f"{feature_id}-{suffix}"
        if candidate.exists():
            templates.append(candidate)
    return templates


def mirror_contract_ready_bundle(
    config: dict[str, Any],
    contract_payload: dict[str, Any],
    lovable_bundle: dict[str, Any] | None,
) -> dict[str, Any] | None:
    feature_id = str(contract_payload.get("feature_id") or "").strip()
    if not feature_id:
        return None

    target_repo_id = "front_ai_trading_system"
    responses_dir = coordination_responses_dir(config, target_repo_id)
    target_root = repository_local_path(config, target_repo_id)
    if responses_dir is None or target_root is None:
        return None

    handoff_dir = target_root / "docs" / "pantheon-handoffs" / feature_id
    mirrored_paths: list[str] = []
    changed = False

    bff_spec = _resolve_source_path(config, str(contract_payload.get("bff_spec_path") or "").strip() or None)
    explicit_examples = [
        path
        for path in (_resolve_source_path(config, str(item).strip() or None) for item in list(contract_payload.get("examples") or []))
        if path is not None
    ]
    example_path = _resolve_source_path(config, str(contract_payload.get("example_path") or "").strip() or None)
    if example_path is not None and example_path not in explicit_examples:
        explicit_examples.append(example_path)

    if bff_spec is None or not explicit_examples:
        default_bff_spec, default_examples = _default_reference_paths(config, feature_id)
        bff_spec = bff_spec or default_bff_spec
        if not explicit_examples:
            explicit_examples = default_examples

    local_bff_ref: str | None = None
    local_example_refs: list[str] = []

    if bff_spec is not None:
        target_bff = handoff_dir / bff_spec.name
        changed = _copy_if_changed(bff_spec, target_bff) or changed
        local_bff_ref = str(target_bff.relative_to(target_root))
        mirrored_paths.append(local_bff_ref)

    for source in explicit_examples:
        target_example = handoff_dir / source.name
        changed = _copy_if_changed(source, target_example) or changed
        rel = str(target_example.relative_to(target_root))
        local_example_refs.append(rel)
        mirrored_paths.append(rel)

    mirrored_contract = dict(contract_payload)
    mirrored_contract.update(
        {
            "mirror_only": True,
            "mirrored_from_repo": "pantheon",
            "mirrored_target_repo": "front-ai-trading-system",
        }
    )
    if local_bff_ref:
        mirrored_contract["bff_spec_path"] = local_bff_ref
    if local_example_refs:
        mirrored_contract["examples"] = local_example_refs

    for source in _default_request_templates(config, feature_id):
        target_template = target_root / ".coordination" / "requests" / source.name
        changed = _copy_if_changed(source, target_template) or changed
        mirrored_paths.append(str(target_template.relative_to(target_root)))

    contract_path = responses_dir / f"{feature_id}-contract-ready.yaml"
    changed = _write_if_changed(contract_path, _yaml_dump(mirrored_contract)) or changed
    mirrored_paths.append(str(contract_path.relative_to(target_root)))

    if lovable_bundle and isinstance(lovable_bundle.get("payload"), dict):
        mirrored_packet = dict(lovable_bundle["payload"])
        mirrored_packet.update(
            {
                "mirror_only": True,
                "mirrored_from_repo": "pantheon",
                "mirrored_target_repo": "front-ai-trading-system",
            }
        )
        links = dict(mirrored_packet.get("links") or {})
        if local_bff_ref:
            links["bff_spec_path"] = local_bff_ref
        if local_example_refs:
            links["example_payload_paths"] = local_example_refs
        mirrored_packet["links"] = links

        packet_path = responses_dir / f"{feature_id}-lovable-ui-task.yaml"
        prompt_path = responses_dir / f"{feature_id}-lovable-prompt.md"
        changed = _write_if_changed(packet_path, _yaml_dump(mirrored_packet)) or changed
        changed = _write_if_changed(prompt_path, render_lovable_prompt(mirrored_packet)) or changed
        mirrored_paths.extend(
            [
                str(packet_path.relative_to(target_root)),
                str(prompt_path.relative_to(target_root)),
            ]
        )

    return {
        "target_repo_id": target_repo_id,
        "target_repo_path": str(target_root),
        "changed": changed,
        "mirrored_paths": mirrored_paths,
    }
