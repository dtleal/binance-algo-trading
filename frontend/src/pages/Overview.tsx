import { useMemo } from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
  BarChart, Bar, Cell, AreaChart, Area, ComposedChart,
} from "recharts";
import { useAccountSummary, useTrades, usePerformance, usePositions, useBotStates, useEquityHistory } from "../hooks/useApi";
import { useFilter } from "../contexts/FilterContext";
import PerformanceMetrics from "../components/PerformanceMetrics";
import PerformanceRankings from "../components/PerformanceRankings";

function Card({
  label, value, sub, color = "text-white",
}: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-xl p-5">
      <p className="text-xs text-gray-400 uppercase tracking-widest mb-2">{label}</p>
      <p className={`text-2xl font-bold ${color}`}>{value}</p>
      {sub && <p className="text-xs text-gray-500 mt-1">{sub}</p>}
    </div>
  );
}

function fmtUSD(n: number) {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
}

function fmtDate(ms: number) {
  return new Date(ms).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

interface EquitySnapshot { time: number; equity: number; unrealized_pnl: number }

/**
 * Merge equity snapshots with realized P&L into a single time-series.
 * Each point carries:
 *   pnl    — cumulative realized P&L up to that moment (step function)
 *   equity — equity delta from the first snapshot (includes unrealized)
 */
function buildCombinedCurve(
  trades: { time: number; realized_pnl: number }[],
  snapshots: EquitySnapshot[],
) {
  if (snapshots.length === 0 && trades.length === 0) return [];

  // Step 1 — sorted closing fills
  const closes = trades
    .filter(t => t.realized_pnl !== 0)
    .sort((a, b) => a.time - b.time);

  // Step 2 — baseline equity (first snapshot)
  const baseEquity = snapshots.length > 0 ? snapshots[0].equity : 0;

  // Step 3 — build points from equity snapshots
  let cumPnl = 0;
  let closeIdx = 0;

  const points = snapshots.map(s => {
    // Advance cumulative P&L to this snapshot's timestamp
    while (closeIdx < closes.length && closes[closeIdx].time <= s.time) {
      cumPnl += closes[closeIdx].realized_pnl;
      closeIdx++;
    }
    const label = new Date(s.time).toISOString().slice(5, 16).replace("T", " ");
    return {
      date:        label,
      pnl:         parseFloat(cumPnl.toFixed(2)),
      equity:      parseFloat((s.equity - baseEquity).toFixed(2)),
      dailyPnl:    null as number | null,
    };
  });

  // Step 4 — if no snapshots, fall back to daily aggregation (original behaviour)
  if (points.length === 0) {
    const byDay: Record<string, number> = {};
    for (const t of closes) {
      const d = new Date(t.time).toISOString().slice(0, 10);
      byDay[d] = (byDay[d] ?? 0) + t.realized_pnl;
    }
    let running = 0;
    return Object.entries(byDay).sort().map(([date, dp]) => {
      running += dp;
      return { date: date.slice(5), pnl: parseFloat(running.toFixed(2)), equity: null, dailyPnl: parseFloat(dp.toFixed(2)) };
    });
  }

  return points;
}

/** Legacy daily P&L bar chart helper */
function buildDailyBars(trades: { time: number; realized_pnl: number }[]) {
  const byDay: Record<string, number> = {};
  for (const t of trades) {
    if (t.realized_pnl === 0) continue;
    const d = new Date(t.time).toISOString().slice(0, 10);
    byDay[d] = (byDay[d] ?? 0) + t.realized_pnl;
  }
  return Object.entries(byDay).sort().map(([date, pnl]) => ({
    date: date.slice(5),
    pnl:  parseFloat(pnl.toFixed(2)),
  }));
}

export default function Overview() {
  const { filter } = useFilter();
  const { summary }      = useAccountSummary();
  const { trades }       = useTrades(filter.dateRange, filter.dateFrom, filter.dateTo);
  const { performance }  = usePerformance();
  const { positions }    = usePositions();
  const { bots }         = useBotStates();
  const { snapshots }    = useEquityHistory(filter.dateRange);

  // Filter trades by symbol and strategy
  const filteredTrades = useMemo(() => {
    return trades.filter(t => {
      const symbolMatch = filter.symbol === "ALL" || t.symbol === filter.symbol;
      // Strategy filtering would require backend support to tag trades with strategy
      return symbolMatch;
    });
  }, [trades, filter.symbol]);

  const todayUTC = new Date().toISOString().slice(0, 10);
  const todayTrades  = filteredTrades.filter(
    (t) => new Date(t.time).toISOString().slice(0, 10) === todayUTC
  );
  const pnlToday = todayTrades.reduce((s, t) => s + t.realized_pnl, 0);

  // Use summary data if available, fallback to trade-based calculations
  const totalEquity = summary?.total_equity ?? 0;
  const unrealizedPnl = summary?.unrealized_pnl ?? 0;
  const pnl24h = summary?.pnl_24h ?? 0;
  const equityChange24h = summary?.equity_change_24h_pct ?? 0;
  const openPositions = summary?.open_positions ?? 0;

  const pnlCurve = buildCombinedCurve(filteredTrades, snapshots);
  const activeBots = Object.values(bots).length;
  const scanningBots = Object.values(bots).filter(b => b.state === "SCANNING").length;

  const dailyPnl = useMemo(() => buildDailyBars(filteredTrades), [filteredTrades]);
  const hasEquity = snapshots.length > 0;

  return (
    <div className="space-y-4 md:space-y-6">
      <h1 className="text-lg md:text-xl font-bold text-white">Overview</h1>

      {/* Metric cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-3 md:gap-4">
        <Card
          label="Open P&L"
          value={summary ? fmtUSD(unrealizedPnl) : "—"}
          sub={openPositions > 0 ? `${openPositions} position${openPositions > 1 ? 's' : ''}` : "No positions"}
          color={unrealizedPnl >= 0 ? "text-emerald-400" : "text-red-400"}
        />
        <Card
          label="Total Equity"
          value={summary ? fmtUSD(totalEquity) : "—"}
          sub={summary ? `Available: ${fmtUSD(summary.available_balance)}` : undefined}
          color="text-white"
        />
        <Card
          label="P&L 24h"
          value={fmtUSD(pnl24h)}
          color={pnl24h >= 0 ? "text-emerald-400" : "text-red-400"}
          sub={`${equityChange24h >= 0 ? "+" : ""}${equityChange24h.toFixed(2)}%`}
        />
        <Card
          label="P&L 30 Days"
          value={performance ? fmtUSD(performance.portfolio.total_pnl) : fmtUSD(0)}
          color={(performance?.portfolio.total_pnl ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"}
          sub={performance ? `Win rate: ${performance.portfolio.win_rate}%` : undefined}
        />
        <Card
          label="Open Positions"
          value={String(openPositions)}
          sub={performance ? `${performance.portfolio.total_trades} total trades` : undefined}
        />
      </div>

      <div className="grid grid-cols-1 gap-6">
        {/* Cumulative P&L + Equity curve */}
        <div className="bg-gray-800 border border-gray-700 rounded-xl p-5">
          <div className="flex items-center justify-between mb-4">
            <div>
              <p className="text-sm font-semibold text-gray-300">Cumulative P&L</p>
              <p className="text-xs text-gray-500 mt-1">
                {hasEquity ? "Realized P&L vs Equity fluctuation" : "Realized P&L · Last 30 days"}
              </p>
            </div>
            <div className="flex items-center gap-4">
              {hasEquity && (
                <div className="flex items-center gap-3 text-xs text-gray-500">
                  <span className="flex items-center gap-1">
                    <span className="inline-block w-4 h-0.5 bg-emerald-400"></span>Realized
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="inline-block w-4 h-0.5 bg-blue-400 border-dashed"></span>Equity
                  </span>
                </div>
              )}
              {pnlCurve.length > 0 && (
                <div className="text-right">
                  <p className={`text-2xl font-bold ${
                    pnlCurve[pnlCurve.length - 1].pnl >= 0 ? "text-emerald-400" : "text-red-400"
                  }`}>
                    {pnlCurve[pnlCurve.length - 1].pnl >= 0 ? "+" : ""}
                    {fmtUSD(pnlCurve[pnlCurve.length - 1].pnl)}
                  </p>
                  <p className="text-xs text-gray-500">Realized</p>
                </div>
              )}
            </div>
          </div>
          <ResponsiveContainer width="100%" height={280}>
            <ComposedChart data={pnlCurve}>
              <defs>
                <linearGradient id="colorPnl" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#10b981" stopOpacity={0.25}/>
                  <stop offset="95%" stopColor="#10b981" stopOpacity={0}/>
                </linearGradient>
                <linearGradient id="colorPnlNeg" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#ef4444" stopOpacity={0.25}/>
                  <stop offset="95%" stopColor="#ef4444" stopOpacity={0}/>
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" vertical={false} />
              <XAxis
                dataKey="date"
                tick={{ fill: "#6b7280", fontSize: 11 }}
                axisLine={{ stroke: "#374151" }}
                interval="preserveStartEnd"
              />
              <YAxis
                tick={{ fill: "#6b7280", fontSize: 11 }}
                tickFormatter={(v) => `$${v}`}
                width={65}
                axisLine={{ stroke: "#374151" }}
              />
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null;
                  const d = payload[0].payload;
                  return (
                    <div className="bg-gray-900 border border-gray-700 rounded-lg px-4 py-3 shadow-xl text-xs">
                      <p className="text-gray-400 mb-2">{d.date}</p>
                      <div className="space-y-1">
                        <div className="flex justify-between gap-4">
                          <span className="text-gray-500">Realized P&L:</span>
                          <span className={`font-bold ${d.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                            {d.pnl >= 0 ? "+" : ""}{fmtUSD(d.pnl)}
                          </span>
                        </div>
                        {d.equity != null && (
                          <div className="flex justify-between gap-4">
                            <span className="text-gray-500">Equity Δ:</span>
                            <span className={`font-bold ${d.equity >= 0 ? "text-blue-400" : "text-orange-400"}`}>
                              {d.equity >= 0 ? "+" : ""}{fmtUSD(d.equity)}
                            </span>
                          </div>
                        )}
                      </div>
                    </div>
                  );
                }}
              />
              {/* Realized P&L — area */}
              <Area
                type="stepAfter"
                dataKey="pnl"
                stroke={pnlCurve.length > 0 && pnlCurve[pnlCurve.length - 1].pnl >= 0 ? "#10b981" : "#ef4444"}
                strokeWidth={2}
                fill={pnlCurve.length > 0 && pnlCurve[pnlCurve.length - 1].pnl >= 0 ? "url(#colorPnl)" : "url(#colorPnlNeg)"}
                dot={false}
                connectNulls
              />
              {/* Equity fluctuation — dashed line (only when snapshots available) */}
              {hasEquity && (
                <Line
                  type="monotone"
                  dataKey="equity"
                  stroke="#60a5fa"
                  strokeWidth={1.5}
                  strokeDasharray="4 3"
                  dot={false}
                  connectNulls
                />
              )}
            </ComposedChart>
          </ResponsiveContainer>
        </div>

        {/* Daily P&L Bar Chart */}
        {dailyPnl.length > 0 && (
          <div className="bg-gray-800 border border-gray-700 rounded-xl p-5">
            <p className="text-sm font-semibold text-gray-300 mb-4">Daily P&L — Last 30 Days</p>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={dailyPnl} barCategoryGap="10%">
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" vertical={false} />
                <XAxis dataKey="date" tick={{ fill: "#6b7280", fontSize: 10 }} />
                <YAxis tick={{ fill: "#6b7280", fontSize: 10 }}
                  tickFormatter={(v) => `$${v}`} width={60} />
                <Tooltip
                  content={({ active, payload }) => {
                    if (!active || !payload?.length) return null;
                    const val = payload[0].value as number;
                    return (
                      <div className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-xs">
                        <p className="text-gray-400">{payload[0].payload.date}</p>
                        <p className={`font-bold ${val >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                          {val >= 0 ? "+" : ""}{fmtUSD(val)}
                        </p>
                      </div>
                    );
                  }}
                />
                <Bar dataKey="pnl" radius={[4, 4, 0, 0]}>
                  {dailyPnl.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.pnl >= 0 ? "#10b981" : "#ef4444"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* Performance Report */}
      <PerformanceMetrics trades={filteredTrades} startCapital={1000} />

      {/* Performance Rankings */}
      <PerformanceRankings trades={filteredTrades} />

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">

        {/* Recent trades or Open Positions */}
        <div className="bg-gray-800 border border-gray-700 rounded-xl p-5">
          <p className="text-sm font-semibold text-gray-300 mb-4">
            {filteredTrades.length > 0 ? "Recent Trades" : "Open Positions"}
          </p>
          <div className="space-y-2 overflow-y-auto max-h-[240px]">
            {filteredTrades.length > 0 ? (
              filteredTrades.slice(0, 15).map((t, i) => (
                <div key={i} className="flex items-center justify-between text-xs py-1.5
                  border-b border-gray-700/50 last:border-0">
                  <div>
                    <span className="text-gray-400">{t.symbol.replace("USDT", "")}</span>
                    <span className={`ml-2 px-1.5 py-0.5 rounded text-[10px] font-medium
                      ${t.side === "BUY" ? "bg-emerald-900/50 text-emerald-400" : "bg-red-900/50 text-red-400"}`}>
                      {t.side}
                    </span>
                  </div>
                  <div className="text-right">
                    <p className={t.realized_pnl >= 0 ? "text-emerald-400" : "text-red-400"}>
                      {t.realized_pnl >= 0 ? "+" : ""}{fmtUSD(t.realized_pnl)}
                    </p>
                    <p className="text-gray-600">{fmtDate(t.time)}</p>
                  </div>
                </div>
              ))
            ) : positions.length > 0 ? (
              positions.map((p, i) => (
                <div key={i} className="flex items-center justify-between text-xs py-2
                  border-b border-gray-700/50 last:border-0">
                  <div>
                    <span className="text-gray-300 font-medium">{p.symbol.replace("USDT", "")}</span>
                    <span className={`ml-2 px-1.5 py-0.5 rounded text-[10px] font-medium
                      ${p.side === "LONG" ? "bg-emerald-900/50 text-emerald-400" : "bg-red-900/50 text-red-400"}`}>
                      {p.side}
                    </span>
                    <p className="text-gray-600 text-[10px] mt-0.5">
                      Entry: ${p.entry_price.toFixed(p.entry_price > 1 ? 4 : 6)} • {p.leverage}x
                    </p>
                  </div>
                  <div className="text-right">
                    <p className={p.unrealized_pnl >= 0 ? "text-emerald-400 font-semibold" : "text-red-400 font-semibold"}>
                      {p.unrealized_pnl >= 0 ? "+" : ""}{fmtUSD(p.unrealized_pnl)}
                    </p>
                    <p className="text-gray-600 text-[10px]">
                      ${p.mark_price.toFixed(p.mark_price > 1 ? 4 : 6)}
                    </p>
                  </div>
                </div>
              ))
            ) : (
              <div className="py-8 text-center">
                <p className="text-gray-500 text-xs mb-2">No open positions</p>
                <p className="text-gray-600 text-[10px]">
                  Bots are scanning for entry signals
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
