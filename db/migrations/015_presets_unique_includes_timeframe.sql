-- 015: Fix unique constraint em presets — incluir timeframe.
--
-- Bug: o constraint original "uq_presets_active" só usa (symbol, strategy),
-- então PDHL ETH 15m e PDHL ETH 30m são tratados como o mesmo preset. Quando
-- o segundo é released, o primeiro é retired indevidamente.
--
-- Fix: incluir timeframe na chave. Mesmo strategy em TFs diferentes são
-- produtos distintos pra o bot (cada um tem seu próprio fingerprint, regime,
-- pos_size).
--
-- Mesma correção em release.rs (já tipado lá).

BEGIN;

-- Drop constraint antigo
DROP INDEX IF EXISTS uq_presets_active;

-- Recria incluindo timeframe
CREATE UNIQUE INDEX uq_presets_active
    ON presets (symbol, strategy, timeframe)
    WHERE status = 'active';

COMMIT;
