//! Bollinger Bands squeeze: (BB upper - BB lower) / SMA — width relativa.
//!
//! Captura "coiling" (compressão) vs "expansion". Útil pra detectar regime
//! de range vs breakout.

use crate::types::Candle;

/// `period`: janela da SMA (default 20). `k`: multiplicador da std (default 2 → BB padrão).
/// Output: NaN antes do warmup; depois (2k * std) / SMA.
pub fn bb_squeeze(candles: &[Candle], period: usize, k: f64) -> Vec<f64> {
    let mut out = vec![f64::NAN; candles.len()];
    if candles.len() < period { return out; }

    for i in (period - 1)..candles.len() {
        let window = &candles[i + 1 - period..=i];
        let mean = window.iter().map(|c| c.close).sum::<f64>() / period as f64;
        let variance = window.iter()
            .map(|c| (c.close - mean).powi(2))
            .sum::<f64>() / period as f64;
        let std = variance.sqrt();
        if mean.abs() > 1e-12 {
            out[i] = (2.0 * k * std) / mean;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::Candle;
    use chrono::{TimeZone, Utc};

    fn synth_candle(close: f64, idx: i64) -> Candle {
        let t = Utc.with_ymd_and_hms(2025, 1, 1, 0, 0, 0).unwrap()
            + chrono::Duration::minutes(idx * 15);
        Candle {
            open_time: t, close_time: t + chrono::Duration::minutes(15),
            open: close, high: close, low: close, close, volume: 1.0,
            day: (t.timestamp() / 86_400) as u32,
            minute_of_day: ((t.timestamp() % 86_400) / 60) as u16,
        }
    }

    #[test]
    fn pre_warmup_is_nan() {
        let candles: Vec<Candle> = (0..10).map(|i| synth_candle(100.0, i)).collect();
        let bb = bb_squeeze(&candles, 20, 2.0);
        assert!(bb.iter().all(|x| x.is_nan()));
    }

    #[test]
    fn squeeze_low_in_flat_market() {
        // Mercado completamente flat → std=0 → squeeze=0
        let candles: Vec<Candle> = (0..30).map(|i| synth_candle(100.0, i)).collect();
        let bb = bb_squeeze(&candles, 20, 2.0);
        assert!(bb[29].abs() < 1e-6, "flat market deveria ter squeeze ~0, got {}", bb[29]);
    }

    #[test]
    fn squeeze_high_in_volatile_market() {
        // Oscilação grande: ±5% ao redor de 100
        let candles: Vec<Candle> = (0..30).map(|i| {
            let v = if i % 2 == 0 { 105.0 } else { 95.0 };
            synth_candle(v, i)
        }).collect();
        let bb = bb_squeeze(&candles, 20, 2.0);
        assert!(bb[29] > 0.05, "volatile deveria ter squeeze > 5%, got {}", bb[29]);
    }
}
