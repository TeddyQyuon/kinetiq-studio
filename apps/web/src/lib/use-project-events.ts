"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { StudioEvent } from "@kinetiq/contracts";
import { API_BASE } from "./api-client";
import { STUDIO_SSE_EVENT_TYPES } from "./live-events";

export type SseConnectionStatus =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "demo"
  | "closed";

export interface UseProjectEventsOptions {
  projectId: string | null | undefined;
  enabled?: boolean;
  onEvent?: (event: StudioEvent) => void;
  maxReconnectDelayMs?: number;
}

export interface UseProjectEventsResult {
  status: SseConnectionStatus;
  lastEventId: number;
  lastEvent: StudioEvent | null;
  reconnectCount: number;
  reconnectNow: () => void;
}

function getStorageKey(projectId: string): string {
  return `kinetiq:sse:last_event_id:${projectId}`;
}

function readStoredEventId(projectId: string): number {
  if (typeof window === "undefined") return 0;
  try {
    const raw = window.sessionStorage.getItem(getStorageKey(projectId));
    if (!raw) return 0;
    const parsed = Number.parseInt(raw, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
  } catch {
    return 0;
  }
}

function writeStoredEventId(projectId: string, id: number): void {
  if (typeof window === "undefined" || id <= 0) return;
  try {
    window.sessionStorage.setItem(getStorageKey(projectId), String(id));
  } catch {
    // Ignore storage quota or private-mode errors
  }
}

export function useProjectEvents({
  projectId,
  enabled = true,
  onEvent,
  maxReconnectDelayMs = 15000,
}: UseProjectEventsOptions): UseProjectEventsResult {
  const [status, setStatus] = useState<SseConnectionStatus>(enabled ? "idle" : "demo");
  const [lastEventId, setLastEventId] = useState<number>(0);
  const [lastEvent, setLastEvent] = useState<StudioEvent | null>(null);
  const [reconnectCount, setReconnectCount] = useState<number>(0);
  const [epoch, setEpoch] = useState<number>(0);

  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const lastIdRef = useRef<number>(0);

  const reconnectNow = useCallback(() => {
    setEpoch((prev) => prev + 1);
  }, []);

  useEffect(() => {
    if (!projectId) {
      setStatus("idle");
      return;
    }

    if (!enabled) {
      setStatus("demo");
      return;
    }

    if (typeof window === "undefined" || typeof window.EventSource === "undefined") {
      setStatus("closed");
      return;
    }

    let disposed = false;
    let currentSource: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let backoffAttempt = 0;

    const initialId = readStoredEventId(projectId);
    lastIdRef.current = initialId;
    setLastEventId(initialId);

    const handleIncoming = (rawEvent: MessageEvent<string>, fallbackType: string) => {
      if (disposed || !rawEvent.data) return;
      try {
        const parsed = JSON.parse(rawEvent.data) as Partial<StudioEvent>;
        const rawId = rawEvent.lastEventId
          ? Number.parseInt(rawEvent.lastEventId, 10)
          : typeof parsed.id === "number"
            ? parsed.id
            : 0;
        const eventId = Number.isFinite(rawId) ? rawId : 0;

        if (eventId > 0 && eventId <= lastIdRef.current) {
          return;
        }

        const normalized: StudioEvent = {
          id: eventId || lastIdRef.current + 1,
          project_id: parsed.project_id ?? projectId,
          event_type: parsed.event_type || fallbackType || "message",
          payload: parsed.payload && typeof parsed.payload === "object" ? parsed.payload : {},
          created_at: parsed.created_at || new Date().toISOString(),
        };

        if (eventId > 0) {
          lastIdRef.current = eventId;
          setLastEventId(eventId);
          writeStoredEventId(projectId, eventId);
        }

        setLastEvent(normalized);
        onEventRef.current?.(normalized);
      } catch {
        // Ignore malformed SSE payloads
      }
    };

    const connect = () => {
      if (disposed) return;
      setStatus(backoffAttempt > 0 ? "reconnecting" : "connecting");

      const params = new URLSearchParams();
      params.set("project_id", projectId);
      if (lastIdRef.current > 0) {
        params.set("last_event_id", String(lastIdRef.current));
      }

      const url = `${API_BASE}/v1/events?${params.toString()}`;
      const es = new EventSource(url);
      currentSource = es;

      const namedListeners: Array<{ type: string; fn: EventListener }> = [];

      es.onopen = () => {
        if (disposed) return;
        backoffAttempt = 0;
        setStatus("connected");
      };

      es.onmessage = (ev) => {
        handleIncoming(ev, "message");
      };

      for (const eventType of STUDIO_SSE_EVENT_TYPES) {
        const listener: EventListener = (ev) => {
          handleIncoming(ev as MessageEvent<string>, eventType);
        };
        es.addEventListener(eventType, listener);
        namedListeners.push({ type: eventType, fn: listener });
      }

      es.onerror = () => {
        if (disposed) return;
        for (const { type, fn } of namedListeners) {
          es.removeEventListener(type, fn);
        }
        es.close();
        if (currentSource === es) {
          currentSource = null;
        }

        backoffAttempt += 1;
        setReconnectCount((c) => c + 1);
        setStatus("reconnecting");

        const delay = Math.min(maxReconnectDelayMs, 1000 * 2 ** (backoffAttempt - 1));
        reconnectTimer = setTimeout(() => {
          reconnectTimer = null;
          connect();
        }, delay);
      };
    };

    connect();

    return () => {
      disposed = true;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (currentSource) {
        currentSource.close();
        currentSource = null;
      }
    };
  }, [projectId, enabled, maxReconnectDelayMs, epoch]);

  return {
    status,
    lastEventId,
    lastEvent,
    reconnectCount,
    reconnectNow,
  };
}