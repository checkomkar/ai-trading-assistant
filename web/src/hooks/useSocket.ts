import { useEffect, useRef, useState } from "react";
import { wsUrl } from "../api";

export type SocketStatus = "connecting" | "open" | "closed";

/**
 * WebSocket with automatic reconnect (exponential backoff, max 10s).
 * `subscription` is (re)sent on every open and whenever it changes.
 */
export function useSocket<T>(
  path: string,
  onMessage: (msg: T) => void,
  subscription?: Record<string, unknown>,
): SocketStatus {
  const [status, setStatus] = useState<SocketStatus>("connecting");
  const wsRef = useRef<WebSocket | null>(null);
  const handlerRef = useRef(onMessage);
  handlerRef.current = onMessage;
  const subRef = useRef(subscription);
  subRef.current = subscription;

  useEffect(() => {
    let closedByUs = false;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      setStatus("connecting");
      const ws = new WebSocket(wsUrl(path));
      wsRef.current = ws;
      ws.onopen = () => {
        retry = 0;
        setStatus("open");
        if (subRef.current) ws.send(JSON.stringify(subRef.current));
      };
      ws.onmessage = (ev) => {
        try {
          handlerRef.current(JSON.parse(ev.data) as T);
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        setStatus("closed");
        if (closedByUs) return;
        timer = setTimeout(connect, Math.min(10_000, 500 * 2 ** retry++));
      };
      ws.onerror = () => ws.close();
    };
    connect();
    return () => {
      closedByUs = true;
      clearTimeout(timer);
      wsRef.current?.close();
    };
  }, [path]);

  const subKey = JSON.stringify(subscription ?? null);
  useEffect(() => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN && subscription) ws.send(subKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subKey]);

  return status;
}
