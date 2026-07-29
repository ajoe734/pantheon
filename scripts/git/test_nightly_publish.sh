#!/usr/bin/env bash
# Regression for the nightly publish helper contract.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_ROOT="$(mktemp -d -t pantheon-nightly-publish-test-XXXXXX)"
trap 'rm -rf "$TMP_ROOT"' EXIT

REMOTE_DIR="$TMP_ROOT/remote.git"
SEED_DIR="$TMP_ROOT/seed"
RUNNER_DIR="$TMP_ROOT/runner"

git init --bare --quiet "$REMOTE_DIR"
git clone --quiet "$REMOTE_DIR" "$SEED_DIR"
git -C "$SEED_DIR" config user.name "publish-test"
git -C "$SEED_DIR" config user.email "publish-test@example.com"
git -C "$SEED_DIR" checkout -b dev --quiet

mkdir -p \
  "$SEED_DIR/scripts/git" \
  "$SEED_DIR/scripts" \
  "$SEED_DIR/.orchestrator"
cp "$ROOT_DIR/scripts/git/nightly_publish.sh" "$SEED_DIR/scripts/git/nightly_publish.sh"
cp "$ROOT_DIR/scripts/release_branch_discipline.py" "$SEED_DIR/scripts/release_branch_discipline.py"
cp "$ROOT_DIR/.orchestrator/config.json" "$SEED_DIR/.orchestrator/config.json"
RELEASE_STATE_SOURCE="$ROOT_DIR/.orchestrator/release-state.json"
RELEASE_STATE_FIXTURE="$SEED_DIR/.orchestrator/release-state.json"
cp "$RELEASE_STATE_SOURCE" "$RELEASE_STATE_FIXTURE"

git -C "$SEED_DIR" add \
  scripts/git/nightly_publish.sh \
  scripts/release_branch_discipline.py \
  .orchestrator/config.json \
  .orchestrator/release-state.json
git -C "$SEED_DIR" commit --quiet -m "seed publish helper"
BASE_SHA="$(git -C "$SEED_DIR" rev-parse HEAD)"
git -C "$SEED_DIR" push --quiet origin dev

# Enough refs to overflow the pipe buffer makes the historical
# `git for-each-ref | head -1 | awk` failure deterministic on Linux.
seq 1 12000 |
  awk -v sha="$BASE_SHA" \
    '{printf "create refs/tags/release/v2020.01.01.%05d %s\n", $1, sha}' |
  git --git-dir="$REMOTE_DIR" update-ref --stdin

git -C "$SEED_DIR" commit --allow-empty --quiet -m "advance dev"
git -C "$SEED_DIR" push --quiet origin dev
git clone --quiet --branch dev "$REMOTE_DIR" "$RUNNER_DIR"
git -C "$RUNNER_DIR" config user.name "publish-test"
git -C "$RUNNER_DIR" config user.email "publish-test@example.com"

set +e
git -C "$RUNNER_DIR" for-each-ref --sort=-creatordate \
  --format='%(objectname) %(refname:short)' 'refs/tags/release/*' |
  head -1 |
  awk '{print $1}' >/dev/null
OLD_PIPELINE_RC=$?
set -e
if [[ "$OLD_PIPELINE_RC" != "141" ]]; then
  echo "expected historical many-tag pipeline to fail with 141, got $OLD_PIPELINE_RC" >&2
  exit 1
fi

FIRST_OUTPUT="$(bash "$RUNNER_DIR/scripts/git/nightly_publish.sh" now)"
PUBLISH_BRANCH=""
RELEASE_TAG=""
while IFS= read -r line; do
  case "$line" in
    publish_branch=*) PUBLISH_BRANCH="${line#publish_branch=}" ;;
    release_tag=*) RELEASE_TAG="${line#release_tag=}" ;;
  esac
done <<< "$FIRST_OUTPUT"
[[ "$PUBLISH_BRANCH" == publish/v* ]]
[[ "$RELEASE_TAG" == release/v* ]]

SECOND_OUTPUT="$(bash "$RUNNER_DIR/scripts/git/nightly_publish.sh" now)"
if [[ "$SECOND_OUTPUT" == *"publish_branch="* ]]; then
  echo "repeated publish created another snapshot instead of no-op" >&2
  exit 1
fi

set +e
bash "$RUNNER_DIR/scripts/git/nightly_publish.sh" check >/dev/null
CHECK_RC=$?
set -e
if [[ "$CHECK_RC" != "10" ]]; then
  echo "expected repeated check to report no advancement with 10, got $CHECK_RC" >&2
  exit 1
fi

[[ "$(git ls-remote --heads "$REMOTE_DIR" "$PUBLISH_BRANCH" | wc -l)" == "1" ]]
[[ "$(git ls-remote --tags "$REMOTE_DIR" "$RELEASE_TAG" | wc -l)" == "1" ]]

if grep -Eq '\|[[:space:]]*(head|tail)[[:space:]]' \
  "$ROOT_DIR/scripts/git/nightly_publish.sh" \
  "$ROOT_DIR/.github/workflows/nightly-publish-cut.yml"; then
  echo "publish contract still contains an early-closing pipeline consumer" >&2
  exit 1
fi

echo "nightly publish many-tag and idempotency regression passed"
