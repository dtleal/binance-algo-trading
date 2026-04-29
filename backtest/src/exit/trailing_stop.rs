//! Trailing-stop exit: SL trail por R-multiple, TP_R opcional, max_hold opcional.
//!
//! Portado de `backtest_sweep_v2/src/main.rs::evaluate` + `trailing_sl_price`.

use crate::types::*;

/// Calcula SL price atual baseado em best_r alcançado.
///
/// Phases:
/// - best_r < be_r/2          → SL = -1.0R (SL inicial fixo)
/// - be_r/2 <= best_r < be_r  → SL = -0.5R
/// - best_r >= be_r           → SL move em passos discretos de trail_step*R
#[inline]
fn trailing_sl_price(entry: f64, r: f64, best_r: f64, short: bool, be_r: f64, trail_step: f64) -> f64 {
    let half_be = be_r / 2.0;
    let delta = if best_r < half_be {
        -r
    } else if best_r < be_r {
        -0.5 * r
    } else {
        let steps = ((best_r - be_r) / trail_step).floor() as i64;
        steps as f64 * trail_step * r
    };
    if short { entry - delta } else { entry + delta }
}

#[inline]
pub fn evaluate(
    entry: &Entry,
    candles: &[Candle],
    sl_pct: f64,
    be_r: f64,
    trail_step: f64,
    tp_r: f64,
    max_hold_min: u16,
) -> ExitResult {
    let short = matches!(entry.direction, Direction::Short);
    let r = entry.entry_price * sl_pct;
    let mut exit_price = entry.eod_close;
    let mut is_eod = true;
    let mut best_r: f64 = 0.0;

    for j in entry.rest_start..entry.rest_end {
        let c = &candles[j];

        if max_hold_min > 0 && c.minute_of_day >= entry.entry_minute + max_hold_min {
            exit_price = c.close;
            is_eod = false;
            break;
        }

        // Atualiza best_r com favorável da barra
        let candle_best_r = if short {
            (entry.entry_price - c.low) / r
        } else {
            (c.high - entry.entry_price) / r
        };
        if candle_best_r > best_r { best_r = candle_best_r; }

        // TP fixo em R-multiples
        if tp_r > 0.0 && best_r >= tp_r {
            exit_price = if short {
                entry.entry_price - tp_r * r
            } else {
                entry.entry_price + tp_r * r
            };
            is_eod = false;
            break;
        }

        // SL trailing
        let sl_price = trailing_sl_price(entry.entry_price, r, best_r, short, be_r, trail_step);
        if short {
            if c.high >= sl_price { exit_price = sl_price; is_eod = false; break; }
        } else {
            if c.low <= sl_price { exit_price = sl_price; is_eod = false; break; }
        }
    }

    ExitResult { exit_price, is_eod }
}

// ── Grid expansion ──────────────────────────────────────────────────────────

pub struct Grid {
    pub sl_pct:       Vec<f64>,
    pub be_r:         Vec<f64>,
    pub trail_step:   Vec<f64>,
    pub tp_r:         Vec<f64>,
    pub max_hold_min: Vec<u16>,
}

impl Default for Grid {
    fn default() -> Self {
        Self {
            sl_pct:       vec![0.001, 0.002, 0.003, 0.004, 0.006, 0.008, 0.01, 0.015, 0.02, 0.05],
            be_r:         vec![1.0, 1.5, 2.0, 3.0],
            trail_step:   vec![0.25, 0.5, 1.0],
            tp_r:         vec![0.0, 1.5, 2.0, 2.5, 3.0],   // 0 = sem TP fixo
            max_hold_min: vec![0, 30, 120, 360],
        }
    }
}

pub struct TrailingStop {
    grid: Grid,
}

impl TrailingStop {
    pub fn new(grid: Grid) -> Self { Self { grid } }
    pub fn with_default() -> Self { Self { grid: Grid::default() } }
}

impl super::Exit for TrailingStop {
    fn name(&self) -> &'static str { "trailing_stop" }

    fn build_variants(&self, _ctx: &Ctx<'_>) -> Vec<ExitVariant> {
        let total = self.grid.sl_pct.len() * self.grid.be_r.len()
            * self.grid.trail_step.len() * self.grid.tp_r.len()
            * self.grid.max_hold_min.len();
        let mut out = Vec::with_capacity(total);
        for &sl in &self.grid.sl_pct {
            for &be in &self.grid.be_r {
                for &ts in &self.grid.trail_step {
                    for &tp in &self.grid.tp_r {
                        for &mh in &self.grid.max_hold_min {
                            let label = format!(
                                "sl={sl:.4} be_r={be:.2} trail_step={ts:.2} tp_r={tp:.2} max_hold={mh}"
                            );
                            let params = ExitParamsRow {
                                sl_pct:       Some(sl),
                                be_r:         Some(be),
                                trail_step:   Some(ts),
                                tp_r:         Some(tp),
                                max_hold_min: Some(mh as i32),
                                ..Default::default()
                            };
                            out.push(ExitVariant {
                                label,
                                params,
                                eval: ExitFn::Trailing {
                                    sl_pct: sl, be_r: be, trail_step: ts, tp_r: tp, max_hold_min: mh,
                                },
                            });
                        }
                    }
                }
            }
        }
        out
    }
}
