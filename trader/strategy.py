"""MomShort strategy logic — pure computation, no SDK imports."""

from trader.config import (
    STRATEGY_MIN_BARS,
    STRATEGY_CONFIRM_BARS,
    STRATEGY_VWAP_PROX,
    STRATEGY_ENTRY_START_MIN,
    STRATEGY_ENTRY_CUTOFF_MIN,
)


class VWAPTracker:
    """Cumulative intraday VWAP that auto-resets on a new UTC day."""

    def __init__(self):
        self._cum_pv = 0.0
        self._cum_vol = 0.0
        self._day = -1
        self.value = 0.0

    def reset(self):
        self._cum_pv = 0.0
        self._cum_vol = 0.0
        self.value = 0.0

    def update(self, high: float, low: float, close: float,
               volume: float, day_ordinal: int) -> float:
        """Feed one closed candle. Returns current VWAP."""
        if day_ordinal != self._day:
            self.reset()
            self._day = day_ordinal

        tp = (high + low + close) / 3.0
        self._cum_pv += tp * volume
        self._cum_vol += volume
        self.value = self._cum_pv / self._cum_vol if self._cum_vol > 0 else close
        return self.value


class MomShortSignal:
    """State machine that emits 'ENTER_SHORT' when the MomShort pattern completes.

    States: IDLE -> CONSOLIDATING -> CONFIRMING -> DONE (signal fired)
    Matches the pseudocode in STRATEGY.md sections 4 and 7.
    """

    def __init__(
        self,
        min_bars: int = STRATEGY_MIN_BARS,
        confirm_bars: int = STRATEGY_CONFIRM_BARS,
        vwap_prox: float = STRATEGY_VWAP_PROX,
        entry_start_min: int = STRATEGY_ENTRY_START_MIN,
        entry_cutoff_min: int = STRATEGY_ENTRY_CUTOFF_MIN,
        vol_filter: bool = False,
    ):
        self.min_bars = min_bars
        self.confirm_bars = confirm_bars
        self.vwap_prox = vwap_prox
        self.entry_start_min = entry_start_min
        self.entry_cutoff_min = entry_cutoff_min
        self.vol_filter = vol_filter

        self.counter = 0
        self.confirming = False
        self.confirm_count = 0
        self.traded_today = False

    def reset_daily(self):
        """Reset all state for a new trading day."""
        self.counter = 0
        self.confirming = False
        self.confirm_count = 0
        self.traded_today = False

    def mark_traded(self):
        """Mark as already traded (e.g. resuming with existing position)."""
        self.traded_today = True

    def on_candle(self, close: float, vwap: float,
                  minute_of_day: int,
                  volume: float = 0.0, vol_sma20: float = 0.0) -> str | None:
        """Process one closed candle. Returns 'ENTER_SHORT' or None."""
        if self.traded_today:
            return None

        # Outside entry window
        if minute_of_day < self.entry_start_min:
            self.counter = 0
            return None
        if minute_of_day >= self.entry_cutoff_min:
            return None

        # Confirmation phase — waiting for confirm_bars candles below VWAP
        if self.confirming:
            if close < vwap:
                self.confirm_count += 1
                if self.confirm_count >= self.confirm_bars:
                    self.confirming = False
                    self.traded_today = True
                    return "ENTER_SHORT"
            else:
                # Confirmation failed
                self.confirming = False
                self.confirm_count = 0
                self.counter = 0
            return None

        # Consolidation / breakdown detection
        pct = (close - vwap) / vwap if vwap > 0 else 0.0

        if abs(pct) <= self.vwap_prox:
            self.counter += 1
        elif self.counter >= self.min_bars and pct < -self.vwap_prox:
            # Breakdown after consolidation
            self.counter = 0
            # Volume filter: skip if breakdown candle volume <= SMA(20)
            if self.vol_filter and volume <= vol_sma20:
                return None
            if self.confirm_bars == 0:
                # Fire immediately on breakdown candle (no confirmation needed)
                self.traded_today = True
                return "ENTER_SHORT"
            # Start confirmation phase
            self.confirming = True
            self.confirm_count = 0
        else:
            self.counter = 0

        return None
