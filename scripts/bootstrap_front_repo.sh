#!/usr/bin/env bash
# bootstrap_front_repo.sh — LOOP-003 prerequisite bootstrap helper
#
# Sets up the front-ai-trading-system repo prerequisites needed for the
# Pantheon Console closed-loop coordination protocol.
#
# Prerequisites (caller must satisfy):
#   - gh CLI authenticated with a token that has repo scope on both
#     ajoe734/pantheon and ajoe734/front-ai-trading-system
#   - git available in PATH
#
# Usage:
#   bash scripts/bootstrap_front_repo.sh [--front-repo-path PATH]
#
# Options:
#   --front-repo-path PATH   Local path to the front-ai-trading-system checkout.
#                            Defaults to ../front-ai-trading-system (sibling).
#   --skip-labels            Skip GitHub label creation (idempotent by default).
#   --skip-workflows         Skip copying workflow templates into the front repo.
#   --dry-run                Print what would be done without making changes.
#
# See .coordination/workflow-templates/ for the front-repo workflow sources.
# See docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/coordination-loop-spec.md
# for the full bootstrap prerequisites spec.

set -euo pipefail

PANTHEON_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONT_REPO_DEFAULT="$(cd "$PANTHEON_ROOT/.." && pwd)/front-ai-trading-system"

FRONT_REPO_PATH="$FRONT_REPO_DEFAULT"
SKIP_LABELS=false
SKIP_WORKFLOWS=false
DRY_RUN=false

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --front-repo-path)
      FRONT_REPO_PATH="$2"; shift 2 ;;
    --skip-labels)
      SKIP_LABELS=true; shift ;;
    --skip-workflows)
      SKIP_WORKFLOWS=true; shift ;;
    --dry-run)
      DRY_RUN=true; shift ;;
    *)
      echo "Unknown option: $1" >&2
      echo "Usage: $0 [--front-repo-path PATH] [--skip-labels] [--skip-workflows] [--dry-run]" >&2
      exit 1 ;;
  esac
done

FRONT_REPO_NAME="ajoe734/front-ai-trading-system"
WORKFLOW_TEMPLATES_DIR="$PANTHEON_ROOT/.coordination/workflow-templates"
FRONT_WORKFLOWS_DIR="$FRONT_REPO_PATH/.github/workflows"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()  { echo "[bootstrap] $*"; }
warn()  { echo "[bootstrap] WARN: $*" >&2; }
error() { echo "[bootstrap] ERROR: $*" >&2; exit 1; }
dry()   { echo "[bootstrap] DRY-RUN: $*"; }

run() {
  if [[ "$DRY_RUN" == "true" ]]; then
    dry "$*"
  else
    "$@"
  fi
}

# ---------------------------------------------------------------------------
# Step 1: Verify sibling checkout
# ---------------------------------------------------------------------------
info "Step 1: verify sibling checkout at $FRONT_REPO_PATH"

if [[ ! -d "$FRONT_REPO_PATH" ]]; then
  warn "Front repo checkout not found at $FRONT_REPO_PATH"
  info "  Clone it with:"
  info "    git clone git@github.com:$FRONT_REPO_NAME.git $FRONT_REPO_PATH"
  if [[ "$DRY_RUN" == "false" ]]; then
    error "Sibling checkout missing. Cannot continue without --dry-run or a valid checkout."
  fi
else
  info "  OK: $FRONT_REPO_PATH exists"
  if [[ -d "$FRONT_REPO_PATH/.git" ]]; then
    FRONT_DEFAULT_BRANCH=$(git -C "$FRONT_REPO_PATH" symbolic-ref refs/remotes/origin/HEAD 2>/dev/null \
      | sed 's|refs/remotes/origin/||' || echo "main")
    info "  Default branch: $FRONT_DEFAULT_BRANCH"
  fi
fi

# ---------------------------------------------------------------------------
# Step 2: GitHub label bootstrap
# ---------------------------------------------------------------------------
if [[ "$SKIP_LABELS" == "false" ]]; then
  info "Step 2: bootstrap GitHub labels on $FRONT_REPO_NAME"

  if ! command -v gh &>/dev/null; then
    warn "gh CLI not found — skipping label creation. Install gh and re-run, or create labels manually."
  else
    # Labels required by the legacy issue bus (compatibility/audit mode)
    declare -A LABELS=(
      ["pantheon-bus"]="0075ca:Pantheon cross-repo coordination bus"
      ["coordination-bus"]="e4e669:Closed-loop coordination protocol messages"
    )

    for label in "${!LABELS[@]}"; do
      color="${LABELS[$label]%%:*}"
      description="${LABELS[$label]#*:}"

      # gh label create is idempotent with --force
      info "  Ensuring label '$label' exists on $FRONT_REPO_NAME"
      run gh label create "$label" \
        --repo "$FRONT_REPO_NAME" \
        --color "$color" \
        --description "$description" \
        --force
    done

    info "  Labels bootstrapped."
  fi
else
  info "Step 2: skipping label bootstrap (--skip-labels)"
fi

# ---------------------------------------------------------------------------
# Step 3: Copy workflow templates into the front repo
# ---------------------------------------------------------------------------
if [[ "$SKIP_WORKFLOWS" == "false" ]]; then
  info "Step 3: copy workflow templates into $FRONT_REPO_PATH"

  TEMPLATES=(
    "pantheon-handoff-receiver.yml"
    "pantheon-feedback-publisher.yml"
  )

  if [[ ! -d "$WORKFLOW_TEMPLATES_DIR" ]]; then
    error "Workflow templates directory not found: $WORKFLOW_TEMPLATES_DIR"
  fi

  if [[ "$DRY_RUN" == "false" && ! -d "$FRONT_REPO_PATH/.github/workflows" ]]; then
    run mkdir -p "$FRONT_WORKFLOWS_DIR"
  fi

  for tpl in "${TEMPLATES[@]}"; do
    src="$WORKFLOW_TEMPLATES_DIR/$tpl"
    dst="$FRONT_WORKFLOWS_DIR/$tpl"

    if [[ ! -f "$src" ]]; then
      error "Template not found: $src"
    fi

    if [[ -f "$dst" ]]; then
      info "  $tpl already exists in front repo — skipping (delete it to re-seed)"
    else
      info "  Copying $tpl → $FRONT_WORKFLOWS_DIR/"
      run cp "$src" "$dst"
    fi
  done

  info "  Workflow templates installed."
else
  info "Step 3: skipping workflow template copy (--skip-workflows)"
fi

# ---------------------------------------------------------------------------
# Step 4: Verify COORDINATION_REPO_TOKEN secret note
# ---------------------------------------------------------------------------
info "Step 4: secret requirement reminder"
info "  Both repos require a GitHub Actions secret named COORDINATION_REPO_TOKEN."
info "  The token must have:"
info "    - contents: write on ajoe734/front-ai-trading-system"
info "    - contents: read  on ajoe734/pantheon"
info "    - repo scope if repository_dispatch is used"
info "  Set it via:"
info "    gh secret set COORDINATION_REPO_TOKEN --repo $FRONT_REPO_NAME"
info "    gh secret set COORDINATION_REPO_TOKEN --repo ajoe734/pantheon"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
if [[ "$DRY_RUN" == "true" ]]; then
  info "Dry-run complete — no changes made."
else
  info "Bootstrap complete."
  info "Next: run 'python3 scripts/validate_loop_prerequisites.py' to verify the full setup."
fi
