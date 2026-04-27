//! Trait `Strategy` + factory.
//!
//! Cada strategy mora em arquivo próprio e implementa o trait. Adicionar
//! strategy = arquivo novo + 1 linha em `build_strategy`.

use crate::types::*;
use anyhow::{bail, Result};

pub mod momentum;
pub mod vwap_pullback;
pub mod ema_scalp;
pub mod orb;
pub mod pdhl;
pub mod range;

pub trait Strategy: Send + Sync {
    fn name(&self) -> &'static str;
    fn build(&self, ctx: &Ctx<'_>) -> StrategyOutput;
}

/// Factory: nome string → Box<dyn Strategy> com grid padrão.
/// (TOML grid spec a implementar quando necessário.)
pub fn build_strategy(name: &str, _grid_spec: &str) -> Result<Box<dyn Strategy>> {
    match name {
        "momentum"      => Ok(Box::new(momentum::Momentum::with_default())),
        "vwap_pullback" => Ok(Box::new(vwap_pullback::VwapPullback::with_default())),
        "ema_scalp"     => Ok(Box::new(ema_scalp::EmaScalp::with_default())),
        "orb"           => Ok(Box::new(orb::Orb::with_default())),
        "pdhl"          => Ok(Box::new(pdhl::Pdhl::with_default())),
        "range"         => Ok(Box::new(range::RangeStrategy::with_default())),
        other           => bail!("unknown strategy: {other}"),
    }
}
