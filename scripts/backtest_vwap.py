"""
Backtest: Intraday VWAP Rejection Short on AXSUSDC 1m candles.

Strategy:
  - Compute intraday VWAP (resets each day at 00:00 UTC)
  - Wait for price to spend >= MIN_BARS_ABOVE candles above VWAP
  - Enter SHORT when a candle closes back below VWAP (rejection)
  - Take-profit / stop-loss as % from entry
  - Force-close at END_OF_DAY if still open
  - Max 1 trade per day
"""

import pandas as pd

# ── Parameters ──────────────────────────────────────────────
CSV_FILE = "axsusdc_1m_klines.csv"
TAKE_PROFIT_PCT = 0.007     # 0.7% below entry
STOP_LOSS_PCT = 0.004       # 0.4% above entry  (R:R ≈ 1.75)
MIN_BARS_ABOVE = 5          # price must be above VWAP for ≥5 candles before rejection
ENTRY_START = "01:00"       # don't trade in the first hour (thin VWAP)
ENTRY_CUTOFF = "22:00"      # stop opening trades after 22:00 UTC
END_OF_DAY = "23:50"        # force-close deadline
FEE_PCT = 0.0004            # 0.04% taker fee per side (Binance futures)
INITIAL_CAPITAL = 1000.0    # USDC
POSITION_SIZE_PCT = 0.5     # risk 50% of capital per trade


def load_data(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.set_index("time", inplace=True)
    df["date"] = df.index.date
    return df


def compute_intraday_vwap(df: pd.DataFrame) -> pd.Series:
    """Cumulative VWAP that resets each day."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical_price * df["volume"]
    cum_pv = pv.groupby(df["date"]).cumsum()
    cum_vol = df["volume"].groupby(df["date"]).cumsum()
    return cum_pv / cum_vol


def run_backtest(df: pd.DataFrame) -> list[dict]:
    df["vwap"] = compute_intraday_vwap(df)
    df["above_vwap"] = df["close"] > df["vwap"]

    trades = []
    capital = INITIAL_CAPITAL

    for day, day_df in df.groupby("date"):
        # Count consecutive bars above VWAP
        bars_above = 0
        in_trade = False
        entry_price = 0.0
        tp_price = 0.0
        sl_price = 0.0
        entry_time = None
        size = 0.0

        for ts, row in day_df.iterrows():
            t = ts.strftime("%H:%M")

            # Force-close at end of day
            if in_trade and t >= END_OF_DAY:
                exit_price = row["close"]
                pnl_pct = (entry_price - exit_price) / entry_price
                gross_pnl = size * pnl_pct
                fees = size * FEE_PCT * 2
                net_pnl = gross_pnl - fees
                capital += net_pnl
                trades.append({
                    "date": day,
                    "entry_time": entry_time,
                    "exit_time": ts,
                    "entry": entry_price,
                    "exit": exit_price,
                    "pnl_pct": round(pnl_pct * 100, 4),
                    "net_pnl": round(net_pnl, 4),
                    "exit_reason": "EOD",
                    "capital": round(capital, 2),
                })
                in_trade = False
                break

            if in_trade:
                # Check stop-loss (hit if high >= sl)
                if row["high"] >= sl_price:
                    exit_price = sl_price
                    pnl_pct = (entry_price - exit_price) / entry_price
                    gross_pnl = size * pnl_pct
                    fees = size * FEE_PCT * 2
                    net_pnl = gross_pnl - fees
                    capital += net_pnl
                    trades.append({
                        "date": day,
                        "entry_time": entry_time,
                        "exit_time": ts,
                        "entry": entry_price,
                        "exit": exit_price,
                        "pnl_pct": round(pnl_pct * 100, 4),
                        "net_pnl": round(net_pnl, 4),
                        "exit_reason": "SL",
                        "capital": round(capital, 2),
                    })
                    in_trade = False
                    break  # max 1 trade per day

                # Check take-profit (hit if low <= tp)
                if row["low"] <= tp_price:
                    exit_price = tp_price
                    pnl_pct = (entry_price - exit_price) / entry_price
                    gross_pnl = size * pnl_pct
                    fees = size * FEE_PCT * 2
                    net_pnl = gross_pnl - fees
                    capital += net_pnl
                    trades.append({
                        "date": day,
                        "entry_time": entry_time,
                        "exit_time": ts,
                        "entry": entry_price,
                        "exit": exit_price,
                        "pnl_pct": round(pnl_pct * 100, 4),
                        "net_pnl": round(net_pnl, 4),
                        "exit_reason": "TP",
                        "capital": round(capital, 2),
                    })
                    in_trade = False
                    break  # max 1 trade per day
                continue

            # Not in trade — track bars above VWAP
            if row["above_vwap"]:
                bars_above += 1
            else:
                # Rejection signal: was above VWAP long enough, now closed below
                if bars_above >= MIN_BARS_ABOVE and t >= ENTRY_START and t < ENTRY_CUTOFF:
                    entry_price = row["close"]
                    tp_price = entry_price * (1 - TAKE_PROFIT_PCT)
                    sl_price = entry_price * (1 + STOP_LOSS_PCT)
                    size = capital * POSITION_SIZE_PCT
                    entry_time = ts
                    in_trade = True
                bars_above = 0

    return trades


def print_results(trades: list[dict]):
    if not trades:
        print("No trades executed.")
        return

    tdf = pd.DataFrame(trades)
    wins = tdf[tdf["net_pnl"] > 0]
    losses = tdf[tdf["net_pnl"] <= 0]

    print("=" * 65)
    print("  VWAP REJECTION SHORT — BACKTEST RESULTS")
    print("=" * 65)
    print(f"  Period         : {tdf['date'].iloc[0]} → {tdf['date'].iloc[-1]}")
    print(f"  Initial capital: ${INITIAL_CAPITAL:,.2f}")
    print(f"  Final capital  : ${tdf['capital'].iloc[-1]:,.2f}")
    print(f"  Net return     : {((tdf['capital'].iloc[-1] / INITIAL_CAPITAL) - 1) * 100:+.2f}%")
    print("-" * 65)
    print(f"  Total trades   : {len(tdf)}")
    print(f"  Winners        : {len(wins)}  ({len(wins)/len(tdf)*100:.1f}%)")
    print(f"  Losers         : {len(losses)}  ({len(losses)/len(tdf)*100:.1f}%)")
    print(f"  Avg win        : ${wins['net_pnl'].mean():.4f}" if len(wins) else "  Avg win        : —")
    print(f"  Avg loss       : ${losses['net_pnl'].mean():.4f}" if len(losses) else "  Avg loss       : —")
    print(f"  Avg pnl/trade  : ${tdf['net_pnl'].mean():.4f}")
    print("-" * 65)

    by_reason = tdf["exit_reason"].value_counts()
    for reason, count in by_reason.items():
        print(f"  Exit {reason:3s}       : {count}")

    print("-" * 65)
    print(f"  Max drawdown   : ${tdf['capital'].min() - INITIAL_CAPITAL:.2f}")
    print(f"  Peak capital   : ${tdf['capital'].max():.2f}")
    print("=" * 65)

    # Save trades log
    tdf.to_csv("backtest_trades.csv", index=False)
    print(f"\nTrade log saved to backtest_trades.csv")


def main():
    print(f"Loading {CSV_FILE}...")
    df = load_data(CSV_FILE)
    print(f"  {len(df):,} candles loaded")
    print(f"  TP={TAKE_PROFIT_PCT*100}%  SL={STOP_LOSS_PCT*100}%  "
          f"min_bars_above={MIN_BARS_ABOVE}  position_size={POSITION_SIZE_PCT*100}%\n")

    trades = run_backtest(df)
    print_results(trades)


if __name__ == "__main__":
    main()
