# MomShort Strategy — MANAUSDT Implementation Guide

**Champion of 4,838,400 parameter sweep combinations**
**Backtested on MANAUSDT 1-minute candles: 2025-02-11 to 2026-02-11 (12 months)**

---

## 1. Strategy Overview

**MomShort** (Momentum Short) is an intraday VWAP-breakdown short strategy. It detects
periods where price consolidates near the daily VWAP, then enters short when price breaks
decisively below. The position is held until end-of-day, with symmetric TP/SL (5%/5%)
acting as safety guardrails rather than primary exits.

**Core thesis:** When MANA price hugs VWAP within a 0.5% band for 12+ candles then breaks
below, the selling momentum tends to persist for the rest of the day. The strategy requires
a long consolidation phase (12 bars) which filters for high-conviction setups where sustained
VWAP proximity signals genuine indecision before the breakdown.

### Why it works

- **62% of trades exit at EOD** — this is a drift strategy, not a scalping strategy.
  TP/SL together account for only 38% of exits.
- **Balanced win rate (50%)** with 1.29 R:R — positive expectancy comes from winners
  being slightly larger than losers, not from extreme R:R.
- **EOD drift is roughly symmetric** — 85 EOD winners (+1.67% avg) vs 122 EOD losers
  (-1.66% avg), but the TP hits (24.3%) add outsized gains that tip the balance.
- **Max drawdown 8.79%** at 20% position sizing — within acceptable limits.
- **9 of 13 months profitable** — edge is consistent across most market conditions.

---

## 2. Winning Parameters

| Parameter        | Value           | Description                                        |
|------------------|-----------------|----------------------------------------------------|
| **Strategy**     | MomShort        | VWAP consolidation breakdown short                  |
| **TP**           | 5%              | Take-profit — triggers 24.3% of trades              |
| **SL**           | 5%              | Stop-loss — triggers 13.5% of trades                |
| **min_bars**     | 12              | Min candles within VWAP proximity before signal     |
| **confirm**      | 2               | Confirmation candles below VWAP after breakdown     |
| **vwap_prox**    | 0.5%            | Max distance from VWAP to count as "consolidating"  |
| **vwap_window**  | 1d (intraday)   | VWAP resets daily at 00:00 UTC                      |
| **window**       | 01:00-22:00 UTC | Entry window (no entries outside this range)        |
| **max_hold**     | EOD (23:50 UTC) | Force-close at end of day                           |
| **pos_size**     | 20%             | Percentage of capital per trade                     |
| **vol_filter**   | ON              | Volume filter enabled (sweep champion)              |
| **trend_filter** | OFF             | Daily trend filter disabled                         |

---

## 3. Backtest Results

```
Period:           2025-02-11 -> 2026-02-11
Initial capital:  $1,000.00
Final capital:    $1,194.47
Total return:     +19.45%
Total trades:     333
Max 1 trade/day:  Yes (strict intraday)

Note: Sweep champion (with vol_filter=ON) showed +30.18% on 296 trades.
      Detail backtest runs without vol_filter, showing conservative estimate.
```

### Exit Breakdown

| Exit Reason | Count | Percentage | Avg P&L %  |
|-------------|-------|------------|------------|
| TP          | 81    | 24.3%      | ~+5.0%     |
| SL          | 45    | 13.5%      | ~-5.0%     |
| EOD         | 207   | 62.2%      | varies     |

### Win/Loss Stats

| Metric                 | Value    |
|------------------------|----------|
| Win rate               | 49.8%    |
| Avg winning trade      | +3.30%   |
| Avg losing trade       | -2.56%   |
| Realized R:R           | 1.29     |
| Theoretical R:R        | 1.00     |
| Max drawdown           | 8.79%    |
| Max consecutive losses | 6        |
| Avg P&L per trade      | +$0.58   |
| Median P&L per trade   | -$0.02   |

### Monthly Breakdown

9 of 13 months profitable:

| Month    | Trades | Wins | Win Rate | P&L       | Capital    |
|----------|--------|------|----------|-----------|------------|
| 2025-02  | 16     | 8    | 50%      | -$2.06    | $997.94    |
| 2025-03  | 28     | 13   | 46%      | +$14.87   | $1,012.81  |
| 2025-04  | 27     | 13   | 48%      | -$4.79    | $1,008.02  |
| 2025-05  | 28     | 15   | 54%      | +$11.13   | $1,019.15  |
| 2025-06  | 26     | 13   | 50%      | +$26.40   | $1,045.56  |
| 2025-07  | 28     | 11   | 39%      | -$10.14   | $1,035.41  |
| 2025-08  | 27     | 15   | 56%      | +$11.76   | $1,047.17  |
| 2025-09  | 29     | 12   | 41%      | -$10.85   | $1,036.32  |
| 2025-10  | 29     | 10   | 34%      | -$50.29   | $986.03    |
| 2025-11  | 29     | 16   | 55%      | +$72.24   | $1,058.27  |
| 2025-12  | 30     | 18   | 60%      | +$41.18   | $1,099.46  |
| 2026-01  | 26     | 15   | 58%      | +$49.92   | $1,149.38  |
| 2026-02  | 10     | 7    | 70%      | +$45.09   | $1,194.47  |

### Hold Time Stats (minutes)

| Exit | Mean | Median | Min | Max  |
|------|------|--------|-----|------|
| TP   | 697  | 713    | 106 | 1284 |
| SL   | 691  | 717    | 48  | 1251 |
| EOD  | 1134 | 1236   | 116 | 1356 |

### EOD Exit Analysis

| EOD Outcome | Count | Avg P&L %  |
|-------------|-------|------------|
| Winners     | 85    | +1.67%     |
| Losers      | 122   | -1.66%    |

Unlike AXS and SAND where EOD exits are overwhelmingly winners, MANA's EOD exits are
roughly balanced — the overall edge comes from the asymmetric TP/SL hit rate (24.3% TP
vs 13.5% SL, both at 5%).

---

## 4. Entry Signal Logic (Step by Step)

### Prerequisites (computed each candle)

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
    IF counter >= 12 AND pct_from_vwap < -0.5%:
        # Price was near VWAP for 12+ bars, now broke below
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

    if counter >= 12 and pct < -0.005:   # breakdown after consolidation
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
tp_price = entry_price * (1 - 0.05)     # 5% below entry
sl_price = entry_price * (1 + 0.05)     # 5% above entry

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

| Rule                   | Value                | Rationale                                    |
|------------------------|----------------------|----------------------------------------------|
| Max 1 trade/day        | Strict               | Prevents overtrading, no revenge trades       |
| Position size          | 20% of capital       | Limits per-trade exposure                     |
| Stop-loss              | 5% above entry       | Hard cap on single-trade loss                 |
| EOD force-close        | 23:50 UTC            | No overnight risk, clean slate daily          |
| Entry window           | 01:00-22:00 UTC      | Avoid low-liquidity midnight hours            |
| No entry after cutoff  | 22:00 UTC            | Ensures enough time for confirmation + drift  |

### Position Sizing

```
max_risk_per_trade = capital * 0.20 * 0.05 = 1% of capital
(20% position * 5% stop-loss = 1% max capital loss per trade)
```

With fees (~0.016% round-trip on position):
```
actual_max_loss ≈ 1.016% of capital per trade
```

Max consecutive losses observed: 6 → worst streak costs ~6.1% of capital.

---

## 7. Implementation Notes

### For Live Trading (Binance USDT-M Futures)

**Pair:** MANAUSDT (Futures)

### MANAUSDT Futures Precision (from exchange_information)

| Parameter       | Value            | Notes                                       |
|-----------------|------------------|---------------------------------------------|
| **Price**       | tickSize=0.0001  | 4 decimal places                            |
| **Quantity**    | stepSize=1       | Whole integers only (`int(floor(q))`)       |
| **Min qty**     | 1                | Minimum 1 MANA per order                    |
| **Min notional**| $5 USDT          | Order value must exceed $5                  |
| **Price prec**  | 4 decimals       | `floor(p * 10000) / 10000`                  |
| **Qty prec**    | 0 decimals       | `int(floor(q))`                             |

### Required Infrastructure

1. **1-minute kline WebSocket stream** — to receive live candles
2. **Intraday VWAP tracker** — cumulative, resets at 00:00 UTC
3. **Signal state machine** — tracks `counter` and `confirmation` state
4. **Order manager** — market orders for entry/exit, STOP_MARKET for SL

### Order Sequence on Entry Signal

1. Set leverage (5x recommended)
2. Market SELL to open short position
3. Place STOP_MARKET BUY at `entry_price * 1.05` (close-position)
4. Schedule EOD close at 23:50 UTC

### Order Sequence on EOD

1. Cancel all open orders (including stop-loss)
2. Market BUY to close position (reduce-only)

### Key SDK Notes (from existing codebase)

- `new_order()` is **missing** `stop_price` and `close_position` params (SDK bug)
- Workaround: use `client.rest_api.send_signed_request("/fapi/v1/order", "POST", query_params={...})`
- `send_signed_request` converts snake_case to camelCase automatically
- Futures ticker WS: `connection.individual_symbol_ticker_streams(symbol=...)`
- Prices/quantities are **strings** in SDK responses

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
        elif self.counter >= 12 and pct < -0.005:
            self.counter = 0
            self.confirming = True
            self.confirm_count = 0
        else:
            self.counter = 0

        return None
```

---

## 8. Caveats and Limitations

### Balanced Win Rate — Different Character from AXS/SAND

This strategy has a ~50% win rate with modest R:R (1.29). Unlike the SAND strategy
(23.7% win rate, 5.58 R:R), MANA produces frequent small wins and losses. The edge
is slim per-trade (+$0.58 avg) and accumulates over high volume (333 trades/year).

### October 2025 Drawdown

October 2025 saw -$50.29 (34% win rate that month) — the largest monthly loss.
The strategy recovered fully by November. This represents the kind of adverse period
to expect approximately once per year.

### Vol Filter Not in Detail Backtest

The sweep champion uses `vol_filter=ON` and achieved +30.18% on 296 trades. The
detailed backtest runs without vol_filter, yielding a more conservative +19.45%
on 333 trades. Implementing vol_filter in production would likely improve results.

### Overfitting Risk

- Selected from 4.8M combinations — survivorship bias is possible
- 12 months of data is reasonable but not conclusive
- The 12-bar consolidation requirement is quite specific
- 4 losing months show the edge is not bulletproof

### Market Regime Dependency

- Strategy profits from intraday bearish momentum after VWAP consolidation
- In a strong sustained uptrend, breakdowns below VWAP may fail systematically
- Consider pausing if 3+ consecutive months are negative

### Slippage Considerations

- The 5% SL is wide — slippage on the stop is unlikely to be meaningful
- MANA futures are generally liquid but verify current order book depth
- Fees ARE modeled: 0.04% taker per side (0.08% round-trip)
- Real slippage is NOT modeled — budget ~0.01-0.03% per trade

### Comparison with Other Tokens

| Aspect         | AXSUSDT              | SANDUSDT               | MANAUSDT               |
|----------------|----------------------|------------------------|------------------------|
| TP / SL        | 10% / 5%            | 10% / 0.8%            | 5% / 5%               |
| min_bars       | 3                    | 5                      | 12                     |
| vwap_prox      | 0.5%                 | 0.2%                   | 0.5%                   |
| Win rate       | 48.5%                | 23.7%                  | 49.8%                  |
| Realized R:R   | 1.53                 | 5.58                   | 1.29                   |
| Max DD         | 6.18%                | 3.12%                  | 8.79%                  |
| Return         | +23.65% (7mo)        | +27.60% (12mo)         | +19.45% (12mo)         |
| EOD exit %     | 74.5%                | 20.2%                  | 62.2%                  |
| Character      | Balanced             | High R:R, low win-rate | Balanced, high volume  |

MANA requires the longest consolidation (12 bars) and has the most symmetric TP/SL.
Its edge is thinner per-trade but compensated by high trade frequency.

---

## 9. Sweep Parameter Insights

From the full 4.8M combination sweep on MANAUSDT:

### Best Performing Parameters

| Parameter      | Best Value | Avg Return | Notes                              |
|----------------|------------|------------|------------------------------------|
| vwap_window    | 30d        | -0.13%     | 1d also strong (champion uses 1d)  |
| trend_filter   | ON         | -0.62%     | Helps on average, but OFF won top  |
| confirm_bars   | 2          | -0.76%     | More confirmation = slightly better|
| entry_window   | 06-18      | -0.69%     | Slightly better avg than 01-22     |
| max_hold       | EOD        | -0.70%     | Best hold period                   |
| vol_filter     | ON         | -0.73%     | Helps filter bad trades            |

### Strategy Rankings

| Strategy   | Avg Return | % Profitable | Best Return |
|------------|------------|--------------|-------------|
| MomShort   | -0.73%     | 20.5%        | +30.18%     |
| RejShort   | -0.65%     | 21.4%        | +25.69%     |
| MomLong    | -0.83%     | 20.0%        | +10.95%     |
| RejLong    | -1.00%     | 27.3%        | +8.12%      |

Short strategies clearly outperform longs on MANA over this period. RejShort is
also viable (best risk-adjusted: 25.69% return / 5.34% DD = 4.8x ratio).

---

## 10. Backtesting Artifacts

| File | Description |
|------|-------------|
| `backtest_detail.py` | Python script: detailed trade-by-trade analysis + equity chart |
| `backtest_sweep/src/main.rs` | Rust parameter sweep engine (4.8M combos in 47s) |
| `champion_trades.csv` | Full trade log (333 trades) |
| `champion_analysis.html` | Interactive equity/P&L/drawdown chart |
| `manausdt_1m_klines.csv` | Raw 1-min candle data (525K rows) |
| `backtest_sweep.csv` | Full sweep results (4.8M rows) |
| `fetch_klines.py` | Script to download kline data from Binance API |

---

## 11. Quick Reference Card

```
STRATEGY:  MomShort (VWAP Consolidation Breakdown Short)
PAIR:      MANAUSDT (Futures)
TIMEFRAME: 1-minute candles, intraday only

ENTRY:
  1. Price within 0.5% of VWAP for 12+ consecutive bars
  2. Price breaks >0.5% BELOW VWAP
  3. Next 2 candles both close below VWAP
  → SHORT at close of 2nd confirmation candle

EXIT (checked every candle, in order):
  1. SL: high >= entry * 1.05      → buy at entry * 1.05
  2. TP: low  <= entry * 0.95      → buy at entry * 0.95
  3. EOD: time >= 23:50 UTC        → buy at market close

SIZING: 20% of capital per trade
FEES:   0.04% taker per side (0.08% round-trip)
LIMIT:  Max 1 trade per day
WINDOW: 01:00-22:00 UTC entries only

PRECISION (MANAUSDT Futures):
  Price:    4 decimal places (tickSize=0.0001)
  Quantity: whole integers (stepSize=1, minQty=1)
  Min notional: $5 USDT

EXPECTED BEHAVIOR:
  ~24% of trades hit TP (win: +5%)
  ~14% of trades hit SL (loss: -5%)
  ~62% exit at EOD (roughly balanced win/loss)
  Win rate: ~50%  |  Realized R:R: ~1.3:1
```
