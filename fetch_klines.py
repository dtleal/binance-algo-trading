"""Fetch 1-minute AXSUSDC klines from Binance public API and save as CSV."""

import csv
import time
from datetime import datetime, timezone
from urllib.request import urlopen, Request
import json

SYMBOL = "ETHUSDT"
INTERVAL = "1m"
LIMIT = 1000  # max per request
BASE_URL = "https://api.binance.com/api/v3/klines"

# 1 year ago from now
end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
start_ms = end_ms - (365 * 24 * 60 * 60 * 1000)

CSV_FILE = "ethusdt_1m_klines.csv"
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
    all_klines = []
    current_start = start_ms
    total_expected = (end_ms - start_ms) // 60_000
    print(f"Fetching ~{total_expected:,} candles for {SYMBOL} ({INTERVAL})...")

    while current_start < end_ms:
        data = fetch_klines(SYMBOL, INTERVAL, current_start, end_ms)
        if not data:
            break

        all_klines.extend(data)
        last_close_time = data[-1][6]  # close_time of last candle
        current_start = last_close_time + 1

        print(f"  {len(all_klines):>7,} candles fetched  (up to {datetime.fromtimestamp(last_close_time / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC)")

        if len(data) < LIMIT:
            break

        time.sleep(0.25)  # respect rate limits

    # Write CSV
    with open(CSV_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADERS)
        for k in all_klines:
            # k[11] is an "ignore" field — skip it
            writer.writerow(k[:11])

    print(f"\nDone! Saved {len(all_klines):,} candles to {CSV_FILE}")


if __name__ == "__main__":
    main()
