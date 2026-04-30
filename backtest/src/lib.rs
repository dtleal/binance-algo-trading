//! backtest — sweep engine unificado.
//!
//! Substitui os 3 binários legados (backtest_sweep, backtest_sweep_v2,
//! backtest_sweep_range) por um único crate trait-based, lendo Postgres direto.
//!
//! Ver `.claude/skills/binance-refac/plans/milestone_sweep_rewrite.md`.

pub mod db;
pub mod indicator;
pub mod strategy;
pub mod exit;
pub mod sweep;
pub mod detail;
pub mod chart;
pub mod release;
pub mod evaluate;
pub mod overfit;
pub mod walkforward;
pub mod onboard;
pub mod progress;
pub mod range_debug;
pub mod params_row;
pub mod correlate;
pub mod regime;

mod types;

pub use types::*;
