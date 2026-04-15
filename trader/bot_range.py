"""Range Mode bot — mean-reversion in horizontal ranges on Binance USDT-M Futures.

Each position carries its own TP (TAKE_PROFIT_MARKET) and SL (STOP_MARKET)
placed as conditional orders immediately after the entry MARKET order.

Architecture:
  - Subscribes to 5m (or configured `interval`) kline WebSocket stream.
  - Every closed candle: recalculate ADX/ATR indicators, throttled range detect,
    call RangeSignal.on_candle(), execute the returned signal.
  - MTF ADX confirmation fetched via REST (M15 klines) once per range recalc.
  - Positions tracked in-memory; user-data stream reconciles TP/SL hits.

Ported from GridTradingEA.mq5 as documented in docs/RANGE_MODE.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import sys
import time
import urllib.request
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
from trader.strategy_range import RangeSignal, RangePosition
from trader import events as _events, bot_registry as _registry
from trader.notifications import (
    notify_bot_started,
    notify_bot_stopped,
    notify_position_opened,
    notify_position_closed,
    notify_error,
    notify_startup_error,
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

logger = logging.getLogger("trader.range")

# MTF timeframe used for ADX confirmation (M15 in spec)
MTF_INTERVAL = "15m"
# How many MTF candles to fetch for ADX(14) calculation
MTF_FETCH_LIMIT = 40

# ADX / ATR lookback
ADX_PERIOD = 14
ATR_PERIOD = 14


# ---------------------------------------------------------------------------
# Indicator helpers (pure functions, no I/O)
# ---------------------------------------------------------------------------

def _wilder_smooth(values: list[float], period: int) -> list[float]:
    """Wilder smoothing: ATR_n = ATR_{n-1} * (p-1)/p + v/p"""
    if len(values) < period:
        return []
    init = sum(values[:period]) / period
    result = [init]
    k = 1.0 / period
    for v in values[period:]:
        result.append(result[-1] * (1.0 - k) + v * k)
    return result


def _compute_atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> float:
    """Wilder ATR. Returns ATR of the last bar."""
    if len(highs) < period + 1:
        return 0.0
    trs = [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        for i in range(1, len(highs))
    ]
    sm = _wilder_smooth(trs, period)
    return sm[-1] if sm else 0.0


def _compute_adx(highs: list[float], lows: list[float], closes: list[float], period: int) -> float:
    """Wilder ADX(period). Returns ADX of the last bar or 0.0 if insufficient data."""
    n = len(highs)
    if n < period * 2 + 2:
        return 0.0

    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, n):
        h_diff = highs[i] - highs[i - 1]
        l_diff = lows[i - 1] - lows[i]
        plus_dm.append(h_diff if h_diff > l_diff and h_diff > 0 else 0.0)
        minus_dm.append(l_diff if l_diff > h_diff and l_diff > 0 else 0.0)
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)

    sm_tr    = _wilder_smooth(trs,      period)
    sm_plus  = _wilder_smooth(plus_dm,  period)
    sm_minus = _wilder_smooth(minus_dm, period)

    dx_vals = []
    for i in range(len(sm_tr)):
        t = sm_tr[i]
        if t == 0:
            dx_vals.append(0.0)
            continue
        pdi = 100 * sm_plus[i] / t
        mdi = 100 * sm_minus[i] / t
        dx  = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0.0
        dx_vals.append(dx)

    sm_adx = _wilder_smooth(dx_vals, period)
    return sm_adx[-1] if sm_adx else 0.0


def _fetch_klines_rest(symbol: str, interval: str, limit: int) -> list[list]:
    """Fetch klines from Binance public REST API (no auth required)."""
    url = (
        f"https://fapi.binance.com/fapi/v1/klines"
        f"?symbol={symbol}&interval={interval}&limit={limit}"
    )
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------

class _State(Enum):
    RUNNING = auto()
    PAUSED = auto()
    STOPPED = auto()


class RangeBot:
    """Mean-reversion range trading bot for Binance USDT-M Futures.

    Opens BUY near range bottom and SELL near range top.
    Each position has individual TP (TAKE_PROFIT_MARKET) and SL (STOP_MARKET).
    """

    def __init__(
        self,
        symbol: str,
        leverage: int = DEFAULT_LEVERAGE,
        capital: float | None = None,
        dry_run: bool = False,
        # Range Mode parameters
        range_lookback: int = 50,
        range_zone_pct: float = 25.0,
        range_tp_pct: float = 50.0,
        range_sl_pct: float = 30.0,
        close_at_opposite_extreme: bool = True,
        enable_mtf_range: bool = True,
        close_on_range_break: bool = False,
        max_adx_strength: float = 25.0,
        max_atr_pct: float = 0.5,
        max_orders: int = 4,
        # Position sizing
        pos_size_pct: float = 0.20,
        # Candle interval for base TF
        interval: str = "5m",
        # Optional precision overrides
        price_decimals: int | None = None,
        qty_decimals: int | None = None,
    ):
        self.symbol = symbol.upper()
        self.leverage = leverage
        self.capital = capital
        self.dry_run = dry_run
        self.interval = interval
        self.pos_size_pct = pos_size_pct
        self.min_notional = 5.0
        self._ws_reconnect_delay_sec = 5
        self._ws_stale_after_sec = self._compute_ws_stale_after_sec(interval)
        self._last_closed_candle_monotonic = time.monotonic()

        # Maker order settings (mirrors bot_pdhl convention)
        self._maker_price_offset_pct  = max(0.0, float(os.getenv("MAKER_PRICE_OFFSET_PCT",  "0.0002")))
        self._maker_entry_timeout_sec = max(0.5, float(os.getenv("MAKER_ENTRY_TIMEOUT_SEC", "8")))
        self._maker_exit_timeout_sec  = max(0.5, float(os.getenv("MAKER_EXIT_TIMEOUT_SEC",  "6")))
        self._maker_poll_interval_sec = max(0.1, float(os.getenv("MAKER_POLL_INTERVAL_SEC", "0.4")))

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

        self._signal = RangeSignal(
            range_lookback=range_lookback,
            range_zone_pct=range_zone_pct,
            range_tp_pct=range_tp_pct,
            range_sl_pct=range_sl_pct,
            close_at_opposite_extreme=close_at_opposite_extreme,
            enable_mtf_range=enable_mtf_range,
            close_on_range_break=close_on_range_break,
            max_adx_strength=max_adx_strength,
            max_atr_pct=max_atr_pct,
            max_orders=max_orders,
        )

        # Indicator history buffers
        self._highs: list[float] = []
        self._lows:  list[float] = []
        self._closes: list[float] = []

        # Range recalculation throttle
        self._last_range_recalc_ts: float = 0.0
        self._range_recalc_interval_sec: float = 60.0

        self._bot_state = _State.RUNNING
        self._reg_key = f"{self.symbol}:range"

        # Background tasks
        self._uds_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _setup_logging(self):
        global logger
        ansi_re = re.compile(r"\033\[[0-9;]*m")

        LOG_DIR.mkdir(exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        log_file = LOG_DIR / f"range_{self.symbol}_{date_str}.log"

        class StripAnsiFormatter(logging.Formatter):
            def format(self, record):
                result = super().format(record)
                return ansi_re.sub("", result)

        bot_logger = logging.getLogger(f"trader.range.{self.symbol}")
        if not bot_logger.handlers:
            bot_logger.setLevel(logging.INFO)
            bot_logger.propagate = False

            fh = logging.FileHandler(log_file)
            fh.setLevel(logging.INFO)
            fh.setFormatter(StripAnsiFormatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))

            ch = logging.StreamHandler(sys.stdout)
            ch.setLevel(logging.INFO)
            ch.setFormatter(logging.Formatter("%(message)s"))

            bot_logger.addHandler(fh)
            bot_logger.addHandler(ch)

            try:
                import redis
                from trader.log_publisher import RedisLogHandler
                redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
                rc = redis.from_url(redis_url, decode_responses=True)
                rh = RedisLogHandler(rc, self._reg_key, max_logs=500)
                rh.setLevel(logging.INFO)
                rh.setFormatter(logging.Formatter("%(message)s"))
                bot_logger.addHandler(rh)
            except Exception:
                pass

        logger = bot_logger

    # ------------------------------------------------------------------
    # Precision helpers
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

    @staticmethod
    def _get_filter_field(f, camel: str, snake: str | None = None):
        if isinstance(f, dict):
            return f.get(camel) or (f.get(snake) if snake else None)
        return getattr(f, camel, None) or (getattr(f, snake, None) if snake else None)

    @staticmethod
    def _positive_float(v) -> float | None:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        return x if x > 0 else None

    def _safe_price(self, raw: float) -> float:
        rounded = self._round_price(raw)
        if rounded > 0:
            return rounded
        return 10 ** (-self._price_decimals) if self._price_decimals > 0 else 1.0

    @staticmethod
    def _interval_seconds(interval: str) -> int:
        if not interval:
            return 60
        unit = interval[-1].lower()
        try:
            value = int(interval[:-1])
        except ValueError:
            return 60
        return value * {"m": 60, "h": 3600, "d": 86400}.get(unit, 60)

    @classmethod
    def _compute_ws_stale_after_sec(cls, interval: str) -> int:
        return max(180, int(cls._interval_seconds(interval) * 3 + 90))

    # ------------------------------------------------------------------
    # Exchange helpers
    # ------------------------------------------------------------------

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
                        tick = self._get_filter_field(f, "tickSize", "tick_size")
                        if tick:
                            self._price_decimals = _decimals_from_step(str(tick))
                    elif f_type == "LOT_SIZE":
                        step = self._get_filter_field(f, "stepSize", "step_size")
                        if step:
                            self._qty_decimals = _decimals_from_step(str(step))
                            s = float(step)
                            self._qty_step = s if s > 0 else 1.0
                    elif f_type == "MIN_NOTIONAL":
                        notional = self._get_filter_field(f, "notional")
                        if notional:
                            self.min_notional = float(notional)
                return
            raise SystemExit(f"Symbol '{self.symbol}' not found on Binance USDT-M Futures.")
        except SystemExit:
            raise
        except Exception as e:
            logger.info(f"{YELLOW}Could not fetch exchange precision: {e} — using defaults{RESET}")

    def _set_leverage(self):
        try:
            self._client.rest_api.change_initial_leverage(
                symbol=self.symbol, leverage=self.leverage
            )
            logger.info(f"Leverage set to {self.leverage}x for {self.symbol}")
        except Exception as e:
            logger.info(f"{YELLOW}Could not set leverage: {e}{RESET}")

    def _get_mark_price(self) -> float:
        try:
            resp = self._client.rest_api.mark_price(symbol=self.symbol)
            return float(resp.data().mark_price)
        except Exception:
            return 0.0

    def _get_position(self) -> dict | None:
        resp = self._client.rest_api.position_information_v3(symbol=self.symbol)
        for pos in resp.data():
            amt = float(pos.position_amt)
            if amt != 0:
                return {"position_amt": amt, "entry_price": float(pos.entry_price)}
        return None

    async def _wait_for_position_open(self, timeout_sec: float, direction: str) -> dict | None:
        """Poll until position appears on exchange or timeout."""
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            pos = self._get_position()
            if pos:
                amt = float(pos["position_amt"])
                if (direction == "long" and amt > 0) or (direction == "short" and amt < 0):
                    return pos
            await asyncio.sleep(self._maker_poll_interval_sec)
        pos = self._get_position()
        if pos:
            amt = float(pos["position_amt"])
            if (direction == "long" and amt > 0) or (direction == "short" and amt < 0):
                return pos
        return None

    def _cancel_open_orders(self):
        """Cancel all open orders for this symbol (cleans up TP/SL orders)."""
        try:
            self._client.rest_api.cancel_all_open_orders(symbol=self.symbol)
            logger.info("Cancelled all open orders")
        except Exception as e:
            logger.info(f"{YELLOW}Could not cancel open orders: {e}{RESET}")

    def _close_all_positions(self):
        """Market-close all open positions for this symbol."""
        try:
            resp = self._client.rest_api.position_information_v3(symbol=self.symbol)
            for pos in resp.data():
                amt = float(pos.position_amt)
                if amt == 0:
                    continue
                side = "SELL" if amt > 0 else "BUY"
                qty = abs(amt)
                self._client.rest_api.new_order(
                    symbol=self.symbol,
                    side=side,
                    type="MARKET",
                    quantity=self._fmt_qty(qty),
                    reduceOnly="true",
                )
            logger.info("Closed all positions")
        except Exception as e:
            logger.info(f"{RED}Could not close all positions: {e}{RESET}")

    # ------------------------------------------------------------------
    # Indicator seeding (historical klines on startup)
    # ------------------------------------------------------------------

    def _seed_indicators(self):
        """Fetch historical klines to populate indicator buffers before trading."""
        logger.info(f"Seeding indicators from historical {self.interval} klines...")
        needed = max(ATR_PERIOD, ADX_PERIOD) * 3 + self._signal.range_lookback + 5
        try:
            klines = _fetch_klines_rest(self.symbol, self.interval, needed)
        except Exception as e:
            logger.info(f"{YELLOW}Could not fetch historical klines: {e}{RESET}")
            return

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        for k in klines:
            close_time_ms = int(k[6])
            if close_time_ms > now_ms:
                continue  # skip unclosed candle
            self._highs.append(float(k[2]))
            self._lows.append(float(k[3]))
            self._closes.append(float(k[4]))

        # Keep buffer bounded
        maxbuf = needed + 10
        self._highs  = self._highs[-maxbuf:]
        self._lows   = self._lows[-maxbuf:]
        self._closes = self._closes[-maxbuf:]

        logger.info(f"  {len(self._highs)} historical candles loaded")

        # Initial range detection
        self._maybe_update_range()
        adx_base = _compute_adx(self._highs, self._lows, self._closes, ADX_PERIOD)
        atr_val  = _compute_atr(self._highs, self._lows, self._closes, ATR_PERIOD)
        last_price = self._closes[-1] if self._closes else 1.0
        atr_pct  = atr_val / last_price * 100 if last_price > 0 else 0.0
        adx_mtf  = self._fetch_mtf_adx() if self._signal.enable_mtf_range else None
        self._signal.update_mode(adx_base, atr_pct, adx_mtf)

        s = self._signal.state
        logger.info(
            f"  ADX(base)={adx_base:.1f}  ATR%={atr_pct:.3f}  "
            f"RangeHigh={s.range_high:.{self._price_decimals}f}  "
            f"RangeLow={s.range_low:.{self._price_decimals}f}  "
            f"InRangeMode={s.is_in_range_mode}"
        )

    def _maybe_update_range(self, now_ts: float | None = None):
        """Update range if throttle interval has elapsed."""
        now_ts = now_ts or time.time()
        if now_ts - self._last_range_recalc_ts < self._range_recalc_interval_sec:
            return
        if len(self._highs) < self._signal.range_lookback:
            return
        highs = self._highs[-self._signal.range_lookback:]
        lows  = self._lows[-self._signal.range_lookback:]
        self._signal.update_range(highs, lows, now_ts)
        self._last_range_recalc_ts = now_ts
        s = self._signal.state
        logger.info(
            f"Range updated: H={s.range_high:.{self._price_decimals}f} "
            f"L={s.range_low:.{self._price_decimals}f} "
            f"Size={s.range_size:.{self._price_decimals}f}"
        )

    def _fetch_mtf_adx(self) -> float | None:
        """Fetch M15 klines and compute ADX for MTF confirmation."""
        try:
            klines = _fetch_klines_rest(self.symbol, MTF_INTERVAL, MTF_FETCH_LIMIT)
            highs  = [float(k[2]) for k in klines]
            lows   = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            return _compute_adx(highs, lows, closes, ADX_PERIOD)
        except Exception as e:
            logger.info(f"{YELLOW}Could not fetch MTF ADX: {e}{RESET}")
            return None

    # ------------------------------------------------------------------
    # Order execution
    # ------------------------------------------------------------------

    def _calc_position_qty(self, entry_price: float) -> float:
        """Calculate order quantity from configured capital and pos_size_pct."""
        if self.capital is None:
            try:
                info = self._client.rest_api.futures_account_balance()
                for item in info.data():
                    if item.asset == "USDT":
                        self.capital = float(item.available_balance)
                        break
            except Exception as e:
                logger.info(f"{YELLOW}Could not fetch account balance: {e} — using 100 USDT{RESET}")
                self.capital = 100.0

        notional = self.capital * self.pos_size_pct * self.leverage
        qty = notional / entry_price
        return self._round_qty(qty)

    def _maker_limit_price(self, reference_price: float, side: str) -> float:
        """Compute a limit price slightly inside the book to qualify as maker."""
        if side == "BUY":
            raw = reference_price * (1 - self._maker_price_offset_pct)
        else:
            raw = reference_price * (1 + self._maker_price_offset_pct)
        return self._safe_price(raw)

    @staticmethod
    def _is_post_only_reject(error: Exception) -> bool:
        msg = str(error)
        return (
            "-5022" in msg
            or "Post Only order will be rejected" in msg
            or "could not be executed as maker" in msg
        )

    async def _place_range_order(self, side: str, tp_price: float, sl_price: float) -> RangePosition | None:
        """Open a maker LIMIT entry (GTX) + LIMIT TP (GTX) + STOP_MARKET SL (algo).

        Entry: LIMIT GTX (post-only). Falls back to MARKET on post-only reject or timeout.
        TP:    LIMIT GTX reduce_only — fills passively at the take-profit level.
        SL:    STOP_MARKET via new_algo_order (conditional, mark-price trigger).

        Returns a RangePosition if successful, None on failure.
        """
        mark = self._get_mark_price()
        if mark <= 0:
            logger.info(f"{RED}Could not get mark price — skipping order{RESET}")
            return None

        qty = self._calc_position_qty(mark)
        if qty <= 0:
            logger.info(f"{RED}Calculated qty={qty} <= 0 — skipping{RESET}")
            return None
        if qty * mark < self.min_notional:
            logger.info(
                f"{YELLOW}Notional {qty*mark:.2f} < min_notional {self.min_notional} — skipping{RESET}"
            )
            return None

        close_side = "SELL" if side == "BUY" else "BUY"
        qty_str    = self._fmt_qty(qty)
        tp_rounded = self._safe_price(tp_price) if tp_price > 0 else 0.0
        sl_rounded = self._safe_price(sl_price) if sl_price > 0 else 0.0

        if self.dry_run:
            logger.info(
                f"{GREEN}[DRY-RUN] {side} {qty_str} {self.symbol} @ ~{mark:.{self._price_decimals}f}"
                f"  TP={tp_rounded:.{self._price_decimals}f}"
                f"  SL={sl_rounded:.{self._price_decimals}f}{RESET}"
            )
            return RangePosition(
                side=side, entry_price=mark, tp_price=tp_rounded,
                sl_price=sl_rounded, qty=qty,
            )

        try:
            # ── 1. Entry: LIMIT GTX (maker / post-only) ─────────────────────
            avg_price     = mark
            executed_qty  = 0.0
            execution_mode = "market"

            maker_price = self._maker_limit_price(mark, side)
            try:
                maker_resp = self._client.rest_api.new_order(
                    symbol=self.symbol,
                    side=side,
                    type="LIMIT",
                    time_in_force="GTX",
                    price=f"{maker_price:.{self._price_decimals}f}",
                    quantity=qty_str,
                    new_order_resp_type="RESULT",
                )
            except Exception as maker_err:
                if self._is_post_only_reject(maker_err):
                    logger.info(
                        f"{YELLOW}Maker entry rejected (post-only) @ "
                        f"{maker_price:.{self._price_decimals}f} — fallback MARKET{RESET}"
                    )
                else:
                    raise
            else:
                logger.info(
                    f"{CYAN}Maker entry posted @ {maker_price:.{self._price_decimals}f} "
                    f"(timeout {self._maker_entry_timeout_sec:.1f}s){RESET}"
                )
                direction = "long" if side == "BUY" else "short"
                pos = await self._wait_for_position_open(self._maker_entry_timeout_sec, direction)
                if pos:
                    execution_mode = "maker"
                    avg_price    = float(pos["entry_price"])
                    executed_qty = abs(float(pos["position_amt"]))
                else:
                    try:
                        self._client.rest_api.cancel_all_open_orders(symbol=self.symbol)
                    except Exception:
                        pass
                    logger.info(f"{YELLOW}Maker entry timeout — fallback MARKET{RESET}")

            # Fallback to MARKET if maker didn't fill
            if executed_qty <= 0:
                order_resp = self._client.rest_api.new_order(
                    symbol=self.symbol,
                    side=side,
                    type="MARKET",
                    quantity=qty_str,
                    new_order_resp_type="RESULT",
                )
                order_data = order_resp.data()
                avg_price_raw = self._positive_float(getattr(order_data, "avg_price", None))
                avg_price    = avg_price_raw if avg_price_raw else mark
                executed_qty = self._positive_float(getattr(order_data, "executed_qty", None)) or qty

            entry_price = avg_price
            logger.info(
                f"{GREEN}{side} {self._fmt_qty(executed_qty)} {self.symbol} "
                f"@ {entry_price:.{self._price_decimals}f} [{execution_mode.upper()}]{RESET}"
            )

            # ── 2. TP: LIMIT GTX reduce_only (maker) ────────────────────────
            if tp_rounded > 0:
                try:
                    tp_resp = self._client.rest_api.new_order(
                        symbol=self.symbol,
                        side=close_side,
                        type="LIMIT",
                        time_in_force="GTX",
                        price=f"{tp_rounded:.{self._price_decimals}f}",
                        quantity=self._fmt_qty(executed_qty),
                        reduce_only="true",
                        new_order_resp_type="RESULT",
                    )
                    logger.info(
                        f"  TP LIMIT GTX @ {tp_rounded:.{self._price_decimals}f} "
                        f"(order_id={getattr(tp_resp.data(), 'order_id', '?')})"
                    )
                except Exception as e:
                    logger.info(f"{YELLOW}  Could not place TP order: {e}{RESET}")

            # ── 3. SL: STOP_MARKET via algo order (conditional, mark price) ─
            if sl_rounded > 0:
                try:
                    sl_resp = self._client.rest_api.new_algo_order(
                        algo_type="CONDITIONAL",
                        symbol=self.symbol,
                        side=close_side,
                        type="STOP_MARKET",
                        trigger_price=sl_rounded,
                        close_position="true",
                    )
                    logger.info(
                        f"  SL STOP_MARKET @ {sl_rounded:.{self._price_decimals}f} "
                        f"(algo_id={getattr(sl_resp.data(), 'algo_id', '?')})"
                    )
                except Exception as e:
                    logger.info(f"{YELLOW}  Could not place SL order: {e}{RESET}")

            return RangePosition(
                side=side, entry_price=entry_price, tp_price=tp_rounded,
                sl_price=sl_rounded, qty=executed_qty,
            )

        except Exception as e:
            logger.info(f"{RED}Order failed ({side}): {e}{RESET}")
            notify_error(self.symbol, f"Range order failed: {e}")
            return None

    async def _close_position_maker(self, pos: RangePosition, reason: str):
        """Close a position using LIMIT GTX (maker). Falls back to MARKET on reject/timeout."""
        if self.dry_run:
            logger.info(f"{CYAN}[DRY-RUN] Close {pos.side} @ maker — {reason}{RESET}")
            self._signal.notify_position_closed(pos)
            return

        close_side = "SELL" if pos.side == "BUY" else "BUY"
        mark = self._get_mark_price()

        try:
            # Cancel existing TP/SL orders first so they don't conflict
            self._cancel_open_orders()

            closed = False
            if mark > 0:
                limit_price = self._maker_limit_price(mark, close_side)
                try:
                    self._client.rest_api.new_order(
                        symbol=self.symbol,
                        side=close_side,
                        type="LIMIT",
                        time_in_force="GTX",
                        price=f"{limit_price:.{self._price_decimals}f}",
                        quantity=self._fmt_qty(pos.qty),
                        reduce_only="true",
                        new_order_resp_type="RESULT",
                    )
                    logger.info(
                        f"{CYAN}Maker close posted @ {limit_price:.{self._price_decimals}f} "
                        f"(timeout {self._maker_exit_timeout_sec:.1f}s) — {reason}{RESET}"
                    )
                    deadline = time.monotonic() + self._maker_exit_timeout_sec
                    while time.monotonic() < deadline:
                        pos_check = self._get_position()
                        if pos_check is None or pos_check["position_amt"] == 0:
                            closed = True
                            break
                        await asyncio.sleep(self._maker_poll_interval_sec)
                    if not closed:
                        try:
                            self._client.rest_api.cancel_all_open_orders(symbol=self.symbol)
                        except Exception:
                            pass
                        logger.info(f"{YELLOW}Maker close timeout — fallback MARKET{RESET}")
                except Exception as mk_err:
                    if self._is_post_only_reject(mk_err):
                        logger.info(f"{YELLOW}Maker close rejected (post-only) — fallback MARKET{RESET}")
                    else:
                        raise

            if not closed:
                self._client.rest_api.new_order(
                    symbol=self.symbol,
                    side=close_side,
                    type="MARKET",
                    quantity=self._fmt_qty(pos.qty),
                    reduceOnly="true",
                )

            logger.info(f"{CYAN}Closed {pos.side} {self._fmt_qty(pos.qty)} — {reason}{RESET}")
            self._signal.notify_position_closed(pos)
        except Exception as e:
            logger.info(f"{RED}Could not close position: {e}{RESET}")

    # ------------------------------------------------------------------
    # User-data stream (reconcile TP/SL hits)
    # ------------------------------------------------------------------

    async def _run_user_data_stream(self):
        """Subscribe to user data stream to detect when TP/SL orders fill."""
        if self.dry_run or self._client is None:
            return
        while self._bot_state != _State.STOPPED:
            try:
                listen_key_resp = self._client.rest_api.start_user_data_stream()
                listen_key = listen_key_resp.data().listen_key
                logger.info(f"User data stream started (listen_key={listen_key[:8]}...)")

                ws_cfg = self._ConfigWS(
                    api_key=BINANCE_API_KEY,
                    proxy=getattr(self, "_proxy", None),
                )
                ws_client = self._ws_factory(config_websocket_streams=ws_cfg)

                async def on_message(msg):
                    try:
                        data = msg if isinstance(msg, dict) else json.loads(msg)
                        event = data.get("e", "")
                        if event == "ORDER_TRADE_UPDATE":
                            order = data.get("o", {})
                            o_status = order.get("X", "")  # status
                            o_type   = order.get("ot", "") # order type
                            # TP = LIMIT GTX (maker) or TAKE_PROFIT_MARKET (legacy)
                            # SL = STOP_MARKET (algo conditional)
                            is_tp = o_status == "FILLED" and o_type in ("LIMIT", "TAKE_PROFIT_MARKET")
                            is_sl = o_status == "FILLED" and o_type == "STOP_MARKET"
                            if is_tp or is_sl:
                                side_closed = "BUY" if order.get("S") == "SELL" else "SELL"
                                fill_price  = float(order.get("ap", 0) or order.get("sp", 0))
                                label = "TP" if is_tp else "SL"
                                logger.info(
                                    f"{GREEN if label == 'TP' else RED}"
                                    f"{label} hit — closed {side_closed} @ {fill_price:.{self._price_decimals}f}"
                                    f"{RESET}"
                                )
                                # Reconcile positions list
                                for pos in list(self._signal.state.positions):
                                    if pos.side == side_closed:
                                        self._signal.notify_position_closed(pos)
                                        break
                    except Exception:
                        pass

                stream_name = f"{listen_key}"
                await ws_client.subscribe_to_user_data_stream(
                    listen_key=listen_key,
                    callback=on_message,
                )

                # Keep-alive every 30 min
                while self._bot_state != _State.STOPPED:
                    await asyncio.sleep(1800)
                    try:
                        self._client.rest_api.keepalive_user_data_stream(listen_key=listen_key)
                    except Exception:
                        break

            except Exception as e:
                logger.info(f"{YELLOW}User data stream error: {e} — reconnecting in 10s{RESET}")
                await asyncio.sleep(10)

    async def _run_heartbeat(self):
        """Publish bot heartbeat to registry every 60s."""
        while self._bot_state != _State.STOPPED:
            try:
                s = self._signal.state
                _registry.set_bot_state(
                    self._reg_key,
                    {
                        "symbol": self.symbol,
                        "strategy": "range",
                        "in_range_mode": s.is_in_range_mode,
                        "range_high": s.range_high,
                        "range_low": s.range_low,
                        "range_size": s.range_size,
                        "open_positions": len(s.positions),
                        "ts": int(time.time()),
                    },
                )
            except Exception:
                pass
            await asyncio.sleep(60)

    # ------------------------------------------------------------------
    # Main candle handler
    # ------------------------------------------------------------------

    async def _on_candle_closed(self, kline: dict):
        """Process a closed kline event (called from WebSocket handler)."""
        try:
            high  = float(kline["h"])
            low   = float(kline["l"])
            close = float(kline["c"])
        except (KeyError, ValueError) as e:
            logger.info(f"{YELLOW}Bad kline data: {e}{RESET}")
            return

        # Update indicator buffers
        self._highs.append(high)
        self._lows.append(low)
        self._closes.append(close)
        maxbuf = self._signal.range_lookback + ADX_PERIOD * 3 + 10
        if len(self._highs) > maxbuf:
            self._highs  = self._highs[-maxbuf:]
            self._lows   = self._lows[-maxbuf:]
            self._closes = self._closes[-maxbuf:]

        now_ts = time.time()

        # Throttled range recalculation
        recalc = (now_ts - self._last_range_recalc_ts) >= self._range_recalc_interval_sec
        if recalc:
            self._maybe_update_range(now_ts)

        # Compute indicators
        adx_base = _compute_adx(self._highs, self._lows, self._closes, ADX_PERIOD)
        atr_val  = _compute_atr(self._highs, self._lows, self._closes, ATR_PERIOD)
        atr_pct  = atr_val / close * 100 if close > 0 else 0.0
        adx_mtf  = self._fetch_mtf_adx() if (recalc and self._signal.enable_mtf_range) else None

        self._signal.update_mode(adx_base, atr_pct, adx_mtf)

        s = self._signal.state
        logger.info(
            f"Candle close={close:.{self._price_decimals}f}  "
            f"ADX={adx_base:.1f}  ATR%={atr_pct:.3f}  "
            f"RangeMode={s.is_in_range_mode}  "
            f"Positions={len(s.positions)}"
        )

        if self._bot_state != _State.RUNNING:
            return

        # Use mid-price approximation for bid/ask (close = last traded)
        # In live trading the WebSocket kline doesn't carry bid/ask; use mark price instead
        bid = close
        ask = close

        signal = self._signal.on_candle(bid=bid, ask=ask)

        if signal is None:
            return

        if signal == "CLOSE_ALL":
            logger.info(f"{YELLOW}Range break — closing all positions{RESET}")
            self._cancel_open_orders()
            self._close_all_positions()
            self._signal.clear_positions()
            return

        if isinstance(signal, tuple):
            sig_type = signal[0]

            if sig_type in ("OPEN_BUY", "OPEN_SELL"):
                _, tp_price, sl_price = signal
                side = "BUY" if sig_type == "OPEN_BUY" else "SELL"
                logger.info(
                    f"{GREEN}Signal: {sig_type}  "
                    f"TP={tp_price:.{self._price_decimals}f}  "
                    f"SL={sl_price:.{self._price_decimals}f}{RESET}"
                )
                pos = await self._place_range_order(side, tp_price, sl_price)
                if pos:
                    self._signal.notify_position_opened(pos)
                    notify_position_opened(
                        self.symbol,
                        side,
                        pos.qty,
                        pos.entry_price,
                        pos.tp_price,
                        pos.sl_price,
                    )

            elif sig_type == "CLOSE_BUY_AT_EXTREME":
                positions_to_close = signal[1]
                logger.info(
                    f"{CYAN}CloseAtOppositeExtreme: closing "
                    f"{len(positions_to_close)} BUY(s) at top{RESET}"
                )
                for pos in positions_to_close:
                    await self._close_position_maker(pos, "CloseAtOppositeExtreme (top)")
                    notify_position_closed(self.symbol, "BUY", pos.qty, 0.0)

            elif sig_type == "CLOSE_SELL_AT_EXTREME":
                positions_to_close = signal[1]
                logger.info(
                    f"{CYAN}CloseAtOppositeExtreme: closing "
                    f"{len(positions_to_close)} SELL(s) at bottom{RESET}"
                )
                for pos in positions_to_close:
                    await self._close_position_maker(pos, "CloseAtOppositeExtreme (bottom)")
                    notify_position_closed(self.symbol, "SELL", pos.qty, 0.0)

    # ------------------------------------------------------------------
    # WebSocket runner
    # ------------------------------------------------------------------

    async def _run_ws(self):
        stream = f"{self.symbol.lower()}@kline_{self.interval}"
        logger.info(f"Subscribing to WebSocket stream: {stream}")

        while self._bot_state != _State.STOPPED:
            try:
                ws_cfg = self._ConfigWS(
                    api_key=BINANCE_API_KEY,
                    proxy=getattr(self, "_proxy", None),
                )
                ws_client = self._ws_factory(config_websocket_streams=ws_cfg)

                async def on_kline(msg):
                    try:
                        data = msg if isinstance(msg, dict) else json.loads(msg)
                        k = data.get("k") or data.get("data", {}).get("k")
                        if k and k.get("x"):  # x=True means candle closed
                            self._last_closed_candle_monotonic = time.monotonic()
                            await self._on_candle_closed(k)
                    except Exception as e:
                        logger.info(f"{YELLOW}WS message error: {e}{RESET}")

                await ws_client.subscribe_kline(
                    symbol=self.symbol,
                    interval=self.interval,
                    callback=on_kline,
                )

                # Stale-connection watchdog
                while self._bot_state != _State.STOPPED:
                    await asyncio.sleep(30)
                    age = time.monotonic() - self._last_closed_candle_monotonic
                    if age > self._ws_stale_after_sec:
                        logger.info(
                            f"{YELLOW}WS stale ({age:.0f}s > {self._ws_stale_after_sec}s) — reconnecting{RESET}"
                        )
                        break

            except Exception as e:
                logger.info(f"{YELLOW}WS error: {e} — reconnecting in {self._ws_reconnect_delay_sec}s{RESET}")

            await asyncio.sleep(self._ws_reconnect_delay_sec)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self):
        self._setup_logging()
        logger.info(f"{BOLD}RangeBot starting — {self.symbol} @ {self.interval}{RESET}")

        if not self.dry_run:
            self._fetch_exchange_precision()
            self._set_leverage()
        self._seed_indicators()

        notify_bot_started(self.symbol, "RangeMode")

        # Start background tasks
        self._uds_task = asyncio.create_task(self._run_user_data_stream())
        self._heartbeat_task = asyncio.create_task(self._run_heartbeat())

        try:
            await self._run_ws()
        except asyncio.CancelledError:
            pass
        finally:
            self._bot_state = _State.STOPPED
            if self._uds_task:
                self._uds_task.cancel()
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
            notify_bot_stopped(self.symbol, "RangeMode")
            logger.info(f"{BOLD}RangeBot stopped — {self.symbol}{RESET}")
