# Review: SVC-BLUEPRINT-BFF-READ-CUTOFF-WAVE4

Reviewer: Claude
Date: 2026-05-03
Decision: approved

## Scope Reviewed

Task: Cut BFF staging/prod reads over to service-backed clients
Owner: Codex
Reviewed worktree state: branch `backend-dev-publish-20260429`, task-owned diff
relative to `HEAD` (commit `3fc437c`).

Task-owned changes (per owner handoff note):
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/test_bff_read_cutoff_wave4_contract.py`

Additional context confirmed in tree (already committed or owned by adjacent
tasks):
- `services/control-plane/bff/read_store.py`
  (`ReadSurfaceStore` constructor, `dataset_source`, `_local_fallback`)
- `docker-compose.yml`, `docker-compose.control.yml`
  (`PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK=false`,
  `volumes: !override` removing cross-service read mounts)
- `env/prod-control.env.example`
  (`PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK=false`)

## Findings

No blocking findings.

The reviewed state satisfies the wave-4 BFF read cutoff requirement:

1. `services/control-plane/bff/main.py` initializes `ReadSurfaceStore` with
   `allow_local_snapshot_fallback=_bool_from_env(
   "PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK", default=False)`. Staging and
   prod compose render that env to `"false"`, so local snapshot reads are
   disabled by default rather than implicitly opted in.
2. `read_store.ReadSurfaceStore.dataset_source(...)` resolves through canonical
   HTTP / service-backed adapters before any local snapshot, and only returns
   `local_snapshot` when `_allow_local_snapshot_fallback=True`. When neither a
   service nor an opted-in snapshot is available it returns `missing` — used
   downstream to mark surfaces as `unavailable`.
3. The new `_read_surface_meta` and `_raise_if_read_surface_unavailable`
   helpers are applied uniformly across the catalog list/detail surfaces
   touched by this wave (personas/sessions, capital pools, persona bindings,
   deployment plans, approval decisions, runtime bindings, capability
   snapshots). List responses now expose the per-surface
   `status`/`source`/`note`/`staleness` metadata plus a top-level
   `meta.degradation.reason` when the read source is degraded or
   unavailable. Detail routes raise `503 DOWNSTREAM_UNAVAILABLE` instead of
   returning silent 404s when the upstream surface is `unavailable`.
4. `services/control-plane/bff/test_bff_read_cutoff_wave4_contract.py` covers
   both directions of the cutoff:
   - `test_prod_catalog_read_does_not_mask_cutoff_with_local_snapshot`:
     `allow_local_snapshot_fallback=False` + empty service URLs → list returns
     `data: []` with `surfaces.deployment_plan_list = {status: unavailable,
     source: missing}` and `meta.degradation.reason`; detail returns
     `503` with `error.code = "DOWNSTREAM_UNAVAILABLE"`.
   - `test_dev_catalog_snapshot_fallback_is_explicitly_degraded`:
     `allow_local_snapshot_fallback=True` → list serves snapshot data but
     marks the surface `degraded`/`local_snapshot` with the
     `"local BFF snapshot fallback"` note.
5. `services/control-plane/bff/test_staging_read_store_cutoff_contract.py`
   continues to pin the staging contract: operator-bff disables snapshot
   fallback, has no cross-service `governance-data`/`runtime-data`/
   `incident-data` volume mounts (only `bff-data:/data/bff` after merge), and
   the prod env example documents the flag.
6. The `docker-compose.control.yml` health-probe edits visible in the
   worktree belong to `SVC-BLUEPRINT-OBSERVABILITY-PROBE-FINALIZE`, not to
   this task; the owner's handoff note explicitly scopes wave-4 changes to
   `main.py` plus the new test file.

## Verification Run

Executed in `/tmp/pantheon-bff-read-cutoff-venv` (matches owner's verified
toolchain):

```bash
/tmp/pantheon-bff-read-cutoff-venv/bin/python -m pytest \
  services/control-plane/bff/test_bff_read_cutoff_wave4_contract.py \
  services/control-plane/bff/test_staging_read_store_cutoff_contract.py \
  services/control-plane/bff/test_read_store_bootstrap_snapshot.py \
  services/control-plane/bff/test_read_store_service_clients.py -v
# 12 passed in 4.49s
```

Regression coverage on adjacent BFF surfaces:

```bash
/tmp/pantheon-bff-read-cutoff-venv/bin/python -m pytest \
  services/control-plane/bff/test_w4_remaining_catalog.py \
  services/control-plane/bff/test_persona_management.py \
  services/control-plane/bff/test_pkt004_deployment_approval_drilldowns_contract.py \
  services/control-plane/bff/test_pkt004_capital_binding_drilldowns_contract.py \
  services/control-plane/bff/test_pkt010_runtime_state_board_contract.py -q
# 9 passed in 4.52s
```

Compose validation:

```bash
docker compose -f docker-compose.control.yml config --quiet
# OK
```

## Acceptance Assessment

Approved. Acceptance items are met:

- staging/prod BFF 不會靜默讀本地 snapshot — fallback flag defaults to
  `False` in code, is wired to `"false"` in the staging operator-bff compose
  block, and `env/prod-control.env.example` documents the same.
- downstream unavailable 回傳明確 degraded 狀態 — list responses surface
  `status`/`source`/`note` plus `meta.degradation.reason`; detail responses
  raise `503` with `DOWNSTREAM_UNAVAILABLE` via
  `_raise_if_read_surface_unavailable` instead of 404-as-empty.
- BFF tests 覆蓋 dev fallback 與 prod cutoff — both directions pinned in
  `test_bff_read_cutoff_wave4_contract.py`, supplemented by the staging
  contract and snapshot-bootstrap suites.

Owner should perform task closeout finalization (task-scoped commit on the
two task-owned files, `scripts/ai-status.sh done`, and the configured push)
per `.orchestrator/skills/task-closeout-finalization.md`.
