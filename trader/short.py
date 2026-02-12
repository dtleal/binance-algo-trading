import asyncio
import logging
import math
import sys
from datetime import datetime, timezone, timedelta

from binance_sdk_derivatives_trading_usds_futures import (
    DerivativesTradingUsdsFutures,
    DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL,
)
from binance_common.configuration import (
    ConfigurationRestAPI,
    ConfigurationWebSocketStreams,
)
from binance_common.errors import BadRequestError

from trader.config import (
    BINANCE_API_KEY,
    BINANCE_SECRET_KEY,
    DEFAULT_SYMBOL_UPPER,
    DEFAULT_ASSET,
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_LEVERAGE,
    LOG_DIR,
    SYMBOL_CONFIGS,
)

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

logger = logging.getLogger("trader.short")


def _setup_logging():
    """Configure logging for short operations."""
    import re

    LOG_DIR.mkdir(exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    log_file = LOG_DIR / f"short_{date_str}.log"

    ansi_re = re.compile(r"\033\[[0-9;]*m")

    class StripAnsiFormatter(logging.Formatter):
        def format(self, record):
            result = super().format(record)
            return ansi_re.sub("", result)

    root = logging.getLogger("trader")
    if not root.handlers:
        root.setLevel(logging.INFO)
        root.propagate = False

        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(StripAnsiFormatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter("%(message)s"))

        root.addHandler(file_handler)
        root.addHandler(console_handler)


class FuturesShort:
    """Manages a single USDT-M futures short position."""

    def __init__(
        self,
        symbol: str = DEFAULT_SYMBOL_UPPER,
        asset: str = DEFAULT_ASSET,
    ):
        self.symbol = symbol
        self.asset = asset
        cfg = SYMBOL_CONFIGS.get(symbol)
        self._price_decimals = cfg.price_decimals if cfg else 3

        if not BINANCE_API_KEY or not BINANCE_SECRET_KEY:
            raise SystemExit(
                "BINANCE_API_KEY and BINANCE_SECRET_KEY must be set "
                "(in .env or as environment variables)"
            )

        rest_config = ConfigurationRestAPI(
            api_key=BINANCE_API_KEY,
            api_secret=BINANCE_SECRET_KEY,
        )
        self._client = DerivativesTradingUsdsFutures(config_rest_api=rest_config)

    def get_price(self) -> float:
        """Get current futures price via REST API."""
        resp = self._client.rest_api.symbol_price_ticker(symbol=self.symbol)
        price_data = resp.data()
        if hasattr(price_data, "actual_instance"):
            return float(price_data.actual_instance.price)
        return float(price_data.price)

    def _get_position(self) -> dict | None:
        """Get active position for symbol, or None if flat."""
        resp = self._client.rest_api.position_information_v3(symbol=self.symbol)
        positions = resp.data()
        for pos in positions:
            if float(pos.position_amt) != 0:
                return {
                    "position_amt": float(pos.position_amt),
                    "entry_price": float(pos.entry_price),
                    "unrealized_profit": float(pos.un_realized_profit),
                    "mark_price": float(pos.mark_price),
                    "liquidation_price": float(pos.liquidation_price),
                }
        return None

    def _round_price(self, price: float) -> float:
        factor = 10 ** self._price_decimals
        return math.floor(price * factor) / factor

    def _round_qty(self, qty: float) -> int:
        """Round quantity to whole number (AXS step size 1)."""
        return int(math.floor(qty))

    async def open(self, quantity: float, stop_loss_pct: float, leverage: int = DEFAULT_LEVERAGE):
        """Open a futures short position.

        1. Set leverage
        2. Sell futures contract at market
        3. Place STOP_MARKET buy order as stop-loss
        """
        _setup_logging()
        quantity = self._round_qty(quantity)

        logger.info(f"{BOLD}Opening futures short on {self.symbol}{RESET}")
        logger.info(f"Quantity: {quantity} {self.asset} | Stop-loss: {stop_loss_pct}% | Leverage: {leverage}x")
        logger.info("-" * 50)

        # Set leverage
        lev_resp = self._client.rest_api.change_initial_leverage(
            symbol=self.symbol, leverage=leverage
        )
        lev_data = lev_resp.data()
        logger.info(f"Leverage set to {lev_data.leverage}x (max notional: {lev_data.max_notional_value})")

        # Get current price
        price = self.get_price()
        logger.info(f"Current price: ${price}")

        # Calculate stop-loss
        stop_price = self._round_price(price * (1 + stop_loss_pct / 100))

        logger.info(f"Stop price: ${stop_price}")
        logger.info(f"Estimated position value: ${price * quantity:.2f}")
        logger.info(f"Max loss at stop: ${(stop_price - price) * quantity:.2f}")
        logger.info("-" * 50)

        # Confirmation
        confirm = input(f"{YELLOW}Confirm short {quantity} {self.asset} @ {leverage}x? (yes/no): {RESET}")
        if confirm.strip().lower() != "yes":
            logger.info("Aborted.")
            return

        # 1. Sell futures contract at market
        logger.info(f"Selling {quantity} {self.asset} futures at market...")
        sell_resp = self._client.rest_api.new_order(
            symbol=self.symbol,
            side="SELL",
            type="MARKET",
            quantity=quantity,
            new_order_resp_type="RESULT",
        )
        sell_data = sell_resp.data()
        avg_price = float(sell_data.avg_price) if sell_data.avg_price else price

        logger.info(
            f"{GREEN}SOLD {sell_data.executed_qty} {self.asset} "
            f"@ avg ${avg_price:.4f} | "
            f"Order ID: {sell_data.order_id} | Status: {sell_data.status}{RESET}"
        )

        # Recalculate stop-loss based on actual fill price
        stop_price = self._round_price(avg_price * (1 + stop_loss_pct / 100))

        # 2. Place STOP_MARKET buy (close-all) via Algo Order API
        logger.info(f"Placing STOP_MARKET BUY @ ${stop_price}...")
        sl_resp = self._client.rest_api.new_algo_order(
            algo_type="CONDITIONAL",
            symbol=self.symbol,
            side="BUY",
            type="STOP_MARKET",
            trigger_price=stop_price,
            close_position="true",
        )
        sl_data = sl_resp.data()
        logger.info(
            f"{GREEN}Stop-loss placed | "
            f"Algo ID: {sl_data.algo_id} | Status: {sl_data.algo_status}{RESET}"
        )

        logger.info("-" * 50)
        logger.info(f"{BOLD}Futures short opened successfully!{RESET}")
        logger.info(f"  Entry price:  ${avg_price:.4f}")
        logger.info(f"  Stop-loss:    ${stop_price} (STOP_MARKET)")
        logger.info(f"  Quantity:     {quantity} {self.asset}")
        logger.info(f"  Leverage:     {leverage}x")

    async def status(self):
        """Show current futures position status and P&L."""
        _setup_logging()

        logger.info(f"{BOLD}Futures Position — {self.symbol}{RESET}")
        logger.info("-" * 50)

        # Current price
        price = self.get_price()
        logger.info(f"Current price: ${price}")

        # Position info
        pos = self._get_position()
        if pos is None:
            logger.info(f"{YELLOW}No active position{RESET}")
            return

        logger.info(f"Position:      {pos['position_amt']} {self.asset}")
        logger.info(f"Entry price:   ${pos['entry_price']:.4f}")
        logger.info(f"Mark price:    ${pos['mark_price']:.4f}")
        logger.info(f"Liq. price:    ${pos['liquidation_price']:.4f}")

        pnl = pos["unrealized_profit"]
        notional = abs(pos["position_amt"]) * pos["entry_price"]
        pnl_pct = (pnl / notional) * 100 if notional else 0
        color = GREEN if pnl >= 0 else RED
        logger.info(f"Unrealized P&L: {color}${pnl:+.4f} ({pnl_pct:+.2f}%){RESET}")

        # Open algo orders (stop-loss etc.)
        algo_resp = self._client.rest_api.current_all_algo_open_orders(symbol=self.symbol)
        algo_orders = algo_resp.data()
        if algo_orders:
            logger.info(f"\nAlgo orders ({len(algo_orders)}):")
            for o in algo_orders:
                logger.info(
                    f"  {o.side} {o.order_type} | "
                    f"Trigger: ${o.trigger_price} | "
                    f"Close-all: {o.close_position} | "
                    f"ID: {o.algo_id}"
                )
        else:
            logger.info(f"\n{YELLOW}No open orders{RESET}")

    async def close(self):
        """Close the futures short position: cancel orders, buy back."""
        _setup_logging()

        logger.info(f"{BOLD}Closing futures short on {self.symbol}{RESET}")
        logger.info("-" * 50)

        # Get position info
        pos = self._get_position()
        if pos is None:
            logger.info(f"{YELLOW}No active position to close{RESET}")
            return

        price = self.get_price()
        logger.info(f"Position: {pos['position_amt']} {self.asset}")
        logger.info(f"Entry: ${pos['entry_price']:.4f} | Current: ${price}")
        logger.info(f"Unrealized P&L: ${pos['unrealized_profit']:+.4f}")
        logger.info("-" * 50)

        confirm = input(f"{YELLOW}Confirm close position? (yes/no): {RESET}")
        if confirm.strip().lower() != "yes":
            logger.info("Aborted.")
            return

        # 1. Cancel all open orders (both regular and algo)
        logger.info("Cancelling all open orders...")
        try:
            self._client.rest_api.cancel_all_open_orders(symbol=self.symbol)
        except BadRequestError:
            pass
        try:
            self._client.rest_api.cancel_all_algo_open_orders(symbol=self.symbol)
        except BadRequestError:
            pass
        logger.info(f"{GREEN}All orders cancelled{RESET}")

        # 2. Buy back to close position
        qty = abs(pos["position_amt"])
        logger.info(f"Buying {qty} {self.asset} to close...")
        close_resp = self._client.rest_api.new_order(
            symbol=self.symbol,
            side="BUY",
            type="MARKET",
            quantity=qty,
            reduce_only="true",
            new_order_resp_type="RESULT",
        )
        close_data = close_resp.data()
        avg_price = float(close_data.avg_price) if close_data.avg_price else price

        logger.info(
            f"{GREEN}BOUGHT {close_data.executed_qty} {self.asset} "
            f"@ avg ${avg_price:.4f} | Status: {close_data.status}{RESET}"
        )

        logger.info("-" * 50)
        logger.info(f"{BOLD}Position closed.{RESET}")

    async def monitor(self):
        """Live P&L monitoring via WebSocket ticker stream. Ctrl+C to stop."""
        _setup_logging()

        pos = self._get_position()
        if pos is None:
            logger.info(f"{YELLOW}No active position to monitor{RESET}")
            return

        entry_price = pos["entry_price"]
        position_amt = abs(pos["position_amt"])

        logger.info(f"{BOLD}Live P&L Monitor — {self.symbol}{RESET}")
        logger.info(f"Position: {pos['position_amt']} {self.asset} | Entry: ${entry_price:.4f}")
        logger.info("Press Ctrl+C to stop monitoring")
        logger.info("-" * 50)

        config = ConfigurationWebSocketStreams(
            stream_url=DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL
        )
        ws_client = DerivativesTradingUsdsFutures(config_ws_streams=config)

        connection = None
        stream = None

        try:
            connection = await ws_client.websocket_streams.create_connection()
            stream = await connection.individual_symbol_ticker_streams(
                symbol=self.symbol.lower()
            )

            last_log_time = 0.0

            def on_ticker(data):
                nonlocal last_log_time
                now = asyncio.get_event_loop().time()
                if now - last_log_time < 3.0:
                    return
                last_log_time = now

                current = float(data.c)
                # Short P&L: entry - current (profit when price drops)
                pnl_per_unit = entry_price - current
                total_pnl = pnl_per_unit * position_amt
                pnl_pct = (pnl_per_unit / entry_price) * 100

                color = GREEN if total_pnl >= 0 else RED
                ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

                logger.info(
                    f"[{ts}] ${current:.4f} | "
                    f"P&L: {color}${total_pnl:+.2f} ({pnl_pct:+.2f}%){RESET} | "
                    f"Qty: {position_amt}"
                )

            stream.on("message", on_ticker)

            while True:
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("\nMonitor stopped.")
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

    @staticmethod
    async def history(days: int = 7):
        """Show trade history with realized P&L for all configured symbols."""
        _setup_logging()

        if not BINANCE_API_KEY or not BINANCE_SECRET_KEY:
            raise SystemExit(
                "BINANCE_API_KEY and BINANCE_SECRET_KEY must be set "
                "(in .env or as environment variables)"
            )

        rest_config = ConfigurationRestAPI(
            api_key=BINANCE_API_KEY,
            api_secret=BINANCE_SECRET_KEY,
        )
        client = DerivativesTradingUsdsFutures(config_rest_api=rest_config)

        now = datetime.now(timezone.utc)
        end = now
        start = now - timedelta(days=days)

        all_trades = []

        for symbol in SYMBOL_CONFIGS:
            # Paginate in 7-day windows (API max)
            window_start = start
            while window_start < end:
                window_end = min(window_start + timedelta(days=7), end)
                start_ms = int(window_start.timestamp() * 1000)
                end_ms = int(window_end.timestamp() * 1000)

                resp = client.rest_api.account_trade_list(
                    symbol=symbol,
                    start_time=start_ms,
                    end_time=end_ms,
                    limit=1000,
                )
                trades = resp.data()
                for t in trades:
                    all_trades.append(t)

                window_start = window_end

        if not all_trades:
            logger.info(f"{YELLOW}No trades found in the last {days} day(s){RESET}")
            return

        # Sort by time descending (most recent first)
        all_trades.sort(key=lambda t: t.time, reverse=True)

        total_pnl = 0.0
        total_commission = 0.0
        total_capital = 0.0

        logger.info(f"{BOLD}Trade History (last {days} day{'s' if days != 1 else ''}){RESET}")
        logger.info("=" * 70)

        for t in all_trades:
            ts = datetime.fromtimestamp(t.time / 1000, tz=timezone.utc)
            ts_str = ts.strftime("%Y-%m-%d %H:%M")
            price = float(t.price)
            qty = float(t.qty)
            pnl = float(t.realized_pnl)
            commission = float(t.commission)

            total_pnl += pnl
            total_commission += commission

            # Capital deployed = qty * price for entry fills
            if t.side == "SELL":
                total_capital += qty * price

            # Determine price decimals from config
            cfg = SYMBOL_CONFIGS.get(t.symbol)
            pd = cfg.price_decimals if cfg else 4

            if pnl != 0:
                color = GREEN if pnl >= 0 else RED
                logger.info(
                    f"  {ts_str}  {t.symbol:<10s}  {t.side:<4s}  "
                    f"{qty:>6g} @ ${price:<{pd + 3}.{pd}f}  "
                    f"{color}P&L: ${pnl:+.4f}{RESET}  Fee: ${commission:.4f}"
                )
            else:
                logger.info(
                    f"  {ts_str}  {t.symbol:<10s}  {t.side:<4s}  "
                    f"{qty:>6g} @ ${price:<{pd + 3}.{pd}f}  (entry)"
                )

        net_pnl = total_pnl - total_commission
        roi = (net_pnl / total_capital * 100) if total_capital else 0

        pnl_color = GREEN if total_pnl >= 0 else RED
        net_color = GREEN if net_pnl >= 0 else RED

        logger.info("-" * 70)
        logger.info(f"  {'Total P&L:':>26s}  {pnl_color}${total_pnl:+.4f}{RESET}")
        logger.info(f"  {'Commissions:':>26s}  ${total_commission:.4f}")
        logger.info(f"  {'Net P&L:':>26s}  {net_color}${net_pnl:+.4f}{RESET}")
        if total_capital:
            logger.info(f"  {'Capital deployed:':>26s}  ${total_capital:.2f}")
            logger.info(f"  {'ROI:':>26s}  {net_color}{roi:+.2f}%{RESET}")
