//! `backtest` — CLI unificado: sweep (grid) + detail (1 combo + chart HTML).

use anyhow::{Context, Result};
use chrono::{DateTime, NaiveDate, NaiveDateTime, Utc};
use clap::{Parser, Subcommand};
use uuid::Uuid;

use backtest::{
    db, sweep, detail, chart,
    Symbol, Timeframe, Ctx,
    detail::DetailParams,
    exit::{Exit, build_exit},
    strategy::{Strategy, build_strategy},
};

#[derive(Parser, Debug)]
#[command(version, about = "Binance algo trading sweep + detail engine")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand, Debug)]
enum Command {
    /// Sweep paramétrico: cartesiano de strategies × exits, grava em sweep_results.
    Sweep(SweepArgs),
    /// Detail: 1 strategy + 1 exit + params únicos. Output trade-a-trade + HTML chart.
    Detail(DetailArgs),
}

#[derive(clap::Args, Debug)]
struct SweepArgs {
    #[arg(long)] symbol: String,
    #[arg(long)] timeframe: String,
    #[arg(long)] from: Option<String>,
    #[arg(long)] until: Option<String>,
    #[arg(long, value_delimiter = ',')] strategy: Vec<String>,
    #[arg(long, value_delimiter = ',', default_value = "fixed_tp_sl")] exit: Vec<String>,
    #[arg(long, default_value_t = 0.10)] pos_size: f64,
    #[arg(long)] no_write: bool,
    #[arg(long)] grid: Option<String>,
}

#[derive(clap::Args, Debug)]
struct DetailArgs {
    #[arg(long)] symbol: String,
    #[arg(long)] timeframe: String,
    #[arg(long)] from: Option<String>,
    #[arg(long)] until: Option<String>,
    #[arg(long)] strategy: String,
    #[arg(long, default_value = "fixed_tp_sl")] exit: String,

    // Exit (fixed_tp_sl)
    #[arg(long)] tp: Option<f64>,
    #[arg(long)] sl: Option<f64>,
    #[arg(long, default_value_t = 0)] max_hold: u16,

    // Exit (trailing_stop)
    #[arg(long)] be_r: Option<f64>,
    #[arg(long)] trail_step: Option<f64>,
    #[arg(long)] tp_r: Option<f64>,

    // Strategy params (overlap entre strategies)
    #[arg(long)] min_bars: Option<usize>,
    #[arg(long)] confirm_bars: Option<usize>,
    #[arg(long)] vwap_prox: Option<f64>,
    #[arg(long)] vwap_window: Option<u32>,
    #[arg(long)] max_trades_per_day: Option<usize>,

    // VWAPPullback
    #[arg(long)] ema_period: Option<usize>,

    // EMAScalp
    #[arg(long)] fast_period: Option<usize>,
    #[arg(long)] slow_period: Option<usize>,

    // ORB
    #[arg(long)] range_mins: Option<u16>,
    #[arg(long)] buffer_pct: Option<f64>,

    // PDHL
    #[arg(long)] prox_pct: Option<f64>,

    // Momentum
    #[arg(long)] kind: Option<String>,    // mom_short|mom_long|rej_short|rej_long
    #[arg(long)] vol_filter: bool,
    #[arg(long)] trend_filter: bool,

    #[arg(long, default_value_t = 0.10)] pos_size: f64,

    /// Path do HTML de output. Vazio para pular geração.
    #[arg(long, default_value = "backtest_detail.html")] output: String,
    #[arg(long)] no_chart: bool,
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

fn init_tracing() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();
}

fn main() -> Result<()> {
    init_tracing();
    let cli = Cli::parse();
    match cli.command {
        Command::Sweep(a)  => run_sweep(a),
        Command::Detail(a) => run_detail(a),
    }
}

fn run_sweep(args: SweepArgs) -> Result<()> {
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

    let mut client = db::connect_from_env()?;
    let candles = db::load_candles(&mut client, &symbol, timeframe, from, until)?;
    tracing::info!(n_candles = candles.len(), "candles loaded");
    if candles.is_empty() { anyhow::bail!("no candles found for {symbol} {timeframe}"); }
    let days = db::group_by_day(&candles);

    let grid_spec = args.grid.as_deref().unwrap_or("");
    let strategies: Vec<Box<dyn Strategy>> = args.strategy.iter()
        .map(|n| build_strategy(n, grid_spec))
        .collect::<Result<_>>()?;
    let exits: Vec<Box<dyn Exit>> = args.exit.iter()
        .map(|n| build_exit(n, grid_spec))
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

fn run_detail(args: DetailArgs) -> Result<()> {
    use backtest::{Direction, ExitFn};

    let symbol = Symbol::new(&args.symbol);
    let timeframe = Timeframe::parse(&args.timeframe)
        .with_context(|| format!("invalid timeframe '{}'", args.timeframe))?;
    let from  = args.from.as_deref().map(parse_date).transpose()?;
    let until = args.until.as_deref().map(parse_date).transpose()?;

    tracing::info!(
        symbol = %symbol, timeframe = %timeframe,
        strategy = %args.strategy, exit = %args.exit,
        "starting detail backtest"
    );

    let mut client = db::connect_from_env()?;
    let candles = db::load_candles(&mut client, &symbol, timeframe, from, until)?;
    if candles.is_empty() { anyhow::bail!("no candles found for {symbol} {timeframe}"); }
    let days = db::group_by_day(&candles);

    // Strategy params do CLI
    let p = DetailParams {
        min_bars:           args.min_bars,
        confirm_bars:       args.confirm_bars,
        vwap_prox:          args.vwap_prox,
        vwap_window:        args.vwap_window,
        max_trades_per_day: args.max_trades_per_day,
        ema_period:         args.ema_period,
        fast_period:        args.fast_period,
        slow_period:        args.slow_period,
        range_mins:         args.range_mins,
        buffer_pct:         args.buffer_pct,
        prox_pct:           args.prox_pct,
        momentum_kind:      args.kind.clone(),
        vol_filter:         Some(args.vol_filter),
        trend_filter:       Some(args.trend_filter),
        entry_window:       None,
    };
    let entries = detail::build_entries(&args.strategy, &candles, &days, &p)?;
    tracing::info!(n_entries = entries.len(), "entries built");

    // Exit fn — params únicos
    let exit_fn = match args.exit.as_str() {
        "fixed_tp_sl" => ExitFn::FixedTpSl {
            tp_pct: args.tp.unwrap_or(0.005),
            sl_pct: args.sl.unwrap_or(0.01),
            max_hold_min: args.max_hold,
        },
        "trailing_stop" => ExitFn::Trailing {
            sl_pct: args.sl.unwrap_or(0.01),
            be_r: args.be_r.unwrap_or(2.0),
            trail_step: args.trail_step.unwrap_or(0.5),
            tp_r: args.tp_r.unwrap_or(0.0),
            max_hold_min: args.max_hold,
        },
        other => anyhow::bail!("exit '{other}' não suportado em detail mode"),
    };

    let result = detail::run_detail(&entries, &candles, exit_fn, args.pos_size);

    print_summary(&args, &result);
    print_trades(&result.trades);

    if !args.no_chart && !args.output.is_empty() {
        let title = format!("{} {} — {} × {}", symbol, timeframe, args.strategy, args.exit);
        chart::write_html(&args.output, &title, &result.trades, backtest::INITIAL_CAPITAL)?;
        tracing::info!(path = %args.output, "chart written");
    }
    Ok(())
}

fn print_summary(args: &DetailArgs, r: &detail::DetailResult) {
    let trades = r.trades.len();
    let wins   = r.trades.iter().filter(|t| t.pnl_dollar > 0.0).count();
    let losses = trades - wins;
    let total_pnl = r.final_capital - backtest::INITIAL_CAPITAL;
    let return_pct = (r.final_capital / backtest::INITIAL_CAPITAL - 1.0) * 100.0;
    let win_rate = if trades > 0 { wins as f64 / trades as f64 * 100.0 } else { 0.0 };
    let avg = if trades > 0 { total_pnl / trades as f64 } else { 0.0 };

    println!();
    println!("════════════════════════════════════════════════════════════════════");
    println!("  {} {}  —  {} × {}  (pos_size={})",
        args.symbol, args.timeframe, args.strategy, args.exit, args.pos_size);
    println!("════════════════════════════════════════════════════════════════════");
    println!("  Trades: {trades}   Wins: {wins} ({win_rate:.1}%)   Losses: {losses}");
    println!("  Total PnL: {sign}${pnl:.2}  ({sign2}{ret:.2}%)",
        sign = if total_pnl >= 0.0 {"+"} else {""},
        sign2 = if return_pct >= 0.0 {"+"} else {""},
        pnl = total_pnl, ret = return_pct);
    println!("  Max DD: {:.2}%   Max consec loss: {}   Avg trade: ${:+.2}",
        r.max_dd_pct, r.max_consec_loss, avg);
    println!("════════════════════════════════════════════════════════════════════");
}

fn print_trades(trades: &[detail::Trade]) {
    if trades.is_empty() { return; }
    use backtest::Direction;
    println!();
    println!("{:>3}  {:<19} {:>10}  {:<19} {:>10}  {:<5} {:>+7}  {:>+8}  {:>10}",
        "#", "entry_time", "entry$", "exit_time", "exit$",
        "dir", "pnl%", "pnl$", "capital$");
    println!("{:─<3}  {:─<19} {:─>10}  {:─<19} {:─>10}  {:─<5} {:─>7}  {:─>8}  {:─>10}",
        "", "", "", "", "", "", "", "", "");
    for t in trades {
        let dir = match t.direction { Direction::Long => "LONG", Direction::Short => "SHORT" };
        println!("{:>3}  {:<19} {:>10.4}  {:<19} {:>10.4}  {:<5} {:>+7.2}  {:>+8.2}  {:>10.2}",
            t.n,
            t.entry_time.format("%Y-%m-%d %H:%M"),
            t.entry_price,
            t.exit_time.format("%Y-%m-%d %H:%M"),
            t.exit_price,
            dir,
            t.pnl_pct,
            t.pnl_dollar,
            t.capital_after);
    }
}
