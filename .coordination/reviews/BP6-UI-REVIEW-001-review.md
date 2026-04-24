# BP6-UI-REVIEW-001 Review

## Findings

1. High: the returned PKT-002 handoff is still not replayable from a Git-visible transport commit.

- The mirrored `ui-done` request now truthfully records `source_commit: faa1bc2d1bd02e0a3d9fc1e1e5c35bc510182ea7`.
- `git -C ../front-ai-trading-system show faa1bc2d1bd02e0a3d9fc1e1e5c35bc510182ea7:.coordination/requests/PKT-002-incident-detail-ui-done.yaml` still fails because the advertised payload path is absent from that commit.
- The current sibling front HEAD is `87340e96ce4247ccc177e8dff7579e804991b895`, but `git -C ../front-ai-trading-system status --short` still shows `.coordination/requests/PKT-002-incident-detail-ui-done.yaml` plus the mirrored `docs/pantheon-feedback/PKT-002-incident-detail/` bundle as untracked while `src/App.tsx` and `src/pages/operator/IncidentDetail.tsx` remain modified.
- Impact: Pantheon can mirror and review the current working tree, but it still cannot mark the `payload_path + source_commit` coordination tuple replay-clean or move the loop to `loop-complete`.

2. Medium: the refreshed front feedback bundle now truthfully captures current UI behavior, but it introduces a non-blocking Pantheon write-contract follow-up for `HardRollback`.

- The mirrored feedback bundle now matches the current sibling tree: route `/incidents/:incidentId`, CTA navigation to `/incident-action-drawer`, explicit `meta.staleness` rendering, and `data.kill_switch.active_commands[]` rendering are all documented consistently.
- The same bundle also raises an open request in `docs/pantheon-feedback/PKT-002-incident-detail/API_GAP_REQUESTS.json`: the Incident Detail packet still does not publish a canonical `target_artifact_id` source for `HardRollback`, so the embedded drawer correctly leaves that command disabled instead of guessing.
- Impact: this is not a blocker for acknowledging the current read-side UI alignment, but it should stay tracked as Pantheon follow-up instead of being silently dropped.

## Outcome

Do not approve `loop-complete` yet.

The current sibling front working tree is materially aligned on the PKT-002 read path and passed targeted static verification, so Pantheon should mirror the refreshed `ui-done` and feedback bundle as `followup-required`. The next front-owned cycle must publish that refreshed bundle from a replayable commit that actually contains the payload paths, and Pantheon should separately track the non-blocking `HardRollback` target-artifact follow-up.

## 2026-04-17 Addendum: review_ready_dispatch replay check

1. High: the republished PKT-002 closeout bundle still does not truthfully describe the live transport boundary in `source_commit: c08acb3`.

- `src/pages/operator/IncidentDetail.tsx` now imports the shared SSE substrate and opens three live streams directly from the screen component:
  - `@/lib/sseClient` / `@/lib/sseReconciler`
  - `/api/v1/runtime/${runtimeId}/events/stream`
  - `/api/v1/incidents/stream`
  - `/api/v1/kill-switch/updates`
- The republished feedback bundle still claims the opposite:
  - `docs/pantheon-feedback/PKT-002-incident-detail/QA_STATUS.md` says the detail screen "uses the shared BFF client and does not add raw network calls in component files"
  - `docs/pantheon-feedback/PKT-002-incident-detail/UI_DECISIONS.md` says the page "reads only through operatorApi.getIncidentResponse(...)"
  - `docs/pantheon-feedback/PKT-002-incident-detail/LOVABLE_CHANGE_FEEDBACK.md` documents the GET incident detail read plus the drawer GET/POST path, but omits the SSE dependency entirely
- The published task packet requires the returned artifacts to stay truthful about the current integration boundary. With the current `c08acb3` source tree, Pantheon cannot treat the handoff as acceptance-clean while the feedback bundle still omits the SSE overlay.
- Impact: do not approve `LUV-CLOSE-001` yet. The next front-owned republish must either:
  - update the feedback bundle to explicitly describe the SSE dependency and its operator impact, or
  - repoint the PKT-002 handoff at a source commit whose Incident Detail implementation stays inside the documented boundary.
