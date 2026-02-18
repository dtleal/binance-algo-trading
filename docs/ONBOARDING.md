# Token Onboarding Guide

How to evaluate a new token for automated trading.

---

## Step 1: Download Historical Data

Edit `fetch_klines.py` — change the symbol and output filename:

```python
SYMBOL = "NEWUSDC"           # or "NEWUSDT" — pick the most liquid pair
CSV_FILE = "newusdc_1m_klines.csv"
```

Run it:

```bash
python fetch_klines.py
```

This downloads up to 1 year of 1-minute candles from Binance's public REST API.
Rate-limited at ~4 requests/sec, takes a few minutes for a full year (~525K candles).

**Check the output:**
- If the pair is newer than 1 year, you'll get less data (e.g. AXSUSDC started July 2025, only ~7 months)
- Minimum recommended: **3 months** of data for any meaningful backtest
- The script prints progress and final candle count

---

## Step 2: Run the Parameter Sweep

Edit `backtest_sweep/src/main.rs` — update the CSV path:

```rust
const CSV_FILE: &str = "../newusdc_1m_klines.csv";
```

Build and run:

```bash
cd backtest_sweep
cargo build --release
cargo run --release
```

This sweeps ~4.8M parameter combinations across 4 strategies (RejShort, RejLong,
MomShort, MomLong) with 12 parameters. Takes ~60-90 seconds on a modern machine.

**What to look for in the output:**

1. **TOP 30 BY RETURN** — the raw best performers. Note which strategy and VWAP window
   dominate. If multiple strategies appear, the token has diverse trading opportunities.

2. **TOP 30 RISK-ADJUSTED** — return/maxDD ratio. High ratio with decent trade count
   (15+) is more trustworthy than raw return.

3. **PER-STRATEGY SUMMARY** — which strategy type works best on average. If all 4 are
   negative, the token may not be suitable for VWAP-based strategies.

4. **PARAMETER IMPACT** — pay attention to:
   - `vwap_window`: which lookback period works best
   - `max_hold=EOD` vs shorter holds: does the intraday drift thesis hold
   - `trend_filter`: if ON is much better, the token is trending not mean-reverting

**Red flags (skip this token):**
- All strategies have negative avg return
- Best return < 5% with low trade count (< 50)
- Max drawdown > 15% on the best strategy
- Win rate < 35% with R:R < 1.5

---

## Step 3: Run Detailed Backtest on the Champion

Once you identify the best strategy from the sweep, update `backtest_detail.py` with
the winning parameters:

```python
CSV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "newusdc_1m_klines.csv")
TP_PCT = 0.10          # from sweep results
SL_PCT = 0.05          # from sweep results
MIN_BARS = 3           # from sweep results
CONFIRM_BARS = 2       # from sweep results
VWAP_PROX = 0.005      # from sweep results (0 for Rejection strategies)
ENTRY_START = 60       # from sweep results (60=01:00, 360=06:00)
ENTRY_CUTOFF = 1320    # from sweep results (1320=22:00, 1080=18:00)
POS_SIZE = 0.20        # from sweep results
```

If the champion is a **Long** strategy, you also need to flip the P&L calculation
in `run_backtest()`:
```python
# Short (default):
pnl_pct = (entry_price - exit_price) / entry_price

# Long (flip to):
pnl_pct = (exit_price - entry_price) / entry_price
```

And flip TP/SL prices:
```python
# Short (default):
tp_price = entry_price * (1 - TP_PCT)
sl_price = entry_price * (1 + SL_PCT)

# Long (flip to):
tp_price = entry_price * (1 + TP_PCT)
sl_price = entry_price * (1 - SL_PCT)
```

Run it:

```bash
python backtest_detail.py
```

**What to verify:**
- All months profitable (or at least most)
- Max consecutive losses < 8
- Max drawdown < 10% (at 20% position size)
- EOD exits dominate (strategy relies on drift, not TP/SL scalping)
- Equity curve is steadily rising, not a few lucky spikes

The script outputs:
- `champion_trades.csv` — full trade log
- `champion_analysis.html` — interactive equity curve, P&L bars, drawdown chart

---

## Step 4: Document the Strategy

Create `docs/STRATEGY_TOKEN.md` (e.g. `docs/STRATEGY_AXSUSDT.md`).

Use this template:

```markdown
# [StrategyName] Strategy — [TOKEN] Implementation Guide

**Champion of [N] parameter sweep combinations**
**Backtested on [PAIR] 1-minute candles: [START] to [END] ([N] months)**

---

## 1. Strategy Overview

[1-2 paragraphs: what the strategy does, why it works on this token]

## 2. Winning Parameters

| Parameter      | Value     | Description                                      |
|----------------|-----------|--------------------------------------------------|
| **Strategy**   | ...       | ...                                              |
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

## Step 5: Configure for Live Trading

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

---

## Quick Checklist — MomShort

```
[ ] 1. Download data         python fetch_klines.py
[ ] 2. Run sweep             cd backtest_sweep && cargo run --release
[ ] 3. Identify champion     Check TOP 30 BY RETURN + RISK-ADJUSTED tables
[ ] 4. Detailed backtest     python backtest_detail.py (with champion params)
[ ] 5. Validate results      All months profitable? DD < 10%? Consistent?
[ ] 6. Document              docs/STRATEGY_TOKEN.md
[ ] 7. Exchange precision    Check tick_size, step_size, min_qty
[ ] 8. Config                Add to trader/config.py
[ ] 9. Paper trade           Run for 1-2 weeks with tiny size first
```

---

## VWAPPullback Strategy — Onboarding a New Asset

The **VWAPPullback** bot (`trader pullback`) is bidirectional: it goes long in uptrends
and short in downtrends, both triggered by a VWAP pullback pattern. It works for any
USDT-M futures symbol without pre-configuration.

### Quick Checklist — VWAPPullback

```
[ ] 1. Download data         python fetch_klines.py  (same as above)
[ ] 2. Detailed backtest     python backtest_detail_pullback.py
[ ] 3. Validate results      All months profitable? DD < 10%? Long/short balanced?
[ ] 4. Tune parameters       Edit constants at top of backtest_detail_pullback.py
[ ] 5. Document              docs/STRATEGY_TOKEN.md
[ ] 6. Exchange precision    Fetched automatically at startup (or check manually)
[ ] 7. Paper trade           Run with --dry-run for 1-2 weeks first
```

### Step 2: Run the VWAPPullback Backtest

Edit `backtest_detail_pullback.py` — update the CSV path and parameters:

```python
CSV_FILE     = "newtoken_1m_klines.csv"
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
python backtest_detail_pullback.py
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

### Step 7: Run in Paper Trade Mode

```bash
# Watch live signals without placing orders
poetry run python -m trader pullback --symbol NEWUSDT --dry-run \
  --tp 5.0 --sl 2.5 --ema-period 200 --min-bars 3 --confirm-bars 2

# After validation, go live
poetry run python -m trader pullback --symbol NEWUSDT \
  --leverage 3 --tp 5.0 --sl 2.5 --pos-size 0.20
```

---

## File Reference

| File | Purpose |
|------|---------|
| `fetch_klines.py` | Download 1m candle data from Binance |
| `backtest_sweep/src/main.rs` | Rust parameter sweep engine (~4.8M combos) — MomShort/Long |
| `backtest_detail.py` | Detailed backtest for MomShort strategy |
| `backtest_detail_pullback.py` | Detailed backtest for VWAPPullback (bidirectional + EMA) |
| `backtest_sweep.csv` | Full sweep results |
| `champion_trades.csv` | Trade log from MomShort detailed backtest |
| `champion_analysis.html` | Interactive chart from MomShort detailed backtest |
| `pullback_trades.csv` | Trade log from VWAPPullback detailed backtest |
| `pullback_analysis.html` | Interactive chart from VWAPPullback detailed backtest |
| `docs/STRATEGY_*.md` | Per-token strategy documentation |
| `trader/config.py` | Live trading configuration (MomShort symbols) |
