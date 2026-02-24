"""MomShort live trading bot — WS kline stream → strategy → order execution."""

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
    AXS_CONFIG,
    SymbolConfig,
)
from trader.strategy import VWAPTracker, MomShortSignal
from trader import events as _events, bot_registry as _registry

def _parse_proxy(url: str) -> dict | None:
    """Parse 'socks5://host:port' into the SDK proxy dict format."""
    if not url:
        return None
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if not parsed.hostname or not parsed.port:
        raise SystemExit(f"Invalid SOCKS_PROXY format: '{url}'. Expected 'socks5://host:port'")
    return {"protocol": parsed.scheme, "host": parsed.hostname, "port": parsed.port}


GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

logger = logging.getLogger("trader.bot")


class _State(Enum):
    SCANNING = auto()
    IN_POSITION = auto()
    COOLDOWN = auto()


class MomShortBot:
    """Automated MomShort trading bot for USDT-M futures."""

    def __init__(
        self,
        cfg: SymbolConfig = AXS_CONFIG,
        leverage: int = DEFAULT_LEVERAGE,
        capital: float | None = None,
        dry_run: bool = False,
    ):
        self.cfg = cfg
        self.symbol = cfg.symbol
        self.asset = cfg.asset
        self.leverage = leverage
        self.capital = capital
        self.dry_run = dry_run

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

        self._vwap = VWAPTracker()
        self._signal = MomShortSignal(
            min_bars=cfg.min_bars,
            confirm_bars=cfg.confirm_bars,
            vwap_prox=cfg.vwap_prox,
            entry_start_min=cfg.entry_start_min,
            entry_cutoff_min=cfg.entry_cutoff_min,
            vol_filter=cfg.vol_filter,
        )
        self._vol_history: collections.deque[float] = collections.deque(maxlen=20)
        self._state = _State.SCANNING
        self._current_day = -1

        # Position tracking
        self._entry_price = 0.0
        self._position_qty = 0.0
        self._sl_price = 0.0
        self._tp_price = 0.0

        # Background tasks
        self._eod_task: asyncio.Task | None = None
        self._monitor_task: asyncio.Task | None = None

        # Dashboard integration
        self._reg_key = f"{self.symbol}:momshort"

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
        log_file = LOG_DIR / f"bot_{self.symbol}_{date_str}.log"

        class StripAnsiFormatter(logging.Formatter):
            def format(self, record):
                result = super().format(record)
                return ansi_re.sub("", result)

        bot_logger = logging.getLogger(f"trader.bot.{self.symbol}")
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
    # Helpers (same rounding as short.py)
    # ------------------------------------------------------------------

    def _round_price(self, price: float) -> float:
        factor = 10 ** self.cfg.price_decimals
        return math.floor(price * factor) / factor

    def _round_qty(self, qty: float) -> int:
        return int(math.floor(qty))

    # ------------------------------------------------------------------
    # REST helpers
    # ------------------------------------------------------------------

    def _get_position(self) -> dict | None:
        resp = self._client.rest_api.position_information_v3(symbol=self.symbol)
        for pos in resp.data():
            if float(pos.position_amt) != 0:
                return {
                    "position_amt": float(pos.position_amt),
                    "entry_price": float(pos.entry_price),
                    "unrealized_profit": float(pos.un_realized_profit),
                    "mark_price": float(pos.mark_price),
                }
        return None

    # ------------------------------------------------------------------
    # Startup checks
    # ------------------------------------------------------------------

    def _check_startup_position(self):
        """If already in a short, resume IN_POSITION state."""
        if self.dry_run:
            return
        pos = self._get_position()
        if pos and pos["position_amt"] < 0:
            self._state = _State.IN_POSITION
            self._entry_price = pos["entry_price"]
            self._position_qty = abs(pos["position_amt"])
            self._sl_price = self._round_price(self._entry_price * (1 + self.cfg.sl_pct / 100))
            self._tp_price = self._round_price(self._entry_price * (1 - self.cfg.tp_pct / 100))
            self._signal.mark_traded()
            logger.info(
                f"{YELLOW}Resuming with existing short: "
                f"{self._position_qty} {self.asset} @ ${self._entry_price:.4f} | "
                f"SL ${self._sl_price} | TP ${self._tp_price}{RESET}"
            )
            # Start fill monitor so we detect SL/TP closure
            self._monitor_task = asyncio.get_event_loop().create_task(
                self._monitor_position_fill()
            )
            return

        # No open position — check if we already traded today
        self._check_traded_today()

    def _check_traded_today(self):
        """Query Binance trade history to see if we already traded today."""
        now = datetime.now(timezone.utc)
        start_of_day_ms = int(
            now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000
        )
        try:
            resp = self._client.rest_api.account_trade_list(
                symbol=self.symbol, start_time=start_of_day_ms
            )
            trades = resp.data()
            if not trades:
                return
            # Any SELL trade today means we already opened a short.
            # Log the info but do NOT enter COOLDOWN — the position is already
            # closed (no open position was found), so the bot starts in SCANNING
            # and is ready to re-engage on a fresh signal.
            for t in trades:
                if t.side == "SELL" and not t.buyer:
                    logger.info(
                        f"{YELLOW}Traded {self.symbol} today already, "
                        f"but no open position — resuming SCANNING{RESET}"
                    )
                    return
        except Exception as e:
            logger.info(f"{YELLOW}Could not check trade history: {e}{RESET}")

    def _resolve_capital(self):
        """Resolve trading capital from account balance if not specified."""
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

        logger.info(f"{BOLD}{prefix}MomShort Bot — {self.symbol}{RESET}")
        vwap_dist_label = f" | VWAP dist stop: {self.cfg.vwap_dist_stop*100:.0f}%" if self.cfg.vwap_dist_stop > 0 else ""
        logger.info(
            f"Leverage: {self.leverage}x | "
            f"Interval: {self.cfg.interval} | "
            f"TP: {self.cfg.tp_pct}% | SL: {self.cfg.sl_pct}% | "
            f"Position size: {self.cfg.pos_size_pct * 100:.0f}%"
            f"{vwap_dist_label}"
        )
        logger.info("-" * 60)

        self._check_startup_position()
        self._resolve_capital()

        per_trade = self.capital * self.cfg.pos_size_pct
        logger.info(f"Capital: ${self.capital:.2f} | Per-trade: ${per_trade:.2f}")
        if per_trade < self.cfg.min_notional:
            min_capital = math.ceil(self.cfg.min_notional / self.cfg.pos_size_pct * 100) / 100
            raise SystemExit(
                f"Per-trade capital ${per_trade:.2f} is below Binance minimum "
                f"notional ${self.cfg.min_notional:.2f} for {self.symbol}. "
                f"Minimum --capital is ${min_capital:.2f} "
                f"(at {self.cfg.pos_size_pct * 100:.0f}% position size)."
            )
        logger.info("-" * 60)

        # Publish bot configuration to registry
        _registry.update(self._reg_key, {
            "symbol": self.symbol,
            "strategy": "momshort",
            "config": {
                "leverage": self.leverage,
                "tp_pct": self.cfg.tp_pct,
                "sl_pct": self.cfg.sl_pct,
                "pos_size_pct": self.cfg.pos_size_pct,
                "min_notional": self.cfg.min_notional,
                "capital": self.capital,
                "per_trade": per_trade,
            },
            "dry_run": self.dry_run,
        })

        # Schedule EOD timer
        self._schedule_eod()

        # Connect to futures kline WebSocket
        if self.dry_run:
            # In dry-run, still connect to WS for live candles (read-only)
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

        # Monkey-patch: aiohttp needs ProxyConnector for SOCKS5 proxies.
        # The SDK creates a plain ClientSession, so we inject one with the connector.
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
                symbol=self.symbol.lower(), interval=self.cfg.interval
            )
            stream.on("message", self._on_kline)
            logger.info(f"Subscribed to {self.symbol.lower()}@kline_{self.cfg.interval} (futures)")
            logger.info(f"State: {self._state.name} | Waiting for candles...")
            logger.info("-" * 60)

            while True:
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("\nBot shutting down...")
        finally:
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
            logger.info("Connection closed. Goodbye.")

    # ------------------------------------------------------------------
    # Kline callback
    # ------------------------------------------------------------------

    def _on_kline(self, data):
        """WS callback — fires every ~250ms per kline update."""
        k = data.k

        # Only process closed candles
        if not k.x:
            return

        # Parse OHLCV
        o, h, l, c, v = float(k.o), float(k.h), float(k.l), float(k.c), float(k.v)
        candle_open_ms = int(k.t)
        day_ordinal = candle_open_ms // 86_400_000
        minute_of_day = (candle_open_ms % 86_400_000) // 60_000

        ts = datetime.fromtimestamp(candle_open_ms / 1000, tz=timezone.utc).strftime("%H:%M")

        # Daily reset check
        self._check_daily_reset(day_ordinal)

        # Update VWAP
        vwap = self._vwap.update(h, l, c, v, day_ordinal)

        prefix = "[DRY-RUN] " if self.dry_run else ""

        # Track volume for SMA(20)
        self._vol_history.append(v)
        vol_sma = sum(self._vol_history) / len(self._vol_history)

        if self._state == _State.SCANNING:
            signal = self._signal.on_candle(c, vwap, minute_of_day,
                                            volume=v, vol_sma20=vol_sma)
            state_info = (
                f"cnt={self._signal.counter}"
                if not self._signal.confirming
                else f"confirm={self._signal.confirm_count}/{self._signal.confirm_bars}"
            )
            logger.info(
                f"{prefix}[{ts}] C={c:.4f} VWAP={vwap:.4f} | "
                f"{state_info} | {self._state.name}"
            )
            # Update dashboard
            _registry.update(self._reg_key, {
                "symbol": self.symbol, "strategy": "momshort",
                "state": self._state.name, "price": c, "vwap": vwap,
                "signal_state": state_info,
            })
            # Publish candle event for real-time frontend updates
            self._emit({
                "type": "candle", "symbol": self.symbol, "strategy": "momshort",
                "ts": ts, "price": c, "vwap": vwap, "ema": None, "trend": "",
                "state": self._state.name, "counter": self._signal.counter,
                "confirming": self._signal.confirming,
            })
            if signal == "ENTER_SHORT":
                logger.info(
                    f"{BOLD}{GREEN}{prefix}SIGNAL: ENTER_SHORT @ {c:.4f}{RESET}"
                )
                self._emit({"type": "signal", "symbol": self.symbol,
                           "strategy": "momshort", "direction": "SHORT",
                           "price": c, "timestamp": ts})
                asyncio.get_event_loop().create_task(self._enter_short(c))

        elif self._state == _State.IN_POSITION:
            # VWAP distance stop: SHORT exits if price > vwap*(1+dist)
            if self.cfg.vwap_dist_stop > 0.0 and vwap > 0.0:
                dist = (c - vwap) / vwap
                if dist > self.cfg.vwap_dist_stop:
                    logger.info(
                        f"{YELLOW}VWAP dist stop: {dist*100:+.2f}% from VWAP "
                        f"(threshold +{self.cfg.vwap_dist_stop*100:.0f}%) — closing short{RESET}"
                    )
                    if self.dry_run:
                        self._state = _State.COOLDOWN
                        _registry.update(self._reg_key, {"state": self._state.name})
                    else:
                        asyncio.get_event_loop().create_task(
                            self._eod_close(reason="VWAP dist stop")
                        )
                    return

            pnl_per_unit = self._entry_price - c
            total_pnl = pnl_per_unit * self._position_qty
            pnl_pct = (pnl_per_unit / self._entry_price) * 100 if self._entry_price else 0
            color = GREEN if total_pnl >= 0 else RED
            logger.info(
                f"{prefix}[{ts}] C={c:.4f} VWAP={vwap:.4f} | "
                f"P&L: {color}${total_pnl:+.2f} ({pnl_pct:+.2f}%){RESET} | "
                f"IN_POSITION"
            )
            # Update dashboard with position P&L
            _registry.update(self._reg_key, {
                "state": self._state.name, "price": c, "vwap": vwap,
                "unrealized_pnl": round(total_pnl, 4), "unrealized_pnl_pct": round(pnl_pct, 4),
                "entry_price": self._entry_price, "sl_price": self._sl_price, "tp_price": self._tp_price,
            })
            # Publish candle event with live P&L
            self._emit({
                "type": "candle", "symbol": self.symbol, "strategy": "momshort",
                "ts": ts, "price": c, "vwap": vwap, "ema": None, "trend": "",
                "state": self._state.name,
                "unrealized_pnl": round(total_pnl, 4),
                "unrealized_pnl_pct": round(pnl_pct, 4),
            })

        elif self._state == _State.COOLDOWN:
            logger.info(
                f"{prefix}[{ts}] C={c:.4f} VWAP={vwap:.4f} | COOLDOWN"
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
            # First candle after startup — just sync the day, don't reset.
            # Startup checks already set the correct state/signal flags.
            return
        logger.info(f"{BOLD}--- New UTC day (ordinal {day_ordinal}) — resetting ---{RESET}")
        self._vwap.reset()
        self._signal.reset_daily()
        self._vol_history.clear()
        if self._state == _State.COOLDOWN:
            self._state = _State.SCANNING
        self._schedule_eod()

    def _schedule_eod(self):
        """Schedule (or reschedule) the EOD close task for 23:50 UTC today."""
        if self._eod_task and not self._eod_task.done():
            self._eod_task.cancel()
        self._eod_task = asyncio.get_event_loop().create_task(self._eod_timer())

    async def _eod_timer(self):
        """Sleep until 23:50 UTC, then trigger EOD close."""
        now = datetime.now(timezone.utc)
        eod_hour = self.cfg.eod_min // 60
        eod_minute = self.cfg.eod_min % 60
        target = now.replace(hour=eod_hour, minute=eod_minute, second=0, microsecond=0)
        if target <= now:
            # Already past EOD for today — schedule for next iteration
            return
        delay = (target - now).total_seconds()
        logger.info(f"EOD timer set for {target.strftime('%H:%M')} UTC ({delay:.0f}s from now)")
        await asyncio.sleep(delay)
        await self._eod_close()

    # ------------------------------------------------------------------
    # Order execution
    # ------------------------------------------------------------------

    async def _enter_short(self, entry_price: float):
        """Execute the short entry: market sell + SL + TP algo orders."""
        prefix = "[DRY-RUN] " if self.dry_run else ""
        trade_capital = self.capital * self.cfg.pos_size_pct
        qty = self._round_qty(trade_capital / entry_price)

        if qty <= 0:
            logger.info(f"{RED}Calculated quantity is 0 — skipping entry{RESET}")
            return

        logger.info(f"{prefix}Entering short: {qty} {self.asset} @ ~${entry_price:.4f}")
        logger.info(f"{prefix}Position value: ${qty * entry_price:.2f} | Leverage: {self.leverage}x")

        if self.dry_run:
            self._entry_price = entry_price
            self._position_qty = qty
            self._sl_price = self._round_price(entry_price * (1 + self.cfg.sl_pct / 100))
            self._tp_price = self._round_price(entry_price * (1 - self.cfg.tp_pct / 100))
            self._state = _State.IN_POSITION
            logger.info(
                f"{GREEN}{prefix}Short opened | "
                f"Entry: ${self._entry_price:.4f} | "
                f"SL: ${self._sl_price} | TP: ${self._tp_price}{RESET}"
            )
            # Update dashboard
            self._emit({"type": "order", "symbol": self.symbol, "side": "SHORT",
                       "qty": qty, "sl_price": self._sl_price, "tp_price": self._tp_price,
                       "dry_run": True})
            _registry.update(self._reg_key, {
                "state": self._state.name, "direction": "SHORT",
                "entry_price": entry_price, "sl_price": self._sl_price, "tp_price": self._tp_price,
            })
            return

        try:
            # Set leverage
            self._client.rest_api.change_initial_leverage(
                symbol=self.symbol, leverage=self.leverage
            )

            # Market sell
            sell_resp = self._client.rest_api.new_order(
                symbol=self.symbol,
                side="SELL",
                type="MARKET",
                quantity=qty,
                new_order_resp_type="RESULT",
            )
            sell_data = sell_resp.data()
            avg_price = float(sell_data.avg_price) if sell_data.avg_price else entry_price
            executed_qty = float(sell_data.executed_qty)

            self._entry_price = avg_price
            self._position_qty = executed_qty

            logger.info(
                f"{GREEN}SOLD {executed_qty} {self.asset} @ ${avg_price:.4f} | "
                f"Order ID: {sell_data.order_id}{RESET}"
            )

            # Recalculate SL/TP from actual fill
            self._sl_price = self._round_price(avg_price * (1 + self.cfg.sl_pct / 100))
            self._tp_price = self._round_price(avg_price * (1 - self.cfg.tp_pct / 100))

            # Place stop-loss (STOP_MARKET)
            sl_resp = self._client.rest_api.new_algo_order(
                algo_type="CONDITIONAL",
                symbol=self.symbol,
                side="BUY",
                type="STOP_MARKET",
                trigger_price=self._sl_price,
                close_position="true",
            )
            logger.info(
                f"{GREEN}SL placed @ ${self._sl_price} | "
                f"Algo ID: {sl_resp.data().algo_id}{RESET}"
            )

            # Place take-profit (TAKE_PROFIT_MARKET)
            tp_resp = self._client.rest_api.new_algo_order(
                algo_type="CONDITIONAL",
                symbol=self.symbol,
                side="BUY",
                type="TAKE_PROFIT_MARKET",
                trigger_price=self._tp_price,
                close_position="true",
            )
            logger.info(
                f"{GREEN}TP placed @ ${self._tp_price} | "
                f"Algo ID: {tp_resp.data().algo_id}{RESET}"
            )

            self._state = _State.IN_POSITION
            logger.info(
                f"{BOLD}Short opened | Entry: ${avg_price:.4f} | "
                f"SL: ${self._sl_price} | TP: ${self._tp_price}{RESET}"
            )

            # Update dashboard
            self._emit({"type": "order", "symbol": self.symbol, "side": "SHORT",
                       "qty": executed_qty, "price": avg_price,
                       "sl_price": self._sl_price, "tp_price": self._tp_price})
            _registry.update(self._reg_key, {
                "state": self._state.name, "direction": "SHORT",
                "entry_price": avg_price, "sl_price": self._sl_price, "tp_price": self._tp_price,
            })

            # Start position monitor
            self._monitor_task = asyncio.get_event_loop().create_task(
                self._monitor_position_fill()
            )

        except Exception as e:
            logger.info(f"{RED}Entry failed: {e}{RESET}")
            # Don't burn the daily trade on a failed order
            self._signal.traded_today = False
            self._state = _State.SCANNING

    # ------------------------------------------------------------------
    # Position monitoring (poll for SL/TP fill)
    # ------------------------------------------------------------------

    async def _monitor_position_fill(self):
        """Poll position every 30s to detect server-side SL/TP fills."""
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
                    pnl_info = ""
                    if self._entry_price > 0:
                        try:
                            resp = self._client.rest_api.symbol_price_ticker(symbol=self.symbol)
                            price_data = resp.data()
                            if hasattr(price_data, "actual_instance"):
                                last_price = float(price_data.actual_instance.price)
                            else:
                                last_price = float(price_data.price)
                            pnl = (self._entry_price - last_price) * self._position_qty
                            pnl_info = f" | Est. P&L: ${pnl:+.2f}"
                        except Exception:
                            pass
                    logger.info(
                        f"{YELLOW}{prefix}Position closed externally (SL/TP or manual){pnl_info}{RESET}"
                    )
                    self._emit({"type": "position_closed", "symbol": self.symbol,
                               "reason": "SL/TP", "pnl_info": pnl_info})
                    # Go back to SCANNING so the bot can re-enter if a new signal forms.
                    # Also reset traded_today so a manual close doesn't permanently block
                    # the bot for the rest of the day.
                    self._signal.traded_today = False
                    self._state = _State.SCANNING
                    _registry.update(self._reg_key, {
                        "state": self._state.name, "direction": None,
                        "entry_price": None, "sl_price": None, "tp_price": None,
                    })
                    return
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # EOD close
    # ------------------------------------------------------------------

    async def _eod_close(self, reason: str | None = None):
        """Force-close position at end of day (23:50 UTC), or on VWAP dist stop."""
        prefix = "[DRY-RUN] " if self.dry_run else ""
        if reason:
            logger.info(f"{BOLD}{prefix}{reason} — closing position{RESET}")
        else:
            logger.info(f"{BOLD}{prefix}EOD close triggered (23:50 UTC){RESET}")

        if self._state != _State.IN_POSITION:
            logger.info(f"{prefix}No position to close at EOD (already closed externally)")
            return

        if self.dry_run:
            logger.info(f"{prefix}Simulating EOD close of {self._position_qty} {self.asset}")
            self._emit({"type": "position_closed", "symbol": self.symbol,
                       "reason": "EOD", "dry_run": True})
            self._state = _State.COOLDOWN
            _registry.update(self._reg_key, {
                "state": self._state.name, "direction": None,
                "entry_price": None, "sl_price": None, "tp_price": None,
            })
            return

        from binance_common.errors import BadRequestError

        try:
            # Cancel all open orders (SL/TP)
            try:
                self._client.rest_api.cancel_all_open_orders(symbol=self.symbol)
            except BadRequestError:
                pass
            try:
                self._client.rest_api.cancel_all_algo_open_orders(symbol=self.symbol)
            except BadRequestError:
                pass
            logger.info("All orders cancelled")

            # Verify position still exists
            pos = self._get_position()
            if pos is None or pos["position_amt"] == 0:
                logger.info(f"{YELLOW}Position already closed{RESET}")
                self._state = _State.COOLDOWN
                return

            qty = int(abs(pos["position_amt"]))

            close_resp = self._client.rest_api.new_order(
                symbol=self.symbol,
                side="BUY",
                type="MARKET",
                quantity=qty,
                reduce_only="true",
                new_order_resp_type="RESULT",
            )
            close_data = close_resp.data()
            avg_price = float(close_data.avg_price) if close_data.avg_price else 0
            pnl = (self._entry_price - avg_price) * self._position_qty if avg_price else 0

            color = GREEN if pnl >= 0 else RED
            logger.info(
                f"{color}EOD closed {qty} {self.asset} @ ${avg_price:.4f} | "
                f"P&L: ${pnl:+.2f}{RESET}"
            )
            self._emit({"type": "position_closed", "symbol": self.symbol,
                       "reason": "EOD", "pnl": pnl})
        except BadRequestError as e:
            # Position was already closed by SL/TP fill
            logger.info(f"{YELLOW}EOD close: position already closed ({e}){RESET}")
        except Exception as e:
            logger.info(f"{RED}EOD close error: {e}{RESET}")

        self._state = _State.COOLDOWN
        _registry.update(self._reg_key, {
            "state": self._state.name, "direction": None,
            "entry_price": None, "sl_price": None, "tp_price": None,
        })

        # Cancel monitor task
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
