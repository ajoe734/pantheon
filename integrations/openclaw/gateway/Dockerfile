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
FROM ghcr.io/openclaw/openclaw:2026.7.1

# This gateway runs Node 24 and may use a newer Claude Code CLI than the
# Python adapter image, whose separate CLI remains constrained by Node 20.
ARG ANTHROPIC_CLAUDE_CODE_NPM_VERSION=2.1.216
ARG GOOGLE_GEMINI_CLI_NPM_VERSION=0.51.0

USER root
RUN npm install -g \
      "@anthropic-ai/claude-code@${ANTHROPIC_CLAUDE_CODE_NPM_VERSION}" \
      "@google/gemini-cli@${GOOGLE_GEMINI_CLI_NPM_VERSION}" \
    && claude --version \
    && gemini --version
# Point the Claude CLI's default config dir (~/.claude) at the persistent
# openclaw-data volume location. OpenClaw spawns `claude -p` in a sanitized env
# WITHOUT CLAUDE_CONFIG_DIR, so it resolves the default ~/.claude — this symlink
# makes that default find the one-time `claude auth login` credentials that live
# on the volume (CLAUDE_CONFIG_DIR=/home/node/.openclaw/claude-cli), so the login
# survives container recreate. Dangling at build time; resolves once the volume
# mounts at runtime.
RUN rm -rf /home/node/.claude \
    && ln -s /home/node/.openclaw/claude-cli /home/node/.claude \
    && chown -h node:node /home/node/.claude
# Pantheon persona tools plugin (design H9): registers persona_memory_recall,
# persona_mental_model_read and persona_proposal_submit and forwards each call to
# the adapter's internal broker. Built here (npm ci from the lockfile, tests run at
# build time) and loaded by the compose startup; it is not
# enabled unless PERSONA_TOOL_BROKER_TOKEN is provided at runtime. The compose startup loads it through a
# runtime-only overlay config, never by persisting plugins.load.paths into the shared volume config.
COPY integrations/openclaw/plugins/pantheon-persona-tools /opt/pantheon/plugins/pantheon-persona-tools
# A host-built node_modules or dist in the build context must never be reused.
RUN cd /opt/pantheon/plugins/pantheon-persona-tools \
    && rm -rf node_modules dist \
    && npm ci --include=dev --no-audit --no-fund \
    && npm test \
    && npm prune --omit=dev --no-audit --no-fund \
    && rm -rf dist/test test \
    && chown -R root:root /opt/pantheon/plugins \
    && chmod -R go-w /opt/pantheon/plugins
# Restore the upstream runtime user; entrypoint/cmd/workdir are inherited.
USER node
