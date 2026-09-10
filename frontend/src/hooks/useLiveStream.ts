"use client";

import { useEffect, useRef, useState } from "react";
import { getAdminToken, wsUrl } from "@/lib/api";

export interface LiveEvent {
  type: string;
  node_id?: string;
  ts?: string;
  scalars?: Record<string, number>;
}

/** Subscribes to the hub's /api/v1/ui/stream and reconnects with backoff. */
export function useLiveStream(onEvent: (e: LiveEvent) => void) {
  const [connected, setConnected] = useState(false);
  const cb = useRef(onEvent);
  cb.current = onEvent;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let backoff = 1000;
    const token = getAdminToken();
    const protocols = ["meerkat42"];
    if (token) {
      const encodedToken = btoa(unescape(encodeURIComponent(token)))
        .replace(/\+/g, "-")
        .replace(/\//g, "_")
        .replace(/=+$/, "");
      protocols.push(`token.${encodedToken}`);
    }

    const connect = () => {
      ws = new WebSocket(wsUrl("/api/v1/ui/stream"), protocols);
      ws.onopen = () => {
        setConnected(true);
        backoff = 1000;
      };
      ws.onmessage = (ev) => {
        try {
          cb.current(JSON.parse(ev.data));
        } catch {
          /* ignore */
        }
      };
      ws.onclose = (event) => {
        setConnected(false);
        if (!closed && event.code !== 4401) {
          setTimeout(connect, backoff);
          backoff = Math.min(backoff * 2, 15000);
        }
      };
      ws.onerror = () => ws?.close();
    };

    connect();
    return () => {
      closed = true;
      ws?.close();
    };
  }, []);

  return { connected };
}
