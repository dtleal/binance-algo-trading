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

    # Step 2: Run parameter sweep (MomShort only)
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
