# Qwen Readout — Replacement Coverage by Claude

> Agent of record: Qwen
> Replacement reviewer: Claude
> Reason: the Qwen auto lane has repeatedly stalled in this repo, so the facilitator absorbs the schema/object-boundary review for this session.

## Lane

- Agent: Claude (covering Qwen lane)
- Capability focus: Audit schemas, object boundaries, and contract formalization gaps for the governance and evolution service surfaces.

## Canonical Sources Read

- L0: `README.md`, `planning-session.json`
- L1: `TARGET_ARCHITECTURE.md`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md`
- L2: `starter-draft.md`, `phase2-phase6-gap-inventory.md`, `claude-readout.md`

## Working Interpretation

- `runtime-control` and `governance-api` must remain separate services in the first materialization wave. The runtime-control service owns side-effectful operator commands such as kill-switch, pause, rollback, and runtime mutation dispatch. The governance-api service owns approval, deployment, binding, and evolution decision state plus the action endpoints that advance those decisions.
- The governance-api surface should be explicit rather than "wrap the existing domain objects somehow." At minimum the session needs named resource families for approvals, deployment plans, runtime bindings, and evolution decisions. If these surfaces stay implicit, `SVC-GOVERNANCE-API` will be too vague to review and the BFF rewiring step will not know which contracts are canonical.
- BFF must stop being the last place where evolution actions short-circuit locally. Once this wave lands, BFF should call governance-api for approval and evolution state changes, and runtime-control only for live operational commands.

## Risks / Contradictions

- Risk 1: `command_executor.py` still treats evolution approval/action as local placeholders. That creates a false service boundary where deployment and kill-switch are service-backed but evolution is not.
- Risk 2: the current task slice wording does not yet require an explicit response/request schema contract for governance-api. Without endpoint families and payload shapes, the session can claim "API exposed" while still leaving BFF/client integration ambiguous.
- Risk 3: `SVC-SURFACES` can only be reviewed honestly if `SVC-GOVERNANCE-API` commits to which state transitions are canonical writes versus read-only projections.

## Suggested Task Slice Additions

- `SVC-GOVERNANCE-API` should require named endpoint families for:
  - approvals / promotion decisions
  - deployment planning and deployment approval
  - runtime bindings and capital/binding mutation
  - evolution decisions and evolution action dispatch
- `SVC-GOVERNANCE-API` should explicitly state that evolution approval/action endpoints live under governance-api, not runtime-control.
- `SVC-SURFACES` should require BFF service clients to consume governance-api contracts directly instead of re-encoding state transitions in local placeholder logic.

## Citations

- [Q1] `TARGET_ARCHITECTURE.md`: the target architecture already separates runtime intervention from governance and approval flow; the service split should preserve that boundary instead of collapsing it into one catch-all control service.
- [Q2] `BINDING_AND_DEPLOYMENT_SEMANTICS.md`: deployment planning and binding mutation are governance semantics, so they belong with approval/evolution state, not inside the low-latency operator command path.
- [Q3] `services/control-plane/bff/command_executor.py`: deployment/pause/rollback already dispatch through the protected internal API, while evolution approval/action still terminate in local placeholder branches.
- [Q4] `EVOLUTION_REVIEW_AND_THRESHOLDS.md`: evolution decisions are governed approval artifacts, which supports placing approval/action endpoints in governance-api.
- [Q5] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md`: the residual gap inventory frames phase 4 as operational service exposure and boundary convergence, not new domain-model invention.
