# EXEC-REBASE-TW04-001 Review

Review date: 2026-04-20
Reviewer: Codex
Status: changes requested

## Findings

1. The claimed TW-04 frontend handoff bundle is not actually present.

- `.coordination/responses/TW-04-teaching-replay-lovable-prompt.md:24` through `.coordination/responses/TW-04-teaching-replay-lovable-prompt.md:28` tell the frontend lane to use `docs/pantheon-handoffs/TW-04-teaching-replay`.
- The repo currently has no `docs/pantheon-handoffs/TW-04-teaching-replay/` directory and no `docs/pantheon-handoffs/TW-04-teaching-replay/FRONTEND_CHANGE_SPEC.md`.
- The same prompt also references `.coordination/requests/TW-04-teaching-replay-bff-gap.example.yaml` at line 2 and `.coordination/requests/TW-04-teaching-replay-ui-done.example.yaml` at line 23, but those request templates are also absent.
- The task acceptance explicitly says the TW-04 frontend handoff bundle should be completed, so this is still blocking.

2. The TW-04 screen identity is internally inconsistent.

- `docs/screens/TW-04-teaching-replay.md:6` defines the screen id as `screen-teaching-replay`.
- `.coordination/responses/TW-04-teaching-replay-lovable-prompt.md:5` repeats `screen-teaching-replay`.
- `.coordination/responses/TW-04-teaching-replay-lovable-ui-task.yaml:9` instead publishes `screen-trainer-teaching-replay`.
- This kind of naming drift is enough to confuse the front-end lane and should be normalized before approval.

3. The coordination / backlog truth is still stale after the rebaseline claim.

- `WORKBENCH_DELIVERY_BACKLOG.md:99` still says `TW-04 Teaching Replay` is `contract published — BFF implementation pending`.
- `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md:8` and `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md:45` still say all trainer modules require live BFF implementation before UI work can begin.
- `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md:157` through `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md:162` still label the replay route family and evidence / authority contracts as pending implementation.
- `docs/lovable/PANTHEON_FRONTEND_SA.md:768` and `docs/lovable/PANTHEON_FRONTEND_SA.md:769` still mark the replay pages as `contract-published` / `pending-bff placeholder only`.
- The task next-step claims the coordination bundle was refreshed to live / ready truth, but the downstream packet and backlog sources still disagree.
