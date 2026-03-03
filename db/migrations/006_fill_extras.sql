-- Migration 006: Fill strategy-specific extras for all symbols
-- ema_period, max_trades_per_day, pdhl_prox_pct, be_r, trail_step, range_mins
-- were never populated — bots were falling back to hardcoded CLI defaults.

-- ── VWAPPullback ─────────────────────────────────────────────────────────────
-- ema_period=200 (EMA trend filter), max_trades_per_day=4
UPDATE symbol_configs
SET
    ema_period         = 200,
    max_trades_per_day = 4
WHERE strategy_name = 'VWAPPullback';

-- ── MomShort ─────────────────────────────────────────────────────────────────
-- max_trades_per_day=1 (one momentum signal per day per symbol)
UPDATE symbol_configs
SET
    max_trades_per_day = 1
WHERE strategy_name = 'MomShort';

-- ── PDHL ─────────────────────────────────────────────────────────────────────
-- pdhl_prox_pct=0.002 (0.2% proximity to PDH/PDL)
-- be_r=2.0, trail_step=0.5 (R-multiple trailing stop defaults)
-- max_trades_per_day=4
UPDATE symbol_configs
SET
    pdhl_prox_pct      = 0.002,
    be_r               = 2.0,
    trail_step         = 0.5,
    max_trades_per_day = 4
WHERE strategy_name = 'PDHL';

-- ── ORB ──────────────────────────────────────────────────────────────────────
-- range_mins=30 (30-min opening range)
-- be_r=2.0, trail_step=0.5
-- max_trades_per_day=4
UPDATE symbol_configs
SET
    range_mins         = 30,
    be_r               = 2.0,
    trail_step         = 0.5,
    max_trades_per_day = 4
WHERE strategy_name = 'ORB';

-- KSM is a 1h ORB — opening range is 60 minutes
UPDATE symbol_configs
SET range_mins = 60
WHERE symbol = 'KSMUSDT';

-- ── EMAScalp ─────────────────────────────────────────────────────────────────
-- fast_period=8, slow_period=21, max_trades_per_day=10, be_r=2.0, trail_step=0.5
UPDATE symbol_configs
SET
    fast_period        = 8,
    slow_period        = 21,
    be_r               = 2.0,
    trail_step         = 0.5,
    max_trades_per_day = 10
WHERE strategy_name = 'EMAScalp';

-- ── updated_at ───────────────────────────────────────────────────────────────
UPDATE symbol_configs SET updated_at = NOW();
