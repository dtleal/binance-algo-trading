//! Range strategy — mean-reversion em ranges horizontais.
//!
//! Portado de `backtest_sweep_range/src/main.rs::run_backtest`.
//!
//! Caso especial: retorna `StrategyOutput::Runs` (exit baked-in, ignora exits externos).
//! Cada combinação de params produz um `MonolithicRun` que executa o backtest completo
//! (entries + exits acoplados) e devolve `RunMetrics`.

use crate::indicator::{atr_wilder, adx_wilder};
use crate::types::*;

const RANGE_THROTTLE: usize = 12;
const ADX_PERIOD: usize = 14;
const ATR_PERIOD: usize = 14;

#[derive(Clone, Copy)]
struct Range {
    high: f64,
    low:  f64,
    size: f64,
}

#[derive(Clone, Copy, PartialEq)]
enum Zone { Buy, Sell, Neutral }

#[derive(Clone)]
struct Position {
    side:        bool,    // true = long
    entry_price: f64,
    tp_price:    f64,
    sl_price:    f64,     // 0.0 = sem SL
    qty_pct:     f64,
}

fn detect_range(highs: &[f64], lows: &[f64], end_idx: usize, lookback: usize) -> Option<Range> {
    if end_idx < lookback { return None; }
    let start = end_idx - lookback;
    let high = highs[start..end_idx].iter().fold(f64::NEG_INFINITY, |a, b| a.max(*b));
    let low  = lows [start..end_idx].iter().fold(f64::INFINITY,     |a, b| a.min(*b));
    let size = high - low;
    if size <= 0.0 { return None; }
    Some(Range { high, low, size })
}

fn price_zone(price: f64, range: &Range, zone_pct: f64) -> Zone {
    let zone_height = range.size * zone_pct / 100.0;
    if price <= range.low + zone_height  { return Zone::Buy;  }
    if price >= range.high - zone_height { return Zone::Sell; }
    Zone::Neutral
}

#[allow(clippy::too_many_arguments)]
pub fn run_range_backtest(
    candles: &[Candle],
    adx: &[f64],
    atr_pct: &[f64],
    adx_thresh:        f64,
    atr_pct_thresh:    f64,
    range_lookback:    usize,
    zone_pct:          f64,
    tp_range_pct:      f64,
    sl_range_pct:      f64,
    recent_thresh_pct: f64,
    max_orders:        usize,
    pos_size_pct:      f64,
) -> RunMetrics {
    let n = candles.len();
    let min_history = range_lookback + ADX_PERIOD * 3;
    if n < min_history {
        return RunMetrics { final_capital: INITIAL_CAPITAL, ..Default::default() };
    }

    // Slices high/low pra detect_range (o ATR/ADX já vem precomputado).
    let highs: Vec<f64> = candles.iter().map(|c| c.high).collect();
    let lows:  Vec<f64> = candles.iter().map(|c| c.low ).collect();

    let mut capital = INITIAL_CAPITAL;
    let mut peak    = capital;
    let mut max_dd  = 0.0_f64;
    let mut wins    = 0_usize;
    let mut losses  = 0_usize;
    let mut cl      = 0_usize;
    let mut mcl     = 0_usize;
    let mut positions: Vec<Position> = Vec::new();
    let mut last_range_calc: usize = 0;
    let mut current_range: Option<Range> = None;

    for i in min_history..n {
        let c = &candles[i];

        if i - last_range_calc >= RANGE_THROTTLE || current_range.is_none() {
            current_range = detect_range(&highs, &lows, i, range_lookback);
            last_range_calc = i;
        }

        let in_range = if let Some(ref r) = current_range {
            adx[i] <= adx_thresh && atr_pct[i] <= atr_pct_thresh && r.size > 0.0
        } else {
            false
        };

        // Step 4: TP/SL nas posições abertas
        let mut remaining: Vec<Position> = Vec::new();
        for pos in positions.drain(..) {
            let hit_tp = if pos.side { c.high >= pos.tp_price } else { c.low <= pos.tp_price };
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
                if net > 0.0 { wins += 1; cl = 0; } else { losses += 1; cl += 1; mcl = mcl.max(cl); }
            } else {
                remaining.push(pos);
            }
        }
        positions = remaining;

        if !in_range { continue; }
        let range = match current_range { Some(ref r) => r, None => continue };

        if positions.len() >= max_orders { continue; }
        let price = c.close;
        let zone  = price_zone(price, range, zone_pct);
        if zone == Zone::Neutral { continue; }
        let is_long = zone == Zone::Buy;

        let threshold = range.size * recent_thresh_pct / 100.0;
        let has_recent = positions.iter().any(|p| {
            p.side == is_long && (p.entry_price - price).abs() < threshold
        });
        if has_recent { continue; }

        let tp_dist = range.size * tp_range_pct / 100.0;
        let sl_dist = if sl_range_pct > 0.0 { range.size * sl_range_pct / 100.0 } else { 0.0 };
        let (tp_price, sl_price) = if is_long {
            (price + tp_dist, if sl_dist > 0.0 { price - sl_dist } else { 0.0 })
        } else {
            (price - tp_dist, if sl_dist > 0.0 { price + sl_dist } else { 0.0 })
        };
        let tp_inside_range = if is_long { tp_price <= range.high } else { tp_price >= range.low };
        if !tp_inside_range { continue; }
        if capital * pos_size_pct * price <= 0.0 { continue; }

        positions.push(Position {
            side: is_long, entry_price: price, tp_price, sl_price, qty_pct: pos_size_pct,
        });
    }

    // Fecha posições restantes no último close (EOD-style)
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

    RunMetrics {
        trades: wins + losses,
        wins, losses, eods: 0,
        final_capital: capital,
        max_dd_pct: max_dd * 100.0,
        max_consec_loss: mcl,
    }
}

// ── Grid expansion ──────────────────────────────────────────────────────────

pub struct Grid {
    pub adx_thresh:        Vec<f64>,
    pub atr_pct_thresh:    Vec<f64>,
    pub range_lookback:    Vec<usize>,
    pub zone_pct:          Vec<f64>,
    pub tp_range_pct:      Vec<f64>,
    pub sl_range_pct:      Vec<f64>,
    pub recent_thresh_pct: Vec<f64>,
    pub max_orders:        Vec<usize>,
    pub pos_size:          Vec<f64>,
}

impl Default for Grid {
    fn default() -> Self {
        Self {
            adx_thresh:        vec![20.0, 25.0, 30.0],
            atr_pct_thresh:    vec![0.3, 0.5, 0.8, 1.0],
            range_lookback:    vec![20, 30, 50, 80],
            zone_pct:          vec![15.0, 20.0, 25.0, 33.0],
            tp_range_pct:      vec![30.0, 40.0, 50.0, 60.0, 70.0],
            sl_range_pct:      vec![0.0, 20.0, 30.0, 40.0, 50.0],
            recent_thresh_pct: vec![1.0, 2.0, 3.0],
            max_orders:        vec![2, 4, 6],
            pos_size:          vec![0.05, 0.10, 0.20],
        }
    }
}

pub struct RangeStrategy { grid: Grid }

impl RangeStrategy {
    pub fn new(grid: Grid) -> Self { Self { grid } }
    pub fn with_default() -> Self { Self { grid: Grid::default() } }
}

impl super::Strategy for RangeStrategy {
    fn name(&self) -> &'static str { "range" }

    fn build(&self, ctx: &Ctx<'_>) -> StrategyOutput {
        // ATR e ADX precomputados uma vez (ATR%/close depende do close de cada candle)
        let atr  = atr_wilder(ctx.candles, ATR_PERIOD);
        let adx  = adx_wilder(ctx.candles, ADX_PERIOD);
        let atr_pct: Vec<f64> = atr.iter().enumerate()
            .map(|(i, a)| if ctx.candles[i].close > 0.0 { a / ctx.candles[i].close * 100.0 } else { 0.0 })
            .collect();

        let mut runs = Vec::new();
        for &adx_t in &self.grid.adx_thresh {
            for &atr_t in &self.grid.atr_pct_thresh {
                for &lb in &self.grid.range_lookback {
                    for &zp in &self.grid.zone_pct {
                        for &tp in &self.grid.tp_range_pct {
                            for &sl in &self.grid.sl_range_pct {
                                for &rt in &self.grid.recent_thresh_pct {
                                    for &mo in &self.grid.max_orders {
                                        for &ps in &self.grid.pos_size {
                                            let label = format!(
                                                "adx={adx_t:.1} atr={atr_t:.2} lb={lb} zone={zp:.1} tp={tp:.1} sl={sl:.1} recent={rt:.1} max_orders={mo} pos={ps:.2}"
                                            );
                                            // Fecha sobre os params; cada run executa independente.
                                            let adx_clone = adx.clone();
                                            let atr_pct_clone = atr_pct.clone();
                                            runs.push(MonolithicRun {
                                                label,
                                                execute: Box::new(move |candles| {
                                                    run_range_backtest(
                                                        candles, &adx_clone, &atr_pct_clone,
                                                        adx_t, atr_t, lb, zp, tp, sl, rt, mo, ps,
                                                    )
                                                }),
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
        StrategyOutput::Runs(runs)
    }
}
