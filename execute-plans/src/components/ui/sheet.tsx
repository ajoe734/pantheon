import React from "react";
import { cn } from "@/lib/utils";

export interface SheetProps {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  children?: React.ReactNode;
}

export function Sheet({ open, children }: SheetProps) {
  if (!open) return null;
  return <>{children}</>;
}

export interface SheetContentProps extends React.HTMLAttributes<HTMLDivElement> {
  side?: "left" | "right" | "top" | "bottom";
  onCloseAutoFocus?: (event: Event) => void;
}

export function SheetContent({
  className,
  children,
  side = "right",
  onCloseAutoFocus: _onCloseAutoFocus,
  ...props
}: SheetContentProps) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      className={cn(
        "fixed inset-y-0 z-50 flex flex-col bg-background p-6 shadow-xl",
        side === "right" && "right-0",
        side === "left" && "left-0",
        side === "top" && "top-0 inset-x-0",
        side === "bottom" && "bottom-0 inset-x-0",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

export function SheetHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex flex-col space-y-1.5", className)} {...props} />;
}

export function SheetTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h2 className={cn("text-lg font-semibold", className)} {...props} />;
}

export function SheetDescription({ className, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("text-sm text-muted-foreground", className)} {...props} />;
}
