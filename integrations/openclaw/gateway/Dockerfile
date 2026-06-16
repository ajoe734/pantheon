# Pantheon-derived OpenClaw gateway image.
#
# Layers the Claude Code CLI onto the GOVERNED upstream OpenClaw runtime so that
# OpenClaw's `claude-cli` agent runtime can spawn `claude -p` for `anthropic/*`
# models. This enables multi-model persona routing — e.g. debate personas on
# different model families (openai/gpt-5.5 via Codex OAuth + anthropic/claude-*
# via Claude CLI) — using subscription OAuth and ZERO API keys.
#
# OpenClaw's claude-cli backend reuse requires the `claude` binary to live in
# the SAME container as the OpenClaw gateway process (it spawns `claude -p`
# directly); the adapter image carrying claude does not help the gateway. See
# integrations/openclaw/anthropic-cli-reuse note in governance.md.
#
# The upstream runtime pin is governed: tag/commit are tracked in
# OSS_INTEGRATION_CHECKLIST.md and integrations/openclaw/governance.md. When
# bumping, change the FROM tag here AND the upstream commit pin there together.
FROM ghcr.io/openclaw/openclaw:2026.6.6

# Keep the Claude Code CLI version aligned with
# services/openclaw-gateway-adapter/Dockerfile (ANTHROPIC_CLAUDE_CODE_NPM_VERSION).
ARG ANTHROPIC_CLAUDE_CODE_NPM_VERSION=2.1.178

USER root
RUN npm install -g "@anthropic-ai/claude-code@${ANTHROPIC_CLAUDE_CODE_NPM_VERSION}" \
    && claude --version
# Restore the upstream runtime user; entrypoint/cmd/workdir are inherited.
USER node
