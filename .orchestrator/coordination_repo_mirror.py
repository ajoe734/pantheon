#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from common import ensure_parent
from lovable_task_publisher import render_lovable_prompt, resolve_contract_packet_refs
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


def _yaml_load(path: Path) -> dict[str, Any] | None:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        payload = yaml.safe_load(text)
        return payload if isinstance(payload, dict) else None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _require_git_checkout(path: Path, display_name: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip() == "true":
        return
    raise RuntimeError(
        f"{display_name} checkout is invalid at {path}; local mirror validation requires a sibling git checkout "
        "of the target repo."
    )


def _handoff_target_path(pantheon_root: Path | None, feature_id: str, source: Path, target_root: Path) -> Path:
    handoff_root = target_root / "docs" / "pantheon-handoffs" / feature_id
    if pantheon_root is None:
        return handoff_root / source.name

    feature_handoff_root = pantheon_root / "docs" / "pantheon-handoffs" / feature_id
    try:
        rel = source.relative_to(feature_handoff_root)
        return handoff_root / rel
    except ValueError:
        pass

    docs_root = pantheon_root / "docs"
    try:
        rel = source.relative_to(docs_root)
        return handoff_root / rel
    except ValueError:
        return handoff_root / source.name


def _default_request_templates(config: dict[str, Any], feature_id: str) -> list[Path]:
    pantheon_root = repository_local_path(config, "pantheon")
    if pantheon_root is None:
        return []
    requests_dir = pantheon_root / ".coordination" / "requests"
    if not requests_dir.exists():
        return []

    templates: list[Path] = []
    for suffix in ("bff-gap.example.yaml", "ui-done.example.yaml", "frontend-feedback.example.yaml"):
        candidate = requests_dir / f"{feature_id}-{suffix}"
        if candidate.exists():
            templates.append(candidate)
    return templates


def mirror_backend_delivery_bundle(
    config: dict[str, Any],
    delivery_payload: dict[str, Any],
) -> dict[str, Any] | None:
    feature_id = str(delivery_payload.get("feature_id") or "").strip()
    if not feature_id:
        return None

    target_repo_id = "front_ai_trading_system"
    responses_dir = coordination_responses_dir(config, target_repo_id)
    target_root = repository_local_path(config, target_repo_id)
    pantheon_root = repository_local_path(config, "pantheon")
    if responses_dir is None or target_root is None or pantheon_root is None:
        return None
    _require_git_checkout(target_root, "front-ai-trading-system")

    changed = False
    mirrored_paths: list[str] = []

    for key in ("delivery_note_path", "contract_lock_path"):
        rel = str(delivery_payload.get(key) or "").strip()
        if not rel:
            continue
        source = pantheon_root / rel
        target = target_root / rel
        if not source.is_file():
            continue
        changed = _copy_if_changed(source, target) or changed
        mirrored_paths.append(str(target.relative_to(target_root)))

    mirrored_delivery = dict(delivery_payload)
    mirrored_delivery.update(
        {
            "mirror_only": True,
            "mirrored_from_repo": "pantheon",
            "mirrored_target_repo": "front-ai-trading-system",
        }
    )

    delivery_path = responses_dir / f"{feature_id}-backend-delivery.yaml"
    changed = _write_if_changed(delivery_path, _yaml_dump(mirrored_delivery)) or changed
    mirrored_paths.append(str(delivery_path.relative_to(target_root)))

    return {
        "target_repo_id": target_repo_id,
        "target_repo_path": str(target_root),
        "changed": changed,
        "mirrored_paths": mirrored_paths,
    }


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
    _require_git_checkout(target_root, "front-ai-trading-system")

    refs = resolve_contract_packet_refs(config, contract_payload)
    pantheon_root = refs.get("pantheon_root")
    artifacts = dict(contract_payload.get("artifacts") or {})
    handoff_dir = target_root / "docs" / "pantheon-handoffs" / feature_id

    mirrored_paths: list[str] = []
    changed = False

    def record_copy(source: Path, target: Path) -> str:
        nonlocal changed
        changed = _copy_if_changed(source, target) or changed
        rel = str(target.relative_to(target_root))
        mirrored_paths.append(rel)
        return rel

    local_artifacts = {
        key: value
        for key, value in artifacts.items()
        if isinstance(value, str) and value.strip()
    }
    local_bff_ref: str | None = None
    local_ui_spec_ref: str | None = None
    local_frontend_change_spec_ref: str | None = None
    local_example_refs: list[str] = []

    artifact_specs = [
        ("bff_contract", refs.get("bff_spec")),
        ("screen_spec", refs.get("ui_spec")),
        ("frontend_change_spec", refs.get("frontend_change_spec")),
    ]
    for key, source in artifact_specs:
        if not isinstance(source, Path):
            continue
        rel = record_copy(source, _handoff_target_path(pantheon_root, feature_id, source, target_root))
        local_artifacts[key] = rel
        if key == "bff_contract":
            local_bff_ref = rel
        elif key == "screen_spec":
            local_ui_spec_ref = rel
        elif key == "frontend_change_spec":
            local_frontend_change_spec_ref = rel

    for source in list(refs.get("examples") or []):
        if not isinstance(source, Path):
            continue
        rel = record_copy(source, _handoff_target_path(pantheon_root, feature_id, source, target_root))
        local_example_refs.append(rel)
    if local_example_refs and "example_payload" in local_artifacts:
        local_artifacts["example_payload"] = local_example_refs[0]

    backend_delivery_ref = str(artifacts.get("backend_delivery") or "").strip()
    if backend_delivery_ref and pantheon_root is not None:
        backend_source = pantheon_root / backend_delivery_ref
        if backend_source.is_file():
            backend_payload = _yaml_load(backend_source)
            if backend_payload is not None:
                mirrored_backend = mirror_backend_delivery_bundle(config, backend_payload)
                if mirrored_backend:
                    changed = bool(mirrored_backend.get("changed")) or changed
                    for mirrored_path in mirrored_backend.get("mirrored_paths", []):
                        if mirrored_path not in mirrored_paths:
                            mirrored_paths.append(str(mirrored_path))
            else:
                record_copy(backend_source, target_root / backend_delivery_ref)

    mirrored_contract = dict(contract_payload)
    mirrored_contract.update(
        {
            "mirror_only": True,
            "mirrored_from_repo": "pantheon",
            "mirrored_target_repo": "front-ai-trading-system",
        }
    )
    if refs.get("workbench"):
        mirrored_contract["workbench"] = refs["workbench"]
    if refs.get("screen_id"):
        mirrored_contract["screen_id"] = refs["screen_id"]
    if local_bff_ref:
        mirrored_contract["bff_spec_path"] = local_bff_ref
    if local_ui_spec_ref:
        mirrored_contract["ui_spec_path"] = local_ui_spec_ref
        mirrored_contract["screen_spec_path"] = local_ui_spec_ref
    if local_frontend_change_spec_ref:
        mirrored_contract["frontend_change_spec_path"] = local_frontend_change_spec_ref
    if local_example_refs:
        mirrored_contract["examples"] = local_example_refs
    if local_artifacts:
        mirrored_contract["artifacts"] = local_artifacts

    for source in _default_request_templates(config, feature_id):
        target_template = target_root / ".coordination" / "requests" / source.name
        changed = _copy_if_changed(source, target_template) or changed
        mirrored_paths.append(str(target_template.relative_to(target_root)))

    contract_path = responses_dir / f"{feature_id}-contract-ready.yaml"
    handoff_contract_path = handoff_dir / contract_path.name
    changed = _write_if_changed(contract_path, _yaml_dump(mirrored_contract)) or changed
    changed = _write_if_changed(handoff_contract_path, _yaml_dump(mirrored_contract)) or changed
    mirrored_paths.extend(
        [
            str(contract_path.relative_to(target_root)),
            str(handoff_contract_path.relative_to(target_root)),
        ]
    )

    if lovable_bundle and isinstance(lovable_bundle.get("payload"), dict):
        mirrored_packet = dict(lovable_bundle["payload"])
        mirrored_packet.update(
            {
                "mirror_only": True,
                "mirrored_from_repo": "pantheon",
                "mirrored_target_repo": "front-ai-trading-system",
            }
        )
        if refs.get("workbench"):
            mirrored_packet["workbench"] = refs["workbench"]
        if refs.get("screen_id"):
            mirrored_packet["screen_id"] = refs["screen_id"]
        if local_ui_spec_ref:
            mirrored_packet["ui_spec_path"] = local_ui_spec_ref
        if local_frontend_change_spec_ref:
            mirrored_packet["frontend_change_spec_path"] = local_frontend_change_spec_ref

        links = dict(mirrored_packet.get("links") or {})
        if local_bff_ref:
            links["bff_spec_path"] = local_bff_ref
        if local_ui_spec_ref:
            links["ui_spec_path"] = local_ui_spec_ref
        if local_frontend_change_spec_ref:
            links["frontend_change_spec_path"] = local_frontend_change_spec_ref
        if local_example_refs:
            links["example_payload_paths"] = local_example_refs
        links["handoff_bundle_dir"] = str(handoff_dir.relative_to(target_root))
        mirrored_packet["links"] = links

        packet_path = responses_dir / f"{feature_id}-lovable-ui-task.yaml"
        prompt_path = responses_dir / f"{feature_id}-lovable-prompt.md"
        handoff_packet_path = handoff_dir / packet_path.name
        handoff_prompt_path = handoff_dir / prompt_path.name
        rendered_prompt = render_lovable_prompt(mirrored_packet)

        local_artifacts["lovable_ui_task"] = str(packet_path.relative_to(target_root))
        if "lovable_prompt" in artifacts:
            local_artifacts["lovable_prompt"] = str(prompt_path.relative_to(target_root))
        mirrored_contract["artifacts"] = local_artifacts
        changed = _write_if_changed(contract_path, _yaml_dump(mirrored_contract)) or changed
        changed = _write_if_changed(handoff_contract_path, _yaml_dump(mirrored_contract)) or changed

        changed = _write_if_changed(packet_path, _yaml_dump(mirrored_packet)) or changed
        changed = _write_if_changed(prompt_path, rendered_prompt) or changed
        changed = _write_if_changed(handoff_packet_path, _yaml_dump(mirrored_packet)) or changed
        changed = _write_if_changed(handoff_prompt_path, rendered_prompt) or changed
        mirrored_paths.extend(
            [
                str(packet_path.relative_to(target_root)),
                str(prompt_path.relative_to(target_root)),
                str(handoff_packet_path.relative_to(target_root)),
                str(handoff_prompt_path.relative_to(target_root)),
            ]
        )

    return {
        "target_repo_id": target_repo_id,
        "target_repo_path": str(target_root),
        "changed": changed,
        "mirrored_paths": mirrored_paths,
    }
