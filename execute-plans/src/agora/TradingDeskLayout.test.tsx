import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TradingDeskLayout } from "./TradingDeskLayout";

afterEach(cleanup);

describe("TradingDeskLayout", () => {
  it("renders the command bar and tab bar", () => {
    render(<TradingDeskLayout activeTab="trading-room" onTabChange={() => {}} />);
    expect(screen.getByTestId("trading-desk-command-bar")).toBeDefined();
    expect(screen.getByTestId("trading-desk-tab-bar")).toBeDefined();
  });

  it("renders exactly three tab buttons", () => {
    render(<TradingDeskLayout activeTab="trading-room" onTabChange={() => {}} />);
    const tabBar = screen.getByTestId("trading-desk-tab-bar");
    const buttons = tabBar.querySelectorAll("button");
    expect(buttons.length).toBe(3);
  });

  it("calls onTabChange with strategy-workshop when that tab is clicked", () => {
    const onTabChange = vi.fn();
    render(<TradingDeskLayout activeTab="trading-room" onTabChange={onTabChange} />);
    const tabBar = screen.getByTestId("trading-desk-tab-bar");
    const tabs = Array.from(tabBar.querySelectorAll("button"));
    const workshopTab = tabs.find((t) => t.textContent?.includes("Strategy Workshop"));
    if (!workshopTab) throw new Error("Strategy Workshop tab not found");
    fireEvent.click(workshopTab);
    expect(onTabChange).toHaveBeenCalledWith("strategy-workshop");
  });

  it("calls onTabChange with strategy-performance when that tab is clicked", () => {
    const onTabChange = vi.fn();
    render(<TradingDeskLayout activeTab="trading-room" onTabChange={onTabChange} />);
    const tabBar = screen.getByTestId("trading-desk-tab-bar");
    const tabs = Array.from(tabBar.querySelectorAll("button"));
    const perfTab = tabs.find((t) => t.textContent?.includes("Performance"));
    if (!perfTab) throw new Error("Performance tab not found");
    fireEvent.click(perfTab);
    expect(onTabChange).toHaveBeenCalledWith("strategy-performance");
  });

  it("renders children in the main content area", () => {
    render(
      <TradingDeskLayout activeTab="trading-room" onTabChange={() => {}}>
        <div data-testid="test-child">Test content</div>
      </TradingDeskLayout>,
    );
    expect(screen.getByTestId("test-child")).toBeDefined();
    expect(screen.getByTestId("trading-desk-main")).toBeDefined();
  });

  it("toggles the servant drawer open and closed", () => {
    render(<TradingDeskLayout activeTab="trading-room" onTabChange={() => {}} />);
    expect(screen.queryByTestId("trading-desk-servant-drawer")).toBeNull();
    const toggleBtn = screen.getByRole("button", { name: /servant/i });
    fireEvent.click(toggleBtn);
    expect(screen.getByTestId("trading-desk-servant-drawer")).toBeDefined();
    fireEvent.click(toggleBtn);
    expect(screen.queryByTestId("trading-desk-servant-drawer")).toBeNull();
  });

  it("renders the bottom strip with Jobs, Shadow, Journal sections", () => {
    render(<TradingDeskLayout activeTab="trading-room" onTabChange={() => {}} />);
    const strip = screen.getByTestId("trading-desk-bottom-strip");
    expect(strip.textContent).toContain("Jobs");
    expect(strip.textContent).toContain("Shadow");
    expect(strip.textContent).toContain("Journal");
  });

  it("marks active tab with aria-current=page (strategy-workshop)", () => {
    render(<TradingDeskLayout activeTab="strategy-workshop" onTabChange={() => {}} />);
    const tabBar = screen.getByTestId("trading-desk-tab-bar");
    const activeEl = tabBar.querySelector('[aria-current="page"]');
    expect(activeEl).not.toBeNull();
    expect(activeEl?.textContent).toContain("Strategy Workshop");
  });

  it("marks active tab with aria-current=page (trading-room)", () => {
    render(<TradingDeskLayout activeTab="trading-room" onTabChange={() => {}} />);
    const tabBar = screen.getByTestId("trading-desk-tab-bar");
    const activeEl = tabBar.querySelector('[aria-current="page"]');
    expect(activeEl).not.toBeNull();
    expect(activeEl?.textContent).toContain("Trading Room");
  });

  it("renders the full shell container", () => {
    render(<TradingDeskLayout activeTab="trading-room" onTabChange={() => {}} />);
    expect(screen.getByTestId("trading-desk-shell")).toBeDefined();
  });
});
