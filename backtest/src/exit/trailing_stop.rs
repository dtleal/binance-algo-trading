//! Trailing-stop exit — port pendente (`evaluate` + `trailing_sl_price` do sweep_v2).

use crate::types::*;

/// Hot path: SL trailing por R-multiple, TP_R opcional.
///
/// Logic atual em `backtest_sweep_v2/src/main.rs::evaluate` + `trailing_sl_price`.
pub fn evaluate(
    _entry: &Entry, _candles: &[Candle],
    _sl_pct: f64, _be_r: f64, _trail_step: f64, _tp_r: f64,
) -> ExitResult {
    // TODO[milestone]: portar. Stub para o scaffold compilar.
    ExitResult { exit_price: 0.0, is_eod: true }
}
