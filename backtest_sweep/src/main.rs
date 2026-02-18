use rayon::prelude::*;
use std::collections::BTreeMap;
use std::time::Instant;

const CSV_FILE: &str = "../galausdt_1m_klines.csv";
const FEE_PCT: f64 = 0.0004;
const INITIAL_CAPITAL: f64 = 1000.0;
const END_OF_DAY: u16 = 1430; // 23:50

// ── Parameter grid ─────────────────────────────────────────
const STRATEGY_VALUES: &[u8] = &[0, 1, 2, 3]; // RejShort, RejLong, MomShort, MomLong

const TP_VALUES: &[f64] = &[
    0.001, 0.002, 0.003, 0.004, 0.005, 0.006, 0.008,
    0.01, 0.015, 0.02, 0.03, 0.05, 0.07, 0.10,
];
const SL_VALUES: &[f64] = &[
    0.001, 0.002, 0.003, 0.004, 0.006, 0.008,
    0.01, 0.015, 0.02, 0.05,
];
const MIN_BARS_VALUES: &[usize] = &[3, 5, 8, 12, 20, 30];
const VOL_FILTER_VALUES: &[bool] = &[false, true];
const CONFIRM_BARS_VALUES: &[usize] = &[0, 1, 2];
const TREND_FILTER_VALUES: &[bool] = &[false, true];
const POS_SIZE_VALUES: &[f64] = &[0.10, 0.20];
const ENTRY_WINDOWS: &[(u16, u16)] = &[(60, 1320), (360, 1080)]; // (start, cutoff) in minutes
const VWAP_PROX_VALUES: &[f64] = &[0.002, 0.005]; // only for momentum strategies
const MAX_HOLD_VALUES: &[u16] = &[0, 30, 120, 360]; // 0 = EOD

// VWAP rolling window in days
const NUM_VWAP_WINDOWS: usize = 5;
const VWAP_WINDOW_VALUES: &[u32] = &[1, 5, 10, 20, 30];

struct Candle {
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    volume: f64,
    day: u32,
    minute_of_day: u16,
    vwaps: [f64; NUM_VWAP_WINDOWS],
    vol_sma20: f64,
}

#[derive(Clone, Copy)]
struct Entry {
    entry_price: f64,
    entry_minute: u16,
    rest_start: usize, // index into candles array (first bar after entry)
    rest_end: usize,   // exclusive end index
    eod_close: f64,
}

#[derive(Clone)]
struct RunResult {
    strategy: &'static str,
    tp_pct: f64,
    sl_pct: f64,
    rr_ratio: f64,
    min_bars: usize,
    vol_filter: bool,
    confirm_bars: usize,
    trend_filter: bool,
    entry_window: &'static str,
    vwap_prox: f64,
    vwap_window: u32,
    max_hold: u16,
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

#[derive(Clone)]
struct EntrySet {
    strategy: u8,
    min_bars: usize,
    vol_filter: bool,
    confirm_bars: usize,
    trend_filter: bool,
    entry_start: u16,
    entry_cutoff: u16,
    vwap_prox: f64,
    vwap_window: u32,
    vwap_window_idx: usize,
    entries: Vec<Entry>,
}

fn strategy_name(s: u8) -> &'static str {
    match s { 0 => "RejShort", 1 => "RejLong", 2 => "MomShort", 3 => "MomLong", _ => "?" }
}
fn is_short(s: u8) -> bool { s == 0 || s == 2 }
fn is_momentum(s: u8) -> bool { s == 2 || s == 3 }
fn window_label(start: u16, cutoff: u16) -> &'static str {
    if start == 60 && cutoff == 1320 { "01-22" } else { "06-18" }
}

fn load_csv(path: &str) -> Vec<Candle> {
    let mut rdr = csv::Reader::from_path(path).expect("Cannot open CSV");
    let mut candles = Vec::with_capacity(530_000);
    for rec in rdr.records() {
        let rec = rec.expect("Bad row");
        let ot: i64 = rec[0].parse().unwrap();
        let total_secs = ot / 1000;
        candles.push(Candle {
            open: rec[1].parse().unwrap(),
            high: rec[2].parse().unwrap(),
            low: rec[3].parse().unwrap(),
            close: rec[4].parse().unwrap(),
            volume: rec[5].parse().unwrap(),
            day: (total_secs / 86400) as u32,
            minute_of_day: ((total_secs % 86400) / 60) as u16,
            vwaps: [0.0; NUM_VWAP_WINDOWS], vol_sma20: 0.0,
        });
    }
    candles
}

fn precompute(candles: &mut [Candle]) {
    let n = candles.len();

    // Prefix sums of (typical_price * volume) and volume for VWAP
    let mut prefix_pv = vec![0.0f64; n + 1];
    let mut prefix_vol = vec![0.0f64; n + 1];
    for i in 0..n {
        let tp = (candles[i].high + candles[i].low + candles[i].close) / 3.0;
        prefix_pv[i + 1] = prefix_pv[i] + tp * candles[i].volume;
        prefix_vol[i + 1] = prefix_vol[i] + candles[i].volume;
    }

    // Day boundaries: sorted vec of (day_number, first_candle_index)
    let mut day_bounds: Vec<(u32, usize)> = Vec::new();
    let mut cur_day = u32::MAX;
    for (i, c) in candles.iter().enumerate() {
        if c.day != cur_day {
            day_bounds.push((c.day, i));
            cur_day = c.day;
        }
    }

    // Compute VWAPs for each rolling window size
    for (wi, &window_days) in VWAP_WINDOW_VALUES.iter().enumerate() {
        for i in 0..n {
            let cday = candles[i].day;
            let start_day = if window_days <= cday { cday - window_days + 1 } else { 0 };

            // Binary search: first day_bounds entry with day >= start_day
            let pos = day_bounds.partition_point(|&(d, _)| d < start_day);
            let start_idx = if pos < day_bounds.len() { day_bounds[pos].1 } else { 0 };

            let pv = prefix_pv[i + 1] - prefix_pv[start_idx];
            let vol = prefix_vol[i + 1] - prefix_vol[start_idx];
            candles[i].vwaps[wi] = if vol > 0.0 { pv / vol } else { candles[i].close };
        }
    }

    // Volume SMA 20
    let mut vs = 0.0f64;
    for i in 0..n {
        vs += candles[i].volume;
        if i >= 20 { vs -= candles[i - 20].volume; candles[i].vol_sma20 = vs / 20.0; }
        else { candles[i].vol_sma20 = vs / (i + 1) as f64; }
    }
}

fn group_by_day(candles: &[Candle]) -> BTreeMap<u32, Vec<usize>> {
    let mut m: BTreeMap<u32, Vec<usize>> = BTreeMap::new();
    for (i, c) in candles.iter().enumerate() { m.entry(c.day).or_default().push(i); }
    m
}

fn find_entries(
    candles: &[Candle], day_indices: &BTreeMap<u32, Vec<usize>>,
    strategy: u8, min_bars: usize, vol_filter: bool, confirm_bars: usize,
    trend_filter: bool, entry_start: u16, entry_cutoff: u16, vwap_prox: f64,
    vwap_idx: usize,
) -> Vec<Entry> {
    let mut entries = Vec::new();
    for indices in day_indices.values() {
        if indices.is_empty() { continue; }
        let day_open = candles[indices[0]].open;
        let mut counter: usize = 0;
        let mut i = 0;
        while i < indices.len() {
            let idx = indices[i];
            let c = &candles[idx];
            let vwap = c.vwaps[vwap_idx];
            if c.minute_of_day < entry_start { counter = 0; i += 1; continue; }
            if c.minute_of_day >= entry_cutoff { i += 1; continue; }

            let signal = match strategy {
                0 => { // RejShort
                    if c.close > vwap { counter += 1; false }
                    else if counter >= min_bars { counter = 0; true }
                    else { counter = 0; false }
                }
                1 => { // RejLong
                    if c.close < vwap { counter += 1; false }
                    else if counter >= min_bars { counter = 0; true }
                    else { counter = 0; false }
                }
                2 => { // MomShort
                    let pct = (c.close - vwap) / vwap;
                    if pct.abs() <= vwap_prox { counter += 1; false }
                    else if counter >= min_bars && pct < -vwap_prox { counter = 0; true }
                    else { counter = 0; false }
                }
                3 => { // MomLong
                    let pct = (c.close - vwap) / vwap;
                    if pct.abs() <= vwap_prox { counter += 1; false }
                    else if counter >= min_bars && pct > vwap_prox { counter = 0; true }
                    else { counter = 0; false }
                }
                _ => false,
            };
            if !signal { i += 1; continue; }
            if vol_filter && c.volume <= c.vol_sma20 { i += 1; continue; }
            if trend_filter {
                let s = is_short(strategy);
                if s && c.close >= day_open { i += 1; continue; }
                if !s && c.close <= day_open { i += 1; continue; }
            }
            // Confirm bars
            let mut ok = true;
            let mut ci = i;
            for _ in 0..confirm_bars {
                ci += 1;
                if ci >= indices.len() { ok = false; break; }
                let cc = &candles[indices[ci]];
                let cc_vwap = cc.vwaps[vwap_idx];
                if cc.minute_of_day >= entry_cutoff { ok = false; break; }
                let valid = match strategy {
                    0 | 2 => cc.close < cc_vwap,
                    1 | 3 => cc.close > cc_vwap,
                    _ => false,
                };
                if !valid { ok = false; break; }
            }
            if !ok { i += 1; continue; }

            let eidx = indices[ci];
            let ep = candles[eidx].close;
            let em = candles[eidx].minute_of_day;
            let rest = &indices[ci + 1..];
            let mut eod_close = candles[*indices.last().unwrap()].close;
            let rest_start = if rest.is_empty() { 0 } else { rest[0] };
            let mut rest_end = rest_start;
            for &ri in rest {
                let rc = &candles[ri];
                if rc.minute_of_day >= END_OF_DAY { eod_close = rc.close; break; }
                rest_end = ri + 1;
            }
            entries.push(Entry { entry_price: ep, entry_minute: em, rest_start, rest_end, eod_close });
            break;
        }
    }
    entries
}

fn evaluate(entries: &[Entry], candles: &[Candle], tp_pct: f64, sl_pct: f64, pos_size: f64, short: bool, max_hold: u16)
    -> (usize, usize, usize, f64, f64, usize)
{
    let mut capital = INITIAL_CAPITAL;
    let mut peak = capital;
    let mut max_dd = 0.0f64;
    let (mut wins, mut losses, mut eods) = (0usize, 0usize, 0usize);
    let mut cl = 0usize;
    let mut mcl = 0usize;

    for e in entries {
        let (tp_price, sl_price) = if short {
            (e.entry_price * (1.0 - tp_pct), e.entry_price * (1.0 + sl_pct))
        } else {
            (e.entry_price * (1.0 + tp_pct), e.entry_price * (1.0 - sl_pct))
        };

        let mut exit_price = e.eod_close;
        let mut is_eod = true;

        for j in e.rest_start..e.rest_end {
            let c = &candles[j];

            // Max hold check
            if max_hold > 0 && c.minute_of_day >= e.entry_minute + max_hold {
                exit_price = c.close;
                is_eod = false;
                break;
            }

            if short {
                if c.high >= sl_price { exit_price = sl_price; is_eod = false; break; }
                if c.low <= tp_price { exit_price = tp_price; is_eod = false; break; }
            } else {
                if c.low <= sl_price { exit_price = sl_price; is_eod = false; break; }
                if c.high >= tp_price { exit_price = tp_price; is_eod = false; break; }
            }
        }

        let pnl = if short { (e.entry_price - exit_price) / e.entry_price }
                  else { (exit_price - e.entry_price) / e.entry_price };
        let size = capital * pos_size;
        let net = size * pnl - size * FEE_PCT * 2.0;
        capital += net;

        if net > 0.0 { wins += 1; cl = 0; }
        else { losses += 1; cl += 1; mcl = mcl.max(cl); }
        if is_eod { eods += 1; }
        peak = peak.max(capital);
        let dd = if peak > 0.0 { (peak - capital) / peak } else { 0.0 };
        max_dd = max_dd.max(dd);
    }
    (wins, losses, eods, capital, max_dd, mcl)
}

fn main() {
    let t0 = Instant::now();
    println!("Loading {}...", CSV_FILE);
    let mut candles = load_csv(CSV_FILE);
    println!("  {} candles", candles.len());
    precompute(&mut candles);
    let days = group_by_day(&candles);
    println!("  {} days", days.len());
    println!("  VWAP windows: {:?}\n", VWAP_WINDOW_VALUES);

    // Phase 1: entry precomputation
    println!("Phase 1 — precomputing entry signals...");
    let mut entry_sets: Vec<EntrySet> = Vec::new();
    for &strat in STRATEGY_VALUES {
        let prox_vals: &[f64] = if is_momentum(strat) { VWAP_PROX_VALUES } else { &[0.0] };
        for &mb in MIN_BARS_VALUES {
            for &vf in VOL_FILTER_VALUES {
                for &cb in CONFIRM_BARS_VALUES {
                    for &tf in TREND_FILTER_VALUES {
                        for &(es, ec) in ENTRY_WINDOWS {
                            for &vp in prox_vals {
                                for (vw_idx, &vw) in VWAP_WINDOW_VALUES.iter().enumerate() {
                                    let entries = find_entries(
                                        &candles, &days, strat, mb, vf, cb, tf, es, ec, vp, vw_idx,
                                    );
                                    entry_sets.push(EntrySet {
                                        strategy: strat, min_bars: mb, vol_filter: vf,
                                        confirm_bars: cb, trend_filter: tf,
                                        entry_start: es, entry_cutoff: ec,
                                        vwap_prox: vp, vwap_window: vw, vwap_window_idx: vw_idx,
                                        entries,
                                    });
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    let nonempty = entry_sets.iter().filter(|e| !e.entries.is_empty()).count();
    println!("  {} entry sets ({} non-empty)", entry_sets.len(), nonempty);

    // Phase 2: parallel sweep
    let combos_per_set = TP_VALUES.len() * SL_VALUES.len() * POS_SIZE_VALUES.len() * MAX_HOLD_VALUES.len();
    let total = entry_sets.len() * combos_per_set;
    println!("\nPhase 2 — evaluating {} combinations (parallel)...", total);

    let mut combos: Vec<(usize, f64, f64, f64, u16)> = Vec::with_capacity(total);
    for (idx, _) in entry_sets.iter().enumerate() {
        for &tp in TP_VALUES { for &sl in SL_VALUES { for &ps in POS_SIZE_VALUES { for &mh in MAX_HOLD_VALUES {
            combos.push((idx, tp, sl, ps, mh));
        }}}}
    }

    let results: Vec<RunResult> = combos.par_iter().map(|&(idx, tp, sl, ps, mh)| {
        let es = &entry_sets[idx];
        let n = es.entries.len();
        let sname = strategy_name(es.strategy);
        let short = is_short(es.strategy);
        let wl = window_label(es.entry_start, es.entry_cutoff);

        if n == 0 {
            return RunResult {
                strategy: sname, tp_pct: tp*100.0, sl_pct: sl*100.0,
                rr_ratio: if sl > 0.0 { tp/sl } else { 0.0 },
                min_bars: es.min_bars, vol_filter: es.vol_filter,
                confirm_bars: es.confirm_bars, trend_filter: es.trend_filter,
                entry_window: wl, vwap_prox: es.vwap_prox * 100.0,
                vwap_window: es.vwap_window,
                max_hold: mh, pos_size_pct: ps*100.0,
                trades: 0, wins: 0, losses: 0, eods: 0,
                win_rate: 0.0, return_pct: 0.0,
                final_capital: INITIAL_CAPITAL, max_dd_pct: 0.0, max_consec_loss: 0,
            };
        }

        let (w, l, e, fc, md, mc) = evaluate(&es.entries, &candles, tp, sl, ps, short, mh);
        RunResult {
            strategy: sname, tp_pct: tp*100.0, sl_pct: sl*100.0,
            rr_ratio: if sl > 0.0 { (tp/sl*100.0).round()/100.0 } else { 0.0 },
            min_bars: es.min_bars, vol_filter: es.vol_filter,
            confirm_bars: es.confirm_bars, trend_filter: es.trend_filter,
            entry_window: wl, vwap_prox: es.vwap_prox * 100.0,
            vwap_window: es.vwap_window,
            max_hold: mh, pos_size_pct: ps*100.0,
            trades: n, wins: w, losses: l, eods: e,
            win_rate: (w as f64 / n as f64 * 1000.0).round() / 10.0,
            return_pct: ((fc / INITIAL_CAPITAL - 1.0) * 10000.0).round() / 100.0,
            final_capital: (fc * 100.0).round() / 100.0,
            max_dd_pct: (md * 10000.0).round() / 100.0,
            max_consec_loss: mc,
        }
    }).collect();

    let elapsed = t0.elapsed();

    // Write CSV (only combos with trades)
    {
        let mut w = csv::Writer::from_path("../backtest_sweep.csv").unwrap();
        w.write_record(["strategy","tp_pct","sl_pct","rr_ratio","min_bars","vol_filter",
            "confirm_bars","trend_filter","entry_window","vwap_prox","vwap_window",
            "max_hold","pos_size_pct","trades","wins","losses","eods",
            "win_rate","return_pct","final_capital","max_dd_pct","max_consec_loss"]).unwrap();
        let mut csv_rows = 0usize;
        for r in &results {
            if r.trades == 0 { continue; }
            w.write_record([
                r.strategy.to_string(), f2(r.tp_pct), f2(r.sl_pct), f2(r.rr_ratio),
                r.min_bars.to_string(), r.vol_filter.to_string(),
                r.confirm_bars.to_string(), r.trend_filter.to_string(),
                r.entry_window.to_string(), f2(r.vwap_prox),
                format!("{}d", r.vwap_window),
                if r.max_hold == 0 { "EOD".to_string() } else { r.max_hold.to_string() },
                format!("{:.0}", r.pos_size_pct),
                r.trades.to_string(), r.wins.to_string(), r.losses.to_string(), r.eods.to_string(),
                format!("{:.1}", r.win_rate), f2(r.return_pct), f2(r.final_capital),
                f2(r.max_dd_pct), r.max_consec_loss.to_string(),
            ]).unwrap();
            csv_rows += 1;
        }
        w.flush().unwrap();
        println!("\n  CSV: {} rows (trades>0) -> backtest_sweep.csv", csv_rows);
    }

    let active: Vec<&RunResult> = results.iter().filter(|r| r.trades > 0).collect();
    println!("Done in {:.2}s — {} total ({} with trades)\n",
             elapsed.as_secs_f64(), results.len(), active.len());

    // ── TOP 30 BY RETURN ───────────────────────────────────
    let mut by_ret: Vec<&RunResult> = active.clone();
    by_ret.sort_by(|a, b| b.return_pct.partial_cmp(&a.return_pct).unwrap());
    println!("{}", "=".repeat(170));
    println!("  TOP 30 BY RETURN %");
    println!("{}", "=".repeat(170));
    ph(); for r in by_ret.iter().take(30) { pr(r); }

    // ── TOP 30 RISK-ADJUSTED ───────────────────────────────
    let mut by_risk: Vec<_> = active.iter()
        .filter(|r| r.trades >= 15 && r.return_pct > 0.0)
        .map(|r| (*r, r.return_pct / r.max_dd_pct.max(0.01)))
        .collect();
    by_risk.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
    println!("\n{}", "=".repeat(170));
    println!("  TOP 30 RISK-ADJUSTED (return/maxDD, min 15 trades, positive)");
    println!("{}", "=".repeat(170));
    ph2(); for (r, ratio) in by_risk.iter().take(30) { pr2(r, *ratio); }

    // ── TOP 20 BY WIN RATE ─────────────────────────────────
    let mut by_wr: Vec<&RunResult> = active.iter()
        .filter(|r| r.trades >= 30 && r.return_pct > 0.0).copied().collect();
    by_wr.sort_by(|a, b| b.win_rate.partial_cmp(&a.win_rate).unwrap());
    println!("\n{}", "=".repeat(170));
    println!("  TOP 20 BY WIN RATE (profitable, min 30 trades)");
    println!("{}", "=".repeat(170));
    ph(); for r in by_wr.iter().take(20) { pr(r); }

    // ── Per-strategy summary ───────────────────────────────
    println!("\n{}", "=".repeat(170));
    println!("  PER-STRATEGY SUMMARY");
    println!("{}", "=".repeat(170));
    for &s in STRATEGY_VALUES {
        let nm = strategy_name(s);
        let sub: Vec<&&RunResult> = active.iter().filter(|r| r.strategy == nm).collect();
        if sub.is_empty() { continue; }
        let n = sub.len();
        let avg = sub.iter().map(|r| r.return_pct).sum::<f64>() / n as f64;
        let prof = sub.iter().filter(|r| r.return_pct > 0.0).count();
        let best = sub.iter().max_by(|a, b| a.return_pct.partial_cmp(&b.return_pct).unwrap()).unwrap();
        println!("  {:>10}  n={:6}  avg_ret={:+6.2}%  profitable={:5} ({:.1}%)  best={:+.2}%",
                 nm, n, avg, prof, prof as f64/n as f64*100.0, best.return_pct);
    }

    // ── Param impact ───────────────────────────────────────
    println!("\n{}", "=".repeat(170));
    println!("  PARAMETER IMPACT (avg return)");
    println!("{}", "=".repeat(170));
    impact_bool(&active, "vol_filter", |r| r.vol_filter);
    impact_bool(&active, "trend_filter", |r| r.trend_filter);
    for &cb in CONFIRM_BARS_VALUES {
        let s: Vec<&&RunResult> = active.iter().filter(|r| r.confirm_bars == cb).collect();
        let a = s.iter().map(|r| r.return_pct).sum::<f64>() / s.len().max(1) as f64;
        println!("  confirm_bars={}: avg_ret={:+6.2}%  (n={})", cb, a, s.len());
    }
    for &(es, ec) in ENTRY_WINDOWS {
        let wl = window_label(es, ec);
        let s: Vec<&&RunResult> = active.iter().filter(|r| r.entry_window == wl).collect();
        let a = s.iter().map(|r| r.return_pct).sum::<f64>() / s.len().max(1) as f64;
        println!("  window={}: avg_ret={:+6.2}%  (n={})", wl, a, s.len());
    }
    for &mh in MAX_HOLD_VALUES {
        let label = if mh == 0 { "EOD".to_string() } else { format!("{}m", mh) };
        let s: Vec<&&RunResult> = active.iter().filter(|r| r.max_hold == mh).collect();
        let a = s.iter().map(|r| r.return_pct).sum::<f64>() / s.len().max(1) as f64;
        println!("  max_hold={:>4}: avg_ret={:+6.2}%  (n={})", label, a, s.len());
    }
    for &vw in VWAP_WINDOW_VALUES {
        let s: Vec<&&RunResult> = active.iter().filter(|r| r.vwap_window == vw).collect();
        let a = s.iter().map(|r| r.return_pct).sum::<f64>() / s.len().max(1) as f64;
        println!("  vwap_window={:>2}d: avg_ret={:+6.2}%  (n={})", vw, a, s.len());
    }
}

fn impact_bool(active: &[&RunResult], label: &str, pred: fn(&RunResult) -> bool) {
    let on: Vec<&&RunResult> = active.iter().filter(|r| pred(r)).collect();
    let off: Vec<&&RunResult> = active.iter().filter(|r| !pred(r)).collect();
    let a_on = on.iter().map(|r| r.return_pct).sum::<f64>() / on.len().max(1) as f64;
    let a_off = off.iter().map(|r| r.return_pct).sum::<f64>() / off.len().max(1) as f64;
    println!("  {:>14} OFF={:+6.2}% (n={})  ON={:+6.2}% (n={})", label, a_off, off.len(), a_on, on.len());
}

fn f2(v: f64) -> String { format!("{:.2}", v) }

fn ph() {
    println!("  {:>8} {:>5} {:>5} {:>5} {:>3} {:>3} {:>2} {:>3} {:>5} {:>4} {:>3} {:>4} {:>3} {:>4} {:>3} {:>3} {:>3} {:>5} {:>8} {:>9} {:>6} {:>4}",
        "strat","TP%","SL%","R:R","bar","vf","cf","tf","wndw","prox","vwD","hold","ps%",
        "trd","win","los","eod","win%","return%","final$","mxDD%","mCL");
}
fn pr(r: &RunResult) {
    let mh = if r.max_hold == 0 { "EOD".to_string() } else { format!("{}", r.max_hold) };
    println!("  {:>8} {:>5.2} {:>5.2} {:>5.1} {:>3} {:>3} {:>2} {:>3} {:>5} {:>4.1} {:>3} {:>4} {:>3.0} {:>4} {:>3} {:>3} {:>3} {:>4.1}% {:>7.2}% {:>9.2} {:>5.2}% {:>4}",
        r.strategy, r.tp_pct, r.sl_pct, r.rr_ratio, r.min_bars, r.vol_filter,
        r.confirm_bars, r.trend_filter, r.entry_window, r.vwap_prox, r.vwap_window, mh, r.pos_size_pct,
        r.trades, r.wins, r.losses, r.eods,
        r.win_rate, r.return_pct, r.final_capital, r.max_dd_pct, r.max_consec_loss);
}
fn ph2() {
    println!("  {:>8} {:>5} {:>5} {:>5} {:>3} {:>3} {:>2} {:>3} {:>5} {:>4} {:>3} {:>4} {:>3} {:>4} {:>3} {:>3} {:>3} {:>5} {:>8} {:>9} {:>6} {:>4} {:>7}",
        "strat","TP%","SL%","R:R","bar","vf","cf","tf","wndw","prox","vwD","hold","ps%",
        "trd","win","los","eod","win%","return%","final$","mxDD%","mCL","ret/dd");
}
fn pr2(r: &RunResult, ratio: f64) {
    let mh = if r.max_hold == 0 { "EOD".to_string() } else { format!("{}", r.max_hold) };
    println!("  {:>8} {:>5.2} {:>5.2} {:>5.1} {:>3} {:>3} {:>2} {:>3} {:>5} {:>4.1} {:>3} {:>4} {:>3.0} {:>4} {:>3} {:>3} {:>3} {:>4.1}% {:>7.2}% {:>9.2} {:>5.2}% {:>4} {:>7.2}",
        r.strategy, r.tp_pct, r.sl_pct, r.rr_ratio, r.min_bars, r.vol_filter,
        r.confirm_bars, r.trend_filter, r.entry_window, r.vwap_prox, r.vwap_window, mh, r.pos_size_pct,
        r.trades, r.wins, r.losses, r.eods,
        r.win_rate, r.return_pct, r.final_capital, r.max_dd_pct, r.max_consec_loss, ratio);
}
