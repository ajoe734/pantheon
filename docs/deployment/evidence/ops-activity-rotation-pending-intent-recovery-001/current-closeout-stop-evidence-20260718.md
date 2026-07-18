# Current closeout stop evidence — 2026-07-18

Task: `OPS-ACTIVITY-ROTATION-PENDING-INTENT-RECOVERY-001`

Outcome: the historical recovery remains successful, but current product-level
closure is fail-closed. No second recovery execution is authorized or needed.

## Read-only boundary inventory

The two immutable legacy gzip leaves and the current lineage file were read
without opening either central coordination lock. Decompressed bytes were
written only to `/tmp/oparpir-current-*` for hashing and comparison. No
central file was written, renamed, truncated, deleted, recompressed, or
locked.

| Source | Gzip bytes | Gzip SHA-256 | Payload bytes | Lines | Payload SHA-256 |
| --- | ---: | --- | ---: | ---: | --- |
| `ai-activity-log.jsonl-2026-07-17T0404Z.gz` | 720,587 | `9aad2a2e5eb40b8233aaf91f02a429142084eef09c33cf36f8fa9076a1c3e65b` | 5,266,919 | 1,727 | `3f137f4c7707d197c130d3b646ad4e46b9aba8f0a9e94b5809697844229d93f8` |
| `ai-activity-log.jsonl-2026-07-17T1754Z.gz` | 795,713 | `5c9a4f97af7e69beb3dd6b547452fad3f56f9e57442d61c49e94d4552c7d6bd2` | 5,398,155 | 2,194 | `9734b2e4c6d8041e4c5daf0548f873ef0dff41572c2696dc8542a6c0c94fe766` |

The `0404Z` suffix and `1754Z` prefix are byte-different for every accepted
legacy keep-lines candidate:

| Candidate lines | `0404Z` suffix SHA-256 | `1754Z` prefix SHA-256 | Equal |
| ---: | --- | --- | --- |
| 999 | `99f7f700cfd30ecc322962fde4698472d2099a4307386df78ca28a81f41ecf5e` | `f68bcf63ff21795c48f13eb6eaf55b810549837e8e1d222cb5d36e70da9bd344` | no |
| 1,000 | `df07b435169cc7609b68fef42b6824e60628b153b6707698f54adf4308be460a` | `ca8fd81af8078b24c9253466069174a95bee3c40161826fdaa3de25c34a3485f` | no |
| 1,001 | `8dbcdc774f7de2f062289de3129f58460658004526ee6a0db5e1a48d7282efc5` | `e94df6869594fdbb878a0aa56369c8bea9b299d09d02d2edf31fbefe287321e9` | no |

The first current schema-v2 lineage row does authenticate the later boundary:

- sequence `1`, transaction
  `activity-rotation-e4b5bdca9e8d6c9ab5dde32eb18fbb0bfb25ad72323737fc25c8f87812c8f35a`;
- predecessor `ai-activity-log.jsonl-2026-07-17T1754Z.gz`, payload SHA-256
  `9734b2e4...fe766`;
- verified excluded 1,000-line prefix SHA-256 `927ca8dc...bb458`;
- first content payload SHA-256 `f2dae488...677bc`.

That row proves the `1754Z -> first schema-v2 archive` normalization. It does
not bind or explain the earlier disjoint `0404Z -> 1754Z` legacy transition.
Filename order, mtime, directory enumeration, and event timestamps are not
ordering authority under the incident plan.

## Diagnostic-model finding

Current `scripts/activity_audit_logical_inventory.py` follows exact overlap
edges from the `1450Z` incident source until the active log. It correctly
stops at `0404Z` because no authenticated fold edge reaches `1754Z`, reporting:

```text
Incident lineage broken at ai-activity-log.jsonl-2026-07-17T0404Z.gz
```

Draft PR #3820 changes the report generator to accept any missing overlap edge
as a validated disjoint epoch boundary once the shared reader has accepted the
ordered source list. That is not sufficient evidence here: the shared reader
can concatenate disjoint legacy leaves in filename order, while the incident
plan requires durable byte/transaction authority and zero-loss proof. The
draft must not be used to clear this task's conservation gate without a
separate authenticated `0404Z -> 1754Z` edge or an explicit planner data-
reconciliation decision.

## Fresh governed status readback

The supervisor-provided command pin and command-root HEAD both matched
`c9560db5cba9583bd2dff70894e583cdca5d2a20`.

- governed `progress` completed with exit `0` and updated this task at
  `2026-07-18T03:13:11Z`;
- the later governed read-only `show` returned bounded exit `2` in 14.7
  seconds with invariant `status_recovery_pending`, pending plane
  `status_activity_outbox`, and evidence digest
  `c866db1c969d57f5cfeafb344e7857dce3d7cee21ae6325bbf1a3e2da24b083c`;
- the pending event observed after that readback was an unrelated supervisor
  reassignment for `OPS-SUPERVISOR-SINGLETON-LOCK-SCOPE-001`, not this task.

No retry or manual outbox edit was attempted. The bounded diagnostic proves
the read-only command no longer hangs, but the status lane cannot be called
fully healthy while recovery remains pending.

## Required decisions before closeout

1. Provide an immutable, hash-bound continuity record for the
   `0404Z -> 1754Z` legacy transition, or formally accept that current global
   missing-event count cannot be proved from the surviving artifacts.
2. Accept or return the recovery-time resolution-row/empty-lineage deviation.
3. Accept or return preserve-then-unlink semantics for the original pending
   and staged paths.
4. Drain and read back the governed activity outbox through the owning writer
   path; do not hand-edit it.
5. Only after those gates, recompose current `dev`, rerun the accepted
   read-only inventory, and request exact-head Antigravity/planner review.

Historical recovery-window `missing=0` and `duplicate=0` remain valid. This
record deliberately does not extend them into a current-history claim.
