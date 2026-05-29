#!/usr/bin/env bash
# Cutover from lupinchen GCP project -> benjamin GCP project.
#
# This script is IDEMPOTENT-ISH but DESTRUCTIVE in the sense that after it runs,
# GitHub Actions will deploy to the benjamin environment instead of lupinchen.
#
# Run only when you (user) decide it's time to cut.
# After cutover, lupinchen VMs can be stopped / deleted manually.
#
# Pre-reqs:
#   - gh CLI authed as ajoe734 with repo write
#   - benjamin VMs already created and verified
#   - Run from repo root
#
# What this changes:
#   1) GitHub repo variables (project ID/number/WIF/SA/bucket/BFF URLs)
#   2) Hardcoded dev BFF URL in two scripts (probe + sidecar)
#
# What this does NOT change:
#   - Archived docs/evidence (immutable history)
#   - Old workflow copies under docs/05/
#   - Anything on lupinchen project (left untouched for rollback safety)

set -euo pipefail

OLD_PROJECT_ID="pantheon-lupin-20260502"
OLD_PROJECT_NUMBER="292583053341"
OLD_DEV_IP="34.81.75.241"
OLD_STAGING_IP="35.221.223.193"

NEW_PROJECT_ID="pantheon-benjamin-20260528"
NEW_PROJECT_NUMBER="773162254001"
NEW_DEV_IP="35.201.239.38"
NEW_STAGING_IP="104.155.223.192"

NEW_SA="pantheon-cloud-build@${NEW_PROJECT_ID}.iam.gserviceaccount.com"
NEW_WIF="projects/${NEW_PROJECT_NUMBER}/locations/global/workloadIdentityPools/pantheon-github-pool/providers/pantheon-github-provider"
NEW_BUCKET_SOURCE="gs://${NEW_PROJECT_ID}-pantheon-builds/source"

echo "=== Cutover: lupinchen -> benjamin ==="
echo "  Project:  ${OLD_PROJECT_ID} -> ${NEW_PROJECT_ID}"
echo "  Dev IP:   ${OLD_DEV_IP} -> ${NEW_DEV_IP}"
echo "  Stg IP:   ${OLD_STAGING_IP} -> ${NEW_STAGING_IP}"
echo

read -p "Confirm cutover? [y/N] " confirm
[[ "${confirm}" == "y" || "${confirm}" == "Y" ]] || { echo "aborted"; exit 1; }

echo
echo "=== Updating GitHub repo variables ==="
gh variable set GCP_PROJECT_ID --body "${NEW_PROJECT_ID}"
gh variable set GCP_PROJECT_NUMBER --body "${NEW_PROJECT_NUMBER}"
gh variable set GCP_DEPLOY_PROJECT_ID --body "${NEW_PROJECT_ID}"
gh variable set GCP_SERVICE_ACCOUNT --body "${NEW_SA}"
gh variable set GCP_DEPLOY_SERVICE_ACCOUNT --body "${NEW_SA}"
gh variable set GCP_WIF_PROVIDER --body "${NEW_WIF}"
gh variable set GCP_BUILD_STAGING_BUCKET --body "${NEW_BUCKET_SOURCE}"
gh variable set DEV_BFF_URL --body "https://pantheon-lupin-dev-bff.${NEW_DEV_IP}.sslip.io"
gh variable set STAGING_BFF_URL --body "https://pantheon-lupin-staging-bff.${NEW_STAGING_IP}.sslip.io"

echo
echo "=== Updating hardcoded refs in active scripts ==="
sed -i "s|${OLD_DEV_IP}|${NEW_DEV_IP}|g" scripts/probe_bff_authenticated_live.py
sed -i "s|${OLD_DEV_IP}|${NEW_DEV_IP}|g" support/sidecars/FE-INT-GATE-bootstrap/assign-align-hosted.sh

echo
echo "=== Done. Verify with: ==="
echo "  gh variable list | grep -E 'GCP_|BFF_URL'"
echo "  curl -sS https://pantheon-lupin-dev-bff.${NEW_DEV_IP}.sslip.io/health"
echo
echo "Next steps (manual):"
echo "  1) git commit + PR for the sed changes"
echo "  2) Run a deploy (gh workflow run nonprod-deploy.yml ...) to publish to benjamin"
echo "  3) Once verified working, stop/delete lupinchen VMs:"
echo "       gcloud compute instances delete pantheon-lupin-dev pantheon-lupin-staging-control pantheon-lupin-staging-exec --zone=asia-east1-b --project=${OLD_PROJECT_ID}"
