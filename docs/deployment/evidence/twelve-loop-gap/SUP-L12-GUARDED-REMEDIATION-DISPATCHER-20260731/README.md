# SUP-L12 guarded remediation dispatcher evidence

This directory is the task-scoped evidence boundary for the program-specific
current-proof dispatcher. The core implementation is anchored at
`5646d3499853b5af59b8c9e75086da402d40f6eb`, and the four Antigravity
pre-review findings are resolved at
`8ca95d7488c4b79dfa7c99389cc9e999c4fb4508`. It consumes the 28-task catalog
from PR #4394 exact head
`fb9adfb84944e276b254ccfdfff784fb6728a7f4` byte-for-byte.
The supervisor's later Codex/Codex2 ownership redispatch is anchored at
`2a8c108ab1db9689e16f63528470756bb5379450`; the earlier implementation and
pre-review actor records remain historical evidence rather than being rewritten.

The local acceptance cut proves exact catalog validation, legacy compatibility,
G1-only dry-run planning, dependency/archive handling, provider readiness
fallback recording, replay and partial-state rejection, concurrent artifact
conflict rejection, real authoritative journal atomic failure behavior,
two-process contention, prepared-receipt crash recovery, and exact canonical
readback. The legacy no-catalog CLI remains the original 25-task profile;
the current-proof catalog requires explicit `--current` (or its exact catalog
path), and current apply does not accept Human/Ops as a substitute actor.

Live materialization is intentionally not claimed by this cut. The pre-merge
live dry-run, repeated by the current owner after redispatch, rejects the G1
frontier because
`SUP-ASSISTANT-DEV-BRIDGE-MATERIALIZATION-20260730` remains nonterminal with an
overlapping BFF artifact scope. After this task PR is independently reviewed,
merged, and promoted to the immutable command root, rerun the explicit current
profile in validate-only mode and then dry-run mode. Only a clean dry-run may
proceed to current-profile apply; the resulting canonical 25-task readback and
committed admission archive must then be appended to `evidence.json` before
governed closeout.
