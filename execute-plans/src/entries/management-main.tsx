import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { LoopTruthPanel } from "@/management/components/loop-truth";
import { LiveEvidenceManifestPanel } from "@/management/components/live-evidence";
import { OodaPacketPanel } from "@/management/components/ooda";
import { ManagementRouteStatus } from "@/management/shell/ManagementRouteStatus";
import { describeManagementRoute } from "@/management/shell/routeRegistry";

const root = document.getElementById("root");
if (!root) throw new Error("Root element #root not found");

function ManagementApp() {
  const route = describeManagementRoute(window.location.pathname);
  const showEvidence = route.panel === "shell" || route.panel === "evidence";
  const showLoopTruth = route.panel === "shell" || route.panel === "loop-truth";
  const showOoda = route.panel === "ooda";
  return (
    <div className="min-h-screen bg-background p-4 text-foreground" data-app={import.meta.env.VITE_APP_KIND}>
      <div className="mx-auto flex max-w-6xl flex-col gap-4">
        <ManagementRouteStatus route={route} />
        {showEvidence ? <LiveEvidenceManifestPanel /> : null}
        {showLoopTruth ? <LoopTruthPanel /> : null}
        {showOoda ? <OodaPacketPanel /> : null}
      </div>
    </div>
  );
}

createRoot(root).render(
  <StrictMode>
    <ManagementApp />
  </StrictMode>,
);
