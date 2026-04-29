# Milestone 1 — Compute regime features (4 ortogonais)

**Output**: módulo `backtest/src/regime/features.rs` com 4 features + collinearity check em CI.

## Por que apenas 4 features

Com ~768 trades/preset e 3 buckets/feature, 4 features × 3 buckets = 12 cells, ~64 trades/cell. Mais que isso vira ruído (variance inflation).

7 features (proposta original) viola este limite. Não negociar até ter ≥3000 trades/preset (mais 2-3 anos de dados).

## As 4 features escolhidas

| Feature | Mede | Justificativa | Range típico |
|---|---|---|---|
| `adx14` | Força de trend (não-direcional) | Wilder padrão, indicador clássico de "está trendando?" | 0-100 |
| `atr_pct` | Volatilidade relativa | ATR(14) / close. Captura tamanho típico do movimento | 0.3% - 8% |
| `rsi14` | Momentum / sobrecompra-venda | Wilder, captura aceleração não medida pelo ADX | 0-100 |
| `bb_squeeze` | Spread coiling (range structure) | (BB upper - BB lower) / SMA20. Mede coiling/expansão | 0-0.15 |

### Por que NÃO incluir as outras

- **EMA slope**: alta corr com ADX (~0.7). ADX já captura.
- **body_ratio**: alta corr com ATR/EMA. Redundante.
- **volume_z**: volume crypto é ruidoso e correlacionado com ATR%. Drop.

### Ortogonalidade (validar empiricamente)

Antes de declarar M1 done, computar matriz de correlação Pearson das 4 features sobre histórico ETH 15m de 1 ano. Critério:

- Cada par com corr <0.6 → ✓
- Algum par com corr ≥0.6 → reescolher feature ou justificar manter

Documentar matriz em `regime_features_correlation.md` no repo.

## Contrato Rust

```rust
pub struct RegimeVector {
    pub adx14:      f64,    // 0-100
    pub atr_pct:    f64,    // 0..1 (e.g. 0.015 = 1.5%)
    pub rsi14:      f64,    // 0-100
    pub bb_squeeze: f64,    // 0..1 (e.g. 0.02 = 2% spread)
}

/// V1: regime computado no mesmo TF do trade.
/// V2 (futuro): adicionar `mtf_candles: Option<&[Candle]>` — Open #5.
pub fn compute_regime_at(candles: &[Candle], i: usize) -> Option<RegimeVector>;
```

`None` quando `i < 50` (warmup) ou qualquer feature é NaN/infinite — fail fast no caller.

## Fórmulas exatas

### ADX14

Já implementado: `crate::indicator::adx_wilder(candles, 14)[i]`. Reusar.

### ATR%

```rust
let atr = atr_wilder(candles, 14)[i];
let close = candles[i].close;
let atr_pct = atr / close;
```

### RSI14 (novo, adicionar em `crate::indicator`)

Wilder smoothing. **Pré-warmup = NaN** (fail-fast explícito; não inferir 50):

```rust
pub fn rsi_wilder(candles: &[Candle], period: usize) -> Vec<f64> {
    let mut out = vec![f64::NAN; candles.len()];
    if candles.len() < period + 1 { return out; }

    let mut gains = 0.0; let mut losses = 0.0;
    for i in 1..=period {
        let delta = candles[i].close - candles[i-1].close;
        if delta > 0.0 { gains += delta; } else { losses += -delta; }
    }
    let mut avg_g = gains / period as f64;
    let mut avg_l = losses / period as f64;
    out[period] = compute_rsi(avg_g, avg_l);

    for i in period+1..candles.len() {
        let delta = candles[i].close - candles[i-1].close;
        let (g, l) = if delta > 0.0 { (delta, 0.0) } else { (0.0, -delta) };
        avg_g = (avg_g * (period - 1) as f64 + g) / period as f64;
        avg_l = (avg_l * (period - 1) as f64 + l) / period as f64;
        out[i] = compute_rsi(avg_g, avg_l);
    }
    out
}

fn compute_rsi(avg_g: f64, avg_l: f64) -> f64 {
    if avg_l <= 1e-12 { 100.0 }
    else { 100.0 - 100.0 / (1.0 + avg_g / avg_l) }
}
```

### BB Squeeze

Pré-warmup = NaN também:

```rust
pub fn bb_squeeze(candles: &[Candle], period: usize, k: f64) -> Vec<f64> {
    let mut out = vec![f64::NAN; candles.len()];
    if candles.len() < period { return out; }
    for i in (period-1)..candles.len() {
        let window = &candles[i+1-period..=i];
        let mean = window.iter().map(|c| c.close).sum::<f64>() / period as f64;
        let variance = window.iter().map(|c| (c.close - mean).powi(2)).sum::<f64>() / period as f64;
        let std = variance.sqrt();
        out[i] = (2.0 * k * std) / mean;
    }
    out
}
```

Com k=2: `bb_squeeze = 4 * std / SMA`. Range típico 0.005-0.10.

`RegimeArrays::at()` retorna `None` se qualquer feature é NaN — caller skip-a o candle.

## TF do regime: trade TF vs higher TF

V1 usa o **mesmo TF do trade** (15m preset → 15m features). Mais simples, sem complexidade extra de carregar candles MTF.

V2 (futuro): testar features em TF mais alta (Open #5). Argumento: regime macro pode ser melhor capturado em 4h/1d. M2 vai gerar dados pra decidir empiricamente:

```bash
backtest fingerprint --preset-id 1 --regime-tf 15m   # V1 baseline
backtest fingerprint --preset-id 1 --regime-tf 4h    # V2 candidato
```

Compara qual gera mais buckets `is_useful`. Migra pra MTF apenas se M2 mostrar uplift ≥20% em buckets úteis.

V1 não muda assinatura da função; V2 adiciona `mtf_candles: Option<&[Candle]>` quando for o momento.

## Look-ahead bias guard

Todos os 4 indicadores são **causais por construção**: feature em candle `i` usa apenas candles `0..=i`.

- ADX/ATR/RSI: Wilder smoothing, OK
- BB squeeze: window `[i-period+1, i]`, OK

**Nunca** usar `for i in 0..n { x[i] = f(candles[i+1]) }` (look-ahead). Tests obrigatórios:

```rust
#[test]
fn no_lookahead_in_features() {
    // Comparar feature[100] computado com candles[..101] vs candles[..200].
    // Esperado: valor idêntico (look-ahead-free).
    let candles = synth_random(200);
    let arrays_short = RegimeArrays::compute(&candles[..101]);
    let arrays_long  = RegimeArrays::compute(&candles);
    assert_eq!(arrays_short.adx14[100], arrays_long.adx14[100]);
    // ... mesma asserção para outras 3 features
}
```

## Performance hot-path

Pré-computar arrays uma vez via `RegimeArrays::compute(candles)`:

```rust
pub struct RegimeArrays {
    pub adx14:      Arc<Vec<f64>>,
    pub atr_pct:    Arc<Vec<f64>>,
    pub rsi14:      Arc<Vec<f64>>,
    pub bb_squeeze: Arc<Vec<f64>>,
}

impl RegimeArrays {
    pub fn compute(candles: &[Candle]) -> Self { /* ... */ }

    pub fn at(&self, i: usize) -> Option<RegimeVector> {
        if i < 50 { return None; }
        // Validar que nenhum array tem NaN no índice i
        let v = RegimeVector {
            adx14:      self.adx14[i],
            atr_pct:    self.atr_pct[i],
            rsi14:      self.rsi14[i],
            bb_squeeze: self.bb_squeeze[i],
        };
        if [v.adx14, v.atr_pct, v.rsi14, v.bb_squeeze].iter().any(|x| x.is_nan() || x.is_infinite()) {
            return None;
        }
        Some(v)
    }
}
```

Compartilha via Arc entre threads (mesmo padrão do `range::run_range_backtest`).

## Tests obrigatórios

```rust
#[test]
fn rsi_overbought_market() {
    // 100 candles em uptrend forte (close[i] = close[i-1] * 1.005)
    let candles = synth_uptrend(100, 1.005);
    let rsi = rsi_wilder(&candles, 14);
    assert!(rsi[99] > 75.0, "trend forte deveria ter RSI > 75, got {}", rsi[99]);
}

#[test]
fn bb_squeeze_low_in_trend() {
    // candles em trend constante (baixa volatilidade ao redor da média móvel)
    // squeeze deveria ser baixo
}

#[test]
fn nan_propagation() {
    // candles com close = NaN num ponto
    // compute_regime_at deveria retornar None, não panic
}

#[test]
fn correlation_matrix_4features() {
    // Carrega ETH 15m, computa as 4 arrays, valida que correlação par-a-par <0.6
    // Roda no CI contra fixture de 1 mês de candles
}
```

## Fixtures de teste

Adicionar `backtest/tests/fixtures/eth_15m_jan2026.csv` com ~3000 candles. Usado para:
- correlation matrix test
- golden vector test (M4)

## Performance target

`RegimeArrays::compute` em <50ms para 35k candles (1 ano de 15m). Validar com `cargo bench`.

## Critério de "M1 done"

- 4 funções (adx, atr, rsi, bb_squeeze) implementadas com tests
- `RegimeArrays` compute + `at()` funciona
- Matriz de correlação computada em CI, todos os pares <0.6
- Performance: 35k candles em <50ms
- Documentação: `regime_features_correlation.md` com matriz observada

## O que NÃO é M1

- Não persiste features no DB (são recomputadas a cada backtest)
- Não bina ainda (M2)
- Não score (M3)
