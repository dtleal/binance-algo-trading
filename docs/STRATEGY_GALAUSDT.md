# MomShort Strategy — GALAUSDT Implementation Guide

**Champion of 4,838,400 parameter sweep combinations**
**Backtested on GALAUSDT 1-minute candles: 2025-02-11 to 2026-02-11 (12 months)**

**NOTE:** All strategies had negative average return in the sweep — a red flag per the
onboarding guide. The champion is an outlier (+35.5% vs -0.91% MomShort average).
Overfitting risk is elevated. Proceed with extra caution.

---

## 1. Strategy Overview

**MomShort** (Momentum Short) is an intraday VWAP-breakdown short strategy. It detects
periods where price consolidates tightly near the intraday VWAP, then enters short when
price breaks decisively below. The position is held until end-of-day, take-profit, or
stop-loss — whichever comes first.

**Core thesis:** When GALA price hugs VWAP within a tight band (0.2%) for 5+ candles
then breaks below, the selling momentum tends to persist through the trading day. With
a symmetric 5%/5% TP/SL, the edge comes from a moderate win rate (56.6%) combined
with slightly larger average wins than losses (realized R:R of 1.14).

### Why it works (with caveats)

- **Balanced win rate** — 56.6% winners with a realized R:R of 1.14 gives positive
  expectancy: `0.566 × 3.28% - 0.434 × 2.88% = +0.61%/trade`.
- **EOD exits dominate** (57.2%) — confirms the intraday drift thesis, though EOD
  exits are nearly 50/50 win/loss (86 wins vs 84 losses), meaning the drift edge is thin.
- **10 of 13 months profitable** — but 3 consecutive losing months (Jul-Sep) show
  regime sensitivity.
- **Max drawdown 9.65%** — right at the 10% threshold with 20% position sizing.
- **No confirmation bars needed** — signal fires immediately on breakdown, unlike
  SAND/AXS which use 1-2 confirmation candles.

---

## 2. Winning Parameters

| Parameter        | Value           | Description                                        |
|------------------|-----------------|----------------------------------------------------|
| **Strategy**     | MomShort        | VWAP consolidation breakdown short                  |
| **TP**           | 5%              | Take-profit — triggers 27.6% of trades              |
| **SL**           | 5%              | Stop-loss — triggers 15.2% of trades                |
| **min_bars**     | 5               | Min candles within VWAP proximity before signal     |
| **confirm**      | 0               | No confirmation candles needed                      |
| **vwap_prox**    | 0.2%            | Max distance from VWAP to count as "consolidating"  |
| **vwap_window**  | 1d (intraday)   | VWAP resets daily at 00:00 UTC                      |
| **window**       | 01:00-22:00 UTC | Entry window (no entries outside this range)        |
| **max_hold**     | EOD (23:50 UTC) | Force-close at end of day                           |
| **pos_size**     | 20%             | Percentage of capital per trade                     |
| **vol_filter**   | ON              | Entry bar volume must exceed 20-SMA                 |
| **trend_filter** | OFF             | Daily trend filter disabled                         |

---

## 3. Backtest Results

```
Period:           2025-02-11 -> 2026-02-11
Initial capital:  $1,000.00
Final capital:    $1,354.77
Total return:     +35.48%
Total trades:     297
Max 1 trade/day:  Yes (strict intraday)
```

### Exit Breakdown

| Exit Reason | Count | Percentage | Avg P&L %  |
|-------------|-------|------------|------------|
| TP          | 82    | 27.6%      | ~+5.0%     |
| SL          | 45    | 15.2%      | ~-5.0%     |
| EOD         | 170   | 57.2%      | mixed      |

### Win/Loss Stats

| Metric                 | Value    |
|------------------------|----------|
| Win rate               | 56.6%    |
| Avg winning trade      | +3.28%   |
| Avg losing trade       | -2.88%   |
| Realized R:R           | 1.14     |
| Theoretical R:R        | 1.00     |
| Max drawdown           | 9.65%    |
| Max consecutive losses | 6        |
| Avg P&L per trade      | +$1.19   |
| Median P&L per trade   | +$1.51   |

### Monthly Breakdown

10 of 13 months profitable (3 consecutive losing months Jul-Sep):

| Month    | Trades | Wins | Win Rate | P&L       | Capital    |
|----------|--------|------|----------|-----------|------------|
| 2025-02  | 12     | 8    | 67%      | +$27.02   | $1,027.02  |
| 2025-03  | 24     | 13   | 54%      | +$24.24   | $1,051.25  |
| 2025-04  | 24     | 16   | 67%      | +$56.86   | $1,108.11  |
| 2025-05  | 24     | 13   | 54%      | +$22.70   | $1,130.81  |
| 2025-06  | 25     | 15   | 60%      | +$41.76   | $1,172.58  |
| 2025-07  | 24     | 10   | 42%      | -$29.29   | $1,143.29  |
| 2025-08  | 26     | 12   | 46%      | -$35.49   | $1,107.80  |
| 2025-09  | 27     | 12   | 44%      | -$7.59    | $1,100.21  |
| 2025-10  | 25     | 14   | 56%      | +$49.72   | $1,149.93  |
| 2025-11  | 26     | 18   | 69%      | +$91.10   | $1,241.02  |
| 2025-12  | 26     | 14   | 54%      | +$26.11   | $1,267.13  |
| 2026-01  | 25     | 16   | 64%      | +$53.19   | $1,320.32  |
| 2026-02  | 9      | 7    | 78%      | +$34.44   | $1,354.77  |

### Hold Time Stats (minutes)

| Exit | Mean | Median | Min | Max  |
|------|------|--------|-----|------|
| TP   | 674  | 698    | 55  | 1305 |
| SL   | 701  | 705    | 150 | 1243 |
| EOD  | 1046 | 1130   | 192 | 1365 |

### EOD Exit Analysis

| EOD Outcome | Count | Avg P&L %  |
|-------------|-------|------------|
| Winners     | 86    | +1.64%     |
| Losers      | 84    | -1.74%     |

**Warning:** EOD exits are nearly 50/50 win/loss. The drift edge on GALA is thin compared
to AXS (where EOD exits are 72% winners) or SAND (where 97% of EOD exits are winners).
Most of the edge comes from TP hits outnumbering SL hits (82 vs 45).

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

2. **Volume SMA(20)** — rolling 20-candle average volume.

3. **Time window check** — only consider candles between 01:00 UTC and 22:00 UTC.

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
        → proceed to VOL FILTER
    ELSE:
        counter = 0           # Reset, pattern broken
        continue to next candle

Step 3: VOLUME FILTER
    IF candle.volume <= candle.vol_sma20:
        → skip entry (volume too low)
        continue to next candle

Step 4: ENTER SHORT (no confirmation needed)
    entry_price = close of the breakdown candle
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

        # Vol filter: require above-average volume
        if candle.volume <= candle.vol_sma20:
            continue

        ENTER SHORT at candle.close
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
| Stop-loss              | 5% above entry       | Symmetric with TP                             |
| EOD force-close        | 23:50 UTC            | No overnight risk, clean slate daily          |
| Entry window           | 01:00-22:00 UTC      | Avoid low-liquidity midnight hours            |
| Volume filter          | ON                   | Only enter when volume > 20-SMA              |
| No entry after cutoff  | 22:00 UTC            | Ensures enough time for drift                 |

### Position Sizing

```
max_risk_per_trade = capital * 0.20 * 0.05 = 1.0% of capital
(20% position * 5% stop-loss = 1.0% max capital loss per trade)
```

With fees (~0.016% round-trip on position):
```
actual_max_loss ≈ 1.016% of capital per trade
```

With max 6 consecutive losses observed, worst streak costs ~6% of capital.

---

## 7. Implementation Notes

### For Live Trading (Binance USDT-M Futures)

**Pair:** GALAUSDT (Futures)

### GALAUSDT Futures Precision

**TODO: Verify via `exchange_information()` before going live.**

Expected (check before trading):

| Parameter       | Value           | Notes                                       |
|-----------------|-----------------|---------------------------------------------|
| **Price**       | TBD             | Check tickSize from exchange_information     |
| **Quantity**    | TBD             | Check stepSize, likely whole integers        |
| **Min qty**     | TBD             | Check min_qty                                |
| **Min notional**| TBD             | Likely $5 USDT                               |

### Required Infrastructure

1. **1-minute kline WebSocket stream** — to receive live candles
2. **Intraday VWAP tracker** — cumulative, resets at 00:00 UTC
3. **Volume SMA(20)** — rolling 20-candle average
4. **Signal state machine** — tracks `counter` state (simpler than SAND — no confirmation)
5. **Order manager** — market orders for entry/exit, STOP_MARKET for SL

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
        self.traded_today = False

    def reset_daily(self):
        self.counter = 0
        self.traded_today = False

    def on_candle(self, close, vwap, volume, vol_sma20, minute_utc) -> str | None:
        """Returns 'ENTER_SHORT' or None."""
        if self.traded_today:
            return None
        if minute_utc < 60 or minute_utc >= 1320:
            if minute_utc < 60:
                self.counter = 0
            return None

        pct = (close - vwap) / vwap if vwap > 0 else 0

        if abs(pct) <= 0.002:
            self.counter += 1
        elif self.counter >= 5 and pct < -0.002:
            self.counter = 0
            # Volume filter
            if volume <= vol_sma20:
                return None
            self.traded_today = True
            return "ENTER_SHORT"
        else:
            self.counter = 0

        return None
```

---

## 8. Caveats and Limitations

### Elevated Overfitting Risk

**All 4 strategies had negative average return in the sweep.** This is the primary red
flag from the onboarding guide. The champion at +35.5% is a statistical outlier from
4.8M combinations — survivorship bias is highly likely.

- MomShort average: -0.91% (champion is +35.5%)
- Only 19.1% of MomShort combos were profitable
- The champion is ~39 standard deviations from the mean — suspiciously good

### Regime Sensitivity

- **3 consecutive losing months** (Jul-Sep 2025): -$72 combined
- Win rate dropped to 42-46% during this period
- This suggests the strategy breaks down in certain market conditions
- Consider pausing if 2+ consecutive months are negative

### Thin EOD Edge

- EOD exits are 86 wins vs 84 losses — essentially a coin flip
- Unlike SAND (97% EOD winners) or AXS (72% EOD winners), GALA shows
  weak intraday drift after the VWAP breakdown signal
- The strategy's edge comes primarily from TP hits (82) vastly outnumbering
  SL hits (45), not from directional drift

### Slippage Considerations

- The 5% TP/SL is wide enough that slippage impact is minimal
- GALA futures are generally liquid but verify current order book depth
- Fees ARE modeled: 0.04% taker per side (0.08% round-trip)
- Real slippage is NOT modeled — budget ~0.01-0.03% per trade

### Comparison with Other Tokens

| Aspect         | AXSUSDT              | SANDUSDT               | GALAUSDT               |
|----------------|----------------------|------------------------|------------------------|
| SL             | 5%                   | 0.8%                   | 5%                     |
| Win rate       | 48.5%                | 23.7%                  | 56.6%                  |
| Realized R:R   | 1.53                 | 5.58                   | 1.14                   |
| Max DD         | 6.18%                | 3.12%                  | 9.65%                  |
| Return (ann.)  | ~33% (7mo)           | +27.6% (12mo)          | +35.5% (12mo)          |
| Character      | Balanced             | High R:R, low win-rate | High win-rate, low R:R |
| EOD exit %     | 74.5%                | 20.2%                  | 57.2%                  |
| SL exit %      | 17.5%                | 75.7%                  | 15.2%                  |
| Sweep avg ret  | N/A                  | -0.59%                 | -0.91%                 |
| Sweep red flag | No                   | No                     | **YES (all negative)** |
| confirm_bars   | 2                    | 2                      | 0                      |
| vol_filter     | ON                   | OFF                    | ON                     |

GALA has the highest raw return but the worst sweep profile and highest drawdown.
The lack of confirmation bars and near-50/50 EOD exits suggest a noisier signal.

---

## 9. Sweep Parameter Insights

From the full 4.8M combination sweep on GALAUSDT:

### Best Performing Parameters

| Parameter      | Best Value | Avg Return | Notes                              |
|----------------|------------|------------|------------------------------------|
| vwap_window    | 20d        | -0.45%     | But 1d dominates the top 30        |
| trend_filter   | ON         | -0.74%     | Better avg but OFF won the top     |
| confirm_bars   | 2          | -0.97%     | More confirmation = slightly better|
| entry_window   | 06-18      | -0.91%     | Slightly better than 01-22         |
| max_hold       | EOD        | -0.97%     | Best hold period                   |
| vol_filter     | ON         | -0.95%     | Marginal improvement               |

### Strategy Rankings

| Strategy   | Avg Return | % Profitable | Best Return |
|------------|------------|--------------|-------------|
| MomShort   | -0.91%     | 19.1%        | +35.83%     |
| RejShort   | -1.05%     | 14.3%        | +28.67%     |
| MomLong    | -1.13%     | 14.1%        | +10.56%     |
| RejLong    | -1.34%     | 11.9%        | +16.54%     |

Short strategies outperform longs. MomShort is the least bad on average but still
negative — the token does not have a strong systematic VWAP-based edge.

---

## 10. Backtesting Artifacts

| File | Description |
|------|-------------|
| `backtest_detail.py` | Python script: detailed trade-by-trade analysis + equity chart |
| `backtest_sweep/src/main.rs` | Rust parameter sweep engine (4.8M combos in 77s) |
| `champion_trades.csv` | Full trade log (297 trades) |
| `champion_analysis.html` | Interactive equity/P&L/drawdown chart |
| `galausdt_1m_klines.csv` | Raw 1-min candle data (525K rows) |
| `backtest_sweep.csv` | Full sweep results (4.8M rows) |
| `fetch_klines.py` | Script to download kline data from Binance API |

---

## 11. Quick Reference Card

```
STRATEGY:  MomShort (VWAP Consolidation Breakdown Short)
PAIR:      GALAUSDT (Futures)
TIMEFRAME: 1-minute candles, intraday only

ENTRY:
  1. Price within 0.2% of VWAP for 5+ consecutive bars
  2. Price breaks >0.2% BELOW VWAP
  3. Volume on breakdown candle > 20-SMA volume
  → SHORT at close of breakdown candle (no confirmation)

EXIT (checked every candle, in order):
  1. SL: high >= entry * 1.05     → buy at entry * 1.05
  2. TP: low  <= entry * 0.95     → buy at entry * 0.95
  3. EOD: time >= 23:50 UTC       → buy at market close

SIZING: 20% of capital per trade
FEES:   0.04% taker per side (0.08% round-trip)
LIMIT:  Max 1 trade per day
WINDOW: 01:00-22:00 UTC entries only

PRECISION (GALAUSDT Futures):
  Price:    TBD — check exchange_information before going live
  Quantity: TBD — likely whole integers (stepSize=1)
  Min notional: TBD — likely $5 USDT

EXPECTED BEHAVIOR:
  ~28% of trades hit TP (win: +5%)
  ~15% of trades hit SL (loss: -5%)
  ~57% exit at EOD (50/50 win/loss, thin edge)
  Win rate: ~57%  |  Realized R:R: ~1.14:1

⚠️  ELEVATED OVERFITTING RISK — all sweep strategies had negative avg return
⚠️  3 consecutive losing months observed (Jul-Sep) — regime sensitive
⚠️  Recommend extended paper trading (2-4 weeks) before live capital
```
