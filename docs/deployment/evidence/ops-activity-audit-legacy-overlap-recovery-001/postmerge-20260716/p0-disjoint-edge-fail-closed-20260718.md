# P0 disjoint-edge fail-closed correction — 2026-07-18

Task: `OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001`

Status: current product-level acceptance withdrawn; corrective implementation
and independent exact-head review required.

This document supersedes the acceptance conclusion of the following immutable
rejected-run artifacts without modifying their bytes:

| Rejected artifact | SHA-256 |
| :--- | :--- |
| `installed-runtime-20260717/evidence.md` | `4ad6bf94fa9473df9104c0a0e3a5fa44061e0dab83229825a6c2d79de1f1374a` |
| `installed-runtime-20260717/summary.json` | `dea7dacce6e0941499f8f5b98aaa1f473ef6b69ab0ac4b16da9627aae43b2a5d` |
| `installed-runtime-20260717/manifest.json` | `8ddca2fd9a0c11bd0243d971fc56c06d9715048e68c13d61fe4df147f9daa9f6` |

The counts and fold metrics in those files remain rejected-reader diagnostics,
not proof of current global completeness, ordering, or conservation.

## Finding

PR #3820 head `306ed10c5d429a19a2f62d76241d49d3547e220b` allowed the
incident overlap component to stop at
`ai-activity-log.jsonl-2026-07-17T0404Z.gz` and called the next sorted legacy
leaf `ai-activity-log.jsonl-2026-07-17T1754Z.gz` a validated disjoint
successor. The edge has no durable continuity authority.

The preserved read-only identities are:

| Source | Gzip bytes | Gzip SHA-256 | Payload bytes | Lines | Payload SHA-256 |
| :--- | ---: | :--- | ---: | ---: | :--- |
| `ai-activity-log.jsonl-2026-07-17T0404Z.gz` | 720,587 | `9aad2a2e5eb40b8233aaf91f02a429142084eef09c33cf36f8fa9076a1c3e65b` | 5,266,919 | 1,727 | `3f137f4c7707d197c130d3b646ad4e46b9aba8f0a9e94b5809697844229d93f8` |
| `ai-activity-log.jsonl-2026-07-17T1754Z.gz` | 795,713 | `5c9a4f97af7e69beb3dd6b547452fad3f56f9e57442d61c49e94d4552c7d6bd2` | 5,398,155 | 2,194 | `9734b2e4c6d8041e4c5daf0548f873ef0dff41572c2696dc8542a6c0c94fe766` |

Their suffix/prefix candidates are byte-different at 999, 1,000, and 1,001
lines. The first schema-v2 lineage row instead binds
`1754Z -> ai-activity-log.jsonl-f2dae488...677bc.gz`; it does not bind
`0404Z -> 1754Z`. Filename order, mtime, directory enumeration, and event
timestamps are not zero-loss authority for this incident chain.

## Required reader contract

- The exact `1450Z -> ... -> 0404Z -> 1754Z` shape must fail closed at the
  unregistered legacy-to-legacy edge.
- Once the code-owned incident continuity anchor is present, every following
  edge must be either an exact accepted legacy overlap or a disjoint edge bound
  by validated schema-v2 lineage and active-head metadata.
- The historical pinned 999-line exception remains the sole non-1,000 overlap
  exception; generic 999, 1001, content overlap, mismatch, and non-adjacent
  overlap remain rejected.
- Failure must be structured, must expose no partially validated logical rows,
  and must leave existing evidence and status bytes unchanged.

## Governed readback

The supervisor-provided command identity was:

- `PANTHEON_COMMAND_ROOT=/home/lupin/pantheon-ci-deploy/dev-root`
- `PANTHEON_COMMAND_RUNTIME_SHA=4104782461c118aad677bd1975a12d2882aed033`
- `AI_NAME=Codex`

A governed `show OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001` returned exit
`2` with invariant `status_recovery_pending`, pending plane
`status_activity_outbox`, and evidence digest
`c866db1c969d57f5cfeafb344e7857dce3d7cee21ae6325bbf1a3e2da24b083c`.
No outbox, status, activity archive, active log, or rotation-control file was
manually edited or cleared.

## Acceptance boundary

The installed-runtime manifest, source hashes, and exact-overlap rows are
retained as historical observations. They do not establish continuity across
the rejected edge, so the associated logical counts and current zero-mismatch
claim cannot clear product-level acceptance.

Before `done`, the corrective PR must receive independent exact-head review
and merge, and its exact merge must be installed. The boundary must then take
one of two explicit paths: (a) obtain immutable hash-bound lineage authority
before a current inventory can accept it; or (b) remain rejected while the
planner records that global conservation cannot be proved. A planner decision
alone never authorizes filename-order concatenation. Only after that decision
may the governed outbox recovery and stale-worktree `show`/`note`/`handoff`
proofs be rerun; current inventory acceptance additionally requires path (a).
