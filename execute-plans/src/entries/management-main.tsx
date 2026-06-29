import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { LoopTruthPanel } from "@/management/components/loop-truth";
import { LiveEvidenceManifestPanel } from "@/management/components/live-evidence";
import { OodaPacketDrawer } from "@/management/components/ooda";

const root = document.getElementById("root");
if (!root) throw new Error("Root element #root not found");

function ManagementApp() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  return (
    <div className="min-h-screen bg-background p-4 text-foreground" data-app={import.meta.env.VITE_APP_KIND}>
      <div className="mx-auto flex max-w-6xl flex-col gap-4">
        <LiveEvidenceManifestPanel />
        <LoopTruthPanel />
      </div>
      <OodaPacketDrawer open={drawerOpen} onOpenChange={setDrawerOpen} />
    </div>
  );
}

createRoot(root).render(
  <StrictMode>
    <ManagementApp />
  </StrictMode>,
);
