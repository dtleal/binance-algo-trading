import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";
import { useBalance, usePositions, useTrades, useBotStates } from "../hooks/useApi";

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

/** Build daily equity curve from trade list */
function buildEquity(trades: { time: number; realized_pnl: number }[], startCapital = 1000) {
  const byDay: Record<string, number> = {};
  for (const t of trades) {
    const d = new Date(t.time).toISOString().slice(0, 10);
    byDay[d] = (byDay[d] ?? 0) + t.realized_pnl;
  }
  const sorted = Object.entries(byDay).sort();
  let cum = startCapital;
  return sorted.map(([date, pnl]) => {
    cum += pnl;
    return { date, equity: parseFloat(cum.toFixed(2)) };
  });
}

const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-xs">
      <p className="text-gray-400">{label}</p>
      <p className="text-emerald-400 font-bold">{fmtUSD(payload[0].value)}</p>
    </div>
  );
};

export default function Overview() {
  const { balance }    = useBalance();
  const { positions }  = usePositions();
  const { trades }     = useTrades(30);
  const { bots }       = useBotStates();

  const todayUTC = new Date().toISOString().slice(0, 10);
  const todayTrades  = trades.filter(
    (t) => new Date(t.time).toISOString().slice(0, 10) === todayUTC
  );
  const pnlToday = todayTrades.reduce((s, t) => s + t.realized_pnl, 0);
  const pnl30d   = trades.reduce((s, t) => s + t.realized_pnl, 0);
  const equityCurve = buildEquity(trades);

  const activeBots = Object.keys(bots).length;

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold text-white">Overview</h1>

      {/* Metric cards */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <Card
          label="USDT Balance"
          value={balance ? fmtUSD(balance.balance) : "—"}
          sub={balance ? `Available: ${fmtUSD(balance.available)}` : undefined}
        />
        <Card
          label="P&L Today"
          value={fmtUSD(pnlToday)}
          color={pnlToday >= 0 ? "text-emerald-400" : "text-red-400"}
          sub={`${todayTrades.length} trade${todayTrades.length !== 1 ? "s" : ""}`}
        />
        <Card
          label="P&L 30 Days"
          value={fmtUSD(pnl30d)}
          color={pnl30d >= 0 ? "text-emerald-400" : "text-red-400"}
          sub={`${trades.length} total trades`}
        />
        <Card
          label="Active Bots"
          value={String(activeBots)}
          sub={`${positions.length} open position${positions.length !== 1 ? "s" : ""}`}
        />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Equity curve */}
        <div className="xl:col-span-2 bg-gray-800 border border-gray-700 rounded-xl p-5">
          <p className="text-sm font-semibold text-gray-300 mb-4">Equity Curve — Last 30 Days</p>
          {equityCurve.length > 1 ? (
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={equityCurve}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis dataKey="date" tick={{ fill: "#6b7280", fontSize: 10 }}
                  tickFormatter={(v) => v.slice(5)} />
                <YAxis tick={{ fill: "#6b7280", fontSize: 10 }}
                  tickFormatter={(v) => `$${v}`} width={60} />
                <Tooltip content={<CustomTooltip />} />
                <Line type="monotone" dataKey="equity" stroke="#10b981"
                  strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-[220px] flex items-center justify-center text-gray-600 text-sm">
              No trade history yet
            </div>
          )}
        </div>

        {/* Recent trades */}
        <div className="bg-gray-800 border border-gray-700 rounded-xl p-5">
          <p className="text-sm font-semibold text-gray-300 mb-4">Recent Trades</p>
          <div className="space-y-2 overflow-y-auto max-h-[240px]">
            {trades.slice(0, 15).map((t, i) => (
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
            ))}
            {trades.length === 0 && (
              <p className="text-gray-600 text-xs text-center py-8">No trades found</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
