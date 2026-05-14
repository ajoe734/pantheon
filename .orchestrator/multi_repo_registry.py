#!/usr/bin/env python3
"""Multi-repository coordination registry.

Artifact routing is prefix based. For example,
``execute-plans/e2e/dummy.spec.ts`` resolves to the ``execute_plans``
repository root with repository-relative path ``e2e/dummy.spec.ts``.
"""
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
        "artifact_prefixes": ["front-ai-trading-system/"],
        "coordination_dir": ".coordination",
        "requests_dir": ".coordination/requests",
        "responses_dir": ".coordination/responses",
        "screen_docs_dir": "docs/screens",
    },
    "execute_plans": {
        "display_name": "execute-plans",
        "repo": "ajoe734/execute-plans",
        "local_path": "../execute-plans",
        "default_branch": "main",
        "artifact_prefixes": ["execute-plans/"],
        "coordination_dir": ".coordination",
        "requests_dir": ".coordination/requests",
        "responses_dir": ".coordination/responses",
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


def _normalized_artifact_path(value: str | Path | None) -> str:
    candidate = str(value or "").strip().replace("\\", "/")
    while candidate.startswith("./"):
        candidate = candidate[2:]
    return candidate


def _safe_artifact_prefix(value: str | None) -> str | None:
    candidate = _normalized_artifact_path(value).strip("/")
    if not candidate or candidate in {".", ".."}:
        return None
    parts = [part for part in candidate.split("/") if part]
    if any(part == ".." for part in parts):
        return None
    return "/".join(parts) + "/"


def repository_artifact_prefixes(config: dict[str, Any], repo_id: str) -> list[str]:
    repo = resolve_repository(config, repo_id)
    raw_prefixes = repo.get("artifact_prefixes")
    candidates: list[str] = []
    if isinstance(raw_prefixes, str):
        candidates.append(raw_prefixes)
    elif isinstance(raw_prefixes, list):
        candidates.extend(str(item) for item in raw_prefixes if str(item).strip())

    for raw in (
        repo_id,
        repo.get("display_name"),
        str(repo.get("repo") or "").rsplit("/", 1)[-1],
        Path(str(repo.get("local_path") or "")).name,
    ):
        value = str(raw or "").strip()
        if value:
            candidates.append(value)

    prefixes: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        prefix = _safe_artifact_prefix(candidate)
        if prefix is None or prefix in seen:
            continue
        seen.add(prefix)
        prefixes.append(prefix)
    return sorted(prefixes, key=len, reverse=True)


def _path_repository_id(config: dict[str, Any], value: Path) -> str | None:
    try:
        resolved = value.resolve(strict=False)
    except OSError:
        resolved = value.absolute()

    matches: list[tuple[int, str]] = []
    for repo in iter_local_repositories(config):
        repo_id = str(repo.get("id") or "").strip()
        root = repo.get("resolved_local_path")
        if not repo_id or not isinstance(root, Path):
            continue
        try:
            resolved_root = root.resolve(strict=False)
        except OSError:
            resolved_root = root.absolute()
        if resolved == resolved_root or resolved_root in resolved.parents:
            matches.append((len(str(resolved_root)), repo_id))
    if not matches:
        return None
    matches.sort(reverse=True)
    return matches[0][1]


def artifact_repository_id(config: dict[str, Any], artifact_path: str | Path | None) -> str:
    candidate = _normalized_artifact_path(artifact_path)
    if not candidate:
        return "pantheon"

    path = Path(candidate)
    if path.is_absolute():
        return _path_repository_id(config, path) or "pantheon"

    for repo_id in repositories(config):
        if repo_id == "pantheon":
            continue
        for prefix in repository_artifact_prefixes(config, repo_id):
            if candidate == prefix[:-1] or candidate.startswith(prefix):
                return repo_id
    return "pantheon"


def repository_relative_artifact_path(
    config: dict[str, Any],
    artifact_path: str | Path | None,
    repo_id: str | None = None,
) -> Path:
    candidate = _normalized_artifact_path(artifact_path)
    if not candidate:
        return Path()

    path = Path(candidate)
    target_repo_id = repo_id or artifact_repository_id(config, candidate)
    if path.is_absolute():
        repo_root = repository_local_path(config, target_repo_id)
        if repo_root is not None:
            try:
                return path.resolve(strict=False).relative_to(repo_root.resolve(strict=False))
            except (OSError, ValueError):
                pass
        return path

    for prefix in repository_artifact_prefixes(config, target_repo_id):
        if candidate == prefix[:-1]:
            return Path()
        if candidate.startswith(prefix):
            return Path(candidate[len(prefix) :])
    return Path(candidate)


def artifact_local_path(config: dict[str, Any], artifact_path: str | Path | None) -> Path | None:
    repo_id = artifact_repository_id(config, artifact_path)
    repo_root = repository_local_path(config, repo_id)
    if repo_root is None:
        return None
    return repo_root / repository_relative_artifact_path(config, artifact_path, repo_id)


def task_artifact_repository_ids(config: dict[str, Any], task: dict[str, Any]) -> list[str]:
    repo_ids: list[str] = []
    seen: set[str] = set()
    for artifact in task.get("artifacts") or []:
        repo_id = artifact_repository_id(config, artifact)
        if repo_id in seen:
            continue
        seen.add(repo_id)
        repo_ids.append(repo_id)
    return repo_ids or ["pantheon"]


def task_primary_repository_id(config: dict[str, Any], task: dict[str, Any]) -> str | None:
    repo_ids = task_artifact_repository_ids(config, task)
    non_pantheon = [repo_id for repo_id in repo_ids if repo_id != "pantheon"]
    if len(non_pantheon) == 1:
        return non_pantheon[0]
    if len(non_pantheon) > 1:
        return None
    return "pantheon"


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
