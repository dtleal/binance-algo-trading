"""ORB (Opening Range Breakout) strategy — pure computation, no SDK imports.

Marks high/low of first `range_mins` minutes of each UTC day, then enters on
close breaking outside the range (with optional buffer):
  close > range_high * (1 + buffer_pct) → ENTER_LONG  (at most once per day)
  close < range_low  * (1 - buffer_pct) → ENTER_SHORT (at most once per day)
"""


class ORBSignal:
    """State machine that emits ENTER_LONG or ENTER_SHORT on opening range breakout.

    State resets completely each UTC day, including the range.
    """

    def __init__(
        self,
        range_mins: int = 30,
        buffer_pct: float = 0.001,
        vol_filter: bool = True,
        max_trades_per_day: int = 4,
    ):
        self.range_mins = range_mins
        self.buffer_pct = buffer_pct
        self.vol_filter = vol_filter
        self.max_trades_per_day = max_trades_per_day

        self._range_high: float | None = None
        self._range_low: float | None = None
        self._range_set = False
        self._long_taken = False
        self._short_taken = False
        self.trades_today = 0

    @property
    def traded_today(self) -> bool:
        return self.trades_today >= self.max_trades_per_day

    def reset_daily(self):
        """Reset all intraday state including the opening range."""
        self._range_high = None
        self._range_low = None
        self._range_set = False
        self._long_taken = False
        self._short_taken = False
        self.trades_today = 0

    def reset_signal(self):
        """No-op: ORB per-direction flags prevent re-entry automatically."""
        pass

    def mark_traded(self):
        """Mark one trade as consumed (e.g. resuming with an existing position)."""
        self.trades_today += 1

    @property
    def range_high(self) -> float | None:
        return self._range_high

    @property
    def range_low(self) -> float | None:
        return self._range_low

    def on_candle(
        self,
        close: float,
        high: float,
        low: float,
        minute_of_day: int,
        volume: float = 0.0,
        vol_sma20: float = 0.0,
    ) -> str | None:
        """Process one closed candle.

        Returns:
            "ENTER_LONG", "ENTER_SHORT", or None.
        """
        # Build opening range during first range_mins minutes of the day
        if minute_of_day < self.range_mins:
            if self._range_high is None:
                self._range_high = high
                self._range_low = low
            else:
                self._range_high = max(self._range_high, high)
                self._range_low = min(self._range_low, low)
            return None

        # Range period just ended (or already set)
        self._range_set = True

        if self._range_high is None or self.traded_today:
            return None

        if self.vol_filter and volume <= vol_sma20:
            return None

        # Long breakout
        if not self._long_taken and close > self._range_high * (1 + self.buffer_pct):
            self._long_taken = True
            self.trades_today += 1
            return "ENTER_LONG"

        # Short breakout
        if not self._short_taken and close < self._range_low * (1 - self.buffer_pct):
            self._short_taken = True
            self.trades_today += 1
            return "ENTER_SHORT"

        return None
