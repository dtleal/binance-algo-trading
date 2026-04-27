//! `backtest` — CLI do sweep unificado.
//!
//! Substitui os 3 binários legados (sweep, sweep_v2, sweep_range).

use anyhow::{Context, Result};
use chrono::{DateTime, NaiveDate, NaiveDateTime, Utc};
use clap::Parser;
use uuid::Uuid;

use backtest::{
    db, sweep,
    Symbol, Timeframe, Ctx,
};

#[derive(Parser, Debug)]
#[command(version, about = "Binance algo trading sweep engine", long_about = None)]
struct Args {
    /// Símbolo Binance (ex.: BTCUSDT)
    #[arg(long)]
    symbol: String,

    /// Timeframe: 1m, 2m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d
    #[arg(long)]
    timeframe: String,

    /// Início do range (YYYY-MM-DD ou YYYY-MM-DD HH:MM:SS UTC). Opcional.
    #[arg(long)]
    from: Option<String>,

    /// Fim do range. Opcional.
    #[arg(long)]
    until: Option<String>,

    /// Strategies separadas por vírgula (ex.: vwap_pullback,orb).
    #[arg(long, value_delimiter = ',')]
    strategy: Vec<String>,

    /// Exit models separados por vírgula (ex.: fixed_tp_sl,trailing_stop).
    /// Ignorado para strategies monolíticas (range).
    #[arg(long, value_delimiter = ',', default_value = "fixed_tp_sl")]
    exit: Vec<String>,

    /// Position size (fração de capital por trade).
    #[arg(long, default_value_t = 0.10)]
    pos_size: f64,

    /// Não escrever em sweep_results (debug/dry-run).
    #[arg(long)]
    no_write: bool,

    /// Path para TOML com grids dos params (opcional). Stub no scaffold.
    #[arg(long)]
    grid: Option<String>,
}

fn parse_date(s: &str) -> Result<DateTime<Utc>> {
    if let Ok(d) = NaiveDate::parse_from_str(s, "%Y-%m-%d") {
        return Ok(d.and_hms_opt(0, 0, 0).unwrap().and_utc());
    }
    if let Ok(dt) = NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S") {
        return Ok(dt.and_utc());
    }
    anyhow::bail!("invalid date '{s}': use YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS'")
}

fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let args = Args::parse();

    let symbol = Symbol::new(&args.symbol);
    let timeframe = Timeframe::parse(&args.timeframe)
        .with_context(|| format!("invalid timeframe '{}'", args.timeframe))?;
    let from  = args.from.as_deref().map(parse_date).transpose()?;
    let until = args.until.as_deref().map(parse_date).transpose()?;

    tracing::info!(
        symbol = %symbol, timeframe = %timeframe,
        from = ?from, until = ?until,
        strategies = ?args.strategy, exits = ?args.exit,
        "starting sweep"
    );

    // Carrega candles do Postgres
    let mut client = db::connect_from_env()?;
    let candles = db::load_candles(&mut client, &symbol, timeframe, from, until)?;
    tracing::info!(n_candles = candles.len(), "candles loaded");
    if candles.is_empty() {
        anyhow::bail!("no candles found for {symbol} {timeframe} in range");
    }
    let days = db::group_by_day(&candles);

    // Constrói strategies e exits via factory (stub no scaffold)
    let grid_spec = args.grid.as_deref().unwrap_or("");
    let strategies: Vec<Box<dyn backtest::strategy::Strategy>> = args.strategy.iter()
        .map(|n| backtest::strategy::build_strategy(n, grid_spec))
        .collect::<Result<_>>()?;
    let exits: Vec<Box<dyn backtest::exit::Exit>> = args.exit.iter()
        .map(|n| backtest::exit::build_exit(n, grid_spec))
        .collect::<Result<_>>()?;

    let sweep_id = Uuid::new_v4();
    let ctx = Ctx { symbol: &symbol, timeframe, candles: &candles, days: &days };

    let results = sweep::run_sweep(&strategies, &exits, &ctx, args.pos_size, sweep_id);
    tracing::info!(n_results = results.len(), "sweep done");

    if !args.no_write {
        let period_start = from.map(|d| d.date_naive());
        let period_end   = until.map(|d| d.date_naive());
        let n = db::write_sweep_results(&mut client, sweep_id, period_start, period_end, &results)?;
        tracing::info!(rows_written = n, %sweep_id, "results persisted");
    } else {
        tracing::info!(%sweep_id, "skip write (--no-write)");
    }

    Ok(())
}
