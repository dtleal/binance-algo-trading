//! EMA sequencial sobre `close`.

use crate::types::Candle;

/// EMA(period) sobre close. NaN nos primeiros (period-1) candles.
pub fn ema(candles: &[Candle], period: usize) -> Vec<f64> {
    let n = candles.len();
    let mut out = vec![f64::NAN; n];
    if n == 0 || period == 0 {
        return out;
    }
    let k = 2.0 / (period as f64 + 1.0);
    let mut e = candles[0].close;
    for i in 0..n {
        e = candles[i].close * k + e * (1.0 - k);
        if i + 1 >= period {
            out[i] = e;
        }
    }
    out
}
