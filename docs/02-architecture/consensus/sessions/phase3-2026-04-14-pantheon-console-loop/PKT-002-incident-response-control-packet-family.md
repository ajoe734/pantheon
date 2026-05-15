# PKT-002 Incident Response and Incident Control Packet Family

## Overview

PKT-002 packetizes the Incident Response Console and Incident Control surface from the APP-002 incident and kill-switch sidecar. This document is the canonical packet requirements record for these screens.

Three surfaces — **Incident Home**, **Incident Detail**, and **Incident Action Drawer** — are packet-ready today. They share one packet family to ensure consistent degraded-state handling and fallback rules across read and control surfaces.

---

## Screen Inventory

### Operator Console — Incident Home

| Attribute | Value |
|---|---|
| Workbench | Operator Console |
| Screen | Incident Home |
| Screen ID | `screen-operator-incident-home` |
| Feature ID | `PKT-002-incident-home` |
| Packet status | **ready** |
| BFF backing | `GET /api/v1/incidents` (list with `status` filter), `GET /api/v1/kill-switch/status` (control rail badge) |
| Lovable readiness | Ready — incident list and kill switch status routes are live |
| Screen spec | `docs/screens/PKT-002-incident-home.md` |
| BFF contract | `docs/bff/PKT-002-incident-home.md` |
| Example payload | `docs/examples/PKT-002-incident-home.json` |
| Contract-ready | `.coordination/responses/PKT-002-incident-home-contract-ready.yaml` |
| Lovable UI task | `.coordination/responses/PKT-002-incident-home-lovable-ui-task.yaml` |
| BFF-gap template | `.coordination/requests/PKT-002-incident-home-bff-gap.example.yaml` |
| UI-done template | `.coordination/requests/PKT-002-incident-home-ui-done.example.yaml` |

### Operator Console — Incident Detail

| Attribute | Value |
|---|---|
| Workbench | Operator Console |
| Screen | Incident Detail |
| Screen ID | `screen-operator-incident-detail` |
| Feature ID | `PKT-002-incident-detail` |
| Packet status | **ready** |
| BFF backing | `GET /api/v1/operator/incident-response/{incident_id}` (composed view) |
| Lovable readiness | Ready — composed view joins incident record, affected bindings, and kill switch state |
| Screen spec | `docs/screens/PKT-002-incident-detail.md` |
| BFF contract | `docs/bff/PKT-002-incident-detail.md` |
| Example payload | `docs/examples/PKT-002-incident-detail.json` |
| Contract-ready | `.coordination/responses/PKT-002-incident-detail-contract-ready.yaml` |
| Lovable UI task | `.coordination/responses/PKT-002-incident-detail-lovable-ui-task.yaml` |
| BFF-gap template | `.coordination/requests/PKT-002-incident-detail-bff-gap.example.yaml` |
| UI-done template | `.coordination/requests/PKT-002-incident-detail-ui-done.example.yaml` |

### Operator Console — Incident Action Drawer

| Attribute | Value |
|---|---|
| Workbench | Operator Console |
| Screen | Incident Action Drawer |
| Screen ID | `screen-operator-incident-action-drawer` |
| Feature ID | `PKT-002-incident-action-drawer` |
| Packet status | **ready** |
| BFF backing | `GET /api/v1/kill-switch/status` (current state), `POST /api/v1/operator/commands` (emergency actions) |
| Lovable readiness | Ready — kill switch status and operator command routes are live; command receipts and action gating are backend-shaped |
| Screen spec | `docs/screens/PKT-002-incident-action-drawer.md` |
| BFF contract | `docs/bff/PKT-002-incident-action-drawer.md` |
| Example payload | `docs/examples/PKT-002-incident-action-drawer.json` |
| Contract-ready | `.coordination/responses/PKT-002-incident-action-drawer-contract-ready.yaml` |
| Lovable UI task | `.coordination/responses/PKT-002-incident-action-drawer-lovable-ui-task.yaml` |
| BFF-gap template | `.coordination/requests/PKT-002-incident-action-drawer-bff-gap.example.yaml` |
| UI-done template | `.coordination/requests/PKT-002-incident-action-drawer-ui-done.example.yaml` |

---

## Shared Design Rules

These rules apply across all three surfaces in this packet family.

### Degraded-State Rule

- When any `meta.surfaces` entry is `degraded` or `unavailable`, a non-dismissable banner must name the affected surface and disable the relevant CTAs.
- Incident Home must show the degradation banner even when only the kill switch status surface is degraded — the operator must always know the kill switch state is stale or unavailable.
- Incident Detail must attribute each degraded panel explicitly. Never render a generic "no data" state.

### Fallback Rule

- If `meta.surfaces.kill_switch = degraded`, Incident Home and Incident Detail must render the last known kill switch state with a staleness timestamp rather than hiding the control rail.
- If `meta.surfaces.kill_switch = unavailable`, Incident Home and Incident Detail must render "Kill switch status unavailable" and must not assume or display any kill switch state.
- The Incident Action Drawer must display the secondary control path panel when `meta.surfaces.kill_switch = degraded` or `unavailable`. The secondary control path is a read-only fallback state display plus a reduced emergency form that routes through the alternative fast path.

### Action Gating Rule

- All CTA visibility and enabled/disabled state comes from `allowedActions` fields returned by the BFF.
- The UI must not derive action eligibility locally.
- Command receipts from `POST /api/v1/operator/commands` must be rendered inline in the action drawer after each emergency command.

---

## Example Payload Gap Summary

| Screen | Example payload status | Gap |
|---|---|---|
| Incident Home | Done | None — `docs/examples/PKT-002-incident-home.json` |
| Incident Detail | Done | None — `docs/examples/PKT-002-incident-detail.json` |
| Incident Action Drawer | Done | None — `docs/examples/PKT-002-incident-action-drawer.json` |

---

## Screen-Spec Gap Summary

| Screen | Screen spec status | Gap |
|---|---|---|
| Incident Home | Done | None — `docs/screens/PKT-002-incident-home.md` |
| Incident Detail | Done | None — `docs/screens/PKT-002-incident-detail.md` |
| Incident Action Drawer | Done | None — `docs/screens/PKT-002-incident-action-drawer.md` |

---

## Lovable Readiness Matrix

| Screen | Lovable readiness | Blocker |
|---|---|---|
| Incident Home | Ready | None |
| Incident Detail | Ready | None |
| Incident Action Drawer | Ready | None |

---

## Acceptance Verification

| Acceptance criterion | Status |
|---|---|
| Incident response read and control surfaces share one packet family with explicit degraded-state and fallback rules | Done — see Shared Design Rules above; all three surfaces inherit the same degradation, fallback, and action-gating rules |
| Command receipts, action gating, and secondary control path copy are mapped to packet fields | Done — Incident Action Drawer spec defines `allowedActions`, `command_receipt`, and `secondaryControlPath` fields; BFF contract defines receipt shape from `POST /api/v1/operator/commands` |
| Screen inventory distinguishes incident home, detail, and action drawer responsibilities | Done — Incident Home owns the list and kill switch badge; Incident Detail owns the composed incident record; Incident Action Drawer owns emergency commands, receipts, and secondary control path |

---

## Wave Assignment

| Screen | Recommended wave |
|---|---|
| Incident Home | Wave 1 |
| Incident Detail | Wave 1 |
| Incident Action Drawer | Wave 1 |
