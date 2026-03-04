# Quick Start Guide

## Installation

```bash
make install
```

This installs backend dependencies (Poetry), frontend dependencies (npm), and builds the frontend.

## Environment

Create `.env` in project root:

```bash
API_KEY=your_api_key_here
SECRET_KEY=your_secret_key_here
REDIS_URL=redis://localhost:6379

# Optional
SOCKS_PROXY=socks5h://127.0.0.1:1080
```

## Start The System

```bash
make start
```

This starts:
- dashboard on `http://localhost:8080`
- trade sync daemon
- **24 trading bots**:
  - MomShort: 5
  - VWAPPullback: 14
  - PDHL: 4
  - ORB: 1

Full active portfolio:
- `docs/ACTIVE_BOTS.md`

## Core Operations

```bash
# Check full status (redis + dashboard + bots)
make status-all

# Stream logs
make logs

# Stop everything
make stop
```

## Run Components Separately

```bash
# Redis only
make redis

# Dashboard only
make dashboard

# Bots only
make bots
```

## Useful Commands

```bash
# One bot in dry-run
poetry run python -m trader bot --symbol axsusdt --dry-run

# Pullback bot
poetry run python -m trader pullback --symbol ethusdt --dry-run

# PDHL bot
poetry run python -m trader pdhl --symbol ltcusdt --dry-run

# ORB bot
poetry run python -m trader orb --symbol ksmusdt --dry-run

# Portfolio status
poetry run python -m trader status

# Trade history
poetry run python -m trader history --days 30
```

## Dashboard

Access: `http://localhost:8080`

Main pages:
- Overview
- Bots
- Positions
- History
- Commissions

## Troubleshooting

If Redis is missing on macOS:

```bash
brew install redis
make redis
```

If dashboard port is busy:

```bash
make stop
make start
```

## Notes

- Bot runtime parameters are loaded from DB (`symbol_configs`) first, with fallback to `trader/config.py`.
- For onboarding new assets, follow `docs/ONBOARDING.md`.
