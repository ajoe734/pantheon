## EP5 Dual-VM Local Evidence

This packet captures a truthful local dual-VM proof raise performed on
`2026-04-24`.

What this packet proves:

- a dedicated VM-2 execution host exists:
  - instance: `pantheon-exec-vm2-20260424`
  - internal IP: `10.140.0.5`
- VM-2 can run the execution-plane stack from `docker-compose.exec.yml`
- VM-2 runtime-manager can create a canary `RuntimeBinding`
- VM-1 telemetry on `10.140.0.4:38083` can accept execution-plane telemetry
  validated against the authoritative VM-2 runtime-manager
- kill-switch and rollback drill executed through the canonical runtime-manager
  HTTP surface
- the VM-2 paper runtime can be retargeted to the post-rollback replacement
  binding and resume healthy telemetry emission

Key evidence files:

- `operator-checklist.json`
- `canary-deployment-plan.json`
- `canary-binding.response.json`
- `kill-switch.response.json`
- `rollback.response.json`
- `rollback-drill-summary.json`
- `telemetry-rollback.response.json`
- `vm2-paper-runtime-health.json`
- `vm2-runtime-bindings.json`
- `telemetry-stats.json`

Important boundary:

- this is **not** a truthful `EP5-002` closeout packet yet
- broker / exchange secret values are still absent
- Secret Manager is enabled, but no usable secret values were available to this
  run
- the execution-plane sidecars are running in `adapter_mode=real` with
  `PANTHEON_SECRETS_OPTIONAL=false`, but they still do not hold real
  credential material

So the current state is:

- `EP5-001` dual-VM readiness and rollback rehearsal: materially raised
- `EP5-002` real canary/live proof: still blocked on real execution secrets and
  operator-owned credential injection
