# Project Memory: binance-algo-trading

Last updated: 2026-03-04

## Purpose

This repository is an automated Binance USDT-M futures trading and research stack.
It supports:
- historical data collection,
- multi-timeframe parameter sweeps,
- detailed backtests,
- per-symbol strategy documentation,
- live/paper trading bots.

## Core Workflow

1. Download 1m klines:
   - `make onboarding-download SYMBOL=dogeusdt DAYS=365`
2. Aggregate timeframes (mandatory):
   - `python scripts/aggregate_klines.py data/klines/dogeusdt_1m_klines.csv`
   - Generates 5m, 15m, 30m, 1h files.
3. Run sweep on all timeframes:
   - `make sweep-rust SYMBOL=dogeusdt`
4. Pick champion (best return + acceptable drawdown/risk-adjusted profile).
5. Run detailed backtest:
   - `make backtest-detail` (MomShort/Rejection)
   - `make backtest-detail-pullback` (VWAPPullback)
6. Validate:
   - profitable months consistency,
   - max drawdown,
   - win rate vs R:R,
   - exit profile (EOD expected to dominate in drift-style setups).
7. Document strategy in `docs/STRATEGY_<SYMBOL>.md`.
8. Configure live symbol in `trader/config.py`.
9. Paper trade 1-2 weeks minimum before live.

## Important Onboarding Rules

- Always run sweeps across all 5 timeframes (1m, 5m, 15m, 30m, 1h).
- Skip assets when red flags appear:
  - all strategies negative on average,
  - best return < 5% with low trades,
  - max drawdown > 15%,
  - win rate < 35% with poor R:R.
- Standard checks before live:
  - exchange precision (tick_size, step_size, min_qty, min_notional),
  - symbol-level config parameters,
  - dry-run parity vs backtest behavior.

## Main Strategies Covered In Docs

- MomShort: intraday VWAP consolidation -> breakdown short.
- VWAPPullback: bidirectional pullback system (long in uptrend, short in downtrend).
- Sweep engine evaluates multiple families:
  - RejShort, RejLong, MomShort, MomLong, VWAPPullback, EMAScalp, ORB, PDHL.

## Canonical Strategy Behavior (MomShort)

- Entry window: usually 01:00-22:00 UTC.
- Intraday VWAP resets daily at 00:00 UTC.
- Signal pattern:
  - price consolidates near VWAP for `min_bars`,
  - breakdown below VWAP threshold,
  - optional confirmation bars below VWAP.
- Exit order on each candle:
  1. SL first,
  2. TP second,
  3. EOD force-close at 23:50 UTC.
- Max 1 trade/day.
- Fees modeled as 0.04% taker per side (0.08% round-trip).

## Known Token Profiles From Existing Docs

- AXS (reference in `docs/STRATEGY.md`):
  - MomShort, TP 10%, SL 5%, min_bars 3, confirm 2, vwap_prox 0.5%.
  - Strong EOD-drift profile, 7/7 profitable months in that sample.

- SAND (`docs/STRATEGY_SANDUSDT.md`):
  - MomShort, TP 10%, SL 0.8%, min_bars 5, confirm 2, vwap_prox 0.2%.
  - Low win-rate / high R:R profile, very low drawdown.

- GALA (`docs/STRATEGY_GALAUSDT.md`):
  - MomShort, TP 5%, SL 5%, min_bars 5, confirm 0, vwap_prox 0.2%, vol_filter ON.
  - High overfitting risk warning:
    - sweep averages negative across strategies,
    - champion appears as outlier.

- MANA (`docs/STRATEGY_MANAUSDT.md`):
  - MomShort, TP 5%, SL 5%, min_bars 12, confirm 2, vwap_prox 0.5%, vol_filter ON in sweep champion.
  - Balanced win-rate profile with moderate drawdown.
  - New onboarding sweep (2026-03-04, 365d) produced stronger PDHL candidate:
    - PDHL 1m: +95.61% return, maxDD 13.87%, 920 trades
    - source: `data/sweeps/manausdt_1m_sweep.csv`

## Active Bot Portfolio (Current)

- Total active bots: 24
- Strategy split:
  - MomShort: 4
  - VWAPPullback: 14
  - PDHL: 5
  - ORB: 1
- Canonical roster and params:
  - `docs/ACTIVE_BOTS.md`

## Precision Notes From Docs

- SANDUSDT:
  - price tick 0.00001 (5 decimals),
  - quantity step 1 (integer qty),
  - min notional around 5 USDT.
- MANAUSDT:
  - price tick 0.0001 (4 decimals),
  - quantity step 1 (integer qty),
  - min notional around 5 USDT.
- GALAUSDT:
  - explicitly marked as TODO to verify before live.

## Important Files

- `scripts/fetch_klines.py`: download historical 1m data.
- `scripts/aggregate_klines.py`: build higher timeframes.
- `scripts/backtest_detail.py`: detailed backtest for MomShort/Rejection.
- `scripts/backtest_detail_pullback.py`: detailed pullback backtest.
- `scripts/analyze_sweep.py`: sweep result analysis.
- `backtest_sweep/target/release/backtest_sweep`: sweep binary.
- `backtest_sweep_v2/target/release/backtest_sweep_v2`: advanced trailing-stop sweep.
- `data/klines/`: historical CSV inputs.
- `data/sweeps/`: sweep outputs.
- `trader/config.py`: live symbol configuration.
- `docs/STRATEGY_*.md`: per-symbol implementation records.
- `docs/DB_ACCESS.md`: canonical database access and query guide (Postgres/Redis).

## Operational Guardrails

- Prefer paper trading before live changes.
- Treat V2 sweep as advanced/optional (explicit request only).
- If champion comes from broadly negative sweep averages, treat as suspect and validate longer.
- Keep one-trade-per-day and EOD close behavior unless there is a clear, tested reason to change.

## Runtime Notes (2026-03-04)

- `make bots` and `make start` now load `.env` before spawning bot processes/dashboard/sync daemon.
  - This is required for DB credentials (`POSTGRES_*`) and Telegram vars in detached startup.
- Startup configuration remains DB-first (`symbol_configs`), with strict fail-closed behavior when DB is unavailable unless `ALLOW_CONFIG_FALLBACK=1`.
- On DB-related startup failure (before async loop), CLI now triggers Telegram notification:
  - `⚠️ Falha ao iniciar bot — <SYMBOL>`, with structured payload:
    - strategy, timeframe, leverage, position size, startup stage and failure reason.
- Startup validation failures inside bots (e.g., `min_notional`) also use the same structured Telegram message format.
- Dashboard (`/bots`) now has a dedicated filter for recovery bots:
  - `Recuperação` filter shows only bots with `mode="monitoring"` (same criterion as red recovery badge).
- Global Symbol filter now includes `RECOVERY` as first option:
  - selecting it filters Overview/History to trades from symbols currently in recovery mode (`mode="monitoring"` in bot states).
- Chat backend now supports two LLM providers:
  - `CHAT_PROVIDER=openai|anthropic|auto` (default: `openai`)
  - Anthropic key: `ANTHROPIC_API_KEY`
  - OpenAI key: `OPENAI_API_KEY` (model via `OPENAI_CHAT_MODEL`, default `gpt-4.1-mini`)
  - If OpenAI key is missing, backend falls back to Anthropic when available.
  - Runtime provider failures no longer return HTTP 500 HTML:
    - `/api/chat` now returns JSON with human-readable error and tries provider fallback when possible.
- For symbols `ETHUSDT`, `GALAUSDT`, `SOLUSDT`, `XAUUSDT`, `1000SHIBUSDT`:
  - `symbol_configs.pos_size_pct = 0.40`
  - `leverage = 1`
  - This avoids low notional startup failures with small account balances.
- `MANAUSDT` was migrated from `MomShort` to `PDHL` in runtime portfolio:
  - DB `symbol_configs` updated to:
    - `strategy_name=PDHL`, `interval=1m`
    - `tp_pct=7.0`, `sl_pct=1.5`, `confirm_bars=3`
    - `pdhl_prox_pct=0.005`, `vwap_dist_stop=0.03`
    - `champion_return_pct=95.61`, `champion_max_dd=13.87`
  - `make bots` now starts `manausdt` via `python -m trader pdhl --symbol manausdt`.
- `MomShort` precision handling was hardened for order placement:
  - `trader/bot.py` now fetches exchange filters (`PRICE_FILTER`/`LOT_SIZE`) at startup
    and uses them to format `quantity` and `trigger_price`.
  - This prevents Binance error `-1111` (`Precision is over the maximum`) on symbols with
    stricter lot-size precision (e.g., `SOLUSDT`).
  - Fallback config for `SOLUSDT` was aligned to `qty_decimals=1` in `trader/config.py`.
- Phase 1 of position risk guard started for live bots (excluding `VWAPPullback-v2` by explicit decision):
  - Bots updated: `MomShort`, `VWAPPullback`, `PDHL`, `ORB`, `EMAScalp`.
  - New deterministic early-exit params (constructor defaults):
    - `time_stop_minutes = 20`
    - `time_stop_min_progress_pct = 0.0`
    - `adverse_exit_bars = 3`
    - `adverse_body_min_pct = 0.20`
  - New per-position internal state:
    - `entry_ts_ms`, `adverse_count`, `risk_exit_pending`
  - New early-exit reasons:
    - `Time stop` (no progress after X minutes)
    - `Adverse momentum` (consecutive adverse candles with minimum body, only when `PnL% < 0`)
  - Position-guard state is reset on position close/error/EOD paths to avoid stale carry-over.
- Bot registry heartbeat/TTL was hardened to keep `/api/bot_states` stable for long-candle bots:
  - `trader/bot_registry.py` now stamps `heartbeat_ts` on every registry update.
  - New async helper `heartbeat_loop()` publishes lightweight Redis heartbeat updates.
  - Live bots (`MomShort`, `VWAPPullback`, `PDHL`, `ORB`, `EMAScalp`, `VWAPPullback-v2`) now start a background heartbeat task at startup and cancel it on shutdown.
  - Default registry settings:
    - `BOT_HEARTBEAT_INTERVAL_SEC=10`
    - `BOT_REGISTRY_TTL_SEC=7200`
  - This prevents the `bot:states` hash from expiring between sparse candles (e.g., 1h interval bots).
- Docker Postgres healthcheck now targets the configured DB explicitly:
  - `pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}`
  - Prevents recurring Postgres log spam like `FATAL: database "trader" does not exist`.
