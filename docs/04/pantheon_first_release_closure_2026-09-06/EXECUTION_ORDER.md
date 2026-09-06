# Execution Order — First Release Closure

Source of record: `archive/APPROVAL_RELEASE_SA_SD.md` §4 and §7. This table
restates that ordering for readers; the signed source is authoritative if
they ever diverge.

## Three formal tasks (sequential dependency chain)

| # | Task ID | Depends on | Scope |
| --- | --- | --- | --- |
| 1 | `DOC-FIRST-RELEASE-PLAN-DELIVERY-001` (this task) | `PLAN-ADMIT-001` (done) | Commit the previously-uncommitted planning/audit/registry sources; pure docs; independently executable. |
| 2 | `GOV-APPROVAL-AUTHORITY-PREREQUISITE-001` | this task, `REGISTRY-STRATEGY-UNIFIED-CONTRACT-001` (Registry successor), `DOMAIN-WRITERS-001` | The exact approval-authority slice (§3 of `archive/APPROVAL_RELEASE_SA_SD.md`): common Governance inbound validation, one shared decision reader, typed HTTP/auth/DTO contract. Serialized after Registry, does not wait on Overlay/Domain corrective. |
| 3 | `STRUCT-RETIRE-001` | docs task, authority slice, Registry successor, Domain corrective, `DEV-DELIVERY` | Actual canonical source-join: retirement of the 17 dead tails / 208 duplicate groups / 216 test files against real ownership/import/test/route gates. Not a new release controller. |

## Three hosted tasks (gated behind the chain above, unchanged scope)

| Task ID | Depends on | Scope |
| --- | --- | --- |
| `DEV-RELEASE-HOSTED-001` | `STRUCT-RETIRE-001` | Existing lane deployment/rollback/served-identity acceptance on the accepted FE/BFF pair. |
| `L12-HOSTED-001` | `DEV-RELEASE-HOSTED-001` (same accepted pair) | Full 12-loop causal chain acceptance on that pair. |
| `MGMT-AGORA-E2E-001` | `DEV-RELEASE-HOSTED-001` (same accepted pair) | Authenticated journeys, restart, SSE, durable replay on that pair. |

All three hosted tasks require a legitimate one-shot, MFA-backed admission
that this documentation task does not grant, perform, or simulate.

## What this task's completion unblocks

Completing `DOC-FIRST-RELEASE-PLAN-DELIVERY-001` removes the "sources are
not durably committed" precondition for reviewers and downstream owners of
`GOV-APPROVAL-AUTHORITY-PREREQUISITE-001` to read the approved plan and its
supporting documents from the repository instead of a workstation path or
`/tmp`. It does not itself start, implement, or approve
`GOV-APPROVAL-AUTHORITY-PREREQUISITE-001`; that remains a separate task with
its own owner and reviewer.
