"""
Parameter sweep for VWAP Rejection Short strategy.

Precomputes entry signals per (min_bars, vol_filter) combo,
then evaluates all TP/SL/position_size variations cheaply.
"""

import time
from itertools import product

import numpy as np
import pandas as pd

CSV_FILE = "axsusdc_1m_klines.csv"
FEE_PCT = 0.0004          # 0.04% taker fee per side
INITIAL_CAPITAL = 1000.0
ENTRY_START = "01:00"
ENTRY_CUTOFF = "22:00"
END_OF_DAY = "23:50"

# ── Parameter grid ──────────────────────────────────────────
TP_VALUES = [0.003, 0.005, 0.007, 0.01, 0.02, 0.05]

SL_VALUES = [0.002, 0.004, 0.006, 0.01, 0.02]

MIN_BARS_VALUES = [5, 10, 15, 20]

VOL_FILTER_VALUES = [False, True]

POS_SIZE_VALUES = [0.10, 0.20]


# ── Data loading ────────────────────────────────────────────
def load_and_prepare() -> pd.DataFrame:
    df = pd.read_csv(CSV_FILE)
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.set_index("time", inplace=True)
    df["date"] = df.index.date
    df["hm"] = df.index.strftime("%H:%M")

    # Intraday VWAP (resets daily)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = tp * df["volume"]
    df["vwap"] = pv.groupby(df["date"]).cumsum() / df["volume"].groupby(df["date"]).cumsum()

    # 20-bar volume SMA for volume filter
    df["vol_sma20"] = df["volume"].rolling(20).mean()

    df["above_vwap"] = df["close"] > df["vwap"]
    return df


# ── Entry detection ─────────────────────────────────────────
def find_entry_for_day(day_df: pd.DataFrame, min_bars: int, vol_filter: bool):
    """Return entry dict for first valid signal in a day, or None."""
    bars_above = 0

    for i in range(len(day_df)):
        row = day_df.iloc[i]
        t = row["hm"]

        if row["above_vwap"]:
            bars_above += 1
        else:
            if bars_above >= min_bars and ENTRY_START <= t < ENTRY_CUTOFF:
                if vol_filter and row["volume"] <= row["vol_sma20"]:
                    bars_above = 0
                    continue

                entry_price = float(row["close"])
                rest = day_df.iloc[i + 1:]
                before_eod = rest[rest["hm"] < END_OF_DAY]
                at_eod = rest[rest["hm"] >= END_OF_DAY]

                eod_close = float(at_eod.iloc[0]["close"]) if len(at_eod) > 0 else float(day_df.iloc[-1]["close"])

                return {
                    "entry_price": entry_price,
                    "highs": before_eod["high"].values.astype(np.float64) if len(before_eod) else np.empty(0),
                    "lows": before_eod["low"].values.astype(np.float64) if len(before_eod) else np.empty(0),
                    "eod_close": eod_close,
                }
            bars_above = 0

    return None


# ── Fast exit evaluation ────────────────────────────────────
def evaluate_entries(entries: list[dict], tp_pct: float, sl_pct: float,
                     pos_size: float) -> dict:
    """Simulate all trades for one param combo. Returns summary stats."""
    capital = INITIAL_CAPITAL
    peak = capital
    max_dd = 0.0
    wins = losses = eods = 0
    consec_loss = 0
    max_consec_loss = 0
    total_pnl_pct = 0.0

    for e in entries:
        ep = e["entry_price"]
        tp_price = ep * (1.0 - tp_pct)
        sl_price = ep * (1.0 + sl_pct)

        exit_price = e["eod_close"]
        reason = "EOD"

        for h, l in zip(e["highs"], e["lows"]):
            if h >= sl_price:
                exit_price = sl_price
                reason = "SL"
                break
            if l <= tp_price:
                exit_price = tp_price
                reason = "TP"
                break

        pnl_pct = (ep - exit_price) / ep
        total_pnl_pct += pnl_pct
        size = capital * pos_size
        net = size * pnl_pct - size * FEE_PCT * 2
        capital += net

        if net > 0:
            wins += 1
            consec_loss = 0
        else:
            losses += 1
            consec_loss += 1
            max_consec_loss = max(max_consec_loss, consec_loss)

        if reason == "EOD":
            eods += 1

        peak = max(peak, capital)
        dd = (peak - capital) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    n = len(entries)
    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "eods": eods,
        "win_rate": round(wins / n * 100, 1) if n else 0,
        "avg_pnl_pct": round(total_pnl_pct / n * 100, 4) if n else 0,
        "return_pct": round((capital / INITIAL_CAPITAL - 1) * 100, 2),
        "final_capital": round(capital, 2),
        "max_dd_pct": round(max_dd * 100, 2),
        "max_consec_loss": max_consec_loss,
    }


# ── Main sweep ──────────────────────────────────────────────
def main():
    t0 = time.time()
    print(f"Loading {CSV_FILE}...")
    df = load_and_prepare()
    days = {date: grp for date, grp in df.groupby("date")}
    sorted_dates = sorted(days.keys())
    print(f"  {len(df):,} candles, {len(days)} days\n")

    # Phase 1: precompute entries per (min_bars, vol_filter)
    print("Phase 1 — precomputing entry signals...")
    entry_cache: dict[tuple, list[dict]] = {}
    for min_bars, vol_filter in product(MIN_BARS_VALUES, VOL_FILTER_VALUES):
        entries = []
        for date in sorted_dates:
            e = find_entry_for_day(days[date], min_bars, vol_filter)
            if e:
                entries.append(e)
        entry_cache[(min_bars, vol_filter)] = entries
        print(f"  min_bars={min_bars:2d}  vol_filter={str(vol_filter):5s}  → {len(entries):3d} entries")

    # Phase 2: sweep TP, SL, position_size for each entry set
    total_combos = (len(TP_VALUES) * len(SL_VALUES) * len(MIN_BARS_VALUES)
                    * len(VOL_FILTER_VALUES) * len(POS_SIZE_VALUES))
    print(f"\nPhase 2 — evaluating {total_combos:,} parameter combinations...")

    results = []
    count = 0
    for (min_bars, vol_filter), entries in entry_cache.items():
        if not entries:
            continue
        for tp_pct, sl_pct, pos_size in product(TP_VALUES, SL_VALUES, POS_SIZE_VALUES):
            stats = evaluate_entries(entries, tp_pct, sl_pct, pos_size)
            stats.update({
                "tp_pct": round(tp_pct * 100, 2),
                "sl_pct": round(sl_pct * 100, 2),
                "rr_ratio": round(tp_pct / sl_pct, 2) if sl_pct else 0,
                "min_bars": min_bars,
                "vol_filter": vol_filter,
                "pos_size_pct": round(pos_size * 100, 0),
            })
            results.append(stats)
            count += 1
            if count % 5000 == 0:
                print(f"  {count:,} / {total_combos:,} done...")

    elapsed = time.time() - t0
    rdf = pd.DataFrame(results)
    rdf.sort_values("return_pct", ascending=False, inplace=True)
    rdf.to_csv("backtest_sweep.csv", index=False)

    print(f"\nDone in {elapsed:.1f}s — {len(rdf):,} results → backtest_sweep.csv\n")

    # ── Reports ─────────────────────────────────────────────
    cols = ["tp_pct", "sl_pct", "rr_ratio", "min_bars", "vol_filter", "pos_size_pct",
            "trades", "win_rate", "return_pct", "max_dd_pct", "max_consec_loss"]

    print("=" * 110)
    print("  TOP 25 BY RETURN %")
    print("=" * 110)
    print(rdf[cols].head(25).to_string(index=False))

    print()
    print("=" * 110)
    print("  TOP 25 BY RISK-ADJUSTED RETURN  (return / max_drawdown)")
    print("=" * 110)
    rdf["ret_dd"] = rdf["return_pct"] / rdf["max_dd_pct"].clip(lower=0.01)
    risk = rdf.sort_values("ret_dd", ascending=False)
    print(risk[cols + ["ret_dd"]].head(25).to_string(index=False))

    print()
    print("=" * 110)
    print("  TOP 25 BY WIN RATE  (min 30 trades)")
    print("=" * 110)
    wr = rdf[rdf["trades"] >= 30].sort_values("win_rate", ascending=False)
    print(wr[cols].head(25).to_string(index=False))

    # Quick insight
    print()
    print("=" * 110)
    print("  VOLUME FILTER IMPACT  (avg return across all combos)")
    print("=" * 110)
    vf = rdf.groupby("vol_filter")["return_pct"].agg(["mean", "median", "std", "count"])
    print(vf.to_string())


if __name__ == "__main__":
    main()
