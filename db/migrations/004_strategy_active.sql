-- Migration 004: add active flag to strategies table
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE;
