# Plano — Alinhar Range Rust com MQL5 GridTradingEA (modo Range)

Status: **planejado**. Todos os 5 itens implementados, todos default ON.

---

## Ordem de execução

1. Atualizar params/grid (RangeParams + JSONB)
2. Throttle = 1 candle
3. MTF Range confirmation (ADX em TF maior, default M15)
4. Remover guard `tp_inside_range`
5. `close_at_opposite_extreme` (default true)
6. `close_on_range_break` (default true)
7. Atualizar `range_debug` com `CloseReason`
8. Smoke test E2E

---

## 1. Param schema atualizado

`Grid` ganha 4 params (booleans/strings, NÃO entram na expansão pra evitar explosão combinatória — fixos no run):

```rust
pub struct Grid {
    // ... existentes ...
    pub mtf_enabled:           bool,        // default true (FIXO, não Vec)
    pub mtf_timeframe:         Timeframe,   // default M15
    pub close_at_opposite:     bool,        // default true
    pub close_on_range_break:  bool,        // default true
}
```

JSONB de cada combo:
```json
{
  "adx_thresh": 25.0, "atr_pct_thresh": 0.5, ...,
  "mtf_enabled": true,
  "mtf_timeframe": "15m",
  "close_at_opposite": true,
  "close_on_range_break": true
}
```

Sweep antigos com rows sem esses fields: tratamos como defaults `true / "15m"` no `evaluate_combo` (com `unwrap_or`).

---

## 2. Throttle = 1 candle

```rust
const RANGE_THROTTLE: usize = 1;   // antes 12
```

Match MQL5 (60s ≈ 1 candle em 5m).

---

## 3. MTF Range confirmation

### Pipeline

**a) Carregar candles MTF** (separado, não agrega — match prod):
```rust
let mtf_candles: Vec<Candle> = if mtf_enabled {
    db::load_candles(client, symbol, mtf_tf, from, until)?
} else { Vec::new() };
let mtf_adx: Vec<f64> = adx_wilder(&mtf_candles, ADX_PERIOD);
```

**b) Mapear cada candle base → MTF correspondente** (binary search por open_time):
```rust
fn map_base_to_mtf(base_time: DateTime<Utc>, mtf_candles: &[Candle]) -> Option<usize> {
    // partition_point: maior MTF candle com open_time <= base_time
    let pos = mtf_candles.partition_point(|c| c.open_time <= base_time);
    if pos == 0 { None } else { Some(pos - 1) }
}
```

**c) Filtro in_range estendido**:
```rust
let mtf_ok = if mtf_enabled {
    map_base_to_mtf(c.open_time, &mtf_candles)
        .map(|i| mtf_adx[i] <= adx_thresh)
        .unwrap_or(false)   // sem MTF data => não trada (mais seguro)
} else { true };

let in_range = adx[i] <= adx_thresh
    && atr_pct[i] <= atr_pct_thresh
    && range_size > 0.0
    && mtf_ok;
```

### Pendências

**onboard auto-fetch MTF**:
```rust
ensure_klines_available(client, symbol, base_tf, days)?;
if mtf_enabled {
    ensure_klines_available(client, symbol, mtf_tf, days)?;   // 15m default
}
```

**Sweep/Detail/RangeDebug erro claro se faltar**:
```
MTF range enabled but no 15m candles for BTCUSDT — run:
  poetry run python -m db.fetch_klines --symbol BTCUSDT --days 365 --timeframe 15m
```

---

## 4. Remover guard `tp_inside_range`

Em `range.rs::run_range_backtest` deletar (linhas 156-157):
```rust
let tp_inside_range = if is_long { tp_price <= range.high } else { tp_price >= range.low };
if !tp_inside_range { continue; }
```

---

## 5. CloseAtOppositeExtreme (default true)

Após TP/SL check do candle, ANTES de abrir nova posição:

```rust
if close_at_opposite_extreme {
    let margin = range.size * 0.05;     // hardcoded 5% match MQL5
    let zone = price_zone(c.close, &range, zone_pct);
    let mut to_keep = Vec::new();
    for pos in positions.drain(..) {
        let force_close = match (pos.side, zone) {
            (true,  Zone::Sell) if c.high >= range.high - margin => true,
            (false, Zone::Buy)  if c.low  <= range.low  + margin => true,
            _ => false,
        };
        if force_close {
            // PnL = (c.close - entry) / entry  (long) ou inverso pra short
            // Registra wins/losses, atualiza capital, peak, dd
        } else {
            to_keep.push(pos);
        }
    }
    positions = to_keep;
}
```

Match `ClosePositionsAtExtreme` MQL5 (linhas 997-1015).

---

## 6. CloseOnRangeBreak (default true)

Tracking estado anterior:

```rust
let mut was_in_range = false;
for i in min_history..n {
    // ... detect_range, in_range ...

    if was_in_range && !in_range && close_on_range_break && !positions.is_empty() {
        for pos in positions.drain(..) {
            // exit_price = c.close, registra como close, reason=RangeBreak
        }
    }
    was_in_range = in_range;
    // ... TP/SL, opposite_extreme, open new ...
}
```

Match MQL5 linha 401.

---

## 7. range_debug atualização

`TradeMark` ganha `reason: Option<CloseReason>` (Some apenas em event=Close):

```rust
pub enum CloseReason { Tp, Sl, OppositeExtreme, RangeBreak, Eod }
```

Chart distingue por símbolo/cor:
- TP win: X verde
- SL loss: X vermelho
- OppositeExtreme: triângulo amarelo
- RangeBreak: quadrado roxo
- Eod: círculo cinza

---

## 8. Smoke test

Pré-requisito: 15m de BTCUSDT no DB.

```bash
poetry run python -m db.fetch_klines --symbol BTCUSDT --days 365 --timeframe 15m
```

Estimativa: ~30s (~35k candles).

Test:
```bash
./backtest/target/release/backtest sweep --symbol BTCUSDT --timeframe 5m \
   --strategy range --exit fixed_tp_sl --from 2026-03-01 --until 2026-04-01
./backtest/target/release/backtest range-debug --symbol BTCUSDT --timeframe 5m \
   --sweep-result-id <novo top> --output /tmp/range_aligned.html
```

Validação:
- Sweep produz menos trades que antes (filtro MTF + close_at_opposite mais restritivos).
- range-debug mostra trades com diferentes `CloseReason`.
- WR provavelmente cai (não vai mais ser 99.67%) — esperado, alinha com prod.

---

## Resumo de arquivos

| # | Arquivo | Mudança |
|---|---|---|
| 1 | `strategy/range.rs` | Grid + Params com 4 novos campos, expand cartesian inalterado |
| 2 | `strategy/range.rs` | RANGE_THROTTLE: 12 → 1 |
| 3 | `strategy/range.rs` | Aceitar mtf_candles + mtf_adx, filtro estendido |
| 4 | `strategy/range.rs` | Deletar guard tp_inside_range |
| 5 | `strategy/range.rs` | Lógica close_at_opposite_extreme |
| 6 | `strategy/range.rs` | Tracking was_in_range + close_on_range_break |
| 7 | `db.rs` | (sem mudança — load_candles já genérico) |
| 8 | `evaluate.rs` | evaluate_range carrega mtf_candles, passa ao backtest |
| 9 | `onboard.rs` | ensure_klines_available pra MTF (default 15m) |
| 10 | `sweep.rs` | Range strategy build() carrega mtf_candles via Ctx |
| 11 | `range_debug.rs` | Espelhar tudo + CloseReason |
| 12 | `chart.rs` | Marcadores diferenciados por CloseReason |
| 13 | `Ctx` em `types.rs` | Adicionar `mtf_candles: Option<&[Candle]>` |

---

## Riscos / pontos de atenção

1. **Boundary base↔MTF**: candle base 14:32 (5m) mapeia pra MTF candle 14:30-14:45 (15m). `partition_point(|c| c.open_time <= base_time)` resolve com `pos - 1`. Cuidado pra não usar MTF do FUTURO (lookahead bias).
2. **Default ON pra todos**: muda comportamento de TODA sweep/detail/onboard. Documentar bem.
3. **Performance**: load extra MTF (35k candles) + ADX MTF + binary search 105k vezes ≈ +50ms total. Negligível.
4. **Compatibilidade**: rows antigas em sweep_results (sem mtf_enabled etc) → range_debug e overfit param_sensitivity podem misturar resultados de "antes" e "depois". Recomendado limpar sweep_results de Range antes de re-rodar.

Confiança: 85%.
