//! VWAP rolante por janela de N dias.

use crate::types::Candle;

/// VWAP com janela móvel de `window_days` dias.
/// Implementação via prefix sums (PV cumulativo + volume cumulativo) para O(N).
pub fn vwap_rolling(candles: &[Candle], window_days: u32) -> Vec<f64> {
    let n = candles.len();
    if n == 0 {
        return Vec::new();
    }

    // Prefix sums
    let mut prefix_pv  = vec![0.0_f64; n + 1];
    let mut prefix_vol = vec![0.0_f64; n + 1];
    for i in 0..n {
        let tp = (candles[i].high + candles[i].low + candles[i].close) / 3.0;
        prefix_pv[i + 1]  = prefix_pv[i]  + tp * candles[i].volume;
        prefix_vol[i + 1] = prefix_vol[i] + candles[i].volume;
    }

    // Day boundaries
    let mut day_bounds: Vec<(u32, usize)> = Vec::new();
    let mut cur_day = u32::MAX;
    for (i, c) in candles.iter().enumerate() {
        if c.day != cur_day {
            day_bounds.push((c.day, i));
            cur_day = c.day;
        }
    }

    let mut out = Vec::with_capacity(n);
    for i in 0..n {
        let cday = candles[i].day;
        let start_day = if window_days <= cday { cday - window_days + 1 } else { 0 };
        let pos = day_bounds.partition_point(|&(d, _)| d < start_day);
        let start_idx = if pos < day_bounds.len() { day_bounds[pos].1 } else { 0 };
        let pv  = prefix_pv[i + 1]  - prefix_pv[start_idx];
        let vol = prefix_vol[i + 1] - prefix_vol[start_idx];
        out.push(if vol > 0.0 { pv / vol } else { candles[i].close });
    }
    out
}
