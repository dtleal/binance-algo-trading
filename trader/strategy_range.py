"""Range Mode strategy — pure computation, no SDK imports.

Mean-reversion strategy that activates when the market is in a horizontal
range (no trend, low volatility). Opens BUY near range bottom and SELL near
range top; each position carries its own TP/SL calculated as % of range size.

Ported from GridTradingEA.mq5 (MQL5) as documented in docs/RANGE_MODE.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Sequence


class PriceZone(Enum):
    BUY_ZONE = auto()
    SELL_ZONE = auto()
    NEUTRAL = auto()


@dataclass
class RangePosition:
    """Tracks a single open position opened by the Range bot."""

    side: str           # "BUY" or "SELL"
    entry_price: float
    tp_price: float
    sl_price: float     # 0.0 if SL disabled
    qty: float


@dataclass
class RangeState:
    """Mutable state shared between RangeSignal ticks (mirrors MQL5 globals)."""

    range_high: float = 0.0
    range_low: float = 0.0
    range_size: float = 0.0
    is_in_range_mode: bool = False
    was_in_range_mode: bool = False
    last_range_calc_ts: float = 0.0   # UNIX timestamp (seconds)
    first_order_placed: bool = False
    positions: list[RangePosition] = field(default_factory=list)


class RangeSignal:
    """State machine for Range Mode mean-reversion trading.

    Signals emitted via on_candle():
        "CLOSE_ALL"                     — exit all positions (range break + CloseOnRangeBreak)
        ("OPEN_BUY",  tp, sl, zone_ctx) — open BUY at current ask
        ("OPEN_SELL", tp, sl, zone_ctx) — open SELL at current bid
        ("CLOSE_BUY_AT_EXTREME",  [positions]) — early-exit BUYs at top extreme
        ("CLOSE_SELL_AT_EXTREME", [positions]) — early-exit SELLs at bottom extreme
        None                            — do nothing this tick
    """

    def __init__(
        self,
        *,
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
        recent_order_threshold_pct: float = 2.0,
    ):
        self.range_lookback = range_lookback
        self.range_zone_pct = range_zone_pct
        self.range_tp_pct = range_tp_pct
        self.range_sl_pct = range_sl_pct
        self.close_at_opposite_extreme = close_at_opposite_extreme
        self.enable_mtf_range = enable_mtf_range
        self.close_on_range_break = close_on_range_break
        self.max_adx_strength = max_adx_strength
        self.max_atr_pct = max_atr_pct
        self.max_orders = max_orders
        self.recent_order_threshold_pct = recent_order_threshold_pct

        self._state = RangeState()

    # ------------------------------------------------------------------ #
    # Public helpers for the bot to update cached market data
    # ------------------------------------------------------------------ #

    def update_range(
        self,
        highs: Sequence[float],
        lows: Sequence[float],
        now_ts: float,
    ) -> None:
        """Recalculate range high/low from recent candles.

        Call this at most 1x/minute (throttle enforced externally by the bot).
        `highs` and `lows` are the last `range_lookback` candles' H and L values.
        `now_ts` is the current UNIX timestamp in seconds.
        """
        if not highs or not lows:
            return
        self._state.last_range_calc_ts = now_ts
        self._state.range_high = max(highs)
        self._state.range_low = min(lows)
        self._state.range_size = self._state.range_high - self._state.range_low

    def update_mode(
        self,
        adx_base: float,
        atr_pct_base: float,
        adx_mtf: float | None = None,
    ) -> None:
        """Recompute is_in_range_mode from cached indicators.

        Call once per candle close, after update_range().
        `adx_mtf` is the ADX on the confirmation timeframe (e.g. M15).
        Pass None if EnableMTFRange is False or data is unavailable.
        """
        s = self._state
        s.was_in_range_mode = s.is_in_range_mode

        ok = (
            adx_base <= self.max_adx_strength
            and atr_pct_base <= self.max_atr_pct
            and s.range_size > 0
        )
        if ok and self.enable_mtf_range and adx_mtf is not None:
            ok = adx_mtf <= self.max_adx_strength

        s.is_in_range_mode = ok

    def notify_position_opened(self, pos: RangePosition) -> None:
        """Called by the bot after successfully placing an order."""
        self._state.positions.append(pos)
        self._state.first_order_placed = True

    def notify_position_closed(self, pos: RangePosition) -> None:
        """Called by the bot after a position is closed (TP/SL hit or manual)."""
        try:
            self._state.positions.remove(pos)
        except ValueError:
            pass

    def clear_positions(self) -> None:
        """Reset all tracked positions (e.g. after CLOSE_ALL)."""
        self._state.positions.clear()
        self._state.first_order_placed = False

    @property
    def state(self) -> RangeState:
        return self._state

    # ------------------------------------------------------------------ #
    # Main signal generation (call once per closed candle)
    # ------------------------------------------------------------------ #

    def on_candle(
        self,
        bid: float,
        ask: float,
    ) -> object:
        """Process one closed candle and emit a signal.

        Returns one of:
            None
            "CLOSE_ALL"
            ("OPEN_BUY",  tp_price, sl_price)
            ("OPEN_SELL", tp_price, sl_price)
            ("CLOSE_BUY_AT_EXTREME",  list[RangePosition])
            ("CLOSE_SELL_AT_EXTREME", list[RangePosition])
        """
        s = self._state

        # ── Transition: Range → Grid ──────────────────────────────────────
        if s.was_in_range_mode and not s.is_in_range_mode:
            if self.close_on_range_break and s.positions:
                self.clear_positions()
                return "CLOSE_ALL"

        if not s.is_in_range_mode:
            return None

        zone = self._get_price_zone(bid)

        # ── CloseAtOppositeExtreme ────────────────────────────────────────
        if self.close_at_opposite_extreme:
            signal = self._check_extreme_close(zone, bid, ask)
            if signal is not None:
                return signal

        # ── Respect MaxOrders ─────────────────────────────────────────────
        if len(s.positions) >= self.max_orders:
            return None

        # ── First order ───────────────────────────────────────────────────
        if not s.first_order_placed and not s.positions:
            if zone == PriceZone.NEUTRAL:
                return None
            side = "SELL" if zone == PriceZone.SELL_ZONE else "BUY"
            return self._build_open_signal(side, bid, ask)

        # ── Subsequent orders ─────────────────────────────────────────────
        if zone == PriceZone.BUY_ZONE and not self._has_recent_order("BUY", bid):
            return self._build_open_signal("BUY", bid, ask)
        if zone == PriceZone.SELL_ZONE and not self._has_recent_order("SELL", ask):
            return self._build_open_signal("SELL", bid, ask)

        return None

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _get_price_zone(self, bid: float) -> PriceZone:
        s = self._state
        if s.range_size <= 0:
            return PriceZone.NEUTRAL
        zone_height = s.range_size * (self.range_zone_pct / 100.0)
        buy_zone_top = s.range_low + zone_height
        sell_zone_bot = s.range_high - zone_height
        if bid <= buy_zone_top:
            return PriceZone.BUY_ZONE
        if bid >= sell_zone_bot:
            return PriceZone.SELL_ZONE
        return PriceZone.NEUTRAL

    def _calc_tp(self, side: str, bid: float, ask: float) -> float:
        s = self._state
        if s.range_size <= 0:
            return 0.0
        dist = s.range_size * (self.range_tp_pct / 100.0)
        if side == "BUY":
            return ask + dist
        return bid - dist

    def _calc_sl(self, side: str, bid: float, ask: float) -> float:
        if self.range_sl_pct <= 0:
            return 0.0
        s = self._state
        dist = s.range_size * (self.range_sl_pct / 100.0)
        if side == "BUY":
            return ask - dist
        return bid + dist

    def _build_open_signal(
        self, side: str, bid: float, ask: float
    ) -> tuple:
        tp = self._calc_tp(side, bid, ask)
        sl = self._calc_sl(side, bid, ask)
        signal_type = "OPEN_BUY" if side == "BUY" else "OPEN_SELL"
        return (signal_type, tp, sl)

    def _has_recent_order(self, side: str, ref_price: float) -> bool:
        """True if an existing same-side position is within 2% of range from ref_price."""
        s = self._state
        if s.range_size <= 0:
            return False
        threshold = s.range_size * (self.recent_order_threshold_pct / 100.0)
        for pos in s.positions:
            if pos.side != side:
                continue
            if abs(pos.entry_price - ref_price) < threshold:
                return True
        return False

    def _check_extreme_close(
        self, zone: PriceZone, bid: float, ask: float
    ) -> object:
        """Return close signals if any position is at its opposite extreme."""
        s = self._state
        margin = s.range_size * 0.05

        buys_to_close = [
            p for p in s.positions
            if p.side == "BUY"
            and zone == PriceZone.SELL_ZONE
            and bid >= (s.range_high - margin)
        ]
        sells_to_close = [
            p for p in s.positions
            if p.side == "SELL"
            and zone == PriceZone.BUY_ZONE
            and ask <= (s.range_low + margin)
        ]

        if buys_to_close:
            return ("CLOSE_BUY_AT_EXTREME", buys_to_close)
        if sells_to_close:
            return ("CLOSE_SELL_AT_EXTREME", sells_to_close)
        return None
