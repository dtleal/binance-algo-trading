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
}

pub struct ReleaseOutput {
    pub preset_id:    i64,
    pub symbol:       String,
    pub strategy:     String,
    pub exit_name:    String,
    pub retired_id:   Option<i64>,    // preset que foi aposentado, se houve
}

pub fn release(client: &mut Client, input: ReleaseInput) -> Result<ReleaseOutput> {
    // 1. Pega a row do sweep_results
    let row = client.query_opt(
        "SELECT symbol, timeframe, strategy, exit_name,
                strategy_params, exit_params,
                sweep_id, return_pct, win_rate, trades, max_dd_pct
         FROM sweep_results WHERE id = $1",
        &[&input.sweep_result_id],
    )?
    .ok_or_else(|| anyhow!("sweep_result {} not found", input.sweep_result_id))?;

    let symbol:    String = row.get(0);
    let timeframe: String = row.get(1);
    let strategy:  String = row.get(2);
    let exit_name: Option<String> = row.get(3);
    let strategy_params: Option<serde_json::Value> = row.get(4);
    let exit_params:     Option<serde_json::Value> = row.get(5);
    let sweep_id:    Option<uuid::Uuid> = row.get(6);
    let return_pct:  Option<rust_decimal::Decimal> = row.get(7);
    let win_rate:    Option<rust_decimal::Decimal> = row.get(8);
    let trades:      Option<i32> = row.get(9);
    let max_dd_pct:  Option<rust_decimal::Decimal> = row.get(10);

    let strategy_params = strategy_params.ok_or_else(|| anyhow!(
        "sweep_result {} has NULL strategy_params — re-run sweep to populate",
        input.sweep_result_id
    ))?;
    let exit_params = exit_params.ok_or_else(|| anyhow!(
        "sweep_result {} has NULL exit_params — strategy monolítica (range) ou sweep antigo",
        input.sweep_result_id
    ))?;
    let exit_name = exit_name.ok_or_else(|| anyhow!(
        "sweep_result {} has NULL exit_name — strategy monolítica não suporta release ainda",
        input.sweep_result_id
    ))?;

    // 2. Aposenta preset ativo anterior, se houver
    let mut tx = client.transaction()?;
    let retired = tx.query_opt(
        "UPDATE presets
         SET status='retired', retired_at=NOW(), retired_reason='superseded'
         WHERE symbol=$1 AND strategy=$2 AND status='active'
         RETURNING id",
        &[&symbol, &strategy],
    )?;
    let retired_id: Option<i64> = retired.map(|r| r.get(0));

    // 3. Insere novo preset 'active'
    let new_row = tx.query_one(
        "INSERT INTO presets (
            symbol, timeframe, strategy, exit_name,
            strategy_params, exit_params,
            sweep_id, sweep_result_id,
            return_pct, win_rate, trades, max_dd_pct,
            released_by, notes
         ) VALUES (
            $1, $2, $3, $4, $5, $6,
            $7, $8, $9, $10, $11, $12,
            $13, $14
         )
         RETURNING id",
        &[
            &symbol, &timeframe, &strategy, &exit_name,
            &strategy_params, &exit_params,
            &sweep_id, &input.sweep_result_id,
            &return_pct, &win_rate, &trades, &max_dd_pct,
            &input.released_by, &input.notes,
        ],
    )
    .with_context(|| format!("inserting preset for {symbol} {strategy}"))?;
    let preset_id: i64 = new_row.get(0);
    tx.commit()?;

    Ok(ReleaseOutput { preset_id, symbol, strategy, exit_name, retired_id })
}
