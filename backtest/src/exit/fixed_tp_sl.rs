//! Fixed TP/SL exit — port pendente (`evaluate` do sweep_v1).

use crate::types::*;

/// Hot path: TP e SL fixos como % do entry price. Sai no primeiro hit, ou EOD.
///
/// Logic atual em `backtest_sweep/src/main.rs::evaluate` (parte do exit).
pub fn evaluate(_entry: &Entry, _candles: &[Candle], _tp_pct: f64, _sl_pct: f64) -> ExitResult {
    // TODO[milestone]: portar do crate antigo. Stub para o scaffold compilar.
    ExitResult { exit_price: 0.0, is_eod: true }
}
