-- Migration 012: outcome TEXT no overfit_tests + UUID em presets.overfit_test_uuid
--
-- Tabelas estão vazias, então mudanças são limpas (sem compat layer).

-- ── overfit_tests: substitui passed BOOLEAN por outcome TEXT ─────────────────
ALTER TABLE overfit_tests DROP COLUMN passed;
ALTER TABLE overfit_tests ADD COLUMN outcome TEXT NOT NULL
    CHECK (outcome IN ('pass', 'fail', 'inconclusive'));

-- ── presets: overfit_test_id BIGINT → overfit_test_uuid UUID ─────────────────
-- BIGINT estava errado (test_uuid agrupa N rows por check_type).
ALTER TABLE presets DROP COLUMN overfit_test_id;
ALTER TABLE presets ADD COLUMN overfit_test_uuid UUID;
