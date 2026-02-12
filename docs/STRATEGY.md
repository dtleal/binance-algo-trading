# MomShort Strategy — Implementation Guide

**Champion of 967,680 parameter sweep combinations**
**Backtested on AXSUSDC 1-minute candles: 2025-07-15 to 2026-02-10 (~7 months)**

---

## 1. Strategy Overview

**MomShort** (Momentum Short) is an intraday VWAP-breakdown short strategy. It detects
periods where price consolidates near the daily VWAP, then enters short when price breaks
decisively below. The position is held until end-of-day, with wide TP/SL acting as safety
guardrails rather than primary exits.

**Core thesis:** When price hugs VWAP for multiple candles then breaks below, the selling
momentum tends to persist for the rest of the day. The strategy captures this asymmetric
intraday drift.

### Why it works

- **74.5% of trades exit at EOD** — this is NOT a scalping strategy. TP/SL rarely trigger.
- **Asymmetric EOD drift:** winning trades drift an average of +3.07% in our favor by EOD,
  while losing trades only drift -1.59% against us. The winners run further than the losers.
- **All 7 backtested months were profitable** — the edge is consistent, not a few lucky trades.
- **Low drawdown (6.18% max)** with 20% position sizing — capital preservation is strong.

---

## 2. Winning Parameters

| Parameter      | Value     | Description                                      |
|----------------|-----------|--------------------------------------------------|
| **Strategy**   | MomShort  | VWAP consolidation breakdown short                |
| **TP**         | 10%       | Take-profit (safety guardrail, rarely triggers)   |
| **SL**         | 5%        | Stop-loss (safety guardrail, triggers ~17.5%)     |
| **min_bars**   | 3         | Min candles within VWAP proximity before signal   |
| **confirm**    | 2         | Confirmation candles below VWAP after breakdown   |
| **vwap_prox**  | 0.5%      | Max distance from VWAP to count as "consolidating"|
| **window**     | 01:00-22:00 UTC | Entry window (no entries before 01:00 or after 22:00) |
| **max_hold**   | EOD (23:50 UTC) | Force-close at end of day                   |
| **pos_size**   | 20%       | Percentage of capital per trade                   |
| **vol_filter** | OFF       | Volume filter disabled                            |
| **trend_filter** | OFF     | Daily trend filter disabled                       |

---

## 3. Backtest Results

```
Period:           2025-07-15 -> 2026-02-09
Initial capital:  $1,000.00
Final capital:    $1,236.52
Total return:     +23.65%
Total trades:     200
Max 1 trade/day:  Yes (strict intraday)
```

### Exit Breakdown

| Exit Reason | Count | Percentage | Avg P&L %  |
|-------------|-------|------------|------------|
| EOD         | 149   | 74.5%      | varies     |
| SL          | 35    | 17.5%      | -5.0%      |
| TP          | 16    | 8.0%       | +10.0%     |

### Win/Loss Stats

| Metric              | Value    |
|---------------------|----------|
| Win rate            | 48.5%    |
| Avg winning trade   | +3.07%   |
| Avg losing trade    | -1.59%   |
| Realized R:R        | 1.53     |
| Theoretical R:R     | 2.00     |
| Max drawdown        | 6.18%    |
| Max consecutive losses | ~6    |
| Avg P&L per trade   | +0.12%   |

### Monthly Breakdown

All months were profitable:

| Month    | Trades | Wins | Win Rate | P&L     |
|----------|--------|------|----------|---------|
| 2025-07  | 15     | 7    | 47%      | +$5.19  |
| 2025-08  | 30     | 15   | 50%      | +$26.82 |
| 2025-09  | 29     | 13   | 45%      | +$20.98 |
| 2025-10  | 30     | 17   | 57%      | +$52.56 |
| 2025-11  | 29     | 14   | 48%      | +$44.80 |
| 2025-12  | 31     | 14   | 45%      | +$29.27 |
| 2026-01  | 30     | 14   | 47%      | +$47.88 |
| 2026-02  | 6      | 3    | 50%      | +$9.02  |

### Hold Time Stats (minutes)

| Exit | Mean | Median | Min | Max  |
|------|------|--------|-----|------|
| TP   | 721  | 775    | 37  | 1289 |
| SL   | 598  | 578    | 12  | 1289 |
| EOD  | 1100 | 1131   | 152 | 1369 |

---

## 4. Entry Signal Logic (Step by Step)

### Prerequisites (computed once per day)

1. **Compute Intraday VWAP** — resets at 00:00 UTC each day:
   ```
   typical_price = (high + low + close) / 3
   cumulative_pv += typical_price * volume     (resets daily)
   cumulative_vol += volume                     (resets daily)
   vwap = cumulative_pv / cumulative_vol
   ```

2. **Time window check** — only consider candles between 01:00 UTC and 22:00 UTC.

### Signal Detection (1-minute candle loop)

For each 1-minute candle during the entry window:

```
Step 1: CONSOLIDATION PHASE
    pct_from_vwap = (close - vwap) / vwap

    IF abs(pct_from_vwap) <= 0.5%:
        counter += 1          # Price is "near" VWAP, consolidating
        continue to next candle

Step 2: BREAKDOWN CHECK
    IF counter >= 3 AND pct_from_vwap < -0.5%:
        # Price was near VWAP for 3+ bars, now broke below
        → proceed to CONFIRMATION
    ELSE:
        counter = 0           # Reset, pattern broken
        continue to next candle

Step 3: CONFIRMATION (2 consecutive candles)
    FOR each of the next 2 candles:
        IF candle.close >= candle.vwap:
            → confirmation FAILED, skip entry
        IF candle.minute >= 22:00 (entry cutoff):
            → confirmation FAILED, too late in the day

    IF both candles closed below VWAP:
        → ENTRY SIGNAL CONFIRMED

Step 4: ENTER SHORT
    entry_price = close of the last confirmation candle
    → Only 1 trade per day (break after entry)
```

### Pseudocode

```python
counter = 0

for each 1-minute candle in today:
    if candle.time < 01:00 UTC:
        counter = 0
        continue
    if candle.time >= 22:00 UTC:
        continue  # no new entries after cutoff

    pct = (candle.close - candle.vwap) / candle.vwap

    if abs(pct) <= 0.005:       # within 0.5% of VWAP
        counter += 1
        continue

    if counter >= 3 and pct < -0.005:   # breakdown after consolidation
        counter = 0

        # Confirm: next 2 candles must close below VWAP
        ok = True
        for j in range(2):
            next_candle = get_next_candle()
            if next_candle is None or next_candle.time >= 22:00:
                ok = False
                break
            if next_candle.close >= next_candle.vwap:
                ok = False
                break

        if ok:
            ENTER SHORT at next_candle.close
            break   # max 1 trade per day
    else:
        counter = 0
        continue
```

---

## 5. Exit Logic

Once a short position is open:

```
tp_price = entry_price * (1 - 0.10)    # 10% below entry
sl_price = entry_price * (1 + 0.05)    # 5% above entry

FOR each subsequent 1-minute candle:

    1. CHECK SL FIRST (high >= sl_price):
       → Exit at sl_price (buy to cover)
       → Reason: "SL"

    2. CHECK TP (low <= tp_price):
       → Exit at tp_price (buy to cover)
       → Reason: "TP"

    3. CHECK END OF DAY (candle.time >= 23:50 UTC):
       → Exit at candle.close (buy to cover)
       → Reason: "EOD"

Note: SL is checked BEFORE TP on each candle (worst-case assumption).
```

### P&L Calculation

```
pnl_pct = (entry_price - exit_price) / entry_price    # short: profit when price drops
position_size = capital * 0.20
gross_pnl = position_size * pnl_pct
fees = position_size * 0.0004 * 2                      # 0.04% taker fee, entry + exit
net_pnl = gross_pnl - fees
```

---

## 6. Risk Management Rules

| Rule                  | Value                | Rationale                                    |
|-----------------------|----------------------|----------------------------------------------|
| Max 1 trade/day       | Strict               | Prevents overtrading, no revenge trades       |
| Position size          | 20% of capital       | Limits per-trade exposure                     |
| Stop-loss             | 5% above entry       | Hard cap on single-trade loss                 |
| EOD force-close       | 23:50 UTC            | No overnight risk, clean slate daily          |
| Entry window          | 01:00-22:00 UTC      | Avoid low-liquidity midnight hours            |
| No entry after cutoff | 22:00 UTC            | Ensures enough time for confirmation + drift  |

### Position Sizing

```
max_risk_per_trade = capital * 0.20 * 0.05 = 1% of capital
(20% position * 5% stop-loss = 1% max capital loss per trade)
```

With fees (~0.016% round-trip on position):
```
actual_max_loss ≈ 1.016% of capital per trade
```

---

## 7. Implementation Notes

### For Live Trading (Binance USDT-M Futures)

**Pair:** AXSUSDT (futures, not spot — AXSUSDC was used for backtest data only)

**Required infrastructure:**
1. **1-minute kline WebSocket stream** — to receive live candles
2. **Intraday VWAP tracker** — cumulative, resets at 00:00 UTC
3. **Signal state machine** — tracks `counter` and `confirmation` state
4. **Order manager** — market orders for entry/exit, STOP_MARKET for SL

**Order sequence on entry signal:**
1. Set leverage (5x recommended, matches existing config)
2. Market SELL to open short position
3. Place STOP_MARKET BUY at `entry_price * 1.05` (close-position)
4. Schedule EOD close at 23:50 UTC

**Order sequence on EOD:**
1. Cancel all open orders (including stop-loss)
2. Market BUY to close position (reduce-only)

**Key SDK notes (from existing codebase):**
- Use `new_algo_order()` for STOP_MARKET with `close_position="true"`
- Futures ticker WS: `connection.individual_symbol_ticker_streams(symbol=...)`
- `send_signed_request` converts snake_case to camelCase automatically
- Prices are strings in SDK, quantities rounded to 1 decimal for AXS

### VWAP Computation (Live)

```python
# Reset at 00:00 UTC each day
cumulative_pv = 0.0
cumulative_vol = 0.0

def on_kline_close(candle):
    global cumulative_pv, cumulative_vol
    tp = (candle.high + candle.low + candle.close) / 3
    cumulative_pv += tp * candle.volume
    cumulative_vol += candle.volume
    vwap = cumulative_pv / cumulative_vol if cumulative_vol > 0 else candle.close
    return vwap
```

### State Machine

```python
class MomShortSignal:
    def __init__(self):
        self.counter = 0
        self.confirming = False
        self.confirm_count = 0
        self.traded_today = False

    def reset_daily(self):
        self.counter = 0
        self.confirming = False
        self.confirm_count = 0
        self.traded_today = False

    def on_candle(self, close, vwap, minute_utc) -> str | None:
        """Returns 'ENTER_SHORT' or None."""
        if self.traded_today:
            return None
        if minute_utc < 60 or minute_utc >= 1320:
            if minute_utc < 60:
                self.counter = 0
            return None

        if self.confirming:
            if close < vwap:
                self.confirm_count += 1
                if self.confirm_count >= 2:
                    self.confirming = False
                    self.traded_today = True
                    return "ENTER_SHORT"
            else:
                self.confirming = False
                self.confirm_count = 0
                self.counter = 0
            return None

        pct = (close - vwap) / vwap if vwap > 0 else 0

        if abs(pct) <= 0.005:
            self.counter += 1
        elif self.counter >= 3 and pct < -0.005:
            self.counter = 0
            self.confirming = True
            self.confirm_count = 0
            # The breakdown candle itself counts as first "outside" candle
            # but confirmation checks the NEXT candles
        else:
            self.counter = 0

        return None
```

---

## 8. Caveats and Limitations

### Overfitting Risk
- **Only 7 months of data** on a single pair (AXSUSDC started July 2025)
- The parameter set was selected from ~1M combinations — survivorship bias is possible
- All months profitable is encouraging, but more out-of-sample data is needed

### Market Regime Dependency
- Strategy exploits the bearish/mean-reverting nature of AXS during the backtest period
- In a strong uptrend, the "below VWAP" breakdown may fail systematically
- Consider pausing the strategy if multiple consecutive months are negative

### Slippage Not Modeled
- Backtest assumes exact fills at TP/SL/close prices
- Real slippage on AXS futures is typically small (liquid market) but non-zero
- Fees ARE modeled: 0.04% taker per side (0.08% round-trip)

### Single Pair
- Only tested on AXSUSDT — may not generalize to other altcoins
- VWAP behavior varies significantly between assets

### No Take-Profit Optimization
- The 10% TP rarely triggers (8% of trades)
- A tighter trailing stop or dynamic TP based on intraday ATR could improve returns
- This is the most obvious area for future improvement

---

## 9. Backtesting Artifacts

| File | Description |
|------|-------------|
| `backtest_detail.py` | Python script: detailed trade-by-trade analysis + equity chart |
| `backtest_sweep/src/main.rs` | Rust parameter sweep engine (967K combos in 46s) |
| `backtest_sweep/champion_trades.csv` | Full trade log (200 trades) |
| `backtest_sweep/champion_analysis.html` | Interactive equity/P&L/drawdown chart |
| `axsusdc_1m_klines.csv` | Raw 1-min candle data (303K rows, ~35MB) |
| `backtest_sweep.csv` | Full sweep results (967K rows) |
| `fetch_klines.py` | Script to re-download kline data from Binance API |

---

## 10. Quick Reference Card

```
STRATEGY:  MomShort (VWAP Consolidation Breakdown Short)
PAIR:      AXSUSDT (Futures)
TIMEFRAME: 1-minute candles, intraday only

ENTRY:
  1. Price within 0.5% of VWAP for 3+ consecutive bars
  2. Price breaks >0.5% BELOW VWAP
  3. Next 2 candles both close below VWAP
  → SHORT at close of 2nd confirmation candle

EXIT (checked every candle, in order):
  1. SL: high >= entry * 1.05      → buy at entry * 1.05
  2. TP: low  <= entry * 0.90      → buy at entry * 0.90
  3. EOD: time >= 23:50 UTC        → buy at market close

SIZING: 20% of capital per trade
FEES:   0.04% taker per side (0.08% round-trip)
LIMIT:  Max 1 trade per day
WINDOW: 01:00-22:00 UTC entries only
```
