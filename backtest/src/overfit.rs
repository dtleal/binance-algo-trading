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
/// todos os params iguais menos um). Compara return_pct.
pub fn check_param_sensitivity(
    client: &mut Client,
    input: &OverfitInput,
    cfg: &ParamSensitivityConfig,
    test_uuid: Uuid,
) -> Result<CheckResult> {
    // 1. Pega return_pct do champion
    let champ = client.query_opt(
        "SELECT return_pct FROM sweep_results
         WHERE symbol=$1 AND timeframe=$2 AND strategy=$3 AND exit_name=$4
           AND strategy_params = $5 AND exit_params = $6
         LIMIT 1",
        &[
            &input.symbol.as_str(), &input.timeframe.as_str(),
            &input.strategy, &Some(input.exit_name.clone()),
            &input.strategy_params, &Some(input.exit_params.clone()),
        ],
    )?;
    let champ_return: rust_decimal::Decimal = match champ {
        Some(r) => r.get::<_, Option<rust_decimal::Decimal>>(0).unwrap_or_default(),
        None => {
            // Champion não está em sweep_results → não dá pra comparar com vizinhos
            let metrics = json!({"reason": "champion not found in sweep_results"});
            return persist(client, test_uuid, "param_sensitivity", input, Outcome::Inconclusive, &metrics);
        }
    };
    let champ_return: f64 = champ_return.try_into().unwrap_or(0.0);

    // 2. Coleta vizinhos: pra cada chave numérica dos strategy_params, busca
    //    rows com mesma strategy/exit + mesmos outros params + mesmo exit_params,
    //    diferindo só naquela chave.
    let mut neighbors: Vec<f64> = Vec::new();
    let mut tested_keys: Vec<String> = Vec::new();

    if let Some(obj) = input.strategy_params.as_object() {
        for (key, val) in obj {
            // Só perturba numéricos
            if !val.is_number() { continue; }
            // Build params com TODAS as keys, marca a target como "diferente"
            // SQL: strategy_params @> all_keys_except_target AND strategy_params->target != val
            // Aproximação: comparar igualdade de todos os outros params
            let mut other_pairs: Vec<(String, Value)> = obj.iter()
                .filter(|(k, _)| k.as_str() != key)
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect();
            // Constrói JSONB de "containment" pros outros params
            let mut contain = serde_json::Map::new();
            for (k, v) in &other_pairs { contain.insert(k.clone(), v.clone()); }
            let contain_v = Value::Object(contain);

            let rows = client.query(
                "SELECT return_pct FROM sweep_results
                 WHERE symbol=$1 AND timeframe=$2 AND strategy=$3 AND exit_name=$4
                   AND exit_params = $5
                   AND strategy_params @> $6
                   AND (strategy_params -> $7) IS DISTINCT FROM $8::jsonb
                   AND return_pct IS NOT NULL",
                &[
                    &input.symbol.as_str(), &input.timeframe.as_str(),
                    &input.strategy, &Some(input.exit_name.clone()),
                    &Some(input.exit_params.clone()),
                    &contain_v,
                    &key.as_str(),
                    &val,
                ],
            )?;
            // Coleta só os 2 mais próximos (1 acima, 1 abaixo) usando max/min
            // de return_pct... na real coletamos todos e deixa median absorver.
            for r in rows {
                if let Some(d) = r.get::<_, Option<rust_decimal::Decimal>>(0) {
                    let f: f64 = d.try_into().unwrap_or(0.0);
                    neighbors.push(f);
                }
            }
            tested_keys.push(key.clone());
        }
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
    let row = client.query_opt(
        "SELECT symbol, timeframe, strategy, exit_name,
                strategy_params, exit_params, period_start, period_end
         FROM sweep_results WHERE id = $1",
        &[&sweep_result_id],
    )?
    .ok_or_else(|| anyhow!("sweep_result {sweep_result_id} not found"))?;

    let symbol_s: String = row.get(0);
    let tf_s:     String = row.get(1);
    let strategy: String = row.get(2);
    let exit_name: Option<String> = row.get(3);
    let strategy_params: Option<Value> = row.get(4);
    let exit_params:     Option<Value> = row.get(5);
    let period_start: Option<chrono::NaiveDate> = row.get(6);
    let period_end:   Option<chrono::NaiveDate> = row.get(7);

    let symbol = Symbol::new(symbol_s);
    let timeframe = Timeframe::parse(&tf_s)
        .ok_or_else(|| anyhow!("invalid timeframe '{tf_s}' in sweep_results"))?;
    let exit_name  = exit_name.ok_or_else(|| anyhow!("exit_name NULL — strategy monolítica não suporta IS/OOS"))?;
    let strategy_params = strategy_params.ok_or_else(|| anyhow!("strategy_params NULL — re-run sweep"))?;
    let exit_params     = exit_params.ok_or_else(|| anyhow!("exit_params NULL — re-run sweep"))?;

    let from  = period_start.map(|d| d.and_hms_opt(0, 0, 0).unwrap().and_utc());
    let until = period_end.map(|d| d.and_hms_opt(23, 59, 59).unwrap().and_utc());

    Ok((OverfitInput {
        symbol, timeframe, strategy,
        strategy_params, exit_name, exit_params,
        pos_size,
    }, from, until))
}
