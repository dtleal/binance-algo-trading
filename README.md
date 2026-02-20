# Binance Trader

Async Python bot for automated USDT-M futures trading on Binance.

The bot implements two strategies:
- **MomShort** (Momentum Short): Intraday VWAP-breakdown strategy for short-selling
- **VWAPPullback**: Bidirectional VWAP pullback with EMA trend filter (supports long and short positions)

Both strategies support **configurable timeframes** (1m, 5m, 15m, 30m, 1h) via WebSocket kline streams.

## How the Strategy Works

1. **Consolidation** — Price stays within 0.5% of the intraday VWAP for 3+ consecutive 1-minute candles
2. **Breakdown** — Price breaks more than 0.5% below VWAP
3. **Confirmation** — The next 2 candles both close below VWAP
4. **Entry** — Open a short at the close of the last confirmation candle

Exits are checked every candle in order: stop-loss (5% above entry), take-profit (10% below entry), or force-close at 23:50 UTC. Max one trade per day. Entry window is 01:00–22:00 UTC.

The strategy is configured per-symbol in `trader/config.py` — parameters like TP/SL, min consolidation bars, confirmation bars, and VWAP proximity threshold vary per asset.

## Active Portfolio (11 Bots)

### MomShort Strategy (1m timeframe)
| Symbol     | Return  | TP   | SL   | Leverage | Config               |
|------------|---------|------|------|----------|----------------------|
| AXSUSDT    | +40.10% | 10%  | 5%   | 20x      | bars=3, cfm=2        |
| SANDUSDT   | —       | 10%  | 0.8% | 20x      | bars=5, cfm=2        |
| GALAUSDT   | —       | 5%   | 5%   | 20x      | bars=5, cfm=0        |
| MANAUSDT   | —       | 5%   | 5%   | 20x      | bars=12, cfm=2       |
| SOLUSDT    | +28.13% | 7%   | 5%   | 20x      | bars=8, cfm=0        |
| PEPEUSDT   | +35.63% | 7%   | 5%   | 20x      | bars=12, cfm=2       |

### VWAPPullback Strategy (5m timeframe)
| Symbol       | Return  | TP   | SL   | Leverage | Config               |
|--------------|---------|------|------|----------|----------------------|
| DOGEUSDT     | +41.28% | 10%  | 5%   | 20x      | bars=3, cfm=0        |
| 1000SHIBUSDT | +37.51% | 7%   | 5%   | 20x      | bars=3, cfm=0        |
| XRPUSDT      | +29.21% | 10%  | 2%   | 20x      | bars=3, cfm=0        |

### VWAPPullback Strategy (1m timeframe)
| Symbol     | Return  | TP   | SL   | Leverage | Config               |
|------------|---------|------|------|----------|----------------------|
| ETHUSDT    | —       | 10%  | 5%   | 5x       | bars=20, cfm=0       |
| AVAXUSDT   | +31.12% | 7%   | 2%   | 20x      | bars=30, cfm=0       |

## Setup

Requires Python >= 3.12 and [Poetry](https://python-poetry.org/).

```bash
poetry install
```

Create a `.env` file with your Binance API credentials:

```
API_KEY=your_api_key
SECRET_KEY=your_secret_key
```

## Usage

```bash
# Run the bot live
poetry run python -m trader bot --symbol axsusdt --leverage 5

# Dry-run (connects to live WebSocket, logs signals, no orders)
poetry run python -m trader bot --symbol galausdt --dry-run

# Monitor live market data (spot WebSocket streams)
poetry run python -m trader monitor --symbol axsusdt

# Check open positions across all symbols
poetry run python -m trader status

# Close open position
poetry run python -m trader close

# Trade history
poetry run python -m trader history --days 30
```

A Makefile provides shortcuts for common commands — run `make help` to see all targets.

## Project Structure

```
trader/
├── config.py      # .env loading, SymbolConfig dataclass, per-symbol registries
├── strategy.py    # VWAPTracker + MomShortSignal (pure logic, no I/O)
├── bot.py         # MomShortBot: WS kline stream → strategy → order execution
├── short.py       # FuturesShort: manual one-off USDT-M futures short
├── monitor.py     # WebSocket market data monitor (Spot)
└── cli.py         # Argparse CLI entry point
```

## Backtesting

Strategy parameters are selected through multi-timeframe parameter sweeps over ~1 year of historical data.

### Onboarding Pipeline

When adding a new asset to the portfolio:

#### 1. Download 1m historical data

```bash
poetry run python fetch_klines.py SYMBOL --days 365
```

This downloads 1-minute klines from the Binance public API.

#### 2. Aggregate to multiple timeframes

```bash
poetry run python aggregate_klines.py SYMBOL_1m_klines.csv
```

Generates 5m, 15m, 30m, and 1h candles from the 1m base data.

#### 3. Run parameter sweeps for all timeframes

```bash
./backtest_sweep/target/release/backtest_sweep SYMBOL_1m_klines.csv > sweep_results/SYMBOL_1m_sweep.txt
./backtest_sweep/target/release/backtest_sweep SYMBOL_5m_klines.csv > sweep_results/SYMBOL_5m_sweep.txt
./backtest_sweep/target/release/backtest_sweep SYMBOL_15m_klines.csv > sweep_results/SYMBOL_15m_sweep.txt
./backtest_sweep/target/release/backtest_sweep SYMBOL_30m_klines.csv > sweep_results/SYMBOL_30m_sweep.txt
./backtest_sweep/target/release/backtest_sweep SYMBOL_1h_klines.csv > sweep_results/SYMBOL_1h_sweep.txt
```

#### 4. Identify champion configuration

Compare returns across all timeframes and select the best-performing strategy+timeframe combination.

#### 5. Update configuration

Add the asset to `trader/config.py` with champion parameters and `interval` field.

#### 2. Run the parameter sweep (Rust)

The `backtest_sweep/` directory contains a Rust program that brute-forces across all parameter combinations using Rayon for parallelism. It tests 4 strategies (RejShort, RejLong, MomShort, MomLong) across a grid of:

- 14 take-profit levels (0.1%–10%)
- 10 stop-loss levels (0.1%–5%)
- 6 min-bars values (3–30)
- 2 volume filter settings (on/off)
- 3 confirmation bar counts (0, 1, 2)
- 2 trend filter settings (on/off)
- 2 entry windows (01:00–22:00, 06:00–18:00)
- 2 VWAP proximity thresholds (0.2%, 0.5%)
- 5 VWAP rolling window sizes (1, 5, 10, 20, 30 days)
- 4 max-hold durations (EOD, 30m, 2h, 6h)
- 2 position sizes (10%, 20%)

This produces ~967K combinations, evaluated in ~46 seconds. Results are ranked by total return, risk-adjusted return (return/max drawdown), and win rate.

```bash
cd backtest_sweep
cargo run --release
```

Output is written to `backtest_sweep.csv` with per-combination stats: trades, wins, losses, win rate, return %, max drawdown, and max consecutive losses.

#### 3. Detailed single-run backtest (Python)

Once you've identified a champion parameter set from the sweep, `backtest_detail.py` runs a detailed simulation with full trade logging and interactive charts:

```bash
python backtest_detail.py
```

This produces:
- Trade-by-trade CSV log with entry/exit prices, P&L, hold time, and exit reason
- Interactive HTML chart (Plotly) with equity curve, per-trade P&L bars, and drawdown

Edit the constants at the top of the file (`TP_PCT`, `SL_PCT`, `MIN_BARS`, etc.) to match the champion parameters you want to analyze.

#### 4. Early VWAP-rejection backtest (Python)

`backtest_vwap.py` is a simpler Python-only backtester for the original VWAP-rejection strategy variant. Useful for quick experiments without the Rust sweep:

```bash
python backtest_vwap.py
```

### Backtest Results (AXSUSDT Champion)

Backtested on AXSUSDC 1-minute candles from 2025-07-15 to 2026-02-10 (~7 months):

```
Initial capital:  $1,000
Final capital:    $1,237
Total return:     +23.65%
Total trades:     200
Win rate:         48.5%
Max drawdown:     6.18%
```

74.5% of trades exited at end-of-day — the strategy captures asymmetric intraday drift rather than relying on TP/SL. All 7 backtested months were individually profitable.

### Visualization

`plot_klines.py` generates an interactive daily candlestick chart with SMA and VWAP overlays:

```bash
python plot_klines.py
```
