-- Migration 009: klines preparado para bulk-insert da Binance
--
-- Mudanças:
--   * NUMERIC(20,8) → DOUBLE PRECISION em OHLCV (mais rápido, ~15-17 dígitos significativos cobrem cripto)
--   * Drop id BIGSERIAL; PK natural (symbol, timeframe, open_time) — sem nextval, 1 índice em vez de 2
--   * fillfactor = 100 (append-only; sem espaço reservado para HOT updates)
--   * CHECK em timeframe (fail-fast em valores inválidos)
--   * +4 colunas que a Binance manda mas não guardávamos: quote_volume, trades, taker_buy_base, taker_buy_quote
--
-- Como é mudança de tipo + PK, faz via tabela nova + swap (atômico via BEGIN/COMMIT do migrate.py).

CREATE TABLE klines_new (
    symbol           TEXT             NOT NULL,
    timeframe        TEXT             NOT NULL,
    open_time        TIMESTAMPTZ      NOT NULL,
    open             DOUBLE PRECISION NOT NULL,
    high             DOUBLE PRECISION NOT NULL,
    low              DOUBLE PRECISION NOT NULL,
    close            DOUBLE PRECISION NOT NULL,
    volume           DOUBLE PRECISION NOT NULL,
    close_time       TIMESTAMPTZ      NOT NULL,
    quote_volume     DOUBLE PRECISION,
    trades           INT,
    taker_buy_base   DOUBLE PRECISION,
    taker_buy_quote  DOUBLE PRECISION,
    PRIMARY KEY (symbol, timeframe, open_time),
    CHECK (timeframe IN ('1m','2m','3m','5m','15m','30m','1h','2h','4h','6h','8h','12h','1d'))
) WITH (fillfactor = 100);

INSERT INTO klines_new
    (symbol, timeframe, open_time, open, high, low, close, volume, close_time)
SELECT
    symbol, timeframe, open_time,
    open::double precision, high::double precision, low::double precision,
    close::double precision, volume::double precision, close_time
FROM klines
ON CONFLICT (symbol, timeframe, open_time) DO NOTHING;

DROP TABLE klines;
ALTER TABLE klines_new RENAME TO klines;

-- Índice DESC para queries "últimos N candles" (frontend chart, monitor).
-- PK natural ASC já cobre BETWEEN para sweep walk-forward.
CREATE INDEX idx_klines_latest
    ON klines (symbol, timeframe, open_time DESC);
