#!/usr/bin/env python3
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from common import resolve_path, to_bool


DEFAULT_REPOSITORIES: dict[str, dict[str, Any]] = {
    "pantheon": {
        "display_name": "Pantheon",
        "repo": None,
        "local_path": ".",
        "default_branch": "master",
        "coordination_dir": ".coordination",
        "requests_dir": ".coordination/requests",
        "responses_dir": ".coordination/responses",
        "screen_docs_dir": "docs/screens",
        "bff_docs_dir": "docs/bff",
        "examples_dir": "docs/examples",
    },
    "front_ai_trading_system": {
        "display_name": "front-ai-trading-system",
        "repo": "ajoe734/front-ai-trading-system",
        "local_path": "../front-ai-trading-system",
        "default_branch": "main",
        "coordination_dir": ".coordination",
        "requests_dir": ".coordination/requests",
        "responses_dir": ".coordination/responses",
        "screen_docs_dir": "docs/screens",
    },
    "runtime_platform": {
        "display_name": "lean-platform",
        "repo": None,
        "local_path": "../lean-platform",
        "default_branch": "main",
        "coordination_dir": ".coordination",
        "requests_dir": ".coordination/requests",
        "responses_dir": ".coordination/responses",
    },
    "lean_engine": {
        "display_name": "Lean",
        "repo": "ajoe734/pantheon-lean",
        "local_path": "../Lean",
        "default_branch": "master",
        "coordination_dir": ".coordination",
        "requests_dir": ".coordination/requests",
        "responses_dir": ".coordination/responses",
    },
}


DEFAULT_WORKER_ROUTES: dict[str, dict[str, Any]] = {
    "pantheon-bff-worker": {
        "target_agent": "Codex",
        "description": "Pantheon BFF and contract work",
    },
    "front-sync-worker": {
        "target_agent": "Codex",
        "description": "Front-end type, SDK, and hook sync work",
    },
    "front-ui-worker": {
        "target_agent": "Copilot",
        "description": "Front-end UI implementation work",
    },
    "runtime-worker": {
        "target_agent": "Gemini",
        "description": "Runtime and platform integration work",
    },
    "engine-worker": {
        "target_agent": "Claude",
        "description": "LEAN engine capability work",
        "requires_human_approval": True,
    },
    "qa-worker": {
        "target_agent": "Claude",
        "description": "QA verification and acceptance work",
    },
}


WORKER_ALIASES = {
    "pantheon-bff": "pantheon-bff-worker",
    "front-sync": "front-sync-worker",
    "front-ui": "front-ui-worker",
    "runtime": "runtime-worker",
    "engine": "engine-worker",
    "qa": "qa-worker",
}


def coordination_enabled(config: dict[str, Any]) -> bool:
    coord_cfg = config.get("coordination")
    if coord_cfg is None:
        return False
    return to_bool(coord_cfg.get("enabled", True))


def coordination_config(config: dict[str, Any]) -> dict[str, Any]:
    return dict(config.get("coordination", {}) or {})


def repositories(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    merged = deepcopy(DEFAULT_REPOSITORIES)
    for repo_id, override in (coordination_config(config).get("repositories", {}) or {}).items():
        current = merged.setdefault(repo_id, {})
        current.update(deepcopy(override or {}))

    pantheon_repo = merged.setdefault("pantheon", {})
    if not pantheon_repo.get("repo"):
        pantheon_repo["repo"] = ((config.get("github_bus") or {}).get("repo")) or None
    return merged


def resolve_repository(config: dict[str, Any], repo_id: str) -> dict[str, Any]:
    repo = deepcopy(repositories(config).get(repo_id, {}))
    repo["id"] = repo_id
    repo["display_name"] = repo.get("display_name") or repo_id
    local_path = repo.get("local_path")
    repo["resolved_local_path"] = resolve_path(local_path) if local_path else None
    return repo


def matching_repo_id(config: dict[str, Any], value: str | None) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    lowered = candidate.casefold()
    for repo_id, repo in repositories(config).items():
        options = {
            repo_id,
            str(repo.get("display_name") or ""),
            str(repo.get("repo") or ""),
        }
        normalized = {item.strip().casefold() for item in options if item and item.strip()}
        if lowered in normalized:
            return repo_id
    return None


def repository_slug(config: dict[str, Any], repo_id: str | None) -> str | None:
    if not repo_id:
        return None
    repo = resolve_repository(config, repo_id)
    slug = str(repo.get("repo") or "").strip()
    return slug or None


def repository_local_path(config: dict[str, Any], repo_id: str | None) -> Path | None:
    if not repo_id:
        return None
    repo = resolve_repository(config, repo_id)
    path = repo.get("resolved_local_path")
    return path if isinstance(path, Path) else None


def coordination_requests_dir(config: dict[str, Any], repo_id: str | None) -> Path | None:
    base = repository_local_path(config, repo_id)
    if base is None:
        return None
    repo = resolve_repository(config, repo_id or "")
    rel = str(repo.get("requests_dir") or ".coordination/requests")
    return base / rel if not Path(rel).is_absolute() else Path(rel)


def coordination_responses_dir(config: dict[str, Any], repo_id: str | None) -> Path | None:
    base = repository_local_path(config, repo_id)
    if base is None:
        return None
    repo = resolve_repository(config, repo_id or "")
    rel = str(repo.get("responses_dir") or ".coordination/responses")
    return base / rel if not Path(rel).is_absolute() else Path(rel)


def screen_docs_dir(config: dict[str, Any], repo_id: str | None) -> Path | None:
    base = repository_local_path(config, repo_id)
    if base is None:
        return None
    repo = resolve_repository(config, repo_id or "")
    rel = str(repo.get("screen_docs_dir") or "docs/screens")
    return base / rel if not Path(rel).is_absolute() else Path(rel)


def bff_docs_dir(config: dict[str, Any], repo_id: str | None) -> Path | None:
    base = repository_local_path(config, repo_id)
    if base is None:
        return None
    repo = resolve_repository(config, repo_id or "")
    rel = str(repo.get("bff_docs_dir") or "docs/bff")
    return base / rel if not Path(rel).is_absolute() else Path(rel)


def examples_dir(config: dict[str, Any], repo_id: str | None) -> Path | None:
    base = repository_local_path(config, repo_id)
    if base is None:
        return None
    repo = resolve_repository(config, repo_id or "")
    rel = str(repo.get("examples_dir") or "docs/examples")
    return base / rel if not Path(rel).is_absolute() else Path(rel)


def iter_local_repositories(config: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for repo_id in repositories(config):
        resolved = resolve_repository(config, repo_id)
        local_path = resolved.get("resolved_local_path")
        if isinstance(local_path, Path):
            items.append(resolved)
    return items


def worker_routes(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    merged = deepcopy(DEFAULT_WORKER_ROUTES)
    for worker_kind, override in (coordination_config(config).get("worker_routes", {}) or {}).items():
        current = merged.setdefault(worker_kind, {})
        current.update(deepcopy(override or {}))
    return merged


def worker_route(config: dict[str, Any], worker_kind: str | None) -> dict[str, Any] | None:
    if not worker_kind:
        return None
    return worker_routes(config).get(str(worker_kind).strip())


def resolve_worker_kind(alias: str | None) -> str | None:
    value = str(alias or "").strip().lower()
    if not value:
        return None
    if value in DEFAULT_WORKER_ROUTES:
        return value
    return WORKER_ALIASES.get(value)
