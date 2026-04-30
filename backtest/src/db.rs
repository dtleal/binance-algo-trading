//! Postgres I/O — load_candles + write_sweep_results.
//!
//! Usa `postgres` (sync). Sem tokio. Binário batch single-thread no IO.

use crate::types::*;
use anyhow::{Context, Result};
use chrono::{DateTime, NaiveDate, Utc};
use postgres::{Client, NoTls};
use postgres::binary_copy::BinaryCopyInWriter;
use postgres::types::Type;
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

    let progress = crate::progress::Progress::start("persist", rows.len());
    let counter = progress.counter.clone();

    let mut tx = client.transaction()?;

    // Temp table com schema flat tipado (53 cols). ON COMMIT DROP.
    tx.batch_execute(
        "CREATE TEMP TABLE _sweep_results_copy (
            symbol               text,
            timeframe            text,
            strategy             text,
            exit_name            text,
            trades               integer,
            wins                 integer,
            losses               integer,
            eods                 integer,
            win_rate             numeric(8,4),
            return_pct           numeric(10,4),
            final_capital        numeric(14,4),
            max_dd_pct           numeric(8,4),
            max_consec_loss      integer,
            period_start         date,
            period_end           date,
            source               text,
            sweep_id             uuid,
            params_hash          char(32),
            -- strategy params (29)
            adx_thresh           numeric(8,4),
            atr_pct_thresh       numeric(8,4),
            range_lookback       integer,
            zone_pct             numeric(8,4),
            tp_range_pct         numeric(8,4),
            sl_range_pct         numeric(8,4),
            recent_thresh_pct    numeric(8,4),
            max_orders           integer,
            pos_size             numeric(8,4),
            mtf_enabled          boolean,
            close_at_opposite    boolean,
            close_on_range_break boolean,
            mtf_timeframe        text,
            min_bars             integer,
            confirm_bars         integer,
            vwap_prox            numeric(10,6),
            vwap_window_days     integer,
            ema_period           integer,
            max_trades_per_day   integer,
            kind                 text,
            vol_filter           boolean,
            trend_filter         boolean,
            entry_window_start   integer,
            entry_window_end     integer,
            fast_period          integer,
            slow_period          integer,
            range_mins           integer,
            buffer_pct           numeric(10,6),
            prox_pct             numeric(10,6),
            -- exit params (6)
            tp_pct               numeric(8,4),
            sl_pct               numeric(8,4),
            max_hold_min         integer,
            be_r                 numeric(8,4),
            trail_step           numeric(8,4),
            tp_r                 numeric(8,4)
         ) ON COMMIT DROP;"
    )?;

    let types = [
        // common (18)
        Type::TEXT, Type::TEXT, Type::TEXT, Type::TEXT,
        Type::INT4, Type::INT4, Type::INT4, Type::INT4,
        Type::NUMERIC, Type::NUMERIC, Type::NUMERIC, Type::NUMERIC,
        Type::INT4,
        Type::DATE, Type::DATE, Type::TEXT, Type::UUID, Type::BPCHAR,
        // strategy (29)
        Type::NUMERIC, Type::NUMERIC, Type::INT4, Type::NUMERIC,
        Type::NUMERIC, Type::NUMERIC, Type::NUMERIC, Type::INT4,
        Type::NUMERIC, Type::BOOL, Type::BOOL, Type::BOOL, Type::TEXT,
        Type::INT4, Type::INT4, Type::NUMERIC, Type::INT4, Type::INT4,
        Type::INT4, Type::TEXT, Type::BOOL, Type::BOOL, Type::INT4, Type::INT4,
        Type::INT4, Type::INT4, Type::INT4, Type::NUMERIC, Type::NUMERIC,
        // exit (6)
        Type::NUMERIC, Type::NUMERIC, Type::INT4, Type::NUMERIC, Type::NUMERIC, Type::NUMERIC,
    ];

    let copy_in = tx.copy_in(
        "COPY _sweep_results_copy (
            symbol, timeframe, strategy, exit_name,
            trades, wins, losses, eods,
            win_rate, return_pct, final_capital, max_dd_pct, max_consec_loss,
            period_start, period_end, source, sweep_id, params_hash,
            adx_thresh, atr_pct_thresh, range_lookback, zone_pct,
            tp_range_pct, sl_range_pct, recent_thresh_pct, max_orders,
            pos_size, mtf_enabled, close_at_opposite, close_on_range_break, mtf_timeframe,
            min_bars, confirm_bars, vwap_prox, vwap_window_days, ema_period,
            max_trades_per_day, kind, vol_filter, trend_filter, entry_window_start, entry_window_end,
            fast_period, slow_period, range_mins, buffer_pct, prox_pct,
            tp_pct, sl_pct, max_hold_min, be_r, trail_step, tp_r
         ) FROM STDIN BINARY"
    )?;
    let mut writer = BinaryCopyInWriter::new(copy_in, &types);

    for r in rows {
        // Filtra rows pathológicos: trades=0 sem signal útil; ou strings
        // requeridas vazias (raríssimo edge case, mas viola check constraint).
        if r.metrics.trades == 0
            || r.symbol.as_str().is_empty()
            || r.strategy.is_empty()
            || r.exit_name.as_deref().map_or(false, str::is_empty)
        {
            continue;
        }

        let win_rate   = to_dec(r.metrics.win_rate());
        let return_pct = to_dec(r.metrics.return_pct(INITIAL_CAPITAL));
        let final_cap  = to_dec(r.metrics.final_capital);
        let max_dd     = to_dec(r.metrics.max_dd_pct);
        let trades     = r.metrics.trades as i32;
        let wins       = r.metrics.wins as i32;
        let losses     = r.metrics.losses as i32;
        let eods       = r.metrics.eods as i32;
        let max_consec = r.metrics.max_consec_loss as i32;
        let sym        = r.symbol.as_str();
        let tf         = r.timeframe.as_str();

        let sp = &r.strategy_params;
        let ep = r.exit_params.as_ref();

        // Materializa Option<f64> → Option<Decimal> (postgres NUMERIC binding).
        let adx_thresh        = sp.adx_thresh.map(to_dec);
        let atr_pct_thresh    = sp.atr_pct_thresh.map(to_dec);
        let zone_pct          = sp.zone_pct.map(to_dec);
        let tp_range_pct      = sp.tp_range_pct.map(to_dec);
        let sl_range_pct      = sp.sl_range_pct.map(to_dec);
        let recent_thresh_pct = sp.recent_thresh_pct.map(to_dec);
        let pos_size          = sp.pos_size.map(to_dec);
        let vwap_prox         = sp.vwap_prox.map(to_dec);
        let buffer_pct        = sp.buffer_pct.map(to_dec);
        let prox_pct          = sp.prox_pct.map(to_dec);
        let tp_pct            = ep.and_then(|e| e.tp_pct).map(to_dec);
        let sl_pct            = ep.and_then(|e| e.sl_pct).map(to_dec);
        let be_r              = ep.and_then(|e| e.be_r).map(to_dec);
        let trail_step        = ep.and_then(|e| e.trail_step).map(to_dec);
        let tp_r              = ep.and_then(|e| e.tp_r).map(to_dec);

        let max_hold_min_e    = ep.and_then(|e| e.max_hold_min);

        writer.write(&[
            // common (18)
            &sym, &tf, &r.strategy, &r.exit_name,
            &trades, &wins, &losses, &eods,
            &win_rate, &return_pct, &final_cap, &max_dd,
            &max_consec,
            &period_start, &period_end,
            &r.source, &sweep_id, &r.params_hash,
            // strategy (29)
            &adx_thresh, &atr_pct_thresh, &sp.range_lookback, &zone_pct,
            &tp_range_pct, &sl_range_pct, &recent_thresh_pct, &sp.max_orders,
            &pos_size, &sp.mtf_enabled, &sp.close_at_opposite, &sp.close_on_range_break, &sp.mtf_timeframe,
            &sp.min_bars, &sp.confirm_bars, &vwap_prox, &sp.vwap_window_days, &sp.ema_period,
            &sp.max_trades_per_day, &sp.kind, &sp.vol_filter, &sp.trend_filter, &sp.entry_window_start, &sp.entry_window_end,
            &sp.fast_period, &sp.slow_period, &sp.range_mins, &buffer_pct, &prox_pct,
            // exit (6)
            &tp_pct, &sl_pct, &max_hold_min_e, &be_r, &trail_step, &tp_r,
        ])?;
        counter.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    }
    writer.finish()?;

    // Move temp → real com ON CONFLICT DO NOTHING (mantém idempotência).
    let inserted = tx.execute(
        "INSERT INTO sweep_results (
            symbol, timeframe, strategy, exit_name,
            trades, wins, losses, eods,
            win_rate, return_pct, final_capital, max_dd_pct, max_consec_loss,
            period_start, period_end, source, sweep_id, params_hash,
            adx_thresh, atr_pct_thresh, range_lookback, zone_pct,
            tp_range_pct, sl_range_pct, recent_thresh_pct, max_orders,
            pos_size, mtf_enabled, close_at_opposite, close_on_range_break, mtf_timeframe,
            min_bars, confirm_bars, vwap_prox, vwap_window_days, ema_period,
            max_trades_per_day, kind, vol_filter, trend_filter, entry_window_start, entry_window_end,
            fast_period, slow_period, range_mins, buffer_pct, prox_pct,
            tp_pct, sl_pct, max_hold_min, be_r, trail_step, tp_r
         )
         SELECT * FROM _sweep_results_copy
         ON CONFLICT DO NOTHING",
        &[],
    )? as usize;

    tx.commit()?;
    drop(progress);
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

/// Cobertura de klines no DB para (symbol, timeframe).
pub fn query_klines_coverage(
    client: &mut Client,
    symbol: &Symbol,
    tf: Timeframe,
) -> Result<(Option<DateTime<Utc>>, Option<DateTime<Utc>>, i64)> {
    let row = client.query_one(
        "SELECT MIN(open_time), MAX(open_time), COUNT(*)::bigint
         FROM klines WHERE symbol=$1 AND timeframe=$2",
        &[&symbol.as_str(), &tf.as_str()],
    )?;
    Ok((row.get(0), row.get(1), row.get(2)))
}

/// Top N candidatos por Calmar (return_pct / max_dd_pct), filtrando ruído estatístico.
/// Restringe pra um sweep_id específico (vizinhos contemporâneos).
pub fn top_by_calmar(
    client: &mut Client,
    sweep_id: uuid::Uuid,
    limit: i64,
) -> Result<Vec<i64>> {
    let rows = client.query(
        "SELECT id FROM sweep_results
         WHERE sweep_id = $1
           AND return_pct > 0
           AND trades >= 10
           AND max_dd_pct >= 0.01
         ORDER BY (return_pct / max_dd_pct) DESC
         LIMIT $2",
        &[&sweep_id, &limit],
    )?;
    Ok(rows.iter().map(|r| r.get(0)).collect())
}
