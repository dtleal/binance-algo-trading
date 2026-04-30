//! M3 da skill /regime-conditional: regime match score.
//!
//! Dado o regime atual + fingerprint de uma strategy, retorna score em [0,1]
//! representando "quão favorável o regime atual é pra essa strategy".
//!
//! - Score 0 = regime previsto perdedor → desativa strategy
//! - Score 1 = regime ideal (igual ao melhor bucket histórico) → opera full
//!
//! Usado pelo composite (M4) pra rotear capital entre presets descorrelacionados.

use anyhow::{anyhow, Result};
use postgres::Client;
use std::collections::HashMap;

use crate::regime::RegimeVector;

const FEATURE_NAMES: [&str; 4] = ["adx14", "atr_pct", "rsi14", "bb_squeeze"];

/// Cap do peso por confiança (sqrt(n)). Acima disso satura.
const N_CAP: f64 = 200.0;

#[derive(Clone, Debug)]
pub struct BucketProfile {
    pub avg_pnl_pct: f64,
    pub n_trades:    usize,
    pub is_useful:   bool,
}

/// 4 features × 3 buckets = 12 perfis + quantiles + max_useful_pnl pra normalização.
#[derive(Clone, Debug)]
pub struct StrategyFingerprint {
    pub preset_id: i64,
    /// feature_name → [low, mid, high]
    pub buckets_by_feature: HashMap<&'static str, [BucketProfile; 3]>,
    /// feature_name → (q33, q67)
    pub feature_quantiles: HashMap<&'static str, (f64, f64)>,
    /// Maior avg_pnl positivo entre buckets is_useful=true. Score=1 quando regime
    /// match esse valor; score=0 quando avg ≤ 0.
    pub max_useful_pnl: f64,
}

/// Carrega fingerprint da DB. Retorna `Err` se preset não existe ou não tem nenhum
/// bucket útil (strategy regime-insensível).
pub fn load_fingerprint(client: &mut Client, preset_id: i64) -> Result<StrategyFingerprint> {
    // 1. Buckets
    let rows = client.query(
        "SELECT feature_name, bucket, avg_pnl_pct, n_trades, is_useful
         FROM strategy_regime_profile WHERE preset_id = $1",
        &[&preset_id],
    )?;
    if rows.is_empty() {
        return Err(anyhow!("preset {preset_id} has no regime fingerprint — run `backtest fingerprint` first"));
    }

    let mut buckets_by_feature: HashMap<&'static str, [BucketProfile; 3]> = HashMap::new();
    for f in &FEATURE_NAMES {
        buckets_by_feature.insert(f, [
            BucketProfile { avg_pnl_pct: 0.0, n_trades: 0, is_useful: false },
            BucketProfile { avg_pnl_pct: 0.0, n_trades: 0, is_useful: false },
            BucketProfile { avg_pnl_pct: 0.0, n_trades: 0, is_useful: false },
        ]);
    }

    let mut max_useful = 0.0_f64;
    for r in rows {
        let feat: String = r.get(0);
        let bucket: String = r.get(1);
        let avg_pnl: Option<rust_decimal::Decimal> = r.get(2);
        let n_trades: i32 = r.get(3);
        let is_useful: bool = r.get(4);

        let feat_static = FEATURE_NAMES.iter()
            .find(|f| **f == feat.as_str())
            .copied()
            .ok_or_else(|| anyhow!("unknown feature_name '{feat}' in DB"))?;
        let bucket_idx = match bucket.as_str() {
            "low" => 0, "mid" => 1, "high" => 2,
            _ => return Err(anyhow!("unknown bucket '{bucket}'")),
        };

        let avg_f = avg_pnl.and_then(|d| d.try_into().ok()).unwrap_or(0.0_f64);
        let arr = buckets_by_feature.get_mut(feat_static).unwrap();
        arr[bucket_idx] = BucketProfile {
            avg_pnl_pct: avg_f,
            n_trades: n_trades as usize,
            is_useful,
        };
        if is_useful && avg_f > max_useful {
            max_useful = avg_f;
        }
    }

    if max_useful <= 0.0 {
        return Err(anyhow!(
            "preset {preset_id}: no useful buckets with positive avg_pnl — strategy not regime-detectable"
        ));
    }

    // 2. Quantiles populacionais — load via JOIN com presets pra pegar (symbol, tf, period)
    let row = client.query_one(
        "SELECT p.symbol, p.timeframe, sr.period_start, sr.period_end
         FROM presets p JOIN sweep_results sr ON p.sweep_result_id = sr.id
         WHERE p.id = $1",
        &[&preset_id],
    )?;
    let symbol: String = row.get(0);
    let timeframe: String = row.get(1);
    let period_start: chrono::NaiveDate = row.get(2);
    let period_end: chrono::NaiveDate = row.get(3);

    let q_rows = client.query(
        "SELECT feature_name, q33, q67 FROM regime_feature_quantiles
         WHERE symbol=$1 AND timeframe=$2 AND period_start=$3 AND period_end=$4",
        &[&symbol, &timeframe, &period_start, &period_end],
    )?;
    let mut feature_quantiles: HashMap<&'static str, (f64, f64)> = HashMap::new();
    for r in q_rows {
        let feat: String = r.get(0);
        let feat_static = FEATURE_NAMES.iter()
            .find(|f| **f == feat.as_str())
            .copied()
            .ok_or_else(|| anyhow!("unknown feature_name '{feat}' in quantiles"))?;
        let q33: f64 = r.get(1);
        let q67: f64 = r.get(2);
        feature_quantiles.insert(feat_static, (q33, q67));
    }
    if feature_quantiles.len() != 4 {
        return Err(anyhow!("missing quantiles for some features (got {}, expected 4)", feature_quantiles.len()));
    }

    Ok(StrategyFingerprint {
        preset_id,
        buckets_by_feature,
        feature_quantiles,
        max_useful_pnl: max_useful,
    })
}

/// Score de match em [0, 1].
/// - 0: regime previsto perdedor (ou nenhuma feature útil)
/// - 1: regime ideal (avg_expectancy = max_useful_pnl)
pub fn regime_match_score(regime: &RegimeVector, fp: &StrategyFingerprint) -> f64 {
    if fp.max_useful_pnl <= 0.0 { return 0.0; }

    let mut weighted_expectancy = 0.0;
    let mut total_weight = 0.0;

    for &feat in &FEATURE_NAMES {
        let value = match feat {
            "adx14"      => regime.adx14,
            "atr_pct"    => regime.atr_pct,
            "rsi14"      => regime.rsi14,
            "bb_squeeze" => regime.bb_squeeze,
            _ => unreachable!(),
        };
        let (q33, q67) = match fp.feature_quantiles.get(feat) {
            Some(q) => *q,
            None => continue,    // missing quantile → skip feature
        };
        let bucket_idx = if value < q33 { 0 } else if value < q67 { 1 } else { 2 };
        let bucket = &fp.buckets_by_feature[feat][bucket_idx];

        if !bucket.is_useful { continue; }    // M2 filter

        // Confidence weight: sqrt(n), capado em sqrt(N_CAP)
        let weight = (bucket.n_trades as f64).sqrt().min(N_CAP.sqrt());
        weighted_expectancy += bucket.avg_pnl_pct * weight;
        total_weight += weight;
    }

    if total_weight == 0.0 { return 0.0; }    // nenhuma feature útil neste regime
    let avg_expectancy = weighted_expectancy / total_weight;
    if avg_expectancy <= 0.0 { return 0.0; }

    (avg_expectancy / fp.max_useful_pnl).min(1.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn synth_fingerprint(useful_pattern: &[(usize, f64, usize)]) -> StrategyFingerprint {
        // useful_pattern: [(bucket_flat_idx 0..12, avg_pnl, n_trades)]
        // Defaults: avg=0, n=0, is_useful=false
        let mut buckets_by_feature: HashMap<&'static str, [BucketProfile; 3]> = HashMap::new();
        for f in &FEATURE_NAMES {
            buckets_by_feature.insert(f, [
                BucketProfile { avg_pnl_pct: 0.0, n_trades: 0, is_useful: false },
                BucketProfile { avg_pnl_pct: 0.0, n_trades: 0, is_useful: false },
                BucketProfile { avg_pnl_pct: 0.0, n_trades: 0, is_useful: false },
            ]);
        }
        let mut max_useful = 0.0_f64;
        for &(idx, avg, n) in useful_pattern {
            let feat = FEATURE_NAMES[idx / 3];
            let bk = idx % 3;
            buckets_by_feature.get_mut(feat).unwrap()[bk] =
                BucketProfile { avg_pnl_pct: avg, n_trades: n, is_useful: avg > 0.0 };
            if avg > max_useful { max_useful = avg; }
        }
        let mut feature_quantiles = HashMap::new();
        for &f in &FEATURE_NAMES {
            // q33=33, q67=67 (regime values 0-100 fit naturally)
            feature_quantiles.insert(f, (33.0, 67.0));
        }
        StrategyFingerprint { preset_id: 0, buckets_by_feature, feature_quantiles, max_useful_pnl: max_useful }
    }

    #[test]
    fn perfect_match_score_high() {
        // High bucket de TODAS as 4 features tem avg_pnl=+0.5%, n=200 (todas useful)
        // Regime atual: cai em high de todas (valores >67 nas 4 features)
        let pattern: Vec<_> = (0..4).map(|f| (f * 3 + 2, 0.5, 200)).collect();
        let fp = synth_fingerprint(&pattern);
        let regime = RegimeVector { adx14: 80.0, atr_pct: 80.0, rsi14: 80.0, bb_squeeze: 80.0 };
        let s = regime_match_score(&regime, &fp);
        assert!(s > 0.9, "perfect match should give ~1, got {}", s);
    }

    #[test]
    fn anti_match_score_zero() {
        // Mesmo fingerprint (high tem avg_pnl=+0.5, useful)
        // Regime atual cai em LOW de todas (low não é useful → score=0)
        let pattern: Vec<_> = (0..4).map(|f| (f * 3 + 2, 0.5, 200)).collect();
        let fp = synth_fingerprint(&pattern);
        let regime = RegimeVector { adx14: 5.0, atr_pct: 5.0, rsi14: 5.0, bb_squeeze: 5.0 };
        let s = regime_match_score(&regime, &fp);
        assert!(s < 0.1, "anti-match should give ~0, got {}", s);
    }

    #[test]
    fn confidence_weighting_dominates_low_n() {
        // Bucket A (low de adx14): n=10, avg=+1% (parece ótimo, amostra pequena)
        // Bucket B (high de atr_pct): n=300, avg=+0.1% (consistente)
        // Regime: low em adx14 (cai em A), high em atr_pct (cai em B)
        // Esperado: dominado por B
        let fp = synth_fingerprint(&[(0, 1.0, 10), (5, 0.1, 300)]);
        let regime = RegimeVector { adx14: 10.0, atr_pct: 80.0, rsi14: 50.0, bb_squeeze: 50.0 };
        let s = regime_match_score(&regime, &fp);
        // weighted: A weight=sqrt(10)=3.16, B weight=sqrt(200)=14.14 (capado)
        // weighted_avg = (3.16 * 1.0 + 14.14 * 0.1) / 17.30 = (3.16 + 1.414) / 17.30 = 0.264
        // max_useful = 1.0 → score = 0.264
        assert!(s > 0.2 && s < 0.4, "weighted score should be ~0.26, got {}", s);
    }

    #[test]
    fn ignores_non_useful_buckets() {
        // adx14 high: avg=+0.5%, useful=true (200 trades)
        // adx14 low: avg=+5% mas useful=false (would be na DB com persistence falhando)
        // Regime atual cai em low de adx14
        let mut fp = synth_fingerprint(&[(2, 0.5, 200)]);    // só high de adx14 useful
        // Manualmente põe um bucket "tentador" mas não-útil
        fp.buckets_by_feature.get_mut("adx14").unwrap()[0] = BucketProfile {
            avg_pnl_pct: 5.0, n_trades: 100, is_useful: false,
        };
        let regime = RegimeVector { adx14: 10.0, atr_pct: 50.0, rsi14: 50.0, bb_squeeze: 50.0 };
        let s = regime_match_score(&regime, &fp);
        assert!(s < 0.1, "non-useful bucket should be ignored, got {}", s);
    }

    #[test]
    fn empty_useful_buckets_returns_zero() {
        // Nenhum bucket útil
        let fp = synth_fingerprint(&[]);
        // max_useful_pnl = 0 → score = 0
        let regime = RegimeVector { adx14: 50.0, atr_pct: 50.0, rsi14: 50.0, bb_squeeze: 50.0 };
        let s = regime_match_score(&regime, &fp);
        assert_eq!(s, 0.0);
    }
}
