//! Tipos do domínio: Candle, Symbol, Timeframe, Entry, ExitResult, RunMetrics, RunResult.

use chrono::{DateTime, Utc};
use std::fmt;
use std::sync::Arc;

// ── Newtypes ─────────────────────────────────────────────────────────────────

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct Symbol(String);

impl Symbol {
    pub fn new(s: impl Into<String>) -> Self {
        Self(s.into().to_uppercase())
    }
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for Symbol {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Timeframe {
    M1, M2, M3, M5, M15, M30,
    H1, H2, H4, H6, H8, H12,
    D1,
}

impl Timeframe {
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "1m" => Some(Self::M1), "2m" => Some(Self::M2), "3m" => Some(Self::M3),
            "5m" => Some(Self::M5), "15m" => Some(Self::M15), "30m" => Some(Self::M30),
            "1h" => Some(Self::H1), "2h" => Some(Self::H2), "4h" => Some(Self::H4),
            "6h" => Some(Self::H6), "8h" => Some(Self::H8), "12h" => Some(Self::H12),
            "1d" => Some(Self::D1),
            _ => None,
        }
    }
    pub fn as_str(self) -> &'static str {
        match self {
            Self::M1 => "1m", Self::M2 => "2m", Self::M3 => "3m",
            Self::M5 => "5m", Self::M15 => "15m", Self::M30 => "30m",
            Self::H1 => "1h", Self::H2 => "2h", Self::H4 => "4h",
            Self::H6 => "6h", Self::H8 => "8h", Self::H12 => "12h",
            Self::D1 => "1d",
        }
    }
    pub fn minutes(self) -> u32 {
        match self {
            Self::M1 => 1, Self::M2 => 2, Self::M3 => 3,
            Self::M5 => 5, Self::M15 => 15, Self::M30 => 30,
            Self::H1 => 60, Self::H2 => 120, Self::H4 => 240,
            Self::H6 => 360, Self::H8 => 480, Self::H12 => 720,
            Self::D1 => 1440,
        }
    }
    /// Próximo TF maior — usado pra MTF Range confirmation.
    /// Match MQL5: TF base 5m → MTF 15m; 15m → 1h; 1h → 4h; etc.
    pub fn next_mtf(self) -> Option<Timeframe> {
        use Timeframe::*;
        match self {
            M1 | M2 | M3 | M5  => Some(M15),
            M15 | M30          => Some(H1),
            H1 | H2            => Some(H4),
            H4 | H6 | H8 | H12 => Some(D1),
            D1                 => None,
        }
    }
}

impl fmt::Display for Timeframe {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// R-multiple (1.0 R = 1× the SL distance from entry).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct R(pub f64);

// ── Candle e índices ─────────────────────────────────────────────────────────

#[derive(Clone, Debug)]
pub struct Candle {
    pub open_time: DateTime<Utc>,
    pub close_time: DateTime<Utc>,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
    pub day: u32,            // dias desde epoch (uso em group_by_day)
    pub minute_of_day: u16,  // 0..1440
}

/// Índices de candles agrupados por dia (uso em find_entries_*).
pub type DayIndex = std::collections::BTreeMap<u32, Vec<usize>>;

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Direction {
    Long,
    Short,
}

#[derive(Clone, Copy, Debug)]
pub struct Entry {
    pub entry_price: f64,
    pub entry_minute: u16,
    pub direction: Direction,
    pub rest_start: usize,   // primeiro candle após o entry
    pub rest_end: usize,     // exclusive
    pub eod_close: f64,
}

// ── Outputs ──────────────────────────────────────────────────────────────────

#[derive(Clone, Copy, Debug)]
pub struct ExitResult {
    pub exit_price: f64,
    pub is_eod: bool,
}

#[derive(Clone, Copy, Debug, Default)]
pub struct RunMetrics {
    pub trades: usize,
    pub wins: usize,
    pub losses: usize,
    pub eods: usize,
    pub final_capital: f64,
    pub max_dd_pct: f64,
    pub max_consec_loss: usize,
}

impl RunMetrics {
    pub fn win_rate(&self) -> f64 {
        if self.trades == 0 { 0.0 } else { self.wins as f64 / self.trades as f64 * 100.0 }
    }
    pub fn return_pct(&self, initial: f64) -> f64 {
        (self.final_capital / initial - 1.0) * 100.0
    }
}

/// Params tipados de strategy — todos Option, cada strategy popula só o seu set.
/// Wide-table flat: cada row de sweep_results tem ~30 colunas, ~70% NULL.
#[derive(Clone, Debug, Default)]
pub struct StrategyParamsRow {
    // range
    pub adx_thresh:           Option<f64>,
    pub atr_pct_thresh:       Option<f64>,
    pub range_lookback:       Option<i32>,
    pub zone_pct:             Option<f64>,
    pub tp_range_pct:         Option<f64>,
    pub sl_range_pct:         Option<f64>,
    pub recent_thresh_pct:    Option<f64>,
    pub max_orders:           Option<i32>,
    pub pos_size:             Option<f64>,
    pub mtf_enabled:          Option<bool>,
    pub close_at_opposite:    Option<bool>,
    pub close_on_range_break: Option<bool>,
    pub mtf_timeframe:        Option<String>,
    // vwap_pullback / momentum (compartilham vários)
    pub min_bars:             Option<i32>,
    pub confirm_bars:         Option<i32>,
    pub vwap_prox:            Option<f64>,
    pub vwap_window_days:     Option<i32>,
    pub ema_period:           Option<i32>,
    pub max_trades_per_day:   Option<i32>,
    // momentum
    pub kind:                 Option<String>,
    pub vol_filter:           Option<bool>,
    pub trend_filter:         Option<bool>,
    pub entry_window_start:   Option<i32>,
    pub entry_window_end:     Option<i32>,
    // ema_scalp
    pub fast_period:          Option<i32>,
    pub slow_period:          Option<i32>,
    // orb
    pub range_mins:           Option<i32>,
    pub buffer_pct:           Option<f64>,
    // pdhl
    pub prox_pct:             Option<f64>,
}

/// Params tipados de exit — todos Option, cada exit popula só o seu set.
#[derive(Clone, Debug, Default)]
pub struct ExitParamsRow {
    // fixed_tp_sl
    pub tp_pct:       Option<f64>,
    pub sl_pct:       Option<f64>,
    pub max_hold_min: Option<i32>,
    // trailing_stop
    pub be_r:         Option<f64>,
    pub trail_step:   Option<f64>,
    pub tp_r:         Option<f64>,
}

/// Linha final escrita em sweep_results.
#[derive(Clone, Debug)]
pub struct RunResult {
    pub symbol: Symbol,
    pub timeframe: Timeframe,
    pub strategy: String,
    pub strategy_params_label: String,
    pub strategy_params: StrategyParamsRow,
    pub exit_name: Option<String>,
    pub exit_params_label: Option<String>,
    pub exit_params: Option<ExitParamsRow>,
    pub period_start: Option<chrono::NaiveDate>,
    pub period_end: Option<chrono::NaiveDate>,
    pub sweep_id: uuid::Uuid,
    pub params_hash: String,         // md5 hex (32 chars)
    pub source: &'static str,
    pub metrics: RunMetrics,
}

// ── Estruturas compartilhadas pelo sweep ─────────────────────────────────────

/// Conjunto de entries gerado por uma combinação de params de uma Strategy.
/// Compartilhado via Arc entre runs que pareiam com diferentes ExitVariants.
#[derive(Clone)]
pub struct EntrySet {
    pub strategy_label: String,
    pub strategy_params: StrategyParamsRow,
    pub entries: Arc<Vec<Entry>>,
}

/// Variante de exit (uma combinação concreta de params do exit model).
#[derive(Clone)]
pub struct ExitVariant {
    pub label: String,
    pub params: ExitParamsRow,
    pub eval: ExitFn,
}

/// Hot-path: enum monomórfico, sem dispatch dinâmico no loop do evaluate.
/// Adicionar exit = nova variant + nova match arm.
#[derive(Clone, Copy, Debug)]
pub enum ExitFn {
    FixedTpSl { tp_pct: f64, sl_pct: f64, max_hold_min: u16 },
    Trailing  { sl_pct: f64, be_r: f64, trail_step: f64, tp_r: f64, max_hold_min: u16 },
}

// Constantes globais usadas por múltiplas strategies (matchando o sweep antigo).
pub const FEE_PCT: f64 = 0.0004;
pub const INITIAL_CAPITAL: f64 = 1000.0;
pub const ENTRY_START_DEFAULT: u16  = 60;    // 01:00 UTC
pub const ENTRY_CUTOFF_DEFAULT: u16 = 1320;  // 22:00 UTC
pub const END_OF_DAY: u16           = 1430;  // 23:50 UTC

/// Run "monolítico": estratégia que produz resultado completo sem parear com
/// exits externos (Range é o caso — TP/SL acoplados ao entry).
pub struct MonolithicRun {
    pub label: String,
    pub strategy_params: StrategyParamsRow,
    pub execute: Box<dyn Fn(&[Candle]) -> RunMetrics + Send + Sync>,
}

/// Output de Strategy::build — escolhe o caminho que o sweep vai seguir.
pub enum StrategyOutput {
    EntrySets(Vec<EntrySet>),
    Runs(Vec<MonolithicRun>),
}

/// Contexto passado para Strategy/Exit no build (read-only, compartilhado).
pub struct Ctx<'a> {
    pub symbol: &'a Symbol,
    pub timeframe: Timeframe,
    pub candles: &'a [Candle],
    pub days: &'a DayIndex,
    /// Candles do timeframe maior pra MTF confirmation (default 15m). None = MTF off.
    pub mtf_candles: Option<&'a [Candle]>,
}
