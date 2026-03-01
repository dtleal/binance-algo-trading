import { useEffect, useState, useMemo } from "react";
import { useBotStates, useTrades } from "../hooks/useApi";
import { WsEvent, BotState } from "../types";
import BotLogsModal from "../components/BotLogsModal";

type WinRateStat = { wins: number; total: number; rate: number };

// Champion backtest win rates from onboarding sweeps
// Format: { SYMBOL: { wr: win_rate_pct, trades: n_trades, ret: return_pct, tf: timeframe, strategy } }
const CHAMPION: Record<string, { wr: number; trades: number; ret: number; tf: string; strat: string }> = {
  AXSUSDT:      { wr: 52.3, trades: 285, ret: 40.10, tf: "1m",  strat: "MomShort"     },
  SANDUSDT:     { wr: 34.0, trades: 250, ret: 27.61, tf: "5m",  strat: "MomShort"     },
  MANAUSDT:     { wr: 52.9, trades: 295, ret: 30.54, tf: "1m",  strat: "MomShort"     },
  GALAUSDT:     { wr: 52.1, trades: 357, ret: 34.85, tf: "1m",  strat: "VWAPPullback" },
  DOGEUSDT:     { wr: 52.5, trades: 322, ret: 42.75, tf: "5m",  strat: "VWAPPullback" },
  "1000SHIBUSDT":{ wr: 53.1, trades: 354, ret: 37.51, tf: "5m",  strat: "VWAPPullback" },
  ETHUSDT:      { wr: 51.0, trades: 251, ret: 31.87, tf: "5m",  strat: "VWAPPullback" },
  SOLUSDT:      { wr: 53.3, trades: 302, ret: 28.13, tf: "1m",  strat: "MomShort"     },
  AVAXUSDT:     { wr: 50.6, trades: 246, ret: 31.12, tf: "1m",  strat: "VWAPPullback" },
  XRPUSDT:      { wr: 45.0, trades: 351, ret: 30.15, tf: "5m",  strat: "VWAPPullback" },
  XAUUSDT:      { wr: 49.1, trades:  53, ret:  7.67, tf: "1m",  strat: "VWAPPullback" },
  LTCUSDT:      { wr: 57.1, trades:1003, ret: 50.76, tf: "1m",  strat: "PDHL"         },
  LINKUSDT:     { wr: 49.8, trades: 876, ret:115.87, tf: "1m",  strat: "PDHL"         },
  BCHUSDT:      { wr: 53.8, trades: 954, ret: 68.46, tf: "5m",  strat: "PDHL"         },
  XMRUSDT:      { wr: 52.1, trades: 349, ret: 35.76, tf: "1m",  strat: "VWAPPullback" },
  APTUSDT:      { wr: 64.6, trades:  65, ret: 19.66, tf: "5m",  strat: "VWAPPullback" },
  UNIUSDT:      { wr: 43.2, trades: 287, ret: 31.71, tf: "15m", strat: "VWAPPullback" },
  "1000PEPEUSDT":{ wr: 58.1, trades: 198, ret: 38.86, tf: "5m",  strat: "VWAPPullback" },
  DASHUSDT:     { wr: 53.8, trades: 171, ret: 22.06, tf: "15m", strat: "VWAPPullback" },
  ZECUSDT:      { wr: 53.9, trades: 280, ret: 25.55, tf: "5m",  strat: "VWAPPullback" },
  KSMUSDT:      { wr: 49.6, trades: 468, ret: 31.95, tf: "1h",  strat: "ORB"          },
  AAVEUSDT:     { wr: 48.5, trades:1040, ret: 56.24, tf: "1m",  strat: "PDHL"         },
};

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

type BotFilter = "all" | "in_position" | "scanning" | "cooldown" | "traded";

function fmtPrice(n: number | undefined, decimals = 4) {
  if (n == null) return "—";
  return n.toFixed(decimals);
}

function BotCard({
  state,
  liveEvents,
  winRate,
  onClick
}: {
  state: BotState;
  liveEvents: WsEvent[];
  winRate?: WinRateStat;  // live WR from actual trades
  onClick: () => void;
}) {
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
    <div
      onClick={onClick}
      className="bg-gray-800 border border-gray-700 rounded-xl p-5 space-y-4 cursor-pointer hover:border-emerald-600 transition-colors"
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        {/* Left: symbol + strategy */}
        <div className="flex-1 min-w-0">
          <p className="text-base font-bold text-white truncate">
            {state.symbol.replace("1000", "1000 ")}
          </p>
          <span className="text-xs font-normal text-emerald-400 bg-emerald-900/30 px-2 py-0.5 rounded border border-emerald-700/50">
            {state.strategy}
          </span>
        </div>

        {/* Middle: win rate (backtest champion + live comparison) */}
        {(() => {
          const champ = CHAMPION[state.symbol];
          const wr = champ?.wr ?? null;
          const liveWr = winRate && winRate.total >= 5 ? winRate.rate : null;
          return (
            <div className="flex flex-col items-center flex-shrink-0 min-w-[60px]">
              {/* Backtest WR (primary) */}
              {wr !== null ? (
                <>
                  <p className={`text-sm font-bold leading-tight ${
                    wr >= 50 ? "text-emerald-400" : wr >= 40 ? "text-amber-400" : "text-red-400"
                  }`}>
                    {wr.toFixed(0)}%
                  </p>
                  <p className="text-[10px] text-gray-500 leading-tight">
                    BT WR
                  </p>
                </>
              ) : (
                <p className="text-sm font-bold text-gray-600 leading-tight">—</p>
              )}
              {/* Live WR (secondary, only if ≥5 trades) */}
              {liveWr !== null && (
                <p className={`text-[10px] leading-tight mt-0.5 font-medium ${
                  liveWr >= 50 ? "text-emerald-300" : liveWr >= 40 ? "text-amber-300" : "text-red-300"
                }`}>
                  {liveWr.toFixed(0)}% live
                </p>
              )}
            </div>
          );
        })()}

        {/* Right: status */}
        <div className="flex flex-col items-end gap-1 flex-shrink-0">
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

      {/* Trades counter */}
      <div className="bg-gray-900/50 rounded-lg px-3 py-2 text-xs text-gray-400">
        Trades today: <span className="text-white font-medium">{state.trades_today ?? 0}/{state.max_trades_per_day ?? 4}</span>
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

function SectionHeader({ label, count, color }: { label: string; count: number; color: string }) {
  return (
    <div className={`flex items-center gap-2 text-xs font-semibold uppercase tracking-widest ${color} mb-2`}>
      <span>{label}</span>
      <span className="bg-gray-700 text-gray-300 px-1.5 py-0.5 rounded font-normal">{count}</span>
    </div>
  );
}

export default function Bots({ events }: { events: WsEvent[] }) {
  const { bots } = useBotStates();
  const { trades } = useTrades(30);
  const [log, setLog] = useState<{ ts: string; msg: string; color: string }[]>([]);
  const [selectedBot, setSelectedBot] = useState<{ key: string; symbol: string } | null>(null);
  const [activeFilter, setActiveFilter] = useState<BotFilter>("all");

  const winRateBySymbol = useMemo(() => {
    const result: Record<string, WinRateStat> = {};
    for (const t of trades) {
      if (t.realized_pnl === 0) continue;
      if (!result[t.symbol]) result[t.symbol] = { wins: 0, total: 0, rate: 0 };
      result[t.symbol].total++;
      if (t.realized_pnl > 0) result[t.symbol].wins++;
    }
    for (const sym in result) {
      result[sym].rate = (result[sym].wins / result[sym].total) * 100;
    }
    return result;
  }, [trades]);

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

  const counts = useMemo(() => ({
    all:         botList.length,
    in_position: botList.filter(b => b.state === "IN_POSITION").length,
    scanning:    botList.filter(b => b.state === "SCANNING").length,
    cooldown:    botList.filter(b => b.state === "COOLDOWN").length,
    traded:      botList.filter(b => (b.trades_today ?? 0) > 0).length,
  }), [botList]);

  const filteredBots = useMemo(() => {
    switch (activeFilter) {
      case "in_position": return botList.filter(b => b.state === "IN_POSITION");
      case "scanning":    return botList.filter(b => b.state === "SCANNING");
      case "cooldown":    return botList.filter(b => b.state === "COOLDOWN");
      case "traded":      return botList.filter(b => (b.trades_today ?? 0) > 0);
      default:            return botList;
    }
  }, [botList, activeFilter]);

  // When showing all, group by state: IN_POSITION → COOLDOWN → SCANNING
  const grouped = useMemo(() => {
    if (activeFilter !== "all") return null;
    return {
      in_position: filteredBots.filter(b => b.state === "IN_POSITION"),
      cooldown:    filteredBots.filter(b => b.state === "COOLDOWN"),
      scanning:    filteredBots.filter(b => b.state === "SCANNING"),
    };
  }, [filteredBots, activeFilter]);

  const FILTERS: { key: BotFilter; label: string; countKey: keyof typeof counts }[] = [
    { key: "all",         label: "All",         countKey: "all" },
    { key: "in_position", label: "In Position",  countKey: "in_position" },
    { key: "cooldown",    label: "Cooldown",     countKey: "cooldown" },
    { key: "scanning",    label: "Scanning",     countKey: "scanning" },
    { key: "traded",      label: "Traded Today", countKey: "traded" },
  ];

  return (
    <div className="space-y-4 md:space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg md:text-xl font-bold text-white">Bots</h1>
        <div className="flex items-center gap-1.5 text-[10px] text-gray-500">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block" />
          <span>{counts.in_position} in position</span>
          <span className="ml-2 w-1.5 h-1.5 rounded-full bg-amber-400 inline-block" />
          <span>{counts.cooldown} cooldown</span>
          <span className="ml-2 w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse inline-block" />
          <span>{counts.scanning} scanning</span>
        </div>
      </div>

      {/* Filter bar */}
      {botList.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap">
          {FILTERS.map(f => (
            <button
              key={f.key}
              onClick={() => setActiveFilter(f.key)}
              className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border transition-colors ${
                activeFilter === f.key
                  ? "bg-emerald-900/40 border-emerald-700 text-emerald-300"
                  : "bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-500 hover:text-gray-300"
              }`}
            >
              {f.label}
              <span className={`text-[10px] px-1 py-0.5 rounded font-medium ${
                activeFilter === f.key ? "bg-emerald-800/60 text-emerald-300" : "bg-gray-700 text-gray-400"
              }`}>
                {counts[f.countKey]}
              </span>
            </button>
          ))}
        </div>
      )}

      {botList.length === 0 ? (
        <div className="bg-gray-800 border border-gray-700 rounded-xl p-6 md:p-12 text-center">
          <p className="text-gray-500 text-sm">No bots running.</p>
          <p className="text-gray-600 text-xs mt-2 break-all">
            Start a bot with <code className="bg-gray-700 px-1.5 py-0.5 rounded">
              make start
            </code>
          </p>
        </div>
      ) : filteredBots.length === 0 ? (
        <div className="bg-gray-800 border border-gray-700 rounded-xl p-8 text-center">
          <p className="text-gray-500 text-sm">No bots match this filter.</p>
        </div>
      ) : grouped ? (
        <div className="space-y-6">
          {grouped.in_position.length > 0 && (
            <div>
              <SectionHeader label="In Position" count={grouped.in_position.length} color="text-emerald-400" />
              <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3 md:gap-4">
                {grouped.in_position.map((b) => (
                  <BotCard
                    key={`${b.symbol}:${b.strategy}`}
                    state={b}
                    liveEvents={events}
                    winRate={winRateBySymbol[b.symbol]}
                    onClick={() => setSelectedBot({ key: `${b.symbol}:${b.strategy}`, symbol: b.symbol })}
                  />
                ))}
              </div>
            </div>
          )}
          {grouped.cooldown.length > 0 && (
            <div>
              <SectionHeader label="Cooldown" count={grouped.cooldown.length} color="text-amber-400" />
              <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3 md:gap-4">
                {grouped.cooldown.map((b) => (
                  <BotCard
                    key={`${b.symbol}:${b.strategy}`}
                    state={b}
                    liveEvents={events}
                    winRate={winRateBySymbol[b.symbol]}
                    onClick={() => setSelectedBot({ key: `${b.symbol}:${b.strategy}`, symbol: b.symbol })}
                  />
                ))}
              </div>
            </div>
          )}
          {grouped.scanning.length > 0 && (
            <div>
              <SectionHeader label="Scanning" count={grouped.scanning.length} color="text-blue-400" />
              <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3 md:gap-4">
                {grouped.scanning.map((b) => (
                  <BotCard
                    key={`${b.symbol}:${b.strategy}`}
                    state={b}
                    liveEvents={events}
                    winRate={winRateBySymbol[b.symbol]}
                    onClick={() => setSelectedBot({ key: `${b.symbol}:${b.strategy}`, symbol: b.symbol })}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3 md:gap-4">
          {filteredBots.map((b) => (
            <BotCard
              key={`${b.symbol}:${b.strategy}`}
              state={b}
              liveEvents={events}
              winRate={winRateBySymbol[b.symbol]}
              onClick={() => setSelectedBot({ key: `${b.symbol}:${b.strategy}`, symbol: b.symbol })}
            />
          ))}
        </div>
      )}

      {/* Bot Logs Modal */}
      {selectedBot && (
        <BotLogsModal
          botKey={selectedBot.key}
          symbol={selectedBot.symbol}
          onClose={() => setSelectedBot(null)}
        />
      )}

      {/* Activity log */}
      <div className="bg-gray-800 border border-gray-700 rounded-xl p-4 md:p-5">
        <p className="text-sm font-semibold text-gray-300 mb-3">Activity Log</p>
        <div className="space-y-1.5 max-h-48 overflow-y-auto font-mono text-[10px] md:text-xs">
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
