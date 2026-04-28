//! Visualização da Range strategy: pra cada candle de um dia, registra se
//! está "in_range" (ADX≤thresh, ATR%≤thresh, range válido), o range detectado,
//! e onde a estratégia abriu/fechou posições. Output → HTML com candlestick +
//! retângulos sobre as áreas laterais + marcadores de trades.

use anyhow::{anyhow, Result};
use chrono::{DateTime, Duration, NaiveDate, Utc};
use postgres::Client;
use serde_json::Value;

use crate::indicator::{adx_wilder, atr_wilder};
use crate::types::*;

const RANGE_THROTTLE: usize = 1;     // alinhado com MQL5
const ADX_PERIOD: usize = 14;
const ATR_PERIOD: usize = 14;
const WARMUP_DAYS: i64 = 7;
const OPPOSITE_EXTREME_MARGIN_PCT: f64 = 5.0;

#[derive(Debug, Clone)]
pub struct CandleSnapshot {
    pub time: DateTime<Utc>,
    pub open: f64, pub high: f64, pub low: f64, pub close: f64,
    pub adx: f64, pub atr_pct: f64,
    pub in_range: bool,
    pub range_high: Option<f64>,
    pub range_low:  Option<f64>,
}

#[derive(Debug, Clone)]
pub struct LateralRegion {
    pub start_time: DateTime<Utc>,
    pub end_time:   DateTime<Utc>,
    pub range_high: f64,
    pub range_low:  f64,
}

#[derive(Debug, Clone, Copy)]
pub enum TradeEvent { Open, Close }

#[derive(Debug, Clone)]
pub struct TradeMark {
    pub trade_id: u32,               // pareia Open com seu Close
    pub time: DateTime<Utc>,
    pub price: f64,
    pub side: Direction,
    pub event: TradeEvent,
    pub pnl_pct: Option<f64>,        // só preenchido em Close
}

#[derive(Debug)]
pub struct DayDebug {
    pub date: NaiveDate,
    pub snapshots: Vec<CandleSnapshot>,
    pub regions:   Vec<LateralRegion>,
    pub trades:    Vec<TradeMark>,
    pub params:    Value,
}

pub fn run_range_debug(
    client: &mut Client,
    symbol: &Symbol,
    tf: Timeframe,
    target_date: Option<NaiveDate>,
    params: &Value,
    pos_size_pct: f64,
) -> Result<DayDebug> {
    let now = Utc::now();
    let (load_from, load_until, fixed_date) = match target_date {
        Some(d) => {
            let f = (d - Duration::days(WARMUP_DAYS)).and_hms_opt(0,0,0).unwrap().and_utc();
            let u = d.and_hms_opt(23, 59, 59).unwrap().and_utc();
            (f, u, Some(d))
        }
        None => {
            let f = now - Duration::days(30 + WARMUP_DAYS);
            (f, now, None)
        }
    };

    let candles = crate::db::load_candles(client, symbol, tf, Some(load_from), Some(load_until))?;
    if candles.is_empty() { return Err(anyhow!("no candles in range")); }

    // MTF: carrega próximo TF maior
    let mtf_enabled = params.get("mtf_enabled").and_then(|v| v.as_bool()).unwrap_or(true);
    let mtf_candles = match (mtf_enabled, tf.next_mtf()) {
        (true, Some(mtf_tf)) => {
            let mtf = crate::db::load_candles(client, symbol, mtf_tf, Some(load_from), Some(load_until))?;
            if mtf.is_empty() {
                return Err(anyhow!(
                    "MTF range enabled but no {mtf_tf} candles for {symbol} — run:\n  poetry run python -m db.fetch_klines --symbol {symbol} --days N --timeframe {mtf_tf}"
                ));
            }
            Some(mtf)
        }
        _ => None,
    };

    // Instrumenta range_backtest
    let snapshots = instrumented_range_run(&candles, mtf_candles.as_deref(), params, pos_size_pct)?;

    // Define o dia alvo
    let chosen_date = match fixed_date {
        Some(d) => d,
        None => pick_busiest_day(&snapshots)
            .ok_or_else(|| anyhow!("no in_range candles found in last 30 days"))?,
    };

    // Filtra snapshots e trades pro dia escolhido
    let day_snapshots: Vec<_> = snapshots.0.iter()
        .filter(|s| s.time.date_naive() == chosen_date)
        .cloned().collect();
    if day_snapshots.is_empty() {
        return Err(anyhow!("no candles for {chosen_date}"));
    }

    // Inclui opens "carryover" (de dias anteriores) cujos closes caem no dia escolhido.
    use std::collections::HashSet;
    let day_close_ids: HashSet<u32> = snapshots.1.iter()
        .filter(|t| matches!(t.event, TradeEvent::Close) && t.time.date_naive() == chosen_date)
        .map(|t| t.trade_id)
        .collect();
    let day_trades: Vec<_> = snapshots.1.iter()
        .filter(|t| {
            let on_day = t.time.date_naive() == chosen_date;
            let carryover_open = matches!(t.event, TradeEvent::Open)
                && t.time.date_naive() < chosen_date
                && day_close_ids.contains(&t.trade_id);
            on_day || carryover_open
        })
        .cloned().collect();

    let regions = group_regions(&day_snapshots);

    Ok(DayDebug {
        date: chosen_date,
        snapshots: day_snapshots,
        regions,
        trades: day_trades,
        params: params.clone(),
    })
}

/// Versão instrumentada do `run_range_backtest` que registra estados internos.
/// Retorna (snapshots, trades). Espelha a lógica de `range::run_range_backtest`
/// com todas as 5 alinhamentos MQL5 (throttle=1, MTF, no tp_inside_range guard,
/// close_at_opposite_extreme, close_on_range_break).
fn instrumented_range_run(
    candles: &[Candle],
    mtf_candles: Option<&[Candle]>,
    params: &Value,
    pos_size_pct: f64,
) -> Result<(Vec<CandleSnapshot>, Vec<TradeMark>)> {
    let p = params.as_object().ok_or_else(|| anyhow!("params not object"))?;
    let g = |k: &str| p.get(k).ok_or_else(|| anyhow!("missing {k}"));
    let adx_thresh        = g("adx_thresh")?.as_f64().ok_or_else(|| anyhow!("adx_thresh"))?;
    let atr_pct_thresh    = g("atr_pct_thresh")?.as_f64().ok_or_else(|| anyhow!("atr_pct_thresh"))?;
    let range_lookback    = g("range_lookback")?.as_u64().ok_or_else(|| anyhow!("range_lookback"))? as usize;
    let zone_pct          = g("zone_pct")?.as_f64().ok_or_else(|| anyhow!("zone_pct"))?;
    let tp_range_pct      = g("tp_range_pct")?.as_f64().ok_or_else(|| anyhow!("tp_range_pct"))?;
    let sl_range_pct      = g("sl_range_pct")?.as_f64().ok_or_else(|| anyhow!("sl_range_pct"))?;
    let recent_thresh_pct = g("recent_thresh_pct")?.as_f64().ok_or_else(|| anyhow!("recent_thresh_pct"))?;
    let max_orders        = g("max_orders")?.as_u64().ok_or_else(|| anyhow!("max_orders"))? as usize;
    // Defaults MQL5 alignment
    let close_at_opposite = p.get("close_at_opposite").and_then(|v| v.as_bool()).unwrap_or(true);
    let close_on_break    = p.get("close_on_range_break").and_then(|v| v.as_bool()).unwrap_or(false);

    let n = candles.len();
    let atr = atr_wilder(candles, ATR_PERIOD);
    let adx = adx_wilder(candles, ADX_PERIOD);
    let atr_pct: Vec<f64> = atr.iter().enumerate()
        .map(|(i, a)| if candles[i].close > 0.0 { a / candles[i].close * 100.0 } else { 0.0 })
        .collect();
    let highs: Vec<f64> = candles.iter().map(|c| c.high).collect();
    let lows:  Vec<f64> = candles.iter().map(|c| c.low ).collect();

    // MTF
    let (mtf_adx_vec, mtf_idx_map): (Option<Vec<f64>>, Option<Vec<Option<usize>>>) = match mtf_candles {
        Some(mtf) if !mtf.is_empty() => (
            Some(adx_wilder(mtf, ADX_PERIOD)),
            Some(crate::strategy::range::build_mtf_idx_map(candles, mtf)),
        ),
        _ => (None, None),
    };

    let min_history = range_lookback + ADX_PERIOD * 3;
    let mut snapshots = Vec::with_capacity(n);
    let mut trades = Vec::new();

    // Estado da simulação
    #[derive(Clone)] struct Pos { id: u32, side: bool, entry: f64, tp: f64, sl: f64 }
    let mut positions: Vec<Pos> = Vec::new();
    let mut next_trade_id: u32 = 1;
    let mut last_range_calc: usize = 0;
    let mut current_range: Option<(f64, f64, f64)> = None;  // (high, low, size)
    let mut was_in_range: bool = false;

    for i in 0..n {
        let c = &candles[i];
        let mut snap = CandleSnapshot {
            time: c.open_time,
            open: c.open, high: c.high, low: c.low, close: c.close,
            adx: adx[i], atr_pct: atr_pct[i],
            in_range: false, range_high: None, range_low: None,
        };

        if i < min_history {
            snapshots.push(snap);
            continue;
        }

        if i - last_range_calc >= RANGE_THROTTLE || current_range.is_none() {
            let start = i - range_lookback;
            let h = highs[start..i].iter().fold(f64::NEG_INFINITY, |a,b| a.max(*b));
            let l = lows [start..i].iter().fold(f64::INFINITY,     |a,b| a.min(*b));
            let s = h - l;
            current_range = if s > 0.0 { Some((h, l, s)) } else { None };
            last_range_calc = i;
        }

        // MTF check
        let mtf_ok = match (&mtf_adx_vec, &mtf_idx_map) {
            (Some(madx), Some(map)) => map.get(i).copied().flatten()
                .map(|idx| madx.get(idx).copied().unwrap_or(f64::INFINITY) <= adx_thresh)
                .unwrap_or(false),
            _ => true,
        };

        let in_range = match current_range {
            Some((_, _, s)) => adx[i] <= adx_thresh && atr_pct[i] <= atr_pct_thresh && s > 0.0 && mtf_ok,
            None => false,
        };

        if let Some((h, l, _)) = current_range {
            snap.range_high = Some(h);
            snap.range_low  = Some(l);
        }
        snap.in_range = in_range;

        // CloseOnRangeBreak: registra closes na transição
        if was_in_range && !in_range && close_on_break && !positions.is_empty() {
            for pos in positions.drain(..) {
                let pnl = if pos.side { (c.close - pos.entry) / pos.entry }
                          else        { (pos.entry - c.close) / pos.entry };
                trades.push(TradeMark {
                    trade_id: pos.id, time: c.open_time, price: c.close,
                    side: if pos.side { Direction::Long } else { Direction::Short },
                    event: TradeEvent::Close,
                    pnl_pct: Some(pnl * 100.0),
                });
            }
        }
        was_in_range = in_range;

        // Fecha posições no TP/SL (registra trade close)
        let mut remaining = Vec::new();
        for pos in positions.drain(..) {
            let hit_tp = if pos.side { c.high >= pos.tp } else { c.low <= pos.tp };
            let hit_sl = pos.sl > 0.0 && if pos.side { c.low <= pos.sl } else { c.high >= pos.sl };
            if hit_tp || hit_sl {
                let exit = if hit_tp { pos.tp } else { pos.sl };
                let pnl = if pos.side { (exit - pos.entry) / pos.entry } else { (pos.entry - exit) / pos.entry };
                trades.push(TradeMark {
                    trade_id: pos.id,
                    time: c.open_time, price: exit,
                    side: if pos.side { Direction::Long } else { Direction::Short },
                    event: TradeEvent::Close,
                    pnl_pct: Some(pnl * 100.0),
                });
            } else {
                remaining.push(pos);
            }
        }
        positions = remaining;

        // CloseAtOppositeExtreme + nova posição
        if in_range {
            let (rh, rl, rsize) = current_range.unwrap();
            let price = c.close;
            let zone_height = rsize * zone_pct / 100.0;
            let zone_buy  = price <= rl + zone_height;
            let zone_sell = price >= rh - zone_height;

            // Fecha posições no extremo oposto (after TP/SL, antes de abrir nova)
            if close_at_opposite && !positions.is_empty() {
                let margin = rsize * OPPOSITE_EXTREME_MARGIN_PCT / 100.0;
                let zone_for_close = if zone_buy { 0_i8 } else if zone_sell { 1 } else { -1 };
                let mut to_keep = Vec::new();
                for pos in positions.drain(..) {
                    let force_close = match (pos.side, zone_for_close) {
                        (true,  1) if c.high >= rh - margin => true,   // LONG no extremo SELL
                        (false, 0) if c.low  <= rl + margin => true,   // SHORT no extremo BUY
                        _ => false,
                    };
                    if force_close {
                        let pnl = if pos.side { (c.close - pos.entry) / pos.entry }
                                  else        { (pos.entry - c.close) / pos.entry };
                        trades.push(TradeMark {
                            trade_id: pos.id, time: c.open_time, price: c.close,
                            side: if pos.side { Direction::Long } else { Direction::Short },
                            event: TradeEvent::Close, pnl_pct: Some(pnl * 100.0),
                        });
                    } else {
                        to_keep.push(pos);
                    }
                }
                positions = to_keep;
            }

            if positions.len() < max_orders && (zone_buy || zone_sell) {
                let is_long = zone_buy;
                let threshold = rsize * recent_thresh_pct / 100.0;
                let has_recent = positions.iter().any(|p|
                    p.side == is_long && (p.entry - price).abs() < threshold
                );
                if !has_recent {
                    let tp_dist = rsize * tp_range_pct / 100.0;
                    let sl_dist = if sl_range_pct > 0.0 { rsize * sl_range_pct / 100.0 } else { 0.0 };
                    let (tp, sl) = if is_long {
                        (price + tp_dist, if sl_dist > 0.0 { price - sl_dist } else { 0.0 })
                    } else {
                        (price - tp_dist, if sl_dist > 0.0 { price + sl_dist } else { 0.0 })
                    };
                    // (tp_inside_range guard removida — alinha com MQL5)
                    let id = next_trade_id; next_trade_id += 1;
                    positions.push(Pos { id, side: is_long, entry: price, tp, sl });
                    trades.push(TradeMark {
                        trade_id: id, time: c.open_time, price,
                        side: if is_long { Direction::Long } else { Direction::Short },
                        event: TradeEvent::Open, pnl_pct: None,
                    });
                }
            }
        }

        snapshots.push(snap);
        let _ = pos_size_pct;  // não relevante pra debug visual (PnL é %)
    }

    Ok((snapshots, trades))
}

/// Agrupa snapshots in_range consecutivos com MESMO range em LateralRegions.
fn group_regions(snapshots: &[CandleSnapshot]) -> Vec<LateralRegion> {
    let mut regions = Vec::new();
    let mut current: Option<(DateTime<Utc>, DateTime<Utc>, f64, f64)> = None;
    for s in snapshots {
        if s.in_range {
            if let (Some(h), Some(l)) = (s.range_high, s.range_low) {
                match current {
                    Some((start, _last_t, ch, cl)) if (ch - h).abs() < 1e-9 && (cl - l).abs() < 1e-9 => {
                        current = Some((start, s.time, ch, cl));
                    }
                    Some((start, last_t, ch, cl)) => {
                        regions.push(LateralRegion { start_time: start, end_time: last_t, range_high: ch, range_low: cl });
                        current = Some((s.time, s.time, h, l));
                    }
                    None => current = Some((s.time, s.time, h, l)),
                }
            }
        } else if let Some((start, last_t, h, l)) = current.take() {
            regions.push(LateralRegion { start_time: start, end_time: last_t, range_high: h, range_low: l });
        }
    }
    if let Some((start, last_t, h, l)) = current {
        regions.push(LateralRegion { start_time: start, end_time: last_t, range_high: h, range_low: l });
    }
    regions
}

/// Escolhe o dia com mais candles in_range (mais didático pra visualizar).
fn pick_busiest_day(snapshots: &(Vec<CandleSnapshot>, Vec<TradeMark>)) -> Option<NaiveDate> {
    use std::collections::HashMap;
    let mut counts: HashMap<NaiveDate, usize> = HashMap::new();
    for s in &snapshots.0 {
        if s.in_range { *counts.entry(s.time.date_naive()).or_insert(0) += 1; }
    }
    counts.into_iter().max_by_key(|(_, n)| *n).map(|(d, _)| d)
}
