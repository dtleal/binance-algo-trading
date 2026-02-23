#!/usr/bin/env python3
"""Aggregate 1-minute klines to higher timeframes (5m, 15m, 30m, 1h).

Reads a 1m klines CSV and generates aggregated CSVs for multiple timeframes.
This avoids downloading data multiple times from Binance API.

Usage:
    python aggregate_klines.py dogeusdt_1m_klines.csv
    python aggregate_klines.py dogeusdt_1m_klines.csv -t 5m 15m 30m
"""

import argparse
import csv
import sys
from pathlib import Path
from collections import defaultdict


HEADERS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades", "taker_buy_base_vol",
    "taker_buy_quote_vol",
]

TIMEFRAME_MINUTES = {
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
}


def aggregate_candles(candles: list[dict], interval_minutes: int) -> list[dict]:
    """Aggregate 1m candles into larger timeframes."""
    if not candles:
        return []

    aggregated = []
    current_group = []
    group_start_time = None

    for candle in candles:
        open_time = int(candle["open_time"])

        # Calculate which group this candle belongs to
        # Round down to nearest interval boundary
        group_boundary = (open_time // (interval_minutes * 60_000)) * (interval_minutes * 60_000)

        if group_start_time is None:
            group_start_time = group_boundary

        # Start new group if we've crossed a boundary
        if group_boundary != group_start_time:
            if current_group:
                aggregated.append(aggregate_group(current_group))
            current_group = [candle]
            group_start_time = group_boundary
        else:
            current_group.append(candle)

    # Don't forget the last group
    if current_group:
        aggregated.append(aggregate_group(current_group))

    return aggregated


def aggregate_group(candles: list[dict]) -> dict:
    """Aggregate a group of 1m candles into a single candle."""
    if not candles:
        raise ValueError("Cannot aggregate empty group")

    return {
        "open_time": candles[0]["open_time"],
        "open": candles[0]["open"],
        "high": str(max(float(c["high"]) for c in candles)),
        "low": str(min(float(c["low"]) for c in candles)),
        "close": candles[-1]["close"],
        "volume": str(sum(float(c["volume"]) for c in candles)),
        "close_time": candles[-1]["close_time"],
        "quote_volume": str(sum(float(c["quote_volume"]) for c in candles)),
        "trades": str(sum(int(c["trades"]) for c in candles)),
        "taker_buy_base_vol": str(sum(float(c["taker_buy_base_vol"]) for c in candles)),
        "taker_buy_quote_vol": str(sum(float(c["taker_buy_quote_vol"]) for c in candles)),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Aggregate 1m klines to higher timeframes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python aggregate_klines.py dogeusdt_1m_klines.csv
  python aggregate_klines.py dogeusdt_1m_klines.csv -t 5m 15m 30m
  python aggregate_klines.py 1000shibusdt_1m_klines.csv -t 5m 15m 30m 1h
        """
    )

    parser.add_argument("input_file", help="Input CSV file with 1m klines")
    parser.add_argument(
        "-t", "--timeframes",
        nargs="+",
        choices=list(TIMEFRAME_MINUTES.keys()),
        default=["5m", "15m", "30m", "1h"],
        help="Timeframes to generate (default: 5m 15m 30m 1h)"
    )

    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f"❌ Error: {args.input_file} not found")
        return 1

    # Extract base name (e.g., "dogeusdt" from "dogeusdt_1m_klines.csv")
    base_name = input_path.stem.replace("_1m_klines", "")

    print(f"📊 Reading 1m candles from {args.input_file}...")

    # Read all 1m candles
    candles = []
    with open(input_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            candles.append(row)

    print(f"   Loaded {len(candles):,} 1-minute candles")

    # Aggregate to each timeframe
    for timeframe in args.timeframes:
        interval_minutes = TIMEFRAME_MINUTES[timeframe]
        output_file = str(input_path.parent / f"{base_name}_{timeframe}_klines.csv")

        print(f"\n🔄 Aggregating to {timeframe}...")
        aggregated = aggregate_candles(candles, interval_minutes)

        # Write output
        with open(output_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=HEADERS)
            writer.writeheader()
            writer.writerows(aggregated)

        print(f"   ✓ {len(aggregated):,} candles written to {output_file}")

    print(f"\n✅ Done! Generated {len(args.timeframes)} timeframe files")

    return 0


if __name__ == "__main__":
    sys.exit(main())
