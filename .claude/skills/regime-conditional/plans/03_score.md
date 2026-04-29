# Milestone 3 — Regime score function (calibrado, confidence-aware)

**Output**: módulo `backtest/src/regime/score.rs` que retorna score 0..1 calibrado, sem magic numbers.

## Mudanças do plano original

1. **Remove magic numbers** (`scale=50`, threshold 0.4/0.6) — calibração via grid no M4
2. **Confidence weighting**: bucket com 100 trades pesa mais que bucket com 25
3. **Filtro por `is_useful` flag** do M2 — bucket não-validado em OOS é ignorado
4. **Substitui produto-de-sigmoids** por **soma ponderada de expectancies** (mais interpretável)

## Contrato

```rust
pub struct StrategyFingerprint {
    pub preset_id: i64,
    /// Map: feature_name → 3 buckets ordenados (low/mid/high)
    pub buckets_by_feature: HashMap<&'static str, [BucketProfile; 3]>,
    pub feature_quantiles: HashMap<&'static str, [f64; 2]>,    // q33, q67
    /// Maior avg_pnl positivo entre buckets is_useful=true. Usado pra normalizar score.
    /// Computado no load. Se todos buckets úteis têm avg<=0, fingerprint é "ruim" (regime não filtra).
    pub max_useful_pnl: f64,
}

pub struct BucketProfile {
    pub avg_pnl_pct: f64,
    pub n_trades: usize,
    pub is_useful: bool,    // do M2
}

pub fn regime_match_score(
    regime: &RegimeVector,
    fingerprint: &StrategyFingerprint,
) -> f64;    // sempre em [0, 1]

/// Carrega fingerprint da DB. Computa max_useful_pnl ao carregar.
/// Erro se nenhum bucket is_useful=true (estratégia não regime-detectável).
pub fn load_fingerprint(client: &mut Client, preset_id: i64) -> Result<StrategyFingerprint>;
```

## Algoritmo

```rust
fn regime_match_score(regime: &RegimeVector, fp: &StrategyFingerprint) -> f64 {
    let mut weighted_expectancy = 0.0;
    let mut total_weight = 0.0;

    for feature_name in ["adx14", "atr_pct", "rsi14", "bb_squeeze"] {
        let value = regime.get(feature_name);
        let bucket_idx = bucket_for(value, fp.feature_quantiles[feature_name]);
        let bucket = &fp.buckets_by_feature[feature_name][bucket_idx];

        if !bucket.is_useful { continue; }    // M2 filter

        // Confidence weight: sqrt(n) (proporcional a 1/std error). Cap em sqrt(200).
        let weight = (bucket.n_trades as f64).sqrt().min(200.0_f64.sqrt());

        weighted_expectancy += bucket.avg_pnl_pct * weight;
        total_weight += weight;
    }

    if total_weight == 0.0 || fp.max_useful_pnl <= 0.0 {
        return 0.0;    // nenhuma feature útil ou nenhum bucket positivo → não opera
    }

    let avg_expectancy = weighted_expectancy / total_weight;
    if avg_expectancy <= 0.0 {
        return 0.0;    // regime previsto perdedor → desativa
    }

    // Normaliza pra [0, 1]: 0 = neutro (avg=0), 1 = igual ao melhor bucket
    (avg_expectancy / fp.max_useful_pnl).min(1.0)
}

fn bucket_for(value: f64, quantiles: [f64; 2]) -> usize {
    if value < quantiles[0] { 0 }      // low
    else if value < quantiles[1] { 1 } // mid
    else { 2 }                         // high
}
```

## Por que essa formulação

### Soma ponderada vs produto

Original (produto): se 1 feature tá em bucket ruim, score zera. Mata sinal mesmo se outras features positivas.

Novo (soma ponderada): cada feature contribui proporcional à sua confiança (n_trades). Bucket "muito útil" (n=300, avg_pnl +0.3%) domina bucket "marginal" (n=20, avg_pnl ±0.05%).

### Confidence weight = sqrt(n)

CLT: variance do mean ~ 1/n. Standard error ~ 1/sqrt(n). Peso por sqrt(n) é proporcional ao **inverso do erro padrão** — bucket com 4x mais trades pesa 2x mais. Cap em sqrt(200) pra não saturar.

### Normalização por referência empírica

Score = avg_expectancy / max_useful_pnl. Score 0 = expectancy zero ou negativa (regime não favorece). Score 1 = expectancy igual ao melhor bucket histórico do preset.

Isso é robusto: cada preset gera seu próprio referencial, sem hyperparam global. Não depende de `sigmoid` arbitrário.

### Filtro `is_useful` do M2

Buckets que falharam persistência fp_a/fp_b ou Bonferroni são ignorados. Garante anti-overfit — só features validadas contam.

## Edge cases

- **Todos buckets `is_useful=false`**: `load_fingerprint` retorna `Err` (não compila fingerprint vazio). Strategy is_regime-insensitive — caller (composite) trata como always-on ou exclui da composição.
- **Todos buckets úteis com avg_pnl ≤ 0**: `max_useful_pnl ≤ 0` → score sempre 0. Strategy não tem regime útil. Logar warning.
- **Regime atual fora de qualquer quantil observado**: bucket_for() já mapeia pros extremos (low/high). Não panic.
- **NaN no regime**: caller (composite) já validou via `regime_arrays.at(i)` antes de chamar score. Se chegou aqui com NaN, é bug — `debug_assert!`.

## Tests

```rust
#[test]
fn perfect_match_score_high() {
    // Fingerprint sintético: high bucket de adx14 tem avg_pnl +0.5% (200 trades),
    // outras features também high são positivas
    // Regime atual: cai em high de todas as features
    // Esperado: score próximo de 1
}

#[test]
fn anti_match_score_low() {
    // Mesmo fingerprint
    // Regime atual: cai em low de todas
    // Esperado: score próximo de 0
}

#[test]
fn confidence_weighting() {
    // Bucket A: n=10, avg_pnl=+1% (parece ótimo, mas amostra pequena)
    // Bucket B: n=300, avg_pnl=+0.1% (consistente)
    // Esperado: score dominado por B, não A
}

#[test]
fn ignores_non_useful_buckets() {
    // Bucket com is_useful=false e avg_pnl=+5% (overfit IS)
    // Esperado: bucket é ignorado, não infla score
}

#[test]
fn empty_useful_buckets_returns_zero() {
    // Todos buckets is_useful=false
    // Esperado: score=0, não panic
}
```

## Sem thresholds (deixar pro M4)

Score retorna [0, 1] cru. Os thresholds (`activate_threshold`, `full_position_threshold`) são **calibrados via grid no M4** durante composite backtest. Não chutar aqui.

## Performance

`regime_match_score` em <1µs (4 lookups + soma + clamp). Crítico pra hot loop do composite backtest.

## Critério de "M3 done"

- 5 tests passando
- Score sempre em [0, 1]
- Ignora buckets não-úteis (M2 filter)
- Confidence-weighted (não pondera ruído)
- Sem magic numbers no código (todos params vêm do fingerprint)

## O que NÃO é M3

- Definição de thresholds (M4)
- Bot Python (M4)
- Composite backtest (M4)
