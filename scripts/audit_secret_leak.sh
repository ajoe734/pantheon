#!/usr/bin/env bash
# audit_secret_leak.sh — stopgap committed-secret scan (direction D-security).
#
# Greps tracked files for high-signal secret ASSIGNMENTS, then filters out the
# false-positive classes verified in V5: code variable references (getenv/env/
# token=...), docs/examples/tests, and self-describing placeholders (e.g. the seed
# value "pantheon-prod-broker-api-key" IS its own description, not a real key).
# Exit 1 if any real-looking secret remains. NOTE: this is a stopgap — adopt
# gitleaks/trufflehog in CI for thorough, entropy-based detection.
set -uo pipefail
REF="${1:-origin/dev}"
hits="$(git grep -nIE "(api[_-]?key|secret[_-]?key|password|access[_-]?token|private[_-]?key)[\"' ]*[:=][\"' ]*[A-Za-z0-9_/+.-]{16,}" "$REF" \
  -- ':(exclude)*.md' ':(exclude)*example*' ':(exclude)*test*' ':(exclude).env*' 2>/dev/null \
  | grep -viE "redacted|placeholder|<set|xxxx|your[_-]|dummy|example|changeme|stub|fake|os\.getenv|getenv|environ|process\.env|import\.meta\.env|\.access_token|requires_confirm|missing_confirm|resume_token|_token_from_env|removeprefix|encode_jwt|generate_token|explicit_token|env_token|_configured_value|_value\(" \
  | grep -viE "\"[a-z_]*(api_key|secret_key)\": \"pantheon-(prod|dev|staging)-[a-z-]+-(api|secret)-key\"" \
  || true)"
if [ -z "$hits" ]; then
  echo "OK: no committed real-looking secrets found on $REF (placeholders/code refs excluded)."
  exit 0
fi
echo "POTENTIAL committed secrets on $REF:"; echo "$hits" | head -40
exit 1
