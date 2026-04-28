//! Helper compartilhado: dado um combo (strategy + params + exit + params + slice
//! de candles), produz `RunMetrics`. Reusado por sweep, detail, overfit, walkforward.
//!
//! Range strategy tem path próprio (entry + exit acoplados, ATR/ADX precomputados).

use anyhow::{anyhow, bail, Result};
use serde_json::Value;

use crate::indicator::{adx_wilder, atr_wilder};
use crate::types::*;
use crate::detail::DetailParams;
use crate::strategy::range;
use crate::exit;

const ATR_PERIOD: usize = 14;
const ADX_PERIOD: usize = 14;

/// Avalia um combo (strategy_params + exit_params) sobre um slice de candles.
/// Retorna métricas finais (sem trade list — usar `detail::run_detail` pra isso).
pub fn evaluate_combo(
    candles: &[Candle],
    days: &DayIndex,
    strategy: &str,
    strategy_params: &Value,
    exit_name: &str,
    exit_params: &Value,
    pos_size: f64,
) -> Result<RunMetrics> {
    evaluate_combo_with_mtf(candles, days, None, strategy, strategy_params, exit_name, exit_params, pos_size)
}

/// Versão estendida: aceita candles do TF maior pra Range strategy MTF check.
#[allow(clippy::too_many_arguments)]
pub fn evaluate_combo_with_mtf(
    candles: &[Candle],
    days: &DayIndex,
    mtf_candles: Option<&[Candle]>,
    strategy: &str,
    strategy_params: &Value,
    exit_name: &str,
    exit_params: &Value,
    pos_size: f64,
) -> Result<RunMetrics> {
    if candles.is_empty() {
        return Ok(RunMetrics { final_capital: INITIAL_CAPITAL, ..Default::default() });
    }

    if strategy == "range" {
        return evaluate_range(candles, mtf_candles, strategy_params);
    }

    // Path padrão: build_entries + per-entry exit dispatch
    let dp = json_to_detail_params(strategy, strategy_params)?;
    let entries = crate::detail::build_entries(strategy, candles, days, &dp)?;
    let exit_fn = exit_fn_from_json(exit_name, exit_params)?;
    Ok(run_loop(&entries, candles, &exit_fn, pos_size))
}

fn evaluate_range(
    candles: &[Candle],
    mtf_candles: Option<&[Candle]>,
    params: &Value,
) -> Result<RunMetrics> {
    // Precompute indicators
    let atr = atr_wilder(candles, ATR_PERIOD);
    let adx = adx_wilder(candles, ADX_PERIOD);
    let atr_pct: Vec<f64> = atr.iter().enumerate()
        .map(|(i, a)| if candles[i].close > 0.0 { a / candles[i].close * 100.0 } else { 0.0 })
        .collect();

    let p = params.as_object()
        .ok_or_else(|| anyhow!("range params not an object"))?;
    let g = |k: &str| p.get(k)
        .ok_or_else(|| anyhow!("range params missing '{k}'"));

    let adx_thresh        = g("adx_thresh")?.as_f64().ok_or_else(|| anyhow!("adx_thresh"))?;
    let atr_pct_thresh    = g("atr_pct_thresh")?.as_f64().ok_or_else(|| anyhow!("atr_pct_thresh"))?;
    let range_lookback    = g("range_lookback")?.as_u64().ok_or_else(|| anyhow!("range_lookback"))? as usize;
    let zone_pct          = g("zone_pct")?.as_f64().ok_or_else(|| anyhow!("zone_pct"))?;
    let tp_range_pct      = g("tp_range_pct")?.as_f64().ok_or_else(|| anyhow!("tp_range_pct"))?;
    let sl_range_pct      = g("sl_range_pct")?.as_f64().ok_or_else(|| anyhow!("sl_range_pct"))?;
    let recent_thresh_pct = g("recent_thresh_pct")?.as_f64().ok_or_else(|| anyhow!("recent_thresh_pct"))?;
    let max_orders        = g("max_orders")?.as_u64().ok_or_else(|| anyhow!("max_orders"))? as usize;
    let pos_size          = g("pos_size")?.as_f64().ok_or_else(|| anyhow!("pos_size"))?;

    // MQL5 alignment params (default ON se ausentes)
    let mtf_enabled       = p.get("mtf_enabled").and_then(|v| v.as_bool()).unwrap_or(true);
    let close_at_opposite = p.get("close_at_opposite").and_then(|v| v.as_bool()).unwrap_or(true);
    let close_on_break    = p.get("close_on_range_break").and_then(|v| v.as_bool()).unwrap_or(false);

    // MTF: pré-computa se enabled E há candles
    let (mtf_adx_vec, mtf_idx_map_vec) = if mtf_enabled {
        match mtf_candles {
            Some(mtf) if !mtf.is_empty() => (
                Some(adx_wilder(mtf, ADX_PERIOD)),
                Some(range::build_mtf_idx_map(candles, mtf)),
            ),
            _ => (None, None),
        }
    } else { (None, None) };

    // detection_mode: opt-in geometric via JSON. Default = Indicator (MQL5).
    let detection_mode = match p.get("detection_mode").and_then(|v| v.as_str()) {
        Some("geometric") => {
            let touch_threshold_pct = p.get("touch_threshold_pct").and_then(|v| v.as_f64()).unwrap_or(15.0);
            let min_touches_each_side = p.get("min_touches_each_side").and_then(|v| v.as_u64()).unwrap_or(2) as usize;
            let max_size_pct = p.get("max_size_pct").and_then(|v| v.as_f64()).unwrap_or(2.0);
            range::DetectionMode::Geometric { touch_threshold_pct, min_touches_each_side, max_size_pct }
        }
        _ => range::DetectionMode::Indicator,
    };

    Ok(range::run_range_backtest(
        candles, &adx, &atr_pct,
        mtf_adx_vec.as_deref(),
        mtf_idx_map_vec.as_deref(),
        adx_thresh, atr_pct_thresh, range_lookback,
        zone_pct, tp_range_pct, sl_range_pct,
        recent_thresh_pct, max_orders, pos_size,
        close_at_opposite, close_on_break,
        detection_mode,
    ))
}

fn run_loop(entries: &[Entry], candles: &[Candle], f: &ExitFn, pos_size: f64) -> RunMetrics {
    let mut capital = INITIAL_CAPITAL;
    let mut peak    = capital;
    let mut max_dd  = 0.0_f64;
    let mut wins    = 0_usize;
    let mut losses  = 0_usize;
    let mut eods    = 0_usize;
    let mut cl      = 0_usize;
    let mut mcl     = 0_usize;

    for entry in entries {
        let result = exit::evaluate(entry, candles, f);
        let pnl_pct = match entry.direction {
            Direction::Short => (entry.entry_price - result.exit_price) / entry.entry_price,
            Direction::Long  => (result.exit_price - entry.entry_price) / entry.entry_price,
        };
        let size = capital * pos_size;
        let net  = size * pnl_pct - size * FEE_PCT * 2.0;
        capital += net;
        if net > 0.0 { wins += 1; cl = 0; } else { losses += 1; cl += 1; mcl = mcl.max(cl); }
        if result.is_eod { eods += 1; }
        peak = peak.max(capital);
        let dd = if peak > 0.0 { (peak - capital) / peak } else { 0.0 };
        max_dd = max_dd.max(dd);
    }

    RunMetrics {
        trades: entries.len(),
        wins, losses, eods,
        final_capital: capital,
        max_dd_pct: max_dd * 100.0,
        max_consec_loss: mcl,
    }
}

fn json_to_detail_params(strategy: &str, params: &Value) -> Result<DetailParams> {
    let p = params.as_object()
        .ok_or_else(|| anyhow!("strategy_params not an object"))?;

    let opt_u = |k: &str| p.get(k).and_then(|v| v.as_u64()).map(|x| x as usize);
    let opt_u16 = |k: &str| p.get(k).and_then(|v| v.as_u64()).map(|x| x as u16);
    let opt_u32 = |k: &str| p.get(k).and_then(|v| v.as_u64()).map(|x| x as u32);
    let opt_f = |k: &str| p.get(k).and_then(|v| v.as_f64());
    let opt_b = |k: &str| p.get(k).and_then(|v| v.as_bool());
    let opt_s = |k: &str| p.get(k).and_then(|v| v.as_str()).map(String::from);

    Ok(DetailParams {
        min_bars:           opt_u("min_bars"),
        confirm_bars:       opt_u("confirm_bars"),
        vwap_prox:          opt_f("vwap_prox"),
        vwap_window:        opt_u32("vwap_window_days"),
        max_trades_per_day: opt_u("max_trades_per_day"),
        ema_period:         opt_u("ema_period"),
        fast_period:        opt_u("fast_period"),
        slow_period:        opt_u("slow_period"),
        range_mins:         opt_u16("range_mins"),
        buffer_pct:         opt_f("buffer_pct"),
        prox_pct:           opt_f("prox_pct"),
        momentum_kind:      opt_s("kind"),
        vol_filter:         opt_b("vol_filter"),
        trend_filter:       opt_b("trend_filter"),
        entry_window:       match (opt_u16("entry_start_min"), opt_u16("entry_cutoff_min")) {
            (Some(a), Some(b)) => Some((a, b)),
            _ => None,
        },
    })
}

fn exit_fn_from_json(name: &str, params: &Value) -> Result<ExitFn> {
    let p = params.as_object()
        .ok_or_else(|| anyhow!("exit_params not an object"))?;
    let g = |k: &str| p.get(k).ok_or_else(|| anyhow!("exit param missing '{k}'"));
    match name {
        "fixed_tp_sl" => Ok(ExitFn::FixedTpSl {
            tp_pct: g("tp_pct")?.as_f64().ok_or_else(|| anyhow!("tp_pct"))?,
            sl_pct: g("sl_pct")?.as_f64().ok_or_else(|| anyhow!("sl_pct"))?,
            max_hold_min: g("max_hold_min")?.as_u64().ok_or_else(|| anyhow!("max_hold_min"))? as u16,
        }),
        "trailing_stop" => Ok(ExitFn::Trailing {
            sl_pct:     g("sl_pct")?.as_f64().ok_or_else(|| anyhow!("sl_pct"))?,
            be_r:       g("be_r")?.as_f64().ok_or_else(|| anyhow!("be_r"))?,
            trail_step: g("trail_step")?.as_f64().ok_or_else(|| anyhow!("trail_step"))?,
            tp_r:       g("tp_r")?.as_f64().ok_or_else(|| anyhow!("tp_r"))?,
            max_hold_min: g("max_hold_min")?.as_u64().ok_or_else(|| anyhow!("max_hold_min"))? as u16,
        }),
        other => bail!("unknown exit: {other}"),
    }
}
