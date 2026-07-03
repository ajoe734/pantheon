import { type ReactNode, useLayoutEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

export interface ManagementDenseTableProps {
  children: ReactNode;
  className?: string;
  minWidth?: number;
  testId?: string;
}

export function ManagementDenseTable({
  children,
  className,
  minWidth = 560,
  testId,
}: ManagementDenseTableProps) {
  const topScrollRef = useRef<HTMLDivElement | null>(null);
  const bodyScrollRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const syncingRef = useRef(false);
  const [scrollWidth, setScrollWidth] = useState(minWidth);

  useLayoutEffect(() => {
    const content = contentRef.current;
    if (!content) return;

    const updateWidth = () => setScrollWidth(Math.max(minWidth, content.scrollWidth));
    updateWidth();

    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(updateWidth);
    observer.observe(content);
    return () => observer.disconnect();
  }, [minWidth]);

  useLayoutEffect(() => {
    const top = topScrollRef.current;
    const body = bodyScrollRef.current;
    if (!top || !body) return;

    const sync = (source: HTMLDivElement, target: HTMLDivElement) => {
      if (syncingRef.current) return;
      syncingRef.current = true;
      target.scrollLeft = source.scrollLeft;
      window.requestAnimationFrame(() => {
        syncingRef.current = false;
      });
    };
    const syncFromTop = () => sync(top, body);
    const syncFromBody = () => sync(body, top);
    top.addEventListener("scroll", syncFromTop, { passive: true });
    body.addEventListener("scroll", syncFromBody, { passive: true });
    return () => {
      top.removeEventListener("scroll", syncFromTop);
      body.removeEventListener("scroll", syncFromBody);
    };
  }, []);

  return (
    <div
      className={cn("relative max-w-full", className)}
      data-management-dense-table="true"
      data-pinned-horizontal-scroll="true"
      data-sticky-scrollbar="true"
      data-testid={testId}
    >
      <div
        ref={topScrollRef}
        aria-hidden="true"
        className="sticky top-0 z-10 h-3 overflow-x-auto overflow-y-hidden bg-background"
      >
        <div style={{ width: scrollWidth, height: 1 }} />
      </div>
      <div ref={bodyScrollRef} className="overflow-x-auto overscroll-x-contain">
        <div ref={contentRef} className="min-w-fit">
          {children}
        </div>
      </div>
    </div>
  );
}
