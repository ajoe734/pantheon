# SUP-L12 guarded remediation dispatcher evidence

This directory is the task-scoped evidence boundary for the program-specific
current-proof dispatcher. The implementation is anchored at
`5646d3499853b5af59b8c9e75086da402d40f6eb`; it consumes the 28-task catalog
from PR #4394 exact head
`fb9adfb84944e276b254ccfdfff784fb6728a7f4` byte-for-byte.

The local acceptance cut proves exact catalog validation, legacy compatibility,
G1-only dry-run planning, dependency/archive handling, provider readiness
fallback recording, replay and partial-state rejection, concurrent artifact
conflict rejection, atomic failure behavior, and exact canonical readback.

Live materialization is intentionally not claimed by this cut. The pre-merge
live dry-run currently rejects the G1 frontier because
`SUP-ASSISTANT-DEV-BRIDGE-MATERIALIZATION-20260730` remains nonterminal with an
overlapping BFF artifact scope. After this task PR is independently reviewed,
merged, and promoted to the immutable command root, rerun validate-only and
dry-run. Only a clean dry-run may proceed to `--apply`; the resulting canonical
25-task readback and external admission archive must then be appended to
`evidence.json` before governed closeout.

