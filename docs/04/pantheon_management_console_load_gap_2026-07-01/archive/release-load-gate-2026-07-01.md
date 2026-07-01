# MGMT-LOAD-006 Release Load Gate

Generated: 2026-07-01T17:38:59.319Z
Audit dir: `docs/04/pantheon_management_console_load_gap_2026-07-01/archive`
Overall: **fail** (pass=false)

## 0_dependencies

| Status | Check | Note |
|---|---|---|
| pass | Dependency MGMT-LOAD-001 is terminal or reviewer-approved. | status:done source:archive |
| pass | Dependency MGMT-LOAD-002 is terminal or reviewer-approved. | status:done source:archive |
| pass | Dependency MGMT-LOAD-003 is terminal or reviewer-approved. | status:done source:archive |
| pass | Dependency MGMT-LOAD-004 is terminal or reviewer-approved. | status:done source:archive |
| pass | Dependency MGMT-LOAD-005 is terminal or reviewer-approved. | status:done source:archive |

## 1_bundle

| Status | Check | Note |
|---|---|---|
| pass | Initial management JS gzip <= 819200 bytes. | observed:269474 budget:819200 |
| pass | Evidence route chunk gzip <= 153600 bytes. | observed:13345 budget:153600 |

## 2_route_timing

| Status | Check | Note |
|---|---|---|
| pass | Route probe did not use `networkidle` as the readiness signal. | usedNetworkidle:false |
| pass | Route probe completed without error. | no error |
| fail | First row/empty-state visible <= 2500 ms. | observed:4668 budget:2500 |

## 3_startup_requests

| Status | Check | Note |
|---|---|---|
| fail | Non-primary BFF startup requests <= 2. | observed:5 budget:2 paths:/bff/me,/bff/approvals,/bff/alerts,/bff/jobs,/bff/jobs |
| fail | Duplicate startup /bff/jobs requests <= 0. | observed:2 duplicate:1 budget:0 |

## 4_bff_fanout

| Status | Check | Note |
|---|---|---|
| fail | /health fanout p95 <= 200 ms. | observed:1328 budget:200 |
| fail | /bff/management/evidence fanout p95 <= 750 ms. | observed:1423 budget:750 |
| missing | /bff/management/shell-summary fanout p95 <= 200 ms. | observed:n/a budget:200 |
