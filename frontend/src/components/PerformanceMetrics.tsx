import { useMemo } from "react";

interface Trade {
  time: number;
  realized_pnl: number;
  commission: number;
}

interface PerformanceMetricsProps {
  trades: Trade[];
  startCapital?: number;
}

function MetricCard({
  label,
  value,
  subValue,
  color = "text-white",
  size = "normal"
}: {
  label: string;
  value: string;
  subValue?: string;
  color?: string;
  size?: "small" | "normal" | "large";
}) {
  const textSize = size === "large" ? "text-2xl" : size === "small" ? "text-base" : "text-xl";

  return (
    <div className="bg-gray-800/50 border border-gray-700 rounded-lg p-3 md:p-4">
      <p className="text-[10px] md:text-xs text-gray-500 uppercase tracking-wider mb-1">
        {label}
      </p>
      <p className={`${textSize} font-bold ${color}`}>{value}</p>
      {subValue && <p className="text-xs text-gray-500 mt-0.5">{subValue}</p>}
    </div>
  );
}

export default function PerformanceMetrics({ trades, startCapital = 1000 }: PerformanceMetricsProps) {
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
    const totalCommissions = closingTrades.reduce((sum, t) => sum + t.commission, 0);

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

    // Consecutive wins/losses
    let maxConsecutiveWins = 0;
    let maxConsecutiveLosses = 0;
    let currentWinStreak = 0;
    let currentLossStreak = 0;

    closingTrades.forEach(t => {
      if (t.realized_pnl > 0) {
        currentWinStreak++;
        currentLossStreak = 0;
        maxConsecutiveWins = Math.max(maxConsecutiveWins, currentWinStreak);
      } else {
        currentLossStreak++;
        currentWinStreak = 0;
        maxConsecutiveLosses = Math.max(maxConsecutiveLosses, currentLossStreak);
      }
    });

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
    const sharpeRatio = stdDev > 0 ? (avgReturn / stdDev) * Math.sqrt(252) : 0; // Annualized

    // Return on capital
    const returnOnCapital = (netProfit / startCapital) * 100;

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
      maxConsecutiveWins,
      maxConsecutiveLosses,
      maxDrawdown,
      sharpeRatio,
      returnOnCapital,
    };
  }, [trades, startCapital]);

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

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg md:text-xl font-bold text-white">Performance Report</h2>
        <span className="text-xs text-gray-500">{metrics.totalTrades} trades</span>
      </div>

      {/* Key Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricCard
          label="Net Profit"
          value={fmtUSD(metrics.netProfit)}
          color={metrics.netProfit >= 0 ? "text-emerald-400" : "text-red-400"}
          size="large"
        />
        <MetricCard
          label="Win Rate"
          value={fmtPct(metrics.winRate)}
          subValue={`${metrics.winningTrades}W / ${metrics.losingTrades}L`}
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
          label="Sharpe Ratio"
          value={fmt(metrics.sharpeRatio)}
          color={metrics.sharpeRatio >= 1.5 ? "text-emerald-400" : metrics.sharpeRatio >= 1 ? "text-amber-400" : "text-red-400"}
          size="large"
        />
      </div>

      {/* P&L Details */}
      <div>
        <h3 className="text-sm font-semibold text-gray-300 mb-3">P&L Breakdown</h3>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
          <MetricCard
            label="Gross Profit"
            value={fmtUSD(metrics.grossProfit)}
            color="text-emerald-400"
          />
          <MetricCard
            label="Gross Loss"
            value={fmtUSD(metrics.grossLoss)}
            color="text-red-400"
          />
          <MetricCard
            label="Commissions"
            value={fmtUSD(metrics.totalCommissions)}
            color="text-amber-400"
          />
          <MetricCard
            label="Avg Trade"
            value={fmtUSD(metrics.avgTrade)}
            color={metrics.avgTrade >= 0 ? "text-emerald-400" : "text-red-400"}
          />
          <MetricCard
            label="ROI"
            value={fmtPct(metrics.returnOnCapital)}
            color={metrics.returnOnCapital >= 0 ? "text-emerald-400" : "text-red-400"}
          />
        </div>
      </div>

      {/* Win/Loss Analysis */}
      <div>
        <h3 className="text-sm font-semibold text-gray-300 mb-3">Win/Loss Analysis</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <MetricCard
            label="Avg Winner"
            value={fmtUSD(metrics.avgWin)}
            color="text-emerald-400"
          />
          <MetricCard
            label="Avg Loser"
            value={fmtUSD(metrics.avgLoss)}
            color="text-red-400"
          />
          <MetricCard
            label="Largest Win"
            value={fmtUSD(metrics.largestWin)}
            color="text-emerald-400"
          />
          <MetricCard
            label="Largest Loss"
            value={fmtUSD(metrics.largestLoss)}
            color="text-red-400"
          />
        </div>
      </div>

      {/* Risk Metrics */}
      <div>
        <h3 className="text-sm font-semibold text-gray-300 mb-3">Risk Metrics</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <MetricCard
            label="Risk/Reward"
            value={metrics.riskRewardRatio === Infinity ? "∞" : fmt(metrics.riskRewardRatio)}
            color={metrics.riskRewardRatio >= 2 ? "text-emerald-400" : "text-amber-400"}
          />
          <MetricCard
            label="Expectancy"
            value={fmtUSD(metrics.expectancy)}
            color={metrics.expectancy >= 0 ? "text-emerald-400" : "text-red-400"}
          />
          <MetricCard
            label="Max Drawdown"
            value={fmtPct(metrics.maxDrawdown)}
            color={metrics.maxDrawdown <= 10 ? "text-emerald-400" : metrics.maxDrawdown <= 20 ? "text-amber-400" : "text-red-400"}
          />
          <MetricCard
            label="Consecutive"
            value={`${metrics.maxConsecutiveWins}W / ${metrics.maxConsecutiveLosses}L`}
            color="text-gray-300"
          />
        </div>
      </div>
    </div>
  );
}
