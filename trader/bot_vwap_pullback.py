"""VWAPPullback live trading bot — bidirectional VWAP pullback with EMA trend filter.

Works with any USDT-M futures symbol. Trend is determined per-candle via an EMA:
  - close > EMA → uptrend  → look for LONG entries
  - close < EMA → downtrend → look for SHORT entries

Exchange precision (tick_size, step_size) is fetched from Binance at startup for
symbols not pre-configured in SYMBOL_CONFIGS. In dry-run mode, sensible defaults
are used.
"""

import asyncio
import collections
import logging
import math
import re
import sys
from datetime import datetime, timezone
from enum import Enum, auto

from trader.config import (
    BINANCE_API_KEY,
    BINANCE_SECRET_KEY,
    SOCKS_PROXY,
    DEFAULT_LEVERAGE,
    LOG_DIR,
    SYMBOL_CONFIGS,
)
from trader.strategy import VWAPRollingTracker
from trader.strategy_vwap_pullback import EMATracker, VWAPPullbackSignal
from trader import events as _events, bot_registry as _registry
from trader.notifications import (
    notify_bot_started, notify_bot_stopped, notify_signal, notify_position_opened,
    notify_position_closed, notify_eod_close, notify_error, notify_cooldown,
)


def _parse_proxy(url: str) -> dict | None:
    if not url:
        return None
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if not parsed.hostname or not parsed.port:
        raise SystemExit(f"Invalid SOCKS_PROXY format: '{url}'. Expected 'socks5://host:port'")
    return {"protocol": parsed.scheme, "host": parsed.hostname, "port": parsed.port}


def _decimals_from_step(step_str: str) -> int:
    """Derive decimal places from a Binance step/tick string (e.g. '0.001' → 3)."""
    step_str = step_str.rstrip("0")
    if "." not in step_str:
        return 0
    return len(step_str.split(".")[1])


GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

logger = logging.getLogger("trader.pullback")


class _State(Enum):
    SCANNING = auto()
    IN_POSITION = auto()
    COOLDOWN = auto()


class VWAPPullbackBot:
    """Bidirectional VWAP pullback bot for USDT-M futures.

    Works for any symbol. Determines long/short direction from an EMA trend
    filter computed on live 1-minute candles.
    """

    def __init__(
        self,
        symbol: str,
        leverage: int = DEFAULT_LEVERAGE,
        capital: float | None = None,
        dry_run: bool = False,
        tp_pct: float = 5.0,
        sl_pct: float = 2.5,
        min_bars: int = 3,
        confirm_bars: int = 2,
        vwap_prox: float = 0.005,
        vwap_window_days: int = 10,
        entry_start_min: int = 60,
        entry_cutoff_min: int = 1320,
        eod_min: int = 1430,
        pos_size_pct: float = 0.20,
        ema_period: int = 200,
        max_trades_per_day: int = 4,
        vol_filter: bool = False,
        interval: str = "1m",
        vwap_dist_stop: float = 0.0,
    ):
        self.symbol = symbol.upper()
        self.leverage = leverage
        self.capital = capital
        self.dry_run = dry_run
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.eod_min = eod_min
        self.pos_size_pct = pos_size_pct
        self.vwap_dist_stop = vwap_dist_stop
        self.min_notional = 5.0  # Binance default minimum
        self.interval = interval

        # Precision — resolved at startup
        self._price_decimals = 4  # default, overridden from exchange info
        self._qty_decimals = 3    # default, overridden from exchange info
        self._qty_step = 0.001    # default

        # Override precision from pre-configured symbols if available
        if self.symbol in SYMBOL_CONFIGS:
            cfg = SYMBOL_CONFIGS[self.symbol]
            self._price_decimals = cfg.price_decimals
            self._qty_decimals = cfg.qty_decimals
            self._qty_step = 10 ** (-cfg.qty_decimals) if cfg.qty_decimals > 0 else 1
            self.min_notional = cfg.min_notional

        self._client = None
        if not dry_run:
            if not BINANCE_API_KEY or not BINANCE_SECRET_KEY:
                raise SystemExit(
                    "BINANCE_API_KEY and BINANCE_SECRET_KEY must be set "
                    "(in .env or as environment variables)"
                )
            from binance_sdk_derivatives_trading_usds_futures import (
                DerivativesTradingUsdsFutures,
                DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL,
            )
            from binance_common.configuration import (
                ConfigurationRestAPI,
                ConfigurationWebSocketStreams,
            )
            self._proxy = _parse_proxy(SOCKS_PROXY)
            rest_config = ConfigurationRestAPI(
                api_key=BINANCE_API_KEY,
                api_secret=BINANCE_SECRET_KEY,
                proxy=self._proxy,
                timeout=5000,
            )
            self._ws_url = DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL
            self._client = DerivativesTradingUsdsFutures(config_rest_api=rest_config)
            self._ws_factory = DerivativesTradingUsdsFutures
            self._ConfigWS = ConfigurationWebSocketStreams
        else:
            self._ws_url = None

        self._vwap = VWAPRollingTracker(window_days=vwap_window_days)
        self._ema = EMATracker(period=ema_period)
        self._signal = VWAPPullbackSignal(
            min_bars=min_bars,
            confirm_bars=confirm_bars,
            vwap_prox=vwap_prox,
            entry_start_min=entry_start_min,
            entry_cutoff_min=entry_cutoff_min,
            max_trades_per_day=max_trades_per_day,
            vol_filter=vol_filter,
        )
        self._vol_history: collections.deque[float] = collections.deque(maxlen=20)
        self._state = _State.SCANNING
        self._current_day = -1

        # Position tracking
        self._direction: str | None = None  # "long" or "short"
        self._entry_price = 0.0
        self._position_qty: float = 0.0
        self._sl_price = 0.0
        self._tp_price = 0.0

        # Background tasks
        self._eod_task: asyncio.Task | None = None
        self._monitor_task: asyncio.Task | None = None
        self._uds_task: asyncio.Task | None = None

        # Dashboard integration
        self._reg_key = f"{self.symbol}:pullback"

    def _emit(self, event: dict) -> None:
        """Fire-and-forget event publish to the dashboard WebSocket bus."""
        try:
            asyncio.get_event_loop().create_task(_events.publish(event))
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # Logging setup
    # ------------------------------------------------------------------

    def _setup_logging(self):
        global logger
        ansi_re = re.compile(r"\033\[[0-9;]*m")

        LOG_DIR.mkdir(exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        log_file = LOG_DIR / f"pullback_{self.symbol}_{date_str}.log"

        class StripAnsiFormatter(logging.Formatter):
            def format(self, record):
                result = super().format(record)
                return ansi_re.sub("", result)

        bot_logger = logging.getLogger(f"trader.pullback.{self.symbol}")
        if not bot_logger.handlers:
            bot_logger.setLevel(logging.INFO)
            bot_logger.propagate = False

            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(logging.INFO)
            file_handler.setFormatter(
                StripAnsiFormatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S")
            )

            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(logging.INFO)
            console_handler.setFormatter(logging.Formatter("%(message)s"))

            bot_logger.addHandler(file_handler)
            bot_logger.addHandler(console_handler)

            # Add Redis log publisher for real-time streaming to UI
            try:
                import os
                import redis
                from trader.log_publisher import RedisLogHandler

                redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
                redis_client = redis.from_url(redis_url, decode_responses=True)
                redis_handler = RedisLogHandler(redis_client, self._reg_key, max_logs=500)
                redis_handler.setLevel(logging.INFO)
                redis_handler.setFormatter(logging.Formatter("%(message)s"))
                bot_logger.addHandler(redis_handler)
            except Exception:
                # Graceful degradation if Redis is unavailable
                pass

        logger = bot_logger

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _round_price(self, price: float) -> float:
        factor = 10 ** self._price_decimals
        return math.floor(price * factor) / factor

    def _round_qty(self, qty: float) -> float:
        if self._qty_decimals == 0:
            return float(int(math.floor(qty)))
        step = self._qty_step
        return math.floor(qty / step) * step

    def _fmt_qty(self, qty: float) -> str:
        if self._qty_decimals == 0:
            return str(int(qty))
        return f"{qty:.{self._qty_decimals}f}"

    # ------------------------------------------------------------------
    # REST helpers
    # ------------------------------------------------------------------

    def _get_position(self) -> dict | None:
        resp = self._client.rest_api.position_information_v3(symbol=self.symbol)
        for pos in resp.data():
            amt = float(pos.position_amt)
            if amt != 0:
                return {
                    "position_amt": amt,
                    "entry_price": float(pos.entry_price),
                    "unrealized_profit": float(pos.un_realized_profit),
                }
        return None

    def _fetch_exchange_precision(self):
        """Fetch tick_size and step_size from Binance exchange info."""
        try:
            info = self._client.rest_api.exchange_information()
            for sym in info.data().symbols:
                if sym.symbol != self.symbol:
                    continue
                for f in sym.filters:
                    if f.get("filterType") == "PRICE_FILTER":
                        self._price_decimals = _decimals_from_step(f["tickSize"])
                    elif f.get("filterType") == "LOT_SIZE":
                        self._qty_decimals = _decimals_from_step(f["stepSize"])
                        step = float(f["stepSize"])
                        self._qty_step = step if step > 0 else 1.0
                    elif f.get("filterType") == "MIN_NOTIONAL":
                        self.min_notional = float(f.get("notional", 5.0))
                logger.info(
                    f"Exchange precision for {self.symbol}: "
                    f"price_decimals={self._price_decimals}, "
                    f"qty_decimals={self._qty_decimals}"
                )
                return
            raise SystemExit(
                f"Symbol '{self.symbol}' not found on Binance USDT-M Futures. "
                f"Check the symbol name and ensure it is a futures pair."
            )
        except SystemExit:
            raise
        except Exception as e:
            logger.info(f"{YELLOW}Could not fetch exchange precision: {e} — using defaults{RESET}")

    # ------------------------------------------------------------------
    # Indicator seeding from historical klines
    # ------------------------------------------------------------------

    def _seed_indicators(self):
        """Pre-populate EMA, VWAP and vol_history from historical 1m klines.

        Fetches from the public Binance Futures REST endpoint (no auth required).
        Uses ema_period + 50 candles so the EMA is fully established on startup.
        VWAP rolling tracker is fed ALL historical candles (not just today).
        """
        import json
        import urllib.request

        limit = min(self._ema.period + 50, 1500)  # Binance max is 1500
        url = (
            f"https://fapi.binance.com/fapi/v1/klines"
            f"?symbol={self.symbol}&interval=1m&limit={limit}"
        )
        logger.info(f"Seeding indicators from last {limit} historical 1m candles...")

        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                klines = json.loads(resp.read())
        except Exception as e:
            logger.info(
                f"{YELLOW}Could not fetch historical klines: {e} "
                f"— EMA will warm up from live candles{RESET}"
            )
            return

        if not klines:
            return

        now_utc = datetime.now(timezone.utc)
        now_ms = int(now_utc.timestamp() * 1000)

        seeded = 0
        for k in klines:
            open_time_ms = int(k[0])
            close_time_ms = int(k[6])

            # Skip the current open (not yet closed) candle
            if close_time_ms > now_ms:
                continue

            h = float(k[2])
            l = float(k[3])
            c = float(k[4])
            v = float(k[5])
            day_ordinal = open_time_ms // 86_400_000

            # EMA spans multiple days — always update
            self._ema.update(c)
            seeded += 1

            # Rolling VWAP — feed ALL historical candles with day_ordinal
            self._vwap.update(h, l, c, v, day_ordinal)
            if self._current_day == -1:
                self._current_day = day_ordinal

            # vol_history: rolling window, keep feeding all candles
            self._vol_history.append(v)

        ema_ready = self._ema._count >= self._ema.period
        if ema_ready and self._ema.value is not None:
            logger.info(
                f"EMA({self._ema.period}) ready: "
                f"{self._ema.value:.{self._price_decimals}f} "
                f"(seeded from {seeded} candles)"
            )
        else:
            remaining = self._ema.period - self._ema._count
            logger.info(
                f"{YELLOW}EMA({self._ema.period}) partially seeded ({seeded} candles). "
                f"Needs {remaining} more live candles before trading.{RESET}"
            )

        if self._vwap.value > 0:
            logger.info(
                f"VWAP({self._vwap.window_days}d) ready: {self._vwap.value:.{self._price_decimals}f} "
                f"(rolling window seeded)"
            )

    # ------------------------------------------------------------------
    # Startup checks
    # ------------------------------------------------------------------

    def _check_startup_position(self):
        if self.dry_run:
            return
        pos = self._get_position()
        if pos is None:
            self._check_traded_today()
            return

        amt = pos["position_amt"]
        if amt > 0:
            self._direction = "long"
        elif amt < 0:
            self._direction = "short"
        else:
            self._check_traded_today()
            return

        self._state = _State.IN_POSITION
        self._entry_price = pos["entry_price"]
        self._position_qty = abs(amt)

        if self._direction == "long":
            self._sl_price = self._round_price(self._entry_price * (1 - self.sl_pct / 100))
            self._tp_price = self._round_price(self._entry_price * (1 + self.tp_pct / 100))
        else:
            self._sl_price = self._round_price(self._entry_price * (1 + self.sl_pct / 100))
            self._tp_price = self._round_price(self._entry_price * (1 - self.tp_pct / 100))

        self._signal.mark_traded()
        logger.info(
            f"{YELLOW}Resuming existing {self._direction.upper()}: "
            f"{self._position_qty} {self.symbol} @ ${self._entry_price:.{self._price_decimals}f} | "
            f"SL ${self._sl_price} | TP ${self._tp_price}{RESET}"
        )
        self._monitor_task = asyncio.get_event_loop().create_task(
            self._monitor_position_fill()
        )

    def _check_traded_today(self):
        now = datetime.now(timezone.utc)
        start_ms = int(
            now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000
        )
        try:
            resp = self._client.rest_api.account_trade_list(
                symbol=self.symbol, start_time=start_ms
            )
            trades = resp.data()
            if not trades:
                return
            for t in trades:
                # Any SELL (short open) or BUY (long open, side=BUY buyer=True)
                if (t.side == "SELL" and not t.buyer) or (t.side == "BUY" and t.buyer):
                    self._signal.mark_traded()
            if self._signal.trades_today > 0:
                logger.info(
                    f"{YELLOW}Found {self._signal.trades_today} trade(s) today for "
                    f"{self.symbol} (limit: {self._signal.max_trades_per_day}){RESET}"
                )
            if self._signal.traded_today:
                self._state = _State.COOLDOWN
                logger.info(
                    f"{YELLOW}Daily limit reached — entering COOLDOWN{RESET}"
                )
        except Exception as e:
            logger.info(f"{YELLOW}Could not check trade history: {e}{RESET}")

    def _resolve_capital(self):
        if self.capital is not None:
            return
        if self.dry_run:
            self.capital = 1000.0
            logger.info(f"[DRY-RUN] Using simulated capital: ${self.capital:.2f}")
            return
        resp = self._client.rest_api.futures_account_balance_v3()
        for bal in resp.data():
            if bal.asset == "USDT":
                self.capital = float(bal.balance)
                logger.info(f"Account USDT balance: ${self.capital:.2f}")
                return
        raise SystemExit("Could not find USDT balance")

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    async def run(self):
        self._setup_logging()
        prefix = "[DRY-RUN] " if self.dry_run else ""

        logger.info(f"{BOLD}{prefix}VWAPPullback Bot — {self.symbol}{RESET}")
        vwap_dist_label = f" | VWAP dist stop: {self.vwap_dist_stop*100:.0f}%" if self.vwap_dist_stop > 0 else ""
        logger.info(
            f"Leverage: {self.leverage}x | "
            f"Interval: {self.interval} | "
            f"TP: {self.tp_pct}% | SL: {self.sl_pct}% | "
            f"Position size: {self.pos_size_pct * 100:.0f}% | "
            f"EMA period: {self._ema.period} | "
            f"Max trades/day: {self._signal.max_trades_per_day}"
            f"{vwap_dist_label}"
        )
        logger.info("-" * 60)
        if not self.dry_run:
            notify_bot_started(self.symbol, "VWAPPullback", self.interval, self.leverage, self.pos_size_pct)

        if not self.dry_run:
            self._fetch_exchange_precision()

        self._seed_indicators()
        self._check_startup_position()
        self._resolve_capital()

        per_trade = self.capital * self.pos_size_pct
        logger.info(f"Capital: ${self.capital:.2f} | Per-trade: ${per_trade:.2f}")
        if per_trade < self.min_notional:
            min_cap = math.ceil(self.min_notional / self.pos_size_pct * 100) / 100
            raise SystemExit(
                f"Per-trade capital ${per_trade:.2f} is below Binance minimum "
                f"notional ${self.min_notional:.2f} for {self.symbol}. "
                f"Minimum --capital is ${min_cap:.2f}."
            )
        logger.info("-" * 60)

        # Publish bot configuration to registry
        _registry.update(self._reg_key, {
            "symbol": self.symbol,
            "strategy": "pullback",
            "config": {
                "leverage": self.leverage,
                "tp_pct": self.tp_pct,
                "sl_pct": self.sl_pct,
                "pos_size_pct": self.pos_size_pct,
                "ema_period": self._ema.period,
                "min_bars": self._signal.min_bars,
                "confirm_bars": self._signal.confirm_bars,
                "vwap_prox": self._signal.vwap_prox,
                "vwap_window_days": self._vwap.window_days,
                "max_trades_per_day": self._signal.max_trades_per_day,
                "capital": self.capital,
                "per_trade": per_trade,
            },
            "dry_run": self.dry_run,
        })

        self._schedule_eod()

        if self.dry_run:
            from binance_sdk_derivatives_trading_usds_futures import (
                DerivativesTradingUsdsFutures,
                DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL,
            )
            from binance_common.configuration import ConfigurationWebSocketStreams
            ws_config = ConfigurationWebSocketStreams(
                stream_url=DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL,
            )
            ws_client = DerivativesTradingUsdsFutures(config_ws_streams=ws_config)
        else:
            ws_config = self._ConfigWS(stream_url=self._ws_url)
            ws_client = self._ws_factory(config_ws_streams=ws_config)

        if SOCKS_PROXY:
            from aiohttp_socks import ProxyConnector
            import aiohttp
            connector = ProxyConnector.from_url(SOCKS_PROXY)
            ws_client.websocket_streams.session = aiohttp.ClientSession(connector=connector)

        connection = None
        stream = None

        try:
            connection = await ws_client.websocket_streams.create_connection()
            stream = await connection.kline_candlestick_streams(
                symbol=self.symbol.lower(), interval=self.interval
            )
            stream.on("message", self._on_kline)
            logger.info(f"Subscribed to {self.symbol.lower()}@kline_{self.interval} (futures)")
            logger.info(f"State: {self._state.name} | Waiting for candles...")
            logger.info("-" * 60)

            if not self.dry_run:
                from trader.user_data_stream import UserDataStream
                uds = UserDataStream(self._client, self._ws_factory, self._ws_url, self._ConfigWS)
                uds.register(self._on_user_data)
                self._uds_task = asyncio.create_task(uds.run())

            while True:
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("\nBot shutting down...")
        finally:
            if self._uds_task and not self._uds_task.done():
                self._uds_task.cancel()
            if self._eod_task and not self._eod_task.done():
                self._eod_task.cancel()
            if self._monitor_task and not self._monitor_task.done():
                self._monitor_task.cancel()
            if stream:
                try:
                    await stream.unsubscribe()
                except Exception:
                    pass
            if connection:
                try:
                    await connection.close_connection(close_session=True)
                except Exception:
                    pass
            if not self.dry_run:
                notify_bot_stopped(self.symbol, "VWAPPullback")
            logger.info("Connection closed. Goodbye.")

    # ------------------------------------------------------------------
    # Kline callback
    # ------------------------------------------------------------------

    def _on_kline(self, data):
        k = data.k
        if not k.x:
            return

        o, h, l, c, v = float(k.o), float(k.h), float(k.l), float(k.c), float(k.v)
        candle_open_ms = int(k.t)
        day_ordinal = candle_open_ms // 86_400_000
        minute_of_day = (candle_open_ms % 86_400_000) // 60_000
        ts = datetime.fromtimestamp(candle_open_ms / 1000, tz=timezone.utc).strftime("%H:%M")

        self._check_daily_reset(day_ordinal)
        vwap = self._vwap.update(h, l, c, v, day_ordinal)

        # EMA trend
        ema = self._ema.update(c)
        if ema is None:
            trend = None
            trend_label = f"{YELLOW}EMA warming up ({self._ema._count}/{self._ema.period}){RESET}"
        elif c > ema:
            trend = "up"
            trend_label = f"UP  EMA={ema:.{self._price_decimals}f}"
        else:
            trend = "down"
            trend_label = f"DOWN EMA={ema:.{self._price_decimals}f}"

        self._vol_history.append(v)
        vol_sma = sum(self._vol_history) / len(self._vol_history)

        prefix = "[DRY-RUN] " if self.dry_run else ""

        if self._state == _State.SCANNING:
            signal = self._signal.on_candle(
                c, vwap, minute_of_day, trend, volume=v, vol_sma20=vol_sma
            )
            state_info = (
                f"cnt={self._signal.counter}"
                if not self._signal.confirming
                else f"confirm={self._signal.confirm_count}/{self._signal.confirm_bars}"
            )
            _registry.update(self._reg_key, {
                "symbol": self.symbol, "strategy": "pullback",
                "state": self._state.name, "price": c, "vwap": vwap,
                "ema": self._ema.value, "trend": trend,
                "counter": self._signal.counter,
                "confirming": self._signal.confirming,
                "trades_today": self._signal.trades_today,
                "max_trades_per_day": self._signal.max_trades_per_day,
                "dry_run": self.dry_run,
            })
            self._emit({
                "type": "candle", "symbol": self.symbol, "strategy": "pullback",
                "ts": ts, "price": c, "vwap": vwap,
                "ema": self._ema.value, "trend": trend,
                "state": self._state.name,
                "counter": self._signal.counter,
                "confirming": self._signal.confirming,
            })
            logger.info(
                f"{prefix}[{ts}] C={c:.{self._price_decimals}f} "
                f"VWAP={vwap:.{self._price_decimals}f} | "
                f"Trend={trend_label} | {state_info} | SCANNING"
            )
            if signal == "ENTER_LONG":
                logger.info(f"{BOLD}{GREEN}{prefix}SIGNAL: ENTER_LONG @ {c:.{self._price_decimals}f}{RESET}")
                self._emit({"type": "signal", "symbol": self.symbol, "strategy": "pullback",
                            "direction": "long", "price": c, "ts": ts})
                if not self.dry_run:
                    notify_signal(self.symbol, "long", round(c, self._price_decimals), "VWAPPullback")
                asyncio.get_event_loop().create_task(self._enter_position("long", c))
            elif signal == "ENTER_SHORT":
                logger.info(f"{BOLD}{RED}{prefix}SIGNAL: ENTER_SHORT @ {c:.{self._price_decimals}f}{RESET}")
                self._emit({"type": "signal", "symbol": self.symbol, "strategy": "pullback",
                            "direction": "short", "price": c, "ts": ts})
                if not self.dry_run:
                    notify_signal(self.symbol, "short", round(c, self._price_decimals), "VWAPPullback")
                asyncio.get_event_loop().create_task(self._enter_position("short", c))

        elif self._state == _State.IN_POSITION:
            # VWAP distance stop: exit if price diverged too far from VWAP in wrong direction
            if self.vwap_dist_stop > 0.0 and vwap > 0.0:
                dist = (c - vwap) / vwap
                too_far = (
                    (self._direction == "short" and dist > self.vwap_dist_stop) or
                    (self._direction == "long" and dist < -self.vwap_dist_stop)
                )
                if too_far:
                    logger.info(
                        f"{YELLOW}VWAP dist stop: {dist*100:+.2f}% from VWAP "
                        f"(threshold ±{self.vwap_dist_stop*100:.0f}%) — closing position{RESET}"
                    )
                    if self.dry_run:
                        self._state = _State.COOLDOWN
                        self._direction = None
                    else:
                        asyncio.get_event_loop().create_task(
                            self._eod_close(reason="VWAP dist stop")
                        )
                    return

            if self._direction == "long":
                pnl = (c - self._entry_price) * self._position_qty
                pnl_pct = ((c - self._entry_price) / self._entry_price) * 100
            else:
                pnl = (self._entry_price - c) * self._position_qty
                pnl_pct = ((self._entry_price - c) / self._entry_price) * 100
            color = GREEN if pnl >= 0 else RED
            _registry.update(self._reg_key, {
                "state": self._state.name, "price": c, "vwap": vwap,
                "unrealized_pnl": round(pnl, 4), "unrealized_pnl_pct": round(pnl_pct, 4),
                "direction": self._direction,
                "entry_price": self._entry_price, "sl_price": self._sl_price,
                "tp_price": self._tp_price, "position_qty": self._position_qty,
            })
            self._emit({
                "type": "candle", "symbol": self.symbol, "strategy": "pullback",
                "ts": ts, "price": c, "vwap": vwap, "state": self._state.name,
                "unrealized_pnl": round(pnl, 4), "unrealized_pnl_pct": round(pnl_pct, 4),
            })
            logger.info(
                f"{prefix}[{ts}] C={c:.{self._price_decimals}f} "
                f"VWAP={vwap:.{self._price_decimals}f} | "
                f"P&L: {color}${pnl:+.2f} ({pnl_pct:+.2f}%){RESET} | "
                f"{self._direction.upper()} IN_POSITION"
            )

        elif self._state == _State.COOLDOWN:
            logger.info(
                f"{prefix}[{ts}] C={c:.{self._price_decimals}f} "
                f"VWAP={vwap:.{self._price_decimals}f} | COOLDOWN"
            )
            # Heartbeat: publish state so bot appears in UI even when idle
            _registry.update(self._reg_key, {
                "state": "COOLDOWN",
                "price": c,
                "vwap": vwap,
            })

    # ------------------------------------------------------------------
    # Daily reset
    # ------------------------------------------------------------------

    def _check_daily_reset(self, day_ordinal: int):
        if day_ordinal == self._current_day:
            return
        first_candle = self._current_day == -1
        self._current_day = day_ordinal
        if first_candle:
            return
        logger.info(f"{BOLD}--- New UTC day (ordinal {day_ordinal}) — resetting signal ---{RESET}")
        # VWAP rolling tracker does not reset (it's a rolling window)
        self._signal.reset_daily()
        self._vol_history.clear()
        if self._state == _State.COOLDOWN:
            self._state = _State.SCANNING
        self._schedule_eod()

    def _schedule_eod(self):
        if self._eod_task and not self._eod_task.done():
            self._eod_task.cancel()
        self._eod_task = asyncio.get_event_loop().create_task(self._eod_timer())

    async def _eod_timer(self):
        now = datetime.now(timezone.utc)
        eod_hour = self.eod_min // 60
        eod_minute = self.eod_min % 60
        target = now.replace(hour=eod_hour, minute=eod_minute, second=0, microsecond=0)
        if target <= now:
            return
        delay = (target - now).total_seconds()
        logger.info(f"EOD timer set for {target.strftime('%H:%M')} UTC ({delay:.0f}s from now)")
        await asyncio.sleep(delay)
        await self._eod_close()

    # ------------------------------------------------------------------
    # Order execution
    # ------------------------------------------------------------------

    async def _enter_position(self, direction: str, entry_price: float):
        prefix = "[DRY-RUN] " if self.dry_run else ""
        trade_capital = self.capital * self.pos_size_pct
        raw_qty = trade_capital / entry_price
        qty = self._round_qty(raw_qty)

        if qty <= 0:
            logger.info(f"{RED}Calculated quantity is 0 — skipping entry{RESET}")
            return

        # Check minimum notional (Binance requires min $20 per order)
        notional = qty * entry_price
        if notional < self.min_notional:
            logger.info(
                f"{RED}Order notional ${notional:.2f} < min ${self.min_notional:.2f} — skipping entry{RESET}"
            )
            logger.info(
                f"{YELLOW}💡 Increase capital or position size to meet minimum${RESET}"
            )
            return

        side = "BUY" if direction == "long" else "SELL"
        sl_price = (
            self._round_price(entry_price * (1 - self.sl_pct / 100))
            if direction == "long"
            else self._round_price(entry_price * (1 + self.sl_pct / 100))
        )
        tp_price = (
            self._round_price(entry_price * (1 + self.tp_pct / 100))
            if direction == "long"
            else self._round_price(entry_price * (1 - self.tp_pct / 100))
        )

        logger.info(
            f"{prefix}Entering {direction.upper()}: {self._fmt_qty(qty)} {self.symbol} "
            f"@ ~${entry_price:.{self._price_decimals}f}"
        )

        if self.dry_run:
            self._direction = direction
            self._entry_price = entry_price
            self._position_qty = qty
            self._sl_price = sl_price
            self._tp_price = tp_price
            self._state = _State.IN_POSITION
            color = GREEN if direction == "long" else RED
            logger.info(
                f"{color}{prefix}{direction.upper()} opened | "
                f"Entry: ${self._entry_price:.{self._price_decimals}f} | "
                f"SL: ${self._sl_price} | TP: ${self._tp_price}{RESET}"
            )
            self._emit({"type": "order", "symbol": self.symbol, "strategy": "pullback",
                        "direction": direction, "entry_price": entry_price,
                        "qty": qty, "sl_price": sl_price, "tp_price": tp_price,
                        "dry_run": True})
            _registry.update(self._reg_key, {
                "state": self._state.name, "direction": direction,
                "entry_price": entry_price, "sl_price": sl_price, "tp_price": tp_price,
                "position_qty": qty,
            })
            return

        try:
            self._client.rest_api.change_initial_leverage(
                symbol=self.symbol, leverage=self.leverage
            )

            order_resp = self._client.rest_api.new_order(
                symbol=self.symbol,
                side=side,
                type="MARKET",
                quantity=self._fmt_qty(qty),
                new_order_resp_type="RESULT",
            )
            order_data = order_resp.data()
            avg_price = float(order_data.avg_price) if order_data.avg_price else entry_price
            executed_qty = float(order_data.executed_qty)

            self._direction = direction
            self._entry_price = avg_price
            self._position_qty = executed_qty
            self._sl_price = (
                self._round_price(avg_price * (1 - self.sl_pct / 100))
                if direction == "long"
                else self._round_price(avg_price * (1 + self.sl_pct / 100))
            )
            self._tp_price = (
                self._round_price(avg_price * (1 + self.tp_pct / 100))
                if direction == "long"
                else self._round_price(avg_price * (1 - self.tp_pct / 100))
            )

            color = GREEN if direction == "long" else RED
            logger.info(
                f"{color}{side} {executed_qty} {self.symbol} @ ${avg_price:.{self._price_decimals}f} | "
                f"Order ID: {order_data.order_id}{RESET}"
            )

            # SL order — opposite side to close position
            sl_side = "SELL" if direction == "long" else "BUY"
            tp_side = sl_side
            sl_type = "STOP_MARKET"
            tp_type = "TAKE_PROFIT_MARKET"

            sl_resp = self._client.rest_api.new_algo_order(
                algo_type="CONDITIONAL",
                symbol=self.symbol,
                side=sl_side,
                type=sl_type,
                trigger_price=self._sl_price,
                close_position="true",
            )
            logger.info(
                f"{color}SL placed @ ${self._sl_price} | "
                f"Algo ID: {sl_resp.data().algo_id}{RESET}"
            )

            tp_resp = self._client.rest_api.new_algo_order(
                algo_type="CONDITIONAL",
                symbol=self.symbol,
                side=tp_side,
                type=tp_type,
                trigger_price=self._tp_price,
                close_position="true",
            )
            logger.info(
                f"{color}TP placed @ ${self._tp_price} | "
                f"Algo ID: {tp_resp.data().algo_id}{RESET}"
            )

            self._state = _State.IN_POSITION
            logger.info(
                f"{BOLD}{direction.upper()} opened | Entry: ${avg_price:.{self._price_decimals}f} | "
                f"SL: ${self._sl_price} | TP: ${self._tp_price}{RESET}"
            )
            notify_position_opened(
                self.symbol, direction, avg_price,
                self._sl_price, self._tp_price, executed_qty, self.leverage,
            )

            self._monitor_task = asyncio.get_event_loop().create_task(
                self._monitor_position_fill()
            )

        except Exception as e:
            logger.info(f"{RED}Entry failed: {e}{RESET}")
            notify_error(self.symbol, str(e), "Entry failed")
            self._signal.trades_today = max(0, self._signal.trades_today - 1)
            self._direction = None
            self._state = _State.SCANNING

    # ------------------------------------------------------------------
    # User data stream handler
    # ------------------------------------------------------------------

    async def _on_user_data(self, event) -> None:
        """Handle real-time account events from the user data stream."""
        from binance_sdk_derivatives_trading_usds_futures.websocket_streams.models import AccountUpdate, OrderTradeUpdate
        actual = getattr(event, "actual_instance", None)
        if isinstance(actual, OrderTradeUpdate) and actual.o:
            o = actual.o
            if (o.s == self.symbol and o.X == "FILLED" and
                    o.ot == "MARKET" and o.R is True and
                    self._state == _State.IN_POSITION):
                self._last_close_reason = "Manual (UI da Binance)"
            return
        if not isinstance(actual, AccountUpdate) or not actual.a or not actual.a.P:
            return
        for pos in actual.a.P:
            if pos.s != self.symbol:
                continue
            pa = float(pos.pa)
            up = float(pos.up)
            if pa == 0.0 and self._state == _State.IN_POSITION:
                close_reason = getattr(self, '_last_close_reason', 'SL/TP')
                self._last_close_reason = 'SL/TP'
                logger.info(
                    f"{YELLOW}[UserData] Position closed for {self.symbol} "
                    f"({close_reason}){RESET}"
                )
                if self._monitor_task and not self._monitor_task.done():
                    self._monitor_task.cancel()
                self._emit({"type": "position_closed", "symbol": self.symbol,
                            "strategy": "pullback", "reason": close_reason})
                notify_position_closed(
                    self.symbol,
                    self._direction or "",
                    self._entry_price,
                    reason=close_reason,
                )
                self._direction = None
                if self._signal.traded_today:
                    self._state = _State.COOLDOWN
                else:
                    self._signal.reset_signal()
                    self._state = _State.SCANNING
                _registry.update(self._reg_key, {
                    "state": self._state.name, "direction": None,
                    "entry_price": None, "sl_price": None, "tp_price": None,
                    "position_qty": 0, "unrealized_pnl": 0,
                })
            elif pa != 0.0 and self._state == _State.IN_POSITION:
                _registry.update(self._reg_key, {"unrealized_pnl": round(up, 4)})

    # Position monitoring
    # ------------------------------------------------------------------

    async def _monitor_position_fill(self):
        prefix = "[DRY-RUN] " if self.dry_run else ""
        try:
            while self._state == _State.IN_POSITION:
                await asyncio.sleep(30)
                if self.dry_run:
                    continue
                try:
                    pos = self._get_position()
                except Exception as e:
                    logger.info(f"{YELLOW}Position poll error: {e}{RESET}")
                    continue
                if pos is None or pos["position_amt"] == 0:
                    logger.info(
                        f"{YELLOW}{prefix}Position closed (SL/TP filled) | "
                        f"trades today: {self._signal.trades_today}/{self._signal.max_trades_per_day}{RESET}"
                    )
                    self._emit({"type": "position_closed", "symbol": self.symbol,
                                "strategy": "pullback", "reason": "SL/TP"})
                    notify_position_closed(
                        self.symbol,
                        self._direction or "",
                        self._entry_price,
                        reason="SL/TP",
                    )
                    self._direction = None
                    if self._signal.traded_today:
                        self._state = _State.COOLDOWN
                    else:
                        self._signal.reset_signal()
                        self._state = _State.SCANNING
                    _registry.update(self._reg_key, {
                        "state": self._state.name, "direction": None,
                        "entry_price": None, "sl_price": None, "tp_price": None,
                        "position_qty": 0, "unrealized_pnl": 0,
                    })
                    return
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # EOD close
    # ------------------------------------------------------------------

    async def _eod_close(self, reason: str | None = None):
        prefix = "[DRY-RUN] " if self.dry_run else ""
        if reason:
            logger.info(f"{BOLD}{prefix}{reason} — closing position{RESET}")
        else:
            logger.info(f"{BOLD}{prefix}EOD close triggered ({self.eod_min // 60:02d}:{self.eod_min % 60:02d} UTC){RESET}")

        if self._state != _State.IN_POSITION:
            logger.info(f"{prefix}No position to close at EOD")
            self._state = _State.COOLDOWN
            return

        if self.dry_run:
            logger.info(
                f"{prefix}Simulating EOD close of {self._fmt_qty(self._position_qty)} "
                f"{self.symbol} ({self._direction})"
            )
            self._state = _State.COOLDOWN
            self._direction = None
            return

        from binance_common.errors import BadRequestError

        try:
            try:
                self._client.rest_api.cancel_all_open_orders(symbol=self.symbol)
            except BadRequestError:
                pass
            try:
                self._client.rest_api.cancel_all_algo_open_orders(symbol=self.symbol)
            except BadRequestError:
                pass
            logger.info("All orders cancelled")

            pos = self._get_position()
            if pos is None or pos["position_amt"] == 0:
                logger.info(f"{YELLOW}Position already closed{RESET}")
                self._state = _State.COOLDOWN
                self._direction = None
                return

            qty = self._fmt_qty(abs(pos["position_amt"]))
            close_side = "SELL" if self._direction == "long" else "BUY"

            close_resp = self._client.rest_api.new_order(
                symbol=self.symbol,
                side=close_side,
                type="MARKET",
                quantity=qty,
                reduce_only="true",
                new_order_resp_type="RESULT",
            )
            close_data = close_resp.data()
            avg_price = float(close_data.avg_price) if close_data.avg_price else 0

            if self._direction == "long":
                pnl = (avg_price - self._entry_price) * self._position_qty
            else:
                pnl = (self._entry_price - avg_price) * self._position_qty

            color = GREEN if pnl >= 0 else RED
            logger.info(
                f"{color}EOD closed {qty} {self.symbol} ({self._direction}) "
                f"@ ${avg_price:.{self._price_decimals}f} | P&L: ${pnl:+.2f}{RESET}"
            )
            notify_eod_close(
                self.symbol, self._direction or "", self._entry_price, avg_price, pnl
            )

        except BadRequestError as e:
            logger.info(f"{YELLOW}EOD close: position already closed ({e}){RESET}")
        except Exception as e:
            logger.info(f"{RED}EOD close error: {e}{RESET}")

        self._state = _State.COOLDOWN
        self._direction = None

        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
