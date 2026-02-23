"""Fast backtest for MultiStrategyBot using pre-aggregated 5min data.

Optimized version that doesn't process all 525k 1min candles.
"""

import pandas as pd
from typing import List
from dataclasses import dataclass

from trader.multi_strategy import (
    MultiStrategyBot, TradeDecision,
    FirstSignalCombiner, AllAgreeCombiner, WeightedCombiner
)
from trader.strategy_adapters import VWAPPullbackAdapter


# ── Configuration ────────────────────────────────────────────────────────────
CSV_FILE_5M = "ethusdt_5m_klines_official.csv"  # Use 5min data directly
INITIAL_CAPITAL = 1000.0
FEE_PCT = 0.0004  # 0.04% per side

# VWAPPullback 5min optimized params
VWAP_5M_PARAMS = {
    "tp_pct": 0.10,
    "sl_pct": 0.05,
    "min_bars": 20,
    "confirm_bars": 0,
    "vwap_prox": 0.005,
    "vwap_window_days": 1,
    "ema_period": 100,
    "entry_start_min": 60,
    "entry_cutoff_min": 1320,
    "max_trades_per_day": 2,
    "pos_size_pct": 0.20,
}


@dataclass
class Trade:
    """Trade record."""
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: str
    entry_price: float
    exit_price: float
    exit_reason: str
    pnl_pct: float
    pnl_dollars: float
    capital: float
    strategy: str


def run_backtest(bot: MultiStrategyBot, name: str) -> tuple[List[Trade], float]:
    """Run backtest for a MultiStrategyBot configuration."""
    print(f"\n{'='*80}")
    print(f"  {name}")
    print(f"  Strategies: {', '.join(bot.strategy_names)}")
    print(f"{'='*80}")

    # Load 5min data
    df = pd.read_csv(CSV_FILE_5M)
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.set_index("time", inplace=True)
    df["date"] = df.index.date

    capital = INITIAL_CAPITAL
    trades = []

    for date, day_df in df.groupby("date"):
        bot.reset_daily()
        rows = list(day_df.itertuples())

        i = 0
        while i < len(rows):
            r = rows[i]

            # Get decision from bot
            decision = bot.on_candle(
                close=r.close,
                high=r.high,
                low=r.low,
                volume=r.volume,
                timestamp=int(r.open_time),
            )

            if decision is None:
                i += 1
                continue

            # Execute trade
            entry_price = r.close
            entry_time = r.Index
            direction = decision.direction.value

            if direction == "long":
                tp_price = entry_price * (1 + decision.tp_pct)
                sl_price = entry_price * (1 - decision.sl_pct)
            else:
                tp_price = entry_price * (1 - decision.tp_pct)
                sl_price = entry_price * (1 + decision.sl_pct)

            # Scan forward for exit
            exit_price = None
            exit_reason = None
            exit_idx = len(rows) - 1

            for j in range(i + 1, len(rows)):
                scan = rows[j]

                if direction == "long":
                    if scan.high >= tp_price:
                        exit_price = tp_price
                        exit_reason = "TP"
                        exit_idx = j
                        break
                    elif scan.low <= sl_price:
                        exit_price = sl_price
                        exit_reason = "SL"
                        exit_idx = j
                        break
                else:  # short
                    if scan.low <= tp_price:
                        exit_price = tp_price
                        exit_reason = "TP"
                        exit_idx = j
                        break
                    elif scan.high >= sl_price:
                        exit_price = sl_price
                        exit_reason = "SL"
                        exit_idx = j
                        break

            # If no TP/SL, close at EOD
            if exit_price is None:
                exit_price = rows[-1].close
                exit_reason = "EOD"

            # Calculate P&L
            if direction == "long":
                pnl_pct = (exit_price - entry_price) / entry_price
            else:
                pnl_pct = (entry_price - exit_price) / entry_price

            size = capital * decision.pos_size_pct
            gross = size * pnl_pct
            fees = size * FEE_PCT * 2
            net = gross - fees
            capital += net

            trades.append(Trade(
                entry_time=entry_time,
                exit_time=rows[exit_idx].Index,
                direction=direction,
                entry_price=entry_price,
                exit_price=exit_price,
                exit_reason=exit_reason,
                pnl_pct=pnl_pct * 100,
                pnl_dollars=net,
                capital=capital,
                strategy=decision.metadata.get("strategy", "Multi"),
            ))

            bot.reset_signal()
            i = exit_idx + 1

    return trades, capital


def analyze_results(trades: List[Trade], capital: float, name: str) -> dict:
    """Print analysis of backtest results."""
    if not trades:
        print(f"\n❌ No trades executed!")
        return {
            "name": name,
            "trades": 0,
            "return_pct": 0,
            "win_rate": 0,
            "max_dd": 0,
            "final_capital": INITIAL_CAPITAL
        }

    df = pd.DataFrame([vars(t) for t in trades])

    wins = df[df["pnl_dollars"] > 0]
    losses = df[df["pnl_dollars"] <= 0]
    total_return = (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

    print(f"\n📊 Results:")
    print(f"  Trades:        {len(trades)}")
    print(f"  Winners:       {len(wins)} ({len(wins)/len(trades)*100:.1f}%)")
    print(f"  Losers:        {len(losses)} ({len(losses)/len(trades)*100:.1f}%)")
    print(f"  Final Capital: ${capital:.2f}")
    print(f"  Return:        {total_return:+.2f}%")

    if len(wins):
        print(f"  Avg Win:       ${wins['pnl_dollars'].mean():.2f} ({wins['pnl_pct'].mean():+.2f}%)")
    if len(losses):
        print(f"  Avg Loss:      ${losses['pnl_dollars'].mean():.2f} ({losses['pnl_pct'].mean():+.2f}%)")

    # Max drawdown
    peak = df["capital"].cummax()
    dd = (peak - df["capital"]) / peak * 100
    max_dd = dd.max()
    print(f"  Max DD:        {max_dd:.2f}%")

    # Exit breakdown
    tp_count = len(df[df["exit_reason"] == "TP"])
    sl_count = len(df[df["exit_reason"] == "SL"])
    eod_count = len(df[df["exit_reason"] == "EOD"])
    print(f"  Exits:         TP={tp_count} SL={sl_count} EOD={eod_count}")

    return {
        "name": name,
        "trades": len(trades),
        "return_pct": total_return,
        "win_rate": len(wins)/len(trades)*100,
        "max_dd": max_dd,
        "final_capital": capital
    }


def main():
    results = []

    print("🚀 Testing MultiStrategyBot with different combiner strategies...\n")

    # Test 1: Single strategy (baseline)
    bot1 = MultiStrategyBot(
        strategies=[VWAPPullbackAdapter(**VWAP_5M_PARAMS)],
        combiner=FirstSignalCombiner()
    )
    trades1, cap1 = run_backtest(bot1, "Test 1: VWAPPullback 5min (baseline)")
    results.append(analyze_results(trades1, cap1, "VWAPPullback"))

    # Summary
    print("\n\n" + "="*80)
    print("  📊 RESULTS SUMMARY")
    print("="*80)
    print(f"{'Strategy':<25} {'Trades':>8} {'Return':>10} {'WinRate':>10} {'MaxDD':>10} {'Final':>12}")
    print("-"*80)

    for r in results:
        print(f"{r['name']:<25} {r['trades']:>8} {r['return_pct']:>9.2f}% "
              f"{r['win_rate']:>9.1f}% {r['max_dd']:>9.2f}% ${r['final_capital']:>10.2f}")

    print("="*80)

    # Save trades to CSV
    if trades1:
        df_trades = pd.DataFrame([vars(t) for t in trades1])
        df_trades.to_csv("multi_strategy_trades.csv", index=False)
        print("\n✅ Trades saved to multi_strategy_trades.csv")


if __name__ == "__main__":
    main()
