//! Range strategy — port pendente (`backtest_sweep_range/`).
//!
//! Caso especial: retorna `StrategyOutput::Runs` (exit baked-in, ignora exits externos).
//! Logic atual em `backtest_sweep_range/src/main.rs::run_backtest`.

// TODO[milestone]: portar com grid (adx_thresh, atr_pct_thresh, range_lookback,
// zone_pct, tp_range_pct, sl_range_pct, recent_thresh_pct, max_orders, pos_size).
