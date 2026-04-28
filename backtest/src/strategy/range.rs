//! Range strategy — mean-reversion em ranges horizontais.
//!
//! Portado de `backtest_sweep_range/src/main.rs::run_backtest`.
//!
//! Caso especial: retorna `StrategyOutput::Runs` (exit baked-in, ignora exits externos).
//! Cada combinação de params produz um `MonolithicRun` que executa o backtest completo
//! (entries + exits acoplados) e devolve `RunMetrics`.

use std::sync::Arc;

use chrono::{DateTime, Utc};

use crate::indicator::{atr_wilder, adx_wilder};
use crate::types::*;

const RANGE_THROTTLE: usize = 1;       // alinhado com MQL5 (60s ≈ 1 candle em 5m)
const ADX_PERIOD: usize = 14;
const ATR_PERIOD: usize = 14;
const OPPOSITE_EXTREME_MARGIN_PCT: f64 = 5.0;   // 5% do range — match MQL5

#[derive(Clone, Copy)]
pub struct Range {
    pub high: f64,
    pub low:  f64,
    pub size: f64,
}

#[derive(Clone, Copy, PartialEq)]
enum Zone { Buy, Sell, Neutral }

#[derive(Clone)]
struct Position {
    side:        bool,    // true = long
    entry_price: f64,
    tp_price:    f64,
    sl_price:    f64,     // 0.0 = sem SL
    qty_pct:     f64,
}

fn detect_range(highs: &[f64], lows: &[f64], end_idx: usize, lookback: usize) -> Option<Range> {
    if end_idx < lookback { return None; }
    let start = end_idx - lookback;
    let high = highs[start..end_idx].iter().fold(f64::NEG_INFINITY, |a, b| a.max(*b));
    let low  = lows [start..end_idx].iter().fold(f64::INFINITY,     |a, b| a.min(*b));
    let size = high - low;
    if size <= 0.0 { return None; }
    Some(Range { high, low, size })
}

/// State machine do modo Visual. Avança 1 candle, retorna (estado novo, range ativo).
/// Estado None = NoRange. Some(s) com confirmed=false = Forming. Some(s) com confirmed=true = Active.
#[allow(clippy::too_many_arguments)]
pub fn visual_step(
    state: Option<VisualRangeState>,
    highs: &[f64], lows: &[f64], closes: &[f64], i: usize,
    confirmation_candles: usize, break_candles: usize,
    tolerance_pct: f64, touch_threshold_pct: f64,
    min_touches_each_side: usize, max_size_pct: f64, lookback: usize,
) -> (Option<VisualRangeState>, Option<Range>) {
    let high_i = highs[i]; let low_i = lows[i]; let close_i = closes[i];

    match state {
        None => {
            // Detecta candidato baseado em lookback passado (filtro: parece range?).
            // Mas RESETA bounds pro candle atual — bounds expandem apenas com candles
            // posteriores, evitando "rectangle preso em preços antigos".
            let candidate = detect_geometric_range(
                highs, lows, i + 1, lookback,
                touch_threshold_pct, min_touches_each_side, max_size_pct, close_i,
            );
            match candidate {
                Some(_) => (Some(VisualRangeState {
                    high: high_i, low: low_i,                  // expansivo (visual)
                    anchor_high: high_i, anchor_low: low_i,    // fixo (tolerance)
                    confirmed: false,
                    candles_inside: 1, candles_broken: 0,
                }), None),
                None => (None, None),
            }
        }
        Some(mut s) => {
            // Tolerance é % do RANGE SIZE (não do preço). Banda é anchor ± tolerance*size.
            // Ex: anchor=[77800,78000] (size $200), tolerance_pct=25 → banda [77750,78050].
            let anchor_size = (s.anchor_high - s.anchor_low).max(1.0);
            let cushion = anchor_size * tolerance_pct / 100.0;
            let band_high = s.anchor_high + cushion;
            let band_low  = s.anchor_low  - cushion;
            let in_band = high_i <= band_high && low_i >= band_low;

            if !s.confirmed {
                // Forming: expande bounds com candles que entram, conta confirmação
                if in_band {
                    if high_i > s.high { s.high = high_i; }
                    if low_i  < s.low  { s.low  = low_i; }
                    s.candles_inside += 1;
                    if s.candles_inside >= confirmation_candles {
                        s.confirmed = true;
                    }
                    let r = if s.confirmed { Some(Range { high: s.high, low: s.low, size: s.high - s.low }) } else { None };
                    (Some(s), r)
                } else {
                    (None, None)
                }
            } else {
                // Active
                if in_band {
                    if high_i > s.high { s.high = high_i; }
                    if low_i  < s.low  { s.low  = low_i; }
                    s.candles_broken = 0;
                } else {
                    s.candles_broken += 1;
                }
                if s.candles_broken >= break_candles {
                    (None, None)   // Broken
                } else {
                    (Some(s), Some(Range { high: s.high, low: s.low, size: s.high - s.low }))
                }
            }
        }
    }
}

/// Geometric range detection — alinha com percepção visual (touches dos extremos).
/// Substitui filtros ADX/ATR.
#[allow(clippy::too_many_arguments)]
pub fn detect_geometric_range(
    highs: &[f64], lows: &[f64], end_idx: usize,
    lookback: usize,
    touch_threshold_pct: f64,    // ex: 15 = 15% do range_size
    min_touches_each_side: usize, // ex: 2
    max_size_pct: f64,            // ex: 2 = range_size ≤ 2% do preço
    current_price: f64,
) -> Option<Range> {
    if end_idx < lookback { return None; }
    let start = end_idx - lookback;
    let high = highs[start..end_idx].iter().fold(f64::NEG_INFINITY, |a, b| a.max(*b));
    let low  = lows [start..end_idx].iter().fold(f64::INFINITY,     |a, b| a.min(*b));
    let size = high - low;
    if size <= 0.0 { return None; }
    // Filtro: range tight (size / preço atual ≤ max_size_pct)
    if size / current_price * 100.0 > max_size_pct { return None; }
    // Touches
    let touch_dist = size * touch_threshold_pct / 100.0;
    let top_zone = high - touch_dist;
    let bot_zone = low  + touch_dist;
    let touches_top = highs[start..end_idx].iter().filter(|&&x| x >= top_zone).count();
    let touches_bot = lows [start..end_idx].iter().filter(|&&x| x <= bot_zone).count();
    if touches_top < min_touches_each_side || touches_bot < min_touches_each_side {
        return None;
    }
    Some(Range { high, low, size })
}

fn price_zone(price: f64, range: &Range, zone_pct: f64) -> Zone {
    let zone_height = range.size * zone_pct / 100.0;
    if price <= range.low + zone_height  { return Zone::Buy;  }
    if price >= range.high - zone_height { return Zone::Sell; }
    Zone::Neutral
}

/// Pré-computa mapa base→MTF index (uma vez por sweep, não por combo).
/// Usa `close_time <= base.open_time` pra evitar lookahead bias.
pub fn build_mtf_idx_map(base: &[Candle], mtf: &[Candle]) -> Vec<Option<usize>> {
    base.iter().map(|c| {
        let pos = mtf.partition_point(|m| m.close_time <= c.open_time);
        if pos == 0 { None } else { Some(pos - 1) }
    }).collect()
}

/// Modo de detecção do range.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum DetectionMode {
    /// MQL5-aligned: ADX(14) + ATR%(14) thresholds + MTF.
    Indicator,
    /// Geometric: range size, touches dos extremos. Sem ADX/ATR.
    Geometric { touch_threshold_pct: f64, min_touches_each_side: usize, max_size_pct: f64 },
    /// Visual: state machine — confirma range após N candles dentro, persiste
    /// enquanto preço respeitar tolerance, quebra após M closes fora.
    /// Match visão humana: 1 macro-region por consolidação, tolera spikes.
    Visual {
        confirmation_candles: usize,    // N candles forming antes de Active (ex: 5)
        break_candles: usize,            // M closes fora antes de Broken (ex: 3)
        tolerance_pct: f64,              // % além das bordas que conta como "dentro" (ex: 5)
        touch_threshold_pct: f64,        // pra detecção inicial (ex: 15)
        min_touches_each_side: usize,    // pra detecção inicial (ex: 3)
        max_size_pct: f64,               // ex: 5
    },
}

/// Estado do range no modo Visual (state machine).
/// `anchor_*` é fixo (pra tolerância do break); `high/low` expandem (pra visualização).
#[derive(Clone, Copy, Debug)]
pub struct VisualRangeState {
    pub high: f64,           // expansivo (max visto)
    pub low:  f64,           // expansivo (min visto)
    pub anchor_high: f64,    // fixo no início (tolerance check)
    pub anchor_low:  f64,    // fixo no início
    pub confirmed: bool,
    pub candles_inside: usize,
    pub candles_broken: usize,
}

#[allow(clippy::too_many_arguments)]
pub fn run_range_backtest(
    candles: &[Candle],
    adx: &[f64],
    atr_pct: &[f64],
    mtf_adx: Option<&[f64]>,
    mtf_idx_map: Option<&[Option<usize>]>,
    adx_thresh:        f64,
    atr_pct_thresh:    f64,
    range_lookback:    usize,
    zone_pct:          f64,
    tp_range_pct:      f64,
    sl_range_pct:      f64,
    recent_thresh_pct: f64,
    max_orders:        usize,
    pos_size_pct:      f64,
    close_at_opposite: bool,
    close_on_break:    bool,
    detection_mode:    DetectionMode,       // novo
) -> RunMetrics {
    let n = candles.len();
    let min_history = range_lookback + ADX_PERIOD * 3;
    if n < min_history {
        return RunMetrics { final_capital: INITIAL_CAPITAL, ..Default::default() };
    }

    // Slices high/low pra detect_range (o ATR/ADX já vem precomputado).
    let highs: Vec<f64> = candles.iter().map(|c| c.high).collect();
    let lows:  Vec<f64> = candles.iter().map(|c| c.low ).collect();

    let mut capital = INITIAL_CAPITAL;
    let mut peak    = capital;
    let mut max_dd  = 0.0_f64;
    let mut wins    = 0_usize;
    let mut losses  = 0_usize;
    let mut cl      = 0_usize;
    let mut mcl     = 0_usize;
    let mut positions: Vec<Position> = Vec::new();
    let mut last_range_calc: usize = 0;
    let mut current_range: Option<Range> = None;
    let mut was_in_range: bool = false;

    for i in min_history..n {
        let c = &candles[i];

        if i - last_range_calc >= RANGE_THROTTLE || current_range.is_none() {
            current_range = match detection_mode {
                DetectionMode::Indicator => detect_range(&highs, &lows, i, range_lookback),
                DetectionMode::Geometric { touch_threshold_pct, min_touches_each_side, max_size_pct } =>
                    detect_geometric_range(
                        &highs, &lows, i, range_lookback,
                        touch_threshold_pct, min_touches_each_side, max_size_pct,
                        c.close,
                    ),
                DetectionMode::Visual { .. } => detect_range(&highs, &lows, i, range_lookback),
                // Nota: Visual em sweep não está implementado ainda (só em range-debug).
                // Cai pro classic detect_range. Pra Visual no sweep, precisa state machine.
            };
            last_range_calc = i;
        }

        // in_range depende do modo
        let in_range = match (detection_mode, &current_range) {
            (DetectionMode::Indicator, Some(r)) => {
                let mtf_ok = match (mtf_adx, mtf_idx_map) {
                    (Some(madx), Some(map)) => map.get(i).copied().flatten()
                        .map(|idx| madx.get(idx).copied().unwrap_or(f64::INFINITY) <= adx_thresh)
                        .unwrap_or(false),
                    _ => true,
                };
                adx[i] <= adx_thresh && atr_pct[i] <= atr_pct_thresh && r.size > 0.0 && mtf_ok
            }
            (DetectionMode::Geometric { .. }, Some(r)) => r.size > 0.0,
            (DetectionMode::Visual { .. }, Some(r)) => r.size > 0.0,  // simplificado
            (_, None) => false,
        };

        // CloseOnRangeBreak: transição in_range → !in_range fecha tudo no close atual
        if was_in_range && !in_range && close_on_break && !positions.is_empty() {
            for pos in positions.drain(..) {
                let pnl = if pos.side { (c.close - pos.entry_price) / pos.entry_price }
                          else        { (pos.entry_price - c.close) / pos.entry_price };
                let size = capital * pos.qty_pct;
                let net  = size * pnl - size * FEE_PCT * 2.0;
                capital += net;
                peak = peak.max(capital);
                let dd = if peak > 0.0 { (peak - capital) / peak } else { 0.0 };
                max_dd = max_dd.max(dd);
                if net > 0.0 { wins += 1; cl = 0; } else { losses += 1; cl += 1; mcl = mcl.max(cl); }
            }
        }
        was_in_range = in_range;

        // Step 4: TP/SL nas posições abertas
        let mut remaining: Vec<Position> = Vec::new();
        for pos in positions.drain(..) {
            let hit_tp = if pos.side { c.high >= pos.tp_price } else { c.low <= pos.tp_price };
            let hit_sl = pos.sl_price > 0.0 && if pos.side {
                c.low <= pos.sl_price
            } else {
                c.high >= pos.sl_price
            };

            if hit_tp || hit_sl {
                let exit_price = if hit_tp { pos.tp_price } else { pos.sl_price };
                let pnl = if pos.side {
                    (exit_price - pos.entry_price) / pos.entry_price
                } else {
                    (pos.entry_price - exit_price) / pos.entry_price
                };
                let size = capital * pos.qty_pct;
                let net  = size * pnl - size * FEE_PCT * 2.0;
                capital += net;
                peak = peak.max(capital);
                let dd = if peak > 0.0 { (peak - capital) / peak } else { 0.0 };
                max_dd = max_dd.max(dd);
                if net > 0.0 { wins += 1; cl = 0; } else { losses += 1; cl += 1; mcl = mcl.max(cl); }
            } else {
                remaining.push(pos);
            }
        }
        positions = remaining;

        if !in_range { continue; }
        let range = match current_range { Some(ref r) => r, None => continue };

        let price = c.close;
        let zone  = price_zone(price, range, zone_pct);

        // CloseAtOppositeExtreme: fecha posições contra-tendência no extremo do range
        if close_at_opposite && !positions.is_empty() {
            let margin = range.size * OPPOSITE_EXTREME_MARGIN_PCT / 100.0;
            let mut to_keep = Vec::new();
            for pos in positions.drain(..) {
                let force_close = match (pos.side, zone) {
                    (true,  Zone::Sell) if c.high >= range.high - margin => true,
                    (false, Zone::Buy)  if c.low  <= range.low  + margin => true,
                    _ => false,
                };
                if force_close {
                    let exit_price = c.close;
                    let pnl = if pos.side { (exit_price - pos.entry_price) / pos.entry_price }
                              else        { (pos.entry_price - exit_price) / pos.entry_price };
                    let size = capital * pos.qty_pct;
                    let net  = size * pnl - size * FEE_PCT * 2.0;
                    capital += net;
                    peak = peak.max(capital);
                    let dd = if peak > 0.0 { (peak - capital) / peak } else { 0.0 };
                    max_dd = max_dd.max(dd);
                    if net > 0.0 { wins += 1; cl = 0; } else { losses += 1; cl += 1; mcl = mcl.max(cl); }
                } else {
                    to_keep.push(pos);
                }
            }
            positions = to_keep;
        }

        if positions.len() >= max_orders { continue; }
        if zone == Zone::Neutral { continue; }
        let is_long = zone == Zone::Buy;

        let threshold = range.size * recent_thresh_pct / 100.0;
        let has_recent = positions.iter().any(|p| {
            p.side == is_long && (p.entry_price - price).abs() < threshold
        });
        if has_recent { continue; }

        let tp_dist = range.size * tp_range_pct / 100.0;
        let sl_dist = if sl_range_pct > 0.0 { range.size * sl_range_pct / 100.0 } else { 0.0 };
        let (tp_price, sl_price) = if is_long {
            (price + tp_dist, if sl_dist > 0.0 { price - sl_dist } else { 0.0 })
        } else {
            (price - tp_dist, if sl_dist > 0.0 { price + sl_dist } else { 0.0 })
        };
        // (removida guard tp_inside_range — MQL5 abre ordem sem essa checagem)
        if capital * pos_size_pct * price <= 0.0 { continue; }

        positions.push(Position {
            side: is_long, entry_price: price, tp_price, sl_price, qty_pct: pos_size_pct,
        });
    }

    // Fecha posições restantes no último close (EOD-style)
    let last_close = candles.last().map(|c| c.close).unwrap_or(0.0);
    for pos in positions.drain(..) {
        let pnl = if pos.side {
            (last_close - pos.entry_price) / pos.entry_price
        } else {
            (pos.entry_price - last_close) / pos.entry_price
        };
        let size = capital * pos.qty_pct;
        let net  = size * pnl - size * FEE_PCT * 2.0;
        capital += net;
        peak = peak.max(capital);
        let dd = if peak > 0.0 { (peak - capital) / peak } else { 0.0 };
        max_dd = max_dd.max(dd);
        if net > 0.0 { wins += 1; } else { losses += 1; }
    }

    RunMetrics {
        trades: wins + losses,
        wins, losses, eods: 0,
        final_capital: capital,
        max_dd_pct: max_dd * 100.0,
        max_consec_loss: mcl,
    }
}

// ── Grid expansion ──────────────────────────────────────────────────────────

pub struct Grid {
    pub adx_thresh:        Vec<f64>,
    pub atr_pct_thresh:    Vec<f64>,
    pub range_lookback:    Vec<usize>,
    pub zone_pct:          Vec<f64>,
    pub tp_range_pct:      Vec<f64>,
    pub sl_range_pct:      Vec<f64>,
    pub recent_thresh_pct: Vec<f64>,
    pub max_orders:        Vec<usize>,
    pub pos_size:          Vec<f64>,
}

impl Default for Grid {
    fn default() -> Self {
        Self {
            adx_thresh:        vec![20.0, 25.0, 30.0],
            atr_pct_thresh:    vec![0.3, 0.5, 0.8, 1.0],
            range_lookback:    vec![20, 30, 50, 80],
            zone_pct:          vec![15.0, 20.0, 25.0, 33.0],
            tp_range_pct:      vec![30.0, 40.0, 50.0, 60.0, 70.0],
            sl_range_pct:      vec![0.0, 20.0, 30.0, 40.0, 50.0],
            recent_thresh_pct: vec![1.0, 2.0, 3.0],
            max_orders:        vec![2, 4, 6],
            pos_size:          vec![0.05, 0.10, 0.20],
        }
    }
}

pub struct RangeStrategy { grid: Grid }

impl RangeStrategy {
    pub fn new(grid: Grid) -> Self { Self { grid } }
    pub fn with_default() -> Self { Self { grid: Grid::default() } }
}

impl super::Strategy for RangeStrategy {
    fn name(&self) -> &'static str { "range" }

    fn build(&self, ctx: &Ctx<'_>) -> StrategyOutput {
        // ATR e ADX precomputados uma vez. Wrap em Arc pra compartilhar entre os
        // ~21k runs sem clonar (cada vetor tem ~50k f64s = 400KB; 21k clones
        // estourariam memória — 18GB).
        let atr  = atr_wilder(ctx.candles, ATR_PERIOD);
        let adx  = Arc::new(adx_wilder(ctx.candles, ADX_PERIOD));
        let atr_pct: Arc<Vec<f64>> = Arc::new(atr.iter().enumerate()
            .map(|(i, a)| if ctx.candles[i].close > 0.0 { a / ctx.candles[i].close * 100.0 } else { 0.0 })
            .collect());

        // MTF: pré-computa ADX no TF maior + mapping base→MTF (uma vez por sweep)
        let (mtf_adx_arc, mtf_idx_map_arc): (Option<Arc<Vec<f64>>>, Option<Arc<Vec<Option<usize>>>>) =
            match ctx.mtf_candles {
                Some(mtf) if !mtf.is_empty() => (
                    Some(Arc::new(adx_wilder(mtf, ADX_PERIOD))),
                    Some(Arc::new(build_mtf_idx_map(ctx.candles, mtf))),
                ),
                _ => (None, None),
            };

        let mut runs = Vec::new();
        for &adx_t in &self.grid.adx_thresh {
            for &atr_t in &self.grid.atr_pct_thresh {
                for &lb in &self.grid.range_lookback {
                    for &zp in &self.grid.zone_pct {
                        for &tp in &self.grid.tp_range_pct {
                            for &sl in &self.grid.sl_range_pct {
                                for &rt in &self.grid.recent_thresh_pct {
                                    for &mo in &self.grid.max_orders {
                                        for &ps in &self.grid.pos_size {
                                            let label = format!(
                                                "adx={adx_t:.1} atr={atr_t:.2} lb={lb} zone={zp:.1} tp={tp:.1} sl={sl:.1} recent={rt:.1} max_orders={mo} pos={ps:.2}"
                                            );
                                            let mtf_enabled = mtf_adx_arc.is_some();
                                            let params = serde_json::json!({
                                                "adx_thresh": adx_t,
                                                "atr_pct_thresh": atr_t,
                                                "range_lookback": lb,
                                                "zone_pct": zp,
                                                "tp_range_pct": tp,
                                                "sl_range_pct": sl,
                                                "recent_thresh_pct": rt,
                                                "max_orders": mo,
                                                "pos_size": ps,
                                                // MQL5 alignment defaults (fixos, não expandidos no grid)
                                                "mtf_enabled": mtf_enabled,
                                                "mtf_timeframe": "15m",
                                                "close_at_opposite": true,
                                                "close_on_range_break": false,    // match MQL5 default
                                            });
                                            // Arc::clone só bumpa refcount, não duplica os dados.
                                            let adx_arc      = Arc::clone(&adx);
                                            let atr_pct_arc  = Arc::clone(&atr_pct);
                                            let mtf_adx_run  = mtf_adx_arc.as_ref().map(Arc::clone);
                                            let mtf_map_run  = mtf_idx_map_arc.as_ref().map(Arc::clone);
                                            runs.push(MonolithicRun {
                                                label,
                                                strategy_params: params,
                                                execute: Box::new(move |candles| {
                                                    run_range_backtest(
                                                        candles, &adx_arc, &atr_pct_arc,
                                                        mtf_adx_run.as_deref().map(Vec::as_slice),
                                                        mtf_map_run.as_deref().map(Vec::as_slice),
                                                        adx_t, atr_t, lb, zp, tp, sl, rt, mo, ps,
                                                        true,    // close_at_opposite default ON
                                                        false,   // close_on_break default OFF (match MQL5)
                                                        DetectionMode::Indicator,  // default MQL5
                                                    )
                                                }),
                                            });
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        StrategyOutput::Runs(runs)
    }
}
