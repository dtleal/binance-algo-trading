//! Orchestrator do sweep: cartesiano EntrySets × ExitVariants + par_iter.

use crate::types::*;
use crate::strategy::Strategy;
use crate::exit::{Exit, evaluate};
use rayon::prelude::*;

const FEE_PCT: f64 = 0.0004;          // taker (entrada + saída)
const INITIAL_CAPITAL: f64 = 1000.0;

/// Entry point: dado strategies + exits + ctx, produz Vec<RunResult>.
pub fn run_sweep(
    strategies: &[Box<dyn Strategy>],
    exits: &[Box<dyn Exit>],
    ctx: &Ctx<'_>,
    pos_size: f64,
    sweep_id: uuid::Uuid,
) -> Vec<RunResult> {
    // Phase 1: build entry sets e exit variants (sequencial, lê de strategies/exits)
    let mut pairings: Vec<(&'static str, EntrySet)> = Vec::new();
    let mut monolithic: Vec<(&'static str, MonolithicRun)> = Vec::new();

    for s in strategies {
        match s.build(ctx) {
            StrategyOutput::EntrySets(sets) => {
                for es in sets { pairings.push((s.name(), es)); }
            }
            StrategyOutput::Runs(runs) => {
                for r in runs { monolithic.push((s.name(), r)); }
            }
        }
    }

    let exit_variants: Vec<(&'static str, ExitVariant)> = exits.iter()
        .flat_map(|e| {
            let name = e.name();
            e.build_variants(ctx).into_iter().map(move |v| (name, v))
        })
        .collect();

    // Phase 2: cartesiano + par_iter (referências, sem move de Symbol)
    let candles = ctx.candles;
    let symbol = ctx.symbol;
    let timeframe = ctx.timeframe;

    let standard: Vec<RunResult> = pairings.par_iter()
        .flat_map_iter(|(s_name, es)| {
            exit_variants.iter().map(move |(e_name, ev)| {
                let metrics = run_pairing(&es.entries, candles, &ev.eval, pos_size);
                RunResult {
                    symbol: symbol.clone(),
                    timeframe,
                    strategy: s_name.to_string(),
                    strategy_params_label: es.strategy_label.clone(),
                    strategy_params: es.strategy_params.clone(),
                    exit_name: Some(e_name.to_string()),
                    exit_params_label: Some(ev.label.clone()),
                    exit_params: Some(ev.params.clone()),
                    period_start: None,
                    period_end: None,
                    sweep_id,
                    params_hash: hash_params(s_name, &es.strategy_label, e_name, &ev.label),
                    source: "backtest",
                    metrics,
                }
            })
        })
        .collect();

    let mono: Vec<RunResult> = monolithic.par_iter()
        .map(|(s_name, run)| {
            let metrics = (run.execute)(candles);
            RunResult {
                symbol: symbol.clone(),
                timeframe,
                strategy: s_name.to_string(),
                strategy_params_label: run.label.clone(),
                strategy_params: run.strategy_params.clone(),
                exit_name: None,
                exit_params_label: None,
                exit_params: None,
                period_start: None,
                period_end: None,
                sweep_id,
                params_hash: hash_params(s_name, &run.label, "", ""),
                source: "backtest",
                metrics,
            }
        })
        .collect();

    [standard, mono].concat()
}

/// Avalia 1 EntrySet contra 1 ExitFn. Hot loop puramente síncrono.
fn run_pairing(entries: &[Entry], candles: &[Candle], f: &ExitFn, pos_size: f64) -> RunMetrics {
    let mut capital = INITIAL_CAPITAL;
    let mut peak    = capital;
    let mut max_dd  = 0.0_f64;
    let mut wins    = 0_usize;
    let mut losses  = 0_usize;
    let mut eods    = 0_usize;
    let mut cl      = 0_usize;
    let mut mcl     = 0_usize;

    for entry in entries {
        let result = evaluate(entry, candles, f);
        let pnl_pct = match entry.direction {
            Direction::Short => (entry.entry_price - result.exit_price) / entry.entry_price,
            Direction::Long  => (result.exit_price - entry.entry_price) / entry.entry_price,
        };
        let size = capital * pos_size;
        let net  = size * pnl_pct - size * FEE_PCT * 2.0;
        capital += net;

        if net > 0.0 { wins += 1; cl = 0; } else { losses += 1; cl += 1; mcl = mcl.max(cl); }
        if result.is_eod { eods += 1; }
        peak = peak.max(capital);
        let dd = if peak > 0.0 { (peak - capital) / peak } else { 0.0 };
        max_dd = max_dd.max(dd);
    }

    RunMetrics {
        trades: entries.len(),
        wins, losses, eods,
        final_capital: capital,
        max_dd_pct: max_dd * 100.0,
        max_consec_loss: mcl,
    }
}

/// MD5 hex(32 chars) de uma representação canônica dos params.
/// Usado em `sweep_results.params_hash` para dedup robusto.
fn hash_params(strategy: &str, s_label: &str, exit: &str, e_label: &str) -> String {
    use md5::{Digest, Md5};
    let mut h = Md5::new();
    h.update(strategy.as_bytes());
    h.update(b"\x00");
    h.update(s_label.as_bytes());
    h.update(b"\x00");
    h.update(exit.as_bytes());
    h.update(b"\x00");
    h.update(e_label.as_bytes());
    format!("{:x}", h.finalize())
}
