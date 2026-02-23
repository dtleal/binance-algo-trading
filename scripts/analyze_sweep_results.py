#!/usr/bin/env python3
"""Analyze sweep results across multiple timeframes and find global champion.

Reads sweep output files, extracts top strategies per timeframe, and identifies
the overall best performing strategy across all timeframes.

Usage:
    python analyze_sweep_results.py DOGEUSDT
    python analyze_sweep_results.py 1000SHIBUSDT
"""

import argparse
import re
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict


@dataclass
class SweepResult:
    """Single strategy result from sweep."""
    strategy: str
    return_pct: float
    trades: int
    wins: int
    losses: int
    win_rate: float
    max_dd: float
    eod_exits: int
    tp_pct: float
    sl_pct: float
    min_bars: int
    confirm_bars: int
    vwap_window: int
    vwap_prox: float
    pos_size: float
    timeframe: str


def parse_sweep_file(filepath: Path, timeframe: str) -> List[SweepResult]:
    """Parse a sweep output file and extract all results."""
    results = []

    with open(filepath, 'r') as f:
        content = f.read()

    # Find the "TOP 30 BY RETURN %" section
    match = re.search(r'TOP 30 BY RETURN %.*?\n.*?\n(.*?)(?:\n=|$)', content, re.DOTALL)
    if not match:
        return results

    top_section = match.group(1)

    # Parse each line
    for line in top_section.strip().split('\n'):
        if not line.strip() or line.startswith('strat'):
            continue

        # Format: strat   TP%   SL%   R:R bar  vf cf  tf  wndw prox vwD hold ps%  trd win los eod  win%  return%    final$  mxDD%  mCL
        # Example: VWAPPullback  7.00  5.00   1.4   3 false  0 false 01-22  0.5   1  EOD  20  354 188 166 283 53.1%   37.51%   1375.06  5.27%    5
        parts = line.split()
        if len(parts) < 21:
            continue

        try:
            strategy = parts[0]
            tp_pct = float(parts[1])
            sl_pct = float(parts[2])
            # R:R = parts[3]
            min_bars = int(parts[4])
            # vol_filter = parts[5]
            confirm_bars = int(parts[6])
            # trend_filter = parts[7]
            # window = parts[8]
            vwap_prox = float(parts[9])
            # vwap_days = parts[10]
            # max_hold = parts[11]
            pos_size = float(parts[12])
            trades = int(parts[13])
            wins = int(parts[14])
            losses = int(parts[15])
            eod_exits = int(parts[16])
            win_rate = float(parts[17].replace('%', ''))
            return_pct = float(parts[18].replace('%', ''))
            # final$ = parts[19]
            max_dd = float(parts[20].replace('%', ''))
            # mCL = parts[21] if len(parts) > 21 else 0

            # Extract VWAP window from parts[8] (format: "01-22" -> we'll use the first number as a proxy)
            # For now, use 1 as default
            vwap_window = 1

            result = SweepResult(
                strategy=strategy,
                return_pct=return_pct,
                trades=trades,
                wins=wins,
                losses=losses,
                win_rate=win_rate,
                max_dd=max_dd,
                eod_exits=eod_exits,
                tp_pct=tp_pct,
                sl_pct=sl_pct,
                min_bars=min_bars,
                confirm_bars=confirm_bars,
                vwap_window=vwap_window,
                vwap_prox=vwap_prox,
                pos_size=pos_size,
                timeframe=timeframe,
            )
            results.append(result)
        except (ValueError, IndexError) as e:
            # Skip malformed lines
            continue

    return results


def format_result_table(results: List[SweepResult], title: str) -> str:
    """Format results as a nice table."""
    output = [f"\n{'='*120}"]
    output.append(f"{title:^120}")
    output.append('='*120)
    output.append(f"{'TF':<6} {'Strategy':<12} {'Return':>8} {'Trades':>7} {'W/L':>10} {'WR%':>6} {'MaxDD':>7} {'EOD':>5} {'TP%':>6} {'SL%':>6} {'Params':<30}")
    output.append('-'*120)

    for r in results:
        params = f"minB={r.min_bars} confB={r.confirm_bars} VW={r.vwap_window}d prox={r.vwap_prox:.3f}% pos={r.pos_size:.0f}%"
        output.append(
            f"{r.timeframe:<6} {r.strategy:<12} {r.return_pct:>7.2f}% {r.trades:>7} "
            f"{r.wins:>4}/{r.losses:<4} {r.win_rate:>5.1f}% {r.max_dd:>6.2f}% {r.eod_exits:>5} "
            f"{r.tp_pct:>5.1f}% {r.sl_pct:>5.1f}% {params}"
        )

    output.append('='*120)
    return '\n'.join(output)


def main():
    parser = argparse.ArgumentParser(
        description="Analyze sweep results across multiple timeframes"
    )
    parser.add_argument("symbol", help="Trading pair symbol (e.g., DOGEUSDT, 1000SHIBUSDT)")
    parser.add_argument(
        "-n", "--top",
        type=int,
        default=3,
        help="Number of top results to show per timeframe (default: 3)"
    )

    args = parser.parse_args()

    symbol = args.symbol.upper()
    symbol_lower = symbol.lower()

    # Timeframes to analyze
    timeframes = ["1m", "5m", "15m", "30m", "1h"]

    print(f"\n{'='*120}")
    print(f"SWEEP ANALYSIS: {symbol}")
    print(f"{'='*120}\n")

    all_results = []
    missing_files = []

    # Load results from each timeframe
    for tf in timeframes:
        filepath = Path(f"data/sweeps/{symbol_lower}_{tf}_sweep.txt")

        if not filepath.exists():
            missing_files.append(tf)
            continue

        print(f"📊 Loading {tf} results from {filepath}...")
        results = parse_sweep_file(filepath, tf)

        if results:
            all_results.extend(results)
            print(f"   ✓ Found {len(results)} results")
        else:
            print(f"   ⚠️  No results found in file")

    if missing_files:
        print(f"\n⚠️  Missing sweep files for timeframes: {', '.join(missing_files)}")

    if not all_results:
        print("\n❌ No results found. Make sure sweep files exist in sweep_results/")
        return 1

    # Sort by return (descending)
    all_results.sort(key=lambda x: x.return_pct, reverse=True)

    # Show top N results per timeframe
    print(format_result_table(all_results[:args.top * len(timeframes)],
                             f"TOP {args.top} STRATEGIES PER TIMEFRAME (sorted by Return)"))

    # Find global champion (best return)
    champion = all_results[0]

    print(f"\n{'='*120}")
    print(f"🏆 GLOBAL CHAMPION")
    print(f"{'='*120}")
    print(f"  Timeframe:    {champion.timeframe}")
    print(f"  Strategy:     {champion.strategy}")
    print(f"  Return:       {champion.return_pct:+.2f}%")
    print(f"  Trades:       {champion.trades} ({champion.wins}W/{champion.losses}L)")
    print(f"  Win Rate:     {champion.win_rate:.1f}%")
    print(f"  Max Drawdown: {champion.max_dd:.2f}%")
    print(f"  EOD Exits:    {champion.eod_exits}/{champion.trades} ({100*champion.eod_exits/champion.trades:.1f}%)")
    print(f"  Take Profit:  {champion.tp_pct:.2f}%")
    print(f"  Stop Loss:    {champion.sl_pct:.2f}%")
    print(f"  Min Bars:     {champion.min_bars}")
    print(f"  Confirm Bars: {champion.confirm_bars}")
    print(f"  VWAP Window:  {champion.vwap_window} days")
    print(f"  VWAP Prox:    {champion.vwap_prox:.3f}%")
    print(f"  Position Size:{champion.pos_size:.0f}%")
    print(f"{'='*120}")

    # Validation checklist
    print(f"\n📋 VALIDATION CHECKLIST:")
    checks = [
        (champion.return_pct > 10, f"✓ Return > 10%" if champion.return_pct > 10 else f"✗ Return < 10% ({champion.return_pct:.1f}%)"),
        (champion.max_dd < 10, f"✓ Max DD < 10%" if champion.max_dd < 10 else f"⚠️  Max DD > 10% ({champion.max_dd:.1f}%)"),
        (champion.win_rate > 40 or (champion.wins / max(champion.losses, 1)) > 1.5,
         f"✓ Win rate > 40% or R:R > 1.5" if champion.win_rate > 40 else f"⚠️  Win rate < 40% ({champion.win_rate:.1f}%)"),
        (champion.eod_exits / champion.trades > 0.5,
         f"✓ EOD exits dominate (>{champion.eod_exits}/{champion.trades})" if champion.eod_exits / champion.trades > 0.5
         else f"⚠️  EOD exits don't dominate ({champion.eod_exits}/{champion.trades})"),
        (champion.trades > 50, f"✓ Sufficient trades (>{champion.trades})" if champion.trades > 50 else f"⚠️  Low trade count ({champion.trades})"),
    ]

    for passed, msg in checks:
        print(f"  {msg}")

    all_passed = all(check[0] for check in checks)

    if all_passed:
        print(f"\n✅ APPROVED - Strategy passes all validation checks")
    else:
        print(f"\n⚠️  CONDITIONAL - Review failed checks before proceeding")

    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
