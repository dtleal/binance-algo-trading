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

mod types;

pub use types::*;
