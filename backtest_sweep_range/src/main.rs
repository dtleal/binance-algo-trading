/// Range Mode backtest sweep — brute-force parameter grid for mean-reversion range trading.
///
/// Strategy: detect horizontal range (low ADX + low ATR), open BUY near range bottom
/// and SELL near range top. Each position has individual TP/SL as % of range size.
///
/// Usage:
///   cargo run --release -- data/klines/BTCUSDT_5m_klines.csv
///
/// Output: range_sweep.csv  (profitable combos only, sorted descending by return%)

use rayon::prelude::*;
use std::time::Instant;
use std::env;

const FEE_PCT: f64 = 0.0004;       // taker fee (0.04%)
const INITIAL_CAPITAL: f64 = 1000.0;
const ADX_PERIOD: usize = 14;
const ATR_PERIOD: usize = 14;

// ── Parameter grid ──────────────────────────────────────────────────────────

// ADX threshold: range valid when ADX <= this value
const ADX_THRESH_VALUES: &[f64] = &[20.0, 25.0, 30.0];

// ATR% threshold: range valid when ATR/close*100 <= this value
const ATR_PCT_THRESH_VALUES: &[f64] = &[0.3, 0.5, 0.8, 1.0];

// Lookback candles to compute range high/low
const RANGE_LOOKBACK_VALUES: &[usize] = &[20, 30, 50, 80];

// Zone percent: % of range size defining BUY/SELL zones (25 → bottom 25% = BUY, top 25% = SELL)
const ZONE_PCT_VALUES: &[f64] = &[15.0, 20.0, 25.0, 33.0];

// TP as % of range size (e.g. 50 → TP at rangeSize*0.50 from entry)
const TP_RANGE_PCT_VALUES: &[f64] = &[30.0, 40.0, 50.0, 60.0, 70.0];

// SL as % of range size (0 = disabled)
const SL_RANGE_PCT_VALUES: &[f64] = &[0.0, 20.0, 30.0, 40.0, 50.0];

// Anti-duplicate threshold as % of range (has_recent_order)
const RECENT_ORDER_THRESH_PCT: &[f64] = &[1.0, 2.0, 3.0];

// Max concurrent positions
const MAX_ORDERS_VALUES: &[usize] = &[2, 4, 6];

// Position size as % of capital (notional / entry_price * leverage)
const POS_SIZE_VALUES: &[f64] = &[0.05, 0.10, 0.20];

// ── Data structures ──────────────────────────────────────────────────────────

#[derive(Clone)]
struct Candle {
    high:           f64,
    low:            f64,
    close:          f64,
    adx:            f64,    // precomputed ADX(14)
    atr_pct:        f64,    // precomputed ATR(14) / close * 100
}

#[derive(Clone)]
struct RunResult {
    adx_thresh:       f64,
    atr_pct_thresh:   f64,
    range_lookback:   usize,
    zone_pct:         f64,
    tp_range_pct:     f64,
    sl_range_pct:     f64,
    recent_thresh_pct:f64,
    max_orders:       usize,
    pos_size_pct:     f64,
    trades:           usize,
    wins:             usize,
    losses:           usize,
    win_rate:         f64,
    return_pct:       f64,
    final_capital:    f64,
    max_dd_pct:       f64,
    max_consec_loss:  usize,
    avg_positions_per_range: f64,
}

// ── Indicator computation ───────────────────────────────────────────────────

fn wilder_smooth(values: &[f64], period: usize) -> Vec<f64> {
    if values.len() < period { return vec![]; }
    let init = values[..period].iter().sum::<f64>() / period as f64;
    let mut result = vec![init];
    let k = 1.0 / period as f64;
    for &v in &values[period..] {
        let prev = *result.last().unwrap();
        result.push(prev * (1.0 - k) + v * k);
    }
    result
}

fn precompute_atr(highs: &[f64], lows: &[f64], closes: &[f64], period: usize) -> Vec<f64> {
    let n = highs.len();
    let mut atr_vals = vec![0.0f64; n];
    if n < period + 1 { return atr_vals; }

    let mut trs: Vec<f64> = (1..n)
        .map(|i| {
            let tr = (highs[i] - lows[i])
                .max((highs[i] - closes[i - 1]).abs())
                .max((lows[i] - closes[i - 1]).abs());
            tr
        })
        .collect();

    let sm = wilder_smooth(&trs, period);
    // sm[0] corresponds to candle index `period` (0-indexed from the first TR)
    for (j, &v) in sm.iter().enumerate() {
        let candle_idx = j + period;
        if candle_idx < n {
            atr_vals[candle_idx] = v;
        }
    }
    atr_vals
}

fn precompute_adx(highs: &[f64], lows: &[f64], closes: &[f64], period: usize) -> Vec<f64> {
    let n = highs.len();
    let mut adx_vals = vec![0.0f64; n];
    if n < period * 2 + 2 { return adx_vals; }

    let mut plus_dm  = vec![0.0f64; n - 1];
    let mut minus_dm = vec![0.0f64; n - 1];
    let mut trs      = vec![0.0f64; n - 1];

    for i in 1..n {
        let h_diff = highs[i] - highs[i - 1];
        let l_diff = lows[i - 1] - lows[i];
        plus_dm[i - 1]  = if h_diff > l_diff && h_diff > 0.0 { h_diff } else { 0.0 };
        minus_dm[i - 1] = if l_diff > h_diff && l_diff > 0.0 { l_diff } else { 0.0 };
        trs[i - 1] = (highs[i] - lows[i])
            .max((highs[i] - closes[i - 1]).abs())
            .max((lows[i] - closes[i - 1]).abs());
    }

    let sm_tr    = wilder_smooth(&trs,      period);
    let sm_plus  = wilder_smooth(&plus_dm,  period);
    let sm_minus = wilder_smooth(&minus_dm, period);

    let len = sm_tr.len().min(sm_plus.len()).min(sm_minus.len());
    let mut dx_vals = Vec::with_capacity(len);
    for i in 0..len {
        let t = sm_tr[i];
        if t == 0.0 { dx_vals.push(0.0); continue; }
        let pdi = 100.0 * sm_plus[i]  / t;
        let mdi = 100.0 * sm_minus[i] / t;
        let dx  = if pdi + mdi > 0.0 { 100.0 * (pdi - mdi).abs() / (pdi + mdi) } else { 0.0 };
        dx_vals.push(dx);
    }

    let sm_adx = wilder_smooth(&dx_vals, period);
    // sm_adx[0] starts at candle index: 1 (first TR) + period-1 (first smooth) + period-1 (adx smooth) + 1
    let start_candle = 1 + (period - 1) + (period - 1) + period;
    for (j, &v) in sm_adx.iter().enumerate() {
        let idx = start_candle + j;
        if idx < n { adx_vals[idx] = v; }
    }
    adx_vals
}

// ── CSV loading ─────────────────────────────────────────────────────────────

fn load_csv(path: &str) -> Vec<Candle> {
    let mut rdr = csv::Reader::from_path(path).expect("Cannot open CSV");
    let mut highs: Vec<f64>  = Vec::with_capacity(300_000);
    let mut lows:  Vec<f64>  = Vec::with_capacity(300_000);
    let mut closes: Vec<f64> = Vec::with_capacity(300_000);

    for rec in rdr.records() {
        let rec = rec.expect("Bad row");
        highs.push(rec[2].parse().unwrap());
        lows.push(rec[3].parse().unwrap());
        closes.push(rec[4].parse().unwrap());
    }

    let n = highs.len();
    println!("  {} candles loaded", n);

    let atr_raw = precompute_atr(&highs, &lows, &closes, ATR_PERIOD);
    let adx_raw = precompute_adx(&highs, &lows, &closes, ADX_PERIOD);

    (0..n).map(|i| {
        let atr_pct = if closes[i] > 0.0 { atr_raw[i] / closes[i] * 100.0 } else { 0.0 };
        Candle {
            high:    highs[i],
            low:     lows[i],
            close:   closes[i],
            adx:     adx_raw[i],
            atr_pct,
        }
    }).collect()
}

// ── Range detection ──────────────────────────────────────────────────────────

#[derive(Clone, Copy)]
struct Range {
    high: f64,
    low:  f64,
    size: f64,
}

fn detect_range(candles: &[Candle], end_idx: usize, lookback: usize) -> Option<Range> {
    if end_idx < lookback { return None; }
    let start = end_idx - lookback;
    let slice = &candles[start..end_idx];
    let high = slice.iter().map(|c| c.high).fold(f64::NEG_INFINITY, f64::max);
    let low  = slice.iter().map(|c| c.low ).fold(f64::INFINITY,     f64::min);
    let size = high - low;
    if size <= 0.0 { return None; }
    Some(Range { high, low, size })
}

// ── Price zone ───────────────────────────────────────────────────────────────

#[derive(Clone, Copy, PartialEq)]
enum Zone { Buy, Sell, Neutral }

fn price_zone(price: f64, range: &Range, zone_pct: f64) -> Zone {
    let zone_height = range.size * zone_pct / 100.0;
    if price <= range.low + zone_height   { return Zone::Buy;  }
    if price >= range.high - zone_height  { return Zone::Sell; }
    Zone::Neutral
}

// ── Backtest simulation ──────────────────────────────────────────────────────

#[derive(Clone)]
struct Position {
    side:        bool,  // true = long (BUY), false = short (SELL)
    entry_price: f64,
    tp_price:    f64,
    sl_price:    f64,   // 0.0 = no SL
    qty_pct:     f64,   // fraction of capital at entry
}

fn run_backtest(
    candles: &[Candle],
    adx_thresh:        f64,
    atr_pct_thresh:    f64,
    range_lookback:    usize,
    zone_pct:          f64,
    tp_range_pct:      f64,
    sl_range_pct:      f64,
    recent_thresh_pct: f64,
    max_orders:        usize,
    pos_size_pct:      f64,
) -> RunResult {
    let n = candles.len();
    let min_history = range_lookback + ADX_PERIOD * 3;
    if n < min_history {
        return empty_result(adx_thresh, atr_pct_thresh, range_lookback, zone_pct,
                            tp_range_pct, sl_range_pct, recent_thresh_pct, max_orders, pos_size_pct);
    }

    let mut capital = INITIAL_CAPITAL;
    let mut peak    = capital;
    let mut max_dd  = 0.0f64;
    let mut wins    = 0usize;
    let mut losses  = 0usize;
    let mut cl      = 0usize;
    let mut mcl     = 0usize;
    let mut positions: Vec<Position> = Vec::new();
    // throttle: only recalculate range every RANGE_THROTTLE candles
    const RANGE_THROTTLE: usize = 12; // ~1min on 5m bars = 12 x 5s ticks; 1 candle = 1 "tick" here
    let mut last_range_calc: usize = 0;
    let mut current_range: Option<Range> = None;
    let mut was_in_range = false;
    let mut total_positions_opened = 0usize;
    let mut range_entries = 0usize; // number of times we entered range mode

    for i in min_history..n {
        let c = &candles[i];

        // ── Step 1: Throttled range recalculation ────────────────────
        if i - last_range_calc >= RANGE_THROTTLE || current_range.is_none() {
            current_range = detect_range(candles, i, range_lookback);
            last_range_calc = i;
        }

        // ── Step 2: Evaluate range mode flag ─────────────────────────
        let in_range = if let Some(ref r) = current_range {
            c.adx     <= adx_thresh
            && c.atr_pct <= atr_pct_thresh
            && r.size  >  0.0
        } else {
            false
        };

        // ── Step 3: Range → exit transition ──────────────────────────
        if was_in_range && !in_range {
            // CloseOnRangeBreak=false by default in sweep: just stop opening
        }
        was_in_range = in_range;

        // ── Step 4: Check existing positions for TP/SL ───────────────
        let mut remaining: Vec<Position> = Vec::new();
        for pos in positions.drain(..) {
            let hit_tp = if pos.side {
                c.high >= pos.tp_price
            } else {
                c.low <= pos.tp_price
            };
            let hit_sl = pos.sl_price > 0.0 && if pos.side {
                c.low <= pos.sl_price
            } else {
                c.high >= pos.sl_price
            };

            if hit_tp || hit_sl {
                let exit_price = if hit_tp { pos.tp_price } else { pos.sl_price };
                let pnl = if pos.side {
                    (exit_price - pos.entry_price) / pos.entry_price
                } else {
                    (pos.entry_price - exit_price) / pos.entry_price
                };
                let size = capital * pos.qty_pct;
                let net  = size * pnl - size * FEE_PCT * 2.0;
                capital += net;
                peak = peak.max(capital);
                let dd = if peak > 0.0 { (peak - capital) / peak } else { 0.0 };
                max_dd = max_dd.max(dd);
                if net > 0.0 { wins += 1; cl = 0; }
                else { losses += 1; cl += 1; mcl = mcl.max(cl); }
            } else {
                remaining.push(pos);
            }
        }
        positions = remaining;

        if !in_range { continue; }

        let range = match current_range { Some(ref r) => r, None => continue };

        // ── Step 5: Determine zone and open new positions ─────────────
        if positions.len() >= max_orders { continue; }

        let price = c.close;
        let zone  = price_zone(price, range, zone_pct);
        if zone == Zone::Neutral { continue; }

        let is_long = zone == Zone::Buy;

        // has_recent_order check
        let threshold = range.size * recent_thresh_pct / 100.0;
        let has_recent = positions.iter().any(|p| {
            p.side == is_long && (p.entry_price - price).abs() < threshold
        });
        if has_recent { continue; }

        // Calculate TP/SL in absolute prices
        let tp_dist = range.size * tp_range_pct / 100.0;
        let sl_dist = if sl_range_pct > 0.0 { range.size * sl_range_pct / 100.0 } else { 0.0 };

        let (tp_price, sl_price) = if is_long {
            (price + tp_dist, if sl_dist > 0.0 { price - sl_dist } else { 0.0 })
        } else {
            (price - tp_dist, if sl_dist > 0.0 { price + sl_dist } else { 0.0 })
        };

        // Sanity: TP must be reachable within the range
        let tp_inside_range = if is_long { tp_price <= range.high } else { tp_price >= range.low };
        if !tp_inside_range { continue; }

        if capital * pos_size_pct * price <= 0.0 { continue; }

        positions.push(Position {
            side: is_long,
            entry_price: price,
            tp_price,
            sl_price,
            qty_pct: pos_size_pct,
        });
        total_positions_opened += 1;
        if positions.len() == 1 { range_entries += 1; }
    }

    // Close all remaining positions at last close price (EOD-style)
    let last_close = candles.last().map(|c| c.close).unwrap_or(0.0);
    for pos in positions.drain(..) {
        let pnl = if pos.side {
            (last_close - pos.entry_price) / pos.entry_price
        } else {
            (pos.entry_price - last_close) / pos.entry_price
        };
        let size = capital * pos.qty_pct;
        let net  = size * pnl - size * FEE_PCT * 2.0;
        capital += net;
        peak = peak.max(capital);
        let dd = if peak > 0.0 { (peak - capital) / peak } else { 0.0 };
        max_dd = max_dd.max(dd);
        if net > 0.0 { wins += 1; } else { losses += 1; }
    }

    let trades = wins + losses;
    let avg_pos = if range_entries > 0 { total_positions_opened as f64 / range_entries as f64 } else { 0.0 };

    RunResult {
        adx_thresh,
        atr_pct_thresh:    atr_pct_thresh,
        range_lookback,
        zone_pct,
        tp_range_pct,
        sl_range_pct,
        recent_thresh_pct,
        max_orders,
        pos_size_pct:      pos_size_pct * 100.0,
        trades,
        wins,
        losses,
        win_rate:          if trades > 0 { (wins as f64 / trades as f64 * 1000.0).round() / 10.0 } else { 0.0 },
        return_pct:        ((capital / INITIAL_CAPITAL - 1.0) * 10000.0).round() / 100.0,
        final_capital:     (capital * 100.0).round() / 100.0,
        max_dd_pct:        (max_dd * 10000.0).round() / 100.0,
        max_consec_loss:   mcl,
        avg_positions_per_range: (avg_pos * 100.0).round() / 100.0,
    }
}

fn empty_result(
    adx_thresh: f64, atr_pct_thresh: f64, range_lookback: usize,
    zone_pct: f64, tp_range_pct: f64, sl_range_pct: f64,
    recent_thresh_pct: f64, max_orders: usize, pos_size_pct: f64,
) -> RunResult {
    RunResult {
        adx_thresh, atr_pct_thresh, range_lookback, zone_pct,
        tp_range_pct, sl_range_pct, recent_thresh_pct, max_orders,
        pos_size_pct: pos_size_pct * 100.0,
        trades: 0, wins: 0, losses: 0, win_rate: 0.0,
        return_pct: 0.0, final_capital: INITIAL_CAPITAL,
        max_dd_pct: 0.0, max_consec_loss: 0, avg_positions_per_range: 0.0,
    }
}

fn f2(v: f64) -> String { format!("{:.2}", v) }

fn print_header() {
    println!(
        "{:<8} {:<8} {:<9} {:<8} {:<8} {:<8} {:<10} {:<9} {:<9} {:<7} {:<7} {:<6} {:<8} {:<8} {:<9} {:<6}",
        "ADX", "ATR%", "Lookback", "ZonePct", "TP%", "SL%",
        "RecentThr", "MaxOrd", "PosSz%", "Trades", "WinRate",
        "Return%", "MaxDD%", "AvgPos", "FinalCap", "MCL"
    );
    println!("{}", "-".repeat(160));
}

fn print_row(r: &RunResult) {
    println!(
        "{:<8} {:<8} {:<9} {:<8} {:<8} {:<8} {:<10} {:<9} {:<9} {:<7} {:<7} {:<6} {:<8} {:<8} {:<9} {:<6}",
        f2(r.adx_thresh), f2(r.atr_pct_thresh), r.range_lookback,
        f2(r.zone_pct), f2(r.tp_range_pct), f2(r.sl_range_pct),
        f2(r.recent_thresh_pct), r.max_orders, f2(r.pos_size_pct),
        r.trades, f2(r.win_rate), f2(r.return_pct), f2(r.max_dd_pct),
        f2(r.avg_positions_per_range), f2(r.final_capital), r.max_consec_loss
    );
}

// ── Main ──────────────────────────────────────────────────────────────────────

fn main() {
    let args: Vec<String> = env::args().collect();
    let csv_file = if args.len() > 1 { &args[1] } else { "data/klines/btcusdt_5m_klines.csv" };

    let t0 = Instant::now();
    println!("Range Mode Backtest Sweep");
    println!("  Input: {}", csv_file);
    let candles = load_csv(csv_file);

    // Build all parameter combinations
    let mut combos: Vec<(f64, f64, usize, f64, f64, f64, f64, usize, f64)> = Vec::new();
    for &adx in ADX_THRESH_VALUES {
        for &atr in ATR_PCT_THRESH_VALUES {
            for &lb in RANGE_LOOKBACK_VALUES {
                for &zp in ZONE_PCT_VALUES {
                    for &tp in TP_RANGE_PCT_VALUES {
                        for &sl in SL_RANGE_PCT_VALUES {
                            for &rt in RECENT_ORDER_THRESH_PCT {
                                for &mo in MAX_ORDERS_VALUES {
                                    for &ps in POS_SIZE_VALUES {
                                        combos.push((adx, atr, lb, zp, tp, sl, rt, mo, ps));
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    println!("  {} parameter combinations", combos.len());
    println!("  Running parallel sweep with rayon...\n");

    let results: Vec<RunResult> = combos.par_iter().map(|&(adx, atr, lb, zp, tp, sl, rt, mo, ps)| {
        run_backtest(&candles, adx, atr, lb, zp, tp, sl, rt, mo, ps)
    }).collect();

    let elapsed = t0.elapsed();

    // Filter and sort
    let mut profitable: Vec<&RunResult> = results.iter()
        .filter(|r| r.return_pct > 0.0 && r.trades > 0)
        .collect();
    profitable.sort_by(|a, b| b.return_pct.partial_cmp(&a.return_pct).unwrap());

    // Write CSV
    {
        let mut w = csv::Writer::from_path("range_sweep.csv").unwrap();
        w.write_record([
            "adx_thresh", "atr_pct_thresh", "range_lookback", "zone_pct",
            "tp_range_pct", "sl_range_pct", "recent_thresh_pct", "max_orders",
            "pos_size_pct", "trades", "wins", "losses", "win_rate",
            "return_pct", "final_capital", "max_dd_pct", "max_consec_loss",
            "avg_positions_per_range",
        ]).unwrap();
        for r in &profitable {
            w.write_record([
                f2(r.adx_thresh), f2(r.atr_pct_thresh), r.range_lookback.to_string(),
                f2(r.zone_pct), f2(r.tp_range_pct), f2(r.sl_range_pct),
                f2(r.recent_thresh_pct), r.max_orders.to_string(), f2(r.pos_size_pct),
                r.trades.to_string(), r.wins.to_string(), r.losses.to_string(), f2(r.win_rate),
                f2(r.return_pct), f2(r.final_capital), f2(r.max_dd_pct),
                r.max_consec_loss.to_string(), f2(r.avg_positions_per_range),
            ]).unwrap();
        }
        w.flush().unwrap();
        println!("CSV written: range_sweep.csv  ({} profitable combos)", profitable.len());
    }

    // Print top results
    println!("\nDone in {:.2}s — {} total, {} profitable\n", elapsed.as_secs_f64(), results.len(), profitable.len());

    println!("{}", "=".repeat(160));
    println!("  TOP 30 BY RETURN%");
    println!("{}", "=".repeat(160));
    print_header();
    for r in profitable.iter().take(30) { print_row(r); }

    // Top by risk-adjusted return
    let mut by_risk: Vec<(&RunResult, f64)> = profitable.iter()
        .filter(|r| r.trades >= 10)
        .map(|r| (*r, r.return_pct / r.max_dd_pct.max(0.01)))
        .collect();
    by_risk.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
    println!("\n{}", "=".repeat(160));
    println!("  TOP 30 RISK-ADJUSTED (return/maxDD, min 10 trades)");
    println!("{}", "=".repeat(160));
    print_header();
    for (r, ratio) in by_risk.iter().take(30) {
        print_row(r);
        print!("  ratio={:.2}\n", ratio);
    }

    // Top by win rate (min 20 trades)
    let mut by_wr: Vec<&RunResult> = profitable.iter()
        .filter(|r| r.trades >= 20)
        .copied()
        .collect::<Vec<_>>();
    by_wr.sort_by(|a, b| b.win_rate.partial_cmp(&a.win_rate).unwrap());
    println!("\n{}", "=".repeat(160));
    println!("  TOP 20 BY WIN RATE (min 20 trades)");
    println!("{}", "=".repeat(160));
    print_header();
    for r in by_wr.iter().take(20) { print_row(r); }
}
