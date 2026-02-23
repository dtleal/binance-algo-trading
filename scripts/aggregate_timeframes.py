"""Aggregate 1-minute klines to higher timeframes (5m, 15m, 30m, 60m)."""

import pandas as pd
import sys
from pathlib import Path

TIMEFRAMES = {
    '5m': 5,
    '15m': 15,
    '30m': 30,
    '60m': 60,
}

HEADERS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades", "taker_buy_base_vol",
    "taker_buy_quote_vol",
]


def aggregate_to_timeframe(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate 1-minute candles to N-minute candles."""

    # Convert open_time to datetime for proper grouping
    df['datetime'] = pd.to_datetime(df['open_time'], unit='ms')

    # Group by N-minute intervals
    # Use floor division to create time buckets
    df['bucket'] = df['datetime'].dt.floor(f'{minutes}min')

    # Aggregate OHLCV data
    agg_dict = {
        'open_time': 'first',      # First timestamp in bucket
        'open': 'first',           # First open
        'high': 'max',             # Highest high
        'low': 'min',              # Lowest low
        'close': 'last',           # Last close
        'volume': 'sum',           # Sum of volumes
        'close_time': 'last',      # Last close time
        'quote_volume': 'sum',     # Sum of quote volumes
        'trades': 'sum',           # Sum of trades
        'taker_buy_base_vol': 'sum',   # Sum
        'taker_buy_quote_vol': 'sum',  # Sum
    }

    aggregated = df.groupby('bucket').agg(agg_dict).reset_index(drop=True)

    return aggregated[HEADERS]


def main():
    if len(sys.argv) < 2:
        print("Usage: python aggregate_timeframes.py <symbol>_1m_klines.csv")
        print("Example: python aggregate_timeframes.py btcusdt_1m_klines.csv")
        sys.exit(1)

    input_file = sys.argv[1]

    if not Path(input_file).exists():
        print(f"Error: File not found: {input_file}")
        sys.exit(1)

    # Extract symbol from filename
    symbol = input_file.replace('_1m_klines.csv', '')

    print(f"📥 Loading {input_file}...")
    df = pd.read_csv(input_file)
    print(f"   Loaded {len(df):,} 1-minute candles")

    # Aggregate to each timeframe
    for tf_name, minutes in TIMEFRAMES.items():
        print(f"\n🔄 Aggregating to {tf_name} ({minutes} minutes)...")

        aggregated = aggregate_to_timeframe(df.copy(), minutes)

        output_file = f"{symbol}_{tf_name}_klines.csv"
        aggregated.to_csv(output_file, index=False)

        print(f"   ✅ Created {len(aggregated):,} {tf_name} candles → {output_file}")
        print(f"      Compression ratio: {len(df)/len(aggregated):.1f}x")

    print(f"\n✨ Done! Created {len(TIMEFRAMES)} timeframe files.")


if __name__ == "__main__":
    main()
