//! Postgres I/O — load_candles + write_sweep_results.
//!
//! Usa `postgres` (sync). Sem tokio. Binário batch single-thread no IO.

use crate::types::*;
use anyhow::{Context, Result};
use chrono::{DateTime, NaiveDate, Utc};
use postgres::{Client, NoTls};
use std::collections::BTreeMap;

pub fn connect_from_env() -> Result<Client> {
    let user = std::env::var("POSTGRES_USER").context("POSTGRES_USER not set")?;
    let pass = std::env::var("POSTGRES_PASSWORD").context("POSTGRES_PASSWORD not set")?;
    let host = std::env::var("POSTGRES_HOST").unwrap_or_else(|_| "localhost".into());
    let port = std::env::var("POSTGRES_PORT").unwrap_or_else(|_| "5432".into());
    let db   = std::env::var("POSTGRES_DB").context("POSTGRES_DB not set")?;
    let dsn  = format!("host={host} port={port} user={user} password={pass} dbname={db}");
    Client::connect(&dsn, NoTls).context("connecting to Postgres")
}

pub fn load_candles(
    client: &mut Client,
    symbol: &Symbol,
    tf: Timeframe,
    from: Option<DateTime<Utc>>,
    until: Option<DateTime<Utc>>,
) -> Result<Vec<Candle>> {
    let rows = match (from, until) {
        (Some(f), Some(u)) => client.query(
            "SELECT open_time, close_time, open, high, low, close, volume
             FROM klines WHERE symbol = $1 AND timeframe = $2
             AND open_time >= $3 AND open_time <= $4 ORDER BY open_time ASC",
            &[&symbol.as_str(), &tf.as_str(), &f, &u],
        )?,
        (Some(f), None) => client.query(
            "SELECT open_time, close_time, open, high, low, close, volume
             FROM klines WHERE symbol = $1 AND timeframe = $2 AND open_time >= $3
             ORDER BY open_time ASC",
            &[&symbol.as_str(), &tf.as_str(), &f],
        )?,
        (None, Some(u)) => client.query(
            "SELECT open_time, close_time, open, high, low, close, volume
             FROM klines WHERE symbol = $1 AND timeframe = $2 AND open_time <= $3
             ORDER BY open_time ASC",
            &[&symbol.as_str(), &tf.as_str(), &u],
        )?,
        (None, None) => client.query(
            "SELECT open_time, close_time, open, high, low, close, volume
             FROM klines WHERE symbol = $1 AND timeframe = $2
             ORDER BY open_time ASC",
            &[&symbol.as_str(), &tf.as_str()],
        )?,
    };

    let mut out = Vec::with_capacity(rows.len());
    for r in rows {
        let open_time:  DateTime<Utc> = r.get(0);
        let close_time: DateTime<Utc> = r.get(1);
        let secs = open_time.timestamp();
        out.push(Candle {
            open_time, close_time,
            open: r.get(2), high: r.get(3), low: r.get(4),
            close: r.get(5), volume: r.get(6),
            day: (secs / 86_400) as u32,
            minute_of_day: ((secs % 86_400) / 60) as u16,
        });
    }
    Ok(out)
}

pub fn group_by_day(candles: &[Candle]) -> DayIndex {
    let mut m: BTreeMap<u32, Vec<usize>> = BTreeMap::new();
    for (i, c) in candles.iter().enumerate() {
        m.entry(c.day).or_default().push(i);
    }
    m
}

/// Escreve resultados em sweep_results via INSERT batch + ON CONFLICT DO NOTHING.
///
/// Idempotente: re-rodar com mesmo `params_hash` (e mesmo período) não duplica.
/// Schema mínimo preenchido: symbol, timeframe, strategy, exit_name, params_hash,
/// sweep_id, source, period_*, métricas, win_rate, return_pct, final_capital.
/// Param-specific columns (tp_pct/sl_pct/be_r/...) NULL — labels carregam o detalhe.
pub fn write_sweep_results(
    client: &mut Client,
    sweep_id: uuid::Uuid,
    period_start: Option<NaiveDate>,
    period_end: Option<NaiveDate>,
    rows: &[RunResult],
) -> Result<usize> {
    if rows.is_empty() { return Ok(0); }

    let mut tx = client.transaction()?;
    let stmt = tx.prepare(
        "INSERT INTO sweep_results (
            symbol, timeframe, strategy, exit_name,
            strategy_params, exit_params,
            trades, wins, losses, eods,
            win_rate, return_pct, final_capital, max_dd_pct, max_consec_loss,
            period_start, period_end, source, sweep_id, params_hash
         ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20)
         ON CONFLICT DO NOTHING"
    )?;

    let mut inserted = 0_usize;
    for r in rows {
        let win_rate   = to_dec(r.metrics.win_rate());
        let return_pct = to_dec(r.metrics.return_pct(INITIAL_CAPITAL));
        let final_cap  = to_dec(r.metrics.final_capital);
        let max_dd     = to_dec(r.metrics.max_dd_pct);

        let n = tx.execute(&stmt, &[
            &r.symbol.as_str(),
            &r.timeframe.as_str(),
            &r.strategy,
            &r.exit_name,
            &r.strategy_params,
            &r.exit_params,
            &(r.metrics.trades as i32),
            &(r.metrics.wins as i32),
            &(r.metrics.losses as i32),
            &(r.metrics.eods as i32),
            &win_rate, &return_pct, &final_cap, &max_dd,
            &(r.metrics.max_consec_loss as i32),
            &period_start, &period_end,
            &r.source, &sweep_id, &r.params_hash,
        ])?;
        inserted += n as usize;
    }
    tx.commit()?;
    Ok(inserted)
}

/// f64 → Decimal com 4 casas. NaN/Inf viram zero.
#[inline]
fn to_dec(v: f64) -> rust_decimal::Decimal {
    use rust_decimal::Decimal;
    use std::str::FromStr;
    let safe = if v.is_finite() { v } else { 0.0 };
    Decimal::from_str(&format!("{:.4}", safe)).unwrap_or_default()
}
