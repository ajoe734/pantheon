# BFF-LUV-GAP-007 Sidecar Handoff Packet

## Task: Reconcile extended Agora and FULL-spec routes

**Task ID:** BFF-LUV-GAP-007
**Owner:** Codex
**Reviewer:** Codex2
**Phase:** BFF Execute-Plans Contract Gap 2026-05-08
**Status:** todo

## Summary

This sidecar task involves preparing a BFF and frontend handoff packet for the parent task BFF-LUV-GAP-007. The packet should document the BFF query gaps, operator journey, and any relevant frontend considerations, without altering the canonical truth of the system.

## BFF Query Gaps

Based on the parent task summary ("整理 FULL spec 與長尾 Agora routes，實作 active source refs 並標記歷史 routes 的 disposition。"), potential BFF query gaps might include:
- Missing endpoints for specific Agora routes as defined in the FULL spec.
- Inconsistent or missing data fields for existing Agora routes.
- Lack of clear disposition statuses for historical routes.

## Operator Journey

The operator journey for this task would involve:
1.  Reviewing the FULL specification for Agora routes.
2.  Identifying routes that are extended or new.
3.  Ensuring these routes are correctly exposed through the BFF.
4.  Verifying that historical route dispositions (e.g., deprecated, superseded) are correctly managed and exposed.
5.  Testing the reconciliation of these routes via the BFF.

## Frontend Considerations

Frontend teams might need to be aware of:
- Any new or modified endpoints they need to consume.
- Changes in data structures or response formats from the BFF for Agora routes.
- How route dispositions are presented to users (e.g., UI indicators for deprecated routes).
- Potential impact on dashboards or user interfaces that rely on Agora route data.

## Support Artifacts

- This document serves as the primary support artifact.
- Links to relevant design documents for Agora routes and the FULL spec would be beneficial if available.
- Details on any placeholder implementations or known limitations.

## Recommendation for Review

This handoff packet is prepared to facilitate the review of task BFF-LUV-GAP-007. Please review the identified gaps, operator journey, and frontend considerations. Ensure that the proposed approach for reconciliation and disposition management is clearly documented and actionable for implementation.

**Note:** This is a support artifact and does not alter the canonical truth of the system. Implementation details should be managed in the parent task's artifacts.
