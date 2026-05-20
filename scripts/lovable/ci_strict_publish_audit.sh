#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEFAULT_OUTPUT_DIR="$ROOT_DIR/support/evidence/lsp-final-audit"

usage() {
  cat <<'USAGE'
Usage: scripts/lovable/ci_strict_publish_audit.sh [deployment-url]

Runs the Lovable LSP strict-publish audit with the required build-time BFF flags:
  VITE_BFF_MODE=live
  VITE_BFF_FALLBACK=strict
  VITE_BFF_REAL_WRITES=false

The deployment URL may be passed as the first argument or via
LOVABLE_DEPLOYMENT_URL. Override LOVABLE_AUDIT_OUTPUT_DIR to write generated
audit artifacts outside support/evidence when running in CI. The final process
exit is controlled by publish_gate_checker.py so publish completion remains
blocked unless the LSP-005 strict-publish-audit packet passed.
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
output_json="${LOVABLE_AUDIT_OUTPUT_JSON:-$output_dir/strict-publish-audit.json}"
report_md="${LOVABLE_AUDIT_REPORT_MD:-$output_dir/strict-publish-audit.md}"
gate_json="${LOVABLE_PUBLISH_GATE_OUTPUT_JSON:-$output_dir/publish-gate.json}"

mkdir -p "$output_dir"

set +e
python3 "$ROOT_DIR/scripts/lovable/strict_publish_audit.py" "$deployment_url" \
  --output "$output_json" \
  --report "$report_md"
audit_rc=$?
set -e

set +e
python3 "$ROOT_DIR/scripts/lovable/publish_gate_checker.py" \
  --audit-json "$output_json" \
  --output "$gate_json"
gate_rc=$?
set -e

if [[ "$audit_rc" -ne 0 && "$gate_rc" -eq 0 ]]; then
  echo "ci_strict_publish_audit: strict audit failed but publish gate passed; refusing inconsistent result" >&2
  exit "$audit_rc"
fi

exit "$gate_rc"
