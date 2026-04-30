//! Regime detection — M1 da skill /regime-conditional.
//!
//! - `features`: 4 indicadores ortogonais (ADX, ATR%, RSI, BB-squeeze) computados
//!   de forma causal (look-ahead-free). NaN antes do warmup.

pub mod features;

pub use features::{RegimeVector, RegimeArrays};
