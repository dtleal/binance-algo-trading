//! Trait `Exit` (build_variants) + enum `ExitFn` (hot path).
//!
//! Trait fora, enum dentro: extensão fácil + zero vtable no `evaluate`.

use crate::types::*;
use anyhow::{bail, Result};

pub mod fixed_tp_sl;
pub mod trailing_stop;

pub trait Exit: Send + Sync {
    fn name(&self) -> &'static str;
    fn build_variants(&self, ctx: &Ctx<'_>) -> Vec<ExitVariant>;
}

/// Aplica `ExitFn` num entry sobre o slice de candles. Hot path do sweep.
///
/// Retorna `(exit_price, is_eod)`. Caller calcula PnL líquido.
#[inline]
pub fn evaluate(entry: &Entry, candles: &[Candle], f: &ExitFn) -> ExitResult {
    match *f {
        ExitFn::FixedTpSl { tp_pct, sl_pct } => {
            fixed_tp_sl::evaluate(entry, candles, tp_pct, sl_pct)
        }
        ExitFn::Trailing { sl_pct, be_r, trail_step, tp_r } => {
            trailing_stop::evaluate(entry, candles, sl_pct, be_r, trail_step, tp_r)
        }
    }
}

pub fn build_exit(name: &str, _grid_spec: &str) -> Result<Box<dyn Exit>> {
    match name {
        "fixed_tp_sl"   => bail!("fixed_tp_sl: not implemented yet (scaffold)"),
        "trailing_stop" => bail!("trailing_stop: not implemented yet (scaffold)"),
        other           => bail!("unknown exit: {other}"),
    }
}
