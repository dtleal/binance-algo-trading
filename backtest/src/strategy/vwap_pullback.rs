//! VWAPPullback — bidirectional, EMA-filtered, multiple trades per day.
//!
//! Portado de `backtest_sweep/src/main.rs::find_entries_pullback` (strategy 4).

use std::sync::Arc;

use crate::indicator::{vwap_rolling, ema};
use crate::types::*;

const ENTRY_START:  u16 = 60;    // 01:00 UTC
const ENTRY_CUTOFF: u16 = 1320;  // 22:00 UTC

/// Detecta consolidação dentro de `vwap_prox` por `min_bars` candles, depois
/// breakout filtrado por trend (close vs EMA), com `confirm_bars` candles
/// de confirmação na direção do breakout.
pub fn find_entries_pullback(
    candles: &[Candle],
    days: &DayIndex,
    vwap: &[f64],
    ema_vals: &[f64],
    min_bars: usize,
    confirm_bars: usize,
    vwap_prox: f64,
    max_trades_per_day: usize,
) -> Vec<Entry> {
    let mut entries = Vec::new();
    for indices in days.values() {
        if indices.is_empty() { continue; }

        let mut counter: usize = 0;
        let mut confirming = false;
        let mut confirm_count: usize = 0;
        let mut pending_long = false;
        let mut trades_today = 0_usize;
        let mut i = 0;

        while i < indices.len() {
            if max_trades_per_day > 0 && trades_today >= max_trades_per_day { break; }
            let idx = indices[i];
            let c = &candles[idx];
            let vw = vwap[idx];
            let em = ema_vals[idx];

            if c.minute_of_day < ENTRY_START  { counter = 0; i += 1; continue; }
            if c.minute_of_day >= ENTRY_CUTOFF { i += 1; continue; }
            if em.is_nan() { i += 1; continue; }

            let trend_up = c.close > em;
            let pct = (c.close - vw) / vw;

            if confirming {
                let confirmed = if pending_long { c.close > vw } else { c.close < vw };
                if confirmed {
                    confirm_count += 1;
                    if confirm_count >= confirm_bars {
                        push_entry(&mut entries, candles, indices, i, pending_long);
                        trades_today += 1;
                        counter = 0;
                        confirming = false;
                        confirm_count = 0;
                        // Pula pra após o exit (heurística do v1)
                        let last_rest = entries.last().map(|e| e.rest_end).unwrap_or(idx);
                        i = if last_rest > indices[0] {
                            last_rest.saturating_sub(indices[0])
                        } else { i + 1 };
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

            // Consolidação / breakout
            if pct.abs() <= vwap_prox {
                counter += 1;
            } else if counter >= min_bars {
                let breakout_long  =  trend_up && pct >  vwap_prox;
                let breakout_short = !trend_up && pct < -vwap_prox;
                if breakout_long || breakout_short {
                    counter = 0;
                    pending_long = breakout_long;
                    if confirm_bars == 0 {
                        push_entry(&mut entries, candles, indices, i, pending_long);
                        trades_today += 1;
                        let last_rest = entries.last().map(|e| e.rest_end).unwrap_or(idx);
                        i = if last_rest > indices[0] {
                            last_rest.saturating_sub(indices[0])
                        } else { i + 1 };
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

/// Helper para empacotar Entry com rest_start/rest_end e eod_close calculados.
fn push_entry(
    entries: &mut Vec<Entry>,
    candles: &[Candle],
    day_indices: &[usize],
    local_i: usize,
    pending_long: bool,
) {
    let idx = day_indices[local_i];
    let c = &candles[idx];
    let rest = &day_indices[local_i + 1..];
    let mut eod_close = candles[*day_indices.last().unwrap()].close;
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
        entry_price: c.close,
        entry_minute: c.minute_of_day,
        direction: if pending_long { Direction::Long } else { Direction::Short },
        rest_start, rest_end, eod_close,
    });
}

// ── Grid expansion ──────────────────────────────────────────────────────────

pub struct Grid {
    pub min_bars:           Vec<usize>,
    pub confirm_bars:       Vec<usize>,
    pub vwap_prox:          Vec<f64>,
    pub vwap_window_days:   Vec<u32>,
    pub ema_period:         Vec<usize>,
    pub max_trades_per_day: Vec<usize>,
}

impl Default for Grid {
    fn default() -> Self {
        Self {
            min_bars:           vec![3, 5, 8, 12, 20, 30],
            confirm_bars:       vec![0, 1, 2],
            vwap_prox:          vec![0.002, 0.005],
            vwap_window_days:   vec![1, 5, 10, 20, 30],
            ema_period:         vec![100, 200, 300, 500],
            max_trades_per_day: vec![1, 2, 4, 6],
        }
    }
}

pub struct VwapPullback {
    grid: Grid,
}

impl VwapPullback {
    pub fn new(grid: Grid) -> Self { Self { grid } }
    pub fn with_default() -> Self { Self { grid: Grid::default() } }
}

impl super::Strategy for VwapPullback {
    fn name(&self) -> &'static str { "vwap_pullback" }

    fn build(&self, ctx: &Ctx<'_>) -> StrategyOutput {
        // Indicators precomputados uma vez por (window/period) — não por combo de grid.
        let vwaps: Vec<Vec<f64>> = self.grid.vwap_window_days.iter()
            .map(|w| vwap_rolling(ctx.candles, *w))
            .collect();
        let emas: Vec<Vec<f64>> = self.grid.ema_period.iter()
            .map(|p| ema(ctx.candles, *p))
            .collect();

        let mut sets = Vec::new();
        for &mb in &self.grid.min_bars {
            for &cb in &self.grid.confirm_bars {
                for &vp in &self.grid.vwap_prox {
                    for (vw_idx, &vw) in self.grid.vwap_window_days.iter().enumerate() {
                        for (ema_idx, &ep) in self.grid.ema_period.iter().enumerate() {
                            for &mt in &self.grid.max_trades_per_day {
                                let entries = find_entries_pullback(
                                    ctx.candles, ctx.days,
                                    &vwaps[vw_idx], &emas[ema_idx],
                                    mb, cb, vp, mt,
                                );
                                let label = format!(
                                    "min_bars={mb} confirm_bars={cb} vwap_prox={vp:.4} vwap_window={vw}d ema_period={ep} max_trades_per_day={mt}"
                                );
                                let params = serde_json::json!({
                                    "min_bars": mb,
                                    "confirm_bars": cb,
                                    "vwap_prox": vp,
                                    "vwap_window_days": vw,
                                    "ema_period": ep,
                                    "max_trades_per_day": mt,
                                });
                                sets.push(EntrySet {
                                    strategy_label: label,
                                    strategy_params: params,
                                    entries: Arc::new(entries),
                                });
                            }
                        }
                    }
                }
            }
        }
        StrategyOutput::EntrySets(sets)
    }
}
