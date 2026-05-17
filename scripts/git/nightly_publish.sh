#!/usr/bin/env bash
# Cut a publish snapshot from dev tip if dev advanced since the latest
# release tag.
#
# Usage: scripts/git/nightly_publish.sh [now|check]
#   now    cut and push immediately (default)
#   check  exit 0 if a cut would happen, exit 10 if dev hasn't advanced
#
# Driven by .github/workflows/nightly-publish-cut.yml on cron. Also
# usable manually after a hotfix when an out-of-band publish is needed.
#
# Output: pushes publish/v<YYYY>.<MM>.<DD>.<N> and release/v<...> tag.
# Refuses to overwrite an existing publish branch (immutable snapshots).

set -euo pipefail

MODE="${1:-now}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

CONFIG_FILE=".orchestrator/config.json"
DEV_BRANCH=$(python3 -c "
import json
try:
    c = json.load(open('$CONFIG_FILE'))
except Exception:
    c = {}
print(c.get('branch_workflow', {}).get('dev_branch') or 'dev')
")
PUBLISH_PREFIX=$(python3 -c "
import json
try:
    c = json.load(open('$CONFIG_FILE'))
except Exception:
    c = {}
print(c.get('branch_workflow', {}).get('publish_branch_prefix') or 'publish/')
")
RELEASE_PREFIX=$(python3 -c "
import json
try:
    c = json.load(open('$CONFIG_FILE'))
except Exception:
    c = {}
print(c.get('branch_workflow', {}).get('release_tag_prefix') or 'release/')
")

echo "→ fetch origin"
git fetch origin --tags --prune --quiet

DEV_SHA=$(git rev-parse "origin/${DEV_BRANCH}")
LATEST_RELEASE_SHA=$(git for-each-ref --sort=-creatordate \
  --format='%(objectname) %(refname:short)' "refs/tags/${RELEASE_PREFIX}*" \
  | head -1 | awk '{print $1}')

# Resolve to the commit the tag points at (handle annotated tags).
if [[ -n "$LATEST_RELEASE_SHA" ]]; then
  LATEST_RELEASE_COMMIT=$(git rev-parse "${LATEST_RELEASE_SHA}^{commit}")
else
  LATEST_RELEASE_COMMIT=""
fi

if [[ -n "$LATEST_RELEASE_COMMIT" && "$DEV_SHA" == "$LATEST_RELEASE_COMMIT" ]]; then
  echo "no new commits on origin/${DEV_BRANCH} since the latest release; nothing to cut"
  if [[ "$MODE" == "check" ]]; then exit 10; fi
  exit 0
fi

# Compute next version. Format: vYYYY.MM.DD.N
DATE=$(date -u +%Y.%m.%d)
EXISTING_TODAY=$(git for-each-ref --format='%(refname:short)' \
  "refs/tags/${RELEASE_PREFIX}v${DATE}.*" 2>/dev/null | wc -l)
N="$EXISTING_TODAY"
VER="v${DATE}.${N}"

PUBLISH_BRANCH="${PUBLISH_PREFIX}${VER}"
RELEASE_TAG="${RELEASE_PREFIX}${VER}"

# Sanity: ensure neither already exists on origin.
if git ls-remote --exit-code --heads origin "$PUBLISH_BRANCH" >/dev/null 2>&1; then
  echo "ERROR: $PUBLISH_BRANCH already exists on origin; refusing to overwrite immutable snapshot" >&2
  exit 4
fi
if git ls-remote --exit-code --tags origin "$RELEASE_TAG" >/dev/null 2>&1; then
  echo "ERROR: tag $RELEASE_TAG already exists on origin; bump N or fix the cron drift" >&2
  exit 5
fi

if [[ "$MODE" == "check" ]]; then
  echo "would cut $PUBLISH_BRANCH @ ${DEV_SHA:0:10}"
  exit 0
fi

# Cut in an isolated worktree so we don't disturb the main checkout.
WT=$(mktemp -d -t nightly-publish-XXXXXX)
cleanup() { git worktree remove --force "$WT" 2>/dev/null || rm -rf "$WT"; }
trap cleanup EXIT

echo "→ cut $PUBLISH_BRANCH from origin/${DEV_BRANCH} (${DEV_SHA:0:10})"
git worktree add "$WT" "origin/${DEV_BRANCH}" --quiet
(
  cd "$WT"
  git checkout -B "$PUBLISH_BRANCH" "origin/${DEV_BRANCH}" --quiet
  git push -u origin "$PUBLISH_BRANCH"
  git tag -a "$RELEASE_TAG" -m "nightly publish: ${VER} from ${DEV_BRANCH}@${DEV_SHA:0:10}"
  git push origin "$RELEASE_TAG"
)

echo "✓ published ${VER} (${DEV_SHA:0:10})"
echo "  branch: $PUBLISH_BRANCH"
echo "  tag:    $RELEASE_TAG"
echo "  next:   publish-promote.yml will pick this up after the soak window"
