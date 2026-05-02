#!/usr/bin/env bash
# gcp_nonprod_baseline.sh — BP5-GCP-001 bootstrap for Pantheon nonprod identity
# and Secret Manager namespace.
#
# What this script does:
# - enables the minimum APIs needed for GitHub OIDC -> GCP WIF
# - provisions the GitHub Workload Identity Pool + Provider
# - provisions the Cloud Build submitter service account
# - provisions baseline nonprod runtime service accounts
# - creates a canonical dev Secret Manager namespace
# - grants secret accessor permissions to the intended runtime identities
# - prints the GitHub repository variables and deploy-time binding snippets
#
# This script is idempotent. Re-running it should converge to the same baseline.

set -euo pipefail

PROJECT_ID=""
PROJECT_NUMBER=""
GITHUB_REPO="ajoe734/pantheon"
REGION="asia-east1"
ENV_NAME="dev"
POOL_ID="pantheon-github-pool"
PROVIDER_ID="pantheon-github-provider"
DRY_RUN=false

usage() {
  cat <<'EOF'
Usage:
  bash scripts/gcp_nonprod_baseline.sh --project-id <gcp-project-id> [options]

Options:
  --project-id <id>         GCP project ID. Required.
  --project-number <num>    GCP project number. Auto-discovered when omitted.
  --github-repo <owner/repo>
                            GitHub repository allowed to exchange OIDC tokens.
                            Default: ajoe734/pantheon
  --region <region>         Default Artifact Registry / Cloud Run region.
                            Default: asia-east1
  --env <name>              Nonprod environment prefix for runtime identities
                            and secrets. Default: dev
  --pool-id <id>            Workload Identity Pool ID.
                            Default: pantheon-github-pool
  --provider-id <id>        Workload Identity Provider ID.
                            Default: pantheon-github-provider
  --dry-run                 Print commands without executing them.
  --help                    Show this message.

Examples:
  bash scripts/gcp_nonprod_baseline.sh --project-id pantheon-shared
  bash scripts/gcp_nonprod_baseline.sh --project-id pantheon-shared --dry-run
EOF
}

info() {
  echo "[gcp-baseline] $*"
}

error() {
  echo "[gcp-baseline] ERROR: $*" >&2
  exit 1
}

run() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[gcp-baseline] DRY-RUN: $*"
  else
    "$@"
  fi
}

capture() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    return 0
  fi
  "$@"
}

resource_exists() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    return 1
  fi
  "$@" >/dev/null 2>&1
}

ensure_service_account() {
  local account_id="$1"
  local display_name="$2"

  if resource_exists gcloud iam service-accounts describe \
    "${account_id}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --project="${PROJECT_ID}"; then
    info "Service account exists: ${account_id}"
    return
  fi

  run gcloud iam service-accounts create "${account_id}" \
    --project="${PROJECT_ID}" \
    --display-name="${display_name}"
}

ensure_project_role() {
  local member="$1"
  local role="$2"

  run gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="${member}" \
    --role="${role}"
}

ensure_secret() {
  local secret_id="$1"

  if resource_exists gcloud secrets describe "${secret_id}" --project="${PROJECT_ID}"; then
    info "Secret exists: ${secret_id}"
    return
  fi

  run gcloud secrets create "${secret_id}" \
    --project="${PROJECT_ID}" \
    --replication-policy="automatic" \
    --labels="app=pantheon,env=${ENV_NAME}"
}

ensure_secret_binding() {
  local secret_id="$1"
  local member="$2"

  run gcloud secrets add-iam-policy-binding "${secret_id}" \
    --project="${PROJECT_ID}" \
    --member="${member}" \
    --role="roles/secretmanager.secretAccessor"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-id)
      PROJECT_ID="${2:-}"
      shift 2
      ;;
    --project-number)
      PROJECT_NUMBER="${2:-}"
      shift 2
      ;;
    --github-repo)
      GITHUB_REPO="${2:-}"
      shift 2
      ;;
    --region)
      REGION="${2:-}"
      shift 2
      ;;
    --env)
      ENV_NAME="${2:-}"
      shift 2
      ;;
    --pool-id)
      POOL_ID="${2:-}"
      shift 2
      ;;
    --provider-id)
      PROVIDER_ID="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      error "Unknown option: $1"
      ;;
  esac
done

[[ -n "${PROJECT_ID}" ]] || error "--project-id is required"

if ! command -v gcloud >/dev/null 2>&1; then
  error "gcloud is required but not installed"
fi

if [[ -z "${PROJECT_NUMBER}" ]]; then
  if PROJECT_NUMBER="$(capture gcloud projects describe "${PROJECT_ID}" --format="value(projectNumber)" 2>/dev/null)"; then
    :
  elif [[ "${DRY_RUN}" == "true" ]]; then
    PROJECT_NUMBER="<project-number-required-for-exact-dry-run>"
    info "Project number auto-discovery skipped; pass --project-number for exact dry-run output"
  else
    error "Unable to auto-discover project number; pass --project-number explicitly"
  fi
fi

CLOUD_BUILD_SA_ID="pantheon-cloud-build"
CLOUD_BUILD_SA="${CLOUD_BUILD_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
CONTROL_PLANE_SA_ID="pantheon-${ENV_NAME}-control-plane"
WORKER_SA_ID="pantheon-${ENV_NAME}-worker"
EXECUTION_SA_ID="pantheon-${ENV_NAME}-execution"
CONTROL_PLANE_SA="${CONTROL_PLANE_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
WORKER_SA="${WORKER_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
EXECUTION_SA="${EXECUTION_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"

WIF_PRINCIPAL="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository/${GITHUB_REPO}"
WIF_PROVIDER_RESOURCE="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"

SECRETS=(
  "pantheon-${ENV_NAME}-postgres-url"
  "pantheon-${ENV_NAME}-openclaw-api-token"
  "pantheon-${ENV_NAME}-vendor-marketdata-token"
  "pantheon-${ENV_NAME}-webhook-signing-secret"
  "pantheon-${ENV_NAME}-broker-api-key"
  "pantheon-${ENV_NAME}-broker-api-secret"
)

CONTROL_PLANE_SECRETS=(
  "pantheon-${ENV_NAME}-postgres-url"
  "pantheon-${ENV_NAME}-openclaw-api-token"
  "pantheon-${ENV_NAME}-webhook-signing-secret"
)

WORKER_SECRETS=(
  "pantheon-${ENV_NAME}-postgres-url"
  "pantheon-${ENV_NAME}-vendor-marketdata-token"
  "pantheon-${ENV_NAME}-openclaw-api-token"
)

EXECUTION_SECRETS=(
  "pantheon-${ENV_NAME}-postgres-url"
  "pantheon-${ENV_NAME}-broker-api-key"
  "pantheon-${ENV_NAME}-broker-api-secret"
)

info "Project      : ${PROJECT_ID}"
info "Project no.  : ${PROJECT_NUMBER:-<auto-discovery skipped>}"
info "GitHub repo  : ${GITHUB_REPO}"
info "Region       : ${REGION}"
info "Environment  : ${ENV_NAME}"

info "Step 1/6: enable required Google APIs"
APIS=(
  "iam.googleapis.com"
  "iamcredentials.googleapis.com"
  "sts.googleapis.com"
  "cloudbuild.googleapis.com"
  "artifactregistry.googleapis.com"
  "secretmanager.googleapis.com"
  "run.googleapis.com"
  "container.googleapis.com"
)
run gcloud services enable --project="${PROJECT_ID}" "${APIS[@]}"

info "Step 2/6: provision Workload Identity Federation"
if resource_exists gcloud iam workload-identity-pools describe "${POOL_ID}" \
  --project="${PROJECT_ID}" \
  --location="global"; then
  info "Workload Identity Pool exists: ${POOL_ID}"
else
  run gcloud iam workload-identity-pools create "${POOL_ID}" \
    --project="${PROJECT_ID}" \
    --location="global" \
    --display-name="Pantheon GitHub Actions"
fi

if resource_exists gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" \
  --project="${PROJECT_ID}" \
  --location="global" \
  --workload-identity-pool="${POOL_ID}"; then
  info "Workload Identity Provider exists: ${PROVIDER_ID}"
else
  run gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
    --project="${PROJECT_ID}" \
    --location="global" \
    --workload-identity-pool="${POOL_ID}" \
    --display-name="Pantheon GitHub Provider" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition=attribute.repository==\"${GITHUB_REPO}\" \
    --issuer-uri="https://token.actions.githubusercontent.com"
fi

info "Step 3/6: provision service accounts"
ensure_service_account "${CLOUD_BUILD_SA_ID}" "Pantheon Cloud Build SA"
ensure_service_account "${CONTROL_PLANE_SA_ID}" "Pantheon ${ENV_NAME} Control Plane"
ensure_service_account "${WORKER_SA_ID}" "Pantheon ${ENV_NAME} Worker"
ensure_service_account "${EXECUTION_SA_ID}" "Pantheon ${ENV_NAME} Execution"

info "Step 4/6: grant GitHub -> Cloud Build submit permissions"
ensure_project_role "serviceAccount:${CLOUD_BUILD_SA}" "roles/cloudbuild.builds.editor"
ensure_project_role "serviceAccount:${CLOUD_BUILD_SA}" "roles/artifactregistry.writer"
ensure_project_role "serviceAccount:${CLOUD_BUILD_SA}" "roles/storage.objectAdmin"
ensure_project_role "serviceAccount:${CLOUD_BUILD_SA}" "roles/serviceusage.serviceUsageConsumer"

run gcloud iam service-accounts add-iam-policy-binding "${CLOUD_BUILD_SA}" \
  --project="${PROJECT_ID}" \
  --member="${WIF_PRINCIPAL}" \
  --role="roles/iam.workloadIdentityUser"

info "Step 5/6: create baseline Secret Manager namespace"
for secret_id in "${SECRETS[@]}"; do
  ensure_secret "${secret_id}"
done

info "Step 6/6: grant runtime identities secret access"
for secret_id in "${CONTROL_PLANE_SECRETS[@]}"; do
  ensure_secret_binding "${secret_id}" "serviceAccount:${CONTROL_PLANE_SA}"
done

for secret_id in "${WORKER_SECRETS[@]}"; do
  ensure_secret_binding "${secret_id}" "serviceAccount:${WORKER_SA}"
done

for secret_id in "${EXECUTION_SECRETS[@]}"; do
  ensure_secret_binding "${secret_id}" "serviceAccount:${EXECUTION_SA}"
done

cat <<EOF

GitHub repository variables
---------------------------
GCP_PROJECT_ID=${PROJECT_ID}
GCP_PROJECT_NUMBER=${PROJECT_NUMBER:-<discover with gcloud projects describe>}
GCP_WIF_PROVIDER=${WIF_PROVIDER_RESOURCE}
GCP_SERVICE_ACCOUNT=${CLOUD_BUILD_SA}

Baseline runtime service accounts
---------------------------------
Cloud Run control plane : ${CONTROL_PLANE_SA}
GKE Autopilot workers   : ${WORKER_SA}
GKE execution workloads : ${EXECUTION_SA}

Next: add secret versions
-------------------------
printf '%s' 'REPLACE_ME' | gcloud secrets versions add pantheon-${ENV_NAME}-postgres-url \\
  --project='${PROJECT_ID}' --data-file=-

printf '%s' 'REPLACE_ME' | gcloud secrets versions add pantheon-${ENV_NAME}-broker-api-key \\
  --project='${PROJECT_ID}' --data-file=-

Deploy-time binding examples
----------------------------
Cloud Run:
gcloud run deploy pantheon-${ENV_NAME}-bff \\
  --project='${PROJECT_ID}' \\
  --region='${REGION}' \\
  --service-account='${CONTROL_PLANE_SA}' \\
  --image='${REGION}-docker.pkg.dev/${PROJECT_ID}/pantheon/bff:dev-candidate' \\
  --set-secrets='DATABASE_URL=pantheon-${ENV_NAME}-postgres-url:1,OPENCLAW_API_TOKEN=pantheon-${ENV_NAME}-openclaw-api-token:1,WEBHOOK_SIGNING_SECRET=pantheon-${ENV_NAME}-webhook-signing-secret:1'

GKE Workload Identity (example):
kubectl create namespace pantheon-${ENV_NAME}
kubectl create serviceaccount runtime-manager -n pantheon-${ENV_NAME}
kubectl annotate serviceaccount runtime-manager -n pantheon-${ENV_NAME} \\
  iam.gke.io/gcp-service-account='${EXECUTION_SA}'

Important boundary
------------------
- GitHub never stores runtime secrets.
- GitHub OIDC only submits builds.
- Cloud Run / GKE identities read secrets at deploy or runtime from Secret Manager.
- This script creates secret containers, not production values.
EOF
