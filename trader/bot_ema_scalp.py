"""EMAScalp bot — R-multiple trailing stop, no fixed TP.

Bidirectional EMA crossover bot. Uses V2 trailing SL logic with configurable
be_r and trail_step parameters.
"""

import asyncio
import collections
import logging
import math
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
    SYMBOL_CONFIGS,
)
from trader.strategy_ema_scalp import EMAScalpSignal
from trader import events as _events, bot_registry as _registry
from trader.notifications import (
    notify_bot_started, notify_bot_stopped, notify_signal, notify_position_opened,
    notify_position_closed, notify_eod_close, notify_error, notify_cooldown,
    notify_startup_error, notify_stop_loss_updated,
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

logger = logging.getLogger("trader.ema_scalp")


class _State(Enum):
    SCANNING = auto()
    IN_POSITION = auto()
    COOLDOWN = auto()


class EMAScalpBot:
    """EMA crossover bot with R-multiple trailing stop (no fixed TP)."""

    def __init__(
        self,
        symbol: str,
        leverage: int = DEFAULT_LEVERAGE,
        capital: float | None = None,
        dry_run: bool = False,
        sl_pct: float = 0.3,
        fast_period: int = 8,
        slow_period: int = 21,
        vol_filter: bool = True,
        max_trades_per_day: int = 10,
        entry_start_min: int = 0,
        entry_cutoff_min: int = 1380,
        eod_min: int = 1430,
        pos_size_pct: float = 0.20,
        be_r: float = 2.0,
        trail_step: float = 0.5,
        be_profit_usd: float = 0.50,
        interval: str = "1m",
        price_decimals: int | None = None,
        qty_decimals: int | None = None,
        time_stop_minutes: int = 20,
        time_stop_min_progress_pct: float = 0.0,
        adverse_exit_bars: int = 3,
        adverse_body_min_pct: float = 0.20,
    ):
        self.symbol = symbol.upper()
        self.leverage = leverage
        self.capital = capital
        self.dry_run = dry_run
        self.sl_pct = sl_pct
        self.eod_min = eod_min
        self.pos_size_pct = pos_size_pct
        self.be_r = be_r
        self.trail_step = trail_step
        self.be_profit_usd = max(0.0, float(be_profit_usd))
        self.min_notional = 5.0
        self.interval = interval
        self._ws_reconnect_delay_sec = 5
        self._ws_stale_after_sec = self._compute_ws_stale_after_sec(interval)
        self._last_closed_candle_monotonic = time.monotonic()

        self._price_decimals = 4
        self._qty_decimals = 3
        self._qty_step = 0.001
        self._precision_from_db = False

        if price_decimals is not None and qty_decimals is not None:
            self._price_decimals = price_decimals
            self._qty_decimals = qty_decimals
            self._qty_step = 10 ** (-qty_decimals) if qty_decimals > 0 else 1
            self._precision_from_db = True
        elif self.symbol in SYMBOL_CONFIGS:
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

        self._signal = EMAScalpSignal(
            fast_period=fast_period,
            slow_period=slow_period,
            vol_filter=vol_filter,
            max_trades_per_day=max_trades_per_day,
            entry_start_min=entry_start_min,
            entry_cutoff_min=entry_cutoff_min,
        )
        self._vol_history: collections.deque[float] = collections.deque(maxlen=20)
        self._state = _State.SCANNING
        self._current_day = -1

        # Position tracking
        self._direction: str | None = None
        self._entry_price = 0.0
        self._position_qty: float = 0.0
        self._sl_price = 0.0

        # Trailing SL state
        self._r_value: float = 0.0
        self._sl_milestone: int = 0
        self._entry_ts_ms: int | None = None
        self._adverse_count = 0
        self._risk_exit_pending = False
        self._be_triggered = False
        self.time_stop_minutes = time_stop_minutes
        self.time_stop_min_progress_pct = time_stop_min_progress_pct
        self.adverse_exit_bars = adverse_exit_bars
        self.adverse_body_min_pct = adverse_body_min_pct

        # Background tasks
        self._eod_task: asyncio.Task | None = None
        self._monitor_task: asyncio.Task | None = None
        self._uds_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None

        self._reg_key = f"{self.symbol}:ema_scalp"

    def _emit(self, event: dict) -> None:
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

    def _position_guard_reason(
        self,
        candle_open_ms: int,
        o: float,
        c: float,
        pnl_pct: float,
        direction: str,
    ) -> str | None:
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
        adverse_candle = (
            (direction == "long" and c < o) or
            (direction == "short" and c > o)
        ) and body_pct >= self.adverse_body_min_pct
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
        log_file = LOG_DIR / f"ema_scalp_{self.symbol}_{date_str}.log"

        class StripAnsiFormatter(logging.Formatter):
            def format(self, record):
                result = super().format(record)
                return ansi_re.sub("", result)

        bot_logger = logging.getLogger(f"trader.ema_scalp.{self.symbol}")
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
        if self._precision_from_db:
            return
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
                        self.min_notional = float(notional)
                return
            raise SystemExit(f"Symbol '{self.symbol}' not found on Binance USDT-M Futures.")
        except SystemExit:
            raise
        except Exception as e:
            logger.info(f"{YELLOW}Could not fetch exchange precision: {e} — using defaults{RESET}")

    # ------------------------------------------------------------------
    # Indicator seeding (warm up fast + slow EMAs)
    # ------------------------------------------------------------------

    def _seed_indicators(self):
        import json
        import urllib.request

        limit = min(self._signal.slow_period + 100, 1500)
        url = (
            f"https://fapi.binance.com/fapi/v1/klines"
            f"?symbol={self.symbol}&interval=1m&limit={limit}"
        )
        logger.info(f"Seeding EMAs from last {limit} historical 1m candles...")

        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                klines = json.loads(resp.read())
        except Exception as e:
            logger.info(f"{YELLOW}Could not fetch historical klines: {e} — EMAs will warm up from live candles{RESET}")
            return

        if not klines:
            return

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        seeded = 0
        for k in klines:
            close_time_ms = int(k[6])
            if close_time_ms > now_ms:
                continue
            c = float(k[4])
            v = float(k[5])
            open_time_ms = int(k[0])
            day_ordinal = open_time_ms // 86_400_000

            self._signal.fast_ema.update(c)
            self._signal.slow_ema.update(c)
            self._vol_history.append(v)
            seeded += 1
            if self._current_day == -1:
                self._current_day = day_ordinal

        # Set prev values for cross detection on first live candle
        self._signal._prev_fast = self._signal.fast_ema.value
        self._signal._prev_slow = self._signal.slow_ema.value

        f = self._signal.fast_ema.value
        s = self._signal.slow_ema.value
        pd = self._price_decimals
        if f is not None and s is not None:
            logger.info(
                f"EMA({self._signal.fast_period})={f:.{pd}f} "
                f"EMA({self._signal.slow_period})={s:.{pd}f} "
                f"(seeded from {seeded} candles)"
            )
        else:
            remaining = self._signal.slow_period - self._signal.slow_ema._count
            logger.info(
                f"{YELLOW}EMAs partially seeded ({seeded} candles). "
                f"Needs {remaining} more before trading.{RESET}"
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
        self._mark_position_opened()

        if self._direction == "long":
            self._sl_price = self._round_price(self._entry_price * (1 - self.sl_pct / 100))
        else:
            self._sl_price = self._round_price(self._entry_price * (1 + self.sl_pct / 100))

        self._r_value = abs(self._entry_price - self._sl_price)
        self._sl_milestone = 0

        self._signal.mark_traded()
        logger.info(
            f"{YELLOW}Resuming existing {self._direction.upper()}: "
            f"{self._position_qty} {self.symbol} @ ${self._entry_price:.{self._price_decimals}f} | "
            f"SL ${self._sl_price} | R=${self._r_value:.{self._price_decimals}f} (trailing){RESET}"
        )
        _registry.update(self._reg_key, {
            "symbol": self.symbol,
            "strategy": "ema_scalp",
            "state": self._state.name,
            "direction": self._direction,
            "entry_price": self._entry_price,
            "sl_price": self._sl_price,
            "tp_price": self._tp_price or None,
            "position_qty": self._position_qty,
            "unrealized_pnl": round(pos["unrealized_profit"], 4),
            "dry_run": self.dry_run,
        })
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
                if (t.side == "SELL" and not t.buyer) or (t.side == "BUY" and t.buyer):
                    self._signal.mark_traded()
            if self._signal.trades_today > 0:
                logger.info(
                    f"{YELLOW}Found {self._signal.trades_today} trade(s) today for "
                    f"{self.symbol} (limit: {self._signal.max_trades_per_day}){RESET}"
                )
            if self._signal.traded_today:
                self._state = _State.COOLDOWN
                logger.info(f"{YELLOW}Daily limit reached — entering COOLDOWN{RESET}")
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

    def _is_sl_at_or_better_than_entry(self) -> bool:
        if self._entry_price <= 0 or self._sl_price <= 0 or not self._direction:
            return False
        if self._direction == "long":
            return self._sl_price >= self._entry_price
        return self._sl_price <= self._entry_price

    async def _move_sl_to_breakeven(self, reason: str) -> bool:
        if self._entry_price <= 0 or self._position_qty <= 0 or not self._direction:
            return False
        if self._is_sl_at_or_better_than_entry():
            self._be_triggered = True
            self._sl_milestone = max(self._sl_milestone, 2)
            return True

        old_sl = self._sl_price
        old_milestone = self._sl_milestone
        new_sl = self._safe_trigger_price(self._entry_price)
        close_side = "SELL" if self._direction == "long" else "BUY"
        self._sl_price = new_sl
        self._sl_milestone = max(self._sl_milestone, 2)

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
                self._client.rest_api.cancel_all_algo_open_orders(symbol=self.symbol)
            except BadRequestError:
                pass

            self._client.rest_api.new_algo_order(
                algo_type="CONDITIONAL",
                symbol=self.symbol,
                side=close_side,
                type="STOP_MARKET",
                trigger_price=new_sl,
                close_position="true",
            )
            _registry.update(self._reg_key, {"sl_price": new_sl})
            notify_stop_loss_updated(
                self.symbol,
                self._direction,
                old_sl,
                new_sl,
                reason,
            )
            self._be_triggered = True
            return True
        except Exception as e:
            logger.info(f"{RED}Failed to move SL to breakeven: {e}{RESET}")
            self._sl_price = old_sl
            self._sl_milestone = old_milestone
            return False

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    async def run(self):
        self._setup_logging()
        prefix = "[DRY-RUN] " if self.dry_run else ""

        logger.info(f"{BOLD}{prefix}EMAScalp Bot (Trailing SL) — {self.symbol}{RESET}")
        logger.info(
            f"Leverage: {self.leverage}x | "
            f"Interval: {self.interval} | "
            f"SL: {self.sl_pct}% (trailing R-multiple) | "
            f"Auto BE: +${self.be_profit_usd:.2f} | "
            f"EMA: fast={self._signal.fast_period} slow={self._signal.slow_period} | "
            f"Position size: {self.pos_size_pct * 100:.0f}% | "
            f"Max trades/day: {self._signal.max_trades_per_day} | "
            f"WS stale watchdog: {self._ws_stale_after_sec}s"
        )
        logger.info("-" * 60)

        if not self.dry_run:
            self._fetch_exchange_precision()

        self._seed_indicators()
        self._check_startup_position()
        self._resolve_capital()

        per_trade = self.capital * self.pos_size_pct
        logger.info(f"Capital: ${self.capital:.2f} | Per-trade: ${per_trade:.2f}")
        if per_trade < self.min_notional:
            min_cap = math.ceil(self.min_notional / self.pos_size_pct * 100) / 100
            msg = (
                f"Per-trade capital ${per_trade:.2f} is below Binance minimum "
                f"notional ${self.min_notional:.2f} for {self.symbol}. "
                f"Minimum --capital is ${min_cap:.2f}."
            )
            if not self.dry_run:
                notify_startup_error(
                    symbol=self.symbol,
                    strategy="EMAScalp",
                    interval=self.interval,
                    leverage=self.leverage,
                    pos_size_pct=self.pos_size_pct,
                    error=msg,
                    stage="pre-trade validation",
                )
            raise SystemExit(
                msg
            )
        logger.info("-" * 60)

        if not self.dry_run:
            notify_bot_started(self.symbol, "EMAScalp", self.interval, self.leverage, self.pos_size_pct)

        _registry.update(self._reg_key, {
            "symbol": self.symbol,
            "strategy": "ema_scalp",
            "config": {
                "leverage": self.leverage,
                "sl_pct": self.sl_pct,
                "pos_size_pct": self.pos_size_pct,
                "fast_period": self._signal.fast_period,
                "slow_period": self._signal.slow_period,
                "max_trades_per_day": self._signal.max_trades_per_day,
                "be_profit_usd": self.be_profit_usd,
                "capital": self.capital,
                "per_trade": per_trade,
            },
            "dry_run": self.dry_run,
        })
        self._heartbeat_task = asyncio.create_task(
            _registry.heartbeat_loop(
                self._reg_key,
                {"symbol": self.symbol, "strategy": "ema_scalp", "dry_run": self.dry_run},
            )
        )

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
                        symbol=self.symbol.lower(), interval=self.interval
                    )
                    stream.on("message", self._on_kline)
                    self._last_closed_candle_monotonic = time.monotonic()
                    logger.info(f"Subscribed to {self.symbol.lower()}@kline_{self.interval} (futures)")
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
                notify_bot_stopped(self.symbol, "EMAScalp")
            logger.info("Connection closed. Goodbye.")

    # ------------------------------------------------------------------
    # Kline callback
    # ------------------------------------------------------------------

    def _on_kline(self, data):
        k = data.k
        if not k.x:
            return
        self._last_closed_candle_monotonic = time.monotonic()

        o, h, l, c, v = float(k.o), float(k.h), float(k.l), float(k.c), float(k.v)
        candle_open_ms = int(k.t)
        day_ordinal = candle_open_ms // 86_400_000
        minute_of_day = (candle_open_ms % 86_400_000) // 60_000
        ts = datetime.fromtimestamp(candle_open_ms / 1000, tz=timezone.utc).strftime("%H:%M")

        self._check_daily_reset(day_ordinal)
        self._vol_history.append(v)
        vol_sma = sum(self._vol_history) / len(self._vol_history)

        prefix = "[DRY-RUN] " if self.dry_run else ""
        pd = self._price_decimals
        fast = self._signal.fast_ema.value
        slow = self._signal.slow_ema.value

        fast_label = f"{fast:.{pd}f}" if fast is not None else f"warm({self._signal.fast_ema._count}/{self._signal.fast_period})"
        slow_label = f"{slow:.{pd}f}" if slow is not None else f"warm({self._signal.slow_ema._count}/{self._signal.slow_period})"

        if self._state == _State.SCANNING:
            signal = self._signal.on_candle(c, minute_of_day, volume=v, vol_sma20=vol_sma)
            _registry.update(self._reg_key, {
                "symbol": self.symbol, "strategy": "ema_scalp",
                "state": self._state.name, "price": c,
                "fast_ema": fast, "slow_ema": slow,
                "trades_today": self._signal.trades_today,
                "max_trades_per_day": self._signal.max_trades_per_day,
                "dry_run": self.dry_run,
            })
            logger.info(
                f"{prefix}[{ts}] C={c:.{pd}f} | "
                f"EMA{self._signal.fast_period}={fast_label} "
                f"EMA{self._signal.slow_period}={slow_label} | "
                f"trades={self._signal.trades_today}/{self._signal.max_trades_per_day} | SCANNING"
            )
            if signal == "ENTER_LONG":
                logger.info(f"{BOLD}{GREEN}{prefix}SIGNAL: ENTER_LONG @ {c:.{pd}f}{RESET}")
                if not self.dry_run:
                    notify_signal(self.symbol, "long", round(c, self._price_decimals), "EMAScalp")
                self._emit({"type": "signal", "symbol": self.symbol, "strategy": "ema_scalp",
                            "direction": "long", "price": c, "ts": ts})
                asyncio.get_event_loop().create_task(self._enter_position("long", c))
            elif signal == "ENTER_SHORT":
                logger.info(f"{BOLD}{RED}{prefix}SIGNAL: ENTER_SHORT @ {c:.{pd}f}{RESET}")
                if not self.dry_run:
                    notify_signal(self.symbol, "short", round(c, self._price_decimals), "EMAScalp")
                self._emit({"type": "signal", "symbol": self.symbol, "strategy": "ema_scalp",
                            "direction": "short", "price": c, "ts": ts})
                asyncio.get_event_loop().create_task(self._enter_position("short", c))

        elif self._state == _State.IN_POSITION:
            if self._direction == "long":
                pnl = (c - self._entry_price) * self._position_qty
                pnl_pct = ((c - self._entry_price) / self._entry_price) * 100
            else:
                pnl = (self._entry_price - c) * self._position_qty
                pnl_pct = ((self._entry_price - c) / self._entry_price) * 100
            if (
                not self._be_triggered
                and self.be_profit_usd > 0
                and pnl >= self.be_profit_usd
            ):
                asyncio.get_event_loop().create_task(
                    self._move_sl_to_breakeven(
                        f"Auto BE +${self.be_profit_usd:.2f}"
                    )
                )

            self._check_trailing_sl(h, l)

            color = GREEN if pnl >= 0 else RED
            guard_reason = self._position_guard_reason(candle_open_ms, o, c, pnl_pct, self._direction or "short")
            if guard_reason:
                self._risk_exit_pending = True
                self._last_close_reason = guard_reason
                logger.info(f"{YELLOW}Early exit: {guard_reason}{RESET}")
                asyncio.get_event_loop().create_task(self._eod_close())
                return

            r_achieved = (
                ((h - self._entry_price) / self._r_value) if self._r_value > 0 and self._direction == "long"
                else ((self._entry_price - l) / self._r_value) if self._r_value > 0
                else 0.0
            )

            _registry.update(self._reg_key, {
                "state": self._state.name, "price": c,
                "unrealized_pnl": round(pnl, 4), "unrealized_pnl_pct": round(pnl_pct, 4),
                "direction": self._direction,
                "entry_price": self._entry_price, "sl_price": self._sl_price,
                "tp_price": None, "position_qty": self._position_qty,
            })
            logger.info(
                f"{prefix}[{ts}] C={c:.{pd}f} | "
                f"P&L: {color}${pnl:+.2f} ({pnl_pct:+.2f}%){RESET} | "
                f"SL=${self._sl_price} (M{self._sl_milestone}) | "
                f"R={r_achieved:.2f} | {self._direction.upper()} IN_POSITION"
            )

        elif self._state == _State.COOLDOWN:
            logger.info(f"{prefix}[{ts}] C={c:.{pd}f} | COOLDOWN")
            _registry.update(self._reg_key, {"state": "COOLDOWN", "price": c})

    # ------------------------------------------------------------------
    # Trailing SL logic (configurable be_r + trail_step)
    # ------------------------------------------------------------------

    def _milestone_sl_delta(self, best_r: float) -> tuple[int, float]:
        R = self._r_value
        half_be = self.be_r / 2.0
        if best_r < half_be:
            return 0, -R
        if best_r < self.be_r:
            return 1, -0.5 * R
        steps = int((best_r - self.be_r) / self.trail_step)
        return 2 + steps, steps * self.trail_step * R

    def _check_trailing_sl(self, high: float, low: float):
        if self._r_value <= 0 or not self._direction:
            return

        if self._direction == "long":
            best_r = (high - self._entry_price) / self._r_value
        else:
            best_r = (self._entry_price - low) / self._r_value

        if best_r <= 0:
            return

        new_milestone, sl_delta = self._milestone_sl_delta(best_r)

        if new_milestone <= self._sl_milestone:
            return

        if self._direction == "long":
            new_sl_price = self._round_price(self._entry_price + sl_delta)
        else:
            new_sl_price = self._round_price(self._entry_price - sl_delta)

        old_sl = self._sl_price
        self._sl_milestone = new_milestone
        self._sl_price = new_sl_price

        if new_milestone == 0:
            label = "Original"
        elif new_milestone == 1:
            label = "-0.5R"
        elif new_milestone == 2:
            label = "Breakeven"
        else:
            trailing_r = (new_milestone - 2) * self.trail_step
            label = f"+{trailing_r:.1f}R"

        logger.info(
            f"{YELLOW}Trailing SL → milestone {new_milestone} ({label}) | "
            f"best_r={best_r:.2f}R | "
            f"${old_sl:.{self._price_decimals}f} → ${new_sl_price:.{self._price_decimals}f}{RESET}"
        )

        if self.dry_run:
            _registry.update(self._reg_key, {"sl_price": new_sl_price})
            return

        try:
            from binance_common.errors import BadRequestError
            try:
                self._client.rest_api.cancel_all_algo_open_orders(symbol=self.symbol)
            except BadRequestError:
                pass

            sl_side = "SELL" if self._direction == "long" else "BUY"
            sl_resp = self._client.rest_api.new_algo_order(
                algo_type="CONDITIONAL",
                symbol=self.symbol,
                side=sl_side,
                type="STOP_MARKET",
                trigger_price=new_sl_price,
                close_position="true",
            )
            logger.info(
                f"{YELLOW}New SL algo order placed @ ${new_sl_price} | "
                f"Algo ID: {sl_resp.data().algo_id}{RESET}"
            )
            _registry.update(self._reg_key, {"sl_price": new_sl_price})
            notify_stop_loss_updated(
                self.symbol,
                self._direction or "",
                old_sl,
                new_sl_price,
                f"Trailing SL ({label})",
            )

        except Exception as e:
            logger.info(f"{RED}Failed to update trailing SL: {e} — keeping old SL{RESET}")
            self._sl_price = old_sl
            self._sl_milestone = new_milestone - 1

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
        self._signal.reset_daily()
        self._vol_history.clear()
        if self._state == _State.COOLDOWN:
            self._state = _State.SCANNING
            self._reset_position_guard()
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

        notional = qty * entry_price
        if notional < self.min_notional:
            logger.info(
                f"{RED}Order notional ${notional:.2f} < min ${self.min_notional:.2f} — skipping entry{RESET}"
            )
            return

        side = "BUY" if direction == "long" else "SELL"
        sl_price = (
            self._safe_trigger_price(entry_price * (1 - self.sl_pct / 100))
            if direction == "long"
            else self._safe_trigger_price(entry_price * (1 + self.sl_pct / 100))
        )

        logger.info(
            f"{prefix}Entering {direction.upper()}: {self._fmt_qty(qty)} {self.symbol} "
            f"@ ~${entry_price:.{self._price_decimals}f}"
        )

        if self.dry_run:
            self._direction = direction
            self._entry_price = entry_price
            self._position_qty = qty
            self._mark_position_opened()
            self._sl_price = sl_price
            self._r_value = abs(entry_price - sl_price)
            self._sl_milestone = 0
            self._state = _State.IN_POSITION
            color = GREEN if direction == "long" else RED
            logger.info(
                f"{color}{prefix}{direction.upper()} opened | "
                f"Entry: ${self._entry_price:.{self._price_decimals}f} | "
                f"SL: ${self._sl_price} | R=${self._r_value:.{self._price_decimals}f} (trailing){RESET}"
            )
            self._emit({"type": "order", "symbol": self.symbol, "strategy": "ema_scalp",
                        "direction": direction, "entry_price": entry_price,
                        "qty": qty, "sl_price": sl_price, "tp_price": None,
                        "dry_run": True})
            _registry.update(self._reg_key, {
                "state": self._state.name, "direction": direction,
                "entry_price": entry_price, "sl_price": sl_price, "tp_price": None,
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
            avg_price = self._resolve_avg_fill_price(order_data, entry_price)
            executed_qty = self._positive_float(getattr(order_data, "executed_qty", None)) or qty
            if executed_qty <= 0:
                raise RuntimeError(f"Invalid executed quantity for {self.symbol}: {executed_qty}")

            self._direction = direction
            self._entry_price = avg_price
            self._position_qty = executed_qty
            self._mark_position_opened()
            self._sl_price = (
                self._safe_trigger_price(avg_price * (1 - self.sl_pct / 100))
                if direction == "long"
                else self._safe_trigger_price(avg_price * (1 + self.sl_pct / 100))
            )
            self._r_value = abs(avg_price - self._sl_price)
            self._sl_milestone = 0

            color = GREEN if direction == "long" else RED
            logger.info(
                f"{color}{side} {executed_qty} {self.symbol} @ ${avg_price:.{self._price_decimals}f} | "
                f"Order ID: {order_data.order_id}{RESET}"
            )

            sl_side = "SELL" if direction == "long" else "BUY"
            sl_resp = self._client.rest_api.new_algo_order(
                algo_type="CONDITIONAL",
                symbol=self.symbol,
                side=sl_side,
                type="STOP_MARKET",
                trigger_price=self._sl_price,
                close_position="true",
            )
            logger.info(
                f"{color}SL placed @ ${self._sl_price} (R=${self._r_value:.{self._price_decimals}f}) | "
                f"Algo ID: {sl_resp.data().algo_id}{RESET}"
            )

            self._state = _State.IN_POSITION
            logger.info(
                f"{BOLD}{direction.upper()} opened | Entry: ${avg_price:.{self._price_decimals}f} | "
                f"SL: ${self._sl_price} | Trailing R-multiple active{RESET}"
            )
            notify_position_opened(
                self.symbol, direction, avg_price,
                self._sl_price, None, executed_qty, self.leverage,
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
            self._reset_position_guard()

    # ------------------------------------------------------------------
    # Position monitoring
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
                            "strategy": "ema_scalp", "reason": close_reason})
                notify_position_closed(
                    self.symbol,
                    self._direction or "",
                    self._entry_price,
                    reason=close_reason,
                )
                self._direction = None
                self._r_value = 0.0
                self._sl_milestone = 0
                if self._signal.traded_today:
                    self._state = _State.COOLDOWN
                else:
                    self._signal.reset_signal()
                    self._state = _State.SCANNING
                self._reset_position_guard()
                _registry.update(self._reg_key, {
                    "state": self._state.name, "direction": None,
                    "entry_price": None, "sl_price": None, "tp_price": None,
                    "position_qty": 0, "unrealized_pnl": 0,
                })
            elif pa != 0.0 and self._state == _State.IN_POSITION:
                _registry.update(self._reg_key, {"unrealized_pnl": round(up, 4)})

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
                        f"{YELLOW}{prefix}Position closed (trailing SL filled) | "
                        f"trades today: {self._signal.trades_today}/{self._signal.max_trades_per_day}{RESET}"
                    )
                    self._emit({"type": "position_closed", "symbol": self.symbol,
                                "strategy": "ema_scalp", "reason": "Trailing SL"})
                    notify_position_closed(
                        self.symbol,
                        self._direction or "",
                        self._entry_price,
                        reason="SL/TP",
                    )
                    self._direction = None
                    self._r_value = 0.0
                    self._sl_milestone = 0
                    if self._signal.traded_today:
                        self._state = _State.COOLDOWN
                    else:
                        self._signal.reset_signal()
                        self._state = _State.SCANNING
                    self._reset_position_guard()
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

    async def _eod_close(self):
        prefix = "[DRY-RUN] " if self.dry_run else ""
        logger.info(f"{BOLD}{prefix}EOD close triggered ({self.eod_min // 60:02d}:{self.eod_min % 60:02d} UTC){RESET}")

        if self._state != _State.IN_POSITION:
            logger.info(f"{prefix}No position to close at EOD")
            self._state = _State.COOLDOWN
            self._reset_position_guard()
            return

        if self.dry_run:
            logger.info(
                f"{prefix}Simulating EOD close of {self._fmt_qty(self._position_qty)} "
                f"{self.symbol} ({self._direction})"
            )
            self._state = _State.COOLDOWN
            self._reset_position_guard()
            self._direction = None
            self._r_value = 0.0
            self._sl_milestone = 0
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
                self._reset_position_guard()
                self._direction = None
                self._r_value = 0.0
                self._sl_milestone = 0
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
        self._reset_position_guard()
        self._direction = None
        self._r_value = 0.0
        self._sl_milestone = 0

        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
