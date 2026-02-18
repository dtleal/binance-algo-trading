#!/usr/bin/env python3
"""
Analyze Rust sweep results for any strategy.

Usage:
    python analyze_sweep.py                              # Show top 5 from ALL strategies
    python analyze_sweep.py --strategy VWAPPullback      # Show top 5 VWAPPullback
    python analyze_sweep.py --strategy MomShort --top 10 # Show top 10 MomShort
    python analyze_sweep.py --run-best                   # Auto-run detailed backtest (VWAPPullback only)
"""

import pandas as pd
import subprocess
import sys
import argparse
from pathlib import Path

def show_config(row, strategy):
    """Display config parameters based on strategy type."""
    print(f"    TP={row['tp_pct']:.1f}%  SL={row['sl_pct']:.1f}%  ", end="")

    # Strategy-specific parameters
    if strategy == "VWAPPullback":
        # Handle EMA (can be int or string like "-")
        try:
            ema = int(float(row['ema_period'])) if pd.notna(row['ema_period']) and str(row['ema_period']) != '-' else 'N/A'
        except (ValueError, TypeError):
            ema = 'N/A'

        # Handle max_trades_per_day (can be int or string like "-")
        try:
            max_t = int(float(row['max_trades_per_day'])) if pd.notna(row['max_trades_per_day']) and str(row['max_trades_per_day']) != '-' else 'N/A'
        except (ValueError, TypeError):
            max_t = 'N/A'

        print(f"EMA={ema}  max_trades={max_t}")
    else:
        # Other strategies don't use EMA/max_trades
        vol_f = "Yes" if row['vol_filter'] else "No"
        trend_f = "Yes" if row['trend_filter'] else "No"
        print(f"vol_filter={vol_f}  trend_filter={trend_f}")

    print(f"    bars={int(row['min_bars'])}  cfm={int(row['confirm_bars'])}  ", end="")
    print(f"prox={row['vwap_prox']:.2f}%  pos_size={row['pos_size_pct']:.0f}%")
    print(f"    window={row['entry_window']}  vwap_win={row['vwap_window']}  max_hold={row['max_hold']}")

def main():
    parser = argparse.ArgumentParser(description="Analyze Rust sweep for any strategy")
    parser.add_argument("--strategy", default=None, help="Strategy to analyze (default: all)")
    parser.add_argument("--top", type=int, default=5, help="Show top N configs")
    parser.add_argument("--metric", default="return_pct", choices=["return_pct", "win_rate", "max_dd_pct"],
                       help="Metric to sort by")
    parser.add_argument("--run-best", action="store_true", help="Auto-run detailed backtest (VWAPPullback only)")
    args = parser.parse_args()

    csv_file = Path("backtest_sweep.csv")
    if not csv_file.exists():
        print("❌ backtest_sweep.csv not found. Run 'make sweep-rust-axs' first.")
        sys.exit(1)

    print(f"📊 Loading {csv_file}...")
    df = pd.read_csv(csv_file, low_memory=False)

    # Show available strategies
    available = df['strategy'].unique().tolist()
    print(f"   Available strategies: {', '.join(available)}\n")

    # Filter by strategy if specified
    if args.strategy:
        strategy_df = df[df["strategy"] == args.strategy].copy()
        if strategy_df.empty:
            print(f"❌ No results found for strategy: {args.strategy}")
            sys.exit(1)
        strategy_name = args.strategy
    else:
        strategy_df = df.copy()
        strategy_name = "ALL STRATEGIES"

    # Filter valid results (min trades)
    strategy_df = strategy_df[strategy_df["trades"] >= 50]

    if strategy_df.empty:
        print(f"❌ No configs with >= 50 trades")
        sys.exit(1)

    # Sort by metric
    ascending = args.metric == "max_dd_pct"  # Lower DD is better
    top_configs = strategy_df.nlargest(args.top, args.metric) if not ascending else strategy_df.nsmallest(args.top, args.metric)

    print(f"{'='*100}")
    print(f"  TOP {args.top} {strategy_name} BY {args.metric.upper()}")
    print(f"{'='*100}\n")

    for i, (idx, row) in enumerate(top_configs.iterrows(), 1):
        strat_label = f"[{row['strategy']}]" if not args.strategy else ""
        print(f"#{i}  {strat_label}  Return: {row['return_pct']:+.2f}%  |  Trades: {int(row['trades'])}  |  Win: {row['win_rate']:.1f}%  |  DD: {row['max_dd_pct']:.2f}%")
        show_config(row, row['strategy'])
        print()

    # Auto-run best config (VWAPPullback only)
    if args.run_best:
        best = top_configs.iloc[0]

        if best['strategy'] != 'VWAPPullback':
            print(f"❌ --run-best only supports VWAPPullback (best config is {best['strategy']})")
            print(f"   Use 'make analyze-best' or specify '--strategy VWAPPullback'")
            sys.exit(1)

        # Parse vwap_window (can be "10d" or 10)
        vwap_window_str = str(best['vwap_window']).rstrip('d')
        vwap_window = int(vwap_window_str)

        print(f"\n🚀 Running detailed backtest with best VWAPPullback config...")
        print(f"   Parameters: TP={best['tp_pct']:.1f}% SL={best['sl_pct']:.1f}% EMA={int(best['ema_period'])} VWAP={vwap_window}d")

        # Create a temporary detailed backtest script with these params
        script_content = f"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from backtest_detail_pullback import load, run_backtest, analyze, plot_equity

# Best config from Rust sweep
CSV_FILE = "axsusdt_1m_klines.csv"
EMA_PERIOD = {int(best['ema_period'])}
TP_PCT = {best['tp_pct'] / 100}
SL_PCT = {best['sl_pct'] / 100}
MIN_BARS = {int(best['min_bars'])}
CONFIRM_BARS = {int(best['confirm_bars'])}
VWAP_PROX = {best['vwap_prox'] / 100}
VWAP_WINDOW_DAYS = {vwap_window}
MAX_TRADES_PER_DAY = {int(best['max_trades_per_day'])}
POS_SIZE = {best['pos_size_pct'] / 100}
ENTRY_START = 60
ENTRY_CUTOFF = 1320
END_OF_DAY = 1430
FEE_PCT = 0.0004
INITIAL_CAP = 1000.0

# Monkey-patch the module
import backtest_detail_pullback as module
module.CSV_FILE = CSV_FILE
module.EMA_PERIOD = EMA_PERIOD
module.TP_PCT = TP_PCT
module.SL_PCT = SL_PCT
module.MIN_BARS = MIN_BARS
module.CONFIRM_BARS = CONFIRM_BARS
module.VWAP_PROX = VWAP_PROX
module.VWAP_WINDOW_DAYS = VWAP_WINDOW_DAYS
module.MAX_TRADES_PER_DAY = MAX_TRADES_PER_DAY
module.POS_SIZE = POS_SIZE
module.ENTRY_START = ENTRY_START
module.ENTRY_CUTOFF = ENTRY_CUTOFF
module.END_OF_DAY = END_OF_DAY
module.FEE_PCT = FEE_PCT
module.INITIAL_CAP = INITIAL_CAP

# Run it
if __name__ == "__main__":
    module.main()
"""

        temp_script = Path("_temp_backtest.py")
        temp_script.write_text(script_content)

        try:
            result = subprocess.run(["poetry", "run", "python", str(temp_script)],
                                  capture_output=False, text=True)
            if result.returncode == 0:
                print("\n✅ Detailed backtest completed!")
                print("   Files generated:")
                print("   - pullback_trades.csv")
                print("   - pullback_analysis.html")
        finally:
            temp_script.unlink(missing_ok=True)

if __name__ == "__main__":
    main()
