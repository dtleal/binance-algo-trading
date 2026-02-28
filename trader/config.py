import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BINANCE_API_KEY = os.getenv("API_KEY", "")
BINANCE_SECRET_KEY = os.getenv("SECRET_KEY", "")
SOCKS_PROXY = os.getenv("SOCKS_PROXY", "")  # e.g. "socks5://127.0.0.1:1081"

DEFAULT_SYMBOL = "axsusdt"
DEFAULT_SYMBOL_UPPER = "AXSUSDT"
DEFAULT_ASSET = "AXS"

ALL_STREAMS = ["trade", "kline", "ticker", "depth"]

DEFAULT_STOP_LOSS_PCT = 5.0  # percent above entry price
DEFAULT_LEVERAGE = 5

# MomShort strategy parameters
STRATEGY_TP_PCT = 10.0           # take-profit % below entry
STRATEGY_SL_PCT = 5.0            # stop-loss % above entry
STRATEGY_MIN_BARS = 3            # min consolidation candles near VWAP
STRATEGY_CONFIRM_BARS = 2        # confirmation candles below VWAP
STRATEGY_VWAP_PROX = 0.005       # 0.5% proximity threshold
STRATEGY_ENTRY_START_MIN = 60    # 01:00 UTC
STRATEGY_ENTRY_CUTOFF_MIN = 1320 # 22:00 UTC
STRATEGY_EOD_MIN = 1430          # 23:50 UTC
STRATEGY_POS_SIZE_PCT = 0.95     # 95% of capital per trade (small account)

LOG_DIR = Path("logs")


# ---------------------------------------------------------------------------
# Per-symbol configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SymbolConfig:
    symbol: str           # e.g. "AXSUSDT"
    asset: str            # e.g. "AXS"
    # Strategy
    tp_pct: float         # take-profit %
    sl_pct: float         # stop-loss %
    min_bars: int         # min consolidation candles near VWAP
    confirm_bars: int     # confirmation candles below VWAP
    vwap_prox: float      # proximity threshold (fraction, e.g. 0.005 = 0.5%)
    entry_start_min: int  # minutes from midnight UTC
    entry_cutoff_min: int
    eod_min: int
    pos_size_pct: float   # fraction of capital per trade (e.g. 0.95)
    # Precision
    price_decimals: int   # 3 for AXS, 5 for SAND
    qty_decimals: int     # 0 for both (whole integers)
    vol_filter: bool = False   # require volume > SMA(20) on breakdown candle
    min_notional: float = 5.0  # Binance minimum order notional (USDT)
    interval: str = "1m"  # WebSocket kline interval (1m, 5m, 15m, 30m, 1h)
    vwap_dist_stop: float = 0.0  # exit if price moves >X% from VWAP in wrong direction (0 = disabled)


AXS_CONFIG = SymbolConfig(
    symbol="AXSUSDT",
    asset="AXS",
    tp_pct=10.0,
    sl_pct=5.0,
    min_bars=3,
    confirm_bars=2,
    vwap_prox=0.005,
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,
    price_decimals=3,
    qty_decimals=0,
    interval="1m",
)

SAND_CONFIG = SymbolConfig(
    symbol="SANDUSDT",
    asset="SAND",
    tp_pct=10.0,        # Champion: 5m MomShort, +27.61% return (250 trades, 34.0% WR)
    sl_pct=1.0,
    min_bars=5,
    confirm_bars=0,
    vwap_prox=0.005,    # 0.5%
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,
    price_decimals=5,
    qty_decimals=0,
    vol_filter=True,
    interval="5m",
)

MANA_CONFIG = SymbolConfig(
    symbol="MANAUSDT",
    asset="MANA",
    tp_pct=5.0,         # Champion: 1m MomShort, +30.54% return (295 trades, 52.9% WR)
    sl_pct=5.0,
    min_bars=12,
    confirm_bars=2,
    vwap_prox=0.005,    # 0.5%
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,  # 40% per trade
    price_decimals=4,
    qty_decimals=0,
    vol_filter=True,
    interval="1m",
)

GALA_CONFIG = SymbolConfig(
    symbol="GALAUSDT",
    asset="GALA",
    tp_pct=10.0,        # Champion: 1m VWAPPullback, +34.85% return (357 trades, 52.1% WR)
    sl_pct=5.0,
    min_bars=3,
    confirm_bars=0,
    vwap_prox=0.002,    # 0.2%
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,
    vol_filter=False,
    price_decimals=5,
    qty_decimals=0,
    interval="1m",
)

DOGE_CONFIG = SymbolConfig(
    symbol="DOGEUSDT",
    asset="DOGE",
    tp_pct=10.0,        # Champion: 5m VWAPPullback, +42.75% return (322 trades, 52.5% WR, maxDD=6.09%)
    sl_pct=5.0,
    min_bars=3,
    confirm_bars=0,
    vwap_prox=0.002,    # 0.2%
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,
    price_decimals=5,
    qty_decimals=0,
    interval="5m",
    vwap_dist_stop=0.03,  # exit if price >3% from VWAP in wrong direction
)

SHIB_CONFIG = SymbolConfig(
    symbol="1000SHIBUSDT",
    asset="SHIB",
    tp_pct=7.0,         # Champion: 5m VWAPPullback, +37.51% return (354 trades, 53.1% WR)
    sl_pct=5.0,
    min_bars=3,
    confirm_bars=0,
    vwap_prox=0.005,    # 0.5%
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,
    price_decimals=6,
    qty_decimals=0,
    interval="5m",
)

ETH_CONFIG = SymbolConfig(
    symbol="ETHUSDT",
    asset="ETH",
    tp_pct=10.0,        # Champion: 5m VWAPPullback, +31.87% return (251 trades, 51.0% WR, maxDD=3.95%)
    sl_pct=5.0,
    min_bars=20,
    confirm_bars=0,
    vwap_prox=0.005,    # 0.5%
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,  # 40% per trade
    price_decimals=2,
    qty_decimals=3,
    min_notional=20.0,
    vol_filter=False,
    interval="5m",
    vwap_dist_stop=0.03,  # exit if price >3% from VWAP in wrong direction
)

SOL_CONFIG = SymbolConfig(
    symbol="SOLUSDT",
    asset="SOL",
    tp_pct=7.0,         # Champion: 1m MomShort, +28.13% return (302 trades, 53.3% WR)
    sl_pct=5.0,
    min_bars=8,
    confirm_bars=0,
    vwap_prox=0.002,    # 0.2%
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,  # 40% per trade
    price_decimals=2,
    qty_decimals=2,
    min_notional=5.0,
    vol_filter=True,    # Volume filter enabled for MomShort
    interval="1m",
)

AVAX_CONFIG = SymbolConfig(
    symbol="AVAXUSDT",
    asset="AVAX",
    tp_pct=7.0,         # Champion: 1m VWAPPullback, +31.12% return
    sl_pct=2.0,
    min_bars=30,
    confirm_bars=0,
    vwap_prox=0.005,    # 0.5%
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,  # 40% per trade
    price_decimals=2,
    qty_decimals=2,
    min_notional=5.0,
    interval="1m",
)

XRP_CONFIG = SymbolConfig(
    symbol="XRPUSDT",
    asset="XRP",
    tp_pct=10.0,        # Champion: 5m VWAPPullback, +30.15% return (351 trades, 45.0% WR)
    sl_pct=2.0,
    min_bars=3,
    confirm_bars=0,
    vwap_prox=0.005,    # 0.5%
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,  # 40% per trade
    price_decimals=4,
    qty_decimals=1,
    vol_filter=False,
    min_notional=5.0,
    interval="5m",
    vwap_dist_stop=0.03,  # exit if price >3% from VWAP in wrong direction
)

PEPE_CONFIG = SymbolConfig(
    symbol="PEPEUSDT",
    asset="PEPE",
    tp_pct=7.0,         # Champion: 1m MomShort, +35.63% return (315 trades, 51.7% WR)
    sl_pct=5.0,
    min_bars=12,
    confirm_bars=2,
    vwap_prox=0.005,    # 0.5%
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,  # 40% per trade
    price_decimals=8,
    qty_decimals=0,
    vol_filter=True,
    min_notional=5.0,
    interval="1m",
)

XAU_CONFIG = SymbolConfig(
    symbol="XAUUSDT",
    asset="XAU",
    tp_pct=5.0,         # Champion: 1m VWAPPullback, +7.67% return (53 trades, 49.1% WR, 75d data)
    sl_pct=5.0,
    min_bars=3,
    confirm_bars=1,
    vwap_prox=0.005,    # 0.5%
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,
    price_decimals=2,
    qty_decimals=3,
    vol_filter=False,
    min_notional=5.0,
    interval="1m",
)

LTC_CONFIG = SymbolConfig(
    symbol="LTCUSDT",
    asset="LTC",
    tp_pct=3.0,         # Champion: 1m PDHL, +50.76% return (1003 trades, 57.1% WR, maxDD=15.16%)
    sl_pct=5.0,
    min_bars=0,
    confirm_bars=1,
    vwap_prox=0.0,
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,  # 40% per trade
    price_decimals=2,
    qty_decimals=3,
    vol_filter=False,
    min_notional=5.0,
    interval="1m",
    vwap_dist_stop=0.05,  # exit if price >5% from VWAP in wrong direction
)

LINK_CONFIG = SymbolConfig(
    symbol="LINKUSDT",
    asset="LINK",
    tp_pct=10.0,        # Champion: 1m PDHL, +115.87% return (876 trades, 49.8% WR, maxDD=15.14%)
    sl_pct=5.0,
    min_bars=0,
    confirm_bars=2,
    vwap_prox=0.0,
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,  # 40% per trade
    price_decimals=3,
    qty_decimals=2,
    vol_filter=False,
    min_notional=5.0,
    interval="1m",
    vwap_dist_stop=0.03,  # exit if price >3% from VWAP in wrong direction
)

BCH_CONFIG = SymbolConfig(
    symbol="BCHUSDT",
    asset="BCH",
    tp_pct=10.0,        # Champion: 5m PDHL, +68.46% return (954 trades, 53.8% WR, maxDD=23.32%)
    sl_pct=5.0,
    min_bars=0,
    confirm_bars=1,
    vwap_prox=0.005,    # 0.5% proximity
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,  # 40% per trade
    price_decimals=2,
    qty_decimals=3,
    vol_filter=False,
    min_notional=5.0,
    interval="5m",
    vwap_dist_stop=0.03,  # exit if price >3% from VWAP in wrong direction
)

XMR_CONFIG = SymbolConfig(
    symbol="XMRUSDT",
    asset="XMR",
    tp_pct=7.0,         # Champion: 1m VWAPPullback, +35.76% return (349 trades, 52.1% WR, maxDD=7.15%)
    sl_pct=5.0,
    min_bars=8,
    confirm_bars=0,
    vwap_prox=0.002,    # 0.2%
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,  # 40% per trade
    price_decimals=2,
    qty_decimals=3,
    vol_filter=False,
    min_notional=5.0,
    interval="1m",
)

APT_CONFIG = SymbolConfig(
    symbol="APTUSDT",
    asset="APT",
    tp_pct=10.0,        # Champion: 5m VWAPPullback, +19.66% return (65 trades, 64.6% WR, maxDD=2.07%)
    sl_pct=5.0,
    min_bars=3,
    confirm_bars=0,
    vwap_prox=0.005,    # 0.5%
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,  # 40% per trade
    price_decimals=5,
    qty_decimals=1,
    vol_filter=False,
    min_notional=5.0,
    interval="5m",
    vwap_dist_stop=0.07,  # exit if price >7% from VWAP in wrong direction
)

UNI_CONFIG = SymbolConfig(
    symbol="UNIUSDT",
    asset="UNI",
    tp_pct=10.0,        # Champion: 15m VWAPPullback, +31.71% return (287 trades, 43.2% WR, maxDD=7.72%)
    sl_pct=2.0,
    min_bars=3,
    confirm_bars=1,
    vwap_prox=0.005,    # 0.5%
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,  # 40% per trade
    price_decimals=4,
    qty_decimals=1,
    vol_filter=False,
    min_notional=5.0,
    interval="15m",
    vwap_dist_stop=0.10,  # exit if price >10% from VWAP in wrong direction
)

PEPE1000_CONFIG = SymbolConfig(
    symbol="1000PEPEUSDT",
    asset="PEPE",
    tp_pct=10.0,        # Champion: 5m VWAPPullback, +38.86% return (198 trades, 58.1% WR, maxDD=3.36%)
    sl_pct=5.0,
    min_bars=5,
    confirm_bars=2,
    vwap_prox=0.002,    # 0.2%
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,  # 40% per trade
    price_decimals=8,
    qty_decimals=0,
    vol_filter=False,
    min_notional=5.0,
    interval="5m",
)

DASH_CONFIG = SymbolConfig(
    symbol="DASHUSDT",
    asset="DASH",
    tp_pct=5.0,         # Champion: 15m VWAPPullback, +22.06% return (171 trades, 53.8% WR, maxDD=2.84%)
    sl_pct=5.0,
    min_bars=3,
    confirm_bars=0,
    vwap_prox=0.002,    # 0.2%
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,  # 40% per trade
    price_decimals=2,
    qty_decimals=3,
    vol_filter=False,
    min_notional=5.0,
    interval="15m",
    vwap_dist_stop=0.03,
)

ZEC_CONFIG = SymbolConfig(
    symbol="ZECUSDT",
    asset="ZEC",
    tp_pct=10.0,        # Champion: 5m VWAPPullback, +25.55% return (280 trades, 53.9% WR, maxDD=9.04%, maxCL=6)
    sl_pct=5.0,
    min_bars=8,
    confirm_bars=2,
    vwap_prox=0.005,    # 0.5%
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.40,  # 40% per trade
    price_decimals=2,
    qty_decimals=3,
    vol_filter=False,
    min_notional=5.0,
    interval="5m",
    vwap_dist_stop=0.03,  # exit if price >3% from VWAP in wrong direction
)

SYMBOL_CONFIGS: dict[str, SymbolConfig] = {
    "AXSUSDT": AXS_CONFIG,
    "SANDUSDT": SAND_CONFIG,
    "MANAUSDT": MANA_CONFIG,
    "GALAUSDT": GALA_CONFIG,
    "DOGEUSDT": DOGE_CONFIG,
    "1000SHIBUSDT": SHIB_CONFIG,
    "ETHUSDT": ETH_CONFIG,
    "SOLUSDT": SOL_CONFIG,
    "AVAXUSDT": AVAX_CONFIG,
    "APTUSDT": APT_CONFIG,
    "XRPUSDT": XRP_CONFIG,
    "XAUUSDT": XAU_CONFIG,
    "LTCUSDT": LTC_CONFIG,
    "LINKUSDT": LINK_CONFIG,
    "BCHUSDT": BCH_CONFIG,
    "XMRUSDT": XMR_CONFIG,
    "DASHUSDT": DASH_CONFIG,
    "UNIUSDT": UNI_CONFIG,
    "1000PEPEUSDT": PEPE1000_CONFIG,
    "ZECUSDT": ZEC_CONFIG,
    # "PEPEUSDT": PEPE_CONFIG,  # Removed: Invalid symbol on Binance Futures API
}


def get_symbol_config(name: str) -> SymbolConfig:
    """Look up a SymbolConfig by symbol name (case-insensitive)."""
    key = name.upper()
    if key not in SYMBOL_CONFIGS:
        valid = ", ".join(SYMBOL_CONFIGS)
        raise SystemExit(f"Unknown symbol '{name}'. Valid symbols: {valid}")
    return SYMBOL_CONFIGS[key]
