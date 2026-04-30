//! RSI Wilder smoothing (causal, NaN antes do warmup).

use crate::types::Candle;

pub fn rsi_wilder(candles: &[Candle], period: usize) -> Vec<f64> {
    let mut out = vec![f64::NAN; candles.len()];
    if candles.len() < period + 1 { return out; }

    let mut gains  = 0.0_f64;
    let mut losses = 0.0_f64;
    for i in 1..=period {
        let delta = candles[i].close - candles[i-1].close;
        if delta > 0.0 { gains += delta; } else { losses += -delta; }
    }
    let mut avg_g = gains  / period as f64;
    let mut avg_l = losses / period as f64;
    out[period] = compute_rsi(avg_g, avg_l);

    for i in (period + 1)..candles.len() {
        let delta = candles[i].close - candles[i-1].close;
        let (g, l) = if delta > 0.0 { (delta, 0.0) } else { (0.0, -delta) };
        let pm1 = (period - 1) as f64;
        avg_g = (avg_g * pm1 + g) / period as f64;
        avg_l = (avg_l * pm1 + l) / period as f64;
        out[i] = compute_rsi(avg_g, avg_l);
    }
    out
}

#[inline]
fn compute_rsi(avg_g: f64, avg_l: f64) -> f64 {
    if avg_l <= 1e-12 { 100.0 }
    else { 100.0 - 100.0 / (1.0 + avg_g / avg_l) }
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
    fn rsi_pre_warmup_is_nan() {
        let candles: Vec<Candle> = (0..10).map(|i| synth_candle(100.0 + i as f64, i)).collect();
        let r = rsi_wilder(&candles, 14);
        assert!(r.iter().all(|x| x.is_nan()));
    }

    #[test]
    fn rsi_overbought_in_strong_uptrend() {
        // close cresce 0.5% por candle por 30 candles
        let mut c = 100.0;
        let candles: Vec<Candle> = (0..30).map(|i| {
            let cd = synth_candle(c, i);
            c *= 1.005;
            cd
        }).collect();
        let r = rsi_wilder(&candles, 14);
        assert!(r[29] > 75.0, "trend forte deveria ter RSI > 75, got {}", r[29]);
    }

    #[test]
    fn rsi_neutral_in_flat_market() {
        // close oscila ±0.1% ao redor de 100
        let candles: Vec<Candle> = (0..30).map(|i| {
            let oscill = if i % 2 == 0 { 100.1 } else { 99.9 };
            synth_candle(oscill, i)
        }).collect();
        let r = rsi_wilder(&candles, 14);
        // Deveria ficar ~50
        assert!((r[29] - 50.0).abs() < 15.0, "flat deveria dar RSI ~50, got {}", r[29]);
    }
}
