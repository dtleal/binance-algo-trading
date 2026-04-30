//! Vetor de regime: 4 features ortogonais por candle.
//!
//! Causal por construção (cada feature em i usa apenas candles 0..=i).
//! NaN antes do warmup (50 candles) — caller via `at()` retorna `None`.

use std::sync::Arc;

use crate::indicator::{adx_wilder, atr_wilder, rsi_wilder, bb_squeeze};
use crate::types::Candle;

const WARMUP: usize = 50;
const ADX_PERIOD:  usize = 14;
const ATR_PERIOD:  usize = 14;
const RSI_PERIOD:  usize = 14;
const BB_PERIOD:   usize = 20;
const BB_K:        f64   = 2.0;

/// Vetor de regime num candle específico.
#[derive(Clone, Copy, Debug)]
pub struct RegimeVector {
    pub adx14:      f64,    // 0-100
    pub atr_pct:    f64,    // 0..1 (e.g. 0.015 = 1.5%)
    pub rsi14:      f64,    // 0-100
    pub bb_squeeze: f64,    // 0..1 (e.g. 0.02 = 2% spread relativo)
}

/// Pré-computa os 4 arrays uma vez. Compartilha via Arc entre threads do sweep.
#[derive(Clone)]
pub struct RegimeArrays {
    pub adx14:      Arc<Vec<f64>>,
    pub atr_pct:    Arc<Vec<f64>>,
    pub rsi14:      Arc<Vec<f64>>,
    pub bb_squeeze: Arc<Vec<f64>>,
}

impl RegimeArrays {
    pub fn compute(candles: &[Candle]) -> Self {
        let adx = adx_wilder(candles, ADX_PERIOD);
        let atr = atr_wilder(candles, ATR_PERIOD);
        // ATR% = atr / close
        let atr_pct: Vec<f64> = atr.iter().zip(candles.iter())
            .map(|(a, c)| if c.close.abs() > 1e-12 { a / c.close } else { f64::NAN })
            .collect();
        let rsi = rsi_wilder(candles, RSI_PERIOD);
        let bb  = bb_squeeze(candles, BB_PERIOD, BB_K);

        Self {
            adx14:      Arc::new(adx),
            atr_pct:    Arc::new(atr_pct),
            rsi14:      Arc::new(rsi),
            bb_squeeze: Arc::new(bb),
        }
    }

    /// Retorna `None` se i < WARMUP ou qualquer feature é NaN/inf.
    pub fn at(&self, i: usize) -> Option<RegimeVector> {
        if i < WARMUP || i >= self.adx14.len() { return None; }
        let v = RegimeVector {
            adx14:      self.adx14[i],
            atr_pct:    self.atr_pct[i],
            rsi14:      self.rsi14[i],
            bb_squeeze: self.bb_squeeze[i],
        };
        if [v.adx14, v.atr_pct, v.rsi14, v.bb_squeeze]
            .iter()
            .any(|x| x.is_nan() || x.is_infinite())
        {
            return None;
        }
        Some(v)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{TimeZone, Utc};

    fn synth(close: f64, idx: i64) -> Candle {
        let t = Utc.with_ymd_and_hms(2025, 1, 1, 0, 0, 0).unwrap()
            + chrono::Duration::minutes(idx * 15);
        Candle {
            open_time: t, close_time: t + chrono::Duration::minutes(15),
            open: close * 0.999, high: close * 1.002, low: close * 0.998, close, volume: 1.0,
            day: (t.timestamp() / 86_400) as u32,
            minute_of_day: ((t.timestamp() % 86_400) / 60) as u16,
        }
    }

    #[test]
    fn pre_warmup_returns_none() {
        let candles: Vec<Candle> = (0..30).map(|i| synth(100.0, i)).collect();
        let arr = RegimeArrays::compute(&candles);
        assert!(arr.at(0).is_none());
        assert!(arr.at(29).is_none());
    }

    #[test]
    fn post_warmup_returns_some_in_active_market() {
        // 100 candles em uptrend leve com vol
        let mut c = 100.0;
        let candles: Vec<Candle> = (0..150).map(|i| {
            c *= 1.001;
            synth(c, i)
        }).collect();
        let arr = RegimeArrays::compute(&candles);
        let v = arr.at(100).expect("should be Some after warmup");
        assert!(v.adx14 >= 0.0 && v.adx14 <= 100.0);
        assert!(v.atr_pct > 0.0 && v.atr_pct < 0.5);    // < 50%
        assert!(v.rsi14 >= 0.0 && v.rsi14 <= 100.0);
        assert!(v.bb_squeeze >= 0.0);
    }

    #[test]
    fn no_lookahead_in_features() {
        // Compute features em prefixo deve dar mesmos valores que no array completo.
        let mut c = 100.0;
        let candles: Vec<Candle> = (0..200).map(|i| {
            c *= if i % 3 == 0 { 1.005 } else { 0.998 };
            synth(c, i)
        }).collect();
        let arr_short = RegimeArrays::compute(&candles[..101]);
        let arr_long  = RegimeArrays::compute(&candles);
        let v_s = arr_short.at(100).expect("warmup ok");
        let v_l = arr_long.at(100).expect("warmup ok");
        assert!((v_s.adx14 - v_l.adx14).abs() < 1e-9, "ADX look-ahead: short={}, long={}", v_s.adx14, v_l.adx14);
        assert!((v_s.atr_pct - v_l.atr_pct).abs() < 1e-9);
        assert!((v_s.rsi14 - v_l.rsi14).abs() < 1e-9);
        assert!((v_s.bb_squeeze - v_l.bb_squeeze).abs() < 1e-9);
    }
}
