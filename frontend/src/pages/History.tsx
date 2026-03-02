import { useState, useMemo } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell,
} from "recharts";
import { useTrades, useBotStates, usePositions } from "../hooks/useApi";
import { Trade } from "../types";
import { useFilter } from "../contexts/FilterContext";

function fmtUSD(n: number) {
  return (n >= 0 ? "+" : "") + n.toLocaleString("en-US", {
    style: "currency", currency: "USD", minimumFractionDigits: 2,
  });
}

function fmtTime(ms: number) {
  return new Date(ms).toLocaleString("en-US", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function StatCard({ label, value, color = "text-white" }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-xl p-4 text-center">
      <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">{label}</p>
      <p className={`text-lg font-bold ${color}`}>{value}</p>
    </div>
  );
}

/** Group trades into open/close pairs by symbol + order sequence */
function pairTrades(trades: Trade[]) {
  // Binance trade list: each fill is a row. For our purposes, show each raw trade.
  // We show realized P&L per fill (from t.realized_pnl).
  return trades;
}

const PnlTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  const v = payload[0].value as number;
  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-xs">
      <p className="text-gray-400">{label}</p>
      <p className={v >= 0 ? "text-emerald-400" : "text-red-400"}>{fmtUSD(v)}</p>
    </div>
  );
};

export default function History() {
  const { filter: globalFilter } = useFilter();
  const { trades, isLoading } = useTrades(globalFilter.dateRange, globalFilter.dateFrom, globalFilter.dateTo, globalFilter.strategy);
  const { bots } = useBotStates();
  const { positions } = usePositions();

  const activeBots = Object.values(bots).length;
  const openPositions = positions.length;

  const filtered = useMemo(() => {
    return trades.filter((t) => {
      if (globalFilter.symbol !== "ALL" && t.symbol !== globalFilter.symbol) return false;
      if (globalFilter.side !== "ALL" && t.realized_pnl !== 0) {
        if (globalFilter.side === "LONG" && t.side !== "SELL") return false;
        if (globalFilter.side === "SHORT" && t.side !== "BUY") return false;
      }
      return true;
    });
  }, [trades, globalFilter.symbol, globalFilter.side]);

  const [sort, setSort] = useState<{ key: keyof Trade; dir: 1 | -1 }>({
    key: "time", dir: -1,
  });

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      const av = a[sort.key];
      const bv = b[sort.key];
      return ((av as number) < (bv as number) ? -1 : (av as number) > (bv as number) ? 1 : 0) * sort.dir;
    });
  }, [filtered, sort]);

  function toggleSort(key: keyof Trade) {
    setSort((prev: { key: keyof Trade; dir: 1 | -1 }) => ({
      key,
      dir: prev.key === key ? ((prev.dir * -1) as 1 | -1) : -1,
    }));
  }

  function SortTh({ k, label }: { k: keyof Trade; label: string }) {
    const active = sort.key === k;
    return (
      <th
        onClick={() => toggleSort(k)}
        className="px-4 py-3 text-left text-xs text-gray-500 uppercase tracking-wider cursor-pointer select-none hover:text-gray-300 transition-colors"
      >
        {label} {active ? (sort.dir === -1 ? "↓" : "↑") : ""}
      </th>
    );
  }

  // Stats (scoped to filtered trades)
  const closingTrades = filtered.filter((t) => t.realized_pnl !== 0);
  const totalPnl  = closingTrades.reduce((s, t) => s + t.realized_pnl, 0);
  const winCount  = closingTrades.filter((t) => t.realized_pnl > 0).length;
  const winRate   = closingTrades.length ? ((winCount / closingTrades.length) * 100).toFixed(1) : "—";
  const avgPnl    = closingTrades.length ? totalPnl / closingTrades.length : 0;
  const totalComm = filtered.reduce((s, t) => s + t.commission, 0);

  // Daily P&L bar chart
  const dailyPnl = useMemo(() => {
    const byDay: Record<string, number> = {};
    for (const t of closingTrades) {
      const d = new Date(t.time).toISOString().slice(0, 10);
      byDay[d] = (byDay[d] ?? 0) + t.realized_pnl;
    }
    return Object.entries(byDay)
      .sort()
      .map(([date, pnl]) => ({ date: date.slice(5), pnl: parseFloat(pnl.toFixed(4)) }));
  }, [closingTrades]);

  const COLS: { key: keyof Trade; label: string }[] = [
    { key: "time",         label: "Date" },
    { key: "symbol",       label: "Symbol" },
    { key: "side",         label: "Side" },
    { key: "price",        label: "Price" },
    { key: "qty",          label: "Qty" },
    { key: "realized_pnl", label: "P&L" },
    { key: "commission",   label: "Fee" },
  ];

  return (
    <div className="space-y-6">
      {/* Header */}
      <h1 className="text-xl font-bold text-white">Trade History</h1>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Total P&L" value={fmtUSD(totalPnl)}
          color={totalPnl >= 0 ? "text-emerald-400" : "text-red-400"} />
        <StatCard label="Win Rate"  value={`${winRate}%`}
          color={parseFloat(winRate) >= 50 ? "text-emerald-400" : "text-red-400"} />
        <StatCard label="Avg P&L"   value={closingTrades.length ? fmtUSD(avgPnl) : "—"}
          color={avgPnl >= 0 ? "text-emerald-400" : "text-red-400"} />
        <StatCard label="Total Fees" value={`-${totalComm.toFixed(4)}`} color="text-amber-400" />
      </div>

      {/* Daily P&L chart */}
      {dailyPnl.length > 1 && (
        <div className="bg-gray-800 border border-gray-700 rounded-xl p-5">
          <p className="text-sm font-semibold text-gray-300 mb-4">Daily Realized P&L</p>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={dailyPnl} barCategoryGap="20%">
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: "#6b7280", fontSize: 10 }} />
              <YAxis tick={{ fill: "#6b7280", fontSize: 10 }} tickFormatter={(v) => `$${v}`} width={55} />
              <Tooltip content={<PnlTooltip />} />
              <Bar dataKey="pnl" radius={[3, 3, 0, 0]}>
                {dailyPnl.map((e, i) => (
                  <Cell key={i} fill={e.pnl >= 0 ? "#10b981" : "#ef4444"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Table */}
      <div className="bg-gray-800 border border-gray-700 rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-700 flex items-center gap-3">
          <span className="text-xs text-gray-500">{sorted.length} records</span>
        </div>

        {isLoading ? (
          <div className="p-8 text-center text-gray-500 text-sm">Loading…</div>
        ) : (
          <div className="overflow-x-auto max-h-[480px] overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-gray-800 border-b border-gray-700">
                <tr>
                  {COLS.map((c) => <SortTh key={c.key} k={c.key} label={c.label} />)}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700/30">
                {sorted.map((t, i) => (
                  <tr key={i} className="hover:bg-gray-700/30 transition-colors">
                    <td className="px-4 py-2.5 text-gray-400 text-xs">{fmtTime(t.time)}</td>
                    <td className="px-4 py-2.5 font-medium text-white">
                      {t.symbol.replace(/USDT$/, "")}
                      <span className="text-gray-600 text-xs">/USDT</span>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`px-1.5 py-0.5 rounded text-xs font-medium
                        ${t.side === "BUY" ? "bg-emerald-900/40 text-emerald-400" : "bg-red-900/40 text-red-400"}`}>
                        {t.side}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-gray-300">${t.price.toFixed(4)}</td>
                    <td className="px-4 py-2.5 text-gray-300">{t.qty}</td>
                    <td className={`px-4 py-2.5 font-medium ${t.realized_pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                      {t.realized_pnl === 0 ? "—" : fmtUSD(t.realized_pnl)}
                    </td>
                    <td className="px-4 py-2.5 text-amber-600 text-xs">
                      -{t.commission.toFixed(5)} {t.commission_asset}
                    </td>
                  </tr>
                ))}
                {sorted.length === 0 && (
                  <tr>
                    <td colSpan={7} className="px-4 py-12 text-center">
                      <div className="flex flex-col items-center space-y-3">
                        <p className="text-sm font-semibold text-gray-400">
                          🤖 {activeBots} Bot{activeBots !== 1 ? "s" : ""} Active
                        </p>
                        <p className="text-xs text-gray-500">
                          {openPositions > 0
                            ? `${openPositions} position${openPositions !== 1 ? "s" : ""} open • Waiting for trades to close`
                            : "Bots are scanning for entry signals"}
                        </p>
                        <div className="bg-gray-900/50 rounded-lg px-4 py-2 mt-2">
                          <p className="text-xs text-gray-400">
                            Trade history will appear here once positions are closed
                          </p>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
