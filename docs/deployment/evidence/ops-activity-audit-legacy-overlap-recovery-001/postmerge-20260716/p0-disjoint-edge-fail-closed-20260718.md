# P0 disjoint-edge fail-closed correction — 2026-07-18

Task: `OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001`

Status: current product-level acceptance withdrawn; corrective implementation
and independent exact-head review required.

Corrective reader/inventory implementation is anchored at
`afb3b67d10ec55a2989bd54b3bc59f22e55b67f7`; see
`p0-regression-receipt-20260718.md` for the exact central failure and test
receipt.

This document supersedes the acceptance conclusion of the following immutable
rejected-run artifacts without modifying their bytes:

| Rejected artifact | SHA-256 |
| :--- | :--- |
| `installed-runtime-20260717/evidence.md` | `4ad6bf94fa9473df9104c0a0e3a5fa44061e0dab83229825a6c2d79de1f1374a` |
| `installed-runtime-20260717/summary.json` | `dea7dacce6e0941499f8f5b98aaa1f473ef6b69ab0ac4b16da9627aae43b2a5d` |
| `installed-runtime-20260717/manifest.json` | `8ddca2fd9a0c11bd0243d971fc56c06d9715048e68c13d61fe4df147f9daa9f6` |

The counts and fold metrics in those files remain rejected-reader diagnostics,
not proof of current global completeness, ordering, or conservation.
The machine-readable superseding verdict is
`p0-disjoint-edge-verdict-20260718.json`.

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

| Candidate lines | `0404Z` suffix SHA-256 | `1754Z` prefix SHA-256 | Equal |
| ---: | :--- | :--- | :---: |
| 999 | `99f7f700cfd30ecc322962fde4698472d2099a4307386df78ca28a81f41ecf5e` | `f68bcf63ff21795c48f13eb6eaf55b810549837e8e1d222cb5d36e70da9bd344` | no |
| 1,000 | `df07b435169cc7609b68fef42b6824e60628b153b6707698f54adf4308be460a` | `ca8fd81af8078b24c9253466069174a95bee3c40161826fdaa3de25c34a3485f` | no |
| 1,001 | `8dbcdc774f7de2f062289de3129f58460658004526ee6a0db5e1a48d7282efc5` | `e94df6869594fdbb878a0aa56369c8bea9b299d09d02d2edf31fbefe287321e9` | no |

The current first schema-v2 row is sequence `1`, transaction
`activity-rotation-e4b5bdca9e8d6c9ab5dde32eb18fbb0bfb25ad72323737fc25c8f87812c8f35a`,
canonical row SHA-256
`b4422d05669ce9bf225a4876909815d547ce5b9cb557e71573075140f0525daa`,
and serialized-row SHA-256
`89e8538354de9362d7239c12b9f76272f5446b780638a895608fe96d59c53a76`.
It binds `1754Z` to the first content archive, whose gzip SHA-256 is
`7aa60a618dece437dfdc463fb914491bf6f4e773c4c95e136937320bbad5b94b`
and payload SHA-256 is
`f2dae488b47ea9f0cff778a701edbffe3cbafaa6cac51ad1e2a7e1a42a2677bc`.

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
- The first schema-v2 writer transition must not create a content archive when
  legacy history exists but no byte-proven boundary can be recorded.

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
