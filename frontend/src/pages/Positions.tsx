import { useEffect, useRef, useState } from "react";
import { mutate } from "swr";
import { createChart, IChartApi, ISeriesApi, CandlestickData, LineData, Time } from "lightweight-charts";
import { usePositions, useKlines } from "../hooks/useApi";
import { useBinanceKlineStream } from "../hooks/useWebSocket";
import { Position } from "../types";

function fmtUSD(n: number) {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
}

function PnLBadge({ pnl }: { pnl: number }) {
  const color = pnl >= 0 ? "text-emerald-400" : "text-red-400";
  return <span className={`font-semibold ${color}`}>{pnl >= 0 ? "+" : ""}{fmtUSD(pnl)}</span>;
}

function CandleChart({ symbol, entryPrice, slPrice, tpPrice }: {
  symbol: string; entryPrice: number; slPrice?: number; tpPrice?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef     = useRef<IChartApi | null>(null);
  const candleRef    = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const { klines }   = useKlines(symbol, "5m", 150);

  // Create chart once
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: { background: { color: "#111827" }, textColor: "#9ca3af" },
      grid:   { vertLines: { color: "#1f2937" }, horzLines: { color: "#1f2937" } },
      rightPriceScale: { borderColor: "#374151" },
      timeScale:       { borderColor: "#374151", timeVisible: true },
      crosshair: { mode: 1 },
      width:  containerRef.current.clientWidth,
      height: 320,
    });
    const series = chart.addCandlestickSeries({
      upColor:   "#10b981", downColor: "#ef4444",
      borderUpColor: "#10b981", borderDownColor: "#ef4444",
      wickUpColor:   "#10b981", wickDownColor:   "#ef4444",
    });
    chartRef.current  = chart;
    candleRef.current = series;

    // Price lines
    if (entryPrice) series.createPriceLine({ price: entryPrice, color: "#6b7280", lineWidth: 1, lineStyle: 2, title: "Entry" });
    if (slPrice)    series.createPriceLine({ price: slPrice,    color: "#ef4444", lineWidth: 1, lineStyle: 2, title: "SL" });
    if (tpPrice)    series.createPriceLine({ price: tpPrice,    color: "#10b981", lineWidth: 1, lineStyle: 2, title: "TP" });

    const ro = new ResizeObserver(() => {
      if (containerRef.current) chart.resize(containerRef.current.clientWidth, 320);
    });
    if (containerRef.current) ro.observe(containerRef.current);

    return () => { ro.disconnect(); chart.remove(); };
  }, [symbol, entryPrice, slPrice, tpPrice]);

  // Load historical klines
  useEffect(() => {
    if (!candleRef.current || !klines.length) return;
    candleRef.current.setData(klines as CandlestickData<Time>[]);
    chartRef.current?.timeScale().fitContent();
  }, [klines]);

  // Live updates from Binance WS
  useBinanceKlineStream(symbol, (c) => {
    candleRef.current?.update({ ...c, time: c.time as Time });
  });

  return <div ref={containerRef} className="w-full rounded-lg overflow-hidden" />;
}

type ActionKey = `${string}:close` | `${string}:breakeven` | `${string}:invert` | "close_all";

function PositionRow({ pos, selected, onSelect, onAction, loadingKey }: {
  pos: Position;
  selected: boolean;
  onSelect: () => void;
  onAction: (symbol: string, action: "close" | "breakeven" | "invert") => void;
  loadingKey: ActionKey | null;
}) {
  const pnlPct = (pos.unrealized_pnl / (pos.entry_price * pos.qty)) * 100;
  const isLoading = (action: string) => loadingKey === `${pos.symbol}:${action}`;

  return (
    <tr
      onClick={onSelect}
      className={`cursor-pointer text-sm transition-colors ${
        selected ? "bg-gray-700/50" : "hover:bg-gray-800/50"
      }`}
    >
      <td className="px-4 py-3 font-medium text-white">{pos.symbol}</td>
      <td className="px-4 py-3">
        <span className={`px-2 py-0.5 rounded text-xs font-semibold ${
          pos.side === "LONG" ? "bg-emerald-900/50 text-emerald-400" : "bg-red-900/50 text-red-400"
        }`}>{pos.side}</span>
      </td>
      <td className="px-4 py-3 text-gray-300">{pos.qty}</td>
      <td className="px-4 py-3 text-gray-300">{fmtUSD(pos.entry_price)}</td>
      <td className="px-4 py-3 text-gray-300">{fmtUSD(pos.mark_price)}</td>
      <td className="px-4 py-3">
        <div>
          <PnLBadge pnl={pos.unrealized_pnl} />
          <span className={`ml-2 text-xs ${pnlPct >= 0 ? "text-emerald-600" : "text-red-600"}`}>
            ({pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%)
          </span>
        </div>
      </td>
      <td className="px-4 py-3 text-gray-500 text-xs">{pos.leverage}x</td>
      <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-1">
          <button
            title="Fechar posição"
            disabled={loadingKey !== null}
            onClick={() => onAction(pos.symbol, "close")}
            className="px-2 py-1 rounded text-xs font-semibold bg-red-900/50 text-red-400 hover:bg-red-800/60 disabled:opacity-40 transition-colors"
          >
            {isLoading("close") ? "…" : "✕"}
          </button>
          <button
            title="Mover SL para breakeven (0a0)"
            disabled={loadingKey !== null}
            onClick={() => onAction(pos.symbol, "breakeven")}
            className="px-2 py-1 rounded text-xs font-semibold bg-yellow-900/50 text-yellow-400 hover:bg-yellow-800/60 disabled:opacity-40 transition-colors"
          >
            {isLoading("breakeven") ? "…" : "0a0"}
          </button>
          <button
            title="Inverter posição"
            disabled={loadingKey !== null}
            onClick={() => onAction(pos.symbol, "invert")}
            className="px-2 py-1 rounded text-xs font-semibold bg-blue-900/50 text-blue-400 hover:bg-blue-800/60 disabled:opacity-40 transition-colors"
          >
            {isLoading("invert") ? "…" : "↔"}
          </button>
        </div>
      </td>
    </tr>
  );
}

export default function Positions() {
  const { positions, isLoading } = usePositions();
  const [selected, setSelected] = useState<string | null>(null);
  const [loadingKey, setLoadingKey] = useState<ActionKey | null>(null);
  const [feedback, setFeedback] = useState<{ ok: boolean; msg: string } | null>(null);

  const selectedPos = positions.find((p) => p.symbol === selected) ?? positions[0] ?? null;

  useEffect(() => {
    if (positions.length && !selected) setSelected(positions[0].symbol);
  }, [positions]);

  async function callAction(url: string, key: ActionKey, confirmMsg: string) {
    if (!window.confirm(confirmMsg)) return;
    setLoadingKey(key);
    setFeedback(null);
    try {
      const res = await fetch(url, { method: "POST" });
      const data = await res.json();
      if (data.ok === false) {
        setFeedback({ ok: false, msg: data.error ?? "Erro desconhecido" });
      } else {
        setFeedback({ ok: true, msg: "Executado com sucesso." });
        mutate("/api/positions");
      }
    } catch (e) {
      setFeedback({ ok: false, msg: String(e) });
    } finally {
      setLoadingKey(null);
    }
  }

  function handleAction(symbol: string, action: "close" | "breakeven" | "invert") {
    const msgs: Record<string, string> = {
      close:     `Fechar posição ${symbol}?`,
      breakeven: `Mover SL para breakeven em ${symbol}?`,
      invert:    `Inverter posição ${symbol} (fechar e abrir sentido oposto)?`,
    };
    callAction(`/api/positions/${symbol}/${action}`, `${symbol}:${action}` as ActionKey, msgs[action]);
  }

  function handleCloseAll() {
    callAction("/api/positions/close_all", "close_all", `Fechar TODAS as ${positions.length} posições abertas?`);
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Open Positions</h1>
        {positions.length > 0 && (
          <button
            disabled={loadingKey !== null}
            onClick={handleCloseAll}
            className="px-3 py-1.5 rounded text-sm font-semibold bg-red-700 hover:bg-red-600 text-white disabled:opacity-40 transition-colors"
          >
            {loadingKey === "close_all" ? "Fechando…" : "⚠ Fechar Todas"}
          </button>
        )}
      </div>

      {feedback && (
        <div className={`px-4 py-2 rounded text-sm font-medium ${
          feedback.ok ? "bg-emerald-900/40 text-emerald-400" : "bg-red-900/40 text-red-400"
        }`}>
          {feedback.ok ? "✓" : "✗"} {feedback.msg}
        </div>
      )}

      {/* Table */}
      <div className="bg-gray-800 border border-gray-700 rounded-xl overflow-hidden">
        {isLoading ? (
          <div className="p-8 text-center text-gray-500 text-sm">Loading positions…</div>
        ) : positions.length === 0 ? (
          <div className="p-8 text-center text-gray-500 text-sm">No open positions</div>
        ) : (
          <table className="w-full">
            <thead>
              <tr className="text-left text-xs text-gray-500 uppercase tracking-wider border-b border-gray-700">
                {["Symbol", "Side", "Qty", "Entry", "Mark", "Unrealized P&L", "Leverage", "Actions"].map((h) => (
                  <th key={h} className="px-4 py-3">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-700/50">
              {positions.map((p) => (
                <PositionRow
                  key={p.symbol}
                  pos={p}
                  selected={selectedPos?.symbol === p.symbol}
                  onSelect={() => setSelected(p.symbol)}
                  onAction={handleAction}
                  loadingKey={loadingKey}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Chart for selected position */}
      {selectedPos && (
        <div className="bg-gray-800 border border-gray-700 rounded-xl p-5">
          <p className="text-sm font-semibold text-gray-300 mb-4">
            {selectedPos.symbol} — 5m Chart (live)
          </p>
          <CandleChart
            symbol={selectedPos.symbol}
            entryPrice={selectedPos.entry_price}
          />
        </div>
      )}
    </div>
  );
}
