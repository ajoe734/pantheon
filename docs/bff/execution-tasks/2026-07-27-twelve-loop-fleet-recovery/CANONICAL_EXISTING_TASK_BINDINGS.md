# Canonical existing task bindings

Status: authoritative dispatch correction

The supervisor artifact-conflict guard is authoritative. Do not create duplicate
active tasks for work already owned by existing canonical L12 tasks. The
recovery packet therefore adds only the three missing control-plane tasks and
binds all PR/loop closeout work to existing tasks.

| Existing task | Covers | Required action |
|---|---|---|
| `OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001` | PR #4273 / telemetry discovery import | Close PR #4273 through the existing owner/reviewer path and exact GitHub review gate. |
| `L12-BFF-001` | PR #4274 / BFF health monitor | Close PR #4274 through the existing canonical task. |
| `L12-CURRENT-GAP-FLEET-AUDIT-20260727` | PR #4269 / current gap fleet audit | Finalize/merge #4269 through the existing review-approved task. |
| `L12-EVO-001` | PR #4267 / evolution | Compose, revalidate, and review #4267 through `L12-EVO-001`. |
| `L12-DIST-001` | PR #4193 / source-distillation | Compose, revalidate, and review #4193 through `L12-DIST-001`. |
| `L12-MANIFEST-001` | worker manifest activation | Attach missing-worker and manifest evidence to the existing manifest task. |
| `L12-VERIFY-KNOW-001` | Source/Distillation/Alpha verification | Run real knowledge-family verification under the existing task. |
| `L12-VERIFY-LEARN-001` | Teaching/Agora/Imitation/Consultation verification | Run real learning-family verification under the existing task. |
| `L12-VERIFY-RUNTIME-001` | Deployment/Capital verification | Run deployment and governed-paper capital proof under the existing task. |
| `L12-VERIFY-OBS-001` | Telemetry/Reconciliation/Evolution/BFF verification | Run observation-family proof under the existing task. |
| `L12-HOSTED-001` | hosted FE/BFF acceptance | Prove hosted manifest, commit, and runtime truth under the existing task. |
| `L12-CLOSE-001` | final twelve-loop signoff | Close final program acceptance under the existing task with Claude reviewer when healthy. |

## Dispatch implication

The immediate newly assignable tasks are:

- `L12-FLEET-STATUS-SYNC-001`
- `L12-FLEET-WORKER-OUTCOME-001`
- `L12-GITHUB-REVIEW-BRIDGE-001`

All PR and loop-product work should continue through the existing task IDs above.
