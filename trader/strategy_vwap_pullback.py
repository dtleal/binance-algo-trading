"""VWAPPullback strategy logic — pure computation, no SDK imports.

Bidirectional VWAP pullback with EMA trend filter:
  - Uptrend (close > EMA):   consolidate near VWAP → break above → ENTER_LONG
  - Downtrend (close < EMA): consolidate near VWAP → break below → ENTER_SHORT

Supports multiple trades per day via max_trades_per_day (default 4).
After each trade fires the signal resets so the next setup can be detected.
"""


class EMATracker:
    """Exponential Moving Average tracker. Does NOT reset on new UTC day —
    it spans multiple days to provide a meaningful trend baseline."""

    def __init__(self, period: int):
        self.period = period
        self._k = 2.0 / (period + 1)
        self.value: float | None = None
        self._count = 0

    def update(self, price: float) -> float | None:
        """Feed one price. Returns EMA once `period` bars have been seen, else None."""
        self._count += 1
        if self.value is None:
            self.value = price
        else:
            self.value = price * self._k + self.value * (1 - self._k)
        return self.value if self._count >= self.period else None


class VWAPPullbackSignal:
    """State machine that emits ENTER_LONG or ENTER_SHORT on a VWAP pullback.

    Logic (same structure as MomShortSignal, bidirectional):

    States: IDLE → CONSOLIDATING → CONFIRMING → signal fired → reset → IDLE…

    Consolidation: |pct_from_vwap| <= vwap_prox  for min_bars candles
    Breakout:
      - Uptrend   → pct > +vwap_prox  → confirm confirm_bars candles above VWAP
      - Downtrend → pct < -vwap_prox  → confirm confirm_bars candles below VWAP

    After each signal fires the intraday pattern state resets so the next
    setup can be detected, up to max_trades_per_day per UTC day.
    """

    def __init__(
        self,
        min_bars: int,
        confirm_bars: int,
        vwap_prox: float,
        entry_start_min: int,
        entry_cutoff_min: int,
        max_trades_per_day: int = 4,
        vol_filter: bool = False,
    ):
        self.min_bars = min_bars
        self.confirm_bars = confirm_bars
        self.vwap_prox = vwap_prox
        self.entry_start_min = entry_start_min
        self.entry_cutoff_min = entry_cutoff_min
        self.max_trades_per_day = max_trades_per_day
        self.vol_filter = vol_filter

        self.counter = 0
        self.confirming = False
        self.confirm_count = 0
        self._pending_direction: str | None = None  # "long" or "short"
        self.trades_today = 0

    @property
    def traded_today(self) -> bool:
        """True when the daily trade limit has been reached."""
        return self.trades_today >= self.max_trades_per_day

    def reset_daily(self):
        """Reset all intraday state for a new UTC day."""
        self.counter = 0
        self.confirming = False
        self.confirm_count = 0
        self._pending_direction = None
        self.trades_today = 0

    def reset_signal(self):
        """Reset pattern state after a trade, allowing the next setup.
        Called by the bot when a position closes mid-day."""
        self.counter = 0
        self.confirming = False
        self.confirm_count = 0
        self._pending_direction = None

    def mark_traded(self):
        """Mark one trade as consumed (e.g. resuming with an existing position)."""
        self.trades_today += 1

    def on_candle(
        self,
        close: float,
        vwap: float,
        minute_of_day: int,
        trend: str | None,
        volume: float = 0.0,
        vol_sma20: float = 0.0,
    ) -> str | None:
        """Process one closed candle.

        Args:
            trend: "up", "down", or None (EMA not yet established — no trades).

        Returns:
            "ENTER_LONG", "ENTER_SHORT", or None.
        """
        if self.traded_today or trend is None:
            return None

        if minute_of_day < self.entry_start_min:
            self.counter = 0
            return None
        if minute_of_day >= self.entry_cutoff_min:
            return None

        pct = (close - vwap) / vwap if vwap > 0 else 0.0

        # --- Confirmation phase ---
        if self.confirming:
            direction = self._pending_direction
            confirmed = (
                (direction == "long" and close > vwap)
                or (direction == "short" and close < vwap)
            )
            if confirmed:
                self.confirm_count += 1
                if self.confirm_count >= self.confirm_bars:
                    self.confirming = False
                    self.trades_today += 1
                    return "ENTER_LONG" if direction == "long" else "ENTER_SHORT"
            else:
                # Confirmation failed — reset
                self.confirming = False
                self.confirm_count = 0
                self.counter = 0
                self._pending_direction = None
            return None

        # --- Consolidation / breakout detection ---
        if abs(pct) <= self.vwap_prox:
            self.counter += 1
        elif self.counter >= self.min_bars:
            breakout_long = trend == "up" and pct > self.vwap_prox
            breakout_short = trend == "down" and pct < -self.vwap_prox

            if breakout_long or breakout_short:
                self.counter = 0
                direction = "long" if breakout_long else "short"

                if self.vol_filter and volume <= vol_sma20:
                    return None

                if self.confirm_bars == 0:
                    self.trades_today += 1
                    return "ENTER_LONG" if direction == "long" else "ENTER_SHORT"

                self.confirming = True
                self.confirm_count = 0
                self._pending_direction = direction
            else:
                self.counter = 0
        else:
            self.counter = 0

        return None
