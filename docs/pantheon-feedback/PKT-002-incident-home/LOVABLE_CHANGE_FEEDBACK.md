# PKT-002 Incident Home — Lovable Change Feedback

Feature ID: `PKT-002-incident-home`
Screen: `incident-home`
Workbench: `operator-console`
Loop status: **implementation-complete / pantheon-review-pending**

Reviewed the current `ajoe734/front-ai-trading-system` working tree on top of commit
`37ebcafacb68ff617f097271c46eaac4a478cbb8`.

## Outcome

Pantheon review result: accepted for follow-up handoff.

The Incident Home screen is implemented against the published PKT-002 contract and
example payload, including the incident list query path, merged degradation banner,
and kill-switch control rail with explicit degraded or unavailable states.

## Verified Against Pantheon

- `GET /api/v1/incidents` is consumed through the shared `operatorApi.listIncidentHome()` client.
- `GET /api/v1/kill-switch/status` is consumed through the shared `operatorApi.getIncidentHomeKillSwitchStatus()` client.
- No raw `fetch()` or `axios` calls were added inside the screen component.
- The screen merges `meta.surfaces.incident_list` and `meta.surfaces.kill_switch` into the global degradation banner instead of deriving reliability locally.
- The kill-switch control rail renders a non-dismissable warning state when `meta.surfaces.kill_switch` is `degraded` and an explicit unavailable alert when it is `unavailable`.
- Missing required contract fields surface a `bff-gap` alert state instead of a mocked or inferred UI.

## Notes

- The current implementation renders the kill-switch rail inline on the Incident Home page rather than via a separate `KillSwitchBadge` component. This still matches the packet requirement because the rail is sourced only from the kill-switch endpoint and stays outside the incident list.
- The list view supports the packet's status filter model via query params and preserves pagination through `page_info.next_page_token`.
- This review included static verification plus local lint/build checks, but not a live browser session against a running Pantheon BFF.

## Pantheon Follow-up

- No Pantheon API gap is requested in this cycle.
- The next Pantheon-owned step is runtime verification against a live BFF plus any later cleanup tied to snapshot policy changes.
