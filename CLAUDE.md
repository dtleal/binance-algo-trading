# Binance Trader

Async Python bot for USDT-M futures short-selling on Binance (AXS, SAND).

## Design Philosophy

- **Fail fast**: Never auto-correct or silently work around bad inputs. Validate upfront and abort with a clear error message. No magic fixes, no silent fallbacks.

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

- `trader/config.py` — Environment vars, `SymbolConfig` dataclass, per-symbol registries (AXS_CONFIG, SAND_CONFIG)
- `trader/strategy.py` — Pure logic: `VWAPTracker` + `MomShortSignal` (no I/O)
- `trader/bot.py` — `MomShortBot`: WS kline stream → strategy → order execution
- `trader/short.py` — `FuturesShort`: manual one-off USDT-M futures short
- `trader/monitor.py` — WebSocket market data monitor (Spot)
- `trader/cli.py` — Argparse CLI entry point

## Code Conventions

- Python >= 3.12, managed with Poetry
- All Binance SDK prices/quantities are **strings**; timestamps are **int** (ms)
- Quantities for AXS and SAND must be **whole integers** (step_size=1)
- Use `math.floor` for rounding quantities down, never `round()`
- Futures orders requiring `stop_price` or `close_position` use `send_signed_request()` (SDK bug workaround) or `new_algo_order()`
- Logging uses `StripAnsiFormatter` to strip ANSI codes from file output; `propagate = False` on the `trader` logger
