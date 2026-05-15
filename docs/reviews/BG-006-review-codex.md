# Review Report: BG-006

**Task ID**: BG-006
**Artifact**: `OPERATOR_ACCEPTANCE_MATRIX.md`
**Reviewer**: Codex
**Date**: 2026-04-13
**Status**: Changes requested

## Findings

### 1. Blocking: the matrix downgrades the real CLI/internal fallback path to "not implemented"

`OPERATOR_ACCEPTANCE_MATRIX.md` currently says the CLI deployment, runtime, and kill-switch paths are `not implemented`, and Section 9 treats the CLI spec itself as future work. That is no longer true in this repo.

- Current matrix: `S-CLI` is marked `not implemented` for deployment, runtime pause, and kill-switch, and the evidence table repeats `not drilled (CLI not implemented)` (`OPERATOR_ACCEPTANCE_MATRIX.md:62`, `OPERATOR_ACCEPTANCE_MATRIX.md:79`, `OPERATOR_ACCEPTANCE_MATRIX.md:95`, `OPERATOR_ACCEPTANCE_MATRIX.md:199`, `OPERATOR_ACCEPTANCE_MATRIX.md:223`)
- Existing operator acceptance doc already records the secondary control path as real and implemented (`docs/02-architecture/consensus/phase2/OPERATOR_ACCEPTANCE_MATRIX.md:184-211`)
- `pantheon-admin` is wired to real internal API calls for deployment approve/reject, runtime pause/resume/force-halt, rollback execute/list/abort, and kill-switch activate/deactivate/status (`tools/pantheon_admin/cli.py:237-317`, `tools/pantheon_admin/cli.py:327-486`)
- The internal API exposes the corresponding real endpoints (`services/control_plane/internal_api.py:182-220`, `services/control_plane/internal_api.py:223-388`, `services/control_plane/internal_api.py:409-748`)

Why this blocks approval:
The task exists to publish acceptance truth for operator surfaces. Re-labeling an implemented fallback path as nonexistent would misstate production readiness, distort drill planning, and conflict with repo reality.

### 2. Blocking: BFF-outage routing sends pause/rollback to the kill-switch fast path instead of the actual fallback path

The runtime-control section says that when BFF is unavailable, pause and rollback must go through `S-EMRG`, and the routing rules reduce BFF outage behavior to `S-IAPI` plus `S-EMRG`. That is not the control-path split documented elsewhere in the repo.

- Current matrix routes BFF-unavailable pause/rollback to `S-EMRG` and omits the implemented CLI fallback from the canonical routing rules (`OPERATOR_ACCEPTANCE_MATRIX.md:75-79`, `OPERATOR_ACCEPTANCE_MATRIX.md:212-213`)
- L1 BFF HA policy says the non-BFF backup control paths are `admin CLI`, `control-plane internal API`, and `runtime-manager protected admin endpoint`, covering pause, rollback, kill-switch, and health diagnostics (`BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md:108-123`)
- The degraded-path contract explicitly lists runtime rollback and runtime pause on the admin CLI / direct HTTPS secondary path, separate from kill-switch activation/status (`services/control-plane/bff/DEGRADED_OPERATOR_PATH.md:99-110`)
- The implemented fallback coverage matches that split: rollback and pause have dedicated CLI/internal API routes, while kill-switch uses the fast path (`docs/02-architecture/consensus/phase2/OPERATOR_ACCEPTANCE_MATRIX.md:198-208`, `tools/pantheon_admin/cli.py:303-317`, `tools/pantheon_admin/cli.py:327-486`)

Why this blocks approval:
`S-EMRG` is the emergency fast path, not the general fallback home for rollback and pause. Publishing the current routing would send operators to the wrong surface during a BFF outage and blur rollback semantics with kill-switch semantics.

### 3. Medium: the runtime-manager outage row overstates unaffected BFF read availability

The degradation summary says that when `runtime-manager` is unavailable, `S-BFF` read remains unaffected. That is too strong and conflicts with the degraded-surface contract.

- Current matrix says `runtime-manager` outage leaves `S-BFF` read unaffected (`OPERATOR_ACCEPTANCE_MATRIX.md:185`)
- The degraded-path contract says downstream failure degrades only the affected surfaces, and runtime surfaces `RT-01` to `RT-04` can fall through replica/cache/unavailable states rather than staying unaffected (`services/control-plane/bff/DEGRADED_OPERATOR_PATH.md:41-82`)

Why this matters:
The acceptance matrix should preserve the "never show false-positive empty state" rule and per-surface degradation model. A blanket "BFF read unaffected" statement hides the required runtime-surface degradation behavior.

## Recommendation

Do not approve `BG-006` in its current form.

The artifact should be revised so that it:

1. Restores the repo-true status of the CLI/internal API fallback path.
2. Separates general pause/rollback fallback from the kill-switch fast path.
3. Aligns the degradation summary with the existing per-surface degradation contract.

## Re-review (2026-04-14)

The three findings above are resolved in the current draft. However, one new blocking issue remains before `BG-006` can move to `review_approved`.

### 4. Blocking: the role matrix now contradicts the per-surface authorization table

The updated document fixes the CLI status and BFF-outage routing, but Section 5 still grants several fallback surfaces to roles that the operation tables do not authorize.

- The role matrix says `governance.approver` can use `S-IAPI`, `deployment.operator` can use `S-CLI` fallback, and `runtime.operator` can use `S-EMRG` fallback (`OPERATOR_ACCEPTANCE_MATRIX.md:161-165`)
- The operation tables say the corresponding writes require higher roles: direct approval write on `S-IAPI` requires `governance.admin` + mTLS, deployment via CLI requires `deployment.admin`, runtime pause via CLI requires `runtime.admin`, and `S-EMRG` emergency actions require `emergency.operator` (`OPERATOR_ACCEPTANCE_MATRIX.md:59-62`, `OPERATOR_ACCEPTANCE_MATRIX.md:75-79`, `OPERATOR_ACCEPTANCE_MATRIX.md:93-95`)
- The degraded-path contract also keeps the non-BFF rollback/pause/kill-switch writes behind admin-grade RBAC rather than the normal operator lane (`services/control-plane/bff/DEGRADED_OPERATOR_PATH.md:103-108`, `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md:129-133`)

Why this blocks approval:
Section 5 is the operator-facing RBAC lookup for the whole acceptance matrix. In its current form it tells lower-privilege roles that they can use fallback surfaces they are not actually permitted to invoke, which would misroute operators during a BFF outage and weaken the acceptance truth this task is supposed to publish.

### Updated recommendation

Do not approve `BG-006` yet. The next revision should keep the already-fixed CLI/routing changes and repair Section 5 so that each role lists only the surfaces that match the per-operation authorization rows.

## Re-review approval (2026-04-14)

The Section 5 role matrix now matches the per-surface authorization rows:

- `governance.approver` is limited to `S-BFF`, with direct approval writes still reserved for `governance.admin` on `S-IAPI`
- `deployment.operator` is limited to `S-BFF`, with CLI fallback still reserved for `deployment.admin`
- `runtime.operator` is limited to `S-BFF`, with `S-CLI` still reserved for `runtime.admin` and `S-EMRG` still reserved for `emergency.operator`
- `runtime.admin` lists only `S-IAPI` and `S-CLI`, leaving `S-EMRG` to `emergency.operator`

The earlier CLI/internal fallback, BFF-outage routing, and runtime-manager degradation fixes remain intact in the current draft. No blocking review findings remain.

### Approval recommendation

`BG-006` is approved and can move to `review_approved`.
