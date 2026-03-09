"""Fetch klines from Binance public API and save as CSV.

Writes batches incrementally so interrupted downloads can resume from the last
saved candle on the next run.
"""

import argparse
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

INTERVAL = "1m"
LIMIT = 1000  # max per request
SPOT_BASE_URL = "https://api.binance.com/api/v3/klines"
FUTURES_BASE_URL = "https://fapi.binance.com/fapi/v1/klines"
REQUEST_SLEEP_SEC = 0.35
MAX_RETRIES = 8

# Default to Futures API — this project trades USDT-M Futures exclusively

HEADERS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades", "taker_buy_base_vol",
    "taker_buy_quote_vol",
]


def _read_existing_progress(path: Path) -> dict[str, int] | None:
    if not path.exists() or path.stat().st_size == 0:
        return None

    with path.open("r", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != HEADERS:
            raise ValueError(f"Unexpected CSV header in {path}")

        first_row = None
        last_row = None
        row_count = 0
        for row in reader:
            if len(row) < len(HEADERS):
                continue
            if first_row is None:
                first_row = row
            last_row = row
            row_count += 1

    if row_count == 0 or first_row is None or last_row is None:
        return None

    return {
        "rows": row_count,
        "first_open_time": int(first_row[0]),
        "last_close_time": int(last_row[6]),
    }


def fetch_klines(symbol: str, interval: str, start: int, end: int, limit: int = LIMIT, use_futures: bool = False) -> list:
    base_url = FUTURES_BASE_URL if use_futures else SPOT_BASE_URL
    url = f"{base_url}?symbol={symbol}&interval={interval}&startTime={start}&endTime={end}&limit={limit}"
    attempt = 0

    while True:
        req = Request(url, headers={"User-Agent": "Python"})
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except HTTPError as exc:
            if exc.code == 429 and attempt < MAX_RETRIES:
                retry_after = exc.headers.get("Retry-After")
                sleep_sec = float(retry_after) if retry_after else min(90.0, 5.0 * (2 ** attempt))
                attempt += 1
                print(f"  HTTP 429 for {symbol} {interval}; sleeping {sleep_sec:.1f}s before retry {attempt}/{MAX_RETRIES}")
                time.sleep(sleep_sec)
                continue
            raise
        except URLError as exc:
            if attempt < MAX_RETRIES:
                sleep_sec = min(90.0, 5.0 * (2 ** attempt))
                attempt += 1
                print(f"  Network error for {symbol} {interval}: {exc.reason}; sleeping {sleep_sec:.1f}s before retry {attempt}/{MAX_RETRIES}")
                time.sleep(sleep_sec)
                continue
            raise


def main():
    parser = argparse.ArgumentParser(description="Download historical kline data from Binance")
    parser.add_argument("symbol", help="Trading pair symbol (e.g., DOGEUSDT, 1000SHIBUSDT)")
    parser.add_argument("-o", "--output", help="Output CSV file (default: <symbol_lower>_1m_klines.csv)")
    parser.add_argument("-d", "--days", type=int, default=365, help="Number of days to fetch (default: 365)")
    parser.add_argument("-i", "--interval", default="1m", help="Candle interval (default: 1m)")
    parser.add_argument("--spot", action="store_true", help="Use Spot API instead of Futures (not recommended for this project)")

    args = parser.parse_args()

    symbol = args.symbol.upper()
    output_path = Path(args.output or f"data/klines/{symbol.lower()}_1m_klines.csv")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Always use Futures API by default (USDT-M Futures project)
    use_futures = not args.spot
    api_type = "Futures" if use_futures else "Spot"

    # Calculate time range
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - (args.days * 24 * 60 * 60 * 1000)

    existing = _read_existing_progress(output_path)
    current_start = start_ms
    total_fetched = 0
    first_open_time = None
    last_close_time = None

    if existing:
        total_fetched = existing["rows"]
        first_open_time = existing["first_open_time"]
        last_close_time = existing["last_close_time"]
        current_start = max(start_ms, last_close_time + 1)
        print(
            f"Resuming from {output_path} with {total_fetched:,} candles "
            f"(up to {datetime.fromtimestamp(last_close_time / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC)"
        )

    total_expected = (end_ms - start_ms) // 60_000
    print(f"Fetching ~{total_expected:,} candles for {symbol} ({args.interval}) from {api_type} API...")

    write_mode = "a" if existing else "w"
    with output_path.open(write_mode, newline="") as f:
        writer = csv.writer(f)
        if not existing:
            writer.writerow(HEADERS)

        while current_start < end_ms:
            data = fetch_klines(symbol, args.interval, current_start, end_ms, use_futures=use_futures)
            if not data:
                break

            for k in data:
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
            f.flush()

            total_fetched += len(data)
            if first_open_time is None:
                first_open_time = int(data[0][0])
            last_close_time = int(data[-1][6])
            current_start = last_close_time + 1

            print(
                f"  {total_fetched:>7,} candles fetched  "
                f"(up to {datetime.fromtimestamp(last_close_time / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC)"
            )

            if len(data) < LIMIT:
                break

            time.sleep(REQUEST_SLEEP_SEC)

    print(f"✓ Done! {total_fetched:,} candles saved to {output_path}")

    # Print data summary
    if first_open_time is not None and last_close_time is not None:
        first_time = datetime.fromtimestamp(first_open_time / 1000, tz=timezone.utc)
        last_time = datetime.fromtimestamp(last_close_time / 1000, tz=timezone.utc)
        days = (last_time - first_time).days
        print(f"  Data range: {first_time.strftime('%Y-%m-%d')} to {last_time.strftime('%Y-%m-%d')} ({days} days)")


if __name__ == "__main__":
    main()
