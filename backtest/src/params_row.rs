//! Helpers de conversão entre StrategyParamsRow/ExitParamsRow e formatos auxiliares
//! (postgres::Row, serde_json::Value) usados nos consumers do sweep.

use crate::types::*;
use postgres::Row;
use serde_json::{json, Value};
use rust_decimal::prelude::ToPrimitive;

fn dec_to_f64(d: Option<rust_decimal::Decimal>) -> Option<f64> {
    d.and_then(|v| v.to_f64())
}

/// Lê todas as colunas tipadas de strategy params de uma row de sweep_results.
/// A row precisa ter sido SELECT-ada com pelo menos as colunas listadas em
/// `select_strategy_params_cols()`.
pub fn strategy_params_from_row(row: &Row) -> StrategyParamsRow {
    StrategyParamsRow {
        adx_thresh:           dec_to_f64(row.get("adx_thresh")),
        atr_pct_thresh:       dec_to_f64(row.get("atr_pct_thresh")),
        range_lookback:       row.get("range_lookback"),
        zone_pct:             dec_to_f64(row.get("zone_pct")),
        tp_range_pct:         dec_to_f64(row.get("tp_range_pct")),
        sl_range_pct:         dec_to_f64(row.get("sl_range_pct")),
        recent_thresh_pct:    dec_to_f64(row.get("recent_thresh_pct")),
        max_orders:           row.get("max_orders"),
        pos_size:             dec_to_f64(row.get("pos_size")),
        mtf_enabled:          row.get("mtf_enabled"),
        close_at_opposite:    row.get("close_at_opposite"),
        close_on_range_break: row.get("close_on_range_break"),
        mtf_timeframe:        row.get("mtf_timeframe"),
        min_bars:             row.get("min_bars"),
        confirm_bars:         row.get("confirm_bars"),
        vwap_prox:            dec_to_f64(row.get("vwap_prox")),
        vwap_window_days:     row.get("vwap_window_days"),
        ema_period:           row.get("ema_period"),
        max_trades_per_day:   row.get("max_trades_per_day"),
        kind:                 row.get("kind"),
        vol_filter:           row.get("vol_filter"),
        trend_filter:         row.get("trend_filter"),
        entry_window_start:   row.get("entry_window_start"),
        entry_window_end:     row.get("entry_window_end"),
        fast_period:          row.get("fast_period"),
        slow_period:          row.get("slow_period"),
        range_mins:           row.get("range_mins"),
        buffer_pct:           dec_to_f64(row.get("buffer_pct")),
        prox_pct:             dec_to_f64(row.get("prox_pct")),
    }
}

pub fn exit_params_from_row(row: &Row) -> ExitParamsRow {
    ExitParamsRow {
        tp_pct:       dec_to_f64(row.get("tp_pct")),
        sl_pct:       dec_to_f64(row.get("sl_pct")),
        max_hold_min: row.get("max_hold_min"),
        be_r:         dec_to_f64(row.get("be_r")),
        trail_step:   dec_to_f64(row.get("trail_step")),
        tp_r:         dec_to_f64(row.get("tp_r")),
    }
}

/// SQL fragment com TODAS as colunas de strategy params (29). Para SELECTs.
pub const SELECT_STRATEGY_PARAMS_COLS: &str =
    "adx_thresh, atr_pct_thresh, range_lookback, zone_pct, \
     tp_range_pct, sl_range_pct, recent_thresh_pct, max_orders, \
     pos_size, mtf_enabled, close_at_opposite, close_on_range_break, mtf_timeframe, \
     min_bars, confirm_bars, vwap_prox, vwap_window_days, ema_period, \
     max_trades_per_day, kind, vol_filter, trend_filter, \
     entry_window_start, entry_window_end, \
     fast_period, slow_period, range_mins, buffer_pct, prox_pct";

pub const SELECT_EXIT_PARAMS_COLS: &str =
    "tp_pct, sl_pct, max_hold_min, be_r, trail_step, tp_r";

/// Converte StrategyParamsRow → serde_json::Value (formato JSONB legacy).
/// Apenas chaves não-NULL aparecem no objeto.
pub fn strategy_params_to_value(p: &StrategyParamsRow) -> Value {
    let mut m = serde_json::Map::new();
    if let Some(v) = p.adx_thresh           { m.insert("adx_thresh".into(),           json!(v)); }
    if let Some(v) = p.atr_pct_thresh       { m.insert("atr_pct_thresh".into(),       json!(v)); }
    if let Some(v) = p.range_lookback       { m.insert("range_lookback".into(),       json!(v)); }
    if let Some(v) = p.zone_pct             { m.insert("zone_pct".into(),             json!(v)); }
    if let Some(v) = p.tp_range_pct         { m.insert("tp_range_pct".into(),         json!(v)); }
    if let Some(v) = p.sl_range_pct         { m.insert("sl_range_pct".into(),         json!(v)); }
    if let Some(v) = p.recent_thresh_pct    { m.insert("recent_thresh_pct".into(),    json!(v)); }
    if let Some(v) = p.max_orders           { m.insert("max_orders".into(),           json!(v)); }
    if let Some(v) = p.pos_size             { m.insert("pos_size".into(),             json!(v)); }
    if let Some(v) = p.mtf_enabled          { m.insert("mtf_enabled".into(),          json!(v)); }
    if let Some(v) = p.close_at_opposite    { m.insert("close_at_opposite".into(),    json!(v)); }
    if let Some(v) = p.close_on_range_break { m.insert("close_on_range_break".into(), json!(v)); }
    if let Some(ref v) = p.mtf_timeframe    { m.insert("mtf_timeframe".into(),        json!(v)); }
    if let Some(v) = p.min_bars             { m.insert("min_bars".into(),             json!(v)); }
    if let Some(v) = p.confirm_bars         { m.insert("confirm_bars".into(),         json!(v)); }
    if let Some(v) = p.vwap_prox            { m.insert("vwap_prox".into(),            json!(v)); }
    if let Some(v) = p.vwap_window_days     { m.insert("vwap_window_days".into(),     json!(v)); }
    if let Some(v) = p.ema_period           { m.insert("ema_period".into(),           json!(v)); }
    if let Some(v) = p.max_trades_per_day   { m.insert("max_trades_per_day".into(),   json!(v)); }
    if let Some(ref v) = p.kind             { m.insert("kind".into(),                 json!(v)); }
    if let Some(v) = p.vol_filter           { m.insert("vol_filter".into(),           json!(v)); }
    if let Some(v) = p.trend_filter         { m.insert("trend_filter".into(),         json!(v)); }
    if let Some(v) = p.entry_window_start   { m.insert("entry_start_min".into(),      json!(v)); }
    if let Some(v) = p.entry_window_end     { m.insert("entry_cutoff_min".into(),     json!(v)); }
    if let Some(v) = p.fast_period          { m.insert("fast_period".into(),          json!(v)); }
    if let Some(v) = p.slow_period          { m.insert("slow_period".into(),          json!(v)); }
    if let Some(v) = p.range_mins           { m.insert("range_mins".into(),           json!(v)); }
    if let Some(v) = p.buffer_pct           { m.insert("buffer_pct".into(),           json!(v)); }
    if let Some(v) = p.prox_pct             { m.insert("prox_pct".into(),             json!(v)); }
    Value::Object(m)
}

pub fn exit_params_to_value(p: &ExitParamsRow) -> Value {
    let mut m = serde_json::Map::new();
    if let Some(v) = p.tp_pct       { m.insert("tp_pct".into(),       json!(v)); }
    if let Some(v) = p.sl_pct       { m.insert("sl_pct".into(),       json!(v)); }
    if let Some(v) = p.max_hold_min { m.insert("max_hold_min".into(), json!(v)); }
    if let Some(v) = p.be_r         { m.insert("be_r".into(),         json!(v)); }
    if let Some(v) = p.trail_step   { m.insert("trail_step".into(),   json!(v)); }
    if let Some(v) = p.tp_r         { m.insert("tp_r".into(),         json!(v)); }
    Value::Object(m)
}

/// Colunas numéricas de strategy params perturbadas no param_sensitivity test.
/// Bools / strings são fixados, só numéricos variam.
pub fn numeric_strategy_param_cols(strategy: &str) -> &'static [&'static str] {
    match strategy {
        "range" => &[
            "adx_thresh", "atr_pct_thresh", "range_lookback", "zone_pct",
            "tp_range_pct", "sl_range_pct", "recent_thresh_pct", "max_orders", "pos_size",
        ],
        "vwap_pullback" => &[
            "min_bars", "confirm_bars", "vwap_prox", "vwap_window_days",
            "ema_period", "max_trades_per_day",
        ],
        "momentum" => &[
            "min_bars", "confirm_bars", "vwap_prox", "vwap_window_days",
            "entry_window_start", "entry_window_end",
        ],
        "ema_scalp" => &["fast_period", "slow_period", "max_trades_per_day"],
        "orb"       => &["range_mins", "buffer_pct"],
        "pdhl"      => &["prox_pct", "confirm_bars"],
        _           => &[],
    }
}

pub fn numeric_exit_param_cols(exit_name: &str) -> &'static [&'static str] {
    match exit_name {
        "fixed_tp_sl"   => &["tp_pct", "sl_pct", "max_hold_min"],
        "trailing_stop" => &["sl_pct", "be_r", "trail_step", "tp_r", "max_hold_min"],
        _               => &[],
    }
}
