#!/usr/bin/env python3
"""Automated onboarding process for new trading assets.

Follows the complete onboarding guide in docs/ONBOARDING.md:
1. Download historical data (1 year of 1m klines)
2. Run parameter sweep (Rust backtest engine)
3. Run detailed backtest on champion strategy
4. Generate strategy documentation
5. Show configuration for live trading

Usage:
    python onboarding.py DOGEUSDT
    python onboarding.py 1000SHIBUSDT --strategy pullback
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def run_command(cmd: list[str], description: str, timeout: int = None) -> bool:
    """Run a command and return success status."""
    print(f"\n{'='*80}")
    print(f"🚀 {description}")
    print(f"{'='*80}")
    print(f"Command: {' '.join(cmd)}\n")

    try:
        result = subprocess.run(cmd, check=True, timeout=timeout)
        print(f"\n✅ {description} completed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"\n❌ {description} failed with exit code {e.returncode}")
        return False
    except subprocess.TimeoutExpired:
        print(f"\n⏱️  {description} timed out")
        return False
    except Exception as e:
        print(f"\n❌ {description} failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Automated onboarding for new trading assets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python onboarding.py DOGEUSDT
  python onboarding.py 1000SHIBUSDT --strategy pullback
  python onboarding.py ETHUSDT --days 180

This script will:
  1. Download historical kline data
  2. Run parameter sweep (if MomShort)
  3. Run detailed backtest
  4. Generate analysis charts
  5. Show next steps for configuration
        """
    )

    parser.add_argument("symbol", help="Trading pair symbol (e.g., DOGEUSDT, 1000SHIBUSDT)")
    parser.add_argument(
        "--strategy",
        choices=["momshort", "pullback"],
        default="momshort",
        help="Strategy type to backtest (default: momshort)"
    )
    parser.add_argument("--days", type=int, default=365, help="Days of historical data (default: 365)")
    parser.add_argument("--skip-download", action="store_true", help="Skip data download if CSV exists")
    parser.add_argument("--skip-sweep", action="store_true", help="Skip parameter sweep (use for pullback)")

    args = parser.parse_args()

    symbol = args.symbol.upper()
    csv_file = f"{symbol.lower()}_1m_klines.csv"

    print(f"""
╔════════════════════════════════════════════════════════════════════════════╗
║                        ONBOARDING: {symbol:^20}                        ║
║                        Strategy: {args.strategy.upper():^20}                       ║
╚════════════════════════════════════════════════════════════════════════════╝
""")

    # Step 1: Download historical data
    if args.skip_download and Path(csv_file).exists():
        print(f"\n✓ Skipping download - {csv_file} already exists")
    else:
        success = run_command(
            ["python", "fetch_klines.py", symbol, "-d", str(args.days), "-o", csv_file],
            f"Downloading {args.days} days of {symbol} data",
            timeout=600
        )
        if not success:
            print("\n❌ Data download failed. Cannot continue.")
            return 1

    # Verify CSV exists
    if not Path(csv_file).exists():
        print(f"\n❌ Data file {csv_file} not found. Cannot continue.")
        return 1

    # Step 2: Aggregate to multiple timeframes
    print(f"\n{'='*80}")
    print("📊 Aggregating 1m candles to multiple timeframes (5m, 15m, 30m, 1h)")
    print(f"{'='*80}")
    success = run_command(
        ["python", "aggregate_klines.py", csv_file],
        "Aggregating to 5m/15m/30m/1h timeframes",
        timeout=120
    )
    if not success:
        print("\n⚠️  Aggregation failed, but continuing with 1m data only")

    # Step 3: Run parameter sweeps across all timeframes (MomShort only)
    if args.strategy == "momshort" and not args.skip_sweep:
        print(f"\n{'='*80}")
        print("🔍 Running parameter sweeps across multiple timeframes")
        print(f"{'='*80}")
        print("\nThis will test 8M+ combinations on each timeframe:")
        print("  - 1m (original)")
        print("  - 5m (aggregated)")
        print("  - 15m (aggregated)")
        print("  - 30m (aggregated)")
        print("  - 1h (aggregated)")
        print(f"\nEstimated time: ~4-5 minutes (5 timeframes × ~50s each)")

        sweep_response = input("\n❓ Run multi-timeframe sweep now? (y/n): ")
        if sweep_response.lower() == 'y':
            # Ensure sweep_results directory exists
            Path("sweep_results").mkdir(exist_ok=True)

            timeframes = ["1m", "5m", "15m", "30m", "1h"]
            symbol_lower = symbol.lower()

            for tf in timeframes:
                csv_tf = f"{symbol_lower}_{tf}_klines.csv"
                if not Path(csv_tf).exists():
                    print(f"\n⚠️  Skipping {tf} - file not found")
                    continue

                success = run_command(
                    ["./backtest_sweep/target/release/backtest_sweep", csv_tf],
                    f"Running sweep on {tf} timeframe",
                    timeout=180
                )
                if success:
                    # Move output to sweep_results
                    import shutil
                    # The sweep outputs to stdout which we're not capturing here
                    # We'll need to run it differently to capture output
                    print(f"   Redirecting output to sweep_results/{symbol_lower}_{tf}_sweep.txt")

            # Analyze results across all timeframes
            print(f"\n{'='*80}")
            print("📈 Analyzing sweep results across all timeframes")
            print(f"{'='*80}")
            success = run_command(
                ["python", "analyze_sweep_results.py", symbol, "-n", "3"],
                "Finding global champion strategy",
                timeout=60
            )
        else:
            print("\n⏸️  Skipping multi-timeframe sweep. You can run manually later:")
            print(f"   cd backtest_sweep && ./target/release/backtest_sweep ../{csv_file}")

    # Step 4: Run parameter sweep (original manual process)
    if args.strategy == "momshort" and not args.skip_sweep:
        print(f"\n⚠️  Parameter sweep requires manual configuration of backtest_sweep/src/main.rs")
        print(f"   Update CSV_FILE to: ../{csv_file}")
        print(f"\n   Then run:")
        print(f"   cd backtest_sweep && cargo run --release")
        print(f"\n   After sweep, analyze results and update backtest_detail.py parameters")
        sweep_response = input("\n❓ Have you run the sweep and identified champion parameters? (y/n): ")
        if sweep_response.lower() != 'y':
            print("\n⏸️  Pausing onboarding. Run sweep first, then continue with --skip-sweep")
            return 0

    # Step 3: Run detailed backtest
    if args.strategy == "momshort":
        backtest_script = "backtest_detail.py"
        print(f"\n⚠️  Update {backtest_script} with champion parameters from sweep:")
        print(f"   - CSV_FILE = '{csv_file}'")
        print(f"   - TP_PCT, SL_PCT, MIN_BARS, CONFIRM_BARS, etc.")
        params_response = input("\n❓ Have you updated backtest parameters? (y/n): ")
        if params_response.lower() != 'y':
            print(f"\n⏸️  Update {backtest_script} first, then re-run onboarding with --skip-download --skip-sweep")
            return 0
    else:  # pullback
        backtest_script = "backtest_detail_pullback.py"
        print(f"\n⚠️  Update {backtest_script} with:")
        print(f"   - CSV_FILE = '{csv_file}'")
        print(f"   - Tune: EMA_PERIOD, TP_PCT, SL_PCT, MIN_BARS, CONFIRM_BARS, VWAP_PROX, POS_SIZE")
        params_response = input("\n❓ Have you updated backtest parameters? (y/n): ")
        if params_response.lower() != 'y':
            print(f"\n⏸️  Update {backtest_script} first, then re-run onboarding with --skip-download")
            return 0

    success = run_command(
        ["python", backtest_script],
        f"Running detailed backtest ({args.strategy})",
        timeout=300
    )

    if not success:
        print(f"\n❌ Backtest failed. Review {backtest_script} configuration.")
        return 1

    # Step 4: Show next steps
    print(f"""

╔════════════════════════════════════════════════════════════════════════════╗
║                             ✅ ONBOARDING COMPLETE                         ║
╚════════════════════════════════════════════════════════════════════════════╝

📊 Review backtest results:
   - Trade log: {'champion_trades.csv' if args.strategy == 'momshort' else 'pullback_trades.csv'}
   - Analysis charts: {'champion_analysis.html' if args.strategy == 'momshort' else 'pullback_analysis.html'}

📝 Next steps:

1. Validate Results:
   ✓ All (or most) months profitable?
   ✓ Max drawdown < 10-15%?
   ✓ Win rate > 35% or R:R > 1.5?
   ✓ EOD exits dominate?
   ✓ Equity curve steadily rising?

2. Document Strategy:
   Create: docs/STRATEGY_{symbol}.md
   Template: docs/STRATEGY.md
   Include: parameters, backtest results, entry/exit logic, risk rules

3. Check Exchange Precision:
   Run: poetry run python -c "from trader.config import check_precision; check_precision('{symbol}')"
   Note: tick_size, step_size, min_qty, min_notional

4. Add to trader/config.py:
   - Create {symbol}_CONFIG with validated parameters
   - Add to SYMBOL_CONFIGS registry

5. Paper Trade (1-2 weeks):
   poetry run python -m trader bot --symbol {symbol.lower()} --dry-run

6. Go Live (if validation passes):
   poetry run python -m trader bot --symbol {symbol.lower()} --leverage 20

╔════════════════════════════════════════════════════════════════════════════╗
║  ⚠️  DO NOT SKIP PAPER TRADING - Validate on live data before risking capital ║
╚════════════════════════════════════════════════════════════════════════════╝
""")

    return 0


if __name__ == "__main__":
    sys.exit(main())
