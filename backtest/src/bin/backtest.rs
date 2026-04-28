//! `backtest` — CLI unificado: sweep (grid) + detail (1 combo + chart HTML).

use anyhow::{Context, Result};
use chrono::{DateTime, NaiveDate, NaiveDateTime, Utc};
use clap::{Parser, Subcommand};

use backtest::{
    db, sweep, detail, chart, release,
    overfit, walkforward, onboard, range_debug,
    Symbol, Timeframe, Ctx,
    detail::DetailParams,
    exit::{Exit, build_exit},
    strategy::{Strategy, build_strategy},
};
use uuid::Uuid;

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
    /// Release: promove 1 sweep_result pra preset ativo (aposenta o anterior).
    Release(ReleaseArgs),
    /// Anti-overfit checks (param sensitivity + IS/OOS).
    OverfitCheck(OverfitCheckArgs),
    /// Walkforward (validação): janelas deslizantes com params fixos.
    Walkforward(WalkforwardArgs),
    /// Onboard: pipeline completo (fetch → sweep IS → gates → release).
    Onboard(OnboardArgs),
    /// Range debug: visualiza áreas laterais detectadas em 1 dia + entradas.
    RangeDebug(RangeDebugArgs),
}

#[derive(clap::Args, Debug)]
struct RangeDebugArgs {
    #[arg(long)] symbol: String,
    #[arg(long)] timeframe: String,
    /// Data específica YYYY-MM-DD (default: dia com mais atividade nos últimos 30d).
    #[arg(long)] date: Option<String>,
    /// Pega params do top-1 dessa sweep_result (alternativa: --params JSON).
    #[arg(long)] sweep_result_id: Option<i64>,
    /// JSON dos params Range. Ex: '{"adx_thresh":20,...}'
    #[arg(long)] params: Option<String>,
    #[arg(long, default_value_t = 0.10)] pos_size: f64,
    #[arg(long, default_value = "/tmp/range_debug.html")] output: String,
}

#[derive(clap::Args, Debug)]
struct OnboardArgs {
    #[arg(long)] symbol: String,
    #[arg(long)] timeframe: String,
    #[arg(long, default_value_t = 365)] days: u32,
    #[arg(long, value_delimiter = ',', default_value = "vwap_pullback,orb,ema_scalp,pdhl,momentum")]
    strategy: Vec<String>,
    #[arg(long, value_delimiter = ',', default_value = "fixed_tp_sl,trailing_stop")]
    exit: Vec<String>,
    #[arg(long, default_value_t = 0.10)] pos_size: f64,
    #[arg(long)] released_by: Option<String>,
}

#[derive(clap::Args, Debug)]
struct ReleaseArgs {
    #[arg(long)] sweep_result_id: i64,
    #[arg(long)] released_by: Option<String>,
    #[arg(long)] notes: Option<String>,
    /// UUID de overfit_tests.test_uuid — bloqueia release se algum check falhou.
    #[arg(long)] require_overfit_test: Option<String>,
    /// UUID de walkforward_runs.walkforward_id — exige que tenha sido rodado.
    #[arg(long)] require_walkforward: Option<String>,
}

#[derive(clap::Args, Debug)]
struct OverfitCheckArgs {
    #[arg(long)] sweep_result_id: i64,
    /// param-sensitivity | is-oos | all
    #[arg(long, default_value = "all")] check: String,
    #[arg(long, default_value_t = 0.10)] pos_size: f64,
    #[arg(long, default_value_t = 0.70)] min_relative_return: f64,
    #[arg(long, default_value_t = 4)]    min_neighbors: usize,
    #[arg(long, default_value_t = 0.50)] min_oos_relative: f64,
    #[arg(long, default_value_t = 30)]   min_oos_trades: usize,
    /// Override range (se quiser IS/OOS num range diferente do period_*).
    #[arg(long)] from:  Option<String>,
    #[arg(long)] until: Option<String>,
}

#[derive(clap::Args, Debug)]
struct WalkforwardArgs {
    #[arg(long)] sweep_result_id: i64,
    #[arg(long)] from:  Option<String>,
    #[arg(long)] until: Option<String>,
    #[arg(long, default_value_t = 30)] window_days: u32,
    #[arg(long, default_value_t = 30)] step_days:   u32,
    #[arg(long, default_value_t = 0.10)] pos_size: f64,
    #[arg(long, default_value_t = 0.70)] min_pct_positive: f64,
    #[arg(long, default_value_t = 30.0)] max_window_dd_pct: f64,
    #[arg(long, default_value_t = 5)]    min_non_empty: usize,
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
        Command::Sweep(a)        => run_sweep(a),
        Command::Detail(a)       => run_detail(a),
        Command::Release(a)      => run_release(a),
        Command::OverfitCheck(a) => run_overfit_check(a),
        Command::Walkforward(a)  => run_walkforward(a),
        Command::Onboard(a)      => run_onboard(a),
        Command::RangeDebug(a)   => run_range_debug_cmd(a),
    }
}

fn run_range_debug_cmd(args: RangeDebugArgs) -> Result<()> {
    use chrono::NaiveDate;
    let symbol = Symbol::new(&args.symbol);
    let timeframe = Timeframe::parse(&args.timeframe)
        .with_context(|| format!("invalid timeframe '{}'", args.timeframe))?;
    let target_date = args.date.as_deref()
        .map(|s| NaiveDate::parse_from_str(s, "%Y-%m-%d"))
        .transpose()
        .context("invalid --date")?;

    let mut client = db::connect_from_env()?;

    // Resolve params
    let params: serde_json::Value = match (args.sweep_result_id, args.params.as_deref()) {
        (Some(id), _) => {
            let row = client.query_one(
                "SELECT strategy_params FROM sweep_results WHERE id=$1 AND strategy='range'",
                &[&id],
            ).with_context(|| format!("sweep_result {id} not found or not range"))?;
            row.get::<_, Option<serde_json::Value>>(0)
                .ok_or_else(|| anyhow::anyhow!("sweep_result {id} has NULL strategy_params"))?
        }
        (None, Some(json)) => serde_json::from_str(json).context("invalid --params JSON")?,
        (None, None) => {
            // defaults do Grid::default()
            serde_json::json!({
                "adx_thresh": 25.0, "atr_pct_thresh": 0.5, "range_lookback": 50,
                "zone_pct": 25.0, "tp_range_pct": 50.0, "sl_range_pct": 30.0,
                "recent_thresh_pct": 2.0, "max_orders": 4, "pos_size": 0.10
            })
        }
    };

    let day = range_debug::run_range_debug(&mut client, &symbol, timeframe, target_date, &params, args.pos_size)?;

    println!("Date:   {}", day.date);
    println!("Candles:{}  Regions:{}  Trades:{} (opens) / {} (closes)",
        day.snapshots.len(), day.regions.len(),
        day.trades.iter().filter(|t| matches!(t.event, range_debug::TradeEvent::Open)).count(),
        day.trades.iter().filter(|t| matches!(t.event, range_debug::TradeEvent::Close)).count());

    chart::write_range_debug_html(&args.output, &day)?;
    println!("HTML: {}", args.output);
    Ok(())
}

fn run_onboard(args: OnboardArgs) -> Result<()> {
    let symbol = Symbol::new(&args.symbol);
    let timeframe = Timeframe::parse(&args.timeframe)
        .with_context(|| format!("invalid timeframe '{}'", args.timeframe))?;

    let out = onboard::run_onboard(onboard::OnboardInput {
        symbol, timeframe, days: args.days,
        strategies: args.strategy, exits: args.exit,
        pos_size: args.pos_size,
        released_by: args.released_by,
    })?;

    println!();
    println!("════════════════════════════════════════════════════════════════════");
    println!("  ✅ ONBOARD COMPLETE");
    println!("════════════════════════════════════════════════════════════════════");
    println!("  preset_id:        {}", out.preset_id);
    println!("  sweep_result_id:  {}", out.sweep_result_id);
    println!("  sweep_id:         {}", out.sweep_id);
    println!("  overfit_test:     {}", out.overfit_test);
    println!("  walkforward_id:   {}", out.walkforward_id);
    println!("════════════════════════════════════════════════════════════════════");
    Ok(())
}

fn run_release(args: ReleaseArgs) -> Result<()> {
    let mut client = db::connect_from_env()?;
    let overfit_uuid = args.require_overfit_test.as_deref().map(Uuid::parse_str).transpose()
        .context("invalid --require-overfit-test UUID")?;
    let wf_uuid = args.require_walkforward.as_deref().map(Uuid::parse_str).transpose()
        .context("invalid --require-walkforward UUID")?;

    let out = release::release(&mut client, release::ReleaseInput {
        sweep_result_id: args.sweep_result_id,
        released_by:     args.released_by,
        notes:           args.notes,
        require_overfit_test_uuid: overfit_uuid,
        require_walkforward_id:    wf_uuid,
    })?;
    println!();
    println!("✅ Released preset id={} for {} {} × {}",
        out.preset_id, out.symbol, out.strategy, out.exit_name);
    if let Some(rid) = out.retired_id {
        println!("   Retired previous active preset id={rid}");
    } else {
        println!("   No previous active preset to retire");
    }
    Ok(())
}

fn run_overfit_check(args: OverfitCheckArgs) -> Result<()> {
    let mut client = db::connect_from_env()?;
    let (input, period_from, period_until) =
        overfit::load_input_from_sweep_result(&mut client, args.sweep_result_id, args.pos_size)?;

    // Range pra IS/OOS: override CLI > period do sweep_result > sem range
    let from  = args.from.as_deref().map(parse_date).transpose()?.or(period_from);
    let until = args.until.as_deref().map(parse_date).transpose()?.or(period_until);

    let test_uuid = Uuid::new_v4();
    let want = args.check.as_str();
    let do_sens = want == "param-sensitivity" || want == "all";
    let do_isoos = want == "is-oos" || want == "all";

    if !do_sens && !do_isoos {
        anyhow::bail!("--check must be 'param-sensitivity', 'is-oos' or 'all'");
    }

    let mut overall_pass = true;
    let mut all_inconclusive = true;

    if do_sens {
        let cfg = overfit::ParamSensitivityConfig {
            min_relative_return: args.min_relative_return,
            min_neighbors:       args.min_neighbors,
        };
        let r = overfit::check_param_sensitivity(&mut client, &input, &cfg, test_uuid)?;
        print_check_result(&r);
        match r.outcome {
            overfit::Outcome::Fail => overall_pass = false,
            overfit::Outcome::Pass => all_inconclusive = false,
            _ => {}
        }
    }

    if do_isoos {
        let cfg = overfit::IsOosConfig {
            min_oos_relative_return: args.min_oos_relative,
            min_oos_trades:          args.min_oos_trades,
        };
        let candles = db::load_candles(&mut client, &input.symbol, input.timeframe, from, until)?;
        let r = overfit::check_is_oos(&mut client, &input, &candles, &cfg, test_uuid)?;
        print_check_result(&r);
        match r.outcome {
            overfit::Outcome::Fail => overall_pass = false,
            overfit::Outcome::Pass => all_inconclusive = false,
            _ => {}
        }
    }

    println!();
    println!("test_uuid: {test_uuid}");
    let final_outcome = if !overall_pass { "FAIL" }
        else if all_inconclusive { "INCONCLUSIVE" } else { "PASS" };
    println!("Overall: {final_outcome}");
    if !overall_pass { std::process::exit(2); }
    if all_inconclusive { std::process::exit(3); }
    Ok(())
}

fn print_check_result(r: &overfit::CheckResult) {
    println!();
    println!("── {} ──", r.test_type);
    println!("outcome: {}", r.outcome.as_str().to_uppercase());
    println!("metrics: {}", serde_json::to_string_pretty(&r.metrics).unwrap_or_default());
}

fn run_walkforward(args: WalkforwardArgs) -> Result<()> {
    let mut client = db::connect_from_env()?;
    let (input, period_from, period_until) =
        overfit::load_input_from_sweep_result(&mut client, args.sweep_result_id, args.pos_size)?;

    let from  = args.from.as_deref().map(parse_date).transpose()?.or(period_from);
    let until = args.until.as_deref().map(parse_date).transpose()?.or(period_until);

    let from_d  = from .map(|d| d.date_naive())
        .ok_or_else(|| anyhow::anyhow!("walkforward requires --from (or period_start no sweep_result)"))?;
    let until_d = until.map(|d| d.date_naive())
        .ok_or_else(|| anyhow::anyhow!("walkforward requires --until (or period_end no sweep_result)"))?;

    let cfg = walkforward::WalkforwardConfig {
        min_pct_positive:  args.min_pct_positive,
        max_window_dd_pct: args.max_window_dd_pct,
        min_non_empty:     args.min_non_empty,
    };

    let out = walkforward::run_walkforward(&mut client, &walkforward::WalkforwardInput {
        input, from: from_d, until: until_d,
        window_days: args.window_days, step_days: args.step_days,
    }, &cfg)?;

    println!();
    println!("── walkforward ──");
    println!("walkforward_id: {}", out.walkforward_id);
    println!("Windows: {}", out.windows.len());
    if !out.windows.is_empty() {
        println!("{:>3}  {:<10} {:<10} {:>8} {:>7} {:>10} {:>8}",
            "#", "start", "end", "trades", "ret%", "winrate%", "dd%");
        for w in &out.windows {
            println!("{:>3}  {:<10} {:<10} {:>8} {:>+7.2} {:>+9.1}% {:>+7.2}",
                w.idx, w.start, w.end, w.trades, w.return_pct, w.win_rate, w.max_dd_pct);
        }
    }
    println!();
    println!("summary: {}", serde_json::to_string_pretty(&out.summary).unwrap_or_default());
    println!("outcome: {}", out.outcome.as_str().to_uppercase());

    match out.outcome {
        overfit::Outcome::Fail => std::process::exit(2),
        overfit::Outcome::Inconclusive => std::process::exit(3),
        overfit::Outcome::Pass => Ok(()),
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
