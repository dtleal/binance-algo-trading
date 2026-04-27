# Arquitetura final — backtest/ (Rust)

Visão geral do estado pós-refactor: 1 crate Rust substitui 3 crates legados + 8 scripts Python.

## Diagrama geral

```
                          ╔═════════════════════════════════════╗
                          ║        backtest/  (Rust crate)      ║
                          ║   1 binário, 2 subcommands          ║
                          ╚═════════════════════════════════════╝

                                     ▼
            ┌────────────────────────────────────────────────────┐
            │           bin/backtest.rs   (clap CLI)             │
            │                                                    │
            │   ┌─────────────────┐      ┌──────────────────┐    │
            │   │  Sweep          │      │  Detail          │    │
            │   │  (grid)         │      │  (1 combo+chart) │    │
            │   └────────┬────────┘      └─────────┬────────┘    │
            └────────────┼─────────────────────────┼─────────────┘
                         │                         │
                         ▼                         ▼
                ┌────────────────┐        ┌────────────────┐
                │   sweep.rs     │        │   detail.rs    │
                │                │        │                │
                │ run_sweep:     │        │ run_detail:    │
                │  Phase 1 build │        │  build_entries │
                │  Phase 2       │        │  loop entries  │
                │  par_iter      │        │  exit::evaluate│
                │  cartesian     │        │  Trade list +  │
                │                │        │   equity curve │
                └───────┬────────┘        └────────┬───────┘
                        │                          │
                        │                          ▼
                        │                  ┌────────────────┐
                        │                  │   chart.rs     │
                        │                  │  HTML+plotly   │
                        │                  │  3 subplots:   │
                        │                  │  equity/PnL/DD │
                        │                  └────────────────┘
                        │
       ┌────────────────┴───────────────────────────────────┐
       │                                                    │
       ▼                                                    ▼
┌──────────────┐                                  ┌──────────────────┐
│  strategy/   │ trait Strategy                   │  exit/           │ trait Exit
│   mod.rs     │ ┌──────────────────┐             │   mod.rs         │ ┌──────────────┐
│              │ │ name()           │             │                  │ │ name()       │
│  factory     │ │ build(ctx)       │             │   factory        │ │ build_       │
│  build_      │ │   ↓ StrategyOutput│            │   build_exit()   │ │   variants() │
│  strategy()  │ │ ╔══════════════╗ │             │                  │ └──────────────┘
└──────┬───────┘ │ ║ EntrySets    ║ │             └──────┬───────────┘
       │         │ ║   (pareia    ║ │                    │
       │         │ ║   c/exit)    ║ │                    │
       │         │ ╚══════════════╝ │             ╔══════▼═══════════╗
       │         │ ╔══════════════╗ │             ║ ExitFn (enum)    ║  hot path
       │         │ ║ Runs         ║ │             ║                  ║  dispatch
       │         │ ║   (monolithic║ │             ║ FixedTpSl{tp,sl, ║  monomórfico
       │         │ ║    range)    ║ │             ║   max_hold}      ║
       │         │ ╚══════════════╝ │             ║ Trailing{sl,be_r,║
       │         └──────────────────┘             ║   trail,tp_r,    ║
       │                                          ║   max_hold}      ║
       │                                          ╚══════════════════╝
       │                                                  ▲
       │                                                  │
       │   ┌────────────────────────────────┐             │
       ├──▶│  strategy/momentum.rs          │             │
       │   │  RejShort/RejLong/MomShort/Long│   ┌─────────┴────────────┐
       │   └────────────────────────────────┘   │  exit/fixed_tp_sl.rs │
       │   ┌────────────────────────────────┐   │  TP%/SL% fixos       │
       ├──▶│  strategy/vwap_pullback.rs     │   │  + max_hold opcional │
       │   │  Bidirecional EMA-filtered     │   └──────────────────────┘
       │   └────────────────────────────────┘   ┌──────────────────────┐
       │   ┌────────────────────────────────┐   │  exit/trailing_stop  │
       ├──▶│  strategy/ema_scalp.rs         │   │  SL trail R-multiple │
       │   │  Fast/slow EMA crossover       │   │  + tp_r + helper     │
       │   └────────────────────────────────┘   └──────────────────────┘
       │   ┌────────────────────────────────┐
       ├──▶│  strategy/orb.rs               │
       │   │  Opening Range Breakout        │
       │   └────────────────────────────────┘
       │   ┌────────────────────────────────┐
       ├──▶│  strategy/pdhl.rs              │
       │   │  Previous Day H/L rejection    │
       │   └────────────────────────────────┘
       │   ┌────────────────────────────────┐
       └──▶│  strategy/range.rs             │
           │  Mean-reversion (monolithic,   │
           │   exit baked-in)               │
           └────────────────────────────────┘

                ▲ (strategies precomputam só os indicators que precisam)
                │
       ┌────────┴───────────────────────────────────┐
       │            indicator/                      │ funções puras
       │  ┌────────┐  ┌──────┐  ┌──────┐  ┌──────┐  │ fn(&[Candle]) → Vec<f64>
       │  │ vwap.rs│  │ema.rs│  │atr.rs│  │adx.rs│  │ (sem trait, sem alocação
       │  │rolling │  │      │  │Wilder│  │Wilder│  │  no hot loop)
       │  │ N-day  │  │      │  │      │  │      │  │
       │  └────────┘  └──────┘  └──────┘  └──────┘  │
       └────────────────────────────────────────────┘


       ┌───────────── types.rs (lib.rs reexporta) ─────────────┐
       │                                                       │
       │  Symbol(String) newtype       Direction { Long,Short} │
       │  Timeframe enum (1m..1d)      Entry  { entry_price,   │
       │  R(f64) R-multiple                     direction,…}   │
       │  FEE_PCT, INITIAL_CAPITAL,    Candle { o,h,l,c,v,…}   │
       │  ENTRY_*, END_OF_DAY                                  │
       │                                                       │
       │  EntrySet     { strategy_label, Arc<Vec<Entry>> }     │
       │  ExitVariant  { label, ExitFn }                       │
       │  MonolithicRun{ label, Box<dyn Fn(&[Candle])>}        │
       │  StrategyOutput::EntrySets(_) | ::Runs(_)             │
       │  Ctx<'a>      { symbol, timeframe, candles, days }    │
       │  RunResult    { strategy, exit_name, sweep_id,        │
       │                 params_hash, period_*, metrics }      │
       │                                                       │
       └───────────────────────────────────────────────────────┘


       ┌─────────────────── db.rs (postgres sync) ─────────────┐
       │                                                       │
       │  connect_from_env()       ──→ postgres::Client        │
       │  load_candles(...)        ──→ Vec<Candle>             │
       │  group_by_day(candles)    ──→ DayIndex                │
       │  write_sweep_results(...) ──→ INSERT batch + ON       │
       │                               CONFLICT DO NOTHING     │
       │                               (NUMERIC via            │
       │                                rust_decimal)          │
       └───────────────────────────────────────────────────────┘
```

## Dependências

```
postgres (sync, with-chrono, with-uuid)     rust_decimal
postgres-types                              rayon
clap (Subcommand derive)                    serde + toml
uuid (v4)                                   md-5
chrono                                      anyhow + thiserror
tracing + tracing-subscriber

❌ sem tokio/sqlx (binário sync; rayon paraleliza CPU)
❌ sem plotters (chart é HTML estático + plotly.js CDN)
```

## Fluxos completos

### Sweep (grid)
```
postgres → load_candles
         → strategies[*].build()  ─┐
         → exits[*].build_variants()
                                   │
         → cartesian × par_iter   ─┘
         → run_pairing (per-entry: ExitFn dispatch)
         → Vec<RunResult>
         → db::write_sweep_results (sweep_id agrupa)
```

### Detail (1 combo + chart)
```
postgres → load_candles
         → detail::build_entries(strategy, params)
         → run_detail (per-entry: ExitFn dispatch + tracking)
         → Vec<Trade> + DetailResult
         → stdout summary + trade table
         → chart::write_html (plotly.js CDN)
```

## Princípios aplicados (decisões finais)

| Decisão | Motivo |
|---|---|
| Trait fora, enum dentro | Strategy/Exit são traits → extensível. ExitFn é enum → hot path monomórfico, zero vtable |
| StrategyOutput enum (EntrySets vs Runs) | Range não cabe no Strategy×Exit cartesian; isola sem poluir o trait |
| Indicators como funções puras (sem trait) | Função pura `fn(&[Candle], params) → Vec<f64>` é testável e composável; trait seria cerimônia |
| `postgres` sync (não `sqlx`/tokio) | Binário batch CPU-bound; async só seria fricção. Rayon cuida do paralelismo |
| HTML estático + plotly.js CDN (não `plotters`) | Sem dep Rust; output indistinguível do Python; ~50 linhas |
| Newtypes (Symbol, Timeframe, R) | Erros de digitação morrem no compilador |
| Phase 1 / Phase 2 explícitas no sweep | Preserva perf — entries precomputados uma vez por strategy params, depois cartesian com exit variants |
| Factory por match (não inventory!/linkme) | 6 strategies + 2 exits = closed set; macro de registro seria overkill |

## Números

| Métrica | Valor |
|---|---|
| Total Rust | ~3.000 linhas em 17 arquivos |
| Substituiu | 3 crates legados (~3.000 linhas) + 8 scripts Python (~3.000) |
| Saldo | **−3.000 linhas, mais funcionalidade** |
| Compile time | ~14s release, ~5s incremental |
| Smoke test (8.640 candles BTCUSDT 5m) | sweep ORB×fixed = 7ms, detail = 30ms |
| Sweep cartesiano grande (vwap_pullback × fixed) | 1.6M evaluations em ~800ms |

## Comandos

```bash
make build-sweep                                       # cargo build --release

make sweep   SYMBOL=btcusdt TIMEFRAME=5m               # grid completo (todas strategies × ambos exits)
make sweep   SYMBOL=btcusdt TIMEFRAME=5m STRATEGY=vwap_pullback EXIT=fixed_tp_sl,trailing_stop
make sweep-range  SYMBOL=btcusdt                       # só range
make sweep-trailing SYMBOL=btcusdt                     # só trailing exit

make detail  SYMBOL=btcusdt TIMEFRAME=5m STRATEGY=vwap_pullback \
             TP=0.005 SL=0.01 EMA=200 VWAP_PROX=0.005 CONFIRM_BARS=1

# Wrappers retrocompatíveis (chamam make detail com defaults)
make backtest-detail            # MomShort momentum
make backtest-detail-pullback   # VWAPPullback
make backtest-detail-pdhl       # PDHL
make backtest-eth-5m            # ETH 5m vwap_pullback hardcoded
```
