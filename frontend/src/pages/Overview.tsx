import { useMemo } from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
  BarChart, Bar, Cell, AreaChart, Area,
} from "recharts";
import { useAccountSummary, useTrades, usePerformance, usePositions, useBotStates } from "../hooks/useApi";
import { useFilter } from "../contexts/FilterContext";

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

/** Build daily P&L curve from trade list */
function buildPnlCurve(trades: { time: number; realized_pnl: number }[]) {
  if (trades.length === 0) {
    // No trades yet - show zero P&L for last 30 days
    const data = [];
    const now = new Date();
    for (let i = 29; i >= 0; i--) {
      const d = new Date(now);
      d.setDate(d.getDate() - i);
      data.push({
        date: d.toISOString().slice(5, 10), // MM-DD format
        pnl: 0,
        dailyPnl: 0,
      });
    }
    return data;
  }

  const byDay: Record<string, number> = {};

  for (const t of trades) {
    if (t.realized_pnl === 0) continue; // Skip opening trades
    const d = new Date(t.time).toISOString().slice(0, 10);
    byDay[d] = (byDay[d] ?? 0) + t.realized_pnl;
  }

  const sorted = Object.entries(byDay).sort();
  let cumulativePnl = 0;

  return sorted.map(([date, dailyPnl]) => {
    cumulativePnl += dailyPnl;
    return {
      date: date.slice(5), // MM-DD format
      pnl: parseFloat(cumulativePnl.toFixed(2)),
      dailyPnl: parseFloat(dailyPnl.toFixed(2)),
    };
  });
}

export default function Overview() {
  const { filter } = useFilter();
  const { summary }      = useAccountSummary();
  const { trades }       = useTrades(filter.dateRange);
  const { performance }  = usePerformance();
  const { positions }    = usePositions();
  const { bots }         = useBotStates();

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

  const pnlCurve = buildPnlCurve(filteredTrades);
  const activeBots = Object.values(bots).length;
  const scanningBots = Object.values(bots).filter(b => b.state === "SCANNING").length;

  // Build daily P&L chart
  const dailyPnl = useMemo(() => {
    const byDay: Record<string, number> = {};
    for (const t of filteredTrades) {
      if (t.realized_pnl === 0) continue; // Skip opening trades
      const d = new Date(t.time).toISOString().slice(0, 10);
      byDay[d] = (byDay[d] ?? 0) + t.realized_pnl;
    }
    return Object.entries(byDay)
      .sort()
      .map(([date, pnl]) => ({
        date: date.slice(5), // MM-DD format
        pnl: parseFloat(pnl.toFixed(2)),
      }));
  }, [filteredTrades]);

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold text-white">Overview</h1>

      {/* Metric cards */}
      <div className="grid grid-cols-2 xl:grid-cols-5 gap-4">
        <Card
          label="Total Equity"
          value={summary ? fmtUSD(totalEquity) : "—"}
          sub={summary ? `Unrealized: ${fmtUSD(unrealizedPnl)}` : undefined}
          color={unrealizedPnl >= 0 ? "text-white" : "text-white"}
        />
        <Card
          label="Available Balance"
          value={summary ? fmtUSD(summary.available_balance) : "—"}
          sub={summary ? `Margin: ${fmtUSD(summary.position_margin)}` : undefined}
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
        {/* P&L Curve */}
        <div className="bg-gray-800 border border-gray-700 rounded-xl p-5">
          <div className="flex items-center justify-between mb-4">
            <div>
              <p className="text-sm font-semibold text-gray-300">Cumulative P&L</p>
              <p className="text-xs text-gray-500 mt-1">Last 30 days performance</p>
            </div>
            {pnlCurve.length > 0 && (
              <div className="text-right">
                <p className={`text-2xl font-bold ${
                  pnlCurve[pnlCurve.length - 1].pnl >= 0 ? "text-emerald-400" : "text-red-400"
                }`}>
                  {pnlCurve[pnlCurve.length - 1].pnl >= 0 ? "+" : ""}
                  {fmtUSD(pnlCurve[pnlCurve.length - 1].pnl)}
                </p>
                <p className="text-xs text-gray-500">Total</p>
              </div>
            )}
          </div>
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={pnlCurve}>
              <defs>
                <linearGradient id="colorPnl" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#10b981" stopOpacity={0.3}/>
                  <stop offset="95%" stopColor="#10b981" stopOpacity={0}/>
                </linearGradient>
                <linearGradient id="colorPnlNegative" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3}/>
                  <stop offset="95%" stopColor="#ef4444" stopOpacity={0}/>
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" vertical={false} />
              <XAxis
                dataKey="date"
                tick={{ fill: "#6b7280", fontSize: 11 }}
                axisLine={{ stroke: "#374151" }}
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
                  const data = payload[0].payload;
                  return (
                    <div className="bg-gray-900 border border-gray-700 rounded-lg px-4 py-3 shadow-xl">
                      <p className="text-gray-400 text-xs mb-2">{data.date}</p>
                      <div className="space-y-1">
                        <div className="flex items-center justify-between gap-4">
                          <span className="text-xs text-gray-500">Daily P&L:</span>
                          <span className={`text-sm font-bold ${
                            data.dailyPnl >= 0 ? "text-emerald-400" : "text-red-400"
                          }`}>
                            {data.dailyPnl >= 0 ? "+" : ""}{fmtUSD(data.dailyPnl)}
                          </span>
                        </div>
                        <div className="flex items-center justify-between gap-4 pt-1 border-t border-gray-700">
                          <span className="text-xs text-gray-500">Total P&L:</span>
                          <span className={`text-base font-bold ${
                            data.pnl >= 0 ? "text-emerald-400" : "text-red-400"
                          }`}>
                            {data.pnl >= 0 ? "+" : ""}{fmtUSD(data.pnl)}
                          </span>
                        </div>
                      </div>
                    </div>
                  );
                }}
              />
              <Area
                type="monotone"
                dataKey="pnl"
                stroke={pnlCurve.length > 0 && pnlCurve[pnlCurve.length - 1].pnl >= 0 ? "#10b981" : "#ef4444"}
                strokeWidth={2.5}
                fill={pnlCurve.length > 0 && pnlCurve[pnlCurve.length - 1].pnl >= 0 ? "url(#colorPnl)" : "url(#colorPnlNegative)"}
                fillOpacity={1}
              />
            </AreaChart>
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
