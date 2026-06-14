# V4 — Canonical document & contract integrity (direction G, broadening)

- Date: 2026-06-14
- Branch: task/verify-v4-doc-integrity
- Non-duplication: pivoted here after finding the first two V4 candidates were
  already owned — repo-wide test-collection is `TEST-FULLSUITE-HARNESS-ISOLATION`
  (Codex2), and kill-switch is `EVO-005` + `ep5_008_v2` (kill-switch demo harness).
  Canonical-doc integrity has no owning brief (the only "map" brief is
  data-ownership migration), so this round is distinct.

## Plan
The whole system treats a fixed set of canonical docs as authoritative (ai-status
`canonical_files`, CANONICAL_DOCUMENT_MAP, the loop/policy docs). Verify that surface
is internally consistent — no canonical file or cross-reference points at a missing doc
(a class of rot that would silently undermine "authoritative" guarantees).

## Verification & result
Added `scripts/audit_canonical_docs.py` which checks:
1. every `ai-status.json` `canonical_files` entry exists (34 entries);
2. every `*.md` referenced in CANONICAL_DOCUMENT_MAP.md exists (74 refs).

Run result: **OK — all canonical_files + CANONICAL_DOCUMENT_MAP references resolve.**
The canonical-doc surface is currently healthy; no broken references to fix this round.

## Deliverable
A reusable integrity gate (exit 1 on any broken reference) so canonical-doc rot is
caught going forward — the durable value, mirroring V1's drift-audit tool.

## Follow-ups
- Wire `audit_canonical_docs.py` into the acceptance gate (e.g. run-acceptance.sh smoke;
  it is fast and dependency-free) once V3's run-acceptance changes (PR #1544) merge, to
  avoid a parallel edit conflict.
- Extend to validate intra-doc section anchors and the DOCUMENT_AUTHORITY record-boundary.
