# Project Memory: binance-algo-trading

Last updated: 2026-03-06

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
   - For guard research without exploding the global search space:
     - `make sweep-pdhl-guards SYMBOL=icxusdt`
     - `make sweep-pullback-guards SYMBOL=ethusdt`
   - For intraday-only pullback research with shorter TP/SL/max-hold:
     - `make sweep-pullback-intraday SYMBOL=dogeusdt`
4. Run anti-overfitting filter (3 layers):
   - `make filter-overfit SYMBOL=dogeusdt`
   - Generates:
     - `data/sweeps/<symbol>_anti_overfit_layer1.csv`
     - `data/sweeps/<symbol>_anti_overfit_layer2.csv`
     - `data/sweeps/<symbol>_anti_overfit_final.csv`
   - Dedicated sweeps can be filtered with suffixes:
     - `make filter-overfit SYMBOL=icxusdt SUFFIX=pdhl_guard_sweep OUT_TAG=pdhl_guard`
     - `make filter-overfit SYMBOL=ethusdt SUFFIX=pullback_guard_sweep OUT_TAG=pullback_guard`
   - Intraday pullback wrapper uses its own thresholds:
     - `make filter-overfit-intraday SYMBOL=dogeusdt`
5. Run walk-forward validation (out-of-sample):
   - `make walk-forward SYMBOL=dogeusdt TF=1m TRAIN_DAYS=180 TEST_DAYS=30 STEP_DAYS=30`
   - Intraday pullback wrapper:
     - `make walk-forward-intraday SYMBOL=dogeusdt TF=5m`
   - Train optimization is Rust sweep per fold; OOS evaluation uses frozen params on the next window.
   - Outputs:
     - `data/sweeps/<symbol>_<tf>_walkforward_folds.csv`
     - `data/sweeps/<symbol>_<tf>_walkforward_summary.csv`
6. Pick champion from anti-overfit + walk-forward survivors.
7. Run detailed backtest:
   - `make backtest-detail` (MomShort/Rejection)
   - `make backtest-detail-pullback` (VWAPPullback)
8. Validate:
   - profitable months consistency,
   - max drawdown,
   - win rate vs R:R,
   - exit profile (EOD expected to dominate in drift-style setups).
9. Document strategy in `docs/STRATEGY_<SYMBOL>.md`.
10. Configure live symbol in `trader/config.py`.
11. Paper trade 1-2 weeks minimum before live.

## Important Onboarding Rules

- Always run sweeps across all 5 timeframes (1m, 5m, 15m, 30m, 1h).
- Run anti-overfitting filter after sweep and before champion selection.
- Run walk-forward OOS validation before final champion promotion.
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

- Total active bots: 28
- Strategy split:
  - MomShort: 4
  - VWAPPullback: 14
  - PDHL: 9
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
- `scripts/backtest_detail_pdhl.py`: detailed backtest for PDHL (signal engine `live|sweep`, optional runtime protections).
- `scripts/backtest_detail_pullback.py`: detailed pullback backtest.
- `scripts/analyze_sweep.py`: sweep result analysis.
- `scripts/filter_overfit.py`: 3-layer anti-overfitting filter (hard thresholds, neighborhood robustness, monthly consistency re-sim).
- `scripts/walk_forward.py`: rolling walk-forward validator (Rust train optimization + OOS window test).
- `backtest_sweep/target/release/backtest_sweep`: sweep binary.
- `backtest_sweep/target/release/pdhl_guard_sweep`: dedicated PDHL sweep with focused runtime-guard grid.
- `backtest_sweep/target/release/pullback_guard_sweep`: dedicated VWAPPullback sweep with focused runtime-guard grid.
- `backtest_sweep/target/release/pullback_intraday_sweep`: dedicated VWAPPullback intraday sweep with short TP/SL, short max-hold, and `max_trades_per_day` in `{2,4}`.
- `backtest_sweep_v2/target/release/backtest_sweep_v2`: advanced trailing-stop sweep.
- `data/klines/`: historical CSV inputs.
- `data/sweeps/`: sweep outputs.
- `trader/config.py`: live symbol configuration.
- `trader/exchange_precision.py`: helper for Binance tick/step quantization.
- `docs/STRATEGY_*.md`: per-symbol implementation records.
- `docs/DB_ACCESS.md`: canonical database access and query guide (Postgres/Redis).

## Operational Guardrails

- Prefer paper trading before live changes.
- Treat V2 sweep as advanced/optional (explicit request only).
- If champion comes from broadly negative sweep averages, treat as suspect and validate longer.
- Keep one-trade-per-day and EOD close behavior unless there is a clear, tested reason to change.
- Do not add runtime guards (`time_stop`, `adverse_exit`) to the global multi-strategy sweep unless the combinatorial cost is explicitly accepted.
- Use the dedicated guard sweeps for `PDHL` and `VWAPPullback` instead of expanding the global sweep space.
- Use the dedicated `pullback_intraday_sweep` when the goal is true intraday rotation instead of EOD drift.
- The intraday pullback wrapper currently uses:
  - `TP = {0.5,1.0,1.5,2.0,3.0}%`
  - `SL = {0.4,0.6,0.8,1.0,1.5,2.0}%`
  - `min_bars = {2,3,5}`
  - `confirm_bars = {0,1}`
  - `vwap_prox = {0.1%,0.2%,0.3%}`
  - `ema_period = {100,200}`
  - `max_hold = {15m,30m,60m}`
  - `time_stop_minutes = {20,40,60}`
  - `max_trades_per_day = {2,4}`
- Intraday filter wrapper defaults are intentionally different from the standard anti-overfit gate:
  - `min_trades=50`
  - `min_return=5`
  - `max_dd=8`
  - `min_ret_dd=1.5`
  - `min_neighbors=12`
  - `min_avg_trades_per_day=1.0`
  - `max_eod_ratio=50`
  - `max_avg_hold_minutes=120`
- Intraday walk-forward wrapper defaults are also relaxed relative to the standard gate:
  - `min_train_trades=30`
  - `max_train_dd=8`
  - `min_train_return=2`
  - `min_train_trades_per_day=1.0`
  - `max_train_eod_ratio=50`
  - `max_train_avg_hold=120`
- `pullback_intraday_sweep` now exports `avg_trades_per_day` in its CSV results, and both
  `filter_overfit.py` and `walk_forward.py` can reject low-frequency setups explicitly.
- Re-running `DOGEUSDT` under the tighter intraday grid confirmed the intended behavior change:
  `EOD` dependence went to ~0 and average hold dropped to roughly 25-60 minutes, but the
  symbol still failed because even the best setups remained below `1.0` trade/day on average.
- When bots need reload/restart during support or debugging, the assistant should ask the user to run `make stop && make start` in their own terminal.
- Do not restart live bots from the assistant side unless the user explicitly asks for that action.
- `scripts/walk_forward.py` cache keys must include the sweep binary identity (path + size + mtime). Without that, dedicated binaries such as `pullback_guard_sweep` and `pullback_intraday_sweep` can incorrectly reuse old train caches from another sweep family.

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
- For symbols `ETHUSDT`, `GALAUSDT`, `XAUUSDT`, `1000SHIBUSDT`:
  - `symbol_configs.pos_size_pct = 0.40`
  - `leverage = 1`
  - This avoids low notional startup failures with small account balances.
- `SOLUSDT` was moved out of recovery mode:
  - DB `symbol_configs.mode` set to `normal` (was `monitoring`)
  - Runtime risk params restored to standard MomShort profile:
    - `pos_size_pct = 0.40`
    - `leverage = 30`
  - Reason: only 1 closed trade on 2026-03-04 (BRT), no longer treated as recovery symbol.
- `MANAUSDT` was migrated from `MomShort` to `PDHL` in runtime portfolio:
  - DB `symbol_configs` updated to:
    - `strategy_name=PDHL`, `interval=1m`
    - `tp_pct=7.0`, `sl_pct=1.5`, `confirm_bars=3`
    - `pdhl_prox_pct=0.005`, `vwap_dist_stop=0.03`
    - `champion_return_pct=95.61`, `champion_max_dd=13.87`
  - `make bots` now starts `manausdt` via `python -m trader pdhl --symbol manausdt`.
- `LDOUSDT` was added to runtime portfolio as `PDHL`:
  - Champion from 1m sweep:
    - TP 7.0 / SL 2.0
    - 985 trades, 40.1% win rate
    - +79.96% return, maxDD 18.23%
  - `make bots` now starts `ldousdt` via `python -m trader pdhl --symbol ldousdt`.
- `RLCUSDT` was added to runtime portfolio as `PDHL`:
  - Selected onboarding profile (2026-03-05):
    - `TF=15m`, `tp_pct=3.0`, `sl_pct=2.0`, `confirm_bars=1`, `vwap_prox=0.0`
    - `champion_return_pct=48.61`, `champion_win_rate=50.7`, `champion_trades=888`, `champion_max_dd=12.31`
  - Fallback config in `trader/config.py` uses:
    - `price_decimals=4`, `qty_decimals=1`, `min_notional=5.0`, `vwap_dist_stop=0.05`
  - `make bots` now starts `rlcusdt` via `python -m trader pdhl --symbol rlcusdt`.
- `MTLUSDT` was added to runtime portfolio as `PDHL`:
  - Selected onboarding profile (2026-03-05, explicit user choice):
    - `TF=1m`, `tp_pct=5.0`, `sl_pct=5.0`, `confirm_bars=1`, `pdhl_prox_pct=0.0`
    - `champion_return_pct=83.58`, `champion_win_rate=53.1`, `champion_trades=980`, `champion_max_dd=14.44`
  - Exchange precision for runtime/fallback:
    - `price_decimals=4`, `qty_decimals=0`, `min_notional=5.0`
  - `make bots` now starts `mtlusdt` via `python -m trader pdhl --symbol mtlusdt`.
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
- `VWAPPullback` execution precision was fixed for maker entries and protective prices:
  - `trader/bot_vwap_pullback.py` no longer trusts DB/config `price_decimals` alone for live execution.
  - On every live startup, it refreshes `PRICE_FILTER`/`LOT_SIZE` from Binance and quantizes prices/qty by the actual `tickSize`/`stepSize`.
  - This fixes Binance `-4014` (`Price not increased by tick size.`) on symbols such as `UNIUSDT`, where `pricePrecision=4` but valid price grid is `tickSize=0.0010` (3 effective decimals).
  - `db/apply_champion.py` now persists `price_decimals`/`qty_decimals` from `tickSize`/`stepSize`, not from `pricePrecision`/`quantityPrecision`.
  - `trader/config.py` fallback for `UNIUSDT` was aligned to `price_decimals=3`.
- Dashboard `Bots` page now reconciles bot registry state with live exchange positions:
  - `frontend/src/pages/Bots.tsx` uses `/api/positions` as the source of truth for the `In Position` count/filter/grouping.
  - This prevents undercounting when a bot registry entry is out of sync with Binance, such as an open position shown in `/api/positions` but `state=SCANNING` in `/api/bot_states`.
  - Cards with an open exchange position but non-`IN_POSITION` registry state now show a `POSITION DESYNC` badge, render the live position details, and display effective status `IN_POSITION` in the UI.
- Docker Postgres healthcheck now targets the configured DB explicitly:
  - `pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}`
  - Prevents recurring Postgres log spam like `FATAL: database "trader" does not exist`.
- Docker Compose naming was normalized for this repo:
  - `docker-compose.yml` now sets `name: binance-algo-trading`.
  - Fixed `container_name` entries were removed to avoid cross-project name conflicts.
  - Containers now follow Compose default naming (project/service/index), e.g. `binance-algo-trading-postgres-1`.
  - Finalized migration to `binance-algo-trading` volume names:
    - Postgres: `binance-algo-trading_pg_data`
    - Redis: `binance-algo-trading_redis-data`
  - Both named volumes are declared as `external: true` in Compose to avoid project-label warnings.
- Docker backend image build was fixed for current project layout:
  - `Dockerfile` now uses `poetry==2.1.3` (compatible with `pyproject.toml` PEP 621 `[project]` metadata).
  - Dependency install uses `poetry install --only main --no-root` to avoid package install before source copy.
  - Backend image now copies both `trader/` and `db/` modules; this fixes runtime startup error:
    - `ModuleNotFoundError: No module named 'db'`.
- New configurable protection was added for live bots:
  - `symbol_configs.be_profit_usd` (default `0.50`) defines unrealized profit in USDT that triggers auto-breakeven.
  - When position PnL reaches `be_profit_usd`, bot moves SL to entry (`0a0`) automatically and sends Telegram notification via `notify_stop_loss_updated(...)`.
  - Implemented in active bots: `MomShort`, `VWAPPullback`, `PDHL`, `ORB`, `EMAScalp`.
  - UI/API support:
    - `PATCH /api/symbol_configs/{symbol}/protection` updates `be_profit_usd` in DB.
    - `/bots` configuration panel now allows editing/saving `be_profit_usd` per symbol.
  - Runtime note:
    - Changing `be_profit_usd` through UI updates DB/state display immediately, but running bot processes need restart to apply new threshold internally.
- `LDOUSDT` (PDHL) was added to DB runtime config (`symbol_configs`):
  - `strategy_name=PDHL`, `interval=1m`
  - `tp_pct=7.0`, `sl_pct=2.0`, `confirm_bars=1`
  - `pos_size_pct=0.40`, `leverage=30`, `mode=normal`
  - `pdhl_prox_pct=0.000`, `be_profit_usd=0.50`
- `GALAUSDT` runtime risk was tightened directly in DB (`symbol_configs`) on 2026-03-06:
  - `strategy_name=VWAPPullback`, `interval=1m`, `mode=monitoring`, `leverage=1`
  - `tp_pct=10.0`, `sl_pct=2.0`, `min_bars=3`, `confirm_bars=0`, `vwap_prox=0.002`
  - `pos_size_pct=0.05`, `be_profit_usd=0.05`
  - Practical effect:
    - next entries target notional near Binance minimum (~5 USDT), so PnL moves should stay in low-cent range;
    - with account capital around 109 USDT, a full `2%` stop is roughly ~0.11 USDT gross loss before fees/rounding;
    - further reductions may start skipping entries because `GALAUSDT` uses integer quantity and ~5 USDT min notional.
- Remaining recovery-mode symbols were tightened directly in DB (`symbol_configs`) on 2026-03-06:
  - `1000SHIBUSDT` (`VWAPPullback`, `5m`, `monitoring`, `leverage=1`):
    - `sl_pct=2.0`, `pos_size_pct=0.05`, `be_profit_usd=0.05`
  - `ETHUSDT` (`VWAPPullback`, `5m`, `monitoring`, `leverage=1`):
    - `sl_pct=2.0`, `pos_size_pct=0.20`, `be_profit_usd=0.10`
  - `XAUUSDT` (`VWAPPullback`, `1m`, `monitoring`, `leverage=1`):
    - `sl_pct=2.0`, `pos_size_pct=0.05`, `be_profit_usd=0.05`
  - Validation method:
    - use signed Binance Futures `/fapi/v1/order/test` with the exact proposed maker `price`/`qty`;
    - do not rely only on raw `exchangeInfo` filter fields for micro-sizing decisions on recovery symbols, because the filter payload may look inconsistent for some contracts while `order/test` accepts the intended order.
  - Accepted live test notionals on 2026-03-06:
    - `1000SHIBUSDT`: ~`5.31` USDT
    - `ETHUSDT`: ~`20.46` USDT
    - `XAUUSDT`: ~`5.11` USDT
- `ICXUSDT` was onboarded to the runtime portfolio as `PDHL` on `5m`:
  - Selected onboarding profile (2026-03-05, explicit user choice despite robustness caveat):
    - `tp_pct=7.0`, `sl_pct=2.0`, `confirm_bars=2`
    - `pdhl_prox_pct=0.005`, `pos_size_pct=0.20`
    - `champion_return_pct=47.31`, `champion_win_rate=44.6`, `champion_trades=879`, `champion_max_dd=13.27`
  - Exchange precision for runtime/fallback:
    - `price_decimals=4`, `qty_decimals=0`, `min_notional=5.0`
  - `make bots` now starts `icxusdt` via `python -m trader pdhl --symbol icxusdt`.
- PostgreSQL default database naming was migrated to the new project convention:
  - Physical DB rename executed: `binance_trader` -> `binance_algo_trading` (data preserved).
  - Local defaults/config references were aligned to `binance_algo_trading` in:
    - `.env` (`POSTGRES_DB`)
    - `docker-compose.yml` (`POSTGRES_DB` defaults + healthcheck target)
    - `Makefile` (`db-shell` fallback DB)
    - `docs/DB_ACCESS.md` examples/defaults
- Live bot runtime hardening was applied for stream resiliency and order-price safety:
  - Bots updated: `MomShort`, `VWAPPullback`, `PDHL`, `ORB`, `EMAScalp`.
  - WebSocket stale-candle watchdog added with automatic reconnect loop:
    - each bot tracks last closed candle timestamp and reconnects stream when stale.
    - stale threshold is interval-aware (`~3x interval + 90s`, minimum 180s).
  - Order fill price safety hardened:
    - market fill `avg_price` now requires positive value; if API returns `"0"`, bots fallback to computed/fallback positive price.
    - prevents invalid `SL/TP` triggers derived from zero fill price.
  - Trigger price safety hardened:
    - `SL/TP` trigger prices are now clamped to positive minimum tick when rounding would produce `<= 0`.
    - prevents Binance error `-4006` (`Stop price less than zero`) on very small-price symbols.
  - Exchange precision parsing was made SDK-object-safe:
    - filter parsing no longer assumes dict (`f.get(...)`) and now supports object attributes.
    - avoids precision-fetch fallback warnings and reduces `-1111` precision failures.
- Bot heartbeat state overwrite bug was fixed in registry publishing:
  - Heartbeat loops for `MomShort`, `VWAPPullback`, `PDHL`, `ORB`, `EMAScalp`, `VWAPPullback-v2`
    no longer send static `state` captured at startup.
  - Root cause: heartbeat was repeatedly overwriting runtime state (e.g., forcing `SCANNING`)
    and hiding real open positions in `/bots` for symbols that opened trades after startup.
- Startup position reconciliation now updates Redis registry immediately:
  - On restart, when a bot resumes an already-open Binance position, it now publishes
    `state=IN_POSITION` plus direction/qty/entry/SL/TP to `bot:states` right away.
  - This removes the stale `SCANNING` window after reboot (especially critical for 1h bots
    like `MAGICUSDT`, which previously waited until next candle to reflect open positions in `/bots`).
- Onboarding workflow now includes an automated anti-overfitting stage:
  - `make filter-overfit SYMBOL=<symbol>`
  - Layer 1: hard constraints (`trades`, `return`, `maxDD`, `ret/DD`)
  - Layer 2: parameter-neighborhood robustness checks
  - Layer 3: monthly consistency check via re-simulation on the selected timeframe kline file
- Walk-forward validation added for OOS robustness:
  - `make walk-forward SYMBOL=<symbol> TF=<tf> TRAIN_DAYS=<n> TEST_DAYS=<m> STEP_DAYS=<k>`
  - Per fold:
    1) optimize on train window with Rust sweep,
    2) freeze best params,
    3) evaluate on next unseen test window.
  - If no fold produces a train candidate, `scripts/walk_forward.py` now still writes the
    folds/summary CSVs cleanly instead of crashing on a missing `test_error` column.
- Maker-first execution mode was introduced for live active bots:
  - Bots updated: `MomShort`, `VWAPPullback`, `PDHL`, `ORB`, `EMAScalp`.
  - Entry flow now prefers maker (`LIMIT` + `GTX` / post-only) and falls back to `MARKET`
    only after timeout if not filled.
  - EOD and early-protection closes now also try maker reduce-only `LIMIT` first,
    then fall back to reduce-only `MARKET` on timeout.
  - `STOP_MARKET` is retained only for critical protection (hard SL / breakeven SL updates).
  - Fixed TP orders for `MomShort`, `VWAPPullback` and `PDHL` (when `tp_pct` is configured)
    were changed from `TAKE_PROFIT_MARKET` to maker reduce-only `LIMIT`.
  - New env knobs (all bots above):
    - `PREFER_MAKER_EXECUTION` (default `1`)
    - `MAKER_PRICE_OFFSET_PCT` (default `0.0002`)
    - `MAKER_ENTRY_TIMEOUT_SEC` (default `8`)
    - `MAKER_EXIT_TIMEOUT_SEC` (default `6`)
    - `MAKER_POLL_INTERVAL_SEC` (default `0.4`)
- `PDHL` CLI fallback now honors per-symbol proximity from `trader/config.py` when DB is unavailable:
  - `trader/cli.py` uses `SymbolConfig.vwap_prox` as the fallback source for `prox_pct`.
  - This keeps fallback behavior aligned with symbol-specific PDHL configs such as `MANAUSDT`, `BCHUSDT`, and `ICXUSDT`.
- Dedicated PDHL detailed backtest workflow was added:
  - `make backtest-detail-pdhl` runs `scripts/backtest_detail_pdhl.py`.
  - The script supports:
    - `signal_engine=live|sweep`
    - fixed `TP/SL/EOD` replay
    - optional runtime protections (`be_profit_usd`, time stop, adverse momentum)
  - It is single-position and serial by design, so results can differ from the Rust sweep when the sweep emitted overlapping same-day PDHL entries.
- ORB/PDHL logging stability fix:
  - Added missing ANSI color constant `CYAN` in `trader/bot_orb.py` and `trader/bot_pdhl.py`.
  - This prevents runtime close-path errors like `name 'CYAN' is not defined` during maker close logging.
  - MomShort (`trader/bot.py`) now treats Binance `-5022` post-only rejects as expected
    maker misses for entry/EOD maker attempts and immediately falls back to `MARKET`
    instead of surfacing `Entry failed`.
  - MomShort TP placement hardening:
    - Initial TP order remains maker-first (`LIMIT` + `GTX`).
    - If TP is rejected by Binance `-5022` (would execute as taker), bot retries TP as
      `LIMIT` + `GTC` (reduce-only) instead of failing the whole entry flow.
    - The same GTX->GTC retry is applied when TP is re-placed during auto-breakeven SL updates.
- Maker reject hardening for non-`MomShort` bots:
  - Bots updated: `VWAPPullback`, `PDHL`, `ORB`, `EMAScalp`.
  - Entry flow now treats Binance `-5022` as an expected maker miss:
    - immediate post-only reject on maker entry no longer raises `Entry failed`;
      the bot logs the reject and falls back to `MARKET`.
  - EOD/early-exit close flow now treats Binance `-5022` on maker reduce-only close
    the same way:
    - immediate post-only reject falls back to reduce-only `MARKET`;
    - the bot only transitions to `COOLDOWN` after re-checking that the live position
      is actually flat on Binance.
  - If a close attempt still fails and the position remains open, the bot now:
    - keeps `state=IN_POSITION`,
    - clears `_risk_exit_pending` so the protection can retry on later candles,
    - updates Redis registry with the still-open live position,
    - re-arms exchange-side `STOP_MARKET` protection immediately,
    - emits `notify_error(..., "Exit failed")` instead of logging
      `position already closed`.
  - Startup resume hardening:
    - when `VWAPPullback`, `PDHL`, `ORB`, or `EMAScalp` adopt an already-open Binance
      position on startup, they now re-arm the exchange-side `STOP_MARKET`
      before returning to live monitoring.
    - This is specifically important after any prior failed close path that cancelled
      algo orders but left the position open.
  - This fixes the class of incidents where `LINKUSDT` / `LDOUSDT` / `GALAUSDT`
    could hit early-exit logic, receive maker close reject `-5022`, and be left
    open while the bot incorrectly moved to `COOLDOWN`.
- Trade sync/dashboard parity hardening (2026-03-08):
  - `trades` now persists Binance `trade_id` per fill and uses `(symbol, trade_id)` as
    the dedupe key instead of `(symbol, order_id)`.
  - Reason:
    - futures orders can fill in multiple parts under the same `order_id`; the old schema
      collapsed partial fills and undercounted realized PnL / fees in the dashboard.
  - Files updated:
    - `db/migrations/009_trades_use_trade_id.sql`
    - `db/sync_trades.py`
    - `db/backfill_trades.py`
  - Repair workflow:
    - after migrating, run `poetry run python -m db.backfill_trades --from YYYY-MM-DD`
      to restore historical missing fills;
    - then delete synthetic placeholder rows where `trade_id = order_id` but real fill rows
      for the same `(symbol, order_id)` now exist, and rebuild `daily_performance` for the
      affected `symbol + trade_date` pairs.
  - `db/backfill_trades.py` now walks Binance `account_trade_list` in explicit 7-day windows
    using `start_time` + `end_time`, which is required for older date ranges.
  - Dashboard calendar behavior was aligned to the project reporting timezone:
    - frontend `Today` now means the current `America/Sao_Paulo` calendar day,
      not a rolling last-24-hours window;
    - daily grouping in `Overview` / `History` also uses `America/Sao_Paulo`.
  - Account/dashboard valuation now includes non-USDT futures wallet assets:
    - `trader/api.py:get_account_summary()` converts balances such as `BNB` to USDT using
      live Binance prices instead of looking only at the `USDT` row from
      `futures_account_balance_v3`.
    - This prevents large balance/equity mismatches versus Binance Balance Analysis when
      fee-discount inventory is held in futures wallet.
  - Dashboard trade/fee reporting now converts non-USDT commissions to USDT:
    - `/api/trades` enriches each trade with `commission_usdt`;
    - `Overview`, `History`, and `PerformanceMetrics` use `commission_usdt` for net/fee totals.
  - `/api/commissions` now reads Binance `income_history` directly and converts fee assets
    (for example `BNB`) into USDT-equivalent values, instead of summing raw DB `commission`
    numbers as if they were already USDT.
  - `/api/account_analysis` now exists for Binance-style account P&L:
    - uses futures account snapshots + transfer history from Binance instead of trade rows;
    - its date window follows the dashboard reporting timezone (`America/Sao_Paulo`) and
      preset filters are calendar-day based (`Today`, `7d`, `30d`), not rolling `N x 24h`;
    - `Overview` now shows this in a separate `Binance Account Analysis` section so users
      do not compare trade-level `Gross/Net P&L` cards against Binance `Balance Analysis`.
