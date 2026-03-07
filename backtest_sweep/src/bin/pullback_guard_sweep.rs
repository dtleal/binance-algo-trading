use rayon::prelude::*;
use std::env;
use std::time::Instant;

#[path = "../guard_sweep_common.rs"]
mod guard_sweep_common;

use guard_sweep_common::{
    Candle, ENTRY_CUTOFF, ENTRY_START, Entry, evaluate_with_guards, f2,
    find_entries_pullback, group_by_day, load_csv, precompute,
};

const TP_VALUES: &[f64] = &[0.005, 0.01, 0.015, 0.02, 0.03, 0.05, 0.07, 0.10];
const SL_VALUES: &[f64] = &[0.002, 0.004, 0.006, 0.008, 0.01, 0.015, 0.02, 0.05];
const POS_SIZE_VALUES: &[f64] = &[0.10, 0.20];
const MAX_HOLD_VALUES: &[u16] = &[0, 30, 360];
const VWAP_DIST_VALUES: &[f64] = &[0.0, 0.02, 0.03, 0.05];
const TIME_STOP_VALUES: &[u16] = &[0, 20];
const TIME_STOP_PROGRESS_VALUES: &[f64] = &[0.0];
const ADVERSE_EXIT_BARS_VALUES: &[usize] = &[0, 3];
const ADVERSE_BODY_MIN_VALUES: &[f64] = &[0.20];

// Focused grid around the pullback regions that already show edge in the historical sweeps.
const MIN_BARS_VALUES: &[usize] = &[3, 5, 8];
const CONFIRM_BARS_VALUES: &[usize] = &[0, 1, 2];
const VWAP_PROX_VALUES: &[f64] = &[0.002, 0.005];
const ENTRY_WINDOWS: &[(u16, u16)] = &[(60, 1320), (360, 1080)];
const FOCUSED_VWAP_WINDOWS: &[(usize, u32)] = &[(2, 10), (3, 20), (4, 30)];
const FOCUSED_EMA_PERIODS: &[(usize, usize)] = &[(0, 100), (1, 200), (2, 300)];
const MAX_TRADES_PER_DAY_VALUES: &[usize] = &[1, 2];

#[derive(Clone)]
struct EntrySet {
    min_bars: usize,
    confirm_bars: usize,
    entry_start: u16,
    entry_cutoff: u16,
    vwap_prox: f64,
    vwap_window: u32,
    vwap_idx: usize,
    ema_period: usize,
    max_trades_per_day: usize,
    entries: Vec<Entry>,
}

#[derive(Clone)]
struct RunResult {
    tp_pct: f64,
    sl_pct: f64,
    rr_ratio: f64,
    min_bars: usize,
    confirm_bars: usize,
    entry_window: &'static str,
    vwap_prox: f64,
    vwap_window: u32,
    ema_period: usize,
    max_trades_per_day: usize,
    max_hold: u16,
    vwap_dist_stop: f64,
    time_stop_minutes: u16,
    time_stop_min_progress_pct: f64,
    adverse_exit_bars: usize,
    adverse_body_min_pct: f64,
    pos_size_pct: f64,
    trades: usize,
    wins: usize,
    losses: usize,
    eods: usize,
    win_rate: f64,
    return_pct: f64,
    final_capital: f64,
    max_dd_pct: f64,
    max_consec_loss: usize,
}

fn window_label(start: u16, cutoff: u16) -> &'static str {
    if start == ENTRY_START && cutoff == ENTRY_CUTOFF { "01-22" } else { "06-18" }
}

fn ph() {
    println!(
        "  {:>4} {:>4} {:>4} {:>3} {:>2} {:>5} {:>4} {:>3} {:>3} {:>4} {:>4} {:>5} {:>4} {:>4} {:>3} {:>4} {:>4} {:>3} {:>3} {:>3} {:>5} {:>8} {:>8} {:>6} {:>4}",
        "TP", "SL", "R:R", "bar", "cf", "wndw", "prox", "vw", "ema", "mtd", "hold", "vds", "tstp", "tprg", "adv", "adb", "ps", "trd", "win", "eod", "win%", "ret%", "final$", "dd%", "mcl"
    );
}

fn pr(r: &RunResult) {
    let hold = if r.max_hold == 0 { "EOD".to_string() } else { r.max_hold.to_string() };
    let vds = if r.vwap_dist_stop == 0.0 { "-".to_string() } else { f2(r.vwap_dist_stop) };
    let tstop = if r.time_stop_minutes == 0 { "-".to_string() } else { r.time_stop_minutes.to_string() };
    let tprg = if r.time_stop_minutes == 0 { "-".to_string() } else { f2(r.time_stop_min_progress_pct) };
    let adv = if r.adverse_exit_bars == 0 { "-".to_string() } else { r.adverse_exit_bars.to_string() };
    let adb = if r.adverse_exit_bars == 0 { "-".to_string() } else { f2(r.adverse_body_min_pct) };
    println!(
        "  {:>4.1} {:>4.1} {:>4.1} {:>3} {:>2} {:>5} {:>4.1} {:>3} {:>3} {:>4} {:>4} {:>5} {:>4} {:>4} {:>3} {:>4} {:>4.0} {:>3} {:>3} {:>3} {:>4.1}% {:>7.2}% {:>8.2} {:>5.2}% {:>4}",
        r.tp_pct,
        r.sl_pct,
        r.rr_ratio,
        r.min_bars,
        r.confirm_bars,
        r.entry_window,
        r.vwap_prox,
        r.vwap_window,
        r.ema_period,
        r.max_trades_per_day,
        hold,
        vds,
        tstop,
        tprg,
        adv,
        adb,
        r.pos_size_pct,
        r.trades,
        r.wins,
        r.eods,
        r.win_rate,
        r.return_pct,
        r.final_capital,
        r.max_dd_pct,
        r.max_consec_loss
    );
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let csv_file = if args.len() > 1 { &args[1] } else { "../data/klines/ETHUSDT_1m_klines.csv" };

    let t0 = Instant::now();
    println!("Loading {}...", csv_file);
    let mut candles: Vec<Candle> = load_csv(csv_file);
    println!("  {} candles", candles.len());
    precompute(&mut candles);
    let days = group_by_day(&candles);
    println!("  {} days", days.len());
    println!("  Dedicated sweep: VWAPPullback with runtime guards (focused grid)\n");

    println!("Phase 1 — precomputing entry signals...");
    let mut entry_sets = Vec::new();
    for &mb in MIN_BARS_VALUES {
        for &cb in CONFIRM_BARS_VALUES {
            for &vp in VWAP_PROX_VALUES {
                for &(es, ec) in ENTRY_WINDOWS {
                    for &(vw_idx, vw) in FOCUSED_VWAP_WINDOWS {
                        for &(ema_idx, ema_p) in FOCUSED_EMA_PERIODS {
                            for &max_t in MAX_TRADES_PER_DAY_VALUES {
                                let entries = find_entries_pullback(
                                    &candles,
                                    &days,
                                    mb,
                                    cb,
                                    vp,
                                    vw_idx,
                                    ema_idx,
                                    max_t,
                                    es,
                                    ec,
                                );
                                entry_sets.push(EntrySet {
                                    min_bars: mb,
                                    confirm_bars: cb,
                                    entry_start: es,
                                    entry_cutoff: ec,
                                    vwap_prox: vp,
                                    vwap_window: vw,
                                    vwap_idx: vw_idx,
                                    ema_period: ema_p,
                                    max_trades_per_day: max_t,
                                    entries,
                                });
                            }
                        }
                    }
                }
            }
        }
    }
    let nonempty = entry_sets.iter().filter(|e| !e.entries.is_empty()).count();
    println!("  {} entry sets ({} non-empty)", entry_sets.len(), nonempty);

    let combos_per_set = TP_VALUES.len()
        * SL_VALUES.len()
        * POS_SIZE_VALUES.len()
        * MAX_HOLD_VALUES.len()
        * VWAP_DIST_VALUES.len()
        * TIME_STOP_VALUES.len()
        * ADVERSE_EXIT_BARS_VALUES.len();
    println!("\nPhase 2 — evaluating {} combinations (parallel)...", entry_sets.len() * combos_per_set);

    let results: Vec<RunResult> = entry_sets
        .par_iter()
        .flat_map_iter(|es| {
            let mut rows = Vec::with_capacity(combos_per_set);
            for &tp in TP_VALUES {
                for &sl in SL_VALUES {
                    for &ps in POS_SIZE_VALUES {
                        for &mh in MAX_HOLD_VALUES {
                            for &vd in VWAP_DIST_VALUES {
                                for &tsm in TIME_STOP_VALUES {
                                    for &tsp in TIME_STOP_PROGRESS_VALUES {
                                        for &aeb in ADVERSE_EXIT_BARS_VALUES {
                                            for &abm in ADVERSE_BODY_MIN_VALUES {
                                                let n = es.entries.len();
                                                if n == 0 {
                                                    rows.push(RunResult {
                                                        tp_pct: tp * 100.0,
                                                        sl_pct: sl * 100.0,
                                                        rr_ratio: if sl > 0.0 { tp / sl } else { 0.0 },
                                                        min_bars: es.min_bars,
                                                        confirm_bars: es.confirm_bars,
                                                        entry_window: window_label(es.entry_start, es.entry_cutoff),
                                                        vwap_prox: es.vwap_prox * 100.0,
                                                        vwap_window: es.vwap_window,
                                                        ema_period: es.ema_period,
                                                        max_trades_per_day: es.max_trades_per_day,
                                                        max_hold: mh,
                                                        vwap_dist_stop: vd * 100.0,
                                                        time_stop_minutes: tsm,
                                                        time_stop_min_progress_pct: tsp,
                                                        adverse_exit_bars: aeb,
                                                        adverse_body_min_pct: abm,
                                                        pos_size_pct: ps * 100.0,
                                                        trades: 0,
                                                        wins: 0,
                                                        losses: 0,
                                                        eods: 0,
                                                        win_rate: 0.0,
                                                        return_pct: 0.0,
                                                        final_capital: 1000.0,
                                                        max_dd_pct: 0.0,
                                                        max_consec_loss: 0,
                                                    });
                                                    continue;
                                                }

                                                let (w, l, e, fc, md, mc) = evaluate_with_guards(
                                                    &es.entries,
                                                    &candles,
                                                    tp,
                                                    sl,
                                                    ps,
                                                    true,
                                                    false,
                                                    mh,
                                                    vd,
                                                    tsm,
                                                    tsp,
                                                    aeb,
                                                    abm,
                                                    es.vwap_idx,
                                                );
                                                rows.push(RunResult {
                                                    tp_pct: tp * 100.0,
                                                    sl_pct: sl * 100.0,
                                                    rr_ratio: if sl > 0.0 { (tp / sl * 100.0).round() / 100.0 } else { 0.0 },
                                                    min_bars: es.min_bars,
                                                    confirm_bars: es.confirm_bars,
                                                    entry_window: window_label(es.entry_start, es.entry_cutoff),
                                                    vwap_prox: es.vwap_prox * 100.0,
                                                    vwap_window: es.vwap_window,
                                                    ema_period: es.ema_period,
                                                    max_trades_per_day: es.max_trades_per_day,
                                                    max_hold: mh,
                                                    vwap_dist_stop: vd * 100.0,
                                                    time_stop_minutes: tsm,
                                                    time_stop_min_progress_pct: tsp,
                                                    adverse_exit_bars: aeb,
                                                    adverse_body_min_pct: abm,
                                                    pos_size_pct: ps * 100.0,
                                                    trades: n,
                                                    wins: w,
                                                    losses: l,
                                                    eods: e,
                                                    win_rate: (w as f64 / n as f64 * 1000.0).round() / 10.0,
                                                    return_pct: ((fc / 1000.0 - 1.0) * 10000.0).round() / 100.0,
                                                    final_capital: (fc * 100.0).round() / 100.0,
                                                    max_dd_pct: (md * 10000.0).round() / 100.0,
                                                    max_consec_loss: mc,
                                                });
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            rows
        })
        .collect();

    let elapsed = t0.elapsed();

    let mut writer = csv::Writer::from_path("backtest_sweep.csv").unwrap();
    writer.write_record([
        "strategy", "tp_pct", "sl_pct", "rr_ratio", "min_bars", "vol_filter", "confirm_bars",
        "trend_filter", "entry_window", "vwap_prox", "vwap_window", "ema_period", "max_trades_per_day",
        "fast_period", "slow_period", "orb_range_mins", "pdhl_prox_pct", "max_hold", "vwap_dist_stop",
        "time_stop_minutes", "time_stop_min_progress_pct", "adverse_exit_bars", "adverse_body_min_pct",
        "pos_size_pct", "trades", "wins", "losses", "eods", "win_rate", "return_pct", "final_capital",
        "max_dd_pct", "max_consec_loss",
    ]).unwrap();
    let mut csv_rows = 0usize;
    for r in &results {
        if r.return_pct <= 0.0 {
            continue;
        }
        writer.write_record([
            "VWAPPullback".to_string(),
            f2(r.tp_pct),
            f2(r.sl_pct),
            f2(r.rr_ratio),
            r.min_bars.to_string(),
            "false".to_string(),
            r.confirm_bars.to_string(),
            "false".to_string(),
            r.entry_window.to_string(),
            f2(r.vwap_prox),
            format!("{}d", r.vwap_window),
            r.ema_period.to_string(),
            r.max_trades_per_day.to_string(),
            "-".to_string(),
            "-".to_string(),
            "-".to_string(),
            "-".to_string(),
            if r.max_hold == 0 { "EOD".to_string() } else { r.max_hold.to_string() },
            if r.vwap_dist_stop == 0.0 { "-".to_string() } else { f2(r.vwap_dist_stop) },
            if r.time_stop_minutes == 0 { "-".to_string() } else { r.time_stop_minutes.to_string() },
            if r.time_stop_minutes == 0 { "-".to_string() } else { f2(r.time_stop_min_progress_pct) },
            if r.adverse_exit_bars == 0 { "-".to_string() } else { r.adverse_exit_bars.to_string() },
            if r.adverse_exit_bars == 0 { "-".to_string() } else { f2(r.adverse_body_min_pct) },
            format!("{:.0}", r.pos_size_pct),
            r.trades.to_string(),
            r.wins.to_string(),
            r.losses.to_string(),
            r.eods.to_string(),
            format!("{:.1}", r.win_rate),
            f2(r.return_pct),
            f2(r.final_capital),
            f2(r.max_dd_pct),
            r.max_consec_loss.to_string(),
        ]).unwrap();
        csv_rows += 1;
    }
    writer.flush().unwrap();

    let active: Vec<&RunResult> = results.iter().filter(|r| r.trades > 0).collect();
    println!("\n  CSV: {} rows (return>0) -> backtest_sweep.csv", csv_rows);
    println!("Done in {:.2}s — {} total ({} with trades)\n", elapsed.as_secs_f64(), results.len(), active.len());

    let mut by_ret = active.clone();
    by_ret.sort_by(|a, b| b.return_pct.partial_cmp(&a.return_pct).unwrap());
    println!("{}", "=".repeat(160));
    println!("  TOP 20 VWAPPULLBACK GUARD SWEEP BY RETURN %");
    println!("{}", "=".repeat(160));
    ph();
    for r in by_ret.iter().take(20) {
        pr(r);
    }

    let mut by_risk: Vec<_> = active
        .iter()
        .filter(|r| r.trades >= 30 && r.return_pct > 0.0)
        .map(|r| (*r, r.return_pct / r.max_dd_pct.max(0.01)))
        .collect();
    by_risk.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
    println!("\n{}", "=".repeat(160));
    println!("  TOP 20 VWAPPULLBACK GUARD SWEEP RISK-ADJUSTED");
    println!("{}", "=".repeat(160));
    ph();
    for (r, _) in by_risk.iter().take(20) {
        pr(r);
    }

    println!("\n{}", "=".repeat(160));
    println!("  PARAMETER IMPACT (avg return)");
    println!("{}", "=".repeat(160));
    for &vw in &[10u32, 20u32, 30u32] {
        let s: Vec<&&RunResult> = active.iter().filter(|r| r.vwap_window == vw).collect();
        let a = s.iter().map(|r| r.return_pct).sum::<f64>() / s.len().max(1) as f64;
        println!("  vwap_window={:>2}d: avg_ret={:+6.2}% (n={})", vw, a, s.len());
    }
    for &ema in &[100usize, 200usize, 300usize] {
        let s: Vec<&&RunResult> = active.iter().filter(|r| r.ema_period == ema).collect();
        let a = s.iter().map(|r| r.return_pct).sum::<f64>() / s.len().max(1) as f64;
        println!("  ema_period={:>3}: avg_ret={:+6.2}% (n={})", ema, a, s.len());
    }
    for &tsm in TIME_STOP_VALUES {
        let label = if tsm == 0 { "off".to_string() } else { format!("{}m", tsm) };
        let s: Vec<&&RunResult> = active.iter().filter(|r| r.time_stop_minutes == tsm).collect();
        let a = s.iter().map(|r| r.return_pct).sum::<f64>() / s.len().max(1) as f64;
        println!("  time_stop={:>4}: avg_ret={:+6.2}% (n={})", label, a, s.len());
    }
    for &aeb in ADVERSE_EXIT_BARS_VALUES {
        let label = if aeb == 0 { "off".to_string() } else { aeb.to_string() };
        let s: Vec<&&RunResult> = active.iter().filter(|r| r.adverse_exit_bars == aeb).collect();
        let a = s.iter().map(|r| r.return_pct).sum::<f64>() / s.len().max(1) as f64;
        println!("  adverse_bars={:>3}: avg_ret={:+6.2}% (n={})", label, a, s.len());
    }
}
