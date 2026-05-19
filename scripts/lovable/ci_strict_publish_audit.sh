#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEFAULT_OUTPUT_DIR="$ROOT_DIR/support/evidence/LOVABLE-STRICT-PUBLISH"

usage() {
  cat <<'USAGE'
Usage: scripts/lovable/ci_strict_publish_audit.sh [deployment-url]

Runs the Lovable strict-publish audit with the required build-time BFF flags:
  VITE_BFF_MODE=live
  VITE_BFF_FALLBACK=strict
  VITE_BFF_REAL_WRITES=false

The deployment URL may be passed as the first argument or via
LOVABLE_DEPLOYMENT_URL. Override LOVABLE_AUDIT_OUTPUT_DIR to write generated
audit artifacts outside support/evidence when running in CI.
USAGE
}

die() {
  echo "ci_strict_publish_audit: $*" >&2
  exit 64
}

require_or_set_env() {
  local key="$1"
  local required="$2"
  local current="${!key-}"

  if [[ -n "$current" && "$current" != "$required" ]]; then
    die "$key must be '$required' for strict-publish audit (got '$current')"
  fi

  export "$key=$required"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "$#" -gt 1 ]]; then
  usage >&2
  die "expected at most one deployment URL argument"
fi

deployment_url="${1:-${LOVABLE_DEPLOYMENT_URL:-}}"
if [[ -z "$deployment_url" ]]; then
  usage >&2
  die "missing deployment URL; pass one argument or set LOVABLE_DEPLOYMENT_URL"
fi

require_or_set_env "VITE_BFF_MODE" "live"
require_or_set_env "VITE_BFF_FALLBACK" "strict"
require_or_set_env "VITE_BFF_REAL_WRITES" "false"

output_dir="${LOVABLE_AUDIT_OUTPUT_DIR:-$DEFAULT_OUTPUT_DIR}"
required_env_path="${LOVABLE_REQUIRED_ENV_PATH:-$DEFAULT_OUTPUT_DIR/required_build_env.json}"
output_json="${LOVABLE_AUDIT_OUTPUT_JSON:-$output_dir/strict-publish-audit.json}"
report_md="${LOVABLE_AUDIT_REPORT_MD:-$output_dir/strict-publish-audit.md}"

mkdir -p "$output_dir"

python3 "$ROOT_DIR/scripts/audit_lovable_strict_publish.py" "$deployment_url" \
  --required-env "$required_env_path" \
  --output "$output_json" \
  --report "$report_md"
