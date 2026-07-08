import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { ManagementAiOpsPanel } from "@/management/components/ai-ops";
import { ManagementDecisionWorkbenchPanel } from "@/management/components/decision-workbench";
import { LoopTruthPanel } from "@/management/components/loop-truth";
import { LiveEvidenceManifestPanel } from "@/management/components/live-evidence";
import { OodaPacketPanel } from "@/management/components/ooda";
import { ManagementPerformanceReviewPanel } from "@/management/components/performance-review";
import { ManagementPromotionAllocationPanel } from "@/management/components/promotion-allocation";
import { ManagementReadinessSuitePanel } from "@/management/components/readiness-suite";
import { ManagementRouteStatus } from "@/management/shell/ManagementRouteStatus";
import { describeManagementRoute } from "@/management/shell/routeRegistry";

const root = document.getElementById("root");
if (!root) throw new Error("Root element #root not found");

function currentManagementLocation(): string {
  return `${window.location.pathname}${window.location.search}`;
}

function ManagementApp() {
  const [locationKey, setLocationKey] = useState(currentManagementLocation);
  const route = describeManagementRoute(locationKey);

  useEffect(() => {
    const handlePopState = () => setLocationKey(currentManagementLocation());
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  useEffect(() => {
    if (!route.redirectTo || currentManagementLocation() === route.redirectTo) return;
    window.history.replaceState({}, "", route.redirectTo);
    setLocationKey(route.redirectTo);
  }, [route.redirectTo]);

  const showEvidence = route.panel === "shell" || route.panel === "evidence";
  const showLoopTruth = route.panel === "shell" || route.panel === "loop-truth";
  const showOoda = route.panel === "ooda";
  const showAiOps = route.panel === "ai-ops";
  const showReadiness = route.panel === "readiness-suite";
  const showDecisionWorkbench = route.panel === "decision-workbench";
  const showPerformanceReview = route.panel === "performance-review";
  const showPromotionAllocation = route.panel === "promotion-allocation";
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
        {showPromotionAllocation ? <ManagementPromotionAllocationPanel /> : null}
      </div>
    </div>
  );
}

createRoot(root).render(
  <StrictMode>
    <ManagementApp />
  </StrictMode>,
);
