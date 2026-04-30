//! M2 da skill /regime-conditional: per-strategy regime fingerprint.
//!
//! Replay um preset, captura regime no entry de cada trade, agrega por
//! (feature × bucket). Valida persistência fp_a/fp_b e bootstrap-Bonferroni.

use anyhow::{anyhow, Result};
use chrono::NaiveDate;
use postgres::Client;
use rand::{Rng, SeedableRng};
use rand::rngs::StdRng;

use crate::db;
use crate::detail::{self, DetailParams, Trade};
use crate::params_row::{
    strategy_params_from_row, exit_params_from_row,
    SELECT_STRATEGY_PARAMS_COLS, SELECT_EXIT_PARAMS_COLS,
};
use crate::regime::RegimeArrays;
use crate::types::*;

/// 4 features que perturbamos no fingerprint (devem casar com `RegimeVector`).
const FEATURE_NAMES: [&str; 4] = ["adx14", "atr_pct", "rsi14", "bb_squeeze"];

/// Bonferroni: 4 features × 3 buckets = 12 hipóteses; α/12 = 0.05/12 ≈ 0.0042.
/// Bootstrap percentil: usar IC `1 - α/12` = 99.58%.
const BONFERRONI_ALPHA: f64 = 0.05 / 12.0;
const BOOTSTRAP_RESAMPLES: usize = 1000;

/// Mínimo trades por bucket pra confiança estatística.
const MIN_BUCKET_TRADES: usize = 30;
const MIN_PERSISTENCE_HALF: usize = 15;

#[derive(Debug, Clone)]
pub struct BucketResult {
    pub feature: &'static str,
    pub bucket: &'static str,    // "low" | "mid" | "high"
    pub n_trades: usize,
    pub n_wins: usize,
    pub avg_pnl: f64,
    pub sum_pnl: f64,
    pub fp_a_n: usize,
    pub fp_a_avg: f64,
    pub fp_b_n: usize,
    pub fp_b_avg: f64,
    pub persistence_ok: bool,
    pub ic_lo: f64,
    pub ic_hi: f64,
    pub bonferroni_ok: bool,
    pub is_useful: bool,
}

#[derive(Debug, Clone)]
pub struct FingerprintOutput {
    pub preset_id: i64,
    pub label: String,
    pub buckets: Vec<BucketResult>,    // 12 rows: 4 features × 3 buckets
    pub useful_count: usize,
    pub quantiles: std::collections::HashMap<&'static str, (f64, f64)>,    // (q33, q67) por feature
}

pub fn run_fingerprint(client: &mut Client, preset_id: i64, pos_size: f64) -> Result<FingerprintOutput> {
    // 1. Load preset
    let sql = format!(
        "SELECT symbol, timeframe, strategy, exit_name, sweep_result_id, \
         {SELECT_STRATEGY_PARAMS_COLS}, {SELECT_EXIT_PARAMS_COLS} \
         FROM presets WHERE id = $1 AND status = 'active'"
    );
    let row = client.query_opt(sql.as_str(), &[&preset_id])?
        .ok_or_else(|| anyhow!("preset {preset_id} not found or not active"))?;

    let symbol_s: String = row.get("symbol");
    let tf_s: String = row.get("timeframe");
    let strategy: String = row.get("strategy");
    let exit_name: Option<String> = row.get("exit_name");
    let exit_name = exit_name.ok_or_else(|| anyhow!("preset has NULL exit_name"))?;
    let sweep_result_id: Option<i64> = row.get("sweep_result_id");
    let sweep_result_id = sweep_result_id.ok_or_else(|| anyhow!("preset has NULL sweep_result_id"))?;

    let symbol = Symbol::new(&symbol_s);
    let timeframe = Timeframe::parse(&tf_s).ok_or_else(|| anyhow!("bad tf '{tf_s}'"))?;
    let sp = strategy_params_from_row(&row);
    let ep = exit_params_from_row(&row);
    let label = format!("{strategy} {symbol_s} {tf_s} (id={preset_id})");

    // 2. Período: pega do sweep_result associado
    let row2 = client.query_one(
        "SELECT period_start, period_end FROM sweep_results WHERE id = $1",
        &[&sweep_result_id],
    )?;
    let period_start: NaiveDate = row2.get(0);
    let period_end: NaiveDate = row2.get(1);
    let from = period_start.and_hms_opt(0, 0, 0).unwrap().and_utc();
    let until = period_end.and_hms_opt(23, 59, 59).unwrap().and_utc();

    // 3. Load candles
    let candles = db::load_candles(client, &symbol, timeframe, Some(from), Some(until))?;
    if candles.is_empty() {
        return Err(anyhow!("no candles for {symbol_s} {tf_s} {period_start}..{period_end}"));
    }

    // 4. Compute regime arrays (M1)
    let regime = RegimeArrays::compute(&candles);

    // 5. Compute or load quantiles populacionais (Q33, Q67) por feature
    let quantiles = compute_or_load_quantiles(
        client, &symbol_s, &tf_s, &period_start, &period_end, &regime,
    )?;

    // 6. Replay strategy → trades
    let trades = replay(&candles, &strategy, &exit_name, &sp, &ep, pos_size)?;
    if trades.len() < 100 {
        return Err(anyhow!("preset {preset_id} has only {} trades — need ≥100 for fingerprint", trades.len()));
    }

    // 7. Para cada trade, captura regime no entry. Calcula índice do candle de entry.
    let entry_indices: Vec<usize> = trades.iter()
        .filter_map(|t| {
            // Linear search por entry_time (pequeno overhead — trades << candles)
            candles.iter().position(|c| c.open_time == t.entry_time)
        })
        .collect();
    if entry_indices.len() != trades.len() {
        return Err(anyhow!("could not match {} entry times to candles", trades.len() - entry_indices.len()));
    }

    // 8. Para cada (feature × bucket), agrega trades
    let half_idx = trades.len() / 2;
    let mut buckets_out: Vec<BucketResult> = Vec::with_capacity(12);

    for &feature in &FEATURE_NAMES {
        let (q33, q67) = quantiles[feature];

        // Bucketize cada trade pra essa feature
        let mut by_bucket: [Vec<(usize, f64)>; 3] = Default::default();    // (trade_idx, pnl_pct%)
        for (ti, (t, &ci)) in trades.iter().zip(entry_indices.iter()).enumerate() {
            let value = match feature {
                "adx14" => regime.adx14[ci],
                "atr_pct" => regime.atr_pct[ci],
                "rsi14" => regime.rsi14[ci],
                "bb_squeeze" => regime.bb_squeeze[ci],
                _ => unreachable!(),
            };
            if value.is_nan() { continue; }
            let bucket_idx = if value < q33 { 0 } else if value < q67 { 1 } else { 2 };
            by_bucket[bucket_idx].push((ti, t.pnl_pct));    // pnl_pct já em % (do detail.rs)
        }

        for (bidx, bname) in ["low", "mid", "high"].iter().enumerate() {
            let trades_in = &by_bucket[bidx];
            let n = trades_in.len();
            if n == 0 {
                buckets_out.push(empty_bucket(feature, bname));
                continue;
            }

            let pnls: Vec<f64> = trades_in.iter().map(|(_, p)| *p).collect();
            let n_wins = pnls.iter().filter(|p| **p > 0.0).count();
            let sum_pnl: f64 = pnls.iter().sum();
            let avg_pnl = sum_pnl / n as f64;

            // FP-A / FP-B persistência (split por trade_idx)
            let fp_a: Vec<f64> = trades_in.iter().filter(|(ti, _)| *ti < half_idx).map(|(_, p)| *p).collect();
            let fp_b: Vec<f64> = trades_in.iter().filter(|(ti, _)| *ti >= half_idx).map(|(_, p)| *p).collect();
            let fp_a_n = fp_a.len();
            let fp_b_n = fp_b.len();
            let fp_a_avg = if fp_a_n > 0 { fp_a.iter().sum::<f64>() / fp_a_n as f64 } else { 0.0 };
            let fp_b_avg = if fp_b_n > 0 { fp_b.iter().sum::<f64>() / fp_b_n as f64 } else { 0.0 };
            let persistence_ok = fp_a_n >= MIN_PERSISTENCE_HALF
                && fp_b_n >= MIN_PERSISTENCE_HALF
                && (fp_a_avg.is_sign_positive() == fp_b_avg.is_sign_positive())
                && fp_b_avg.abs() >= 0.5 * fp_a_avg.abs();

            // Bootstrap IC (1 - α/12) pra avg_pnl
            let (ic_lo, ic_hi) = bootstrap_ic(&pnls, BOOTSTRAP_RESAMPLES, BONFERRONI_ALPHA);
            let bonferroni_ok = ic_lo > 0.0 || ic_hi < 0.0;

            let is_useful = persistence_ok && bonferroni_ok && n >= MIN_BUCKET_TRADES;

            buckets_out.push(BucketResult {
                feature, bucket: bname,
                n_trades: n, n_wins,
                avg_pnl, sum_pnl,
                fp_a_n, fp_a_avg, fp_b_n, fp_b_avg, persistence_ok,
                ic_lo, ic_hi, bonferroni_ok, is_useful,
            });
        }
    }

    let useful_count = buckets_out.iter().filter(|b| b.is_useful).count();

    Ok(FingerprintOutput {
        preset_id, label, buckets: buckets_out, useful_count,
        quantiles,
    })
}

fn empty_bucket(feature: &'static str, bucket: &'static str) -> BucketResult {
    BucketResult {
        feature, bucket,
        n_trades: 0, n_wins: 0,
        avg_pnl: 0.0, sum_pnl: 0.0,
        fp_a_n: 0, fp_a_avg: 0.0, fp_b_n: 0, fp_b_avg: 0.0, persistence_ok: false,
        ic_lo: 0.0, ic_hi: 0.0, bonferroni_ok: false,
        is_useful: false,
    }
}

/// Bootstrap percentil bilateral.
fn bootstrap_ic(values: &[f64], n_resamples: usize, alpha: f64) -> (f64, f64) {
    if values.is_empty() { return (0.0, 0.0); }
    let n = values.len();
    let mut means = Vec::with_capacity(n_resamples);
    let mut rng = StdRng::seed_from_u64(42);    // reprodutível
    for _ in 0..n_resamples {
        let mut sum = 0.0;
        for _ in 0..n {
            sum += values[rng.gen_range(0..n)];
        }
        means.push(sum / n as f64);
    }
    means.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let lo_idx = ((alpha / 2.0) * n_resamples as f64) as usize;
    let hi_idx = ((1.0 - alpha / 2.0) * n_resamples as f64) as usize;
    let hi_idx = hi_idx.min(n_resamples - 1);
    (means[lo_idx], means[hi_idx])
}

fn compute_or_load_quantiles(
    client: &mut Client,
    symbol_s: &str,
    tf_s: &str,
    period_start: &NaiveDate,
    period_end: &NaiveDate,
    regime: &RegimeArrays,
) -> Result<std::collections::HashMap<&'static str, (f64, f64)>> {
    use std::collections::HashMap;
    let mut out: HashMap<&'static str, (f64, f64)> = HashMap::new();

    for &feature in &FEATURE_NAMES {
        // Tenta load do cache
        let row = client.query_opt(
            "SELECT q33, q67 FROM regime_feature_quantiles
             WHERE symbol=$1 AND timeframe=$2 AND feature_name=$3
               AND period_start=$4 AND period_end=$5",
            &[&symbol_s, &tf_s, &feature, period_start, period_end],
        )?;
        if let Some(r) = row {
            out.insert(feature, (r.get(0), r.get(1)));
            continue;
        }
        // Compute
        let arr: &[f64] = match feature {
            "adx14" => &regime.adx14,
            "atr_pct" => &regime.atr_pct,
            "rsi14" => &regime.rsi14,
            "bb_squeeze" => &regime.bb_squeeze,
            _ => unreachable!(),
        };
        let mut clean: Vec<f64> = arr.iter().filter(|x| !x.is_nan()).copied().collect();
        if clean.len() < 100 {
            return Err(anyhow!("feature {feature}: only {} non-NaN observations", clean.len()));
        }
        clean.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let q33 = clean[clean.len() / 3];
        let q67 = clean[2 * clean.len() / 3];

        // Persist
        client.execute(
            "INSERT INTO regime_feature_quantiles
             (symbol, timeframe, feature_name, period_start, period_end, q33, q67, n_observations)
             VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
             ON CONFLICT (symbol, timeframe, feature_name, period_start, period_end) DO UPDATE
             SET q33=$6, q67=$7, n_observations=$8, computed_at=NOW()",
            &[&symbol_s, &tf_s, &feature, period_start, period_end, &q33, &q67, &(clean.len() as i32)],
        )?;
        out.insert(feature, (q33, q67));
    }
    Ok(out)
}

/// Replay strategy com params do preset (similar ao correlate, mas isolado pra este módulo).
fn replay(
    candles: &[Candle],
    strategy: &str,
    exit_name: &str,
    sp: &StrategyParamsRow,
    ep: &ExitParamsRow,
    pos_size: f64,
) -> Result<Vec<Trade>> {
    let days = db::group_by_day(candles);

    let entry_window = match (sp.entry_window_start, sp.entry_window_end) {
        (Some(s), Some(e)) => Some((s as u16, e as u16)),
        _ => None,
    };
    let dp = DetailParams {
        min_bars:           sp.min_bars.map(|x| x as usize),
        confirm_bars:       sp.confirm_bars.map(|x| x as usize),
        vwap_prox:          sp.vwap_prox,
        vwap_window:        sp.vwap_window_days.map(|x| x as u32),
        ema_period:         sp.ema_period.map(|x| x as usize),
        max_trades_per_day: sp.max_trades_per_day.map(|x| x as usize),
        fast_period:        sp.fast_period.map(|x| x as usize),
        slow_period:        sp.slow_period.map(|x| x as usize),
        range_mins:         sp.range_mins.map(|x| x as u16),
        buffer_pct:         sp.buffer_pct,
        prox_pct:           sp.prox_pct,
        momentum_kind:      sp.kind.clone(),
        vol_filter:         sp.vol_filter,
        trend_filter:       sp.trend_filter,
        entry_window,
    };

    let entries = detail::build_entries(strategy, candles, &days, &dp)?;
    let exit_fn = build_exit_fn(exit_name, ep)?;
    let result = detail::run_detail(&entries, candles, exit_fn, pos_size);
    Ok(result.trades)
}

fn build_exit_fn(exit_name: &str, ep: &ExitParamsRow) -> Result<ExitFn> {
    match exit_name {
        "fixed_tp_sl" => Ok(ExitFn::FixedTpSl {
            tp_pct: ep.tp_pct.ok_or_else(|| anyhow!("fixed_tp_sl needs tp_pct"))?,
            sl_pct: ep.sl_pct.ok_or_else(|| anyhow!("fixed_tp_sl needs sl_pct"))?,
            max_hold_min: ep.max_hold_min.unwrap_or(0) as u16,
        }),
        "trailing_stop" => Ok(ExitFn::Trailing {
            sl_pct: ep.sl_pct.ok_or_else(|| anyhow!("trailing_stop needs sl_pct"))?,
            be_r: ep.be_r.unwrap_or(1.0),
            trail_step: ep.trail_step.unwrap_or(0.25),
            tp_r: ep.tp_r.unwrap_or(0.0),
            max_hold_min: ep.max_hold_min.unwrap_or(0) as u16,
        }),
        other => Err(anyhow!("exit_name '{other}' not supported (range monolithic out of scope for fingerprint)")),
    }
}

/// Persiste resultado em strategy_regime_profile (UPSERT).
pub fn persist_fingerprint(client: &mut Client, out: &FingerprintOutput) -> Result<()> {
    use rust_decimal::prelude::FromPrimitive;
    use rust_decimal::Decimal;
    let to_dec = |v: f64| -> Option<Decimal> {
        if v.is_finite() { Decimal::from_f64(v) } else { None }
    };

    let mut tx = client.transaction()?;
    for b in &out.buckets {
        let win_rate: Option<Decimal> = if b.n_trades > 0 {
            to_dec(100.0 * b.n_wins as f64 / b.n_trades as f64)
        } else { None };
        tx.execute(
            "INSERT INTO strategy_regime_profile
             (preset_id, feature_name, bucket,
              n_trades, n_wins, win_rate, avg_pnl_pct, sum_pnl_pct,
              fp_a_n_trades, fp_a_avg_pnl, fp_b_n_trades, fp_b_avg_pnl, persistence_ok,
              bootstrap_ic_lo, bootstrap_ic_hi, bonferroni_ok, is_useful)
             VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
             ON CONFLICT (preset_id, feature_name, bucket) DO UPDATE
             SET n_trades=$4, n_wins=$5, win_rate=$6, avg_pnl_pct=$7, sum_pnl_pct=$8,
                 fp_a_n_trades=$9, fp_a_avg_pnl=$10, fp_b_n_trades=$11, fp_b_avg_pnl=$12,
                 persistence_ok=$13, bootstrap_ic_lo=$14, bootstrap_ic_hi=$15,
                 bonferroni_ok=$16, is_useful=$17, computed_at=NOW()",
            &[
                &out.preset_id, &b.feature, &b.bucket,
                &(b.n_trades as i32), &(b.n_wins as i32), &win_rate,
                &to_dec(b.avg_pnl), &to_dec(b.sum_pnl),
                &(b.fp_a_n as i32), &to_dec(b.fp_a_avg),
                &(b.fp_b_n as i32), &to_dec(b.fp_b_avg),
                &b.persistence_ok,
                &to_dec(b.ic_lo), &to_dec(b.ic_hi), &b.bonferroni_ok,
                &b.is_useful,
            ],
        )?;
    }
    tx.commit()?;
    Ok(())
}
