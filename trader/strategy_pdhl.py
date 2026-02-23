"""PDHL (Previous Day High/Low Rejection) strategy — pure computation, no SDK imports.

SHORT when price approaches Previous Day High (PDH) and rejects (closes below threshold).
LONG  when price approaches Previous Day Low  (PDL) and rejects (closes above threshold).

PDH/PDL are updated at each UTC day rollover from the previous day's high/low.
The signal cannot trade until at least one full day of data has been seen.
"""


class PDHLSignal:
    """State machine that emits ENTER_LONG or ENTER_SHORT on PDH/PDL rejection.

    Approach zone: within `prox_pct` of PDH or PDL.
    Confirmation: `confirm_bars` consecutive candles where close exits the zone
                  (rejection confirmed), then signal fires.
    """

    def __init__(
        self,
        prox_pct: float = 0.002,
        confirm_bars: int = 1,
        max_trades_per_day: int = 4,
        entry_start_min: int = 60,
        entry_cutoff_min: int = 1320,
    ):
        self.prox_pct = prox_pct
        self.confirm_bars = confirm_bars
        self.max_trades_per_day = max_trades_per_day
        self.entry_start_min = entry_start_min
        self.entry_cutoff_min = entry_cutoff_min

        # Previous day levels (None on first day — no trades until set)
        self._pdh: float | None = None
        self._pdl: float | None = None

        # Running today's range (used to populate tomorrow's PDH/PDL)
        self._today_high: float | None = None
        self._today_low: float | None = None

        # Approach / confirmation state
        self._testing_pdh = False
        self._testing_pdl = False
        self._pdh_conf = 0
        self._pdl_conf = 0

        self.trades_today = 0

    @property
    def traded_today(self) -> bool:
        return self.trades_today >= self.max_trades_per_day

    def reset_daily(self):
        """Save today's H/L as tomorrow's PDH/PDL; reset all intraday state."""
        if self._today_high is not None:
            self._pdh = self._today_high
            self._pdl = self._today_low

        self._today_high = None
        self._today_low = None
        self._testing_pdh = False
        self._testing_pdl = False
        self._pdh_conf = 0
        self._pdl_conf = 0
        self.trades_today = 0

    def reset_signal(self):
        """Reset confirmation state after a trade closes mid-day."""
        self._testing_pdh = False
        self._testing_pdl = False
        self._pdh_conf = 0
        self._pdl_conf = 0

    def mark_traded(self):
        """Mark one trade as consumed (e.g. resuming with an existing position)."""
        self.trades_today += 1

    def on_candle(
        self,
        close: float,
        high: float,
        low: float,
        minute_of_day: int,
    ) -> str | None:
        """Process one closed candle.

        Returns:
            "ENTER_LONG", "ENTER_SHORT", or None.
        """
        # Always track today's running H/L (used by reset_daily)
        if self._today_high is None:
            self._today_high = high
            self._today_low = low
        else:
            self._today_high = max(self._today_high, high)
            self._today_low = min(self._today_low, low)

        # Need at least one full day of history
        if self._pdh is None or self._pdl is None:
            return None

        if self.traded_today:
            return None

        if minute_of_day < self.entry_start_min or minute_of_day >= self.entry_cutoff_min:
            return None

        pdh = self._pdh
        pdl = self._pdl

        # Detect approach to PDH (within prox_pct below PDH)
        if high >= pdh * (1 - self.prox_pct):
            self._testing_pdh = True

        # Detect approach to PDL (within prox_pct above PDL)
        if low <= pdl * (1 + self.prox_pct):
            self._testing_pdl = True

        # Confirmation: PDH rejection → SHORT
        if self._testing_pdh:
            if close < pdh * (1 - self.prox_pct):
                self._pdh_conf += 1
                if self._pdh_conf >= self.confirm_bars:
                    self._testing_pdh = False
                    self._pdh_conf = 0
                    self.trades_today += 1
                    return "ENTER_SHORT"
            # else: still inside or above zone — keep testing

        # Confirmation: PDL rejection → LONG
        if self._testing_pdl:
            if close > pdl * (1 + self.prox_pct):
                self._pdl_conf += 1
                if self._pdl_conf >= self.confirm_bars:
                    self._testing_pdl = False
                    self._pdl_conf = 0
                    self.trades_today += 1
                    return "ENTER_LONG"
            # else: still inside or below zone — keep testing

        return None
