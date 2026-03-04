-- Migration 007: Add mode column to symbol_configs
-- Values: 'normal' (default) | 'monitoring' (reduced risk, under observation)

ALTER TABLE symbol_configs
  ADD COLUMN IF NOT EXISTS mode VARCHAR(20) NOT NULL DEFAULT 'normal';
