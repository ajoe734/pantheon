import React, { useState } from "react";

export type AgoraTab = "trading-room" | "strategy-workshop" | "strategy-performance";

interface TradingDeskLayoutProps {
  activeTab: AgoraTab;
  onTabChange: (tab: AgoraTab) => void;
  children?: React.ReactNode;
  workshopId?: string;
}

const TABS: Array<{ id: AgoraTab; label: string }> = [
  { id: "trading-room", label: "交易操盤室" },
  { id: "strategy-workshop", label: "策略工坊" },
  { id: "strategy-performance", label: "策略執行與績效" },
];

/* ── shared dark AGORA palette ───────────────────────────────────────────── */
const C = {
  shellBg: "#111417",
  barBg: "#1a1d23",
  secondaryBg: "#171b22",
  border: "#2a2e38",
  text: "#f0ece4",
  textSecondary: "#8c96a6",
  textMuted: "#737d8e",
  amber: "#e8b750",
  amberBg: "rgba(232,183,80,0.12)",
  amberBorder: "rgba(232,183,80,0.35)",
} as const;

export function TradingDeskLayout({ activeTab, onTabChange, children }: TradingDeskLayoutProps): JSX.Element {
  const [servantOpen, setServantOpen] = useState(false);

  return (
    <div
      data-testid="trading-desk-shell"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        background: C.shellBg,
        color: C.text,
        fontFamily: "'Noto Sans TC', -apple-system, BlinkMacSystemFont, sans-serif",
      }}
    >
      {/* ── Command bar ────────────────────────────────────────────────── */}
      <div
        data-testid="trading-desk-command-bar"
        style={{
          display: "flex",
          alignItems: "center",
          height: 44,
          padding: "0 16px",
          background: C.barBg,
          borderBottom: `1px solid ${C.border}`,
          flexShrink: 0,
          gap: 12,
        }}
      >
        {/* AGORA logo */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          <div
            aria-hidden="true"
            style={{
              width: 28,
              height: 28,
              background: C.amber,
              borderRadius: 6,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: 900,
              fontSize: 14,
              color: "#1a1410",
              flexShrink: 0,
            }}
          >
            A
          </div>
          <span style={{ fontWeight: 700, fontSize: 14, letterSpacing: "0.04em", color: C.text }}>
            AGORA
          </span>
        </div>

        {/* Command input area */}
        <div
          style={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            background: "#202631",
            border: `1px solid ${C.border}`,
            borderRadius: 20,
            padding: "5px 14px",
            gap: 8,
            minWidth: 0,
            maxWidth: 520,
            margin: "0 auto",
          }}
        >
          <span style={{ color: C.amber, fontSize: 13, flexShrink: 0 }}>✦</span>
          <span style={{ fontSize: 12, color: C.textMuted, overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis" }}>
            交代助理：描述您的交易策略想法，例如「我想找有大戶建立⋯」
          </span>
        </div>

        {/* Servant drawer toggle */}
        <button
          aria-label="servant drawer"
          onClick={() => setServantOpen((v) => !v)}
          style={{
            border: `1px solid ${C.amberBorder}`,
            background: servantOpen ? C.amberBg : "transparent",
            borderRadius: 18,
            padding: "5px 14px",
            fontSize: 12,
            fontWeight: 600,
            color: C.amber,
            cursor: "pointer",
            flexShrink: 0,
            transition: "background 0.15s",
          }}
        >
          交代助理
        </button>
      </div>

      {/* ── Tab bar ────────────────────────────────────────────────────── */}
      <div
        data-testid="trading-desk-tab-bar"
        role="tablist"
        style={{
          display: "flex",
          alignItems: "stretch",
          background: C.secondaryBg,
          borderBottom: `1px solid ${C.border}`,
          flexShrink: 0,
          padding: "0 16px",
          gap: 4,
        }}
      >
        {TABS.map((tab) => {
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              role="tab"
              aria-current={isActive ? "page" : undefined}
              onClick={() => onTabChange(tab.id)}
              style={{
                padding: "10px 16px",
                background: "none",
                border: "none",
                borderBottom: isActive ? `2px solid ${C.amber}` : "2px solid transparent",
                cursor: "pointer",
                fontSize: 13,
                fontWeight: isActive ? 600 : 400,
                color: isActive ? C.amber : C.textSecondary,
                transition: "color 0.15s, border-color 0.15s",
                whiteSpace: "nowrap",
              }}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* ── Main content + optional servant drawer ──────────────────────── */}
      <div style={{ display: "flex", flex: 1, overflow: "hidden", minHeight: 0 }}>
        <div
          data-testid="trading-desk-main"
          style={{ flex: 1, overflow: "auto", minWidth: 0 }}
        >
          {children}
        </div>

        {servantOpen && (
          <div
            data-testid="trading-desk-servant-drawer"
            style={{
              width: 340,
              borderLeft: `1px solid ${C.border}`,
              background: C.secondaryBg,
              overflow: "auto",
              flexShrink: 0,
            }}
          >
            <div style={{ padding: "14px 16px", borderBottom: `1px solid ${C.border}` }}>
              <strong style={{ fontSize: 13, color: C.amber }}>交代助理</strong>
            </div>
          </div>
        )}
      </div>

      {/* ── Bottom strip ───────────────────────────────────────────────── */}
      <div
        data-testid="trading-desk-bottom-strip"
        style={{
          display: "flex",
          alignItems: "center",
          borderTop: `1px solid ${C.border}`,
          background: C.barBg,
          padding: "5px 16px",
          gap: 20,
          flexShrink: 0,
          fontSize: 11,
          color: C.textMuted,
        }}
      >
        <span>Jobs</span>
        <span>Shadow</span>
        <span>Journal</span>
      </div>
    </div>
  );
}
