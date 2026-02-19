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
    pos_size_pct=0.95,
    price_decimals=3,
    qty_decimals=0,
)

SAND_CONFIG = SymbolConfig(
    symbol="SANDUSDT",
    asset="SAND",
    tp_pct=10.0,
    sl_pct=0.8,
    min_bars=5,
    confirm_bars=2,
    vwap_prox=0.002,
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.20,
    price_decimals=5,
    qty_decimals=0,
)

MANA_CONFIG = SymbolConfig(
    symbol="MANAUSDT",
    asset="MANA",
    tp_pct=5.0,
    sl_pct=5.0,
    min_bars=12,
    confirm_bars=2,
    vwap_prox=0.005,
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.30,  # 30% → $73 × 0.30 = $21.90/trade ✅
    price_decimals=4,
    qty_decimals=0,
)

GALA_CONFIG = SymbolConfig(
    symbol="GALAUSDT",
    asset="GALA",
    tp_pct=5.0,
    sl_pct=5.0,
    min_bars=5,
    confirm_bars=0,
    vwap_prox=0.002,
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.20,
    vol_filter=True,
    price_decimals=5,
    qty_decimals=0,
)

DOGE_CONFIG = SymbolConfig(
    symbol="DOGEUSDT",
    asset="DOGE",
    tp_pct=10.0,
    sl_pct=5.0,
    min_bars=3,
    confirm_bars=2,
    vwap_prox=0.005,
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.20,
    price_decimals=5,
    qty_decimals=0,
)

SHIB_CONFIG = SymbolConfig(
    symbol="1000SHIBUSDT",
    asset="SHIB",
    tp_pct=10.0,
    sl_pct=5.0,
    min_bars=3,
    confirm_bars=2,
    vwap_prox=0.005,
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.20,
    price_decimals=6,
    qty_decimals=0,
)

ETH_CONFIG = SymbolConfig(
    symbol="ETHUSDT",
    asset="ETH",
    tp_pct=10.0,
    sl_pct=5.0,
    min_bars=20,
    confirm_bars=0,
    vwap_prox=0.005,
    entry_start_min=60,
    entry_cutoff_min=1320,
    eod_min=1430,
    pos_size_pct=0.30,  # 30% per trade ($68 × 0.30 = $20.40 ✅)
    price_decimals=2,
    qty_decimals=3,
    min_notional=20.0,
)

SYMBOL_CONFIGS: dict[str, SymbolConfig] = {
    "AXSUSDT": AXS_CONFIG,
    "SANDUSDT": SAND_CONFIG,
    "MANAUSDT": MANA_CONFIG,
    "GALAUSDT": GALA_CONFIG,
    "DOGEUSDT": DOGE_CONFIG,
    "1000SHIBUSDT": SHIB_CONFIG,
    "ETHUSDT": ETH_CONFIG,
}


def get_symbol_config(name: str) -> SymbolConfig:
    """Look up a SymbolConfig by symbol name (case-insensitive)."""
    key = name.upper()
    if key not in SYMBOL_CONFIGS:
        valid = ", ".join(SYMBOL_CONFIGS)
        raise SystemExit(f"Unknown symbol '{name}'. Valid symbols: {valid}")
    return SYMBOL_CONFIGS[key]
