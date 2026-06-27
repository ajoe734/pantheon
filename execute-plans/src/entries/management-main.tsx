import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { LoopTruthPanel } from "@/management/components/loop-truth";
import { OodaPacketDrawer } from "@/management/components/ooda";

const root = document.getElementById("root");
if (!root) throw new Error("Root element #root not found");

function ManagementApp() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  return (
    <div data-app={import.meta.env.VITE_APP_KIND}>
      <LoopTruthPanel />
      <OodaPacketDrawer open={drawerOpen} onOpenChange={setDrawerOpen} />
    </div>
  );
}

createRoot(root).render(
  <StrictMode>
    <ManagementApp />
  </StrictMode>,
);
