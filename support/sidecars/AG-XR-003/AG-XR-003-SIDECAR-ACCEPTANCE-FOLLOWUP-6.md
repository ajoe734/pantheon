# AG-XR-003 Sidecar Acceptance Follow-up 6

- Parent task: `AG-XR-003` - Dev deployment compatibility manifest
- Helper task: `AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-6`
- Helper kind: `acceptance_packet`
- Owner: `Codex2`
- Reviewer: `Codex`
- Generated: `2026-06-20`
- Mutates canonical truth: `no`
- Baseline inspected: `origin/dev` `5bc93c6eca3ccb2437d465dba9a8fd3b9ca441ce`

This is a support packet only. It does not edit
`docs/contracts/agora/dev-compatibility-manifest.json`,
`scripts/agora_compat_manifest.py`, tests, frontend generated types, runtime
registry behavior, governance behavior, deployment workflow, or L1/L2
canonical documents.

## Purpose

Follow-up 5 recorded that Pantheon PR #1852 had merged and that parent
`AG-XR-003` remained blocked on the execute-plans mirror PR #63 and reviewer
disposition. This follow-up rechecks the current `origin/dev` baseline and the
current GitHub PR state, then packages the updated acceptance/dependency map
for reviewer `Codex`.

No AG-XR-003 implementation surface changed between this branch's previous
baseline and `origin/dev` `5bc93c6e`; the latest dev commits were AG-FE-ID
sidecar documentation only. The parent task remains blocked, waiting for
`Claude2`.

## Current Status Snapshot

| Surface | Current state | Sidecar stance |
|---|---|---|
| Parent `AG-XR-003` | `blocked`, waiting for `Claude2` | Correct to keep blocked until PR #63 disposition and manifest sanity issues are resolved or explicitly deferred. |
| Pantheon PR #1852 | `MERGED` at merge commit `0765018c838547108fa56fcf089b5e2bbafd4387` | Pantheon-side manifest gate implementation is durable on `dev`. |
| execute-plans PR #63 | `OPEN`, head `e1cb9125c87d9ace0adf3dd9f17f24ff0542d9c5`, `mergeStateStatus=UNSTABLE` | Cross-repo mirror is not merged; parent acceptance is still blocked. |
| PR #63 integration gate | Failed at `Aggregate release gate` in run `27877483718` | The job completed earlier steps, but the aggregate release decision remains red. |
| Local manifest sanity | `verify --allow-pending` fails on generated-types hash mismatch | Parent cannot claim repo sanity green on this baseline. |
| Local deployment gate | Fails closed | Correct behavior while status is pending, frontend commits are placeholders, and hashes mismatch. |
| Agora frontend drift | `npm --prefix execute-plans run contract:drift` passes locally | Useful evidence, but not equivalent to a green deployment gate or a merged mirror PR. |

## Source Evidence

| Source | Evidence used here |
|---|---|
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-6` | Sidecar is `in_progress`, owner `Codex2`, reviewer `Codex`, artifact path is this packet. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003` | Parent is `blocked`, waiting for `Claude2`; note records PR #1852 merged and PR #63 blocked at integration gate. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-002` | Archived `done`; generated-types predecessor is complete as a task record. |
| `gh pr view 1852 --repo ajoe734/pantheon ...` | PR #1852 is merged at `0765018...`; Branch CI and orchestrator sync checks succeeded. |
| `gh pr view 63 --repo ajoe734/execute-plans ...` | PR #63 is still open, unstable, with `integration-gate` failure. |
| `gh run view 27877483718 --repo ajoe734/execute-plans ...` | Job failed only at step 22, `Aggregate release gate`; earlier job steps were successful. |
| `gh run view 27877483718 --repo ajoe734/execute-plans --log-failed` | Aggregate summary reports failing release subgates; F13 Agora is listed as passing, while broader static/contract, BFF write/deep validation, F01/F05, performance, SSE rerender, and final release decision gates remain red. |
| `docs/contracts/agora/dev-compatibility-manifest.json` | Committed manifest sha256 is `d5143fb...`; backend commit is still `7ab267...`; frontend commits are placeholders; committed generated-types hash is `a6a9296...`; blocking reasons include the generated-types blocker and the two placeholder frontend commit blockers. |
| `scripts/agora_compat_manifest.py write --stdout` | Fresh generator at HEAD `5bc93c6e` emits backend commit `5bc93c6e`, frontend generated-types hash `0244eb11...`, and only the two placeholder frontend commit blockers. |

## Manifest Delta To Resolve

| Field | Committed manifest | Fresh generator output at `5bc93c6e` |
|---|---|---|
| `backend.runtime_commit` / `backend.contract_commit` | `7ab267adc9f88519149ae01a874764d8fd8c1108` | `5bc93c6eca3ccb2437d465dba9a8fd3b9ca441ce` |
| `frontend.generated_types_sha256` | `a6a9296efed4c3d00a3bb4d5d20896fd17027bd2484c4ead7b560785772319be` | `0244eb11c43aabe56a4c00ca0244fff4dd3cac134cae8f704bf38335c72b1740` |
| `blocking_reasons` | `frontend-generated-contract-commit-placeholder`, `frontend-generated-types-not-agora-v1.1`, `frontend-runtime-commit-placeholder` | `frontend-generated-contract-commit-placeholder`, `frontend-runtime-commit-placeholder` |
| `compatibility_status` | `pending` | `pending` |

Current `verify --allow-pending` fails because the committed manifest's
generated-types hash does not match the local generated-types hash expected by
the verifier. Current `deployment-gate` fails closed on the same hash mismatch,
pending status, placeholder frontend commits, frontend/backend contract commit
mismatch, and non-empty blocking reasons.

## Dependency Map

```mermaid
graph TD
    XR001["AG-XR-001: frozen v1 schema/OpenAPI bundle"]
    XR001A["AG-XR-001A: v1.1 extension bundle + compatibility schema"]
    XROPENAPI["AG-XR-OPENAPI-001: v1.1 OpenAPI + capability v1.1"]
    XRDASH["AG-XR-DASH-001: dashboard v2 schema/routes"]
    XR002["AG-XR-002: generated types + drift CI (DONE)"]
    XR003["AG-XR-003: manifest generator + validator + deploy docs (BLOCKED)"]
    PR1852["Pantheon PR #1852: merged"]
    PR63["execute-plans PR #63: open, unstable"]
    DEPLOY["Dev deployment compatibility gate"]

    XR001 --> XR001A
    XR001A --> XROPENAPI
    XR001A --> XRDASH
    XR002 --> XR003
    XROPENAPI --> XR003
    XRDASH --> XR003
    XR003 --> PR1852
    PR1852 --> PR63
    PR63 --> DEPLOY
```

Durable interpretation:

- `AG-XR-002` is complete as an archived task, and the local Agora drift check
  passes.
- Pantheon-side implementation is merged, but the committed manifest is stale
  relative to the current generator output and local verifier.
- execute-plans PR #63 is still the cross-repo mirror gate and has not merged.
- PR #63's failed aggregate release gate includes broader execute-plans release
  concerns. Parent `AG-XR-003` should not silently absorb or ignore those
  failures; `Claude2` or the parent owner must give disposition.

## Updated Parent Acceptance Checklist

| Parent acceptance surface | Current evidence | Follow-up 6 stance |
|---|---|---|
| Pantheon manifest/gate implementation exists | PR #1852 merged; `scripts/agora_compat_manifest.py` and JSON manifest path exist on `dev`. | Satisfied for Pantheon-side existence. |
| execute-plans mirror exists and can merge | PR #63 is open and unstable. | Not satisfied. |
| Manifest is internally fresh | Committed manifest still records backend `7ab267...` and generated-types hash `a6a9296...`; fresh generator emits HEAD `5bc93c6e` and `0244eb11...`. | Not satisfied. |
| `verify --allow-pending` supports repo sanity | Fails on generated-types hash mismatch. | Not satisfied. |
| `deployment-gate` fails closed until compatible | Fails closed on pending status, placeholders, mismatch, and non-empty blockers. | Satisfied as a guardrail, not as deployment readiness. |
| Unit tests reflect current generator behavior | `scripts/test_agora_compat_manifest.py` has 1 stale assertion failure and 3 passes. | Not satisfied. |
| Local Agora drift remains green | `npm --prefix execute-plans run contract:drift` passes. | Useful evidence; not sufficient for parent closeout. |
| Scope boundary preserved | No broker order, live capital, RuntimeBinding write, registry authority, or governance authority changed by this sidecar. | Satisfied for sidecar scope. |

## Reviewer Rejection Criteria

| Problematic parent move | Why reviewer should reject it |
|---|---|
| Claiming `AG-XR-003` done while PR #63 is still open or unstable. | The parent task scope includes the cross-repo mirror. |
| Treating local `contract:drift` success as equivalent to manifest verification or deployment readiness. | Drift proves generated Agora snapshot consistency; deployment gate also requires manifest freshness, commit pins, parity, and compatible status. |
| Calling `verify --allow-pending` green on this baseline. | The verifier currently exits non-zero on generated-types hash mismatch. |
| Marking deployment compatibility while frontend commits are placeholders. | Deployment gate must fail closed until immutable execute-plans refs are recorded. |
| Updating the stale unit test without also reconciling the committed manifest and verifier expectation. | The test failure is linked to the same generated-types state transition. |
| Absorbing broader PR #63 aggregate release failures without explicit disposition. | PR #63 is blocked by the release gate even though F13 Agora passes; the parent reviewer must decide whether those failures block or defer parent closeout. |
| Expanding runtime, registry, governance, broker, or capital-facing authority through this compatibility gate. | `AG-XR-003` is only a dev deployment compatibility validation gate. |

## Suggested Handoff To Reviewer

```text
Follow-up 6 packet ready:
support/sidecars/AG-XR-003/AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-6.md

Current origin/dev is 5bc93c6e. Parent AG-XR-003 remains blocked waiting for
Claude2. Pantheon PR #1852 is merged at 0765018..., but execute-plans PR #63 is
still open/unstable with integration-gate failing at Aggregate release gate.

Local validation shows the committed manifest is still stale relative to the
fresh generator: manifest has generated_types_sha256 a6a9296..., while write
and verify expect 0244eb11... at HEAD. verify --allow-pending fails;
deployment-gate fails closed; pytest has 1 stale assertion failure and 3 passes;
Agora contract drift passes locally. This sidecar changes only support
material and should be used as reviewer/parent-owner intake, not as parent
approval.
```

## Verification

Commands run while preparing this packet:

```bash
git status -sb
git merge --ff-only origin/dev
git rev-parse HEAD
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-6
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-002
gh pr view 63 --repo ajoe734/execute-plans --json number,title,state,mergeStateStatus,statusCheckRollup,headRefOid,headRefName,baseRefName,url,isDraft,mergedAt,reviewDecision
gh pr view 1852 --repo ajoe734/pantheon --json number,title,state,mergeStateStatus,statusCheckRollup,headRefOid,headRefName,baseRefName,url,isDraft,mergedAt,reviewDecision,mergeCommit
gh run view 27877483718 --repo ajoe734/execute-plans --json name,displayTitle,status,conclusion,createdAt,updatedAt,jobs,url
gh run view 27877483718 --repo ajoe734/execute-plans --log-failed
python3 scripts/agora_schema_bundle.py --verify
python3 scripts/agora_compat_manifest.py verify --allow-pending --manifest docs/contracts/agora/dev-compatibility-manifest.json
python3 scripts/agora_compat_manifest.py deployment-gate --manifest docs/contracts/agora/dev-compatibility-manifest.json
python3 -m pytest scripts/test_agora_compat_manifest.py -v
npm --prefix execute-plans run contract:drift
python3 scripts/agora_compat_manifest.py write --stdout
sha256sum docs/contracts/agora/dev-compatibility-manifest.json execute-plans/src/lib/bff-v1/agora/contract-snapshot.json execute-plans/src/lib/bff-v1/agora/types.ts services/control-plane/specs/agora/bundle_index.json services/control-plane/specs/agora/bundle_index.v1_1.json services/control-plane/openapi/agora_v1_1.openapi.yaml
```

Results:

- `git merge --ff-only origin/dev`: pass; branch moved to
  `5bc93c6eca3ccb2437d465dba9a8fd3b9ca441ce`.
- `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-6`:
  sidecar active, owner `Codex2`, reviewer `Codex`.
- `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003`: parent remains
  `blocked`, waiting for `Claude2`.
- `gh pr view 1852 --repo ajoe734/pantheon`: merged at
  `0765018c838547108fa56fcf089b5e2bbafd4387`.
- `gh pr view 63 --repo ajoe734/execute-plans`: open, unstable, head
  `e1cb9125c87d9ace0adf3dd9f17f24ff0542d9c5`, `integration-gate` failed.
- `gh run view 27877483718 --repo ajoe734/execute-plans`: job failed at
  `Aggregate release gate`; all earlier job steps were completed successfully.
- `python3 scripts/agora_schema_bundle.py --verify`: pass for all 15 frozen
  v1 indexed files.
- `python3 scripts/agora_compat_manifest.py verify --allow-pending --manifest
  docs/contracts/agora/dev-compatibility-manifest.json`: expected fail;
  expected local generated-types hash `0244eb11...`, committed manifest value
  `a6a9296...`.
- `python3 scripts/agora_compat_manifest.py deployment-gate --manifest
  docs/contracts/agora/dev-compatibility-manifest.json`: expected fail-closed
  on generated-types mismatch, pending status, placeholder frontend commits,
  frontend/backend contract commit mismatch, and non-empty blocking reasons.
- `python3 -m pytest scripts/test_agora_compat_manifest.py -v`: expected
  partial fail; 1 failed and 3 passed. Failed assertion expects
  `frontend-generated-types-not-agora-v1.1`, while fresh `write` produces only
  the two frontend commit placeholder blockers.
- `npm --prefix execute-plans run contract:drift`: pass; 20 bundle digests, 17
  schemas, and 96 OpenAPI operations verified.
- `python3 scripts/agora_compat_manifest.py write --stdout`: pass; fresh output
  records backend commit `5bc93c6e...`, generated-types hash `0244eb11...`,
  and only the two frontend commit placeholder blockers.
- Manifest sha256: `d5143fb19314d761fb5bd82e23d98e15b2058104bd81c93376aec1b02fceb01b`.
- Contract snapshot sha256: `fb750e29aa5099ad1afee69f0f4f794f5a70fe884aacb58e110bdecd896c6e28`.
- Generated types file sha256: `ce03bdc116bd8d5972920a5da9bf952b5314ca1ad564c02a9b5e3953dae59fc4`.

