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
- `changed_files` is required so Pantheon review can target the right front-end files.
- `pantheon_review_hint` is a short machine-readable hint such as `review-ui`, `update-bff`, or `prepare-backend-delivery`.
- `source_commit` pins the front-repo commit Pantheon should inspect during review or replay.

### `backend-delivery`

Required fields:

- `feature_id`
- `type: backend-delivery`
- `target_repo`
- `workbench`
- `screen_id`
- `status`
- `backend_commit`
- `contracts_version`
- `sdk_version`
- `delivery_note_path`
- `contract_lock_path`
- `followup_expectation`
- `source_payload`

Semantics:

- `contracts_version` must identify the Pantheon contract lock used for the delivery, either as a semantic version, commit sha, or artifact hash.
- `sdk_version` may be `n/a` during direct BFF-client wiring, but the field stays present for schema stability.
- `contract_lock_path` points to a version-lock or commit-lock artifact that the front repo can use for CI validation.
- `source_payload` points to the `frontend-feedback`, `bff-gap`, or `ui-done` payload that triggered the delivery.

Recommended status values:

- `delivered`: backend or contract changes are ready for the next UI cycle.
- `followup-required`: delivery is partial and the front repo may proceed only for the listed scope.
- `blocked`: Pantheon cannot yet complete the requested delivery and a human or dependency owner must intervene.

## Trigger Sources

| Trigger | Source | Effect |
|---|---|---|
| `pantheon.contract_ready` | Pantheon workflow or orchestrator | mirrors the handoff bundle into the front repo and publishes `lovable-ui-task` |
| Human Lovable run | Lovable user action | implements or updates the UI in the front repo |
| `pantheon.frontend_feedback` | front repo workflow | notifies Pantheon that feedback artifacts are ready |
| `pantheon.bff_gap` | front repo workflow | notifies Pantheon of a hard contract blocker |
| `pantheon.ui_done` | front repo workflow | notifies Pantheon that the UI implementation is ready for review or integration |
| `pantheon.backend_delivery` | Pantheon workflow | delivers backend or contract completion notes back to the front repo |
| `workflow_dispatch` replay | human operator | replays a stuck or failed loop step without reauthoring payloads |

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

- the sibling checkout `../front-ai-trading-system` must exist locally for mirror validation
- GitHub labels `pantheon-bus` and `coordination-bus` must exist if the legacy issue bus remains enabled
- the new dispatch workflows must live on the default branch of each repo

## Failure and Replay Path

- if a front-repo feedback file is missing, Pantheon must not continue automatically
- if GitHub dispatch fails, operators use the manual replay workflow instead of editing payloads by hand
- if the front repo checkout is absent locally, `LOOP-003` treats that as a hard prerequisite failure
- if a cycle stalls after Lovable but before feedback publish, the source of truth remains the front repo commit plus `.coordination/requests`
- if the legacy GitHub issue bus fails due to missing labels, that must not block `.coordination`-based loop execution once dispatch workflows are available

### Replay contract

- Replay inputs are `feature_id`, `event_type`, `payload_path`, and the source commit or ref that originally produced the payload.
- Replay must validate that `payload_path` still exists and that `type` matches `event_type` before dispatch.
- Replay reuses the existing payload file; if content must change, a new normal-cycle payload must be published instead of mutating the replay target.
- `backend-delivery.source_payload` and `frontend-feedback.source_commit` are the canonical join points for replaying the last Pantheon or front-repo step.

### Failure ownership

- Missing mirrored handoff files are Pantheon mirror failures until proven otherwise.
- Missing feedback bundle files are front-repo publication failures until proven otherwise.
- Dispatch transport failure does not invalidate the payload; it only blocks notification and is recoverable through replay.
