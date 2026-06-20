import React from "react";

export interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  cta?: { label: string; onClick: () => void };
}

export function EmptyState({ icon, title, description, cta }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-8 text-center">
      {icon && <div className="text-muted-foreground">{icon}</div>}
      <h3 className="text-sm font-medium">{title}</h3>
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
      {cta && (
        <button
          className="mt-2 text-xs underline"
          type="button"
          onClick={cta.onClick}
        >
          {cta.label}
        </button>
      )}
    </div>
  );
}
