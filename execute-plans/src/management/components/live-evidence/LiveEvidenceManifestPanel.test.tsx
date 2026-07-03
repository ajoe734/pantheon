import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { managementClient } from "@/lib/bff/client";
import type { ManagementEvidenceResponse } from "@/lib/bff-v1/management";
import {
  collectLiveEvidenceManifests,
  LiveEvidenceManifestPanel,
} from "./LiveEvidenceManifestPanel";

const response: ManagementEvidenceResponse = {
  data: {
    id: "management-evidence",
    items: [
      {
        id: "BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY",
        ref_id: "BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY",
        title: "Strict BFF live evidence artifact verifier",
        source_type: "workflow_artifact",
        link_type: "current_run_evidence",
        credibility: { tier: "primary", verified: true },
        redacted: false,
        overall: "fail",
        criteria: {
          secret_preflight: {
            status: "fail",
            label: "Secret preflight",
            note: "missing bearer token secrets: PANTHEON_BFF_RBAC_TOKENS_JSON",
          },
          rbac_matrix: {
            status: "pass",
            label: "RBAC matrix",
            note: "strict:true bearer:true rbac:56/56 matrixCoverage:56/56 detailLinks:56/56 providedCases:7/7 distinctBearers:7/7 readDeniedEnvelopeProofs:9/9 writeSideEffectProofs:32/32 writeReadbackProofs:12/12 writeDeniedEnvelopeProofs:16/16 writeMarkerLinks:32/32",
          },
          dry_run_no_side_effects: {
            status: "pass",
            label: "Dry-run BffErrorEnvelope",
            note: "strict:true dryRun:7/7 familyCoverage:7/7 invalidEnvelope:true readbackLinked:true sideEffectProofs:7/7 sideEffects:none",
          },
          approval_race: {
            status: "pass",
            label: "Approval race",
            note: "strict:true bounded:true accepted:1 safeErrors:1 safeErrorEnvelope:1/1 results:2/2 targetLinks:2/2 duplicateWinners:false tokenPair:true tokenPairDistinct:true",
          },
          two_man_race: {
            status: "pass",
            label: "Two-man sign race",
            note: "strict:true operatorScoped:true accepted:2 replayed:0 commandIds:2/2 detailAccepted:2/2 detailReplayed:0/0 detailCommandIds:2/2 results:2/2 targetLinks:2/2 signatureLinks:2/2 tokenPair:true tokenPairDistinct:true",
          },
          sse_replay: {
            status: "pass",
            label: "SSE reconnect replay",
            note: "strict:true soak:75s heartbeat:2/2 reconnect:7/7 attemptDetails:true attemptLineage:true observed:7/7 observedSequence:true duplicates:0 missingReplay:0",
          },
          current_run_only: {
            status: "pass",
            label: "Current-run artifact scope",
            note: "4 artifact file(s); current-run scope only",
          },
        },
        artifact_manifest: {
          file_count: 4,
          total_bytes: 39730,
          limits: {
            max_files: 32,
            max_total_bytes: 8388608,
            max_file_bytes: 4194304,
          },
          files: [
            {
              path: "BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY.json",
              bytes: 0,
              current_run_allowed: true,
              forbidden_audit_scope: false,
              oversized: false,
            },
            {
              path: "BFF-LIVE-EVIDENCE-PREFLIGHT.json",
              bytes: 5454,
              current_run_allowed: true,
              forbidden_audit_scope: false,
              oversized: false,
            },
            {
              path: "release-gate-summary.json",
              bytes: 16514,
              current_run_allowed: true,
              forbidden_audit_scope: false,
              oversized: false,
            },
            {
              path: "release-gate-summary.md",
              bytes: 13710,
              current_run_allowed: true,
              forbidden_audit_scope: false,
              oversized: false,
            },
          ],
        },
      },
    ],
    summary: {
      total_evidence: 1,
      returned_evidence: 1,
      visible_evidence: 1,
      redacted_evidence: 0,
      verified_evidence: 1,
      by_source_type: { workflow_artifact: 1 },
      by_link_type: { current_run_evidence: 1 },
      by_credibility_tier: { primary: 1 },
    },
    facets: {
      source_types: { workflow_artifact: 1 },
      link_types: { current_run_evidence: 1 },
      credibility_tiers: { primary: 1 },
    },
  },
  page_info: { total: 1, page_size: 25, next_page_token: null },
  meta: {
    redacted_evidence_count: 0,
    surfaces: {
      management_evidence: { status: "ok", source: "bff_composed" },
    },
  },
};

describe("LiveEvidenceManifestPanel", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("normalizes strict verifier artifact manifests for management display", () => {
    const manifests = collectLiveEvidenceManifests(response);

    expect(manifests).toHaveLength(1);
    expect(manifests[0]).toMatchObject({
      id: "BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY",
      status: "fail",
      fileCount: 4,
      totalBytes: 39730,
      maxFiles: 32,
      maxTotalBytes: 8388608,
      maxFileBytes: 4194304,
    });
    expect(manifests[0].files.map((file) => file.path)).toEqual([
      "BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY.json",
      "BFF-LIVE-EVIDENCE-PREFLIGHT.json",
      "release-gate-summary.json",
      "release-gate-summary.md",
    ]);
    expect(manifests[0].criteria.map((criterion) => criterion.key)).toEqual([
      "secret_preflight",
      "rbac_matrix",
      "dry_run_no_side_effects",
      "approval_race",
      "two_man_race",
      "sse_replay",
      "current_run_only",
    ]);
    expect(manifests[0].criteria[0]).toEqual({
      key: "secret_preflight",
      label: "Secret preflight",
      status: "fail",
      note: "missing bearer token secrets: PANTHEON_BFF_RBAC_TOKENS_JSON",
    });
    expect(manifests[0].criteria[1]).toMatchObject({
      key: "rbac_matrix",
      label: "RBAC matrix",
      status: "pass",
    });
  });

  it("renders manifest file counts, byte limits, current-run scope, and file paths", async () => {
    const listSpy = vi.spyOn(managementClient.evidenceExplorer, "list").mockResolvedValue(response);

    render(<LiveEvidenceManifestPanel />);

    await waitFor(() => expect(listSpy).toHaveBeenCalledWith({ page_size: 25 }));
    await screen.findByText("BFF Live Evidence");
    expect(screen.getByText("Source: bff_composed")).toBeTruthy();
    expect(screen.getByText("Manifests: 1")).toBeTruthy();
    expect(screen.getByText("Evidence: 1/1")).toBeTruthy();

    const manifest = screen.getByTestId("live-evidence-manifest-BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY");
    expect(within(manifest).getByText("Strict BFF live evidence artifact verifier")).toBeTruthy();
    expect(within(manifest).getByText("Files: 4/32")).toBeTruthy();
    expect(within(manifest).getByText("Total: 38.8 KB")).toBeTruthy();
    expect(within(manifest).getByText("Limit: 8.00 MB")).toBeTruthy();
    expect(within(manifest).getByTestId("live-evidence-current-run-BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY").textContent).toContain("Current-run 4/4");
    expect(within(manifest).getByText("BFF-LIVE-EVIDENCE-PREFLIGHT.json")).toBeTruthy();
    expect(within(manifest).getByText("release-gate-summary.json")).toBeTruthy();
    const filesTable = screen.getByTestId("live-evidence-files-BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY");
    const criteriaTable = screen.getByTestId("live-evidence-criteria-BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY");
    expect(filesTable.getAttribute("data-management-dense-table")).toBe("true");
    expect(filesTable.getAttribute("data-pinned-horizontal-scroll")).toBe("true");
    expect(criteriaTable.getAttribute("data-management-dense-table")).toBe("true");
    expect(within(manifest).getByText("Secret preflight")).toBeTruthy();
    expect(within(manifest).getByText("missing bearer token secrets: PANTHEON_BFF_RBAC_TOKENS_JSON")).toBeTruthy();
    expect(within(manifest).getByText("RBAC matrix")).toBeTruthy();
    expect(within(manifest).getByText("Dry-run BffErrorEnvelope")).toBeTruthy();
    expect(within(manifest).getByText("Approval race")).toBeTruthy();
    expect(within(manifest).getByText("Two-man sign race")).toBeTruthy();
    expect(within(manifest).getByText("SSE reconnect replay")).toBeTruthy();
    expect(within(manifest).getByTestId("live-evidence-token-BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY-rbac_matrix-bearer").textContent).toBe("bearer:true");
    expect(within(manifest).getByTestId("live-evidence-token-BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY-rbac_matrix-readDeniedEnvelopeProofs").textContent).toBe("readDeniedEnvelopeProofs:9/9");
    expect(within(manifest).getByTestId("live-evidence-token-BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY-rbac_matrix-writeDeniedEnvelopeProofs").textContent).toBe("writeDeniedEnvelopeProofs:16/16");
    expect(within(manifest).getByTestId("live-evidence-token-BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY-dry_run_no_side_effects-invalidEnvelope").textContent).toBe("invalidEnvelope:true");
    expect(within(manifest).getByTestId("live-evidence-token-BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY-dry_run_no_side_effects-sideEffects").textContent).toBe("sideEffects:none");
    expect(within(manifest).getByTestId("live-evidence-token-BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY-approval_race-duplicateWinners").textContent).toBe("duplicateWinners:false");
    expect(within(manifest).getByTestId("live-evidence-token-BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY-two_man_race-operatorScoped").textContent).toBe("operatorScoped:true");
    expect(within(manifest).getByTestId("live-evidence-token-BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY-sse_replay-reconnect").textContent).toBe("reconnect:7/7");
    expect(within(manifest).getByTestId("live-evidence-token-BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY-sse_replay-duplicates").textContent).toBe("duplicates:0");
    expect(within(manifest).getByText("Current-run artifact scope")).toBeTruthy();
    expect(within(manifest).getByText("4 artifact file(s); current-run scope only")).toBeTruthy();
    expect(within(manifest).getAllByText("current-run")).toHaveLength(4);
    expect(within(manifest).getAllByText("clean")).toHaveLength(4);
  });
});
