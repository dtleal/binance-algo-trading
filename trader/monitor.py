import asyncio
import logging
import re
import sys
from datetime import datetime, timezone

from binance_sdk_spot.spot import (
    Spot,
    SPOT_WS_STREAMS_PROD_URL,
    ConfigurationWebSocketStreams,
)
from binance_sdk_spot.websocket_streams.models import (
    KlineIntervalEnum,
    PartialBookDepthLevelsEnum,
)

from trader.config import DEFAULT_SYMBOL, ALL_STREAMS, LOG_DIR

# ANSI colors for console output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
RESET = "\033[0m"
BOLD = "\033[1m"

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")

logger = logging.getLogger("trader.monitor")


class StripAnsiFormatter(logging.Formatter):
    """Formatter that strips ANSI escape codes for clean file output."""

    def format(self, record):
        result = super().format(record)
        return _ANSI_RE.sub("", result)


def setup_logging():
    """Configure logging to both console and daily log file."""
    LOG_DIR.mkdir(exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    log_file = LOG_DIR / f"axsusdt_{date_str}.log"

    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(StripAnsiFormatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s"))

    root_logger = logging.getLogger("trader")
    root_logger.setLevel(logging.INFO)
    root_logger.propagate = False
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


def _ts_str(ts_ms):
    """Convert millisecond timestamp to HH:MM:SS string."""
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return dt.strftime("%H:%M:%S")


def _handle_trade(data):
    """Handle individual trade stream messages."""
    ts = _ts_str(data.E)
    price = data.p
    qty = data.q
    side = f"{RED}SELL{RESET}" if data.m else f"{GREEN}BUY{RESET}"

    logger.info(
        f"[{ts}] {CYAN}TRADE{RESET} | Price: ${price} | Qty: {qty} | Side: {side}"
    )


def _handle_kline(data):
    """Handle kline/candlestick stream messages."""
    ts = _ts_str(data.E)
    k = data.k
    logger.info(
        f"[{ts}] {MAGENTA}KLINE 1m{RESET} | "
        f"O: {k.o} H: {k.h} L: {k.l} C: {k.c} | Vol: {k.v}"
    )


_last_ticker_time = 0.0


def _handle_ticker(data):
    """Handle 24hr ticker stream messages. Rate-limited to every 5 seconds."""
    global _last_ticker_time
    now = asyncio.get_event_loop().time()
    if now - _last_ticker_time < 5.0:
        return
    _last_ticker_time = now

    ts = _ts_str(data.E)
    logger.info(
        f"[{ts}] {YELLOW}TICKER{RESET} | "
        f"Price: ${data.c} | 24h Change: {data.P}% | Vol: {data.v}"
    )


_last_best_bid = None
_last_best_ask = None


def _handle_depth(data):
    """Handle partial order book depth messages. Only logs on significant spread changes."""
    global _last_best_bid, _last_best_ask

    if not data.bids or not data.asks:
        return

    best_bid = data.bids[0][0]
    best_ask = data.asks[0][0]

    if best_bid == _last_best_bid and best_ask == _last_best_ask:
        return

    _last_best_bid = best_bid
    _last_best_ask = best_ask

    spread = float(best_ask) - float(best_bid)
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

    logger.info(
        f"[{ts}] {BOLD}DEPTH{RESET} | "
        f"Bid: ${best_bid} | Ask: ${best_ask} | Spread: ${spread:.4f}"
    )


async def run_monitor(symbol: str, streams: list[str]):
    """Main monitoring loop. Connects to Binance WebSocket and subscribes to streams."""
    setup_logging()

    symbol = symbol.lower()
    active_streams = streams if streams else ALL_STREAMS

    logger.info(f"{BOLD}Starting market monitor for {symbol.upper()}{RESET}")
    logger.info(f"Streams: {', '.join(active_streams)}")
    logger.info("-" * 60)

    config = ConfigurationWebSocketStreams(stream_url=SPOT_WS_STREAMS_PROD_URL)
    client = Spot(config_ws_streams=config)

    connection = None
    subscribed = []

    try:
        connection = await client.websocket_streams.create_connection()

        if "trade" in active_streams:
            stream = await connection.trade(symbol=symbol)
            stream.on("message", _handle_trade)
            subscribed.append(stream)
            logger.info(f"  Subscribed to {symbol}@trade")

        if "kline" in active_streams:
            stream = await connection.kline(
                symbol=symbol,
                interval=KlineIntervalEnum["INTERVAL_1m"].value,
            )
            stream.on("message", _handle_kline)
            subscribed.append(stream)
            logger.info(f"  Subscribed to {symbol}@kline_1m")

        if "ticker" in active_streams:
            stream = await connection.ticker(symbol=symbol)
            stream.on("message", _handle_ticker)
            subscribed.append(stream)
            logger.info(f"  Subscribed to {symbol}@ticker")

        if "depth" in active_streams:
            stream = await connection.partial_book_depth(
                symbol=symbol,
                levels=PartialBookDepthLevelsEnum["LEVELS_20"].value,
            )
            stream.on("message", _handle_depth)
            subscribed.append(stream)
            logger.info(f"  Subscribed to {symbol}@depth20")

        logger.info("-" * 60)

        # Keep running until interrupted
        while True:
            await asyncio.sleep(1)

    except asyncio.CancelledError:
        logger.info("Monitor cancelled.")
    finally:
        for s in subscribed:
            try:
                await s.unsubscribe()
            except Exception:
                pass
        if connection:
            try:
                await connection.close_connection(close_session=True)
            except Exception:
                pass
        logger.info("Connection closed. Goodbye.")
