---
name: binance-refac
description: Refatorar o projeto binance-algo-trading com segurança — extrair duplicações entre bots/strategies, preservar comportamento de trading e respeitar os princípios do CLAUDE.md (fail fast, sem fallbacks silenciosos)
user_invocable: true
---

# /binance-refac — Refactor seguro do binance-algo-trading

Este projeto roda dinheiro real em USDT-M Futures. Refatorar aqui é mais arriscado que em código comum: uma regressão em uma curva de TP/SL ou em um cálculo de quantidade pode causar perda financeira. A skill existe para que cada refactor siga um caminho consistente, focado em duplicação real e com checagens de equivalência de comportamento.

## Princípios não-negociáveis

Estes princípios vêm do `CLAUDE.md` e devem ser respeitados em todo refactor:

- **Fail fast.** Se faltar config ou input vier inválido, levante erro com mensagem clara. Nunca "ajeite" silenciosamente.
- **Sem fallbacks mágicos.** Não introduzir defaults novos durante refactor — mantenha exatamente os mesmos valores e mesmas exceções.
- **Preços/quantidades são strings** no SDK Binance; timestamps são `int` ms. Não converter "de passagem".
- **Quantidades arredondam com `math.floor`**, nunca `round()`.
- **Ordens com `stop_price` ou `close_position`** usam `send_signed_request()` ou `new_algo_order()` (workaround do bug do SDK). Não substituir por `new_order()`.
- **Logger**: `propagate = False` no logger `trader`, `StripAnsiFormatter` no file handler. Não trocar de logger nem mexer na config a menos que seja o objetivo declarado.

## Anti-patterns proibidos no refactor

Tirado também do `CLAUDE.md`:

- Não adicionar features, validações extras, error handling para casos impossíveis, nem refatoração além do escopo pedido.
- Não inventar abstrações para o futuro hipotético. Três trechos parecidos só viram helper se a duplicação for *exata* e óbvia.
- Não criar shims de backwards-compat (`# old name kept for compat`, re-exports, etc.). Se a função some, atualize todas as chamadas.
- Não adicionar docstrings/comments explicando o óbvio. Comentário só quando o *porquê* não é trivial (ex.: "Binance SDK bug: stop orders precisam de send_signed_request").
- Não trocar nomes só por estética. Renomear só se o nome atual confunde de verdade *ou* se a extração obriga.

## Mapa do código (o que é duplicação real vs. o que parece duplicação)

Os arquivos `trader/bot_*.py` (1.1k–1.7k linhas cada) têm muito código repetido. Antes de extrair, classifique:

**Duplicação real e segura para extrair:**
- `_parse_proxy(url)` — idêntico em todos os bots.
- `_decimals_from_step(step_str)` — idêntico em todos os bots.
- Constantes ANSI (`GREEN`, `RED`, `YELLOW`, `CYAN`, `BOLD`, `RESET`).
- Setup do logger por-bot (file handler com `StripAnsiFormatter`, `propagate = False`).
- Helpers de formatação numérica baseados em `tickSize`/`stepSize`.

**Duplicação aparente — cuidado:**
- Loops de execução de ordem (entry/TP/SL): cada estratégia tem nuances (RangeBot usa LIMIT GTX maker, MomShort usa MARKET, etc.). NÃO extrair sem mapear cada diferença.
- Máquinas de estado (`_State` enum): nomes parecidos, transições diferentes por estratégia. Manter separadas.
- Reconexão de WebSocket: parece igual mas tem timings/backoffs ligeiramente diferentes. Extrair só após diff line-by-line.
- Cálculos de indicadores: já moram em `trader/strategy*.py` — confirme antes de tocar.

**Não-duplicação (deixe quieto):**
- Lógica pura de sinal — já está separada em `strategy*.py` (esse design é intencional).
- `config.py` — registry per-symbol é por design.

## Fluxo de execução de um refactor

Siga sempre nesta ordem. Não pular etapas.

### 1. Definir escopo com o usuário

Pergunte (ou confirme se o usuário já disse) **exatamente** o que refatorar. Exemplos válidos:
- "extrair `_parse_proxy` e `_decimals_from_step` para um módulo comum"
- "consolidar setup de logger nos bots"
- "remover código morto em `bot_vwap_pullback.py`"

Exemplos que requerem pushback:
- "refatore os bots" → muito vago. Peça um alvo específico.
- "deixe mais limpo" → não é escopo. Peça o quê especificamente incomoda.

### 2. Inventário do escopo

Antes de mexer:

```bash
# Listar ocorrências do que vai mudar
rg -n 'def _parse_proxy' trader/
rg -n 'GREEN = "\\033\\[92m"' trader/

# Ver tamanho/superfície de cada arquivo afetado
wc -l trader/bot_*.py
```

Se o número de ocorrências for diferente do esperado, *pare* e investigue antes de continuar — pode haver variação que parece igual mas não é.

### 3. Diff line-by-line antes de extrair

Para qualquer função/bloco que parece duplicado em N arquivos, rode um diff entre as versões:

```bash
diff <(sed -n '/def _parse_proxy/,/^$/p' trader/bot.py) \
     <(sed -n '/def _parse_proxy/,/^$/p' trader/bot_range.py)
```

Se o diff não for vazio, **não é duplicação real** — documente as diferenças e reavalie escopo.

### 4. Decidir onde mora o código compartilhado

Convenção do projeto:

- Helpers de I/O / utilidades genéricas: criar `trader/_utils.py` (com underscore, indica privado ao pacote).
- Constantes de cor/ANSI: `trader/_ansi.py` ou agregadas em `_utils.py` se forem o único uso.
- Setup de logger: `trader/log_publisher.py` já existe — estender ele em vez de criar novo módulo.
- Helpers ligados a precisão da exchange: `trader/exchange_precision.py` já existe.

Não criar pacote/subpasta nova só para um helper. Mover só quando o módulo plano fica grande demais (>500 linhas).

### 5. Aplicar a mudança

- Mover/extrair em uma só passada.
- Atualizar todos os imports — sem deixar definição antiga "por compatibilidade".
- Apagar a função/constante antiga nos bots (não comentar, não deixar `# moved to _utils`).

### 6. Verificações de equivalência (obrigatórias)

Refactor sem verificação não está pronto. Rode na ordem:

```bash
# 1. Imports e sintaxe
poetry run python -c "import trader.bot, trader.bot_range, trader.bot_vwap_pullback, trader.bot_ema_scalp, trader.bot_orb, trader.bot_pdhl, trader.bot_vwap_pullback_v2"

# 2. CLI ainda monta
poetry run python -m trader --help
poetry run python -m trader bot --help

# 3. Dry-run de um bot por estratégia (não envia ordem)
poetry run python -m trader bot --symbol axsusdt --dry-run --once 2>&1 | tail -30
```

Se algum dos bots tem teste/backtest associado em `scripts/` ou `backtest_sweep*/`, rode-o antes e depois e compare a saída numericamente — qualquer diferença em P&L, número de trades, ou timestamps de entrada/saída é uma regressão.

### 7. Reportar de volta

Resposta final ao usuário deve conter:
- Lista de arquivos tocados.
- Linhas removidas vs. adicionadas (`git diff --stat`).
- O que foi verificado (imports? dry-run? backtest comparado?).
- O que **não** foi verificado (ex.: "não rodei contra Binance real, não rodei o sweep Rust").

## Checklist final antes de declarar pronto

- [ ] Escopo do refactor confirmado e delimitado.
- [ ] Cada extração foi precedida por diff confirmando duplicação real.
- [ ] Nenhum comportamento novo introduzido (mesmas exceções, mesmos defaults, mesmas mensagens de erro).
- [ ] Imports atualizados em todos os call sites.
- [ ] Nenhum shim de compat / definição duplicada deixada para trás.
- [ ] `poetry run python -m trader --help` funciona.
- [ ] Dry-run de pelo menos um bot afetado passou.
- [ ] Backtest/sweep relevante comparado (ou explicitamente declarado como não rodado).
- [ ] `git diff --stat` reportado ao usuário.

## Quando recusar

Recuse (ou peça reescopo) se o pedido for:

- "Refatore tudo." → escopo aberto demais, alta chance de regressão silenciosa.
- "Reescreva o `bot.py` em classes menores." → reescrita ≠ refactor; vire um plano discutido antes.
- Mudar a interface pública de `cli.py` ou `config.py` "de passagem" → afeta scripts/Makefile/docs do usuário; tratar como feature separada.
- Refatorar algo que está sendo modificado em uma branch ativa → pedir para mergear primeiro.

---

# Mapeamento atual — Banco de dados

Esta seção descreve o **estado real do schema** (Postgres 16) tal como existe nas migrations `db/migrations/001_initial.sql` … `008_be_profit_usd.sql`. Foco puramente em modelagem: tabelas, colunas, chaves, relações. Sem código Python, sem fluxo de aplicação.

## Agrupamento lógico

```
┌─── CATÁLOGO ────────┐    ┌─── INFRA ──────────┐
│  strategies         │    │  schema_migrations │
│  symbol_configs     │    └────────────────────┘
└─────────────────────┘

┌─── LIVE TRADING ────────┐    ┌─── MARKET DATA ───┐
│  trades                 │    │  klines           │
│  sync_state             │    └───────────────────┘
│  daily_performance      │
│  equity_snapshots       │    ┌─── BACKTESTING ───┐
└─────────────────────────┘    │  sweep_results    │
                               └───────────────────┘
```

## Diagrama ER

```
                ┌──────────────────────┐
                │      strategies      │
                │──────────────────────│
                │ PK  id  SERIAL       │
                │ UQ  name  TEXT       │◄──┐
                │     description      │   │
                │     bot_command      │   │  FK: strategy_name → strategies.name
                │     direction        │   │  (única FK declarada do schema)
                │     active           │   │
                └──────────────────────┘   │
                                           │
                ┌──────────────────────┐   │
                │   symbol_configs     │───┘
                │──────────────────────│
                │ PK  symbol  TEXT     │◄────────┐
                │     asset            │         │
                │  →  strategy_name    │         │
                │     interval         │         │  Relações lógicas
                │     entry_*_min      │         │  (NENHUMA é FK real)
                │     tp_pct, sl_pct   │         │
                │     ema_period…      │         │  trades.symbol
                │     mode             │         │  klines.symbol
                │     leverage         │         │  sync_state.symbol
                │     be_profit_usd    │         │  daily_performance.symbol
                │     champion_*       │         │  sweep_results.symbol
                │     active           │         │
                │     updated_at       │         │
                └──────────────────────┘         │
                                                 │
   ─────── LIVE TRADING ──────────────           │
                                                 │
   ┌──────────────────┐    ┌─────────────────┐   │
   │     trades       │    │   sync_state    │   │
   │──────────────────│    │─────────────────│   │
   │ PK  id BIGSERIAL │    │ PK  symbol ─────┼───┤
   │ UQ (symbol,      │    │     last_order  │   │
   │     order_id)    │    │     last_synced │   │
   │     symbol  ─────┼────┤                 │   │
   │     order_id     │    └─────────────────┘   │
   │     side         │                          │
   │     price        │    ┌─────────────────────┴─┐
   │     qty          │    │  daily_performance    │
   │     realized_pnl │◄───┤───────────────────────│
   │     commission   │ A  │ PK (symbol, trade_date)│
   │     buyer        │    │     symbol            │
   │     trade_time   │    │     total_trades      │
   │     created_at   │    │     winning_trades    │
   └──────────────────┘    │     total_pnl         │
                           │     total_commission  │
                           │     gross_pnl         │
                           └───────────────────────┘
                                A = agregação SUM/COUNT por dia
                                    (refrescada por DELETE+INSERT,
                                     sem trigger / sem materialized view)

   ┌──────────────────────┐
   │  equity_snapshots    │   (independente; não referencia symbol)
   │──────────────────────│
   │ PK  id BIGSERIAL     │
   │ UQ  snapshot_time    │
   │     total_equity     │
   │     unrealized_pnl   │
   │     total_balance    │
   └──────────────────────┘

   ─────── MARKET DATA ─────────────                ─── BACKTESTING ───

   ┌──────────────────────┐                         ┌───────────────────┐
   │       klines         │                         │  sweep_results    │
   │──────────────────────│                         │───────────────────│
   │ PK  id BIGSERIAL     │                         │ PK  id BIGSERIAL  │
   │ UQ (symbol,          │                         │ UQ (symbol,       │
   │     timeframe,       │                         │     timeframe,    │
   │     open_time)       │                         │     strategy,     │
   │     symbol           │                         │     tp_pct, …,    │
   │     timeframe        │                         │     pdhl_prox_pct)│
   │     open/high/       │                         │     symbol        │
   │     low/close        │                         │     timeframe     │
   │     volume           │                         │     strategy      │
   │     close_time       │                         │     tp_pct, sl_pct│
   └──────────────────────┘                         │     rr_ratio      │
                                                    │     min_bars      │
                                                    │     ema_period    │
                                                    │     ...           │
                                                    │     trades, wins  │
                                                    │     win_rate      │
                                                    │     return_pct    │
                                                    │     max_dd_pct    │
                                                    │     is_champion   │
                                                    │     imported_at   │
                                                    └───────────────────┘

   ─────── INFRA ───────────────────

   ┌──────────────────────┐
   │  schema_migrations   │   (independente, gerenciada por migrate.py)
   │──────────────────────│
   │ PK  version  TEXT    │
   │     applied_at       │
   └──────────────────────┘
```

## Quadro de chaves e índices

| Tabela | PK | UNIQUE | Índices secundários | FK |
|---|---|---|---|---|
| `schema_migrations` | `version` | — | — | — |
| `strategies` | `id` | `name` | — | — |
| `symbol_configs` | `symbol` | — | `strategy_name`; `(active) WHERE active` | `strategy_name → strategies(name)` |
| `trades` | `id` | `(symbol, order_id)` | `(symbol, trade_time DESC)`; `(trade_time DESC)`; `(symbol, trade_time DESC) WHERE realized_pnl != 0` | — |
| `sync_state` | `symbol` | — | — | — |
| `klines` | `id` | `(symbol, timeframe, open_time)` | `(symbol, timeframe, open_time DESC)` | — |
| `daily_performance` | `(symbol, trade_date)` | — | `(trade_date DESC)` | — |
| `equity_snapshots` | `id` | `snapshot_time` | `(snapshot_time DESC)` | — |
| `sweep_results` | `id` | UQ longa em params (10 colunas) | `(symbol, timeframe)`; `(strategy)`; `(symbol, return_pct DESC)`; `(symbol, timeframe, is_champion) WHERE is_champion` | — |

## Cardinalidades

| De → Para | Cardinalidade | Tipo |
|---|---|---|
| `strategies` ← `symbol_configs` | 1 : N | FK declarada |
| `symbol_configs` ← `trades` | 1 : N | só por convenção (texto) |
| `symbol_configs` ← `klines` | 1 : N | só por convenção (texto) |
| `symbol_configs` ← `sync_state` | 1 : 1 | só por convenção (texto) |
| `symbol_configs` ← `daily_performance` | 1 : N | só por convenção (texto) |
| `symbol_configs` ← `sweep_results` | 1 : N | só por convenção (texto) |
| `equity_snapshots` | — | sem relação |
| `schema_migrations` | — | sem relação |

## Observações estruturais (problemas conhecidos)

1. **Ilha de FK**: a única foreign key real do schema é `symbol_configs.strategy_name → strategies(name)`. Todo o resto referencia `symbol` por string, sem integridade referencial. Apagar uma linha de `symbol_configs` não cascata em `trades`, `klines`, `sync_state`, `daily_performance`, `sweep_results`.

2. **Sem `ON DELETE` / `ON UPDATE`** definidos em lugar nenhum (porque quase não há FK).

3. **`symbol_configs` como super-tabela**: 30+ colunas misturando catálogo, parâmetros de 5 estratégias (`ema_period`, `fast_period`, `slow_period`, `range_mins`, `pdhl_prox_pct`), champion stats (`champion_return_pct`, `champion_win_rate`, `champion_trades`, `champion_max_dd`) e estado operacional (`active`, `mode`, `updated_at`). Candidato natural a normalizar em `symbol_strategy_params`, `symbol_runtime_state` e `symbol_backtest_stats`.

4. **`daily_performance` é cache derivável**: pode ser obtido de `trades` via `GROUP BY symbol, trade_time::date`. Hoje é materializado manualmente (DELETE + INSERT por dia), não é `MATERIALIZED VIEW`. Pode ficar fora de sincronia se alguém escrever em `trades` sem rodar o refresh.

5. **`sweep_results` UQ frágil**: a unique key combina 10 colunas com várias `NULL`-able. No Postgres, `NULL` é distinto em UNIQUE — duas linhas com mesmos params e algum `NULL` passam como diferentes. Frágil também para parâmetros novos: se uma estratégia futura introduzir param distintivo fora dessa lista, vira duplicata legal.

6. **Sem `CHECK` em campos enumerados**:
   - `klines.timeframe` é TEXT livre (esperado: `1m|5m|15m|30m|1h`).
   - `sweep_results.timeframe` idem.
   - `symbol_configs.mode` é `VARCHAR(20)` livre (esperado: `normal|monitoring`).
   - `trades.side` é TEXT livre (esperado: `BUY|SELL`).
   - Apenas `strategies.direction` tem `CHECK (direction IN ('LONG','SHORT','BOTH'))`.

7. **Inconsistência de modelagem para "janela de entrada"**:
   - Em `symbol_configs`: `entry_start_min INT`, `entry_cutoff_min INT` (minutos do dia).
   - Em `sweep_results`: `entry_window TEXT` (ex.: `"01-22"`).
   Mesmo conceito, dois formatos diferentes.

8. **Sem partição** em nenhuma tabela. `trades` e `klines` crescem linearmente — em algum momento viram candidatas a particionamento por mês ou por símbolo.

9. **Auditoria inconsistente**:
   - `trades.created_at` ✓
   - `symbol_configs.updated_at` ✓
   - `sync_state.last_synced_at` ✓
   - `sweep_results.imported_at` ✓
   - **Faltam**: `strategies` (sem `updated_at`/`active_changed_at`), `klines`, `equity_snapshots`, `daily_performance` (sem `refreshed_at`).

10. **Tipos numéricos não documentados mas consistentes**:
    - Preço/qty/PnL/commission: `NUMERIC(20,8)`.
    - Volume (klines): `NUMERIC(24,8)`.
    - Percentuais: `NUMERIC(8,4)`.
    - Equity/capital: `NUMERIC(20,8)` ou `NUMERIC(16,4)` (sweep_results).

11. **`equity_snapshots` é tabela órfã**: não tem `symbol` nem `account_id`. Implicitamente representa "a conta inteira do bot". Se um dia houver multi-conta, schema quebra silenciosamente — não há como diferenciar.

12. **Migrations sem checksum** (`db/migrations/001_…008_…`): `schema_migrations` só guarda `version` aplicada. Editar um `.sql` já aplicado é silenciosamente ignorado pelo runner.

13. **Migration 006 é dados, não schema**: `006_fill_extras.sql` é só `UPDATE symbol_configs SET …`. Roda novamente sobrescreve overrides manuais feitos depois. Idealmente seria seed em código, não migration.

## Eixos de refactor possíveis no schema (ordem sugerida, sem propor mudança ainda)

Estes são eixos para futuras conversas — **não execute sem novo escopo aprovado**:

1. **Adicionar `CHECK`s** em campos enumerados (`timeframe`, `mode`, `side`). Risco baixo, ganho alto.
2. **Adicionar FKs** `(symbol) → symbol_configs(symbol)` nas tabelas de live trading. Cuidado: hoje há `trades` para símbolos que podem nunca ter passado por `symbol_configs` (testes, símbolos descontinuados). Precisa de inventário antes.
3. **Separar `symbol_configs`** em 3 tabelas (config × runtime × backtest stats). Refactor grande, exige migração de dados e atualização de todos os SELECTs.
4. **Trocar `daily_performance` por `MATERIALIZED VIEW`** com refresh agendado. Elimina classe inteira de bug de "fora de sync".
5. **Particionar `trades` e `klines`** por mês quando o tamanho começar a impactar VACUUM/queries.
6. **Adicionar checksum a `schema_migrations`** (ex.: `MD5` do SQL). Detecta edição de migration aplicada.
7. **Mover `006_fill_extras.sql`** para um seeder Python (idempotente, opt-in).
