//! ORB — Opening Range Breakout.
//!
//! Portado de `backtest_sweep/src/main.rs::find_entries_orb` (strategy 6).

use std::sync::Arc;
use crate::types::*;

const END_OF_DAY: u16 = 1430;

pub fn find_entries_orb(
    candles: &[Candle],
    days: &DayIndex,
    range_mins: u16,
    buffer_pct: f64,
) -> Vec<Entry> {
    let mut entries = Vec::new();
    for indices in days.values() {
        if indices.is_empty() { continue; }
        let mut range_high = f64::NEG_INFINITY;
        let mut range_low  = f64::INFINITY;
        let mut range_set  = false;
        let mut long_taken  = false;
        let mut short_taken = false;

        for (local_i, &idx) in indices.iter().enumerate() {
            let c = &candles[idx];
            if c.minute_of_day < range_mins {
                if c.high > range_high { range_high = c.high; }
                if c.low  < range_low  { range_low  = c.low;  }
                continue;
            }
            if !range_set {
                if range_high == f64::NEG_INFINITY { break; }
                range_set = true;
            }
            if long_taken && short_taken { break; }

            let signal_long  = !long_taken  && c.close > range_high * (1.0 + buffer_pct);
            let signal_short = !short_taken && c.close < range_low  * (1.0 - buffer_pct);

            if signal_long || signal_short {
                let rest = &indices[local_i + 1..];
                let mut eod_close = candles[*indices.last().unwrap()].close;
                let rest_start = if rest.is_empty() { 0 } else { rest[0] };
                let mut rest_end = rest_start;
                for &ri in rest {
                    let rc = &candles[ri];
                    if rc.minute_of_day >= END_OF_DAY { eod_close = rc.close; rest_end = ri; break; }
                    rest_end = ri + 1;
                }
                entries.push(Entry {
                    entry_price: c.close, entry_minute: c.minute_of_day,
                    direction: if signal_long { Direction::Long } else { Direction::Short },
                    rest_start, rest_end, eod_close,
                });
                if signal_long  { long_taken  = true; }
                if signal_short { short_taken = true; }
            }
        }
    }
    entries
}

// ── Grid expansion ──────────────────────────────────────────────────────────

pub struct Grid {
    pub range_mins:  Vec<u16>,
    pub buffer_pct:  Vec<f64>,
}

impl Default for Grid {
    fn default() -> Self {
        Self {
            range_mins:  vec![15, 30, 60],
            buffer_pct:  vec![0.001, 0.002, 0.005],
        }
    }
}

pub struct Orb { grid: Grid }

impl Orb {
    pub fn new(grid: Grid) -> Self { Self { grid } }
    pub fn with_default() -> Self { Self { grid: Grid::default() } }
}

impl super::Strategy for Orb {
    fn name(&self) -> &'static str { "orb" }

    fn build(&self, ctx: &Ctx<'_>) -> StrategyOutput {
        let mut sets = Vec::new();
        for &rm in &self.grid.range_mins {
            for &buf in &self.grid.buffer_pct {
                let entries = find_entries_orb(ctx.candles, ctx.days, rm, buf);
                let label = format!("range_mins={rm} buffer_pct={buf:.4}");
                let params = StrategyParamsRow {
                    range_mins: Some(rm as i32),
                    buffer_pct: Some(buf),
                    ..Default::default()
                };
                sets.push(EntrySet {
                    strategy_label: label,
                    strategy_params: params,
                    entries: Arc::new(entries),
                });
            }
        }
        StrategyOutput::EntrySets(sets)
    }
}
