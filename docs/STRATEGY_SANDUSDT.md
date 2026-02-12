# MomShort Strategy — SANDUSDT Implementation Guide

**Champion of 4,838,400 parameter sweep combinations**
**Backtested on SANDUSDT 1-minute candles: 2025-02-11 to 2026-02-11 (12 months)**

---

## 1. Strategy Overview

**MomShort** (Momentum Short) is an intraday VWAP-breakdown short strategy. It detects
periods where price consolidates tightly near the intraday VWAP, then enters short when
price breaks decisively below. The position uses a tight stop-loss (0.8%) that cuts losers
quickly, while letting winners drift through the day for outsized gains.

**Core thesis:** When SAND price hugs VWAP within a very tight band (0.2%) for 5+ candles
then breaks below, the selling momentum tends to persist. Most trades get stopped out for
small losses, but the ~24% that survive produce average gains of +4.4% — a realized R:R
of 5.58:1 that more than compensates for the low win rate.

### Why it works

- **High R:R compensates for low win rate** — 23.7% wins but each winner is 5.58x the
  average loser. Expectancy is positive: `0.237 × 4.43% - 0.763 × 0.79% = +0.45%/trade`.
- **Tight stop limits damage** — 0.8% SL means max loss per trade is 0.16% of capital
  (at 20% position size). Even 14 consecutive losses only cost ~2.2%.
- **EOD exits are almost all winners** — 67 of 69 EOD exits were profitable, averaging
  +3.27%. The intraday drift thesis is confirmed.
- **Very low drawdown (3.12% max)** with 20% position sizing over a full year.
- **10 of 13 months profitable** — edge is consistent, not driven by outlier months.

---

## 2. Winning Parameters

| Parameter        | Value           | Description                                        |
|------------------|-----------------|----------------------------------------------------|
| **Strategy**     | MomShort        | VWAP consolidation breakdown short                  |
| **TP**           | 10%             | Take-profit (safety guardrail, rarely triggers 4%)  |
| **SL**           | 0.8%            | Tight stop-loss — cuts losers fast (triggers 76%)   |
| **min_bars**     | 5               | Min candles within VWAP proximity before signal     |
| **confirm**      | 2               | Confirmation candles below VWAP after breakdown     |
| **vwap_prox**    | 0.2%            | Max distance from VWAP to count as "consolidating"  |
| **vwap_window**  | 1d (intraday)   | VWAP resets daily at 00:00 UTC                      |
| **window**       | 01:00-22:00 UTC | Entry window (no entries outside this range)        |
| **max_hold**     | EOD (23:50 UTC) | Force-close at end of day                           |
| **pos_size**     | 20%             | Percentage of capital per trade                     |
| **vol_filter**   | OFF             | Volume filter disabled                              |
| **trend_filter** | OFF             | Daily trend filter disabled                         |

---

## 3. Backtest Results

```
Period:           2025-02-11 -> 2026-02-11
Initial capital:  $1,000.00
Final capital:    $1,276.02
Total return:     +27.60%
Total trades:     342
Max 1 trade/day:  Yes (strict intraday)
```

### Exit Breakdown

| Exit Reason | Count | Percentage | Avg P&L %  |
|-------------|-------|------------|------------|
| SL          | 259   | 75.7%      | -0.8%      |
| EOD         | 69    | 20.2%      | +3.17%     |
| TP          | 14    | 4.1%       | +10.0%     |

### Win/Loss Stats

| Metric                 | Value    |
|------------------------|----------|
| Win rate               | 23.7%    |
| Avg winning trade      | +4.43%   |
| Avg losing trade       | -0.79%   |
| Realized R:R           | 5.58     |
| Theoretical R:R        | 12.50    |
| Max drawdown           | 3.12%    |
| Max consecutive losses | 14       |
| Avg P&L per trade      | +$0.81   |
| Median P&L per trade   | -$1.95   |

### Monthly Breakdown

10 of 13 months profitable (2 slightly negative, 1 partial):

| Month    | Trades | Wins | Win Rate | P&L       | Capital    |
|----------|--------|------|----------|-----------|------------|
| 2025-02  | 18     | 5    | 28%      | +$31.86   | $1,031.86  |
| 2025-03  | 29     | 8    | 28%      | +$53.24   | $1,085.10  |
| 2025-04  | 29     | 4    | 14%      | -$6.72    | $1,078.38  |
| 2025-05  | 26     | 5    | 19%      | +$20.59   | $1,098.97  |
| 2025-06  | 27     | 9    | 33%      | +$25.53   | $1,124.50  |
| 2025-07  | 29     | 6    | 21%      | +$12.20   | $1,136.70  |
| 2025-08  | 29     | 8    | 28%      | +$35.36   | $1,172.06  |
| 2025-09  | 30     | 7    | 23%      | +$1.40    | $1,173.46  |
| 2025-10  | 28     | 5    | 18%      | +$3.83    | $1,177.29  |
| 2025-11  | 27     | 5    | 18%      | -$5.80    | $1,171.49  |
| 2025-12  | 31     | 6    | 19%      | +$2.80    | $1,174.29  |
| 2026-01  | 29     | 9    | 31%      | +$70.24   | $1,244.53  |
| 2026-02  | 10     | 4    | 40%      | +$31.49   | $1,276.02  |

### Hold Time Stats (minutes)

| Exit | Mean | Median | Min | Max  |
|------|------|--------|-----|------|
| TP   | 890  | 924    | 372 | 1209 |
| SL   | 230  | 122    | 2   | 1308 |
| EOD  | 1152 | 1220   | 472 | 1363 |

### EOD Exit Analysis

| EOD Outcome | Count | Avg P&L %  |
|-------------|-------|------------|
| Winners     | 67    | +3.27%     |
| Losers      | 2     | -0.09%     |

Key insight: when the trade survives the tight 0.8% stop, it almost always drifts into
profit by end of day. This confirms the momentum thesis — surviving the initial noise is
the hard part, and the drift is real.

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

    IF abs(pct_from_vwap) <= 0.2%:
        counter += 1          # Price is "near" VWAP, consolidating
        continue to next candle

Step 2: BREAKDOWN CHECK
    IF counter >= 5 AND pct_from_vwap < -0.2%:
        # Price was near VWAP for 5+ bars, now broke below
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

    if abs(pct) <= 0.002:       # within 0.2% of VWAP
        counter += 1
        continue

    if counter >= 5 and pct < -0.002:   # breakdown after consolidation
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
tp_price = entry_price * (1 - 0.10)     # 10% below entry
sl_price = entry_price * (1 + 0.008)    # 0.8% above entry

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
| Stop-loss              | 0.8% above entry     | Tight — cuts losers fast, limits damage       |
| EOD force-close        | 23:50 UTC            | No overnight risk, clean slate daily          |
| Entry window           | 01:00-22:00 UTC      | Avoid low-liquidity midnight hours            |
| No entry after cutoff  | 22:00 UTC            | Ensures enough time for confirmation + drift  |

### Position Sizing

```
max_risk_per_trade = capital * 0.20 * 0.008 = 0.16% of capital
(20% position * 0.8% stop-loss = 0.16% max capital loss per trade)
```

With fees (~0.016% round-trip on position):
```
actual_max_loss ≈ 0.176% of capital per trade
```

This is very conservative — even 14 consecutive losses (the worst streak observed)
only costs ~2.5% of capital.

---

## 7. Implementation Notes

### For Live Trading (Binance USDT-M Futures)

**Pair:** SANDUSDT (Futures)

### SANDUSDT Futures Precision (from exchange_information)

| Parameter       | Value           | Notes                                       |
|-----------------|-----------------|---------------------------------------------|
| **Price**       | tickSize=0.00001 | 5 decimal places                           |
| **Quantity**    | stepSize=1       | Whole integers only (`int(floor(q))`)       |
| **Min qty**     | 1               | Minimum 1 SAND per order                    |
| **Min notional**| $5 USDT         | Order value must exceed $5                  |
| **Price prec**  | 5 decimals      | `floor(p * 100000) / 100000`               |
| **Qty prec**    | 0 decimals      | `int(floor(q))`                             |

### Required Infrastructure

1. **1-minute kline WebSocket stream** — to receive live candles
2. **Intraday VWAP tracker** — cumulative, resets at 00:00 UTC
3. **Signal state machine** — tracks `counter` and `confirmation` state
4. **Order manager** — market orders for entry/exit, STOP_MARKET for SL

### Order Sequence on Entry Signal

1. Set leverage (5x recommended)
2. Market SELL to open short position
3. Place STOP_MARKET BUY at `entry_price * 1.008` (close-position)
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

        if abs(pct) <= 0.002:
            self.counter += 1
        elif self.counter >= 5 and pct < -0.002:
            self.counter = 0
            self.confirming = True
            self.confirm_count = 0
        else:
            self.counter = 0

        return None
```

---

## 8. Caveats and Limitations

### Strategy Character — Low Win Rate

This is a **high R:R, low win-rate** strategy. Expect:
- 3 out of 4 trades to be stopped out for small losses
- Losing streaks of 10-14 trades are normal
- Profitability comes from the ~24% of trades that run big
- **Psychologically demanding** — the median trade is a loser (-$1.95)
- Do NOT increase the stop-loss to "improve" win rate — it destroys the edge

### Overfitting Risk

- Selected from 4.8M combinations — survivorship bias is possible
- The tight 0.2% proximity threshold is very specific to SAND's price behavior
- 12 months of data is reasonable but not conclusive
- 2 losing months (Apr, Nov) show the strategy isn't bulletproof

### Market Regime Dependency

- Strategy profits from intraday bearish momentum after VWAP consolidation
- In a strong sustained uptrend, breakdowns below VWAP may fail systematically
- Consider pausing if 3+ consecutive months are negative

### Slippage Considerations

- The 0.8% SL is tight — slippage on the stop could meaningfully impact returns
- SAND futures are generally liquid but verify current order book depth
- Fees ARE modeled: 0.04% taker per side (0.08% round-trip)
- Real slippage is NOT modeled — budget ~0.01-0.03% per trade

### Comparison with AXS Strategy

| Aspect         | AXSUSDT              | SANDUSDT               |
|----------------|----------------------|------------------------|
| SL             | 5%                   | 0.8%                   |
| Win rate       | 48.5%                | 23.7%                  |
| Realized R:R   | 1.53                 | 5.58                   |
| Max DD         | 6.18%                | 3.12%                  |
| Return (ann.)  | ~33% (7mo)           | +27.6% (12mo)          |
| Character      | Balanced             | High R:R, low win-rate |
| EOD exit %     | 74.5%                | 20.2%                  |
| SL exit %      | 17.5%                | 75.7%                  |
| vwap_prox      | 0.5%                 | 0.2%                   |
| min_bars       | 3                    | 5                      |

Very different strategy profiles despite both being MomShort — SAND uses a much tighter
stop and proximity, resulting in a fundamentally different risk/reward character.

---

## 9. Sweep Parameter Insights

From the full 4.8M combination sweep on SANDUSDT:

### Best Performing Parameters

| Parameter      | Best Value | Avg Return | Notes                              |
|----------------|------------|------------|------------------------------------|
| vwap_window    | 20d        | -0.07%     | 1d also strong (champion uses 1d)  |
| trend_filter   | ON         | -0.45%     | Helps on average, but OFF won top  |
| confirm_bars   | 2          | -0.68%     | More confirmation = slightly better|
| entry_window   | 06-18      | -0.66%     | Slightly better than 01-22         |
| max_hold       | EOD        | -0.67%     | Best hold period                   |
| vol_filter     | ON         | -0.72%     | Marginal improvement               |

### Strategy Rankings

| Strategy   | Avg Return | % Profitable | Best Return |
|------------|------------|--------------|-------------|
| MomShort   | -0.59%     | 22.7%        | +27.60%     |
| MomLong    | -0.74%     | 23.9%        | +9.02%      |
| RejShort   | -0.74%     | 21.6%        | +25.34%     |
| RejLong    | -1.05%     | 19.8%        | +13.31%     |

Short strategies clearly outperform longs on SAND over this period.

---

## 10. Backtesting Artifacts

| File | Description |
|------|-------------|
| `backtest_detail.py` | Python script: detailed trade-by-trade analysis + equity chart |
| `backtest_sweep/src/main.rs` | Rust parameter sweep engine (4.8M combos in 88s) |
| `champion_trades.csv` | Full trade log (342 trades) |
| `champion_analysis.html` | Interactive equity/P&L/drawdown chart |
| `sandusdt_1m_klines.csv` | Raw 1-min candle data (525K rows) |
| `backtest_sweep.csv` | Full sweep results (4.8M rows) |
| `fetch_klines.py` | Script to download kline data from Binance API |

---

## 11. Quick Reference Card

```
STRATEGY:  MomShort (VWAP Consolidation Breakdown Short)
PAIR:      SANDUSDT (Futures)
TIMEFRAME: 1-minute candles, intraday only

ENTRY:
  1. Price within 0.2% of VWAP for 5+ consecutive bars
  2. Price breaks >0.2% BELOW VWAP
  3. Next 2 candles both close below VWAP
  → SHORT at close of 2nd confirmation candle

EXIT (checked every candle, in order):
  1. SL: high >= entry * 1.008     → buy at entry * 1.008
  2. TP: low  <= entry * 0.90      → buy at entry * 0.90
  3. EOD: time >= 23:50 UTC        → buy at market close

SIZING: 20% of capital per trade
FEES:   0.04% taker per side (0.08% round-trip)
LIMIT:  Max 1 trade per day
WINDOW: 01:00-22:00 UTC entries only

PRECISION (SANDUSDT Futures):
  Price:    5 decimal places (tickSize=0.00001)
  Quantity: whole integers (stepSize=1, minQty=1)
  Min notional: $5 USDT

EXPECTED BEHAVIOR:
  ~76% of trades hit SL (small loss: -0.16% of capital)
  ~20% exit at EOD (almost always profitable: +3.3% avg)
  ~4% hit TP (big win: +10%)
  Win rate: ~24%  |  Realized R:R: ~5.6:1
```
