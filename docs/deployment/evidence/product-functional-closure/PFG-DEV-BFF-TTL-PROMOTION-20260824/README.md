# PFG-DEV-BFF-TTL-PROMOTION-20260824: Promote dev BFF TTL contract and verify hosted readback

This task promotes the merged dev BFF login-token TTL (1800s default) contract (from `PFG-DEV-LOGIN-TTL-CONTRACT-20260824`, commit `40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0`) to the Pantheon dev environment using the governed `Pantheon Nonprod Deploy` workflow with `component=bff`, `dev_auth_profile=strict`, and paired execute-plans dev SHA `cc4007f7f78a31c73548ce85457af17a45a4c4b9`.

## Promotion Summary

- **Target Environment:** `dev`
- **Component:** `bff`
- **Backend Ref:** `40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0` (Pantheon `dev` HEAD)
- **Frontend Ref:** `cc4007f7f78a31c73548ce85457af17a45a4c4b9` (execute-plans `dev` HEAD)
- **Auth Profile:** `strict`
- **Workflow:** `Pantheon Nonprod Deploy` (`.github/workflows/nonprod-deploy.yml`)
- **Workflow Run:** [32679566250](https://github.com/ajoe734/pantheon/actions/runs/32679566250)
- **Paired FE Gate Run:** [32679780054](https://github.com/ajoe734/execute-plans/actions/runs/32679780054)
- **Paired FE Deploy Run:** [32680429819](https://github.com/ajoe734/execute-plans/actions/runs/32680429819)
- **Proof Toggles:** all false (`ppl_alloc_009_dev_proof_enabled=false`, `run_evolution_dispatch_probe=false`, `run_loop_prod_tel_002_probe=false`)

## Scope & Boundaries

- **Owned Layer:** Deployment promotion of dev BFF container stack to dev VM, followed by hosted readback verification of exact commit SHA, TTL posture, and safe operating defaults.
- **Not Changing:**
  - Live capital action remains disabled / fail-closed.
  - Source Ingestion remains reconcile-only (no outbound pull scheduler).
  - Frontend code and hosting configuration remain unchanged (read-only profile).
  - Production / staging-live strict IdP posture remains intact.

## Hosted Readback Verification

- BFF health endpoint (`/health`) returns 200 OK (`{"status":"ok","service":"operator-bff","version":"0.2.0"}`).
- BFF version endpoint (`/bff/version`) returns `source_commit_sha: 40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0`, `environment: dev`, `auth_mode: strict`, `dev_login_enabled: true`.
- Runtime configuration confirms dev login TTL 1800s default.
- Frontend deployment (`/deployment.json`) remains accepted read-only with paired backend SHA `40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0` and frontend SHA `cc4007f7f78a31c73548ce85457af17a45a4c4b9`.
- Source Ingestion remains in `reconcile_only` mode with `MAX_TICKS=0`.
- Zero live capital actions and zero external source egress occurred during deployment.
