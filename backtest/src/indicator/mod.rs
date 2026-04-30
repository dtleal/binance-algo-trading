//! Indicadores como funções puras sobre `&[Candle]`.
//!
//! Cada arquivo expõe funções diretas (`fn vwap`, `fn ema`, ...).
//! Sem trait — strategies importam o que precisam.

pub mod vwap;
pub mod ema;
pub mod atr;
pub mod adx;
pub mod rsi;
pub mod bb_squeeze;

pub use vwap::vwap_rolling;
pub use ema::ema;
pub use atr::atr_wilder;
pub use adx::adx_wilder;
pub use rsi::rsi_wilder;
pub use bb_squeeze::bb_squeeze;
