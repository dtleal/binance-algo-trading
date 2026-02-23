"""
Detailed backtest for VWAPPullback strategy — bidirectional with EMA trend filter.

Direction is determined per-candle by EMA(EMA_PERIOD):
  close > EMA  →  uptrend   →  look for LONG  (consolidate near VWAP, break above)
  close < EMA  →  downtrend →  look for SHORT (consolidate near VWAP, break below)

Usage:
    Edit the parameters below, then:
        python backtest_detail_pullback.py

Outputs:
    pullback_trades.csv          — full trade log
    pullback_analysis.html       — interactive equity/P&L/drawdown chart
"""

import os
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ── Configuration — OPTIMIZED PARAMETERS FROM SWEEP ─────────────────────────
CSV_FILE      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ethusdt_5m_klines_official.csv")
EMA_PERIOD    = 100       # bars — trend filter (does NOT reset daily)
TP_PCT        = 0.10      # 10% take-profit
SL_PCT        = 0.05      # 5% stop-loss
MIN_BARS      = 20        # min consolidation bars near VWAP
CONFIRM_BARS  = 0         # confirmation bars after breakout
VWAP_PROX     = 0.005     # 0.5% proximity threshold (CSV shows 0.50 = 0.005 * 100)
VWAP_WINDOW_DAYS = 1     # rolling VWAP window in days (1, 5, 10, 20, 30)
ENTRY_START        = 60    # 01:00 UTC (minutes from midnight)
ENTRY_CUTOFF       = 1320  # 22:00 UTC
END_OF_DAY         = 1430  # 23:50 UTC
POS_SIZE           = 0.20  # 20% of capital per trade
MAX_TRADES_PER_DAY = 2     # max entries per UTC day (0 = unlimited)
FEE_PCT            = 0.0004  # 0.04% taker fee per side
INITIAL_CAP   = 1000.0
# ─────────────────────────────────────────────────────────────────────────────


def load(csv_file: str) -> pd.DataFrame:
    """Load CSV and precompute VWAP, EMA using the same trackers as the bot."""
    from trader.strategy import VWAPRollingTracker
    from trader.strategy_vwap_pullback import EMATracker

    df = pd.read_csv(csv_file)
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.set_index("time", inplace=True)
    df["date"]   = df.index.date
    df["minute"] = df.index.hour * 60 + df.index.minute

    # Rolling VWAP — using the same tracker as the bot
    vwap_tracker = VWAPRollingTracker(window_days=VWAP_WINDOW_DAYS)
    vwap_vals = []
    for _, row in df.iterrows():
        day_ordinal = int(row.name.timestamp()) // 86400
        vwap = vwap_tracker.update(row["high"], row["low"], row["close"], row["volume"], day_ordinal)
        vwap_vals.append(vwap)
    df["vwap"] = vwap_vals

    # Volume SMA(20)
    df["vol_sma20"] = df["volume"].rolling(20).mean()

    # EMA — using the same tracker as the bot
    ema_tracker = EMATracker(period=EMA_PERIOD)
    ema_vals = []
    for c in df["close"]:
        ema = ema_tracker.update(c)
        ema_vals.append(ema)
    df["ema"] = ema_vals

    return df


def run_backtest(df: pd.DataFrame):
    """Run backtest using VWAPPullbackSignal — same logic as the live bot."""
    from trader.strategy_vwap_pullback import VWAPPullbackSignal

    trades = []
    capital = INITIAL_CAP
    equity_curve = []

    # Initialize signal generator (same as bot uses)
    signal = VWAPPullbackSignal(
        min_bars=MIN_BARS,
        confirm_bars=CONFIRM_BARS,
        vwap_prox=VWAP_PROX,
        entry_start_min=ENTRY_START,
        entry_cutoff_min=ENTRY_CUTOFF,
        max_trades_per_day=MAX_TRADES_PER_DAY,
    )

    for day, day_df in df.groupby("date"):
        signal.reset_daily()  # Reset signal state for new day
        rows = list(day_df.itertuples())

        i = 0
        while i < len(rows):
            r = rows[i]

            # Determine trend from EMA (same as bot does)
            if r.ema is None or (isinstance(r.ema, float) and r.ema != r.ema):
                trend = None
            else:
                trend = "up" if r.close > r.ema else "down"

            # Feed candle to signal generator (SAME AS BOT)
            sig = signal.on_candle(
                close=r.close,
                vwap=r.vwap,
                minute_of_day=r.minute,
                trend=trend,
                volume=r.volume,
                vol_sma20=r.vol_sma20 if not pd.isna(r.vol_sma20) else 0.0,
            )

            # No signal, move to next candle
            if sig is None:
                i += 1
                continue

            # Signal fired! Execute trade
            direction = "long" if sig == "ENTER_LONG" else "short"
            entry_price = r.close
            entry_time = r.Index

            if direction == "long":
                tp_price = entry_price * (1 + TP_PCT)
                sl_price = entry_price * (1 - SL_PCT)
            else:
                tp_price = entry_price * (1 - TP_PCT)
                sl_price = entry_price * (1 + SL_PCT)

            # Scan for exit (TP/SL/EOD)
            exit_price = rows[-1].close
            exit_time = rows[-1].Index
            reason = "EOD"
            exit_idx = len(rows) - 1

            for j in range(i + 1, len(rows)):
                rr = rows[j]
                if rr.minute >= END_OF_DAY:
                    exit_price = rr.close
                    exit_time = rr.Index
                    reason = "EOD"
                    exit_idx = j
                    break
                if direction == "long":
                    if rr.low <= sl_price:
                        exit_price = sl_price
                        exit_time = rr.Index
                        reason = "SL"
                        exit_idx = j
                        break
                    if rr.high >= tp_price:
                        exit_price = tp_price
                        exit_time = rr.Index
                        reason = "TP"
                        exit_idx = j
                        break
                else:  # short
                    if rr.high >= sl_price:
                        exit_price = sl_price
                        exit_time = rr.Index
                        reason = "SL"
                        exit_idx = j
                        break
                    if rr.low <= tp_price:
                        exit_price = tp_price
                        exit_time = rr.Index
                        reason = "TP"
                        exit_idx = j
                        break

            # Calculate P&L
            if direction == "long":
                pnl_pct = (exit_price - entry_price) / entry_price
            else:
                pnl_pct = (entry_price - exit_price) / entry_price

            size = capital * POS_SIZE
            gross = size * pnl_pct
            fees = size * FEE_PCT * 2
            net = gross - fees
            capital += net

            trades.append({
                "date": day,
                "direction": direction,
                "trend_at_entry": trend if trend else "N/A",
                "entry_time": entry_time,
                "exit_time": exit_time,
                "entry_price": round(entry_price, 6),
                "exit_price": round(exit_price, 6),
                "tp_price": round(tp_price, 6),
                "sl_price": round(sl_price, 6),
                "vwap_at_entry": round(r.vwap, 6),
                "ema_at_entry": round(r.ema, 6) if r.ema and r.ema == r.ema else 0,
                "pnl_pct": round(pnl_pct * 100, 4),
                "net_pnl": round(net, 4),
                "reason": reason,
                "capital": round(capital, 2),
                "hold_mins": int((exit_time - entry_time).total_seconds() / 60),
            })
            equity_curve.append({"time": exit_time, "capital": capital})

            # Reset signal state after trade closes (allows next setup)
            signal.reset_signal()

            # Jump to after exit
            i = exit_idx + 1

    return pd.DataFrame(trades), equity_curve


def analyze(tdf: pd.DataFrame):
    """Analyze trades and print detailed statistics."""
    symbol = os.path.basename(CSV_FILE).replace("_1m_klines.csv", "").upper()

    print("=" * 80)
    print(f"  VWAP PULLBACK — {symbol} DETAILED ANALYSIS")
    print(f"  EMA={EMA_PERIOD}  TP={TP_PCT*100:.1f}%  SL={SL_PCT*100:.1f}%  "
          f"bars={MIN_BARS}  cfm={CONFIRM_BARS}  prox={VWAP_PROX*100:.2f}%  pos={POS_SIZE*100:.0f}%")
    print("=" * 80)

    n = len(tdf)
    if n == 0:
        print("  No trades found. Try loosening parameters.")
        return

    wins   = tdf[tdf["net_pnl"] > 0]
    losses = tdf[tdf["net_pnl"] <= 0]
    longs  = tdf[tdf["direction"] == "long"]
    shorts = tdf[tdf["direction"] == "short"]
    tp_exits  = tdf[tdf["reason"] == "TP"]
    sl_exits  = tdf[tdf["reason"] == "SL"]
    eod_exits = tdf[tdf["reason"] == "EOD"]

    print(f"\n  Period:   {tdf['date'].iloc[0]} → {tdf['date'].iloc[-1]}")
    print(f"  Trades:   {n}  (long={len(longs)}  short={len(shorts)})")
    print(f"  Initial:  ${INITIAL_CAP:.2f}")
    print(f"  Final:    ${tdf['capital'].iloc[-1]:.2f}")
    print(f"  Return:   {(tdf['capital'].iloc[-1] / INITIAL_CAP - 1) * 100:+.2f}%")

    print(f"\n  --- Exit Breakdown ---")
    print(f"  TP hits:  {len(tp_exits):3d}  ({len(tp_exits)/n*100:.1f}%)")
    print(f"  SL hits:  {len(sl_exits):3d}  ({len(sl_exits)/n*100:.1f}%)")
    print(f"  EOD:      {len(eod_exits):3d}  ({len(eod_exits)/n*100:.1f}%)")

    print(f"\n  --- Win/Loss ---")
    print(f"  Winners:  {len(wins):3d}  ({len(wins)/n*100:.1f}%)")
    print(f"  Losers:   {len(losses):3d}  ({len(losses)/n*100:.1f}%)")
    if len(wins):
        print(f"  Avg win:  ${wins['net_pnl'].mean():.4f}  ({wins['pnl_pct'].mean():+.3f}%)")
    if len(losses):
        print(f"  Avg loss: ${losses['net_pnl'].mean():.4f}  ({losses['pnl_pct'].mean():+.3f}%)")
    print(f"  Avg P&L:  ${tdf['net_pnl'].mean():.4f}")

    print(f"\n  --- Realized R:R ---")
    avg_win_pct  = wins["pnl_pct"].mean()   if len(wins)   else 0
    avg_loss_pct = abs(losses["pnl_pct"].mean()) if len(losses) else 0
    real_rr = avg_win_pct / avg_loss_pct if avg_loss_pct > 0 else float("inf")
    print(f"  Avg win %:    {avg_win_pct:+.4f}%")
    print(f"  Avg loss %:  -{avg_loss_pct:.4f}%")
    print(f"  Realized R:R: {real_rr:.2f}  (theoretical: {TP_PCT/SL_PCT:.2f})")

    print(f"\n  --- Risk ---")
    peak = tdf["capital"].cummax()
    dd   = (peak - tdf["capital"]) / peak * 100
    print(f"  Max drawdown:      {dd.max():.2f}%")
    print(f"  Peak capital:      ${tdf['capital'].max():.2f}")
    print(f"  Min capital:       ${tdf['capital'].min():.2f}")
    streak = max_streak = 0
    for _, row in tdf.iterrows():
        streak = streak + 1 if row["net_pnl"] <= 0 else 0
        max_streak = max(max_streak, streak)
    print(f"  Max consec losses: {max_streak}")

    print(f"\n  --- Direction Breakdown ---")
    for direction, subset in [("LONG", longs), ("SHORT", shorts)]:
        if len(subset) == 0:
            continue
        w = subset[subset["net_pnl"] > 0]
        print(f"  {direction:5s}: trades={len(subset):3d}  wins={len(w):3d} "
              f"({len(w)/len(subset)*100:.0f}%)  "
              f"avg_pnl=${subset['net_pnl'].mean():.4f}")

    print(f"\n  --- Monthly Breakdown ---")
    tdf["month"] = pd.to_datetime(tdf["date"]).dt.to_period("M")
    monthly = tdf.groupby("month").agg(
        trades=("net_pnl", "count"),
        wins=("net_pnl", lambda x: (x > 0).sum()),
        total_pnl=("net_pnl", "sum"),
        end_capital=("capital", "last"),
    )
    monthly["win_rate"] = (monthly["wins"] / monthly["trades"] * 100).round(1)
    for idx, row in monthly.iterrows():
        print(f"  {idx}:  trades={row['trades']:3.0f}  wins={row['wins']:2.0f} "
              f"({row['win_rate']:.0f}%)  "
              f"pnl=${row['total_pnl']:+7.2f}  capital=${row['end_capital']:.2f}")

    print(f"\n  --- Hold Time (minutes) ---")
    for reason in ["TP", "SL", "EOD"]:
        subset = tdf[tdf["reason"] == reason]
        if len(subset):
            print(f"  {reason:3s}: mean={subset['hold_mins'].mean():.0f}  "
                  f"median={subset['hold_mins'].median():.0f}  "
                  f"min={subset['hold_mins'].min():.0f}  "
                  f"max={subset['hold_mins'].max():.0f}")

    print(f"\n  --- EOD Exit Analysis ---")
    eod_w = eod_exits[eod_exits["net_pnl"] > 0]
    eod_l = eod_exits[eod_exits["net_pnl"] <= 0]
    if len(eod_w):
        print(f"  EOD winners: {len(eod_w):3d}  avg={eod_w['pnl_pct'].mean():+.4f}%")
    if len(eod_l):
        print(f"  EOD losers:  {len(eod_l):3d}  avg={eod_l['pnl_pct'].mean():+.4f}%")

    out = "pullback_trades.csv"
    tdf.to_csv(out, index=False)
    print(f"\n  Trade log → {out}")


def plot_equity(tdf: pd.DataFrame, equity: list):
    """Generate interactive Plotly chart with equity curve, P&L, and drawdown."""
    if tdf.empty:
        return

    symbol = os.path.basename(CSV_FILE).replace("_1m_klines.csv", "").upper()
    edf    = pd.DataFrame(equity)
    edf["time"] = pd.to_datetime(edf["time"])

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.5, 0.25, 0.25],
        subplot_titles=("Equity Curve", "Trade P&L", "Drawdown %"),
    )

    # Equity curve
    fig.add_trace(go.Scatter(
        x=edf["time"], y=edf["capital"],
        name="Equity", line=dict(color="#26a69a", width=2),
        fill="tozeroy", fillcolor="rgba(38,166,154,0.1)",
    ), row=1, col=1)
    fig.add_hline(y=INITIAL_CAP, line_dash="dash", line_color="gray", row=1, col=1)

    # Per-trade P&L bars — color by direction
    colors = []
    for _, row in tdf.iterrows():
        if row["net_pnl"] > 0:
            colors.append("#26a69a")   # green win
        else:
            colors.append("#ef5350")   # red loss
    fig.add_trace(go.Bar(
        x=pd.to_datetime(tdf["date"]), y=tdf["net_pnl"],
        name="Trade P&L", marker_color=colors,
        text=tdf["direction"].str.upper(),
        textposition="outside",
    ), row=2, col=1)

    # Drawdown
    peak   = edf["capital"].cummax()
    dd_pct = (peak - edf["capital"]) / peak * 100
    fig.add_trace(go.Scatter(
        x=edf["time"], y=-dd_pct,
        name="Drawdown", line=dict(color="#ef5350", width=1),
        fill="tozeroy", fillcolor="rgba(239,83,80,0.2)",
    ), row=3, col=1)

    fig.update_layout(
        title=(
            f"VWAPPullback {symbol} — "
            f"EMA={EMA_PERIOD}  TP={TP_PCT*100:.1f}%  SL={SL_PCT*100:.1f}%  "
            f"bars={MIN_BARS}  cfm={CONFIRM_BARS}  prox={VWAP_PROX*100:.2f}%"
        ),
        template="plotly_dark",
        height=900,
        showlegend=False,
    )
    fig.update_yaxes(title_text="Capital ($)", row=1, col=1)
    fig.update_yaxes(title_text="P&L ($)",     row=2, col=1)
    fig.update_yaxes(title_text="DD %",        row=3, col=1)

    out = "pullback_analysis.html"
    fig.write_html(out)
    print(f"  Chart → {out}")
    fig.show()


def main():
    print(f"Loading {CSV_FILE}...")
    df = load(CSV_FILE)
    print(f"Loaded {len(df):,} candles  ({df.index[0].date()} → {df.index[-1].date()})")
    ema_ready = df["ema"].notna().sum()
    print(f"EMA({EMA_PERIOD}) active from candle {EMA_PERIOD} ({ema_ready:,} candles eligible)\n")

    tdf, equity = run_backtest(df)
    analyze(tdf)
    plot_equity(tdf, equity)


if __name__ == "__main__":
    main()
