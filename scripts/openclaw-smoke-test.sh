#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="v2026.4.7"
TAG_COMMIT="5050017543011b61df67744ebc6368d889c25a95"
IMAGE="ghcr.io/openclaw/openclaw:2026.4.7"
IMAGE_DIGEST="sha256:be45b5187cbec1ff0f4e2503393d66acfc121c2d97eadf03bb1ac75826bad77c"
FIXTURE="$REPO_ROOT/integrations/openclaw/fixtures/raw_research_handoff.minimal.json"
WORK_DIR="${OPENCLAW_SMOKE_WORK_DIR:-$(mktemp -d /tmp/openclaw-bp5-oss-001.XXXXXX)}"
NORMALIZED_DIR="$WORK_DIR/normalized"
SKIP_DOCKER=false
PASS=0
FAIL=0

for arg in "$@"; do
  case "$arg" in
    --skip-docker) SKIP_DOCKER=true ;;
    *) echo "Unknown flag: $arg" >&2; exit 1 ;;
  esac
done

pass() {
  echo "PASS: $1"
  PASS=$((PASS + 1))
}

fail() {
  echo "FAIL: $1" >&2
  FAIL=$((FAIL + 1))
}

echo "Working directory: $WORK_DIR"
mkdir -p "$NORMALIZED_DIR"

echo "=== Step 1: Verify the pinned source tag ==="
TAG_MATCH="$(git ls-remote --tags https://github.com/openclaw/openclaw.git "refs/tags/${TAG}^{}" | awk '{print $1}')"
if [[ "$TAG_MATCH" == "$TAG_COMMIT" ]]; then
  pass "Git tag ${TAG} resolves to ${TAG_COMMIT}"
else
  fail "Git tag ${TAG} resolved to ${TAG_MATCH:-<missing>} instead of ${TAG_COMMIT}"
fi

if [[ "$SKIP_DOCKER" == false ]]; then
  echo "=== Step 2: Verify the pinned container artifact and command surface ==="

  if docker manifest inspect "$IMAGE" >/dev/null; then
    pass "Manifest exists for ${IMAGE}"
  else
    fail "Manifest lookup failed for ${IMAGE}"
  fi

  docker pull "$IMAGE" >/dev/null
  DIGEST_MATCH="$(docker image inspect "$IMAGE" --format '{{join .RepoDigests "\n"}}' | grep "^ghcr.io/openclaw/openclaw@${IMAGE_DIGEST}$" || true)"
  if [[ -n "$DIGEST_MATCH" ]]; then
    pass "Local image digest matches ${IMAGE_DIGEST}"
  else
    fail "Local image digest did not match ${IMAGE_DIGEST}"
  fi

  docker run --rm --entrypoint node "$IMAGE" dist/index.js --help >"$WORK_DIR/openclaw-help.txt"
  if grep -q "Usage: openclaw" "$WORK_DIR/openclaw-help.txt"; then
    pass "openclaw --help succeeded from the pinned image"
  else
    fail "openclaw --help did not print the expected usage banner"
  fi

  docker run --rm --entrypoint node "$IMAGE" dist/index.js gateway --help >"$WORK_DIR/openclaw-gateway-help.txt"
  if grep -q "Usage: openclaw gateway" "$WORK_DIR/openclaw-gateway-help.txt"; then
    pass "openclaw gateway --help succeeded from the pinned image"
  else
    fail "openclaw gateway --help did not print the expected usage banner"
  fi
else
  echo "=== Step 2: SKIPPED (--skip-docker) ==="
fi

echo "=== Step 3: Normalize the governed handoff fixture ==="
python3 "$REPO_ROOT/services/control-plane/specs/normalize_handoff.py" "$FIXTURE" "$NORMALIZED_DIR" >"$WORK_DIR/normalize.log"
if [[ -f "$NORMALIZED_DIR/strategy_spec.json" && -f "$NORMALIZED_DIR/workflow_handoff.json" ]]; then
  pass "Fixture normalized into canonical StrategySpec and WorkflowHandoff"
else
  fail "Normalization output files were not written"
fi

echo
echo "=== Results: $PASS passed, $FAIL failed ==="
echo "Artifacts: $WORK_DIR"

if [[ "$FAIL" -eq 0 ]]; then
  exit 0
fi

exit 1
