import React, { useState } from "react";

export type AgoraTab = "trading-room" | "strategy-workshop" | "strategy-performance";

interface TradingDeskLayoutProps {
  activeTab: AgoraTab;
  onTabChange: (tab: AgoraTab) => void;
  children?: React.ReactNode;
  workshopId?: string;
}

const TABS: Array<{ id: AgoraTab; label: string }> = [
  { id: "trading-room", label: "Trading Room" },
  { id: "strategy-workshop", label: "Strategy Workshop" },
  { id: "strategy-performance", label: "Strategy Performance" },
];

export function TradingDeskLayout({ activeTab, onTabChange, children }: TradingDeskLayoutProps): JSX.Element {
  const [servantOpen, setServantOpen] = useState(false);

  return (
    <div data-testid="trading-desk-shell" style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <div data-testid="trading-desk-command-bar" style={{ display: "flex", alignItems: "center", padding: "0 16px", borderBottom: "1px solid #e2e8f0" }}>
        <span style={{ fontWeight: 600, marginRight: "auto" }}>Agora</span>
        <button
          aria-label="servant drawer"
          onClick={() => setServantOpen((v) => !v)}
          style={{ marginLeft: 8 }}
        >
          Servant
        </button>
      </div>

      <div data-testid="trading-desk-tab-bar" role="tablist" style={{ display: "flex", borderBottom: "1px solid #e2e8f0" }}>
        {TABS.map((tab) => (
          <button
            key={tab.id}
            role="tab"
            aria-current={activeTab === tab.id ? "page" : undefined}
            onClick={() => onTabChange(tab.id)}
            style={{ padding: "8px 16px", background: "none", border: "none", cursor: "pointer", fontWeight: activeTab === tab.id ? 600 : 400 }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        <div data-testid="trading-desk-main" style={{ flex: 1, overflow: "auto" }}>
          {children}
        </div>

        {servantOpen && (
          <div data-testid="trading-desk-servant-drawer" style={{ width: 320, borderLeft: "1px solid #e2e8f0", overflow: "auto" }}>
            <div style={{ padding: 16 }}>
              <strong>Servant</strong>
            </div>
          </div>
        )}
      </div>

      <div data-testid="trading-desk-bottom-strip" style={{ display: "flex", borderTop: "1px solid #e2e8f0", padding: "4px 16px", gap: 16 }}>
        <span>Jobs</span>
        <span>Shadow</span>
        <span>Journal</span>
      </div>
    </div>
  );
}
