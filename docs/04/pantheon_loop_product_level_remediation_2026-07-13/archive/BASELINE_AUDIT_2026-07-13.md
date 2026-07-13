# Baseline Audit — 2026-07-13

Status: archived planning evidence; not a completion claim

Machine snapshot: [BASELINE_AUDIT_2026-07-13.json](BASELINE_AUDIT_2026-07-13.json)

This audit is the evidence boundary used to create the product remediation DAG.
The clean planning worktree started at Pantheon
`349249e8f5ab1a89f82afd71925eb99342d66ed1`; `origin/dev` advanced to
`2e6c169a7867fd161cebdfe1baa13f932ebd312e` while the packet was authored.
Every fleet task must therefore re-audit its scope before editing.

## Runtime truth

An authenticated, redacted `/bff/v5/loop-health` read at 12:49 UTC returned:

| Metric | Baseline |
| --- | ---: |
| Canonical rows | 12 |
| Controller records | 0 |
| Live rows | 0 |
| Reconciled rows | 0 |
| API-only rows | 11 |
| Manual rows | 1 |
| Surface/source | degraded / registry_metadata |

No token is stored in this archive. A public unauthenticated recheck correctly
returned 401 and was not used to weaken the earlier authenticated result.

The archive contained 58 terminal `LOOP-AUTO-*` records: 37 primary tasks and
21 sidecar/follow-up records, all done. The zero-controller runtime snapshot
proves why archived task completion cannot be treated as loop or product
completion.

## Hosted delivery truth

At 13:35 UTC the public execute-plans deployment manifest served remote
`dev` head `12b78ef210e535cd4a3d80358f78b44c9396e588`, but reported:

- `VITE_BFF_MODE=live`
- `VITE_BFF_FALLBACK=strict`
- `VITE_BFF_REAL_WRITES=true`
- `VITE_BFF_ALLOW_DEV_STUB_WRITES=true`

BFF `/health` returned `status=ok`, `service=operator-bff`, and
`version=0.2.0`, but no git SHA, image digest, build time, or configuration
identity. The workflow audit also found switch-before-probe and no automatic
rollback. The deployment was current, but it was not an accepted safe
product-level baseline.

## Missing product effects

| Scope | Missing or unproven product segment |
| --- | --- |
| Source | persona requirement → connector/schedule reconciler → real normalized record |
| Distillation | durable SourceRecord consumer → mutable StrategySpec draft |
| Alpha | durable reviewed-spec queue → real ExperimentRun |
| Teaching | authoritative dataset/eval; no stub or manufactured pass |
| Agora evidence | durable interaction → dataset/handoff background owner |
| Imitation | governed dataset discovery → real shadow/OOS candidate |
| Consultation | real participant/provider authorship and reviewed publication |
| Deployment | canonical runtime apply → RuntimeBinding/post-state readback |
| Capital | first-class bounded paper signal producer with binding discovery |
| Telemetry | default telemetry → drift → incident with restart/replay truth |
| Evolution | real target-plane command and post-state, not synthetic SUBMITTED |
| BFF health | durable controller snapshots rather than registry metadata |
| OODA overlay | canonical schedule reconciliation, orphan repair, restart and health |
| Strategy Workshop | six intentionally 501 operations need real canonical commands |
| Trade Journey | canonical lifecycle projector and governed action dispatcher/UI |
| Management AI | hosted scoped sentinel → SA/SD → packet → supervisor receipt |

## Existing work boundary

The audit preserved active work rather than duplicating it. At the last local
snapshot PPL allocation tasks were mixed blocked/in-progress/todo,
`TJ-E2E-014`, branch reconciliation, SSE, and EVOCHAIN were active, and
`PINT-010-R2` was blocked. Their IDs are declared external dependencies in
the machine catalog.

Focused component validation had 117 passing tests with 12 warnings, and
Docker Compose configuration parsed successfully. Those results validate
substrate quality only; none overrides the target-host, terminal-readback,
recovery, security, hosted UX, or exact-identity requirements in the master
plan.

The permitted target for this program is target-dev governed paper. Real order,
broker, and capital side effects remain prohibited.
