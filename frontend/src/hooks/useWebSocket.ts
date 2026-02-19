import { useEffect, useRef, useState, useCallback } from "react";
import { WsEvent } from "../types";

const WS_URL = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/feed`;
const MAX_EVENTS = 200;
const RECONNECT_MS = 3000;

export function useFeedWebSocket() {
  const [events, setEvents] = useState<WsEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(() => {
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);
    ws.onclose = () => {
      setConnected(false);
      timerRef.current = setTimeout(connect, RECONNECT_MS);
    };
    ws.onerror = () => ws.close();
    ws.onmessage = (e) => {
      try {
        const evt = JSON.parse(e.data) as WsEvent;
        setEvents((prev) => [evt, ...prev].slice(0, MAX_EVENTS));
      } catch {}
    };
  }, []);

  useEffect(() => {
    connect();
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { events, connected };
}

/** Subscribe to a Binance futures market data stream (public, no auth). */
export function useBinanceKlineStream(
  symbol: string,
  onCandle: (c: { time: number; open: number; high: number; low: number; close: number; volume: number }) => void
) {
  const wsRef = useRef<WebSocket | null>(null);
  const cbRef = useRef(onCandle);
  cbRef.current = onCandle;

  useEffect(() => {
    if (!symbol) return;
    const url = `wss://fstream.binance.com/ws/${symbol.toLowerCase()}@kline_1m`;
    const ws = new WebSocket(url);
    wsRef.current = ws;
    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        const k = data.k;
        cbRef.current({
          time: Math.floor(k.t / 1000),
          open: parseFloat(k.o),
          high: parseFloat(k.h),
          low: parseFloat(k.l),
          close: parseFloat(k.c),
          volume: parseFloat(k.v),
        });
      } catch {}
    };
    return () => ws.close();
  }, [symbol]);
}
