//! M4 da skill /regime-conditional: composite backtest engine.
//!
//! V1: cada preset opera independente sobre uma fração igual de capital
//! (1/N), com regime score gerando "entry mask" — entradas só ocorrem
//! em candles cujo regime favorece a strategy. Pos_size escalado pelo
//! score (full ou meia se score>=full_threshold ou >=activate_threshold).
//!
//! Não há capital sharing real entre presets em V1 — cada um tem sua
//! conta isolada. Composite return = média dos returns. Conservador
//! mas direto.

use anyhow::{anyhow, Result};
use chrono::{DateTime, NaiveDate, Utc};
use postgres::Client;

use crate::db;
use crate::detail::{self, DetailParams, Trade};
use crate::params_row::{
    strategy_params_from_row, exit_params_from_row,
    SELECT_STRATEGY_PARAMS_COLS, SELECT_EXIT_PARAMS_COLS,
};
use crate::regime::{RegimeArrays, StrategyFingerprint, regime_match_score, load_fingerprint};
use crate::types::*;

#[derive(Debug, Clone)]
pub struct CompositeConfig {
    pub activate_threshold:    f64,    // 0.40
    pub full_position_threshold: f64,  // 0.65
    pub kill_switch_dd:        f64,    // 0.05 (5%)
}

impl Default for CompositeConfig {
    fn default() -> Self {
        Self {
            activate_threshold: 0.40,
            full_position_threshold: 0.65,
            kill_switch_dd: 0.05,
        }
    }
}

#[derive(Debug)]
pub struct CompositeResult {
    pub preset_results: Vec<PresetResult>,
    pub aggregated: AggregatedMetrics,
}

#[derive(Debug, Clone)]
pub struct PresetResult {
    pub preset_id: i64,
    pub label: String,
    pub trades: Vec<Trade>,
    pub return_pct: f64,
    pub max_dd_pct: f64,
    pub n_trades_total: usize,
    pub n_trades_active: usize,    // após regime mask
    pub n_candles_active: usize,   // candles onde score >= activate
    pub n_candles_full: usize,     // candles onde score >= full
    pub avg_score: f64,
}

#[derive(Debug)]
pub struct AggregatedMetrics {
    pub return_pct_mean: f64,    // média entre presets
    pub return_pct_sum:  f64,    // soma (cap deployment 1/N por preset)
    pub max_dd_pct_max:  f64,    // pior DD entre presets
    pub n_trades_total:  usize,
}

/// Roda composite backtest sobre N presets com regime gating.
/// Cada preset usa sua pos_size original × mult derivado do score.
pub fn run_composite(
    client: &mut Client,
    preset_ids: &[i64],
    cfg: &CompositeConfig,
    period_start: Option<NaiveDate>,
    period_end: Option<NaiveDate>,
) -> Result<CompositeResult> {
    if preset_ids.is_empty() {
        return Err(anyhow!("preset_ids is empty"));
    }

    let mut preset_results = Vec::with_capacity(preset_ids.len());
    let mut sum_return = 0.0;
    let mut max_dd = 0.0_f64;
    let mut total_trades = 0;

    for &pid in preset_ids {
        let pr = run_one_preset(client, pid, cfg, period_start, period_end)?;
        sum_return += pr.return_pct;
        max_dd = max_dd.max(pr.max_dd_pct);
        total_trades += pr.trades.len();
        preset_results.push(pr);
    }

    let n = preset_results.len() as f64;
    Ok(CompositeResult {
        aggregated: AggregatedMetrics {
            return_pct_mean: sum_return / n,
            return_pct_sum:  sum_return,
            max_dd_pct_max:  max_dd,
            n_trades_total:  total_trades,
        },
        preset_results,
    })
}

fn run_one_preset(
    client: &mut Client,
    preset_id: i64,
    cfg: &CompositeConfig,
    period_start_override: Option<NaiveDate>,
    period_end_override: Option<NaiveDate>,
) -> Result<PresetResult> {
    // 1. Load preset + fingerprint
    let strat_cols = qualified(SELECT_STRATEGY_PARAMS_COLS, "p");
    let exit_cols  = qualified(SELECT_EXIT_PARAMS_COLS, "p");
    let sql = format!(
        "SELECT p.symbol, p.timeframe, p.strategy, p.exit_name, sr.period_start, sr.period_end, \
         {strat_cols}, {exit_cols} \
         FROM presets p JOIN sweep_results sr ON p.sweep_result_id = sr.id \
         WHERE p.id = $1 AND p.status = 'active'"
    );
    let row = client.query_opt(sql.as_str(), &[&preset_id])?
        .ok_or_else(|| anyhow!("preset {preset_id} not found or not active"))?;

    let symbol_s: String = row.get("symbol");
    let tf_s: String = row.get("timeframe");
    let strategy: String = row.get("strategy");
    let exit_name: Option<String> = row.get("exit_name");
    let exit_name = exit_name.ok_or_else(|| anyhow!("preset has NULL exit_name"))?;
    let period_start_db: NaiveDate = row.get("period_start");
    let period_end_db: NaiveDate = row.get("period_end");

    let period_start = period_start_override.unwrap_or(period_start_db);
    let period_end = period_end_override.unwrap_or(period_end_db);

    let symbol = Symbol::new(&symbol_s);
    let timeframe = Timeframe::parse(&tf_s).ok_or_else(|| anyhow!("bad tf '{tf_s}'"))?;
    let sp = strategy_params_from_row(&row);
    let ep = exit_params_from_row(&row);
    let label = format!("{strategy} {symbol_s} {tf_s} (id={preset_id})");

    let fingerprint: StrategyFingerprint = load_fingerprint(client, preset_id)?;

    // 2. Load candles
    let from = period_start.and_hms_opt(0, 0, 0).unwrap().and_utc();
    let until = period_end.and_hms_opt(23, 59, 59).unwrap().and_utc();
    let candles = db::load_candles(client, &symbol, timeframe, Some(from), Some(until))?;
    if candles.is_empty() {
        return Err(anyhow!("no candles for {symbol_s} {tf_s} in {period_start}..{period_end}"));
    }

    // 3. Compute regime arrays + entry mask
    let regime = RegimeArrays::compute(&candles);
    let n = candles.len();
    let mut entry_mask: Vec<f64> = vec![0.0; n];    // mult: 0.0, 0.5, 1.0
    let mut sum_score = 0.0;
    let mut n_active = 0_usize;
    let mut n_full = 0_usize;

    for i in 0..n {
        if let Some(r) = regime.at(i) {
            let s = regime_match_score(&r, &fingerprint);
            sum_score += s;
            if s >= cfg.full_position_threshold {
                entry_mask[i] = 1.0;
                n_full += 1;
                n_active += 1;
            } else if s >= cfg.activate_threshold {
                entry_mask[i] = 0.5;
                n_active += 1;
            }
        }
    }

    // 4. Build entries (sem regime mask) + exit fn
    let pos_size_base = sp.pos_size.unwrap_or(0.10);
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

    let days = db::group_by_day(&candles);
    let entries = detail::build_entries(&strategy, &candles, &days, &dp)?;
    let exit_fn = build_exit_fn(&exit_name, &ep)?;

    // 5. Filtra entries pelo entry_mask + executa com pos_size escalado
    let n_total_entries = entries.len();
    let trades = run_with_mask_and_killswitch(&entries, &candles, exit_fn, pos_size_base, &entry_mask, cfg);

    // 6. Métricas
    let return_pct = if let Some(t) = trades.last() {
        (t.capital_after - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100.0
    } else { 0.0 };
    let max_dd_pct = trades.iter().map(|t| t.drawdown_pct).fold(0.0_f64, f64::max);

    let avg_score = if n > 0 { sum_score / n as f64 } else { 0.0 };

    Ok(PresetResult {
        preset_id, label,
        n_trades_total: n_total_entries,
        n_trades_active: trades.len(),
        n_candles_active: n_active,
        n_candles_full: n_full,
        avg_score,
        return_pct, max_dd_pct,
        trades,
    })
}

/// Helper SQL pra prefixar colunas na query JOIN com tabela alias.
fn qualified(cols: &str, alias: &str) -> String {
    cols.split(',')
        .map(|c| format!("{alias}.{}", c.trim()))
        .collect::<Vec<_>>()
        .join(", ")
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
        other => Err(anyhow!("exit_name '{other}' not supported in composite (range monolithic out of scope)")),
    }
}

/// Roda entries com mask aplicado (entry_mask=mult em [0, 0.5, 1.0]) + kill switch state machine.
/// Entry com mult=0 é skipped. Existing positions são gerenciadas até saída normal mesmo se mask cai.
fn run_with_mask_and_killswitch(
    entries: &[Entry],
    candles: &[Candle],
    f: ExitFn,
    pos_size_base: f64,
    entry_mask: &[f64],
    cfg: &CompositeConfig,
) -> Vec<Trade> {
    let mut capital = INITIAL_CAPITAL;
    let mut peak    = INITIAL_CAPITAL;
    let mut trades  = Vec::new();
    let mut killed  = false;
    let mut n_seq   = 0_usize;

    for e in entries.iter() {
        // Entry candle idx = rest_start - 1 (do find_entries, e.rest_start = i+1)
        let entry_candle_idx = e.rest_start.saturating_sub(1).min(candles.len() - 1);
        let mult = entry_mask.get(entry_candle_idx).copied().unwrap_or(0.0);

        if mult == 0.0 { continue; }    // regime score não favorece

        // Kill switch state machine: pausa novas entradas quando DD >= limit
        let cur_dd = if peak > 0.0 { (peak - capital) / peak } else { 0.0 };
        if !killed && cur_dd >= cfg.kill_switch_dd { killed = true; }
        if killed && cur_dd <= cfg.kill_switch_dd * 0.5 { killed = false; }
        if killed { continue; }

        // Executa entry com pos_size escalado
        let pos_size = pos_size_base * mult;
        let result = crate::exit::evaluate(e, candles, &f);
        let pnl_pct_raw = match e.direction {
            Direction::Short => (e.entry_price - result.exit_price) / e.entry_price,
            Direction::Long  => (result.exit_price - e.entry_price) / e.entry_price,
        };
        let size = capital * pos_size;
        let net = size * pnl_pct_raw - size * FEE_PCT * 2.0;
        capital += net;
        peak = peak.max(capital);
        let dd_pct = if peak > 0.0 { (peak - capital) / peak * 100.0 } else { 0.0 };

        // Localiza candle de exit: percorre rest_start..rest_end e usa a primeira
        // ocorrência. Para is_eod, último candle do dia.
        let exit_idx = locate_exit_idx(candles, e, &result);
        let exit_time  = candles[exit_idx].open_time;
        let entry_time = candles[entry_candle_idx].open_time;

        n_seq += 1;
        trades.push(Trade {
            n: n_seq,
            entry_time,
            entry_price: e.entry_price,
            exit_time,
            exit_price:  result.exit_price,
            direction:   e.direction,
            pnl_pct:     net / size * 100.0,
            pnl_dollar:  net,
            is_eod:      result.is_eod,
            capital_after: capital,
            drawdown_pct:  dd_pct,
        });
    }

    trades
}

fn locate_exit_idx(candles: &[Candle], e: &Entry, result: &ExitResult) -> usize {
    let end = e.rest_end.min(candles.len());
    if result.is_eod {
        for j in e.rest_start..end {
            if candles[j].minute_of_day >= END_OF_DAY { return j; }
        }
        return end.saturating_sub(1);
    }
    // Match approximate: candle cujo high/low cruza exit_price
    for j in e.rest_start..end {
        let c = &candles[j];
        if c.low <= result.exit_price && result.exit_price <= c.high { return j; }
    }
    end.saturating_sub(1)
}
