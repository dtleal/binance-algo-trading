//! Detail mode: 1 strategy + 1 exit + params únicos → trade-a-trade output.
//!
//! Substitui os scripts Python `scripts/backtest_detail*.py`.

use crate::types::*;
use crate::indicator::{vwap_rolling, ema};
use crate::strategy;
use crate::exit;
use anyhow::{bail, Result};
use chrono::{DateTime, Utc};

/// Params unificados — cada strategy usa o que precisa, ignora o resto.
#[derive(Debug, Default, Clone)]
pub struct DetailParams {
    // Comuns
    pub min_bars:           Option<usize>,
    pub confirm_bars:       Option<usize>,
    pub vwap_prox:          Option<f64>,
    pub vwap_window:        Option<u32>,
    pub max_trades_per_day: Option<usize>,

    // VWAPPullback
    pub ema_period:         Option<usize>,

    // EMAScalp
    pub fast_period:        Option<usize>,
    pub slow_period:        Option<usize>,

    // ORB
    pub range_mins:         Option<u16>,
    pub buffer_pct:         Option<f64>,

    // PDHL
    pub prox_pct:           Option<f64>,

    // Momentum kind ("mom_short", "mom_long", "rej_short", "rej_long")
    pub momentum_kind:      Option<String>,
    pub vol_filter:         Option<bool>,
    pub trend_filter:       Option<bool>,
    pub entry_window:       Option<(u16, u16)>,
}

#[derive(Debug, Clone)]
pub struct Trade {
    pub n: usize,
    pub entry_time:    DateTime<Utc>,
    pub entry_price:   f64,
    pub exit_time:     DateTime<Utc>,
    pub exit_price:    f64,
    pub direction:     Direction,
    pub pnl_pct:       f64,    // já com fees
    pub pnl_dollar:    f64,
    pub is_eod:        bool,
    pub capital_after: f64,
    pub drawdown_pct:  f64,
}

#[derive(Debug, Default)]
pub struct DetailResult {
    pub trades:          Vec<Trade>,
    pub final_capital:   f64,
    pub max_dd_pct:      f64,
    pub max_consec_loss: usize,
}

/// Constrói entries usando params únicos (não grid). Defaults razoáveis.
pub fn build_entries(
    strategy: &str,
    candles: &[Candle],
    days: &DayIndex,
    p: &DetailParams,
) -> Result<Vec<Entry>> {
    use strategy::{vwap_pullback, momentum, ema_scalp, orb, pdhl};
    match strategy {
        "vwap_pullback" => {
            let vwap = vwap_rolling(candles, p.vwap_window.unwrap_or(5));
            let em   = ema(candles, p.ema_period.unwrap_or(200));
            Ok(vwap_pullback::find_entries_pullback(
                candles, days, &vwap, &em,
                p.min_bars.unwrap_or(5),
                p.confirm_bars.unwrap_or(1),
                p.vwap_prox.unwrap_or(0.005),
                p.max_trades_per_day.unwrap_or(4),
            ))
        }
        "ema_scalp" => {
            let fast = ema(candles, p.fast_period.unwrap_or(8));
            let slow = ema(candles, p.slow_period.unwrap_or(21));
            Ok(ema_scalp::find_entries_ema_scalp(
                candles, days, &fast, &slow,
                p.max_trades_per_day.unwrap_or(4),
            ))
        }
        "orb" => Ok(orb::find_entries_orb(
            candles, days,
            p.range_mins.unwrap_or(30),
            p.buffer_pct.unwrap_or(0.002),
        )),
        "pdhl" => Ok(pdhl::find_entries_pdhl(
            candles, days,
            p.prox_pct.unwrap_or(0.002),
            p.confirm_bars.unwrap_or(2),
        )),
        "momentum" => {
            let kind = match p.momentum_kind.as_deref().unwrap_or("mom_short") {
                "mom_short" => momentum::Kind::MomShort,
                "mom_long"  => momentum::Kind::MomLong,
                "rej_short" => momentum::Kind::RejShort,
                "rej_long"  => momentum::Kind::RejLong,
                other       => bail!("unknown momentum kind: {other}"),
            };
            let vwap = vwap_rolling(candles, p.vwap_window.unwrap_or(5));
            let (es, ec) = p.entry_window.unwrap_or((ENTRY_START_DEFAULT, ENTRY_CUTOFF_DEFAULT));
            Ok(momentum::find_entries(
                candles, days, &vwap, kind,
                p.min_bars.unwrap_or(5),
                p.vol_filter.unwrap_or(false),
                p.confirm_bars.unwrap_or(0),
                p.trend_filter.unwrap_or(false),
                es, ec,
                p.vwap_prox.unwrap_or(0.005),
            ))
        }
        "range" => bail!(
            "range strategy não suporta detail mode (entry+exit acoplados, fluxo monolítico)"
        ),
        other => bail!("unknown strategy: {other}"),
    }
}

/// Executa o backtest detalhado: 1 strategy + 1 exit, output trade-a-trade.
pub fn run_detail(
    entries: &[Entry],
    candles: &[Candle],
    exit_fn: ExitFn,
    pos_size: f64,
) -> DetailResult {
    let mut trades = Vec::with_capacity(entries.len());
    let mut capital = INITIAL_CAPITAL;
    let mut peak    = capital;
    let mut max_dd  = 0.0_f64;
    let mut cl      = 0_usize;
    let mut mcl     = 0_usize;

    for (i, e) in entries.iter().enumerate() {
        let result = exit::evaluate(e, candles, &exit_fn);
        let pnl_pct_raw = match e.direction {
            Direction::Short => (e.entry_price - result.exit_price) / e.entry_price,
            Direction::Long  => (result.exit_price - e.entry_price) / e.entry_price,
        };
        let size = capital * pos_size;
        let net  = size * pnl_pct_raw - size * FEE_PCT * 2.0;
        capital += net;
        peak = peak.max(capital);
        let dd = if peak > 0.0 { (peak - capital) / peak * 100.0 } else { 0.0 };
        max_dd = max_dd.max(dd);
        if net > 0.0 { cl = 0; } else { cl += 1; mcl = mcl.max(cl); }

        // Encontra o candle correspondente ao exit_price para timestamp.
        let exit_time = locate_exit_time(candles, e, &result);

        trades.push(Trade {
            n: i + 1,
            entry_time:  candles[entry_candle_idx(candles, e)].open_time,
            entry_price: e.entry_price,
            exit_time,
            exit_price:  result.exit_price,
            direction:   e.direction,
            pnl_pct:     net / size * 100.0,    // PnL líquido %
            pnl_dollar:  net,
            is_eod:      result.is_eod,
            capital_after: capital,
            drawdown_pct:  dd,
        });
    }

    DetailResult {
        trades,
        final_capital:   capital,
        max_dd_pct:      max_dd,
        max_consec_loss: mcl,
    }
}

/// Acha o índice do candle de entry. Entry guarda price+minute mas não o índice
/// direto — recuperamos buscando no rest_start - 1 slot ou aproximação.
fn entry_candle_idx(candles: &[Candle], e: &Entry) -> usize {
    if e.rest_start == 0 { return 0; }
    // O entry é a barra ANTES de rest_start (find_entries usa rest_start = i+1)
    e.rest_start.saturating_sub(1).min(candles.len() - 1)
}

/// Acha o candle de saída (qualquer rule entre rest_start..rest_end que match).
/// Para is_eod=true, é o último com minute_of_day >= END_OF_DAY (ou fallback).
fn locate_exit_time(candles: &[Candle], e: &Entry, result: &ExitResult) -> DateTime<Utc> {
    if result.is_eod {
        // Procura na fatia rest_start..rest_end o primeiro candle de EOD.
        for j in e.rest_start..e.rest_end.min(candles.len()) {
            if candles[j].minute_of_day >= END_OF_DAY {
                return candles[j].open_time;
            }
        }
        // Fallback: último candle do range
        let idx = e.rest_end.saturating_sub(1).min(candles.len() - 1);
        return candles[idx].open_time;
    }
    // Não-EOD: aproxima procurando o primeiro candle cujo OHLC bate o exit_price.
    // Para fixed TP/SL: SL/TP price exato; para trailing: aprox via low/high.
    for j in e.rest_start..e.rest_end.min(candles.len()) {
        let c = &candles[j];
        let touched = match e.direction {
            Direction::Short => c.high >= result.exit_price || c.low <= result.exit_price,
            Direction::Long  => c.low  <= result.exit_price || c.high >= result.exit_price,
        };
        if touched { return c.open_time; }
    }
    // Fallback
    let idx = e.rest_start.min(candles.len() - 1);
    candles[idx].open_time
}
