"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { severityVariant } from "@/components/status-badge";
import { api, type AlertEvent } from "@/lib/api";

export function AlertsPanel({
  alerts,
  onChange,
}: {
  alerts: AlertEvent[];
  onChange: () => void;
}) {
  const firing = alerts.filter((a) => a.state === "FIRING");
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>Active alerts</CardTitle>
        <Badge variant={firing.length ? "destructive" : "secondary"}>{firing.length}</Badge>
      </CardHeader>
      <CardContent className="space-y-2">
        {alerts.length === 0 && (
          <p className="text-sm text-muted-foreground">No active alerts.</p>
        )}
        {alerts.map((a) => (
          <div
            key={a.id}
            className="flex items-start justify-between gap-3 rounded-md border p-2.5 text-sm"
          >
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <Badge variant={severityVariant(a.severity)}>{a.severity}</Badge>
                <span className="font-medium">{a.type}</span>
                {a.state === "ACKNOWLEDGED" && (
                  <Badge variant="outline">ack: {a.acknowledged_by}</Badge>
                )}
              </div>
              <p className="text-muted-foreground">{a.message}</p>
              <p className="text-xs text-muted-foreground">
                {a.node_id} · {new Date(a.started_at).toLocaleString()}
              </p>
            </div>
            {a.state === "FIRING" && (
              <Button
                size="sm"
                variant="outline"
                onClick={async () => {
                  await api.ackAlert(a.id);
                  onChange();
                }}
              >
                Ack
              </Button>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
