# Binance Trader

Async Python bot for USDT-M futures trading on Binance.

## Design Philosophy

- **Fail fast**: Never auto-correct or silently work around bad inputs. Validate upfront and abort with a clear error message. No magic fixes, no silent fallbacks.
- **Multi-timeframe support**: All bots support configurable timeframes (1m, 5m, 15m, 30m, 1h) via the `interval` field in SymbolConfig.

## Commands

```bash
poetry install              # Install dependencies
poetry run python -m trader bot --symbol axsusdt --leverage 5   # Run bot
poetry run python -m trader bot --symbol sandusdt --dry-run     # Dry-run
poetry run python -m trader monitor --symbol axsusdt            # WS monitor
poetry run python -m trader status                              # Position status
poetry run python -m trader close                               # Close position
```

## Project Structure

- `trader/config.py` — Environment vars, `SymbolConfig` dataclass (includes `interval` field), per-symbol registries
- `trader/strategy.py` — Pure logic: `VWAPTracker` + `MomShortSignal` (no I/O)
- `trader/strategy_vwap_pullback.py` — VWAPPullback strategy logic with EMA trend filter
- `trader/bot.py` — `MomShortBot`: WS kline stream → strategy → order execution (configurable interval)
- `trader/bot_vwap_pullback.py` — `VWAPPullbackBot`: Bidirectional VWAP pullback bot (configurable interval)
- `trader/short.py` — `FuturesShort`: manual one-off USDT-M futures short
- `trader/monitor.py` — WebSocket market data monitor (Spot)
- `trader/cli.py` — Argparse CLI entry point
- `scripts/` — Python scripts: `fetch_klines.py`, `aggregate_klines.py`, backtests, analysis
- `data/klines/` — Historical kline CSVs (1m, 5m, 15m, 30m, 1h per symbol)
- `data/sweeps/` — Sweep result CSVs and TXT files
- `backtest_sweep/` — Standard Rust sweep binary (strategies 0-7 including EMAScalp/ORB/PDHL)
- `backtest_sweep_v2/` — V2 Rust sweep binary (trailing stop, no fixed TP; use when explicitly requested)

## Code Conventions

- Python >= 3.12, managed with Poetry
- All Binance SDK prices/quantities are **strings**; timestamps are **int** (ms)
- Use `math.floor` for rounding quantities down, never `round()`
- Futures orders requiring `stop_price` or `close_position` use `send_signed_request()` (SDK bug workaround) or `new_algo_order()`
- Logging uses `StripAnsiFormatter` to strip ANSI codes from file output; `propagate = False` on the `trader` logger
- **Timeframes**: Configured per-symbol via `interval` field in SymbolConfig. Bots subscribe to WebSocket kline streams with the configured interval (1m, 5m, 15m, 30m, 1h).

## Onboarding New Assets

Use `make onboarding SYMBOL=dogeusdt` to run the full automated process, or manually:

1. Download 1m historical data: `python scripts/fetch_klines.py SYMBOL -d 365 -o data/klines/SYMBOL_1m_klines.csv`
2. **MANDATORY** — Aggregate to all timeframes: `python scripts/aggregate_klines.py data/klines/SYMBOL_1m_klines.csv`
3. Run sweeps for all timeframes: `make sweep-rust SYMBOL=dogeusdt` (iterates 1m, 5m, 15m, 30m, 1h automatically)
4. Compare results in `data/sweeps/SYMBOL_*_sweep.csv`, identify champion (best return)
5. Add SymbolConfig to `trader/config.py` with champion parameters and `interval` field
6. Update `Makefile` bots target with correct command (`bot` for MomShort, `pullback` for VWAPPullback)
7. Update README with new asset in portfolio table
