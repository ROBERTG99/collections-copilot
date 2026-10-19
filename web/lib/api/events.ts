"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

export type ProgressEvent = { id: string; event: string; data: Record<string, unknown> };

/** Live case progress over SSE through the BFF (`/api/cases/{id}/events`). The browser's
 * EventSource resumes with Last-Event-ID after a drop; status events refresh the case query. */
export function useCaseEvents(caseId: string): { events: ProgressEvent[]; connected: boolean } {
  const client = useQueryClient();
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    const source = new EventSource(`/api/cases/${caseId}/events`);
    const push = (name: string) => (msg: MessageEvent<string>) => {
      let data: Record<string, unknown> = {};
      try {
        data = JSON.parse(msg.data) as Record<string, unknown>;
      } catch {
        data = {};
      }
      setEvents((prev) =>
        (prev.some((e) => e.id === msg.lastEventId)
          ? prev
          : [...prev, { id: msg.lastEventId, event: name, data }]
        ).slice(-200),
      );
      if (name === "status" || name === "draft") {
        void client.invalidateQueries({ queryKey: ["case", caseId] });
      }
    };
    for (const name of ["node", "draft", "status"]) source.addEventListener(name, push(name));
    source.addEventListener("heartbeat", () => setConnected(true));
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    return () => source.close();
  }, [caseId, client]);
  return { events, connected };
}
