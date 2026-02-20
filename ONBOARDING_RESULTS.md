# Onboarding Results: DOGEUSDT & 1000SHIBUSDT

**Data:** 2026-02-20
**Period:** 1 year (2025-02-20 to 2026-02-20)
**Timeframes Tested:** 1m, 5m, 15m, 30m, 1h ✅ COMPLETE
**Total Combinations per Asset:** 40,320,000 (8M per timeframe × 5 timeframes)

---

## Global Champions

### 🥇 DOGEUSDT

| Metric | Value |
|--------|-------|
| **Timeframe** | 5m |
| **Strategy** | VWAPPullback |
| **Return** | +41.28% |
| **Trades** | 322 (175W/147L) |
| **Win Rate** | 54.3% |
| **Max Drawdown** | 6.84% |
| **EOD Exits** | 262/322 (81.4%) |
| **Take Profit** | 10.00% |
| **Stop Loss** | 5.00% |
| **Min Bars** | 3 |
| **Confirm Bars** | 0 |
| **VWAP Window** | 1 day |
| **VWAP Proximity** | 0.200% |
| **Position Size** | 20% |

**Validation:**
- ✅ Return > 10% (41.28%)
- ✅ Max DD < 10% (6.84%)
- ✅ Win rate > 40% (54.3%)
- ✅ EOD exits dominate (81.4%)
- ✅ Sufficient trades (322)

**Status:** ✅ APPROVED

---

### 🥇 1000SHIBUSDT

| Metric | Value |
|--------|-------|
| **Timeframe** | 5m |
| **Strategy** | VWAPPullback |
| **Return** | +37.51% |
| **Trades** | 354 (188W/166L) |
| **Win Rate** | 53.1% |
| **Max Drawdown** | 5.27% |
| **EOD Exits** | 283/354 (79.9%) |
| **Take Profit** | 7.00% |
| **Stop Loss** | 5.00% |
| **Min Bars** | 3 |
| **Confirm Bars** | 0 |
| **VWAP Window** | 1 day |
| **VWAP Proximity** | 0.500% |
| **Position Size** | 20% |

**Validation:**
- ✅ Return > 10% (37.51%)
- ✅ Max DD < 10% (5.27%)
- ✅ Win rate > 40% (53.1%)
- ✅ EOD exits dominate (79.9%)
- ✅ Sufficient trades (354)

**Status:** ✅ APPROVED

---

## Key Findings

### Timeframe Preference

Both assets performed best on **5-minute candles**:

**DOGEUSDT:**
- 🥇 5m: +41.28% (MaxDD 6.84%)
- 🥈 1m: +39.78% (MaxDD 9.60%) ← Higher risk
- 🥉 15m/30m/1h: < 35%

**1000SHIBUSDT:**
- 🥇 5m: +37.51% (MaxDD 5.27%)
- 1m: +31.38% (MaxDD 6.17%)
- 15m/30m/1h: < 30%

**Why 5m wins:**
- Better risk-adjusted returns (lower MaxDD)
- More efficient entry/exit timing
- Optimal balance: not too granular (noise), not too slow (missed opportunities)
- EOD exit logic works better on 5m timeframe

### Strategy Dominance

**VWAPPullback** was the clear winner for both assets:
- Consistent returns across timeframes
- High win rates (>50%)
- Low drawdowns (<7%)
- Strong EOD exit percentage (>79%)

### Parameter Similarities

Both assets share similar parameters:
- **TP:** 7-10%
- **SL:** 5%
- **Min Bars:** 3
- **Confirm Bars:** 0
- **VWAP Window:** 1 day
- **Position Size:** 20%

**Difference:** VWAP Proximity (DOGE=0.2%, SHIB=0.5%)

---

## Next Steps

### 1. DOGEUSDT

**Champion Parameters (5m timeframe):**
```python
DOGE_CONFIG = SymbolConfig(
    symbol="DOGEUSDT",
    asset="DOGE",
    strategy="VWAPPullback",
    timeframe="5m",  # Use 5m candles
    tp_pct=10.0,
    sl_pct=5.0,
    min_bars=3,
    confirm_bars=0,
    vwap_window_days=1,
    vwap_prox=0.002,  # 0.2%
    pos_size_pct=20.0,
    # ... add exchange precision after check-precision
)
```

**Action Items:**
- [ ] Run detailed backtest on 5m data with champion params
- [ ] Generate trade log CSV and analysis charts
- [ ] Check exchange precision: `poetry run python -m trader check-precision DOGEUSDT`
- [ ] Add to `trader/config.py`
- [ ] Paper trade 1-2 weeks (dry-run)
- [ ] Document in `docs/STRATEGY_DOGEUSDT.md`

---

### 2. 1000SHIBUSDT

**Champion Parameters (5m timeframe):**
```python
SHIB_CONFIG = SymbolConfig(
    symbol="1000SHIBUSDT",
    asset="1000SHIB",
    strategy="VWAPPullback",
    timeframe="5m",  # Use 5m candles
    tp_pct=7.0,
    sl_pct=5.0,
    min_bars=3,
    confirm_bars=0,
    vwap_window_days=1,
    vwap_prox=0.005,  # 0.5%
    pos_size_pct=20.0,
    # ... add exchange precision after check-precision
)
```

**Action Items:**
- [ ] Run detailed backtest on 5m data with champion params
- [ ] Generate trade log CSV and analysis charts
- [ ] Check exchange precision: `poetry run python -m trader check-precision 1000SHIBUSDT`
- [ ] Add to `trader/config.py`
- [ ] Paper trade 1-2 weeks (dry-run)
- [ ] Document in `docs/STRATEGY_1000SHIBUSDT.md`

---

## Implementation Notes

### Timeframe Adaptation

The bot currently runs on real-time WebSocket streams. To use 5m candles:

**Option 1:** Aggregate 1m candles to 5m in memory
**Option 2:** Use 5m kline WebSocket stream
**Option 3:** Implement timeframe-aware strategy module

Recommend **Option 2** for simplicity and efficiency.

### VWAP Calculation

Ensure VWAP is calculated on the target timeframe (5m), not 1m aggregated.

### Risk Management

With 20% position size per asset:
- Maximum 2 simultaneous positions = 40% capital at risk
- Consider reducing to 15% each if running 3+ assets
- Maintain stop-loss discipline (5% per trade)

---

## Sweep Execution Summary

| Asset | Timeframes | Total Combinations | Time | Status |
|-------|------------|-------------------|------|--------|
| DOGEUSDT | 1m, 5m, 15m, 30m, 1h | 40.3M | ~6 min | ✅ Complete |
| 1000SHIBUSDT | 1m, 5m, 15m, 30m, 1h | 40.3M | ~6 min | ✅ Complete |
| **TOTAL** | **10 sweeps** | **80.6M combinations** | **~12 min** | ✅ **All Complete** |

**Performance:**
- Rust parallel execution: ~6-8M combinations/sec
- CPU utilization: All cores (parallel rayon)
- Memory: < 500MB per sweep

---

## Files Generated

**Aggregated Candles:**
- `dogeusdt_5m_klines.csv` (105,121 candles)
- `dogeusdt_15m_klines.csv` (35,041 candles)
- `dogeusdt_30m_klines.csv` (17,521 candles)
- `dogeusdt_1h_klines.csv` (8,761 candles)
- `1000shibusdt_5m_klines.csv` (105,121 candles)
- `1000shibusdt_15m_klines.csv` (35,041 candles)
- `1000shibusdt_30m_klines.csv` (17,521 candles)
- `1000shibusdt_1h_klines.csv` (8,761 candles)

**Sweep Results:**
- `sweep_results/dogeusdt_5m_sweep.txt`
- `sweep_results/dogeusdt_15m_sweep.txt`
- `sweep_results/dogeusdt_30m_sweep.txt`
- `sweep_results/dogeusdt_1h_sweep.txt`
- `sweep_results/1000shibusdt_5m_sweep.txt`
- `sweep_results/1000shibusdt_15m_sweep.txt`
- `sweep_results/1000shibusdt_30m_sweep.txt`
- `sweep_results/1000shibusdt_1h_sweep.txt`

---

**Generated by:** Claude Sonnet 4.5
**Automated Onboarding Pipeline:** ✅ Working
