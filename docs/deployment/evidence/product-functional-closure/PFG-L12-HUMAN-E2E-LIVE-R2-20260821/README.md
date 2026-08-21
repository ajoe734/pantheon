# PFG-L12-HUMAN-E2E-LIVE-R2-20260821: Deployed Human Learning Recovery & Proof

This task recovers the post-merge dev deployment and hosted Human E2E proof for
Loops 5 through 7 following the false terminal closeout of PFG-L12-HUMAN-E2E-20260820.

## Objective

1. Trigger `Pantheon Nonprod Deploy` on workflow ref `dev` with:
   - `environment=dev`
   - `component=root`
   - `ref=bb83df12e3cec11de0f441850f08a179ddd7394a`
   - `frontend_sha=8b5a7bbe868f9e3a56a4ed7baf818b642d57ba74`
   - `dev_auth_profile=strict`
2. Wait for deployment success and verify hosted `/bff/version` exact SHA matches `bb83df12e3cec11de0f441850f08a179ddd7394a`.
3. Run `PANTHEON_L12_HUMAN_LEARNING_E2E=1` against the hosted dev BFF using governed dev credentials without printing or persisting secrets.
4. Capture durable IDs plus restart/replay evidence in this evidence directory.
5. Verify no Source scheduler and `PANTHEON_EXTERNAL_EGRESS=deny`.

## Deployment Execution & Blocker Diagnosis (2026-08-21)

`Pantheon Nonprod Deploy` was triggered via `gh workflow run nonprod-deploy.yml` on `dev` (GitHub Actions Run ID: `32485613630`).

### Deployment Failure Details
- **Run ID**: [32485613630](https://github.com/ajoe734/pantheon/actions/runs/32485613630)
- **Job**: `Deploy dev under shared environment lease`
- **Step**: `Deploy dev VM stack under lease`
- **Error Log**:
  ```text
  Updating instance ssh metadata... failed.
  ERROR: (gcloud.compute.ssh) Could not add SSH key to instance metadata, refer https://cloud.google.com/compute/docs/access#granting_users_ssh_access_to_vm_instances for granting users SSH access to VM instances:
   - This API method requires billing to be enabled. Please enable billing on project #pantheon-lupin-dev-20260719 by visiting https://console.developers.google.com/billing/enable?project=pantheon-lupin-dev-20260719 then retry. If you enabled billing for this project recently, wait a few minutes for the action to propagate to our systems and retry.
  Process completed with exit code 75.
  ```

### Root Cause & Required Corrective Action
- Google Cloud Compute Engine rejected SSH metadata modification on VM instance `pantheon-lupin-dev` because billing is disabled or requires re-activation on GCP project `pantheon-lupin-dev-20260719`.
- **Actor Required**: `Human/Ops`
- **Action Required**: Enable/verify billing on GCP project `pantheon-lupin-dev-20260719`.
- Once billing is restored, re-dispatch `Pantheon Nonprod Deploy` with the exact backend SHA `bb83df12e3cec11de0f441850f08a179ddd7394a` and execute-plans frontend SHA `8b5a7bbe868f9e3a56a4ed7baf818b642d57ba74`, then proceed with the hosted `PANTHEON_L12_HUMAN_LEARNING_E2E=1` execution proof.
