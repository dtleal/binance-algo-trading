use std::collections::BTreeMap;

pub const FEE_PCT: f64 = 0.0004;
pub const INITIAL_CAPITAL: f64 = 1000.0;
pub const END_OF_DAY: u16 = 1430; // 23:50 UTC
pub const ENTRY_START: u16 = 60; // 01:00 UTC
pub const ENTRY_CUTOFF: u16 = 1320; // 22:00 UTC

pub const NUM_VWAP_WINDOWS: usize = 5;
pub const VWAP_WINDOW_VALUES: &[u32] = &[1, 5, 10, 20, 30];

pub const NUM_EMA_PERIODS: usize = 4;
pub const EMA_PERIODS: &[usize] = &[100, 200, 300, 500];

#[derive(Clone)]
pub struct Candle {
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
    pub day: u32,
    pub minute_of_day: u16,
    pub vwaps: [f64; NUM_VWAP_WINDOWS],
    pub emas: [f64; NUM_EMA_PERIODS],
}

#[derive(Clone, Copy)]
pub struct Entry {
    pub entry_price: f64,
    pub entry_minute: u16,
    pub rest_start: usize,
    pub rest_end: usize,
    pub eod_close: f64,
    pub is_long: bool,
}

pub fn load_csv(path: &str) -> Vec<Candle> {
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
            vwaps: [0.0; NUM_VWAP_WINDOWS],
            emas: [0.0; NUM_EMA_PERIODS],
        });
    }
    candles
}

pub fn precompute(candles: &mut [Candle]) {
    let n = candles.len();

    let mut prefix_pv = vec![0.0f64; n + 1];
    let mut prefix_vol = vec![0.0f64; n + 1];
    for i in 0..n {
        let tp = (candles[i].high + candles[i].low + candles[i].close) / 3.0;
        prefix_pv[i + 1] = prefix_pv[i] + tp * candles[i].volume;
        prefix_vol[i + 1] = prefix_vol[i] + candles[i].volume;
    }

    let mut day_bounds: Vec<(u32, usize)> = Vec::new();
    let mut cur_day = u32::MAX;
    for (i, c) in candles.iter().enumerate() {
        if c.day != cur_day {
            day_bounds.push((c.day, i));
            cur_day = c.day;
        }
    }

    for (wi, &window_days) in VWAP_WINDOW_VALUES.iter().enumerate() {
        for i in 0..n {
            let cday = candles[i].day;
            let start_day = if window_days <= cday { cday - window_days + 1 } else { 0 };
            let pos = day_bounds.partition_point(|&(d, _)| d < start_day);
            let start_idx = if pos < day_bounds.len() { day_bounds[pos].1 } else { 0 };

            let pv = prefix_pv[i + 1] - prefix_pv[start_idx];
            let vol = prefix_vol[i + 1] - prefix_vol[start_idx];
            candles[i].vwaps[wi] = if vol > 0.0 { pv / vol } else { candles[i].close };
        }
    }

    for (ei, &period) in EMA_PERIODS.iter().enumerate() {
        let k = 2.0 / (period as f64 + 1.0);
        let mut ema = candles[0].close;
        for i in 0..n {
            let c = candles[i].close;
            ema = c * k + ema * (1.0 - k);
            candles[i].emas[ei] = if i + 1 >= period { ema } else { f64::NAN };
        }
    }
}

pub fn group_by_day(candles: &[Candle]) -> BTreeMap<u32, Vec<usize>> {
    let mut m: BTreeMap<u32, Vec<usize>> = BTreeMap::new();
    for (i, c) in candles.iter().enumerate() {
        m.entry(c.day).or_default().push(i);
    }
    m
}

#[allow(dead_code)]
pub fn find_entries_pullback(
    candles: &[Candle],
    day_indices: &BTreeMap<u32, Vec<usize>>,
    min_bars: usize,
    confirm_bars: usize,
    vwap_prox: f64,
    vwap_idx: usize,
    ema_idx: usize,
    max_trades_per_day: usize,
    entry_start: u16,
    entry_cutoff: u16,
) -> Vec<Entry> {
    let mut entries = Vec::new();
    for indices in day_indices.values() {
        if indices.is_empty() {
            continue;
        }
        let mut counter = 0usize;
        let mut confirming = false;
        let mut confirm_count = 0usize;
        let mut pending_long = false;
        let mut trades_today = 0usize;
        let mut i = 0usize;

        while i < indices.len() {
            if max_trades_per_day > 0 && trades_today >= max_trades_per_day {
                break;
            }
            let idx = indices[i];
            let c = &candles[idx];
            let vwap = c.vwaps[vwap_idx];
            let ema = c.emas[ema_idx];

            if c.minute_of_day < entry_start {
                counter = 0;
                i += 1;
                continue;
            }
            if c.minute_of_day >= entry_cutoff {
                i += 1;
                continue;
            }
            if ema.is_nan() {
                i += 1;
                continue;
            }

            let trend_up = c.close > ema;
            let pct = (c.close - vwap) / vwap;

            if confirming {
                let confirmed = if pending_long { c.close > vwap } else { c.close < vwap };
                if confirmed {
                    confirm_count += 1;
                    if confirm_count >= confirm_bars {
                        let ep = c.close;
                        let em = c.minute_of_day;
                        let rest = &indices[i + 1..];
                        let mut eod_close = candles[*indices.last().unwrap()].close;
                        let rest_start = if rest.is_empty() { 0 } else { rest[0] };
                        let mut rest_end = rest_start;
                        for &ri in rest {
                            let rc = &candles[ri];
                            if rc.minute_of_day >= END_OF_DAY {
                                eod_close = rc.close;
                                rest_end = ri;
                                break;
                            }
                            rest_end = ri + 1;
                        }
                        entries.push(Entry {
                            entry_price: ep,
                            entry_minute: em,
                            rest_start,
                            rest_end,
                            eod_close,
                            is_long: pending_long,
                        });
                        trades_today += 1;
                        counter = 0;
                        confirming = false;
                        confirm_count = 0;
                        i = if rest_end > i { rest_end.saturating_sub(indices[0]) } else { i + 1 };
                        continue;
                    }
                } else {
                    confirming = false;
                    confirm_count = 0;
                    counter = 0;
                }
                i += 1;
                continue;
            }

            if pct.abs() <= vwap_prox {
                counter += 1;
            } else if counter >= min_bars {
                let breakout_long = trend_up && pct > vwap_prox;
                let breakout_short = !trend_up && pct < -vwap_prox;

                if breakout_long || breakout_short {
                    counter = 0;
                    pending_long = breakout_long;

                    if confirm_bars == 0 {
                        let ep = c.close;
                        let em = c.minute_of_day;
                        let rest = &indices[i + 1..];
                        let mut eod_close = candles[*indices.last().unwrap()].close;
                        let rest_start = if rest.is_empty() { 0 } else { rest[0] };
                        let mut rest_end = rest_start;
                        for &ri in rest {
                            let rc = &candles[ri];
                            if rc.minute_of_day >= END_OF_DAY {
                                eod_close = rc.close;
                                rest_end = ri;
                                break;
                            }
                            rest_end = ri + 1;
                        }
                        entries.push(Entry {
                            entry_price: ep,
                            entry_minute: em,
                            rest_start,
                            rest_end,
                            eod_close,
                            is_long: pending_long,
                        });
                        trades_today += 1;
                        counter = 0;
                        i = if rest_end > i { rest_end.saturating_sub(indices[0]) } else { i + 1 };
                        continue;
                    } else {
                        confirming = true;
                        confirm_count = 0;
                    }
                } else {
                    counter = 0;
                }
            } else {
                counter = 0;
            }

            i += 1;
        }
    }
    entries
}

#[allow(dead_code)]
pub fn find_entries_pdhl(
    candles: &[Candle],
    day_indices: &BTreeMap<u32, Vec<usize>>,
    prox_pct: f64,
    confirm_bars: usize,
) -> Vec<Entry> {
    let mut entries = Vec::new();
    let mut pdh = f64::NAN;
    let mut pdl = f64::NAN;
    let mut day_high = f64::NEG_INFINITY;
    let mut day_low = f64::INFINITY;

    for indices in day_indices.values() {
        if indices.is_empty() {
            continue;
        }
        if day_high != f64::NEG_INFINITY {
            pdh = day_high;
            pdl = day_low;
        }
        day_high = f64::NEG_INFINITY;
        day_low = f64::INFINITY;

        if pdh.is_nan() {
            for &idx in indices {
                let c = &candles[idx];
                if c.high > day_high {
                    day_high = c.high;
                }
                if c.low < day_low {
                    day_low = c.low;
                }
            }
            continue;
        }

        let mut testing_pdh = false;
        let mut testing_pdl = false;
        let mut pdh_conf = 0usize;
        let mut pdl_conf = 0usize;
        let mut trades_today = 0usize;

        for (local_i, &idx) in indices.iter().enumerate() {
            let c = &candles[idx];
            if c.high > day_high {
                day_high = c.high;
            }
            if c.low < day_low {
                day_low = c.low;
            }
            if c.minute_of_day < ENTRY_START || c.minute_of_day >= ENTRY_CUTOFF {
                continue;
            }
            if trades_today >= 4 {
                break;
            }

            if c.high >= pdh * (1.0 - prox_pct) {
                testing_pdh = true;
            }
            if testing_pdh && c.close < pdh * (1.0 - prox_pct) {
                pdh_conf += 1;
                if pdh_conf >= confirm_bars {
                    let ep = c.close;
                    let em = c.minute_of_day;
                    let rest = &indices[local_i + 1..];
                    let mut eod_close = candles[*indices.last().unwrap()].close;
                    let rest_start = if rest.is_empty() { 0 } else { rest[0] };
                    let mut rest_end = rest_start;
                    for &ri in rest {
                        let rc = &candles[ri];
                        if rc.minute_of_day >= END_OF_DAY {
                            eod_close = rc.close;
                            rest_end = ri;
                            break;
                        }
                        rest_end = ri + 1;
                    }
                    entries.push(Entry {
                        entry_price: ep,
                        entry_minute: em,
                        rest_start,
                        rest_end,
                        eod_close,
                        is_long: false,
                    });
                    trades_today += 1;
                    testing_pdh = false;
                    pdh_conf = 0;
                }
            } else if testing_pdh && c.close >= pdh * (1.0 - prox_pct) {
                pdh_conf = 0;
            } else if testing_pdh && c.close > pdh * (1.0 + prox_pct) {
                testing_pdh = false;
                pdh_conf = 0;
            }

            if c.low <= pdl * (1.0 + prox_pct) {
                testing_pdl = true;
            }
            if testing_pdl && c.close > pdl * (1.0 + prox_pct) {
                pdl_conf += 1;
                if pdl_conf >= confirm_bars {
                    let ep = c.close;
                    let em = c.minute_of_day;
                    let rest = &indices[local_i + 1..];
                    let mut eod_close = candles[*indices.last().unwrap()].close;
                    let rest_start = if rest.is_empty() { 0 } else { rest[0] };
                    let mut rest_end = rest_start;
                    for &ri in rest {
                        let rc = &candles[ri];
                        if rc.minute_of_day >= END_OF_DAY {
                            eod_close = rc.close;
                            rest_end = ri;
                            break;
                        }
                        rest_end = ri + 1;
                    }
                    entries.push(Entry {
                        entry_price: ep,
                        entry_minute: em,
                        rest_start,
                        rest_end,
                        eod_close,
                        is_long: true,
                    });
                    trades_today += 1;
                    testing_pdl = false;
                    pdl_conf = 0;
                }
            } else if testing_pdl && c.close <= pdl * (1.0 + prox_pct) {
                pdl_conf = 0;
            } else if testing_pdl && c.close < pdl * (1.0 - prox_pct) {
                testing_pdl = false;
                pdl_conf = 0;
            }
        }
    }
    entries
}

#[allow(clippy::too_many_arguments)]
#[derive(Clone, Copy)]
pub struct GuardEvalStats {
    pub wins: usize,
    pub losses: usize,
    pub eods: usize,
    pub final_capital: f64,
    pub max_dd: f64,
    pub max_consec_loss: usize,
    pub avg_hold_minutes: f64,
    pub eod_ratio_pct: f64,
}

#[allow(clippy::too_many_arguments)]
pub fn evaluate_with_guards_stats(
    entries: &[Entry],
    candles: &[Candle],
    tp_pct: f64,
    sl_pct: f64,
    pos_size: f64,
    use_entry_direction: bool,
    default_short: bool,
    max_hold: u16,
    vwap_dist_stop: f64,
    time_stop_minutes: u16,
    time_stop_min_progress_pct: f64,
    adverse_exit_bars: usize,
    adverse_body_min_pct: f64,
    vwap_idx: usize,
) -> GuardEvalStats {
    let mut capital = INITIAL_CAPITAL;
    let mut peak = capital;
    let mut max_dd = 0.0f64;
    let (mut wins, mut losses, mut eods) = (0usize, 0usize, 0usize);
    let mut cl = 0usize;
    let mut mcl = 0usize;
    let mut total_hold_minutes = 0u64;

    for e in entries {
        let short = if use_entry_direction { !e.is_long } else { default_short };
        let (tp_price, sl_price) = if short {
            (e.entry_price * (1.0 - tp_pct), e.entry_price * (1.0 + sl_pct))
        } else {
            (e.entry_price * (1.0 + tp_pct), e.entry_price * (1.0 - sl_pct))
        };

        let mut exit_price = e.eod_close;
        let mut is_eod = true;
        let mut exit_minute = END_OF_DAY;
        let mut adverse_count = 0usize;

        for j in e.rest_start..e.rest_end {
            let c = &candles[j];

            if max_hold > 0 && c.minute_of_day >= e.entry_minute.saturating_add(max_hold) {
                exit_price = c.close;
                is_eod = false;
                exit_minute = c.minute_of_day;
                break;
            }

            if vwap_dist_stop > 0.0 {
                let vwap = c.vwaps[vwap_idx];
                if vwap > 0.0 {
                    let dist = (c.close - vwap) / vwap;
                    let too_far = if short { dist > vwap_dist_stop } else { dist < -vwap_dist_stop };
                    if too_far {
                        exit_price = c.close;
                        is_eod = false;
                        exit_minute = c.minute_of_day;
                        break;
                    }
                }
            }

            if short {
                if c.high >= sl_price {
                    exit_price = sl_price;
                    is_eod = false;
                    exit_minute = c.minute_of_day;
                    break;
                }
                if c.low <= tp_price {
                    exit_price = tp_price;
                    is_eod = false;
                    exit_minute = c.minute_of_day;
                    break;
                }
            } else {
                if c.low <= sl_price {
                    exit_price = sl_price;
                    is_eod = false;
                    exit_minute = c.minute_of_day;
                    break;
                }
                if c.high >= tp_price {
                    exit_price = tp_price;
                    is_eod = false;
                    exit_minute = c.minute_of_day;
                    break;
                }
            }

            let pnl_pct = if short {
                (e.entry_price - c.close) / e.entry_price * 100.0
            } else {
                (c.close - e.entry_price) / e.entry_price * 100.0
            };

            if time_stop_minutes > 0
                && c.minute_of_day >= e.entry_minute.saturating_add(time_stop_minutes)
                && pnl_pct <= time_stop_min_progress_pct
            {
                exit_price = c.close;
                is_eod = false;
                exit_minute = c.minute_of_day;
                break;
            }

            let body_pct = if c.open > 0.0 {
                (c.close - c.open).abs() / c.open * 100.0
            } else {
                0.0
            };
            let adverse_candle = if short {
                c.close > c.open && body_pct >= adverse_body_min_pct
            } else {
                c.close < c.open && body_pct >= adverse_body_min_pct
            };
            adverse_count = if adverse_candle { adverse_count + 1 } else { 0 };
            if adverse_exit_bars > 0 && adverse_count >= adverse_exit_bars && pnl_pct < 0.0 {
                exit_price = c.close;
                is_eod = false;
                exit_minute = c.minute_of_day;
                break;
            }
        }

        let pnl = if short {
            (e.entry_price - exit_price) / e.entry_price
        } else {
            (exit_price - e.entry_price) / e.entry_price
        };
        let size = capital * pos_size;
        let net = size * pnl - size * FEE_PCT * 2.0;
        capital += net;

        if net > 0.0 {
            wins += 1;
            cl = 0;
        } else {
            losses += 1;
            cl += 1;
            mcl = mcl.max(cl);
        }
        if is_eod {
            eods += 1;
        }
        total_hold_minutes += u64::from(exit_minute.saturating_sub(e.entry_minute));
        peak = peak.max(capital);
        let dd = if peak > 0.0 { (peak - capital) / peak } else { 0.0 };
        max_dd = max_dd.max(dd);
    }

    let trades = entries.len() as f64;
    let avg_hold_minutes = if trades > 0.0 { total_hold_minutes as f64 / trades } else { 0.0 };
    let eod_ratio_pct = if trades > 0.0 { eods as f64 / trades * 100.0 } else { 0.0 };

    GuardEvalStats {
        wins,
        losses,
        eods,
        final_capital: capital,
        max_dd,
        max_consec_loss: mcl,
        avg_hold_minutes,
        eod_ratio_pct,
    }
}

#[allow(clippy::too_many_arguments)]
pub fn evaluate_with_guards(
    entries: &[Entry],
    candles: &[Candle],
    tp_pct: f64,
    sl_pct: f64,
    pos_size: f64,
    use_entry_direction: bool,
    default_short: bool,
    max_hold: u16,
    vwap_dist_stop: f64,
    time_stop_minutes: u16,
    time_stop_min_progress_pct: f64,
    adverse_exit_bars: usize,
    adverse_body_min_pct: f64,
    vwap_idx: usize,
) -> (usize, usize, usize, f64, f64, usize) {
    let stats = evaluate_with_guards_stats(
        entries,
        candles,
        tp_pct,
        sl_pct,
        pos_size,
        use_entry_direction,
        default_short,
        max_hold,
        vwap_dist_stop,
        time_stop_minutes,
        time_stop_min_progress_pct,
        adverse_exit_bars,
        adverse_body_min_pct,
        vwap_idx,
    );
    (
        stats.wins,
        stats.losses,
        stats.eods,
        stats.final_capital,
        stats.max_dd,
        stats.max_consec_loss,
    )
}

pub fn f2(v: f64) -> String {
    format!("{:.2}", v)
}
