# Quick Start: Automated Onboarding

Onboard new trading assets in minutes with automated scripts.

---

## TL;DR

```bash
# Complete onboarding for a new asset
make onboarding ATIVO=DOGEUSDT

# For pullback strategy
make onboarding ATIVO=ETHUSDT STRATEGY=pullback

# Just download data (180 days)
make onboarding-download ATIVO=1000SHIBUSDT DAYS=180
```

---

## Full Workflow

### 1. Start Onboarding

```bash
make onboarding ATIVO=DOGEUSDT
```

**What happens:**
- ✅ Downloads 1 year of 1-minute klines from Binance
- ❓ Prompts you to run parameter sweep (if MomShort)
- ❓ Prompts you to configure backtest parameters
- ✅ Runs detailed backtest
- ✅ Generates trade log CSV and interactive charts
- ✅ Shows validation checklist

### 2. Parameter Sweep (MomShort only)

When prompted, you'll need to:

**A. Update backtest sweep:**
```bash
cd backtest_sweep
# Edit src/main.rs - update CSV_FILE path
cargo run --release
```

**B. Analyze results:**
- Check TOP 30 BY RETURN
- Check TOP 30 RISK-ADJUSTED
- Note best strategy, VWAP window, parameters

**C. Continue onboarding:**
```bash
# Re-run with skip flags
poetry run python onboarding.py DOGEUSDT --skip-download --skip-sweep
```

### 3. Configure Backtest

Update `backtest_detail.py` (MomShort) or `backtest_detail_pullback.py` (Pullback):

```python
CSV_FILE = "dogeusdt_1m_klines.csv"
TP_PCT = 0.10          # From sweep results
SL_PCT = 0.05          # From sweep results
MIN_BARS = 3           # From sweep results
CONFIRM_BARS = 2       # From sweep results
VWAP_PROX = 0.005      # From sweep results
# ... etc
```

### 4. Review Results

**Files generated:**
- `champion_trades.csv` or `pullback_trades.csv` - Full trade log
- `champion_analysis.html` or `pullback_analysis.html` - Interactive charts

**Validation checklist:**
- [ ] All (or most) months profitable?
- [ ] Max drawdown < 10-15%?
- [ ] Win rate > 35% or R:R > 1.5?
- [ ] EOD exits dominate?
- [ ] Equity curve steadily rising?

### 5. Document Strategy

Create `docs/STRATEGY_DOGEUSDT.md` with:
- Strategy overview
- Winning parameters table
- Backtest results
- Entry/exit logic
- Risk management rules
- Implementation notes

See `docs/STRATEGY_GALAUSDT.md` for template.

### 6. Configure for Live Trading

**A. Check exchange precision:**
```python
# Run this to get tick_size, step_size, min_qty, min_notional
poetry run python -m trader check-precision DOGEUSDT
```

**B. Add to `trader/config.py`:**
```python
DOGE_CONFIG = SymbolConfig(
    symbol="DOGEUSDT",
    asset="DOGE",
    tp_pct=10.0,
    sl_pct=5.0,
    min_bars=3,
    confirm_bars=2,
    vwap_prox=0.005,
    # ... etc
)

SYMBOL_CONFIGS = {
    # ... existing configs
    "DOGEUSDT": DOGE_CONFIG,
}
```

### 7. Paper Trade (REQUIRED)

```bash
# Dry run for 1-2 weeks minimum
poetry run python -m trader bot --symbol dogeusdt --leverage 20 --dry-run
```

Monitor:
- Signal quality
- Entry timing
- Exit distribution (TP/SL/EOD)
- Win rate matches backtest

### 8. Go Live

```bash
# Only after successful paper trading
poetry run python -m trader bot --symbol dogeusdt --leverage 20
```

---

## Command Reference

```bash
# Full onboarding
make onboarding ATIVO=DOGEUSDT

# Pullback strategy
make onboarding ATIVO=ETHUSDT STRATEGY=pullback

# Custom days
make onboarding ATIVO=1000SHIBUSDT DAYS=180

# Download only
make onboarding-download ATIVO=BTCUSDT DAYS=90

# Manual steps
poetry run python fetch_klines.py DOGEUSDT -d 365
poetry run python onboarding.py DOGEUSDT
poetry run python onboarding.py DOGEUSDT --skip-download --skip-sweep
```

---

## Options

| Option | Values | Default | Description |
|--------|--------|---------|-------------|
| ATIVO | Any USDT pair | - | Trading pair symbol (required) |
| STRATEGY | momshort, pullback | momshort | Strategy type |
| DAYS | 1-365 | 365 | Days of historical data |

---

## Troubleshooting

**"Data download failed"**
- Check symbol name (must be exact, e.g., 1000SHIBUSDT not SHIBUSDT)
- Check internet connection
- Binance API may be rate-limiting (wait 1 min and retry)

**"Backtest failed"**
- Verify CSV file exists
- Check backtest_detail.py parameters are valid
- Ensure CSV has enough data (minimum 3 months recommended)

**"No data available for symbol"**
- Symbol may be too new (< 1 year history)
- Try fewer days: `DAYS=180` or `DAYS=90`

**"Sweep takes too long"**
- Expected ~60-90 seconds for 4.8M combinations
- Use `cargo run --release` not `cargo run`
- Make sure you've run `cargo build --release` first

---

## Red Flags (Skip Asset)

🚫 **Do NOT proceed if:**
- All strategies have negative average return
- Best return < 5% with low trade count (< 50)
- Max drawdown > 15% on best strategy
- Win rate < 35% with R:R < 1.5
- Only one month profitable, rest negative
- Equity curve is a few lucky spikes, not steady growth

✅ **Proceed if:**
- Multiple strategies profitable
- Consistent monthly returns
- Max DD < 10% at 20% position size
- Win rate > 40% or R:R > 2.0
- EOD exits dominate (strategy edge is clear)
- Equity curve steadily rising

---

## Next: Multi-Asset Strategy

Once you have 3+ validated assets:
- Run portfolio backtest
- Analyze correlation
- Optimize capital allocation
- Test simultaneous positions

See `docs/PORTFOLIO.md` for multi-asset strategies.
