#!/usr/bin/env python3
"""
Three-layer anti-overfitting filter for Rust sweep outputs.

Layer 1: hard risk/return thresholds.
Layer 2: neighborhood robustness around each candidate.
Layer 3: monthly consistency check via re-simulation on klines.

Usage:
    poetry run python scripts/filter_overfit.py --symbol ldousdt
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

INITIAL_CAPITAL = 1000.0
FEE_PCT = 0.0004
END_OF_DAY_MINUTE = 1430  # 23:50 UTC

SUPPORTED_TFS = ["1m", "5m", "15m", "30m", "1h"]
VWAP_WINDOWS = [1, 5, 10, 20, 30]
EMA_PERIODS = [100, 200, 300, 500]
EMA_FAST_VALUES = [5, 8, 13]
EMA_SLOW_VALUES = [21, 34, 55]

ENTRY_WINDOW_MAP = {
    "01-22": (60, 1320),
    "06-18": (360, 1080),
}


@dataclass
class PreparedData:
    times: pd.DatetimeIndex
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    day: np.ndarray
    minute: np.ndarray
    vol_sma20: np.ndarray
    vwaps: dict[int, np.ndarray]
    ema_pullback: dict[int, np.ndarray]
    ema_fast: dict[int, np.ndarray]
    ema_slow: dict[int, np.ndarray]
    day_slices: list[tuple[int, int, int]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run 3-layer anti-overfitting filter.")
    parser.add_argument("--symbol", required=True, help="Symbol in any case, e.g. ldoUSDT")
    parser.add_argument("--timeframes", default="1m,5m,15m,30m,1h", help="Comma-separated timeframes")
    parser.add_argument("--sweeps-dir", default="data/sweeps")
    parser.add_argument("--klines-dir", default="data/klines")
    parser.add_argument("--sweep-suffix", default="sweep", help="Sweep file suffix without timeframe prefix, e.g. sweep or pdhl_guard_sweep")
    parser.add_argument("--output-tag", default="", help="Optional tag added to anti-overfit output filenames")
    parser.add_argument("--layer3-top-n", type=int, default=12, help="Top candidates from Layer 2 to validate in Layer 3")

    # Layer 1 thresholds
    parser.add_argument("--min-trades", type=int, default=150)
    parser.add_argument("--min-return", type=float, default=12.0)
    parser.add_argument("--max-dd", type=float, default=10.0)
    parser.add_argument("--min-ret-dd", type=float, default=1.8)
    parser.add_argument("--zero-dd-min-trades", type=int, default=80)
    parser.add_argument("--max-eod-ratio", type=float, default=-1.0, help="Reject setups above this EOD exit ratio percentage when the metric is available; <=0 disables")
    parser.add_argument("--max-avg-hold-minutes", type=float, default=-1.0, help="Reject setups above this average hold in minutes when the metric is available; <=0 disables")
    parser.add_argument("--min-avg-trades-per-day", type=float, default=-1.0, help="Reject setups below this average trades-per-day when the metric is available; <=0 disables")

    # Layer 2 thresholds
    parser.add_argument("--min-neighbors", type=int, default=25)
    parser.add_argument("--min-neighbor-half-return-share", type=float, default=0.60)
    parser.add_argument("--min-neighbor-ret-dd-median", type=float, default=1.00)
    parser.add_argument("--tp-radius", type=float, default=2.0, help="TP neighborhood radius in percentage points")
    parser.add_argument("--sl-radius", type=float, default=1.5, help="SL neighborhood radius in percentage points")
    parser.add_argument("--min-bars-radius", type=int, default=3)
    parser.add_argument("--confirm-radius", type=int, default=1)
    parser.add_argument("--vwap-prox-radius", type=float, default=0.30, help="VWAP proximity radius in percentage points")
    parser.add_argument("--vwap-dist-radius", type=float, default=1.0, help="VWAP distance stop radius in percentage points")
    parser.add_argument("--time-stop-progress-radius", type=float, default=0.25, help="Time-stop progress radius in percentage points")
    parser.add_argument("--adverse-body-radius", type=float, default=0.10, help="Adverse candle body radius in percentage points")
    parser.add_argument("--ema-radius", type=int, default=100)
    parser.add_argument("--max-trades-radius", type=int, default=1)
    parser.add_argument("--pdhl-prox-radius", type=float, default=0.25, help="PDHL proximity radius in percentage points")

    # Layer 3 thresholds
    parser.add_argument("--min-positive-month-ratio", type=float, default=0.70)
    parser.add_argument("--max-worst-month-loss", type=float, default=6.0, help="Worst month minimum return percentage (absolute)")
    parser.add_argument("--max-losing-streak", type=int, default=8)
    parser.add_argument("--min-months", type=int, default=6)
    return parser.parse_args()


def _to_float(v: Any) -> float:
    if v is None:
        return float("nan")
    s = str(v).strip()
    if s == "" or s == "-" or s.lower() in {"nan", "none", "null"}:
        return float("nan")
    if s.endswith("d"):
        s = s[:-1]
    s = s.replace("%", "")
    try:
        return float(s)
    except ValueError:
        return float("nan")


def _to_int(v: Any, default: int = 0) -> int:
    f = _to_float(v)
    if math.isnan(f):
        return default
    return int(f)


def _to_bool(v: Any) -> bool:
    s = str(v).strip().lower()
    return s in {"1", "true", "t", "yes", "y"}


def _parse_max_hold(v: Any) -> int:
    s = str(v).strip()
    if s.upper() == "EOD" or s == "-" or s == "":
        return 0
    return _to_int(s, 0)


def _parse_window(v: Any) -> tuple[int, int]:
    s = str(v).strip()
    if s in ENTRY_WINDOW_MAP:
        return ENTRY_WINDOW_MAP[s]
    return ENTRY_WINDOW_MAP["01-22"]


def load_sweeps(symbol: str, timeframes: list[str], sweeps_dir: Path, sweep_suffix: str) -> tuple[pd.DataFrame, list[str]]:
    rows: list[pd.DataFrame] = []
    missing: list[str] = []

    symbol_lower = symbol.lower()
    for tf in timeframes:
        p = sweeps_dir / f"{symbol_lower}_{tf}_{sweep_suffix}.csv"
        if not p.exists():
            missing.append(tf)
            continue
        df = pd.read_csv(p, low_memory=False)
        df["timeframe"] = tf
        rows.append(df)

    if not rows:
        raise FileNotFoundError(f"No sweep CSV found for {symbol_lower} in {sweeps_dir}")

    all_df = pd.concat(rows, ignore_index=True)
    return normalize_sweep_df(all_df), missing


def normalize_sweep_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [c.strip() for c in out.columns]

    # Canonical numeric columns from Rust sweep CSV.
    for col in [
        "tp_pct", "sl_pct", "rr_ratio", "min_bars", "confirm_bars", "vwap_prox", "vwap_window",
        "ema_period", "max_trades_per_day", "fast_period", "slow_period", "orb_range_mins", "pdhl_prox_pct",
        "max_hold", "vwap_dist_stop", "time_stop_minutes", "time_stop_min_progress_pct",
        "adverse_exit_bars", "adverse_body_min_pct", "pos_size_pct", "trades", "wins", "losses", "eods",
        "eod_ratio_pct", "avg_hold_minutes", "avg_trades_per_day", "win_rate", "return_pct", "final_capital", "max_dd_pct", "max_consec_loss",
    ]:
        if col not in out.columns:
            out[col] = np.nan

    for col in [
        "tp_pct", "sl_pct", "rr_ratio", "vwap_prox", "vwap_window", "ema_period", "max_trades_per_day",
        "fast_period", "slow_period", "orb_range_mins", "pdhl_prox_pct", "vwap_dist_stop",
        "time_stop_min_progress_pct", "adverse_body_min_pct",
        "pos_size_pct", "eod_ratio_pct", "avg_hold_minutes", "avg_trades_per_day", "win_rate", "return_pct", "final_capital", "max_dd_pct",
    ]:
        out[col] = out[col].map(_to_float)

    for col in ["min_bars", "confirm_bars", "max_hold", "time_stop_minutes", "adverse_exit_bars", "trades", "wins", "losses", "eods", "max_consec_loss"]:
        if col == "max_hold":
            out[col] = out[col].map(_parse_max_hold).astype(int)
        else:
            out[col] = out[col].map(_to_int).astype(int)

    for col in ["time_stop_min_progress_pct", "adverse_body_min_pct"]:
        out[col] = out[col].fillna(0.0)

    for col in ["vol_filter", "trend_filter"]:
        if col not in out.columns:
            out[col] = False
        out[col] = out[col].map(_to_bool)

    if "entry_window" not in out.columns:
        out["entry_window"] = "01-22"
    out["entry_window"] = out["entry_window"].astype(str)

    if "strategy" not in out.columns:
        raise ValueError("Column 'strategy' not found in sweep CSV.")
    out["strategy"] = out["strategy"].astype(str)

    if "timeframe" not in out.columns:
        out["timeframe"] = "1m"
    out["timeframe"] = out["timeframe"].astype(str)

    # Risk-adjusted ratio with DD floor to avoid divide-by-zero blowups.
    out["ret_dd"] = out["return_pct"] / np.maximum(out["max_dd_pct"], 0.01)
    out["row_id"] = np.arange(len(out))
    return out


def run_layer1(df: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    mask = (
        (df["trades"] >= args.min_trades)
        & (df["return_pct"] >= args.min_return)
        & (df["max_dd_pct"] <= args.max_dd)
        & (df["ret_dd"] >= args.min_ret_dd)
        & ~((df["max_dd_pct"] <= 1e-9) & (df["trades"] < args.zero_dd_min_trades))
    )
    if args.max_eod_ratio > 0 and "eod_ratio_pct" in df.columns:
        mask &= df["eod_ratio_pct"].fillna(0.0) <= args.max_eod_ratio
    if args.max_avg_hold_minutes > 0 and "avg_hold_minutes" in df.columns:
        mask &= df["avg_hold_minutes"].fillna(0.0) <= args.max_avg_hold_minutes
    if args.min_avg_trades_per_day > 0 and "avg_trades_per_day" in df.columns:
        mask &= df["avg_trades_per_day"].fillna(0.0) >= args.min_avg_trades_per_day
    return df.loc[mask].copy()


def _neighbors_mask(all_df: pd.DataFrame, cand: pd.Series, args: argparse.Namespace) -> pd.Series:
    # Base neighborhood: same strategy+timeframe and close core risk params.
    mask = (
        (all_df["strategy"] == cand["strategy"])
        & (all_df["timeframe"] == cand["timeframe"])
        & (np.abs(all_df["tp_pct"] - cand["tp_pct"]) <= args.tp_radius)
        & (np.abs(all_df["sl_pct"] - cand["sl_pct"]) <= args.sl_radius)
        & (np.abs(all_df["confirm_bars"] - cand["confirm_bars"]) <= args.confirm_radius)
    )

    if not math.isnan(float(cand["min_bars"])):
        mask &= np.abs(all_df["min_bars"] - cand["min_bars"]) <= args.min_bars_radius

    # Keep same categorical risk profile.
    mask &= (all_df["entry_window"] == cand["entry_window"])
    mask &= (all_df["max_hold"] == cand["max_hold"])
    mask &= (all_df["time_stop_minutes"] == cand["time_stop_minutes"])
    mask &= (all_df["adverse_exit_bars"] == cand["adverse_exit_bars"])
    if int(cand["time_stop_minutes"]) > 0:
        mask &= np.abs(all_df["time_stop_min_progress_pct"] - cand["time_stop_min_progress_pct"]) <= args.time_stop_progress_radius
    if int(cand["adverse_exit_bars"]) > 0:
        mask &= np.abs(all_df["adverse_body_min_pct"] - cand["adverse_body_min_pct"]) <= args.adverse_body_radius

    # Strategy-specific neighborhood constraints.
    strategy = str(cand["strategy"])
    if strategy in {"MomShort", "MomLong", "RejShort", "RejLong", "VWAPPullback"}:
        mask &= (all_df["vwap_window"] == cand["vwap_window"])
        mask &= (np.abs(all_df["vwap_prox"] - cand["vwap_prox"]) <= args.vwap_prox_radius)
    if strategy in {"MomShort", "MomLong", "RejShort", "RejLong"}:
        mask &= (all_df["vol_filter"] == cand["vol_filter"])
        mask &= (all_df["trend_filter"] == cand["trend_filter"])
    if strategy == "VWAPPullback":
        mask &= np.abs(all_df["ema_period"] - cand["ema_period"]) <= args.ema_radius
        mask &= np.abs(all_df["max_trades_per_day"] - cand["max_trades_per_day"]) <= args.max_trades_radius
    if strategy == "EMAScalp":
        mask &= (all_df["fast_period"] == cand["fast_period"])
        mask &= (all_df["slow_period"] == cand["slow_period"])
    if strategy == "ORB":
        mask &= (all_df["orb_range_mins"] == cand["orb_range_mins"])
    if strategy == "PDHL":
        mask &= np.abs(all_df["pdhl_prox_pct"] - cand["pdhl_prox_pct"]) <= args.pdhl_prox_radius

    # VWAP distance stop can materially change behavior.
    mask &= np.abs(all_df["vwap_dist_stop"] - cand["vwap_dist_stop"]) <= args.vwap_dist_radius
    return mask


def run_layer2(layer1_df: pd.DataFrame, all_df: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    if layer1_df.empty:
        return layer1_df.copy()

    enriched: list[pd.Series] = []
    for _, cand in layer1_df.iterrows():
        nmask = _neighbors_mask(all_df, cand, args)
        neighbors = all_df.loc[nmask]
        n_count = int(len(neighbors))

        if n_count == 0:
            continue

        share_above_half = float((neighbors["return_pct"] >= (cand["return_pct"] * 0.5)).mean())
        median_ret_dd = float(neighbors["ret_dd"].median())
        median_return = float(neighbors["return_pct"].median())
        median_dd = float(neighbors["max_dd_pct"].median())

        passed = (
            n_count >= args.min_neighbors
            and share_above_half >= args.min_neighbor_half_return_share
            and median_ret_dd >= args.min_neighbor_ret_dd_median
        )
        if not passed:
            continue

        row = cand.copy()
        row["neighbors_count"] = n_count
        row["neighbors_half_return_share"] = share_above_half
        row["neighbors_ret_dd_median"] = median_ret_dd
        row["neighbors_return_median"] = median_return
        row["neighbors_dd_median"] = median_dd
        enriched.append(row)

    if not enriched:
        return pd.DataFrame(columns=list(layer1_df.columns) + [
            "neighbors_count", "neighbors_half_return_share", "neighbors_ret_dd_median",
            "neighbors_return_median", "neighbors_dd_median",
        ])

    out = pd.DataFrame(enriched)
    return out.sort_values(
        by=["ret_dd", "return_pct", "trades"],
        ascending=[False, False, False],
        ignore_index=True,
    )


def _ema(series: np.ndarray, period: int) -> np.ndarray:
    out = np.empty_like(series, dtype=float)
    k = 2.0 / (period + 1.0)
    ema_val = float(series[0])
    for i, c in enumerate(series):
        ema_val = c * k + ema_val * (1.0 - k)
        out[i] = ema_val if i + 1 >= period else np.nan
    return out


def prepare_klines(csv_path: Path) -> PreparedData:
    df = pd.read_csv(csv_path, low_memory=False)
    needed = {"open_time", "open", "high", "low", "close", "volume"}
    missing = needed - set(df.columns)
    if missing:
        raise ValueError(f"Kline file {csv_path} missing columns: {sorted(missing)}")

    open_time_series = pd.to_numeric(df["open_time"], errors="coerce")
    if open_time_series.isna().any():
        raise ValueError(f"Invalid open_time values in {csv_path}")
    open_time = open_time_series.to_numpy(dtype=np.int64)

    times = pd.to_datetime(open_time, unit="ms", utc=True)
    open_ = pd.to_numeric(df["open"], errors="coerce").to_numpy(dtype=float)
    high = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype=float)
    low = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype=float)
    close = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
    volume = pd.to_numeric(df["volume"], errors="coerce").to_numpy(dtype=float)

    if np.isnan(close).any() or np.isnan(high).any() or np.isnan(low).any():
        raise ValueError(f"NaN values found in OHLC columns for {csv_path}")

    day = (open_time // 1000 // 86400).astype(np.int64)
    minute = (times.hour * 60 + times.minute).to_numpy(dtype=np.int16)

    vol_sma20 = (
        pd.Series(volume)
        .rolling(window=20, min_periods=1)
        .mean()
        .to_numpy(dtype=float)
    )

    typical = (high + low + close) / 3.0
    prefix_pv = np.concatenate(([0.0], np.cumsum(typical * volume)))
    prefix_vol = np.concatenate(([0.0], np.cumsum(volume)))

    unique_days, first_idx = np.unique(day, return_index=True)
    vwaps: dict[int, np.ndarray] = {}
    for w in VWAP_WINDOWS:
        start_day = day - (w - 1)
        pos = np.searchsorted(unique_days, start_day, side="left")
        pos = np.clip(pos, 0, len(first_idx) - 1)
        start_idx = first_idx[pos]

        cur = np.arange(len(close), dtype=np.int64) + 1
        pv = prefix_pv[cur] - prefix_pv[start_idx]
        vv = prefix_vol[cur] - prefix_vol[start_idx]
        vwap = np.divide(pv, vv, out=close.copy(), where=vv > 0.0)
        vwaps[w] = vwap

    ema_pullback = {p: _ema(close, p) for p in EMA_PERIODS}
    ema_fast = {p: _ema(close, p) for p in EMA_FAST_VALUES}
    ema_slow = {p: _ema(close, p) for p in EMA_SLOW_VALUES}

    day_slices: list[tuple[int, int, int]] = []
    for i, start in enumerate(first_idx):
        end = first_idx[i + 1] if i + 1 < len(first_idx) else len(close)
        day_slices.append((int(start), int(end), int(unique_days[i])))

    return PreparedData(
        times=times,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        day=day,
        minute=minute,
        vol_sma20=vol_sma20,
        vwaps=vwaps,
        ema_pullback=ema_pullback,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        day_slices=day_slices,
    )


def _simulate_exit(
    prep: PreparedData,
    entry_idx: int,
    day_end: int,
    is_short: bool,
    tp_pct: float,
    sl_pct: float,
    max_hold: int,
    vwap_dist_stop: float,
    time_stop_minutes: int,
    time_stop_min_progress_pct: float,
    adverse_exit_bars: int,
    adverse_body_min_pct: float,
    vwap_arr: np.ndarray,
) -> tuple[int, float, str]:
    entry_price = prep.close[entry_idx]
    entry_minute = int(prep.minute[entry_idx])
    if is_short:
        tp_price = entry_price * (1.0 - tp_pct)
        sl_price = entry_price * (1.0 + sl_pct)
    else:
        tp_price = entry_price * (1.0 + tp_pct)
        sl_price = entry_price * (1.0 - sl_pct)

    # Default EOD exit.
    eod_idx = day_end - 1
    for j in range(entry_idx + 1, day_end):
        if int(prep.minute[j]) >= END_OF_DAY_MINUTE:
            eod_idx = j
            break
    exit_idx = eod_idx
    exit_price = float(prep.close[eod_idx])
    reason = "EOD"
    adverse_count = 0

    for j in range(entry_idx + 1, day_end):
        minute = int(prep.minute[j])
        if minute >= END_OF_DAY_MINUTE:
            exit_idx = j
            exit_price = float(prep.close[j])
            reason = "EOD"
            break

        if max_hold > 0 and minute >= entry_minute + max_hold:
            exit_idx = j
            exit_price = float(prep.close[j])
            reason = "MAX_HOLD"
            break

        if vwap_dist_stop > 0.0:
            vwap = float(vwap_arr[j])
            if vwap > 0.0:
                dist = (float(prep.close[j]) - vwap) / vwap
                too_far = dist > vwap_dist_stop if is_short else dist < -vwap_dist_stop
                if too_far:
                    exit_idx = j
                    exit_price = float(prep.close[j])
                    reason = "VWAP_DIST"
                    break

        high = float(prep.high[j])
        low = float(prep.low[j])
        if is_short:
            if high >= sl_price:
                exit_idx = j
                exit_price = sl_price
                reason = "SL"
                break
            if low <= tp_price:
                exit_idx = j
                exit_price = tp_price
                reason = "TP"
                break
        else:
            if low <= sl_price:
                exit_idx = j
                exit_price = sl_price
                reason = "SL"
                break
            if high >= tp_price:
                exit_idx = j
                exit_price = tp_price
                reason = "TP"
                break

        close = float(prep.close[j])
        pnl_pct = ((entry_price - close) / entry_price * 100.0) if is_short else ((close - entry_price) / entry_price * 100.0)
        if (
            time_stop_minutes > 0
            and minute >= entry_minute + time_stop_minutes
            and pnl_pct <= time_stop_min_progress_pct
        ):
            exit_idx = j
            exit_price = close
            reason = "TIME_STOP"
            break

        open_ = float(prep.open[j])
        body_pct = (abs(close - open_) / open_ * 100.0) if open_ else 0.0
        adverse_candle = (
            ((not is_short and close < open_) or (is_short and close > open_))
            and body_pct >= adverse_body_min_pct
        )
        adverse_count = adverse_count + 1 if adverse_candle else 0
        if adverse_exit_bars > 0 and adverse_count >= adverse_exit_bars and pnl_pct < 0.0:
            exit_idx = j
            exit_price = close
            reason = "ADVERSE_MOMENTUM"
            break

    return exit_idx, exit_price, reason


def _extract_exit_guards(row: pd.Series) -> tuple[int, float, int, float]:
    return (
        int(row["time_stop_minutes"]),
        float(row["time_stop_min_progress_pct"]),
        int(row["adverse_exit_bars"]),
        float(row["adverse_body_min_pct"]),
    )


def _append_trade(
    trades: list[dict[str, Any]],
    prep: PreparedData,
    entry_idx: int,
    exit_idx: int,
    is_short: bool,
    exit_price: float,
    reason: str,
    capital: float,
    pos_size: float,
) -> tuple[float, bool]:
    entry_price = float(prep.close[entry_idx])
    pnl_frac = ((entry_price - exit_price) / entry_price) if is_short else ((exit_price - entry_price) / entry_price)
    size = capital * pos_size
    net = size * pnl_frac - size * FEE_PCT * 2.0
    new_cap = capital + net
    win = net > 0.0

    trades.append({
        "entry_time": prep.times[entry_idx],
        "exit_time": prep.times[exit_idx],
        "is_short": is_short,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "pnl_pct": pnl_frac * 100.0,
        "net_pnl": net,
        "reason": reason,
        "capital_after": new_cap,
    })
    return new_cap, win


def _simulate_momentum_or_rejection(
    prep: PreparedData,
    strategy: str,
    row: pd.Series,
    vwap_arr: np.ndarray,
) -> tuple[list[dict[str, Any]], int]:
    tp = float(row["tp_pct"]) / 100.0
    sl = float(row["sl_pct"]) / 100.0
    pos = float(row["pos_size_pct"]) / 100.0
    max_hold = int(row["max_hold"])
    vwap_dist = float(row["vwap_dist_stop"]) / 100.0
    min_bars = int(row["min_bars"])
    confirm_bars = int(row["confirm_bars"])
    vwap_prox = float(row["vwap_prox"]) / 100.0
    vol_filter = bool(row["vol_filter"])
    trend_filter = bool(row["trend_filter"])
    entry_start, entry_cutoff = _parse_window(row["entry_window"])
    time_stop_minutes, time_stop_min_progress_pct, adverse_exit_bars, adverse_body_min_pct = _extract_exit_guards(row)

    trades: list[dict[str, Any]] = []
    capital = INITIAL_CAPITAL
    losing_streak = 0
    max_losing_streak = 0

    for day_start, day_end, _ in prep.day_slices:
        day_open = float(prep.open[day_start])
        counter = 0
        i = day_start

        while i < day_end:
            minute = int(prep.minute[i])
            if minute < entry_start:
                counter = 0
                i += 1
                continue
            if minute >= entry_cutoff:
                i += 1
                continue

            vwap = float(vwap_arr[i])
            close = float(prep.close[i])
            signal = False
            is_short = True

            if strategy == "RejShort":
                if close > vwap:
                    counter += 1
                elif counter >= min_bars:
                    counter = 0
                    signal = True
                    is_short = True
                else:
                    counter = 0
            elif strategy == "RejLong":
                if close < vwap:
                    counter += 1
                elif counter >= min_bars:
                    counter = 0
                    signal = True
                    is_short = False
                else:
                    counter = 0
            elif strategy == "MomShort":
                pct = (close - vwap) / vwap if vwap > 0.0 else 0.0
                if abs(pct) <= vwap_prox:
                    counter += 1
                elif counter >= min_bars and pct < -vwap_prox:
                    counter = 0
                    signal = True
                    is_short = True
                else:
                    counter = 0
            elif strategy == "MomLong":
                pct = (close - vwap) / vwap if vwap > 0.0 else 0.0
                if abs(pct) <= vwap_prox:
                    counter += 1
                elif counter >= min_bars and pct > vwap_prox:
                    counter = 0
                    signal = True
                    is_short = False
                else:
                    counter = 0
            else:
                raise ValueError(f"Unsupported strategy in momentum/rejection simulator: {strategy}")

            if not signal:
                i += 1
                continue

            if vol_filter and float(prep.volume[i]) <= float(prep.vol_sma20[i]):
                i += 1
                continue
            if trend_filter:
                if is_short and close >= day_open:
                    i += 1
                    continue
                if (not is_short) and close <= day_open:
                    i += 1
                    continue

            ci = i
            ok = True
            for _ in range(confirm_bars):
                ci += 1
                if ci >= day_end:
                    ok = False
                    break
                if int(prep.minute[ci]) >= entry_cutoff:
                    ok = False
                    break
                cvwap = float(vwap_arr[ci])
                cclose = float(prep.close[ci])
                if is_short and cclose >= cvwap:
                    ok = False
                    break
                if (not is_short) and cclose <= cvwap:
                    ok = False
                    break
            if not ok:
                i += 1
                continue

            entry_idx = ci
            exit_idx, exit_price, reason = _simulate_exit(
                prep=prep,
                entry_idx=entry_idx,
                day_end=day_end,
                is_short=is_short,
                tp_pct=tp,
                sl_pct=sl,
                max_hold=max_hold,
                vwap_dist_stop=vwap_dist,
                time_stop_minutes=time_stop_minutes,
                time_stop_min_progress_pct=time_stop_min_progress_pct,
                adverse_exit_bars=adverse_exit_bars,
                adverse_body_min_pct=adverse_body_min_pct,
                vwap_arr=vwap_arr,
            )
            capital, win = _append_trade(
                trades=trades,
                prep=prep,
                entry_idx=entry_idx,
                exit_idx=exit_idx,
                is_short=is_short,
                exit_price=exit_price,
                reason=reason,
                capital=capital,
                pos_size=pos,
            )
            if win:
                losing_streak = 0
            else:
                losing_streak += 1
                max_losing_streak = max(max_losing_streak, losing_streak)

            # These families are max 1 trade/day in the Rust sweep.
            break

    return trades, max_losing_streak


def _simulate_pullback(prep: PreparedData, row: pd.Series, vwap_arr: np.ndarray) -> tuple[list[dict[str, Any]], int]:
    tp = float(row["tp_pct"]) / 100.0
    sl = float(row["sl_pct"]) / 100.0
    pos = float(row["pos_size_pct"]) / 100.0
    max_hold = int(row["max_hold"])
    vwap_dist = float(row["vwap_dist_stop"]) / 100.0
    min_bars = int(row["min_bars"])
    confirm_bars = int(row["confirm_bars"])
    vwap_prox = float(row["vwap_prox"]) / 100.0
    entry_start, entry_cutoff = _parse_window(row["entry_window"])
    max_trades_per_day = max(0, int(row["max_trades_per_day"]))
    ema_period = int(row["ema_period"])
    time_stop_minutes, time_stop_min_progress_pct, adverse_exit_bars, adverse_body_min_pct = _extract_exit_guards(row)
    ema_arr = prep.ema_pullback.get(ema_period)
    if ema_arr is None:
        raise ValueError(f"EMA period {ema_period} not precomputed for VWAPPullback")

    trades: list[dict[str, Any]] = []
    capital = INITIAL_CAPITAL
    losing_streak = 0
    max_losing_streak = 0

    for day_start, day_end, _ in prep.day_slices:
        counter = 0
        confirming = False
        confirm_count = 0
        pending_long = False
        trades_today = 0
        i = day_start

        while i < day_end:
            if max_trades_per_day > 0 and trades_today >= max_trades_per_day:
                break

            minute = int(prep.minute[i])
            if minute < entry_start:
                counter = 0
                i += 1
                continue
            if minute >= entry_cutoff:
                i += 1
                continue

            ema = float(ema_arr[i])
            if math.isnan(ema):
                i += 1
                continue

            close = float(prep.close[i])
            vwap = float(vwap_arr[i])
            pct = (close - vwap) / vwap if vwap > 0.0 else 0.0
            trend_up = close > ema

            if confirming:
                confirmed = close > vwap if pending_long else close < vwap
                if confirmed:
                    confirm_count += 1
                    if confirm_count >= confirm_bars:
                        entry_idx = i
                        is_short = not pending_long
                        exit_idx, exit_price, reason = _simulate_exit(
                            prep=prep,
                            entry_idx=entry_idx,
                            day_end=day_end,
                            is_short=is_short,
                            tp_pct=tp,
                            sl_pct=sl,
                            max_hold=max_hold,
                            vwap_dist_stop=vwap_dist,
                            time_stop_minutes=time_stop_minutes,
                            time_stop_min_progress_pct=time_stop_min_progress_pct,
                            adverse_exit_bars=adverse_exit_bars,
                            adverse_body_min_pct=adverse_body_min_pct,
                            vwap_arr=vwap_arr,
                        )
                        capital, win = _append_trade(
                            trades=trades,
                            prep=prep,
                            entry_idx=entry_idx,
                            exit_idx=exit_idx,
                            is_short=is_short,
                            exit_price=exit_price,
                            reason=reason,
                            capital=capital,
                            pos_size=pos,
                        )
                        if win:
                            losing_streak = 0
                        else:
                            losing_streak += 1
                            max_losing_streak = max(max_losing_streak, losing_streak)
                        trades_today += 1
                        counter = 0
                        confirming = False
                        confirm_count = 0
                        i = exit_idx + 1
                        continue
                else:
                    confirming = False
                    confirm_count = 0
                    counter = 0
                i += 1
                continue

            if abs(pct) <= vwap_prox:
                counter += 1
            elif counter >= min_bars:
                breakout_long = trend_up and pct > vwap_prox
                breakout_short = (not trend_up) and pct < -vwap_prox
                if breakout_long or breakout_short:
                    counter = 0
                    pending_long = breakout_long
                    if confirm_bars == 0:
                        entry_idx = i
                        is_short = breakout_short
                        exit_idx, exit_price, reason = _simulate_exit(
                            prep=prep,
                            entry_idx=entry_idx,
                            day_end=day_end,
                            is_short=is_short,
                            tp_pct=tp,
                            sl_pct=sl,
                            max_hold=max_hold,
                            vwap_dist_stop=vwap_dist,
                            time_stop_minutes=time_stop_minutes,
                            time_stop_min_progress_pct=time_stop_min_progress_pct,
                            adverse_exit_bars=adverse_exit_bars,
                            adverse_body_min_pct=adverse_body_min_pct,
                            vwap_arr=vwap_arr,
                        )
                        capital, win = _append_trade(
                            trades=trades,
                            prep=prep,
                            entry_idx=entry_idx,
                            exit_idx=exit_idx,
                            is_short=is_short,
                            exit_price=exit_price,
                            reason=reason,
                            capital=capital,
                            pos_size=pos,
                        )
                        if win:
                            losing_streak = 0
                        else:
                            losing_streak += 1
                            max_losing_streak = max(max_losing_streak, losing_streak)
                        trades_today += 1
                        i = exit_idx + 1
                        continue
                    confirming = True
                    confirm_count = 0
                else:
                    counter = 0
            else:
                counter = 0
            i += 1

    return trades, max_losing_streak


def _simulate_ema_scalp(prep: PreparedData, row: pd.Series, vwap_arr: np.ndarray) -> tuple[list[dict[str, Any]], int]:
    tp = float(row["tp_pct"]) / 100.0
    sl = float(row["sl_pct"]) / 100.0
    pos = float(row["pos_size_pct"]) / 100.0
    max_hold = int(row["max_hold"])
    vwap_dist = float(row["vwap_dist_stop"]) / 100.0
    max_trades_per_day = max(0, int(row["max_trades_per_day"]))
    time_stop_minutes, time_stop_min_progress_pct, adverse_exit_bars, adverse_body_min_pct = _extract_exit_guards(row)

    fast_period = int(row["fast_period"])
    slow_period = int(row["slow_period"])
    fast_arr = prep.ema_fast.get(fast_period)
    slow_arr = prep.ema_slow.get(slow_period)
    if fast_arr is None or slow_arr is None:
        raise ValueError(f"EMAScalp periods not precomputed: fast={fast_period}, slow={slow_period}")

    trades: list[dict[str, Any]] = []
    capital = INITIAL_CAPITAL
    losing_streak = 0
    max_losing_streak = 0

    for day_start, day_end, _ in prep.day_slices:
        trades_today = 0
        i = day_start + 1
        while i < day_end:
            if max_trades_per_day > 0 and trades_today >= max_trades_per_day:
                break
            minute = int(prep.minute[i])
            if minute >= 1380:
                i += 1
                continue

            fast = float(fast_arr[i])
            slow = float(slow_arr[i])
            pfast = float(fast_arr[i - 1])
            pslow = float(slow_arr[i - 1])
            if any(math.isnan(v) for v in [fast, slow, pfast, pslow]):
                i += 1
                continue

            cross_long = pfast <= pslow and fast > slow
            cross_short = pfast >= pslow and fast < slow
            if not (cross_long or cross_short):
                i += 1
                continue

            is_short = cross_short
            exit_idx, exit_price, reason = _simulate_exit(
                prep=prep,
                entry_idx=i,
                day_end=day_end,
                is_short=is_short,
                tp_pct=tp,
                sl_pct=sl,
                max_hold=max_hold,
                vwap_dist_stop=vwap_dist,
                time_stop_minutes=time_stop_minutes,
                time_stop_min_progress_pct=time_stop_min_progress_pct,
                adverse_exit_bars=adverse_exit_bars,
                adverse_body_min_pct=adverse_body_min_pct,
                vwap_arr=vwap_arr,
            )
            capital, win = _append_trade(
                trades=trades,
                prep=prep,
                entry_idx=i,
                exit_idx=exit_idx,
                is_short=is_short,
                exit_price=exit_price,
                reason=reason,
                capital=capital,
                pos_size=pos,
            )
            if win:
                losing_streak = 0
            else:
                losing_streak += 1
                max_losing_streak = max(max_losing_streak, losing_streak)
            trades_today += 1
            i = exit_idx + 1

    return trades, max_losing_streak


def _simulate_pdhl(prep: PreparedData, row: pd.Series, vwap_arr: np.ndarray) -> tuple[list[dict[str, Any]], int]:
    tp = float(row["tp_pct"]) / 100.0
    sl = float(row["sl_pct"]) / 100.0
    pos = float(row["pos_size_pct"]) / 100.0
    max_hold = int(row["max_hold"])
    vwap_dist = float(row["vwap_dist_stop"]) / 100.0
    prox = float(row["pdhl_prox_pct"]) / 100.0
    confirm_bars = int(row["confirm_bars"])
    time_stop_minutes, time_stop_min_progress_pct, adverse_exit_bars, adverse_body_min_pct = _extract_exit_guards(row)

    pdh: float | None = None
    pdl: float | None = None
    trades: list[dict[str, Any]] = []
    capital = INITIAL_CAPITAL
    losing_streak = 0
    max_losing_streak = 0

    for day_start, day_end, _ in prep.day_slices:
        day_high = float("-inf")
        day_low = float("inf")

        if pdh is None or pdl is None:
            for i in range(day_start, day_end):
                day_high = max(day_high, float(prep.high[i]))
                day_low = min(day_low, float(prep.low[i]))
            pdh, pdl = day_high, day_low
            continue

        testing_pdh = False
        testing_pdl = False
        pdh_conf = 0
        pdl_conf = 0
        trades_today = 0

        i = day_start
        while i < day_end:
            day_high = max(day_high, float(prep.high[i]))
            day_low = min(day_low, float(prep.low[i]))

            minute = int(prep.minute[i])
            if minute < 60 or minute >= 1320:
                i += 1
                continue
            if trades_today >= 4:
                break

            # PDH approach (short rejection).
            if float(prep.high[i]) >= pdh * (1.0 - prox):
                testing_pdh = True
            if testing_pdh and float(prep.close[i]) < pdh * (1.0 - prox):
                pdh_conf += 1
                if pdh_conf >= confirm_bars:
                    exit_idx, exit_price, reason = _simulate_exit(
                        prep=prep,
                        entry_idx=i,
                        day_end=day_end,
                        is_short=True,
                        tp_pct=tp,
                        sl_pct=sl,
                        max_hold=max_hold,
                        vwap_dist_stop=vwap_dist,
                        time_stop_minutes=time_stop_minutes,
                        time_stop_min_progress_pct=time_stop_min_progress_pct,
                        adverse_exit_bars=adverse_exit_bars,
                        adverse_body_min_pct=adverse_body_min_pct,
                        vwap_arr=vwap_arr,
                    )
                    capital, win = _append_trade(
                        trades=trades,
                        prep=prep,
                        entry_idx=i,
                        exit_idx=exit_idx,
                        is_short=True,
                        exit_price=exit_price,
                        reason=reason,
                        capital=capital,
                        pos_size=pos,
                    )
                    if win:
                        losing_streak = 0
                    else:
                        losing_streak += 1
                        max_losing_streak = max(max_losing_streak, losing_streak)
                    trades_today += 1
                    testing_pdh = False
                    pdh_conf = 0
                    i = exit_idx + 1
                    continue
            elif testing_pdh and float(prep.close[i]) >= pdh * (1.0 - prox):
                pdh_conf = 0
            elif testing_pdh and float(prep.close[i]) > pdh * (1.0 + prox):
                testing_pdh = False
                pdh_conf = 0

            # PDL approach (long rejection).
            if float(prep.low[i]) <= pdl * (1.0 + prox):
                testing_pdl = True
            if testing_pdl and float(prep.close[i]) > pdl * (1.0 + prox):
                pdl_conf += 1
                if pdl_conf >= confirm_bars:
                    exit_idx, exit_price, reason = _simulate_exit(
                        prep=prep,
                        entry_idx=i,
                        day_end=day_end,
                        is_short=False,
                        tp_pct=tp,
                        sl_pct=sl,
                        max_hold=max_hold,
                        vwap_dist_stop=vwap_dist,
                        time_stop_minutes=time_stop_minutes,
                        time_stop_min_progress_pct=time_stop_min_progress_pct,
                        adverse_exit_bars=adverse_exit_bars,
                        adverse_body_min_pct=adverse_body_min_pct,
                        vwap_arr=vwap_arr,
                    )
                    capital, win = _append_trade(
                        trades=trades,
                        prep=prep,
                        entry_idx=i,
                        exit_idx=exit_idx,
                        is_short=False,
                        exit_price=exit_price,
                        reason=reason,
                        capital=capital,
                        pos_size=pos,
                    )
                    if win:
                        losing_streak = 0
                    else:
                        losing_streak += 1
                        max_losing_streak = max(max_losing_streak, losing_streak)
                    trades_today += 1
                    testing_pdl = False
                    pdl_conf = 0
                    i = exit_idx + 1
                    continue
            elif testing_pdl and float(prep.close[i]) <= pdl * (1.0 + prox):
                pdl_conf = 0
            elif testing_pdl and float(prep.close[i]) < pdl * (1.0 - prox):
                testing_pdl = False
                pdl_conf = 0

            i += 1

        pdh, pdl = day_high, day_low

    return trades, max_losing_streak


def _simulate_orb(prep: PreparedData, row: pd.Series, vwap_arr: np.ndarray) -> tuple[list[dict[str, Any]], int]:
    # ORB buffer is not exported in Rust CSV, so we cannot reproduce faithfully.
    # Use an intermediate default (0.2%) to still allow rough monthly screening.
    tp = float(row["tp_pct"]) / 100.0
    sl = float(row["sl_pct"]) / 100.0
    pos = float(row["pos_size_pct"]) / 100.0
    max_hold = int(row["max_hold"])
    vwap_dist = float(row["vwap_dist_stop"]) / 100.0
    range_mins = int(row["orb_range_mins"])
    buffer_pct = 0.002
    time_stop_minutes, time_stop_min_progress_pct, adverse_exit_bars, adverse_body_min_pct = _extract_exit_guards(row)

    trades: list[dict[str, Any]] = []
    capital = INITIAL_CAPITAL
    losing_streak = 0
    max_losing_streak = 0

    for day_start, day_end, _ in prep.day_slices:
        range_high = float("-inf")
        range_low = float("inf")
        range_set = False
        long_taken = False
        short_taken = False
        i = day_start

        while i < day_end:
            minute = int(prep.minute[i])
            if minute < range_mins:
                range_high = max(range_high, float(prep.high[i]))
                range_low = min(range_low, float(prep.low[i]))
                i += 1
                continue

            if not range_set:
                if range_high == float("-inf"):
                    break
                range_set = True
            if long_taken and short_taken:
                break

            close = float(prep.close[i])
            signal_long = (not long_taken) and close > range_high * (1.0 + buffer_pct)
            signal_short = (not short_taken) and close < range_low * (1.0 - buffer_pct)
            if not (signal_long or signal_short):
                i += 1
                continue

            is_short = signal_short and not signal_long
            exit_idx, exit_price, reason = _simulate_exit(
                prep=prep,
                entry_idx=i,
                day_end=day_end,
                is_short=is_short,
                tp_pct=tp,
                sl_pct=sl,
                max_hold=max_hold,
                vwap_dist_stop=vwap_dist,
                time_stop_minutes=time_stop_minutes,
                time_stop_min_progress_pct=time_stop_min_progress_pct,
                adverse_exit_bars=adverse_exit_bars,
                adverse_body_min_pct=adverse_body_min_pct,
                vwap_arr=vwap_arr,
            )
            capital, win = _append_trade(
                trades=trades,
                prep=prep,
                entry_idx=i,
                exit_idx=exit_idx,
                is_short=is_short,
                exit_price=exit_price,
                reason=reason,
                capital=capital,
                pos_size=pos,
            )
            if win:
                losing_streak = 0
            else:
                losing_streak += 1
                max_losing_streak = max(max_losing_streak, losing_streak)

            if signal_long:
                long_taken = True
            if signal_short:
                short_taken = True
            i = exit_idx + 1

    return trades, max_losing_streak


def simulate_candidate(prep: PreparedData, row: pd.Series) -> tuple[list[dict[str, Any]], int, str | None]:
    strategy = str(row["strategy"])
    vwap_window = int(row["vwap_window"]) if not math.isnan(float(row["vwap_window"])) else 1
    if vwap_window not in prep.vwaps:
        vwap_window = 1
    vwap_arr = prep.vwaps[vwap_window]

    try:
        if strategy in {"MomShort", "MomLong", "RejShort", "RejLong"}:
            trades, mls = _simulate_momentum_or_rejection(prep, strategy, row, vwap_arr)
        elif strategy == "VWAPPullback":
            trades, mls = _simulate_pullback(prep, row, vwap_arr)
        elif strategy == "EMAScalp":
            trades, mls = _simulate_ema_scalp(prep, row, vwap_arr)
        elif strategy == "PDHL":
            trades, mls = _simulate_pdhl(prep, row, vwap_arr)
        elif strategy == "ORB":
            trades, mls = _simulate_orb(prep, row, vwap_arr)
        else:
            return [], 0, f"unsupported strategy for layer3: {strategy}"
    except Exception as exc:
        return [], 0, str(exc)
    return trades, mls, None


def compute_monthly_metrics(trades: list[dict[str, Any]]) -> tuple[int, float, float]:
    if not trades:
        return 0, 0.0, -999.0

    cap = INITIAL_CAPITAL
    months: list[float] = []
    cur_month: str | None = None
    month_start_cap = INITIAL_CAPITAL
    month_net = 0.0

    for t in trades:
        exit_time = pd.Timestamp(t["exit_time"])
        m = f"{exit_time.year:04d}-{exit_time.month:02d}"
        if cur_month is None:
            cur_month = m
            month_start_cap = cap
            month_net = 0.0
        elif m != cur_month:
            denom = month_start_cap if month_start_cap > 0 else INITIAL_CAPITAL
            months.append((month_net / denom) * 100.0)
            cur_month = m
            month_start_cap = cap
            month_net = 0.0

        month_net += float(t["net_pnl"])
        cap = float(t["capital_after"])

    denom = month_start_cap if month_start_cap > 0 else INITIAL_CAPITAL
    months.append((month_net / denom) * 100.0)

    arr = np.array(months, dtype=float)
    positive_ratio = float((arr > 0.0).mean()) if len(arr) else 0.0
    worst_month = float(arr.min()) if len(arr) else -999.0
    return len(arr), positive_ratio, worst_month


def compute_trade_behavior_metrics(trades: list[dict[str, Any]], total_days: int) -> tuple[float, float, float]:
    if not trades:
        return 0.0, 0.0, 0.0

    holds: list[float] = []
    eod_count = 0
    for t in trades:
        entry_time = pd.Timestamp(t["entry_time"])
        exit_time = pd.Timestamp(t["exit_time"])
        hold_minutes = max((exit_time - entry_time).total_seconds() / 60.0, 0.0)
        holds.append(float(hold_minutes))
        if str(t.get("reason", "")).upper() == "EOD":
            eod_count += 1

    avg_hold_minutes = float(np.mean(holds)) if holds else 0.0
    eod_ratio_pct = float(eod_count / len(trades) * 100.0) if trades else 0.0
    avg_trades_per_day = float(len(trades) / max(total_days, 1))
    return avg_hold_minutes, eod_ratio_pct, avg_trades_per_day


def run_layer3(
    layer2_df: pd.DataFrame,
    symbol: str,
    klines_dir: Path,
    args: argparse.Namespace,
) -> pd.DataFrame:
    if layer2_df.empty:
        return layer2_df.copy()

    candidates = layer2_df.copy()
    candidates = candidates.sort_values(
        by=["ret_dd", "return_pct", "trades"],
        ascending=[False, False, False],
        ignore_index=True,
    ).head(args.layer3_top_n)

    prepared_cache: dict[str, PreparedData] = {}
    enriched: list[pd.Series] = []
    symbol_lower = symbol.lower()

    for i, (_, row) in enumerate(candidates.iterrows(), start=1):
        tf = str(row["timeframe"])
        kline_path = klines_dir / f"{symbol_lower}_{tf}_klines.csv"

        base = row.copy()
        base["layer3_error"] = ""
        base["months_total"] = 0
        base["positive_month_ratio"] = 0.0
        base["worst_month_return_pct"] = -999.0
        base["layer3_max_losing_streak"] = 0
        base["layer3_trades"] = 0
        base["layer3_return_pct"] = -999.0
        base["layer3_max_dd_pct"] = 999.0
        base["layer3_avg_hold_minutes"] = 0.0
        base["layer3_eod_ratio_pct"] = 0.0
        base["layer3_avg_trades_per_day"] = 0.0
        base["layer3_pass"] = False

        if not kline_path.exists():
            base["layer3_error"] = f"missing kline file: {kline_path}"
            enriched.append(base)
            continue

        if tf not in prepared_cache:
            prepared_cache[tf] = prepare_klines(kline_path)
        prep = prepared_cache[tf]

        print(
            f"  [Layer3 {i}/{len(candidates)}] {row['strategy']} {tf} "
            f"TP={row['tp_pct']:.2f}% SL={row['sl_pct']:.2f}%"
        )
        trades, mls, err = simulate_candidate(prep, row)
        if err:
            base["layer3_error"] = err
            enriched.append(base)
            continue
        if not trades:
            base["layer3_error"] = "no trades in layer3 simulation"
            enriched.append(base)
            continue

        equity = np.array([INITIAL_CAPITAL] + [float(t["capital_after"]) for t in trades], dtype=float)
        peak = np.maximum.accumulate(equity)
        drawdown = np.where(peak > 0, (peak - equity) / peak, 0.0)
        max_dd = float(drawdown.max()) * 100.0
        final_cap = float(equity[-1])
        ret = (final_cap / INITIAL_CAPITAL - 1.0) * 100.0

        months_total, pos_ratio, worst_month = compute_monthly_metrics(trades)
        avg_hold_minutes, eod_ratio_pct, avg_trades_per_day = compute_trade_behavior_metrics(trades, len(prep.day_slices))

        passed = (
            months_total >= args.min_months
            and pos_ratio >= args.min_positive_month_ratio
            and worst_month >= -abs(args.max_worst_month_loss)
            and mls <= args.max_losing_streak
            and ret > 0.0
            and (args.max_eod_ratio <= 0 or eod_ratio_pct <= args.max_eod_ratio)
            and (args.max_avg_hold_minutes <= 0 or avg_hold_minutes <= args.max_avg_hold_minutes)
            and (args.min_avg_trades_per_day <= 0 or avg_trades_per_day >= args.min_avg_trades_per_day)
        )

        base["months_total"] = months_total
        base["positive_month_ratio"] = pos_ratio
        base["worst_month_return_pct"] = worst_month
        base["layer3_max_losing_streak"] = mls
        base["layer3_trades"] = len(trades)
        base["layer3_return_pct"] = ret
        base["layer3_max_dd_pct"] = max_dd
        base["layer3_avg_hold_minutes"] = avg_hold_minutes
        base["layer3_eod_ratio_pct"] = eod_ratio_pct
        base["layer3_avg_trades_per_day"] = avg_trades_per_day
        base["layer3_pass"] = passed
        enriched.append(base)

    out = pd.DataFrame(enriched)
    out = out[out["layer3_pass"] == True].copy()  # noqa: E712
    if out.empty:
        return out

    out["final_score"] = (
        0.35 * out["ret_dd"]
        + 0.25 * out["return_pct"]
        - 0.20 * out["max_dd_pct"]
        + 0.20 * np.log1p(out["trades"])
    )
    out = out.sort_values(by=["final_score", "ret_dd", "return_pct"], ascending=[False, False, False], ignore_index=True)
    return out


def save_outputs(symbol: str, sweeps_dir: Path, layer1: pd.DataFrame, layer2: pd.DataFrame, layer3: pd.DataFrame, output_tag: str) -> tuple[Path, Path, Path]:
    sweeps_dir.mkdir(parents=True, exist_ok=True)
    symbol_lower = symbol.lower()
    prefix = f"{symbol_lower}_{output_tag}" if output_tag else symbol_lower
    out1 = sweeps_dir / f"{prefix}_anti_overfit_layer1.csv"
    out2 = sweeps_dir / f"{prefix}_anti_overfit_layer2.csv"
    out3 = sweeps_dir / f"{prefix}_anti_overfit_final.csv"
    layer1.to_csv(out1, index=False)
    layer2.to_csv(out2, index=False)
    layer3.to_csv(out3, index=False)
    return out1, out2, out3


def main() -> None:
    args = parse_args()
    symbol = args.symbol.strip().lower()
    timeframes = [x.strip() for x in args.timeframes.split(",") if x.strip()]
    if not timeframes:
        timeframes = SUPPORTED_TFS

    sweeps_dir = Path(args.sweeps_dir)
    klines_dir = Path(args.klines_dir)
    output_tag = args.output_tag.strip() or ("" if args.sweep_suffix == "sweep" else args.sweep_suffix)

    print(f"Loading sweeps for {symbol.upper()} from {sweeps_dir} ...")
    try:
        all_df, missing_tfs = load_sweeps(symbol, timeframes, sweeps_dir, args.sweep_suffix.strip())
    except FileNotFoundError as exc:
        raise SystemExit(f"❌ {exc}") from exc
    except Exception as exc:
        raise SystemExit(f"❌ Failed to load/normalize sweeps: {exc}") from exc
    if missing_tfs:
        print(f"  Missing timeframes (skipped): {', '.join(missing_tfs)}")
    print(f"  Loaded rows: {len(all_df):,}")

    layer1 = run_layer1(all_df, args)
    print(f"Layer 1 passed: {len(layer1):,}")

    layer2 = run_layer2(layer1, all_df, args)
    print(f"Layer 2 passed: {len(layer2):,}")

    layer3 = run_layer3(layer2, symbol, klines_dir, args)
    print(f"Layer 3 passed: {len(layer3):,}")

    out1, out2, out3 = save_outputs(symbol, sweeps_dir, layer1, layer2, layer3, output_tag)
    print("")
    print("Outputs:")
    print(f"  Layer 1: {out1}")
    print(f"  Layer 2: {out2}")
    print(f"  Layer 3: {out3}")

    if not layer3.empty:
        cols = [
            "strategy", "timeframe", "tp_pct", "sl_pct", "time_stop_minutes",
            "time_stop_min_progress_pct", "adverse_exit_bars", "adverse_body_min_pct", "trades", "win_rate",
            "return_pct", "max_dd_pct", "eod_ratio_pct", "avg_hold_minutes", "avg_trades_per_day", "ret_dd", "final_score",
            "months_total", "positive_month_ratio", "worst_month_return_pct", "layer3_max_losing_streak",
            "layer3_eod_ratio_pct", "layer3_avg_hold_minutes", "layer3_avg_trades_per_day",
        ]
        top = layer3[cols].head(10)
        print("")
        print("Top approved setups:")
        print(top.to_string(index=False))
    else:
        print("")
        print("No setup survived all 3 layers with current thresholds.")


if __name__ == "__main__":
    main()
