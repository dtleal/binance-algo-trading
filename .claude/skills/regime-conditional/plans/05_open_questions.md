# Open Questions — decisões pendentes / unknowns

Lista honesta do que ainda não está resolvido. Cada item é decisão técnica ou validação empírica que pode mudar o plano.

## Open #1 — 3 buckets é o número certo?

Decisão atual: 3 buckets (low/mid/high) por feature.

Trade-offs:
- **3 buckets**: ~256 trades/cell em PDHL ETH, estatística boa, mas perde resolução (regime extremo vs moderado fica junto em "high")
- **5 buckets**: ~150 trades/cell, mais resolução, mais ruído estatístico
- **Adaptive (decision tree)**: ótimo, mas overfit fácil com nossas amostras

**Validação proposta**: rodar M2 com 3 e 5 buckets, comparar quantos buckets `is_useful=true` em cada. Mais nem sempre é melhor — pode ser que com 5 buckets vários virem "noise".

## Open #2 — Quão estacionário é o regime?

Premissa: regime detectável em 50/50 IS/OOS split do histórico (1 ano).

Crypto regimes mudam em ~6 meses (BTC dominance shifts, halving cycles, regulatory news). **Fingerprint construído em Apr 2025-Out 2025 pode não persistir em 2027**.

**Validação proposta**: incluir no M2 um teste de **fingerprint decay**:
- Construir fingerprint em primeiros 6 meses
- Validar nos meses 7-9
- Validar nos meses 10-12
- Se win rate cai monotonicamente, fingerprint não é estacionário → precisa rebuilder periódico

Se decay >30% em 6 meses, considerar **re-fingerprint mensal** em produção (computar é barato — só replay).

## Open #3 — Múltiplos símbolos ou ETH primeiro?

Decisão atual: M0-M4 valida com presets em **mesmo símbolo** (ETH 15m).

Aberto:
- Se M0 só encontra 1 preset (PDHL ETH 15m) e mais 0: composite vira inútil
- Se M0 encontra 3+ presets em **símbolos diferentes**: composite precisa lidar com correlação cross-asset (ETH e BTC andam 0.7-0.9 correlated)

**Validação**: no M0c, computar correlação **cross-asset** entre presets. Se alta, expectativa de uplift cai.

## Open #4 — Latência de detecção de regime

Quando regime muda (e.g. trend → range), os indicadores tomam ~10-20 candles pra refletir. Nesse intervalo, score do PDHL ainda parece alto, e o composite ativa em regime já trocado → losses.

**Não tem solução perfeita**. Mitigantes possíveis:
- Smoothing do score (média móvel sobre 3-5 candles): suaviza mas atrasa
- Detector de mudança de regime (CUSUM, GARCH): complexidade alta
- Aceitar latência como custo do framework

**Decisão**: documentar e medir. Se latência custa >2% do return total, considerar mitigantes.

## Open #5 — Símbolo do score: qual TF usar pra regime?

Atual: score computado no MESMO TF do trade (15m pro PDHL 15m).

Alternativa: regime no **TF mais alto** (4h ou 1d). Argumento: regime de mercado é macro, captado melhor em TF alta. PDHL opera em 15m mas decisão de regime usa 4h.

**Validação**: testar ambos em M2 (computar fingerprint nos 2 TFs). Comparar qual gera mais buckets `is_useful`. Provavelmente TF alto.

## Open #6 — Bot Python ou subprocess Rust?

Plano atual: porta features+score pra Python (M4d).

Alternativa: bot Python invoca subprocess Rust pra cada cálculo de score. Latência ~5-10ms (aceitável pra TF 15m).

**Trade-off**:
- Porta Python: mais rápido em runtime, mais código pra manter, fragilidade de paridade
- Subprocess Rust: zero divergência possível, mas overhead de comunicação

**Decisão por ora**: porta Python + golden vectors em CI. Se golden vectors derem trabalho recorrente de calibrar, migrar pra subprocess.

## Open #7 — Position sizing dinâmico?

Atual: 2 níveis (mult=0.5 ou 1.0).

Alternativa: contínuo (`pos_size_mult = score`).

**Trade-off**:
- Discreto: mais simples, mais transição, comportamento mais previsível
- Contínuo: mais smooth, mas posições "morrem" via redução em vez de exit, complica lógica

**Decisão**: V1 discreto (atual). V2 contínuo se houver evidência empírica que vale.

## Open #8 — Como tratar correlação cross-strategy mas same-symbol?

PDHL ETH 15m e range ETH 1h podem ter correlação alta (mesma direção de ETH). M0c computa correlação. M4 filtra.

Mas se composite só tem 2 presets e ambos ETH: filtro pode rejeitar ambos quando ambos ativos. Resultado: 50% do tempo só 1 preset opera.

**Mitigação**: max_correlation pode ser **calibrado** (não hardcoded 0.7). 0.5 é mais agressivo, 0.85 é mais permissivo. Calibrar no grid de M4.

## Open #9 — Stop-loss para o composite (não os presets)

Atual: kill switch desativa tudo se DD>limit.

Aberto: composite pode ter **stop-loss agregado** (fechar posições abertas, não só desativar entradas)?

**Trade-off**:
- Soft kill: para abrir, deixa abertas → DD continua se mercado piora
- Hard kill: fecha todas → cristaliza loss, pode ser falso alarme

**Decisão**: V1 soft kill (mais conservador, evita panic-close). V2 hard kill se backtest mostrar redução de DD significativa.

## Open #10 — Métricas de sucesso do composite

Plano atual: bater baselines por ≥5% em retorno absoluto.

Alternativas:
- Sharpe ≥1.5
- Calmar ≥3
- Max DD <10%

Devemos olhar múltiplas métricas. Plano não detalhou.

**Decisão**: M4 reporta TODAS (return, Sharpe, Calmar, max DD, Sortino, recovery time). Composite "ganha" se domina baselines em ≥3 das 5 métricas. Não absolutamente, mas Pareto-superior.

## Como usar essa lista

- Antes de cada milestone, revisar Opens relevantes
- Open vira "fechado" quando milestone produz dados que respondem
- Novos Opens podem surgir — adicionar aqui

Mantém o plano honesto: o que sabemos ≠ o que ainda não sabemos.
