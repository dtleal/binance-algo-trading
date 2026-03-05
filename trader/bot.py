"""MomShort live trading bot — WS kline stream → strategy → order execution."""

import asyncio
import collections
import logging
import math
import os
import re
import sys
import time
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
from trader.notifications import (
    notify_bot_started, notify_bot_stopped, notify_signal, notify_position_opened,
    notify_position_closed, notify_eod_close, notify_error, notify_cooldown,
    notify_startup_error, notify_stop_loss_updated,
)

def _parse_proxy(url: str) -> dict | None:
    """Parse 'socks5://host:port' into the SDK proxy dict format."""
    if not url:
        return None
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if not parsed.hostname or not parsed.port:
        raise SystemExit(f"Invalid SOCKS_PROXY format: '{url}'. Expected 'socks5://host:port'")
    return {"protocol": parsed.scheme, "host": parsed.hostname, "port": parsed.port}


def _decimals_from_step(step_str: str) -> int:
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
        be_profit_usd: float = 0.50,
        time_stop_minutes: int = 20,
        time_stop_min_progress_pct: float = 0.0,
        adverse_exit_bars: int = 3,
        adverse_body_min_pct: float = 0.20,
    ):
        self.cfg = cfg
        self.symbol = cfg.symbol
        self.asset = cfg.asset
        self.leverage = leverage
        self.capital = capital
        self.dry_run = dry_run
        self.be_profit_usd = max(0.0, float(be_profit_usd))

        # Precision (runtime-adjusted from exchange info at startup)
        self._price_decimals = cfg.price_decimals
        self._qty_decimals = cfg.qty_decimals
        self._qty_step = 10 ** (-cfg.qty_decimals) if cfg.qty_decimals > 0 else 1.0
        self._ws_reconnect_delay_sec = 5
        self._ws_stale_after_sec = self._compute_ws_stale_after_sec(cfg.interval)
        self._last_closed_candle_monotonic = time.monotonic()

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
        self._entry_ts_ms: int | None = None
        self._adverse_count = 0
        self._risk_exit_pending = False
        self._be_triggered = False
        self.time_stop_minutes = time_stop_minutes
        self.time_stop_min_progress_pct = time_stop_min_progress_pct
        self.adverse_exit_bars = adverse_exit_bars
        self.adverse_body_min_pct = adverse_body_min_pct
        self._prefer_maker = os.getenv("PREFER_MAKER_EXECUTION", "1").strip().lower() not in {"0", "false", "no", "off"}
        self._maker_price_offset_pct = max(0.0, float(os.getenv("MAKER_PRICE_OFFSET_PCT", "0.0002")))
        self._maker_entry_timeout_sec = max(0.5, float(os.getenv("MAKER_ENTRY_TIMEOUT_SEC", "8")))
        self._maker_exit_timeout_sec = max(0.5, float(os.getenv("MAKER_EXIT_TIMEOUT_SEC", "6")))
        self._maker_poll_interval_sec = max(0.1, float(os.getenv("MAKER_POLL_INTERVAL_SEC", "0.4")))

        # Background tasks
        self._eod_task: asyncio.Task | None = None
        self._monitor_task: asyncio.Task | None = None
        self._uds_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None

        # Dashboard integration
        self._reg_key = f"{self.symbol}:momshort"

    def _emit(self, event: dict) -> None:
        """Fire-and-forget event publish to the dashboard WebSocket bus."""
        try:
            asyncio.get_event_loop().create_task(_events.publish(event))
        except RuntimeError:
            pass

    def _mark_position_opened(self) -> None:
        self._entry_ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        self._adverse_count = 0
        self._risk_exit_pending = False
        self._be_triggered = False

    def _reset_position_guard(self) -> None:
        self._entry_ts_ms = None
        self._adverse_count = 0
        self._risk_exit_pending = False
        self._be_triggered = False

    def _maker_limit_price(self, reference_price: float, side: str) -> float:
        if side.upper() == "BUY":
            raw = reference_price * (1 - self._maker_price_offset_pct)
        else:
            raw = reference_price * (1 + self._maker_price_offset_pct)
        return self._safe_trigger_price(raw)

    async def _wait_for_position_open(self, timeout_sec: float) -> dict | None:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            pos = self._get_position()
            if pos and pos["position_amt"] < 0:
                return pos
            await asyncio.sleep(self._maker_poll_interval_sec)
        pos = self._get_position()
        if pos and pos["position_amt"] < 0:
            return pos
        return None

    async def _wait_for_position_closed(self, timeout_sec: float) -> bool:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            pos = self._get_position()
            if pos is None or pos["position_amt"] == 0:
                return True
            await asyncio.sleep(self._maker_poll_interval_sec)
        pos = self._get_position()
        return pos is None or pos["position_amt"] == 0

    @staticmethod
    def _is_post_only_reject(error: Exception) -> bool:
        msg = str(error)
        return (
            "-5022" in msg
            or "Post Only order will be rejected" in msg
            or "could not be executed as maker" in msg
        )

    def _position_guard_reason(self, candle_open_ms: int, o: float, c: float, pnl_pct: float) -> str | None:
        if self._risk_exit_pending:
            return None

        if self._entry_ts_ms and self.time_stop_minutes > 0:
            elapsed_min = (candle_open_ms - self._entry_ts_ms) / 60_000
            if elapsed_min >= self.time_stop_minutes and pnl_pct <= self.time_stop_min_progress_pct:
                return (
                    f"Time stop ({self.time_stop_minutes}m): "
                    f"PnL {pnl_pct:+.2f}% <= {self.time_stop_min_progress_pct:+.2f}%"
                )

        body_pct = (abs(c - o) / o * 100) if o else 0.0
        adverse_candle = c > o and body_pct >= self.adverse_body_min_pct
        self._adverse_count = self._adverse_count + 1 if adverse_candle else 0
        if (
            self.adverse_exit_bars > 0
            and self._adverse_count >= self.adverse_exit_bars
            and pnl_pct < 0
        ):
            return (
                f"Adverse momentum: {self._adverse_count} candles contra "
                f"(body>={self.adverse_body_min_pct:.2f}%, PnL {pnl_pct:+.2f}%)"
            )
        return None

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
        factor = 10 ** self._price_decimals
        return math.floor(price * factor) / factor

    def _round_qty(self, qty: float) -> float:
        if self._qty_decimals == 0:
            return float(int(math.floor(qty)))
        return math.floor(qty / self._qty_step) * self._qty_step

    def _fmt_qty(self, qty: float) -> str:
        if self._qty_decimals == 0:
            return str(int(qty))
        return f"{qty:.{self._qty_decimals}f}"

    def _fmt_price(self, price: float) -> str:
        return f"{price:.{self._price_decimals}f}"

    @staticmethod
    def _get_filter_field(filter_obj, camel_name: str, snake_name: str | None = None):
        if isinstance(filter_obj, dict):
            if camel_name in filter_obj:
                return filter_obj.get(camel_name)
            if snake_name:
                return filter_obj.get(snake_name)
            return None
        if hasattr(filter_obj, camel_name):
            return getattr(filter_obj, camel_name)
        if snake_name and hasattr(filter_obj, snake_name):
            return getattr(filter_obj, snake_name)
        return None

    @staticmethod
    def _positive_float(value) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if parsed > 0:
            return parsed
        return None

    def _safe_trigger_price(self, raw_price: float) -> float:
        rounded = self._round_price(raw_price)
        if rounded > 0:
            return rounded
        min_tick = 10 ** (-self._price_decimals) if self._price_decimals > 0 else 1.0
        return min_tick

    def _resolve_avg_fill_price(self, order_data, fallback_price: float) -> float:
        avg_price = self._positive_float(getattr(order_data, "avg_price", None))
        if avg_price:
            return avg_price
        cum_quote = self._positive_float(getattr(order_data, "cum_quote", None))
        executed_qty = self._positive_float(getattr(order_data, "executed_qty", None))
        if cum_quote and executed_qty:
            computed = cum_quote / executed_qty
            if computed > 0:
                return computed
        fallback = self._positive_float(fallback_price)
        if fallback:
            return fallback
        raise RuntimeError(f"Could not resolve a positive fill price for {self.symbol}")

    @staticmethod
    def _interval_seconds(interval: str) -> int:
        if not interval:
            return 60
        unit = interval[-1].lower()
        try:
            value = int(interval[:-1])
        except ValueError:
            return 60
        if unit == "m":
            return value * 60
        if unit == "h":
            return value * 3600
        if unit == "d":
            return value * 86400
        return 60

    @classmethod
    def _compute_ws_stale_after_sec(cls, interval: str) -> int:
        base = cls._interval_seconds(interval)
        return max(180, int(base * 3 + 90))

    def _fetch_exchange_precision(self):
        """Refresh precision/min notional from Binance filters for the current symbol."""
        try:
            info = self._client.rest_api.exchange_information()
            for sym in info.data().symbols:
                if sym.symbol != self.symbol:
                    continue
                for f in sym.filters:
                    f_type = self._get_filter_field(f, "filterType", "filter_type")
                    if f_type == "PRICE_FILTER":
                        tick_size = self._get_filter_field(f, "tickSize", "tick_size")
                        if tick_size:
                            self._price_decimals = _decimals_from_step(str(tick_size))
                    elif f_type == "LOT_SIZE":
                        step_size = self._get_filter_field(f, "stepSize", "step_size")
                        if not step_size:
                            continue
                        self._qty_decimals = _decimals_from_step(str(step_size))
                        step = float(step_size)
                        self._qty_step = step if step > 0 else 1.0
                    elif f_type == "MIN_NOTIONAL":
                        notional = self._get_filter_field(f, "notional")
                        if not notional:
                            continue
                        self.cfg = self.cfg.__class__(**{
                            **self.cfg.__dict__,
                            "min_notional": float(notional),
                        })
                logger.info(
                    f"Exchange precision for {self.symbol}: "
                    f"price_decimals={self._price_decimals}, "
                    f"qty_decimals={self._qty_decimals}"
                )
                return
            logger.info(
                f"{YELLOW}Symbol '{self.symbol}' not found in exchange info; "
                f"using configured precision values{RESET}"
            )
        except Exception as e:
            logger.info(
                f"{YELLOW}Could not fetch exchange precision: {e} — using configured defaults{RESET}"
            )

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
            self._mark_position_opened()
            self._sl_price = self._round_price(self._entry_price * (1 + self.cfg.sl_pct / 100))
            self._tp_price = self._round_price(self._entry_price * (1 - self.cfg.tp_pct / 100))
            self._signal.mark_traded()
            logger.info(
                f"{YELLOW}Resuming with existing short: "
                f"{self._position_qty} {self.asset} @ ${self._entry_price:.4f} | "
                f"SL ${self._sl_price} | TP ${self._tp_price}{RESET}"
            )
            _registry.update(self._reg_key, {
                "symbol": self.symbol,
                "strategy": "momshort",
                "state": self._state.name,
                "direction": "SHORT",
                "entry_price": self._entry_price,
                "sl_price": self._sl_price,
                "tp_price": self._tp_price,
                "position_qty": self._position_qty,
                "price": pos["mark_price"],
                "unrealized_pnl": round(pos["unrealized_profit"], 4),
                "dry_run": self.dry_run,
            })
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

    def _is_sl_at_or_better_than_entry(self) -> bool:
        # For shorts, "better" SL means lower or equal than entry.
        return self._sl_price > 0 and self._entry_price > 0 and self._sl_price <= self._entry_price

    async def _move_sl_to_breakeven(self, reason: str) -> bool:
        if self._entry_price <= 0 or self._position_qty <= 0:
            return False
        if self._is_sl_at_or_better_than_entry():
            self._be_triggered = True
            return True

        old_sl = self._sl_price
        new_sl = self._safe_trigger_price(self._entry_price)
        self._sl_price = new_sl

        if self.dry_run:
            logger.info(
                f"{YELLOW}[DRY-RUN] Auto BE: SL ${old_sl} → ${new_sl}{RESET}"
            )
            _registry.update(self._reg_key, {"sl_price": new_sl})
            self._be_triggered = True
            return True

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

            # Re-place SL at entry (breakeven) and re-place TP unchanged.
            self._client.rest_api.new_algo_order(
                algo_type="CONDITIONAL",
                symbol=self.symbol,
                side="BUY",
                type="STOP_MARKET",
                trigger_price=self._fmt_price(new_sl),
                close_position="true",
            )
            if self._tp_price > 0:
                try:
                    self._client.rest_api.new_order(
                        symbol=self.symbol,
                        side="BUY",
                        type="LIMIT",
                        time_in_force="GTX",
                        price=self._fmt_price(self._tp_price),
                        quantity=self._fmt_qty(self._position_qty),
                        reduce_only="true",
                    )
                except Exception as tp_err:
                    if self._is_post_only_reject(tp_err):
                        logger.info(
                            f"{YELLOW}BE TP post-only rejected @ ${self._tp_price} "
                            f"— retry LIMIT GTC{RESET}"
                        )
                        self._client.rest_api.new_order(
                            symbol=self.symbol,
                            side="BUY",
                            type="LIMIT",
                            time_in_force="GTC",
                            price=self._fmt_price(self._tp_price),
                            quantity=self._fmt_qty(self._position_qty),
                            reduce_only="true",
                        )
                    else:
                        raise
            _registry.update(self._reg_key, {"sl_price": new_sl, "tp_price": self._tp_price})
            notify_stop_loss_updated(self.symbol, "short", old_sl, new_sl, reason)
            self._be_triggered = True
            return True
        except Exception as e:
            logger.info(f"{RED}Failed to move SL to breakeven: {e}{RESET}")
            self._sl_price = old_sl
            return False

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
            f"Auto BE: +${self.be_profit_usd:.2f} | "
            f"Position size: {self.cfg.pos_size_pct * 100:.0f}% | "
            f"WS stale watchdog: {self._ws_stale_after_sec}s | "
            f"Maker mode: {'ON' if self._prefer_maker else 'OFF'}"
            f"{vwap_dist_label}"
        )
        logger.info("-" * 60)

        if not self.dry_run:
            self._fetch_exchange_precision()

        self._check_startup_position()
        self._resolve_capital()

        per_trade = self.capital * self.cfg.pos_size_pct
        logger.info(f"Capital: ${self.capital:.2f} | Per-trade: ${per_trade:.2f}")
        if per_trade < self.cfg.min_notional:
            min_capital = math.ceil(self.cfg.min_notional / self.cfg.pos_size_pct * 100) / 100
            msg = (
                f"Per-trade capital ${per_trade:.2f} is below Binance minimum "
                f"notional ${self.cfg.min_notional:.2f} for {self.symbol}. "
                f"Minimum --capital is ${min_capital:.2f} "
                f"(at {self.cfg.pos_size_pct * 100:.0f}% position size)."
            )
            if not self.dry_run:
                notify_startup_error(
                    symbol=self.symbol,
                    strategy="MomShort",
                    interval=self.cfg.interval,
                    leverage=self.leverage,
                    pos_size_pct=self.cfg.pos_size_pct,
                    error=msg,
                    stage="pre-trade validation",
                )
            raise SystemExit(
                msg
            )
        logger.info("-" * 60)
        if not self.dry_run:
            notify_bot_started(self.symbol, "MomShort", self.cfg.interval, self.leverage, self.cfg.pos_size_pct)

        # Publish bot configuration to registry
        _registry.update(self._reg_key, {
            "symbol": self.symbol,
            "strategy": "momshort",
            "config": {
                "leverage": self.leverage,
                "tp_pct": self.cfg.tp_pct,
                "sl_pct": self.cfg.sl_pct,
                "pos_size_pct": self.cfg.pos_size_pct,
                "be_profit_usd": self.be_profit_usd,
                "min_notional": self.cfg.min_notional,
                "capital": self.capital,
                "per_trade": per_trade,
            },
            "dry_run": self.dry_run,
        })
        self._heartbeat_task = asyncio.create_task(
            _registry.heartbeat_loop(
                self._reg_key,
                {"symbol": self.symbol, "strategy": "momshort", "dry_run": self.dry_run},
            )
        )

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
            if not self.dry_run:
                from trader.user_data_stream import UserDataStream
                uds = UserDataStream(self._client, self._ws_factory, self._ws_url, self._ConfigWS)
                uds.register(self._on_user_data)
                self._uds_task = asyncio.create_task(uds.run())
            while True:
                connection = None
                stream = None
                try:
                    connection = await ws_client.websocket_streams.create_connection()
                    stream = await connection.kline_candlestick_streams(
                        symbol=self.symbol.lower(), interval=self.cfg.interval
                    )
                    stream.on("message", self._on_kline)
                    self._last_closed_candle_monotonic = time.monotonic()
                    logger.info(f"Subscribed to {self.symbol.lower()}@kline_{self.cfg.interval} (futures)")
                    logger.info(f"State: {self._state.name} | Waiting for candles...")
                    logger.info("-" * 60)
                    while True:
                        await asyncio.sleep(1)
                        silence_sec = time.monotonic() - self._last_closed_candle_monotonic
                        if silence_sec > self._ws_stale_after_sec:
                            raise RuntimeError(
                                f"No closed candle for {silence_sec:.0f}s "
                                f"(limit {self._ws_stale_after_sec}s)"
                            )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.info(
                        f"{YELLOW}Kline stream issue ({e}) — reconnecting in "
                        f"{self._ws_reconnect_delay_sec}s{RESET}"
                    )
                    await asyncio.sleep(self._ws_reconnect_delay_sec)
                finally:
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

        except asyncio.CancelledError:
            logger.info("\nBot shutting down...")
        finally:
            if self._uds_task and not self._uds_task.done():
                self._uds_task.cancel()
            if self._eod_task and not self._eod_task.done():
                self._eod_task.cancel()
            if self._monitor_task and not self._monitor_task.done():
                self._monitor_task.cancel()
            if self._heartbeat_task and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()
            if not self.dry_run:
                notify_bot_stopped(self.symbol, "MomShort")
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
        self._last_closed_candle_monotonic = time.monotonic()

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
                if not self.dry_run:
                    notify_signal(self.symbol, "short", round(c, 4), "MomShort")
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
            if (
                not self._be_triggered
                and self.be_profit_usd > 0
                and total_pnl >= self.be_profit_usd
            ):
                asyncio.get_event_loop().create_task(
                    self._move_sl_to_breakeven(
                        f"Auto BE +${self.be_profit_usd:.2f}"
                    )
                )
            guard_reason = self._position_guard_reason(candle_open_ms, o, c, pnl_pct)
            if guard_reason:
                self._risk_exit_pending = True
                self._last_close_reason = guard_reason
                logger.info(f"{YELLOW}Early exit: {guard_reason}{RESET}")
                asyncio.get_event_loop().create_task(self._eod_close(reason=guard_reason))
                return
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
            self._mark_position_opened()
            self._sl_price = self._safe_trigger_price(entry_price * (1 + self.cfg.sl_pct / 100))
            self._tp_price = self._safe_trigger_price(entry_price * (1 - self.cfg.tp_pct / 100))
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

            avg_price = entry_price
            executed_qty = 0.0
            execution_mode = "market"
            order_id = None

            if self._prefer_maker:
                maker_price = self._maker_limit_price(entry_price, "SELL")
                try:
                    maker_resp = self._client.rest_api.new_order(
                        symbol=self.symbol,
                        side="SELL",
                        type="LIMIT",
                        time_in_force="GTX",
                        price=self._fmt_price(maker_price),
                        quantity=self._fmt_qty(qty),
                        new_order_resp_type="RESULT",
                    )
                except Exception as maker_err:
                    if self._is_post_only_reject(maker_err):
                        logger.info(
                            f"{YELLOW}Maker entry rejected by Post-Only rule "
                            f"(@ ${maker_price:.4f}) — fallback MARKET{RESET}"
                        )
                    else:
                        raise
                else:
                    maker_data = maker_resp.data()
                    order_id = getattr(maker_data, "order_id", None)
                    logger.info(
                        f"{CYAN}Maker entry posted @ ${maker_price:.4f} "
                        f"(timeout {self._maker_entry_timeout_sec:.1f}s){RESET}"
                    )
                    pos = await self._wait_for_position_open(self._maker_entry_timeout_sec)
                    if pos:
                        execution_mode = "maker"
                        avg_price = float(pos["entry_price"])
                        executed_qty = abs(float(pos["position_amt"]))
                    else:
                        try:
                            self._client.rest_api.cancel_all_open_orders(symbol=self.symbol)
                        except Exception:
                            pass
                        logger.info(f"{YELLOW}Maker entry timeout — fallback MARKET{RESET}")

            if executed_qty <= 0:
                sell_resp = self._client.rest_api.new_order(
                    symbol=self.symbol,
                    side="SELL",
                    type="MARKET",
                    quantity=self._fmt_qty(qty),
                    new_order_resp_type="RESULT",
                )
                sell_data = sell_resp.data()
                avg_price = self._resolve_avg_fill_price(sell_data, entry_price)
                executed_qty = self._positive_float(getattr(sell_data, "executed_qty", None)) or qty
                order_id = getattr(sell_data, "order_id", None)
                if executed_qty <= 0:
                    raise RuntimeError(f"Invalid executed quantity for {self.symbol}: {executed_qty}")

            self._entry_price = avg_price
            self._position_qty = executed_qty
            self._mark_position_opened()

            logger.info(
                f"{GREEN}SOLD {executed_qty} {self.asset} @ ${avg_price:.4f} | "
                f"Order ID: {order_id} | exec={execution_mode.upper()}{RESET}"
            )

            # Recalculate SL/TP from actual fill
            self._sl_price = self._safe_trigger_price(avg_price * (1 + self.cfg.sl_pct / 100))
            self._tp_price = self._safe_trigger_price(avg_price * (1 - self.cfg.tp_pct / 100))

            # Place stop-loss (STOP_MARKET)
            sl_resp = self._client.rest_api.new_algo_order(
                algo_type="CONDITIONAL",
                symbol=self.symbol,
                side="BUY",
                type="STOP_MARKET",
                trigger_price=self._fmt_price(self._sl_price),
                close_position="true",
            )
            logger.info(
                f"{GREEN}SL placed @ ${self._sl_price} | "
                f"Algo ID: {sl_resp.data().algo_id}{RESET}"
            )

            # Place take-profit as maker reduce-only limit.
            tp_tif = "GTX"
            try:
                tp_resp = self._client.rest_api.new_order(
                    symbol=self.symbol,
                    side="BUY",
                    type="LIMIT",
                    time_in_force=tp_tif,
                    price=self._fmt_price(self._tp_price),
                    quantity=self._fmt_qty(executed_qty),
                    reduce_only="true",
                    new_order_resp_type="RESULT",
                )
            except Exception as tp_err:
                if self._is_post_only_reject(tp_err):
                    tp_tif = "GTC"
                    logger.info(
                        f"{YELLOW}TP post-only rejected @ ${self._tp_price} "
                        f"— retry LIMIT {tp_tif}{RESET}"
                    )
                    tp_resp = self._client.rest_api.new_order(
                        symbol=self.symbol,
                        side="BUY",
                        type="LIMIT",
                        time_in_force=tp_tif,
                        price=self._fmt_price(self._tp_price),
                        quantity=self._fmt_qty(executed_qty),
                        reduce_only="true",
                        new_order_resp_type="RESULT",
                    )
                else:
                    raise
            tp_data = tp_resp.data()
            logger.info(
                f"{GREEN}TP placed @ ${self._tp_price} | "
                f"TIF: {tp_tif} | Order ID: {getattr(tp_data, 'order_id', None)}{RESET}"
            )

            self._state = _State.IN_POSITION
            logger.info(
                f"{BOLD}Short opened | Entry: ${avg_price:.4f} | "
                f"SL: ${self._sl_price} | TP: ${self._tp_price}{RESET}"
            )
            notify_position_opened(
                self.symbol, "short", avg_price,
                self._sl_price, self._tp_price, executed_qty, self.leverage,
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
            notify_error(self.symbol, str(e), "Entry failed")
            # Don't burn the daily trade on a failed order
            self._signal.traded_today = False
            self._state = _State.SCANNING
            self._reset_position_guard()

    # ------------------------------------------------------------------
    # Position monitoring (poll for SL/TP fill)
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
                            "strategy": "momshort", "reason": close_reason})
                notify_position_closed(
                    self.symbol, "short", self._entry_price, reason=close_reason
                )
                self._signal.traded_today = False
                self._state = _State.SCANNING
                self._reset_position_guard()
                _registry.update(self._reg_key, {
                    "state": self._state.name, "direction": None,
                    "entry_price": None, "sl_price": None, "tp_price": None,
                })
            elif pa != 0.0 and self._state == _State.IN_POSITION:
                _registry.update(self._reg_key, {"unrealized_pnl": round(up, 4)})

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
                    notify_position_closed(
                        self.symbol, "short", self._entry_price, reason="SL/TP"
                    )
                    # Go back to SCANNING so the bot can re-enter if a new signal forms.
                    # Also reset traded_today so a manual close doesn't permanently block
                    # the bot for the rest of the day.
                    self._signal.traded_today = False
                    self._state = _State.SCANNING
                    self._reset_position_guard()
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
            self._reset_position_guard()
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
                self._reset_position_guard()
                return

            qty = abs(pos["position_amt"])
            close_mode = "market"
            avg_price = 0.0

            if self._prefer_maker:
                ref_price = float(pos.get("mark_price", 0.0)) if isinstance(pos, dict) else 0.0
                if ref_price <= 0:
                    ref_price = self._entry_price
                maker_close_price = self._maker_limit_price(ref_price, "BUY")
                try:
                    self._client.rest_api.new_order(
                        symbol=self.symbol,
                        side="BUY",
                        type="LIMIT",
                        time_in_force="GTX",
                        price=self._fmt_price(maker_close_price),
                        quantity=self._fmt_qty(qty),
                        reduce_only="true",
                        new_order_resp_type="RESULT",
                    )
                except Exception as maker_err:
                    if self._is_post_only_reject(maker_err):
                        logger.info(
                            f"{YELLOW}EOD maker close rejected by Post-Only rule "
                            f"(@ ${maker_close_price:.4f}) — fallback MARKET{RESET}"
                        )
                    else:
                        raise
                else:
                    logger.info(
                        f"{CYAN}EOD maker close posted @ ${maker_close_price:.4f} "
                        f"(timeout {self._maker_exit_timeout_sec:.1f}s){RESET}"
                    )
                    if await self._wait_for_position_closed(self._maker_exit_timeout_sec):
                        close_mode = "maker"
                        avg_price = maker_close_price
                    else:
                        try:
                            self._client.rest_api.cancel_all_open_orders(symbol=self.symbol)
                        except Exception:
                            pass
                        logger.info(f"{YELLOW}EOD maker timeout — fallback MARKET{RESET}")

            if close_mode != "maker":
                pos = self._get_position()
                if pos is not None and pos["position_amt"] != 0:
                    qty = abs(pos["position_amt"])
                    close_resp = self._client.rest_api.new_order(
                        symbol=self.symbol,
                        side="BUY",
                        type="MARKET",
                        quantity=self._fmt_qty(qty),
                        reduce_only="true",
                        new_order_resp_type="RESULT",
                    )
                    close_data = close_resp.data()
                    avg_price = float(close_data.avg_price) if close_data.avg_price else 0
            pnl = (self._entry_price - avg_price) * self._position_qty if avg_price else 0

            color = GREEN if pnl >= 0 else RED
            logger.info(
                f"{color}EOD closed {qty} {self.asset} @ ${avg_price:.4f} | "
                f"P&L: ${pnl:+.2f} | exec={close_mode.upper()}{RESET}"
            )
            self._emit({"type": "position_closed", "symbol": self.symbol,
                       "reason": "EOD", "pnl": pnl})
            notify_eod_close(self.symbol, "short", self._entry_price, avg_price, pnl)
        except BadRequestError as e:
            # Position was already closed by SL/TP fill
            logger.info(f"{YELLOW}EOD close: position already closed ({e}){RESET}")
        except Exception as e:
            logger.info(f"{RED}EOD close error: {e}{RESET}")

        self._state = _State.COOLDOWN
        self._reset_position_guard()
        _registry.update(self._reg_key, {
            "state": self._state.name, "direction": None,
            "entry_price": None, "sl_price": None, "tp_price": None,
        })

        # Cancel monitor task
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
