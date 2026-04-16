#!/usr/bin/env bash
# gcp_nonprod_foundation.sh — BP5-GCP-002 bootstrap for Pantheon nonprod runtime
# foundation: dev/sandbox split, private Cloud SQL, Pub/Sub backbone, and
# internal-only control-plane ingress prerequisites.
#
# This script is idempotent. Re-running it should converge to the same nonprod
# baseline without deleting existing resources.

set -euo pipefail

PROJECT_ID=""
SHARED_PROJECT_ID=""
REGION="asia-east1"
NETWORK_NAME="pantheon-nonprod"
DEV_SUBNET_CIDR="10.20.0.0/24"
SANDBOX_SUBNET_CIDR="10.20.1.0/24"
DEV_CONNECTOR_CIDR="10.20.16.0/28"
SANDBOX_CONNECTOR_CIDR="10.20.17.0/28"
PRIVATE_SERVICES_CIDR="10.20.240.0/20"
DB_TIER="db-custom-1-3840"
DB_VERSION="POSTGRES_15"
DB_DISK_SIZE_GB="20"
DRY_RUN=false

PUBSUB_TOPICS=(
  "governance-events"
  "runtime-events"
  "telemetry-events"
  "lineage-events"
)

SECRET_SUFFIXES=(
  "postgres-url"
  "openclaw-api-token"
  "vendor-marketdata-token"
  "webhook-signing-secret"
  "broker-api-key"
  "broker-api-secret"
)

usage() {
  cat <<'EOF'
Usage:
  bash scripts/gcp_nonprod_foundation.sh --project-id <gcp-project-id> [options]

Options:
  --project-id <id>            Runtime project ID. Required.
  --shared-project-id <id>     Shared Artifact Registry / build project ID.
                               Defaults to the runtime project ID.
  --region <region>            Runtime region. Default: asia-east1
  --network-name <name>        VPC network name. Default: pantheon-nonprod
  --dev-subnet-cidr <cidr>     Dev subnet CIDR. Default: 10.20.0.0/24
  --sandbox-subnet-cidr <cidr> Sandbox subnet CIDR. Default: 10.20.1.0/24
  --dev-connector-cidr <cidr>  Dev VPC connector range. Default: 10.20.16.0/28
  --sandbox-connector-cidr <cidr>
                               Sandbox VPC connector range. Default: 10.20.17.0/28
  --private-services-cidr <cidr>
                               Private Service Access reserved range.
                               Default: 10.20.240.0/20
  --db-tier <tier>             Cloud SQL tier. Default: db-custom-1-3840
  --db-version <version>       Cloud SQL Postgres version. Default: POSTGRES_15
  --db-disk-size-gb <int>      Cloud SQL SSD disk size in GB. Default: 20
  --dry-run                    Print commands without executing them.
  --help                       Show this message.

Examples:
  bash scripts/gcp_nonprod_foundation.sh --project-id pantheon-nonprod --dry-run
  bash scripts/gcp_nonprod_foundation.sh \
    --project-id pantheon-nonprod \
    --shared-project-id pantheon-shared
EOF
}

info() {
  echo "[gcp-foundation] $*"
}

error() {
  echo "[gcp-foundation] ERROR: $*" >&2
  exit 1
}

run() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[gcp-foundation] DRY-RUN: $*"
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

ensure_secret() {
  local secret_id="$1"
  local env_label="$2"

  if resource_exists gcloud secrets describe "${secret_id}" --project="${PROJECT_ID}"; then
    info "Secret exists: ${secret_id}"
    return
  fi

  run gcloud secrets create "${secret_id}" \
    --project="${PROJECT_ID}" \
    --replication-policy="automatic" \
    --labels="app=pantheon,env=${env_label}"
}

ensure_secret_binding() {
  local secret_id="$1"
  local member="$2"

  run gcloud secrets add-iam-policy-binding "${secret_id}" \
    --project="${PROJECT_ID}" \
    --member="${member}" \
    --role="roles/secretmanager.secretAccessor"
}

ensure_network() {
  if resource_exists gcloud compute networks describe "${NETWORK_NAME}" --project="${PROJECT_ID}"; then
    info "Network exists: ${NETWORK_NAME}"
    return
  fi

  run gcloud compute networks create "${NETWORK_NAME}" \
    --project="${PROJECT_ID}" \
    --subnet-mode="custom" \
    --bgp-routing-mode="regional"
}

ensure_subnet() {
  local subnet_name="$1"
  local cidr="$2"

  if resource_exists gcloud compute networks subnets describe "${subnet_name}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}"; then
    info "Subnet exists: ${subnet_name}"
    return
  fi

  run gcloud compute networks subnets create "${subnet_name}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --network="${NETWORK_NAME}" \
    --range="${cidr}" \
    --enable-private-ip-google-access
}

ensure_private_services_range() {
  local range_name="${NETWORK_NAME}-private-services"

  if resource_exists gcloud compute addresses describe "${range_name}" \
    --project="${PROJECT_ID}" \
    --global; then
    info "Private services range exists: ${range_name}"
    return
  fi

  run gcloud compute addresses create "${range_name}" \
    --project="${PROJECT_ID}" \
    --global \
    --purpose="VPC_PEERING" \
    --addresses="${PRIVATE_SERVICES_CIDR%/*}" \
    --prefix-length="${PRIVATE_SERVICES_CIDR#*/}" \
    --network="${NETWORK_NAME}"
}

ensure_private_services_connection() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    run gcloud services vpc-peerings connect \
      --project="${PROJECT_ID}" \
      --service="servicenetworking.googleapis.com" \
      --network="${NETWORK_NAME}" \
      --ranges="${NETWORK_NAME}-private-services"
    return
  fi

  if gcloud services vpc-peerings list \
    --project="${PROJECT_ID}" \
    --network="${NETWORK_NAME}" \
    --service="servicenetworking.googleapis.com" \
    --format="value(state)" | grep -q "ACTIVE"; then
    info "Private services connection already active for ${NETWORK_NAME}"
    return
  fi

  run gcloud services vpc-peerings connect \
    --project="${PROJECT_ID}" \
    --service="servicenetworking.googleapis.com" \
    --network="${NETWORK_NAME}" \
    --ranges="${NETWORK_NAME}-private-services"
}

ensure_vpc_connector() {
  local connector_name="$1"
  local cidr="$2"

  if resource_exists gcloud compute networks vpc-access connectors describe "${connector_name}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}"; then
    info "VPC connector exists: ${connector_name}"
    return
  fi

  run gcloud compute networks vpc-access connectors create "${connector_name}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --network="${NETWORK_NAME}" \
    --range="${cidr}" \
    --min-instances="2" \
    --max-instances="3"
}

ensure_cloud_sql_instance() {
  local instance_name="$1"

  if resource_exists gcloud sql instances describe "${instance_name}" --project="${PROJECT_ID}"; then
    info "Cloud SQL instance exists: ${instance_name}"
    return
  fi

  run gcloud sql instances create "${instance_name}" \
    --project="${PROJECT_ID}" \
    --database-version="${DB_VERSION}" \
    --region="${REGION}" \
    --network="projects/${PROJECT_ID}/global/networks/${NETWORK_NAME}" \
    --no-assign-ip \
    --availability-type="ZONAL" \
    --storage-type="SSD" \
    --storage-size="${DB_DISK_SIZE_GB}" \
    --tier="${DB_TIER}" \
    --backup-start-time="03:00" \
    --maintenance-window-day="SUN" \
    --maintenance-window-hour="04"
}

ensure_database() {
  local instance_name="$1"
  local database_name="$2"

  if resource_exists gcloud sql databases describe "${database_name}" \
    --instance="${instance_name}" \
    --project="${PROJECT_ID}"; then
    info "Database exists: ${instance_name}/${database_name}"
    return
  fi

  run gcloud sql databases create "${database_name}" \
    --instance="${instance_name}" \
    --project="${PROJECT_ID}"
}

ensure_pubsub_topic() {
  local topic_name="$1"

  if resource_exists gcloud pubsub topics describe "${topic_name}" --project="${PROJECT_ID}"; then
    info "Pub/Sub topic exists: ${topic_name}"
    return
  fi

  run gcloud pubsub topics create "${topic_name}" \
    --project="${PROJECT_ID}" \
    --message-retention-duration="7d"
}

ensure_pubsub_subscription() {
  local subscription_name="$1"
  local topic_name="$2"

  if resource_exists gcloud pubsub subscriptions describe "${subscription_name}" --project="${PROJECT_ID}"; then
    info "Pub/Sub subscription exists: ${subscription_name}"
    return
  fi

  run gcloud pubsub subscriptions create "${subscription_name}" \
    --project="${PROJECT_ID}" \
    --topic="${topic_name}" \
    --expiration-period="never" \
    --message-retention-duration="7d" \
    --ack-deadline="30"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-id)
      PROJECT_ID="${2:-}"
      shift 2
      ;;
    --shared-project-id)
      SHARED_PROJECT_ID="${2:-}"
      shift 2
      ;;
    --region)
      REGION="${2:-}"
      shift 2
      ;;
    --network-name)
      NETWORK_NAME="${2:-}"
      shift 2
      ;;
    --dev-subnet-cidr)
      DEV_SUBNET_CIDR="${2:-}"
      shift 2
      ;;
    --sandbox-subnet-cidr)
      SANDBOX_SUBNET_CIDR="${2:-}"
      shift 2
      ;;
    --dev-connector-cidr)
      DEV_CONNECTOR_CIDR="${2:-}"
      shift 2
      ;;
    --sandbox-connector-cidr)
      SANDBOX_CONNECTOR_CIDR="${2:-}"
      shift 2
      ;;
    --private-services-cidr)
      PRIVATE_SERVICES_CIDR="${2:-}"
      shift 2
      ;;
    --db-tier)
      DB_TIER="${2:-}"
      shift 2
      ;;
    --db-version)
      DB_VERSION="${2:-}"
      shift 2
      ;;
    --db-disk-size-gb)
      DB_DISK_SIZE_GB="${2:-}"
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
SHARED_PROJECT_ID="${SHARED_PROJECT_ID:-${PROJECT_ID}}"

if ! command -v gcloud >/dev/null 2>&1; then
  error "gcloud is required but not installed"
fi

info "Runtime project : ${PROJECT_ID}"
info "Shared project  : ${SHARED_PROJECT_ID}"
info "Region          : ${REGION}"
info "Network         : ${NETWORK_NAME}"
info "DB tier         : ${DB_TIER}"
info "DB version      : ${DB_VERSION}"

info "Step 1/8: enable required Google APIs"
APIS=(
  "compute.googleapis.com"
  "servicenetworking.googleapis.com"
  "sqladmin.googleapis.com"
  "pubsub.googleapis.com"
  "vpcaccess.googleapis.com"
  "secretmanager.googleapis.com"
  "run.googleapis.com"
  "container.googleapis.com"
)
run gcloud services enable --project="${PROJECT_ID}" "${APIS[@]}"

info "Step 2/8: create nonprod VPC and subnets"
ensure_network
ensure_subnet "${NETWORK_NAME}-dev" "${DEV_SUBNET_CIDR}"
ensure_subnet "${NETWORK_NAME}-sandbox" "${SANDBOX_SUBNET_CIDR}"

info "Step 3/8: reserve Private Service Access for Cloud SQL private IP"
ensure_private_services_range
ensure_private_services_connection

info "Step 4/8: create environment runtime identities and secrets"
for env_name in dev sandbox; do
  control_plane_sa="pantheon-${env_name}-control-plane"
  worker_sa="pantheon-${env_name}-worker"
  execution_sa="pantheon-${env_name}-execution"
  control_plane_email="${control_plane_sa}@${PROJECT_ID}.iam.gserviceaccount.com"
  worker_email="${worker_sa}@${PROJECT_ID}.iam.gserviceaccount.com"
  execution_email="${execution_sa}@${PROJECT_ID}.iam.gserviceaccount.com"

  ensure_service_account "${control_plane_sa}" "Pantheon ${env_name} Control Plane"
  ensure_service_account "${worker_sa}" "Pantheon ${env_name} Worker"
  ensure_service_account "${execution_sa}" "Pantheon ${env_name} Execution"

  for suffix in "${SECRET_SUFFIXES[@]}"; do
    secret_id="pantheon-${env_name}-${suffix}"
    ensure_secret "${secret_id}" "${env_name}"

    case "${suffix}" in
      postgres-url|openclaw-api-token|webhook-signing-secret)
        ensure_secret_binding "${secret_id}" "serviceAccount:${control_plane_email}"
        ;;
    esac

    case "${suffix}" in
      postgres-url|vendor-marketdata-token|openclaw-api-token)
        ensure_secret_binding "${secret_id}" "serviceAccount:${worker_email}"
        ;;
    esac

    case "${suffix}" in
      postgres-url|broker-api-key|broker-api-secret)
        ensure_secret_binding "${secret_id}" "serviceAccount:${execution_email}"
        ;;
    esac
  done
done

info "Step 5/8: create per-environment VPC connectors for Cloud Run internal egress"
ensure_vpc_connector "pantheon-dev-connector" "${DEV_CONNECTOR_CIDR}"
ensure_vpc_connector "pantheon-sandbox-connector" "${SANDBOX_CONNECTOR_CIDR}"

info "Step 6/8: create private Cloud SQL instances"
for env_name in dev sandbox; do
  instance_name="pantheon-${env_name}-pg"
  ensure_cloud_sql_instance "${instance_name}"
  ensure_database "${instance_name}" "pantheon"
done

info "Step 7/8: create Pub/Sub backbone topics and smoke subscriptions"
for env_name in dev sandbox; do
  for topic_suffix in "${PUBSUB_TOPICS[@]}"; do
    topic_name="pantheon-${env_name}-${topic_suffix}"
    ensure_pubsub_topic "${topic_name}"
    ensure_pubsub_subscription "${topic_name}-smoke" "${topic_name}"
  done
done

info "Step 8/8: print environment deploy bindings"

dev_control_plane="pantheon-dev-control-plane@${PROJECT_ID}.iam.gserviceaccount.com"
sandbox_control_plane="pantheon-sandbox-control-plane@${PROJECT_ID}.iam.gserviceaccount.com"
dev_execution="pantheon-dev-execution@${PROJECT_ID}.iam.gserviceaccount.com"
sandbox_execution="pantheon-sandbox-execution@${PROJECT_ID}.iam.gserviceaccount.com"

cat <<EOF

Nonprod runtime foundation established
--------------------------------------
VPC network             : ${NETWORK_NAME}
Dev subnet              : ${NETWORK_NAME}-dev (${DEV_SUBNET_CIDR})
Sandbox subnet          : ${NETWORK_NAME}-sandbox (${SANDBOX_SUBNET_CIDR})
Private services range  : ${NETWORK_NAME}-private-services (${PRIVATE_SERVICES_CIDR})
Dev connector           : pantheon-dev-connector (${DEV_CONNECTOR_CIDR})
Sandbox connector       : pantheon-sandbox-connector (${SANDBOX_CONNECTOR_CIDR})
Dev Cloud SQL           : pantheon-dev-pg / database pantheon
Sandbox Cloud SQL       : pantheon-sandbox-pg / database pantheon

Pub/Sub topics
--------------
$(for env_name in dev sandbox; do for topic_suffix in "${PUBSUB_TOPICS[@]}"; do echo " - pantheon-${env_name}-${topic_suffix}"; done; done)

Next: create DB users and secret versions
-----------------------------------------
DEV_DB_HOST=\$(gcloud sql instances describe pantheon-dev-pg --project='${PROJECT_ID}' --format='value(ipAddresses[0].ipAddress)')
SANDBOX_DB_HOST=\$(gcloud sql instances describe pantheon-sandbox-pg --project='${PROJECT_ID}' --format='value(ipAddresses[0].ipAddress)')

gcloud sql users create pantheon_app --project='${PROJECT_ID}' --instance='pantheon-dev-pg' --password='REPLACE_ME'
gcloud sql users create pantheon_app --project='${PROJECT_ID}' --instance='pantheon-sandbox-pg' --password='REPLACE_ME'

printf '%s' "postgresql://pantheon_app:REPLACE_ME@\${DEV_DB_HOST}:5432/pantheon" | \\
  gcloud secrets versions add pantheon-dev-postgres-url --project='${PROJECT_ID}' --data-file=-

printf '%s' "postgresql://pantheon_app:REPLACE_ME@\${SANDBOX_DB_HOST}:5432/pantheon" | \\
  gcloud secrets versions add pantheon-sandbox-postgres-url --project='${PROJECT_ID}' --data-file=-

Deploy examples
---------------
Cloud Run dev (internal-only ingress, private DB path):
gcloud run deploy pantheon-dev-bff \\
  --project='${PROJECT_ID}' \\
  --region='${REGION}' \\
  --service-account='${dev_control_plane}' \\
  --ingress='internal' \\
  --no-allow-unauthenticated \\
  --vpc-connector='pantheon-dev-connector' \\
  --vpc-egress='private-ranges-only' \\
  --set-secrets='DATABASE_URL=pantheon-dev-postgres-url:1,OPENCLAW_API_TOKEN=pantheon-dev-openclaw-api-token:1,WEBHOOK_SIGNING_SECRET=pantheon-dev-webhook-signing-secret:1' \\
  --image='${REGION}-docker.pkg.dev/${SHARED_PROJECT_ID}/pantheon/bff:dev-candidate'

Cloud Run sandbox (internal-only ingress, shared image truth):
gcloud run deploy pantheon-sandbox-bff \\
  --project='${PROJECT_ID}' \\
  --region='${REGION}' \\
  --service-account='${sandbox_control_plane}' \\
  --ingress='internal' \\
  --no-allow-unauthenticated \\
  --vpc-connector='pantheon-sandbox-connector' \\
  --vpc-egress='private-ranges-only' \\
  --set-secrets='DATABASE_URL=pantheon-sandbox-postgres-url:1,OPENCLAW_API_TOKEN=pantheon-sandbox-openclaw-api-token:1,WEBHOOK_SIGNING_SECRET=pantheon-sandbox-webhook-signing-secret:1' \\
  --image='${REGION}-docker.pkg.dev/${SHARED_PROJECT_ID}/pantheon/bff:dev-candidate'

GKE Workload Identity bindings:
kubectl create namespace pantheon-dev
kubectl create serviceaccount runtime-manager -n pantheon-dev
kubectl annotate serviceaccount runtime-manager -n pantheon-dev \\
  iam.gke.io/gcp-service-account='${dev_execution}'

kubectl create namespace pantheon-sandbox
kubectl create serviceaccount runtime-manager -n pantheon-sandbox
kubectl annotate serviceaccount runtime-manager -n pantheon-sandbox \\
  iam.gke.io/gcp-service-account='${sandbox_execution}'

Important boundary
------------------
- Shared project remains the image truth path (GitHub OIDC -> Cloud Build -> Artifact Registry).
- Runtime project owns environment-scoped DB, Pub/Sub, private networking, runtime identities, and runtime secrets.
- Cloud Run ingress stays internal-only; operator access should come through approved proxy/VPN paths rather than public exposure.
EOF
