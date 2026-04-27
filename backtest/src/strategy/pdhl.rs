//! PDHL — Previous Day High/Low rejection.
//!
//! Portado de `backtest_sweep/src/main.rs::find_entries_pdhl` (strategy 7).

use std::sync::Arc;
use crate::types::*;

const ENTRY_START:  u16 = 60;
const ENTRY_CUTOFF: u16 = 1320;
const END_OF_DAY:   u16 = 1430;

pub fn find_entries_pdhl(
    candles: &[Candle],
    days: &DayIndex,
    prox_pct: f64,
    confirm_bars: usize,
) -> Vec<Entry> {
    let mut entries = Vec::new();
    let mut pdh = f64::NAN;
    let mut pdl = f64::NAN;
    let mut day_high = f64::NEG_INFINITY;
    let mut day_low  = f64::INFINITY;

    for indices in days.values() {
        if indices.is_empty() { continue; }
        if day_high != f64::NEG_INFINITY {
            pdh = day_high;
            pdl = day_low;
        }
        day_high = f64::NEG_INFINITY;
        day_low  = f64::INFINITY;

        if pdh.is_nan() {
            for &idx in indices.iter() {
                let c = &candles[idx];
                if c.high > day_high { day_high = c.high; }
                if c.low  < day_low  { day_low  = c.low;  }
            }
            continue;
        }

        let mut testing_pdh = false;
        let mut testing_pdl = false;
        let mut pdh_conf = 0_usize;
        let mut pdl_conf = 0_usize;
        let mut trades_today = 0_usize;

        for (local_i, &idx) in indices.iter().enumerate() {
            let c = &candles[idx];
            if c.high > day_high { day_high = c.high; }
            if c.low  < day_low  { day_low  = c.low;  }
            if c.minute_of_day < ENTRY_START || c.minute_of_day >= ENTRY_CUTOFF { continue; }
            if trades_today >= 4 { break; }

            // PDH approach (rejeição → SHORT)
            if c.high >= pdh * (1.0 - prox_pct) { testing_pdh = true; }
            if testing_pdh && c.close < pdh * (1.0 - prox_pct) {
                pdh_conf += 1;
                if pdh_conf >= confirm_bars {
                    push_entry(&mut entries, candles, indices, local_i, false);
                    trades_today += 1;
                    testing_pdh = false; pdh_conf = 0;
                }
            } else if testing_pdh && c.close >= pdh * (1.0 - prox_pct) {
                pdh_conf = 0;
            } else if testing_pdh && c.close > pdh * (1.0 + prox_pct) {
                testing_pdh = false; pdh_conf = 0;
            }

            // PDL approach (rejeição → LONG)
            if c.low <= pdl * (1.0 + prox_pct) { testing_pdl = true; }
            if testing_pdl && c.close > pdl * (1.0 + prox_pct) {
                pdl_conf += 1;
                if pdl_conf >= confirm_bars {
                    push_entry(&mut entries, candles, indices, local_i, true);
                    trades_today += 1;
                    testing_pdl = false; pdl_conf = 0;
                }
            } else if testing_pdl && c.close <= pdl * (1.0 + prox_pct) {
                pdl_conf = 0;
            } else if testing_pdl && c.close < pdl * (1.0 - prox_pct) {
                testing_pdl = false; pdl_conf = 0;
            }
        }
    }
    entries
}

fn push_entry(
    entries: &mut Vec<Entry>,
    candles: &[Candle],
    day_indices: &[usize],
    local_i: usize,
    is_long: bool,
) {
    let idx = day_indices[local_i];
    let c = &candles[idx];
    let rest = &day_indices[local_i + 1..];
    let mut eod_close = candles[*day_indices.last().unwrap()].close;
    let rest_start = if rest.is_empty() { 0 } else { rest[0] };
    let mut rest_end = rest_start;
    for &ri in rest {
        let rc = &candles[ri];
        if rc.minute_of_day >= END_OF_DAY { eod_close = rc.close; rest_end = ri; break; }
        rest_end = ri + 1;
    }
    entries.push(Entry {
        entry_price: c.close, entry_minute: c.minute_of_day,
        direction: if is_long { Direction::Long } else { Direction::Short },
        rest_start, rest_end, eod_close,
    });
}

// ── Grid expansion ──────────────────────────────────────────────────────────

pub struct Grid {
    pub prox_pct:     Vec<f64>,
    pub confirm_bars: Vec<usize>,
}

impl Default for Grid {
    fn default() -> Self {
        Self {
            prox_pct:     vec![0.001, 0.002, 0.005],
            confirm_bars: vec![1, 2, 3],
        }
    }
}

pub struct Pdhl { grid: Grid }

impl Pdhl {
    pub fn new(grid: Grid) -> Self { Self { grid } }
    pub fn with_default() -> Self { Self { grid: Grid::default() } }
}

impl super::Strategy for Pdhl {
    fn name(&self) -> &'static str { "pdhl" }

    fn build(&self, ctx: &Ctx<'_>) -> StrategyOutput {
        let mut sets = Vec::new();
        for &prox in &self.grid.prox_pct {
            for &cb in &self.grid.confirm_bars {
                let entries = find_entries_pdhl(ctx.candles, ctx.days, prox, cb);
                let label = format!("prox_pct={prox:.4} confirm_bars={cb}");
                sets.push(EntrySet { strategy_label: label, entries: Arc::new(entries) });
            }
        }
        StrategyOutput::EntrySets(sets)
    }
}
