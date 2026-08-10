#!/usr/bin/env python3
"""Canonical review-before-merge gate for Pantheon task PRs.

Every task PR helper (`task_finalize.sh`, `safe_pr.sh`, `auto_integrator.py`)
asks this module one question before it hands merge authority to GitHub:

    may `task/<TASK-ID>` merge into the delivery branch right now?

The answer is derived from canonical task state only -- the task row in
`ai-status.json` under the bound status root plus the immutable
`review_approved` record in the activity audit.  Nothing in the PR itself, no
label, and no helper flag can open the gate.

Failure modes fail closed.  A missing task row, an unreadable status root, an
ambiguous PR selection, an unknown head, an unparseable timestamp, a reviewer
identity that does not match the canonical row, or a head that moved after the
approval all resolve to "do not merge, do not enable auto-merge".

Two policies exist:

``review_before_merge``
    The default and the only policy available to a task whose canonical
    contract assigns an independent reviewer.  Merge authority is withheld
    until the exact assigned reviewer has approved the exact PR head.

``merge_then_review``
    Preserved for the pre-existing integration paths that are explicitly
    declared as merge-then-review *and* whose canonical contract permits it
    (no independent reviewer is required for the task).  A declaration alone
    never downgrades a task that requires independent review.

The 2026-07-26 premature merges of PRs #4212, #4213 and #4214 are the
regression this module exists to prevent: three task PRs were opened with
`gh pr merge --auto --merge` and landed in `dev` one to two minutes later,
before any reviewer had looked at them.  #4212 was independently rejected
eight minutes *after* it was already merged.

Seven further live regressions from the same evening extend the contract:

* #4217 landed by a plain `gh pr merge` with `autoMergeRequest=null`; the
  gate therefore cannot be an auto-merge-only guard.
* #4222 enabled auto-merge and completed 67 seconds later; the reviewer's
  rejection arrived twenty minutes after the merge.
* #4225 first attempted a premature auto-merge enable, then landed as a
  direct merge by the owner's own GitHub credential while Human/Ops had
  posted exact-head do-not-merge notes.
* #4226 was the third unreviewed merge on that one task branch.
* #4227 shows why an auto-merge *request* is the hazard rather than the
  merge call: auto-merge was enabled at 23:10:54Z, the head then moved at
  23:13:21Z, and GitHub merged the newer, never-reviewed head at 23:14:41Z.
  Its payload was docs-only, which does not waive review-before-merge.
* #4230 merged with every required check green and `reviewDecision` empty:
  green CI is not canonical reviewer approval.

Nothing on the PR side unlocks the gate: a GitHub approving review, the
identity that pressed merge or enabled auto-merge, green CI, and any risk
or payload claim recorded on the task row are all ignored.  Every Pantheon
agent pushes through one GitHub account, so GitHub-side identity cannot
distinguish an owner from a reviewer at all.

An approval is structurally bound to what it approved.  `scripts/ai_status.py`
records the reviewed PR number, head sha, head branch and expected base inside
the immutable `review_approved` audit event, and this module compares those
exact identities against the PR standing now.  A timestamp comparison alone was
not enough: approving head `bbbb...` and then replacing the PR head with an
*older* commit `cccc...` -- committed before the approval, so "not newer than
the approval" still held -- opened the gate for a head no reviewer ever saw.
An approval that carries no binding is unusable, not permissive.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[2]
STATUS_ROOT_ENV = "PANTHEON_STATUS_ROOT"
DEFAULT_DEV_BRANCH = "dev"
DEFAULT_TASK_PREFIX = "task/"

POLICY_REVIEW_BEFORE_MERGE = "review_before_merge"
POLICY_MERGE_THEN_REVIEW = "merge_then_review"

#: Canonical statuses in which an approval record can be binding.
APPROVED_STATUSES = {"review_approved", "done"}
#: Events that revoke a previously recorded approval.  `reopen` is the
#: canonical rejection path, `blocker` re-opens the delivery question, and
#: `assign` rebinds owner/reviewer identity after the fact.
REVOCATION_EVENT_TYPES = {"reopen", "blocker", "assign"}
APPROVAL_EVENT_TYPE = "review_approved"
#: A `note` is normally commentary, but PRs #4225 and #4222 were merged while
#: exact-head "do not merge" / "changes required" notes stood in the audit.
#: A note carrying one of these markers revokes an approval.  This signal can
#: only ever block a merge, never unlock one, so a false positive costs a
#: re-approval rather than an unreviewed delivery.
REVOCATION_NOTE_TYPES = {"note"}
REVOCATION_NOTE_MARKERS = (
    "do not merge",
    "do-not-merge",
    "changes required",
    "changes-required",
    "changes requested",
    "rejects",
    "rejected",
    "revert",
)

#: Claims an owner may record on a task row that describe how risky or how
#: small a delivery is.  They are read only so the gate can say out loud that
#: it ignored them; none of them waives independent review.
WAIVER_CLAIM_KEYS = (
    "risk",
    "risk_level",
    "payload",
    "payload_class",
    "low_risk",
    "docs_only",
    "evidence_only",
    "review_waived",
    "skip_review",
    "no_review_required",
)

ACTIVITY_LOG_NAME = "ai-activity-log.jsonl"
ACTIVITY_ARCHIVE_SUBDIR = Path("archive") / "logs"
ACTIVITY_LEGACY_ARCHIVE_SUBDIR = Path(".orchestrator") / "logs" / "activity-log-archive"

OID_RE = re.compile(r"^[0-9a-f]{40}$")
#: Key carrying the reviewed PR identity on a `review_approved` audit event and
#: on the canonical task row.  `scripts/ai_status.py::command_approve` writes
#: it; nothing else may.
APPROVAL_BINDING_KEY = "review_binding"
#: Approval events are recorded by the same wrapper that stamps the task row,
#: so a small amount of clock skew between the two is tolerable; anything
#: beyond this is treated as a non-credible timestamp.
CLOCK_SKEW_SECONDS = 120


class TaskReviewGateError(RuntimeError):
    """Raised only for caller mistakes; policy problems become decisions."""


# --------------------------------------------------------------------------
# canonical identity helpers
# --------------------------------------------------------------------------


def normalize_agent(value: Any) -> str:
    """Case-fold an agent name for identity comparison without renaming it."""

    return str(value or "").strip().casefold()


def normalize_status(value: Any) -> str:
    return str(value or "").strip().lower()


def parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def resolve_status_root(explicit: Path | str | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    raw = str(os.environ.get(STATUS_ROOT_ENV) or "").strip()
    if raw:
        return Path(raw)
    return ROOT


# --------------------------------------------------------------------------
# canonical task contract
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskContract:
    """The merge-relevant slice of one canonical task row."""

    task_id: str
    source: str  # active | archive | missing | unreadable
    owner: str = ""
    reviewer: str = ""
    status: str = ""
    policy: str = POLICY_REVIEW_BEFORE_MERGE
    declared_policy: str = ""
    declaration_honored: bool = False
    declaration_detail: str = ""
    declared_head_sha: str = ""
    declared_head_branch: str = ""
    ignored_waiver_claims: tuple[str, ...] = ()

    @property
    def requires_independent_review(self) -> bool:
        reviewer = normalize_agent(self.reviewer)
        return bool(reviewer) and reviewer != normalize_agent(self.owner)

    @property
    def gated(self) -> bool:
        return self.policy == POLICY_REVIEW_BEFORE_MERGE

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "source": self.source,
            "owner": self.owner,
            "reviewer": self.reviewer,
            "status": self.status,
            "policy": self.policy,
            "declared_policy": self.declared_policy,
            "declaration_honored": self.declaration_honored,
            "declaration_detail": self.declaration_detail,
            "declared_head_sha": self.declared_head_sha,
            "declared_head_branch": self.declared_head_branch,
            "requires_independent_review": self.requires_independent_review,
            "ignored_waiver_claims": list(self.ignored_waiver_claims),
        }


def _declared_policy(task: Mapping[str, Any]) -> str:
    """Read an explicit merge policy declaration from the canonical row."""

    for key in ("merge_policy", "review_merge_policy", "integration_policy"):
        raw = task.get(key)
        if raw is None:
            continue
        text = str(raw).strip().lower().replace("-", "_")
        if text:
            return text
    return ""


def _ignored_waiver_claims(task: Mapping[str, Any]) -> tuple[str, ...]:
    """List risk/payload claims on the row so the gate can say it ignored them.

    PR #4227 was defended after the fact as "docs/evidence only, live swap
    still blocked".  That may all be true and it still does not waive
    review-before-merge, so these keys are surfaced as diagnostics and are
    never consulted when resolving the policy.
    """

    found: list[str] = []
    for key in WAIVER_CLAIM_KEYS:
        if key not in task:
            continue
        value = task.get(key)
        if value is None or value is False or str(value).strip() == "":
            continue
        found.append(f"{key}={value}")
    return tuple(found)


def _declared_github_binding(task: Mapping[str, Any]) -> tuple[str, str]:
    meta = task.get("github")
    if not isinstance(meta, Mapping):
        return "", ""
    head_sha = str(meta.get("head_sha") or "").strip().lower()
    head_branch = str(meta.get("head_branch") or "").strip()
    return head_sha, head_branch


def contract_from_task_row(
    task: Mapping[str, Any],
    *,
    source: str = "active",
) -> TaskContract:
    """Resolve the merge policy that the canonical contract permits."""

    task_id = str(task.get("id") or "").strip()
    owner = str(task.get("owner") or "").strip()
    reviewer = str(task.get("reviewer") or "").strip()
    status = normalize_status(task.get("status"))
    declared = _declared_policy(task)
    head_sha, head_branch = _declared_github_binding(task)
    waiver_claims = _ignored_waiver_claims(task)

    policy = POLICY_REVIEW_BEFORE_MERGE
    honored = False
    detail = ""
    independent = bool(normalize_agent(reviewer)) and normalize_agent(reviewer) != normalize_agent(owner)

    if declared == POLICY_MERGE_THEN_REVIEW:
        if independent:
            detail = (
                f"task {task_id or '?'} declares merge_then_review but its canonical "
                f"contract assigns independent reviewer {reviewer!r} against owner "
                f"{owner!r}; the declaration is not honored"
            )
        else:
            policy = POLICY_MERGE_THEN_REVIEW
            honored = True
            detail = (
                f"task {task_id or '?'} declares merge_then_review and its canonical "
                "contract requires no independent review"
            )
    elif declared and declared != POLICY_REVIEW_BEFORE_MERGE:
        detail = f"unknown declared merge policy {declared!r}; defaulting to review_before_merge"

    if waiver_claims and policy == POLICY_REVIEW_BEFORE_MERGE:
        claims = ", ".join(waiver_claims)
        note = f"ignored risk/payload claims on {task_id or '?'}: {claims}"
        detail = f"{detail}; {note}" if detail else note

    return TaskContract(
        task_id=task_id,
        source=source,
        owner=owner,
        reviewer=reviewer,
        status=status,
        policy=policy,
        declared_policy=declared,
        declaration_honored=honored,
        declaration_detail=detail,
        declared_head_sha=head_sha,
        declared_head_branch=head_branch,
        ignored_waiver_claims=waiver_claims,
    )


def _load_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    return json.loads(text)


def canonical_archive_task_path(root: Path, task_id: str) -> Path:
    """Match ``task_archive.archive_task_path`` without importing its runtime.

    The archive writer preserves task-id case and percent-escapes every
    character except ``-_.``.  The merge gate is intentionally self-contained,
    but its lookup must use that exact filename contract.
    """

    slug = quote(task_id, safe="-_.")
    return root / "ai-task-archive" / "tasks" / f"{slug}.json"


def load_task_contract(
    task_id: str,
    *,
    status_root: Path | str | None = None,
    state: Mapping[str, Any] | None = None,
) -> TaskContract:
    """Read one task row from canonical state, failing closed when absent."""

    task_id = str(task_id or "").strip()
    if not task_id:
        raise TaskReviewGateError("task id is required")
    root = resolve_status_root(status_root)

    if state is None:
        status_file = root / "ai-status.json"
        try:
            state = _load_json(status_file)
        except (OSError, json.JSONDecodeError):
            return TaskContract(task_id=task_id, source="unreadable")
    if not isinstance(state, Mapping):
        return TaskContract(task_id=task_id, source="unreadable")

    tasks = state.get("tasks")
    if isinstance(tasks, list):
        for raw in tasks:
            if isinstance(raw, Mapping) and str(raw.get("id") or "").strip() == task_id:
                return contract_from_task_row(raw, source="active")

    archive = canonical_archive_task_path(root, task_id)
    if archive.is_file():
        try:
            record = _load_json(archive)
        except (OSError, json.JSONDecodeError):
            return TaskContract(task_id=task_id, source="unreadable")
        if isinstance(record, Mapping):
            archived = record.get("task")
            if isinstance(archived, Mapping):
                return contract_from_task_row(archived, source="archive")

    return TaskContract(task_id=task_id, source="missing")


# --------------------------------------------------------------------------
# canonical approval record
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ApprovalRecord:
    """The last `review_approved` audit event and anything that revoked it."""

    task_id: str
    reviewer: str = ""
    approved_at: datetime | None = None
    approved_at_text: str = ""
    message: str = ""
    revoked_by: str = ""
    revoked_at_text: str = ""
    revocation_type: str = ""
    scan_error: str = ""
    approved_pr_number: int | None = None
    approved_head_sha: str = ""
    approved_head_branch: str = ""
    approved_base_branch: str = ""
    binding_error: str = ""

    @property
    def present(self) -> bool:
        return self.approved_at is not None

    @property
    def revoked(self) -> bool:
        return bool(self.revocation_type)

    @property
    def binding_present(self) -> bool:
        """True only when the approval names the PR, head, and base it covers.

        A free-text approval message is not a binding: the reviewer may have
        typed the head into it, but nothing compares it and nothing stops the
        head from being replaced afterwards.
        """

        return bool(
            self.approved_pr_number is not None
            and self.approved_head_sha
            and self.approved_base_branch
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "reviewer": self.reviewer,
            "approved_at": self.approved_at_text,
            "revoked_by": self.revoked_by,
            "revoked_at": self.revoked_at_text,
            "revocation_type": self.revocation_type,
            "scan_error": self.scan_error,
            "approved_pr_number": self.approved_pr_number,
            "approved_head_sha": self.approved_head_sha,
            "approved_head_branch": self.approved_head_branch,
            "approved_base_branch": self.approved_base_branch,
            "binding_present": self.binding_present,
            "binding_error": self.binding_error,
        }


def normalize_pr_number(value: Any) -> int | None:
    """Read a PR number from canonical evidence or a GitHub payload."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = str(value or "").strip().lstrip("#")
    if not text.isdigit():
        return None
    number = int(text)
    return number if number > 0 else None


def parse_approval_binding(event: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """Extract the reviewed PR identity recorded on an approval event.

    Returns ``({}, "")`` for a legacy approval that carries no binding at all,
    and ``({}, "<problem>")`` when a binding is present but unusable.  Both
    resolve to "do not merge"; they are distinguished so the operator is told
    whether to re-approve or to repair a corrupted audit line.
    """

    raw = event.get(APPROVAL_BINDING_KEY)
    if raw is None:
        return {}, ""
    if not isinstance(raw, Mapping):
        return {}, f"{APPROVAL_BINDING_KEY} is not an object"

    head_sha = str(raw.get("head_sha") or "").strip().lower()
    if not head_sha:
        return {}, f"{APPROVAL_BINDING_KEY} carries no head_sha"
    if not OID_RE.match(head_sha):
        return {}, f"{APPROVAL_BINDING_KEY}.head_sha {head_sha!r} is not a 40-hex oid"

    pr_number = normalize_pr_number(raw.get("pr"))
    if pr_number is None:
        return {}, f"{APPROVAL_BINDING_KEY} carries no usable pr number"

    base_branch = str(raw.get("base") or "").strip()
    if not base_branch:
        return {}, f"{APPROVAL_BINDING_KEY} carries no expected base branch"

    return (
        {
            "pr": pr_number,
            "head_sha": head_sha,
            "head_branch": str(raw.get("head_branch") or "").strip(),
            "base": base_branch,
        },
        "",
    )


def _is_rejection_note(message: Any) -> bool:
    """True when a `note` event carries an explicit do-not-merge instruction."""

    text = str(message or "").lower()
    return any(marker in text for marker in REVOCATION_NOTE_MARKERS)


def _activity_sources(status_root: Path) -> list[Path]:
    active = status_root / ACTIVITY_LOG_NAME
    sources: list[Path] = []
    archive_dir = status_root / ACTIVITY_ARCHIVE_SUBDIR
    legacy_dir = status_root / ACTIVITY_LEGACY_ARCHIVE_SUBDIR
    if archive_dir.is_dir():
        sources.extend(sorted(archive_dir.glob(f"{ACTIVITY_LOG_NAME}-*.gz")))
    if legacy_dir.is_dir():
        sources.extend(sorted(legacy_dir.glob("ai-activity-log-*.jsonl.gz")))
    if active.is_file():
        sources.append(active)
    return sources


def _iter_source_lines(path: Path) -> Iterator[str]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            yield from handle
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        yield from handle


def load_approval_record(
    task_id: str,
    *,
    status_root: Path | str | None = None,
    events: Iterable[Mapping[str, Any]] | None = None,
) -> ApprovalRecord:
    """Find the newest approval for ``task_id`` and any later revocation.

    ``events`` lets fixtures inject an ordered audit slice; production callers
    read the bound status root's activity audit (active tail plus rotated
    archives) so a rotation cannot silently erase an approval.
    """

    task_id = str(task_id or "").strip()
    if not task_id:
        raise TaskReviewGateError("task id is required")

    if events is None:
        root = resolve_status_root(status_root)
        sources = _activity_sources(root)
        if not sources:
            return ApprovalRecord(task_id=task_id, scan_error="activity audit is unavailable")
        materialized: list[Mapping[str, Any]] = []
        for source in sources:
            try:
                for line in _iter_source_lines(source):
                    line = line.strip()
                    if not line or f'"{task_id}"' not in line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        return ApprovalRecord(
                            task_id=task_id,
                            scan_error=f"activity audit line in {source.name} is not valid JSON",
                        )
                    if isinstance(event, Mapping):
                        materialized.append(event)
            except OSError as exc:
                return ApprovalRecord(
                    task_id=task_id,
                    scan_error=f"cannot read activity audit {source.name}: {exc}",
                )
        events = materialized

    approval: ApprovalRecord | None = None
    revocation: tuple[str, str, str] | None = None
    for event in events:
        if not isinstance(event, Mapping):
            continue
        if str(event.get("task_id") or "").strip() != task_id:
            continue
        event_type = str(event.get("type") or "").strip()
        timestamp_text = str(event.get("ts") or "").strip()
        if event_type == APPROVAL_EVENT_TYPE:
            approved_at = parse_timestamp(timestamp_text)
            binding, binding_error = parse_approval_binding(event)
            approval = ApprovalRecord(
                task_id=task_id,
                reviewer=str(event.get("agent") or "").strip(),
                approved_at=approved_at,
                approved_at_text=timestamp_text,
                message=str(event.get("message") or ""),
                approved_pr_number=binding.get("pr"),
                approved_head_sha=str(binding.get("head_sha") or ""),
                approved_head_branch=str(binding.get("head_branch") or ""),
                approved_base_branch=str(binding.get("base") or ""),
                binding_error=binding_error,
            )
            revocation = None
            continue
        if approval is None:
            continue
        if event_type in REVOCATION_EVENT_TYPES:
            revocation = (
                str(event.get("agent") or "").strip(),
                timestamp_text,
                event_type,
            )
            continue
        if event_type in REVOCATION_NOTE_TYPES and _is_rejection_note(event.get("message")):
            revocation = (
                str(event.get("agent") or "").strip(),
                timestamp_text,
                f"{event_type}:do_not_merge",
            )

    if approval is None:
        return ApprovalRecord(task_id=task_id)
    if revocation is None:
        return approval
    return ApprovalRecord(
        task_id=approval.task_id,
        reviewer=approval.reviewer,
        approved_at=approval.approved_at,
        approved_at_text=approval.approved_at_text,
        message=approval.message,
        revoked_by=revocation[0],
        revoked_at_text=revocation[1],
        revocation_type=revocation[2],
        approved_pr_number=approval.approved_pr_number,
        approved_head_sha=approval.approved_head_sha,
        approved_head_branch=approval.approved_head_branch,
        approved_base_branch=approval.approved_base_branch,
        binding_error=approval.binding_error,
    )


# --------------------------------------------------------------------------
# gate decision
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GateDecision:
    task_id: str
    policy: str
    allow_merge: bool
    allow_auto_merge: bool
    reason: str
    detail: str
    head_oid: str = ""
    approved_at: str = ""
    revoke_auto_merge: bool = False
    contract: dict[str, Any] = field(default_factory=dict)
    approval: dict[str, Any] = field(default_factory=dict)
    auto_merge_request: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "policy": self.policy,
            "allow_merge": self.allow_merge,
            "allow_auto_merge": self.allow_auto_merge,
            "reason": self.reason,
            "detail": self.detail,
            "head_oid": self.head_oid,
            "approved_at": self.approved_at,
            "revoke_auto_merge": self.revoke_auto_merge,
            "contract": self.contract,
            "approval": self.approval,
            "auto_merge_request": self.auto_merge_request,
        }


def _blocked(
    contract: TaskContract,
    approval: ApprovalRecord | None,
    reason: str,
    detail: str,
    *,
    head_oid: str = "",
    revoke_auto_merge: bool = True,
    auto_merge_request: Mapping[str, Any] | None = None,
) -> GateDecision:
    return GateDecision(
        task_id=contract.task_id,
        policy=contract.policy,
        allow_merge=False,
        allow_auto_merge=False,
        reason=reason,
        detail=detail,
        head_oid=head_oid,
        approved_at=approval.approved_at_text if approval else "",
        revoke_auto_merge=revoke_auto_merge,
        contract=contract.as_dict(),
        approval=approval.as_dict() if approval else {},
        auto_merge_request=dict(auto_merge_request or {}),
    )


def pr_head_oid(pr: Mapping[str, Any]) -> str:
    return str(pr.get("headRefOid") or "").strip().lower()


def pr_auto_merge_summary(
    pr: Mapping[str, Any] | None,
    head_committed_at: datetime | None = None,
) -> dict[str, Any]:
    """Describe any pending auto-merge request, including a carried-over one.

    GitHub keeps an auto-merge request alive across a head change: PR #4227
    enabled auto-merge at 23:10:54Z, the head moved at 23:13:21Z, and the
    newer head merged at 23:14:41Z.  ``outlived_head`` records exactly that
    shape so a gated PR can refuse it instead of inheriting merge authority
    granted for a commit that no longer exists.
    """

    request = (pr or {}).get("autoMergeRequest")
    if not request:
        return {"present": False, "enabled_at": "", "enabled_by": "", "outlived_head": False}
    enabled_at_text = ""
    enabled_by = ""
    if isinstance(request, Mapping):
        enabled_at_text = str(request.get("enabledAt") or "").strip()
        enabler = request.get("enabledBy")
        if isinstance(enabler, Mapping):
            enabled_by = str(enabler.get("login") or "").strip()
        elif enabler:
            enabled_by = str(enabler).strip()
    enabled_at = parse_timestamp(enabled_at_text)
    outlived = bool(
        head_committed_at is not None
        and enabled_at is not None
        and enabled_at < head_committed_at
    )
    return {
        "present": True,
        "enabled_at": enabled_at_text,
        "enabled_by": enabled_by,
        "outlived_head": outlived,
    }


def pr_head_committed_at(pr: Mapping[str, Any]) -> tuple[datetime | None, str]:
    """Return the newest commit date on the PR head, plus a diagnostic."""

    commits = pr.get("commits")
    if not isinstance(commits, list) or not commits:
        return None, "PR payload carries no commit history"
    newest: datetime | None = None
    for item in commits:
        if not isinstance(item, Mapping):
            return None, "PR commit history contains a malformed entry"
        stamp = parse_timestamp(item.get("committedDate") or item.get("authoredDate"))
        if stamp is None:
            return None, "PR commit history contains an unparseable commit date"
        if newest is None or stamp > newest:
            newest = stamp
    return newest, ""


def evaluate_gate(
    contract: TaskContract,
    approval: ApprovalRecord | None,
    pr: Mapping[str, Any] | None,
    *,
    dev_branch: str = DEFAULT_DEV_BRANCH,
    task_branch_prefix: str = DEFAULT_TASK_PREFIX,
    now: datetime | None = None,
    task_brief_carry_forward: Mapping[str, Any] | None = None,
) -> GateDecision:
    """Decide whether this exact PR head may merge under this contract."""

    if contract.policy == POLICY_MERGE_THEN_REVIEW:
        return GateDecision(
            task_id=contract.task_id,
            policy=contract.policy,
            allow_merge=True,
            allow_auto_merge=True,
            reason="merge_then_review_permitted",
            detail=contract.declaration_detail
            or "canonical contract permits the declared merge-then-review policy",
            head_oid=pr_head_oid(pr) if pr else "",
            contract=contract.as_dict(),
            approval=approval.as_dict() if approval else {},
            auto_merge_request=pr_auto_merge_summary(pr),
        )

    # Every gated failure below must also revoke a pending auto-merge request,
    # so resolve it once up front and carry it on every decision.
    head_committed_at, commit_problem = pr_head_committed_at(pr) if pr else (None, "no PR payload")
    auto_merge = pr_auto_merge_summary(pr, head_committed_at)
    block = partial(_blocked, auto_merge_request=auto_merge)

    if contract.source in {"missing", "unreadable"}:
        return block(
            contract,
            approval,
            "task_state_unavailable",
            f"canonical task state for {contract.task_id} is {contract.source}; refusing to merge",
        )

    if not contract.requires_independent_review:
        return block(
            contract,
            approval,
            "no_independent_reviewer",
            f"{contract.task_id} has no independent reviewer distinct from owner "
            f"{contract.owner!r}; the review-before-merge gate cannot be satisfied",
        )

    if pr is None:
        return block(
            contract,
            approval,
            "pr_missing",
            f"no PR payload was supplied for {contract.task_id}",
        )

    expected_branch = f"{task_branch_prefix}{contract.task_id}"
    if str(pr.get("headRefName") or "").strip() != expected_branch:
        return block(
            contract,
            approval,
            "head_branch_mismatch",
            f"PR head {str(pr.get('headRefName') or '')!r} is not the exact task branch {expected_branch!r}",
        )
    if contract.declared_head_branch and contract.declared_head_branch != expected_branch:
        return block(
            contract,
            approval,
            "declared_head_branch_mismatch",
            f"{contract.task_id} declares head branch {contract.declared_head_branch!r} "
            f"but the canonical task branch is {expected_branch!r}",
        )
    if str(pr.get("baseRefName") or "").strip() != dev_branch:
        return block(
            contract,
            approval,
            "base_branch_mismatch",
            f"PR base {str(pr.get('baseRefName') or '')!r} is not the expected base {dev_branch!r}",
        )
    if bool(pr.get("isDraft")):
        return block(contract, approval, "pr_is_draft", "PR is a draft")

    head_oid = pr_head_oid(pr)
    if not OID_RE.match(head_oid):
        return block(
            contract,
            approval,
            "pr_head_unknown",
            "PR payload does not carry a resolvable 40-hex head oid; refusing an unbound merge",
        )
    if contract.declared_head_sha and contract.declared_head_sha != head_oid:
        return block(
            contract,
            approval,
            "declared_head_sha_mismatch",
            f"{contract.task_id} declares exact head {contract.declared_head_sha} "
            f"but the PR head is {head_oid}",
            head_oid=head_oid,
        )

    if contract.status not in APPROVED_STATUSES:
        return block(
            contract,
            approval,
            "review_not_approved",
            f"{contract.task_id} is {contract.status or 'unknown'}; "
            f"reviewer {contract.reviewer} has not approved this delivery",
            head_oid=head_oid,
        )

    if approval is None or not approval.present:
        detail = f"no review_approved audit record exists for {contract.task_id}"
        if approval is not None and approval.scan_error:
            detail = f"{detail} ({approval.scan_error})"
        return block(contract, approval, "approval_record_missing", detail, head_oid=head_oid)
    if approval.scan_error:
        return block(
            contract,
            approval,
            "approval_audit_unreadable",
            f"activity audit could not be read for {contract.task_id}: {approval.scan_error}",
            head_oid=head_oid,
        )
    if normalize_agent(approval.reviewer) != normalize_agent(contract.reviewer):
        return block(
            contract,
            approval,
            "approval_reviewer_mismatch",
            f"approval was recorded by {approval.reviewer!r} but the canonical reviewer "
            f"for {contract.task_id} is {contract.reviewer!r}",
            head_oid=head_oid,
        )
    if approval.revoked:
        return block(
            contract,
            approval,
            "approval_revoked",
            f"approval at {approval.approved_at_text} was revoked by a "
            f"{approval.revocation_type} from {approval.revoked_by!r} at {approval.revoked_at_text}",
            head_oid=head_oid,
        )

    # The approval must name what it approved.  Timestamps alone cannot tell an
    # unchanged head from a head that was swapped for an older commit, so the
    # recorded PR identity is compared exactly before anything else is trusted.
    if approval.binding_error:
        return block(
            contract,
            approval,
            "approval_binding_unusable",
            f"the review_approved record for {contract.task_id} carries an unusable "
            f"binding: {approval.binding_error}",
            head_oid=head_oid,
        )
    if not approval.binding_present:
        return block(
            contract,
            approval,
            "approval_head_binding_missing",
            f"the review_approved record for {contract.task_id} names no PR, head sha, "
            "and base branch; an unbound approval cannot prove which commit was "
            "reviewed. Re-approve with REVIEW_PR and REVIEW_HEAD_SHA set to the "
            "exact reviewed head",
            head_oid=head_oid,
        )
    carried_task_brief_only = (
        approval.approved_head_sha != head_oid
        and isinstance(task_brief_carry_forward, Mapping)
        and task_brief_carry_forward.get("kind") == "task_brief_only_successor"
        and str(task_brief_carry_forward.get("approved_head_sha") or "").strip().lower()
        == approval.approved_head_sha
        and str(task_brief_carry_forward.get("successor_head_sha") or "").strip().lower()
        == head_oid
        and isinstance(task_brief_carry_forward.get("changed_paths"), list)
        and bool(task_brief_carry_forward.get("changed_paths"))
        and all(
            str(path or "").startswith(".orchestrator/task-briefs/")
            and ".." not in str(path or "").split("/")
            for path in task_brief_carry_forward.get("changed_paths", [])
        )
    )
    if approval.approved_head_sha != head_oid and not carried_task_brief_only:
        return block(
            contract,
            approval,
            "approval_head_mismatch",
            f"reviewer {contract.reviewer} approved head {approval.approved_head_sha} "
            f"but the PR head is {head_oid}; this exact commit was never reviewed",
            head_oid=head_oid,
        )
    pr_number = normalize_pr_number(pr.get("number"))
    if pr_number != approval.approved_pr_number:
        return block(
            contract,
            approval,
            "approval_pr_mismatch",
            f"approval covers PR #{approval.approved_pr_number} but this decision is "
            f"about PR #{pr_number if pr_number is not None else pr.get('number')!r}",
            head_oid=head_oid,
        )
    if approval.approved_base_branch != str(pr.get("baseRefName") or "").strip():
        return block(
            contract,
            approval,
            "approval_base_mismatch",
            f"approval expects base {approval.approved_base_branch!r} but the PR "
            f"targets {str(pr.get('baseRefName') or '')!r}",
            head_oid=head_oid,
        )
    if approval.approved_head_branch and approval.approved_head_branch != expected_branch:
        return block(
            contract,
            approval,
            "approval_head_branch_mismatch",
            f"approval covers head branch {approval.approved_head_branch!r} but the "
            f"canonical task branch is {expected_branch!r}",
            head_oid=head_oid,
        )

    if str(pr.get("state") or "").strip().upper() == "MERGED":
        merged_at = parse_timestamp(pr.get("mergedAt"))
        if merged_at is None:
            return block(
                contract,
                approval,
                "merge_timestamp_unknown",
                "PR reports MERGED without a parseable mergedAt; cannot prove the "
                "merge followed the approval",
                head_oid=head_oid,
            )
        if merged_at < approval.approved_at:  # type: ignore[operator]
            return block(
                contract,
                approval,
                "merged_before_approval",
                f"PR merged at {pr.get('mergedAt')} before the approval at "
                f"{approval.approved_at_text}; the delivery was never reviewed before landing",
                head_oid=head_oid,
            )

    if head_committed_at is None:
        return block(
            contract,
            approval,
            "pr_head_timestamp_unknown",
            f"{commit_problem}; cannot prove the approval covers head {head_oid}",
            head_oid=head_oid,
        )

    approved_at = approval.approved_at
    assert approved_at is not None  # guarded by approval.present
    if head_committed_at > approved_at and not carried_task_brief_only:
        return block(
            contract,
            approval,
            "head_changed_after_approval",
            f"head {head_oid} was committed at {head_committed_at.isoformat()} after the "
            f"approval at {approval.approved_at_text}; the exact reviewed head no longer exists",
            head_oid=head_oid,
        )

    reference = now or datetime.now(timezone.utc)
    if (approved_at - reference).total_seconds() > CLOCK_SKEW_SECONDS:
        return block(
            contract,
            approval,
            "approval_timestamp_not_credible",
            f"approval timestamp {approval.approved_at_text} is in the future relative to {reference.isoformat()}",
            head_oid=head_oid,
        )

    if auto_merge["outlived_head"]:
        # PR #4227's shape: merge authority granted for an older commit is
        # still armed against the head standing now.  Approval of this head
        # does not retroactively legitimise that request.
        return block(
            contract,
            approval,
            "auto_merge_request_outlived_head",
            f"an auto-merge request enabled at {auto_merge['enabled_at']} predates head "
            f"{head_oid} committed at {head_committed_at.isoformat()}; GitHub would land a "
            "head that the request never covered. Revoke it and merge the exact head",
            head_oid=head_oid,
        )

    return GateDecision(
        task_id=contract.task_id,
        policy=contract.policy,
        allow_merge=True,
        allow_auto_merge=False,
        reason=(
            "task_brief_only_approval_carried_forward"
            if carried_task_brief_only
            else "exact_head_approved"
        ),
        detail=(
            (
                f"reviewer {contract.reviewer} approved {contract.task_id} at "
                f"{approval.approved_at_text}; the one direct successor "
                f"{head_oid} changes only generated task-brief paths, so the "
                "approval is carried forward without a second review"
            )
            if carried_task_brief_only
            else (
                f"reviewer {contract.reviewer} approved {contract.task_id} at "
                f"{approval.approved_at_text}, bound to PR #{approval.approved_pr_number} "
                f"head {approval.approved_head_sha} onto {approval.approved_base_branch}; "
                "that is exactly the head standing now"
            )
        ),
        head_oid=head_oid,
        approved_at=approval.approved_at_text,
        # A gated task never merges through `--auto`, so even on the approved
        # path any standing auto-merge request is revoked before the exact-head
        # merge rather than left armed for the next push.
        revoke_auto_merge=bool(auto_merge["present"]),
        contract=contract.as_dict(),
        approval=approval.as_dict(),
        auto_merge_request=auto_merge,
    )


def gate_for_task(
    task_id: str,
    pr: Mapping[str, Any] | None,
    *,
    status_root: Path | str | None = None,
    dev_branch: str = DEFAULT_DEV_BRANCH,
    task_branch_prefix: str = DEFAULT_TASK_PREFIX,
    state: Mapping[str, Any] | None = None,
    events: Iterable[Mapping[str, Any]] | None = None,
    now: datetime | None = None,
    task_brief_carry_forward: Mapping[str, Any] | None = None,
) -> GateDecision:
    contract = load_task_contract(task_id, status_root=status_root, state=state)
    approval = None
    if contract.policy == POLICY_REVIEW_BEFORE_MERGE:
        approval = load_approval_record(task_id, status_root=status_root, events=events)
    return evaluate_gate(
        contract,
        approval,
        pr,
        dev_branch=dev_branch,
        task_branch_prefix=task_branch_prefix,
        now=now,
        task_brief_carry_forward=task_brief_carry_forward,
    )


def select_open_pr(prs: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any] | None, str]:
    """Pick the single open PR for a task branch, or refuse the ambiguity."""

    candidates = [item for item in prs if isinstance(item, Mapping)]
    if not candidates:
        return None, "no_pr"
    if len(candidates) > 1:
        numbers = ", ".join(f"#{item.get('number')}" for item in candidates)
        return None, f"ambiguous_prs:{numbers}"
    return candidates[0], ""


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _read_pr_payload(source: str) -> Mapping[str, Any]:
    if source == "-":
        text = sys.stdin.read()
    else:
        text = Path(source).read_text(encoding="utf-8")
    payload = json.loads(text)
    if isinstance(payload, list):
        selected, problem = select_open_pr(payload)
        if problem:
            raise TaskReviewGateError(f"cannot select a single PR: {problem}")
        payload = selected
    if not isinstance(payload, Mapping):
        raise TaskReviewGateError("PR payload must be a JSON object")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Canonical review-before-merge gate for Pantheon task PRs.",
    )
    parser.add_argument("--status-root", type=Path, default=None)
    parser.add_argument("--dev-branch", default=DEFAULT_DEV_BRANCH)
    parser.add_argument("--task-branch-prefix", default=DEFAULT_TASK_PREFIX)
    parser.add_argument("--json", action="store_true", help="Emit the full decision as JSON.")
    sub = parser.add_subparsers(dest="command", required=True)

    policy = sub.add_parser("policy", help="Print the merge policy for a task.")
    policy.add_argument("task_id")
    policy.add_argument("--json", dest="json_after", action="store_const", const=True, default=None)

    check = sub.add_parser("check", help="Decide whether an exact PR head may merge.")
    check.add_argument("task_id")
    check.add_argument("--pr-json", required=True, help="Path to `gh pr view --json ...` output, or - for stdin.")
    check.add_argument("--json", dest="json_after", action="store_const", const=True, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # `--json` is accepted on either side of the subcommand.
    args.json = bool(args.json or getattr(args, "json_after", None))

    if args.command == "policy":
        try:
            contract = load_task_contract(args.task_id, status_root=args.status_root)
        except TaskReviewGateError as exc:
            # Fail closed: an unusable invocation must not unlock auto-merge.
            print(POLICY_REVIEW_BEFORE_MERGE)
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(contract.as_dict(), indent=2, sort_keys=True))
        else:
            print(contract.policy)
            if contract.declaration_detail:
                print(f"note: {contract.declaration_detail}", file=sys.stderr)
        return 0

    try:
        pr = _read_pr_payload(args.pr_json)
    except (OSError, json.JSONDecodeError, TaskReviewGateError) as exc:
        print(json.dumps({"allow_merge": False, "reason": "pr_payload_unreadable", "detail": str(exc)}))
        return 2

    decision = gate_for_task(
        args.task_id,
        pr,
        status_root=args.status_root,
        dev_branch=args.dev_branch,
        task_branch_prefix=args.task_branch_prefix,
    )
    if args.json:
        print(json.dumps(decision.as_dict(), indent=2, sort_keys=True))
    else:
        verdict = "allow" if decision.allow_merge else "block"
        print(f"{verdict} {decision.task_id} policy={decision.policy} reason={decision.reason}")
        print(f"  {decision.detail}")
    return 0 if decision.allow_merge else 1


if __name__ == "__main__":
    raise SystemExit(main())
