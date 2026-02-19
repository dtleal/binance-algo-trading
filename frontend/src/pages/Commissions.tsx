import { useState, useMemo } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";
import { useCommissions } from "../hooks/useApi";

type Days = 7 | 30 | 90;

const DayTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  const v = payload[0].value as number;
  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-xs">
      <p className="text-gray-400">{label}</p>
      <p className="text-amber-400">-${v.toFixed(4)}</p>
    </div>
  );
};

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-xl p-4 text-center">
      <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">{label}</p>
      <p className="text-lg font-bold text-amber-400">{value}</p>
      {sub && <p className="text-xs text-gray-600 mt-0.5">{sub}</p>}
    </div>
  );
}

export default function Commissions() {
  const [days, setDays] = useState<Days>(30);
  const { data, isLoading } = useCommissions(days);

  const dailyChart = useMemo(() => {
    if (!data?.daily) return [];
    return Object.entries(data.daily)
      .sort()
      .map(([date, usdt]) => ({
        date: date.slice(5),
        usdt: parseFloat((usdt as number).toFixed(4)),
      }));
  }, [data]);

  const byAsset = data?.by_asset ?? {};
  const bySymbol = data?.by_symbol ?? {};
  const totalUsdt = data?.total_usdt ?? 0;

  // Avg daily commission
  const avgDaily =
    dailyChart.length > 0
      ? totalUsdt / dailyChart.length
      : 0;

  // Top symbol by fee
  const topSymbol = Object.entries(bySymbol).sort((a, b) => (b[1] as number) - (a[1] as number))[0];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Commissions</h1>
        <div className="flex gap-1 bg-gray-800 border border-gray-700 rounded-lg p-1">
          {([7, 30, 90] as Days[]).map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`px-3 py-1 rounded text-sm transition-colors ${
                days === d ? "bg-amber-600 text-white" : "text-gray-400 hover:text-white"
              }`}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="p-8 text-center text-gray-500 text-sm">Loading…</div>
      ) : (
        <>
          {/* Stat cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard
              label={`Total Fees (${days}d)`}
              value={`-$${totalUsdt.toFixed(4)}`}
              sub="USDT equivalent"
            />
            <StatCard
              label="Avg / Day"
              value={`-$${avgDaily.toFixed(4)}`}
            />
            <StatCard
              label="Top Symbol"
              value={topSymbol ? topSymbol[0].replace(/USDT$/, "") : "—"}
              sub={topSymbol ? `-$${(topSymbol[1] as number).toFixed(4)}` : undefined}
            />
            <StatCard
              label="Days Tracked"
              value={String(dailyChart.length)}
            />
          </div>

          {/* Daily bar chart */}
          {dailyChart.length > 1 && (
            <div className="bg-gray-800 border border-gray-700 rounded-xl p-5">
              <p className="text-sm font-semibold text-gray-300 mb-4">Daily Fees (USDT)</p>
              <ResponsiveContainer width="100%" height={160}>
                <BarChart data={dailyChart} barCategoryGap="20%">
                  <CartesianGrid strokeDasharray="3 3" stroke="#374151" vertical={false} />
                  <XAxis dataKey="date" tick={{ fill: "#6b7280", fontSize: 10 }} />
                  <YAxis
                    tick={{ fill: "#6b7280", fontSize: 10 }}
                    tickFormatter={(v) => `$${v}`}
                    width={55}
                  />
                  <Tooltip content={<DayTooltip />} />
                  <Bar dataKey="usdt" fill="#d97706" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Breakdown tables */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* By asset */}
            <div className="bg-gray-800 border border-gray-700 rounded-xl overflow-hidden">
              <p className="text-sm font-semibold text-gray-300 px-4 py-3 border-b border-gray-700">
                By Asset
              </p>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-gray-500 uppercase tracking-wider">
                    <th className="px-4 py-2 text-left">Asset</th>
                    <th className="px-4 py-2 text-right">Total Paid</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-700/30">
                  {Object.entries(byAsset).length === 0 ? (
                    <tr>
                      <td colSpan={2} className="px-4 py-6 text-center text-gray-600 text-xs">
                        No data
                      </td>
                    </tr>
                  ) : (
                    Object.entries(byAsset)
                      .sort((a, b) => (b[1] as number) - (a[1] as number))
                      .map(([asset, amount]) => (
                        <tr key={asset} className="hover:bg-gray-700/30 transition-colors">
                          <td className="px-4 py-2.5 font-medium text-white">{asset}</td>
                          <td className="px-4 py-2.5 text-right text-amber-400">
                            -{(amount as number).toFixed(6)}
                          </td>
                        </tr>
                      ))
                  )}
                </tbody>
              </table>
            </div>

            {/* By symbol */}
            <div className="bg-gray-800 border border-gray-700 rounded-xl overflow-hidden">
              <p className="text-sm font-semibold text-gray-300 px-4 py-3 border-b border-gray-700">
                By Symbol
              </p>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-gray-500 uppercase tracking-wider">
                    <th className="px-4 py-2 text-left">Symbol</th>
                    <th className="px-4 py-2 text-right">Fees (USDT)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-700/30">
                  {Object.entries(bySymbol).length === 0 ? (
                    <tr>
                      <td colSpan={2} className="px-4 py-6 text-center text-gray-600 text-xs">
                        No data
                      </td>
                    </tr>
                  ) : (
                    Object.entries(bySymbol)
                      .sort((a, b) => (b[1] as number) - (a[1] as number))
                      .map(([symbol, usdt]) => (
                        <tr key={symbol} className="hover:bg-gray-700/30 transition-colors">
                          <td className="px-4 py-2.5 font-medium text-white">
                            {symbol.replace(/USDT$/, "")}
                            <span className="text-gray-600 text-xs">/USDT</span>
                          </td>
                          <td className="px-4 py-2.5 text-right text-amber-400">
                            -${(usdt as number).toFixed(4)}
                          </td>
                        </tr>
                      ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
