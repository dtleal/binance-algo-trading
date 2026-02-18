"""
Parameter sweep for VWAPPullback strategy — bidirectional with EMA trend filter.

Separates signal-finding from exit-evaluation for efficiency:
  Phase 1: For each (ema_period, vwap_prox, min_bars, confirm_bars, max_trades) combo,
           find all entry signals (day, direction, entry_price, remaining candles).
  Phase 2: For each entry, evaluate all (tp_pct, sl_pct, pos_size) exits using
           numpy to avoid Python inner loops.

Total combinations: 4 × 3 × 4 × 4 × 4 × 4 × 4 × 2 = 24,576

Usage:
    Edit CSV_FILE below, then:
        python backtest_sweep_pullback.py

Outputs:
    pullback_sweep.csv   — full results (one row per parameter combo)
    TOP 30 BY RETURN and TOP 30 RISK-ADJUSTED printed to stdout
"""

import time
from itertools import product

import numpy as np
import pandas as pd

# ── Configuration ────────────────────────────────────────────────────────────
CSV_FILE    = "axsusdc_1m_klines.csv"   # edit to match your file
FEE_PCT     = 0.0004                    # 0.04% taker fee per side
INITIAL_CAP = 1000.0
ENTRY_START   = 60    # 01:00 UTC
ENTRY_CUTOFF  = 1320  # 22:00 UTC
END_OF_DAY    = 1430  # 23:50 UTC

# ── Parameter grid ────────────────────────────────────────────────────────────
EMA_PERIODS       = [100, 200, 300, 500]
VWAP_PROXS        = [0.002, 0.005, 0.010]
MIN_BARS_LIST     = [2, 3, 5, 8]
CONFIRM_BARS_LIST = [0, 1, 2, 3]
MAX_TRADES_LIST   = [1, 2, 4, 6]
TP_PCTS           = [0.03, 0.05, 0.08, 0.10]
SL_PCTS           = [0.015, 0.025, 0.040, 0.050]
POS_SIZES         = [0.10, 0.20]
# ─────────────────────────────────────────────────────────────────────────────


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load(csv_file: str) -> pd.DataFrame:
    df = pd.read_csv(csv_file)
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.set_index("time", inplace=True)
    df["date"]   = df.index.date
    df["minute"] = df.index.hour * 60 + df.index.minute

    # Intraday VWAP — resets at UTC midnight
    tp_col = (df["high"] + df["low"] + df["close"]) / 3
    pv     = tp_col * df["volume"]
    df["vwap"] = (
        pv.groupby(df["date"]).cumsum()
        / df["volume"].groupby(df["date"]).cumsum()
    )

    # EMA for each period — sequential, never resets
    k_arr = {p: 2.0 / (p + 1) for p in EMA_PERIODS}
    for period in EMA_PERIODS:
        k   = k_arr[period]
        col = f"ema_{period}"
        ema_vals = np.empty(len(df), dtype=np.float64)
        ema_vals[:] = np.nan
        ema = float(df["close"].iloc[0])
        for i, c in enumerate(df["close"].values):
            ema = c * k + ema * (1 - k)
            ema_vals[i] = ema if i + 1 >= period else np.nan
        df[col] = ema_vals

    return df


# ---------------------------------------------------------------------------
# Phase 1 — find entry signals per (ema_period, vwap_prox, min_bars, confirm_bars, max_trades)
# ---------------------------------------------------------------------------

def find_entries(day_groups: dict, ema_period: int, vwap_prox: float,
                 min_bars: int, confirm_bars: int,
                 max_trades_per_day: int = 4) -> list:
    """Return list of entry dicts for one signal combo.

    Each entry: {direction, entry_price, remaining}
    remaining: numpy array shape (N, 3) — (high, low, minute) for candles after entry

    Up to max_trades_per_day entries are collected per UTC day.  After each
    entry fires the signal state resets so the next setup can be detected.
    The scan resumes from the candle immediately after the entry (not after the
    exit, which is unknown at this phase).  This is a valid approximation for
    sweeping — the detailed backtest tracks exact exit indices.
    """
    entries = []

    for day, arr in day_groups.items():
        # arr columns: [minute, high, low, close, vwap, ema]
        n       = len(arr)
        minutes = arr[:, 0]
        highs   = arr[:, 1]
        lows    = arr[:, 2]
        closes  = arr[:, 3]
        vwaps   = arr[:, 4]
        emas    = arr[:, 5]  # pre-selected for this ema_period

        counter       = 0
        confirming    = False
        confirm_count = 0
        pending_dir   = None
        trades_today  = 0

        i = 0
        while i < n:
            if max_trades_per_day > 0 and trades_today >= max_trades_per_day:
                break

            m = minutes[i]

            if m < ENTRY_START:
                counter = 0
                i += 1
                continue
            if m >= ENTRY_CUTOFF:
                i += 1
                continue

            ema_val = emas[i]
            if np.isnan(ema_val):
                i += 1
                continue

            close = closes[i]
            vwap  = vwaps[i]
            trend = "up" if close > ema_val else "down"
            pct   = (close - vwap) / vwap if vwap > 0 else 0.0

            # ── Confirmation phase ───────────────────────────────────────
            if confirming:
                confirmed = (
                    (pending_dir == "long"  and close > vwap) or
                    (pending_dir == "short" and close < vwap)
                )
                if confirmed:
                    confirm_count += 1
                    if confirm_count >= confirm_bars:
                        # Signal fired
                        remaining_idx = i + 1
                        if remaining_idx < n:
                            remaining = arr[remaining_idx:, [1, 2, 0]]  # high, low, minute
                        else:
                            remaining = np.empty((0, 3))
                        entries.append({
                            "direction":   pending_dir,
                            "entry_price": close,
                            "remaining":   remaining,
                        })
                        trades_today += 1
                        # Reset signal state for the next setup
                        counter       = 0
                        confirming    = False
                        confirm_count = 0
                        pending_dir   = None
                        i += 1
                        continue
                else:
                    confirming    = False
                    confirm_count = 0
                    pending_dir   = None
                    counter       = 0
                i += 1
                continue

            # ── Consolidation / breakout ─────────────────────────────────
            if abs(pct) <= vwap_prox:
                counter += 1
            elif counter >= min_bars:
                breakout_long  = trend == "up"   and pct >  vwap_prox
                breakout_short = trend == "down" and pct < -vwap_prox

                if breakout_long or breakout_short:
                    counter     = 0
                    pending_dir = "long" if breakout_long else "short"

                    if confirm_bars == 0:
                        remaining_idx = i + 1
                        remaining = arr[remaining_idx:, [1, 2, 0]] if remaining_idx < n else np.empty((0, 3))
                        entries.append({
                            "direction":   pending_dir,
                            "entry_price": close,
                            "remaining":   remaining,
                        })
                        trades_today += 1
                        # Reset signal state for the next setup
                        counter     = 0
                        pending_dir = None
                        i += 1
                        continue
                    else:
                        confirming    = True
                        confirm_count = 0
                else:
                    counter = 0
            else:
                counter = 0

            i += 1

    return entries


# ---------------------------------------------------------------------------
# Phase 2 — evaluate exits for all (tp, sl, pos_size) combos given entries
# ---------------------------------------------------------------------------

def evaluate_exits(entries: list, tp_pct: float, sl_pct: float,
                   pos_size: float) -> dict:
    """Simulate a full backtest for one complete parameter set.

    Returns metrics dict.
    """
    capital = INITIAL_CAP
    trade_count = wins = sl_hits = tp_hits = eod_hits = 0
    long_count = short_count = long_wins = short_wins = 0
    pnls = []
    capitals = []

    for entry in entries:
        direction   = entry["direction"]
        entry_price = entry["entry_price"]
        remaining   = entry["remaining"]  # shape (N, 3): high, low, minute

        if direction == "long":
            tp_price = entry_price * (1 + tp_pct)
            sl_price = entry_price * (1 - sl_pct)
        else:
            tp_price = entry_price * (1 - tp_pct)
            sl_price = entry_price * (1 + sl_pct)

        if len(remaining) == 0:
            # No candles after entry — treat as tiny EOD
            exit_price = entry_price
            reason     = "EOD"
        else:
            highs   = remaining[:, 0]
            lows    = remaining[:, 1]
            minutes = remaining[:, 2]

            if direction == "long":
                sl_mask = lows  <= sl_price
                tp_mask = highs >= tp_price
            else:
                sl_mask = highs >= sl_price
                tp_mask = lows  <= tp_price

            eod_mask = minutes >= END_OF_DAY

            sl_first  = int(np.argmax(sl_mask))  if sl_mask.any()  else len(remaining)
            tp_first  = int(np.argmax(tp_mask))  if tp_mask.any()  else len(remaining)
            eod_first = int(np.argmax(eod_mask)) if eod_mask.any() else len(remaining) - 1

            # SL has priority over TP in case of same candle (worst-case)
            if sl_first <= tp_first and sl_first < len(remaining):
                exit_price = sl_price
                reason     = "SL"
            elif tp_first <= eod_first and tp_first < len(remaining):
                exit_price = tp_price
                reason     = "TP"
            else:
                # EOD — exit at close of EOD candle (stored as lows col position)
                # We don't store close in remaining, so approximate with mid
                idx = min(eod_first, len(remaining) - 1)
                # Use high+low midpoint as proxy for close (good enough for sweep)
                exit_price = (remaining[idx, 0] + remaining[idx, 1]) / 2
                reason     = "EOD"

        if direction == "long":
            pnl_pct = (exit_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - exit_price) / entry_price

        size  = capital * pos_size
        gross = size * pnl_pct
        fees  = size * FEE_PCT * 2
        net   = gross - fees
        capital += net

        pnls.append(net)
        capitals.append(capital)
        trade_count += 1

        if net > 0:
            wins += 1
        if reason == "SL":
            sl_hits += 1
        elif reason == "TP":
            tp_hits += 1
        else:
            eod_hits += 1

        if direction == "long":
            long_count += 1
            if net > 0:
                long_wins += 1
        else:
            short_count += 1
            if net > 0:
                short_wins += 1

    if trade_count == 0:
        return {"trades": 0, "return_pct": 0.0, "win_rate": 0.0,
                "max_dd": 0.0, "risk_adj": 0.0, "avg_pnl": 0.0,
                "sl_pct_hits": 0.0, "tp_pct_hits": 0.0, "eod_pct": 0.0,
                "long_count": 0, "short_count": 0,
                "long_win_rate": 0.0, "short_win_rate": 0.0}

    caps = np.array(capitals)
    peak = np.maximum.accumulate(caps)
    dd   = ((peak - caps) / peak * 100)
    max_dd = float(dd.max()) if len(dd) else 0.0

    return_pct = (capital / INITIAL_CAP - 1) * 100
    win_rate   = wins / trade_count * 100
    risk_adj   = return_pct / max_dd if max_dd > 0 else return_pct

    return {
        "trades":         trade_count,
        "return_pct":     round(return_pct, 3),
        "win_rate":       round(win_rate, 1),
        "max_dd":         round(max_dd, 2),
        "risk_adj":       round(risk_adj, 3),
        "avg_pnl":        round(np.mean(pnls), 4) if pnls else 0.0,
        "sl_pct_hits":    round(sl_hits / trade_count * 100, 1),
        "tp_pct_hits":    round(tp_hits / trade_count * 100, 1),
        "eod_pct":        round(eod_hits / trade_count * 100, 1),
        "long_count":     long_count,
        "short_count":    short_count,
        "long_win_rate":  round(long_wins / long_count * 100, 1) if long_count else 0.0,
        "short_win_rate": round(short_wins / short_count * 100, 1) if short_count else 0.0,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

COLS = ["ema", "prox", "min_bars", "cfm", "max_t", "tp", "sl", "pos",
        "return%", "trades", "win%", "max_dd%", "risk_adj",
        "sl_hits%", "tp_hits%", "eod%", "long", "short", "long_win%", "short_win%"]


def _row(r: dict) -> list:
    return [
        r["ema_period"], f"{r['vwap_prox']*100:.1f}%",
        r["min_bars"], r["confirm_bars"], r["max_trades_per_day"],
        f"{r['tp_pct']*100:.0f}%", f"{r['sl_pct']*100:.1f}%",
        f"{r['pos_size']*100:.0f}%",
        f"{r['return_pct']:+.2f}%", r["trades"],
        f"{r['win_rate']:.1f}%", f"{r['max_dd']:.2f}%",
        f"{r['risk_adj']:.2f}",
        f"{r['sl_pct_hits']:.0f}%", f"{r['tp_pct_hits']:.0f}%", f"{r['eod_pct']:.0f}%",
        r["long_count"], r["short_count"],
        f"{r['long_win_rate']:.0f}%", f"{r['short_win_rate']:.0f}%",
    ]


def print_table(title: str, rows: list):
    widths = [max(len(str(r[i])) for r in ([COLS] + rows)) for i in range(len(COLS))]
    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    header = "| " + " | ".join(str(COLS[i]).ljust(widths[i]) for i in range(len(COLS))) + " |"

    print(f"\n{'=' * (sum(widths) + 3 * len(widths) + 1)}")
    print(f"  {title}")
    print(sep)
    print(header)
    print(sep)
    for row in rows:
        print("| " + " | ".join(str(row[i]).ljust(widths[i]) for i in range(len(COLS))) + " |")
    print(sep)


def print_param_impact(results: list):
    df = pd.DataFrame(results)
    print(f"\n{'=' * 60}")
    print("  PARAMETER IMPACT (avg return % across all other combos)")
    print(f"{'=' * 60}")

    param_map = {
        "ema_period":         ("EMA Period",     EMA_PERIODS),
        "vwap_prox":          ("VWAP Prox",      [f"{v*100:.1f}%" for v in VWAP_PROXS]),
        "min_bars":           ("Min Bars",       MIN_BARS_LIST),
        "confirm_bars":       ("Confirm Bars",   CONFIRM_BARS_LIST),
        "max_trades_per_day": ("Max Trades/Day", MAX_TRADES_LIST),
        "tp_pct":             ("TP %",           [f"{v*100:.0f}%" for v in TP_PCTS]),
        "sl_pct":             ("SL %",           [f"{v*100:.1f}%" for v in SL_PCTS]),
        "pos_size":           ("Pos Size",       [f"{v*100:.0f}%" for v in POS_SIZES]),
    }

    for col, (label, vals) in param_map.items():
        avgs = df.groupby(col)["return_pct"].mean()
        row_str = "  " + f"{label:16s}: "
        for v, display in zip(avgs.index, vals):
            row_str += f"{display}={avgs[v]:+.2f}%  "
        print(row_str)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    t0 = time.time()

    print(f"Loading {CSV_FILE}...")
    df = load(CSV_FILE)
    print(f"Loaded {len(df):,} candles  ({df.index[0].date()} → {df.index[-1].date()})")

    n_sig  = (len(EMA_PERIODS) * len(VWAP_PROXS) * len(MIN_BARS_LIST)
              * len(CONFIRM_BARS_LIST) * len(MAX_TRADES_LIST))
    n_exit = len(TP_PCTS) * len(SL_PCTS) * len(POS_SIZES)
    print(f"Signal combos: {n_sig}  ×  Exit combos: {n_exit}  =  {n_sig * n_exit:,} total\n")

    # Precompute per-day arrays for each ema_period
    # Structure: day_groups[ema_period][day] = np.array shape (N, 6)
    #   columns: [minute, high, low, close, vwap, ema]
    print("Precomputing per-day arrays...")
    day_groups_all: dict[int, dict] = {}
    for ema_period in EMA_PERIODS:
        ema_col = f"ema_{ema_period}"
        groups  = {}
        for day, g in df.groupby("date"):
            arr = g[["minute", "high", "low", "close", "vwap", ema_col]].values.astype(np.float64)
            groups[day] = arr
        day_groups_all[ema_period] = groups

    # Phase 1 — find entries per signal combo
    print("Phase 1: Finding entry signals...")
    entry_cache: dict[tuple, list] = {}
    for ema_period, vwap_prox, min_bars, confirm_bars, max_trades in product(
        EMA_PERIODS, VWAP_PROXS, MIN_BARS_LIST, CONFIRM_BARS_LIST, MAX_TRADES_LIST
    ):
        sig_key = (ema_period, vwap_prox, min_bars, confirm_bars, max_trades)
        entry_cache[sig_key] = find_entries(
            day_groups_all[ema_period], ema_period, vwap_prox, min_bars, confirm_bars, max_trades
        )

    t1 = time.time()
    total_entries = sum(len(v) for v in entry_cache.values())
    print(f"Phase 1 done in {t1 - t0:.1f}s — {total_entries:,} total entry signals found\n")

    # Phase 2 — evaluate exits for all combos
    print("Phase 2: Evaluating exits...")
    results = []
    for (ema_period, vwap_prox, min_bars, confirm_bars, max_trades), (tp_pct, sl_pct, pos_size) in product(
        entry_cache.keys(),
        product(TP_PCTS, SL_PCTS, POS_SIZES),
    ):
        entries = entry_cache[(ema_period, vwap_prox, min_bars, confirm_bars, max_trades)]
        metrics = evaluate_exits(entries, tp_pct, sl_pct, pos_size)

        row = {
            "ema_period":         ema_period,
            "vwap_prox":          vwap_prox,
            "min_bars":           min_bars,
            "confirm_bars":       confirm_bars,
            "max_trades_per_day": max_trades,
            "tp_pct":             tp_pct,
            "sl_pct":             sl_pct,
            "pos_size":           pos_size,
            **metrics,
        }
        results.append(row)

    t2 = time.time()
    print(f"Phase 2 done in {t2 - t1:.1f}s\n")

    # Sort and display
    results_df = pd.DataFrame(results)

    # Filter out combos with too few trades to be meaningful
    MIN_TRADES = 15
    valid = results_df[results_df["trades"] >= MIN_TRADES]
    if valid.empty:
        print(f"No combos with >= {MIN_TRADES} trades. Showing all results.")
        valid = results_df

    top_return    = valid.nlargest(30, "return_pct")
    top_risk_adj  = valid.nlargest(30, "risk_adj")

    print_table("TOP 30 BY RETURN", [_row(r) for _, r in top_return.iterrows()])
    print_table("TOP 30 RISK-ADJUSTED (Return / MaxDD)", [_row(r) for _, r in top_risk_adj.iterrows()])
    print_param_impact(results)

    out = "pullback_sweep.csv"
    results_df.to_csv(out, index=False)
    print(f"\nFull results ({len(results_df):,} rows) → {out}")
    print(f"Total time: {t2 - t0:.1f}s")


if __name__ == "__main__":
    main()
