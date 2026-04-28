//! ATR via Wilder smoothing.

use crate::types::Candle;

fn wilder_smooth(values: &[f64], period: usize) -> Vec<f64> {
    if values.len() < period { return Vec::new(); }
    let init = values[..period].iter().sum::<f64>() / period as f64;
    let mut out = vec![init];
    let k = 1.0 / period as f64;
    for &v in &values[period..] {
        let prev = *out.last().unwrap();
        out.push(prev * (1.0 - k) + v * k);
    }
    out
}

/// ATR(period) Wilder. Retorna `Vec<f64>` do tamanho de candles, com 0.0 nos
/// primeiros `period` valores onde ATR ainda não está definido.
pub fn atr_wilder(candles: &[Candle], period: usize) -> Vec<f64> {
    let n = candles.len();
    let mut out = vec![0.0_f64; n];
    if n < period + 1 { return out; }

    let trs: Vec<f64> = (1..n)
        .map(|i| {
            let h = candles[i].high;
            let l = candles[i].low;
            let pc = candles[i - 1].close;
            (h - l).max((h - pc).abs()).max((l - pc).abs())
        })
        .collect();

    let sm = wilder_smooth(&trs, period);
    for (j, &v) in sm.iter().enumerate() {
        let idx = j + period;
        if idx < n { out[idx] = v; }
    }
    out
}
