// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, type MockInstance, vi } from "vitest";

import { useCaseEvents } from "@/lib/api/events";

/** A stand-in for the browser's EventSource that the test drives by hand. */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly listeners = new Map<string, ((e: MessageEvent<string>) => void)[]>();
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(name: string, fn: (e: MessageEvent<string>) => void) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), fn]);
  }

  close() {
    this.closed = true;
  }

  emit(name: string, id: string, data: string) {
    const event = new MessageEvent<string>(name, { data, lastEventId: id });
    for (const fn of this.listeners.get(name) ?? []) fn(event);
  }
}

let client: QueryClient;
let invalidate: MockInstance<QueryClient["invalidateQueries"]>;

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  client = new QueryClient();
  invalidate = vi.spyOn(client, "invalidateQueries");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function source(): FakeEventSource {
  const s = FakeEventSource.instances.at(-1);
  if (!s) throw new Error("no EventSource was opened");
  return s;
}

describe("useCaseEvents", () => {
  it("subscribes to the case's event stream through the BFF", () => {
    const { result } = renderHook(() => useCaseEvents("case-1"), { wrapper });
    expect(source().url).toBe("/api/cases/case-1/events");
    expect(result.current).toEqual({ events: [], connected: false });
  });

  it("tracks the connection from open, heartbeat and error", () => {
    const { result } = renderHook(() => useCaseEvents("case-1"), { wrapper });
    act(() => source().onopen?.());
    expect(result.current.connected).toBe(true);
    act(() => source().onerror?.());
    expect(result.current.connected).toBe(false);
    act(() => source().emit("heartbeat", "", ""));
    expect(result.current.connected).toBe(true);
  });

  it("collects progress events once each (a resume replays by Last-Event-ID)", () => {
    const { result } = renderHook(() => useCaseEvents("case-1"), { wrapper });
    act(() => {
      source().emit("node", "1", JSON.stringify({ node: "score" }));
      source().emit("node", "2", JSON.stringify({ node: "draft" }));
      source().emit("node", "1", JSON.stringify({ node: "score" })); // replayed after a drop
      source().emit("node", "3", "not json");
    });
    expect(result.current.events).toEqual([
      { id: "1", event: "node", data: { node: "score" } },
      { id: "2", event: "node", data: { node: "draft" } },
      { id: "3", event: "node", data: {} },
    ]);
    expect(invalidate).not.toHaveBeenCalled(); // node progress does not change the case
  });

  it("refreshes the case query on status and draft events", () => {
    const { result } = renderHook(() => useCaseEvents("case-1"), { wrapper });
    act(() => source().emit("status", "10", JSON.stringify({ status: "awaiting_approval" })));
    act(() => source().emit("draft", "11", JSON.stringify({ version: 2 })));
    expect(result.current.events.map((e) => e.event)).toEqual(["status", "draft"]);
    expect(invalidate.mock.calls.map((c) => c[0])).toEqual([
      { queryKey: ["case", "case-1"] },
      { queryKey: ["case", "case-1"] },
    ]);
  });

  it("keeps only the latest 200 events", () => {
    const { result } = renderHook(() => useCaseEvents("case-1"), { wrapper });
    act(() => {
      for (let i = 1; i <= 205; i++) source().emit("node", String(i), "{}");
    });
    expect(result.current.events).toHaveLength(200);
    expect(result.current.events[0]?.id).toBe("6");
    expect(result.current.events.at(-1)?.id).toBe("205");
  });

  it("closes the stream on unmount and reopens for another case", () => {
    const { rerender, unmount } = renderHook(({ id }) => useCaseEvents(id), {
      wrapper,
      initialProps: { id: "case-1" },
    });
    const first = source();
    rerender({ id: "case-2" });
    expect(first.closed).toBe(true);
    expect(source().url).toBe("/api/cases/case-2/events");
    unmount();
    expect(source().closed).toBe(true);
  });
});
