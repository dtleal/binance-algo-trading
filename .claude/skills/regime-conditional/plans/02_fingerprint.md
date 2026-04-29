# Milestone 2 — Per-strategy regime fingerprint (com OOS validation)

**Output**: tabela `strategy_regime_profile` + binário `fingerprint` que processa preset → popula tabela. **Critical**: fingerprint validado em OOS antes de adotar.

## Conceito revisado

Pra cada preset, descobrir empiricamente em quais regimes ele tem edge. Mas com 3 guards contra overfit:

1. **Time-split persistência (FP-A / FP-B)**: dividir TODO histórico do preset (IS+OOS combinados, já que ambos foram validados nos 3 gates) em primeiro 50% (FP-A) vs último 50% (FP-B). Construir buckets em A, validar persistência em B. Diferente do split IS/OOS dos 3 gates — esse é separação **temporal** dentro do válido.
2. **Bonferroni com bootstrap**: 4 features × 3 buckets = 12 hipóteses. Pra cada bucket, bootstrap percentil 95% do avg_pnl. H0 (avg=0) rejeitada se IC95 não cruza 0. Bonferroni: α/12 → IC99.6%.
3. **Min sample size**: descartar bucket com <30 trades (binomial variance ~ sqrt(0.5×0.5/30) = 9% std → erro grande).

Por que bootstrap em vez de t-test: pnl distributions de trades são heavy-tailed (poucos vencedores grandes, muitos pequenos). t-test assume normalidade — falso. Bootstrap não assume distribuição.

### Plano B: Bonferroni pode matar todos os buckets

α/12 = 0.0042 é exigente. Com 64 trades/bucket e variance pnl alto, talvez ZERO buckets passem. **Antes de declarar a strategy "regime-insensível"**, fallback:

1. Tentar α/12 (Bonferroni estrito)
2. Se 0 buckets úteis → tentar α/4 = 0.0125 (FDR-style relaxado, ainda Bonferroni-like)
3. Se 0 buckets úteis novamente → tentar α=0.05 sem correção (apenas pra inspeção visual)

Anotar em `strategy_regime_profile` qual nível foi usado pra `is_useful`. Composite pode optar por usar só strategies com Bonferroni estrito (mais conservador) ou aceitar relaxado (mais agressivo).

### Plano B: k-fold se fp_a/fp_b der inconclusivo

fp_a/fp_b é UM split temporal. Se persistence_ok flutuar com data de corte, é frágil. Como fallback: **3-fold time-split** (primeiro 1/3, meio, último 1/3). Bucket é "useful" se persistence se mantém em ≥2 dos 3 folds.

Adicionar `--use-3fold` flag no subcomando fingerprint.

## Schema (migration 014)

```sql
CREATE TABLE regime_feature_quantiles (
    -- Quantis populacionais por (símbolo, TF, período).
    -- Calcula 1x por janela, reusa pra todos presets desse contexto.
    symbol         TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    feature_name   TEXT NOT NULL,    -- 'adx14', 'atr_pct', 'rsi14', 'bb_squeeze'
    period_start   DATE NOT NULL,
    period_end     DATE NOT NULL,
    q33            NUMERIC(10,6) NOT NULL,    -- divisor low/mid
    q67            NUMERIC(10,6) NOT NULL,    -- divisor mid/high
    PRIMARY KEY (symbol, timeframe, feature_name, period_start, period_end)
);

CREATE TABLE strategy_regime_profile (
    id              BIGSERIAL PRIMARY KEY,
    preset_id       BIGINT NOT NULL REFERENCES presets(id) ON DELETE CASCADE,
    feature_name    TEXT NOT NULL,        -- 4 valores
    bucket          TEXT NOT NULL,        -- 'low' | 'mid' | 'high'
    n_trades        INT  NOT NULL,
    n_wins          INT  NOT NULL,
    win_rate        NUMERIC(8,4),
    avg_pnl_pct     NUMERIC(10,4),
    sum_pnl_pct     NUMERIC(10,4),
    -- Time-split persistência (FP-A primeiro 50%, FP-B último 50%)
    fp_a_n_trades   INT,
    fp_a_avg_pnl    NUMERIC(10,4),
    fp_b_n_trades   INT,
    fp_b_avg_pnl    NUMERIC(10,4),
    persistence_ok  BOOLEAN,    -- TRUE se sign(fp_a) == sign(fp_b) E |fp_b| >= 0.5*|fp_a|
    -- Bonferroni com bootstrap (IC 99.6% via 1000 reamostragens)
    bootstrap_ic_lo NUMERIC(10,4),    -- limite inferior IC99.6
    bootstrap_ic_hi NUMERIC(10,4),    -- limite superior IC99.6
    bonferroni_ok   BOOLEAN,    -- TRUE se IC não cruza zero
    -- Decisão final
    is_useful       BOOLEAN,    -- TRUE se persistence_ok AND bonferroni_ok AND n_trades>=30
    computed_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (preset_id, feature_name, bucket)
);

CREATE INDEX idx_regime_profile_preset ON strategy_regime_profile(preset_id);
```

12 rows por preset (4 features × 3 buckets). `is_useful=true` é o filtro pro score (M3).

## 3 buckets, não 5

Com ~64 trades/cell em 5 buckets vs ~64 trades/cell em 3 buckets... espera, com 3 buckets temos ~256 trades/cell. Bem melhor pra estatística.

3 buckets também alinha com ação binária do detector:
- `low` (q0-q33): regime "ruim" pra strategy → desativa
- `mid` (q33-q67): regime "neutro" → opera com pos_size reduzido
- `high` (q67-q100): regime "bom" → opera com pos_size full

## Workflow do binário `fingerprint`

```bash
backtest fingerprint --preset-id 1
```

### Pseudo-algoritmo

```
1. Load preset (presets WHERE id=$1)
2. Load candles do period_start..period_end
3. Compute RegimeArrays (M1)
4. Compute or load quantiles populacionais
   - Q33, Q67 de cada feature em todo o universo de candles
   - Persiste em regime_feature_quantiles
5. Replay preset em todo histórico, capturar trade-by-trade:
   - Entry candle index (i)
   - Regime vector at i
   - Win/loss + pnl_pct
6. Split trades por TEMPO: fp_a = trades em primeiros 50% do período, fp_b = últimos 50%
7. Pra cada (feature, bucket):
   - n_total, avg_pnl_total
   - n_a, avg_pnl_a = stats em fp_a daquele bucket
   - n_b, avg_pnl_b = stats em fp_b daquele bucket
   - persistence_ok = (n_a >= 15 AND n_b >= 15) AND
                     sign(avg_pnl_a) == sign(avg_pnl_b) AND
                     abs(avg_pnl_b) >= 0.5 * abs(avg_pnl_a)
   - bootstrap_ic = bootstrap_pct_ic(trades_in_bucket, n_resamples=1000, alpha=0.004)
   - bonferroni_ok = (bootstrap_ic_lo > 0 OR bootstrap_ic_hi < 0)    // IC não cruza zero
   - is_useful = persistence_ok AND bonferroni_ok AND n_total >= 30
8. UPSERT em strategy_regime_profile
```

### Output ao usuário

```
═══ Fingerprint preset_id=1 (PDHL ETH 15m) ═══

adx14:
  low  (0-12.3)  : 78 trades  fp_a:+0.05% fp_b:-0.02%  → ❌ persistence FAIL
  mid  (12.3-23) : 312 trades fp_a:+0.18% fp_b:+0.12%  → ✅ useful
  high (23-99)   : 378 trades fp_a:+0.21% fp_b:+0.19%  → ✅ useful

atr_pct:
  low  (0-0.4%) : 45 trades  → ⚠ n<30, dropped
  mid  (0.4-1.2%): 432 trades fp_a:+0.15% fp_b:+0.14%  → ✅ useful
  high (1.2-5%) : 291 trades fp_a:+0.10% fp_b:-0.05%  → ❌ persistence FAIL

rsi14:
  ...

bb_squeeze:
  ...

Useful buckets: 6/12
Strategy is regime-sensitive: yes (filterable)
```

(`fp_a` = primeiros 50% do tempo do preset, `fp_b` = últimos 50%. Comparar com IS/OOS dos 3 gates é confusão diferente.)

## Reusing existing replay code

O replay do preset (passo 5) já existe em `crate::detail`. Adicionar hook pra capturar regime vector no entry de cada trade:

```rust
pub fn run_detail_with_regime(
    candles: &[Candle],
    days: &DayIndex,
    strategy: &str,
    params: &DetailParams,
    regime_arrays: &RegimeArrays,
) -> Result<Vec<DetailTradeWithRegime>>;

pub struct DetailTradeWithRegime {
    pub trade: DetailTrade,
    pub regime_at_entry: RegimeVector,
}
```

## Edge cases (fail fast)

- Preset com <100 trades total: `bail!("preset has insufficient trades for fingerprint (need ≥100)")`
- Quantile inválido (todas observações iguais): bucket fica null, log warning
- fp_a ou fp_b sem trades suficientes em algum bucket: marca `is_useful=false`, log

## Tests

- Smoke: rodar fingerprint pro preset 1, verificar 12 rows na tabela.
- Sanity: pelo menos 1 bucket marcado `is_useful=true` (preset é positivo no agg, deveria ter buckets bons em algum lugar).
- Property: para preset com 768 trades positivo no agg, ≥1 bucket em ≥1 feature deve ser `useful` (qualquer bucket — depende da strategy: range strategy útil em ADX-low, PDHL útil em ADX-high).
- Statistical: gerar preset sintético com PnL constante (todos trades = +0.1%), validar que NENHUM bucket marca `useful` (sem edge diferencial entre buckets).
- Statistical: gerar preset sintético com PnL aleatório IID, validar ≤1 bucket marca `useful` por chance (Bonferroni controlando type I).

## Critério de "M2 done"

- Migration 014 aplicada
- Subcomando `fingerprint` funciona pra qualquer preset
- Roda fingerprint pros 3+ presets do M0
- Output documenta useful buckets / total
- Tests passam

## O que NÃO é M2

- Score function (M3)
- Composite (M4)
- Nenhum threshold de ativação ainda
