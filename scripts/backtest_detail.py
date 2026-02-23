"""
Detailed backtest for MomShort champion (GALAUSDT):
  TP=5%, SL=5%, min_bars=5, confirm=0, vol_filter=ON, trend_filter=OFF,
  window=01:00-22:00, vwap_prox=0.2%, max_hold=EOD, pos_size=20%
"""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import os
CSV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../data/klines/galausdt_1m_klines.csv")
TP_PCT = 0.05
SL_PCT = 0.05
MIN_BARS = 5
CONFIRM_BARS = 0
VWAP_PROX = 0.002
ENTRY_START = 60      # 01:00
ENTRY_CUTOFF = 1320   # 22:00
END_OF_DAY = 1430     # 23:50
FEE_PCT = 0.0004
POS_SIZE = 0.20
INITIAL_CAPITAL = 1000.0


def load():
    df = pd.read_csv(CSV_FILE)
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.set_index("time", inplace=True)
    df["date"] = df.index.date
    df["minute"] = df.index.hour * 60 + df.index.minute

    # Intraday VWAP
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = tp * df["volume"]
    df["vwap"] = pv.groupby(df["date"]).cumsum() / df["volume"].groupby(df["date"]).cumsum()
    df["vol_sma20"] = df["volume"].rolling(20).mean()
    return df


def run_backtest(df):
    trades = []
    capital = INITIAL_CAPITAL
    equity_curve = []

    for day, day_df in df.groupby("date"):
        counter = 0
        i = 0
        rows = list(day_df.itertuples())

        while i < len(rows):
            r = rows[i]
            m = r.minute

            if m < ENTRY_START:
                counter = 0
                i += 1
                continue
            if m >= ENTRY_CUTOFF:
                i += 1
                continue

            pct = (r.close - r.vwap) / r.vwap if r.vwap > 0 else 0
            if abs(pct) <= VWAP_PROX:
                counter += 1
                i += 1
                continue
            elif counter >= MIN_BARS and pct < -VWAP_PROX:
                counter = 0
                # Confirm bars
                ok = True
                ci = i
                for _ in range(CONFIRM_BARS):
                    ci += 1
                    if ci >= len(rows):
                        ok = False
                        break
                    cc = rows[ci]
                    if cc.minute >= ENTRY_CUTOFF:
                        ok = False
                        break
                    if cc.close >= cc.vwap:
                        ok = False
                        break
                if not ok:
                    i += 1
                    continue

                # Vol filter: entry bar volume must exceed 20-SMA
                signal_row = rows[ci] if CONFIRM_BARS > 0 else rows[i]
                if hasattr(signal_row, "vol_sma20") and pd.notna(signal_row.vol_sma20) and signal_row.volume <= signal_row.vol_sma20:
                    i += 1
                    continue

                # Entry
                entry_row = rows[ci]
                entry_price = entry_row.close
                entry_time = entry_row.Index
                entry_minute = entry_row.minute
                tp_price = entry_price * (1 - TP_PCT)
                sl_price = entry_price * (1 + SL_PCT)

                exit_price = rows[-1].close  # fallback EOD
                exit_time = rows[-1].Index
                reason = "EOD"

                for j in range(ci + 1, len(rows)):
                    rr = rows[j]
                    if rr.minute >= END_OF_DAY:
                        exit_price = rr.close
                        exit_time = rr.Index
                        reason = "EOD"
                        break
                    if rr.high >= sl_price:
                        exit_price = sl_price
                        exit_time = rr.Index
                        reason = "SL"
                        break
                    if rr.low <= tp_price:
                        exit_price = tp_price
                        exit_time = rr.Index
                        reason = "TP"
                        break

                pnl_pct = (entry_price - exit_price) / entry_price
                size = capital * POS_SIZE
                gross = size * pnl_pct
                fees = size * FEE_PCT * 2
                net = gross - fees
                capital += net

                trades.append({
                    "date": day,
                    "entry_time": entry_time,
                    "exit_time": exit_time,
                    "entry_price": round(entry_price, 6),
                    "exit_price": round(exit_price, 6),
                    "tp_price": round(tp_price, 6),
                    "sl_price": round(sl_price, 6),
                    "vwap_at_entry": round(entry_row.vwap, 6),
                    "pnl_pct": round(pnl_pct * 100, 4),
                    "net_pnl": round(net, 4),
                    "reason": reason,
                    "capital": round(capital, 2),
                    "hold_mins": int((exit_time - entry_time).total_seconds() / 60),
                })
                equity_curve.append({"time": exit_time, "capital": capital})
                break  # 1 trade per day
            else:
                counter = 0
                i += 1
                continue
            i += 1

    return pd.DataFrame(trades), equity_curve


def analyze(tdf):
    print("=" * 80)
    print("  MOMSHORT CHAMPION — GALAUSDT DETAILED ANALYSIS")
    print("  TP=5%  SL=5%  bars=5  cfm=0  vwap_prox=0.2%  vol_filter=ON  pos=20%")
    print("=" * 80)

    n = len(tdf)
    wins = tdf[tdf["net_pnl"] > 0]
    losses = tdf[tdf["net_pnl"] <= 0]
    tp_exits = tdf[tdf["reason"] == "TP"]
    sl_exits = tdf[tdf["reason"] == "SL"]
    eod_exits = tdf[tdf["reason"] == "EOD"]

    print(f"\n  Period:  {tdf['date'].iloc[0]} -> {tdf['date'].iloc[-1]}")
    print(f"  Trades:  {n}")
    print(f"  Initial: ${INITIAL_CAPITAL:.2f}")
    print(f"  Final:   ${tdf['capital'].iloc[-1]:.2f}")
    print(f"  Return:  {(tdf['capital'].iloc[-1] / INITIAL_CAPITAL - 1) * 100:+.2f}%")

    print(f"\n  --- Exit Breakdown ---")
    print(f"  TP hits:  {len(tp_exits):3d}  ({len(tp_exits)/n*100:.1f}%)")
    print(f"  SL hits:  {len(sl_exits):3d}  ({len(sl_exits)/n*100:.1f}%)")
    print(f"  EOD:      {len(eod_exits):3d}  ({len(eod_exits)/n*100:.1f}%)")

    print(f"\n  --- Win/Loss ---")
    print(f"  Winners:  {len(wins)}  ({len(wins)/n*100:.1f}%)")
    print(f"  Losers:   {len(losses)}  ({len(losses)/n*100:.1f}%)")
    print(f"  Avg win:  ${wins['net_pnl'].mean():.4f}" if len(wins) else "  Avg win:  -")
    print(f"  Avg loss: ${losses['net_pnl'].mean():.4f}" if len(losses) else "  Avg loss: -")
    print(f"  Avg P&L:  ${tdf['net_pnl'].mean():.4f}")
    print(f"  Median:   ${tdf['net_pnl'].median():.4f}")

    print(f"\n  --- Realized R:R ---")
    avg_win_pct = wins["pnl_pct"].mean() if len(wins) else 0
    avg_loss_pct = abs(losses["pnl_pct"].mean()) if len(losses) else 0
    real_rr = avg_win_pct / avg_loss_pct if avg_loss_pct > 0 else float("inf")
    print(f"  Avg win %:   {avg_win_pct:+.4f}%")
    print(f"  Avg loss %:  {-avg_loss_pct:.4f}%")
    print(f"  Realized R:R: {real_rr:.2f}  (theoretical: {TP_PCT/SL_PCT:.2f})")

    print(f"\n  --- Risk ---")
    peak = tdf["capital"].cummax()
    dd = (peak - tdf["capital"]) / peak * 100
    print(f"  Max drawdown: {dd.max():.2f}%")
    print(f"  Peak capital: ${tdf['capital'].max():.2f}")
    print(f"  Min capital:  ${tdf['capital'].min():.2f}")

    # Consecutive losses
    streak = 0
    max_streak = 0
    for _, row in tdf.iterrows():
        if row["net_pnl"] <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    print(f"  Max consec losses: {max_streak}")

    # Monthly breakdown
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
        print(f"  {idx}:  trades={row['trades']:3.0f}  wins={row['wins']:2.0f} ({row['win_rate']:.0f}%)  "
              f"pnl=${row['total_pnl']:+7.2f}  capital=${row['end_capital']:.2f}")

    # Hold time analysis
    print(f"\n  --- Hold Time (minutes) ---")
    for reason in ["TP", "SL", "EOD"]:
        subset = tdf[tdf["reason"] == reason]
        if len(subset) > 0:
            print(f"  {reason:3s}: mean={subset['hold_mins'].mean():.0f}  "
                  f"median={subset['hold_mins'].median():.0f}  "
                  f"min={subset['hold_mins'].min():.0f}  max={subset['hold_mins'].max():.0f}")

    # EOD trades: are they mostly winners or losers?
    print(f"\n  --- EOD Exit Analysis ---")
    eod_wins = eod_exits[eod_exits["net_pnl"] > 0]
    eod_losses = eod_exits[eod_exits["net_pnl"] <= 0]
    print(f"  EOD winners:  {len(eod_wins)}  avg pnl={eod_wins['pnl_pct'].mean():.4f}%" if len(eod_wins) else "  EOD winners: 0")
    print(f"  EOD losers:   {len(eod_losses)}  avg pnl={eod_losses['pnl_pct'].mean():.4f}%" if len(eod_losses) else "  EOD losers: 0")

    tdf.to_csv("champion_trades.csv", index=False)
    print(f"\n  Trade log -> champion_trades.csv")


def plot_equity(tdf, equity):
    edf = pd.DataFrame(equity)
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
    fig.add_hline(y=INITIAL_CAPITAL, line_dash="dash", line_color="gray", row=1, col=1)

    # Per-trade P&L bars
    colors = ["#26a69a" if p > 0 else "#ef5350" for p in tdf["net_pnl"]]
    fig.add_trace(go.Bar(
        x=pd.to_datetime(tdf["date"]), y=tdf["net_pnl"],
        name="Trade P&L", marker_color=colors,
    ), row=2, col=1)

    # Drawdown
    peak = edf["capital"].cummax()
    dd_pct = (peak - edf["capital"]) / peak * 100
    fig.add_trace(go.Scatter(
        x=edf["time"], y=-dd_pct,
        name="Drawdown", line=dict(color="#ef5350", width=1),
        fill="tozeroy", fillcolor="rgba(239,83,80,0.2)",
    ), row=3, col=1)

    fig.update_layout(
        title="MomShort Champion GALAUSDT — TP=5% SL=5% bars=5 cfm=0 prox=0.2% vol_filter=ON",
        template="plotly_dark",
        height=900,
        showlegend=False,
    )
    fig.update_yaxes(title_text="Capital ($)", row=1, col=1)
    fig.update_yaxes(title_text="P&L ($)", row=2, col=1)
    fig.update_yaxes(title_text="DD %", row=3, col=1)

    fig.write_html("champion_analysis.html")
    print("  Chart -> champion_analysis.html")
    fig.show()


def main():
    df = load()
    tdf, equity = run_backtest(df)
    analyze(tdf)
    plot_equity(tdf, equity)


if __name__ == "__main__":
    main()
