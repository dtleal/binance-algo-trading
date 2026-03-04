# Project Memory: binance-algo-trading

Last updated: 2026-03-04

## Purpose

This repository is an automated Binance USDT-M futures trading and research stack.
It supports:
- historical data collection,
- multi-timeframe parameter sweeps,
- detailed backtests,
- per-symbol strategy documentation,
- live/paper trading bots.

## Core Workflow

1. Download 1m klines:
   - `make onboarding-download SYMBOL=dogeusdt DAYS=365`
2. Aggregate timeframes (mandatory):
   - `python scripts/aggregate_klines.py data/klines/dogeusdt_1m_klines.csv`
   - Generates 5m, 15m, 30m, 1h files.
3. Run sweep on all timeframes:
   - `make sweep-rust SYMBOL=dogeusdt`
4. Pick champion (best return + acceptable drawdown/risk-adjusted profile).
5. Run detailed backtest:
   - `make backtest-detail` (MomShort/Rejection)
   - `make backtest-detail-pullback` (VWAPPullback)
6. Validate:
   - profitable months consistency,
   - max drawdown,
   - win rate vs R:R,
   - exit profile (EOD expected to dominate in drift-style setups).
7. Document strategy in `docs/STRATEGY_<SYMBOL>.md`.
8. Configure live symbol in `trader/config.py`.
9. Paper trade 1-2 weeks minimum before live.

## Important Onboarding Rules

- Always run sweeps across all 5 timeframes (1m, 5m, 15m, 30m, 1h).
- Skip assets when red flags appear:
  - all strategies negative on average,
  - best return < 5% with low trades,
  - max drawdown > 15%,
  - win rate < 35% with poor R:R.
- Standard checks before live:
  - exchange precision (tick_size, step_size, min_qty, min_notional),
  - symbol-level config parameters,
  - dry-run parity vs backtest behavior.

## Main Strategies Covered In Docs

- MomShort: intraday VWAP consolidation -> breakdown short.
- VWAPPullback: bidirectional pullback system (long in uptrend, short in downtrend).
- Sweep engine evaluates multiple families:
  - RejShort, RejLong, MomShort, MomLong, VWAPPullback, EMAScalp, ORB, PDHL.

## Canonical Strategy Behavior (MomShort)

- Entry window: usually 01:00-22:00 UTC.
- Intraday VWAP resets daily at 00:00 UTC.
- Signal pattern:
  - price consolidates near VWAP for `min_bars`,
  - breakdown below VWAP threshold,
  - optional confirmation bars below VWAP.
- Exit order on each candle:
  1. SL first,
  2. TP second,
  3. EOD force-close at 23:50 UTC.
- Max 1 trade/day.
- Fees modeled as 0.04% taker per side (0.08% round-trip).

## Known Token Profiles From Existing Docs

- AXS (reference in `docs/STRATEGY.md`):
  - MomShort, TP 10%, SL 5%, min_bars 3, confirm 2, vwap_prox 0.5%.
  - Strong EOD-drift profile, 7/7 profitable months in that sample.

- SAND (`docs/STRATEGY_SANDUSDT.md`):
  - MomShort, TP 10%, SL 0.8%, min_bars 5, confirm 2, vwap_prox 0.2%.
  - Low win-rate / high R:R profile, very low drawdown.

- GALA (`docs/STRATEGY_GALAUSDT.md`):
  - MomShort, TP 5%, SL 5%, min_bars 5, confirm 0, vwap_prox 0.2%, vol_filter ON.
  - High overfitting risk warning:
    - sweep averages negative across strategies,
    - champion appears as outlier.

- MANA (`docs/STRATEGY_MANAUSDT.md`):
  - MomShort, TP 5%, SL 5%, min_bars 12, confirm 2, vwap_prox 0.5%, vol_filter ON in sweep champion.
  - Balanced win-rate profile with moderate drawdown.

## Active Bot Portfolio (Current)

- Total active bots: 24
- Strategy split:
  - MomShort: 5
  - VWAPPullback: 14
  - PDHL: 4
  - ORB: 1
- Canonical roster and params:
  - `docs/ACTIVE_BOTS.md`

## Precision Notes From Docs

- SANDUSDT:
  - price tick 0.00001 (5 decimals),
  - quantity step 1 (integer qty),
  - min notional around 5 USDT.
- MANAUSDT:
  - price tick 0.0001 (4 decimals),
  - quantity step 1 (integer qty),
  - min notional around 5 USDT.
- GALAUSDT:
  - explicitly marked as TODO to verify before live.

## Important Files

- `scripts/fetch_klines.py`: download historical 1m data.
- `scripts/aggregate_klines.py`: build higher timeframes.
- `scripts/backtest_detail.py`: detailed backtest for MomShort/Rejection.
- `scripts/backtest_detail_pullback.py`: detailed pullback backtest.
- `scripts/analyze_sweep.py`: sweep result analysis.
- `backtest_sweep/target/release/backtest_sweep`: sweep binary.
- `backtest_sweep_v2/target/release/backtest_sweep_v2`: advanced trailing-stop sweep.
- `data/klines/`: historical CSV inputs.
- `data/sweeps/`: sweep outputs.
- `trader/config.py`: live symbol configuration.
- `docs/STRATEGY_*.md`: per-symbol implementation records.

## Operational Guardrails

- Prefer paper trading before live changes.
- Treat V2 sweep as advanced/optional (explicit request only).
- If champion comes from broadly negative sweep averages, treat as suspect and validate longer.
- Keep one-trade-per-day and EOD close behavior unless there is a clear, tested reason to change.
