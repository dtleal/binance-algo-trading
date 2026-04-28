//! Walkforward modo validação: janelas deslizantes com params fixos.
//!
//! Sem train/test split (não treina nada — params já vêm fixos do sweep).
//! Cada janela `[start, start + window_days)` vira 1 row em `walkforward_runs`.

use anyhow::{anyhow, Context, Result};
use chrono::{DateTime, Duration, NaiveDate, Utc};
use postgres::Client;
use serde_json::{json, Value};
use uuid::Uuid;

use crate::db;
use crate::evaluate::evaluate_combo;
use crate::overfit::{Outcome, OverfitInput};
use crate::types::*;

/// Buffer extra de dias antes da janela para warmup do range strategy
/// (ATR/ADX precisam de ~50 candles antes de produzir valores).
const RANGE_WARMUP_DAYS: i64 = 7;

#[derive(Debug, Clone)]
pub struct WalkforwardInput {
    pub input: OverfitInput,
    pub from:  NaiveDate,
    pub until: NaiveDate,
    pub window_days: u32,
    pub step_days:   u32,
}

#[derive(Debug, Clone)]
pub struct WalkforwardConfig {
    pub min_pct_positive:    f64,    // 0.70
    pub max_window_dd_pct:   f64,    // 30.0
    pub min_non_empty:       usize,  // 5
}

impl Default for WalkforwardConfig {
    fn default() -> Self {
        Self { min_pct_positive: 0.70, max_window_dd_pct: 30.0, min_non_empty: 5 }
    }
}

#[derive(Debug, Clone)]
pub struct WindowResult {
    pub idx: usize,
    pub start: NaiveDate,
    pub end:   NaiveDate,
    pub return_pct:    f64,
    pub win_rate:      f64,
    pub trades:        usize,
    pub max_dd_pct:    f64,
}

#[derive(Debug)]
pub struct WalkforwardOutput {
    pub walkforward_id: Uuid,
    pub windows: Vec<WindowResult>,
    pub outcome: Outcome,
    pub summary: Value,
}

pub fn run_walkforward(
    client: &mut Client,
    wf_input: &WalkforwardInput,
    cfg: &WalkforwardConfig,
) -> Result<WalkforwardOutput> {
    if wf_input.window_days == 0 || wf_input.step_days == 0 {
        return Err(anyhow!("window_days and step_days must be > 0"));
    }
    if wf_input.until <= wf_input.from {
        return Err(anyhow!("until ({}) must be after from ({})", wf_input.until, wf_input.from));
    }
    let total_days = (wf_input.until - wf_input.from).num_days();
    if total_days < wf_input.window_days as i64 {
        return Err(anyhow!("range too short: {total_days}d < window_days {}", wf_input.window_days));
    }

    let walkforward_id = Uuid::new_v4();
    let mut windows: Vec<WindowResult> = Vec::new();

    let mut win_start = wf_input.from;
    let mut idx = 0_usize;
    while win_start + Duration::days(wf_input.window_days as i64) <= wf_input.until {
        let win_end = win_start + Duration::days(wf_input.window_days as i64);
        idx += 1;

        // Warmup pra range
        let load_from = if wf_input.input.strategy == "range" {
            win_start - Duration::days(RANGE_WARMUP_DAYS)
        } else {
            win_start
        };

        let candles = db::load_candles(
            client,
            &wf_input.input.symbol,
            wf_input.input.timeframe,
            Some(date_to_dt(load_from, false)),
            Some(date_to_dt(win_end, true)),
        ).with_context(|| format!("load candles for window {win_start}..{win_end}"))?;

        if candles.is_empty() {
            // Sem dados nesse intervalo — pula janela (não conta)
            win_start = win_start + Duration::days(wf_input.step_days as i64);
            continue;
        }
        let days = db::group_by_day(&candles);

        let metrics = evaluate_combo(
            &candles, &days,
            &wf_input.input.strategy, &wf_input.input.strategy_params,
            &wf_input.input.exit_name, &wf_input.input.exit_params,
            wf_input.input.pos_size,
        )?;

        let return_pct = metrics.return_pct(INITIAL_CAPITAL);
        let win_rate = metrics.win_rate();
        let w = WindowResult {
            idx, start: win_start, end: win_end,
            return_pct, win_rate,
            trades: metrics.trades,
            max_dd_pct: metrics.max_dd_pct,
        };
        persist_window(client, walkforward_id, &wf_input.input, &w)?;
        windows.push(w);

        win_start = win_start + Duration::days(wf_input.step_days as i64);
    }

    if windows.is_empty() {
        let summary = json!({"reason": "no candles in any window — load data first"});
        return Ok(WalkforwardOutput {
            walkforward_id, windows, outcome: Outcome::Inconclusive, summary,
        });
    }

    // Pass criteria
    let total = windows.len();
    let empty = windows.iter().filter(|w| w.trades == 0).count();
    let non_empty = total - empty;
    let positive = windows.iter().filter(|w| w.trades > 0 && w.return_pct > 0.0).count();
    let negative = windows.iter().filter(|w| w.trades > 0 && w.return_pct <= 0.0).count();
    let dd_breach = windows.iter().any(|w| w.max_dd_pct > cfg.max_window_dd_pct);

    let positive_pct = if non_empty > 0 { positive as f64 / non_empty as f64 } else { 0.0 };

    let outcome = if non_empty < cfg.min_non_empty {
        Outcome::Inconclusive
    } else if dd_breach {
        Outcome::Fail
    } else if positive_pct >= cfg.min_pct_positive {
        Outcome::Pass
    } else {
        Outcome::Fail
    };

    let summary = json!({
        "total_windows": total,
        "empty_windows": empty,
        "non_empty":     non_empty,
        "positive":      positive,
        "negative":      negative,
        "positive_pct":  positive_pct,
        "max_dd_breach": dd_breach,
        "max_dd_threshold": cfg.max_window_dd_pct,
        "min_pct_positive": cfg.min_pct_positive,
        "min_non_empty":    cfg.min_non_empty,
    });

    Ok(WalkforwardOutput { walkforward_id, windows, outcome, summary })
}

fn date_to_dt(d: NaiveDate, end_of_day: bool) -> DateTime<Utc> {
    let nt = if end_of_day { d.and_hms_opt(23, 59, 59).unwrap() } else { d.and_hms_opt(0, 0, 0).unwrap() };
    nt.and_utc()
}

fn persist_window(
    client: &mut Client,
    walkforward_id: Uuid,
    input: &OverfitInput,
    w: &WindowResult,
) -> Result<()> {
    use rust_decimal::Decimal;
    use std::str::FromStr;
    let to_dec = |v: f64| Decimal::from_str(&format!("{:.4}", if v.is_finite() { v } else { 0.0 })).unwrap_or_default();

    // Sem train: campos train_* duplicam test_* (schema requer NOT NULL).
    // O significado real está no agrupamento por walkforward_id + period.
    client.execute(
        "INSERT INTO walkforward_runs (
            walkforward_id, symbol, timeframe, strategy, exit_name,
            strategy_params, exit_params,
            train_start, train_end, test_start, test_end,
            train_return, test_return,
            train_trades, test_trades,
            test_max_dd, test_win_rate
         ) VALUES (
            $1,$2,$3,$4,$5,
            $6,$7,
            $8,$9,$10,$11,
            $12,$13,
            $14,$15,
            $16,$17
         )",
        &[
            &walkforward_id,
            &input.symbol.as_str(), &input.timeframe.as_str(),
            &input.strategy, &input.exit_name,
            &input.strategy_params, &input.exit_params,
            &w.start, &w.end, &w.start, &w.end,            // train_* = test_* (validação)
            &to_dec(w.return_pct), &to_dec(w.return_pct),
            &(w.trades as i32), &(w.trades as i32),
            &to_dec(w.max_dd_pct), &to_dec(w.win_rate),
        ],
    )?;
    Ok(())
}
