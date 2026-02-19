"""Adapters to wrap existing strategies for MultiStrategyBot.

These adapters allow existing strategy classes (VWAPPullbackSignal, MomShortSignal)
to work with the MultiStrategyBot framework WITHOUT duplicating code.
"""

from typing import Optional
from trader.multi_strategy import Strategy, Signal, Direction
from trader.strategy_vwap_pullback import VWAPPullbackSignal, EMATracker
from trader.strategy import MomShortSignal, VWAPRollingTracker


class VWAPPullbackAdapter:
    """Adapter for VWAPPullbackSignal to work with MultiStrategyBot.

    Wraps the existing VWAPPullbackSignal without modifying it.
    """

    def __init__(
        self,
        tp_pct: float,
        sl_pct: float,
        min_bars: int,
        confirm_bars: int,
        vwap_prox: float,
        vwap_window_days: int,
        ema_period: int,
        entry_start_min: int,
        entry_cutoff_min: int,
        max_trades_per_day: int,
        pos_size_pct: float = 0.20,
        vol_filter: bool = False,
    ):
        self._signal = VWAPPullbackSignal(
            min_bars=min_bars,
            confirm_bars=confirm_bars,
            vwap_prox=vwap_prox,
            entry_start_min=entry_start_min,
            entry_cutoff_min=entry_cutoff_min,
            max_trades_per_day=max_trades_per_day,
            vol_filter=vol_filter,
        )

        self._ema = EMATracker(period=ema_period)
        self._vwap = VWAPRollingTracker(window_days=vwap_window_days)

        # Store parameters
        self._tp_pct = tp_pct
        self._sl_pct = sl_pct
        self._pos_size_pct = pos_size_pct
        self._ema_period = ema_period
        self._vwap_window_days = vwap_window_days

    def on_candle(
        self,
        close: float,
        high: float,
        low: float,
        volume: float,
        timestamp: int,
        **kwargs
    ) -> Optional[Signal]:
        """Process candle and return signal if strategy triggers."""
        # Update indicators
        ema_value = self._ema.update(close)

        day_ordinal = timestamp // 86400000
        vwap_value = self._vwap.update(high, low, close, volume, day_ordinal)

        # Determine trend
        if ema_value is None or ema_value == 0:
            trend = None
        else:
            trend = "up" if close > ema_value else "down"

        # Get minute of day
        minute_of_day = (timestamp // 60000) % 1440

        # Call original strategy
        result = self._signal.on_candle(
            close=close,
            vwap=vwap_value,
            minute_of_day=minute_of_day,
            trend=trend,
            volume=volume,
            vol_sma20=0.0,  # Not using volume filter
        )

        if result is None:
            return None

        # Convert to Signal
        direction = Direction.LONG if result == "ENTER_LONG" else Direction.SHORT

        return Signal(
            direction=direction,
            confidence=1.0,
            metadata={
                "vwap": vwap_value,
                "ema": ema_value,
                "trend": trend,
            }
        )

    def reset_daily(self) -> None:
        """Reset daily state."""
        self._signal.reset_daily()

    def reset_signal(self) -> None:
        """Reset signal state after trade."""
        self._signal.reset_signal()

    @property
    def name(self) -> str:
        return f"VWAPPullback(EMA={self._ema_period},VWAP={self._vwap_window_days}d)"

    @property
    def tp_pct(self) -> float:
        return self._tp_pct

    @property
    def sl_pct(self) -> float:
        return self._sl_pct

    @property
    def pos_size_pct(self) -> float:
        return self._pos_size_pct


class MomShortAdapter:
    """Adapter for MomShortSignal to work with MultiStrategyBot.

    Wraps the existing MomShortSignal without modifying it.
    """

    def __init__(
        self,
        tp_pct: float,
        sl_pct: float,
        min_bars: int,
        confirm_bars: int,
        vwap_prox: float,
        vwap_window_days: int,
        entry_start_min: int,
        entry_cutoff_min: int,
        pos_size_pct: float = 0.20,
        vol_filter: bool = False,
    ):
        self._signal = MomShortSignal(
            min_bars=min_bars,
            confirm_bars=confirm_bars,
            vwap_prox=vwap_prox,
            entry_start_min=entry_start_min,
            entry_cutoff_min=entry_cutoff_min,
            vol_filter=vol_filter,
        )

        self._vwap = VWAPRollingTracker(window_days=vwap_window_days)

        # Store parameters
        self._tp_pct = tp_pct
        self._sl_pct = sl_pct
        self._pos_size_pct = pos_size_pct
        self._vwap_window_days = vwap_window_days

    def on_candle(
        self,
        close: float,
        high: float,
        low: float,
        volume: float,
        timestamp: int,
        **kwargs
    ) -> Optional[Signal]:
        """Process candle and return signal if strategy triggers."""
        # Update VWAP
        day_ordinal = timestamp // 86400000
        vwap_value = self._vwap.update(high, low, close, volume, day_ordinal)

        # Get minute of day
        minute_of_day = (timestamp // 60000) % 1440

        # Call original strategy
        result = self._signal.on_candle(
            close=close,
            vwap=vwap_value,
            minute_of_day=minute_of_day,
            volume=volume,
            vol_sma20=0.0,  # Not using volume filter
        )

        if not result:
            return None

        # MomShort only generates SHORT signals
        return Signal(
            direction=Direction.SHORT,
            confidence=1.0,
            metadata={
                "vwap": vwap_value,
            }
        )

    def reset_daily(self) -> None:
        """Reset daily state."""
        self._signal.reset_daily()

    def reset_signal(self) -> None:
        """Reset signal state after trade.

        MomShort only allows 1 trade/day, so no mid-day reset needed.
        """
        pass  # MomShortSignal doesn't support mid-day signal reset

    @property
    def name(self) -> str:
        return f"MomShort(VWAP={self._vwap_window_days}d)"

    @property
    def tp_pct(self) -> float:
        return self._tp_pct

    @property
    def sl_pct(self) -> float:
        return self._sl_pct

    @property
    def pos_size_pct(self) -> float:
        return self._pos_size_pct
