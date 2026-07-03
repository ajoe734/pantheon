import { Compass, Route } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

import type { ManagementRouteDescriptor, ManagementRouteStatus as RouteStatus } from "./routeRegistry";

const statusTone: Record<RouteStatus, string> = {
  shell: "bg-primary/10 text-primary border-primary/30",
  "active-panel": "bg-status-success/15 text-status-success border-status-success/30",
  "planned-workflow": "bg-status-warning/15 text-status-warning border-status-warning/30",
};

const statusLabel: Record<RouteStatus, string> = {
  shell: "Shell",
  "active-panel": "Active panel",
  "planned-workflow": "Planned workflow",
};

export function ManagementRouteStatus({ route }: { route: ManagementRouteDescriptor }) {
  return (
    <section
      className="rounded-md border border-border bg-background p-3"
      data-testid="management-route-status"
      data-route-status={route.status}
      data-route-path={route.path}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Compass className="h-4 w-4 text-primary" />
            <h1 className="text-base font-semibold">Pantheon Management</h1>
            <Badge variant="outline" className={cn("whitespace-nowrap", statusTone[route.status])}>
              {statusLabel[route.status]}
            </Badge>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span className="inline-flex min-w-0 items-center gap-1">
              <Route className="h-3.5 w-3.5 flex-none" />
              <span className="font-mono">{route.path}</span>
            </span>
            <span>{route.workflow}</span>
          </div>
        </div>
        <Badge variant="outline" className="max-w-full truncate">
          {route.label}
        </Badge>
      </div>
      <p className="mt-2 text-sm text-muted-foreground">{route.summary}</p>
    </section>
  );
}
