"""Multi-strategy bot framework - combine multiple trading strategies.

Architecture:
    Strategy (Protocol) → defines interface for any strategy
    SignalCombiner → logic to combine signals from multiple strategies
    MultiStrategyBot → orchestrates multiple strategies
"""

from typing import Protocol, List, Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum


class Direction(Enum):
    """Trade direction."""
    LONG = "long"
    SHORT = "short"


@dataclass
class Signal:
    """Trading signal from a strategy."""
    direction: Direction
    confidence: float = 1.0  # 0.0 to 1.0
    metadata: Dict[str, Any] = None  # Strategy-specific data

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class TradeDecision:
    """Final trade decision after combining signals."""
    direction: Direction
    tp_pct: float
    sl_pct: float
    pos_size_pct: float
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class Strategy(Protocol):
    """Protocol that all strategies must implement.

    This allows MultiStrategyBot to work with any strategy.
    """

    def on_candle(
        self,
        close: float,
        high: float,
        low: float,
        volume: float,
        timestamp: int,
        **kwargs
    ) -> Optional[Signal]:
        """Process a candle and return a signal if conditions met.

        Args:
            close: Close price
            high: High price
            low: Low price
            volume: Volume
            timestamp: Candle open time (ms)
            **kwargs: Strategy-specific parameters (vwap, ema, etc)

        Returns:
            Signal if strategy triggers, None otherwise
        """
        ...

    def reset_daily(self) -> None:
        """Reset daily state (called at start of new UTC day)."""
        ...

    def reset_signal(self) -> None:
        """Reset signal state after a trade (allows next setup)."""
        ...

    @property
    def name(self) -> str:
        """Strategy name for logging."""
        ...

    @property
    def tp_pct(self) -> float:
        """Default take-profit %."""
        ...

    @property
    def sl_pct(self) -> float:
        """Default stop-loss %."""
        ...

    @property
    def pos_size_pct(self) -> float:
        """Default position size %."""
        ...


class SignalCombiner:
    """Base class for signal combination logic.

    Subclasses implement different strategies for combining
    signals from multiple strategies.
    """

    def combine(
        self,
        signals: List[tuple[Strategy, Optional[Signal]]]
    ) -> Optional[TradeDecision]:
        """Combine signals from multiple strategies into a trade decision.

        Args:
            signals: List of (strategy, signal) pairs

        Returns:
            TradeDecision if should trade, None otherwise
        """
        raise NotImplementedError


class FirstSignalCombiner(SignalCombiner):
    """Use the first signal that appears (by strategy order).

    Example:
        Strategy 1 (priority): no signal
        Strategy 2: LONG signal  ← use this
        Strategy 3: SHORT signal (ignored)
    """

    def combine(
        self,
        signals: List[tuple[Strategy, Optional[Signal]]]
    ) -> Optional[TradeDecision]:
        for strategy, signal in signals:
            if signal is not None:
                return TradeDecision(
                    direction=signal.direction,
                    tp_pct=strategy.tp_pct,
                    sl_pct=strategy.sl_pct,
                    pos_size_pct=strategy.pos_size_pct,
                    metadata={
                        "strategy": strategy.name,
                        "confidence": signal.confidence,
                        **signal.metadata
                    }
                )
        return None


class AllAgreeCombiner(SignalCombiner):
    """Only trade if ALL strategies agree on direction.

    If all strategies give same signal → trade with increased conviction
    If strategies disagree or some have no signal → don't trade

    Example:
        Strategy 1: LONG
        Strategy 2: LONG
        Strategy 3: LONG
        → Result: LONG with higher position size

        Strategy 1: LONG
        Strategy 2: SHORT
        → Result: None (conflict)
    """

    def __init__(self, conviction_multiplier: float = 1.5):
        """
        Args:
            conviction_multiplier: Multiply position size when all agree (default 1.5x)
        """
        self.conviction_multiplier = conviction_multiplier

    def combine(
        self,
        signals: List[tuple[Strategy, Optional[Signal]]]
    ) -> Optional[TradeDecision]:
        # Filter out None signals
        active_signals = [(s, sig) for s, sig in signals if sig is not None]

        if not active_signals:
            return None

        # Check if all agree
        directions = [sig.direction for _, sig in active_signals]
        if len(set(directions)) > 1:
            # Conflict: strategies disagree
            return None

        # All agree! Use weighted average of parameters
        direction = directions[0]
        strategies = [s for s, _ in active_signals]
        sigs = [sig for _, sig in active_signals]

        # Average TP/SL/pos_size
        avg_tp = sum(s.tp_pct for s in strategies) / len(strategies)
        avg_sl = sum(s.sl_pct for s in strategies) / len(strategies)
        avg_pos = sum(s.pos_size_pct for s in strategies) / len(strategies)

        # Increase position size due to conviction
        boosted_pos = min(avg_pos * self.conviction_multiplier, 1.0)

        return TradeDecision(
            direction=direction,
            tp_pct=avg_tp,
            sl_pct=avg_sl,
            pos_size_pct=boosted_pos,
            metadata={
                "strategies": [s.name for s in strategies],
                "conviction": "HIGH",
                "num_agreeing": len(strategies),
                "avg_confidence": sum(sig.confidence for sig in sigs) / len(sigs)
            }
        )


class WeightedCombiner(SignalCombiner):
    """Combine signals using weighted average based on confidence.

    Each strategy has a weight. If signals conflict, use the one
    with highest weighted confidence.
    """

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        """
        Args:
            weights: Dict mapping strategy name to weight (default: equal weights)
        """
        self.weights = weights or {}

    def combine(
        self,
        signals: List[tuple[Strategy, Optional[Signal]]]
    ) -> Optional[TradeDecision]:
        # Filter active signals
        active = [(s, sig) for s, sig in signals if sig is not None]

        if not active:
            return None

        # Calculate weighted confidence for each direction
        long_score = 0.0
        short_score = 0.0

        for strategy, signal in active:
            weight = self.weights.get(strategy.name, 1.0)
            score = signal.confidence * weight

            if signal.direction == Direction.LONG:
                long_score += score
            else:
                short_score += score

        # Choose direction with highest score
        if long_score > short_score:
            direction = Direction.LONG
            winning_strategies = [s for s, sig in active if sig.direction == Direction.LONG]
        elif short_score > long_score:
            direction = Direction.SHORT
            winning_strategies = [s for s, sig in active if sig.direction == Direction.SHORT]
        else:
            # Tie - don't trade
            return None

        # Average parameters from winning strategies
        avg_tp = sum(s.tp_pct for s in winning_strategies) / len(winning_strategies)
        avg_sl = sum(s.sl_pct for s in winning_strategies) / len(winning_strategies)
        avg_pos = sum(s.pos_size_pct for s in winning_strategies) / len(winning_strategies)

        return TradeDecision(
            direction=direction,
            tp_pct=avg_tp,
            sl_pct=avg_sl,
            pos_size_pct=avg_pos,
            metadata={
                "strategies": [s.name for s in winning_strategies],
                "long_score": long_score,
                "short_score": short_score,
                "winner": direction.value
            }
        )


class MultiStrategyBot:
    """Orchestrates multiple trading strategies.

    Usage:
        strategies = [
            VWAPPullbackStrategy(...),
            MomShortStrategy(...),
        ]

        combiner = AllAgreeCombiner(conviction_multiplier=1.5)
        bot = MultiStrategyBot(strategies, combiner)

        # On each candle
        decision = bot.on_candle(close=2000, high=2010, ...)
        if decision:
            # Execute trade based on decision.direction, decision.tp_pct, etc
    """

    def __init__(
        self,
        strategies: List[Strategy],
        combiner: SignalCombiner
    ):
        """
        Args:
            strategies: List of strategy instances
            combiner: Signal combination logic
        """
        self.strategies = strategies
        self.combiner = combiner

    def on_candle(
        self,
        close: float,
        high: float,
        low: float,
        volume: float,
        timestamp: int,
        **kwargs
    ) -> Optional[TradeDecision]:
        """Process candle through all strategies and combine signals.

        Args:
            close: Close price
            high: High price
            low: Low price
            volume: Volume
            timestamp: Candle open time (ms)
            **kwargs: Additional data (vwap, ema, etc)

        Returns:
            TradeDecision if should trade, None otherwise
        """
        # Collect signals from all strategies
        signals = []
        for strategy in self.strategies:
            signal = strategy.on_candle(
                close=close,
                high=high,
                low=low,
                volume=volume,
                timestamp=timestamp,
                **kwargs
            )
            signals.append((strategy, signal))

        # Combine signals
        return self.combiner.combine(signals)

    def reset_daily(self) -> None:
        """Reset all strategies for new UTC day."""
        for strategy in self.strategies:
            strategy.reset_daily()

    def reset_signal(self) -> None:
        """Reset all strategies after trade."""
        for strategy in self.strategies:
            strategy.reset_signal()

    @property
    def strategy_names(self) -> List[str]:
        """Names of all active strategies."""
        return [s.name for s in self.strategies]
