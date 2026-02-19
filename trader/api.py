"""FastAPI dashboard backend.

Exposes:
  REST  /api/balance, /api/positions, /api/trades, /api/klines/{symbol},
        /api/commissions, /api/bot_states
  WS    /ws/feed  — real-time bot events (candle, signal, order, position_closed)

Run standalone (dashboard-only, bots running separately):
    uvicorn trader.api:app --port 8080

Or via CLI (bots co-located):
    poetry run python -m trader serve [--port 8080]
"""

import asyncio
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from trader.config import BINANCE_API_KEY, BINANCE_SECRET_KEY, SOCKS_PROXY, SYMBOL_CONFIGS
from trader import events, bot_registry

app = FastAPI(title="Binance Trader Dashboard", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Binance client (lazy, shared) ────────────────────────────────────────────

_client = None
_client_lock = asyncio.Lock()


def _build_client():
    from binance_sdk_derivatives_trading_usds_futures import DerivativesTradingUsdsFutures
    from binance_common.configuration import ConfigurationRestAPI

    proxy = None
    if SOCKS_PROXY:
        from urllib.parse import urlparse
        p = urlparse(SOCKS_PROXY)
        proxy = {"protocol": p.scheme, "host": p.hostname, "port": p.port}

    cfg = ConfigurationRestAPI(
        api_key=BINANCE_API_KEY,
        api_secret=BINANCE_SECRET_KEY,
        proxy=proxy,
        timeout=10000,
    )
    return DerivativesTradingUsdsFutures(config_rest_api=cfg)


async def get_client():
    global _client
    async with _client_lock:
        if _client is None:
            if not BINANCE_API_KEY or not BINANCE_SECRET_KEY:
                raise ValueError("BINANCE_API_KEY / BINANCE_SECRET_KEY not configured")
            _client = await asyncio.to_thread(_build_client)
    return _client


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/api/balance")
async def get_balance():
    client = await get_client()
    resp = await asyncio.to_thread(client.rest_api.futures_account_balance_v3)
    usdt = next(
        (
            {"asset": b.asset, "balance": _safe_float(b.balance),
             "available": _safe_float(b.available_balance)}
            for b in resp.data()
            if b.asset == "USDT"
        ),
        None,
    )
    return {"usdt": usdt}


@app.get("/api/positions")
async def get_positions():
    client = await get_client()
    resp = await asyncio.to_thread(client.rest_api.position_information_v3)
    positions = [
        {
            "symbol":          p.symbol,
            "side":            "LONG" if _safe_float(p.position_amt) > 0 else "SHORT",
            "qty":             abs(_safe_float(p.position_amt)),
            "entry_price":     _safe_float(p.entry_price),
            "mark_price":      _safe_float(getattr(p, "mark_price", 0)),
            "unrealized_pnl":  _safe_float(p.un_realized_profit),
            "leverage":        int(_safe_float(getattr(p, "leverage", 1))),
        }
        for p in resp.data()
        if abs(_safe_float(p.position_amt)) > 0
    ]
    return {"positions": positions}


@app.get("/api/trades")
async def get_trades(symbol: str | None = None, days: int = 7):
    client = await get_client()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - days * 86_400_000

    symbols = [symbol.upper()] if symbol else list(SYMBOL_CONFIGS.keys())

    all_trades: list[dict] = []
    for sym in symbols:
        try:
            resp = await asyncio.to_thread(
                lambda s=sym: client.rest_api.account_trade_list(
                    symbol=s, start_time=start_ms, limit=500
                )
            )
            for t in resp.data():
                all_trades.append({
                    "symbol":          t.symbol,
                    "side":            t.side,
                    "price":           _safe_float(t.price),
                    "qty":             _safe_float(t.qty),
                    "realized_pnl":    _safe_float(t.realized_pnl),
                    "commission":      _safe_float(t.commission),
                    "commission_asset": t.commission_asset,
                    "time":            int(t.time),
                    "order_id":        t.order_id,
                    "buyer":           t.buyer,
                })
        except Exception:
            pass

    all_trades.sort(key=lambda x: x["time"], reverse=True)
    return {"trades": all_trades}


@app.get("/api/klines/{symbol}")
async def get_klines(symbol: str, interval: str = "1m", limit: int = 500):
    url = (
        f"https://fapi.binance.com/fapi/v1/klines"
        f"?symbol={symbol.upper()}&interval={interval}&limit={limit}"
    )
    raw = await asyncio.to_thread(
        lambda: urllib.request.urlopen(url, timeout=10).read()
    )
    klines = json.loads(raw)
    return {
        "klines": [
            {
                "time":   k[0] // 1000,   # seconds for TradingView Lightweight Charts
                "open":   float(k[1]),
                "high":   float(k[2]),
                "low":    float(k[3]),
                "close":  float(k[4]),
                "volume": float(k[5]),
            }
            for k in klines
        ]
    }


@app.get("/api/commissions")
async def get_commissions(days: int = 30):
    data = await get_trades(days=days)
    by_asset: dict[str, float] = {}
    by_symbol: dict[str, float] = {}
    daily: dict[str, float] = {}

    for t in data["trades"]:
        asset = t["commission_asset"]
        amt   = t["commission"]
        sym   = t["symbol"]
        date  = datetime.fromtimestamp(t["time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

        by_asset[asset]   = by_asset.get(asset, 0.0)   + amt
        by_symbol[sym]    = by_symbol.get(sym, 0.0)    + amt
        if asset == "USDT":
            daily[date] = daily.get(date, 0.0) + amt

    sorted_daily = [
        {"date": d, "commission": round(v, 6)}
        for d, v in sorted(daily.items())
    ]
    return {
        "by_asset":       {k: round(v, 6) for k, v in by_asset.items()},
        "by_symbol":      {k: round(v, 6) for k, v in by_symbol.items()},
        "daily":          sorted_daily,
        "total_usdt":     round(by_asset.get("USDT", 0.0), 6),
        "days":           days,
    }


@app.get("/api/bot_states")
async def get_bot_states():
    return {"bots": bot_registry.get_states()}


# ── WebSocket feed ─────────────────────────────────────────────────────────────

@app.websocket("/ws/feed")
async def ws_feed(websocket: WebSocket):
    await websocket.accept()
    q = events.subscribe()
    try:
        while True:
            event = await q.get()
            await websocket.send_json(event)
    except (WebSocketDisconnect, Exception):
        events.unsubscribe(q)


# ── Serve built frontend (production) ─────────────────────────────────────────

_dist = Path(__file__).parent.parent / "frontend" / "dist"

if _dist.exists():
    _assets = _dist / "assets"
    if _assets.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        index = _dist / "index.html"
        return FileResponse(str(index))
