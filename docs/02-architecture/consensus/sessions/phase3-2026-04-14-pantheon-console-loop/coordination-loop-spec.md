# Coordination Loop Spec

## Summary

This document defines the target Pantheon Console closed loop:

1. Pantheon produces a frontend-ready handoff packet
2. Lovable reads the handoff from the front repo through GitHub sync
3. the front repo writes structured feedback artifacts and machine handoffs back into `.coordination`
4. Pantheon consumes that feedback, updates BFF or contract work, and emits a delivery packet
5. the front repo uses the delivery packet for the next cycle

`.coordination` remains the canonical machine-readable protocol. Human-readable support files remain alongside it, but they do not replace it.

## Protocol Rules

- All `.coordination` payloads are YAML and must include `feature_id` and `type`.
- The feature-scoped filename is part of the protocol and must stay stable across replay.
- All repo paths in payload fields and dispatch envelopes are repo-relative paths using forward slashes; they must not be rewritten into local absolute paths during mirror, dispatch, or replay.
- Pantheon-authored payloads remain authoritative in `pantheon`; front-repo mirrors do not create alternate truth.
- Front-repo feedback bundles remain authoritative in `front-ai-trading-system`; Pantheon consumes them through referenced paths instead of mirroring them back.
- Replay must re-use an existing payload path and commit reference. Operators may re-dispatch a stalled step, but they must not mutate payload contents during replay.

## Repo Roles

| Repo | Role |
|---|---|
| `pantheon` | canonical BFF, contract, orchestration, packet generation, task materialization, runtime and governance authority |
| `front-ai-trading-system` | Pantheon Console front-end repo, screen implementation, UI state, Lovable-connected handoff target |
| GitHub | default-branch sync surface, cross-repo dispatch transport, audit trail |
| Lovable | human-triggered UI lane that consumes front-repo packets and writes front-repo feedback back to GitHub |

## File System Contract

### Pantheon-side canonical paths

- `.coordination/responses/<feature>-contract-ready.yaml`
- `.coordination/responses/<feature>-lovable-ui-task.yaml`
- `.coordination/responses/<feature>-backend-delivery.yaml`
- `.coordination/requests/<feature>-bff-gap.example.yaml`
- `.coordination/requests/<feature>-ui-done.example.yaml`
- `docs/screens/<screen>.md`
- `docs/pantheon-handoffs/<feature>/...` mirrored into the front repo
- `docs/pantheon-delivery/<feature>/DELIVERY_NOTE.md`

### Front-repo canonical paths

- `.coordination/responses/<feature>-contract-ready.yaml` mirror only
- `.coordination/responses/<feature>-lovable-ui-task.yaml` mirror only
- `.coordination/responses/<feature>-backend-delivery.yaml` mirror only
- `.coordination/requests/<feature>-bff-gap.yaml`
- `.coordination/requests/<feature>-frontend-feedback.yaml`
- `.coordination/requests/<feature>-ui-done.yaml`
- `docs/pantheon-handoffs/<feature>/...` mirrored Pantheon handoff bundle
- `docs/pantheon-feedback/<feature>/LOVABLE_CHANGE_FEEDBACK.md`
- `docs/pantheon-feedback/<feature>/API_GAP_REQUESTS.json`
- `docs/pantheon-feedback/<feature>/UI_DECISIONS.md`
- `docs/pantheon-feedback/<feature>/QA_STATUS.md`

### Naming and location rules

- `<feature>` must be the canonical feature or packet id such as `F-042` or `PKT-001-governance-review`.
- Pantheon-owned response payloads live under `.coordination/responses/`; front-owned request payloads live under `.coordination/requests/`.
- `docs/pantheon-handoffs/<feature>/` is the mirror root for Pantheon-authored support files consumed by the front repo.
- `docs/pantheon-feedback/<feature>/` is the front-owned feedback bundle root and is never back-mirrored into Pantheon automatically.
- `docs/pantheon-delivery/<feature>/` holds Pantheon-authored delivery notes referenced by `backend-delivery`.

## Payload Types

### Existing payloads retained

| Type | Direction | Purpose |
|---|---|---|
| `contract-ready` | Pantheon -> front | says BFF and contract material is ready for UI implementation |
| `lovable-ui-task` | Pantheon -> front | gives Lovable a machine-readable screen task packet |
| `bff-gap` | front -> Pantheon | stops UI work when required contract fields are missing |
| `ui-done` | front -> Pantheon | final signal that implementation and feedback bundle are ready for Pantheon review |

### New payloads

| Type | Direction | Purpose |
|---|---|---|
| `frontend-feedback` | front -> Pantheon | carries the front-end feedback bundle for the current cycle |
| `backend-delivery` | Pantheon -> front | carries the backend or contract delivery note for the next UI cycle |

## Loop Cycle Contract

The closed loop is feature-scoped. Each cycle keeps the same `<feature>` filename stem while the owning commit references advance.

| Step | Canonical payload | Owner | Completion condition |
|---|---|---|---|
| 1 | `contract-ready` | Pantheon | Pantheon has published contract and handoff artifacts needed for UI work |
| 2 | `lovable-ui-task` | Pantheon | front repo has a machine-readable UI task packet and mirrored handoff bundle |
| 3 | `frontend-feedback` | front repo | the required feedback bundle exists and Pantheon can review the front-end result |
| 4a | `bff-gap` | front repo | UI work is blocked on missing backend or contract truth and Pantheon must respond |
| 4b | `ui-done` | front repo | UI implementation is ready for Pantheon review or integration follow-up |
| 5 | `backend-delivery` | Pantheon | Pantheon has published the next contract, backend, or delivery-lock note for the front repo |

Rules:

- `frontend-feedback` is expected for every completed Lovable or front-end cycle, even when `bff-gap` or `ui-done` is also emitted.
- `bff-gap` is the fast-fail branch for missing contract truth; `ui-done` is the ready-for-review branch; both may reference the same feature cycle as the accompanying `frontend-feedback`.
- `backend-delivery` closes the current Pantheon response leg. If more UI work is required, Pantheon publishes a new `lovable-ui-task` for the same feature instead of mutating the prior one.

### Closed-loop outcome branches

Every feature cycle must resolve through exactly one of these front-repo branch outcomes after `lovable-ui-task` is consumed:

| Branch | Required payloads | Meaning | Pantheon next action |
|---|---|---|---|
| UI blocked on contract truth | `frontend-feedback` + `bff-gap` | UI lane cannot proceed without backend or contract correction | publish `backend-delivery` when the blocker is resolved, then optionally emit a fresh `lovable-ui-task` |
| UI completed and ready for review | `frontend-feedback` + `ui-done` | UI lane finished a reviewable implementation cycle | review front-end changes, then publish `backend-delivery` if Pantheon follow-up exists |
| UI completed but not yet ready for formal handoff | `frontend-feedback` only | feedback bundle is available but no hard blocker or final completion signal was raised | Pantheon may review, request changes, or publish a replacement `lovable-ui-task` for the same feature |

Rules:

- `frontend-feedback` is the required machine summary for all three branches and is never skipped just because `bff-gap` or `ui-done` exists.
- `bff-gap` and `ui-done` are mutually exclusive within the same front-repo commit for a single feature cycle.
- If a later front-repo commit changes the branch outcome, the front repo must publish a new payload set with the same feature-stable filenames and updated commit references rather than editing the meaning of an already-dispatched commit in place.

## Payload Schemas

### `lovable-ui-task`

Required fields:

- `feature_id`
- `type: lovable-ui-task`
- `project`
- `status`
- `workbench`
- `screen`
- `screen_id`
- `ui_spec_path`
- `frontend_change_spec_path`
- `allowed_endpoints`
- `constraints`
- `acceptance`
- `required_feedback`
- `delivery_dependencies`
- `links`
- `gap_handoff_path`
- `gap_handoff_template`
- `completion_handoff_path`
- `completion_handoff_template`

Notes:

- `screen` remains for backward compatibility with the existing publisher and mirror flow.
- `screen_id` is the stable canonical id for the screen packet.
- `ui_spec_path` points to the canonical Pantheon screen spec or packet spec that the front repo must implement.
- `frontend_change_spec_path` points to the expected front-repo change plan or screen-specific implementation note consumed by Lovable or a front-end engineer.
- `required_feedback` must enumerate the four feedback artifact paths under `docs/pantheon-feedback/<feature>/`.
- `delivery_dependencies` lists any contract, backend-delivery, or replay prerequisite that must exist before the next Lovable cycle.
- `links` should carry mirrored artifact paths rather than repo-absolute local paths.
- `gap_handoff_path` and `completion_handoff_path` stay feature-stable across replay; only the source commit changes between loop cycles.

Recommended status values:

- `ready`: safe for Lovable or a front-end engineer to implement.
- `blocked`: packet is published but should not be acted on until a prerequisite is cleared.
- `superseded`: packet is retained for audit only and replaced by a newer packet or replay.

### `frontend-feedback`

Required fields:

- `feature_id`
- `type: frontend-feedback`
- `source_repo`
- `source_branch`
- `workbench`
- `screen_id`
- `status`
- `feedback_bundle_dir`
- `feedback_path`
- `api_gap_requests_path`
- `ui_decisions_path`
- `qa_status_path`
- `blocking_summary`
- `changed_files`
- `pantheon_review_hint`
- `source_commit`

Semantics:

- `status=completed` means the feedback bundle is ready and Pantheon should continue review or integration.
- `status=blocked` means the UI lane is blocked even if a `bff-gap` was not emitted.
- `feedback_path` should point to `docs/pantheon-feedback/<feature>/LOVABLE_CHANGE_FEEDBACK.md` so Pantheon has a stable human-readable summary anchor for review.
- `changed_files` is required so Pantheon review can target the right front-end files.
- `pantheon_review_hint` is a short machine-readable hint such as `review-ui`, `update-bff`, or `prepare-backend-delivery`.
- `source_commit` inside `frontend-feedback` pins the reviewed front-repo UI cycle commit that Pantheon should inspect.
- The transport envelope `source_commit` points to the commit that actually contains `payload_path` and must remain replayable.
- `frontend-feedback` summarizes the cycle outcome but does not replace `bff-gap` or `ui-done`; those payloads remain the authoritative branch signal for blocked versus completed UI execution.

### `backend-delivery`

Required fields:

- `feature_id`
- `type: backend-delivery`
- `target_repo`
- `workbench`
- `screen_id`
- `status`
- `backend_commit`
- `bff_contract_version`
- `delivery_note_path`
- `contract_lock_path`
- `followup_expectation`
- `source_payload`

Optional fields:

- `sdk_version`

Semantics:

- `bff_contract_version` must identify the Pantheon BFF or contract lock used for the delivery, either as a semantic version, commit sha, or artifact hash.
- `sdk_version` is only present when Pantheon publishes a front-end SDK artifact. Direct BFF-client wiring must omit the field instead of fabricating placeholder values.
- `contract_lock_path` points to a version-lock or commit-lock artifact that the front repo can use for CI validation.
- `source_payload` points to the `frontend-feedback`, `bff-gap`, or `ui-done` payload that triggered the delivery.
- `backend_commit`, `bff_contract_version`, and `contract_lock_path` form the minimum version-lock tuple for delivery replay or CI verification; if any of them changes, Pantheon must publish a new normal-cycle payload instead of replaying the old one.

## Dispatch Receiver Rules

All receivers for `repository_dispatch` or `workflow_dispatch` replay events must enforce the same validation rules before acting on a payload:

1. `feature_id` in the transport envelope must match `feature_id` inside the referenced YAML payload.
2. `payload_path` must exist at the transport envelope `source_commit` in `source_repo`.
3. The payload `type` must be valid for the incoming `event_type`.
4. Repo-relative support-file paths referenced by the payload must stay inside the owning repo and must not be rewritten into absolute paths.
5. Replays must use `trigger_mode=replay` and include `replay_of`.

If any validation fails, the receiver must stop before mutating mirrors, task state, or downstream artifacts.

Recommended status values:

- `delivered`: backend or contract changes are ready for the next UI cycle.
- `followup-required`: delivery is partial and the front repo may proceed only for the listed scope.
- `blocked`: Pantheon cannot yet complete the requested delivery and a human or dependency owner must intervene.

## Protocol Fixtures

The repo-level `.coordination` examples must remain aligned with this spec and serve as the review fixture for workflow or mirror changes:

- `.coordination/responses/F-042-lovable-ui-task.yaml`
- `.coordination/requests/F-042-frontend-feedback.example.yaml`
- `.coordination/responses/F-042-backend-delivery.example.yaml`

Rules:

- fixture payloads must use repo-relative paths only
- fixture filenames stay feature-stable so replay and mirror tooling can be regression-tested against them
- if the schema changes, update these fixtures in the same change as the spec

## Trigger Sources

`repository_dispatch` is the primary machine-to-machine trigger path for this loop. The legacy GitHub issue or label bus may remain enabled for compatibility or audit breadcrumbs, but it is not a prerequisite for normal closed-loop execution.

| Trigger | Source | Effect |
|---|---|---|
| `pantheon.contract_ready` | Pantheon workflow or orchestrator | mirrors the handoff bundle into the front repo and publishes `lovable-ui-task` |
| Human Lovable run | Lovable user action | implements or updates the UI in the front repo |
| `pantheon.frontend_feedback` | front repo workflow | notifies Pantheon that feedback artifacts are ready |
| `pantheon.bff_gap` | front repo workflow | notifies Pantheon of a hard contract blocker |
| `pantheon.ui_done` | front repo workflow | notifies Pantheon that the UI implementation is ready for review or integration |
| `pantheon.backend_delivery` | Pantheon workflow | delivers backend or contract completion notes back to the front repo |
| `workflow_dispatch` replay | human operator | replays a stuck or failed loop step without reauthoring payloads |

### Dispatch envelope

Every `repository_dispatch` or `workflow_dispatch` replay event in this loop must carry the same canonical transport envelope:

- `event_type`: one of the trigger names above
- `feature_id`: canonical feature or packet id
- `payload_path`: repo-relative path to the YAML payload that the receiver must load
- `source_repo`: repo that owns the payload and commit reference
- `source_commit`: transport commit sha that contains `payload_path` and was used to publish or replay the event
- `source_ref`: branch or tag used when a human replay targets a named ref instead of only a sha
- `trigger_mode`: `normal` or `replay`
- `origin_workflow`: workflow or orchestrator entrypoint that emitted the dispatch

Optional fields:

- `mirror_commit`: commit sha of the mirrored front-repo sync when the payload originated in Pantheon
- `replay_of`: prior dispatch run id, workflow run id, or audit token when retrying a stalled step
- `requested_by`: operator or automation identity that initiated the replay

### Event-to-payload mapping

| `event_type` | Allowed payload `type` | Owning repo |
|---|---|---|
| `pantheon.contract_ready` | `contract-ready` | `pantheon` |
| `pantheon.frontend_feedback` | `frontend-feedback` | `front-ai-trading-system` |
| `pantheon.bff_gap` | `bff-gap` | `front-ai-trading-system` |
| `pantheon.ui_done` | `ui-done` | `front-ai-trading-system` |
| `pantheon.backend_delivery` | `backend-delivery` | `pantheon` |

`pantheon.contract_ready` may trigger the mirror and `lovable-ui-task` publication flow, but the transport envelope must still point at the concrete Pantheon-owned payload that started the receiver step.

## Replay Contract

Replay is for transport or workflow recovery only. It is not a patch path for changing protocol meaning after publication.

### Replay eligibility

| Payload type | Replay allowed when | Replay forbidden when |
|---|---|---|
| `contract-ready` | mirror or downstream dispatch failed, but the contract packet commit is still valid | contract artifacts changed and need a new handoff cycle |
| `lovable-ui-task` | front repo did not receive or act on the packet and the task contents are still current | acceptance, links, or required feedback changed |
| `frontend-feedback` | Pantheon receiver or review automation failed after the front repo already published the bundle | the feedback bundle contents or changed-files summary changed |
| `bff-gap` | Pantheon did not consume the blocker signal | blocker details changed or the blocker was resolved |
| `ui-done` | Pantheon did not consume the completion signal | the front-end implementation commit changed |
| `backend-delivery` | front repo did not receive the delivery note and the version-lock tuple is unchanged | `backend_commit`, `bff_contract_version`, or `contract_lock_path` changed |

### Replay rules

- Replay must reuse the original `payload_path` and the original owning repo for that payload type.
- Replay may update `source_ref`, `replay_of`, and `requested_by` in the dispatch envelope, but it must not mutate the YAML payload contents.
- If operators need to change payload fields, support-file paths, or version-lock fields, they must publish a new normal-cycle payload and emit a non-replay dispatch.
- Replay receivers should record that the attempt was a replay, but must otherwise execute the same validation and side-effect logic as a normal dispatch.
- Front-repo feedback bundles and Pantheon delivery bundles remain authoritative in their owning repos during replay; mirror consumers must re-read from the referenced commit instead of relying on cached local copies.

Rules:

- The dispatch envelope references payload files by path; it must not inline mutable payload content into `client_payload`.
- `payload_path` and `source_commit` are the minimum replay tuple. If either is unknown, the event is not replayable.
- `source_repo` determines where `payload_path` is resolved. Pantheon never reinterprets a front-repo path as a Pantheon-local path, and vice versa.
- `trigger_mode=replay` means transport retry only; downstream workers must treat the referenced YAML payload as immutable.

## Mirror Contract

- Pantheon remains the source of truth for handoff inputs and mirrors them into the front repo through `coordination_repo_mirror.py`.
- mirror target for input bundles: `docs/pantheon-handoffs/<feature>/`
- Pantheon mirrors `.coordination/responses/<feature>-contract-ready.yaml`, `.coordination/responses/<feature>-lovable-ui-task.yaml`, the prompt packet, request templates, and referenced BFF/spec/example artifacts needed by the front repo.
- feedback bundles are not mirrored back automatically; Pantheon consumes them from front-repo paths referenced in `frontend-feedback`.
- `mirror_only: true` continues to mark Pantheon-authored files that are copied into the front repo.
- `backend-delivery` is authored in Pantheon, mirrored into the front repo, and must reference only Pantheon-owned delivery artifacts.

## Required Feedback Artifacts

Every Lovable cycle must produce:

- `LOVABLE_CHANGE_FEEDBACK.md`
- `API_GAP_REQUESTS.json`
- `UI_DECISIONS.md`
- `QA_STATUS.md`

Rules:

- absence of the feedback bundle means the cycle is incomplete, even if UI code changed
- `frontend-feedback.feedback_bundle_dir` must resolve to the directory containing these four artifacts
- `ui-done` should only be emitted after the feedback bundle exists
- `bff-gap` remains the fast-fail path when the screen cannot be implemented safely
- `frontend-feedback` is the canonical summary record for the bundle; the markdown and json support files do not replace it

## GitHub Automation Target

### Pantheon workflows

- `coordination-dispatch-receiver.yml`
  - receives `pantheon.frontend_feedback`, `pantheon.bff_gap`, `pantheon.ui_done`
  - validates payload shape
  - queues the next Pantheon worker or replay request
- `coordination-manual-replay.yml`
  - `workflow_dispatch`
  - replays one loop step from an existing feature id and payload path

### Front-repo workflows

- `pantheon-handoff-receiver.yml`
  - receives `pantheon.contract_ready` and `pantheon.backend_delivery`
  - validates mirrored packet presence and updates audit state
- `pantheon-feedback-publisher.yml`
  - publishes `pantheon.frontend_feedback`, `pantheon.bff_gap`, and `pantheon.ui_done`
  - sends the file paths needed by Pantheon review or follow-up lanes

### Bootstrap prerequisites

#### Hard prerequisite: sibling checkout

The directory `../front-ai-trading-system` (one level above the `pantheon` root, i.e., a sibling checkout of the front repo) is a **hard prerequisite** for all local mirror and validation operations.

- If the sibling checkout is absent, `coordination_repo_mirror.py` and all mirror validation steps must halt immediately with a descriptive error. They must not proceed, silently skip, or produce partial output.
- This directory must point to the canonical `front-ai-trading-system` repository checked out on its default branch (or an explicitly specified ref). A stale or partial clone is treated the same as absent.
- Operators who cannot check out the front repo locally must route validation through the GitHub Actions mirror workflow instead of running local mirror tooling.
- All CI validation and pre-dispatch checks in Pantheon workflows must verify that the sibling checkout or the equivalent CI workspace checkout is present and on a valid commit before executing mirror or validation steps.

This is not optional for closed-loop execution. Any automation step that bypasses this check is in violation of the loop contract.

#### Label bootstrap

The following GitHub labels must exist on both the `pantheon` and `front-ai-trading-system` repositories before the legacy issue bus can be used for compatibility or audit mirroring:

| Label | Color (suggested) | Purpose |
|---|---|---|
| `pantheon-bus` | `#0075ca` | marks issues or PRs that carry cross-repo coordination bus events |
| `coordination-bus` | `#e4e669` | marks issues or PRs that carry `.coordination` protocol messages or replay records |

Bootstrap procedure:

1. Verify the labels exist: `gh label list --repo <owner>/pantheon` and `gh label list --repo <owner>/front-ai-trading-system`.
2. If absent, create them:
   ```bash
   gh label create pantheon-bus     --color 0075ca --description "Cross-repo coordination bus event" --repo <owner>/pantheon
   gh label create coordination-bus --color e4e669 --description ".coordination protocol message or replay record" --repo <owner>/pantheon
   gh label create pantheon-bus     --color 0075ca --description "Cross-repo coordination bus event" --repo <owner>/front-ai-trading-system
   gh label create coordination-bus --color e4e669 --description ".coordination protocol message or replay record" --repo <owner>/front-ai-trading-system
   ```
3. Record the label creation commit or API response as evidence in the loop bootstrap audit log.

Rules:

- Label names are stable and must not be renamed. Renaming breaks any existing label-filter queries in the legacy bus.
- If the legacy issue bus is disabled, label creation may be deferred, but the labels must still exist on both repos before any re-enablement.
- Label creation is a one-time bootstrap step. It does not repeat per feature cycle.
- The new `.coordination`-based dispatch loop does not depend on these labels for normal execution. Their absence must not block dispatch-based loop steps once the required workflows are deployed.

#### New dispatch workflows must be on the default branch

The following workflows must be merged to the default branch of each repo before closed-loop dispatch is enabled:

| Repo | Required workflow file |
|---|---|
| `pantheon` | `.github/workflows/coordination-dispatch-receiver.yml` |
| `pantheon` | `.github/workflows/coordination-manual-replay.yml` |
| `front-ai-trading-system` | `.github/workflows/pantheon-handoff-receiver.yml` |
| `front-ai-trading-system` | `.github/workflows/pantheon-feedback-publisher.yml` |

Bootstrap validation: run `gh workflow list --repo <owner>/<repo>` on both repos and confirm all four workflows appear and are in an `active` state before sending the first `repository_dispatch` event.

#### Mirror validation checklist

Before sending `pantheon.contract_ready` or beginning the first Lovable cycle for a feature, validate the following paths. All paths are relative to the repo root unless noted.

**Handoff bundle (Pantheon-side, mirrored into front repo)**

| Path | Repo | Required | Notes |
|---|---|---|---|
| `.coordination/responses/<feature>-contract-ready.yaml` | `pantheon` | yes | authored in Pantheon; mirrored into front repo |
| `.coordination/responses/<feature>-lovable-ui-task.yaml` | `pantheon` | yes | authored in Pantheon; mirrored into front repo |
| `docs/pantheon-handoffs/<feature>/` | `front-ai-trading-system` | yes | mirror target directory; must exist after mirror step |
| `docs/pantheon-handoffs/<feature>/` (contract-ready, lovable-ui-task, prompt packet, and referenced BFF/spec artifacts) | `front-ai-trading-system` | yes | all files listed in `lovable-ui-task.links` must be present |

**Request templates (Pantheon-side example fixtures)**

| Path | Repo | Required | Notes |
|---|---|---|---|
| `.coordination/requests/<feature>-bff-gap.example.yaml` | `pantheon` | yes | template the front repo uses when raising a contract blocker |
| `.coordination/requests/<feature>-ui-done.example.yaml` | `pantheon` | yes | template the front repo uses when signalling completion |

**Feedback bundle (front-repo-side, consumed by Pantheon)**

| Path | Repo | Required before `ui-done` | Notes |
|---|---|---|---|
| `docs/pantheon-feedback/<feature>/LOVABLE_CHANGE_FEEDBACK.md` | `front-ai-trading-system` | yes | human-readable cycle summary |
| `docs/pantheon-feedback/<feature>/API_GAP_REQUESTS.json` | `front-ai-trading-system` | yes | structured API gap list |
| `docs/pantheon-feedback/<feature>/UI_DECISIONS.md` | `front-ai-trading-system` | yes | front-end decision record |
| `docs/pantheon-feedback/<feature>/QA_STATUS.md` | `front-ai-trading-system` | yes | QA and smoke-test status |
| `.coordination/requests/<feature>-frontend-feedback.yaml` | `front-ai-trading-system` | yes | machine-readable cycle summary; references the four files above |

Validation rules:

- Absence of any required handoff bundle file is a Pantheon mirror failure. Fix by re-running `coordination_repo_mirror.py` or replaying `pantheon.contract_ready`.
- Absence of any required feedback bundle file before `ui-done` is a front-repo publication failure. Pantheon must not continue automatic review until all four artifacts exist.
- Request template paths are Pantheon fixtures and must exist in the Pantheon repo before the handoff is dispatched. They are never generated by the front repo.
- The mirror validation checklist must be re-run at the start of each new feature cycle (i.e., each time a new `lovable-ui-task` is published for the same feature), not just at initial bootstrap.

## Failure and Replay Path

- if a front-repo feedback file is missing, Pantheon must not continue automatically
- if GitHub dispatch fails, operators use the manual replay workflow instead of editing payloads by hand
- if the front repo checkout is absent locally, `LOOP-003` treats that as a hard prerequisite failure
- if a cycle stalls after Lovable but before feedback publish, the source of truth remains the front repo commit plus `.coordination/requests`
- if the legacy GitHub issue bus fails due to missing labels, that must not block `.coordination`-based loop execution once dispatch workflows are available on the default branch

### Replay contract

- Replay inputs are `feature_id`, `event_type`, `payload_path`, and the source commit or ref that originally produced the payload.
- Replay must validate that `payload_path` still exists and that `type` matches `event_type` before dispatch.
- Replay reuses the existing payload file; if content must change, a new normal-cycle payload must be published instead of mutating the replay target.
- `backend-delivery.source_payload` and `frontend-feedback.source_commit` are the canonical review join points for the last Pantheon or front-repo step.
- The transport replay tuple remains `payload_path` plus the transport envelope `source_commit`.
- Replaying `pantheon.contract_ready` or `pantheon.backend_delivery` also requires the mirrored handoff bundle to exist at the referenced front-repo paths before dispatch is retried.
- Replaying `pantheon.frontend_feedback`, `pantheon.bff_gap`, or `pantheon.ui_done` requires the front-repo commit to contain the referenced feedback bundle or request payload unchanged at `payload_path`.
- Successful replay must preserve the original feature-scoped filename. Operators may advance the source commit to a newer validating commit only when the payload file contents remain byte-equivalent.

### Failure ownership

- Missing mirrored handoff files are Pantheon mirror failures until proven otherwise.
- Missing feedback bundle files are front-repo publication failures until proven otherwise.
- Dispatch transport failure does not invalidate the payload; it only blocks notification and is recoverable through replay.
