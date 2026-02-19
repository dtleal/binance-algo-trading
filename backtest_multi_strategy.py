"""Backtest MultiStrategyBot with different strategy combinations.

Tests:
1. VWAPPullback 5min alone
2. MomShort 1min alone
3. Both with FirstSignalCombiner (priority to VWAPPullback)
4. Both with AllAgreeCombiner (only when both agree)
5. Both with WeightedCombiner (weighted by confidence)
"""

import pandas as pd
from typing import List, Optional
from dataclasses import dataclass

from trader.multi_strategy import (
    MultiStrategyBot, TradeDecision,
    FirstSignalCombiner, AllAgreeCombiner, WeightedCombiner
)
from trader.strategy_adapters import VWAPPullbackAdapter, MomShortAdapter


# ── Test Configuration ───────────────────────────────────────────────────────
CSV_FILE_1M = "ethusdt_1m_klines.csv"
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

# MomShort 1min params (example - adjust based on sweep results)
MOM_1M_PARAMS = {
    "tp_pct": 0.03,
    "sl_pct": 0.015,
    "min_bars": 3,
    "confirm_bars": 2,
    "vwap_prox": 0.005,
    "vwap_window_days": 10,
    "entry_start_min": 60,
    "entry_cutoff_min": 1320,
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
    tp_price: float
    sl_price: float
    exit_reason: str
    pnl_pct: float
    pnl_dollars: float
    capital: float
    strategy: str


def load_and_prepare_data(csv_file: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load 1min data and prepare 5min aggregated data.

    Returns:
        (df_1m, df_5m): 1-minute and 5-minute dataframes
    """
    df = pd.read_csv(csv_file)
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.set_index("time", inplace=True)
    df["date"] = df.index.date
    df["timestamp_ms"] = df["open_time"]

    # Aggregate to 5min for VWAPPullback
    df_5m = df.resample("5min").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "open_time": "first"  # Keep original open_time
    }).dropna()

    df_5m["timestamp_ms"] = df_5m["open_time"]
    df_5m["date"] = df_5m.index.date

    return df, df_5m


def run_backtest(
    df_1m: pd.DataFrame,
    df_5m: pd.DataFrame,
    bot: MultiStrategyBot,
    name: str
) -> tuple[List[Trade], float]:
    """Run backtest for a MultiStrategyBot configuration.

    FIXED: Process only 5min candles for 5min strategies.
    This prevents duplicate signals and incorrect timeframe data.

    Args:
        df_1m: 1-minute candle data (used for exit scanning)
        df_5m: 5-minute candle data (used for signals)
        bot: Configured MultiStrategyBot
        name: Test name for logging

    Returns:
        (trades, final_capital)
    """
    print(f"\n{'='*80}")
    print(f"  {name}")
    print(f"  Strategies: {', '.join(bot.strategy_names)}")
    print(f"{'='*80}")

    capital = INITIAL_CAPITAL
    trades = []

    # Process by date
    for date in df_5m["date"].unique():
        bot.reset_daily()

        day_df_5m = df_5m[df_5m["date"] == date]
        day_df_1m = df_1m[df_1m["date"] == date]

        if len(day_df_1m) == 0:
            continue

        rows_5m = list(day_df_5m.itertuples())
        rows_1m = list(day_df_1m.itertuples())

        i = 0
        while i < len(rows_5m):
            r = rows_5m[i]

            # Feed 5min candle to bot
            decision = bot.on_candle(
                close=r.close,
                high=r.high,
                low=r.low,
                volume=r.volume,
                timestamp=int(r.timestamp_ms),
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

            # Scan forward for exit using 1min data for better precision
            exit_price = None
            exit_reason = None
            exit_idx_1m = None

            # Find where we are in 1min data
            entry_idx_1m = None
            for j, r1m in enumerate(rows_1m):
                if r1m.Index >= entry_time:
                    entry_idx_1m = j
                    break

            if entry_idx_1m is None:
                # Fallback: close at EOD
                exit_price = rows_1m[-1].close
                exit_reason = "EOD"
                exit_idx_1m = len(rows_1m) - 1
            else:
                # Scan forward in 1min candles
                for j in range(entry_idx_1m + 1, len(rows_1m)):
                    scan = rows_1m[j]

                    if direction == "long":
                        if scan.high >= tp_price:
                            exit_price = tp_price
                            exit_reason = "TP"
                            exit_idx_1m = j
                            break
                        elif scan.low <= sl_price:
                            exit_price = sl_price
                            exit_reason = "SL"
                            exit_idx_1m = j
                            break
                    else:  # short
                        if scan.low <= tp_price:
                            exit_price = tp_price
                            exit_reason = "TP"
                            exit_idx_1m = j
                            break
                        elif scan.high >= sl_price:
                            exit_price = sl_price
                            exit_reason = "SL"
                            exit_idx_1m = j
                            break

                # If no TP/SL, close at EOD
                if exit_price is None:
                    exit_price = rows_1m[-1].close
                    exit_reason = "EOD"
                    exit_idx_1m = len(rows_1m) - 1

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
                exit_time=rows_1m[exit_idx_1m].Index,
                direction=direction,
                entry_price=entry_price,
                exit_price=exit_price,
                tp_price=tp_price,
                sl_price=sl_price,
                exit_reason=exit_reason,
                pnl_pct=pnl_pct * 100,
                pnl_dollars=net,
                capital=capital,
                strategy=decision.metadata.get("strategy", "Multi"),
            ))

            bot.reset_signal()
            i += 1

    return trades, capital


def analyze_results(trades: List[Trade], capital: float, name: str):
    """Print analysis of backtest results."""
    if not trades:
        print(f"\n❌ No trades executed!")
        return

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
    print(f"  Max DD:        {dd.max():.2f}%")

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
        "max_dd": dd.max(),
        "final_capital": capital
    }


def main():
    print("Loading data...")
    df_1m, df_5m = load_and_prepare_data(CSV_FILE_1M)
    print(f"Loaded {len(df_1m):,} 1min candles, {len(df_5m):,} 5min candles")

    results = []

    # Test 1: VWAPPullback 5min alone
    vwap_strategy = VWAPPullbackAdapter(**VWAP_5M_PARAMS)
    bot1 = MultiStrategyBot(
        strategies=[vwap_strategy],
        combiner=FirstSignalCombiner()
    )
    trades1, cap1 = run_backtest(df_1m, df_5m, bot1, "Test 1: VWAPPullback 5min (alone)")
    results.append(analyze_results(trades1, cap1, "VWAPPullback 5m"))

    # Test 2: MomShort 1min alone
    mom_strategy = MomShortAdapter(**MOM_1M_PARAMS)
    bot2 = MultiStrategyBot(
        strategies=[mom_strategy],
        combiner=FirstSignalCombiner()
    )
    trades2, cap2 = run_backtest(df_1m, df_5m, bot2, "Test 2: MomShort 1min (alone)")
    results.append(analyze_results(trades2, cap2, "MomShort 1m"))

    # Test 3: Both with FirstSignal (priority to VWAPPullback)
    bot3 = MultiStrategyBot(
        strategies=[
            VWAPPullbackAdapter(**VWAP_5M_PARAMS),
            MomShortAdapter(**MOM_1M_PARAMS)
        ],
        combiner=FirstSignalCombiner()
    )
    trades3, cap3 = run_backtest(df_1m, df_5m, bot3, "Test 3: FirstSignal (VWAP priority)")
    results.append(analyze_results(trades3, cap3, "FirstSignal"))

    # Test 4: Both with AllAgree (only when both signal)
    bot4 = MultiStrategyBot(
        strategies=[
            VWAPPullbackAdapter(**VWAP_5M_PARAMS),
            MomShortAdapter(**MOM_1M_PARAMS)
        ],
        combiner=AllAgreeCombiner(conviction_multiplier=1.5)
    )
    trades4, cap4 = run_backtest(df_1m, df_5m, bot4, "Test 4: AllAgree (both must signal)")
    results.append(analyze_results(trades4, cap4, "AllAgree"))

    # Test 5: Both with Weighted
    bot5 = MultiStrategyBot(
        strategies=[
            VWAPPullbackAdapter(**VWAP_5M_PARAMS),
            MomShortAdapter(**MOM_1M_PARAMS)
        ],
        combiner=WeightedCombiner(weights={
            "VWAPPullback(EMA=100,VWAP=1d)": 1.5,  # Higher weight
            "MomShort(VWAP=10d)": 1.0
        })
    )
    trades5, cap5 = run_backtest(df_1m, df_5m, bot5, "Test 5: Weighted (VWAP=1.5x, Mom=1.0x)")
    results.append(analyze_results(trades5, cap5, "Weighted"))

    # Summary comparison
    print("\n\n" + "="*80)
    print("  📊 COMPARISON SUMMARY")
    print("="*80)
    print(f"{'Strategy':<25} {'Trades':>8} {'Return':>10} {'WinRate':>10} {'MaxDD':>10} {'Final':>12}")
    print("-"*80)

    for r in results:
        print(f"{r['name']:<25} {r['trades']:>8} {r['return_pct']:>9.2f}% "
              f"{r['win_rate']:>9.1f}% {r['max_dd']:>9.2f}% ${r['final_capital']:>10.2f}")

    print("="*80)


if __name__ == "__main__":
    main()
