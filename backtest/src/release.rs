//! Release de preset: pega 1 row de sweep_results e promove pra `presets`.
//!
//! Aposenta o preset 'active' anterior pra (symbol, strategy) — UPDATE status='retired'.
//! Insere o novo como 'active'. Append-only, audit trail completo via timestamps.

use anyhow::{anyhow, Context, Result};
use postgres::Client;

pub struct ReleaseInput {
    pub sweep_result_id: i64,
    pub released_by:     Option<String>,
    pub notes:           Option<String>,
    /// Se Some, valida que TODOS os checks daquele test_uuid em overfit_tests
    /// têm outcome='pass'. Senão bloqueia release.
    pub require_overfit_test_uuid: Option<uuid::Uuid>,
    /// Se Some, valida que walkforward_runs daquele uuid produziu summary OK
    /// (delegado: o caller já checou ao gerar; aqui só verificamos que existe
    /// E que pelo menos 1 row está no DB).
    pub require_walkforward_id: Option<uuid::Uuid>,
}

pub struct ReleaseOutput {
    pub preset_id:    i64,
    pub symbol:       String,
    pub strategy:     String,
    pub exit_name:    String,
    pub retired_id:   Option<i64>,    // preset que foi aposentado, se houve
}

pub fn release(client: &mut Client, input: ReleaseInput) -> Result<ReleaseOutput> {
    // 1. Pega metadata do sweep_results (gates checks). Params em si vão via
    //    INSERT...SELECT no final pra evitar listar 35 colunas aqui.
    let row = client.query_opt(
        "SELECT symbol, timeframe, strategy, exit_name FROM sweep_results WHERE id = $1",
        &[&input.sweep_result_id],
    )?
    .ok_or_else(|| anyhow!("sweep_result {} not found", input.sweep_result_id))?;

    let symbol:    String = row.get(0);
    let timeframe: String = row.get(1);
    let strategy:  String = row.get(2);
    let exit_name: Option<String> = row.get(3);

    let exit_name = exit_name.ok_or_else(|| anyhow!(
        "sweep_result {} has NULL exit_name — strategy monolítica não suporta release ainda",
        input.sweep_result_id
    ))?;

    // 2. Gates opcionais
    if let Some(test_uuid) = input.require_overfit_test_uuid {
        let row = client.query_one(
            "SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN outcome='pass' THEN 1 ELSE 0 END) AS passed,
                SUM(CASE WHEN outcome='fail' THEN 1 ELSE 0 END) AS failed
             FROM overfit_tests WHERE test_uuid = $1",
            &[&test_uuid],
        )?;
        let total: i64 = row.get(0);
        let passed: Option<i64> = row.get(1);
        let failed: Option<i64> = row.get(2);
        if total == 0 {
            return Err(anyhow!("require-overfit-test {test_uuid} not found in overfit_tests"));
        }
        if failed.unwrap_or(0) > 0 {
            return Err(anyhow!(
                "require-overfit-test {test_uuid} has {} failed checks (out of {total}) — blocked",
                failed.unwrap_or(0)
            ));
        }
        if passed.unwrap_or(0) == 0 {
            return Err(anyhow!(
                "require-overfit-test {test_uuid} has 0 passed checks (all inconclusive) — blocked"
            ));
        }
    }

    if let Some(wf_uuid) = input.require_walkforward_id {
        let row = client.query_one(
            "SELECT COUNT(*) FROM walkforward_runs WHERE walkforward_id = $1",
            &[&wf_uuid],
        )?;
        let total: i64 = row.get(0);
        if total == 0 {
            return Err(anyhow!("require-walkforward {wf_uuid} not found in walkforward_runs"));
        }
        // Outcome do walkforward é decidido pelo caller (CLI) que pode ter exit'ed
        // antes mesmo de chegar aqui se outcome != pass. Apenas validar que
        // existe é suficiente nessa fase.
    }

    // 3. Aposenta preset ativo anterior, se houver (dentro da mesma transação do INSERT)
    let mut tx = client.transaction()?;
    let retired = tx.query_opt(
        "UPDATE presets
         SET status='retired', retired_at=NOW(), retired_reason='superseded'
         WHERE symbol=$1 AND timeframe=$2 AND strategy=$3 AND status='active'
         RETURNING id",
        &[&symbol, &timeframe, &strategy],
    )?;
    let retired_id: Option<i64> = retired.map(|r| r.get(0));

    // 4. Insere novo preset 'active' via INSERT...SELECT (copia 35+ cols
    //    sem listar manualmente — só sobrescreve sweep_result_id, walkforward_id, etc.).
    let new_row = tx.query_one(
        "INSERT INTO presets (
            symbol, timeframe, strategy, exit_name,
            sweep_id, sweep_result_id, walkforward_id, overfit_test_uuid,
            return_pct, win_rate, trades, max_dd_pct,
            released_by, notes,
            -- strategy params
            adx_thresh, atr_pct_thresh, range_lookback, zone_pct,
            tp_range_pct, sl_range_pct, recent_thresh_pct, max_orders,
            pos_size, mtf_enabled, close_at_opposite, close_on_range_break, mtf_timeframe,
            min_bars, confirm_bars, vwap_prox, vwap_window_days, ema_period,
            max_trades_per_day, kind, vol_filter, trend_filter,
            entry_window_start, entry_window_end,
            fast_period, slow_period, range_mins, buffer_pct, prox_pct,
            -- exit params
            tp_pct, sl_pct, max_hold_min, be_r, trail_step, tp_r
         )
         SELECT
            symbol, timeframe, strategy, exit_name,
            sweep_id, $1, $2, $3,
            return_pct, win_rate, trades, max_dd_pct,
            $4, $5,
            adx_thresh, atr_pct_thresh, range_lookback, zone_pct,
            tp_range_pct, sl_range_pct, recent_thresh_pct, max_orders,
            pos_size, mtf_enabled, close_at_opposite, close_on_range_break, mtf_timeframe,
            min_bars, confirm_bars, vwap_prox, vwap_window_days, ema_period,
            max_trades_per_day, kind, vol_filter, trend_filter,
            entry_window_start, entry_window_end,
            fast_period, slow_period, range_mins, buffer_pct, prox_pct,
            tp_pct, sl_pct, max_hold_min, be_r, trail_step, tp_r
         FROM sweep_results WHERE id = $6
         RETURNING id",
        &[
            &input.sweep_result_id,
            &input.require_walkforward_id, &input.require_overfit_test_uuid,
            &input.released_by, &input.notes,
            &input.sweep_result_id,
        ],
    )
    .with_context(|| format!("inserting preset for {symbol} {strategy}"))?;
    let preset_id: i64 = new_row.get(0);
    tx.commit()?;

    Ok(ReleaseOutput { preset_id, symbol, strategy, exit_name, retired_id })
}
