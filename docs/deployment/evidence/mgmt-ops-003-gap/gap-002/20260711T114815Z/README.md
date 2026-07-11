# MGMT-OPS-003-GAP-002 Hosted Evidence

Captured: 2026-07-11T11:48:15Z

Task: `MGMT-OPS-003-GAP-002`

Verdict: `REQUEST_CHANGES`

## Delivery identity

- Implementation merge: `18d064477a5ec88740b7da4b879735be589df97e`.
- Deployed dev SHA: `636f989563157c78118de17b81ef8651389a7acd`.
- `git merge-base --is-ancestor 18d064477 636f98956` returned success.
- Deployment: GitHub Actions `nonprod-deploy.yml` run `29151498421`, target
  `dev/bff`, succeeded. The explicit `allow_dirty` path preserved the managed
  deploy worktree's unrelated `.dockerignore` modification in a named stash
  before checkout; it did not discard that change.

## Authenticated BFF capture

The captures use the dev stub operator identity and the allowed
`pantheon-dev` tenant. The bearer credential is not stored in these artifacts.

| Measure | Prior hosted baseline | Current hosted capture |
|---|---:|---:|
| Runtime count | 6 | 10 |
| Telemetry runtime count | 2 | 5 |
| Holdings | 14 | 18 |
| Missing-binding holdings | 10 | 10 |
| Degraded holdings | 14 | 18 |
| Holding incidents | 14 | 18 |

The prior values are the baseline recorded in the task handoff and reviewer
packet. The current values come from `portfolio-book.json` and
`portfolio-book-holdings.json` in this directory.

The core portfolio summary reports zero missing bindings for its pool/runtime
join. That does not erase the holding-level truth: all ten unresolved holdings
remain present, degraded, and incident-backed in the holdings response. Formal
attribution values remain unavailable for those degraded rows. This is a
quarantine/isolation outcome, not a claim that the authoritative source data
has been repaired.

## Hosted browser capture

Playwright loaded the current Pantheon-owned frontend in strict live mode with
the same authenticated operator context at desktop (1440x1000) and mobile
(390x844) viewports.

- Browser console errors: 0 desktop, 0 mobile.
- Failed BFF requests: 0 desktop, 0 mobile.
- Required holdings response: HTTP 200 on both viewports.
- Seed/mock fallback text: absent on both viewports.
- Raw `undefined`, `NaN`, or `Invalid Date`: absent on both viewports.
- Screenshots: `portfolio-book-desktop.png` and
  `portfolio-book-mobile.png`.

## Blocking UI-to-API difference

The hosted UI does not yet represent the captured BFF truth:

- API portfolio summary: runtime count 10 and telemetry runtime count 5.
- API holdings: 10 missing bindings, 18 degraded rows, and 18 incidents.
- Hosted UI summary card: `Telemetry Runtime 0`.
- The desktop pool table labels some `0/0` runtime rows as telemetry covered.

That difference violates the fail-closed reviewer checklist: UI counts do not
match the authenticated response and displayed confidence exceeds the
holding-level source truth. Backend deployment freshness is now proven, but
this task cannot be approved until `MGMT-OPS-003-GAP-001` deploys the frontend
incident/count/confidence treatment and a reviewer repeats the hosted sample.

## Files

- `portfolio-book.json`: authenticated portfolio summary response.
- `portfolio-book-holdings.json`: authenticated holdings and incidents.
- `portfolio-book-positions.json`: authenticated positions response.
- `performance-attribution.json`: authenticated attribution response.
- `hosted-summary.json`: compact deployment identity and summary extract.
- `hosted-browser-evidence.json`: viewport, console, network, and fallback
  counters.
- `portfolio-book-desktop.png`: desktop hosted capture.
- `portfolio-book-mobile.png`: mobile hosted capture.
