# Plano — Detail mode no crate `backtest`

Status: **planejado**. Substitui os 8 scripts Python (`scripts/backtest_*.py`, ~3k linhas) por um modo `detail` no binário Rust.

## Por que

Hoje o crate `backtest/` só faz **sweep** (varredura de grid). Os scripts Python existem para o caso "1 conjunto de params, output trade-a-trade + gráfico HTML" — caso clássico de inspeção pós-sweep ("rodei sweep, achei o champion, agora quero ver cada trade desse combo").

Refatorar pra Rust:
- Reuso da lógica já portada (`find_entries_*`, `evaluate`).
- Match exato com o sweep (mesmas regras de TP/SL/trailing — sem deriva entre Rust e Python).
- 1 binário pra tudo.

## CLI alvo

Refactor pra clap subcommand:

```bash
backtest sweep ...    # já existe
backtest detail \
   --symbol BTCUSDT --timeframe 5m \
   --from 2024-01-01 --until 2024-06-30 \
   --strategy vwap_pullback --exit fixed_tp_sl \
   --tp 0.005 --sl 0.01 --max-hold 0 \
   --ema-period 200 --vwap-prox 0.005 \
   --confirm-bars 1 --min-bars 5 \
   --vwap-window 5 --max-trades-per-day 4 \
   --pos-size 0.10 \
   --output detail.html
```

Params **únicos** (não listas). Cada strategy usa só os flags relevantes; restantes ignorados.

Range strategy: detail mode não suportado (entry + exit acoplados, fluxo monolítico). Erro explícito.

## Output

1. **Stdout — summary**:
   ```
   Trades: 47   Wins: 24 (51.1%)   Losses: 23
   Total PnL: +$73.42 (+7.34%)   Max DD: 4.21%
   Max consec loss: 4   Avg trade: +$1.56
   ```

2. **Stdout — trade table** (TSV-friendly):
   ```
   #   entry_time          entry    exit_time           exit      dir    pnl%     pnl$    capital
   1   2024-01-02 03:25    42150.5  2024-01-02 04:55    42360.0   LONG  +0.50   +5.21   1005.21
   ...
   ```

3. **HTML chart** (`--output FILE.html`): 3 subplots
   - Equity curve (capital × time)
   - Per-trade P&L (bars colored by win/loss)
   - Drawdown % (area chart)

   Implementação: HTML estático com CDN do `plotly.js` (~50KB) + JSON inline. Sem dep Rust extra.

## Arquivos novos

```
backtest/src/
├─ detail.rs         # Trade struct, run_detail, build_entries dispatch
└─ chart.rs          # HTML+plotly.js writer
```

`bin/backtest.rs` refactorado:
- `clap::Subcommand` enum: `Sweep` | `Detail`
- main dispatches por subcommand
- Sweep mantém comportamento atual

## Comportamento detalhado

### `detail.rs`
- `struct Trade { entry_time, entry_price, exit_time, exit_price, direction, pnl_pct, pnl_dollar, is_eod, capital_after, drawdown_pct }`
- `run_detail(entries, candles, exit_fn, pos_size) -> DetailResult`
  - Por entry: `exit::evaluate()` → aplica fee 2× → atualiza capital e DD
  - Calcula `exit_time` de `candles[rest_start..rest_end]` baseado em qual saída foi triggered (TP/SL/EOD)
- `build_entries(strategy, params, candles, days)` dispatcha pra `find_entries_*` de cada módulo

### `chart.rs`
- `write_html(path, trades, summary)` produz HTML autocontido
- Plotly via CDN (`https://cdn.plot.ly/plotly-2.x.min.js`)
- 3 subplots empilhados, `template: plotly_dark`

## Makefile

```
detail: ## Run detail backtest (SYMBOL=x TIMEFRAME=y STRATEGY=z [params...])
   ./backtest/target/release/backtest detail $(...)
```

Mantém os comandos Python por enquanto (`backtest-detail*`) — apagar depois de validar.

## Pendências

- Range strategy detail (precisa instrumentar `run_range_backtest` pra emitir trades).
- Comparar trade-a-trade output Python vs Rust no mesmo CSV (gate de equivalência opcional).
- Vwap_dist_stop param (ainda omitido como no sweep).

## Ordem de execução

1. Refactor `bin/backtest.rs` pra usar `Subcommand` (sem quebrar `make sweep`).
2. Criar `src/detail.rs` com `Trade`, `run_detail`, `build_entries`.
3. Criar `src/chart.rs` com HTML writer.
4. Adicionar `make detail` ao Makefile.
5. Test E2E com VWAPPullback + FixedTpSl.
6. Commit + push.
