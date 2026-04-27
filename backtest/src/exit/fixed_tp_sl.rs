//! Fixed TP/SL exit + max_hold opcional.
//!
//! Portado de `backtest_sweep/src/main.rs::evaluate` (parte do per-entry).
//! Omite vwap_dist_stop (esoteric, raramente usado — adicionar depois se necessário).

use crate::types::*;

/// Por-entry: TP/SL fixos como % do entry. Sai no primeiro hit (SL prioritário em barra
/// que toca os dois) ou no max_hold ou no EOD. SL precedência preserva v1.
#[inline]
pub fn evaluate(
    entry: &Entry,
    candles: &[Candle],
    tp_pct: f64,
    sl_pct: f64,
    max_hold_min: u16,
) -> ExitResult {
    let short = matches!(entry.direction, Direction::Short);
    let (tp_price, sl_price) = if short {
        (entry.entry_price * (1.0 - tp_pct), entry.entry_price * (1.0 + sl_pct))
    } else {
        (entry.entry_price * (1.0 + tp_pct), entry.entry_price * (1.0 - sl_pct))
    };

    let mut exit_price = entry.eod_close;
    let mut is_eod = true;

    for j in entry.rest_start..entry.rest_end {
        let c = &candles[j];

        if max_hold_min > 0 && c.minute_of_day >= entry.entry_minute + max_hold_min {
            exit_price = c.close;
            is_eod = false;
            break;
        }

        if short {
            if c.high >= sl_price { exit_price = sl_price; is_eod = false; break; }
            if c.low <= tp_price  { exit_price = tp_price; is_eod = false; break; }
        } else {
            if c.low <= sl_price  { exit_price = sl_price; is_eod = false; break; }
            if c.high >= tp_price { exit_price = tp_price; is_eod = false; break; }
        }
    }

    ExitResult { exit_price, is_eod }
}

// ── Grid expansion ──────────────────────────────────────────────────────────

/// Grid de params para sweep. Match com `backtest_sweep/src/main.rs` constants.
pub struct Grid {
    pub tp_pct:       Vec<f64>,
    pub sl_pct:       Vec<f64>,
    pub max_hold_min: Vec<u16>,
}

impl Default for Grid {
    fn default() -> Self {
        Self {
            tp_pct: vec![
                0.001, 0.002, 0.003, 0.004, 0.005, 0.006, 0.008,
                0.01, 0.015, 0.02, 0.03, 0.05, 0.07, 0.10,
            ],
            sl_pct: vec![
                0.001, 0.002, 0.003, 0.004, 0.006, 0.008,
                0.01, 0.015, 0.02, 0.05,
            ],
            max_hold_min: vec![0, 30, 120, 360],   // 0 = EOD
        }
    }
}

pub struct FixedTpSl {
    grid: Grid,
}

impl FixedTpSl {
    pub fn new(grid: Grid) -> Self { Self { grid } }
    pub fn with_default() -> Self { Self { grid: Grid::default() } }
}

impl super::Exit for FixedTpSl {
    fn name(&self) -> &'static str { "fixed_tp_sl" }

    fn build_variants(&self, _ctx: &Ctx<'_>) -> Vec<ExitVariant> {
        let mut out = Vec::with_capacity(
            self.grid.tp_pct.len() * self.grid.sl_pct.len() * self.grid.max_hold_min.len()
        );
        for &tp in &self.grid.tp_pct {
            for &sl in &self.grid.sl_pct {
                for &mh in &self.grid.max_hold_min {
                    let label = format!(
                        "tp={tp:.4} sl={sl:.4} max_hold={mh}",
                        tp = tp, sl = sl, mh = mh
                    );
                    out.push(ExitVariant {
                        label,
                        eval: ExitFn::FixedTpSl { tp_pct: tp, sl_pct: sl, max_hold_min: mh },
                    });
                }
            }
        }
        out
    }
}
