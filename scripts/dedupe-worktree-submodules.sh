#!/usr/bin/env bash
# Deduplicate per-worktree git submodule object stores.
#
# Each worker worktree that runs `git submodule update` clones a *full* copy of
# the lean submodule's objects (~577M) into
#   .git/worktrees/<name>/modules/lean/objects
# With dozens of concurrent OODA worktrees this duplicates ~17G+ of identical
# objects. This script points each worktree submodule at the superproject's
# single shared object store via objects/info/alternates, then repacks to drop
# the now-redundant local copy. Safe + idempotent: objects not present in the
# shared store are kept locally; worktrees with a live worker process are skipped.
#
# Installed as an hourly cron alongside rotate-worker-logs.sh. Run manually any
# time. See also: the permanent fix is to pre-init the submodule with
# `--reference` in supervisor._create_worker_worktree.
set -euo pipefail

REPO_ROOT="${PANTHEON_REPO_ROOT:-/home/lupin/code/pantheon}"
SUBMODULE="${1:-lean}"
SHARED_OBJECTS="${REPO_ROOT}/.git/modules/${SUBMODULE}/objects"
MIN_MB=100   # only bother with worktree modules larger than this

cd "$REPO_ROOT"

if [ ! -d "$SHARED_OBJECTS" ]; then
  echo "shared object store missing: $SHARED_OBJECTS" >&2
  exit 1
fi

# Working dirs of live worker processes — never touch a worktree in use.
active_cwds="$(mktemp)"
trap 'rm -f "$active_cwds"' EXIT
for pid in $(ps -eo pid=,cmd= | grep -iE 'worker_runner|native-binary/claude|codex|gemini|qwen|agy|dotnet|/lean' | grep -v grep | awk '{print $1}'); do
  readlink "/proc/$pid/cwd" 2>/dev/null || true
done | sort -u > "$active_cwds"

# Map worktree name -> checkout path (handles worktrees outside /tmp too).
declare -A WT_PATH
while IFS= read -r line; do
  case "$line" in
    worktree\ *) cur="${line#worktree }" ;;
  esac
  [ -n "${cur:-}" ] && WT_PATH["$(basename "$cur")"]="$cur"
done < <(git worktree list --porcelain)

reclaimed=0; done=0; skipped=0
for mod in .git/worktrees/*/modules/"$SUBMODULE"; do
  [ -d "$mod/objects" ] || continue
  name="$(echo "$mod" | cut -d/ -f3)"
  sz_mb="$(du -sm "$mod/objects" 2>/dev/null | cut -f1)"
  [ "${sz_mb:-0}" -ge "$MIN_MB" ] || continue
  wt="${WT_PATH[$name]:-/tmp/pantheon-worker-worktrees/pantheon/$name}"
  leandir="$wt/$SUBMODULE"
  [ -d "$leandir" ] || { skipped=$((skipped+1)); continue; }
  if grep -qF "$wt" "$active_cwds"; then
    echo "skip active: $name"; skipped=$((skipped+1)); continue
  fi
  mkdir -p "$mod/objects/info"
  echo "$SHARED_OBJECTS" > "$mod/objects/info/alternates"
  git -C "$leandir" repack -a -d -l >/dev/null 2>&1 || { echo "repack failed: $name" >&2; continue; }
  git -C "$leandir" prune-packed >/dev/null 2>&1 || true
  if git -C "$leandir" rev-parse HEAD >/dev/null 2>&1; then
    after_mb="$(du -sm "$mod/objects" 2>/dev/null | cut -f1)"
    reclaimed=$((reclaimed + sz_mb - after_mb))
    done=$((done+1))
    echo "deduped: $name ${sz_mb}M -> ${after_mb}M"
  else
    echo "INTEGRITY FAIL after dedupe: $name" >&2
  fi
done

git worktree prune 2>/dev/null || true
echo "summary: deduped=$done skipped=$skipped reclaimed~=${reclaimed}MB"
