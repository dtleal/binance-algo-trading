# 🚀 Quick Start Guide

## Installation

```bash
make install
```

This will:
- ✅ Install all backend dependencies (Poetry)
- ✅ Install all frontend dependencies (npm)
- ✅ Build the production frontend

## Starting the System

### Option 1: Start Everything (Recommended)

```bash
make start
```

This starts:
- 📊 **Dashboard** on http://localhost:8080
- 🤖 **8 Trading Bots** with optimal configurations:
  - 6 MomShort bots (20x leverage): AXSUSDT, SANDUSDT, DOGEUSDT, 1000SHIBUSDT, GALAUSDT, MANAUSDT
  - 2 VWAP Pullback bots:
    - ETHUSDT (5x leverage) - 5min candles
    - AXSUSDT (20x leverage) - 1min candles

### Option 2: Start Components Separately

```bash
# Start only dashboard
make dashboard

# Start only bots
make bots

# Start Redis (required for both)
make redis
```

## Managing the System

### Check Status

```bash
make status-all
```

Shows:
- ✅/❌ Redis status
- ✅/❌ Dashboard status
- ✅/❌ Number of active bots

### Stop Everything

```bash
make stop
```

Stops all processes:
- ⛔ All trading bots
- ⛔ Dashboard
- ⛔ Redis

### View Logs

```bash
make logs
```

## Configuration

Before starting, create a `.env` file:

```bash
BINANCE_API_KEY=your_api_key_here
BINANCE_SECRET_KEY=your_secret_key_here
REDIS_URL=redis://localhost:6379

# Optional
SOCKS_PROXY=socks5h://127.0.0.1:1080
```

## Bot Configurations

### MomShort Bots (6 bots)
- **Leverage**: 20x
- **Symbols**: AXSUSDT, SANDUSDT, DOGEUSDT, 1000SHIBUSDT, GALAUSDT, MANAUSDT
- **Strategy**: Momentum-based short selling on consolidation breakdowns

### VWAP Pullback - ETHUSDT
- **Leverage**: 5x
- **Candle Size**: 5 minutes
- **Parameters**:
  - TP: 10% | SL: 5%
  - Min Bars: 20 | Confirm Bars: 0
  - EMA: 100 | VWAP Window: 1 day
  - Position Size: 30% | Max Trades/Day: 2
- **Backtest Results**: +31.38% return, 49.8% win rate, 6.47% max DD

### VWAP Pullback - AXSUSDT
- **Leverage**: 20x
- **Candle Size**: 1 minute
- **Parameters**:
  - TP: 10% | SL: 5%
  - Min Bars: 5 | Confirm Bars: 1
  - EMA: 200 | VWAP Window: 10 days
  - Position Size: 20% | Max Trades/Day: 1

## Makefile Commands Reference

### Quick Start
- `make install` - Install all dependencies
- `make start` - Start everything
- `make stop` - Stop everything
- `make status-all` - Check status

### Development
- `make dashboard` - Start dashboard only
- `make bots` - Start all bots
- `make logs` - View bot logs
- `make build-frontend` - Rebuild frontend

### Individual Bots
- `make bot-dry` - Run AXSUSDT MomShort in dry-run
- `make pullback-eth` - Run ETHUSDT Pullback
- `make pullback-best` - Run AXSUSDT Pullback with best params

### Monitoring
- `make monitor` - Monitor market data
- `make status` - Show position status
- `make history` - Show trade history

### Backtesting
- `make sweep-rust-eth` - Run parameter sweep for ETH
- `make analyze-best` - Analyze sweep results
- `make backtest-detail` - Run detailed backtest

## Dashboard Features

Access at **http://localhost:8080**

### Pages:
1. **Overview** - P&L curves, account summary, performance metrics
2. **Bots** - Real-time bot status, positions, configurations
3. **Positions** - Open positions with P&L tracking
4. **History** - Trade history with P&L analysis
5. **Commissions** - Fee tracking and breakdown

### Global Filter
Filter data across all pages by:
- **Symbol** - Specific asset or all
- **Strategy** - MomShort, Pullback, or all
- **Date Range** - 7, 30, or 90 days

## Troubleshooting

### Redis not starting
```bash
brew install redis
make redis
```

### Port 8080 already in use
```bash
# Stop existing processes
make stop

# Or change port in trader/cli.py
```

### Bots not appearing in dashboard
```bash
# Check Redis is running
make status-all

# Restart everything
make stop
make start
```

## Safety Features

- ✅ Dry-run mode available for all bots
- ✅ Per-trade position sizing
- ✅ Daily trade limits
- ✅ Stop-loss on all positions
- ✅ Take-profit targets
- ✅ Redis-based state management

## Getting Help

```bash
make help
```

Shows all available commands with descriptions.
