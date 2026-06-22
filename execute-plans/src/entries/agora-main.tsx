import { StrictMode, useState, useCallback, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { TradingDeskLayout, type AgoraTab } from "@/agora/TradingDeskLayout";
import { StrategyWorkshopPage } from "@/agora/pages/strategy-workshop/StrategyWorkshopPage";

// Legacy routes redirect to /agora/trading-room (canonical IA per §10)
const LEGACY_PATH_PREFIXES = [
  "/agora/daily",
  "/agora/watchlist",
  "/agora/signals",
  "/agora/notebook",
];

function isLegacyOrBareAgora(pathname: string): boolean {
  if (pathname === "/agora" || pathname === "/agora/") return true;
  return LEGACY_PATH_PREFIXES.some(
    (p) => pathname === p || pathname.startsWith(p + "/"),
  );
}

function derivedTab(pathname: string): AgoraTab {
  if (pathname.startsWith("/agora/strategy-workshop")) return "strategy-workshop";
  if (pathname.startsWith("/agora/strategy-performance")) return "strategy-performance";
  return "trading-room";
}

function workshopIdFromPath(pathname: string): string | undefined {
  const m = pathname.match(/^\/agora\/strategy-workshop\/([^/?#]+)/);
  return m ? decodeURIComponent(m[1]) : undefined;
}

// Apply initial redirect before React renders so the shell starts on the right route
if (typeof window !== "undefined" && isLegacyOrBareAgora(window.location.pathname)) {
  window.history.replaceState(null, "", "/agora/trading-room");
}

function AgoraApp() {
  const [pathname, setPathname] = useState<string>(() =>
    typeof window !== "undefined" ? window.location.pathname : "/agora/trading-room",
  );

  useEffect(() => {
    const handler = () => setPathname(window.location.pathname);
    window.addEventListener("popstate", handler);
    return () => window.removeEventListener("popstate", handler);
  }, []);

  const activeTab = derivedTab(pathname);
  const workshopId = workshopIdFromPath(pathname);

  const handleTabChange = useCallback((tab: AgoraTab) => {
    const tabPaths: Record<AgoraTab, string> = {
      "trading-room": "/agora/trading-room",
      "strategy-workshop": "/agora/strategy-workshop",
      "strategy-performance": "/agora/strategy-performance",
    };
    window.history.pushState(null, "", tabPaths[tab]);
    setPathname(tabPaths[tab]);
  }, []);

  let pageContent: React.ReactNode;
  if (activeTab === "strategy-workshop") {
    pageContent = <StrategyWorkshopPage workshopId={workshopId} />;
  } else {
    // Placeholder for tabs implemented in subsequent tasks (AG-FE-TR-001, AG-FE-PERF-001)
    pageContent = (
      <div
        className="flex h-full items-center justify-center"
        data-testid={`${activeTab}-placeholder`}
      >
        <p className="text-sm text-slate-400">{activeTab}</p>
      </div>
    );
  }

  return (
    <TradingDeskLayout
      activeTab={activeTab}
      onTabChange={handleTabChange}
      workshopId={workshopId}
    >
      {pageContent}
    </TradingDeskLayout>
  );
}

const root = document.getElementById("root");
if (!root) throw new Error("Root element #root not found");

createRoot(root).render(
  <StrictMode>
    <AgoraApp />
  </StrictMode>,
);
