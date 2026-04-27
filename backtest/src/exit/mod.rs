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

/// Hot path: dispatch monomórfico do enum `ExitFn` para o impl correto.
#[inline]
pub fn evaluate(entry: &Entry, candles: &[Candle], f: &ExitFn) -> ExitResult {
    match *f {
        ExitFn::FixedTpSl { tp_pct, sl_pct, max_hold_min } => {
            fixed_tp_sl::evaluate(entry, candles, tp_pct, sl_pct, max_hold_min)
        }
        ExitFn::Trailing { sl_pct, be_r, trail_step, tp_r, max_hold_min } => {
            trailing_stop::evaluate(entry, candles, sl_pct, be_r, trail_step, tp_r, max_hold_min)
        }
    }
}

/// Factory: nome string → Box<dyn Exit> com grid padrão.
/// (TOML grid spec a implementar quando necessário.)
pub fn build_exit(name: &str, _grid_spec: &str) -> Result<Box<dyn Exit>> {
    match name {
        "fixed_tp_sl"   => Ok(Box::new(fixed_tp_sl::FixedTpSl::with_default())),
        "trailing_stop" => Ok(Box::new(trailing_stop::TrailingStop::with_default())),
        other           => bail!("unknown exit: {other}"),
    }
}
