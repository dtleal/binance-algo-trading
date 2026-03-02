import { useMemo, useState } from "react";
import {
  XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
  AreaChart, Area, BarChart, Bar, Cell, LabelList,
} from "recharts";
import { useAccountSummary, useTrades, usePositions, useBotStates } from "../hooks/useApi";
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

function SectionHeader({ title, badge }: { title: string; badge?: string }) {
  return (
    <div className="flex items-center gap-3 pt-2">
      <span className="text-xs font-semibold text-gray-400 uppercase tracking-widest whitespace-nowrap">{title}</span>
      {badge && (
        <span className="text-[10px] font-medium px-2 py-0.5 rounded-full text-blue-400 bg-blue-900/30 whitespace-nowrap">
          {badge}
        </span>
      )}
      <div className="h-px flex-1 bg-gray-700/40" />
    </div>
  );
}

function fmtUSD(n: number) {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
}

function fmtDate(ms: number) {
  return new Date(ms).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function buildDailyPnl(trades: { time: number; realized_pnl: number }[]) {
  const byDay: Record<string, number> = {};
  for (const t of trades) {
    if (t.realized_pnl === 0) continue;
    const d = new Date(t.time).toISOString().slice(0, 10);
    byDay[d] = (byDay[d] ?? 0) + t.realized_pnl;
  }
  return Object.entries(byDay).sort().map(([date, pnl]) => ({
    date: date.slice(5),
    pnl: parseFloat(pnl.toFixed(2)),
  }));
}

function buildPnlCurve(trades: { time: number; realized_pnl: number }[]) {
  const byDay: Record<string, number> = {};
  for (const t of trades) {
    if (t.realized_pnl === 0) continue;
    const d = new Date(t.time).toISOString().slice(0, 10);
    byDay[d] = (byDay[d] ?? 0) + t.realized_pnl;
  }
  let running = 0;
  return Object.entries(byDay).sort().map(([date, dp]) => {
    running += dp;
    return { date: date.slice(5), pnl: parseFloat(running.toFixed(2)), tradePnl: null as number | null, symbol: null as string | null };
  });
}

function buildPnlCurvePerTrade(trades: { time: number; realized_pnl: number; symbol?: string }[]) {
  const sorted = [...trades]
    .filter(t => t.realized_pnl !== 0)
    .sort((a, b) => a.time - b.time);
  let running = 0;
  return sorted.map(t => {
    running += t.realized_pnl;
    const dt = new Date(t.time);
    const date = `${(dt.getMonth() + 1).toString().padStart(2, "0")}/${dt.getDate().toString().padStart(2, "0")} ${dt.getHours().toString().padStart(2, "0")}:${dt.getMinutes().toString().padStart(2, "0")}`;
    return {
      date,
      pnl: parseFloat(running.toFixed(2)),
      tradePnl: parseFloat(t.realized_pnl.toFixed(2)),
      symbol: t.symbol ?? null,
    };
  });
}

export default function Overview() {
  const { filter } = useFilter();
  const { summary }   = useAccountSummary();
  const { trades }    = useTrades(filter.dateRange, filter.dateFrom, filter.dateTo, filter.strategy);
  const { positions } = usePositions();
  const { bots }      = useBotStates();
  const [pnlView, setPnlView] = useState<"trade" | "daily">("trade");

  const filteredTrades = useMemo(() => {
    return trades.filter(t => {
      return filter.symbol === "ALL" || t.symbol === filter.symbol;
    });
  }, [trades, filter.symbol]);

  const totalEquity = summary?.total_equity ?? 0;
  const pnl24h = summary?.pnl_24h ?? 0;
  const equityChange24h = summary?.equity_change_24h_pct ?? 0;

  // Open P&L from Binance live positions (source of truth — reflects manual closes immediately)
  const unrealizedPnl = positions.reduce((sum, p) => sum + p.unrealized_pnl, 0);
  const openPositions = positions.length;

  // Filtered period stats (responds to active filters)
  const closingTrades      = useMemo(() => filteredTrades.filter(t => t.realized_pnl !== 0), [filteredTrades]);
  const filteredPnl        = useMemo(() => closingTrades.reduce((s, t) => s + t.realized_pnl, 0), [closingTrades]);
  const filteredCommission = useMemo(() => closingTrades.reduce((s, t) => s + t.commission, 0), [closingTrades]);
  const filteredNetPnl     = filteredPnl + filteredCommission; // commission is already negative
  const filteredWins       = useMemo(() => closingTrades.filter(t => t.realized_pnl > 0).length, [closingTrades]);
  const filteredWinRate    = closingTrades.length
    ? ((filteredWins / closingTrades.length) * 100).toFixed(1)
    : null;

  // P&L grouped by strategy
  const strategyPnl = useMemo(() => {
    const byStrategy: Record<string, { pnl: number; wins: number; total: number }> = {};
    for (const t of closingTrades) {
      const s = t.strategy || "Unknown";
      if (!byStrategy[s]) byStrategy[s] = { pnl: 0, wins: 0, total: 0 };
      byStrategy[s].pnl += t.realized_pnl;
      byStrategy[s].total += 1;
      if (t.realized_pnl > 0) byStrategy[s].wins += 1;
    }
    return Object.entries(byStrategy)
      .map(([strategy, d]) => ({
        strategy,
        pnl: parseFloat(d.pnl.toFixed(2)),
        winRate: parseFloat(((d.wins / d.total) * 100).toFixed(1)),
        trades: d.total,
      }))
      .sort((a, b) => b.pnl - a.pnl);
  }, [closingTrades]);

  const pnlCurveDaily    = useMemo(() => buildPnlCurve(filteredTrades), [filteredTrades]);
  const pnlCurvePerTrade = useMemo(() => buildPnlCurvePerTrade(filteredTrades), [filteredTrades]);
  const pnlCurve  = pnlView === "trade" ? pnlCurvePerTrade : pnlCurveDaily;
  const dailyPnl  = useMemo(() => buildDailyPnl(filteredTrades), [filteredTrades]);
  const activeBots = Object.values(bots).length;

  return (
    <div className="space-y-4 md:space-y-6">
      <h1 className="text-lg md:text-xl font-bold text-white">Overview</h1>

      {/* Live Account — real-time, not affected by filters */}
      <SectionHeader title="Live Account" badge="Real-time · Not filtered" />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 md:gap-4">
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
          label="Open Positions"
          value={String(openPositions)}
          sub={`${activeBots} bot${activeBots !== 1 ? "s" : ""} active`}
        />
      </div>

      {/* Filtered Analysis — responds to symbol / strategy / date filters */}
      <SectionHeader title="Filtered Analysis" />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 md:gap-4">
        <Card
          label="Gross P&L"
          value={closingTrades.length ? fmtUSD(filteredPnl) : "—"}
          color={filteredPnl >= 0 ? "text-emerald-400" : "text-red-400"}
          sub={closingTrades.length ? `${closingTrades.length} closed trade${closingTrades.length !== 1 ? "s" : ""}` : "No closed trades"}
        />
        <Card
          label="Commissions"
          value={closingTrades.length ? fmtUSD(filteredCommission) : "—"}
          color="text-amber-400"
          sub="Fees paid"
        />
        <Card
          label="Net P&L"
          value={closingTrades.length ? fmtUSD(filteredNetPnl) : "—"}
          color={filteredNetPnl >= 0 ? "text-emerald-400" : "text-red-400"}
          sub={closingTrades.length ? `Avg ${fmtUSD(filteredNetPnl / closingTrades.length)}/trade` : undefined}
        />
        <Card
          label="Win Rate"
          value={filteredWinRate ? `${filteredWinRate}%` : "—"}
          color={filteredWinRate && parseFloat(filteredWinRate) >= 50 ? "text-emerald-400" : "text-amber-400"}
          sub={filteredWinRate ? `${filteredWins}W / ${closingTrades.length - filteredWins}L` : undefined}
        />
      </div>

      {/* Cumulative P&L chart */}
      <div className="bg-gray-800 border border-gray-700 rounded-xl p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <p className="text-sm font-semibold text-gray-300">Cumulative P&L</p>
            <p className="text-xs text-gray-500 mt-1">
              Realized P&L · {filter.dateFrom ? `${filter.dateFrom} → ${filter.dateTo ?? "today"}` : `Last ${filter.dateRange}d`}
            </p>
          </div>
          <div className="flex items-center gap-3">
            {/* View toggle */}
            <div className="flex items-center bg-gray-900 border border-gray-700 rounded-lg p-0.5 text-xs">
              <button
                onClick={() => setPnlView("trade")}
                className={`px-3 py-1.5 rounded-md font-medium transition-colors ${
                  pnlView === "trade"
                    ? "bg-emerald-600 text-white"
                    : "text-gray-400 hover:text-gray-200"
                }`}
              >
                Per Trade
              </button>
              <button
                onClick={() => setPnlView("daily")}
                className={`px-3 py-1.5 rounded-md font-medium transition-colors ${
                  pnlView === "daily"
                    ? "bg-emerald-600 text-white"
                    : "text-gray-400 hover:text-gray-200"
                }`}
              >
                Daily
              </button>
            </div>
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
          <AreaChart data={pnlCurve}>
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
                    <p className="text-gray-400 mb-1">{d.date}</p>
                    {d.symbol && (
                      <p className="text-gray-500 mb-1">{d.symbol.replace("USDT", "").replace("1000", "")}</p>
                    )}
                    {d.tradePnl !== null && (
                      <p className={`mb-1 ${d.tradePnl >= 0 ? "text-emerald-300" : "text-red-300"}`}>
                        Trade: {d.tradePnl >= 0 ? "+" : ""}{fmtUSD(d.tradePnl)}
                      </p>
                    )}
                    <span className={`font-bold ${d.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                      Cumul: {d.pnl >= 0 ? "+" : ""}{fmtUSD(d.pnl)}
                    </span>
                  </div>
                );
              }}
            />
            <Area
              type="monotone"
              dataKey="pnl"
              stroke={pnlCurve.length > 0 && pnlCurve[pnlCurve.length - 1].pnl >= 0 ? "#10b981" : "#ef4444"}
              strokeWidth={2}
              fill={pnlCurve.length > 0 && pnlCurve[pnlCurve.length - 1].pnl >= 0 ? "url(#colorPnl)" : "url(#colorPnlNeg)"}
              dot={pnlView === "trade" ? { r: 3, fill: pnlCurve.length > 0 && pnlCurve[pnlCurve.length - 1].pnl >= 0 ? "#10b981" : "#ef4444", strokeWidth: 0 } : false}
              activeDot={{ r: 5 }}
            />
          </AreaChart>
        </ResponsiveContainer>

        {/* Daily P&L bars */}
        <div className="mt-6 pt-5 border-t border-gray-700">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-3">Daily P&L</p>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={dailyPnl} barCategoryGap="30%">
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
                      <p className="text-gray-400 mb-1">{d.date}</p>
                      <span className={`font-bold ${d.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                        {d.pnl >= 0 ? "+" : ""}{fmtUSD(d.pnl)}
                      </span>
                    </div>
                  );
                }}
              />
              <Bar dataKey="pnl" radius={[3, 3, 0, 0]}>
                {dailyPnl.map((entry, i) => (
                  <Cell key={i} fill={entry.pnl >= 0 ? "#10b981" : "#ef4444"} fillOpacity={0.85} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* P&L by Strategy */}
        {strategyPnl.length > 0 && (
          <div className="mt-6 pt-5 border-t border-gray-700">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-4">P&L by Strategy</p>
            <ResponsiveContainer width="100%" height={strategyPnl.length * 52 + 8}>
              <BarChart
                data={strategyPnl}
                layout="vertical"
                margin={{ left: 0, right: 80, top: 0, bottom: 0 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" horizontal={false} />
                <XAxis
                  type="number"
                  tick={{ fill: "#6b7280", fontSize: 11 }}
                  tickFormatter={(v) => `$${v}`}
                  axisLine={{ stroke: "#374151" }}
                />
                <YAxis
                  type="category"
                  dataKey="strategy"
                  tick={{ fill: "#9ca3af", fontSize: 12 }}
                  axisLine={false}
                  tickLine={false}
                  width={120}
                />
                <Tooltip
                  content={({ active, payload }) => {
                    if (!active || !payload?.length) return null;
                    const d = payload[0].payload;
                    return (
                      <div className="bg-gray-900 border border-gray-700 rounded-lg px-4 py-3 shadow-xl text-xs">
                        <p className="text-gray-300 font-semibold mb-2">{d.strategy}</p>
                        <p className={`font-bold mb-1 ${d.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                          {d.pnl >= 0 ? "+" : ""}{fmtUSD(d.pnl)}
                        </p>
                        <p className="text-gray-400">Win Rate: {d.winRate}%</p>
                        <p className="text-gray-500">{d.trades} trades</p>
                      </div>
                    );
                  }}
                />
                <Bar dataKey="pnl" radius={[0, 3, 3, 0]}>
                  {strategyPnl.map((e, i) => (
                    <Cell key={i} fill={e.pnl >= 0 ? "#10b981" : "#ef4444"} fillOpacity={0.85} />
                  ))}
                  <LabelList
                    dataKey="pnl"
                    position="right"
                    formatter={(v: number) => `${v >= 0 ? "+" : ""}${fmtUSD(v)}`}
                    style={{ fill: "#9ca3af", fontSize: 11 }}
                  />
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
