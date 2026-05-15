#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_DIR = ROOT / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

from coordination_repo_mirror import mirror_backend_delivery_bundle, mirror_contract_ready_bundle
from lovable_task_publisher import publish_lovable_task_packet

try:
    import yaml
except ImportError as exc:  # pragma: no cover - workflow bootstrap should install it
    raise SystemExit(f"PyYAML is required: {exc}") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mirror a Pantheon coordination handoff bundle into the front repo checkout."
    )
    parser.add_argument(
        "--payload-path",
        required=True,
        help="Repo-relative or absolute path to the Pantheon coordination payload YAML.",
    )
    parser.add_argument(
        "--target-root",
        required=True,
        help="Filesystem path to the checked-out front-ai-trading-system repository.",
    )
    parser.add_argument(
        "--config",
        default=str(ROOT / ".orchestrator" / "config.json"),
        help="Path to the Pantheon coordination config JSON.",
    )
    parser.add_argument(
        "--output-json",
        help="Optional path to write the publish result JSON.",
    )
    return parser.parse_args()


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def load_payload(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"Coordination payload is not a mapping: {path}")
    return payload


def with_target_override(config: dict[str, Any], target_root: Path) -> dict[str, Any]:
    effective = dict(config)
    coordination = dict(effective.get("coordination") or {})
    repositories = dict(coordination.get("repositories") or {})

    pantheon_repo = dict(repositories.get("pantheon") or {})
    pantheon_repo["local_path"] = str(ROOT)
    pantheon_repo.setdefault("repo", ((effective.get("github_bus") or {}).get("repo")) or "ajoe734/pantheon")
    repositories["pantheon"] = pantheon_repo

    front_repo = dict(repositories.get("front_ai_trading_system") or {})
    front_repo["local_path"] = str(target_root)
    front_repo.setdefault("repo", "ajoe734/front-ai-trading-system")
    repositories["front_ai_trading_system"] = front_repo

    coordination["repositories"] = repositories
    effective["coordination"] = coordination
    return effective


def publish(payload_path: Path, target_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    payload = load_payload(payload_path)
    payload_type = str(payload.get("type") or "").strip()
    feature_id = str(payload.get("feature_id") or "").strip()

    if payload_type == "contract-ready":
        lovable_bundle = publish_lovable_task_packet(config, payload)
        mirrored = mirror_contract_ready_bundle(config, payload, lovable_bundle)
        event_type = "pantheon.contract_ready"
    elif payload_type == "backend-delivery":
        mirrored = mirror_backend_delivery_bundle(config, payload)
        event_type = "pantheon.backend_delivery"
    else:
        raise SystemExit(f"Unsupported payload type for publish_handoff: {payload_type}")

    if mirrored is None:
        raise SystemExit(f"Failed to mirror handoff bundle for {feature_id or payload_path.name}")

    return {
        "feature_id": feature_id,
        "payload_type": payload_type,
        "event_type": event_type,
        "payload_path": str(payload_path.relative_to(ROOT)),
        "target_root": str(target_root),
        "changed": bool(mirrored.get("changed")),
        "mirrored_paths": list(mirrored.get("mirrored_paths") or []),
    }


def main() -> int:
    args = parse_args()
    payload_path = Path(args.payload_path)
    if not payload_path.is_absolute():
        payload_path = ROOT / payload_path
    payload_path = payload_path.resolve()
    target_root = Path(args.target_root).resolve()
    config = with_target_override(load_config(Path(args.config).resolve()), target_root)
    result = publish(payload_path, target_root, config)
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output_json:
        output_path = Path(args.output_json).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
