import { useMemo } from "react";

interface Trade {
  symbol: string;
  time: number;
  realized_pnl: number;
  side: string;
}

interface PerformanceRankingsProps {
  trades: Trade[];
}

function RankingCard({
  title,
  items,
}: {
  title: string;
  items: { label: string; value: string; color: string; subValue?: string }[];
}) {
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-xl p-4">
      <h3 className="text-sm font-semibold text-gray-300 mb-3 border-b border-gray-700 pb-2">
        {title}
      </h3>
      <div className="space-y-2">
        {items.length === 0 ? (
          <p className="text-xs text-gray-500 text-center py-4">No data available</p>
        ) : (
          items.map((item, idx) => (
            <div key={idx} className="flex items-center justify-between text-sm hover:bg-gray-900/50 px-2 py-1.5 rounded">
              <div className="flex items-center gap-2">
                <span className="text-gray-600 text-xs w-5">#{idx + 1}</span>
                <span className="text-gray-300 font-medium">{item.label}</span>
              </div>
              <div className="text-right">
                <p className={`font-bold ${item.color}`}>{item.value}</p>
                {item.subValue && <p className="text-xs text-gray-500">{item.subValue}</p>}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

export default function PerformanceRankings({ trades }: PerformanceRankingsProps) {
  const rankings = useMemo(() => {
    const closingTrades = trades.filter(t => t.realized_pnl !== 0);

    if (closingTrades.length === 0) {
      return { bestAssets: [], worstAssets: [], bestHours: [], bestRiskReward: [] };
    }

    // ═══ Best Assets (by total P&L) ═══
    const assetStats: Record<string, { pnl: number; trades: number; wins: number }> = {};
    closingTrades.forEach(t => {
      if (!assetStats[t.symbol]) {
        assetStats[t.symbol] = { pnl: 0, trades: 0, wins: 0 };
      }
      assetStats[t.symbol].pnl += t.realized_pnl;
      assetStats[t.symbol].trades++;
      if (t.realized_pnl > 0) assetStats[t.symbol].wins++;
    });

    const sortedAssets = Object.entries(assetStats)
      .map(([symbol, stats]) => ({
        symbol,
        pnl: stats.pnl,
        winRate: (stats.wins / stats.trades) * 100,
        trades: stats.trades,
      }))
      .sort((a, b) => b.pnl - a.pnl);

    const bestAssets = sortedAssets.slice(0, 5).map(a => ({
      label: a.symbol.replace("USDT", ""),
      value: `$${a.pnl.toFixed(2)}`,
      color: a.pnl >= 0 ? "text-emerald-400" : "text-red-400",
      subValue: `${a.winRate.toFixed(0)}% WR · ${a.trades} trades`,
    }));

    const worstAssets = sortedAssets.slice(-3).reverse().map(a => ({
      label: a.symbol.replace("USDT", ""),
      value: `$${a.pnl.toFixed(2)}`,
      color: "text-red-400",
      subValue: `${a.winRate.toFixed(0)}% WR · ${a.trades} trades`,
    }));

    // ═══ Best Hours (by P&L) ═══
    const hourStats: Record<number, { pnl: number; trades: number }> = {};
    closingTrades.forEach(t => {
      const hour = new Date(t.time).getHours();
      if (!hourStats[hour]) hourStats[hour] = { pnl: 0, trades: 0 };
      hourStats[hour].pnl += t.realized_pnl;
      hourStats[hour].trades++;
    });

    const bestHours = Object.entries(hourStats)
      .map(([hour, stats]) => ({
        hour: parseInt(hour),
        pnl: stats.pnl,
        trades: stats.trades,
        avgPnl: stats.pnl / stats.trades,
      }))
      .sort((a, b) => b.pnl - a.pnl)
      .slice(0, 5)
      .map(h => ({
        label: `${h.hour.toString().padStart(2, "0")}:00 - ${(h.hour + 1).toString().padStart(2, "0")}:00`,
        value: `$${h.pnl.toFixed(2)}`,
        color: h.pnl >= 0 ? "text-emerald-400" : "text-red-400",
        subValue: `${h.trades} trades · Avg: $${h.avgPnl.toFixed(2)}`,
      }));

    // ═══ Best Risk/Reward (by symbol) ═══
    const symbolRR: Record<string, { wins: number; losses: number; totalWin: number; totalLoss: number; trades: number }> = {};
    closingTrades.forEach(t => {
      if (!symbolRR[t.symbol]) {
        symbolRR[t.symbol] = { wins: 0, losses: 0, totalWin: 0, totalLoss: 0, trades: 0 };
      }
      symbolRR[t.symbol].trades++;
      if (t.realized_pnl > 0) {
        symbolRR[t.symbol].wins++;
        symbolRR[t.symbol].totalWin += t.realized_pnl;
      } else {
        symbolRR[t.symbol].losses++;
        symbolRR[t.symbol].totalLoss += Math.abs(t.realized_pnl);
      }
    });

    const bestRiskReward = Object.entries(symbolRR)
      .filter(([, stats]) => stats.wins > 0 && stats.losses > 0 && stats.trades >= 3) // Min 3 trades
      .map(([symbol, stats]) => {
        const avgWin = stats.totalWin / stats.wins;
        const avgLoss = stats.totalLoss / stats.losses;
        const rr = avgWin / avgLoss;
        const winRate = (stats.wins / stats.trades) * 100;
        return {
          symbol,
          rr,
          winRate,
          trades: stats.trades,
        };
      })
      .sort((a, b) => b.rr - a.rr)
      .slice(0, 5)
      .map(s => ({
        label: s.symbol.replace("USDT", ""),
        value: `${s.rr.toFixed(2)}:1`,
        color: s.rr >= 2 ? "text-emerald-400" : "text-amber-400",
        subValue: `${s.winRate.toFixed(0)}% WR · ${s.trades} trades`,
      }));

    return { bestAssets, worstAssets, bestHours, bestRiskReward };
  }, [trades]);

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
      <RankingCard title="🏆 Top Performing Assets" items={rankings.bestAssets} />
      <RankingCard title="⏰ Best Trading Hours" items={rankings.bestHours} />
      <RankingCard title="📊 Best Risk/Reward Ratio" items={rankings.bestRiskReward} />
      <RankingCard title="⚠️ Worst Performing Assets" items={rankings.worstAssets} />
    </div>
  );
}
