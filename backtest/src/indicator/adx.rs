//! ADX via Wilder smoothing.

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

/// ADX(period) Wilder. Retorna `Vec<f64>` do tamanho de candles, com 0.0 nos
/// primeiros candles onde ADX ainda não está definido.
pub fn adx_wilder(candles: &[Candle], period: usize) -> Vec<f64> {
    let n = candles.len();
    let mut out = vec![0.0_f64; n];
    if n < period * 2 + 1 { return out; }

    let mut plus_dm  = Vec::with_capacity(n - 1);
    let mut minus_dm = Vec::with_capacity(n - 1);
    let mut tr       = Vec::with_capacity(n - 1);

    for i in 1..n {
        let up   = candles[i].high - candles[i - 1].high;
        let down = candles[i - 1].low - candles[i].low;
        plus_dm.push(if up > down && up > 0.0 { up } else { 0.0 });
        minus_dm.push(if down > up && down > 0.0 { down } else { 0.0 });
        let h = candles[i].high; let l = candles[i].low; let pc = candles[i - 1].close;
        tr.push((h - l).max((h - pc).abs()).max((l - pc).abs()));
    }

    let sm_pdm = wilder_smooth(&plus_dm,  period);
    let sm_mdm = wilder_smooth(&minus_dm, period);
    let sm_tr  = wilder_smooth(&tr,       period);

    let len = sm_pdm.len().min(sm_mdm.len()).min(sm_tr.len());
    let mut dx = Vec::with_capacity(len);
    for i in 0..len {
        let pdi = if sm_tr[i] > 0.0 { 100.0 * sm_pdm[i] / sm_tr[i] } else { 0.0 };
        let mdi = if sm_tr[i] > 0.0 { 100.0 * sm_mdm[i] / sm_tr[i] } else { 0.0 };
        let sum = pdi + mdi;
        dx.push(if sum > 0.0 { 100.0 * (pdi - mdi).abs() / sum } else { 0.0 });
    }

    let adx = wilder_smooth(&dx, period);
    // sm_*[0] corresponde a candles[period]; adx[0] corresponde a candles[period*2].
    for (j, &v) in adx.iter().enumerate() {
        let idx = j + period * 2;
        if idx < n { out[idx] = v; }
    }
    out
}
