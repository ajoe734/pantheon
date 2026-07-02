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
          rbac_matrix: {
            status: "fail",
            label: "RBAC matrix",
            note: "missing bearer token secrets: PANTHEON_BFF_RBAC_TOKENS_JSON",
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
    expect(manifests[0].criteria).toEqual([
      {
        key: "rbac_matrix",
        label: "RBAC matrix",
        status: "fail",
        note: "missing bearer token secrets: PANTHEON_BFF_RBAC_TOKENS_JSON",
      },
      {
        key: "current_run_only",
        label: "Current-run artifact scope",
        status: "pass",
        note: "4 artifact file(s); current-run scope only",
      },
    ]);
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
    expect(within(manifest).getByText("RBAC matrix")).toBeTruthy();
    expect(within(manifest).getByText("missing bearer token secrets: PANTHEON_BFF_RBAC_TOKENS_JSON")).toBeTruthy();
    expect(within(manifest).getByText("Current-run artifact scope")).toBeTruthy();
    expect(within(manifest).getByText("4 artifact file(s); current-run scope only")).toBeTruthy();
    expect(within(manifest).getAllByText("current-run")).toHaveLength(4);
    expect(within(manifest).getAllByText("clean")).toHaveLength(4);
  });
});
