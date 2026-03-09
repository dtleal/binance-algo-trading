# Token Onboarding Guide

How to evaluate a new token for automated trading.

---

## Quick Start (Automated)

```bash
make onboarding SYMBOL=dogeusdt          # full process: download → aggregate → sweep all TFs
make onboarding SYMBOL=btcusdt DAYS=365  # explicit day count
```

This runs the download/aggregate/sweep pipeline automatically. Use the manual steps below to re-run specific parts (including the anti-overfitting stage).

---

## Step 1: Download Historical Data

```bash
python scripts/fetch_klines.py DOGEUSDT -d 365 -o data/klines/dogeusdt_1m_klines.csv
```

Or via Makefile:
```bash
make onboarding-download SYMBOL=dogeusdt DAYS=365
```

This downloads up to 1 year of 1-minute candles from Binance's public REST API.
Rate-limited at ~4 requests/sec, takes a few minutes for a full year (~525K candles).

If the download is interrupted, re-running the same command with the same output path
automatically resumes from the last saved candle and retries `HTTP 429` with backoff.

**Check the output:**
- If the pair is newer than 1 year, you'll get less data
- Minimum recommended: **3 months** of data for any meaningful backtest
- The script prints progress and final candle count

Output file: `data/klines/dogeusdt_1m_klines.csv`

---

## Step 2: Aggregate to All Timeframes (MANDATORY)

After downloading 1m data, **always** aggregate to all timeframes before running any sweep:

```bash
python scripts/aggregate_klines.py data/klines/dogeusdt_1m_klines.csv
```

This generates **4 additional files** in `data/klines/`:
- `data/klines/dogeusdt_5m_klines.csv`
- `data/klines/dogeusdt_15m_klines.csv`
- `data/klines/dogeusdt_30m_klines.csv`
- `data/klines/dogeusdt_1h_klines.csv`

**This step is mandatory.** The sweep must be run on all 5 timeframes (1m, 5m, 15m, 30m, 1h)
to find the champion. Skipping aggregation means missing the timeframe where the strategy
performs best.

---

## Step 3: Run the Parameter Sweep on All Timeframes

Use the Makefile target (recommended — handles all TFs automatically):

```bash
make sweep-rust SYMBOL=dogeusdt
```

If you are testing runtime protection guards without blowing up the global search space,
use the dedicated strategy sweeps instead of modifying the standard sweep:

```bash
make sweep-pdhl-guards SYMBOL=icxusdt
make sweep-pullback-guards SYMBOL=ethusdt
```

These dedicated sweeps:
- keep the standard multi-strategy sweep unchanged,
- add `time_stop_minutes`, `time_stop_min_progress_pct`, `adverse_exit_bars`, and `adverse_body_min_pct`,
- use a focused parameter grid so they remain runnable on a normal workstation.

If you want a true intraday pullback grid instead of EOD-drift setups, use:

```bash
make sweep-pullback-intraday SYMBOL=dogeusdt
```

This dedicated intraday sweep narrows the grid to:
- `TP`: `0.5%, 1%, 1.5%, 2%, 3%`
- `SL`: `0.4%, 0.6%, 0.8%, 1%, 1.5%, 2%`
- `min_bars`: `2, 3, 5`
- `confirm_bars`: `0, 1`
- `vwap_prox`: `0.1%, 0.2%, 0.3%`
- `ema_period`: `100, 200`
- `max_hold`: `15m, 30m, 60m`
- `time_stop_minutes`: `20, 40, 60`
- `max_trades_per_day`: `2, 4`

Use it when the research goal is faster intraday rotation and low `EOD` dependence.

Or run manually per timeframe:

```bash
BINARY=./backtest_sweep/target/release/backtest_sweep

$BINARY data/klines/dogeusdt_1m_klines.csv
mv backtest_sweep.csv data/sweeps/dogeusdt_1m_sweep.csv

$BINARY data/klines/dogeusdt_5m_klines.csv
mv backtest_sweep.csv data/sweeps/dogeusdt_5m_sweep.csv

$BINARY data/klines/dogeusdt_15m_klines.csv
mv backtest_sweep.csv data/sweeps/dogeusdt_15m_sweep.csv

$BINARY data/klines/dogeusdt_30m_klines.csv
mv backtest_sweep.csv data/sweeps/dogeusdt_30m_sweep.csv

$BINARY data/klines/dogeusdt_1h_klines.csv
mv backtest_sweep.csv data/sweeps/dogeusdt_1h_sweep.csv
```

Results are saved to `data/sweeps/SYMBOL_TF_sweep.csv`.

This sweeps all 8 strategies (RejShort, RejLong, MomShort, MomLong, VWAPPullback,
EMAScalp, ORB, PDHL) across thousands of parameter combinations. Takes ~2-3 min per
timeframe on 1m data, much faster on higher timeframes.

**What to look for in the output:**

1. **TOP 30 BY RETURN** — the raw best performers. Note which strategy and timeframe
   dominate. If the same strategy appears across multiple timeframes, the edge is robust.

2. **TOP 30 RISK-ADJUSTED** — return/maxDD ratio. High ratio with decent trade count
   (15+) is more trustworthy than raw return.

3. **PER-STRATEGY SUMMARY** — which strategy type works best on average. If all strategies
   have negative avg return on a given timeframe, skip it.

4. **PARAMETER IMPACT** — pay attention to:
   - `vwap_window`: which lookback period works best
   - `max_hold=EOD` vs shorter holds: does the intraday drift thesis hold
   - `trend_filter`: if ON is much better, the token is trending not mean-reverting

**Red flags (skip this token):**
- All strategies have negative avg return across all timeframes
- Best return < 5% with low trade count (< 50)
- Max drawdown > 15% on the best strategy
- Win rate < 35% with R:R < 1.5

---

## Step 3.5: Run the 3-Layer Anti-Overfitting Filter

After the raw sweep, run the structured filter:

```bash
make filter-overfit SYMBOL=dogeusdt
# or:
python scripts/filter_overfit.py --symbol dogeusdt
```

For dedicated guard sweeps, pass the file suffix so the filter reads the correct CSV family:

```bash
make filter-overfit SYMBOL=icxusdt SUFFIX=pdhl_guard_sweep OUT_TAG=pdhl_guard
make filter-overfit SYMBOL=ethusdt SUFFIX=pullback_guard_sweep OUT_TAG=pullback_guard
```

For the dedicated intraday pullback grid, use the wrapper:

```bash
make filter-overfit-intraday SYMBOL=dogeusdt
```

This wrapper applies intraday-specific gates on top of the normal 3-layer flow:
- `min_trades = 50`
- `min_return = 5%`
- `max_dd = 8%`
- `min_ret_dd = 1.5`
- `min_neighbors = 12`
- `min_avg_trades_per_day = 1.0`
- reject setups with `EOD > 50%`
- reject setups with `avg_hold_minutes > 120`

Generated files:
- `data/sweeps/dogeusdt_anti_overfit_layer1.csv`
- `data/sweeps/dogeusdt_anti_overfit_layer2.csv`
- `data/sweeps/dogeusdt_anti_overfit_final.csv`

What each layer does:

1. **Layer 1 (Hard Filter)**  
   Minimum trades, minimum return, max drawdown cap, minimum `return/maxDD`.

2. **Layer 2 (Neighborhood Robustness)**  
   Keeps only configs whose nearby parameter region is also strong (not just one isolated point).

3. **Layer 3 (Monthly Consistency)**  
   Re-simulates finalists on kline data and validates:
   - positive month ratio,
   - worst month loss limit,
   - max losing streak.

Use `*_anti_overfit_final.csv` as the shortlist for detailed backtests/live candidacy.

---

## Step 3.6: Walk-Forward Validation (OOS)

After selecting candidates, validate robustness with walk-forward:

```bash
make walk-forward SYMBOL=dogeusdt TF=1m TRAIN_DAYS=180 TEST_DAYS=30 STEP_DAYS=30
# optional fast smoke test:
make walk-forward SYMBOL=dogeusdt TF=1h TRAIN_DAYS=120 TEST_DAYS=15 STEP_DAYS=15 MAX_FOLDS=2
```

If you want walk-forward on a dedicated sweep binary, pass the Rust binary and an output prefix:

```bash
make walk-forward SYMBOL=icxusdt TF=5m \
  BINARY=./backtest_sweep/target/release/pdhl_guard_sweep \
  OUT_PREFIX=icxusdt_5m_pdhl_guard \
  TRAIN_DAYS=180 TEST_DAYS=30 STEP_DAYS=30
```

For the intraday pullback grid, use:

```bash
make walk-forward-intraday SYMBOL=dogeusdt TF=5m
```

This wrapper uses:
- `BINARY=./backtest_sweep/target/release/pullback_intraday_sweep`
- `min_train_trades = 30`
- `max_train_dd = 8%`
- `min_train_return = 2%`
- `min_train_trades_per_day = 1.0`
- `max_train_eod_ratio = 50%`
- `max_train_avg_hold = 120`

Important:
- `scripts/walk_forward.py` cache keys must distinguish the sweep binary.
- Otherwise a dedicated run can accidentally reuse train caches from another sweep family and invalidate the result.

How it works:

1. For each fold, it runs **Rust sweep** on the train window only.
2. Picks the best train config (metric-based, with train filters).
3. Freezes params and evaluates on the next **out-of-sample** test window.
4. Repeats across rolling windows and aggregates OOS-only performance.

Outputs:
- `data/sweeps/<symbol>_<tf>_walkforward_folds.csv`
- `data/sweeps/<symbol>_<tf>_walkforward_summary.csv`

---

## Step 4: Run Detailed Backtest on the Champion

Once you identify the best strategy + timeframe from the sweep, update `scripts/backtest_detail.py`
with the winning parameters:

```python
CSV_FILE     = "data/klines/dogeusdt_5m_klines.csv"   # use the champion's timeframe
TP_PCT       = 0.10    # from sweep results
SL_PCT       = 0.05    # from sweep results
MIN_BARS     = 3       # from sweep results
CONFIRM_BARS = 2       # from sweep results
VWAP_PROX    = 0.005   # from sweep results (0 for Rejection strategies)
ENTRY_START  = 60      # from sweep results (60=01:00, 360=06:00)
ENTRY_CUTOFF = 1320    # from sweep results (1320=22:00, 1080=18:00)
POS_SIZE     = 0.20    # from sweep results
```

If the champion is a **Long** strategy, flip the P&L calculation in `run_backtest()`:
```python
# Short (default):
pnl_pct = (entry_price - exit_price) / entry_price

# Long (flip to):
pnl_pct = (exit_price - entry_price) / entry_price
```

Run it:

```bash
python scripts/backtest_detail.py
# or:
make backtest-detail
```

**What to verify:**
- All months profitable (or at least most)
- Max consecutive losses < 8
- Max drawdown < 10% (at 20% position size)
- EOD exits dominate (strategy relies on drift, not TP/SL scalping)
- Equity curve is steadily rising, not a few lucky spikes

The script outputs:
- `data/sweeps/champion_trades.csv` — full trade log
- `champion_analysis.html` — interactive equity curve, P&L bars, drawdown chart

---

## Step 5: Document the Strategy

Create `docs/STRATEGY_TOKEN.md` (e.g. `docs/STRATEGY_DOGEUSDT.md`).

Use this template:

```markdown
# [StrategyName] Strategy — [TOKEN] Implementation Guide

**Champion of [N] parameter sweep combinations**
**Backtested on [PAIR] [TF] candles: [START] to [END] ([N] months)**

---

## 1. Strategy Overview

[1-2 paragraphs: what the strategy does, why it works on this token]

## 2. Winning Parameters

| Parameter      | Value     | Description                                      |
|----------------|-----------|--------------------------------------------------|
| **Strategy**   | ...       | ...                                              |
| **Timeframe**  | ...       | Champion timeframe (1m / 5m / 15m / 30m / 1h)   |
| **TP**         | ...       | ...                                              |
| **SL**         | ...       | ...                                              |
| **min_bars**   | ...       | ...                                              |
| **confirm**    | ...       | ...                                              |
| **vwap_prox**  | ...       | (momentum strategies only, 0 for rejection)      |
| **vwap_window**| ...       | VWAP lookback in days                            |
| **window**     | ...       | Entry window in UTC                              |
| **max_hold**   | ...       | Force-close timing                               |
| **pos_size**   | ...       | Percentage of capital per trade                  |
| **vol_filter** | ...       | Volume filter on/off                             |
| **trend_filter** | ...     | Daily trend filter on/off                        |

## 3. Backtest Results

[Paste the detailed backtest output: trades, return, exit breakdown,
monthly P&L, hold times, risk metrics]

## 4. Entry Signal Logic

[Step-by-step pseudocode — copy from STRATEGY.md and adjust for the
specific strategy type (Short vs Long, Rejection vs Momentum)]

## 5. Exit Logic

[TP/SL/EOD rules with exact formulas]

## 6. Risk Management Rules

[Position sizing, max loss per trade, trade limits]

## 7. Implementation Notes

[Token-specific: pair name, precision/tick size, min qty, any SDK quirks]

## 8. Caveats

[Data limitations, overfitting risk, market regime dependency]

## 9. Quick Reference Card

[One-glance cheat sheet with entry/exit/sizing rules]
```

---

## Step 6: Configure for Live Trading

If the strategy passes validation, add the token config to `trader/config.py`:

```python
# Token-specific strategy parameters
NEWTOKEN_SYMBOL = "NEWUSDT"
NEWTOKEN_SYMBOL_UPPER = "NEWUSDT"
NEWTOKEN_ASSET = "NEW"
NEWTOKEN_STRATEGY_TP_PCT = 10.0
NEWTOKEN_STRATEGY_SL_PCT = 5.0
NEWTOKEN_STRATEGY_MIN_BARS = 3
# ... etc
```

Check the token's **exchange precision** before going live:

```python
# Get tick size and step size from Binance
from binance_sdk_derivatives_trading_usds_futures import DerivativesTradingUsdsFutures
client = DerivativesTradingUsdsFutures(config_rest_api=rest_config)
info = client.rest_api.exchange_information()
# Find your symbol in info.data().symbols and check:
#   - price_filter.tick_size  → decimal places for prices
#   - lot_size.step_size      → decimal places for quantities
#   - lot_size.min_qty        → minimum order quantity
#   - min_notional.notional   → minimum order value in USDT
```

If you need to reload bots after changing live config/runtime behavior, use:

```bash
make stop && make start
```

- The assistant should ask the user to run this in their own terminal.
- The assistant should not restart live bots directly unless the user explicitly requests it.

---

## Quick Checklist — Standard Onboarding

```
[ ] 0. Build sweep binary    make build-sweep   (only once, or after Rust code changes)
[ ] 1. Download data         make onboarding-download SYMBOL=dogeusdt
                             → data/klines/dogeusdt_1m_klines.csv
[ ] 2. Aggregate timeframes  python scripts/aggregate_klines.py data/klines/dogeusdt_1m_klines.csv
                             → data/klines/dogeusdt_{5m,15m,30m,1h}_klines.csv
                             (or just: make onboarding SYMBOL=dogeusdt — does steps 1+2+3)
[ ] 3. Run sweep (all TFs)   make sweep-rust SYMBOL=dogeusdt
                             → data/sweeps/dogeusdt_{1m,5m,15m,30m,1h}_sweep.csv
[ ] 4. Anti-overfit filter   make filter-overfit SYMBOL=dogeusdt
                             → data/sweeps/dogeusdt_anti_overfit_final.csv
[ ] 5. Walk-forward (OOS)    make walk-forward SYMBOL=dogeusdt TF=1m TRAIN_DAYS=180 TEST_DAYS=30 STEP_DAYS=30
[ ] 6. Identify champion     Pick from anti-overfit + walk-forward survivors
[ ] 7. Detailed backtest     Use the strategy-specific detailed backtest:
                             - MomShort/Rejection: edit scripts/backtest_detail.py
                               and run make backtest-detail
                             - PDHL: run make backtest-detail-pdhl with CLI overrides if needed
                             - VWAPPullback: edit scripts/backtest_detail_pullback.py
                               and run make backtest-detail-pullback
[ ] 8. Validate results      All months profitable? DD < 10%? Consistent?
[ ] 9. Document              docs/STRATEGY_TOKEN.md
[ ] 10. Exchange precision   Check tick_size, step_size, min_qty
[ ] 11. Config               Add to trader/config.py with interval field
[ ] 12. Paper trade          Run with --dry-run for 1-2 weeks first
```

---

## VWAPPullback Strategy — Onboarding a New Asset

The **VWAPPullback** bot (`trader pullback`) is bidirectional: it goes long in uptrends
and short in downtrends, both triggered by a VWAP pullback pattern. It works for any
USDT-M futures symbol without pre-configuration.

### Quick Checklist — VWAPPullback

```
[ ] 0. Build sweep binary    make build-sweep   (only once)
[ ] 1. Download data         make onboarding-download SYMBOL=dogeusdt
                             → data/klines/dogeusdt_1m_klines.csv
[ ] 2. Aggregate timeframes  python scripts/aggregate_klines.py data/klines/dogeusdt_1m_klines.csv
[ ] 3. Run sweep (all TFs)   make sweep-rust SYMBOL=dogeusdt
                             → data/sweeps/dogeusdt_{1m,5m,15m,30m,1h}_sweep.csv
[ ] 4. Anti-overfit filter   make filter-overfit SYMBOL=dogeusdt
                             → data/sweeps/dogeusdt_anti_overfit_final.csv
[ ] 5. Walk-forward (OOS)    make walk-forward SYMBOL=dogeusdt TF=1m TRAIN_DAYS=180 TEST_DAYS=30 STEP_DAYS=30
[ ] 6. Detailed backtest     Edit scripts/backtest_detail_pullback.py with champion TF + params
                             make backtest-detail-pullback
[ ] 7. Validate results      All months profitable? DD < 10%? Long/short balanced?
[ ] 8. Tune parameters       Edit constants at top of scripts/backtest_detail_pullback.py
[ ] 9. Document              docs/STRATEGY_TOKEN.md
[ ] 10. Exchange precision   Fetched automatically at startup (or check manually)
[ ] 11. Paper trade          Run with --dry-run for 1-2 weeks first
```

### Step 4: Run the VWAPPullback Detailed Backtest

Edit `scripts/backtest_detail_pullback.py` — update the CSV path and parameters:

```python
CSV_FILE     = "data/klines/newtoken_5m_klines.csv"  # use the champion timeframe from sweep
EMA_PERIOD   = 200      # trend filter period (try 100, 200, 500)
TP_PCT       = 0.05     # 5% take-profit
SL_PCT       = 0.025    # 2.5% stop-loss
MIN_BARS     = 3        # consolidation bars near VWAP
CONFIRM_BARS = 2        # confirmation bars after breakout
VWAP_PROX    = 0.005    # 0.5% proximity threshold
POS_SIZE     = 0.20     # 20% of capital per trade
```

Run it:

```bash
python scripts/backtest_detail_pullback.py
# or:
make backtest-detail-pullback
```

**What to look for:**

1. **Direction breakdown** — are both LONG and SHORT profitable, or only one direction?
   If only one direction works, consider using the MomShort bot instead.

2. **EOD exits** — should dominate (> 60%). High TP/SL hit rate means parameters need tuning.

3. **Monthly breakdown** — most months should be profitable.

4. **EMA period sensitivity** — try 100, 200, 500. Shorter EMA = more reactive to trend
   changes (more trades, more whipsaws). Longer = slower but more stable.

**Red flags (tune or skip):**
- Win rate < 35% with R:R < 1.5
- Max drawdown > 15%
- Only one direction (long or short) has positive average P&L
- Best month > 3× worst month (unstable edge)

### Paper Trade Mode

```bash
# Watch live signals without placing orders
poetry run python -m trader pullback --symbol NEWUSDT --dry-run \
  --tp 5.0 --sl 2.5 --ema-period 200 --min-bars 3 --confirm-bars 2

# After validation, go live
poetry run python -m trader pullback --symbol NEWUSDT \
  --leverage 3 --tp 5.0 --sl 2.5 --pos-size 0.20
```

---

## V2 Sweep (Trailing Stop — Advanced)

The V2 sweep (`make sweep-v2 SYMBOL=...`) uses a trailing R-multiple stop instead of fixed TP.
**This is not part of standard onboarding.** Only run it when explicitly requested.

```bash
make build-sweep-v2          # build once (separate Cargo project: backtest_sweep_v2/)
make sweep-v2 SYMBOL=btcusdt # results → data/sweeps/btcusdt_{1m,5m,...}_sweep_v2.csv
```

---

## File Reference

| File/Path | Purpose |
|-----------|---------|
| `scripts/fetch_klines.py` | Download 1m candle data from Binance |
| `scripts/aggregate_klines.py` | Aggregate 1m data to 5m, 15m, 30m, 1h — **run after every download** |
| `backtest_sweep/target/release/backtest_sweep` | Rust sweep binary (8 strategies: RejS, RejL, MomS, MomL, VWAPPullback, EMAScalp, ORB, PDHL) |
| `backtest_sweep_v2/target/release/backtest_sweep_v2` | V2 Rust sweep binary (trailing stop, explicit request only) |
| `scripts/backtest_detail.py` | Detailed backtest for MomShort/Rejection strategies |
| `scripts/backtest_detail_pdhl.py` | Detailed backtest for PDHL (fixed TP/SL, optional runtime protections) |
| `scripts/backtest_detail_pullback.py` | Detailed backtest for VWAPPullback (bidirectional + EMA) |
| `scripts/analyze_sweep.py` | Analyze sweep CSV results, show top configs |
| `data/klines/` | Historical kline CSVs (`SYMBOL_TF_klines.csv`) |
| `data/sweeps/` | Sweep result CSVs (`SYMBOL_TF_sweep.csv`) and TXT files |
| `docs/STRATEGY_*.md` | Per-token strategy documentation |
| `trader/config.py` | Live trading configuration (symbols + interval) |
