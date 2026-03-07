#!/usr/bin/env python3
"""
Walk-forward validation with Rust optimization per train window.

Workflow per fold:
1) Optimize on train window with Rust sweep binary (or cache hit).
2) Pick best candidate by metric with minimum quality filters.
3) Evaluate frozen candidate on next test window (out-of-sample) in Python.

Example:
    python scripts/walk_forward.py --symbol ldousdt --timeframe 1m
"""

from __future__ import annotations

import argparse
import hashlib
import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from filter_overfit import INITIAL_CAPITAL, compute_monthly_metrics, normalize_sweep_df, prepare_klines, simulate_candidate


@dataclass
class FoldSpec:
    fold: int
    train_start_day: int
    train_end_day_exclusive: int
    test_start_day: int
    test_end_day_exclusive: int


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Walk-forward validator (Rust optimize + OOS test).")
    p.add_argument("--symbol", required=True, help="Symbol, e.g. ldousdt")
    p.add_argument("--timeframe", default="1m", help="1m,5m,15m,30m,1h")
    p.add_argument("--klines-dir", default="data/klines")
    p.add_argument("--sweeps-dir", default="data/sweeps")
    p.add_argument("--binary", default="./backtest_sweep/target/release/backtest_sweep")
    p.add_argument("--cache-dir", default="data/sweeps/walkforward_cache")
    p.add_argument("--out-prefix", default="", help="Optional output prefix")

    p.add_argument("--train-days", type=int, default=180)
    p.add_argument("--test-days", type=int, default=30)
    p.add_argument("--step-days", type=int, default=30)
    p.add_argument("--max-folds", type=int, default=0, help="0 means all possible folds")

    # Candidate selection on train sweep.
    p.add_argument("--metric", choices=["ret_dd", "return_pct"], default="ret_dd")
    p.add_argument("--min-train-trades", type=int, default=80)
    p.add_argument("--max-train-dd", type=float, default=15.0)
    p.add_argument("--min-train-return", type=float, default=0.0)
    p.add_argument("--max-train-eod-ratio", type=float, default=-1.0, help="Reject train candidates above this EOD exit ratio percentage; <=0 disables")
    p.add_argument("--max-train-avg-hold", type=float, default=-1.0, help="Reject train candidates above this average hold in minutes; <=0 disables")
    p.add_argument("--min-train-trades-per-day", type=float, default=-1.0, help="Reject train candidates below this average trades-per-day when the metric is available; <=0 disables")
    p.add_argument("--require-unique", action="store_true", help="Drop duplicate param sets before ranking")
    return p.parse_args()


def _hash_fold(symbol: str, timeframe: str, train_start: int, train_end: int, binary: Path) -> str:
    binary_resolved = binary.resolve()
    binary_stat = binary_resolved.stat()
    raw = (
        f"{symbol}|{timeframe}|{train_start}|{train_end}|"
        f"{binary_resolved}|{binary_stat.st_size}|{binary_stat.st_mtime_ns}"
    )
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:10]


def load_klines_with_day(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, low_memory=False)
    if "open_time" not in df.columns:
        raise ValueError(f"Missing open_time column in {csv_path}")
    df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce")
    if df["open_time"].isna().any():
        raise ValueError(f"Invalid open_time values in {csv_path}")
    df["day"] = (df["open_time"].astype(np.int64) // 1000 // 86400).astype(np.int64)
    return df


def build_folds(unique_days: list[int], train_days: int, test_days: int, step_days: int, max_folds: int) -> list[FoldSpec]:
    folds: list[FoldSpec] = []
    i = 0
    fold_num = 1
    total_days = len(unique_days)
    while i + train_days + test_days <= total_days:
        train_slice = unique_days[i : i + train_days]
        test_slice = unique_days[i + train_days : i + train_days + test_days]
        if not train_slice or not test_slice:
            break
        folds.append(
            FoldSpec(
                fold=fold_num,
                train_start_day=int(train_slice[0]),
                train_end_day_exclusive=int(train_slice[-1] + 1),
                test_start_day=int(test_slice[0]),
                test_end_day_exclusive=int(test_slice[-1] + 1),
            )
        )
        fold_num += 1
        i += step_days
        if max_folds > 0 and len(folds) >= max_folds:
            break
    return folds


def write_window_csv(df: pd.DataFrame, day_start: int, day_end_exclusive: int, out_csv: Path) -> int:
    window = df[(df["day"] >= day_start) & (df["day"] < day_end_exclusive)].copy()
    window.to_csv(out_csv, index=False)
    return len(window)


def run_rust_sweep(train_csv: Path, binary: Path, cwd: Path) -> None:
    if not binary.exists():
        raise FileNotFoundError(f"Sweep binary not found: {binary}. Build with: make build-sweep")
    proc = subprocess.run([str(binary), str(train_csv)], cwd=str(cwd), capture_output=True, text=True)
    if proc.returncode != 0:
        msg = "\n".join([proc.stdout[-1000:], proc.stderr[-1000:]])
        raise RuntimeError(f"Rust sweep failed.\n{msg}")


def choose_candidate(train_results: pd.DataFrame, timeframe: str, args: argparse.Namespace) -> pd.Series | None:
    if train_results.empty:
        return None

    norm = normalize_sweep_df(train_results.assign(timeframe=timeframe))
    filt = norm[
        (norm["trades"] >= args.min_train_trades)
        & (norm["max_dd_pct"] <= args.max_train_dd)
        & (norm["return_pct"] >= args.min_train_return)
    ].copy()
    if args.max_train_eod_ratio > 0 and "eod_ratio_pct" in filt.columns:
        filt = filt[filt["eod_ratio_pct"].fillna(0.0) <= args.max_train_eod_ratio].copy()
    if args.max_train_avg_hold > 0 and "avg_hold_minutes" in filt.columns:
        filt = filt[filt["avg_hold_minutes"].fillna(0.0) <= args.max_train_avg_hold].copy()
    if args.min_train_trades_per_day > 0 and "avg_trades_per_day" in filt.columns:
        filt = filt[filt["avg_trades_per_day"].fillna(0.0) >= args.min_train_trades_per_day].copy()

    if filt.empty:
        return None

    if args.require_unique:
        dedup_cols = [c for c in [
            "strategy", "tp_pct", "sl_pct", "min_bars", "confirm_bars", "entry_window", "vwap_prox",
            "vwap_window", "ema_period", "max_trades_per_day", "fast_period", "slow_period",
            "orb_range_mins", "pdhl_prox_pct", "max_hold", "vwap_dist_stop",
            "time_stop_minutes", "time_stop_min_progress_pct", "adverse_exit_bars", "adverse_body_min_pct",
            "pos_size_pct",
        ] if c in filt.columns]
        if dedup_cols:
            filt = filt.drop_duplicates(subset=dedup_cols, keep="first")

    sort_cols = ["ret_dd", "return_pct", "trades"] if args.metric == "ret_dd" else ["return_pct", "ret_dd", "trades"]
    filt = filt.sort_values(by=sort_cols, ascending=[False, False, False], ignore_index=True)
    return filt.iloc[0].copy()


def eval_candidate_on_test(test_csv: Path, candidate: pd.Series) -> dict[str, Any]:
    prep = prepare_klines(test_csv)
    trades, max_losing_streak, err = simulate_candidate(prep, candidate)
    if err is not None:
        return {
            "test_trades": 0,
            "test_wins": 0,
            "test_win_rate": 0.0,
            "test_return_pct": 0.0,
            "test_max_dd_pct": 0.0,
            "test_eod_ratio_pct": 0.0,
            "test_avg_hold_minutes": 0.0,
            "test_avg_trades_per_day": 0.0,
            "test_months_total": 0,
            "test_positive_month_ratio": 0.0,
            "test_worst_month_return_pct": 0.0,
            "test_max_losing_streak": 0,
            "test_error": err,
        }

    if not trades:
        return {
            "test_trades": 0,
            "test_wins": 0,
            "test_win_rate": 0.0,
            "test_return_pct": 0.0,
            "test_max_dd_pct": 0.0,
            "test_eod_ratio_pct": 0.0,
            "test_avg_hold_minutes": 0.0,
            "test_avg_trades_per_day": 0.0,
            "test_months_total": 0,
            "test_positive_month_ratio": 0.0,
            "test_worst_month_return_pct": 0.0,
            "test_max_losing_streak": 0,
            "test_error": "no trades in test window",
        }

    wins = int(sum(1 for t in trades if float(t["net_pnl"]) > 0.0))
    n = len(trades)
    win_rate = wins / n * 100.0

    equity = np.array([INITIAL_CAPITAL] + [float(t["capital_after"]) for t in trades], dtype=float)
    peak = np.maximum.accumulate(equity)
    drawdown = np.where(peak > 0, (peak - equity) / peak, 0.0)
    max_dd = float(drawdown.max()) * 100.0
    ret = (float(equity[-1]) / INITIAL_CAPITAL - 1.0) * 100.0

    months_total, pos_ratio, worst_month = compute_monthly_metrics(trades)
    hold_minutes = [
        max((pd.Timestamp(t["exit_time"]) - pd.Timestamp(t["entry_time"])).total_seconds() / 60.0, 0.0)
        for t in trades
    ]
    eod_ratio_pct = float(sum(1 for t in trades if str(t.get("reason", "")).upper() == "EOD") / n * 100.0)
    return {
        "test_trades": n,
        "test_wins": wins,
        "test_win_rate": win_rate,
        "test_return_pct": ret,
        "test_max_dd_pct": max_dd,
        "test_eod_ratio_pct": eod_ratio_pct,
        "test_avg_hold_minutes": float(np.mean(hold_minutes)) if hold_minutes else 0.0,
        "test_avg_trades_per_day": float(n / max(len(prep.day_slices), 1)),
        "test_months_total": months_total,
        "test_positive_month_ratio": pos_ratio,
        "test_worst_month_return_pct": worst_month,
        "test_max_losing_streak": int(max_losing_streak),
        "test_error": "",
    }


def empty_test_metrics(error: str = "") -> dict[str, Any]:
    return {
        "test_trades": 0,
        "test_wins": 0,
        "test_win_rate": 0.0,
        "test_return_pct": 0.0,
        "test_max_dd_pct": 0.0,
        "test_eod_ratio_pct": 0.0,
        "test_avg_hold_minutes": 0.0,
        "test_avg_trades_per_day": 0.0,
        "test_months_total": 0,
        "test_positive_month_ratio": 0.0,
        "test_worst_month_return_pct": 0.0,
        "test_max_losing_streak": 0,
        "test_error": error,
    }


def main() -> None:
    args = parse_args()

    symbol = args.symbol.lower()
    tf = args.timeframe.lower()
    repo = Path.cwd()
    klines_dir = Path(args.klines_dir)
    sweeps_dir = Path(args.sweeps_dir)
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    sweeps_dir.mkdir(parents=True, exist_ok=True)

    kline_csv = klines_dir / f"{symbol}_{tf}_klines.csv"
    if not kline_csv.exists():
        raise SystemExit(f"❌ Missing kline file: {kline_csv}")

    binary = Path(args.binary)
    if not binary.is_absolute():
        binary = repo / binary

    df = load_klines_with_day(kline_csv)
    unique_days = sorted(df["day"].unique().tolist())
    folds = build_folds(unique_days, args.train_days, args.test_days, args.step_days, args.max_folds)
    if not folds:
        raise SystemExit("❌ No folds available with current train/test/step settings.")

    print(
        f"Walk-forward: {symbol.upper()} {tf} | days={len(unique_days)} "
        f"| train={args.train_days} test={args.test_days} step={args.step_days} | folds={len(folds)}"
    )

    fold_rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="wf_") as tdir:
        tpath = Path(tdir)
        for fs in folds:
            print(f"\n[Fold {fs.fold}] train={fs.train_start_day}->{fs.train_end_day_exclusive - 1} "
                  f"test={fs.test_start_day}->{fs.test_end_day_exclusive - 1}")

            fold_hash = _hash_fold(symbol, tf, fs.train_start_day, fs.train_end_day_exclusive, binary)
            cache_train_sweep = cache_dir / f"{symbol}_{tf}_{fold_hash}_train_sweep.csv"
            train_csv = tpath / f"train_{fs.fold}.csv"
            test_csv = tpath / f"test_{fs.fold}.csv"

            train_candles = write_window_csv(df, fs.train_start_day, fs.train_end_day_exclusive, train_csv)
            test_candles = write_window_csv(df, fs.test_start_day, fs.test_end_day_exclusive, test_csv)
            print(f"  candles: train={train_candles} test={test_candles}")

            if cache_train_sweep.exists():
                train_results = pd.read_csv(cache_train_sweep, low_memory=False)
                print(f"  cache hit: {cache_train_sweep.name} ({len(train_results):,} rows)")
            else:
                run_rust_sweep(train_csv, binary=binary, cwd=repo)
                generated = repo / "backtest_sweep.csv"
                if not generated.exists():
                    raise RuntimeError("Expected backtest_sweep.csv after Rust sweep, but file is missing.")
                generated.replace(cache_train_sweep)
                train_results = pd.read_csv(cache_train_sweep, low_memory=False)
                print(f"  cache save: {cache_train_sweep.name} ({len(train_results):,} rows)")

            cand = choose_candidate(train_results, timeframe=tf, args=args)
            if cand is None:
                print("  no candidate passed train filters")
                row = {
                    "fold": fs.fold,
                    "train_start_day": fs.train_start_day,
                    "train_end_day_exclusive": fs.train_end_day_exclusive,
                    "test_start_day": fs.test_start_day,
                    "test_end_day_exclusive": fs.test_end_day_exclusive,
                    "candidate_found": False,
                    "candidate_error": "no candidate passed train filters",
                }
                row.update(empty_test_metrics())
                fold_rows.append(row)
                continue

            test_metrics = eval_candidate_on_test(test_csv, cand)
            print(
                f"  candidate: {cand['strategy']} tp={cand['tp_pct']:.2f}% sl={cand['sl_pct']:.2f}% "
                f"train_ret={cand['return_pct']:.2f}% train_dd={cand['max_dd_pct']:.2f}% "
                f"test_ret={test_metrics['test_return_pct']:.2f}% test_dd={test_metrics['test_max_dd_pct']:.2f}%"
            )

            row: dict[str, Any] = {
                "fold": fs.fold,
                "train_start_day": fs.train_start_day,
                "train_end_day_exclusive": fs.train_end_day_exclusive,
                "test_start_day": fs.test_start_day,
                "test_end_day_exclusive": fs.test_end_day_exclusive,
                "candidate_found": True,
                "candidate_error": "",
                "strategy": cand.get("strategy", ""),
                "tp_pct": float(cand.get("tp_pct", math.nan)),
                "sl_pct": float(cand.get("sl_pct", math.nan)),
                "rr_ratio": float(cand.get("rr_ratio", math.nan)),
                "min_bars": int(cand.get("min_bars", 0)),
                "confirm_bars": int(cand.get("confirm_bars", 0)),
                "entry_window": cand.get("entry_window", ""),
                "vwap_prox": float(cand.get("vwap_prox", math.nan)),
                "vwap_window": float(cand.get("vwap_window", math.nan)),
                "ema_period": float(cand.get("ema_period", math.nan)),
                "max_trades_per_day": float(cand.get("max_trades_per_day", math.nan)),
                "fast_period": float(cand.get("fast_period", math.nan)),
                "slow_period": float(cand.get("slow_period", math.nan)),
                "orb_range_mins": float(cand.get("orb_range_mins", math.nan)),
                "pdhl_prox_pct": float(cand.get("pdhl_prox_pct", math.nan)),
                "max_hold": int(cand.get("max_hold", 0)),
                "vwap_dist_stop": float(cand.get("vwap_dist_stop", math.nan)),
                "time_stop_minutes": int(cand.get("time_stop_minutes", 0)),
                "time_stop_min_progress_pct": float(cand.get("time_stop_min_progress_pct", 0.0)),
                "adverse_exit_bars": int(cand.get("adverse_exit_bars", 0)),
                "adverse_body_min_pct": float(cand.get("adverse_body_min_pct", 0.0)),
                "pos_size_pct": float(cand.get("pos_size_pct", math.nan)),
                "train_trades": int(cand.get("trades", 0)),
                "train_win_rate": float(cand.get("win_rate", 0.0)),
                "train_return_pct": float(cand.get("return_pct", 0.0)),
                "train_max_dd_pct": float(cand.get("max_dd_pct", 0.0)),
                "train_eod_ratio_pct": float(cand.get("eod_ratio_pct", 0.0)),
                "train_avg_hold_minutes": float(cand.get("avg_hold_minutes", 0.0)),
                "train_avg_trades_per_day": float(cand.get("avg_trades_per_day", 0.0)),
                "train_ret_dd": float(cand.get("ret_dd", 0.0)),
            }
            row.update(test_metrics)
            fold_rows.append(row)

    folds_df = pd.DataFrame(fold_rows)
    if folds_df.empty:
        raise SystemExit("❌ No fold rows generated.")

    if "test_error" not in folds_df.columns:
        folds_df["test_error"] = ""

    out_prefix = args.out_prefix.strip() or f"{symbol}_{tf}"
    folds_out = sweeps_dir / f"{out_prefix}_walkforward_folds.csv"
    summary_out = sweeps_dir / f"{out_prefix}_walkforward_summary.csv"
    folds_df.to_csv(folds_out, index=False)

    valid = folds_df[(folds_df["candidate_found"] == True) & (folds_df["test_error"].fillna("") == "")].copy()  # noqa: E712
    if valid.empty:
        summary = pd.DataFrame([{
            "symbol": symbol.upper(),
            "timeframe": tf,
            "folds_total": len(folds_df),
            "folds_with_candidate": int(folds_df["candidate_found"].sum()),
            "folds_valid_test": 0,
            "oos_compound_return_pct": 0.0,
            "avg_test_return_pct": 0.0,
            "median_test_return_pct": 0.0,
            "positive_test_fold_ratio": 0.0,
            "avg_test_max_dd_pct": 0.0,
            "avg_test_eod_ratio_pct": 0.0,
            "avg_test_avg_hold_minutes": 0.0,
            "avg_test_avg_trades_per_day": 0.0,
        }])
    else:
        cap = INITIAL_CAPITAL
        for r in valid["test_return_pct"].tolist():
            cap *= (1.0 + float(r) / 100.0)
        oos_compound = (cap / INITIAL_CAPITAL - 1.0) * 100.0
        summary = pd.DataFrame([{
            "symbol": symbol.upper(),
            "timeframe": tf,
            "folds_total": len(folds_df),
            "folds_with_candidate": int(folds_df["candidate_found"].sum()),
            "folds_valid_test": len(valid),
            "oos_compound_return_pct": oos_compound,
            "avg_test_return_pct": float(valid["test_return_pct"].mean()),
            "median_test_return_pct": float(valid["test_return_pct"].median()),
            "positive_test_fold_ratio": float((valid["test_return_pct"] > 0.0).mean()),
            "avg_test_max_dd_pct": float(valid["test_max_dd_pct"].mean()),
            "avg_test_eod_ratio_pct": float(valid["test_eod_ratio_pct"].mean()),
            "avg_test_avg_hold_minutes": float(valid["test_avg_hold_minutes"].mean()),
            "avg_test_avg_trades_per_day": float(valid["test_avg_trades_per_day"].mean()),
        }])

    summary.to_csv(summary_out, index=False)

    print("\nSaved:")
    print(f"  folds:   {folds_out}")
    print(f"  summary: {summary_out}")
    print("\nSummary:")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
