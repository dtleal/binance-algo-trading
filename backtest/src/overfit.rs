//! Anti-overfit: param sensitivity (vizinhança da grade) + IS/OOS split.
//!
//! Cada check insere row em `overfit_tests` com `outcome IN (pass|fail|inconclusive)`.

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use postgres::Client;
use serde_json::{json, Value};
use uuid::Uuid;

use crate::db;
use crate::evaluate::evaluate_combo;
use crate::types::*;

#[derive(Debug, Clone)]
pub struct OverfitInput {
    pub symbol: Symbol,
    pub timeframe: Timeframe,
    pub strategy: String,
    pub strategy_params: Value,
    pub exit_name: String,
    pub exit_params: Value,
    pub pos_size: f64,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Outcome { Pass, Fail, Inconclusive }

impl Outcome {
    pub fn as_str(self) -> &'static str {
        match self { Self::Pass => "pass", Self::Fail => "fail", Self::Inconclusive => "inconclusive" }
    }
}

#[derive(Debug)]
pub struct CheckResult {
    pub test_uuid: Uuid,
    pub test_type: &'static str,
    pub outcome:   Outcome,
    pub metrics:   Value,
}

// ── Param sensitivity ────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct ParamSensitivityConfig {
    pub min_relative_return: f64,    // 0.70 → mediana vizinhos / champion ≥ 0.70
    pub min_neighbors:       usize,  // 4
}

impl Default for ParamSensitivityConfig {
    fn default() -> Self {
        Self { min_relative_return: 0.70, min_neighbors: 4 }
    }
}

/// Procura no `sweep_results` combos vizinhos do champion (mesma strategy/exit,
/// todos os params numéricos iguais menos um). Compara return_pct.
pub fn check_param_sensitivity(
    client: &mut Client,
    input: &OverfitInput,
    cfg: &ParamSensitivityConfig,
    test_uuid: Uuid,
) -> Result<CheckResult> {
    use crate::params_row::{numeric_strategy_param_cols, numeric_exit_param_cols};
    use rust_decimal::Decimal;
    use rust_decimal::prelude::FromPrimitive;
    use postgres::types::ToSql;

    // Helper: extract champion numeric value from input Value object
    let extract_dec = |v: &Value, key: &str| -> Option<Decimal> {
        v.get(key)
            .and_then(|x| x.as_f64())
            .and_then(Decimal::from_f64)
    };

    let strategy_cols = numeric_strategy_param_cols(&input.strategy);
    let exit_cols     = numeric_exit_param_cols(&input.exit_name);

    // 1. return_pct do champion: encontra row exata cruzando todos os numéricos.
    //    Se não encontrar, marca inconclusive.
    let exit_name_opt = Some(input.exit_name.clone());

    // Pré-materializa todos os valores Decimal do champion (lifetime estende ao loop).
    let s_vals: Vec<(&str, Decimal)> = strategy_cols.iter()
        .filter_map(|c| extract_dec(&input.strategy_params, c).map(|d| (*c, d)))
        .collect();
    let e_vals: Vec<(&str, Decimal)> = exit_cols.iter()
        .filter_map(|c| extract_dec(&input.exit_params, c).map(|d| (*c, d)))
        .collect();

    let symbol_str    = input.symbol.as_str();
    let timeframe_str = input.timeframe.as_str();

    let mut where_eq: Vec<String> = Vec::new();
    let mut champ_params: Vec<&(dyn ToSql + Sync)> = vec![
        &symbol_str, &timeframe_str, &input.strategy, &exit_name_opt,
    ];
    let mut idx = 5;
    for (col, _) in &s_vals {
        where_eq.push(format!("{col}::numeric = ${idx}::numeric"));
        idx += 1;
    }
    for (col, _) in &e_vals {
        where_eq.push(format!("{col}::numeric = ${idx}::numeric"));
        idx += 1;
    }
    for (_, d) in &s_vals { champ_params.push(d); }
    for (_, d) in &e_vals { champ_params.push(d); }

    let champ_sql = format!(
        "SELECT return_pct FROM sweep_results \
         WHERE symbol=$1 AND timeframe=$2 AND strategy=$3 AND exit_name=$4{}{} \
         LIMIT 1",
        if where_eq.is_empty() { "".to_string() } else { format!(" AND {}", where_eq.join(" AND ")) },
        ""
    );
    let champ = client.query_opt(champ_sql.as_str(), &champ_params)?;
    let champ_return: f64 = match champ {
        Some(r) => {
            let d: Option<Decimal> = r.get(0);
            d.and_then(|v| v.try_into().ok()).unwrap_or(0.0)
        }
        None => {
            let metrics = json!({"reason": "champion not found in sweep_results"});
            return persist(client, test_uuid, "param_sensitivity", input, Outcome::Inconclusive, &metrics);
        }
    };

    // 2. Para cada coluna numérica de strategy: query vizinhos (target != champion, outros =).
    let mut neighbors: Vec<f64> = Vec::new();
    let mut tested_keys: Vec<String> = Vec::new();

    for (target_col, target_val) in &s_vals {
        // WHERE: strategy + exit cols all equal champion EXCEPT target_col which is different.
        let mut clauses: Vec<String> = Vec::new();
        let mut params: Vec<&(dyn ToSql + Sync)> = vec![
            &symbol_str, &timeframe_str, &input.strategy, &exit_name_opt,
        ];
        let mut i = 5;
        for (c, d) in &s_vals {
            if c == target_col {
                clauses.push(format!("{c}::numeric != ${i}::numeric"));
            } else {
                clauses.push(format!("{c}::numeric = ${i}::numeric"));
            }
            params.push(d);
            i += 1;
            // unused warning suppression
            let _ = target_val;
        }
        for (c, d) in &e_vals {
            clauses.push(format!("{c}::numeric = ${i}::numeric"));
            params.push(d);
            i += 1;
        }
        let sql = format!(
            "SELECT return_pct FROM sweep_results \
             WHERE symbol=$1 AND timeframe=$2 AND strategy=$3 AND exit_name=$4 \
               AND {} AND return_pct IS NOT NULL",
            clauses.join(" AND ")
        );
        let rows = client.query(sql.as_str(), &params)?;
        for r in rows {
            let d: Option<Decimal> = r.get(0);
            if let Some(d) = d {
                let f: f64 = d.try_into().unwrap_or(0.0);
                neighbors.push(f);
            }
        }
        tested_keys.push((*target_col).to_string());
    }

    let n = neighbors.len();
    if n < cfg.min_neighbors {
        let metrics = json!({
            "reason": format!("only {n} neighbors found, need ≥{}", cfg.min_neighbors),
            "champion_return_pct": champ_return,
            "neighbors_count": n,
            "tested_keys": tested_keys,
        });
        return persist(client, test_uuid, "param_sensitivity", input, Outcome::Inconclusive, &metrics);
    }

    // Mediana
    let mut sorted = neighbors.clone();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let median = if sorted.len() % 2 == 0 {
        (sorted[sorted.len()/2 - 1] + sorted[sorted.len()/2]) / 2.0
    } else {
        sorted[sorted.len()/2]
    };

    let outcome = if champ_return.abs() < 1e-9 {
        Outcome::Inconclusive  // champion = 0% return, ratio undefined
    } else if median / champ_return >= cfg.min_relative_return {
        Outcome::Pass
    } else {
        Outcome::Fail
    };

    let metrics = json!({
        "champion_return_pct": champ_return,
        "neighbors_count": n,
        "median_neighbor_return_pct": median,
        "ratio_median_to_champion": if champ_return.abs() > 1e-9 { median / champ_return } else { 0.0 },
        "min_neighbor_return_pct": sorted.first().copied().unwrap_or(0.0),
        "max_neighbor_return_pct": sorted.last().copied().unwrap_or(0.0),
        "tested_keys": tested_keys,
    });
    persist(client, test_uuid, "param_sensitivity", input, outcome, &metrics)
}

// ── IS / OOS split (70/30 fixo) ──────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct IsOosConfig {
    pub min_oos_relative_return: f64,    // 0.50
    pub min_oos_trades:          usize,  // 30
}

impl Default for IsOosConfig {
    fn default() -> Self {
        Self { min_oos_relative_return: 0.50, min_oos_trades: 30 }
    }
}

pub fn check_is_oos(
    client: &mut Client,
    input: &OverfitInput,
    candles_full: &[Candle],
    cfg: &IsOosConfig,
    test_uuid: Uuid,
) -> Result<CheckResult> {
    if candles_full.len() < 100 {
        let metrics = json!({"reason": "insufficient candles for IS/OOS split"});
        return persist(client, test_uuid, "is_oos_split", input, Outcome::Inconclusive, &metrics);
    }
    let split = (candles_full.len() as f64 * 0.70) as usize;
    let is_slice  = &candles_full[..split];
    let oos_slice = &candles_full[split..];
    check_is_oos_with_split(client, input, is_slice, oos_slice, cfg, test_uuid)
}

/// Versão explícita: caller já passa os slices de IS e OOS pré-calculados.
/// Usado pelo `onboard` que faz sweep só no IS.
pub fn check_is_oos_with_split(
    client: &mut Client,
    input: &OverfitInput,
    is_slice: &[Candle],
    oos_slice: &[Candle],
    cfg: &IsOosConfig,
    test_uuid: Uuid,
) -> Result<CheckResult> {
    if is_slice.is_empty() || oos_slice.is_empty() {
        let metrics = json!({"reason": "IS or OOS slice is empty"});
        return persist(client, test_uuid, "is_oos_split", input, Outcome::Inconclusive, &metrics);
    }
    let is_days  = db::group_by_day(is_slice);
    let oos_days = db::group_by_day(oos_slice);

    let is_metrics = evaluate_combo(
        is_slice, &is_days,
        &input.strategy, &input.strategy_params,
        &input.exit_name, &input.exit_params,
        input.pos_size,
    ).context("evaluate IS")?;
    let oos_metrics = evaluate_combo(
        oos_slice, &oos_days,
        &input.strategy, &input.strategy_params,
        &input.exit_name, &input.exit_params,
        input.pos_size,
    ).context("evaluate OOS")?;

    let is_return  = is_metrics.return_pct(INITIAL_CAPITAL);
    let oos_return = oos_metrics.return_pct(INITIAL_CAPITAL);

    // Pré-condição estatística
    if oos_metrics.trades < cfg.min_oos_trades {
        let metrics = json!({
            "reason": format!("oos_trades={} < min={}", oos_metrics.trades, cfg.min_oos_trades),
            "is_return_pct": is_return,
            "oos_return_pct": oos_return,
            "is_trades": is_metrics.trades,
            "oos_trades": oos_metrics.trades,
            "split_at": is_slice.last().map(|c| c.open_time.to_rfc3339()),
        });
        return persist(client, test_uuid, "is_oos_split", input, Outcome::Inconclusive, &metrics);
    }

    // Matriz 2x2 (sinais de IS e OOS)
    let outcome = match (is_return >= 0.0, oos_return >= 0.0) {
        (true,  true)  => {
            // Ambos positivos: ratio test
            let ratio = oos_return / is_return.max(1e-9);
            if ratio >= cfg.min_oos_relative_return { Outcome::Pass } else { Outcome::Fail }
        }
        (true,  false) => Outcome::Fail,    // IS funcionou, OOS quebrou (overfit clássico)
        (false, false) => Outcome::Fail,    // ambos ruins (champion mau)
        (false, true)  => Outcome::Pass,    // IS ruim mas OOS recuperou (não há o que decorar)
    };

    let metrics = json!({
        "is_return_pct":  is_return,
        "oos_return_pct": oos_return,
        "is_trades":      is_metrics.trades,
        "oos_trades":     oos_metrics.trades,
        "is_max_dd_pct":  is_metrics.max_dd_pct,
        "oos_max_dd_pct": oos_metrics.max_dd_pct,
        "ratio":          if is_return.abs() > 1e-9 { oos_return / is_return } else { 0.0 },
        "split_at":       is_slice.last().map(|c| c.open_time.to_rfc3339()),
        "split_ratio":    0.70,
    });
    persist(client, test_uuid, "is_oos_split", input, outcome, &metrics)
}

// ── Persistência ─────────────────────────────────────────────────────────────

fn persist(
    client: &mut Client,
    test_uuid: Uuid,
    test_type: &'static str,
    input: &OverfitInput,
    outcome: Outcome,
    metrics: &Value,
) -> Result<CheckResult> {
    client.execute(
        "INSERT INTO overfit_tests (
            test_uuid, symbol, strategy, exit_name,
            strategy_params, exit_params,
            test_type, outcome, metrics
         ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        &[
            &test_uuid,
            &input.symbol.as_str(), &input.strategy, &input.exit_name,
            &input.strategy_params, &input.exit_params,
            &test_type, &outcome.as_str(), &metrics,
        ],
    )?;
    Ok(CheckResult { test_uuid, test_type, outcome, metrics: metrics.clone() })
}

// ── Helpers utilizados pelo CLI ──────────────────────────────────────────────

/// Carrega input do overfit a partir de uma linha de sweep_results.
pub fn load_input_from_sweep_result(
    client: &mut Client,
    sweep_result_id: i64,
    pos_size: f64,
) -> Result<(OverfitInput, Option<DateTime<Utc>>, Option<DateTime<Utc>>)> {
    use anyhow::anyhow;
    use crate::params_row::{
        strategy_params_from_row, exit_params_from_row,
        strategy_params_to_value, exit_params_to_value,
        SELECT_STRATEGY_PARAMS_COLS, SELECT_EXIT_PARAMS_COLS,
    };
    let sql = format!(
        "SELECT symbol, timeframe, strategy, exit_name, period_start, period_end, \
         {SELECT_STRATEGY_PARAMS_COLS}, {SELECT_EXIT_PARAMS_COLS} \
         FROM sweep_results WHERE id = $1"
    );
    let row = client.query_opt(sql.as_str(), &[&sweep_result_id])?
        .ok_or_else(|| anyhow!("sweep_result {sweep_result_id} not found"))?;

    let symbol_s:  String = row.get("symbol");
    let tf_s:      String = row.get("timeframe");
    let strategy:  String = row.get("strategy");
    let exit_name: Option<String> = row.get("exit_name");
    let period_start: Option<chrono::NaiveDate> = row.get("period_start");
    let period_end:   Option<chrono::NaiveDate> = row.get("period_end");

    let strategy_params_row = strategy_params_from_row(&row);
    let exit_params_row     = exit_params_from_row(&row);

    let symbol = Symbol::new(symbol_s);
    let timeframe = Timeframe::parse(&tf_s)
        .ok_or_else(|| anyhow!("invalid timeframe '{tf_s}' in sweep_results"))?;
    let exit_name = exit_name.ok_or_else(|| anyhow!("exit_name NULL — strategy monolítica não suporta IS/OOS"))?;

    let strategy_params = strategy_params_to_value(&strategy_params_row);
    let exit_params     = exit_params_to_value(&exit_params_row);

    let from  = period_start.map(|d| d.and_hms_opt(0, 0, 0).unwrap().and_utc());
    let until = period_end.map(|d| d.and_hms_opt(23, 59, 59).unwrap().and_utc());

    Ok((OverfitInput {
        symbol, timeframe, strategy,
        strategy_params, exit_name, exit_params,
        pos_size,
    }, from, until))
}
