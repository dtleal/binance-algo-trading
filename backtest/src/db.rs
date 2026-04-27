//! Postgres I/O — load_candles + write_sweep_results.
//!
//! Usa `postgres` (sync). Sem tokio. Binário batch single-thread no IO.

use crate::types::*;
use anyhow::{Context, Result};
use chrono::{DateTime, NaiveDate, Utc};
use postgres::{Client, NoTls};
use std::collections::BTreeMap;

/// Constrói client a partir das env vars POSTGRES_*.
pub fn connect_from_env() -> Result<Client> {
    let user = std::env::var("POSTGRES_USER").context("POSTGRES_USER not set")?;
    let pass = std::env::var("POSTGRES_PASSWORD").context("POSTGRES_PASSWORD not set")?;
    let host = std::env::var("POSTGRES_HOST").unwrap_or_else(|_| "localhost".into());
    let port = std::env::var("POSTGRES_PORT").unwrap_or_else(|_| "5432".into());
    let db   = std::env::var("POSTGRES_DB").context("POSTGRES_DB not set")?;
    let dsn  = format!("host={host} port={port} user={user} password={pass} dbname={db}");
    Client::connect(&dsn, NoTls).context("connecting to Postgres")
}

/// Carrega candles para (symbol, timeframe) num intervalo opcional.
///
/// Range é inclusivo nos dois lados; None significa "sem limite naquele lado".
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
             FROM klines
             WHERE symbol = $1 AND timeframe = $2
               AND open_time >= $3 AND open_time <= $4
             ORDER BY open_time ASC",
            &[&symbol.as_str(), &tf.as_str(), &f, &u],
        )?,
        (Some(f), None) => client.query(
            "SELECT open_time, close_time, open, high, low, close, volume
             FROM klines
             WHERE symbol = $1 AND timeframe = $2 AND open_time >= $3
             ORDER BY open_time ASC",
            &[&symbol.as_str(), &tf.as_str(), &f],
        )?,
        (None, Some(u)) => client.query(
            "SELECT open_time, close_time, open, high, low, close, volume
             FROM klines
             WHERE symbol = $1 AND timeframe = $2 AND open_time <= $3
             ORDER BY open_time ASC",
            &[&symbol.as_str(), &tf.as_str(), &u],
        )?,
        (None, None) => client.query(
            "SELECT open_time, close_time, open, high, low, close, volume
             FROM klines
             WHERE symbol = $1 AND timeframe = $2
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
            open_time,
            close_time,
            open:   r.get::<_, f64>(2),
            high:   r.get::<_, f64>(3),
            low:    r.get::<_, f64>(4),
            close:  r.get::<_, f64>(5),
            volume: r.get::<_, f64>(6),
            day: (secs / 86_400) as u32,
            minute_of_day: ((secs % 86_400) / 60) as u16,
        });
    }
    Ok(out)
}

/// Agrupa índices por dia (mesmo formato usado pelos find_entries_*).
pub fn group_by_day(candles: &[Candle]) -> DayIndex {
    let mut m: BTreeMap<u32, Vec<usize>> = BTreeMap::new();
    for (i, c) in candles.iter().enumerate() {
        m.entry(c.day).or_default().push(i);
    }
    m
}

/// Escreve resultados em sweep_results via staging table + ON CONFLICT DO NOTHING.
///
/// Idempotente: re-rodar com mesmos params (mesmo `params_hash`) não duplica.
pub fn write_sweep_results(
    client: &mut Client,
    sweep_id: uuid::Uuid,
    period_start: Option<NaiveDate>,
    period_end: Option<NaiveDate>,
    rows: &[RunResult],
) -> Result<usize> {
    // TODO[milestone]: implementar com COPY binário pra staging UNLOGGED + INSERT ON CONFLICT.
    // Stub para o scaffold compilar; preenchido quando o sweep estiver gerando rows.
    let _ = (client, sweep_id, period_start, period_end, rows);
    anyhow::bail!("write_sweep_results: not implemented yet (scaffold)")
}
