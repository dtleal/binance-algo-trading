# Position Risk Guard — Plano de Implementacao

Last updated: 2026-03-04

## Objetivo

Reduzir drawdown intraday causado por "morte lenta" contra a posicao (quando o mercado invalida a tese e o bot permanece ate SL/TP/EOD).

## Fase 1 — Regras Deterministicas (implementar agora)

Escopo: todos os bots de execucao (`MomShort`, `VWAPPullback`, `PDHL`, `ORB`, `EMAScalp`).

Observacao:
- `VWAPPullback-v2` ficou explicitamente fora desta fase por decisao operacional atual.

Itens:
- `time_stop_minutes`:
  - Se a operacao nao mostrar progresso minimo apos X minutos (default 20), fecha antecipadamente.
  - Criterio default: apos X minutos, se `PnL% <= time_stop_min_progress_pct` (default 0.0), encerra.
- `adverse_exit`:
  - Se ocorrer sequencia de candles adversos "fortes" contra a direcao da posicao, encerra.
  - Defaults:
    - `adverse_exit_bars = 3`
    - `adverse_body_min_pct = 0.20` (corpo minimo do candle em %)
  - Condicao adicional: acionar somente com `PnL% < 0` para evitar sair de trade vencedor por ruido.

Entrega tecnica:
- Novos parametros por bot (defaults hardcoded na Fase 1):
  - `time_stop_minutes`
  - `time_stop_min_progress_pct`
  - `adverse_exit_bars`
  - `adverse_body_min_pct`
- Novos estados internos por posicao:
  - `entry_ts_ms`
  - `adverse_count`
  - `risk_exit_pending`
- Novo motivo de fechamento antecipado:
  - `Time stop`
  - `Adverse momentum`
- Logs e eventos:
  - Log explicito de disparo da regra.
  - Manter `position_closed` e notificacoes existentes.

Validacao:
- Teste sintatico e build.
- Smoke test em paper:
  - abrir posicao e simular candles adversos
  - validar fechamento antecipado e reset de estado.

## Fase 2 — Parametrizacao via DB + Dashboard

Objetivo: tornar o guard configuravel sem deploy.

Itens:
- Persistir parametros no `symbol_configs`:
  - `time_stop_minutes`
  - `time_stop_min_progress_pct`
  - `adverse_exit_bars`
  - `adverse_body_min_pct`
- Carregar via `DB > CLI > config.py`.
- Exibir no dashboard (card config + motivo do ultimo close).

Migracoes:
- SQL migration em `db/migrations/`.
- Atualizacao de queries em `db/queries/symbol_config.py`.

## Fase 3 — AI Position Guardian (OpenAI default, Anthropic fallback)

Objetivo: camada supervisora para reduzir risco em posicoes abertas, sem substituir regras deterministicas.

Arquitetura:
- Novo worker assinc:
  - varre posicoes abertas a cada N segundos.
  - coleta features (preco, pnl, distancia para SL/TP, volatilidade, regime, candles recentes).
- Provider:
  - `CHAT_PROVIDER=openai` default.
  - fallback `anthropic`.
- Acoes permitidas:
  - `hold`
  - `close_position`
  - `move_stop_to_breakeven`
  - `tighten_stop` (nunca afrouxar)
  - `reduce_position` (opcional)

Guardrails obrigatorios:
- Nunca aumentar risco:
  - proibido ampliar SL
  - proibido aumentar size em perda.
- Cooldown de acao por simbolo.
- Max acoes por trade.
- Threshold de confianca minimo.
- Kill switch global por env.
- Auditoria completa de decisao (prompt/resumo/features/acao/resultado).

## Fase 4 — Contexto Externo (Noticias/Fundamental) + Backtest/Replay

Objetivo: enriquecer decisao da IA sem comprometer seguranca.

Itens:
- Integracao de feed de noticias por ativo (headline + sentimento + timestamp + source).
- Janela de bloqueio em eventos de alto impacto (ex: CPI/FOMC para majors).
- Replay framework:
  - reexecutar historico de posicoes com/sem guardian
  - comparar KPIs.

KPIs de aceite:
- reduzir max drawdown
- reduzir media de perda por trade
- manter ou melhorar expectancy
- degradacao de win rate aceitavel, desde que net PnL e DD melhorem.

## Rollout e Risco Operacional

Rollout recomendado:
1. Fase 1 em paper para todos os bots.
2. Canary em 2-3 simbolos em producao.
3. Expandir gradualmente.

Rollback:
- Flag para desativar `time_stop` e `adverse_exit`.
- Reversao rapida via env/DB sem remover codigo.

## Ordem de Implementacao

1. Fase 1 (agora)
2. DB parametrization (Fase 2)
3. AI guardian com guardrails (Fase 3)
4. Noticias + replay comparativo (Fase 4)
