# Guarded-remediation catalog correction evidence cut

This document records the source receipt for the corrected 2026-08-03
guarded-remediation catalog. It replaces PR #4528 as review input because the
older branch's pushed history contains trailer-invalid commits; it does not
change controller implementations, task state, deployment authority, or
live-capital policy.

The cut is bound to replacement PR #4539 source receipt
`f2b48094226f56a392f33a3f65d7a5118dca37a1`, whose Commit trailers, Runtime
mirror guard, and Smoke acceptance jobs succeeded. The dispatcher retains the
previous-current profile and adds the corrected profile without materializing
any product tasks.

This evidence cut scanned through canonical task-state journal sequence 9545.
At that boundary, the catalog task remains owned by Codex2 with Codex as its
independent reviewer and has not been approved, merged, or marked done.

The full two-file dispatcher suite is deliberately not claimed as passing:
the single remaining failure is the out-of-scope `L12-SIGNOFF-001` task-card
owner/header mismatch. Its owner must land the correction on `dev`; this branch
will then refresh from `dev`, rerun the complete suite, and submit the
resulting exact PR head for independent review.
