# Active Bots Portfolio (28)

Source of truth:
- Runtime strategy allocation: `Makefile` target `bots`
- Symbol parameters: `trader/config.py` (`SYMBOL_CONFIGS`)

Last updated: 2026-03-05

## Strategy Distribution

- MomShort: 4 bots
- VWAPPullback: 14 bots
- PDHL: 9 bots
- ORB: 1 bot

Total: 28 bots

## Full Bot Matrix

| Symbol | Strategy | TF | TP% | SL% | min_bars | confirm | vwap_prox | vol_filter | pos_size | leverage | mode |
|---|---|---|---:|---:|---:|---:|---:|---|---:|---:|---|
| AXSUSDT | MomShort | 1m | 10.0 | 5.0 | 3 | 2 | 0.005 | no | 0.40 | 30 | normal |
| SANDUSDT | MomShort | 5m | 10.0 | 1.0 | 5 | 0 | 0.005 | yes | 0.40 | 30 | normal |
| MANAUSDT | PDHL | 1m | 7.0 | 1.5 | 0 | 3 | 0.005 | no | 0.40 | 30 | normal |
| LDOUSDT | PDHL | 1m | 7.0 | 2.0 | 0 | 1 | 0.000 | no | 0.40 | 30 | normal |
| RLCUSDT | PDHL | 15m | 3.0 | 2.0 | 0 | 1 | 0.000 | no | 0.40 | 30 | normal |
| MTLUSDT | PDHL | 1m | 5.0 | 5.0 | 0 | 1 | 0.000 | no | 0.40 | 30 | normal |
| ICXUSDT | PDHL | 5m | 7.0 | 2.0 | 0 | 2 | 0.005 | no | 0.20 | 30 | normal |
| SOLUSDT | MomShort | 1m | 7.0 | 5.0 | 8 | 0 | 0.002 | yes | 0.40 | 30 | normal |
| THETAUSDT | MomShort | 5m | 3.0 | 5.0 | 3 | 2 | 0.005 | yes | 0.40 | 30 | normal |
| GALAUSDT | VWAPPullback | 1m | 10.0 | 5.0 | 3 | 0 | 0.002 | no | 0.40 | 1 | monitoring |
| AVAXUSDT | VWAPPullback | 1m | 7.0 | 2.0 | 30 | 0 | 0.005 | no | 0.40 | 30 | normal |
| DOGEUSDT | VWAPPullback | 5m | 10.0 | 5.0 | 3 | 0 | 0.002 | no | 0.40 | 30 | normal |
| 1000SHIBUSDT | VWAPPullback | 5m | 7.0 | 5.0 | 3 | 0 | 0.005 | no | 0.40 | 1 | monitoring |
| XRPUSDT | VWAPPullback | 5m | 10.0 | 2.0 | 3 | 0 | 0.005 | no | 0.40 | 30 | normal |
| ETHUSDT | VWAPPullback | 5m | 10.0 | 5.0 | 20 | 0 | 0.005 | no | 0.40 | 1 | monitoring |
| XAUUSDT | VWAPPullback | 1m | 5.0 | 5.0 | 3 | 1 | 0.005 | no | 0.40 | 1 | monitoring |
| XMRUSDT | VWAPPullback | 1m | 7.0 | 5.0 | 8 | 0 | 0.002 | no | 0.40 | 30 | normal |
| UNIUSDT | VWAPPullback | 15m | 10.0 | 2.0 | 3 | 1 | 0.005 | no | 0.40 | 30 | normal |
| APTUSDT | VWAPPullback | 5m | 10.0 | 5.0 | 3 | 0 | 0.005 | no | 0.40 | 30 | normal |
| 1000PEPEUSDT | VWAPPullback | 5m | 10.0 | 5.0 | 5 | 2 | 0.002 | no | 0.40 | 30 | normal |
| DASHUSDT | VWAPPullback | 15m | 5.0 | 5.0 | 3 | 0 | 0.002 | no | 0.40 | 30 | normal |
| ZECUSDT | VWAPPullback | 5m | 10.0 | 5.0 | 8 | 2 | 0.005 | no | 0.40 | 30 | normal |
| AAVEUSDT | VWAPPullback | 5m | 10.0 | 5.0 | 3 | 2 | 0.002 | no | 0.40 | 30 | normal |
| LTCUSDT | PDHL | 1m | 3.0 | 5.0 | 0 | 1 | 0.000 | no | 0.40 | 30 | normal |
| LINKUSDT | PDHL | 1m | 10.0 | 5.0 | 0 | 2 | 0.000 | no | 0.40 | 30 | normal |
| BCHUSDT | PDHL | 5m | 10.0 | 5.0 | 0 | 1 | 0.005 | no | 0.40 | 30 | normal |
| MAGICUSDT | PDHL | 1h | 10.0 | 5.0 | 0 | 1 | 0.000 | no | 0.40 | 30 | normal |
| KSMUSDT | ORB | 1h | 10.0 | 5.0 | 0 | 0 | 0.000 | no | 0.40 | 30 | normal |

## Notes

- `PEPEUSDT` exists in config as `PEPE_CONFIG` but is intentionally not active in `SYMBOL_CONFIGS`.
- Some bots run in `monitoring` mode with lower leverage/position size.
- If `symbol_configs` in DB differs from Python config, runtime loads DB first and uses `trader/config.py` as fallback.

## Portfolio Candidates (Onboarding)

| Symbol | Candidate Strategy | TF | Return | Max DD | Source |
|---|---|---|---:|---:|---|
| MANAUSDT | PDHL | 1m | +95.61% | 13.87% | `data/sweeps/manausdt_1m_sweep.csv` |
| LDOUSDT | PDHL | 1m | +79.96% | 18.23% | `data/sweeps/LDOUSDT_1m_sweep.csv` |
| RLCUSDT | PDHL | 15m | +48.61% | 12.31% | `data/sweeps/RLCUSDT_15m_sweep.csv` |
| MTLUSDT | PDHL | 1m | +83.58% | 14.44% | `data/sweeps/MTLUSDT_1m_sweep.csv` |
