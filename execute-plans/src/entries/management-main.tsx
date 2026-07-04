import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ManagementAiOpsPanel } from "@/management/components/ai-ops";
import { ManagementDecisionWorkbenchPanel } from "@/management/components/decision-workbench";
import { LoopTruthPanel } from "@/management/components/loop-truth";
import { LiveEvidenceManifestPanel } from "@/management/components/live-evidence";
import { OodaPacketPanel } from "@/management/components/ooda";
import { ManagementPerformanceReviewPanel } from "@/management/components/performance-review";
import { ManagementReadinessSuitePanel } from "@/management/components/readiness-suite";
import { ManagementRouteStatus } from "@/management/shell/ManagementRouteStatus";
import { describeManagementRoute } from "@/management/shell/routeRegistry";

const root = document.getElementById("root");
if (!root) throw new Error("Root element #root not found");

function ManagementApp() {
  const route = describeManagementRoute(window.location.pathname);
  const showEvidence = route.panel === "shell" || route.panel === "evidence";
  const showLoopTruth = route.panel === "shell" || route.panel === "loop-truth";
  const showOoda = route.panel === "ooda";
  const showAiOps = route.panel === "ai-ops";
  const showReadiness = route.panel === "readiness-suite";
  const showDecisionWorkbench = route.panel === "decision-workbench";
  const showPerformanceReview = route.panel === "performance-review";
  return (
    <div className="min-h-screen bg-background p-4 text-foreground" data-app={import.meta.env.VITE_APP_KIND}>
      <div className="mx-auto flex max-w-6xl flex-col gap-4">
        <ManagementRouteStatus route={route} />
        {showEvidence ? <LiveEvidenceManifestPanel /> : null}
        {showLoopTruth ? <LoopTruthPanel /> : null}
        {showOoda ? <OodaPacketPanel /> : null}
        {showAiOps ? <ManagementAiOpsPanel /> : null}
        {showReadiness ? <ManagementReadinessSuitePanel /> : null}
        {showDecisionWorkbench ? <ManagementDecisionWorkbenchPanel /> : null}
        {showPerformanceReview ? <ManagementPerformanceReviewPanel /> : null}
      </div>
    </div>
  );
}

createRoot(root).render(
  <StrictMode>
    <ManagementApp />
  </StrictMode>,
);
