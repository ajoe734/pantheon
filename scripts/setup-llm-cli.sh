#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

cat > "$ROOT_DIR/.orchestrator/claude-approval-broker.mcp.json" <<EOF
{
  "mcpServers": {
    "orchestrator_approval_broker": {
      "command": "python3",
      "args": [
        "$ROOT_DIR/.orchestrator/claude_permission_prompt_mcp.py",
        "--config",
        "$ROOT_DIR/.orchestrator/config.json"
      ],
      "env": {
        "PYTHONUNBUFFERED": "1"
      }
    }
  }
}
EOF

python3 "$ROOT_DIR/.orchestrator/sync_provider_permissions.py" --apply >/dev/null
python3 "$ROOT_DIR/scripts/ai_status.py" sync >/dev/null
FIRST_PROMPT="$(python3 "$ROOT_DIR/scripts/ai_status.py" prompt)"

cat <<EOF
LLM CLI setup applied for: $ROOT_DIR

Next steps:
1. Start supervisor
   bash scripts/run-supervisor.sh --verbose

2. Start dashboard
   bash scripts/run-dashboard.sh

3. In each LLM CLI, use this first prompt:
   $FIRST_PROMPT

4. As the repo gains project-specific architecture or backlog docs, update
   AI_COLLABORATION_GUIDE.md and ai-status.json canonical layers so the prompt
   stays aligned with the repo's real source of truth.
EOF
