"""Fetch 1-minute klines from Binance public API and save as CSV."""

import argparse
import csv
import time
from datetime import datetime, timezone
from urllib.request import urlopen, Request
import json

INTERVAL = "1m"
LIMIT = 1000  # max per request
BASE_URL = "https://api.binance.com/api/v3/klines"

HEADERS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades", "taker_buy_base_vol",
    "taker_buy_quote_vol",
]


def fetch_klines(symbol: str, interval: str, start: int, end: int, limit: int = LIMIT) -> list:
    url = f"{BASE_URL}?symbol={symbol}&interval={interval}&startTime={start}&endTime={end}&limit={limit}"
    req = Request(url, headers={"User-Agent": "Python"})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main():
    parser = argparse.ArgumentParser(description="Download historical kline data from Binance")
    parser.add_argument("symbol", help="Trading pair symbol (e.g., DOGEUSDT, 1000SHIBUSDT)")
    parser.add_argument("-o", "--output", help="Output CSV file (default: <symbol_lower>_1m_klines.csv)")
    parser.add_argument("-d", "--days", type=int, default=365, help="Number of days to fetch (default: 365)")
    parser.add_argument("-i", "--interval", default="1m", help="Candle interval (default: 1m)")

    args = parser.parse_args()

    symbol = args.symbol.upper()
    output_file = args.output or f"{symbol.lower()}_1m_klines.csv"

    # Calculate time range
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - (args.days * 24 * 60 * 60 * 1000)

    all_klines = []
    current_start = start_ms
    total_expected = (end_ms - start_ms) // 60_000
    print(f"Fetching ~{total_expected:,} candles for {symbol} ({args.interval})...")

    while current_start < end_ms:
        data = fetch_klines(symbol, args.interval, current_start, end_ms)
        if not data:
            break

        all_klines.extend(data)
        last_close_time = data[-1][6]  # close_time of last candle
        current_start = last_close_time + 1

        print(f"  {len(all_klines):>7,} candles fetched  (up to {datetime.fromtimestamp(last_close_time / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC)")

        if len(data) < LIMIT:
            break

        time.sleep(0.25)  # rate limit ~4 req/sec

    print(f"\nWriting {len(all_klines):,} candles to {output_file}...")
    with open(output_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADERS)
        for k in all_klines:
            writer.writerow([
                k[0],  # open_time
                k[1],  # open
                k[2],  # high
                k[3],  # low
                k[4],  # close
                k[5],  # volume
                k[6],  # close_time
                k[7],  # quote_volume
                k[8],  # trades
                k[9],  # taker_buy_base_vol
                k[10], # taker_buy_quote_vol
            ])

    print(f"✓ Done! {len(all_klines):,} candles saved to {output_file}")

    # Print data summary
    if all_klines:
        first_time = datetime.fromtimestamp(all_klines[0][0] / 1000, tz=timezone.utc)
        last_time = datetime.fromtimestamp(all_klines[-1][0] / 1000, tz=timezone.utc)
        days = (last_time - first_time).days
        print(f"  Data range: {first_time.strftime('%Y-%m-%d')} to {last_time.strftime('%Y-%m-%d')} ({days} days)")


if __name__ == "__main__":
    main()
