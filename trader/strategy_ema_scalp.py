"""EMAScalp strategy — pure computation, no SDK imports.

Bidirectional EMA crossover:
  Fast crosses above slow → ENTER_LONG
  Fast crosses below slow → ENTER_SHORT

EMAs span multiple days and never reset.
"""

from trader.strategy_vwap_pullback import EMATracker


class EMAScalpSignal:
    """State machine that emits ENTER_LONG or ENTER_SHORT on EMA crossover.

    Uses two EMATrackers (fast + slow). On each closed candle, if the fast EMA
    crosses above the slow EMA → LONG signal; crosses below → SHORT signal.

    EMAs accumulate continuously across days — never reset.
    Only trades_today is reset on UTC day rollover.
    """

    def __init__(
        self,
        fast_period: int = 8,
        slow_period: int = 21,
        vol_filter: bool = True,
        max_trades_per_day: int = 10,
        entry_start_min: int = 0,
        entry_cutoff_min: int = 1380,
    ):
        if fast_period >= slow_period:
            raise ValueError(
                f"fast_period ({fast_period}) must be < slow_period ({slow_period})"
            )
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.vol_filter = vol_filter
        self.max_trades_per_day = max_trades_per_day
        self.entry_start_min = entry_start_min
        self.entry_cutoff_min = entry_cutoff_min

        self.fast_ema = EMATracker(period=fast_period)
        self.slow_ema = EMATracker(period=slow_period)

        self._prev_fast: float | None = None
        self._prev_slow: float | None = None
        self.trades_today = 0

    @property
    def traded_today(self) -> bool:
        return self.trades_today >= self.max_trades_per_day

    def reset_daily(self):
        """Reset only trades_today — EMAs span multiple days, never reset."""
        self.trades_today = 0

    def reset_signal(self):
        """No-op: crossover signal is stateless between bars."""
        pass

    def mark_traded(self):
        """Mark one trade as consumed (e.g. resuming with an existing position)."""
        self.trades_today += 1

    def on_candle(
        self,
        close: float,
        minute_of_day: int,
        volume: float = 0.0,
        vol_sma20: float = 0.0,
    ) -> str | None:
        """Process one closed candle.

        Returns:
            "ENTER_LONG", "ENTER_SHORT", or None.
        """
        fast = self.fast_ema.update(close)
        slow = self.slow_ema.update(close)

        # Always update prev values so cross is detected correctly next bar
        if fast is None or slow is None:
            self._prev_fast = fast
            self._prev_slow = slow
            return None

        signal = None

        if (
            not self.traded_today
            and self.entry_start_min <= minute_of_day < self.entry_cutoff_min
            and self._prev_fast is not None
            and self._prev_slow is not None
        ):
            if not (self.vol_filter and volume <= vol_sma20):
                prev_above = self._prev_fast > self._prev_slow
                curr_above = fast > slow

                if not prev_above and curr_above:
                    self.trades_today += 1
                    signal = "ENTER_LONG"
                elif prev_above and not curr_above:
                    self.trades_today += 1
                    signal = "ENTER_SHORT"

        self._prev_fast = fast
        self._prev_slow = slow
        return signal
