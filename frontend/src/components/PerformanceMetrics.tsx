import { useMemo } from "react";

interface Trade {
  time: number;
  realized_pnl: number;
  commission: number;
  symbol: string;
  side: string;
}

interface PerformanceMetricsProps {
  trades: Trade[];
  startCapital?: number;
  totalEquity?: number;
}

function MetricCard({
  label,
  value,
  color = "text-white",
  size = "normal"
}: {
  label: string;
  value: string;
  color?: string;
  size?: "small" | "normal" | "large";
}) {
  const textSize = size === "large" ? "text-xl md:text-2xl" : size === "small" ? "text-sm" : "text-base md:text-lg";

  return (
    <div className="bg-gray-800/50 border border-gray-700 rounded-lg p-3">
      <p className="text-[10px] md:text-xs text-gray-500 uppercase tracking-wider mb-1">
        {label}
      </p>
      <p className={`${textSize} font-bold ${color}`}>{value}</p>
    </div>
  );
}

export default function PerformanceMetrics({ trades, startCapital = 1000, totalEquity }: PerformanceMetricsProps) {
  const metrics = useMemo(() => {
    // Filter only closing trades (with P&L)
    const closingTrades = trades.filter(t => t.realized_pnl !== 0);

    if (closingTrades.length === 0) {
      return null;
    }

    // Winning and losing trades
    const winners = closingTrades.filter(t => t.realized_pnl > 0);
    const losers = closingTrades.filter(t => t.realized_pnl < 0);

    // Basic metrics
    const totalTrades = closingTrades.length;
    const winningTrades = winners.length;
    const losingTrades = losers.length;
    const winRate = (winningTrades / totalTrades) * 100;

    // P&L metrics
    const grossProfit = winners.reduce((sum, t) => sum + t.realized_pnl, 0);
    const grossLoss = Math.abs(losers.reduce((sum, t) => sum + t.realized_pnl, 0));
    const netProfit = grossProfit - grossLoss;
    const totalCommissions = trades.reduce((sum, t) => sum + t.commission, 0);

    // Average metrics
    const avgWin = winningTrades > 0 ? grossProfit / winningTrades : 0;
    const avgLoss = losingTrades > 0 ? grossLoss / losingTrades : 0;
    const avgTrade = netProfit / totalTrades;

    // Best/Worst
    const largestWin = winners.length > 0 ? Math.max(...winners.map(t => t.realized_pnl)) : 0;
    const largestLoss = losers.length > 0 ? Math.abs(Math.min(...losers.map(t => t.realized_pnl))) : 0;

    // Profit Factor
    const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0;

    // Risk/Reward Ratio
    const riskRewardRatio = avgLoss > 0 ? avgWin / avgLoss : avgWin > 0 ? Infinity : 0;

    // Expectancy
    const expectancy = (winRate / 100) * avgWin - ((100 - winRate) / 100) * avgLoss;

    // Calculate equity curve for drawdown
    const equityCurve: number[] = [];
    let runningEquity = startCapital;

    closingTrades
      .sort((a, b) => a.time - b.time)
      .forEach(t => {
        runningEquity += t.realized_pnl;
        equityCurve.push(runningEquity);
      });

    // Max Drawdown
    let maxDrawdown = 0;
    let peak = startCapital;

    equityCurve.forEach(equity => {
      if (equity > peak) peak = equity;
      const drawdown = ((peak - equity) / peak) * 100;
      if (drawdown > maxDrawdown) maxDrawdown = drawdown;
    });

    // Sharpe Ratio (simplified - assumes daily trades, risk-free rate = 0)
    const returns = closingTrades.map(t => t.realized_pnl);
    const avgReturn = returns.reduce((sum, r) => sum + r, 0) / returns.length;
    const variance = returns.reduce((sum, r) => sum + Math.pow(r - avgReturn, 2), 0) / returns.length;
    const stdDev = Math.sqrt(variance);
    const sharpeRatio = stdDev > 0 ? (avgReturn / stdDev) * Math.sqrt(252) : 0;

    // Return on capital — net of commissions, over current equity
    const capital = totalEquity && totalEquity > 0 ? totalEquity : startCapital;
    const returnOnCapital = ((netProfit - totalCommissions) / capital) * 100;

    // Calculate average trade duration (pair SELL→BUY for each symbol)
    const tradesBySymbol: Record<string, Trade[]> = {};
    trades.forEach(t => {
      if (!tradesBySymbol[t.symbol]) tradesBySymbol[t.symbol] = [];
      tradesBySymbol[t.symbol].push(t);
    });

    let totalWinDuration = 0;
    let totalLossDuration = 0;
    let winDurationCount = 0;
    let lossDurationCount = 0;

    Object.values(tradesBySymbol).forEach(symbolTrades => {
      const sorted = [...symbolTrades].sort((a, b) => a.time - b.time);

      for (let i = 0; i < sorted.length - 1; i++) {
        const entry = sorted[i];
        const exit = sorted[i + 1];

        // Pair SELL (entry) with BUY (exit)
        if (entry.side === "SELL" && exit.side === "BUY" && exit.realized_pnl !== 0) {
          const durationMs = exit.time - entry.time;
          const durationMinutes = durationMs / (1000 * 60);

          if (exit.realized_pnl > 0) {
            totalWinDuration += durationMinutes;
            winDurationCount++;
          } else {
            totalLossDuration += durationMinutes;
            lossDurationCount++;
          }
        }
      }
    });

    const avgWinDuration = winDurationCount > 0 ? totalWinDuration / winDurationCount : 0;
    const avgLossDuration = lossDurationCount > 0 ? totalLossDuration / lossDurationCount : 0;

    return {
      totalTrades,
      winningTrades,
      losingTrades,
      winRate,
      grossProfit,
      grossLoss,
      netProfit,
      totalCommissions,
      avgWin,
      avgLoss,
      avgTrade,
      largestWin,
      largestLoss,
      profitFactor,
      riskRewardRatio,
      expectancy,
      maxDrawdown,
      sharpeRatio,
      returnOnCapital,
      avgWinDuration,
      avgLossDuration,
    };
  }, [trades, startCapital, totalEquity]);

  if (!metrics) {
    return (
      <div className="bg-gray-800 border border-gray-700 rounded-xl p-6 text-center">
        <p className="text-gray-500 text-sm">No trades data available</p>
        <p className="text-gray-600 text-xs mt-2">Performance metrics will appear after closing trades</p>
      </div>
    );
  }

  const fmt = (n: number) => n.toFixed(2);
  const fmtUSD = (n: number) => `$${Math.abs(n).toFixed(2)}`;
  const fmtPct = (n: number) => `${n.toFixed(2)}%`;
  const fmtDuration = (minutes: number) => {
    if (minutes < 60) return `${minutes.toFixed(0)}m`;
    const hours = Math.floor(minutes / 60);
    const mins = Math.floor(minutes % 60);
    return `${hours}h ${mins}m`;
  };

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-xl p-4 md:p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-700 pb-3">
        <h2 className="text-lg md:text-xl font-bold text-white">Performance Report</h2>
        <span className="text-xs text-gray-500">{metrics.totalTrades} trades</span>
      </div>

      {/* Key Metrics Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
        <MetricCard
          label="Net Profit"
          value={fmtUSD(metrics.netProfit)}
          color={metrics.netProfit >= 0 ? "text-emerald-400" : "text-red-400"}
          size="large"
        />
        <MetricCard
          label="Win Rate"
          value={fmtPct(metrics.winRate)}
          color={metrics.winRate >= 50 ? "text-emerald-400" : "text-amber-400"}
          size="large"
        />
        <MetricCard
          label="Profit Factor"
          value={metrics.profitFactor === Infinity ? "∞" : fmt(metrics.profitFactor)}
          color={metrics.profitFactor >= 2 ? "text-emerald-400" : metrics.profitFactor >= 1 ? "text-amber-400" : "text-red-400"}
          size="large"
        />
        <MetricCard
          label="Total Trades"
          value={String(metrics.totalTrades)}
          color="text-white"
          size="large"
        />
        <MetricCard
          label="Winners"
          value={String(metrics.winningTrades)}
          color="text-emerald-400"
          size="large"
        />
        <MetricCard
          label="Losers"
          value={String(metrics.losingTrades)}
          color="text-red-400"
          size="large"
        />
      </div>

      {/* Secondary Metrics - 2 Rows */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <MetricCard label="Gross Profit" value={fmtUSD(metrics.grossProfit)} color="text-emerald-400" />
        <MetricCard label="Gross Loss" value={fmtUSD(metrics.grossLoss)} color="text-red-400" />
        <MetricCard label="Commissions" value={fmtUSD(metrics.totalCommissions)} color="text-amber-400" />
        <MetricCard label="Avg Trade" value={fmtUSD(metrics.avgTrade)} color={metrics.avgTrade >= 0 ? "text-emerald-400" : "text-red-400"} />
        <MetricCard label="ROI" value={fmtPct(metrics.returnOnCapital)} color={metrics.returnOnCapital >= 0 ? "text-emerald-400" : "text-red-400"} />
        <MetricCard label="Sharpe Ratio" value={fmt(metrics.sharpeRatio)} color={metrics.sharpeRatio >= 1.5 ? "text-emerald-400" : metrics.sharpeRatio >= 1 ? "text-amber-400" : "text-red-400"} />
      </div>

      {/* Win/Loss Analysis */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricCard label="Avg Winner" value={fmtUSD(metrics.avgWin)} color="text-emerald-400" />
        <MetricCard label="Avg Loser" value={fmtUSD(metrics.avgLoss)} color="text-red-400" />
        <MetricCard label="Largest Win" value={fmtUSD(metrics.largestWin)} color="text-emerald-400" />
        <MetricCard label="Largest Loss" value={fmtUSD(metrics.largestLoss)} color="text-red-400" />
      </div>

      {/* Duration Analysis */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricCard
          label="Avg Win Duration"
          value={metrics.avgWinDuration > 0 ? fmtDuration(metrics.avgWinDuration) : "—"}
          color="text-emerald-400"
        />
        <MetricCard
          label="Avg Loss Duration"
          value={metrics.avgLossDuration > 0 ? fmtDuration(metrics.avgLossDuration) : "—"}
          color="text-red-400"
        />
        <MetricCard
          label="Risk/Reward"
          value={metrics.riskRewardRatio === Infinity ? "∞" : fmt(metrics.riskRewardRatio)}
          color={metrics.riskRewardRatio >= 2 ? "text-emerald-400" : "text-amber-400"}
        />
        <MetricCard
          label="Max Drawdown"
          value={fmtPct(metrics.maxDrawdown)}
          color={metrics.maxDrawdown <= 10 ? "text-emerald-400" : metrics.maxDrawdown <= 20 ? "text-amber-400" : "text-red-400"}
        />
      </div>
    </div>
  );
}
