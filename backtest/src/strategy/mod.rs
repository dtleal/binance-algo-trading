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
    /// Nome canônico (vai pra `sweep_results.strategy`).
    fn name(&self) -> &'static str;

    /// Expande grid de params em EntrySets (caminho padrão) ou Runs (Range).
    fn build(&self, ctx: &Ctx<'_>) -> StrategyOutput;
}

/// Factory: nome string → Box<dyn Strategy> com grid carregado.
///
/// O `grid_spec` é um JSON/TOML que cada strategy parseia internamente.
/// Implementação stub no scaffold; preenchida na fase de port.
pub fn build_strategy(name: &str, _grid_spec: &str) -> Result<Box<dyn Strategy>> {
    match name {
        "momentum"      => bail!("momentum: not implemented yet (scaffold)"),
        "vwap_pullback" => bail!("vwap_pullback: not implemented yet (scaffold)"),
        "ema_scalp"     => bail!("ema_scalp: not implemented yet (scaffold)"),
        "orb"           => bail!("orb: not implemented yet (scaffold)"),
        "pdhl"          => bail!("pdhl: not implemented yet (scaffold)"),
        "range"         => bail!("range: not implemented yet (scaffold)"),
        other           => bail!("unknown strategy: {other}"),
    }
}
