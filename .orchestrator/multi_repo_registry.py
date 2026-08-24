#!/usr/bin/env python3
"""Multi-repository coordination registry.

Artifact routing is prefix based. For example,
``execute-plans/e2e/dummy.spec.ts`` resolves to the ``execute_plans``
repository root with repository-relative path ``e2e/dummy.spec.ts``.
"""
from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from common import config_status_root


DEFAULT_REPOSITORIES: dict[str, dict[str, Any]] = {
    "pantheon": {
        "display_name": "Pantheon",
        "repo": "ajoe734/pantheon",
        "local_path": ".",
        "default_branch": "dev",
        "coordination_dir": ".coordination",
        "requests_dir": ".coordination/requests",
        "responses_dir": ".coordination/responses",
    },
    "execute_plans": {
        "display_name": "execute-plans",
        "repo": "ajoe734/execute-plans",
        "local_path": "../code/execute-plans",
        "default_branch": "dev",
        "artifact_prefixes": ["execute-plans/", "frontend-checkout/"],
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


def coordination_config(config: dict[str, Any]) -> dict[str, Any]:
    return dict(config.get("coordination", {}) or {})


def repositories(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    merged = deepcopy(DEFAULT_REPOSITORIES)
    for repo_id, override in (coordination_config(config).get("repositories", {}) or {}).items():
        current = merged.setdefault(repo_id, {})
        current.update(deepcopy(override or {}))

    return merged


def resolve_repository(config: dict[str, Any], repo_id: str) -> dict[str, Any]:
    repo = deepcopy(repositories(config).get(repo_id, {}))
    repo["id"] = repo_id
    repo["display_name"] = repo.get("display_name") or repo_id
    configured_local_path = repository_configured_local_path(config, repo_id)
    resolved_local_path = (
        configured_local_path.resolve(strict=False)
        if configured_local_path is not None
        else None
    )
    repo["configured_local_path"] = configured_local_path
    repo["resolved_local_path"] = resolved_local_path
    return repo


def repository_configured_local_path(
    config: dict[str, Any], repo_id: str | None
) -> Path | None:
    if not repo_id:
        return None
    repo = repositories(config).get(repo_id, {})
    local_path = repo.get("local_path")
    if not local_path:
        return None
    candidate = Path(str(local_path)).expanduser()
    if not candidate.is_absolute():
        # Repository paths belong to the coordination/data plane.  The
        # installed command checkout may be an immutable release root and must
        # never become path authority for delivery repositories.
        candidate = config_status_root(config) / candidate
    # Normalize parent references lexically without following symlinks.  Callers
    # that establish authority can inspect every component before resolving.
    return Path(os.path.abspath(candidate))


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


def repository_artifact_colon_prefixes(config: dict[str, Any], repo_id: str) -> list[str]:
    """Return legacy ``repo:path`` aliases used by durable task packets.

    The registry's canonical spelling is ``repo/path``, but a large body of
    task metadata was emitted with a colon separator.  Treat the colon as a
    repository delimiter only when its left side exactly matches a registered
    repository alias; ordinary paths containing colons remain untouched.
    """

    repo = resolve_repository(config, repo_id)
    candidates: list[str] = [
        repo_id,
        str(repo.get("display_name") or ""),
        str(repo.get("repo") or "").rsplit("/", 1)[-1],
        Path(str(repo.get("local_path") or "")).name,
    ]
    raw_prefixes = repo.get("artifact_prefixes")
    if isinstance(raw_prefixes, str):
        candidates.append(raw_prefixes)
    elif isinstance(raw_prefixes, list):
        candidates.extend(str(item) for item in raw_prefixes)

    prefixes: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        alias = _normalized_artifact_path(raw).strip("/")
        if not alias or "/" in alias or alias in {".", ".."}:
            continue
        prefix = f"{alias}:"
        if prefix in seen:
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


def task_declared_target_repository(task: Mapping[str, Any] | None) -> str | None:
    if not isinstance(task, Mapping):
        return None
    source_ref = task.get("source_ref") if isinstance(task.get("source_ref"), Mapping) else {}
    metadata = task.get("metadata") if isinstance(task.get("metadata"), Mapping) else {}
    for candidate in (
        task.get("target_repo"),
        task.get("target_repository"),
        task.get("target_repo_id"),
        task.get("repository_id"),
        source_ref.get("target_repo"),
        source_ref.get("target_repository"),
        source_ref.get("target_repo_id"),
        source_ref.get("repository_id"),
        metadata.get("target_repo"),
        metadata.get("target_repository"),
        metadata.get("target_repo_id"),
        metadata.get("repository_id"),
    ):
        if candidate is not None and str(candidate).strip():
            return str(candidate).strip()
    return None


def task_target_repository_id(
    config: dict[str, Any], task: Mapping[str, Any] | None
) -> str | None:
    declared = task_declared_target_repository(task)
    if not declared:
        return None
    return matching_repo_id(config, declared)


def artifact_explicit_repository_id(
    config: dict[str, Any], artifact_path: str | Path | None
) -> str | None:
    candidate = _normalized_artifact_path(artifact_path)
    if not candidate:
        return None

    path = Path(candidate)
    if path.is_absolute():
        return _path_repository_id(config, path)

    for repo_id in repositories(config):
        if repo_id == "pantheon":
            continue
        for prefix in repository_artifact_prefixes(config, repo_id):
            if candidate == prefix[:-1] or candidate.startswith(prefix):
                return repo_id
        for prefix in repository_artifact_colon_prefixes(config, repo_id):
            if candidate == prefix or candidate.startswith(prefix):
                return repo_id
    for prefix in repository_artifact_prefixes(config, "pantheon"):
        if candidate == prefix[:-1] or candidate.startswith(prefix):
            return "pantheon"
    for prefix in repository_artifact_colon_prefixes(config, "pantheon"):
        if candidate == prefix or candidate.startswith(prefix):
            return "pantheon"
    return None


def artifact_repository_id(
    config: dict[str, Any],
    artifact_path: str | Path | None,
    default_repo_id: str | None = None,
) -> str:
    explicit = artifact_explicit_repository_id(config, artifact_path)
    if explicit is not None:
        return explicit
    return default_repo_id or "pantheon"


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
    for prefix in repository_artifact_colon_prefixes(config, target_repo_id):
        if candidate == prefix:
            return Path()
        if candidate.startswith(prefix):
            return Path(candidate[len(prefix) :])
    return Path(candidate)


def task_artifact_repository_ids(
    config: dict[str, Any], task: Mapping[str, Any] | None
) -> list[str]:
    if not isinstance(task, Mapping):
        return ["pantheon"]
    declared_target = task_target_repository_id(config, task)
    raw_target = task_declared_target_repository(task)
    default_repo = (
        declared_target
        if declared_target is not None
        else ("pantheon" if not raw_target else None)
    )

    repo_ids: list[str] = []
    seen: set[str] = set()
    raw_artifacts = task.get("artifacts") or []
    for artifact in raw_artifacts:
        explicit_repo = artifact_explicit_repository_id(config, artifact)
        if explicit_repo is not None:
            repo_id = explicit_repo
        elif default_repo is not None:
            repo_id = default_repo
        else:
            repo_id = "unknown"
        if repo_id not in seen:
            seen.add(repo_id)
            repo_ids.append(repo_id)
    if not repo_ids:
        if declared_target:
            return [declared_target]
        return ["pantheon"]
    return repo_ids


def task_primary_repository_id(
    config: dict[str, Any], task: Mapping[str, Any] | None
) -> str | None:
    if not isinstance(task, Mapping):
        return "pantheon"

    raw_target = task_declared_target_repository(task)
    declared_target = task_target_repository_id(config, task)
    if raw_target and declared_target is None:
        return None

    raw_artifacts = task.get("artifacts") or []
    explicit_repos: set[str] = set()
    for art in raw_artifacts:
        rep = artifact_explicit_repository_id(config, art)
        if rep is not None:
            explicit_repos.add(rep)

    explicit_non_pantheon = {r for r in explicit_repos if r != "pantheon"}
    if len(explicit_non_pantheon) > 1:
        return None

    if declared_target is not None:
        if any(r != declared_target for r in explicit_repos):
            return None
        return declared_target

    if len(explicit_non_pantheon) == 1:
        return next(iter(explicit_non_pantheon))

    if "pantheon" in explicit_repos or raw_artifacts:
        return "pantheon"

    return "pantheon"


def validate_task_repository_scope(
    config: dict[str, Any],
    task: Mapping[str, Any] | None,
) -> str:
    task_map = task if isinstance(task, Mapping) else {}
    task_id = str(task_map.get("id") or "?").strip() or "?"

    raw_target = task_declared_target_repository(task_map)
    declared_target = task_target_repository_id(config, task_map)
    if raw_target and declared_target is None:
        if "+" in raw_target or "," in raw_target:
            raise ValueError(
                f"Task {task_id} has ambiguous multi-repository target_repo: {raw_target!r}. "
                "Split into separate single-repository tasks or specify one target_repo."
            )
        raise ValueError(
            f"Task {task_id} specifies unrecognized target_repo: {raw_target!r}."
        )

    raw_artifacts = task_map.get("artifacts") or []
    explicit_repos: set[str] = set()
    for art in raw_artifacts:
        rep = artifact_explicit_repository_id(config, art)
        if rep is not None:
            explicit_repos.add(rep)

    explicit_non_pantheon = {r for r in explicit_repos if r != "pantheon"}
    if len(explicit_non_pantheon) > 1:
        raise ValueError(
            f"Task {task_id} artifacts span multiple non-Pantheon repositories "
            f"({', '.join(sorted(explicit_non_pantheon))}). Split into separate per-repository tasks."
        )

    if declared_target is not None:
        conflicts = sorted(r for r in explicit_repos if r != declared_target)
        if conflicts:
            raise ValueError(
                f"Task {task_id} has conflicting repository scope: declared target_repo is "
                f"{declared_target!r} but artifact prefix requires {', '.join(conflicts)}."
            )
        return declared_target

    if len(explicit_non_pantheon) == 1:
        return next(iter(explicit_non_pantheon))

    return "pantheon"


def iter_local_repositories(config: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for repo_id in repositories(config):
        resolved = resolve_repository(config, repo_id)
        local_path = resolved.get("resolved_local_path")
        if isinstance(local_path, Path):
            items.append(resolved)
    return items
