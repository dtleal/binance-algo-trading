//! Momentum / Rejection strategies (RejShort/RejLong/MomShort/MomLong).
//!
//! Portado de `backtest_sweep/src/main.rs::find_entries` (strategies 0..3).
//! Variant `Kind` escolhe direção e tipo (rejection vs momentum).

use std::sync::Arc;

use crate::indicator::vwap_rolling;
use crate::types::*;

const END_OF_DAY: u16 = 1430;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind { RejShort, RejLong, MomShort, MomLong }

impl Kind {
    fn is_short(self) -> bool { matches!(self, Self::RejShort | Self::MomShort) }
    fn is_momentum(self) -> bool { matches!(self, Self::MomShort | Self::MomLong) }
    fn name(self) -> &'static str {
        match self {
            Self::RejShort => "rej_short",
            Self::RejLong  => "rej_long",
            Self::MomShort => "mom_short",
            Self::MomLong  => "mom_long",
        }
    }
}

#[allow(clippy::too_many_arguments)]
pub fn find_entries(
    candles: &[Candle],
    days: &DayIndex,
    vwap: &[f64],
    kind: Kind,
    min_bars: usize,
    vol_filter: bool,
    confirm_bars: usize,
    trend_filter: bool,
    entry_start: u16,
    entry_cutoff: u16,
    vwap_prox: f64,
) -> Vec<Entry> {
    // vol_sma(20) precomputado on-the-fly (curto, simples).
    let vol_sma20 = sma_volume(candles, 20);

    let mut entries = Vec::new();
    for indices in days.values() {
        if indices.is_empty() { continue; }
        let day_open = candles[indices[0]].open;
        let mut counter: usize = 0;
        let mut i = 0;

        while i < indices.len() {
            let idx = indices[i];
            let c = &candles[idx];
            let vw = vwap[idx];
            if c.minute_of_day < entry_start  { counter = 0; i += 1; continue; }
            if c.minute_of_day >= entry_cutoff { i += 1; continue; }

            let signal = match kind {
                Kind::RejShort => {
                    if c.close > vw { counter += 1; false }
                    else if counter >= min_bars { counter = 0; true }
                    else { counter = 0; false }
                }
                Kind::RejLong => {
                    if c.close < vw { counter += 1; false }
                    else if counter >= min_bars { counter = 0; true }
                    else { counter = 0; false }
                }
                Kind::MomShort => {
                    let pct = (c.close - vw) / vw;
                    if pct.abs() <= vwap_prox { counter += 1; false }
                    else if counter >= min_bars && pct < -vwap_prox { counter = 0; true }
                    else { counter = 0; false }
                }
                Kind::MomLong => {
                    let pct = (c.close - vw) / vw;
                    if pct.abs() <= vwap_prox { counter += 1; false }
                    else if counter >= min_bars && pct > vwap_prox { counter = 0; true }
                    else { counter = 0; false }
                }
            };
            if !signal { i += 1; continue; }
            if vol_filter && c.volume <= vol_sma20[idx] { i += 1; continue; }
            if trend_filter {
                let s = kind.is_short();
                if  s && c.close >= day_open { i += 1; continue; }
                if !s && c.close <= day_open { i += 1; continue; }
            }

            // confirm_bars
            let mut ok = true;
            let mut ci = i;
            for _ in 0..confirm_bars {
                ci += 1;
                if ci >= indices.len() { ok = false; break; }
                let cc = &candles[indices[ci]];
                let cc_vwap = vwap[indices[ci]];
                if cc.minute_of_day >= entry_cutoff { ok = false; break; }
                let valid = match kind {
                    Kind::RejShort | Kind::MomShort => cc.close < cc_vwap,
                    Kind::RejLong  | Kind::MomLong  => cc.close > cc_vwap,
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
            entries.push(Entry {
                entry_price: ep, entry_minute: em,
                direction: if kind.is_short() { Direction::Short } else { Direction::Long },
                rest_start, rest_end, eod_close,
            });
            // v1 quebra após 1 entry/dia para essas strategies (max_trades_per_day=1)
            break;
        }
        let _ = kind.is_momentum();
    }
    entries
}

fn sma_volume(candles: &[Candle], window: usize) -> Vec<f64> {
    let n = candles.len();
    let mut out = vec![0.0_f64; n];
    let mut sum = 0.0_f64;
    for i in 0..n {
        sum += candles[i].volume;
        if i >= window { sum -= candles[i - window].volume; out[i] = sum / window as f64; }
        else { out[i] = sum / (i + 1) as f64; }
    }
    out
}

// ── Grid expansion ──────────────────────────────────────────────────────────

pub struct Grid {
    pub kinds:            Vec<Kind>,
    pub min_bars:         Vec<usize>,
    pub vol_filter:       Vec<bool>,
    pub confirm_bars:     Vec<usize>,
    pub trend_filter:     Vec<bool>,
    pub entry_windows:    Vec<(u16, u16)>,
    pub vwap_prox:        Vec<f64>,        // só momentum
    pub vwap_window_days: Vec<u32>,
}

impl Default for Grid {
    fn default() -> Self {
        Self {
            kinds:            vec![Kind::MomShort],   // v1 sweep só roda 2,4,5,6,7 — Mom é o representante
            min_bars:         vec![3, 5, 8, 12, 20, 30],
            vol_filter:       vec![false, true],
            confirm_bars:     vec![0, 1, 2],
            trend_filter:     vec![false, true],
            entry_windows:    vec![(60, 1320), (360, 1080)],
            vwap_prox:        vec![0.002, 0.005],
            vwap_window_days: vec![1, 5, 10, 20, 30],
        }
    }
}

pub struct Momentum {
    grid: Grid,
}

impl Momentum {
    pub fn new(grid: Grid) -> Self { Self { grid } }
    pub fn with_default() -> Self { Self { grid: Grid::default() } }
}

impl super::Strategy for Momentum {
    fn name(&self) -> &'static str { "momentum" }

    fn build(&self, ctx: &Ctx<'_>) -> StrategyOutput {
        let vwaps: Vec<Vec<f64>> = self.grid.vwap_window_days.iter()
            .map(|w| vwap_rolling(ctx.candles, *w))
            .collect();

        let mut sets = Vec::new();
        for &kind in &self.grid.kinds {
            let prox_vals: &[f64] = if kind.is_momentum() {
                &self.grid.vwap_prox
            } else {
                &[0.0]
            };
            for &mb in &self.grid.min_bars {
                for &vf in &self.grid.vol_filter {
                    for &cb in &self.grid.confirm_bars {
                        for &tf in &self.grid.trend_filter {
                            for &(es, ec) in &self.grid.entry_windows {
                                for &vp in prox_vals {
                                    for (vw_idx, &vw) in self.grid.vwap_window_days.iter().enumerate() {
                                        let entries = find_entries(
                                            ctx.candles, ctx.days, &vwaps[vw_idx],
                                            kind, mb, vf, cb, tf, es, ec, vp,
                                        );
                                        let label = format!(
                                            "kind={kind_name} min_bars={mb} vol_filter={vf} confirm_bars={cb} trend_filter={tf} window={es}-{ec} vwap_prox={vp:.4} vwap_window={vw}d",
                                            kind_name = kind.name()
                                        );
                                        sets.push(EntrySet {
                                            strategy_label: label,
                                            entries: Arc::new(entries),
                                        });
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        StrategyOutput::EntrySets(sets)
    }
}
