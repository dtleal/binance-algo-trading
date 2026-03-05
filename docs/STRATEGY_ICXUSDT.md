# PDHL Strategy — ICXUSDT Onboarding Guide

Backtested on `ICXUSDT` `5m` candles over 366 days.

## Selected Runtime Profile

The selected onboarding profile uses the best `5m` PDHL sweep result:

| Parameter | Value |
|---|---:|
| Strategy | `PDHL` |
| Timeframe | `5m` |
| TP | `7.0%` |
| SL | `2.0%` |
| Confirm bars | `2` |
| PDHL proximity | `0.5%` |
| Entry window | `01:00-22:00 UTC` |
| Max hold | `EOD (23:50 UTC)` |
| Sweep position size | `20%` |
| Sweep trades | `879` |
| Win rate | `44.6%` |
| Return | `+47.31%` |
| Max drawdown | `13.27%` |

## Runtime Notes

- Live bot command: `python -m trader pdhl --symbol icxusdt`
- Runtime stays on the current PDHL bot rules:
  - maker-first entries/exits when possible,
  - hard protection stop,
  - auto-breakeven (`0a0`) after `+$0.50` unrealized PnL,
  - early protection exits (time stop / adverse momentum) remain enabled.
- Exchange precision for Binance Futures:
  - `price_decimals=4`
  - `qty_decimals=0`
  - `min_notional=5.0`

## Caveats

- `ICXUSDT` did not pass the onboarding sweep cleanly on robustness:
  - all strategies remained negative on average across all tested timeframes,
  - the selected `PDHL 5m` setup is a strong champion, but still an outlier inside a broadly weak search space.
- Because of that, runtime onboarding keeps the champion `pos_size_pct=0.20` instead of the portfolio-wide `0.40` default used by many other bots.
- No dedicated detailed `PDHL` trade-by-trade backtest script was added in this task; this onboarding is based on the sweep champion plus current live bot protection rules.
