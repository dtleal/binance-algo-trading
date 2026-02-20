import { useEffect, useState } from "react";
import { useBotStates } from "../hooks/useApi";
import { WsEvent, BotState } from "../types";

const STATE_STYLE: Record<string, string> = {
  SCANNING:    "bg-blue-900/40 text-blue-300 border border-blue-700/50",
  IN_POSITION: "bg-emerald-900/40 text-emerald-300 border border-emerald-700/50",
  COOLDOWN:    "bg-amber-900/40 text-amber-300 border border-amber-700/50",
};

const STATE_DOT: Record<string, string> = {
  SCANNING:    "bg-blue-400 animate-pulse",
  IN_POSITION: "bg-emerald-400",
  COOLDOWN:    "bg-amber-400",
};

function fmtPrice(n: number | undefined, decimals = 4) {
  if (n == null) return "—";
  return n.toFixed(decimals);
}

function BotCard({ state, liveEvents }: { state: BotState; liveEvents: WsEvent[] }) {
  // Overlay the last candle event for this bot to get freshest price
  const lastCandle = [...liveEvents]
    .filter((e) => e.type === "candle" && e.symbol === state.symbol) as Extract<WsEvent, { type: "candle" }>[];
  const live = lastCandle[0];

  const price    = live?.price    ?? state.price;
  const vwap     = live?.vwap     ?? state.vwap;
  const pnl      = live?.unrealized_pnl     ?? state.unrealized_pnl ?? 0;
  const pnlPct   = live?.unrealized_pnl_pct ?? state.unrealized_pnl_pct ?? 0;
  const botState = state.state;

  const decimals = price && price > 100 ? 2 : price && price > 1 ? 4 : 6;

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-xl p-5 space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <p className="text-base font-bold text-white">{state.symbol}</p>
          <p className="text-xs text-gray-500 capitalize">{state.strategy}</p>
        </div>
        <div className="flex items-center gap-2">
          {state.dry_run && (
            <span className="text-[10px] px-2 py-0.5 rounded bg-gray-700 text-gray-400">
              DRY RUN
            </span>
          )}
          <span className={`flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full font-medium ${STATE_STYLE[botState] ?? ""}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${STATE_DOT[botState] ?? "bg-gray-400"}`} />
            {botState}
          </span>
        </div>
      </div>

      {/* Price row */}
      <div className="grid grid-cols-3 gap-3">
        <div>
          <p className="text-[10px] text-gray-500 uppercase tracking-wider">Price</p>
          <p className="text-sm font-semibold text-white">${fmtPrice(price, decimals)}</p>
        </div>
        <div>
          <p className="text-[10px] text-gray-500 uppercase tracking-wider">VWAP</p>
          <p className="text-sm text-gray-300">${fmtPrice(vwap, decimals)}</p>
        </div>
        <div>
          <p className="text-[10px] text-gray-500 uppercase tracking-wider">EMA</p>
          <p className="text-sm text-gray-300">
            {state.ema != null ? `$${fmtPrice(state.ema, decimals)}` : "warming…"}
          </p>
        </div>
      </div>

      {/* Signal progress (SCANNING) */}
      {botState === "SCANNING" && (
        <div className="bg-gray-900/50 rounded-lg px-3 py-2 text-xs text-gray-400">
          {state.confirming ? (
            <span>
              Confirming: <span className="text-blue-300 font-medium">{state.confirm_count ?? 0}/{state.confirm_bars ?? "?"}</span> bars
            </span>
          ) : (
            <span>
              Consolidation: <span className="text-blue-300 font-medium">{state.counter ?? 0}</span> bars
              {" · "}
              Trend: <span className={state.trend === "up" ? "text-emerald-400" : "text-red-400"}>
                {state.trend ? (state.trend === "up" ? "↑ UP" : "↓ DOWN") : "—"}
              </span>
            </span>
          )}
          {" · "}
          Trades: <span className="text-white">{state.trades_today ?? 0}/{state.max_trades_per_day ?? 4}</span>
        </div>
      )}

      {/* Position info (IN_POSITION) */}
      {botState === "IN_POSITION" && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className={`text-xs font-semibold uppercase tracking-wider px-2 py-0.5 rounded
              ${state.direction === "long" ? "bg-emerald-900/40 text-emerald-400" : "bg-red-900/40 text-red-400"}`}>
              {state.direction}
            </span>
            <span className={`text-sm font-bold ${pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
              {pnl >= 0 ? "+" : ""}{pnl.toFixed(2)} USDT
              <span className="text-xs font-normal ml-1">({pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%)</span>
            </span>
          </div>
          <div className="grid grid-cols-3 gap-2 text-[10px] text-gray-500">
            <div>
              <p>Entry</p>
              <p className="text-gray-300 text-xs">${fmtPrice(state.entry_price, decimals)}</p>
            </div>
            <div>
              <p className="text-red-500">SL</p>
              <p className="text-red-400 text-xs">${fmtPrice(state.sl_price, decimals)}</p>
            </div>
            <div>
              <p className="text-emerald-500">TP</p>
              <p className="text-emerald-400 text-xs">${fmtPrice(state.tp_price, decimals)}</p>
            </div>
          </div>
        </div>
      )}

      {/* Bot Configuration */}
      {state.config && (
        <details className="bg-gray-900/50 rounded-lg">
          <summary className="px-3 py-2 text-xs text-gray-400 cursor-pointer hover:text-gray-300 select-none">
            Configuration
          </summary>
          <div className="px-3 pb-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[10px]">
            {state.config.leverage && (
              <div className="flex justify-between">
                <span className="text-gray-500">Leverage:</span>
                <span className="text-gray-300 font-medium">{state.config.leverage}x</span>
              </div>
            )}
            {state.config.tp_pct != null && (
              <div className="flex justify-between">
                <span className="text-gray-500">TP:</span>
                <span className="text-emerald-400 font-medium">{state.config.tp_pct}%</span>
              </div>
            )}
            {state.config.sl_pct != null && (
              <div className="flex justify-between">
                <span className="text-gray-500">SL:</span>
                <span className="text-red-400 font-medium">{state.config.sl_pct}%</span>
              </div>
            )}
            {state.config.pos_size_pct != null && (
              <div className="flex justify-between">
                <span className="text-gray-500">Position Size:</span>
                <span className="text-gray-300 font-medium">{(state.config.pos_size_pct * 100).toFixed(0)}%</span>
              </div>
            )}
            {state.config.capital != null && (
              <div className="flex justify-between">
                <span className="text-gray-500">Capital:</span>
                <span className="text-gray-300 font-medium">${state.config.capital.toFixed(2)}</span>
              </div>
            )}
            {state.config.per_trade != null && (
              <div className="flex justify-between">
                <span className="text-gray-500">Per Trade:</span>
                <span className="text-gray-300 font-medium">${state.config.per_trade.toFixed(2)}</span>
              </div>
            )}
            {state.config.ema_period && (
              <div className="flex justify-between">
                <span className="text-gray-500">EMA Period:</span>
                <span className="text-gray-300 font-medium">{state.config.ema_period}</span>
              </div>
            )}
            {state.config.min_bars && (
              <div className="flex justify-between">
                <span className="text-gray-500">Min Bars:</span>
                <span className="text-gray-300 font-medium">{state.config.min_bars}</span>
              </div>
            )}
            {state.config.confirm_bars && (
              <div className="flex justify-between">
                <span className="text-gray-500">Confirm Bars:</span>
                <span className="text-gray-300 font-medium">{state.config.confirm_bars}</span>
              </div>
            )}
            {state.config.vwap_prox != null && (
              <div className="flex justify-between">
                <span className="text-gray-500">VWAP Proximity:</span>
                <span className="text-gray-300 font-medium">{(state.config.vwap_prox * 100).toFixed(1)}%</span>
              </div>
            )}
            {state.config.vwap_window_days && (
              <div className="flex justify-between">
                <span className="text-gray-500">VWAP Window:</span>
                <span className="text-gray-300 font-medium">{state.config.vwap_window_days}d</span>
              </div>
            )}
            {state.config.max_trades_per_day && (
              <div className="flex justify-between">
                <span className="text-gray-500">Max Trades/Day:</span>
                <span className="text-gray-300 font-medium">{state.config.max_trades_per_day}</span>
              </div>
            )}
            {state.config.min_notional != null && (
              <div className="flex justify-between">
                <span className="text-gray-500">Min Notional:</span>
                <span className="text-gray-300 font-medium">${state.config.min_notional.toFixed(2)}</span>
              </div>
            )}
          </div>
        </details>
      )}
    </div>
  );
}

export default function Bots({ events }: { events: WsEvent[] }) {
  const { bots } = useBotStates();
  const [log, setLog] = useState<{ ts: string; msg: string; color: string }[]>([]);

  // Append signal/order/closed events to the activity log
  useEffect(() => {
    const last = events[0];
    if (!last) return;
    if (last.type === "signal") {
      setLog((prev) => [
        {
          ts:    new Date().toLocaleTimeString(),
          msg:   `${last.symbol} → SIGNAL ${last.direction.toUpperCase()} @ $${last.price}`,
          color: last.direction === "long" ? "text-emerald-400" : "text-red-400",
        },
        ...prev.slice(0, 49),
      ]);
    } else if (last.type === "order") {
      setLog((prev) => [
        {
          ts:    new Date().toLocaleTimeString(),
          msg:   `${last.symbol} → ORDER ${last.direction.toUpperCase()} ${last.qty} @ $${last.entry_price}`,
          color: last.direction === "long" ? "text-emerald-300" : "text-red-300",
        },
        ...prev.slice(0, 49),
      ]);
    } else if (last.type === "position_closed") {
      setLog((prev) => [
        { ts: new Date().toLocaleTimeString(), msg: `${last.symbol} → CLOSED (${last.reason})`, color: "text-amber-400" },
        ...prev.slice(0, 49),
      ]);
    }
  }, [events]);

  const botList = Object.values(bots);

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold text-white">Bots</h1>

      {botList.length === 0 ? (
        <div className="bg-gray-800 border border-gray-700 rounded-xl p-12 text-center">
          <p className="text-gray-500 text-sm">No bots running.</p>
          <p className="text-gray-600 text-xs mt-2">
            Start a bot with <code className="bg-gray-700 px-1.5 py-0.5 rounded">
              poetry run python -m trader serve --with-pullback axsusdt
            </code>
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {botList.map((b) => (
            <BotCard key={`${b.symbol}:${b.strategy}`} state={b} liveEvents={events} />
          ))}
        </div>
      )}

      {/* Activity log */}
      <div className="bg-gray-800 border border-gray-700 rounded-xl p-5">
        <p className="text-sm font-semibold text-gray-300 mb-3">Activity Log</p>
        <div className="space-y-1.5 max-h-48 overflow-y-auto font-mono text-xs">
          {log.length === 0 ? (
            <p className="text-gray-600">Waiting for events…</p>
          ) : (
            log.map((l, i) => (
              <div key={i} className="flex gap-3">
                <span className="text-gray-600 shrink-0">{l.ts}</span>
                <span className={l.color}>{l.msg}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
