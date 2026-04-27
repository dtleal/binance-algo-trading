//! EMAScalp — fast/slow EMA crossover.
//!
//! Portado de `backtest_sweep/src/main.rs::find_entries_ema_scalp` (strategy 5).

use std::sync::Arc;

use crate::indicator::ema;
use crate::types::*;

const ENTRY_START:  u16 = 0;
const ENTRY_CUTOFF: u16 = 1380;
const END_OF_DAY:   u16 = 1430;

pub fn find_entries_ema_scalp(
    candles: &[Candle],
    days: &DayIndex,
    fast: &[f64],
    slow: &[f64],
    max_trades_per_day: usize,
) -> Vec<Entry> {
    let mut entries = Vec::new();
    for indices in days.values() {
        if indices.len() < 2 { continue; }
        let mut trades_today = 0_usize;
        let mut i = 1_usize;

        while i < indices.len() {
            if max_trades_per_day > 0 && trades_today >= max_trades_per_day { break; }
            let idx = indices[i];
            let c = &candles[idx];
            if c.minute_of_day < ENTRY_START  { i += 1; continue; }
            if c.minute_of_day >= ENTRY_CUTOFF { i += 1; continue; }

            let f = fast[idx]; let s = slow[idx];
            if f.is_nan() || s.is_nan() { i += 1; continue; }
            let prev_idx = indices[i - 1];
            let pf = fast[prev_idx]; let ps = slow[prev_idx];
            if pf.is_nan() || ps.is_nan() { i += 1; continue; }

            let cross_long  = pf <= ps && f > s;
            let cross_short = pf >= ps && f < s;

            if cross_long || cross_short {
                let rest = &indices[i + 1..];
                let mut eod_close = candles[*indices.last().unwrap()].close;
                let rest_start = if rest.is_empty() { 0 } else { rest[0] };
                let mut rest_end = rest_start;
                for &ri in rest {
                    let rc = &candles[ri];
                    if rc.minute_of_day >= END_OF_DAY {
                        eod_close = rc.close; rest_end = ri; break;
                    }
                    rest_end = ri + 1;
                }
                entries.push(Entry {
                    entry_price: c.close, entry_minute: c.minute_of_day,
                    direction: if cross_long { Direction::Long } else { Direction::Short },
                    rest_start, rest_end, eod_close,
                });
                trades_today += 1;
                i = if rest_end > idx { rest_end.saturating_sub(indices[0]) } else { i + 1 };
                continue;
            }
            i += 1;
        }
    }
    entries
}

// ── Grid expansion ──────────────────────────────────────────────────────────

pub struct Grid {
    pub fast_period:        Vec<usize>,
    pub slow_period:        Vec<usize>,
    pub max_trades_per_day: Vec<usize>,
}

impl Default for Grid {
    fn default() -> Self {
        Self {
            fast_period:        vec![5, 8, 13],
            slow_period:        vec![21, 34, 55],
            max_trades_per_day: vec![1, 2, 4, 6],
        }
    }
}

pub struct EmaScalp { grid: Grid }

impl EmaScalp {
    pub fn new(grid: Grid) -> Self { Self { grid } }
    pub fn with_default() -> Self { Self { grid: Grid::default() } }
}

impl super::Strategy for EmaScalp {
    fn name(&self) -> &'static str { "ema_scalp" }

    fn build(&self, ctx: &Ctx<'_>) -> StrategyOutput {
        let fasts: Vec<Vec<f64>> = self.grid.fast_period.iter()
            .map(|p| ema(ctx.candles, *p)).collect();
        let slows: Vec<Vec<f64>> = self.grid.slow_period.iter()
            .map(|p| ema(ctx.candles, *p)).collect();

        let mut sets = Vec::new();
        for (fi, &fp) in self.grid.fast_period.iter().enumerate() {
            for (si, &sp) in self.grid.slow_period.iter().enumerate() {
                for &mt in &self.grid.max_trades_per_day {
                    let entries = find_entries_ema_scalp(
                        ctx.candles, ctx.days, &fasts[fi], &slows[si], mt,
                    );
                    let label = format!("fast_period={fp} slow_period={sp} max_trades_per_day={mt}");
                    sets.push(EntrySet {
                        strategy_label: label,
                        entries: Arc::new(entries),
                    });
                }
            }
        }
        StrategyOutput::EntrySets(sets)
    }
}
