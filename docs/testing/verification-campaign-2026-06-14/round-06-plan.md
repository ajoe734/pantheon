# Round 6 — Authorization depth (RBAC) audit of the write surface

**Date:** 2026-06-14
**Depth/breadth step:** Rounds 1–5 used an admin token (authentication). Round 6
is the first to examine **authorization** — does each write endpoint enforce a
*write-level* role, or can a read-only (`viewer`) principal mutate state? This
is a security-relevant, code-level audit.

## Why this round (not a duplicate)

No prior doc maps write-endpoint authorization coverage. Unit tests assert a few
specific 403s (e.g. MCP tool import rejects `viewer`), but there is no
systematic audit of the full write surface's role-gating.

## Hypotheses

- H1: every state-mutating endpoint enforces a write-level role (operator /
  approver / admin / reviewer or a domain-specific write gate), rejecting a
  read-only `viewer` with 403.
- H2: high-risk endpoints (capital, approvals, DLQ replay, search-index,
  signals) provably enforce a write role.

## Method (mutation-safe; static + spot live)

1. Recognize that a live POST with a `viewer` token + malformed body cannot
   distinguish "authz enforced" from "validation-runs-first" (FastAPI validates
   typed bodies before the handler's role check → 422 not 403). So authorization
   is audited **statically** in code, not by mutating live state.
2. Parse every `@app.{post,put,patch,delete}` handler in `main.py`; classify by
   the authorization it performs (role function, inline role-set intersection,
   role-set constant, deprecated-route short-circuit, or read-gate only).
3. Spot-read high-risk handlers to confirm the static classification.

## Pass criteria

- H1/H2: every mutating endpoint enforces a write gate, OR each exception is
  characterized (deprecated route, intentional broad authoring, or a genuine
  gap flagged for product decision). Security-sensitive authz changes are
  **not** applied unilaterally — they are queued with a recommendation.
