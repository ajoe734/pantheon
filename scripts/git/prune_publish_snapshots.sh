#!/usr/bin/env bash
# Prune old publish/v* branches and release/v* tags beyond a retention window.
#
# Only the daily per-task version scheme vYYYY.MM.DD.N is ever pruned. The
# legacy wave scheme vYYYY.WW.N (and anything that does not match the daily
# regex) is left completely untouched. The most recent KEEP_MIN snapshots and
# the single newest one are always kept regardless of age, so promotion
# (publish-promote.yml soaks recent snapshots) and the "latest release" anchor
# used by nightly_publish.sh are never disturbed.
#
# Hourly cuts (OPS-HOURLY-PUBLISH-AUTODEPLOY) create up to ~24 refs/day; this
# keeps the publish/* and release/* namespaces bounded.
#
# Usage:
#   prune_publish_snapshots.sh            # dry-run: only prints what it would do
#   prune_publish_snapshots.sh apply      # actually deletes on the remote
#
# Env:
#   RETENTION_DAYS  delete daily-format refs older than this many days (default 7)
#   KEEP_MIN        always keep at least this many newest refs (default 24)
#   REMOTE          git remote to prune (default origin)
set -euo pipefail

MODE="${1:-dry-run}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
KEEP_MIN="${KEEP_MIN:-24}"
REMOTE="${REMOTE:-origin}"

cutoff="$(date -u -d "${RETENTION_DAYS} days ago" +%Y%m%d)"
echo "prune mode=${MODE} retention_days=${RETENTION_DAYS} keep_min=${KEEP_MIN} cutoff=${cutoff} remote=${REMOTE}"

# Emit "YYYYMMDD <zero-padded-N> <name>" for every daily-format ref, newest
# first. Args: <ls-remote pattern> <strip-prefix>.
collect_daily() {
  local pattern="$1" strip="$2" kind="$3" line ref
  while read -r line; do
    ref="${line##*$'\t'}"
    ref="${ref#"$strip"}"
    if [[ "$ref" =~ ^v([0-9]{4})\.([0-9]{2})\.([0-9]{2})\.([0-9]+)$ ]]; then
      printf '%s%s%s %010d %s\n' \
        "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}" \
        "${BASH_REMATCH[4]}" "$ref"
    fi
  done < <(git ls-remote "$kind" "$REMOTE" "$pattern") | sort -r
}

# Given the sorted (newest-first) list on stdin, print the refs to delete:
# those ranked at/after KEEP_MIN whose date is strictly older than the cutoff.
select_deletions() {
  local idx=0 datekey _n ref
  while read -r datekey _n ref; do
    if (( idx >= KEEP_MIN )) && (( datekey < cutoff )); then
      echo "$ref"
    fi
    idx=$((idx + 1))
  done
}

deleted=0 failed=0 kept_recent=0

prune_set() {
  local kind="$1" pattern="$2" strip="$3" refspec_fmt="$4"
  local total del_count ref
  local listing deletions
  listing="$(collect_daily "$pattern" "$strip" "$kind")"
  total="$(printf '%s' "$listing" | grep -c . || true)"
  deletions="$(printf '%s\n' "$listing" | select_deletions)"
  del_count="$(printf '%s' "$deletions" | grep -c . || true)"
  echo "--- ${pattern}: ${total} daily-format refs, ${del_count} eligible to prune"
  [[ -z "$deletions" ]] && return 0
  while read -r ref; do
    [[ -z "$ref" ]] && continue
    # shellcheck disable=SC2059
    local refspec; refspec="$(printf "$refspec_fmt" "$ref")"
    if [[ "$MODE" == "apply" ]]; then
      if git push "$REMOTE" --delete "$refspec" >/dev/null 2>&1; then
        echo "  deleted ${refspec}"
        deleted=$((deleted + 1))
      else
        echo "  WARN failed to delete ${refspec} (protected or already gone)"
        failed=$((failed + 1))
      fi
    else
      echo "  would delete ${refspec}"
      deleted=$((deleted + 1))
    fi
  done <<< "$deletions"
}

# Branches: publish/v*  → delete refspec "publish/<ref>"
prune_set --heads 'publish/*' 'refs/heads/publish/' 'publish/%s'
# Tags: release/v*       → delete refspec "refs/tags/release/<ref>"
prune_set --tags  'release/*' 'refs/tags/release/' 'refs/tags/release/%s'

echo "summary: $([[ "$MODE" == apply ]] && echo deleted || echo would-delete)=${deleted} failed=${failed}"
