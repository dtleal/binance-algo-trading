#!/usr/bin/env python3
"""Detailed PDHL backtest with fixed TP/SL and optional runtime protections.

This script is intended for onboarding validation of PDHL candidates.

It supports two signal engines:
- ``live``: uses ``trader.strategy_pdhl.PDHLSignal`` (matches the live bot signal code)
- ``sweep``: uses a local implementation that mirrors the Rust sweep confirmation resets

Execution is single-position and serial: a new trade is not opened until the prior
trade closes. This matches live bot behavior and will differ from the Rust sweep
whenever the sweep generated overlapping same-day entries.

Maker-first execution is not simulated from OHLC candles. Fees default to a
conservative taker-like assumption.
"""

from __future__ import annotations

import argparse
import math
import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from trader.strategy_pdhl import PDHLSignal


DEFAULT_CSV_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "../data/klines/ICXUSDT_5m_klines.csv",
)


@dataclass(frozen=True)
class BacktestConfig:
    csv_file: str
    signal_engine: str
    tp_pct: float
    sl_pct: float
    confirm_bars: int
    prox_pct: float
    entry_start_min: int
    entry_cutoff_min: int
    eod_min: int
    max_hold_min: int
    max_trades_per_day: int
    pos_size_pct: float
    initial_capital: float
    fee_pct: float
    price_decimals: int
    qty_decimals: int
    min_notional: float
    be_profit_usd: float
    enable_breakeven: bool
    enable_runtime_guards: bool
    time_stop_minutes: int
    time_stop_min_progress_pct: float
    adverse_exit_bars: int
    adverse_body_min_pct: float
    output_prefix: str | None


class SweepPDHLSignal:
    """Rust-sweep-compatible PDHL signal state machine."""

    def __init__(
        self,
        prox_pct: float,
        confirm_bars: int,
        max_trades_per_day: int,
        entry_start_min: int,
        entry_cutoff_min: int,
    ):
        self.prox_pct = prox_pct
        self.confirm_bars = confirm_bars
        self.max_trades_per_day = max_trades_per_day
        self.entry_start_min = entry_start_min
        self.entry_cutoff_min = entry_cutoff_min

        self._pdh: float | None = None
        self._pdl: float | None = None
        self._today_high: float | None = None
        self._today_low: float | None = None
        self._testing_pdh = False
        self._testing_pdl = False
        self._pdh_conf = 0
        self._pdl_conf = 0
        self.trades_today = 0

    @property
    def traded_today(self) -> bool:
        return self.trades_today >= self.max_trades_per_day

    def reset_daily(self):
        if self._today_high is not None:
            self._pdh = self._today_high
            self._pdl = self._today_low

        self._today_high = None
        self._today_low = None
        self._testing_pdh = False
        self._testing_pdl = False
        self._pdh_conf = 0
        self._pdl_conf = 0
        self.trades_today = 0

    def reset_signal(self):
        self._testing_pdh = False
        self._testing_pdl = False
        self._pdh_conf = 0
        self._pdl_conf = 0

    def mark_traded(self):
        self.trades_today += 1

    def on_candle(
        self,
        close: float,
        high: float,
        low: float,
        minute_of_day: int,
    ) -> str | None:
        if self._today_high is None:
            self._today_high = high
            self._today_low = low
        else:
            self._today_high = max(self._today_high, high)
            self._today_low = min(self._today_low, low)

        if self._pdh is None or self._pdl is None:
            return None
        if self.traded_today:
            return None
        if minute_of_day < self.entry_start_min or minute_of_day >= self.entry_cutoff_min:
            return None

        pdh = self._pdh
        pdl = self._pdl

        if high >= pdh * (1 - self.prox_pct):
            self._testing_pdh = True
        if self._testing_pdh:
            if close < pdh * (1 - self.prox_pct):
                self._pdh_conf += 1
                if self._pdh_conf >= self.confirm_bars:
                    self._testing_pdh = False
                    self._pdh_conf = 0
                    self.trades_today += 1
                    return "ENTER_SHORT"
            elif close >= pdh * (1 - self.prox_pct):
                self._pdh_conf = 0
            elif close > pdh * (1 + self.prox_pct):
                self._testing_pdh = False
                self._pdh_conf = 0

        if low <= pdl * (1 + self.prox_pct):
            self._testing_pdl = True
        if self._testing_pdl:
            if close > pdl * (1 + self.prox_pct):
                self._pdl_conf += 1
                if self._pdl_conf >= self.confirm_bars:
                    self._testing_pdl = False
                    self._pdl_conf = 0
                    self.trades_today += 1
                    return "ENTER_LONG"
            elif close <= pdl * (1 + self.prox_pct):
                self._pdl_conf = 0
            elif close < pdl * (1 - self.prox_pct):
                self._testing_pdl = False
                self._pdl_conf = 0

        return None


def parse_args() -> BacktestConfig:
    parser = argparse.ArgumentParser(description="Detailed fixed-TP PDHL backtest")
    parser.add_argument("--csv", default=DEFAULT_CSV_FILE, help="Kline CSV path")
    parser.add_argument("--signal-engine", choices=["live", "sweep"], default="live")
    parser.add_argument("--tp", type=float, default=7.0, help="Take-profit percent")
    parser.add_argument("--sl", type=float, default=2.0, help="Stop-loss percent")
    parser.add_argument("--confirm-bars", type=int, default=2)
    parser.add_argument("--prox-pct", type=float, default=0.005, help="PDHL proximity as fraction")
    parser.add_argument("--entry-start", type=int, default=60, help="Entry window start minute UTC")
    parser.add_argument("--entry-cutoff", type=int, default=1320, help="Entry cutoff minute UTC")
    parser.add_argument("--eod-min", type=int, default=1430, help="Force-close minute UTC")
    parser.add_argument("--max-hold", type=int, default=0, help="0 means hold until EOD")
    parser.add_argument("--max-trades", type=int, default=4)
    parser.add_argument("--pos-size", type=float, default=0.20, help="Fraction of capital per trade")
    parser.add_argument("--initial-capital", type=float, default=1000.0)
    parser.add_argument("--fee-pct", type=float, default=0.0004, help="Fee per side")
    parser.add_argument("--price-decimals", type=int, default=4)
    parser.add_argument("--qty-decimals", type=int, default=0)
    parser.add_argument("--min-notional", type=float, default=5.0)
    parser.add_argument("--be-profit-usd", type=float, default=0.50)
    parser.add_argument("--disable-breakeven", action="store_true")
    parser.add_argument("--disable-runtime-guards", action="store_true")
    parser.add_argument("--time-stop-minutes", type=int, default=20)
    parser.add_argument("--time-stop-min-progress-pct", type=float, default=0.0)
    parser.add_argument("--adverse-exit-bars", type=int, default=3)
    parser.add_argument("--adverse-body-min-pct", type=float, default=0.20)
    parser.add_argument(
        "--output-prefix",
        default=None,
        help="Optional output prefix; files become <prefix>_trades.csv and <prefix>_analysis.html",
    )
    args = parser.parse_args()

    return BacktestConfig(
        csv_file=args.csv,
        signal_engine=args.signal_engine,
        tp_pct=args.tp / 100.0,
        sl_pct=args.sl / 100.0,
        confirm_bars=args.confirm_bars,
        prox_pct=args.prox_pct,
        entry_start_min=args.entry_start,
        entry_cutoff_min=args.entry_cutoff,
        eod_min=args.eod_min,
        max_hold_min=args.max_hold,
        max_trades_per_day=args.max_trades,
        pos_size_pct=args.pos_size,
        initial_capital=args.initial_capital,
        fee_pct=args.fee_pct,
        price_decimals=args.price_decimals,
        qty_decimals=args.qty_decimals,
        min_notional=args.min_notional,
        be_profit_usd=args.be_profit_usd,
        enable_breakeven=not args.disable_breakeven,
        enable_runtime_guards=not args.disable_runtime_guards,
        time_stop_minutes=args.time_stop_minutes,
        time_stop_min_progress_pct=args.time_stop_min_progress_pct,
        adverse_exit_bars=args.adverse_exit_bars,
        adverse_body_min_pct=args.adverse_body_min_pct,
        output_prefix=args.output_prefix,
    )


def load(csv_file: str) -> pd.DataFrame:
    df = pd.read_csv(csv_file)
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.sort_values("time").set_index("time")
    df["date"] = df.index.date
    df["minute"] = df.index.hour * 60 + df.index.minute
    return df


def build_signal(cfg: BacktestConfig):
    klass = PDHLSignal if cfg.signal_engine == "live" else SweepPDHLSignal
    return klass(
        prox_pct=cfg.prox_pct,
        confirm_bars=cfg.confirm_bars,
        max_trades_per_day=cfg.max_trades_per_day,
        entry_start_min=cfg.entry_start_min,
        entry_cutoff_min=cfg.entry_cutoff_min,
    )


def round_price(price: float, decimals: int) -> float:
    factor = 10 ** decimals
    return math.floor(price * factor) / factor


def safe_trigger_price(price: float, decimals: int) -> float:
    rounded = round_price(price, decimals)
    if rounded > 0:
        return rounded
    return 10 ** (-decimals) if decimals > 0 else 1.0


def round_qty(qty: float, qty_decimals: int) -> float:
    if qty_decimals <= 0:
        return float(int(math.floor(qty)))
    step = 10 ** (-qty_decimals)
    return math.floor(qty / step) * step


def rollback_trade_counter(signal) -> None:
    signal.trades_today = max(0, signal.trades_today - 1)


def guard_reason(
    cfg: BacktestConfig,
    direction: str,
    candle_open_ms: int,
    entry_ts_ms: int,
    o: float,
    c: float,
    pnl_pct: float,
    adverse_count: int,
) -> tuple[str | None, str | None, int]:
    if cfg.enable_runtime_guards and cfg.time_stop_minutes > 0:
        elapsed_min = (candle_open_ms - entry_ts_ms) / 60_000
        if elapsed_min >= cfg.time_stop_minutes and pnl_pct <= cfg.time_stop_min_progress_pct:
            detail = (
                f"Time stop ({cfg.time_stop_minutes}m): "
                f"PnL {pnl_pct:+.2f}% <= {cfg.time_stop_min_progress_pct:+.2f}%"
            )
            return "TIME_STOP", detail, adverse_count

    body_pct = (abs(c - o) / o * 100) if o else 0.0
    adverse_candle = (
        ((direction == "long" and c < o) or (direction == "short" and c > o))
        and body_pct >= cfg.adverse_body_min_pct
    )
    adverse_count = adverse_count + 1 if adverse_candle else 0
    if (
        cfg.enable_runtime_guards
        and cfg.adverse_exit_bars > 0
        and adverse_count >= cfg.adverse_exit_bars
        and pnl_pct < 0
    ):
        detail = (
            f"Adverse momentum: {adverse_count} candles contra "
            f"(body>={cfg.adverse_body_min_pct:.2f}%, PnL {pnl_pct:+.2f}%)"
        )
        return "ADVERSE_MOMENTUM", detail, adverse_count
    return None, None, adverse_count


def build_output_prefix(cfg: BacktestConfig) -> str:
    if cfg.output_prefix:
        return cfg.output_prefix
    stem = Path(cfg.csv_file).stem.replace("_klines", "")
    return f"data/sweeps/{stem}_pdhl_detailed_{cfg.signal_engine}"


def run_backtest(df: pd.DataFrame, cfg: BacktestConfig):
    signal = build_signal(cfg)
    trades: list[dict] = []
    equity_curve: list[dict] = []
    capital = cfg.initial_capital

    for day_idx, (day, day_df) in enumerate(df.groupby("date", sort=True)):
        if day_idx > 0:
            signal.reset_daily()

        rows = list(day_df.itertuples())
        i = 0
        while i < len(rows):
            row = rows[i]
            sig = signal.on_candle(
                close=row.close,
                high=row.high,
                low=row.low,
                minute_of_day=row.minute,
            )
            if sig is None:
                i += 1
                continue

            direction = "long" if sig == "ENTER_LONG" else "short"
            signal_level = "PDL" if direction == "long" else "PDH"
            level_price = signal._pdl if direction == "long" else signal._pdh
            capital_before = capital
            trade_notional = capital_before * cfg.pos_size_pct
            raw_qty = trade_notional / row.close if row.close > 0 else 0.0
            qty = round_qty(raw_qty, cfg.qty_decimals)
            notional = qty * row.close

            if qty <= 0 or notional < cfg.min_notional:
                rollback_trade_counter(signal)
                i += 1
                continue

            entry_price = row.close
            entry_time = row.Index
            entry_ts_ms = int(entry_time.timestamp() * 1000)
            entry_minute = row.minute
            entry_price_rounded = safe_trigger_price(entry_price, cfg.price_decimals)
            initial_sl = (
                safe_trigger_price(entry_price_rounded * (1 - cfg.sl_pct), cfg.price_decimals)
                if direction == "long"
                else safe_trigger_price(entry_price_rounded * (1 + cfg.sl_pct), cfg.price_decimals)
            )
            tp_price = (
                safe_trigger_price(entry_price_rounded * (1 + cfg.tp_pct), cfg.price_decimals)
                if direction == "long"
                else safe_trigger_price(entry_price_rounded * (1 - cfg.tp_pct), cfg.price_decimals)
            )
            current_sl = initial_sl
            be_triggered = False
            be_trigger_time = None
            be_updates = 0
            adverse_count = 0

            exit_price = rows[-1].close
            exit_time = rows[-1].Index
            exit_reason = "EOD"
            exit_detail = ""
            exit_idx = len(rows) - 1
            bars_held = 0
            mfe_pct = 0.0
            mae_pct = 0.0

            for j in range(i + 1, len(rows)):
                rr = rows[j]
                bars_held += 1

                favorable = (
                    (rr.high - entry_price_rounded) / entry_price_rounded * 100
                    if direction == "long"
                    else (entry_price_rounded - rr.low) / entry_price_rounded * 100
                )
                adverse = (
                    (rr.low - entry_price_rounded) / entry_price_rounded * 100
                    if direction == "long"
                    else (entry_price_rounded - rr.high) / entry_price_rounded * 100
                )
                mfe_pct = max(mfe_pct, favorable)
                mae_pct = min(mae_pct, adverse)

                if cfg.max_hold_min > 0 and rr.minute >= entry_minute + cfg.max_hold_min:
                    exit_price = rr.close
                    exit_time = rr.Index
                    exit_reason = "MAX_HOLD"
                    exit_idx = j
                    break

                if rr.minute >= cfg.eod_min:
                    exit_price = rr.close
                    exit_time = rr.Index
                    exit_reason = "EOD"
                    exit_idx = j
                    break

                if direction == "long":
                    stop_hit = rr.low <= current_sl
                    tp_hit = rr.high >= tp_price
                else:
                    stop_hit = rr.high >= current_sl
                    tp_hit = rr.low <= tp_price

                if stop_hit:
                    exit_price = current_sl
                    exit_time = rr.Index
                    exit_reason = "BE_SL" if be_triggered and math.isclose(current_sl, entry_price_rounded, rel_tol=0.0, abs_tol=10 ** (-cfg.price_decimals)) else "SL"
                    exit_idx = j
                    break

                if tp_hit:
                    exit_price = tp_price
                    exit_time = rr.Index
                    exit_reason = "TP"
                    exit_idx = j
                    break

                close_pnl_usd = (
                    (rr.close - entry_price_rounded) * qty
                    if direction == "long"
                    else (entry_price_rounded - rr.close) * qty
                )
                close_pnl_pct = (
                    (rr.close - entry_price_rounded) / entry_price_rounded * 100
                    if direction == "long"
                    else (entry_price_rounded - rr.close) / entry_price_rounded * 100
                )

                if (
                    cfg.enable_breakeven
                    and not be_triggered
                    and cfg.be_profit_usd > 0
                    and close_pnl_usd >= cfg.be_profit_usd
                ):
                    current_sl = safe_trigger_price(entry_price_rounded, cfg.price_decimals)
                    be_triggered = True
                    be_trigger_time = rr.Index
                    be_updates += 1

                reason, detail, adverse_count = guard_reason(
                    cfg=cfg,
                    direction=direction,
                    candle_open_ms=int(rr.Index.timestamp() * 1000),
                    entry_ts_ms=entry_ts_ms,
                    o=rr.open,
                    c=rr.close,
                    pnl_pct=close_pnl_pct,
                    adverse_count=adverse_count,
                )
                if reason:
                    exit_price = rr.close
                    exit_time = rr.Index
                    exit_reason = reason
                    exit_detail = detail or ""
                    exit_idx = j
                    break

            gross = (
                (exit_price - entry_price_rounded) * qty
                if direction == "long"
                else (entry_price_rounded - exit_price) * qty
            )
            entry_fee = entry_price_rounded * qty * cfg.fee_pct
            exit_fee = exit_price * qty * cfg.fee_pct
            net = gross - entry_fee - exit_fee
            capital += net

            dist_to_level_pct = 0.0
            if level_price:
                if direction == "long":
                    dist_to_level_pct = (entry_price_rounded - level_price) / level_price * 100
                else:
                    dist_to_level_pct = (level_price - entry_price_rounded) / level_price * 100

            trades.append({
                "date": day,
                "direction": direction,
                "signal_level": signal_level,
                "entry_time": entry_time,
                "exit_time": exit_time,
                "entry_price": round(entry_price_rounded, cfg.price_decimals),
                "exit_price": round(exit_price, cfg.price_decimals),
                "initial_sl_price": round(initial_sl, cfg.price_decimals),
                "final_sl_price": round(current_sl, cfg.price_decimals),
                "tp_price": round(tp_price, cfg.price_decimals),
                "level_price": round(level_price, cfg.price_decimals) if level_price else None,
                "level_gap_pct": round(dist_to_level_pct, 4),
                "qty": qty,
                "gross_pnl": round(gross, 4),
                "fees": round(entry_fee + exit_fee, 4),
                "net_pnl": round(net, 4),
                "pnl_pct": round((net / capital_before) * 100 if capital_before else 0.0, 4),
                "raw_move_pct": round(
                    ((exit_price - entry_price_rounded) / entry_price_rounded * 100)
                    if direction == "long"
                    else ((entry_price_rounded - exit_price) / entry_price_rounded * 100),
                    4,
                ),
                "reason": exit_reason,
                "reason_detail": exit_detail,
                "capital_before": round(capital_before, 2),
                "capital_after": round(capital, 2),
                "hold_mins": int((exit_time - entry_time).total_seconds() / 60),
                "bars_held": bars_held,
                "mfe_pct": round(mfe_pct, 4),
                "mae_pct": round(mae_pct, 4),
                "be_triggered": be_triggered,
                "be_trigger_time": be_trigger_time,
                "be_updates": be_updates,
                "signal_engine": cfg.signal_engine,
            })
            equity_curve.append({"time": exit_time, "capital": capital})

            if not signal.traded_today:
                signal.reset_signal()
            i = exit_idx + 1

    return pd.DataFrame(trades), equity_curve


def analyze(tdf: pd.DataFrame, cfg: BacktestConfig, output_prefix: str) -> None:
    symbol = Path(cfg.csv_file).stem.replace("_klines", "")
    print("=" * 100)
    print(f"  PDHL DETAILED BACKTEST — {symbol}")
    print(
        f"  engine={cfg.signal_engine}  TP={cfg.tp_pct*100:.1f}%  SL={cfg.sl_pct*100:.1f}%  "
        f"confirm={cfg.confirm_bars}  prox={cfg.prox_pct*100:.2f}%  "
        f"pos={cfg.pos_size_pct*100:.0f}%  max_trades={cfg.max_trades_per_day}"
    )
    print(
        f"  protections: breakeven={'ON' if cfg.enable_breakeven else 'OFF'} "
        f"(+${cfg.be_profit_usd:.2f})  runtime_guards={'ON' if cfg.enable_runtime_guards else 'OFF'}"
    )
    print("  fee model: conservative OHLC replay with taker-like fee default; maker fills are not modeled")
    print("=" * 100)

    if tdf.empty:
        print("\n  No trades found.")
        return

    n = len(tdf)
    wins = tdf[tdf["net_pnl"] > 0]
    losses = tdf[tdf["net_pnl"] <= 0]
    be_hits = tdf[tdf["reason"] == "BE_SL"]
    exit_order = ["TP", "SL", "BE_SL", "TIME_STOP", "ADVERSE_MOMENTUM", "MAX_HOLD", "EOD"]

    final_capital = float(tdf["capital_after"].iloc[-1])
    peak = tdf["capital_after"].cummax()
    dd = (peak - tdf["capital_after"]) / peak * 100

    print(f"\n  Period:   {tdf['date'].iloc[0]} -> {tdf['date'].iloc[-1]}")
    print(f"  Trades:   {n}")
    print(f"  Initial:  ${cfg.initial_capital:.2f}")
    print(f"  Final:    ${final_capital:.2f}")
    print(f"  Return:   {(final_capital / cfg.initial_capital - 1) * 100:+.2f}%")

    print("\n  --- Exit Breakdown ---")
    for reason in exit_order:
        subset = tdf[tdf["reason"] == reason]
        if len(subset):
            print(f"  {reason:16s} {len(subset):4d}  ({len(subset)/n*100:5.1f}%)")

    print("\n  --- Win/Loss ---")
    print(f"  Winners:  {len(wins):4d}  ({len(wins)/n*100:5.1f}%)")
    print(f"  Losers:   {len(losses):4d}  ({len(losses)/n*100:5.1f}%)")
    if len(wins):
        print(f"  Avg win:  ${wins['net_pnl'].mean():.4f}  ({wins['pnl_pct'].mean():+.3f}%)")
    if len(losses):
        print(f"  Avg loss: ${losses['net_pnl'].mean():.4f}  ({losses['pnl_pct'].mean():+.3f}%)")
    print(f"  Avg P&L:  ${tdf['net_pnl'].mean():.4f}")
    print(f"  Median:   ${tdf['net_pnl'].median():.4f}")

    print("\n  --- Risk ---")
    print(f"  Max drawdown:      {dd.max():.2f}%")
    print(f"  Peak capital:      ${tdf['capital_after'].max():.2f}")
    print(f"  Min capital:       ${tdf['capital_after'].min():.2f}")
    streak = 0
    max_streak = 0
    for _, row in tdf.iterrows():
        streak = streak + 1 if row["net_pnl"] <= 0 else 0
        max_streak = max(max_streak, streak)
    print(f"  Max consec losses: {max_streak}")

    print("\n  --- Direction Breakdown ---")
    for direction in ["long", "short"]:
        subset = tdf[tdf["direction"] == direction]
        if len(subset) == 0:
            continue
        subset_wins = subset[subset["net_pnl"] > 0]
        print(
            f"  {direction.upper():5s}: trades={len(subset):4d}  "
            f"wins={len(subset_wins):4d} ({len(subset_wins)/len(subset)*100:5.1f}%)  "
            f"avg_pnl=${subset['net_pnl'].mean():.4f}"
        )

    print("\n  --- Signal Level Breakdown ---")
    for level in ["PDH", "PDL"]:
        subset = tdf[tdf["signal_level"] == level]
        if len(subset) == 0:
            continue
        print(
            f"  {level:3s}: trades={len(subset):4d}  "
            f"avg_gap={subset['level_gap_pct'].mean():+.3f}%  "
            f"avg_pnl=${subset['net_pnl'].mean():.4f}"
        )

    print("\n  --- Protection Summary ---")
    print(f"  Auto BE triggered: {int(tdf['be_triggered'].sum()):4d}  ({tdf['be_triggered'].mean()*100:5.1f}%)")
    if len(be_hits):
        print(f"  BE stop exits:     {len(be_hits):4d}  ({len(be_hits)/n*100:5.1f}%)")

    print("\n  --- Monthly Breakdown ---")
    month_series = pd.to_datetime(tdf["date"].astype(str)).dt.to_period("M")
    monthly = tdf.assign(month=month_series).groupby("month").agg(
        trades=("net_pnl", "count"),
        wins=("net_pnl", lambda x: (x > 0).sum()),
        total_pnl=("net_pnl", "sum"),
        end_capital=("capital_after", "last"),
    )
    monthly["win_rate"] = monthly["wins"] / monthly["trades"] * 100
    for idx, row in monthly.iterrows():
        print(
            f"  {idx}: trades={row['trades']:4.0f}  wins={row['wins']:4.0f} "
            f"({row['win_rate']:5.1f}%)  pnl=${row['total_pnl']:+8.2f}  capital=${row['end_capital']:.2f}"
        )

    print("\n  --- Hold Time (minutes) ---")
    for reason in exit_order:
        subset = tdf[tdf["reason"] == reason]
        if len(subset) == 0:
            continue
        print(
            f"  {reason:16s} mean={subset['hold_mins'].mean():6.1f}  "
            f"median={subset['hold_mins'].median():6.1f}  "
            f"max={subset['hold_mins'].max():6.0f}"
        )

    print("\n  --- Excursion ---")
    print(f"  Avg MFE: {tdf['mfe_pct'].mean():+.3f}%")
    print(f"  Avg MAE: {tdf['mae_pct'].mean():+.3f}%")
    print(f"  Best trade:  ${tdf['net_pnl'].max():+.2f}")
    print(f"  Worst trade: ${tdf['net_pnl'].min():+.2f}")

    trades_path = f"{output_prefix}_trades.csv"
    tdf.to_csv(trades_path, index=False)
    print(f"\n  Trade log -> {trades_path}")


def plot_equity(tdf: pd.DataFrame, equity: list[dict], cfg: BacktestConfig, output_prefix: str) -> None:
    if tdf.empty:
        return

    edf = pd.DataFrame(equity)
    edf["time"] = pd.to_datetime(edf["time"])
    peak = edf["capital"].cummax()
    dd_pct = (peak - edf["capital"]) / peak * 100
    colors = ["#26a69a" if p > 0 else "#ef5350" for p in tdf["net_pnl"]]

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.5, 0.25, 0.25],
        subplot_titles=("Equity Curve", "Trade P&L", "Drawdown %"),
    )

    fig.add_trace(
        go.Scatter(
            x=edf["time"],
            y=edf["capital"],
            name="Equity",
            line=dict(color="#26a69a", width=2),
            fill="tozeroy",
            fillcolor="rgba(38,166,154,0.1)",
        ),
        row=1,
        col=1,
    )
    fig.add_hline(y=cfg.initial_capital, line_dash="dash", line_color="gray", row=1, col=1)

    fig.add_trace(
        go.Bar(
            x=pd.to_datetime(tdf["entry_time"]),
            y=tdf["net_pnl"],
            name="Trade P&L",
            marker_color=colors,
            text=tdf["direction"].str.upper(),
            textposition="outside",
        ),
        row=2,
        col=1,
    )

    fig.add_trace(
        go.Scatter(
            x=edf["time"],
            y=-dd_pct,
            name="Drawdown",
            line=dict(color="#ef5350", width=1),
            fill="tozeroy",
            fillcolor="rgba(239,83,80,0.2)",
        ),
        row=3,
        col=1,
    )

    fig.update_layout(
        title=(
            f"PDHL detailed backtest | engine={cfg.signal_engine} | "
            f"TP={cfg.tp_pct*100:.1f}% SL={cfg.sl_pct*100:.1f}% "
            f"confirm={cfg.confirm_bars} prox={cfg.prox_pct*100:.2f}%"
        ),
        template="plotly_dark",
        height=900,
        showlegend=False,
    )
    fig.update_yaxes(title_text="Capital ($)", row=1, col=1)
    fig.update_yaxes(title_text="P&L ($)", row=2, col=1)
    fig.update_yaxes(title_text="DD %", row=3, col=1)

    html_path = f"{output_prefix}_analysis.html"
    fig.write_html(html_path)
    print(f"  Chart -> {html_path}")


def main() -> None:
    cfg = parse_args()
    output_prefix = build_output_prefix(cfg)

    print(f"Loading {cfg.csv_file}...")
    df = load(cfg.csv_file)
    print(f"Loaded {len(df):,} candles  ({df.index[0].date()} -> {df.index[-1].date()})\n")

    tdf, equity = run_backtest(df, cfg)
    analyze(tdf, cfg, output_prefix)
    plot_equity(tdf, equity, cfg, output_prefix)


if __name__ == "__main__":
    main()
