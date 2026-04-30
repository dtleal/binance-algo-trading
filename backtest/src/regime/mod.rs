//! Regime detection — M1 da skill /regime-conditional.
//!
//! - `features`: 4 indicadores ortogonais (ADX, ATR%, RSI, BB-squeeze) computados
//!   de forma causal (look-ahead-free). NaN antes do warmup.

pub mod features;
pub mod fingerprint;
pub mod score;

pub use features::{RegimeVector, RegimeArrays};
pub use score::{StrategyFingerprint, BucketProfile, regime_match_score, load_fingerprint};
