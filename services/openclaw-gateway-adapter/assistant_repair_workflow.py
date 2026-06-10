"""Repair-mode worktree workflow guardrails for assistant kernel mode.

This module validates repository workflow metadata before a provider is allowed
to run in ``kernel_repair`` with ``workspace-write``.  It is deliberately a
guard/audit helper, not a git workflow executor: normal Pantheon commit, push,
PR, checks, and merge operations still happen through the repository workflow.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit


DEFAULT_REPAIR_WORKTREE_ROOT = "/srv/pantheon-assistant/worktrees"
DEFAULT_REMOTE = "origin"
DEFAULT_MERGE_TARGET = "dev"
DEFAULT_TASK_BRANCH_PREFIX = "task/"
DEFAULT_GIT_USER_EMAIL = "pantheon-assistant@pantheon.local"
DEFAULT_GIT_USER_NAME = "Pantheon Assistant"

GitRunner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]
PullRequestLookup = Callable[[Path, str], Mapping[str, Any] | None]


class AssistantRepairWorkflowError(RuntimeError):
    """Raised when repair-mode repository workflow guardrails fail."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = dict(details or {})

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": "repair_workflow_error",
            "error_code": self.code,
            "message": str(self),
            "retryable": False,
        }
        if self.details:
            payload["details"] = self.details
        return payload


@dataclasses.dataclass(frozen=True)
class RepairWorkflowRequest:
    task_id: str
    worktree: Path
    worktree_root: Path
    declared_scope: tuple[str, ...]
    expected_branch: str
    remote: str
    merge_target: str
    require_clean: bool
    require_pr: bool
    pull_request: Mapping[str, Any] | None = None


@dataclasses.dataclass(frozen=True)
class RepairStatusEntry:
    index_status: str
    worktree_status: str
    path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "index": self.index_status,
            "worktree": self.worktree_status,
            "path": self.path,
        }


@dataclasses.dataclass(frozen=True)
class RepairWorkflowSnapshot:
    task_id: str
    worktree: str
    branch: str
    expected_branch: str
    remote: str
    remote_url: str
    merge_target: str
    validation_commit: str
    upstream: str | None
    declared_scope: tuple[str, ...]
    clean: bool
    dirty_entries: tuple[RepairStatusEntry, ...]
    staged_paths: tuple[str, ...]
    pull_request: Mapping[str, Any] | None
    require_pr: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "worktree": self.worktree,
            "branch": self.branch,
            "expected_branch": self.expected_branch,
            "remote": self.remote,
            "remote_url": self.remote_url,
            "merge_target": self.merge_target,
            "validation_commit": self.validation_commit,
            "upstream": self.upstream,
            "declared_scope": list(self.declared_scope),
            "scope_enforced": True,
            "clean": self.clean,
            "dirty_entries": [entry.to_dict() for entry in self.dirty_entries],
            "staged_paths": list(self.staged_paths),
            "pull_request": dict(self.pull_request) if self.pull_request else None,
            "require_pr": self.require_pr,
        }


@dataclasses.dataclass(frozen=True)
class RepairWorktreePreparation:
    created: bool
    source_repo_url: str
    repair_metadata: Mapping[str, Any]
    workflow: RepairWorkflowSnapshot

    def to_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "existing": not self.created,
            "source_repo_url": _sanitize_remote_url(self.source_repo_url),
            "repair": dict(self.repair_metadata),
            "repairMetadata": dict(self.repair_metadata),
            "workflow": self.workflow.to_dict(),
        }


class AssistantRepairWorkflow:
    """Validate repair-mode task branch/worktree metadata."""

    def __init__(
        self,
        environ: Mapping[str, str] | None = None,
        *,
        git_runner: GitRunner | None = None,
        pr_lookup: PullRequestLookup | None = None,
    ) -> None:
        self._environ = dict(environ if environ is not None else os.environ)
        self._git_runner = git_runner or _run_command
        self._pr_lookup = pr_lookup or _lookup_pr_with_gh

    def validate(
        self,
        metadata: Mapping[str, Any],
        *,
        require_pr: bool | None = None,
        require_clean: bool | None = None,
    ) -> RepairWorkflowSnapshot:
        request = self.request_from_metadata(
            metadata,
            require_pr=require_pr,
            require_clean=require_clean,
        )
        return self.validate_request(request)

    def prepare(self, metadata: Mapping[str, Any]) -> RepairWorktreePreparation:
        """Create or validate a clean repair task worktree.

        The repository source is controlled by adapter environment, not by the
        browser.  Callers provide task identity and scope; this method prepares a
        task branch under the configured repair root and then runs the same
        validation used before provider execution.
        """
        prepared_metadata = self.prepare_metadata(metadata)
        request = self.request_from_metadata(prepared_metadata, require_clean=True, require_pr=False)
        repo_source_url = self._repair_repo_source_url(metadata)
        remote_url = self._repair_remote_url(
            metadata,
            repo_source_url=repo_source_url,
            remote=request.remote,
        )

        request.worktree_root.mkdir(parents=True, exist_ok=True)
        created = False
        if not request.worktree.exists():
            created = True
            self._git(
                ["clone", "--origin", request.remote, repo_source_url, request.worktree.as_posix()],
                cwd=request.worktree_root,
                failure_code="REPAIR_WORKTREE_CLONE_FAILED",
                failure_message="Failed to clone repair repository source.",
            )
            self._configure_prepared_worktree(request.worktree)
            if remote_url:
                self._git(["remote", "set-url", request.remote, remote_url], cwd=request.worktree)
            self._git(["fetch", request.remote, request.merge_target], cwd=request.worktree, required=False)
            base_ref = self._select_base_ref(request.worktree, request.remote, request.merge_target)
            self._git(["checkout", "-B", request.expected_branch, base_ref], cwd=request.worktree)
            self._git(
                ["branch", "--set-upstream-to", f"{request.remote}/{request.merge_target}", request.expected_branch],
                cwd=request.worktree,
                required=False,
            )

        workflow = self.validate_request(request)
        return RepairWorktreePreparation(
            created=created,
            source_repo_url=repo_source_url,
            repair_metadata={
                "task_id": request.task_id,
                "taskId": request.task_id,
                "task_worktree": request.worktree.as_posix(),
                "taskWorktree": request.worktree.as_posix(),
                "declared_scope": list(request.declared_scope),
                "declaredScope": list(request.declared_scope),
                "expected_branch": request.expected_branch,
                "expectedBranch": request.expected_branch,
                "remote": request.remote,
                "merge_target": request.merge_target,
                "mergeTarget": request.merge_target,
                "require_clean": True,
                "requireClean": True,
                "repo_key": str(prepared_metadata.get("repo_key") or "pantheon"),
                "repoKey": str(prepared_metadata.get("repo_key") or "pantheon"),
            },
            workflow=workflow,
        )

    def prepare_metadata(self, metadata: Mapping[str, Any]) -> dict[str, Any]:
        task_id = str(metadata.get("task_id") or metadata.get("taskId") or "").strip()
        if not task_id:
            raise AssistantRepairWorkflowError(
                "REPAIR_TASK_ID_REQUIRED",
                "Repair worktree preparation requires task_id.",
                details={"required": ["task_id"]},
            )
        repo_key = _repo_key(metadata, self._environ)
        root = _resolve_path(
            self._environ.get("PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT", DEFAULT_REPAIR_WORKTREE_ROOT)
        )
        worktree_raw = str(metadata.get("task_worktree") or metadata.get("taskWorktree") or "").strip()
        if worktree_raw:
            worktree = _resolve_path(worktree_raw, base=root)
        else:
            worktree = root / _safe_worktree_leaf(repo_key) / _safe_worktree_leaf(task_id)

        prepared = dict(metadata)
        prepared["task_id"] = task_id
        prepared["repo_key"] = repo_key
        prepared["task_worktree"] = worktree.as_posix()
        prepared.setdefault(
            "expected_branch",
            f"{self._environ.get('PANTHEON_ASSISTANT_TASK_BRANCH_PREFIX', DEFAULT_TASK_BRANCH_PREFIX)}{task_id}",
        )
        prepared.setdefault(
            "remote",
            self._environ.get("PANTHEON_ASSISTANT_REPAIR_REMOTE") or DEFAULT_REMOTE,
        )
        prepared.setdefault(
            "merge_target",
            self._environ.get("PANTHEON_ASSISTANT_REPAIR_MERGE_TARGET") or DEFAULT_MERGE_TARGET,
        )
        prepared["require_clean"] = True
        return prepared

    def request_from_metadata(
        self,
        metadata: Mapping[str, Any],
        *,
        require_pr: bool | None = None,
        require_clean: bool | None = None,
    ) -> RepairWorkflowRequest:
        task_id = str(metadata.get("task_id") or metadata.get("taskId") or "").strip()
        worktree_raw = str(metadata.get("task_worktree") or metadata.get("taskWorktree") or "").strip()
        if not task_id or not worktree_raw:
            raise AssistantRepairWorkflowError(
                "REPAIR_METADATA_REQUIRED",
                "kernel_repair requires task_id and task_worktree metadata.",
                details={"required": ["task_id", "task_worktree"]},
            )

        root = _resolve_path(
            self._environ.get("PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT", DEFAULT_REPAIR_WORKTREE_ROOT)
        )
        worktree = _resolve_path(worktree_raw, base=root)
        try:
            worktree.relative_to(root)
        except ValueError as exc:
            raise AssistantRepairWorkflowError(
                "REPAIR_WORKTREE_OUTSIDE_ROOT",
                "kernel_repair task_worktree must be inside PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT.",
                details={"worktree": worktree.as_posix(), "worktree_root": root.as_posix()},
            ) from exc

        scope_values = _metadata_list(
            metadata,
            "declared_scope",
            "declaredScope",
            "scope",
            env_value=self._environ.get("PANTHEON_ASSISTANT_REPAIR_DECLARED_SCOPE"),
        )
        declared_scope = _normalize_scope(scope_values, worktree=worktree)
        if not declared_scope:
            raise AssistantRepairWorkflowError(
                "REPAIR_SCOPE_REQUIRED",
                "kernel_repair requires a non-empty declared_scope for repository changes.",
                details={"required": ["declared_scope"]},
            )

        expected_branch = str(
            metadata.get("expected_branch")
            or metadata.get("expectedBranch")
            or f"{self._environ.get('PANTHEON_ASSISTANT_TASK_BRANCH_PREFIX', DEFAULT_TASK_BRANCH_PREFIX)}{task_id}"
        ).strip()
        remote = str(
            metadata.get("remote")
            or self._environ.get("PANTHEON_ASSISTANT_REPAIR_REMOTE")
            or DEFAULT_REMOTE
        ).strip()
        merge_target = str(
            metadata.get("merge_target")
            or metadata.get("mergeTarget")
            or self._environ.get("PANTHEON_ASSISTANT_REPAIR_MERGE_TARGET")
            or DEFAULT_MERGE_TARGET
        ).strip()
        if not expected_branch or not remote or not merge_target:
            raise AssistantRepairWorkflowError(
                "REPAIR_WORKFLOW_TARGET_REQUIRED",
                "kernel_repair requires expected_branch, remote, and merge_target metadata.",
                details={
                    "expected_branch": expected_branch,
                    "remote": remote,
                    "merge_target": merge_target,
                },
            )

        if require_clean is not None:
            resolved_require_clean = require_clean
        else:
            resolved_require_clean = _coerce_bool(
                metadata,
                "require_clean",
                "requireClean",
                env_value=self._environ.get("PANTHEON_ASSISTANT_REPAIR_REQUIRE_CLEAN"),
                default=True,
            )
        if require_pr is not None:
            resolved_require_pr = require_pr
        else:
            resolved_require_pr = _coerce_bool(
                metadata,
                "require_pr",
                "requirePr",
                env_value=self._environ.get("PANTHEON_ASSISTANT_REPAIR_REQUIRE_PR"),
                default=False,
            )
        return RepairWorkflowRequest(
            task_id=task_id,
            worktree=worktree,
            worktree_root=root,
            declared_scope=declared_scope,
            expected_branch=expected_branch,
            remote=remote,
            merge_target=merge_target,
            require_clean=resolved_require_clean,
            require_pr=resolved_require_pr,
            pull_request=_pull_request_from_metadata(metadata),
        )

    def validate_request(self, request: RepairWorkflowRequest) -> RepairWorkflowSnapshot:
        if not request.worktree.is_dir():
            raise AssistantRepairWorkflowError(
                "REPAIR_WORKTREE_MISSING",
                "kernel_repair task_worktree is not available.",
                status_code=404,
                details={"worktree": request.worktree.as_posix()},
            )

        repo_root = _resolve_path(self._git(["rev-parse", "--show-toplevel"], cwd=request.worktree))
        if repo_root != request.worktree:
            raise AssistantRepairWorkflowError(
                "REPAIR_WORKTREE_NOT_REPO_ROOT",
                "kernel_repair task_worktree must point at the git repository root.",
                details={"worktree": request.worktree.as_posix(), "repo_root": repo_root.as_posix()},
            )

        branch = self._git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=request.worktree)
        if branch == "HEAD":
            raise AssistantRepairWorkflowError(
                "REPAIR_DETACHED_HEAD",
                "kernel_repair requires a named task branch, not detached HEAD.",
                status_code=409,
            )
        if branch != request.expected_branch:
            raise AssistantRepairWorkflowError(
                "REPAIR_BRANCH_MISMATCH",
                "kernel_repair requires the expected task branch.",
                status_code=409,
                details={"branch": branch, "expected_branch": request.expected_branch},
            )

        remote_url = self._git(
            ["remote", "get-url", request.remote],
            cwd=request.worktree,
            failure_code="REPAIR_REMOTE_MISSING",
            failure_message="kernel_repair requires the configured git remote.",
        )
        validation_commit = self._git(["rev-parse", "HEAD"], cwd=request.worktree)
        upstream = self._git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], cwd=request.worktree, required=False)

        status_entries = _parse_status(self._git(["status", "--porcelain=v1", "--untracked-files=all"], cwd=request.worktree))
        staged_paths = tuple(sorted({
            entry.path
            for entry in status_entries
            if entry.index_status not in {" ", "?"}
        }))

        staged_outside = [path for path in staged_paths if not _within_scope(path, request.declared_scope)]
        if staged_outside:
            raise AssistantRepairWorkflowError(
                "REPAIR_STAGED_SCOPE_VIOLATION",
                "kernel_repair staged files must stay inside declared_scope.",
                status_code=409,
                details={"staged_outside_scope": staged_outside, "declared_scope": list(request.declared_scope)},
            )

        dirty_paths = [entry.path for entry in status_entries]
        dirty_outside = [path for path in dirty_paths if not _within_scope(path, request.declared_scope)]
        if dirty_outside:
            raise AssistantRepairWorkflowError(
                "REPAIR_DIRTY_UNRELATED",
                "kernel_repair worktree has dirty files outside declared_scope.",
                status_code=409,
                details={"dirty_outside_scope": dirty_outside, "declared_scope": list(request.declared_scope)},
            )
        if request.require_clean and status_entries:
            raise AssistantRepairWorkflowError(
                "REPAIR_WORKTREE_DIRTY",
                "kernel_repair requires a clean task worktree before provider execution.",
                status_code=409,
                details={"dirty_entries": [entry.to_dict() for entry in status_entries]},
            )

        pull_request = request.pull_request
        if pull_request is None and request.require_pr:
            pull_request = self._pr_lookup(request.worktree, branch)
        pull_request = _normalize_pr(pull_request, merge_target=request.merge_target)
        if request.require_pr and pull_request is None:
            raise AssistantRepairWorkflowError(
                "REPAIR_PR_REQUIRED",
                "kernel_repair closeout requires an associated pull request.",
                status_code=409,
                details={"branch": branch, "merge_target": request.merge_target},
            )

        return RepairWorkflowSnapshot(
            task_id=request.task_id,
            worktree=request.worktree.as_posix(),
            branch=branch,
            expected_branch=request.expected_branch,
            remote=request.remote,
            remote_url=_sanitize_remote_url(remote_url),
            merge_target=request.merge_target,
            validation_commit=validation_commit,
            upstream=upstream or None,
            declared_scope=request.declared_scope,
            clean=not status_entries,
            dirty_entries=tuple(status_entries),
            staged_paths=staged_paths,
            pull_request=pull_request,
            require_pr=request.require_pr,
        )

    def _git(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        required: bool = True,
        failure_code: str = "REPAIR_GIT_COMMAND_FAILED",
        failure_message: str = "kernel_repair git workflow validation failed.",
    ) -> str:
        completed = self._git_runner(["git", *args], cwd)
        if completed.returncode == 0:
            return (completed.stdout or "").strip()
        if not required:
            return ""
        detail = (completed.stderr or completed.stdout or "").strip()
        raise AssistantRepairWorkflowError(
            failure_code,
            failure_message,
            status_code=409,
            details={"git_args": list(args), "detail": detail},
        )

    def _repair_repo_source_url(self, metadata: Mapping[str, Any]) -> str:
        repo_key = _repo_key(metadata, self._environ)
        value = str(
            metadata.get("repo_source_url")
            or metadata.get("repoSourceUrl")
            or self._environ.get(_repo_env_name("PANTHEON_ASSISTANT_REPAIR_REPO_URL", repo_key))
            or self._environ.get("PANTHEON_ASSISTANT_REPAIR_REPO_URL")
            or ""
        ).strip()
        if not value:
            raise AssistantRepairWorkflowError(
                "REPAIR_REPO_URL_NOT_CONFIGURED",
                "Repair worktree preparation requires PANTHEON_ASSISTANT_REPAIR_REPO_URL.",
                status_code=503,
            )
        return value

    def _repair_remote_url(self, metadata: Mapping[str, Any], *, repo_source_url: str, remote: str) -> str:
        repo_key = _repo_key(metadata, self._environ)
        configured = str(
            metadata.get("remote_url")
            or metadata.get("remoteUrl")
            or self._environ.get(_repo_env_name("PANTHEON_ASSISTANT_REPAIR_REMOTE_URL", repo_key))
            or self._environ.get("PANTHEON_ASSISTANT_REPAIR_REMOTE_URL")
            or ""
        ).strip()
        if configured:
            return configured
        source_path = _repo_source_path(repo_source_url)
        if source_path is not None and source_path.exists():
            completed = self._git_runner(["git", "remote", "get-url", remote], source_path)
            if completed.returncode == 0 and (completed.stdout or "").strip():
                return (completed.stdout or "").strip()
        return repo_source_url

    def _configure_prepared_worktree(self, worktree: Path) -> None:
        email = self._environ.get("PANTHEON_ASSISTANT_GIT_USER_EMAIL", DEFAULT_GIT_USER_EMAIL)
        name = self._environ.get("PANTHEON_ASSISTANT_GIT_USER_NAME", DEFAULT_GIT_USER_NAME)
        self._git(["config", "user.email", email], cwd=worktree)
        self._git(["config", "user.name", name], cwd=worktree)

    def _select_base_ref(self, worktree: Path, remote: str, merge_target: str) -> str:
        for candidate in (f"{remote}/{merge_target}", merge_target, "HEAD"):
            completed = self._git_runner(["git", "rev-parse", "--verify", candidate], worktree)
            if completed.returncode == 0:
                return candidate
        raise AssistantRepairWorkflowError(
            "REPAIR_BASE_REF_MISSING",
            "Repair worktree preparation could not resolve the merge target base ref.",
            status_code=409,
            details={"merge_target": merge_target, "remote": remote},
        )


def _run_command(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _safe_worktree_leaf(task_id: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", task_id).strip(".-").lower()
    if not clean:
        raise AssistantRepairWorkflowError(
            "REPAIR_TASK_ID_INVALID",
            "Repair task_id must contain at least one filesystem-safe character.",
            details={"task_id": task_id},
    )
    if len(clean) > 96:
        digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:8]
        clean = f"{clean[:87]}-{digest}"
    return clean


def _repo_key(metadata: Mapping[str, Any], environ: Mapping[str, str]) -> str:
    raw = str(
        metadata.get("repo_key")
        or metadata.get("repoKey")
        or metadata.get("repository")
        or environ.get("PANTHEON_ASSISTANT_REPAIR_DEFAULT_REPO_KEY")
        or "pantheon"
    ).strip()
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip(".-").lower()
    if not clean:
        raise AssistantRepairWorkflowError(
            "REPAIR_REPO_KEY_INVALID",
            "Repair repository key must contain at least one filesystem-safe character.",
            details={"repo_key": raw},
        )
    return clean


def _repo_env_name(prefix: str, repo_key: str) -> str:
    suffix = re.sub(r"[^A-Z0-9]+", "_", repo_key.upper()).strip("_")
    return f"{prefix}_{suffix}" if suffix else prefix


def _repo_source_path(value: str) -> Path | None:
    if "://" in value:
        try:
            parsed = urlsplit(value)
        except ValueError:
            return None
        if parsed.scheme != "file":
            return None
        return Path(parsed.path).expanduser().resolve()
    return Path(value).expanduser().resolve()


def _lookup_pr_with_gh(worktree: Path, branch: str) -> Mapping[str, Any] | None:
    try:
        completed = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                branch,
                "--json",
                "number,state,mergeStateStatus,mergedAt,mergeCommit,url,baseRefName,headRefName",
            ],
            cwd=worktree,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, Mapping) else None


def _resolve_path(value: str | os.PathLike[str], *, base: Path | None = None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() and base is not None:
        path = base / path
    return path.resolve()


def _metadata_list(metadata: Mapping[str, Any], *keys: str, env_value: str | None = None) -> list[str]:
    raw: Any = None
    for key in keys:
        if key in metadata:
            raw = metadata[key]
            break
    if raw is None:
        raw = env_value
    if raw is None:
        return []
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    if isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
        return [str(item).strip() for item in raw if str(item).strip()]
    raise AssistantRepairWorkflowError(
        "REPAIR_SCOPE_SCHEMA_ERROR",
        "kernel_repair declared_scope must be a list of paths or comma-delimited string.",
        details={"declared_scope_type": type(raw).__name__},
    )


def _normalize_scope(values: Sequence[str], *, worktree: Path) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in values:
        candidate = Path(raw)
        if candidate.is_absolute():
            try:
                candidate = candidate.resolve().relative_to(worktree)
            except ValueError as exc:
                raise AssistantRepairWorkflowError(
                    "REPAIR_SCOPE_OUTSIDE_WORKTREE",
                    "kernel_repair declared_scope entries must be inside the task worktree.",
                    details={"scope": str(raw), "worktree": worktree.as_posix()},
                ) from exc
        entry = PurePosixPath(candidate.as_posix())
        parts = entry.parts
        if not parts or entry.as_posix() in {"", "."} or ".." in parts or ".git" in parts:
            raise AssistantRepairWorkflowError(
                "REPAIR_SCOPE_INVALID",
                "kernel_repair declared_scope entries must be repo-relative paths.",
                details={"scope": str(raw)},
            )
        normalized.append(entry.as_posix().rstrip("/"))
    return tuple(sorted(set(normalized)))


def _coerce_bool(
    metadata: Mapping[str, Any],
    *keys: str,
    env_value: str | None = None,
    default: bool,
) -> bool:
    raw: Any = None
    for key in keys:
        if key in metadata:
            raw = metadata[key]
            break
    if raw is None:
        raw = env_value
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(raw)


def _pull_request_from_metadata(metadata: Mapping[str, Any]) -> Mapping[str, Any] | None:
    raw = metadata.get("pull_request") or metadata.get("pullRequest") or metadata.get("pr")
    return raw if isinstance(raw, Mapping) and raw else None


def _normalize_pr(pr: Mapping[str, Any] | None, *, merge_target: str) -> Mapping[str, Any] | None:
    if pr is None:
        return None
    base = str(pr.get("baseRefName") or pr.get("base_ref_name") or pr.get("base") or "").strip()
    if base and base != merge_target:
        raise AssistantRepairWorkflowError(
            "REPAIR_PR_MERGE_TARGET_MISMATCH",
            "kernel_repair pull request base must match merge_target.",
            status_code=409,
            details={"pr_base": base, "merge_target": merge_target},
        )
    normalized: dict[str, Any] = {
        "number": pr.get("number"),
        "url": pr.get("url"),
        "state": pr.get("state"),
        "merge_state": pr.get("mergeStateStatus") or pr.get("merge_state"),
        "merged_at": pr.get("mergedAt") or pr.get("merged_at"),
        "base": base or merge_target,
        "head": pr.get("headRefName") or pr.get("head_ref_name") or pr.get("head"),
    }
    merge_commit = pr.get("mergeCommit") or pr.get("merge_commit")
    if isinstance(merge_commit, Mapping):
        normalized["merge_commit"] = merge_commit.get("oid") or merge_commit.get("sha")
    elif merge_commit:
        normalized["merge_commit"] = merge_commit
    return {key: value for key, value in normalized.items() if value not in {None, ""}}


def _parse_status(output: str) -> list[RepairStatusEntry]:
    entries: list[RepairStatusEntry] = []
    for raw in output.splitlines():
        if not raw:
            continue
        if len(raw) < 4:
            continue
        index_status = raw[0]
        worktree_status = raw[1]
        path = raw[3:]
        if " -> " in path:
            src, dst = path.split(" -> ", 1)
            for part in (src, dst):
                entries.append(
                    RepairStatusEntry(
                        index_status=index_status,
                        worktree_status=worktree_status,
                        path=_normalize_status_path(part),
                    )
                )
        else:
            entries.append(
                RepairStatusEntry(
                    index_status=index_status,
                    worktree_status=worktree_status,
                    path=_normalize_status_path(path),
                )
            )
    return entries


def _normalize_status_path(path: str) -> str:
    return PurePosixPath(path.strip().strip('"')).as_posix()


def _within_scope(path: str, scope: Sequence[str]) -> bool:
    normalized = _normalize_status_path(path)
    return any(normalized == entry or normalized.startswith(f"{entry}/") for entry in scope)


def _sanitize_remote_url(value: str) -> str:
    if "://" not in value:
        return value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "[REDACTED_REMOTE_URL]"
    if "@" not in parsed.netloc:
        return value
    host = parsed.netloc.rsplit("@", 1)[1]
    return urlunsplit((parsed.scheme, f"[REDACTED]@{host}", parsed.path, parsed.query, parsed.fragment))
