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
- `aggregate_klines.py` — Converts 1m candles to higher timeframes (5m, 15m, 30m, 1h)

## Code Conventions

- Python >= 3.12, managed with Poetry
- All Binance SDK prices/quantities are **strings**; timestamps are **int** (ms)
- Use `math.floor` for rounding quantities down, never `round()`
- Futures orders requiring `stop_price` or `close_position` use `send_signed_request()` (SDK bug workaround) or `new_algo_order()`
- Logging uses `StripAnsiFormatter` to strip ANSI codes from file output; `propagate = False` on the `trader` logger
- **Timeframes**: Configured per-symbol via `interval` field in SymbolConfig. Bots subscribe to WebSocket kline streams with the configured interval (1m, 5m, 15m, 30m, 1h).

## Onboarding New Assets

1. Download 1m historical data: `python fetch_klines.py SYMBOL --days 365`
2. Aggregate to multiple timeframes: `python aggregate_klines.py SYMBOL_1m_klines.csv`
3. Run sweeps for all timeframes (1m, 5m, 15m, 30m, 1h): `./backtest_sweep/target/release/backtest_sweep SYMBOL_TF_klines.csv`
4. Compare results and identify champion (best return across all timeframes)
5. Add SymbolConfig to `trader/config.py` with champion parameters and `interval` field
6. Update `Makefile` bots target with correct command (`bot` for MomShort, `pullback` for VWAPPullback)
7. Update README with new asset in portfolio table
